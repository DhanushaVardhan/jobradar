// JobRadar web app: static, framework-free, private by design.
import { Taxonomy, profileSkills, ruleScore, skillGaps, ageDays } from "./match.js";

const PAGE = 30;
const ROLE_LABELS = {
  software: "Software engineering", frontend: "Frontend", backend: "Backend", fullstack: "Full stack",
  mobile: "Mobile", data: "Data & analytics", ml_ai: "AI / ML", devops_cloud: "DevOps & cloud",
  security: "Security", qa: "QA & testing", product: "Product", design: "Design", sales: "Sales",
  marketing: "Marketing & content", finance: "Finance", operations: "Operations", hr: "HR & recruiting",
  support: "Customer support", other: "Other",
};
const SENIORITY_LABELS = { intern: "Internship", entry: "Entry level", mid: "Mid level", senior: "Senior", lead: "Lead / staff", manager: "Manager", director: "Director+" };

const state = {
  jobs: [], stats: null, tax: null, feeds: [],
  shown: PAGE, saved: new Set(), profile: null, scores: new Map(),
};

// ------------------------------------------------------------------ utils
const $ = (sel) => document.querySelector(sel);

/** Tiny DOM builder. Strings become text nodes: job data is untrusted, never innerHTML. */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, text) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
function ago(iso) {
  const d = ageDays(iso);
  if (d === null) return "";
  if (d < 1 / 24) return "just now";
  if (d < 1) return `${Math.round(d * 24)}h ago`;
  if (d < 30) return `${Math.round(d)}d ago`;
  return `${Math.round(d / 30)}mo ago`;
}
const fmt = (n) => new Intl.NumberFormat("en-IN").format(n);
function expLabel(j) {
  const [lo, hi] = j.exp || [];
  if (lo === null || lo === undefined) return null;
  return hi !== null && hi !== undefined ? `${lo}–${hi} yrs` : `${lo}+ yrs`;
}

// ------------------------------------------------------------------ tooltip
const tip = $("#tooltip");
function showTip(evt, value, label) {
  tip.replaceChildren(h("strong", {}, value), label);
  tip.hidden = false;
  const r = evt.target.getBoundingClientRect ? evt.target.getBoundingClientRect() : null;
  const x = evt.clientX ?? (r ? r.left + r.width / 2 : 0);
  const y = evt.clientY ?? (r ? r.top : 0);
  const w = tip.offsetWidth;
  tip.style.left = Math.min(window.innerWidth - w - 8, Math.max(8, x - w / 2)) + "px";
  tip.style.top = Math.max(8, y - tip.offsetHeight - 12) + "px";
}
const hideTip = () => { tip.hidden = true; };

// ------------------------------------------------------------------ charts (single series, one hue)
function hbar(fig, rows, { onClick, tooltip } = {}) {
  fig.querySelectorAll("svg, details, .empty-chart").forEach((n) => n.remove());
  if (!rows.length) { fig.append(h("p", { class: "via empty-chart" }, "No data yet.")); return; }
  const W = 560, rowH = 26, barH = 14, labelW = 150, right = 44;
  const H = rows.length * rowH + 4;
  const max = Math.max(...rows.map((r) => r.value)) || 1;
  const span = W - labelW - right;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": fig.querySelector("figcaption").textContent });
  svg.append(s("line", { x1: labelW, x2: labelW, y1: 0, y2: H, class: "base-line" }));
  rows.forEach((r, i) => {
    const y = i * rowH + (rowH - barH) / 2;
    const w = Math.max(2, (r.value / max) * span);
    const label = r.label.length > 22 ? r.label.slice(0, 21) + "…" : r.label;
    svg.append(s("text", { x: labelW - 8, y: y + barH / 2 + 4, "text-anchor": "end", class: "label-text" }, label));
    const hit = s("rect", { x: 0, y: i * rowH, width: W, height: rowH, class: "hit", tabindex: 0, role: "button", "aria-label": `${r.label}: ${r.value}` });
    const rad = Math.min(4, w / 2);
    const bar = s("path", {
      class: "bar",
      d: `M${labelW},${y} h${w - rad} a${rad},${rad} 0 0 1 ${rad},${rad} v${barH - 2 * rad} a${rad},${rad} 0 0 1 -${rad},${rad} h-${w - rad} z`,
    });
    const tipText = tooltip ? tooltip(r) : r.label;
    const enter = (e) => { bar.classList.add("hover"); showTip(e, fmt(r.value), tipText); };
    const leave = () => { bar.classList.remove("hover"); hideTip(); };
    hit.addEventListener("pointermove", enter);
    hit.addEventListener("focus", enter);
    hit.addEventListener("pointerleave", leave);
    hit.addEventListener("blur", leave);
    if (onClick) {
      hit.addEventListener("click", () => onClick(r));
      hit.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(r); } });
    } else hit.style.cursor = "default";
    svg.append(hit, bar, s("text", { x: labelW + w + 6, y: y + barH / 2 + 4, class: "value-text" }, fmt(r.value)));
  });
  fig.append(svg, tableView(rows, ["Item", "Jobs"], (r) => [r.label, fmt(r.value)]));
}

