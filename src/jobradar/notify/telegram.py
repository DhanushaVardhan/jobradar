"""Telegram delivery: private digest for the owner, public channel posts, health alerts."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from jobradar.normalize import location_label

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 3900  # Telegram max is 4096 chars; keep headroom for entities
IST = ZoneInfo("Asia/Kolkata")
_TAG = re.compile(r"<[^>]+>")


def esc(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)


def _exp(enr: dict[str, Any]) -> str:
    lo, hi = enr.get("experience_min"), enr.get("experience_max")
    if lo is None and hi is None:
        return "fresher-friendly" if enr.get("fresher_friendly") else ""
    if hi is None:
        return f"{lo}+ yrs"
    return f"{lo}-{hi} yrs"


def _loc(job: dict[str, Any]) -> str:
    return location_label(
        job.get("cities") or [], bool(job.get("is_remote")), job.get("remote_scope")
    )


def hashtag(text: str) -> str:
    tag = "".join(ch for ch in text.title() if ch.isalnum())
    return f"#{tag}" if tag else ""


def format_digest(
    items: list[dict[str, Any]], site_url: str, stats: dict[str, Any], name: str = ""
) -> list[str]:
    """items: [{job, score, ai, matched, missing}] -> one or more HTML messages."""
    today = datetime.now(IST).strftime("%a %d %b")
    head = f"<b>JobRadar · {len(items)} new matches{' for ' + esc(name.split()[0]) if name else ''}</b> · {today}\n"
    blocks = []
    for i, it in enumerate(items, 1):
        j, ai = it["job"], it.get("ai") or {}
        enr = j.get("enrichment") or {}
        lines = [f"<b>{i}. {esc(j['title'])}</b>", f"{esc(j['company'])} · {esc(_loc(j))}"]
        meta = [f"Match {it['score']:.0f}/100"]
        if ai.get("verdict"):
            meta.append(ai["verdict"])
        if _exp(enr):
            meta.append(_exp(enr))
        lines.append(" · ".join(meta))
        if it.get("matched"):
            lines.append("✓ " + esc(", ".join(it["matched"][:6])))
        gaps = ai.get("gaps") or it.get("missing") or []
        if gaps:
            lines.append("✗ " + esc(", ".join(gaps[:3])))
        if ai.get("tip"):
            lines.append("<i>Tip: " + esc(ai["tip"]) + "</i>")
        elif enr.get("summary"):
            lines.append("<i>" + esc(enr["summary"]) + "</i>")
        lines.append(
            f'<a href="{esc(j["url"])}">Open posting</a>'
            + (f" · via {esc(j['via'])}" if j.get("via") else "")
        )
        blocks.append("\n".join(lines))
    foot = (
        f"\n{stats.get('new_jobs', 0)} new jobs today · {stats.get('open_jobs', 0)} open · "
        f'<a href="{esc(site_url)}">browse all</a>'
    )
    return pack(head, blocks, foot)


def format_channel(jobs: list[dict[str, Any]], site_url: str) -> list[str]:
    head = "<b>Fresh jobs · JobRadar India</b>\n"
    blocks = []
    for j in jobs:
        enr = j.get("enrichment") or {}
        tags = [hashtag(c) for c in (j.get("cities") or [])[:2]]
        tags += [hashtag(s) for s in (j.get("skills") or [])[:3]]
        if enr.get("fresher_friendly"):
            tags.append("#Fresher")
        if j.get("is_remote"):
            tags.append("#Remote")
        exp = _exp(enr)
        lines = [
            f"<b>{esc(j['title'])}</b> — {esc(j['company'])}",
            f"{esc(_loc(j))}{' · ' + exp if exp else ''}",
        ]
        if enr.get("summary"):
            lines.append(esc(enr["summary"]))
        lines.append(
            f'<a href="{esc(j["url"])}">Apply</a>'
            + (f" · via {esc(j['via'])}" if j.get("via") else "")
        )
        lines.append(" ".join(t for t in tags if t))
        blocks.append("\n".join(lines))
    return pack(
        head, blocks, f'\n<a href="{esc(site_url)}">Search all jobs & match your resume</a>'
    )


def pack(head: str, blocks: list[str], foot: str) -> list[str]:
    """Split blocks into messages under Telegram's size limit, never splitting a block."""
    messages, cur = [], head
    for b in blocks:
        if len(cur) + len(b) + 2 > LIMIT:
            messages.append(cur.rstrip())
            cur = ""
        cur += b + "\n\n"
    if len(cur) + len(foot) > LIMIT:
        messages.append(cur.rstrip())
        cur = ""
    cur += foot
    messages.append(cur.strip())
    return [m for m in messages if m]


class Telegram:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.token = token
        self.client = httpx.AsyncClient(timeout=30, transport=transport)
        self.sent = 0

    async def aclose(self) -> None:
        await self.client.aclose()

    async def send(self, chat_id: str, text: str) -> bool:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        for _ in range(3):
            resp = await self.client.post(
                API.format(token=self.token, method="sendMessage"), json=body
            )
            if resp.status_code == 200:
                self.sent += 1
                return True
            if resp.status_code == 429:
                retry = (resp.json().get("parameters") or {}).get("retry_after", 3)
                await asyncio.sleep(min(float(retry), 30) + 0.5)
                continue
            log.warning("Telegram sendMessage failed (%s): %s", resp.status_code, resp.text[:300])
            if resp.status_code == 400 and "parse" in resp.text.lower() and "parse_mode" in body:
                # HTML rejected: resend as plain text rather than lose the message
                body = {
                    "chat_id": chat_id,
                    "text": html.unescape(_TAG.sub("", text)),
                    "disable_web_page_preview": True,
                }
                continue
            return False
        return False

    async def send_many(self, chat_id: str, messages: list[str]) -> int:
        ok = 0
        for m in messages:
            if await self.send(chat_id, m):
                ok += 1
            await asyncio.sleep(1.1)  # stay well under per-chat limits
        return ok
