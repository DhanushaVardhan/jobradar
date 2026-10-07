"""Connectors for public job aggregators (remote-job boards and Adzuna India).

Aggregator feeds are *partial* (recent pages only), so jobs are closed by age
("not seen for N days") instead of by absence. Every job keeps a link to the
aggregator's own page plus a `via` credit, as their API terms require.
"""

from __future__ import annotations

from typing import Any

from jobradar.models import Job, TargetResult
from jobradar.normalize import age_days, html_to_text, parse_date
from jobradar.sources.ats import _fmt_salary
from jobradar.sources.base import Source


def _names(items: Any) -> list[str]:
    out: list[str] = []
    for it in items or []:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            out.append(str(it.get("name") or it.get("alpha2") or it.get("value") or ""))
    return [x for x in out if x]


class Himalayas(Source):
    """https://himalayas.app/api — search endpoint, country filter, 20 jobs per page."""

    name = "himalayas"
    via = "Himalayas"
    URL = "https://himalayas.app/jobs/api/search"

    async def fetch(self) -> list[TargetResult]:
        return [await self.guarded("himalayas", self._fetch)]

    async def _fetch(self) -> TargetResult:
        jobs: list[Job] = []
        max_age = float(self.opts.get("max_age_days", 4))
        for page in range(1, int(self.opts.get("max_pages", 8)) + 1):
            params = {"sort": "recent", "page": page}
            if self.opts.get("country"):
                params["country"] = self.opts["country"]
            data = await self.http.get_json(self.URL, params=params)
            items = data.get("jobs") or [] if isinstance(data, dict) else []
            if not items:
                break
            too_old = 0
            for j in items:
                posted = parse_date(j.get("pubDate"))
                if (age_days(posted) or 0) > max_age:
                    too_old += 1
                    continue
                url = j.get("guid") or j.get("applicationLink")
                if not url or not j.get("title"):
                    continue
                restrictions = _names(j.get("locationRestrictions"))
                seniority = j.get("seniority")
                tags = _names(j.get("categories")) + (
                    _names(seniority)
                    if isinstance(seniority, list)
                    else [seniority]
                    if seniority
                    else []
                )
                jobs.append(
                    Job(
                        source=self.name,
                        source_id=str(url),
                        target="himalayas",
                        company=j.get("companyName") or "Unknown",
                        title=str(j["title"]).strip(),
                        url=url,
                        location_raw=", ".join(restrictions) if restrictions else "Worldwide",
                        description=html_to_text(j.get("description")) or j.get("excerpt") or "",
                        posted_at=posted,
                        remote_hint=True,
                        employment_type=j.get("employmentType"),
                        salary_raw=_fmt_salary(
                            j.get("minSalary"),
                            j.get("maxSalary"),
                            j.get("currency"),
                            j.get("salaryPeriod") or "year",
                        ),
                        tags=[t for t in tags if t][:8],
                        via=self.via,
                    )
                )
            if too_old == len(items):  # results are sorted by recency; the rest are older
                break
        return TargetResult(target="himalayas", source=self.name, jobs=jobs, complete=False)


class Remotive(Source):
    """https://remotive.com/api/remote-jobs — Remotive asks for a few calls per day at most."""

    name = "remotive"
    via = "Remotive"
    URL = "https://remotive.com/api/remote-jobs"

    async def fetch(self) -> list[TargetResult]:
        return [await self.guarded("remotive", self._fetch)]

    async def _fetch(self) -> TargetResult:
        data = await self.http.get_json(self.URL)
        jobs = []
        for j in data.get("jobs") or []:
            if not j.get("id") or not j.get("title") or not j.get("url"):
                continue
            jobs.append(
                Job(
                    source=self.name,
                    source_id=str(j["id"]),
                    target="remotive",
                    company=j.get("company_name") or "Unknown",
                    title=str(j["title"]).strip(),
                    url=j["url"],
                    location_raw=j.get("candidate_required_location") or "Worldwide",
                    description=html_to_text(j.get("description")),
                    posted_at=parse_date(j.get("publication_date")),
                    remote_hint=True,
                    employment_type=j.get("job_type"),
                    department=j.get("category"),
                    salary_raw=j.get("salary") or None,
                    tags=[str(t) for t in j.get("tags") or []][:8],
                    via=self.via,
                )
            )
        return TargetResult(target="remotive", source=self.name, jobs=jobs, complete=False)