function columns(fig, rows) {
  fig.querySelectorAll("svg, details, .empty-chart").forEach((n) => n.remove());
  if (!rows.length) return;
  const W = 560, H = 190, left = 34, bottom = 24, top = 18, right = 8;
  const max = Math.max(...rows.map((r) => r.value), 1);
  const step = Math.pow(10, Math.floor(Math.log10(max)));
  const niceMax = Math.ceil(max / step) * step || 1;
  const plotW = W - left - right, plotH = H - top - bottom;
  const slot = plotW / rows.length;
  const bw = Math.min(24, slot - 2);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": fig.querySelector("figcaption").textContent });
  for (const t of [0, niceMax / 2, niceMax]) {
    const y = top + plotH - (t / niceMax) * plotH;
    svg.append(s("line", { x1: left, x2: W - right, y1: y, y2: y, class: t === 0 ? "base-line" : "grid-line" }));
    svg.append(s("text", { x: left - 6, y: y + 4, "text-anchor": "end", class: "axis-text" }, fmt(Math.round(t))));
  }
  const dateLabel = (d) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  const peak = rows.reduce((a, b, i) => (b.value > rows[a].value ? i : a), 0);
  rows.forEach((r, i) => {
    const x = left + i * slot + (slot - bw) / 2;
    const bh = (r.value / niceMax) * plotH;
    const y = top + plotH - bh;
    const hit = s("rect", { x: left + i * slot, y: top, width: slot, height: plotH, class: "hit", tabindex: 0, "aria-label": `${dateLabel(r.label)}: ${r.value} new jobs` });
    const rad = Math.min(4, bw / 2, bh / 2);
    const bar = bh > 0
      ? s("path", { class: "bar", d: `M${x},${y + bh} v-${bh - rad} a${rad},${rad} 0 0 1 ${rad},-${rad} h${bw - 2 * rad} a${rad},${rad} 0 0 1 ${rad},${rad} v${bh - rad} z` })
      : s("path", { class: "bar", d: "" });
    const enter = (e) => { bar.classList.add("hover"); showTip(e, `${fmt(r.value)} new jobs`, dateLabel(r.label)); };
    const leave = () => { bar.classList.remove("hover"); hideTip(); };
    hit.addEventListener("pointermove", enter); hit.addEventListener("focus", enter);
    hit.addEventListener("pointerleave", leave); hit.addEventListener("blur", leave);
    hit.style.cursor = "default";
    svg.append(hit, bar);
    if (i === peak && r.value > 0) svg.append(s("text", { x: x + bw / 2, y: y - 5, "text-anchor": "middle", class: "value-text" }, fmt(r.value)));
    if (i === 0 || i === rows.length - 1 || i === Math.floor(rows.length / 2)) {
      svg.append(s("text", { x: x + bw / 2, y: H - 6, "text-anchor": "middle", class: "axis-text" }, dateLabel(r.label)));
    }
  });
  fig.append(svg, tableView(rows, ["Date", "New jobs"], (r) => [dateLabel(r.label), fmt(r.value)]));
}

function tableView(rows, head, cells) {
  return h("details", {}, h("summary", {}, "Show as table"),
    h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, head.map((x, i) => h("th", { class: i ? "num" : "" }, x)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, cells(r).map((c, i) => h("td", { class: i ? "num" : "" }, c))))))));
}

