import asyncio

import httpx
from helpers import FakeLLM, make_config

from jobradar.llm import LLMRouter, parse_duration, parse_json_loose
from jobradar.store import Store

KEYS = {"GROQ_API_KEY": "gsk_test", "GEMINI_API_KEY": "gem_test"}


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(s)
        self.t += s


def router(tmp_path, fake, secrets=KEYS, **llm):
    cfg = make_config(tmp_path, secrets=secrets, llm=llm)
    clock = Clock()
    r = LLMRouter(
        cfg,
        Store(tmp_path / "db.sqlite"),
        transport=fake.transport(),
        clock=clock,
        sleep=clock.sleep,
    )
    return r, clock


def ask(r, user="Parse these postings:\n[]", task="enrich", **kw):
    async def go():
        try:
            return await r.complete_json(task, "You are a precise job-posting parser", user, **kw)
        finally:
            await r.aclose()

    return asyncio.run(go())


def test_parse_duration():
    assert parse_duration("7.66s") == 7.66
    assert abs(parse_duration("2m59.56s") - 179.56) < 1e-9
    assert parse_duration("1h2m3s") == 3723
    assert parse_duration("120ms") == 0.12
    assert parse_duration("12") == 12
    assert parse_duration("soon") is None and parse_duration(None) is None


def test_parse_json_loose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('<think>hmm</think>{"a": [1, 2]}') == {"a": [1, 2]}
    assert parse_json_loose('Here you go: {"jobs": []} Thanks!') == {"jobs": []}


def test_success_records_usage_and_caches(tmp_path):
    fake = FakeLLM()
    r, _ = router(tmp_path, fake)
    res = ask(r)
    assert res.data == {"jobs": []} and res.provider == "groq-oss-20b"
    assert fake.calls[0]["auth"] == "Bearer gsk_test"
    assert r.store.llm_requests_total(r._today()) == 1
    # identical prompt -> served from cache, no new HTTP call
    r2 = LLMRouter(r.config, r.store, transport=fake.transport())
    res2 = ask(r2)
    assert res2.cached and len(fake.calls) == 1


def test_429_waits_then_retries(tmp_path):
    fake = FakeLLM(script=[429])
    r, clock = router(tmp_path, fake)
    res = ask(r)
    assert res is not None and res.provider == "groq-oss-20b"
    assert any(s >= 1 for s in clock.slept)
    assert r.stats.retries == 1


def test_failover_on_auth_error_and_daily_limit(tmp_path):
    fake = FakeLLM(script=[401, "day-limit"])
    r, _ = router(tmp_path, fake)
    res = ask(r)
    # groq-oss-20b: 401 -> out; groq-qwen: per-day 429 -> out; gemini answers
    assert res.provider == "gemini-flash-lite"
    assert "auth failed" in r.stats.exhausted["groq-oss-20b"]
    assert "rate limited" in r.stats.exhausted["groq-qwen"]
    assert fake.calls[-1]["url"].startswith("https://generativelanguage.googleapis.com/")


def test_bad_json_is_retried(tmp_path):
    fake = FakeLLM(script=["bad-json"])
    r, _ = router(tmp_path, fake)
    assert ask(r).data == {"jobs": []}
    assert len(fake.calls) == 2


def test_no_keys_means_unavailable(tmp_path):
    fake = FakeLLM()
    r, _ = router(tmp_path, fake, secrets={})
    assert not r.available("enrich")
    assert ask(r) is None and fake.calls == []


def test_daily_cap_is_respected(tmp_path):
    fake = FakeLLM()
    r, _ = router(tmp_path, fake, daily_request_cap=2)
    r.store.add_llm_usage("groq-oss-20b", r._today(), 2, 10)
    assert not r.available("enrich")
    assert ask(r, user="Parse these postings:\n[1]") is None


def test_per_provider_daily_budget_moves_to_next_provider(tmp_path):
    fake = FakeLLM()
    r, _ = router(tmp_path, fake, daily_request_cap=5000)
    r.store.add_llm_usage("groq-oss-20b", r._today(), 950, 1000)  # > 90% of 1000 RPD
    res = ask(r)
    assert res.provider == "groq-qwen"


def test_minute_window_throttles(tmp_path):
    fake = FakeLLM()
    r, clock = router(tmp_path, fake)
    spec = r.specs["groq-oss-20b"]
    # fill the minute window with 7,000 tokens so the next request must wait
    r.state[spec.name].window.append([clock(), 7000])
    assert r._window_wait(spec, 1500) > 50
    clock.t += 61
    assert r._window_wait(spec, 1500) == 0


def test_response_format_rejection_disables_json_mode(tmp_path):
    calls = []

    def handler(request):
        import json

        body = json.loads(request.content)
        calls.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(
                400, json={"error": {"message": "response_format is not supported"}}
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"total_tokens": 10},
            },
        )

    cfg = make_config(tmp_path, secrets=KEYS)
    r = LLMRouter(cfg, Store(tmp_path / "db.sqlite"), transport=httpx.MockTransport(handler))
    res = ask(r)
    assert res.data == {"ok": True} and calls == [True, False]
