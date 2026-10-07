import asyncio

import yaml
from helpers import FakeLLM, iso_ago, make_config, sample_profile

from jobradar.config import CONFIG_DIR
from jobradar.llm import LLMRouter
from jobradar.match import ai_rerank, combined_score, profile_skills, rule_score, title_tokens
from jobradar.skills import SkillTaxonomy
from jobradar.store import Store

TAX = SkillTaxonomy(yaml.safe_load((CONFIG_DIR / "skills.yaml").read_text()))


def job(
    title,
    must,
    nice=(),
    exp=(None, None),
    cities=("Bengaluru",),
    remote=False,
    fresher=False,
    seniority="mid",
    age_h=5,
):
    return {
        "uid": title,
        "title": title,
        "company": "Acme",
        "description": "",
        "location_raw": "",
        "skills": list(must),
        "cities": list(cities),
        "is_remote": remote,
        "is_india": True,
        "posted_at": iso_ago(hours=age_h),
        "first_seen": iso_ago(hours=age_h),
        "enrichment": {
            "must_have": list(must),
            "nice_to_have": list(nice),
            "experience_min": exp[0],
            "experience_max": exp[1],
            "fresher_friendly": fresher,
            "seniority": seniority,
        },
    }


def test_title_tokens_synonyms():
    assert {"machine", "learning", "engineer"} <= title_tokens("Senior ML Developer")
    assert "software" in title_tokens("SDE II")


def test_profile_skills_merges_list_and_resume():
    skills = profile_skills(sample_profile(), TAX)
    assert skills[:3] == ["Python", "SQL", "Kubernetes"]
    assert {"PyTorch", "Apache Spark", "AWS", "LLMs", "LangChain", "RAG"} <= set(skills)


def test_rule_score_ranks_sensibly():
    p = sample_profile()
    us = profile_skills(p, TAX)
    great = rule_score(
        job("Machine Learning Engineer", ["Python", "PyTorch", "AWS"], exp=(0, 2)), p, us
    )
    ok = rule_score(job("Backend Engineer", ["Java", "Python"], exp=(2, 4)), p, us)
    senior = rule_score(job("Principal Architect", ["Python"], seniority="lead"), p, us)
    far = rule_score(job("Data Engineer", ["Python", "SQL"], cities=("Pune",)), p, us)
    assert great.score > ok.score > senior.score
    assert great.matched == ["Python", "PyTorch", "AWS"] and great.missing == []
    assert ok.missing == ["Java"]
    assert far.parts["location"] == 0.5
    assert 0 <= senior.score <= 100


def test_excluded_titles_score_zero():
    p = sample_profile()
    assert (
        rule_score(job("Inside Sales Associate", ["Python"]), p, profile_skills(p, TAX)).score == 0
    )


def test_remote_and_fresher_rules():
    p = sample_profile()
    us = profile_skills(p, TAX)
    r = rule_score(
        job("Data Engineer", ["Python"], cities=(), remote=True, exp=(3, None), fresher=True), p, us
    )
    assert r.parts["location"] == 1.0 and r.parts["experience"] == 1.0


def test_ai_rerank_with_fake_llm(tmp_path):
    cfg = make_config(tmp_path, secrets={"GROQ_API_KEY": "k"})
    router = LLMRouter(cfg, Store(tmp_path / "db.sqlite"), transport=FakeLLM().transport())
    jobs = [job(f"ML Engineer {i}", ["Python"]) for i in range(4)]

    async def go():
        try:
            return await ai_rerank(jobs, sample_profile(), router, batch_size=3)
        finally:
            await router.aclose()

    out = asyncio.run(go())
    assert set(out) == {j["uid"] for j in jobs}
    assert out["ML Engineer 0"]["fit"] == 90 and out["ML Engineer 0"]["tip"]
    assert out["ML Engineer 0"]["provider"] == "groq-oss-120b"  # match task prefers the big model
    assert combined_score(50, 90) == 74.0 and combined_score(50, None) == 50
