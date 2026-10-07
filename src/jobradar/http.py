"""A small polite async HTTP layer: global + per-host concurrency, retries, backoff."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Http:
    def __init__(
        self,
        user_agent: str,
        timeout: float = 30,
        concurrency: int = 12,
        per_host: int = 3,
        retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_base: float = 1.0,
    ):
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=httpx.Timeout(timeout, connect=15),
            follow_redirects=True,
            transport=transport,
        )
        self._global = asyncio.Semaphore(concurrency)
        self._per_host_limit = per_host
        self._hosts: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(self._per_host_limit)
        )
        self.retries = retries
        self.backoff_base = backoff_base
        self.request_count = 0

    async def __aenter__(self) -> Http:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = urlsplit(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            async with self._global, self._hosts[host]:
                try:
                    self.request_count += 1
                    resp = await self._client.request(method, url, **kwargs)
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    last_exc = exc
                    resp = None
            if resp is not None:
                if resp.status_code < 400:
                    return resp
                if resp.status_code not in RETRY_STATUS or attempt == self.retries:
                    raise HttpError(f"HTTP {resp.status_code} for {url}", resp.status_code)
                last_exc = HttpError(f"HTTP {resp.status_code} for {url}", resp.status_code)
                delay = _retry_after(resp) or self._backoff(attempt)
            else:
                if attempt == self.retries:
                    break
                delay = self._backoff(attempt)
            log.debug("retrying %s in %.1fs (attempt %d): %s", url, delay, attempt + 1, last_exc)
            await asyncio.sleep(delay)
        raise HttpError(f"{type(last_exc).__name__}: {last_exc} for {url}")

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = await self.request("GET", url, **kwargs)
        try:
            return resp.json()
        except ValueError as exc:
            raise HttpError(f"Invalid JSON from {url}: {exc}") from exc

    def _backoff(self, attempt: int) -> float:
        return self.backoff_base * (2**attempt) + random.uniform(0, self.backoff_base / 2)


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return min(float(value), 60.0)
    except ValueError:
        return None
