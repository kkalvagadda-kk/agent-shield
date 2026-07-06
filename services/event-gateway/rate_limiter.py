"""
Rate limiting + replay protection for the Event Gateway (threat model T-3/T-4/T-11).

Two-dimensional sliding-window rate limit backed by Redis:
  - per-agent   key: ratelimit:agent:{agent_name}
  - per-source-IP key: ratelimit:ip:{ip}

Sliding window via a Redis sorted set of request timestamps (ms). Each check
drops entries older than the window, counts what remains, and — if under the
cap — records the new request. 401s are counted too (caller increments before
deciding), so token brute-force is metered.

Fail-CLOSED: if Redis is unreachable, `allowed()` returns False (throttle)
rather than letting unbounded traffic through — the gateway is the only thing
standing between the public internet and agent execution.

Replay protection:
  - nonce uniqueness: SET nonce:{agent}:{nonce} NX EX 3600  (dup ⇒ replay)
"""
from __future__ import annotations

import logging
import os
import time

import redis

logger = logging.getLogger("event-gateway.ratelimit")

WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
MAX_PER_AGENT = int(os.getenv("RATE_LIMIT_MAX_PER_AGENT", "100"))
MAX_PER_IP = int(os.getenv("RATE_LIMIT_MAX_PER_IP", "60"))
NONCE_TTL_SECONDS = int(os.getenv("REPLAY_NONCE_TTL_SECONDS", "3600"))

_redis_url = os.getenv("REDIS_URL", "redis://agentshield-redis-master:6379/0")


class RateLimiter:
    def __init__(self, url: str | None = None) -> None:
        self._client = redis.Redis.from_url(
            url or _redis_url, socket_connect_timeout=2, socket_timeout=2
        )

    def _sliding_ok(self, key: str, limit: int, now_ms: int) -> bool:
        """Sliding-window check+record for one key. Raises on Redis error."""
        window_ms = WINDOW_SECONDS * 1000
        cutoff = now_ms - window_ms
        pipe = self._client.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)      # drop entries older than window
        pipe.zcard(key)                            # count remaining
        results = pipe.execute()
        count = results[1]
        if count >= limit:
            return False
        # Under the cap — record this request and refresh the key TTL.
        member = f"{now_ms}-{os.urandom(4).hex()}"
        pipe = self._client.pipeline()
        pipe.zadd(key, {member: now_ms})
        pipe.expire(key, WINDOW_SECONDS + 1)
        pipe.execute()
        return True

    def allowed(self, agent_name: str, source_ip: str) -> tuple[bool, str]:
        """Check both dimensions. Returns (allowed, dimension_that_tripped).

        Fail-closed on Redis error.
        """
        now_ms = int(time.time() * 1000)
        try:
            if not self._sliding_ok(f"ratelimit:agent:{agent_name}", MAX_PER_AGENT, now_ms):
                return False, "agent"
            if not self._sliding_ok(f"ratelimit:ip:{source_ip}", MAX_PER_IP, now_ms):
                return False, "ip"
            return True, ""
        except Exception as exc:  # fail-closed
            logger.error("rate limiter Redis error (fail-closed): %s", exc)
            return False, "redis-unavailable"

    def check_nonce(self, agent_name: str, nonce: str) -> bool:
        """Return True if the nonce is fresh (first use), False if replayed.

        Fail-closed on Redis error (treat as replay ⇒ reject).
        """
        key = f"nonce:{agent_name}:{nonce}"
        try:
            # SET NX returns True only if the key did not already exist.
            return bool(self._client.set(key, "1", nx=True, ex=NONCE_TTL_SECONDS))
        except Exception as exc:
            logger.error("nonce check Redis error (fail-closed): %s", exc)
            return False
