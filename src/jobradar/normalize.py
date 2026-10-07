"""Text cleaning, Indian location parsing, dates and cross-source dedupe keys."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime

from jobradar.models import Job, stable_id

# --------------------------------------------------------------------------------------
# HTML -> text
# --------------------------------------------------------------------------------------
_BLOCK_TAGS = re.compile(
    r"</?(?:p|div|br|li|ul|ol|h[1-6]|tr|section|article|blockquote)\b[^>]*>", re.I
)
_LI_OPEN = re.compile(r"<li\b[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_WS = re.compile(r"[ \t ​]+")
_MULTI_NL = re.compile(r"\n\s*\n\s*\n+")


def html_to_text(raw: str | None) -> str:
    """Convert (possibly entity-escaped) HTML into readable plain text."""
    if not raw:
        return ""
    s = raw
    # Greenhouse double-encodes its HTML (&lt;p&gt;...). Decode once if that's the case.
    if "&lt;" in s and "<" not in s[:200]:
        s = html.unescape(s)
    s = _SCRIPT_STYLE.sub(" ", s)
    s = _LI_OPEN.sub("\n- ", s)
    s = _BLOCK_TAGS.sub("\n", s)
    s = _TAGS.sub(" ", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    s = "\n".join(line.strip() for line in s.splitlines())
    s = _MULTI_NL.sub("\n\n", s)
    return s.strip()


_SMALL_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "via",
    "vs",
}
_ACRONYMS = {
    "ai",
    "ml",
    "qa",
    "hr",
    "ui",
    "ux",
    "sde",
    "sre",
    "bi",
    "d2c",
    "b2b",
    "nbfc",
    "api",
    "ios",
    "it",
    "gtm",
    "fp&a",
    "us",
    "uk",
    "l1",
    "l2",
    "l3",
    "ii",
    "iii",
    "iv",
}


def tidy_title(title: str) -> str:
    """Normalise whitespace; title-case postings written entirely in lower case."""
    t = " ".join((title or "").split())
    if t and t == t.lower() and any(c.isalpha() for c in t):
        words = []
        for i, w in enumerate(t.split(" ")):
            core = w.strip("()[],-–:/")
            if core in _ACRONYMS:
                words.append(w.replace(core, core.upper()))
            elif i > 0 and core in _SMALL_WORDS:
                words.append(w)
            else:
                words.append(
                    w[:1].upper() + w[1:] if w[:1].isalpha() else w[:1] + w[1:2].upper() + w[2:]
                )
        t = " ".join(words)
    return t


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # avoid cutting mid-word
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.8 else cut).rstrip() + "…"


_REQ_HEADINGS = re.compile(
    r"^\s*(?:[-#*•]\s*)?(?:requirements|qualifications|minimum qualifications|basic qualifications|"
    r"preferred qualifications|what you(?:'ll| will)? need|what we(?:'re| are) looking for|"
    r"who you are|about you|you (?:have|bring|should have)|must[- ]haves?|skills(?: required)?|"
    r"required skills|experience required|your profile|what you bring|key skills)\b.*$",
    re.I | re.M,
)


def requirements_excerpt(title: str, text: str, limit: int = 1600) -> str:
    """Pick the most informative slice of a job description for the LLM.

    Requirements sections carry the skills/experience signal, while the first
    paragraphs are usually company boilerplate. We send the requirements first
    (if found) and top up with the opening text.
    """
    text = text or ""
    m = _REQ_HEADINGS.search(text)
    if m and m.start() > 0:
        req = text[m.start() :]
        head = text[: m.start()]
        budget_req = int(limit * 0.7)
        excerpt = clip(req, budget_req)
        remaining = limit - len(excerpt)
        if remaining > 200:
            excerpt = clip(head, remaining) + "\n...\n" + excerpt
    else:
        excerpt = clip(text, limit)
    return f"{title}\n{excerpt}".strip()


# --------------------------------------------------------------------------------------
# Locations (India-first)
# --------------------------------------------------------------------------------------
INDIA_CITIES: dict[str, list[str]] = {
    "Bengaluru": ["bengaluru", "bangalore", "blr", "bengalore"],
    "Hyderabad": ["hyderabad", "secunderabad", "hyd", "hitec city", "gachibowli"],
    "Pune": ["pune"],
    "Mumbai": ["mumbai", "bombay", "navi mumbai", "thane", "powai"],
    "Delhi NCR": ["delhi", "new delhi", "ncr", "delhi ncr"],
    "Gurugram": ["gurugram", "gurgaon"],
    "Noida": ["noida", "greater noida"],
    "Chennai": ["chennai", "madras"],
    "Kolkata": ["kolkata", "calcutta"],
    "Ahmedabad": ["ahmedabad", "gandhinagar", "gift city"],
    "Jaipur": ["jaipur"],
    "Kochi": ["kochi", "cochin", "ernakulam"],
    "Thiruvananthapuram": ["thiruvananthapuram", "trivandrum", "technopark"],
    "Coimbatore": ["coimbatore"],
    "Indore": ["indore"],
    "Chandigarh": ["chandigarh", "mohali", "panchkula"],
    "Bhubaneswar": ["bhubaneswar"],
    "Visakhapatnam": ["visakhapatnam", "vizag"],
    "Vijayawada": ["vijayawada", "amaravati"],
    "Mysuru": ["mysuru", "mysore"],
    "Nagpur": ["nagpur"],
    "Lucknow": ["lucknow"],
    "Goa": ["goa", "panaji"],
    "Mangaluru": ["mangaluru", "mangalore"],
}
# States / generic markers that imply India without a specific city.
INDIA_MARKERS = [
    "india",
    "karnataka",
    "telangana",
    "maharashtra",
    "tamil nadu",
    "haryana",
    "uttar pradesh",
    "west bengal",
    "gujarat",
    "andhra pradesh",
    "rajasthan",
    "madhya pradesh",
    "odisha",
    "punjab",
    "kerala",
    "bihar",
    "jharkhand",
    "assam",
    "bharat",
    "pan india",
    "pan-india",
]

_CITY_PATTERNS = [
    (
        city,
        re.compile(
            r"(?<![a-z])(?:" + "|".join(re.escape(a) for a in aliases) + r")(?![a-z])", re.I
        ),
    )
    for city, aliases in INDIA_CITIES.items()
]
_INDIA_RE = re.compile(
    r"(?<![a-z])(?:" + "|".join(re.escape(m) for m in INDIA_MARKERS) + r")(?![a-z])", re.I
)
_REMOTE_RE = re.compile(
    r"\b(?:remote|work from home|wfh|anywhere|distributed|home[- ]based)\b", re.I
)
_GLOBAL_RE = re.compile(
    r"\b(?:worldwide|anywhere|global|world ?wide|all countries|international)\b", re.I
)
_APAC_RE = re.compile(
    r"\b(?:apac|asia|asia[- ]pacific|south asia|ist|gmt\+5:?30|utc\+5:?30)\b", re.I
)


def match_cities(text: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for city, pat in _CITY_PATTERNS:
        m = pat.search(text)
        if m:
            found.append((m.start(), city))
    return [c for _, c in sorted(found)]


def canonical_city(name: str) -> str | None:
    cities = match_cities(name)
    return cities[0] if cities else None


def enrich_location(job: Job) -> Job:
    """Fill cities / is_india / is_remote / remote_scope from raw fields."""
    raw = job.location_raw or ""
    cities = match_cities(raw)
    is_india = (
        bool(cities)
        or bool(_INDIA_RE.search(raw))
        or (job.country_hint or "").upper() in {"IN", "IND", "INDIA"}
    )
    is_remote = bool(job.remote_hint) or bool(_REMOTE_RE.search(raw))
    scope = None
    if is_remote:
        if is_india:
            scope = "india"
        elif not raw.strip() or _GLOBAL_RE.search(raw):
            scope = "global"
        elif _APAC_RE.search(raw):
            scope = "apac"
        else:
            scope = "other"
    job.cities, job.is_india, job.is_remote, job.remote_scope = cities, is_india, is_remote, scope
    return job


def is_relevant(job: Job, keep: list[str]) -> bool:
    """Apply the `filters.keep` policy from settings.yaml."""
    keep_set = set(keep)
    if job.is_india and not job.is_remote and "india" in keep_set:
        return True
    if job.is_remote:
        return f"remote_{job.remote_scope}" in keep_set
    return False


def location_label(job_or_cities, is_remote: bool = False, scope: str | None = None) -> str:
    cities = job_or_cities.cities if isinstance(job_or_cities, Job) else job_or_cities
    if isinstance(job_or_cities, Job):
        is_remote, scope = job_or_cities.is_remote, job_or_cities.remote_scope
    parts = list(cities[:3])
    if len(cities) > 3:
        parts.append(f"+{len(cities) - 3}")
    if is_remote:
        parts.append(
            {
                "india": "Remote (India)",
                "global": "Remote (Worldwide)",
                "apac": "Remote (APAC)",
            }.get(scope or "", "Remote")
        )
    return ", ".join(parts) if parts else "India"


# --------------------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_date(value) -> str | None:
    """Accept ISO strings, epoch seconds or epoch milliseconds; return ISO-8601 UTC."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, int | float) or (isinstance(value, str) and value.isdigit()):
            num = float(value)
            if num > 1e12:  # milliseconds
                num /= 1000
            return iso(datetime.fromtimestamp(num, UTC))
        s = str(value).strip().replace(" ", "T", 1) if "T" not in str(value) else str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return iso(dt)
    except (ValueError, OverflowError, OSError):
        return None


