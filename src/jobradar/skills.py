"""Deterministic skill extraction from a shared taxonomy (config/skills.yaml).

The same compiled patterns are exported to the website, so a resume parsed in
the browser and a job parsed in the pipeline are tokenised identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property

_WORD = "A-Za-z0-9"


def _alias_to_regex(alias: str) -> tuple[str, str]:
    """Return (pattern, flags) where flags is '' or 'i'. Patterns are JS-compatible."""
    if alias.startswith("re:"):
        return alias[3:], ""
    if alias.startswith("rei:"):
        return alias[4:], "i"
    case_sensitive = alias.startswith("cs:")
    text = alias[3:] if case_sensitive else alias
    parts = [re.escape(p) for p in text.split()]
    body = r"[\s\-]+".join(parts)
    # Python's re.escape escapes '-', '/', '#' etc. which JS also accepts inside a regex.
    # Symbols like '+' and '#' at the end must not be followed by more symbol chars.
    pattern = rf"(?<![{_WORD}]){body}(?![{_WORD}+#])"
    return pattern, "" if case_sensitive else "i"


@dataclass(frozen=True)
class SkillPattern:
    canonical: str
    category: str
    pattern: str
    flags: str

    @cached_property
    def regex(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.I if "i" in self.flags else 0)


class SkillTaxonomy:
    def __init__(self, taxonomy: dict[str, dict[str, list[str]]]):
        self.patterns: list[SkillPattern] = []
        self.category_of: dict[str, str] = {}
        self._lookup: dict[str, str] = {}
        for category, skills in taxonomy.items():
            for canonical, aliases in (skills or {}).items():
                canonical = str(canonical)
                self.category_of[canonical] = category
                self._lookup[canonical.lower()] = canonical
                # The canonical name is NOT matched implicitly: words like "Express" or
                # "React" are also plain English, so the YAML lists every alias explicitly.
                for alias in (str(a) for a in aliases or []):
                    pattern, flags = _alias_to_regex(alias)
                    self.patterns.append(SkillPattern(canonical, category, pattern, flags))
                    if not alias.startswith(("re:", "rei:")):
                        plain = alias[3:] if alias.startswith("cs:") else alias
                        self._lookup.setdefault(plain.lower(), canonical)

    # ------------------------------------------------------------------------------
    def extract(self, text: str, exclude: set[str] | None = None) -> list[str]:
        """Return canonical skills in order of first appearance."""
        if not text:
            return []
        exclude_l = {e.lower() for e in (exclude or set())}
        hits: dict[str, int] = {}
        for sp in self.patterns:
            if sp.canonical in hits and hits[sp.canonical] == 0:
                continue
            m = sp.regex.search(text)
            if not m:
                continue
            if exclude_l and (sp.canonical.lower() in exclude_l or m.group(0).lower() in exclude_l):
                continue
            pos = m.start()
            if sp.canonical not in hits or pos < hits[sp.canonical]:
                hits[sp.canonical] = pos
        return [s for s, _ in sorted(hits.items(), key=lambda kv: kv[1])]

    def canonicalize(self, name: str) -> str | None:
        """Map a free-text skill (e.g. from an LLM) onto the taxonomy, or None."""
        if not name:
            return None
        key = name.strip().lower()
        if key in self._lookup:
            return self._lookup[key]
        found = self.extract(name)
        return found[0] if len(found) == 1 else None

    def normalize_list(self, names: list[str], limit: int = 12) -> tuple[list[str], list[str]]:
        """Split names into (canonical skills, other skills), de-duplicated, order kept."""
        canon: list[str] = []
        other: list[str] = []
        for n in names or []:
            if not isinstance(n, str) or not n.strip():
                continue
            c = self.canonicalize(n)
            if c:
                if c not in canon:
                    canon.append(c)
            else:
                clean = " ".join(n.strip().split())[:40]
                if (
                    clean
                    and clean.lower() not in {o.lower() for o in other}
                    and len(clean.split()) <= 4
                ):
                    other.append(clean)
        return canon[:limit], other[:limit]

    def export(self) -> dict:
        """JSON-serialisable form for the website."""
        return {
            "patterns": [{"s": p.canonical, "p": p.pattern, "f": p.flags} for p in self.patterns],
            "categories": self.category_of,
        }
