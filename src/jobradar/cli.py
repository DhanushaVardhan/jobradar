"""Command-line interface: `python -m jobradar <command>`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx

from jobradar import __version__
from jobradar.config import load_config
from jobradar.http import Http, HttpError
from jobradar.normalize import enrich_location
from jobradar.pipeline import run, summary_markdown
from jobradar.site import build_site
from jobradar.skills import SkillTaxonomy
from jobradar.sources.ats import ATS_SOURCES
from jobradar.store import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


# --------------------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    digest = True if args.digest else False if args.no_digest else None
    transports = None
    if args.offline:
        from jobradar.offline import fixture_transport
        from jobradar.pipeline import Transports

        transports = Transports(sources=fixture_transport([args.offline]))
        print(f"Offline mode: serving source APIs from {args.offline}")
    stats = asyncio.run(
        run(
            config,
            use_llm=not args.no_llm,
            notify=not args.no_notify,
            force_digest=digest,
            transports=transports,
        )
    )
    report = summary_markdown(stats)
    print(report)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    if args.json:
        Path(args.json).write_text(json.dumps(stats, indent=2, default=str), encoding="utf-8")
    return 0


def cmd_site(args: argparse.Namespace) -> int:
    config = load_config()
    store = Store(config.db_path)
    try:
        info = build_site(store, config, SkillTaxonomy(config.taxonomy))
    finally:
        store.close()
    print(f"Built site in {config.site_dir}: {info}")
    return 0


async def _probe_ats(slug: str, config) -> list[tuple[str, int, int, str | None]]:
    out = []
    async with Http(
        user_agent=config.section("http").get("user_agent", "JobRadar"), retries=1
    ) as http:
        for cls in ATS_SOURCES:
            src = cls(http, config)
            try:
                res = await src.fetch_board(slug)
                india = sum(1 for j in res.jobs if enrich_location(j).is_india or j.is_remote)
                out.append((cls.name, len(res.jobs), india, None))
            except HttpError as exc:
                out.append((cls.name, 0, 0, str(exc.status or exc)))
            except Exception as exc:  # pragma: no cover - diagnostics only
                out.append((cls.name, 0, 0, type(exc).__name__))
    return out


def cmd_discover(args: argparse.Namespace) -> int:
    config = load_config()
    for slug in args.slugs:
        print(f"\n{slug}")
        found = False
        for ats, total, india, err in asyncio.run(_probe_ats(slug, config)):
            if err:
                print(f"  {ats:16} not found ({err})")
            else:
                found = found or total > 0
                print(f"  {ats:16} {total:4} jobs, {india} in India/remote")
                if total:
                    print(
                        f"    -> add under `{ats}:` in config/companies.yaml:  - {{ slug: {slug}, name: {slug.title()} }}"
                    )
        if not found:
            print("  No public board found. Try the slug from the company's careers URL.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config()
    ok = True
    print(f"JobRadar {__version__}\n")
    print("Secrets / environment:")
    for name, required in [
        ("GROQ_API_KEY", "recommended"),
        ("GEMINI_API_KEY", "optional backup LLM"),
        ("TELEGRAM_BOT_TOKEN", "optional"),
        ("TELEGRAM_CHAT_ID", "optional"),
        ("TELEGRAM_CHANNEL_ID", "optional"),
        ("ADZUNA_APP_ID", "optional"),
        ("ADZUNA_APP_KEY", "optional"),
        ("JOBRADAR_PROFILE", "optional"),
    ]:
        print(f"  {'set ' if config.env(name) else '---'}  {name:22} ({required})")
    prof = config.profile
    print(
        f"\nProfile: {'loaded — ' + (prof.name or 'unnamed') if prof and prof.is_configured else 'not set (no personal digest)'}"
    )
    try:
        tax = SkillTaxonomy(config.taxonomy)
        print(f"Skills taxonomy: {len(tax.category_of)} skills, {len(tax.patterns)} patterns")
    except Exception as exc:
        ok = False
        print(f"Skills taxonomy: INVALID ({exc})")

    if not args.offline:
        print("\nCompany boards:")

        async def check() -> None:
            async with Http(
                user_agent=config.section("http").get("user_agent", "JobRadar"), retries=1
            ) as http:
                for cls in ATS_SOURCES:
                    src = cls(http, config)
                    results = await src.fetch()
                    for r in results:
                        india = sum(1 for j in r.jobs if enrich_location(j).is_india)
                        mark = "ok " if r.ok else "ERR"
                        print(
                            f"  {mark} {r.target:32} {len(r.jobs):4} jobs  {india:3} India  {r.error or ''}"
                        )

        asyncio.run(check())
    return 0 if ok else 1


def cmd_telegram(args: argparse.Namespace) -> int:
    """Helps with setup: prints chat ids that messaged the bot, or sends a test message."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Set TELEGRAM_BOT_TOKEN first (create a bot with @BotFather).")
        return 1
    base = f"https://api.telegram.org/bot{token}"
    if args.send is not None:
        chat = args.send or os.environ.get("TELEGRAM_CHAT_ID")
        if not chat:
            print("Give a chat id: `jobradar telegram --send <chat_id>` or set TELEGRAM_CHAT_ID.")
            return 1
        r = httpx.post(
            f"{base}/sendMessage",
            json={"chat_id": chat, "text": "JobRadar is connected ✔"},
            timeout=30,
        )
        print(r.status_code, r.text[:300])
        return 0 if r.status_code == 200 else 1
    r = httpx.get(f"{base}/getUpdates", timeout=30)
    updates = r.json().get("result", [])
    if not updates:
        print(
            "No messages yet. Send any message to your bot (or add it to your channel), then run this again."
        )
    seen = set()
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") in seen:
            continue
        seen.add(chat.get("id"))
        print(
            f"chat_id={chat.get('id'):<16} type={chat.get('type'):<8} {chat.get('title') or chat.get('username') or chat.get('first_name')}"
        )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    config = load_config()
    store = Store(config.db_path)
    try:
        jobs = store.open_jobs()
        print(f"{len(jobs)} open jobs in {config.db_path}")
        for r in store.recent_runs(5):
            s = r["stats"]
            print(
                f"  {r['started_at']}  {r['status']:8} new={s.get('new')} llm={(s.get('llm') or {}).get('requests')} {s.get('duration_s')}s"
            )
    finally:
        store.close()
    return 0


