"""End-to-end: recorded + synthetic sources -> store -> fake LLM -> matching -> site -> fake Telegram."""

import asyncio
import json
import re
import xml.etree.ElementTree as ET

from helpers import FIXTURES, FakeLLM, FakeTelegram, make_config, sample_profile, synthetic_payloads

from jobradar.offline import fixture_transport
from jobradar.pipeline import Transports, run, summary_markdown
from jobradar.store import Store

SECRETS = {
    "GROQ_API_KEY": "gsk",
    "TELEGRAM_BOT_TOKEN": "1:abc",
    "TELEGRAM_CHAT_ID": "100",
    "TELEGRAM_CHANNEL_ID": "@jobradar_test",
    "ADZUNA_APP_ID": "id",
    "ADZUNA_APP_KEY": "key",
}
COMPANIES = {
    "greenhouse": [
        {"slug": "mongodb", "name": "MongoDB"},
        {"slug": "hackerrank", "name": "HackerRank"},
    ],
    "lever": [{"slug": "meesho", "name": "Meesho"}, {"slug": "hevodata", "name": "Hevo Data"}],
    "ashby": [{"slug": "atlan", "name": "Atlan"}],
    "smartrecruiters": [{"slug": "ExampleCorp", "name": "Example Corp"}],
}


def setup(tmp_path):
    cfg = make_config(tmp_path, secrets=SECRETS, profile=sample_profile())
    cfg.companies = COMPANIES
    cfg.settings["sources"]["adzuna"]["queries"] = ["python"]
    return cfg


def go(cfg, overrides=None, llm=None, tg=None, **kw):
    payloads = synthetic_payloads() | (overrides or {})
    t = Transports(
        sources=fixture_transport([FIXTURES], overrides=payloads),
        llm=(llm or FakeLLM()).transport(),
        telegram=(tg or FakeTelegram()).transport(),
    )
    return asyncio.run(run(cfg, transports=t, **kw))


def test_full_pipeline_run_and_idempotent_rerun(tmp_path):
    cfg = setup(tmp_path)
    llm, tg = FakeLLM(), FakeTelegram()
    stats = go(cfg, llm=llm, tg=tg, force_digest=True)

    # --- ingest
    assert stats["new"] >= 20
    failed = [t for t in stats["targets"] if not t["ok"]]
    assert failed == []
    assert stats["sources_skipped"] == {}
    # US-only remote job and stale Himalayas job were filtered out
    store = Store(cfg.db_path)
    titles = {j["title"] for j in store.open_jobs()}
    assert "Old Posting" not in titles and "Enterprise Account Executive - West" not in titles
    assert {"Backend Engineer (Python)", "Python Developer", "QA Automation Engineer"} <= titles

    # --- enrichment: every job has rules, LLM upgraded recent ones in batches
    assert stats["enrich"]["rules"] == stats["new"]
    assert stats["enrich"]["llm"] > 0 and stats["llm"]["requests"] >= stats["enrich"]["llm_batches"]
    ai_jobs = [j for j in store.open_jobs() if j["enriched_by"] != "rules"]
    assert ai_jobs and all(j["enrichment"]["summary"] for j in ai_jobs)

    # --- matching + digest + channel
    assert stats["match"]["candidates"] > 0 and stats["match"]["ai_scored"] > 0
    assert stats["notify"]["digest"] > 0 and stats["notify"]["channel"] > 0
    owner_msgs = [m for m in tg.messages if m["chat_id"] == "100"]
    channel_msgs = [m for m in tg.messages if m["chat_id"] == "@jobradar_test"]
    assert owner_msgs and "new matches for Asha" in owner_msgs[0]["text"]
    assert channel_msgs and "#" in channel_msgs[0]["text"]

    # --- site output
    site = cfg.site_dir
    jobs = json.loads((site / "data/jobs.json").read_text())
    stats_json = json.loads((site / "data/stats.json").read_text())
    tax = json.loads((site / "data/taxonomy.json").read_text())
    assert len(jobs) == stats["open_jobs"] and any(j["ai"] for j in jobs)
    assert {"id", "title", "company", "url", "skills", "must", "exp", "fresher", "location"} <= set(
        jobs[0]
    )
    assert stats_json["totals"]["open"] == len(jobs) and stats_json["runs"][0]["status"] == "ok"
    assert "Bengaluru" in tax["cities"] and tax["patterns"]
    assert (site / "index.html").exists() and (site / "app.js").exists()
    feeds = json.loads((site / "feeds/index.json").read_text())
    for f in feeds:
        root = ET.parse(site / f["path"]).getroot()  # every feed is valid XML
        assert root.tag == "rss"
    assert "## JobRadar run" in summary_markdown(stats)
    store.close()

    # --- second run: nothing new; later digests only carry jobs not sent before
    tg2 = FakeTelegram()
    stats2 = go(cfg, llm=FakeLLM(), tg=tg2, force_digest=True)
    assert stats2["new"] == 0 and stats2["enrich"]["rules"] == 0

    def links(msgs, chat):
        return set(
            re.findall(r'href="([^"]+)"', " ".join(m["text"] for m in msgs if m["chat_id"] == chat))
        )

    for chat in ("100", "@jobradar_test"):  # the owner and the channel never get the same job twice
        assert (links(tg.messages, chat) & links(tg2.messages, chat)) - {cfg.site_url} == set()
    # after enough runs everything relevant has been delivered exactly once
    for _ in range(4):
        go(cfg, force_digest=True)
    tg_last = FakeTelegram()
    last = go(cfg, tg=tg_last, force_digest=True)
    assert (
        last["notify"]["digest"] == 0 and last["notify"]["channel"] == 0 and tg_last.messages == []
    )


def test_closed_jobs_disappear(tmp_path):
    cfg = setup(tmp_path)
    go(cfg, notify=False)
    board = json.loads((FIXTURES / "greenhouse_hackerrank.json").read_text())
    board["jobs"] = board["jobs"][1:]  # one HackerRank posting was taken down
    stats = go(cfg, overrides={"greenhouse_hackerrank": board}, notify=False)
    assert stats["closed"] == 1
    ids = {j["id"] for j in json.loads((cfg.site_dir / "data/jobs.json").read_text())}
    store = Store(cfg.db_path)
    closed = [
        j for j in store.open_jobs(include_duplicates=True) if j["title"] == "Applied AI Engineer"
    ]
    assert closed == [] and len(ids) == stats["open_jobs"]
    store.close()


def test_runs_without_any_keys(tmp_path):
    cfg = make_config(tmp_path, secrets={})
    cfg.companies = COMPANIES
    stats = go(cfg)
    assert stats["new"] > 0
    assert stats["enrich"]["llm"] == 0 and stats["enrich"]["llm_skipped_reason"]
    assert stats["notify"] == {"skipped": "no TELEGRAM_BOT_TOKEN"}
    assert stats["match"]["skipped"]
    assert stats["sources_skipped"] == {"adzuna": "missing env: ADZUNA_APP_ID, ADZUNA_APP_KEY"}
    assert (cfg.site_dir / "data/jobs.json").exists()
