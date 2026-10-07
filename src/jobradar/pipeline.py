"""The end-to-end run: fetch -> normalise -> store -> enrich -> match -> notify -> publish."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

import httpx

from jobradar.config import Config
from jobradar.enrich import llm_enrich_batch, rule_enrich
from jobradar.http import Http
from jobradar.llm import LLMRouter
from jobradar.match import ai_rerank, combined_score, profile_skills, rule_score
from jobradar.models import TargetResult
from jobradar.normalize import age_days, enrich_location, is_relevant, iso, tidy_title, utcnow
from jobradar.notify.telegram import Telegram, format_channel, format_digest
from jobradar.site import build_site
from jobradar.skills import SkillTaxonomy
from jobradar.sources import ALL_SOURCES, SOURCE_PRIORITY
from jobradar.sources.base import Source
from jobradar.store import Store

log = logging.getLogger(__name__)

TECH_ROLES = {
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
}
OWNER = "owner"


class Transports:
    """Injectable HTTP transports (tests and offline demos use httpx.MockTransport)."""

    def __init__(self, sources=None, llm=None, telegram=None):
        self.sources: httpx.AsyncBaseTransport | None = sources
        self.llm: httpx.AsyncBaseTransport | None = llm
        self.telegram: httpx.AsyncBaseTransport | None = telegram


# --------------------------------------------------------------------------------------
# 1. Fetch
# --------------------------------------------------------------------------------------
async def fetch_all(
    config: Config, store: Store, transport=None
) -> tuple[list[TargetResult], dict[str, str], int]:
    http_cfg = config.section("http")
    async with Http(
        user_agent=http_cfg.get("user_agent", "JobRadar/1.0"),
        timeout=float(http_cfg.get("timeout", 30)),
        concurrency=int(http_cfg.get("concurrency", 12)),
        per_host=int(http_cfg.get("per_host", 3)),
        transport=transport,
    ) as http:
        sources = [cls(http, config, known_ids=store.known_uids(cls.name)) for cls in ALL_SOURCES]
        skipped = {s.name: s.skip_reason() or "" for s in sources if not s.enabled()}
        for s in sources:
            if s.name not in skipped and (wait := cooldown(s, store)):
                skipped[s.name] = wait
        active = [s for s in sources if s.name not in skipped]
        nested = await asyncio.gather(*(s.fetch() for s in active))
        return [r for group in nested for r in group], skipped, http.request_count


def cooldown(source: Source, store: Store) -> str | None:
    """Skip reason for a source called less than `min_interval_hours` ago.

    Some APIs ask for only a few calls a day (Remotive: at most 4). The setting keeps that
    promise even when a run is retried by a backup schedule or started by hand.
    """
    hours = float(source.opts.get("min_interval_hours") or 0)
    elapsed = age_days(store.last_attempt(source.name)) if hours > 0 else None
    if elapsed is None or elapsed * 24 >= hours:
        return None
    return f"last called {elapsed * 24:.1f}h ago (min {hours:g}h between calls)"


def ingest(config: Config, store: Store, results: list[TargetResult]) -> dict[str, Any]:
    keep = config.section("filters").get("keep") or [
        "india",
        "remote_india",
        "remote_global",
        "remote_apac",
    ]
    max_age = float(config.section("filters").get("max_age_days", 30))
    known = store.known_uids()
    totals = {"fetched": 0, "relevant": 0, "new": 0, "updated": 0, "reopened": 0, "closed": 0}
    per_target: list[dict[str, Any]] = []
    now = iso(utcnow())
    for res in results:
        store.record_health(res, now)
        row = {
            "target": res.target,
            "ok": res.ok,
            "fetched": len(res.jobs),
            "relevant": 0,
            "new": 0,
            "error": res.error,
        }
        if res.ok:
            relevant = []
            for job in res.jobs:
                job.title = tidy_title(job.title)
                enrich_location(job)
                if not is_relevant(job, keep):
                    continue
                # Company boards list only currently-open jobs, so an old posting date there
                # is still a live job. Partial aggregator feeds get the freshness cut-off.
                if (
                    not res.complete
                    and job.uid not in known
                    and (age_days(job.posted_at) or 0) > max_age
                ):
                    continue
                relevant.append(job)
            counts = store.upsert_jobs(relevant, now)
            closed = store.close_missing(res, now)
            row |= {"relevant": len(relevant), "new": counts["new"]}
            totals["fetched"] += len(res.jobs)
            totals["relevant"] += len(relevant)
            for k in ("new", "updated", "reopened"):
                totals[k] += counts[k]
            totals["closed"] += closed
        per_target.append(row)
    retention = config.section("retention")
    totals["closed"] += store.close_stale(int(retention.get("close_after_missing_days", 5)))
    totals["duplicates"] = store.mark_duplicates(SOURCE_PRIORITY)
    store.commit()
    return {"totals": totals, "targets": per_target}


# --------------------------------------------------------------------------------------
# 2. Enrich
# --------------------------------------------------------------------------------------
async def enrich_jobs(
    config: Config, store: Store, taxonomy: SkillTaxonomy, router: LLMRouter
) -> dict[str, Any]:
    llm_cfg = config.section("llm")
    stats = {"rules": 0, "llm": 0, "llm_batches": 0, "llm_skipped_reason": None}

    for job in store.jobs_needing_enrichment(llm=False, limit=1_000_000):
        skills, enr = rule_enrich(job, taxonomy)
        store.save_enrichment(job["uid"], skills, enr, "rules")
        stats["rules"] += 1
    store.commit()

    if not router.available("enrich"):
        stats["llm_skipped_reason"] = "no LLM provider available (missing key or quota)"
        return stats

    cands = store.jobs_needing_enrichment(
        llm=True, limit=int(llm_cfg.get("enrich_max_per_run", 200)) * 3
    )
    cands.sort(
        key=lambda j: (
            not j["is_india"] and not j["is_remote"],
            (j.get("enrichment") or {}).get("role_category") not in TECH_ROLES,
        )
    )
    cands = cands[: int(llm_cfg.get("enrich_max_per_run", 200))]
    size = max(1, int(llm_cfg.get("enrich_batch_size", 5)))
    batches = [cands[i : i + size] for i in range(0, len(cands), size)]
    queue: asyncio.Queue = asyncio.Queue()
    for b in batches:
        queue.put_nowait(b)
    deadline = time.monotonic() + float(llm_cfg.get("time_budget_minutes", 15)) * 60
    excerpt = int(llm_cfg.get("excerpt_chars", 1200))

    async def worker(prefer: str) -> None:
        while not queue.empty() and time.monotonic() < deadline and router.available("enrich"):
            batch = queue.get_nowait()
            bases = {j["uid"]: j.get("enrichment") or rule_enrich(j, taxonomy)[1] for j in batch}
            results, provider = await llm_enrich_batch(
                batch, bases, router, taxonomy, excerpt, prefer=prefer
            )
            if provider is None:
                return
            stats["llm_batches"] += 1
            for uid, (skills, enr) in results.items():
                store.save_enrichment(uid, skills, enr, provider)
                stats["llm"] += 1
            store.commit()

    workers = router.usable_providers("enrich") or [None]
    await asyncio.gather(*(worker(p) for p in workers))
    if not queue.empty():
        stats["llm_skipped_reason"] = (
            f"{queue.qsize()} batches left for the next run (quota/time budget)"
        )
    return stats


# --------------------------------------------------------------------------------------
# 3. Match (owner profile)
# --------------------------------------------------------------------------------------
async def match_owner(
    config: Config, store: Store, taxonomy: SkillTaxonomy, router: LLMRouter
) -> dict[str, Any]:
    profile = config.profile
    if not profile or not profile.is_configured:
        return {"skipped": "no profile (set JOBRADAR_PROFILE)"}
    mcfg = config.section("match")
    min_score = float(mcfg.get("min_score", 40))
    user_skills = profile_skills(profile, taxonomy)
    cutoff = iso(utcnow() - timedelta(days=7))
    recent = [j for j in store.open_jobs() if j["first_seen"] >= cutoff]

    scored = []
    for j in recent:
        r = rule_score(j, profile, user_skills)
        if r.score >= min_score:
            scored.append((j, r))
    scored.sort(key=lambda x: x[1].score, reverse=True)

    need_ai = []
    existing: dict[str, dict[str, Any]] = {}
    for j, _ in scored:
        row = store.match_row(j["uid"], OWNER)
        if row:
            existing[j["uid"]] = row
        if (not row or row["ai_fit"] is None) and not (row and row["notified_at"]):
            need_ai.append(j)
    need_ai = need_ai[: int(mcfg.get("ai_top_k", 20))]
    ai = (
        await ai_rerank(need_ai, profile, router, int(mcfg.get("ai_batch_size", 3)))
        if need_ai and router.available("match")
        else {}
    )

    for j, r in scored:
        prev = existing.get(j["uid"])
        ai_d = ai.get(j["uid"]) or ((prev or {}).get("details") or {}).get("ai")
        fit = ai_d["fit"] if ai_d else None
        store.save_match(
            j["uid"],
            OWNER,
            combined_score(r.score, fit),
            fit,
            {
                "rule": r.score,
                "matched": r.matched,
                "missing": r.missing,
                "parts": r.parts,
                "ai": ai_d,
            },
        )
    store.commit()
    return {"profile_skills": len(user_skills), "candidates": len(scored), "ai_scored": len(ai)}


# --------------------------------------------------------------------------------------
# 4. Notify
# --------------------------------------------------------------------------------------
def pick_channel_jobs(store: Store, limit: int) -> list[dict[str, Any]]:
    cutoff = iso(utcnow() - timedelta(days=1))
    fresh = [
        j
        for j in store.open_jobs()
        if j["first_seen"] >= cutoff and not store.was_posted(j["uid"], "channel")
    ]

    def rank(j: dict[str, Any]) -> tuple:
        e = j.get("enrichment") or {}
        return (
            not e.get("fresher_friendly"),
            e.get("role_category") not in TECH_ROLES,
            not j["is_india"],
            j.get("enriched_by") == "rules",
        )

    fresh.sort(key=rank)
    return fresh[:limit]


async def notify_all(
    config: Config,
    store: Store,
    run_totals: dict[str, Any],
    force_digest: bool | None,
    transport=None,
) -> dict[str, Any]:
    token = config.env("TELEGRAM_BOT_TOKEN")
    if not token:
        return {"skipped": "no TELEGRAM_BOT_TOKEN"}
    tcfg = (config.section("notify").get("telegram")) or {}
    owner_chat = config.env("TELEGRAM_CHAT_ID")
    channel = config.env("TELEGRAM_CHANNEL_ID")
    site = config.site_url
    out: dict[str, Any] = {"digest": 0, "channel": 0, "alerts": 0}
    tg = Telegram(token, transport=transport)
    try:
        # health alerts: fire once when a board crosses the failure threshold
        threshold = int(tcfg.get("health_alert_after_failures", 3))
        broken = [h for h in store.health() if h["consecutive_failures"] == threshold]
        if owner_chat and broken:
            lines = ["<b>JobRadar health alert</b>"] + [
                f"• {h['target']}: failing {h['consecutive_failures']} runs — {(h['last_error'] or '')[:120]}"
                for h in broken
            ]
            lines.append(
                "Tip: run <code>python -m jobradar doctor</code> or remove the board from companies.yaml."
            )
            out["alerts"] = await tg.send_many(owner_chat, ["\n".join(lines)])

        # owner digest
        hour = utcnow().hour
        due = (
            force_digest
            if force_digest is not None
            else hour in (tcfg.get("digest_hours_utc") or [2, 3])
        )
        if owner_chat and tcfg.get("owner_digest", True) and due and config.profile:
            rows = store.unnotified_matches(OWNER)
            cutoff = iso(utcnow() - timedelta(days=3))
            items = []
            for m in sorted(rows, key=lambda r: r["score"], reverse=True):
                job = store.get_job(m["uid"])
                if not job or job["first_seen"] < cutoff:
                    continue
                d = m["details"]
                items.append(
                    {
                        "job": job,
                        "score": m["score"],
                        "ai": d.get("ai"),
                        "matched": d.get("matched"),
                        "missing": d.get("missing"),
                    }
                )
                if len(items) >= int(config.section("match").get("digest_size", 12)):
                    break
            if items:
                msgs = format_digest(items, site, run_totals, config.profile.name)
                if await tg.send_many(owner_chat, msgs):
                    store.mark_notified([it["job"]["uid"] for it in items], OWNER)
                    out["digest"] = len(items)

        # public channel
        if channel:
            jobs = pick_channel_jobs(store, int(tcfg.get("channel_posts_per_run", 8)))
            if jobs and await tg.send_many(channel, format_channel(jobs, site)):
                store.mark_posted([j["uid"] for j in jobs], "channel")
                out["channel"] = len(jobs)
        store.commit()
    finally:
        await tg.aclose()
    return out


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
async def run(
    config: Config,
    *,
    use_llm: bool = True,
    notify: bool = True,
    force_digest: bool | None = None,
    transports: Transports | None = None,
) -> dict[str, Any]:
    transports = transports or Transports()
    taxonomy = SkillTaxonomy(config.taxonomy)
    store = Store(config.db_path)
    router = LLMRouter(config, store, transport=transports.llm, enabled=use_llm)
    run_id = store.start_run()
    t0 = time.monotonic()
    stats: dict[str, Any] = {"started_at": iso(utcnow())}
    status = "failed"
    try:
        results, skipped, requests = await fetch_all(config, store, transports.sources)
        ing = ingest(config, store, results)
        stats |= {
            "sources_skipped": skipped,
            "http_requests": requests,
            **ing["totals"],
            "targets": ing["targets"],
        }

        stats["enrich"] = await enrich_jobs(config, store, taxonomy, router)
        stats["match"] = await match_owner(config, store, taxonomy, router)

        retention = config.section("retention")
        stats["pruned"] = store.prune(
            int(retention.get("delete_closed_after_days", 14)),
            int(retention.get("delete_after_days", 60)),
        )
        store.commit()
        stats["open_jobs"] = len(store.open_jobs())
        stats["new_jobs"] = stats["new"]

        if notify:
            stats["notify"] = await notify_all(
                config, store, stats, force_digest, transports.telegram
            )

        failed = [t for t in ing["targets"] if not t["ok"]]
        status = "degraded" if failed and len(failed) >= max(3, len(ing["targets"]) // 3) else "ok"
    finally:
        stats["llm"] = router.stats.as_dict()
        stats["duration_s"] = round(time.monotonic() - t0, 1)
        store.finish_run(run_id, status, stats)
        await router.aclose()
        try:
            if status != "failed":
                stats["site"] = build_site(store, config, taxonomy)
        finally:
            store.close()
    return stats


# --------------------------------------------------------------------------------------
def summary_markdown(stats: dict[str, Any]) -> str:
    """Human-readable run report (written to $GITHUB_STEP_SUMMARY in CI)."""
    llm = stats.get("llm") or {}
    enrich = stats.get("enrich") or {}
    lines = [
        "## JobRadar run",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Jobs fetched / relevant | {stats.get('fetched', 0)} / {stats.get('relevant', 0)} |",
        f"| New · updated · closed | {stats.get('new', 0)} · {stats.get('updated', 0)} · {stats.get('closed', 0)} |",
        f"| Open jobs on site | {stats.get('open_jobs', 0)} |",
        f"| Enriched by AI / rules | {enrich.get('llm', 0)} / {enrich.get('rules', 0)} |",
        f"| LLM requests · tokens · cache hits | {llm.get('requests', 0)} · {llm.get('tokens', 0)} · {llm.get('cache_hits', 0)} |",
        f"| HTTP requests | {stats.get('http_requests', 0)} |",
        f"| Duration | {stats.get('duration_s', 0)}s |",
    ]
    if enrich.get("llm_skipped_reason"):
        lines.append(f"| LLM note | {enrich['llm_skipped_reason']} |")
    if llm.get("exhausted"):
        lines.append(
            "| Providers unavailable | "
            + "; ".join(f"{k}: {v}" for k, v in llm["exhausted"].items())
            + " |"
        )
    notify = stats.get("notify") or {}
    if notify:
        lines.append(
            f"| Telegram | digest {notify.get('digest', 0)} · channel {notify.get('channel', 0)} · alerts {notify.get('alerts', 0)}{' · ' + notify['skipped'] if notify.get('skipped') else ''} |"
        )
    match = stats.get("match") or {}
    if match:
        lines.append(
            f"| Matching | {match.get('skipped') or str(match.get('candidates', 0)) + ' candidates, ' + str(match.get('ai_scored', 0)) + ' AI-scored'} |"
        )

    lines += [
        "",
        "### Sources",
        "",
        "| Board | Status | Fetched | Relevant | New |",
        "|---|---|---|---|---|",
    ]
    for t in sorted(stats.get("targets") or [], key=lambda t: (t["ok"], t["target"])):
        status = "ok" if t["ok"] else f"**failed**: {str(t.get('error') or '')[:80]}"
        lines.append(
            f"| {t['target']} | {status} | {t['fetched']} | {t['relevant']} | {t['new']} |"
        )
    for name, reason in (stats.get("sources_skipped") or {}).items():
        lines.append(f"| {name} | skipped: {reason} | – | – | – |")
    return "\n".join(lines) + "\n"
