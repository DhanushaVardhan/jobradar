"""Connectors for company applicant-tracking systems (ATS) with public job-board APIs.

These return the *complete* list of open jobs for a company, so a job missing
from a successful fetch means the posting was closed.
"""

from __future__ import annotations

from typing import Any

from jobradar.models import Job, TargetResult
from jobradar.normalize import html_to_text, parse_date
from jobradar.sources.base import Source


def _fmt_salary(lo: Any, hi: Any, currency: str | None, period: str | None) -> str | None:
    try:
        lo_f = float(lo) if lo not in (None, "") else None
        hi_f = float(hi) if hi not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if not lo_f and not hi_f:
        return None
    rng = f"{lo_f:,.0f}" if lo_f else ""
    if hi_f and hi_f != lo_f:
        rng = f"{rng}–{hi_f:,.0f}" if rng else f"up to {hi_f:,.0f}"
    per = f" / {period.lower().replace('per-', '').replace('per ', '')}" if period else ""
    return f"{(currency or '').upper()} {rng}{per}".strip()


class _CompanyBoards(Source):
    """Shared plumbing: one request per company slug listed in companies.yaml."""

    def companies(self) -> list[dict[str, str]]:
        return [c for c in self.config.companies.get(self.name, []) if c and c.get("slug")]

    async def fetch(self) -> list[TargetResult]:
        return await self.gather(
            [
                self.guarded(
                    f"{self.name}:{c['slug']}",
                    lambda c=c: self.fetch_board(c["slug"], c.get("name")),
                )
                for c in self.companies()
            ]
        )

    async def fetch_board(self, slug: str, name: str | None = None) -> TargetResult:
        raise NotImplementedError


class Greenhouse(_CompanyBoards):
    name = "greenhouse"
    URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    async def fetch_board(self, slug: str, name: str | None = None) -> TargetResult:
        target = f"{self.name}:{slug}"
        data = await self.http.get_json(self.URL.format(slug=slug))
        jobs = []
        for j in data.get("jobs") or []:
            if not j.get("id") or not j.get("title"):
                continue
            offices = [
                o.get("location") or o.get("name")
                for o in j.get("offices") or []
                if isinstance(o, dict)
            ]
            loc = (j.get("location") or {}).get("name") or ""
            meta = {
                str(m.get("name", "")).lower(): m.get("value")
                for m in j.get("metadata") or []
                if isinstance(m, dict)
            }
            emp = meta.get("employment type")
            depts = [
                d.get("name")
                for d in j.get("departments") or []
                if isinstance(d, dict) and d.get("name")
            ]
            jobs.append(
                Job(
                    source=self.name,
                    source_id=str(j["id"]),
                    target=target,
                    company=name or j.get("company_name") or slug,
                    title=j["title"].strip(),
                    url=j.get("absolute_url")
                    or f"https://boards.greenhouse.io/{slug}/jobs/{j['id']}",
                    location_raw=" | ".join(x for x in [loc, *offices] if x),
                    description=html_to_text(j.get("content")),
                    posted_at=parse_date(j.get("first_published") or j.get("updated_at")),
                    employment_type=emp if isinstance(emp, str) else None,
                    department=depts[0] if depts else None,
                )
            )
        return TargetResult(target=target, source=self.name, jobs=jobs, complete=True)


class Lever(_CompanyBoards):
    name = "lever"
    URL = "https://api.lever.co/v0/postings/{slug}?mode=json"

    async def fetch_board(self, slug: str, name: str | None = None) -> TargetResult:
        target = f"{self.name}:{slug}"
        data = await self.http.get_json(self.URL.format(slug=slug))
        if isinstance(data, dict):  # error payloads are objects, postings are a list
            data = data.get("data") or []
        jobs = []
        for p in data:
            if not isinstance(p, dict) or not p.get("id") or not p.get("text"):
                continue
            cats = p.get("categories") or {}
            locs = cats.get("allLocations") or [cats.get("location")]
            parts = [p.get("descriptionPlain") or html_to_text(p.get("description"))]
            for lst in p.get("lists") or []:
                if isinstance(lst, dict):
                    parts.append(f"{lst.get('text', '')}:\n{html_to_text(lst.get('content'))}")
            sal = p.get("salaryRange") or {}
            workplace = str(p.get("workplaceType") or "").lower()
            jobs.append(
                Job(
                    source=self.name,
                    source_id=str(p["id"]),
                    target=target,
                    company=name or slug,
                    title=p["text"].strip(),
                    url=p.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{p['id']}",
                    location_raw="; ".join(str(x) for x in locs if x),
                    description="\n\n".join(x for x in parts if x).strip(),
                    posted_at=parse_date(p.get("createdAt")),
                    remote_hint=True if workplace == "remote" else None,
                    country_hint=p.get("country"),
                    employment_type=cats.get("commitment"),
                    department=cats.get("department") or cats.get("team"),
                    salary_raw=_fmt_salary(
                        sal.get("min"), sal.get("max"), sal.get("currency"), sal.get("interval")
                    ),
                )
            )
        return TargetResult(target=target, source=self.name, jobs=jobs, complete=True)


