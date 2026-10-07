"""Source registry."""

from __future__ import annotations

from jobradar.sources.aggregators import AGGREGATOR_SOURCES
from jobradar.sources.ats import ATS_SOURCES
from jobradar.sources.base import Source

ALL_SOURCES: list[type[Source]] = [*ATS_SOURCES, *AGGREGATOR_SOURCES]
SOURCE_BY_NAME: dict[str, type[Source]] = {s.name: s for s in ALL_SOURCES}

# When the same job is found twice, keep the copy from the more authoritative source.
SOURCE_PRIORITY: dict[str, int] = {
    "greenhouse": 0,
    "lever": 0,
    "ashby": 0,
    "smartrecruiters": 0,
    "adzuna": 1,
    "himalayas": 2,
    "remotive": 2,
    "remoteok": 3,
    "jobicy": 3,
}

__all__ = ["ALL_SOURCES", "SOURCE_BY_NAME", "SOURCE_PRIORITY", "Source"]
