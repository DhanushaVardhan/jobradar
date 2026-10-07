from jobradar.models import Job
from jobradar.normalize import (
    enrich_location,
    fingerprint,
    html_to_text,
    is_relevant,
    location_label,
    parse_date,
    requirements_excerpt,
    tidy_title,
)

KEEP = ["india", "remote_india", "remote_global", "remote_apac"]


def job(
    location="", remote=None, country=None, source="greenhouse", company="Acme", title="Engineer"
):
    return enrich_location(
        Job(
            source,
            "1",
            f"{source}:x",
            company,
            title,
            "https://x",
            location_raw=location,
            remote_hint=remote,
            country_hint=country,
        )
    )


def test_html_to_text_handles_greenhouse_double_encoding():
    raw = "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;"
    text = html_to_text(raw)
    assert "Hello & welcome" in text
    assert "- Python" in text and "- SQL" in text
    assert "<" not in text


def test_html_to_text_plain_and_empty():
    assert html_to_text(None) == ""
    assert html_to_text("<script>alert(1)</script><b>Bold</b>&nbsp;text") == "Bold text"


def test_indian_locations_detected():
    cases = {
        "Gurugram | Gurugram, Haryana, India": (["Gurugram"], True),
        "Bangalore, Karnataka": (["Bengaluru"], True),
        "Hybrid in Bangalore, India": (["Bengaluru"], True),
        "Bengaluru-VTP, India": (["Bengaluru"], True),
        "tamil nadu": ([], True),
        "Vizag": (["Visakhapatnam"], True),
        "New York, NY, United States": ([], False),
    }
    for raw, (cities, india) in cases.items():
        j = job(raw)
        assert j.cities == cities, raw
        assert j.is_india is india, raw


def test_remote_scopes_and_relevance():
    assert job("Remote - India").remote_scope == "india"
    assert job("Worldwide", remote=True).remote_scope == "global"
    assert job("APAC", remote=True).remote_scope == "apac"
    us = job("USA Only", remote=True)
    assert us.remote_scope == "other" and not is_relevant(us, KEEP)
    assert is_relevant(job("Pune, Maharashtra"), KEEP)
    assert not is_relevant(job("London"), KEEP)
    assert is_relevant(job("", country="IN"), KEEP)


def test_location_label():
    assert location_label(job("Remote - India")) == "Remote (India)"
    assert (
        location_label(job("Bengaluru; Mumbai; Pune; Hyderabad")) == "Bengaluru, Mumbai, Pune, +1"
    )


def test_parse_date_formats():
    assert parse_date(1757916149833) == "2025-09-15T06:02:29Z"  # epoch ms (Lever)
    assert parse_date(1789977975) == parse_date("1789977975")  # epoch s (Himalayas)
    assert parse_date("2026-09-21T08:11:30-04:00") == "2026-09-21T12:11:30Z"
    assert parse_date("2024-01-01 10:00:00") == "2024-01-01T10:00:00Z"  # Jobicy
    assert parse_date("garbage") is None and parse_date(None) is None


def test_requirements_excerpt_prefers_requirements():
    text = "About us. " * 200 + "\nRequirements:\n- 3+ years Python\n- SQL"
    ex = requirements_excerpt("Data Engineer", text, 600)
    assert ex.startswith("Data Engineer")
    assert "3+ years Python" in ex
    assert len(ex) < 700


def test_fingerprint_matches_same_job_across_sources():
    a = job("Bangalore", company="Hevo Data Pvt Ltd", title="Sr. Software Engineer", source="lever")
    b = job(
        "Bengaluru, India", company="Hevo Data", title="Senior Software Engineer", source="adzuna"
    )
    c = job("Pune", company="Hevo Data", title="Senior Software Engineer")
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(c)


def test_tidy_title():
    assert (
        tidy_title("area collections manager hyderabad -flows")
        == "Area Collections Manager Hyderabad -Flows"
    )
    assert tidy_title("sde  ii - backend") == "SDE II - Backend"
    assert tidy_title("Senior ML Engineer") == "Senior ML Engineer"
