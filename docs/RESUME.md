# Putting JobRadar on your resume

Replace the bracketed numbers with real ones from your deployment. The site's **Insights** tab and each Actions run summary show them.

## Project entry (pick 3–4 bullets)

**JobRadar: AI-powered job aggregator for India** · Python, asyncio, SQLite, LLM APIs (Groq/Gemini), GitHub Actions, JavaScript · [live site](https://dhanushavardhan.github.io/jobradar/) · [GitHub](https://github.com/DhanushaVardhan/jobradar)

- Built an automated pipeline that aggregates **[1,500+] jobs/day** from **[20+]** company career boards (Greenhouse, Lever, Ashby, SmartRecruiters) and 5 job APIs, with async fetching, retries/backoff and per-host rate limiting.
- Designed an **LLM enrichment layer** (Groq, Gemini fallback) that turns job descriptions into structured data (skills, experience, seniority, fresher-eligibility), with batched prompts, token-aware rate limiting, daily budgets, response caching and a rule-based fallback, running **[~100] LLM calls/day at ₹0**.
- Implemented resume-to-job matching (weighted skill/title/experience/location score plus LLM re-ranking) and delivered a daily personalised **Telegram digest**; the same scorer runs **client-side in JavaScript**, so visitors' resumes never leave their browser (kept in sync by a parity test).
- Shipped a public, mobile-friendly site on GitHub Pages with filters, skill-gap analysis, market insights and per-skill RSS feeds, serving **[N] visitors** with no backend.
- Automated operations with **GitHub Actions** (twice-daily cron, SQLite state on a data branch, health alerts, run reports) and **60+ tests** with mocked external services, including an offline end-to-end run in CI.

## One-liner (for LinkedIn headline or a short resume)

> Built JobRadar, a zero-cost AI job aggregator that collects 1,000+ jobs/day from 25+ sources, extracts skills with LLMs, and matches them to resumes. Fully automated on GitHub Actions.

## Interview talking points

1. **Why precompute enrichment?** Free LLM quotas are per day. Enriching each job once and matching in the browser makes cost independent of traffic.
2. **Rate limits in practice:** sliding-window limiter for RPM/TPM, per-day budget persisted across runs, header-driven backoff, provider failover, cache. What happens on a 429 with `retry-after: 2m`, and what happens on a daily limit?
3. **Trusting LLM output:** field-level validation against enums and a taxonomy; fall back per field, never per record.
4. **Data quality:** India location normalisation, cross-source dedupe with fingerprints and source priority, closing jobs that vanish from a complete board but not from partial feeds.
5. **Testing external systems:** injectable httpx transports, fake LLM and Telegram, recorded fixtures, parity test between Python and JS.
6. **Trade-offs you'd revisit:** SQLite-in-git suits one writer and small data. With per-user alerts you'd move to a hosted database (Postgres/D1) plus a webhook bot.

## Metrics worth tracking (and screenshotting)

- Open jobs, new jobs/day, number of companies and sources (Insights tab)
- LLM requests/tokens per day and cache hit rate (Actions run summary)
- Uptime: successful scheduled runs over 30 days (Actions history)
- Users: Telegram channel members, RSS subscribers, GitHub stars
