"""Multi-provider LLM router tuned for FREE tiers.

Groq and Gemini both expose OpenAI-compatible `/chat/completions` endpoints, so one
client covers both. The router:

* picks the first provider (per task) that has an API key and quota left,
* enforces per-minute request/token windows locally (no 429 storms),
* tracks per-day usage in SQLite so the budget survives across the 2 daily runs,
* reads rate-limit headers and backs off / fails over on 429, 5xx and bad JSON,
* caches responses by prompt hash so re-runs never pay twice,
* returns None when everything is exhausted, so callers degrade to rule-based logic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from jobradar.config import Config
from jobradar.normalize import utcnow
from jobradar.store import Store

log = logging.getLogger(__name__)


@dataclass
class ProviderSpec:
    name: str
    base_url: str
    api_key_env: str
    model: str
    rpm: int = 30
    rpd: int = 1000
    tpm: int = 8000
    tpd: int = 200_000
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProviderSpec:
        limits = d.get("limits") or {}
        return cls(
            name=d["name"],
            base_url=d["base_url"].rstrip("/"),
            api_key_env=d["api_key_env"],
            model=d["model"],
            rpm=int(limits.get("rpm", 30)),
            rpd=int(limits.get("rpd", 1000)),
            tpm=int(limits.get("tpm", 8000)),
            tpd=int(limits.get("tpd", 200_000)),
            params=dict(d.get("params") or {}),
        )


@dataclass
class _State:
    window: deque = field(default_factory=deque)  # [timestamp, tokens] entries of the last 60s
    exhausted: str | None = None  # reason this provider is out for the run
    json_mode: bool = True


@dataclass
class LLMResult:
    data: Any
    provider: str
    cached: bool = False
    tokens: int = 0


@dataclass
class LLMStats:
    requests: int = 0
    tokens: int = 0
    cache_hits: int = 0
    failures: int = 0
    retries: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)
    exhausted: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class LLMUnavailable(Exception):
    pass


_DURATION = re.compile(
    r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$"
)


def parse_duration(value: str | None) -> float | None:
    """Parse Groq-style durations: '7.66s', '2m59.56s', '1h2m3s', '120ms', or plain seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    m = _DURATION.match(value)
    if not m or not any(m.groups()):
        return None
    h, mnt, s, ms = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mnt * 60 + s + ms / 1000


def parse_json_loose(text: str | None) -> Any:
    """Parse JSON from a model reply that may include fences or thinking tags."""
    if not text:
        raise ValueError("empty response")
    s = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip(), flags=re.I)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON found")
    start = min(starts)
    end = max(s.rfind("}"), s.rfind("]"))
    if end <= start:
        raise ValueError("no JSON found")
    return json.loads(s[start : end + 1])


def estimate_tokens(text: str) -> int:
    return int(len(text) / 3.6) + 8


