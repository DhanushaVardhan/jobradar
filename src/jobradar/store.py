"""SQLite persistence: jobs, run history, source health, LLM usage/cache, matches, posts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from typing import Any

from jobradar.models import Job, TargetResult
from jobradar.normalize import fingerprint, iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    target        TEXT NOT NULL,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    location_raw  TEXT,
    cities        TEXT NOT NULL DEFAULT '[]',
    is_india      INTEGER NOT NULL DEFAULT 0,
    is_remote     INTEGER NOT NULL DEFAULT 0,
    remote_scope  TEXT,
    description   TEXT,
    posted_at     TEXT,
    employment_type TEXT,
    department    TEXT,
    salary_raw    TEXT,
    tags          TEXT NOT NULL DEFAULT '[]',
    via           TEXT,
    fingerprint   TEXT,
    duplicate_of  TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    closed_at     TEXT,
    content_hash  TEXT,
    skills        TEXT NOT NULL DEFAULT '[]',
    enrichment    TEXT,
    enriched_by   TEXT,
    enriched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_open ON jobs(closed_at, duplicate_of);
CREATE INDEX IF NOT EXISTS idx_jobs_fp ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS idx_jobs_target ON jobs(target);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    stats       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS source_health (
    target               TEXT PRIMARY KEY,
    source               TEXT NOT NULL,
    last_ok              TEXT,
    last_attempt         TEXT,
    last_error           TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_count           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS llm_usage (
    day      TEXT NOT NULL,
    provider TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    tokens   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, provider)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key        TEXT PRIMARY KEY,
    provider   TEXT,
    response   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    uid         TEXT NOT NULL,
    profile     TEXT NOT NULL,
    score       REAL NOT NULL,
    ai_fit      INTEGER,
    details     TEXT NOT NULL DEFAULT '{}',
    scored_at   TEXT NOT NULL,
    notified_at TEXT,
    PRIMARY KEY (uid, profile)
);

CREATE TABLE IF NOT EXISTS posts (
    uid       TEXT NOT NULL,
    channel   TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    PRIMARY KEY (uid, channel)
);
"""

JSON_COLS = ("cities", "tags", "skills")


