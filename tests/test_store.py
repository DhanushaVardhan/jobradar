from datetime import timedelta

from jobradar.models import Job, TargetResult
from jobradar.normalize import enrich_location, iso, utcnow
from jobradar.sources import SOURCE_PRIORITY
from jobradar.store import Store


def mk(
    sid,
    source="greenhouse",
    target="greenhouse:acme",
    title="Data Engineer",
    company="Acme",
    loc="Bengaluru",
    desc="Python",
):
    return enrich_location(
        Job(
            source,
            sid,
            target,
            company,
            title,
            f"https://x/{sid}",
            location_raw=loc,
            description=desc,
        )
    )


def test_upsert_new_unchanged_updated(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    assert st.upsert_jobs([mk("1"), mk("2")])["new"] == 2
    st.save_enrichment(mk("1").uid, ["Python"], {"seniority": "mid"}, "groq-oss-20b")
    counts = st.upsert_jobs([mk("1"), mk("2", desc="Python and SQL")])
    assert counts == {"new": 0, "updated": 1, "unchanged": 1, "reopened": 0}
    j2 = st.get_job(mk("2").uid)
    assert (
        j2["description"] == "Python and SQL" and j2["enriched_at"] is None
    )  # re-enrich changed jobs
    assert st.get_job(mk("1").uid)["enriched_by"] == "groq-oss-20b"  # untouched


def test_empty_description_never_overwrites(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    st.upsert_jobs([mk("1", desc="Full description")])
    st.upsert_jobs([mk("1", desc="")])
    assert st.get_job(mk("1").uid)["description"] == "Full description"


def test_close_missing_only_for_complete_results(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    st.upsert_jobs([mk("1"), mk("2")])
    partial = TargetResult("greenhouse:acme", "greenhouse", jobs=[mk("1")], complete=False)
    assert st.close_missing(partial) == 0
    failed = TargetResult("greenhouse:acme", "greenhouse", jobs=[], ok=False, complete=True)
    assert st.close_missing(failed) == 0
    full = TargetResult("greenhouse:acme", "greenhouse", jobs=[mk("1")], complete=True)
    assert st.close_missing(full) == 1
    assert [j["source_id"] for j in st.open_jobs()] == ["1"]
    # reappearing job is reopened
    assert st.upsert_jobs([mk("2")])["reopened"] == 1
    assert len(st.open_jobs()) == 2


def test_close_stale_and_prune(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    old = iso(utcnow() - timedelta(days=10))
    st.upsert_jobs([mk("old", source="remotive", target="remotive")], now=old)
    st.upsert_jobs([mk("fresh", source="remotive", target="remotive")])
    assert st.close_stale(5) == 1
    assert (
        st.prune(
            delete_closed_after_days=0, delete_after_days=60, now=utcnow() + timedelta(seconds=5)
        )
        == 1
    )
    assert [j["source_id"] for j in st.open_jobs()] == ["fresh"]


def test_duplicates_prefer_company_board(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    board = mk("gh1", title="Senior Data Engineer", company="Hevo Data")
    agg = mk(
        "adz1",
        source="adzuna",
        target="adzuna",
        title="Sr. Data Engineer",
        company="Hevo Data Pvt Ltd",
        loc="Bangalore, Karnataka",
    )
    st.upsert_jobs([agg])
    st.upsert_jobs([board])
    assert st.mark_duplicates(SOURCE_PRIORITY) == 1
    visible = st.open_jobs()
    assert [j["source"] for j in visible] == ["greenhouse"]
    assert len(st.open_jobs(include_duplicates=True)) == 2


def test_health_tracks_consecutive_failures(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    bad = TargetResult("lever:x", "lever", ok=False, error="HTTP 404")
    st.record_health(bad)
    st.record_health(bad)
    assert st.health()[0]["consecutive_failures"] == 2
    st.record_health(TargetResult("lever:x", "lever", jobs=[mk("1")]))
    h = st.health()[0]
    assert h["consecutive_failures"] == 0 and h["last_count"] == 1 and h["last_error"] is None


def test_llm_usage_and_cache(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    st.add_llm_usage("groq", "2026-09-24", 1, 500)
    st.add_llm_usage("groq", "2026-09-24", 2, 700)
    st.add_llm_usage("gemini", "2026-09-24", 1, 100)
    assert st.llm_usage_today("groq", "2026-09-24") == (3, 1200)
    assert st.llm_requests_total("2026-09-24") == 4
    st.cache_set("k", "groq", '{"a": 1}')
    assert st.cache_get("k") == '{"a": 1}' and st.cache_get("nope") is None