// ------------------------------------------------------------------ profile
function loadProfile() {
  const p = store.get("jr_profile", null);
  return p || { resume: "", added: [], removed: [], roles: "", exp: 0, exclude: "", cities: [], remote: true };
}
function effectiveProfile(p) {
  const base = profileSkills({ resume: p.resume, skills: p.added }, state.tax);
  const skills = base.filter((x) => !p.removed.includes(x));
  return {
    skills,
    target_roles: p.roles.split(",").map((x) => x.trim()).filter(Boolean),
    experience_years: Number(p.exp) || 0,
    locations: p.cities,
    open_to_remote: p.remote,
    exclude_keywords: p.exclude.split(",").map((x) => x.trim()).filter(Boolean),
  };
}
function computeScores() {
  state.scores.clear();
  const eff = effectiveProfile(state.profile);
  state.eff = eff;
  if (!eff.skills.length && !eff.target_roles.length) return;
  for (const j of state.jobs) state.scores.set(j.id, ruleScore(j, eff, eff.skills, state.tax));
}
const hasProfile = () => state.scores.size > 0;

// ------------------------------------------------------------------ job card
function jobCard(j, { showScore = hasProfile() } = {}) {
  const res = state.scores.get(j.id);
  const matched = new Set(res ? res.matched : []);
  const exp = expLabel(j);
  const meta = [
    j.seniority && SENIORITY_LABELS[j.seniority] && !exp ? h("span", { class: "chip" }, SENIORITY_LABELS[j.seniority]) : null,
    exp ? h("span", { class: "chip" }, exp) : null,
    j.fresher ? h("span", { class: "chip accent" }, "Fresher-friendly") : null,
    j.remote ? h("span", { class: "chip accent" }, j.remote === "india" ? "Remote · India" : "Remote") : j.mode === "hybrid" ? h("span", { class: "chip" }, "Hybrid") : null,
    j.salary ? h("span", { class: "chip warn" }, j.salary) : null,
  ];
  const skills = (j.must.length ? j.must : j.skills).slice(0, 7).map((sk) =>
    h("button", { class: "chip" + (matched.has(sk) ? " match" : ""), type: "button", title: `Search ${sk} jobs`, onclick: () => searchFor(sk) }, sk));
  const savedNow = state.saved.has(j.id);
  const saveBtn = h("button", { class: "btn ghost", type: "button", "aria-pressed": String(savedNow), onclick: (e) => toggleSave(j.id, e.currentTarget) },
    savedNow ? "Saved" : "Save");
  let scoreEl = null;
  if (showScore && res) {
    const sc = Math.round(res.score);
    scoreEl = h("div", { class: "score " + (sc >= 70 ? "high" : sc >= 50 ? "mid" : ""), title: "Rule-based match score for your profile" }, `${sc}%`, h("small", {}, "match"));
  }
  return h("li", { class: "job" },
    h("div", { class: "job-head" },
      h("div", {},
        h("h3", {}, h("a", { href: safeUrl(j.url), target: "_blank", rel: "noopener nofollow" }, j.title)),
        h("div", { class: "sub" }, h("span", { class: "company" }, j.company), ` · ${j.location} · ${ago(j.posted_at || j.first_seen)}`)),
      scoreEl),
    h("div", { class: "chip-row" }, meta),
    j.summary ? h("p", { class: "summary" }, j.summary) : j.snippet ? h("p", { class: "summary" }, j.snippet) : null,
    res && res.missing.length && showScore ? h("div", { class: "chip-row" }, h("span", { class: "via" }, "Missing:"), res.missing.slice(0, 4).map((m) => h("span", { class: "chip miss" }, m))) : null,
    h("div", { class: "job-foot" },
      h("div", { class: "left" }, skills),
      h("div", { class: "job-actions" },
        j.ai ? h("span", { class: "chip ai", title: "Summary and skills extracted by an LLM" }, "AI") : null,
        h("span", { class: "via" }, j.via ? `via ${j.via}` : j.source[0].toUpperCase() + j.source.slice(1)),
        saveBtn,
        h("a", { class: "btn primary", href: safeUrl(j.url), target: "_blank", rel: "noopener nofollow" }, "Apply ↗"))));
}

function toggleSave(id, btn) {
  if (state.saved.has(id)) state.saved.delete(id); else state.saved.add(id);
  store.set("jr_saved", [...state.saved]);
  const on = state.saved.has(id);
  btn.setAttribute("aria-pressed", String(on));
  btn.textContent = on ? "Saved" : "Save";
}

// ------------------------------------------------------------------ jobs view
const F = { q: "#q", city: "#f-city", role: "#f-role", exp: "#f-exp", age: "#f-age" };
const T = { remote: "#f-remote", fresher: "#f-fresher", saved: "#f-saved" };