class Ashby(_CompanyBoards):
    name = "ashby"
    URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

    async def fetch_board(self, slug: str, name: str | None = None) -> TargetResult:
        target = f"{self.name}:{slug}"
        data = await self.http.get_json(self.URL.format(slug=slug))
        jobs = []
        for j in data.get("jobs") or []:
            if not j.get("id") or not j.get("title") or j.get("isListed") is False:
                continue
            locs = [j.get("location")]
            for s in j.get("secondaryLocations") or []:
                if isinstance(s, dict):
                    locs.append(s.get("location"))
                elif isinstance(s, str):
                    locs.append(s)
            country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
            if country and country not in locs:
                locs.append(country)
            comp = j.get("compensation") or {}
            workplace = str(j.get("workplaceType") or "").lower()
            jobs.append(
                Job(
                    source=self.name,
                    source_id=str(j["id"]),
                    target=target,
                    company=name or slug,
                    title=j["title"].strip(),
                    url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j['id']}",
                    location_raw="; ".join(str(x) for x in locs if x),
                    description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                    posted_at=parse_date(j.get("publishedAt")),
                    remote_hint=True if (j.get("isRemote") or workplace == "remote") else None,
                    employment_type=j.get("employmentType"),
                    department=j.get("department") or j.get("team"),
                    salary_raw=comp.get("compensationTierSummary")
                    or comp.get("scrapeableCompensationSalarySummary"),
                )
            )
        return TargetResult(target=target, source=self.name, jobs=jobs, complete=True)


class SmartRecruiters(_CompanyBoards):
    name = "smartrecruiters"
    LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    MAX_PAGES = 5

    async def fetch_board(self, slug: str, name: str | None = None) -> TargetResult:
        target = f"{self.name}:{slug}"
        country = self.opts.get("country", "in")
        postings: list[dict] = []
        offset = total = 0
        for _ in range(self.MAX_PAGES):
            params = {"limit": 100, "offset": offset}
            if country:
                params["country"] = country
            data = await self.http.get_json(self.LIST_URL.format(slug=slug), params=params)
            batch = data.get("content") or []
            postings.extend(batch)
            offset += len(batch)
            total = int(data.get("totalFound") or 0)
            if not batch or offset >= total:
                break

        jobs: list[Job] = []
        details_left = int(self.opts.get("max_details", 40))
        for p in postings:
            if not p.get("id") or not p.get("name"):
                continue
            loc = p.get("location") or {}
            company_id = (p.get("company") or {}).get("identifier") or slug
            job = Job(
                source=self.name,
                source_id=str(p["id"]),
                target=target,
                company=name or (p.get("company") or {}).get("name") or slug,
                title=p["name"].strip(),
                url=f"https://jobs.smartrecruiters.com/{company_id}/{p['id']}",
                location_raw=loc.get("fullLocation")
                or ", ".join(
                    str(x) for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x
                ),
                posted_at=parse_date(p.get("releasedDate")),
                remote_hint=True if loc.get("remote") else None,
                country_hint=loc.get("country"),
                employment_type=(p.get("typeOfEmployment") or {}).get("label"),
                department=(p.get("department") or {}).get("label")
                or (p.get("function") or {}).get("label"),
                tags=[t for t in [(p.get("experienceLevel") or {}).get("label")] if t],
            )
            # Descriptions need one extra call per posting; only fetch them for new jobs.
            if details_left > 0 and job.uid not in self.known_ids and p.get("ref"):
                details_left -= 1
                try:
                    detail = await self.http.get_json(p["ref"])
                    sections = (detail.get("jobAd") or {}).get("sections") or {}
                    job.description = "\n\n".join(
                        html_to_text((sections.get(k) or {}).get("text"))
                        for k in ("jobDescription", "qualifications", "additionalInformation")
                    ).strip()
                except Exception:  # a missing description is not fatal
                    pass
            jobs.append(job)
        return TargetResult(
            target=target, source=self.name, jobs=jobs, complete=len(postings) >= total
        )


ATS_SOURCES: list[type[_CompanyBoards]] = [Greenhouse, Lever, Ashby, SmartRecruiters]
