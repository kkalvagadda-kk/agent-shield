"""Platform-admin bootstrap — the platform creates its own sole admin, from code.

THE INVARIANT THIS MODULE OWNS
------------------------------
The platform has exactly ONE auto-created user, ``platform-admin``, and that user
HAS a ``user_team_assignments`` row. Decision 40: users are created by platform
code, never by the Helm chart and never auto-provisioned from the IdP. A missing
row is therefore not a kind of user — it is corruption
(``rbac.NoPlatformRole`` refuses it).

WHY THE LOOKUP IS BY **USERNAME**, NEVER BY A STORED ``sub``
------------------------------------------------------------
This is the whole self-healing property, not an implementation detail.
``scripts/seed-platform-admin-role.sh:12-17`` records the failure of the
alternative: when the Keycloak realm is recreated the admin gets a NEW ``sub``,
an assignment pinned to the old one strands on a dead subject, ``/me`` resolves
no platform role, and the Studio Admin menu silently vanishes (observed
2026-07-20, admin stranded on ``643b0e62…`` while the live user was
``75c7c8b3…``). A username is stable across realm recreation; a ``sub`` is not.
Looking the admin up by username on EVERY attempt makes re-pinning fall out of
the design instead of out of a script someone remembers to run. Do not "optimise"
this into a cached sub. See ``docs/plan/rbac-r0-r1/research.md`` D3.

WHY THE EMAIL IS PINNED AND NEVER PARAMETERISED
-----------------------------------------------
``ADMIN_EMAIL`` must stay ``platform-admin@agentshield.local``. Langfuse
authorizes trace access by PROJECT MEMBERSHIP keyed on an email address, and its
bootstrap user is created with exactly this one
(``charts/agentshield/values.yaml:509`` ``LANGFUSE_INIT_USER_EMAIL``). A different
address does not fail here — it silently makes every Langfuse trace link answer
"You do not have access to this trace" after a successful SSO login. See
``docs/bugs/langfuse-trace-access-sso-and-membership.md``. That is why step 7
RE-WRITES the email on every successful attempt rather than only on create.

WHY FAILURE IS NON-FATAL
------------------------
``ensure_platform_admin`` NEVER raises and never aborts startup. registry-api's
``wait-for-keycloak`` init container waits only for the MASTER realm; the
``agentshield`` realm is created later by a post-install Helm hook, so this
process routinely runs before the realm exists. Crash-looping a fresh install on
a dependency that is expected to be late is worse than degrading: instead,
failure is recorded in ``bootstrap_state`` and ``GET /ready`` stays RED (503,
``status="bootstrapping"``) until an attempt succeeds. ``/health`` is unaffected,
so the liveness/readiness split keeps a Keycloak outage from restarting the pod.
There is deliberately no "ready after N attempts" escape — that trades a visible
failure for an invisible one (spec OQ-1, resolved 2026-08-04).

FR-1 (bootstrap from lifespan), FR-2 (single-flighted across replicas),
FR-3 (username lookup ⇒ realm recreation self-heals).
Contract: ``docs/plan/rbac-r0-r1/contracts/bootstrap-and-ready.md``.
"""
from __future__ import annotations

import asyncio
import logging
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone

from config import settings

logger = logging.getLogger(__name__)

# 63-bit positive advisory-lock key. Same idiom and rationale as
# mcp_health._SWEEP_LOCK_KEY (mcp_health.py:39-45): the scheduler
# (services/scheduler/ha.py) takes single-bigint session locks keyed as
# ``crc32(...) & 0x7FFFFFFF`` — i.e. 31-bit values in [0, 2**31). Seeding from the
# same crc32 idiom but setting bit 62 puts this key permanently above 2**31, so it
# can NEVER collide with a scheduler fire lock, while staying positive for a signed
# Postgres bigint (< 2**63). Value: 4611686019521751996
# (the health sweep is 4611686020559757442 — distinct).
BOOTSTRAP_LOCK_KEY = (zlib.crc32(b"platform-admin-bootstrap") & 0x7FFFFFFF) | (1 << 62)

ADMIN_USERNAME = "platform-admin"
# PINNED. See the module docstring — Langfuse project membership keys on this
# literal (charts/agentshield/values.yaml:509). Never make it a setting.
ADMIN_EMAIL = "platform-admin@agentshield.local"
ADMIN_FIRST_NAME = "Platform"
ADMIN_LAST_NAME = "Admin"
ADMIN_ROLE = "platform-admin"
# Hard-coded per spec OQ-3 option (a); matches the team every existing admin row
# already carries. Making it configurable buys nothing while there is one admin.
ADMIN_TEAM = "platform"