function readFilters() {
  const f = {};
  for (const [k, sel] of Object.entries(F)) f[k] = $(sel).value.trim();
  for (const [k, sel] of Object.entries(T)) f[k] = $(sel).checked;
  f.sort = $("#sort").value;
  return f;
}
function writeUrl(f) {
  const p = new URLSearchParams();
  for (const k of Object.keys(F)) if (f[k]) p.set(k, f[k]);
  for (const k of Object.keys(T)) if (f[k]) p.set(k, "1");
  if (f.sort !== "new") p.set("sort", f.sort);
  const qs = p.toString();
  history.replaceState(null, "", (qs ? "?" + qs : location.pathname) + location.hash);
}
function applyUrl() {
  const p = new URLSearchParams(location.search);
  for (const [k, sel] of Object.entries(F)) if (p.has(k)) $(sel).value = p.get(k);
  for (const [k, sel] of Object.entries(T)) $(sel).checked = p.get(k) === "1";
  if (p.has("sort")) $("#sort").value = p.get("sort");
}

function expMatch(j, band) {
  const lo = j.exp ? j.exp[0] : null;
  const known = lo !== null && lo !== undefined;
  switch (band) {
    case "fresher": return j.fresher || (known && lo <= 1);
    case "0-2": return known ? lo <= 2 : j.fresher || ["intern", "entry"].includes(j.seniority);
    case "2-5": return known ? lo >= 2 && lo <= 5 : j.seniority === "mid";
    case "5+": return known ? lo >= 5 : ["senior", "lead", "manager", "director"].includes(j.seniority);
    default: return true;
  }
}

function filtered() {
  const f = readFilters();
  const terms = f.q.toLowerCase().split(/\s+/).filter(Boolean);
  let list = state.jobs.filter((j) => {
    if (f.city && !j.cities.includes(f.city)) return false;
    if (f.role && j.role !== f.role) return false;
    if (f.exp && !expMatch(j, f.exp)) return false;
    if (f.age && (ageDays(j.first_seen) ?? 999) > Number(f.age)) return false;
    if (f.remote && !j.remote) return false;
    if (f.fresher && !j.fresher) return false;
    if (f.saved && !state.saved.has(j.id)) return false;
    if (terms.length) {
      const hay = `${j.title} ${j.company} ${j.skills.join(" ")} ${j.summary || ""} ${j.location}`.toLowerCase();
      if (!terms.every((t) => hay.includes(t))) return false;
    }
    return true;
  });
  if (f.sort === "match" && hasProfile()) list = list.slice().sort((a, b) => state.scores.get(b.id).score - state.scores.get(a.id).score);
  else if (f.sort === "company") list = list.slice().sort((a, b) => a.company.localeCompare(b.company));
  return { list, f };
}

function renderJobs(reset = true) {
  if (reset) state.shown = PAGE;
  const { list, f } = filtered();
  writeUrl(f);
  $("#count").textContent = `${fmt(list.length)} job${list.length === 1 ? "" : "s"}${list.length !== state.jobs.length ? ` of ${fmt(state.jobs.length)}` : ""}`;
  const ol = $("#job-list");
  if (!list.length) {
    ol.replaceChildren(h("li", { class: "empty" }, "No jobs match these filters. Try removing one, or search for a broader term."));
  } else {
    ol.replaceChildren(...list.slice(0, state.shown).map((j) => jobCard(j)));
  }
  $("#more").hidden = list.length <= state.shown;
}

