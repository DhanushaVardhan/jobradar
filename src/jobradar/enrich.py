"""Turn raw postings into structured, searchable facts.

Two layers:
1. `rule_enrich` – instant, free, deterministic (regex + skills taxonomy). Every job gets it.
2. `llm_enrich_batch` – an LLM reads several postings per request and returns a
   one-line summary, seniority, experience range, must-have vs nice-to-have skills,
   fresher-friendliness, work mode and salary. Used on as many new jobs as the free
   quota allows; anything it can't cover keeps the rule-based result.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from jobradar.llm import LLMRouter
from jobradar.normalize import requirements_excerpt
from jobradar.skills import SkillTaxonomy

log = logging.getLogger(__name__)

SENIORITY = ["intern", "entry", "mid", "senior", "lead", "manager", "director", "unknown"]
ROLE_CATEGORIES = [
    "software",
    "frontend",
    "backend",
    "fullstack",
    "mobile",
    "data",
    "ml_ai",
    "devops_cloud",
    "security",
    "qa",
    "product",
    "design",
    "sales",
    "marketing",
    "finance",
    "operations",
    "hr",
    "support",
    "other",
]
WORK_MODES = ["remote", "hybrid", "onsite", "unknown"]

# --------------------------------------------------------------------------------------
# Rule-based layer
# --------------------------------------------------------------------------------------
_SENIORITY_RULES = [
    ("intern", r"\b(?:intern|internship|apprentice)\b"),
    ("director", r"\b(?:director|vp|vice president|head of|chief|cto|cfo)\b"),
    ("manager", r"\b(?:manager|mgr)\b"),
    ("lead", r"\b(?:lead|staff|principal|architect)\b"),
    ("senior", r"\b(?:senior|sr\.?|iii|iv)\b"),
    (
        "entry",
        r"\b(?:junior|jr\.?|associate|graduate|trainee|fresher|entry[- ]level|new grad|campus|i)\b",
    ),
]
_ROLE_RULES = [
    (
        "ml_ai",
        r"\b(?:machine learning|ml|ai|deep learning|nlp|computer vision|llm|genai|applied scientist|research scientist)\b",
    ),
    ("data", r"\b(?:data|analytics|analyst|bi|business intelligence|statistician)\b"),
    (
        "devops_cloud",
        r"\b(?:devops|sre|site reliability|cloud|platform|infrastructure|infra|kubernetes)\b",
    ),
    ("security", r"\b(?:security|cyber|soc|penetration|appsec|infosec)\b"),
    ("qa", r"\b(?:qa|quality|test|tester|sdet|automation engineer)\b"),
    ("mobile", r"\b(?:android|ios|mobile|flutter|react native)\b"),
    ("frontend", r"\b(?:frontend|front[- ]end|ui engineer|web developer)\b"),
    ("backend", r"\b(?:backend|back[- ]end|server[- ]side|api engineer)\b"),
    ("fullstack", r"\b(?:full[- ]?stack)\b"),
    ("product", r"\b(?:product manager|product owner|program manager|tpm)\b"),
    ("design", r"\b(?:designer|ux|ui/ux|design)\b"),
    ("software", r"\b(?:software|developer|engineer|sde|swe|programmer)\b"),
    ("sales", r"\b(?:sales|account executive|business development|bdr|sdr|account development)\b"),
    ("marketing", r"\b(?:marketing|growth|seo|content|brand|social media)\b"),
    ("finance", r"\b(?:finance|financial|accountant|accounting|audit|tax|treasury|risk|credit)\b"),
    ("hr", r"\b(?:recruiter|talent|hr|human resources|people partner)\b"),
    ("support", r"\b(?:support|customer success|customer service|helpdesk)\b"),
    ("operations", r"\b(?:operations|ops|supply chain|logistics|procurement)\b"),
]
_EXP_RANGE = re.compile(
    r"(?<![\d.])(\d{1,2}(?:\.\d)?)\s*(?:\+|plus)?\s*(?:-|–|to)\s*(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)",
    re.I,
)
_EXP_UPTO = re.compile(r"\b(?:up to|upto|maximum of|max\.?)\s*(\d{1,2})\s*(?:years?|yrs?)", re.I)
_EXP_MIN = re.compile(r"(?<![\d.])(\d{1,2})\s*(\+|plus)?\s*(?:years?|yrs?)(\s+of)?", re.I)
_FRESHER = re.compile(
    r"\b(?:fresher|freshers|new grad|new graduate|recent graduate|graduate trainee|entry[- ]level|"
    r"0\s*(?:-|–|to)\s*[12]\s*(?:years?|yrs?)|20(?:2[4-9])\s*(?:/\s*20\d\d\s*)?(?:batch|pass[- ]?out|graduates?)|"
    r"campus hire|no experience required)\b",
    re.I,
)
_REMOTE = re.compile(
    r"\b(?:fully remote|100% remote|remote[- ]first|work from home|remote)\b", re.I
)
_HYBRID = re.compile(r"\bhybrid\b", re.I)


def _first_match(rules: list[tuple[str, str]], text: str, default: str) -> str:
    for label, pattern in rules:
        if re.search(pattern, text, re.I):
            return label
    return default


def rule_experience(text: str) -> tuple[int | None, int | None]:
    m = _EXP_RANGE.search(text)
    if m:
        lo, hi = int(float(m.group(1))), int(float(m.group(2)) + 0.5)
        if lo <= hi <= 30:
            return lo, hi
    m = _EXP_UPTO.search(text)
    if m and int(m.group(1)) <= 15:
        return 0, int(m.group(1))
    for m in _EXP_MIN.finditer(text):
        # "5+ years" / "5 years of" are reliable; a bare "5 years" must sit near "experience"
        window = text[max(0, m.start() - 60) : m.end() + 60].lower()
        if m.group(2) or m.group(3) or "experience" in window or "exp" in window:
            n = int(m.group(1))
            if n <= 25:
                return n, None
    return None, None


def rule_enrich(job: dict[str, Any], taxonomy: SkillTaxonomy) -> tuple[list[str], dict[str, Any]]:
    title = job["title"]
    desc = job.get("description") or ""
    text = f"{title}\n{desc}"
    skills = taxonomy.extract(text, exclude={job["company"]})
    seniority = _first_match(_SENIORITY_RULES, title, "mid")
    exp_min, exp_max = rule_experience(desc)
    if exp_min is not None:
        if seniority == "mid":
            seniority = "entry" if exp_min <= 1 else "senior" if exp_min >= 6 else "mid"
        elif seniority == "entry" and exp_min >= 3:
            seniority = "mid"  # "Associate" titles often still ask for 3+ years
    fresher = (
        bool(_FRESHER.search(text))
        or seniority == "intern"
        or (seniority == "entry" and not exp_min)
    )
    if job.get("is_remote"):
        mode = "remote"
    elif _HYBRID.search(text[:3000]):
        mode = "hybrid"
    elif _REMOTE.search(job.get("location_raw") or ""):
        mode = "remote"
    else:
        mode = "onsite" if job.get("cities") else "unknown"
    enrichment = {
        "summary": None,
        "seniority": seniority,
        "experience_min": exp_min,
        "experience_max": exp_max,
        "fresher_friendly": fresher,
        "role_category": _first_match(_ROLE_RULES, title, "other"),
        "must_have": skills[:10],
        "nice_to_have": [],
        "other_skills": [],
        "work_mode": mode,
        "salary": None,
    }
    return skills, enrichment


# --------------------------------------------------------------------------------------
# LLM layer
# --------------------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a precise job-posting parser for an Indian job board.
For EACH posting return one object. Rules:
- Use only facts stated in the posting. If unknown, use null (or "unknown").
- summary: max 22 words, plain English, what the person will actually do. No company hype.
- seniority: one of intern|entry|mid|senior|lead|manager|director|unknown.
- experience_min / experience_max: integer years required (null if not stated).
- fresher_friendly: true only if freshers/new grads/0-1 years/current batch can apply.
- role_category: one of software|frontend|backend|fullstack|mobile|data|ml_ai|devops_cloud|security|qa|product|design|sales|marketing|finance|operations|hr|support|other.
- must_have: up to 8 concrete skills/tools the posting REQUIRES (e.g. "Python", "Kubernetes", "SQL"). No soft skills.
- nice_to_have: up to 6 concrete skills marked preferred/bonus/plus.
- work_mode: remote|hybrid|onsite|unknown.
- salary: {"min": number, "max": number, "currency": "INR"|"USD"|..., "period": "year"|"month"|"hour"} or null. Only if stated.
Return JSON: {"jobs": [{"id": "...", "summary": ..., "seniority": ..., "experience_min": ..., "experience_max": ..., "fresher_friendly": ..., "role_category": ..., "must_have": [...], "nice_to_have": [...], "work_mode": ..., "salary": ...}]}"""


