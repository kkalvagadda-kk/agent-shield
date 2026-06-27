"""
Async HTTP clients for each safety scanner.

Each client embeds:
- Exponential-backoff retry (3 retries: 100 ms / 500 ms / 2 s)
- In-process circuit breaker (5 consecutive failures → open for 30 s)
- Fail-closed: raises on open circuit so the orchestrator can block
"""

import asyncio
import time

import httpx

# Retry delays (seconds) for attempts 2, 3, 4 (first attempt has no delay)
_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.5, 2.0)


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, reset_timeout: float = 30.0):
        self.name = name
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._failures = 0
        self._opened_at: float | None = None

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self._reset_timeout:
            self._failures = 0
            self._opened_at = None
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._opened_at = time.monotonic()


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    body: dict,
    cb: CircuitBreaker,
    timeout: float = 5.0,
) -> dict:
    if cb.is_open():
        raise RuntimeError(f"Circuit breaker open for {cb.name}")

    last_exc: Exception | None = None
    for attempt, delay in enumerate([0.0, *_RETRY_DELAYS]):
        if delay:
            await asyncio.sleep(delay)
        try:
            r = await client.post(url, json=body, timeout=timeout)
            r.raise_for_status()
            cb.record_success()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

    cb.record_failure()
    raise last_exc  # type: ignore[misc]


class LLMGuardClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url)
        self._cb = CircuitBreaker("llm-guard")

    async def scan(self, text: str) -> dict:
        return await _post_with_retry(self._client, "/analyze", {"text": text}, self._cb)

    async def ping(self) -> bool:
        try:
            r = await self._client.get("/health", timeout=2.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        await self._client.aclose()


class PresidioClient:
    def __init__(self, analyzer_url: str, anonymizer_url: str) -> None:
        self._analyzer = httpx.AsyncClient(base_url=analyzer_url)
        self._anonymizer = httpx.AsyncClient(base_url=anonymizer_url)
        self._cb_analyzer = CircuitBreaker("presidio-analyzer")
        self._cb_anonymizer = CircuitBreaker("presidio-anonymizer")

    async def analyze(self, text: str) -> list[dict]:
        result = await _post_with_retry(
            self._analyzer,
            "/analyze",
            {"text": text, "language": "en"},
            self._cb_analyzer,
        )
        return result if isinstance(result, list) else []

    async def anonymize(self, text: str, analyzer_results: list[dict]) -> dict:
        return await _post_with_retry(
            self._anonymizer,
            "/anonymize",
            {"text": text, "analyzer_results": analyzer_results},
            self._cb_anonymizer,
        )

    async def deanonymize(self, anonymized_text: str, anonymizer_results: dict) -> dict:
        return await _post_with_retry(
            self._anonymizer,
            "/deanonymize",
            {"text": anonymized_text, "anonymizer_results": anonymizer_results},
            self._cb_anonymizer,
        )

    async def ping(self) -> bool:
        try:
            r = await self._analyzer.get("/health", timeout=2.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        await self._analyzer.aclose()
        await self._anonymizer.aclose()


class NeMoClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url)
        self._cb = CircuitBreaker("nemo")

    async def check(self, text: str) -> dict:
        return await _post_with_retry(self._client, "/check", {"text": text}, self._cb)

    async def ping(self) -> bool:
        try:
            r = await self._client.get("/health", timeout=2.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