function searchFor(term) {
  $("#q").value = term;
  location.hash = "#jobs";
  renderJobs();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ------------------------------------------------------------------ for-you view
function renderProfileForm() {
  const p = state.profile;
  $("#p-resume").value = p.resume;
  $("#p-roles").value = p.roles;
  $("#p-exp").value = p.exp;
  $("#p-exclude").value = p.exclude;
  $("#p-remote").checked = p.remote;
  renderSkillChips();
  const cities = (state.stats?.cities || []).map((c) => c.city).slice(0, 10);
  for (const c of p.cities) if (!cities.includes(c)) cities.push(c);
  $("#p-cities").replaceChildren(...cities.map((c) => {
    const input = h("input", { type: "checkbox", value: c });
    input.checked = p.cities.includes(c);
    input.addEventListener("change", () => {
      p.cities = input.checked ? [...p.cities, c] : p.cities.filter((x) => x !== c);
      saveProfile();
    });
    return h("label", { class: "toggle" }, input, c);
  }));
}
function renderSkillChips() {
  const eff = effectiveProfile(state.profile);
  $("#p-skill-count").textContent = eff.skills.length ? `(${eff.skills.length})` : "";
  $("#p-skills").replaceChildren(...(eff.skills.length
    ? eff.skills.map((sk) => h("button", { class: "chip match", type: "button", title: `Remove ${sk}`, onclick: () => removeSkill(sk) }, sk, h("span", { class: "x", "aria-hidden": "true" }, "×")))
    : [h("span", { class: "via" }, "Paste your resume above, or add skills one by one.")]));
}
function removeSkill(sk) {
  const p = state.profile;
  p.added = p.added.filter((x) => x !== sk);
  if (!p.removed.includes(sk)) p.removed.push(sk);
  saveProfile();
  renderSkillChips();
}
function addSkill(raw) {
  const sk = state.tax.canonicalize(raw) || raw.trim();
  if (!sk) return;
  const p = state.profile;
  p.removed = p.removed.filter((x) => x !== sk);
  if (!p.added.includes(sk)) p.added.push(sk);
  saveProfile();
  renderSkillChips();
}
function saveProfile() { store.set("jr_profile", state.profile); }

function renderMatches() {
  computeScores();
  const scored = state.jobs
    .map((j) => ({ job: j, result: state.scores.get(j.id) }))
    .filter((x) => x.result && x.result.score > 0)
    .sort((a, b) => b.result.score - a.result.score);
  const top = scored.slice(0, 50);
  $("#match-count").textContent = top.length
    ? `Your top ${top.length} matches out of ${fmt(state.jobs.length)} open jobs`
    : "Add your skills or target roles to see matches.";
  $("#match-list").replaceChildren(...top.map((x) => jobCard(x.job, { showScore: true })));
  const gaps = skillGaps(top, 8);
  $("#gaps-panel").hidden = !gaps.length;
  if (gaps.length) {
    const fig = h("figure", { class: "chart", style: "border:0;padding:0" }, h("figcaption", { class: "visually-hidden" }, "Skills that would unlock more jobs"));
    $("#gaps").replaceChildren(fig);
    hbar(fig, gaps.map((g) => ({ label: g.skill, value: g.count })), {
      tooltip: (r) => `of your top ${top.length} matches ask for ${r.label}`,
      onClick: (r) => searchFor(r.label),
    });
  }
  $("#sort").querySelector('option[value="match"]').disabled = !hasProfile();
}

// ------------------------------------------------------------------ insights view
function renderInsights() {
  const st = state.stats;
  if (!st) return;
  const t = st.totals;
  const tile = (label, value, note) => h("div", { class: "tile" }, h("div", { class: "label" }, label), h("div", { class: "value" }, fmt(value)), note ? h("div", { class: "note" }, note) : null);
  $("#tiles").replaceChildren(
    tile("Open jobs", t.open, `${fmt(t.companies)} companies`),
    tile("New in 24 hours", t.new_24h, `${fmt(t.new_7d)} this week`),
    tile("Fresher-friendly", t.fresher, "open to 0–1 yrs"),
    tile("Remote", t.remote, "open to India"),
    tile("AI-summarized", t.ai_enriched, "via free LLM APIs"),
  );
  columns($("#chart-daily"), st.daily.map((d) => ({ label: d.date, value: d.count })));
  hbar($("#chart-skills"), st.top_skills.slice(0, 15).map((x) => ({ label: x.skill, value: x.count, x })), {
    tooltip: (r) => `open jobs ask for ${r.label} · ${r.x.this_week} new this week (last week ${r.x.last_week})`,
    onClick: (r) => searchFor(r.label),
  });
  hbar($("#chart-cities"), st.cities.slice(0, 10).map((c) => ({ label: c.city, value: c.count })), {
    tooltip: (r) => `open jobs in ${r.label}`,
    onClick: (r) => { $("#f-city").value = r.label; location.hash = "#jobs"; renderJobs(); },
  });
  hbar($("#chart-roles"), st.roles.slice(0, 10).map((r) => ({ label: ROLE_LABELS[r.role] || r.role, value: r.count, role: r.role })), {
    tooltip: (r) => `open ${r.label} jobs`,
    onClick: (r) => { $("#f-role").value = r.role; location.hash = "#jobs"; renderJobs(); },
  });
  $("#rising").replaceChildren(...(st.rising_skills.length
    ? st.rising_skills.map((sk) => h("button", { class: "chip accent", type: "button", onclick: () => searchFor(sk) }, sk))
    : [h("span", { class: "via" }, "Needs a week of history. Check back soon.")]));
  $("#companies").replaceChildren(
    h("thead", {}, h("tr", {}, h("th", {}, "Company"), h("th", { class: "num" }, "Open jobs"))),
    h("tbody", {}, st.companies.slice(0, 12).map((c) => h("tr", {}, h("td", {}, h("a", { href: "#jobs", onclick: (e) => { e.preventDefault(); searchFor(c.company); } }, c.company)), h("td", { class: "num" }, fmt(c.count))))));
  $("#runs").replaceChildren(
    h("thead", {}, h("tr", {}, h("th", {}, "Started (IST)"), h("th", {}, "Status"), h("th", { class: "num" }, "New"), h("th", { class: "num" }, "LLM calls"), h("th", { class: "num" }, "Time"))),
    h("tbody", {}, st.runs.map((r) => h("tr", {},
      h("td", {}, new Date(r.started_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })),
      h("td", {}, h("span", { class: "status " + (r.status === "ok" ? "ok" : "bad") }, r.status)),
      h("td", { class: "num" }, r.new ?? "–"), h("td", { class: "num" }, r.llm_requests ?? "–"),
      h("td", { class: "num" }, r.duration_s ? `${Math.round(r.duration_s)}s` : "–")))));
  const bad = st.health.filter((x) => !x.ok).length;
  $("#health-hint").textContent = `${st.health.length - bad} of ${st.health.length} boards healthy.`;
  $("#health").replaceChildren(
    h("thead", {}, h("tr", {}, h("th", {}, "Board"), h("th", {}, "Status"), h("th", { class: "num" }, "Jobs seen"))),
    h("tbody", {}, st.health.slice().sort((a, b) => a.ok - b.ok || b.count - a.count).map((x) => h("tr", {},
      h("td", {}, x.target),
      h("td", { title: x.error || "" }, h("span", { class: "status " + (x.ok ? "ok" : "bad") }, x.ok ? "ok" : `failing ×${x.failures}`)),
      h("td", { class: "num" }, fmt(x.count))))));
}