def _as_int(v: Any, lo: int = 0, hi: int = 40) -> int | None:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def validate_llm_item(
    item: dict[str, Any], base: dict[str, Any], taxonomy: SkillTaxonomy
) -> tuple[list[str], dict[str, Any]]:
    """Coerce an LLM object into our schema, falling back to rule values field by field."""
    out = dict(base)
    summary = item.get("summary")
    if isinstance(summary, str) and summary.strip():
        words = summary.strip().split()
        out["summary"] = " ".join(words[:30])
    if item.get("seniority") in SENIORITY and item["seniority"] != "unknown":
        out["seniority"] = item["seniority"]
    exp_min, exp_max = _as_int(item.get("experience_min")), _as_int(item.get("experience_max"))
    if exp_min is not None or exp_max is not None:
        if exp_min is not None and exp_max is not None and exp_max < exp_min:
            exp_max = None
        out["experience_min"], out["experience_max"] = exp_min, exp_max
    if isinstance(item.get("fresher_friendly"), bool):
        out["fresher_friendly"] = item["fresher_friendly"] or base.get("fresher_friendly", False)
    if item.get("role_category") in ROLE_CATEGORIES:
        out["role_category"] = item["role_category"]
    if item.get("work_mode") in WORK_MODES and item["work_mode"] != "unknown":
        out["work_mode"] = item["work_mode"]

    must, must_other = taxonomy.normalize_list(item.get("must_have") or [], limit=8)
    nice, nice_other = taxonomy.normalize_list(item.get("nice_to_have") or [], limit=6)
    nice = [s for s in nice if s not in must]
    if must or nice:
        out["must_have"], out["nice_to_have"] = must, nice
    out["other_skills"] = (must_other + nice_other)[:6]

    sal = item.get("salary")
    if isinstance(sal, dict):
        lo, hi = sal.get("min"), sal.get("max")
        try:
            lo = float(lo) if lo is not None else None
            hi = float(hi) if hi is not None else None
        except (TypeError, ValueError):
            lo = hi = None
        if lo or hi:
            out["salary"] = {
                "min": lo,
                "max": hi,
                "currency": str(sal.get("currency") or "").upper()[:3] or None,
                "period": sal.get("period")
                if sal.get("period") in {"year", "month", "hour"}
                else None,
            }
    # Final skills list: LLM must/nice first (they're ranked), then any rule hits it missed.
    skills = list(
        dict.fromkeys([*out["must_have"], *out["nice_to_have"], *base.get("must_have", [])])
    )
    return skills[:15], out


async def llm_enrich_batch(
    jobs: list[dict[str, Any]],
    bases: dict[str, dict[str, Any]],
    router: LLMRouter,
    taxonomy: SkillTaxonomy,
    excerpt_chars: int = 1200,
    prefer: str | None = None,
) -> tuple[dict[str, tuple[list[str], dict[str, Any]]], str | None]:
    """Enrich a small batch in one request. Returns ({uid: (skills, enrichment)}, provider)."""
    postings = [
        {
            "id": j["uid"],
            "company": j["company"],
            "location": j.get("location_raw") or "",
            "text": requirements_excerpt(j["title"], j.get("description") or "", excerpt_chars),
        }
        for j in jobs
    ]
    user = "Parse these postings:\n" + json.dumps(postings, ensure_ascii=False)
    result = await router.complete_json(
        "enrich", SYSTEM_PROMPT, user, max_tokens=250 * len(jobs) + 400, prefer=prefer
    )
    if result is None:
        return {}, None
    data = result.data
    items = data.get("jobs") if isinstance(data, dict) else data
    out: dict[str, tuple[list[str], dict[str, Any]]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("id") or "")
        if uid in bases:
            out[uid] = validate_llm_item(item, bases[uid], taxonomy)
    return out, result.provider
