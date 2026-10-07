"""Configuration loading: YAML files for settings, env vars for secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("JOBRADAR_CONFIG_DIR", ROOT / "config"))


def _load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class Profile:
    """The job seeker's private profile (never published)."""

    name: str = ""
    target_roles: list[str] = field(default_factory=list)
    experience_years: float = 0
    locations: list[str] = field(default_factory=list)
    open_to_remote: bool = True
    skills: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    resume: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Profile:
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d and d[k] is not None}
        prof = cls(**known)
        prof.experience_years = float(prof.experience_years or 0)
        return prof

    @property
    def is_configured(self) -> bool:
        return bool(self.resume.strip() or self.skills or self.target_roles)


@dataclass
class Config:
    settings: dict[str, Any]
    companies: dict[str, list[dict[str, str]]]
    taxonomy: dict[str, dict[str, list[str]]]
    profile: Profile | None
    db_path: Path
    site_dir: Path
    web_dir: Path
    # Secrets override (tests/offline); when None, values come from the environment.
    secrets: dict[str, str] | None = None

    # ---- convenience accessors -------------------------------------------------
    def section(self, name: str) -> dict[str, Any]:
        return self.settings.get(name) or {}

    def source_settings(self, name: str) -> dict[str, Any]:
        return (self.settings.get("sources") or {}).get(name) or {}

    @property
    def site_url(self) -> str:
        url = os.environ.get("SITE_URL") or self.section("site").get("url", "")
        return url if url.endswith("/") else url + "/"

    def env(self, name: str) -> str | None:
        source = self.secrets if self.secrets is not None else os.environ
        val = (source.get(name) or "").strip()
        return val or None


def load_profile() -> Profile | None:
    """Profile comes from JOBRADAR_PROFILE (YAML text, used in CI) or profile.yaml."""
    raw = os.environ.get("JOBRADAR_PROFILE", "").strip()
    if raw:
        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return Profile.from_dict(data)
    path = Path(os.environ.get("JOBRADAR_PROFILE_FILE", ROOT / "profile.yaml"))
    if path.exists():
        return Profile.from_dict(_load_yaml(path))
    return None


def load_config(config_dir: Path | None = None) -> Config:
    cdir = Path(config_dir) if config_dir else CONFIG_DIR
    settings = _load_yaml(cdir / "settings.yaml")
    companies = _load_yaml(cdir / "companies.yaml")
    taxonomy = _load_yaml(cdir / "skills.yaml")
    return Config(
        settings=settings,
        companies={k: v or [] for k, v in companies.items()},
        taxonomy=taxonomy,
        profile=load_profile(),
        db_path=Path(os.environ.get("JOBRADAR_DB", ROOT / "data" / "jobradar.db")),
        site_dir=Path(os.environ.get("JOBRADAR_SITE_DIR", ROOT / "site_dist")),
        web_dir=ROOT / "web",
    )
