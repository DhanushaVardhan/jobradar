"""Shared test helpers: config factory, synthetic aggregator payloads, fake LLM & Telegram APIs."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from jobradar.config import Profile, load_config

FIXTURES = Path(__file__).parent / "fixtures"


def make_config(
    tmp_path: Path,
    secrets: dict[str, str] | None = None,
    profile: Profile | None = None,
    **settings,
):
    cfg = load_config()
    cfg.db_path = tmp_path / "jobradar.db"
    cfg.site_dir = tmp_path / "site"
    cfg.secrets = secrets or {}
    cfg.profile = profile
    for section, values in settings.items():
        cfg.settings.setdefault(section, {}).update(values)
    return cfg


def sample_profile() -> Profile:
    return Profile(
        name="Asha Test",
        target_roles=["Machine Learning Engineer", "Data Engineer"],
        experience_years=1,
        locations=["Bangalore", "Hyderabad"],
        open_to_remote=True,
        skills=["python", "sql", "k8s"],
        exclude_keywords=["sales"],
        resume="B.Tech CSE. Python, SQL, PyTorch, Apache Spark, AWS, LLMs, LangChain. Built a RAG app.",
    )


def iso_ago(**delta) -> str:
    return (
        (datetime.now(UTC) - timedelta(**delta))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


# --------------------------------------------------------------------------------------
# Synthetic aggregator payloads (schema-accurate, fake companies, fresh timestamps)
# --------------------------------------------------------------------------------------
def synthetic_payloads() -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "himalayas": {
            "totalCount": 3,
            "jobs": [
                {
                    "title": "Backend Engineer (Python)",
                    "excerpt": "Build APIs",
                    "companyName": "Example Remote Co",
                    "employmentType": "Full Time",
                    "minSalary": 30000,
                    "maxSalary": 50000,
                    "currency": "USD",
                    "seniority": ["Mid-level"],
                    "locationRestrictions": ["India", "Singapore"],
                    "categories": ["Engineering"],
                    "description": "<p>Requirements: 2+ years with Python, Django, PostgreSQL and Docker.</p>",
                    "pubDate": int((now - timedelta(hours=5)).timestamp()),
                    "applicationLink": "https://example.com/apply/1",
                    "guid": "https://himalayas.app/companies/example-remote-co/jobs/backend-engineer-python",
                },
                {
                    "title": "Data Analyst",
                    "companyName": "Worldwide Labs",
                    "locationRestrictions": [],
                    "description": "<p>SQL, Tableau and Excel. Freshers welcome.</p>",
                    "pubDate": int((now - timedelta(hours=30)).timestamp()),
                    "guid": "https://himalayas.app/companies/worldwide-labs/jobs/data-analyst",
                },
                {
                    "title": "Old Posting",
                    "companyName": "Stale Inc",
                    "locationRestrictions": ["India"],
                    "description": "old",
                    "pubDate": int((now - timedelta(days=20)).timestamp()),
                    "guid": "https://himalayas.app/companies/stale/jobs/old",
                },
            ],
        },
        "remotive": {
            "job-count": 2,
            "jobs": [
                {
                    "id": 101,
                    "url": "https://remotive.com/remote-jobs/software-dev/frontend-101",
                    "title": "Frontend Developer",
                    "company_name": "Pixel Example",
                    "category": "Software Development",
                    "tags": ["react", "typescript"],
                    "job_type": "full_time",
                    "publication_date": (now - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S"),
                    "candidate_required_location": "Worldwide",
                    "salary": "",
                    "description": "<ul><li>3+ years React and TypeScript</li><li>Next.js a plus</li></ul>",
                },
                {
                    "id": 102,
                    "url": "https://remotive.com/remote-jobs/sales/ae-102",
                    "title": "Account Executive",
                    "company_name": "US Only Example",
                    "publication_date": (now - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S"),
                    "candidate_required_location": "USA Only",
                    "description": "Sell.",
                },
            ],
        },
        "remoteok": [
            {"legal": "API terms: link back to Remote OK"},
            {
                "id": "9001",
                "epoch": int((now - timedelta(hours=2)).timestamp()),
                "date": (now - timedelta(hours=2)).isoformat(),
                "company": "DevTools Example",
                "position": "DevOps Engineer",
                "tags": ["devops", "aws"],
                "location": "Asia",
                "description": "<p>Kubernetes, Terraform, AWS. 4+ years.</p>",
                "salary_min": 40000,
                "salary_max": 60000,
                "url": "https://remoteok.com/remote-jobs/9001",
            },
        ],
        "jobicy": {
            "jobCount": 1,
            "jobs": [
                {
                    "id": 555,
                    "url": "https://jobicy.com/jobs/555-ml-engineer",
                    "jobTitle": "ML Engineer",
                    "companyName": "Model Example",
                    "jobIndustry": ["Data Science"],
                    "jobType": ["full-time"],
                    "jobGeo": "APAC",
                    "jobLevel": "Senior",
                    "jobExcerpt": "Train models",
                    "jobDescription": "<p>PyTorch, MLOps, 5+ years experience.</p>",
                    "pubDate": (now - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
                    "annualSalaryMin": "60000",
                    "annualSalaryMax": "90000",
                    "salaryCurrency": "USD",
                },
            ],
        },
        "adzuna": {
            "count": 2,
            "results": [
                {
                    "id": "4001",
                    "title": "<strong>Python</strong> Developer",
                    "description": "Python, Django, REST APIs. 0-2 years.",
                    "created": iso_ago(hours=6),
                    "redirect_url": "https://www.adzuna.in/land/ad/4001",
                    "company": {"display_name": "Chennai Example Tech"},
                    "location": {
                        "display_name": "Chennai, Tamil Nadu",
                        "area": ["India", "Tamil Nadu", "Chennai"],
                    },
                    "salary_min": 400000,
                    "salary_max": 600000,
                    "salary_is_predicted": "0",
                    "contract_time": "full_time",
                    "category": {"label": "IT Jobs"},
                },
                {
                    "id": "4002",
                    "title": "Graduate Engineer Trainee",
                    "description": "2025 batch graduates. B.E/B.Tech.",
                    "created": iso_ago(hours=20),
                    "redirect_url": "https://www.adzuna.in/land/ad/4002",
                    "company": {"display_name": "Pune Example Industries"},
                    "location": {
                        "display_name": "Pune, Maharashtra",
                        "area": ["India", "Maharashtra", "Pune"],
                    },
                    "salary_min": 300000,
                    "salary_max": 300000,
                    "salary_is_predicted": "1",
                },
            ],
        },
        "smartrecruiters_ExampleCorp": {
            "offset": 0,
            "limit": 100,
            "totalFound": 1,
            "content": [
                {
                    "id": "743999",
                    "name": "QA Automation Engineer",
                    "releasedDate": iso_ago(days=1),
                    "company": {"identifier": "ExampleCorp", "name": "Example Corp"},
                    "location": {
                        "city": "Hyderabad",
                        "region": "Telangana",
                        "country": "in",
                        "remote": False,
                    },
                    "typeOfEmployment": {"label": "Full-time"},
                    "experienceLevel": {"label": "Associate"},
                    "department": {"label": "Engineering"},
                    "ref": "https://api.smartrecruiters.com/v1/companies/ExampleCorp/postings/743999",
                }
            ],
        },
        "smartrecruiters_ExampleCorp_743999": {
            "jobAd": {
                "sections": {
                    "jobDescription": {"text": "<p>Automate tests.</p>"},
                    "qualifications": {
                        "text": "<ul><li>Selenium, Java, TestNG</li><li>0-2 years</li></ul>"
                    },
                }
            }
        },
    }


# --------------------------------------------------------------------------------------
# Fake LLM (OpenAI-compatible) and Telegram APIs
# --------------------------------------------------------------------------------------
class FakeLLM:
    """Answers enrich/match prompts deterministically, and can simulate failures."""

    def __init__(self, script: list[Any] | None = None):
        self.calls: list[dict[str, Any]] = []
        self.script = list(script or [])  # items: int status / "bad-json" / None(=normal)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(
            {
                "url": str(request.url),
                "model": body["model"],
                "auth": request.headers.get("authorization"),
            }
        )
        step = self.script.pop(0) if self.script else None
        if isinstance(step, int):
            headers = {"retry-after": "1"} if step == 429 else {}
            return httpx.Response(
                step, headers=headers, json={"error": {"message": f"simulated {step}"}}
            )
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if step == "bad-json":
            content = "Sure! Here is the data: {not json"
        elif step == "day-limit":
            return httpx.Response(
                429,
                headers={"retry-after": "3600"},
                json={"error": {"message": "Rate limit reached on tokens per day (TPD)"}},
            )
        elif "job-posting parser" in system:
            postings = json.loads(user.split("\n", 1)[1])
            content = json.dumps({"jobs": [self._enrich(p) for p in postings]})
        else:
            jobs = json.loads(user.split("JOBS\n", 1)[1])
            content = json.dumps(
                {
                    "jobs": [
                        {
                            "id": j["id"],
                            "fit": 90 - 10 * i,
                            "verdict": "good",
                            "why": ["Python match"],
                            "gaps": ["Kafka"],
                            "tip": "Lead with your RAG project.",
                        }
                        for i, j in enumerate(jobs)
                    ]
                }
            )
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-remaining-requests": "900",
                "x-ratelimit-remaining-tokens": "7000",
                "x-ratelimit-reset-tokens": "1s",
            },
            json={
                "choices": [{"message": {"content": "```json\n" + content + "\n```"}}],
                "usage": {"total_tokens": 1234},
            },
        )

    @staticmethod
    def _enrich(p: dict[str, Any]) -> dict[str, Any]:
        text = p["text"]
        skills = [
            s
            for s in ["Python", "SQL", "AWS", "Kubernetes", "React", "Spark", "Communication"]
            if re.search(rf"\b{s}\b", text, re.I)
        ]
        return {
            "id": p["id"],
            "summary": f"Work on {text.splitlines()[0][:40]} problems at {p['company']}.",
            "seniority": "entry" if "intern" in text.lower() else "mid",
            "experience_min": 2,
            "experience_max": "5",
            "fresher_friendly": "fresher" in text.lower(),
            "role_category": "software",
            "must_have": skills + ["Communication"],
            "nice_to_have": ["Docker"],
            "work_mode": "hybrid",
            "salary": {"min": "1200000", "max": 2000000, "currency": "inr", "period": "year"},
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


class FakeTelegram:
    def __init__(self, fail_parse_once: bool = False):
        self.messages: list[dict[str, Any]] = []
        self.fail_parse_once = fail_parse_once

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if self.fail_parse_once and body.get("parse_mode"):
            self.fail_parse_once = False
            return httpx.Response(
                400, json={"ok": False, "description": "Bad Request: can't parse entities"}
            )
        self.messages.append(body)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.messages)}})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)