# The bootstrap upsert. The guarded WHERE is what makes a restart a GENUINE no-op:
# assigned_at moves only when team_name or role actually changed, which is exactly
# what Story 1 scenario 2 asserts. Do not simplify it into an unconditional
# DO UPDATE — that would re-stamp assigned_at on every pod start and destroy the
# only signal for "when was this role last really changed".
_UPSERT_SQL = """
INSERT INTO user_team_assignments (user_sub, team_name, role, assigned_by, assigned_at)
VALUES (:sub, :team, :role, 'system:bootstrap', now())
ON CONFLICT (user_sub) DO UPDATE
   SET team_name   = EXCLUDED.team_name,
       role        = EXCLUDED.role,
       assigned_by = EXCLUDED.assigned_by,
       assigned_at = now()
 WHERE user_team_assignments.team_name   IS DISTINCT FROM EXCLUDED.team_name
    OR user_team_assignments.role        IS DISTINCT FROM EXCLUDED.role
    OR user_team_assignments.assigned_by IS DISTINCT FROM EXCLUDED.assigned_by
"""
# The guard compares every column this statement WRITES except assigned_at itself, so a
# restart that changes nothing is a genuine no-op (assigned_at does not move) while any
# real divergence is reclaimed. assigned_by is in the list deliberately: it is the row's
# PROVENANCE, and without it the bootstrap could never take a row back from another
# writer. That is not hypothetical — scripts/seed-platform-admin-role.sh stamps
# 'system:seed-platform-admin', deploy-eks.sh called it after every deploy, and the row
# then stayed mis-attributed through every subsequent restart because only team/role were
# compared. Caught by suite-97 T-S97-001, which asserts the bootstrap wrote the row.


@dataclass
class BootstrapState:
    """Process-local readiness flag for the bootstrap. Read by ``main.ready()``.

    Deliberately NOT persisted: it answers "has THIS replica confirmed the admin
    is pinned", which is what its own /ready probe must report. A replica that
    lost the advisory lock keeps ``ok=False`` and retries until it can confirm
    for itself.
    """

    ok: bool = False
    last_error: str | None = None
    last_attempt_at: datetime | None = None
    admin_sub: str | None = None
    attempts: int = 0


bootstrap_state = BootstrapState()