class RemoteOK(Source):
    """https://remoteok.com/api — first element is a legal notice; attribution required."""

    name = "remoteok"
    via = "Remote OK"
    URL = "https://remoteok.com/api"

    async def fetch(self) -> list[TargetResult]:
        return [await self.guarded("remoteok", self._fetch)]

    async def _fetch(self) -> TargetResult:
        data = await self.http.get_json(self.URL)
        jobs = []
        for j in data if isinstance(data, list) else []:
            if not isinstance(j, dict) or not j.get("id") or not j.get("position"):
                continue
            url = j.get("url") or f"https://remoteok.com/remote-jobs/{j['id']}"
            jobs.append(
                Job(
                    source=self.name,
                    source_id=str(j["id"]),
                    target="remoteok",
                    company=j.get("company") or "Unknown",
                    title=str(j["position"]).strip(),
                    url=url,
                    location_raw=j.get("location") or "Worldwide",
                    description=html_to_text(j.get("description")),
                    posted_at=parse_date(j.get("date") or j.get("epoch")),
                    remote_hint=True,
                    salary_raw=_fmt_salary(j.get("salary_min"), j.get("salary_max"), "USD", "year"),
                    tags=[str(t) for t in j.get("tags") or []][:8],
                    via=self.via,
                )
            )
        return TargetResult(target="remoteok", source=self.name, jobs=jobs, complete=False)


class Jobicy(Source):
    """https://jobicy.com/jobs-rss-feed — JSON API v2."""

    name = "jobicy"
    via = "Jobicy"
    URL = "https://jobicy.com/api/v2/remote-jobs"

    async def fetch(self) -> list[TargetResult]:
        return [await self.guarded("jobicy", self._fetch)]

    async def _fetch(self) -> TargetResult:
        data = await self.http.get_json(
            self.URL, params={"count": int(self.opts.get("count", 100))}
        )
        jobs = []
        for j in data.get("jobs") or []:
            if not j.get("id") or not j.get("jobTitle") or not j.get("url"):
                continue
            industry = j.get("jobIndustry")
            jtype = j.get("jobType")
            jobs.append(
                Job(
                    source=self.name,
                    source_id=str(j["id"]),
                    target="jobicy",
                    company=j.get("companyName") or "Unknown",
                    title=html_to_text(str(j["jobTitle"])),
                    url=j["url"],
                    location_raw=j.get("jobGeo") or "Anywhere",
                    description=html_to_text(j.get("jobDescription")) or j.get("jobExcerpt") or "",
                    posted_at=parse_date(j.get("pubDate")),
                    remote_hint=True,
                    employment_type=", ".join(jtype) if isinstance(jtype, list) else jtype,
                    department=", ".join(industry) if isinstance(industry, list) else industry,
                    salary_raw=_fmt_salary(
                        j.get("annualSalaryMin"),
                        j.get("annualSalaryMax"),
                        j.get("salaryCurrency"),
                        "year",
                    ),
                    tags=[t for t in [j.get("jobLevel")] if t],
                    via=self.via,
                )
            )
        return TargetResult(target="jobicy", source=self.name, jobs=jobs, complete=False)


class Adzuna(Source):
    """https://developer.adzuna.com — India search API (free key)."""

    name = "adzuna"
    via = "Adzuna"
    requires_env = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
    URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

    async def fetch(self) -> list[TargetResult]:
        return [await self.guarded("adzuna", self._fetch)]

    async def _fetch(self) -> TargetResult:
        country = self.opts.get("country", "in")
        calls_left = int(self.opts.get("max_calls", 16))
        queries = self.opts.get("queries") or ["software engineer"]
        seen: set[str] = set()
        jobs: list[Job] = []
        for page in (1, 2):
            for q in queries:
                if calls_left <= 0:
                    break
                calls_left -= 1
                data = await self.http.get_json(
                    self.URL.format(country=country, page=page),
                    params={
                        "app_id": self.config.env("ADZUNA_APP_ID"),
                        "app_key": self.config.env("ADZUNA_APP_KEY"),
                        "results_per_page": int(self.opts.get("results_per_page", 50)),
                        "what": q,
                        "max_days_old": int(self.opts.get("max_days_old", 3)),
                        "sort_by": "date",
                    },
                )
                for r in data.get("results") or []:
                    rid = str(r.get("id") or "")
                    if not rid or rid in seen or not r.get("redirect_url"):
                        continue
                    seen.add(rid)
                    loc = r.get("location") or {}
                    area = [a for a in loc.get("area") or [] if isinstance(a, str)]
                    predicted = str(r.get("salary_is_predicted", "1")) == "1"
                    jobs.append(
                        Job(
                            source=self.name,
                            source_id=rid,
                            target="adzuna",
                            company=(r.get("company") or {}).get("display_name") or "Unknown",
                            title=html_to_text(r.get("title") or ""),
                            url=r["redirect_url"],
                            location_raw=", ".join([loc.get("display_name") or "", *area]).strip(
                                ", "
                            ),
                            description=html_to_text(r.get("description")),
                            posted_at=parse_date(r.get("created")),
                            country_hint="IN" if country == "in" else None,
                            employment_type=r.get("contract_time"),
                            department=(r.get("category") or {}).get("label"),
                            salary_raw=None
                            if predicted
                            else _fmt_salary(
                                r.get("salary_min"), r.get("salary_max"), "INR", "year"
                            ),
                            via=self.via,
                        )
                    )
        return TargetResult(target="adzuna", source=self.name, jobs=jobs, complete=False)


AGGREGATOR_SOURCES: list[type[Source]] = [Himalayas, Remotive, RemoteOK, Jobicy, Adzuna]