def _content_hash(job: Job) -> str:
    import hashlib

    return hashlib.sha1(f"{job.title}\x1f{job.description[:4000]}".encode()).hexdigest()[:16]


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def commit(self) -> None:
        self.db.commit()

    # ------------------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------------------
    def known_uids(self, source: str | None = None) -> set[str]:
        q = "SELECT uid FROM jobs" + (" WHERE source = ?" if source else "")
        return {r[0] for r in self.db.execute(q, (source,) if source else ())}

    def upsert_jobs(self, jobs: Iterable[Job], now: str | None = None) -> dict[str, int]:
        """Insert new jobs / refresh existing ones. Returns counts."""
        now = now or iso(utcnow())
        counts = {"new": 0, "updated": 0, "unchanged": 0, "reopened": 0}
        cur = self.db.cursor()
        for job in jobs:
            chash = _content_hash(job)
            row = cur.execute(
                "SELECT content_hash, closed_at, description FROM jobs WHERE uid = ?", (job.uid,)
            ).fetchone()
            if row is None:
                cur.execute(
                    """INSERT INTO jobs (uid, source, source_id, target, company, title, url, location_raw,
                        cities, is_india, is_remote, remote_scope, description, posted_at, employment_type,
                        department, salary_raw, tags, via, fingerprint, first_seen, last_seen, content_hash)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job.uid,
                        job.source,
                        job.source_id,
                        job.target,
                        job.company,
                        job.title,
                        job.url,
                        job.location_raw,
                        json.dumps(job.cities),
                        int(job.is_india),
                        int(job.is_remote),
                        job.remote_scope,
                        job.description,
                        job.posted_at or now,
                        job.employment_type,
                        job.department,
                        job.salary_raw,
                        json.dumps(job.tags),
                        job.via,
                        fingerprint(job),
                        now,
                        now,
                        chash,
                    ),
                )
                counts["new"] += 1
                continue

            changed = row["content_hash"] != chash and bool(
                job.description or not row["description"]
            )
            if row["closed_at"]:
                counts["reopened"] += 1
            fields = {
                "last_seen": now,
                "closed_at": None,
                "title": job.title,
                "url": job.url,
                "location_raw": job.location_raw,
                "cities": json.dumps(job.cities),
                "is_india": int(job.is_india),
                "is_remote": int(job.is_remote),
                "remote_scope": job.remote_scope,
                "salary_raw": job.salary_raw,
                "fingerprint": fingerprint(job),
            }
            if changed:
                fields |= {
                    "description": job.description,
                    "content_hash": chash,
                    "enriched_at": None,
                }
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
            sets = ", ".join(f"{k} = ?" for k in fields)
            cur.execute(f"UPDATE jobs SET {sets} WHERE uid = ?", (*fields.values(), job.uid))
        return counts

    def close_missing(self, result: TargetResult, now: str | None = None) -> int:
        """For complete board listings: close open jobs that disappeared from the board."""
        if not (result.ok and result.complete):
            return 0
        now = now or iso(utcnow())
        seen = [j.uid for j in result.jobs]
        placeholders = ",".join("?" * len(seen)) or "''"
        cur = self.db.execute(
            f"UPDATE jobs SET closed_at = ? WHERE target = ? AND closed_at IS NULL AND uid NOT IN ({placeholders})",
            (now, result.target, *seen),
        )
        return cur.rowcount

    def close_stale(self, days: int, now=None) -> int:
        """For partial feeds: close jobs not seen for `days`."""
        now = now or utcnow()
        cutoff = iso(now - timedelta(days=days))
        cur = self.db.execute(
            "UPDATE jobs SET closed_at = ? WHERE closed_at IS NULL AND last_seen < ?",
            (iso(now), cutoff),
        )
        return cur.rowcount

    def mark_duplicates(self, priority: dict[str, int]) -> int:
        """Collapse the same job posted on several sources onto one canonical row."""
        self.db.execute("UPDATE jobs SET duplicate_of = NULL WHERE duplicate_of IS NOT NULL")
        rows = self.db.execute(
            """SELECT uid, source, fingerprint, first_seen FROM jobs
               WHERE closed_at IS NULL AND fingerprint IN (
                   SELECT fingerprint FROM jobs WHERE closed_at IS NULL
                   GROUP BY fingerprint HAVING COUNT(*) > 1)"""
        ).fetchall()
        groups: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            groups.setdefault(r["fingerprint"], []).append(r)
        marked = 0
        for members in groups.values():
            members.sort(key=lambda r: (priority.get(r["source"], 9), r["first_seen"], r["uid"]))
            keeper = members[0]["uid"]
            for r in members[1:]:
                self.db.execute(
                    "UPDATE jobs SET duplicate_of = ? WHERE uid = ?", (keeper, r["uid"])
                )
                marked += 1
        return marked

    def prune(self, delete_closed_after_days: int, delete_after_days: int, now=None) -> int:
        now = now or utcnow()
        c1 = iso(now - timedelta(days=delete_closed_after_days))
        c2 = iso(now - timedelta(days=delete_after_days))
        cur = self.db.execute(
            "DELETE FROM jobs WHERE (closed_at IS NOT NULL AND closed_at < ?) OR first_seen < ?",
            (c1, c2),
        )
        n = cur.rowcount
        self.db.execute("DELETE FROM matches WHERE uid NOT IN (SELECT uid FROM jobs)")
        self.db.execute("DELETE FROM posts WHERE uid NOT IN (SELECT uid FROM jobs)")
        self.db.execute(
            "DELETE FROM llm_cache WHERE created_at < ?", (iso(now - timedelta(days=30)),)
        )
        self.db.execute("DELETE FROM runs WHERE started_at < ?", (iso(now - timedelta(days=120)),))
        return n

    # ------------------------------------------------------------------------------
    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for col in JSON_COLS:
            if col in d:
                d[col] = json.loads(d[col] or "[]")
        if "enrichment" in d:
            d["enrichment"] = json.loads(d["enrichment"]) if d["enrichment"] else {}
        for col in ("is_india", "is_remote"):
            if col in d:
                d[col] = bool(d[col])
        return d

    def open_jobs(self, include_duplicates: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM jobs WHERE closed_at IS NULL"
        if not include_duplicates:
            q += " AND duplicate_of IS NULL"
        q += " ORDER BY COALESCE(posted_at, first_seen) DESC"
        return [self._row_to_dict(r) for r in self.db.execute(q)]

    def get_job(self, uid: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM jobs WHERE uid = ?", (uid,)).fetchone()
        return self._row_to_dict(row) if row else None

    def jobs_needing_enrichment(
        self, llm: bool, limit: int, max_age_days: int = 4
    ) -> list[dict[str, Any]]:
        """llm=False: never enriched at all. llm=True: rule-enriched recent jobs to upgrade via LLM."""
        if not llm:
            q = "SELECT * FROM jobs WHERE enriched_at IS NULL AND closed_at IS NULL LIMIT ?"
            return [self._row_to_dict(r) for r in self.db.execute(q, (limit,))]
        cutoff = iso(utcnow() - timedelta(days=max_age_days))
        q = """SELECT * FROM jobs
               WHERE closed_at IS NULL AND duplicate_of IS NULL
                 AND (enriched_by IS NULL OR enriched_by = 'rules') AND first_seen >= ?
               ORDER BY is_india DESC, first_seen DESC, COALESCE(posted_at, first_seen) DESC
               LIMIT ?"""
        return [self._row_to_dict(r) for r in self.db.execute(q, (cutoff, limit))]

    def save_enrichment(
        self, uid: str, skills: list[str], enrichment: dict[str, Any], by: str
    ) -> None:
        self.db.execute(
            "UPDATE jobs SET skills = ?, enrichment = ?, enriched_by = ?, enriched_at = ? WHERE uid = ?",
            (
                json.dumps(skills),
                json.dumps(enrichment, ensure_ascii=False),
                by,
                iso(utcnow()),
                uid,
            ),
        )

    # ------------------------------------------------------------------------------
    # Runs & health
    # ------------------------------------------------------------------------------
    def start_run(self) -> int:
        cur = self.db.execute(
            "INSERT INTO runs (started_at, status) VALUES (?, 'running')", (iso(utcnow()),)
        )
        self.db.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, stats: dict[str, Any]) -> None:
        self.db.execute(
            "UPDATE runs SET finished_at = ?, status = ?, stats = ? WHERE id = ?",
            (iso(utcnow()), status, json.dumps(stats), run_id),
        )
        self.db.commit()

    def recent_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) | {"stats": json.loads(r["stats"] or "{}")} for r in rows]

    def record_health(self, result: TargetResult, now: str | None = None) -> None:
        now = now or iso(utcnow())
        if result.ok:
            self.db.execute(
                """INSERT INTO source_health (target, source, last_ok, last_attempt, last_error, consecutive_failures, last_count)
                   VALUES (?, ?, ?, ?, NULL, 0, ?)
                   ON CONFLICT(target) DO UPDATE SET last_ok = excluded.last_ok, last_attempt = excluded.last_attempt,
                       last_error = NULL, consecutive_failures = 0, last_count = excluded.last_count""",
                (result.target, result.source, now, now, len(result.jobs)),
            )
        else:
            self.db.execute(
                """INSERT INTO source_health (target, source, last_attempt, last_error, consecutive_failures)
                   VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(target) DO UPDATE SET last_attempt = excluded.last_attempt,
                       last_error = excluded.last_error, consecutive_failures = consecutive_failures + 1""",
                (result.target, result.source, now, result.error),
            )

    def health(self) -> list[dict[str, Any]]:
        return [
            dict(r) for r in self.db.execute("SELECT * FROM source_health ORDER BY source, target")
        ]

    # ------------------------------------------------------------------------------
    # LLM usage & cache
    # ------------------------------------------------------------------------------
    def llm_usage_today(self, provider: str, day: str) -> tuple[int, int]:
        row = self.db.execute(
            "SELECT requests, tokens FROM llm_usage WHERE day = ? AND provider = ?", (day, provider)
        ).fetchone()
        return (row["requests"], row["tokens"]) if row else (0, 0)

    def llm_requests_total(self, day: str) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM(requests), 0) FROM llm_usage WHERE day = ?", (day,)
        ).fetchone()
        return int(row[0])

    def add_llm_usage(self, provider: str, day: str, requests: int, tokens: int) -> None:
        self.db.execute(
            """INSERT INTO llm_usage (day, provider, requests, tokens) VALUES (?, ?, ?, ?)
               ON CONFLICT(day, provider) DO UPDATE SET requests = requests + excluded.requests,
                   tokens = tokens + excluded.tokens""",
            (day, provider, requests, tokens),
        )
        self.db.commit()

    def llm_usage_by_day(self, days: int = 14) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT day, provider, requests, tokens FROM llm_usage ORDER BY day DESC LIMIT ?",
            (days * 6,),
        )
        return [dict(r) for r in rows]

    def cache_get(self, key: str) -> str | None:
        row = self.db.execute("SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
        return row["response"] if row else None

    def cache_set(self, key: str, provider: str, response: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO llm_cache (key, provider, response, created_at) VALUES (?, ?, ?, ?)",
            (key, provider, response, iso(utcnow())),
        )

    # ------------------------------------------------------------------------------
    # Matches & posts
    # ------------------------------------------------------------------------------
    def save_match(
        self, uid: str, profile: str, score: float, ai_fit: int | None, details: dict
    ) -> None:
        self.db.execute(
            """INSERT INTO matches (uid, profile, score, ai_fit, details, scored_at) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(uid, profile) DO UPDATE SET score = excluded.score,
                   ai_fit = COALESCE(excluded.ai_fit, matches.ai_fit),
                   details = excluded.details, scored_at = excluded.scored_at""",
            (uid, profile, score, ai_fit, json.dumps(details, ensure_ascii=False), iso(utcnow())),
        )

    def match_row(self, uid: str, profile: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM matches WHERE uid = ? AND profile = ?", (uid, profile)
        ).fetchone()
        return dict(row) | {"details": json.loads(row["details"])} if row else None

    def unnotified_matches(self, profile: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """SELECT m.*, j.closed_at FROM matches m JOIN jobs j ON j.uid = m.uid
               WHERE m.profile = ? AND m.notified_at IS NULL AND j.closed_at IS NULL AND j.duplicate_of IS NULL""",
            (profile,),
        )
        return [dict(r) | {"details": json.loads(r["details"])} for r in rows]

    def mark_notified(self, uids: list[str], profile: str) -> None:
        now = iso(utcnow())
        self.db.executemany(
            "UPDATE matches SET notified_at = ? WHERE uid = ? AND profile = ?",
            [(now, u, profile) for u in uids],
        )

    def was_posted(self, uid: str, channel: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM posts WHERE uid = ? AND channel = ?", (uid, channel)
            ).fetchone()
            is not None
        )

    def mark_posted(self, uids: list[str], channel: str) -> None:
        now = iso(utcnow())
        self.db.executemany(
            "INSERT OR IGNORE INTO posts VALUES (?, ?, ?)", [(u, channel, now) for u in uids]
        )
