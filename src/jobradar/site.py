"""Static site + open data export: jobs.json, stats.json, skills.json and RSS feeds."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections import Counter
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from jobradar.config import Config
from jobradar.normalize import INDIA_CITIES, clip, iso, location_label, utcnow
from jobradar.skills import SkillTaxonomy
from jobradar.store import Store


def slugify(text: str) -> str:
    s = (
        text.lower()
        .replace("+", "plus")
        .replace("#", "sharp")
        .replace(".", "dot-" if text.startswith(".") else "")
    )
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "x"


def _salary(enr: dict[str, Any], raw: str | None) -> str | None:
    sal = enr.get("salary")
    if isinstance(sal, dict) and (sal.get("min") or sal.get("max")):
        cur = sal.get("currency") or ""
        lo, hi = sal.get("min"), sal.get("max")

        def fmt(v: float) -> str:
            if cur == "INR" and v >= 100000:
                return f"{v / 100000:.1f}".rstrip("0").rstrip(".") + "L"
            return f"{v / 1000:.0f}k" if v >= 10000 else f"{v:,.0f}"

        rng = f"{fmt(lo)}–{fmt(hi)}" if lo and hi and hi != lo else fmt(lo or hi)
        per = {"year": "/yr", "month": "/mo", "hour": "/hr"}.get(sal.get("period") or "", "")
        return f"{cur} {rng}{per}".strip()
    return raw


def job_record(j: dict[str, Any]) -> dict[str, Any]:
    enr = j.get("enrichment") or {}
    posted = (j.get("posted_at") or j["first_seen"])[:10]
    rec = {
        "id": j["uid"],
        "title": j["title"],
        "company": j["company"],
        "url": j["url"],
        "via": j.get("via"),
        "source": j["source"],
        "location": location_label(
            j.get("cities") or [], bool(j.get("is_remote")), j.get("remote_scope")
        ),
        "cities": j.get("cities") or [],
        "remote": j.get("remote_scope") if j.get("is_remote") else None,
        "india": bool(j.get("is_india")),
        "posted": posted,
        "posted_at": j.get("posted_at"),
        "first_seen": j["first_seen"],
        "skills": j.get("skills") or [],
        "must": enr.get("must_have") or [],
        "nice": enr.get("nice_to_have") or [],
        "seniority": enr.get("seniority"),
        "exp": [enr.get("experience_min"), enr.get("experience_max")],
        "fresher": bool(enr.get("fresher_friendly")),
        "role": enr.get("role_category"),
        "mode": enr.get("work_mode"),
        "salary": _salary(enr, j.get("salary_raw")),
        "summary": enr.get("summary"),
        "ai": bool(j.get("enriched_by") and j["enriched_by"] != "rules"),
        "type": j.get("employment_type"),
    }
    if not rec["summary"]:
        rec["snippet"] = clip(" ".join((j.get("description") or "").split()), 220)
    return rec


# --------------------------------------------------------------------------------------
def build_stats(
    store: Store, records: list[dict[str, Any]], config: Config, taxonomy: SkillTaxonomy
) -> dict[str, Any]:
    now = utcnow()
    d1, d7, d14 = (iso(now - timedelta(days=n)) for n in (1, 7, 14))

    all_rows = store.db.execute("SELECT first_seen, skills, duplicate_of FROM jobs").fetchall()
    this_week, last_week = Counter(), Counter()
    for r in all_rows:
        if r["duplicate_of"]:
            continue
        skills = json.loads(r["skills"] or "[]")
        if r["first_seen"] >= d7:
            this_week.update(skills)
        elif r["first_seen"] >= d14:
            last_week.update(skills)

    skill_counts = Counter(s for rec in records for s in rec["skills"])
    top_skills = [
        {
            "skill": s,
            "count": c,
            "category": taxonomy.category_of.get(s, "other"),
            "this_week": this_week.get(s, 0),
            "last_week": last_week.get(s, 0),
        }
        for s, c in skill_counts.most_common(40)
    ]
    rising = sorted(
        (x for x in top_skills if x["this_week"] >= 5),
        key=lambda x: (x["this_week"] - x["last_week"]) / max(x["last_week"], 3),
        reverse=True,
    )[:8]

    daily = Counter(
        r["first_seen"][:10] for r in all_rows if r["first_seen"] >= iso(now - timedelta(days=30))
    )
    days = [(now - timedelta(days=n)).date().isoformat() for n in range(29, -1, -1)]

    runs = store.recent_runs(12)
    today = now.date().isoformat()
    llm_today = [
        dict(r)
        for r in store.db.execute(
            "SELECT provider, requests, tokens FROM llm_usage WHERE day = ?", (today,)
        )
    ]

    return {
        "generated_at": iso(now),
        "site": {
            **config.section("site"),
            "url": config.site_url,
            "repo_url": os.environ.get("REPO_URL") or config.section("site").get("repo_url"),
            "telegram_channel_url": config.section("site").get("telegram_channel_url"),
        },
        "totals": {
            "open": len(records),
            "new_24h": sum(1 for r in records if r["first_seen"] >= d1),
            "new_7d": sum(1 for r in records if r["first_seen"] >= d7),
            "companies": len({r["company"] for r in records}),
            "fresher": sum(1 for r in records if r["fresher"]),
            "remote": sum(1 for r in records if r["remote"]),
            "india_onsite": sum(1 for r in records if r["india"] and not r["remote"]),
            "ai_enriched": sum(1 for r in records if r["ai"]),
        },
        "top_skills": top_skills,
        "rising_skills": [x["skill"] for x in rising],
        "cities": [
            {"city": c, "count": n}
            for c, n in Counter(c for r in records for c in r["cities"]).most_common(15)
        ],
        "roles": [
            {"role": k, "count": v}
            for k, v in Counter(r["role"] or "other" for r in records).most_common()
        ],
        "seniority": [
            {"level": k, "count": v}
            for k, v in Counter(r["seniority"] or "unknown" for r in records).most_common()
        ],
        "sources": [
            {"source": k, "count": v}
            for k, v in Counter(r["source"] for r in records).most_common()
        ],
        "companies": [
            {"company": k, "count": v}
            for k, v in Counter(r["company"] for r in records).most_common(20)
        ],
        "daily": [{"date": d, "count": daily.get(d, 0)} for d in days],
        "health": [
            {
                "target": h["target"],
                "source": h["source"],
                "ok": h["consecutive_failures"] == 0,
                "last_ok": h["last_ok"],
                "count": h["last_count"],
                "error": h["last_error"],
                "failures": h["consecutive_failures"],
            }
            for h in store.health()
        ],
        "runs": [
            {
                "started_at": r["started_at"],
                "status": r["status"],
                "duration_s": r["stats"].get("duration_s"),
                "new": r["stats"].get("new"),
                "llm_requests": (r["stats"].get("llm") or {}).get("requests"),
            }
            for r in runs
        ],
        "llm_today": llm_today,
    }


# --------------------------------------------------------------------------------------
def _rss(title: str, link: str, desc: str, items: list[dict[str, Any]], self_url: str) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{escape(title)}</title>",
        f"<link>{escape(link)}</link>",
        f"<description>{escape(desc)}</description>",
        f'<atom:link href="{escape(self_url)}" rel="self" type="application/rss+xml"/>',
        f"<lastBuildDate>{format_datetime(utcnow())}</lastBuildDate>",
    ]
    for r in items:
        from datetime import datetime

        pub = datetime.fromisoformat(r["first_seen"].replace("Z", "+00:00"))
        parts = [r["company"], r["location"]]
        if r["exp"][0] is not None:
            parts.append(f"{r['exp'][0]}+ yrs")
        if r["fresher"]:
            parts.append("fresher-friendly")
        body = " · ".join(parts)
        if r.get("summary"):
            body += f"\n{r['summary']}"
        if r["skills"]:
            body += "\nSkills: " + ", ".join(r["skills"][:8])
        if r.get("via"):
            body += f"\nvia {r['via']}"
        out += [
            "<item>",
            f"<title>{escape(r['title'] + ' — ' + r['company'])}</title>",
            f"<link>{escape(r['url'])}</link>",
            f'<guid isPermaLink="false">jobradar-{r["id"]}</guid>',
            f"<pubDate>{format_datetime(pub)}</pubDate>",
            f"<description>{escape(body)}</description>",
            *[f"<category>{escape(s)}</category>" for s in r["skills"][:6]],
            "</item>",
        ]
    out += ["</channel>", "</rss>"]
    return "\n".join(out)


def build_feeds(
    out_dir: Path, records: list[dict[str, Any]], stats: dict[str, Any], config: Config
) -> list[dict[str, str]]:
    opts = config.section("feeds")
    n = int(opts.get("items_per_feed", 50))
    site = config.site_url
    title = config.section("site").get("title", "JobRadar")
    feeds_dir = out_dir / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)
    by_new = sorted(records, key=lambda r: r["first_seen"], reverse=True)

    specs: list[tuple[str, str, str, list[dict[str, Any]]]] = [
        ("all", "All jobs", "Every new job", by_new),
        (
            "fresher",
            "Fresher-friendly",
            "Jobs open to freshers and new graduates",
            [r for r in by_new if r["fresher"]],
        ),
        (
            "remote",
            "Remote",
            "Remote jobs open to candidates in India",
            [r for r in by_new if r["remote"]],
        ),
    ]
    for s in stats["top_skills"][: int(opts.get("top_skills", 40))]:
        specs.append(
            (
                f"skill-{slugify(s['skill'])}",
                s["skill"],
                f"New {s['skill']} jobs",
                [r for r in by_new if s["skill"] in r["skills"]],
            )
        )
    for c in stats["cities"][: int(opts.get("top_cities", 12))]:
        specs.append(
            (
                f"city-{slugify(c['city'])}",
                c["city"],
                f"New jobs in {c['city']}",
                [r for r in by_new if c["city"] in r["cities"]],
            )
        )

    index = []
    for key, label, desc, items in specs:
        path = f"feeds/{key}.xml"
        (out_dir / path).write_text(
            _rss(f"{title} · {label}", site, desc, items[:n], site + path), encoding="utf-8"
        )
        index.append({"key": key, "label": label, "path": path, "count": len(items)})
    (feeds_dir / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    return index


# --------------------------------------------------------------------------------------
def build_site(store: Store, config: Config, taxonomy: SkillTaxonomy) -> dict[str, Any]:
    out = config.site_dir
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(
        config.web_dir, out, ignore=shutil.ignore_patterns("tests", "*.md", "node_modules")
    )
    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    records = [job_record(j) for j in store.open_jobs()]
    stats = build_stats(store, records, config, taxonomy)
    feeds = build_feeds(out, records, stats, config)
    stats["feeds"] = feeds

    def dump(name: str, obj: Any) -> None:
        (data_dir / name).write_text(
            json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )

    dump("jobs.json", records)
    dump("stats.json", stats)
    dump("taxonomy.json", taxonomy.export() | {"cities": INDIA_CITIES})
    return {
        "jobs": len(records),
        "feeds": len(feeds),
        "bytes": sum(f.stat().st_size for f in data_dir.iterdir()),
    }
