"""The browser (web/match.js) and the pipeline (match.py) must rank jobs identically."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import sample_profile

from jobradar.config import CONFIG_DIR, ROOT
from jobradar.match import profile_skills, rule_score
from jobradar.normalize import INDIA_CITIES
from jobradar.skills import SkillTaxonomy

TAX = SkillTaxonomy(yaml.safe_load((CONFIG_DIR / "skills.yaml").read_text()))

NODE_SCRIPT = r"""
import { readFileSync } from "node:fs";
import { Taxonomy, profileSkills, ruleScore } from "%(match)s";
const data = JSON.parse(readFileSync(process.argv[2], "utf8"));
const tax = new Taxonomy(data.taxonomy);
const skills = profileSkills(data.profile, tax);
const out = { skills, extracted: data.texts.map((t) => tax.extract(t)),
  scores: data.jobs.map((j) => ruleScore(j, data.profile, skills, tax, data.now)) };
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_python_and_js_agree(tmp_path: Path):
    from datetime import datetime

    now = datetime(2026, 9, 24, 6, 0)
    texts = [
        "Strong Python, C/C++ and C#; React.js, Node.js; Kubernetes (k8s) and AWS. SOC 2 compliant.",
        "Requirements: PySpark, Airflow, dbt, Snowflake, Tableau, Power BI, A/B testing, CI/CD, .NET",
        "Welcome to Meesho, a spark of inspiration. Express interest. R, Go, and Rust developers.",
    ]
    profile = sample_profile()
    jobs = []
    for i, (must, exp, cities, remote, fresher, sen) in enumerate(
        [
            (["Python", "PyTorch", "AWS"], (0, 2), ["Bengaluru"], None, False, "entry"),
            (["Java", "Python"], (2, 4), ["Pune"], None, False, "mid"),
            (["Python"], (None, None), [], "global", True, "senior"),
            ([], (None, None), ["Hyderabad"], None, False, "lead"),
        ]
    ):
        jobs.append(
            {
                "id": str(i),
                "title": [
                    "ML Engineer",
                    "Backend Developer",
                    "Data Engineer",
                    "Principal Architect",
                ][i],
                "must": must,
                "nice": ["Docker"] if i == 0 else [],
                "skills": must,
                "exp": list(exp),
                "cities": cities,
                "remote": remote,
                "india": remote is None,
                "fresher": fresher,
                "seniority": sen,
                "posted_at": "2026-09-20T06:00:00Z",
                "first_seen": "2026-09-21T06:00:00Z",
            }
        )

    # Python side
    us = profile_skills(profile, TAX)
    import jobradar.match as m

    orig = m.age_days
    m.age_days = lambda ts, now_=None: orig(ts, now.astimezone())
    try:
        py_scores = []
        for j in jobs:
            pj = {
                "title": j["title"],
                "skills": j["skills"],
                "cities": j["cities"],
                "is_remote": bool(j["remote"]),
                "is_india": j["india"],
                "posted_at": j["posted_at"],
                "first_seen": j["first_seen"],
                "enrichment": {
                    "must_have": j["must"],
                    "nice_to_have": j["nice"],
                    "experience_min": j["exp"][0],
                    "experience_max": j["exp"][1],
                    "fresher_friendly": j["fresher"],
                    "seniority": j["seniority"],
                },
            }
            py_scores.append(rule_score(pj, profile, us))
    finally:
        m.age_days = orig

    payload = {
        "taxonomy": TAX.export() | {"cities": INDIA_CITIES},
        "profile": {
            "skills": profile.skills,
            "resume": profile.resume,
            "target_roles": profile.target_roles,
            "experience_years": profile.experience_years,
            "locations": profile.locations,
            "open_to_remote": profile.open_to_remote,
            "exclude_keywords": profile.exclude_keywords,
        },
        "jobs": jobs,
        "texts": texts,
        "now": now.astimezone().timestamp() * 1000,
    }
    (tmp_path / "in.json").write_text(json.dumps(payload))
    script = tmp_path / "run.mjs"
    script.write_text(NODE_SCRIPT % {"match": (ROOT / "web" / "match.js").as_uri()})
    out = json.loads(
        subprocess.run(
            ["node", str(script), str(tmp_path / "in.json")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )

    assert out["skills"] == us
    assert out["extracted"] == [TAX.extract(t) for t in texts]
    for py, js in zip(py_scores, out["scores"], strict=True):
        assert abs(py.score - js["score"]) < 0.2, (py, js)
        assert py.matched == js["matched"] and py.missing == js["missing"]
