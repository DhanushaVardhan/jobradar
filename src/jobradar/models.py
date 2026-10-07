"""Core data model shared by every source, the store, the matcher and the site builder."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


def stable_id(*parts: str, length: int = 16) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:length]


@dataclass
class Job:
    # identity ------------------------------------------------------------------
    source: str  # connector name: greenhouse, lever, himalayas, ...
    source_id: str  # id inside that source
    target: str  # board the job came from, e.g. "greenhouse:databricks"
    company: str
    title: str
    url: str  # canonical link to the original posting (attribution!)

    # raw facts from the source -----------------------------------------------------
    location_raw: str = ""
    description: str = ""  # plain text
    posted_at: str | None = None  # ISO-8601 UTC
    remote_hint: bool | None = None
    country_hint: str | None = None
    employment_type: str | None = None
    department: str | None = None
    salary_raw: str | None = None
    tags: list[str] = field(default_factory=list)
    via: str | None = None  # aggregator credit, e.g. "Himalayas"

    # derived by normalize.enrich_location --------------------------------------------
    cities: list[str] = field(default_factory=list)
    is_india: bool = False
    is_remote: bool = False
    remote_scope: str | None = None  # india | global | apac | other

    @property
    def uid(self) -> str:
        return stable_id(self.source, self.source_id)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__} | {"uid": self.uid}


@dataclass
class TargetResult:
    """Outcome of fetching one board / feed."""

    target: str
    source: str
    jobs: list[Job] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    complete: bool = False  # True when `jobs` is the full list of open jobs on that board
