import asyncio

import yaml
from helpers import FakeLLM, make_config

from jobradar.config import CONFIG_DIR
from jobradar.enrich import llm_enrich_batch, rule_enrich, rule_experience, validate_llm_item
from jobradar.llm import LLMRouter
from jobradar.skills import SkillTaxonomy
from jobradar.store import Store

TAX = SkillTaxonomy(yaml.safe_load((CONFIG_DIR / "skills.yaml").read_text()))


def jobdict(title, desc, company="Acme", **kw):
    return {
        "uid": kw.pop("uid", "u1"),
        "title": title,
        "company": company,
        "description": desc,
        "location_raw": kw.pop("loc", "Bengaluru"),
        "cities": ["Bengaluru"],
        "is_remote": False,
        **kw,
    }


def test_experience_patterns():
    assert rule_experience("3-6  years of relevant Fin-Tech experience") == (3, 6)
    assert rule_experience("Up to 3 years relevant experience") == (0, 3)
    assert rule_experience("1-2.5 years lender partnership") == (1, 3)
    assert rule_experience("Requirements: 3+ years DevOps") == (3, None)
    assert rule_experience("We have 5 offices, founded 3 years ago") == (None, None)


def test_rule_enrich_fresher_and_role():
    skills, e = rule_enrich(
        jobdict(
            "Assistant Manager - Business Finance",
            "Strong analytics. Qualified CA Fresher from 2025/2026 batch.",
        ),
        TAX,
    )
    assert e["fresher_friendly"] is True and e["role_category"] == "finance"

    skills, e = rule_enrich(
        jobdict(
            "Data Engineer II", "Requirements: 2-4 years data engineering. Spark, AWS, Airflow."
        ),
        TAX,
    )
    assert skills == ["Apache Spark", "AWS", "Airflow"]
    assert (e["experience_min"], e["experience_max"]) == (2, 4)
    assert (
        e["role_category"] == "data" and e["fresher_friendly"] is False and e["seniority"] == "mid"
    )

    _, e = rule_enrich(jobdict("Software Engineering Intern", "Python basics"), TAX)
    assert e["seniority"] == "intern" and e["fresher_friendly"] is True

    _, e = rule_enrich(
        jobdict("Associate Technical Support Engineer", "Experience: 3 to 6 years."), TAX
    )
    assert e["seniority"] == "mid"  # "associate" but asks for 3+ years


def test_validate_llm_item_coerces_and_falls_back():
    _, base = rule_enrich(jobdict("Backend Engineer", "Python, SQL"), TAX)
    skills, out = validate_llm_item(
        {
            "summary": "Build payment APIs " * 10,
            "seniority": "galactic",  # invalid -> keep rule value
            "experience_min": "3",
            "experience_max": 1,  # max < min -> dropped
            "fresher_friendly": False,
            "role_category": "backend",
            "must_have": ["python", "Go lang", "k8s", "Communication"],
            "nice_to_have": ["Python", "Kafka"],
            "work_mode": "hybrid",
            "salary": {"min": "1500000", "max": None, "currency": "inr", "period": "yearly"},
        },
        base,
        TAX,
    )
    assert len(out["summary"].split()) == 30
    assert out["seniority"] == base["seniority"]
    assert (out["experience_min"], out["experience_max"]) == (3, None)
    assert out["must_have"] == ["Python", "Go", "Kubernetes"]
    assert out["nice_to_have"] == ["Kafka"]
    assert "Communication" in out["other_skills"]
    assert out["salary"] == {"min": 1500000.0, "max": None, "currency": "INR", "period": None}
    assert skills[:3] == ["Python", "Go", "Kubernetes"]


def test_llm_enrich_batch_end_to_end(tmp_path):
    cfg = make_config(tmp_path, secrets={"GROQ_API_KEY": "k"})
    store = Store(tmp_path / "db.sqlite")
    fake = FakeLLM()
    router = LLMRouter(cfg, store, transport=fake.transport())
    jobs = [
        jobdict("Data Engineer", "Python, SQL and AWS", uid="a"),
        jobdict("DevOps Intern", "Kubernetes. Freshers welcome", uid="b"),
    ]
    bases = {j["uid"]: rule_enrich(j, TAX)[1] for j in jobs}

    async def go():
        try:
            return await llm_enrich_batch(jobs, bases, router, TAX)
        finally:
            await router.aclose()

    out, provider = asyncio.run(go())
    assert provider == "groq-oss-20b" and set(out) == {"a", "b"}
    skills_a, enr_a = out["a"]
    assert enr_a["summary"].startswith("Work on") and enr_a["experience_max"] == 5
    assert {"Python", "SQL", "AWS"} <= set(skills_a)
    assert out["b"][1]["fresher_friendly"] is True
    assert len(fake.calls) == 1  # both jobs in ONE request
