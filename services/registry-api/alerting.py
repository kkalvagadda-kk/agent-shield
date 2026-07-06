"""
Failure alerting for triggered (scheduled / event-driven) agent runs.

When an internal run completes with status=failed, we look up the originating
trigger's alert config (`alert_email`, `alert_on_failure`) and, if enabled,
send a failure notification over SMTP.

Transport: plain SMTP, configured via env vars. If SMTP_HOST is unset the
dispatcher logs the intended alert and returns without sending — this keeps
local/dev and CI (no mail server) fully functional while still proving the
decision path in e2e tests via the log line.

Env:
  SMTP_HOST   — mail server host (unset ⇒ log-only mode)
  SMTP_PORT   — mail server port (default 25)
  SMTP_FROM   — From: address (default alerts@agentshield.local)
  SMTP_USER   — optional SMTP auth username
  SMTP_PASSWORD — optional SMTP auth password
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AgentTrigger

logger = logging.getLogger(__name__)


def _smtp_config() -> dict[str, str | int | None]:
    return {
        "host": os.getenv("SMTP_HOST"),
        "port": int(os.getenv("SMTP_PORT", "25")),
        "from_addr": os.getenv("SMTP_FROM", "alerts@agentshield.local"),
        "user": os.getenv("SMTP_USER"),
        "password": os.getenv("SMTP_PASSWORD"),
    }


def _send_smtp(to_addr: str, subject: str, body: str) -> None:
    """Send one email. Log-only when SMTP_HOST is unset. Never raises."""
    cfg = _smtp_config()
    if not cfg["host"]:
        # Log-only mode. The e2e suite asserts on this line to prove dispatch.
        logger.info(
            "ALERT (log-only, SMTP_HOST unset) to=%s subject=%r", to_addr, subject
        )
        return

    msg = EmailMessage()
    msg["From"] = cfg["from_addr"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as smtp:
            try:
                smtp.starttls(context=ssl.create_default_context())
            except (smtplib.SMTPNotSupportedError, smtplib.SMTPException):
                pass  # server without STARTTLS — send plaintext
            if cfg["user"] and cfg["password"]:
                smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        logger.info("ALERT sent to=%s subject=%r via=%s", to_addr, subject, cfg["host"])
    except Exception as exc:  # a broken mail server must not break run recording
        logger.error("ALERT dispatch failed to=%s: %s", to_addr, exc)


async def dispatch_failure_alert(
    session: AsyncSession,
    *,
    trigger_id: UUID | None,
    agent_name: str,
    run_id: str,
    error_message: str | None,
) -> None:
    """Look up the trigger's alert config and notify on failure.

    No-op (with debug log) when there is no trigger_id, no configured
    alert_email, or alert_on_failure is disabled.
    """
    if trigger_id is None:
        logger.debug("no trigger_id for run %s — skipping failure alert", run_id)
        return

    result = await session.execute(
        select(AgentTrigger).where(AgentTrigger.id == trigger_id)
    )
    trigger = result.scalar_one_or_none()
    if trigger is None:
        logger.debug("trigger %s not found — skipping failure alert", trigger_id)
        return
    if not trigger.alert_on_failure:
        logger.debug("alert_on_failure disabled for trigger %s", trigger_id)
        return
    if not trigger.alert_email:
        logger.debug("no alert_email set for trigger %s", trigger_id)
        return

    subject = f"[AgentShield] Agent '{agent_name}' run failed"
    body = (
        f"A triggered run for agent '{agent_name}' failed.\n\n"
        f"Run ID: {run_id}\n"
        f"Trigger: {trigger.trigger_type} ({trigger_id})\n"
        f"Error: {error_message or 'unknown'}\n"
    )
    _send_smtp(trigger.alert_email, subject, body)
