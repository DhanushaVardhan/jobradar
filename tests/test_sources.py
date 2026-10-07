import asyncio

import httpx
from helpers import FIXTURES, make_config, synthetic_payloads

from jobradar.http import Http
from jobradar.offline import fixture_transport
from jobradar.sources.aggregators import Adzuna, Himalayas, Jobicy, RemoteOK, Remotive
from jobradar.sources.ats import Ashby, Greenhouse, Lever, SmartRecruiters


def run_source(cls, cfg, transport, **kw):
    async def go():
        async with Http("test-agent", transport=transport, retries=0) as http:
            return await cls(http, cfg, **kw).fetch()

    return asyncio.run(go())


def test_greenhouse_parses_recorded_board(tmp_path):
    cfg = make_config(tmp_path)
    cfg.companies = {"greenhouse": [{"slug": "mongodb", "name": "MongoDB"}]}
    [res] = run_source(Greenhouse, cfg, fixture_transport([FIXTURES]))
    assert res.ok and res.complete and res.target == "greenhouse:mongodb"
    ard = next(j for j in res.jobs if j.source_id == "7318558")
    assert ard.title == "Account Development Representative"
    assert "Gurugram, Haryana, India" in ard.location_raw
    assert ard.company == "MongoDB"
    assert "MEDDIC" in ard.description and "<p>" not in ard.description
    assert ard.posted_at == "2026-07-27T11:46:53Z"


def test_lever_parses_lists_and_country(tmp_path):
    cfg = make_config(tmp_path)
    cfg.companies = {"lever": [{"slug": "meesho", "name": "Meesho"}]}
    [res] = run_source(Lever, cfg, fixture_transport([FIXTURES]))
    assert len(res.jobs) == 2
    fin = next(j for j in res.jobs if "Business Finance" in j.title)
    assert fin.country_hint == "IN"
    assert "Qualified CA Fresher" in fin.description
    assert fin.url.startswith("https://jobs.lever.co/meesho/")


def test_ashby_skips_unlisted_and_adds_country(tmp_path):
    cfg = make_config(tmp_path)
    cfg.companies = {"ashby": [{"slug": "atlan", "name": "Atlan"}]}
    [res] = run_source(Ashby, cfg, fixture_transport([FIXTURES]))
    assert len(res.jobs) == 2
    sec = res.jobs[0]
    assert sec.remote_hint is True and "India" in sec.location_raw


def test_http_errors_become_failed_results(tmp_path):
    cfg = make_config(tmp_path)
    cfg.companies = {"greenhouse": [{"slug": "gone", "name": "Gone"}]}
    transport = httpx.MockTransport(lambda r: httpx.Response(404, json={"status": 404}))
    [res] = run_source(Greenhouse, cfg, transport)
    assert not res.ok and "404" in res.error and res.jobs == []


def test_malformed_payload_does_not_crash(tmp_path):
    cfg = make_config(tmp_path)
    cfg.companies = {"lever": [{"slug": "weird", "name": "Weird"}]}
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"ok": False, "error": "Document not found"})
    )
    [res] = run_source(Lever, cfg, transport)
    assert res.ok and res.jobs == []


def test_retries_then_succeeds(tmp_path):
    cfg = make_config(tmp_path)
    cfg.companies = {"ashby": [{"slug": "atlan", "name": "Atlan"}]}
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"jobs": []})

    async def go():
        async with Http(
            "t", transport=httpx.MockTransport(handler), retries=2, backoff_base=0.01
        ) as http:
            return await Ashby(http, cfg).fetch()

    [res] = asyncio.run(go())
    assert res.ok and calls["n"] == 2


def test_aggregators_parse_synthetic_payloads(tmp_path):
    cfg = make_config(tmp_path, secrets={"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key"})
    cfg.settings["sources"]["adzuna"]["queries"] = ["python"]
    t = fixture_transport([], overrides=synthetic_payloads())

    [him] = run_source(Himalayas, cfg, t)
    assert [j.title for j in him.jobs] == [
        "Backend Engineer (Python)",
        "Data Analyst",
    ]  # 20-day-old job skipped
    assert him.jobs[0].via == "Himalayas" and him.jobs[0].url.startswith("https://himalayas.app/")
    assert him.jobs[1].location_raw == "Worldwide"
    assert not him.complete

    [rem] = run_source(Remotive, cfg, t)
    assert {j.title for j in rem.jobs} == {"Frontend Developer", "Account Executive"}

    [rok] = run_source(RemoteOK, cfg, t)
    assert len(rok.jobs) == 1 and rok.jobs[0].salary_raw == "USD 40,000–60,000 / year"

    [jcy] = run_source(Jobicy, cfg, t)
    assert jcy.jobs[0].location_raw == "APAC" and jcy.jobs[0].posted_at

    [adz] = run_source(Adzuna, cfg, t)
    titles = {j.title for j in adz.jobs}
    assert "Python Developer" in titles  # <strong> tags stripped
    predicted = next(j for j in adz.jobs if j.source_id == "4002")
    assert predicted.salary_raw is None  # predicted salaries are not shown


def test_adzuna_requires_keys(tmp_path):
    cfg = make_config(tmp_path)

    async def go():
        async with Http("t", transport=fixture_transport([])) as http:
            src = Adzuna(http, cfg)
            return src.enabled(), src.skip_reason()

    enabled, reason = asyncio.run(go())
    assert not enabled and "ADZUNA_APP_ID" in reason


def test_smartrecruiters_fetches_details_for_new_jobs_only(tmp_path):
    cfg = make_config(tmp_path)
    cfg.companies = {"smartrecruiters": [{"slug": "ExampleCorp", "name": "Example Corp"}]}
    t = fixture_transport([], overrides=synthetic_payloads())
    [res] = run_source(SmartRecruiters, cfg, t)
    job = res.jobs[0]
    assert job.url == "https://jobs.smartrecruiters.com/ExampleCorp/743999"
    assert "Selenium" in job.description and res.complete

    [again] = run_source(SmartRecruiters, cfg, t, known_ids={job.uid})
    assert again.jobs[0].description == ""  # known job: no extra detail request
