# Test fixtures

`greenhouse_*.json`, `lever_*.json` and `ashby_*.json` are **trimmed snapshots** of real public
job-board API responses (September 2026), cut down to a few postings with shortened
descriptions. They mirror each API's schema so the parsers are tested against real shapes.
They are also used by `python -m jobradar run --offline tests/fixtures` so anyone can try
the whole pipeline without network access or API keys.

Aggregator payloads (Himalayas, Remotive, Remote OK, Jobicy, Adzuna, SmartRecruiters) are
generated in `tests/helpers.py::synthetic_payloads()` with **fictional companies** and
timestamps relative to "now", because those feeds filter by recency.