// ------------------------------------------------------------------ subscribe view
function feedRow(f) {
  const url = new URL(f.path, location.href).href;
  const copy = h("button", { class: "btn", type: "button" }, "Copy link");
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(url); copy.textContent = "Copied"; } catch { copy.textContent = "Copy failed"; }
    setTimeout(() => (copy.textContent = "Copy link"), 1500);
  });
  return h("div", { class: "feed" }, h("div", {}, h("b", {}, f.label), " ", h("span", { class: "via" }, `${fmt(f.count)} jobs`)),
    h("div", { class: "job-actions" }, h("a", { class: "btn ghost", href: f.path, target: "_blank" }, "Open"), copy));
}
function renderSubscribe() {
  const feeds = state.feeds;
  $("#feeds-main").replaceChildren(...feeds.filter((f) => ["all", "fresher", "remote"].includes(f.key)).map(feedRow));
  const fill = (sel, prefix) => {
    const opts = feeds.filter((f) => f.key.startsWith(prefix));
    $(sel).replaceChildren(h("option", { value: "" }, "Choose…"), ...opts.map((f) => h("option", { value: f.key }, `${f.label} (${f.count})`)));
  };
  fill("#feed-skill", "skill-");
  fill("#feed-city", "city-");
  const pick = (e) => {
    const f = feeds.find((x) => x.key === e.target.value);
    $("#feeds-picked").replaceChildren(...(f ? [feedRow(f)] : []));
  };
  $("#feed-skill").onchange = pick;
  $("#feed-city").onchange = pick;
  const site = state.stats?.site || {};
  // The Telegram block stays hidden until a public channel is set in config/settings.yaml.
  if (site.telegram_channel_url) {
    $("#tg-section").hidden = false;
    $("#tg-link").replaceChildren(h("a", { class: "btn primary", href: safeUrl(site.telegram_channel_url), target: "_blank", rel: "noopener" }, "Join the Telegram channel"));
  }
}