# --------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobradar", description="AI-powered job radar for India")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="full pipeline: fetch, enrich, match, notify, build site")
    r.add_argument("--no-llm", action="store_true", help="rule-based enrichment only")
    r.add_argument("--no-notify", action="store_true", help="don't send Telegram messages")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--digest", action="store_true", help="force the personal digest this run")
    g.add_argument(
        "--no-digest", action="store_true", help="never send the personal digest this run"
    )
    r.add_argument(
        "--summary", help="append a Markdown report to this file (e.g. $GITHUB_STEP_SUMMARY)"
    )
    r.add_argument("--json", help="write full run stats as JSON")
    r.add_argument(
        "--offline", metavar="DIR", help="use recorded API responses from DIR (no network)"
    )
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("site", help="rebuild the static site from the database")
    s.set_defaults(func=cmd_site)

    d = sub.add_parser("discover", help="find which ATS a company uses, e.g. `discover razorpay`")
    d.add_argument("slugs", nargs="+")
    d.set_defaults(func=cmd_discover)

    doc = sub.add_parser("doctor", help="check secrets, profile, taxonomy and every company board")
    doc.add_argument("--offline", action="store_true", help="skip network checks")
    doc.set_defaults(func=cmd_doctor)

    t = sub.add_parser("telegram", help="list chat ids that messaged your bot, or --send a test")
    t.add_argument(
        "--send", nargs="?", const="", help="send a test message (to TELEGRAM_CHAT_ID or given id)"
    )
    t.set_defaults(func=cmd_telegram)

    st = sub.add_parser("stats", help="show database stats and recent runs")
    st.set_defaults(func=cmd_stats)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
