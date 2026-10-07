// JobRadar matching engine for the browser.
// Mirrors src/jobradar/match.py (rule_score) and skills.py (extract) exactly, so a visitor's
// ranking matches what the pipeline computes. Runs 100% client-side: resumes never leave the page.

export const WEIGHTS = { skills: 0.45, title: 0.25, experience: 0.15, location: 0.1, recency: 0.05 };

const STOP = new Set(["the", "a", "an", "and", "or", "of", "for", "to", "in", "with", "at", "i", "ii", "iii", "iv",
  "senior", "junior", "sr", "jr", "lead", "staff", "principal", "associate", "intern", "remote"]);
const SYNONYMS = {
  sde: ["software", "engineer"], swe: ["software", "engineer"], developer: ["engineer"],
  dev: ["engineer"], ml: ["machine", "learning"], ai: ["artificial", "intelligence"],
  frontend: ["front", "end"], backend: ["back", "end"], fullstack: ["full", "stack"],
  programmer: ["engineer"], analytics: ["analyst"],
};

export function titleTokens(text) {
  const out = new Set();
  for (let tok of (text || "").toLowerCase().match(/[a-z0-9+#.]+/g) || []) {
    tok = tok.replace(/^\.+|\.+$/g, "");
    if (!tok || STOP.has(tok)) continue;
    for (const t of SYNONYMS[tok] || [tok]) out.add(t);
  }
  return out;
}

export class Taxonomy {
  constructor(data) {
    this.patterns = data.patterns.map((p) => ({ skill: p.s, re: new RegExp(p.p, p.f.includes("i") ? "i" : "") }));
    this.categories = data.categories || {};
    this.skills = Object.keys(this.categories).sort((a, b) => a.localeCompare(b));
    this.cityPatterns = Object.entries(data.cities || {}).map(([city, aliases]) => ({
      city,
      re: new RegExp("(?<![a-z])(?:" + aliases.map(escapeRe).join("|") + ")(?![a-z])", "i"),
    }));
  }

  /** Canonical skills in order of first appearance (same as SkillTaxonomy.extract). */
  extract(text, exclude = []) {
    if (!text) return [];
    const ex = new Set(exclude.map((e) => e.toLowerCase()));
    const hits = new Map();
    for (const { skill, re } of this.patterns) {
      const m = re.exec(text);
      if (!m) continue;
      if (ex.size && (ex.has(skill.toLowerCase()) || ex.has(m[0].toLowerCase()))) continue;
      if (!hits.has(skill) || m.index < hits.get(skill)) hits.set(skill, m.index);
    }
    return [...hits.entries()].sort((a, b) => a[1] - b[1]).map(([s]) => s);
  }

  canonicalize(name) {
    const key = (name || "").trim().toLowerCase();
    if (!key) return null;
    const exact = this.skills.find((s) => s.toLowerCase() === key);
    if (exact) return exact;
    const found = this.extract(name);
    return found.length === 1 ? found[0] : null;
  }

  canonicalCity(name) {
    let best = null;
    for (const { city, re } of this.cityPatterns) {
      const m = re.exec(name || "");
      if (m && (!best || m.index < best.index)) best = { city, index: m.index };
    }
    return best ? best.city : null;
  }
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Skills for a profile: explicit list first, then anything found in the resume text. */
export function profileSkills(profile, tax) {
  const out = [];
  for (const s of profile.skills || []) {
    const c = tax.canonicalize(s);
    if (c && !out.includes(c)) out.push(c);
  }
  for (const s of tax.extract(profile.resume || "")) if (!out.includes(s)) out.push(s);
  return out;
}

export function ageDays(iso, now = Date.now()) {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.max(0, (now - t) / 86400000);
}

/**
 * Score one job record (from data/jobs.json) for a profile. Returns
 * {score, matched, missing, parts}. Same weights and rules as match.py.
 */
export function ruleScore(job, profile, userSkills, tax, now = Date.now()) {
  const title = job.title || "";
  const excl = (profile.exclude_keywords || []).filter((k) => k.trim());
  if (excl.some((k) => title.toLowerCase().includes(k.toLowerCase()))) {
    return { score: 0, matched: [], missing: [], parts: {} };
  }
  const must = job.must && job.must.length ? job.must : job.skills || [];
  const nice = (job.nice || []).filter((s) => !must.includes(s));
  const us = new Set(userSkills);

  let skill = 0.3;
  if (must.length || nice.length) {
    const got = must.filter((s) => us.has(s)).length + 0.5 * nice.filter((s) => us.has(s)).length;
    skill = got / (must.length + 0.5 * nice.length);
  }
  const matched = [...must, ...nice].filter((s) => us.has(s));
  const missing = must.filter((s) => !us.has(s)).slice(0, 6);

  const jt = titleTokens(title);
  let titleS = 0;
  for (const role of profile.target_roles || []) {
    const rt = titleTokens(role);
    if (rt.size) titleS = Math.max(titleS, [...rt].filter((t) => jt.has(t)).length / rt.size);
  }

  const yrs = Number(profile.experience_years || 0);
  const [lo, hi] = job.exp || [null, null];
  let exp;
  if (lo === null || lo === undefined) {
    exp = !job.fresher || yrs <= 1 ? 0.7 : 0.6;
    if (["senior", "lead", "manager", "director"].includes(job.seniority) && yrs < 3) exp = 0.2;
  } else if (yrs + 0.5 >= lo) {
    exp = hi === null || hi === undefined || yrs <= hi + 3 ? 1.0 : 0.6;
  } else {
    exp = Math.max(0, 1 - 0.3 * (lo - yrs));
  }
  if (job.fresher && yrs <= 1) exp = 1.0;

  const wanted = new Set((profile.locations || []).map((x) => tax.canonicalCity(x)).filter(Boolean));
  const cities = job.cities || [];
  let loc;
  if (job.remote && profile.open_to_remote) loc = 1.0;
  else if (cities.some((c) => wanted.has(c)) || (!wanted.size && job.india)) loc = 1.0;
  else if (job.india) loc = 0.5;
  else loc = 0.2;

  const age = ageDays(job.posted_at || job.first_seen, now) || 0;
  const rec = Math.pow(0.5, age / 7);

  const parts = { skills: skill, title: titleS, experience: exp, location: loc, recency: rec };
  let score = 100 * Object.entries(WEIGHTS).reduce((acc, [k, w]) => acc + w * parts[k], 0);
  if (exp < 0.4) score *= 0.6 + exp; // seniority gate, same as match.py
  return { score: Math.round(score * 10) / 10, matched, missing, parts };
}

/** Skills that appear most often as "missing" across a set of scored jobs. */
export function skillGaps(scored, limit = 8) {
  const counts = new Map();
  for (const { result } of scored) for (const s of result.missing) counts.set(s, (counts.get(s) || 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit).map(([skill, count]) => ({ skill, count }));
}