// ------------------------------------------------------------------ routing & boot
function route() {
  const tab = (location.hash || "#jobs").slice(1).split("?")[0] || "jobs";
  const valid = ["jobs", "foryou", "insights", "subscribe"].includes(tab) ? tab : "jobs";
  for (const v of document.querySelectorAll(".view")) v.hidden = v.id !== `view-${valid}`;
  for (const a of document.querySelectorAll(".tabs a")) {
    if (a.dataset.tab === valid) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (valid === "insights") renderInsights();
  if (valid === "foryou") renderMatches();
}

function initTheme() {
  $("#theme-toggle").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("jr_theme", next); } catch { /* ignore */ }
  });
}

async function boot() {
  initTheme();
  state.saved = new Set(store.get("jr_saved", []));
  state.profile = loadProfile();
  let jobs, stats, tax;
  try {
    [jobs, stats, tax] = await Promise.all(["data/jobs.json", "data/stats.json", "data/taxonomy.json"].map((u) => fetch(u).then((r) => { if (!r.ok) throw new Error(u); return r.json(); })));
    state.feeds = await fetch("feeds/index.json").then((r) => r.json()).catch(() => []);
  } catch (err) {
    $("#updated").textContent = "Couldn't load job data. The first pipeline run may still be in progress.";
    $("#job-list").replaceChildren(h("li", { class: "empty" }, "No data yet. Refresh in a few minutes."));
    return;
  }
  state.jobs = jobs; state.stats = stats; state.tax = new Taxonomy(tax);

  const site = stats.site || {};
  if (site.tagline) $("#tagline").textContent = site.tagline;
  if (site.title) document.title = site.title;
  if (site.repo_url) $("#repo-link").href = safeUrl(site.repo_url);
  $("#updated").replaceChildren(h("span", { class: "dot-live" }), `Updated ${ago(stats.generated_at)}`);
  $("#hero-counts").textContent = `${fmt(stats.totals.open)} open jobs · ${fmt(stats.totals.new_24h)} new today · ${fmt(stats.totals.companies)} companies`;
  $("#footer-updated").textContent = `Data refreshed ${new Date(stats.generated_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST`;

  for (const c of stats.cities) $("#f-city").append(h("option", { value: c.city }, `${c.city} (${c.count})`));
  for (const r of stats.roles) $("#f-role").append(h("option", { value: r.role }, `${ROLE_LABELS[r.role] || r.role} (${r.count})`));
  $("#skill-options").replaceChildren(...state.tax.skills.map((sk) => h("option", { value: sk })));

  applyUrl();
  computeScores();
  $("#sort").querySelector('option[value="match"]').disabled = !hasProfile();
  renderJobs();
  renderProfileForm();
  renderSubscribe();

  let timer;
  const debounced = () => { clearTimeout(timer); timer = setTimeout(() => renderJobs(), 120); };
  $("#q").addEventListener("input", debounced);
  for (const sel of [...Object.values(F), ...Object.values(T), "#sort"]) if (sel !== "#q") $(sel).addEventListener("change", () => renderJobs());
  $("#more").addEventListener("click", () => { state.shown += PAGE; renderJobs(false); });

  const p = state.profile;
  const bind = (sel, key, parse = (v) => v) => $(sel).addEventListener("input", (e) => { p[key] = parse(e.target.value); saveProfile(); if (key === "resume") renderSkillChips(); });
  bind("#p-resume", "resume"); bind("#p-roles", "roles"); bind("#p-exp", "exp", Number); bind("#p-exclude", "exclude");
  $("#p-remote").addEventListener("change", (e) => { p.remote = e.target.checked; saveProfile(); });
  $("#p-skill-add").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addSkill(e.target.value); e.target.value = ""; } });
  $("#p-skill-add").addEventListener("change", (e) => { if (state.tax.skills.includes(e.target.value)) { addSkill(e.target.value); e.target.value = ""; } });
  $("#p-go").addEventListener("click", () => { renderMatches(); renderJobs(); $("#match-count").scrollIntoView({ behavior: "smooth", block: "start" }); });
  $("#p-clear").addEventListener("click", () => {
    state.profile = Object.assign(p, { resume: "", added: [], removed: [], roles: "", exp: 0, exclude: "", cities: [], remote: true });
    saveProfile(); renderProfileForm(); renderMatches(); renderJobs();
  });

  window.addEventListener("hashchange", route);
  route();
}

boot();
