# Architecture & design decisions

## Run lifecycle

```
python -m jobradar run
│
├─ fetch_all()          all sources concurrently (asyncio + httpx)
│    └─ Source.guarded()   every board is isolated: a 404 or parser bug becomes a failed
│                          TargetResult, recorded in source_health, never a crashed run
├─ ingest()             tidy titles → parse India locations → relevance filter →
│                       upsert (new / updated / reopened) → close jobs missing from complete
│                       boards → close stale aggregator jobs → mark cross-source duplicates
├─ enrich_jobs()        1) rule_enrich for every un-enriched job (instant, free)
│                       2) LLM upgrade for recent jobs: batches of 5, one async worker per
│                          provider, a time budget, stops cleanly when quota runs out
├─ match_owner()        rule_score all recent jobs → top K re-ranked by LLM vs full resume
├─ prune()              delete closed/old rows, orphaned matches, old cache and run rows
├─ notify_all()         health alerts, personal digest (morning run), public channel posts
└─ build_site()         jobs.json, stats.json, taxonomy.json, RSS feeds, static web app
```

## Key decisions

**1. Precompute per job, match per user in the browser.**
LLM calls are the scarce resource (free tiers allow about 1,000 requests and 200k tokens per model per day). Enriching each posting once and publishing structured data means the LLM bill scales with *new jobs per day* (hundreds), not with *visitors* (unbounded). Visitor matching runs in `web/match.js`, so resumes never leave the device and the site needs no backend.

**2. Two implementations of one scoring function, kept honest by a test.**
`match.py` and `web/match.js` implement the same formula. `tests/test_web_parity.py` runs both on the same jobs and profile (through Node) and fails if skills, matched/missing lists or scores drift apart.

**3. A shared taxonomy instead of free-text skills.**
LLMs return "Rest API", "RESTful services" and "REST". Everything is mapped onto 165 canonical skills (`config/skills.yaml`) with aliases, case-sensitive rules for ambiguous words ("React", "Spark", "Go") and regex guards ("SOC 2" is not SIEM; "Series C" is not the C language). The exported patterns are valid in both Python and JavaScript.

**4. An LLM router built for free tiers** (`llm.py`)
- Provider chain per task (`enrich`: small fast models; `match`: the 120B model), via OpenAI-compatible APIs.
- Local sliding-window limiter for requests and tokens per minute, so it waits instead of collecting 429s.
- Per-day usage stored in SQLite, so the budget holds across the two daily runs; a global daily cap on top.
- Reads `x-ratelimit-*` and `retry-after` headers; short waits are retried, day-level limits fail over.
- Handles 401/413/5xx, invalid JSON (retry), and providers that reject `response_format` (turns JSON mode off).
- Response cache keyed by prompt hash, so re-runs and crash-restarts never pay twice.
- Returns `None` when everything is exhausted, and callers fall back to rules. The pipeline never fails because of an LLM.

**5. Field-level validation of LLM output.**
`validate_llm_item` checks every field (enums, integer ranges, min ≤ max, salary shape, skill canonicalisation) and falls back to the rule value per field. A partly wrong answer improves the record without corrupting it.

**6. Correct job lifecycles.**
ATS boards return *every* open job, so a job missing from a successful fetch is closed (and reopened if it returns). Aggregator feeds are partial, so their jobs close after N days unseen. A failed fetch never closes anything.

**7. State in SQLite on an orphan `data` branch.**
The workflow restores `jobradar.db.gz` from the `data` branch, runs, VACUUMs, and force-pushes a **single-commit** snapshot. The state survives between runs, main history stays clean, and the repository doesn't grow. There is no database server to sign up for or keep awake.

**8. Observability without infrastructure.**
Each run writes a Markdown report to the Actions job summary (per-board counts and errors, LLM requests/tokens/cache hits, notifications). `source_health` tracks consecutive failures; the owner gets one Telegram alert when a board crosses the threshold, and the site's Insights tab shows run history and board status.

**9. Politeness and terms of use.**
Only documented public JSON endpoints. Global and per-host concurrency limits, exponential backoff with jitter, an identifying User-Agent, attribution and links back for aggregators, and Remotive at most twice a day.

## Data model (SQLite)

| Table | Purpose |
|---|---|
| `jobs` | one row per posting: raw facts, derived location, fingerprint, lifecycle timestamps, `skills`, `enrichment` JSON, `enriched_by` |
| `runs` | start/finish/status and the full stats JSON of each run |
| `source_health` | per-board last success, last error, consecutive failures, last count |
| `llm_usage` | requests and tokens per provider per UTC day (budgeting) |
| `llm_cache` | prompt-hash → response (30-day TTL) |
| `matches` | owner's rule score, AI fit, reasons, notified timestamp |
| `posts` | which jobs were posted to which channel (no duplicates) |

## Testing strategy

All network I/O goes through injectable `httpx` transports:

- `offline.fixture_transport`: recorded Greenhouse/Lever/Ashby responses, plus in-memory synthetic payloads for aggregators (with fresh timestamps).
- `tests/helpers.FakeLLM`: an OpenAI-compatible fake that can return 429, 401, day limits and invalid JSON on demand.
- `tests/helpers.FakeTelegram`: records messages and can reject HTML parse mode.

`test_pipeline.py` runs the whole product end to end several times, checking idempotency (no duplicate notifications), job closing, key-less degradation, valid RSS and site output.