async def ensure_platform_admin() -> bool:
    """Create-or-reconcile the platform-admin user and its assignment row.

    Idempotent and single-flighted across replicas by ``pg_try_advisory_lock``.

    Returns ``True`` when the row is pinned to the LIVE Keycloak ``sub``, or when
    a peer replica holds the lock (nothing to do this attempt). Returns ``False``
    when the attempt failed — ``bootstrap_state.last_error`` names the cause.

    **NEVER raises.** A Keycloak outage must degrade /ready, not crash-loop the
    pod. Never deletes anything, and never resets an existing user's password.
    """
    # 1 — config gate. With the bootstrap off, /ready must NOT gate on it, or a
    # deploy that provisions the admin some other way can never become ready.
    if not settings.platform_admin_bootstrap_enabled:
        bootstrap_state.ok = True
        logger.info("bootstrap: disabled by config — /ready will not gate on it")
        return True

    # 2 — attempt counters. These count ATTEMPTS, so they advance even on the
    # lock-lost path below; ok/admin_sub/last_error are what carry the verdict.
    bootstrap_state.attempts += 1
    bootstrap_state.last_attempt_at = datetime.now(timezone.utc)

    # 3 — password guard. Minting an admin with an unknown password is worse than
    # failing: it looks installed and nobody can log in.
    if not settings.platform_admin_password:
        bootstrap_state.ok = False
        bootstrap_state.last_error = "PLATFORM_ADMIN_PASSWORD is empty"
        logger.error(
            "bootstrap: PLATFORM_ADMIN_PASSWORD is empty — refusing to create "
            "%s with an unknown password. Check the keycloak-user-passwords "
            "Secret (key: platform-admin) is mounted into registry-api.",
            ADMIN_USERNAME,
        )
        return False

    try:
        import keycloak_client as kc
        from sqlalchemy import text

        from db import AsyncSessionLocal, engine

        # 4 — single-flight. A DEDICATED raw connection for the lock, separate
        # from the session that writes: the advisory lock is session-scoped, so
        # acquire and unlock must ride the SAME connection, and holding one open
        # transaction on it pins the PgBouncer server backend so the lock
        # semantics survive transaction pooling. Same shape as
        # mcp_health._sweep_once (mcp_health.py:190-236), unlock in a finally.
        async with engine.connect() as lock_conn:
            acquired = (
                await lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": BOOTSTRAP_LOCK_KEY}
                )
            ).scalar()
            if not acquired:
                # A peer owns this bootstrap. That is success for the CLUSTER, so
                # do not record a failure — but leave ok=False so this replica
                # re-checks and confirms for itself before serving traffic.
                await lock_conn.rollback()
                logger.info("bootstrap: another replica holds the lock — skipping")
                return True

            try:
                # 5 — find the admin BY USERNAME (never by a stored sub). The
                # second filter is defensive: Keycloak honours `exact`, but the
                # response is still a list.
                users = await kc.list_users(username=ADMIN_USERNAME, exact=True)
                existing = next(
                    (u for u in users if u.get("username") == ADMIN_USERNAME), None
                )

                # 6 — create if absent. create_user writes a TEMPORARY password
                # plus requiredActions=["UPDATE_PASSWORD"], so the follow-up
                # reset_password(temporary=False) is required for the admin to be
                # able to log in at all.
                if existing is None:
                    kc_id = await kc.create_user(
                        username=ADMIN_USERNAME,
                        email=ADMIN_EMAIL,
                        first_name=ADMIN_FIRST_NAME,
                        last_name=ADMIN_LAST_NAME,
                        temp_password=settings.platform_admin_password,
                    )
                    await kc.reset_password(
                        kc_id, settings.platform_admin_password, temporary=False
                    )
                    created = True
                else:
                    kc_id = existing["id"]
                    created = False

                # 7 — profile reconcile, on EVERY attempt (not only on create).
                # Re-pins the Langfuse-critical email and clears the
                # UPDATE_PASSWORD action create_user sets, so both a browser login
                # and a direct-grant `password` flow work. Keycloak's declarative
                # user profile (VERIFY_PROFILE) rejects a direct grant with
                # "Account is not fully set up" when email/emailVerified/
                # firstName/lastName are missing — measured, see
                # charts/agentshield/templates/realm-init-job.yaml:249-253 and
                # scripts/e2e/lib/e2e-auth.sh. Running it unconditionally is what
                # makes a hand-edited admin self-heal.
                #
                # It deliberately does NOT touch the password on an existing user:
                # an operator's rotation must survive a restart.
                await kc.update_user(
                    kc_id,
                    email=ADMIN_EMAIL,
                    emailVerified=True,
                    firstName=ADMIN_FIRST_NAME,
                    lastName=ADMIN_LAST_NAME,
                    enabled=True,
                    requiredActions=[],
                )

                # 8 — realm role. NOT wrapped in a bare `except: pass` — a user
                # whose Keycloak role and DB row disagree is the half-created
                # state R0 exists to remove, so let it raise into the handler
                # below. A missing realm-role OBJECT is not an error:
                # set_user_realm_role (keycloak_client.py:203) silently skips a
                # name absent from role_map, which is today's state (G-R0-4).
                await kc.set_user_realm_role(kc_id, ADMIN_ROLE)

                # 9 — the row, on a fresh ORM session (NOT lock_conn, whose
                # transaction is holding the advisory lock open).
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        text(_UPSERT_SQL),
                        {"sub": kc_id, "team": ADMIN_TEAM, "role": ADMIN_ROLE},
                    )
                    await session.commit()

                # 10 — success.
                bootstrap_state.ok = True
                bootstrap_state.admin_sub = kc_id
                bootstrap_state.last_error = None
                logger.info(
                    "bootstrap: platform-admin pinned sub=%s team=%s role=%s created=%s",
                    kc_id,
                    ADMIN_TEAM,
                    ADMIN_ROLE,
                    created,
                )
                return True
            finally:
                await lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": BOOTSTRAP_LOCK_KEY}
                )
                await lock_conn.commit()

    # 11 — catch-all. NEVER re-raise: this runs from lifespan, and a Keycloak
    # outage must hold /ready red rather than crash-loop the pod.
    except Exception as exc:
        bootstrap_state.ok = False
        bootstrap_state.last_error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "bootstrap: FAILED (attempt %d): %s",
            bootstrap_state.attempts,
            exc,
            exc_info=True,
        )
        return False


async def bootstrap_admin_loop(interval_seconds: int = 30) -> None:
    """Retry ``ensure_platform_admin`` until it succeeds, then return.

    Unlike the health/cost sweeps this is NOT a forever-loop: once the admin is
    pinned there is nothing left to do, and /ready has gone green. Cancelled by
    ``lifespan`` on shutdown.
    """
    try:
        while not bootstrap_state.ok:
            await ensure_platform_admin()
            if bootstrap_state.ok:
                break
            logger.warning(
                "bootstrap: not ready (attempt %d: %s) — retrying in %ss. "
                "/ready stays 503 until this succeeds.",
                bootstrap_state.attempts,
                bootstrap_state.last_error,
                interval_seconds,
            )
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        # Shutdown. Return rather than re-raise — lifespan awaits this task and
        # there is no partial state to unwind (the advisory lock is released in
        # ensure_platform_admin's own finally, and the upsert commits atomically).
        logger.info("bootstrap: loop cancelled during shutdown")
        return
