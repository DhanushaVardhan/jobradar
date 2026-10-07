"""Resume-to-job matching.

`rule_score` is a transparent, explainable score (0-100) and is mirrored 1:1 in
web/match.js so visitors get the same ranking in their browser, privately.
`ai_rerank` asks an LLM to judge the best candidates against the owner's resume.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from jobradar.config import Profile
from jobradar.llm import LLMRouter
from jobradar.normalize import age_days, canonical_city, requirements_excerpt
from jobradar.skills import SkillTaxonomy

log = logging.getLogger(__name__)

WEIGHTS = {"skills": 0.45, "title": 0.25, "experience": 0.15, "location": 0.10, "recency": 0.05}

_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "for",
    "to",
    "in",
    "with",
    "at",
    "i",
    "ii",
    "iii",
    "iv",
    "senior",
    "junior",
    "sr",
    "jr",
    "lead",
    "staff",
    "principal",
    "associate",
    "intern",
    "remote",
}
_SYNONYMS = {
    "sde": ["software", "engineer"],
    "swe": ["software", "engineer"],
    "developer": ["engineer"],
    "dev": ["engineer"],
    "ml": ["machine", "learning"],
    "ai": ["artificial", "intelligence"],
    "frontend": ["front", "end"],
    "backend": ["back", "end"],
    "fullstack": ["full", "stack"],
    "programmer": ["engineer"],
    "analytics": ["analyst"],
}


def title_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for tok in re.findall(r"[a-z0-9+#.]+", text.lower()):
        tok = tok.strip(".")
        if not tok or tok in _STOP:
            continue
        out.update(_SYNONYMS.get(tok, [tok]))
    return out


@dataclass
class MatchResult:
    score: float
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    parts: dict[str, float] = field(default_factory=dict)


def profile_skills(profile: Profile, taxonomy: SkillTaxonomy) -> list[str]:
    skills, _ = taxonomy.normalize_list(profile.skills, limit=60)
    if profile.resume:
        for s in taxonomy.extract(profile.resume):
            if s not in skills:
                skills.append(s)
    return skills


def rule_score(job: dict[str, Any], profile: Profile, user_skills: list[str]) -> MatchResult:
    title = job["title"]
    if any(k.lower() in title.lower() for k in profile.exclude_keywords if k.strip()):
        return MatchResult(0.0)
    enr = job.get("enrichment") or {}
    must = list(enr.get("must_have") or job.get("skills") or [])
    nice = [s for s in enr.get("nice_to_have") or [] if s not in must]
    us = set(user_skills)

    # skills: must-haves count double relative to nice-to-haves
    if must or nice:
        got = sum(1 for s in must if s in us) + 0.5 * sum(1 for s in nice if s in us)
        skill = got / (len(must) + 0.5 * len(nice))
    else:
        skill = 0.3  # unknown requirements: neutral-ish
    matched = [s for s in [*must, *nice] if s in us]
    missing = [s for s in must if s not in us]

    # title: best overlap between any target role and the job title
    jt = title_tokens(title)
    title_s = 0.0
    for role in profile.target_roles:
        rt = title_tokens(role)
        if rt:
            title_s = max(title_s, len(rt & jt) / len(rt))

    # experience fit
    yrs = profile.experience_years
    lo, hi = enr.get("experience_min"), enr.get("experience_max")
    if lo is None:
        exp = 0.7 if not enr.get("fresher_friendly") or yrs <= 1 else 0.6
        if enr.get("seniority") in {"senior", "lead", "manager", "director"} and yrs < 3:
            exp = 0.2
    elif yrs + 0.5 >= lo:
        exp = 1.0 if hi is None or yrs <= hi + 3 else 0.6
    else:
        exp = max(0.0, 1 - 0.3 * (lo - yrs))
    if enr.get("fresher_friendly") and yrs <= 1:
        exp = 1.0

    # location
    wanted = {c for c in (canonical_city(x) for x in profile.locations) if c}
    cities = set(job.get("cities") or [])
    if (
        job.get("is_remote")
        and profile.open_to_remote
        or cities & wanted
        or not wanted
        and job.get("is_india")
    ):
        loc = 1.0
    elif job.get("is_india"):
        loc = 0.5
    else:
        loc = 0.2

    age = age_days(job.get("posted_at") or job.get("first_seen"))
    rec = 0.5 ** ((age or 0) / 7)

    parts = {"skills": skill, "title": title_s, "experience": exp, "location": loc, "recency": rec}
    score = 100 * sum(WEIGHTS[k] * v for k, v in parts.items())
    if exp < 0.4:  # seniority gate: a big experience gap can't be offset by keyword overlap
        score *= 0.6 + exp
    return MatchResult(
        round(score, 1), matched, missing[:6], {k: round(v, 3) for k, v in parts.items()}
    )


# --------------------------------------------------------------------------------------
# AI rerank (owner only)
# --------------------------------------------------------------------------------------
MATCH_SYSTEM = """You are a blunt, helpful career coach for the Indian tech job market.
Given a candidate resume and job postings, judge fit for EACH job.
Return JSON: {"jobs": [{"id": "...", "fit": 0-100, "verdict": "strong"|"good"|"stretch"|"poor",
"why": ["max 2 short reasons"], "gaps": ["missing requirements, max 3"],
"tip": "one concrete sentence on what to highlight when applying"}]}
Be strict: required years of experience and must-have skills matter most. No flattery."""


def _profile_brief(profile: Profile, limit: int = 3000) -> str:
    return (
        f"Target roles: {', '.join(profile.target_roles)}\n"
        f"Experience: {profile.experience_years:g} years\n"
        f"Preferred locations: {', '.join(profile.locations)} (remote ok: {profile.open_to_remote})\n"
        f"Resume:\n{profile.resume.strip()[:limit]}"
    )


async def ai_rerank(
    jobs: list[dict[str, Any]], profile: Profile, router: LLMRouter, batch_size: int = 3
) -> dict[str, dict[str, Any]]:
    """Return {uid: {fit, verdict, why, gaps, tip, provider}} for jobs the LLM scored."""
    brief = _profile_brief(profile)
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(jobs), batch_size):
        batch = jobs[i : i + batch_size]
        payload = [
            {
                "id": j["uid"],
                "title": j["title"],
                "company": j["company"],
                "location": j.get("location_raw"),
                "posting": requirements_excerpt(j["title"], j.get("description") or "", 1400),
            }
            for j in batch
        ]
        user = f"CANDIDATE\n{brief}\n\nJOBS\n{json.dumps(payload, ensure_ascii=False)}"
        res = await router.complete_json(
            "match", MATCH_SYSTEM, user, max_tokens=220 * len(batch) + 300
        )
        if res is None:
            break
        items = res.data.get("jobs") if isinstance(res.data, dict) else res.data
        for item in items or []:
            if not isinstance(item, dict) or str(item.get("id")) not in {j["uid"] for j in batch}:
                continue
            try:
                fit = max(0, min(100, int(float(item.get("fit", 0)))))
            except (TypeError, ValueError):
                continue
            out[str(item["id"])] = {
                "fit": fit,
                "verdict": item.get("verdict")
                if item.get("verdict") in {"strong", "good", "stretch", "poor"}
                else None,
                "why": [str(x)[:160] for x in (item.get("why") or [])][:2],
                "gaps": [str(x)[:80] for x in (item.get("gaps") or [])][:3],
                "tip": str(item.get("tip") or "")[:220] or None,
                "provider": res.provider,
            }
    return out


def combined_score(rule: float, ai_fit: int | None) -> float:
    return round(rule if ai_fit is None else 0.4 * rule + 0.6 * ai_fit, 1)