def age_days(ts: str | None, now: datetime | None = None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, ((now or utcnow()) - dt).total_seconds() / 86400)


# --------------------------------------------------------------------------------------
# Dedupe keys
# --------------------------------------------------------------------------------------
_COMPANY_SUFFIX = re.compile(
    r"\b(?:inc|llc|ltd|limited|pvt|private|corp|corporation|co|technologies|technology|labs|"
    r"software|solutions|india|group|holdings|gmbh|plc)\b\.?",
    re.I,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm_company(name: str) -> str:
    s = _COMPANY_SUFFIX.sub(" ", name.lower())
    return _NON_ALNUM.sub("", s) or _NON_ALNUM.sub("", name.lower())


def norm_title(title: str) -> str:
    s = title.lower()
    s = re.sub(r"\bsr\.?\b", "senior", s)
    s = re.sub(r"\bjr\.?\b", "junior", s)
    s = re.sub(r"\bsde\b", "software development engineer", s)
    s = re.sub(r"\bswe\b", "software engineer", s)
    return " ".join(_NON_ALNUM.sub(" ", s).split())


def fingerprint(job: Job) -> str:
    place = (
        job.cities[0]
        if job.cities
        else (f"remote-{job.remote_scope}" if job.is_remote else "india")
    )
    return stable_id(norm_company(job.company), norm_title(job.title), place.lower())