class LLMRouter:
    MAX_WAIT = 75.0  # longest we'll wait for a minute-window to free up before failing over

    def __init__(
        self,
        config: Config,
        store: Store,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        enabled: bool = True,
    ):
        llm = config.section("llm")
        self.config = config
        self.store = store
        self.specs = {p["name"]: ProviderSpec.from_dict(p) for p in llm.get("providers") or []}
        self.tasks: dict[str, list[str]] = llm.get("tasks") or {}
        self.margin = float(llm.get("safety_margin", 0.9))
        self.daily_cap = int(llm.get("daily_request_cap", 700))
        self.state = {name: _State() for name in self.specs}
        self.stats = LLMStats()
        self.enabled = enabled
        self._clock = clock
        self._sleep = sleep
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15), transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------------------
    def _today(self) -> str:
        return utcnow().date().isoformat()

    def _key(self, spec: ProviderSpec) -> str | None:
        return self.config.env(spec.api_key_env)

    def providers_for(self, task: str) -> list[ProviderSpec]:
        names = self.tasks.get(task) or list(self.specs)
        return [self.specs[n] for n in names if n in self.specs]

    def usable_providers(self, task: str) -> list[str]:
        return [s.name for s in self.providers_for(task) if self._usable(s, 0) is None]

    def available(self, task: str) -> bool:
        if not self.enabled:
            return False
        if self.store.llm_requests_total(self._today()) >= self.daily_cap:
            return False
        return any(self._usable(spec, 0) is None for spec in self.providers_for(task))

    def _usable(self, spec: ProviderSpec, est_tokens: int) -> str | None:
        """Return a reason the provider can't take a request right now, or None."""
        st = self.state[spec.name]
        if st.exhausted:
            return st.exhausted
        if not self._key(spec):
            return f"no {spec.api_key_env}"
        reqs, toks = self.store.llm_usage_today(spec.name, self._today())
        if reqs + 1 > spec.rpd * self.margin:
            return "daily request budget used"
        if toks + est_tokens > spec.tpd * self.margin:
            return "daily token budget used"
        return None

    def _window_wait(self, spec: ProviderSpec, est_tokens: int) -> float:
        """Seconds to wait until the 60s window has room for this request."""
        st = self.state[spec.name]
        now = self._clock()
        while st.window and now - st.window[0][0] >= 60:
            st.window.popleft()
        max_req = max(1, int(spec.rpm * self.margin))
        max_tok = max(est_tokens, int(spec.tpm * self.margin))
        used_tok = sum(t for _, t in st.window)
        if len(st.window) < max_req and used_tok + est_tokens <= max_tok:
            return 0.0
        # wait until enough old entries expire
        need_tok = used_tok + est_tokens - max_tok
        freed = 0
        for i, (ts, tok) in enumerate(st.window):
            freed += tok
            if (len(st.window) - (i + 1)) < max_req and freed >= need_tok:
                return max(0.0, 60 - (now - ts)) + 0.25
        return 60.0

    # ------------------------------------------------------------------------------
    async def complete_json(
        self,
        task: str,
        system: str,
        user: str,
        max_tokens: int = 1500,
        use_cache: bool = True,
        prefer: str | None = None,
    ) -> LLMResult | None:
        """Return parsed JSON from the first provider that succeeds, or None."""
        if not self.enabled:
            return None
        cache_key = hashlib.sha256(f"{task}\x1f{system}\x1f{user}".encode()).hexdigest()
        if use_cache:
            hit = self.store.cache_get(cache_key)
            if hit is not None:
                self.stats.cache_hits += 1
                return LLMResult(json.loads(hit), provider="cache", cached=True)

        if self.store.llm_requests_total(self._today()) >= self.daily_cap:
            log.info("LLM daily cap reached (%s requests)", self.daily_cap)
            return None

        est = estimate_tokens(system + user) + int(max_tokens * 0.5)
        specs = self.providers_for(task)
        if prefer:  # let parallel workers each start on a different provider
            specs = sorted(specs, key=lambda s: s.name != prefer)
        for spec in specs:
            reason = self._usable(spec, est)
            if reason:
                self.stats.exhausted.setdefault(spec.name, reason)
                continue
            try:
                result = await self._call_provider(spec, system, user, max_tokens, est)
            except LLMUnavailable as exc:
                self.stats.exhausted[spec.name] = str(exc)
                continue
            if result is not None:
                self.store.cache_set(
                    cache_key, spec.name, json.dumps(result.data, ensure_ascii=False)
                )
                return result
        self.stats.failures += 1
        return None

    async def _call_provider(
        self, spec: ProviderSpec, system: str, user: str, max_tokens: int, est: int
    ) -> LLMResult | None:
        st = self.state[spec.name]
        attempts = 0
        while attempts < 3:
            attempts += 1
            wait = self._window_wait(spec, est)
            if wait > self.MAX_WAIT:
                raise LLMUnavailable("minute window saturated")
            if wait > 0:
                await self._sleep(wait)

            body: dict[str, Any] = {
                "model": spec.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
                "max_tokens": max_tokens,
                **spec.params,
            }
            if st.json_mode:
                body["response_format"] = {"type": "json_object"}

            entry = [self._clock(), est]
            st.window.append(entry)
            self.stats.requests += 1
            self.stats.by_provider[spec.name] = self.stats.by_provider.get(spec.name, 0) + 1
            try:
                resp = await self._client.post(
                    f"{spec.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._key(spec)}"},
                    json=body,
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                log.warning("%s transport error: %s", spec.name, exc)
                self.store.add_llm_usage(spec.name, self._today(), 1, 0)
                self.stats.retries += 1
                await self._sleep(2.0 * attempts)
                continue

            self._absorb_headers(spec, resp)
            status = resp.status_code
            text = resp.text[:500]

            if status == 200:
                payload = resp.json()
                usage = payload.get("usage") or {}
                tokens = int(usage.get("total_tokens") or est)
                self.store.add_llm_usage(spec.name, self._today(), 1, tokens)
                self.stats.tokens += tokens
                entry[1] = tokens  # replace the estimate with the real token count
                content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
                try:
                    return LLMResult(parse_json_loose(content), provider=spec.name, tokens=tokens)
                except (ValueError, json.JSONDecodeError):
                    log.warning("%s returned non-JSON content; retrying", spec.name)
                    self.stats.retries += 1
                    continue

            # count failed calls too: they consume the provider's request quota
            self.store.add_llm_usage(spec.name, self._today(), 1, 0)
            if status == 429:
                retry = parse_duration(resp.headers.get("retry-after")) or 10.0
                per_day = (
                    "per day" in text.lower() or "(rpd)" in text.lower() or "(tpd)" in text.lower()
                )
                if per_day or retry > self.MAX_WAIT:
                    raise LLMUnavailable(f"rate limited for {retry:.0f}s")
                self.stats.retries += 1
                await self._sleep(retry + 0.5)
                continue
            if status in (401, 403):
                raise LLMUnavailable(f"auth failed (HTTP {status}) — check {spec.api_key_env}")
            if status == 413:
                raise LLMUnavailable("request too large for this provider's limits")
            if status == 400 and "response_format" in text and st.json_mode:
                st.json_mode = False
                continue
            if status == 400 and "json_validate_failed" in text:
                self.stats.retries += 1
                continue
            if status >= 500:
                self.stats.retries += 1
                await self._sleep(3.0 * attempts)
                continue
            log.warning("%s HTTP %s: %s", spec.name, status, text)
            return None
        return None

    def _absorb_headers(self, spec: ProviderSpec, resp: httpx.Response) -> None:
        """Use Groq's x-ratelimit-* headers to avoid hitting walls."""
        st = self.state[spec.name]
        remaining_req = resp.headers.get("x-ratelimit-remaining-requests")
        if remaining_req is not None and remaining_req.isdigit() and int(remaining_req) <= 1:
            st.exhausted = "provider reports daily requests exhausted"
        remaining_tok = resp.headers.get("x-ratelimit-remaining-tokens")
        reset_tok = parse_duration(resp.headers.get("x-ratelimit-reset-tokens"))
        if remaining_tok is not None and remaining_tok.isdigit() and reset_tok:
            # Make the local window reflect the server's view of the minute budget.
            used_estimate = max(0, int(spec.tpm * self.margin) - int(remaining_tok))
            local_used = sum(t for _, t in st.window)
            if used_estimate > local_used:
                st.window.appendleft(
                    [self._clock() - max(0.0, 60 - reset_tok), used_estimate - local_used]
                )
