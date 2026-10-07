"""Offline mode: serve recorded API responses so anyone can try JobRadar without network or keys.

`python -m jobradar run --offline tests/fixtures` routes every source request to JSON
snapshots in that folder (greenhouse_<slug>.json, lever_<slug>.json, ashby_<slug>.json,
smartrecruiters_<slug>.json, himalayas.json, remotive.json, remoteok.json, jobicy.json,
adzuna.json). Boards without a snapshot return an empty list.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

Payload = Any
Resolver = Callable[[httpx.Request], Payload | None]

_ROUTES: list[tuple[str, re.Pattern[str], Callable[[re.Match[str]], str], Payload]] = [
    (
        "boards-api.greenhouse.io",
        re.compile(r"/v1/boards/([^/]+)/jobs"),
        lambda m: f"greenhouse_{m[1]}",
        {"jobs": []},
    ),
    ("api.lever.co", re.compile(r"/v0/postings/([^/?]+)"), lambda m: f"lever_{m[1]}", []),
    (
        "api.ashbyhq.com",
        re.compile(r"/posting-api/job-board/([^/?]+)"),
        lambda m: f"ashby_{m[1]}",
        {"jobs": []},
    ),
    (
        "api.smartrecruiters.com",
        re.compile(r"/v1/companies/([^/]+)/postings$"),
        lambda m: f"smartrecruiters_{m[1]}",
        {"content": [], "totalFound": 0},
    ),
    (
        "api.smartrecruiters.com",
        re.compile(r"/v1/companies/([^/]+)/postings/(.+)$"),
        lambda m: f"smartrecruiters_{m[1]}_{m[2]}",
        {},
    ),
    ("himalayas.app", re.compile(r"/jobs/api"), lambda m: "himalayas", {"jobs": []}),
    ("remotive.com", re.compile(r"/api/remote-jobs"), lambda m: "remotive", {"jobs": []}),
    ("remoteok.com", re.compile(r"/api"), lambda m: "remoteok", [{"legal": "offline"}]),
    ("jobicy.com", re.compile(r"/api/v2/remote-jobs"), lambda m: "jobicy", {"jobs": []}),
    (
        "api.adzuna.com",
        re.compile(r"/v1/api/jobs/\w+/search/(\d+)"),
        lambda m: "adzuna" if m[1] == "1" else "adzuna_page2",
        {"results": []},
    ),
]


def fixture_transport(
    directories: list[Path | str], overrides: dict[str, Payload] | None = None
) -> httpx.MockTransport:
    """Build an httpx transport that answers source API calls from JSON files.

    `overrides` maps a fixture name (e.g. "himalayas") to an in-memory payload, which
    tests use for data that must have fresh timestamps.
    """
    dirs = [Path(d) for d in directories]
    overrides = overrides or {}

    def load(name: str) -> Payload | None:
        if name in overrides:
            return overrides[name]
        for d in dirs:
            f = d / f"{name}.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        for route_host, pattern, name_fn, empty in _ROUTES:
            if host != route_host:
                continue
            m = pattern.search(path)
            if not m:
                continue
            name = name_fn(m)
            payload = load(name)
            if payload is None:
                payload = empty
            # Himalayas paging: only page 1 is recorded
            if route_host == "himalayas.app" and request.url.params.get("page", "1") != "1":
                payload = {"jobs": []}
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": f"no offline route for {host}{path}"})

    return httpx.MockTransport(handler)
