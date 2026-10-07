// Nightshift hosted: login, a project per client, their tests, runs and client reports.
// Plain JS, no build step. Every request sends X-Nightshift: 1, which the server requires for
// changes (a form on another site can't send it). The page's CSP allows no inline styles, so all
// looks are classes in app.css; bars are SVG, whose sizes are attributes, not styles.

const view = document.getElementById("view");
const side = document.getElementById("side");
let me = null;
let workspace = null;  // the agency's workspace: its name shows in the sidebar
let poll = null;
// Each page load gets a number. A load that finishes after the user moved on (an auto-refresh still in
// flight, a slow request) is stale and must not paint over the page they are on now.
let routeToken = 0;
const stale = (token) => token !== routeToken;

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const lines = (text) => esc(text).replace(/\n/g, "&#10;");  // a newline inside an attribute

// --- icons (24 px line icons, drawn with currentColor) --------------------------------------
const PATHS = {
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  sparkles: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.7 1.8 1.8.7-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7z"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="M16 8l-2 6-6 2 2-6z"/>',
  upload: '<path d="M12 15V3M7 8l5-5 5 5M5 21h14"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="m5.7 5.7 12.6 12.6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  loader: '<path d="M21 12a9 9 0 1 1-6.2-8.6"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  monitor: '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
  phone: '<rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M11 18h2"/>',
  external: '<path d="M14 3h7v7M21 3l-9 9"/><path d="M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/>',
  report: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  log: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  left: '<path d="m15 18-6-6 6-6"/>',
  right: '<path d="m9 18 6-6-6-6"/>',
  key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.3-9.3M17 6l3 3M15 8l2 2"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  ticket: '<path d="M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v3a2 2 0 0 0 0 4v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3a2 2 0 0 0 0-4z"/>',
  message: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
  list: '<path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
};
const icon = (name, cls = "") => `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name] || ""}</svg>`;

// --- small pieces ---------------------------------------------------------------------------
const TARGETS = { chrome: ["Chrome", "monitor"], firefox: ["Firefox", "monitor"], safari: ["Safari", "monitor"],
  iphone: ["iPhone", "phone"], android: ["Android", "phone"] };
const initials = (name) => (String(name).match(/[A-Za-z0-9]+/g) || ["?"]).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function ago(seconds) {
  if (!seconds) return "";
  const s = Date.now() / 1000 - seconds;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}
const fullTime = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleString() : "");
const took = (run) => {
  if (!run.started || !run.finished) return "";
  const s = Math.round(run.finished - run.started);
  return s < 90 ? `${s} s` : `${Math.round(s / 60)} min`;
};

// How a run went, as one tone: ok, bad, warn, block, run, info (explore/generate), off.
function runTone(run) {
  if (["queued", "running"].includes(run.status)) return "run";
  if (run.status === "stopped") return "off";
  if (run.status === "failed") return "bad";
  if (["explore", "generate"].includes(run.trigger)) return "info";
  if (run.failed) return "bad";
  if (run.flaky) return "warn";
  if (run.errors && !run.passed) return "block";
  if (run.errors) return "warn";
  return "ok";
}
const TONE_ICON = { ok: "check", bad: "x", warn: "alert", block: "ban", run: "loader", info: "sparkles", off: "stop" };
const statusIcon = (tone) => `<span class="status-icon ${tone}">${icon(TONE_ICON[tone] || "clock", `${tone} ${tone === "run" ? "spin" : ""}`)}</span>`;
const RUN_LABEL = { queued: "Queued", running: "Running", done: "Done", failed: "Didn't finish", stopped: "Stopped" };
function runPill(run) {
  const tone = runTone(run);
  const label = ["queued", "running", "stopped", "failed"].includes(run.status) ? RUN_LABEL[run.status]
    : { ok: "All passed", bad: "Bugs found", warn: "Needs a look", block: "Blocked", info: "Done" }[tone];
  return `<span class="pill ${tone === "info" ? "run" : tone}">${label}</span>`;
}
const VERDICT = { pass: ["ok", "Passed"], fail: ["bad", "Failed"], flaky: ["warn", "Flaky"], error: ["block", "Couldn't finish"] };

function resultBar(run) {
  const total = run.passed + run.failed + run.flaky + run.errors;
  if (!total) return "";
  let x = 0;
  const part = (n, cls) => { if (!n) return ""; const w = (100 * n) / total; const r = `<rect class="${cls}" x="${x}" y="0" width="${w}" height="8"/>`; x += w; return r; };
  return `<svg class="bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">${part(run.passed, "b-ok")}${part(run.flaky, "b-warn")}${part(run.failed, "b-bad")}${part(run.errors, "b-block")}</svg>`;
}
function countsLine(run) {
  if (["queued", "running"].includes(run.status)) return `<span class="run">${run.status === "queued" ? "Waiting to start…" : "Running…"}</span>`;
  if (run.status !== "done" || ["explore", "generate"].includes(run.trigger)) return `<span class="muted">${esc(run.message || "")}</span>`;
  const parts = [[run.passed, "passed", "ok"], [run.failed, "failed", "bad"], [run.flaky, "flaky", "warn"], [run.errors, "couldn't finish", "block"]]
    .filter(([n]) => n).map(([n, word, cls]) => `<span class="${cls}">${n} ${word}</span>`);
  return parts.join("") || '<span class="muted">No tests ran</span>';
}
function historyStrip(history) {
  const cells = (history || []).map((run) => `<i class="${runTone(run)}" title="#${run.id} · ${esc(run.trigger)}: ${run.passed} passed, ${run.failed} failed"></i>`);
  for (let i = cells.length; i < 10; i++) cells.unshift("<i></i>");
  return `<div class="history" aria-label="last runs">${cells.join("")}</div>`;
}
const targetChips = (targets) => (targets || "chrome").split(",").map((t) => {
  const [label, ic] = TARGETS[t] || [t, "monitor"];
  return `<span class="chip">${icon(ic)}${esc(label)}</span>`;
}).join("");
// "checkout@iphone" -> the test's name and a chip for where it ran. When a run used several targets, a
// name without "@" ran on Chrome, and says so.
function testName(spec, several) {
  const [name, target = several ? "chrome" : ""] = spec.split("@");
  if (!target) return `<span>${esc(name)}</span>`;
  const [label, ic] = TARGETS[target] || [target, "monitor"];
  return `<span>${esc(name)}</span><span class="chip">${icon(ic)}${esc(label)}</span>`;
}
const linkButton = (href, label, name) => (href
  ? `<a class="btn ghost small" href="${esc(href)}" target="_blank" rel="noopener" title="${esc(label)}">${icon(name)}<span class="hide-sm">${esc(label)}</span></a>` : "");

function toast(message, tone = "") {
  const box = document.createElement("div");
  box.className = `toast ${tone}`;
  box.innerHTML = `${icon(tone === "bad" ? "alert" : "check")}<span></span>`;
  box.querySelector("span").textContent = message;
  document.getElementById("toasts").append(box);
  setTimeout(() => box.remove(), 4200);
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Nightshift": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== "/api/login") {
    me = null;
    location.hash = "#/login";
    throw new Error(data.error || "log in first");
  }
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

// Wire a form to an action; its .error element shows what went wrong.
function onSubmit(form, action) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = form.querySelector(".error");
    if (error) error.textContent = "";
    const button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    try {
      await action(Object.fromEntries(new FormData(form)));
    } catch (exc) {
      if (error) error.textContent = exc.message;
      else toast(exc.message, "bad");
    } finally {
      if (button) button.disabled = false;
    }
  });
}
// A button that calls the API, shows a toast, and refreshes the page.
function action(button, run, message) {
  button?.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const result = await run();
      const text = typeof message === "function" ? message(result) : message;
      if (text) toast(text);
      route();
    } catch (exc) {
      toast(exc.message, "bad");
      button.disabled = false;
    }
  });
}

// --- the sidebar ----------------------------------------------------------------------------

let clients = [];
async function renderSide(active) {
  side.hidden = false;
  try { clients = (await api("GET", "/api/projects")).projects; } catch { clients = []; }
  const nav = [["#/", "grid", "Clients", "projects"], ...(me.admin ? [["#/users", "users", "Team", "users"]] : []),
    ...(me.owner ? [["#/workspaces", "layers", "Workspaces", "workspaces"]] : []), ["#/account", "user", "Account", "account"]];
  side.innerHTML = `
    <a href="#/" class="logo"><span class="logo-mark">${icon("moon")}</span><span class="word">Nightshift QA</span></a>
    <div class="side-ws" title="Your workspace">${esc(workspace?.name || "")}</div>
    <nav class="side-nav">${nav.map(([href, ic, label, key]) => `<a href="${href}" class="${active === key ? "on" : ""}">${icon(ic)}<span class="word">${label}</span></a>`).join("")}</nav>
    <div class="side-label">Clients</div>
    <div class="side-projects">${clients.map((p) => `<a href="#/p/${esc(p.slug)}" class="${active === `p:${p.slug}` ? "on" : ""}">
      <span class="dot ${p.last_run ? runTone(p.last_run) : ""}"></span><span class="name">${esc(p.client)}</span></a>`).join("")
      || '<div class="side-label">None yet</div>'}</div>
    <div class="side-foot"><div class="who"><div>${esc(me.name || me.email)}</div><div class="muted">${me.admin ? "Admin" : "Staff"}</div></div>
      <button type="button" class="ghost small" id="logout" title="Log out">${icon("logout")}</button></div>`;
  document.getElementById("logout").addEventListener("click", async () => {
    await api("POST", "/api/logout").catch(() => {});
    me = workspace = null;
    location.hash = "#/login";
  });
}

// --- pages ----------------------------------------------------------------------------------

function loginPage() {
  side.hidden = true;
  view.classList.add("bare");
  view.innerHTML = `
    <div class="auth">
      <div class="auth-brand">
        <div class="logo"><span class="logo-mark">${icon("moon")}</span>Nightshift QA</div>
        <h1>AI regression testing your clients can trust.</h1>
        <ul class="proof">
          <li>${icon("check")} Plain-English tests, run every night in a real browser</li>
          <li>${icon("check")} Every pass proven by a quote from the page</li>
          <li>${icon("check")} Visual checks, and why every failure happened</li>
          <li>${icon("check")} A client report with your agency's name after each run</li>
        </ul>
      </div>
      <div class="auth-form">
        <form class="card stack" id="login">
          <div><h2>Welcome back</h2><p class="muted">Log in to your agency's workspace.</p></div>
          <label>Email <input name="email" type="email" autocomplete="username" required autofocus></label>
          <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
          <p class="error" role="alert"></p>
          <button type="submit">Log in</button>
          <div class="auth-links">
            <p>New here? <a href="https://nightshift-qa.github.io/pilot/#apply" target="_blank" rel="noopener">Apply for a free pilot</a></p>
            <p class="muted small">Invited by your team? Open the invite link you were sent to set up your account.</p>
          </div>
        </form>
      </div>
    </div>`;
  onSubmit(document.getElementById("login"), async (form) => {
    me = (await api("POST", "/api/login", form)).user;
    workspace = null;  // fetched with the next page
    location.hash = "#/";
  });
}

// An invite link: who it's from, then a name, email and password make the account.
async function joinPage(inviteToken) {
  side.hidden = true;
  view.classList.add("bare");
  const shell = (inner) => `
    <div class="auth">
      <div class="auth-brand">
        <div class="logo"><span class="logo-mark">${icon("moon")}</span>Nightshift QA</div>
        <h1>AI regression testing your clients can trust.</h1>
        <ul class="proof">
          <li>${icon("check")} Plain-English tests, run every night in a real browser</li>
          <li>${icon("check")} Every pass proven by a quote from the page</li>
          <li>${icon("check")} A client report with your agency's name after each run</li>
        </ul>
      </div>
      <div class="auth-form">${inner}</div>
    </div>`;
  let invite;
  try {
    invite = await api("GET", `/api/join/${encodeURIComponent(inviteToken)}`);
  } catch (exc) {
    view.innerHTML = shell(`<div class="card stack"><div><h2>This link doesn't work</h2>
      <p class="muted">${esc(exc.message)}</p></div><a class="btn" href="#/login">Go to log in</a></div>`);
    return;
  }
  view.innerHTML = shell(`
    <form class="card stack" id="join">
      <div><h2>Join ${esc(invite.workspace)}</h2>
        <p class="muted">You've been invited${invite.admin ? " as an admin" : ""}. Set up your account to get started.</p></div>
      <label>Your name <input name="name" autocomplete="name" required autofocus></label>
      <label>Work email <input name="email" type="email" autocomplete="username" required></label>
      <label>Password <span class="hint">10+ characters</span><input name="password" type="password" autocomplete="new-password" minlength="10" required></label>
      <p class="error" role="alert"></p>
      <button type="submit">Create my account</button>
      <p class="muted small">Already have one? <a href="#/login">Log in</a></p>
    </form>`);
  onSubmit(document.getElementById("join"), async (fields) => {
    me = (await api("POST", `/api/join/${encodeURIComponent(inviteToken)}`, fields)).user;
    workspace = null;
    toast(`Welcome to ${invite.workspace}`);
    location.hash = "#/";
  });
}

// A new invite: the link, shown this once (only its hash is kept), with a copy button.
function inviteResult(made, who) {
  const link = made.link || `${location.origin}${made.path}`;
  return `
    <div class="card stack invite-made">
      <div class="card-head">${icon("check")}<h3>Invite link for ${esc(who || "your invite")}</h3></div>
      <div class="copy-row"><input readonly value="${esc(link)}" id="invite-link"><button type="button" id="copy-link">Copy link</button></div>
      <p class="muted small">Send it to them yourself (email or WhatsApp). It works once and expires in 7 days.
        Copy it now: for safety it can't be shown again.${made.link ? "" : " This link uses this computer's address; start Nightshift QA with its public link for one that works anywhere."}</p>
    </div>`;
}
function wireCopy() {
  document.getElementById("copy-link")?.addEventListener("click", async () => {
    const input = document.getElementById("invite-link");
    try { await navigator.clipboard.writeText(input.value); } catch { input.select(); document.execCommand("copy"); }
    toast("Link copied");
  });
}

async function projectsPage() {
  const token = routeToken;
  await renderSide("projects");
  if (stale(token)) return;
  const cards = clients.map((p) => {
    const last = p.last_run;
    return `
    <a class="card client" href="#/p/${esc(p.slug)}">
      <div class="client-top">
        <span class="avatar">${esc(initials(p.client))}</span>
        <div class="grow"><div class="client-name">${esc(p.client)}</div>
          <div class="muted small ellipsis">${esc(p.base_url) || "No main website"}</div></div>
        ${last ? runPill(last) : '<span class="pill off">No runs yet</span>'}
      </div>
      <div class="chips"><span class="chip">${icon("calendar")}${p.nightly ? `Nightly ${esc(p.nightly)}` : "No schedule"}</span>${targetChips(p.targets)}</div>
      <div class="last">
        ${last ? `${resultBar(last)}<div class="last-line"><div class="counts">${countsLine(last)}</div>
          <span class="muted" title="${esc(fullTime(last.queued))}">${esc(ago(last.queued))}</span></div>` : '<div class="muted small">Add tests, then run them or schedule a nightly run.</div>'}
      </div>
      ${historyStrip(p.history)}
    </a>`;
  }).join("");
  view.innerHTML = `
    <div class="page-head">
      <div><h1>Clients</h1><div class="sub">One project per client: its tests, a nightly run, and a report you can send.</div></div>
      ${me.admin ? `<div class="head-actions"><button type="button" id="new-client">${icon("plus")}New client</button></div>` : ""}
    </div>
    <form class="card stack" id="new-project" hidden>
      <div class="card-head">${icon("plus")}<h3>New client</h3></div>
      <div class="fields">
        <label>Client name <input name="client" required placeholder="Acme Retail"></label>
        <label>Website <span class="hint">optional: their staging site</span><input name="base_url" type="url" placeholder="https://staging.acme.example"></label>
        <label>Reports prepared by <span class="hint">your agency's name</span><input name="brand" placeholder="Your QA Co"></label>
        <label>Nightly run at <span class="hint">24-hour, empty for none</span><input name="nightly" placeholder="02:30"></label>
      </div>
      <p class="error" role="alert"></p>
      <div class="actions"><button type="submit">Create client</button><button type="button" class="ghost" id="cancel-new">Cancel</button></div>
    </form>
    ${cards ? `<div class="grid">${cards}</div>` : `
    <div class="card empty">${icon("layers")}<h3>No clients yet</h3>
      <p>Each client gets its own tests, nightly schedule and branded reports.</p>
      ${me.admin ? `<div class="actions"><button type="button" id="new-client-empty">${icon("plus")}Add your first client</button></div>` : ""}</div>`}`;
  const form = document.getElementById("new-project");
  const open = () => { form.hidden = false; form.elements.client.focus(); };
  document.getElementById("new-client")?.addEventListener("click", open);
  document.getElementById("new-client-empty")?.addEventListener("click", open);
  document.getElementById("cancel-new")?.addEventListener("click", () => { form.hidden = true; });
  onSubmit(form, async (fields) => {
    const { project } = await api("POST", "/api/projects", fields);
    toast(`${project.client} added. Now give it some tests.`);
    location.hash = `#/p/${project.slug}/tests`;
  });
}

async function projectPage(slug, tab, extra) {
  const token = routeToken;
  const [data] = await Promise.all([api("GET", `/api/projects/${slug}`), renderSide(`p:${slug}`)]);
  if (stale(token)) return;
  const p = data.project;
  const active = data.runs.find((r) => ["queued", "running"].includes(r.status));
  const tabs = [["runs", "Runs", data.runs.length], ["tests", "Tests", data.specs.length], ...(me.admin ? [["settings", "Settings", null]] : [])]
    .map(([key, label, n]) => `<a href="#/p/${esc(slug)}/${key}" class="${tab === key || (tab === "run" && key === "runs") ? "on" : ""}">${label}${n !== null ? `<span class="count">${n}</span>` : ""}</a>`).join("");
  view.innerHTML = `
    <div class="crumbs"><a href="#/">Clients</a>${icon("right")}<span>${esc(p.client)}</span></div>
    <div class="page-head">
      <div class="head-left"><span class="avatar lg">${esc(initials(p.client))}</span>
        <div><h1>${esc(p.client)}</h1>
          <div class="chips">${p.base_url ? `<span class="chip">${icon("globe")}${esc(p.base_url)}</span>` : ""}
            <span class="chip">${icon("calendar")}${p.nightly ? `Nightly ${esc(p.nightly)}` : "No schedule"}</span>${targetChips(p.targets)}
            <span class="chip">${icon("layers")}${data.parallel} at a time</span></div></div></div>
      <div class="head-actions">
        ${active ? `${runPill(active)}<button type="button" class="danger" id="stop" data-run="${active.id}">${icon("stop")}Stop</button>`
          : `<button type="button" id="run-now" ${data.specs.length ? "" : "disabled title=\"Add tests first\""}>${icon("play")}Run now</button>`}
      </div>
    </div>
    <nav class="tabs">${tabs}</nav>
    <section id="tab"></section>`;
  action(document.getElementById("run-now"), () => api("POST", `/api/projects/${slug}/runs`), "Run started");
  const stop = document.getElementById("stop");
  action(stop, () => api("POST", `/api/projects/${slug}/runs/${stop.dataset.run}/stop`), "Run stopped");
  const section = document.getElementById("tab");
  if (tab === "run") return runDetail(section, slug, extra);
  if (tab === "tests") return testsTab(section, slug, data, extra);
  if (tab === "settings" && me.admin) return settingsTab(section, slug, data);
  runsTab(section, slug, data, Boolean(active));
  if (active) poll = setTimeout(route, 4000);  // follow the run until it finishes
}

function runsTab(section, slug, data, busy) {
  const rows = data.runs.map((run) => `
    <tr class="click" data-href="#/p/${esc(slug)}/run/${run.id}">
      <td class="status">${statusIcon(runTone(run))}</td>
      <td><div class="cell-title"><a href="#/p/${esc(slug)}/run/${run.id}">Run #${run.id}</a>${runPill(run)}</div>
        <div class="muted small">${esc({ manual: "Started by hand", nightly: "Nightly", explore: "Explore", generate: "Generate tests" }[run.trigger] || run.trigger)}${run.target ? ` · ${esc(run.target)}` : ""}</div></td>
      <td class="bar-cell hide-sm">${run.status === "done" && !["explore", "generate"].includes(run.trigger) ? resultBar(run) : ""}<div class="counts small">${countsLine(run)}</div></td>
      <td class="narrow"><div title="${esc(fullTime(run.queued))}">${esc(ago(run.queued))}</div><div class="muted small">${esc(took(run))}</div></td>
      <td class="narrow"><div class="links">${linkButton(run.links.client_report, "Client report", "report")}${linkButton(run.links.report, "Full report", "file")}${linkButton(run.links.findings, "Findings", "list")}${linkButton(run.links.log, "Log", "log")}</div></td>
    </tr>`).join("");
  section.innerHTML = `
    <details class="card panel" id="explore-panel">
      <summary>${icon("compass")}Explore a website for bugs <span class="hint">no tests needed</span>${icon("right", "chev")}</summary>
      <form class="stack" id="explore">
        <p class="muted">Paste an address: the AI uses the site like a curious user and lists what it found broken. Only sites you own or are allowed to test.</p>
        <div class="fields">
          <label>Website <input name="url" type="url" required placeholder="https://academybugs.com/" value="${esc(data.project.base_url)}"></label>
          <label>Actions <span class="hint">how many clicks it may use</span><input name="steps" type="number" min="5" max="60" value="25"></label>
          <label>Focus on <span class="hint">optional</span><input name="focus" placeholder="the checkout"></label>
        </div>
        <p class="error" role="alert"></p>
        <div class="actions"><button type="submit" ${busy ? "disabled" : ""}>${icon("compass")}Explore</button></div>
      </form>
    </details>
    ${rows ? `<div class="table-wrap"><table><thead><tr><th></th><th>Run</th><th class="hide-sm">Results</th><th>When</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<div class="card empty">${icon("play")}<h3>No runs yet</h3><p>${data.specs.length ? "Press Run now, or set a nightly time in Settings." : "Add tests on the Tests tab, or explore a website above."}</p></div>`}
    ${data.keep_runs ? `<p class="muted small">The newest ${data.keep_runs} runs keep their screenshots and reports; older ones keep only their summary.</p>` : ""}`;
  section.querySelectorAll("tr.click").forEach((row) => row.addEventListener("click", (event) => {
    if (!event.target.closest("a")) location.hash = row.dataset.href;
  }));
  onSubmit(document.getElementById("explore"), async (fields) => {
    await api("POST", `/api/projects/${slug}/explore`, fields);
    toast("Exploring: it takes a few minutes");
    route();
  });
}

const VISUAL = { baseline: "Saved as the approved look", same: "Looks as approved", changed: "Looks different (judged harmless)",
  "visual bug": "Visual bug", skipped: "Not checked (no approved look yet)" };

// One run: every test with its result, why it failed, and its visual check; and what to do next.
async function runDetail(section, slug, id) {
  const token = routeToken;
  const d = await api("GET", `/api/projects/${slug}/runs/${id}`);
  if (stale(token)) return;
  const r = d.run;
  const changes = d.tests.filter((t) => ["changed", "visual bug"].includes(t.visual.status)).length;
  const isTests = !["explore", "generate"].includes(r.trigger);
  const several = d.tests.some((t) => t.spec.includes("@"));
  const stat = (n, label, cls) => `<div class="stat ${n ? "" : "zero"}"><div class="n ${n ? cls : ""}">${n}</div><div class="l">${label}</div></div>`;
  const rows = d.tests.map((t) => {
    const [tone, label] = VERDICT[t.verdict] || ["off", t.verdict];
    const visualBad = t.visual.status === "visual bug";
    return `
    <tr>
      <td class="status">${statusIcon(visualBad && t.verdict === "pass" ? "warn" : tone)}</td>
      <td><div class="cell-title">${testName(t.spec, several)}</div>
        <div class="muted small">${label}${t.category && t.verdict !== "pass" ? ` · ${esc(t.category)}` : ""}${t.mode === "replay" ? " · replayed, no AI" : ""}</div></td>
      <td><div class="cause">${t.cause ? esc(t.cause) : t.verdict === "pass" ? '<span class="muted">Every expected result was proven on the page.</span>' : esc(t.reason)}</div>
        ${t.visual.status ? `<div class="visual-line ${visualBad ? "bad" : ""}">${icon("eye")}${esc(VISUAL[t.visual.status] || t.visual.status)}${t.visual.what && t.visual.status !== "visual bug" ? `: ${esc(t.visual.what)}` : ""}
          ${t.visual.picture ? `<a href="${esc(t.visual.picture)}" target="_blank" rel="noopener">compare</a>` : ""}</div>` : ""}</td>
      <td class="narrow"><div class="links">${linkButton(t.links.report, "Report", "file")}${linkButton(t.links.bug, "Bug report", "alert")}</div></td>
    </tr>`;
  }).join("");
  section.innerHTML = `
    <div class="page-head">
      <div><div class="cell-title"><h2 class="flush">Run #${r.id}</h2>${runPill(r)}</div>
        <div class="sub">${esc({ manual: "Started by hand", nightly: "Nightly run", explore: "Exploration", generate: "Generating tests" }[r.trigger] || r.trigger)}
          · <span title="${esc(fullTime(r.queued))}">${esc(ago(r.queued))}</span>${took(r) ? ` · took ${esc(took(r))}` : ""}${r.target ? ` · ${esc(r.target)}` : ""}</div></div>
      <div class="head-actions">${r.links.client_report ? `<a class="btn" href="${esc(r.links.client_report)}" target="_blank" rel="noopener">${icon("report")}Client report</a>` : ""}
        ${linkButton(r.links.report, "Full report", "file")}${linkButton(r.links.findings, "Findings", "list")}${linkButton(r.links.log, "Log", "log")}</div>
    </div>
    ${isTests && r.status === "done" ? `<div class="stats">${stat(r.passed, "Passed", "ok")}${stat(r.failed, "Failed", "bad")}${stat(r.flaky, "Flaky", "warn")}${stat(r.errors, "Couldn't finish", "block")}</div>` : ""}
    ${isTests && d.tests.length ? `<div class="actions">
      ${changes ? `<button type="button" class="secondary" id="accept-visual">${icon("eye")}Accept the new look (${changes})</button>` : ""}
      <button type="button" class="secondary" id="jira" ${d.jira ? "" : "disabled"}>${icon("ticket")}File bugs in Jira</button>
      <button type="button" class="secondary" id="slack" ${d.slack ? "" : "disabled"}>${icon("message")}Post to Slack</button>
      ${d.issues.length ? `<span class="muted small">Filed: ${d.issues.map((i) => `<a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.key)}</a>`).join(", ")}</span>` : ""}
    </div>
    ${d.jira && d.slack ? "" : `<p class="muted small">To use Jira or Slack, add under Settings → Secrets: ${[d.jira ? "" : "JIRA_URL, JIRA_PROJECT, JIRA_EMAIL, JIRA_API_TOKEN", d.slack ? "" : "SLACK_WEBHOOK_URL"].filter(Boolean).join("; ")}.</p>`}` : ""}
    <h2>What happened</h2>
    ${rows ? `<div class="table-wrap"><table><thead><tr><th></th><th>Test</th><th>Why</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<div class="card empty">${icon(r.trigger === "explore" ? "compass" : r.trigger === "generate" ? "sparkles" : "clock")}<h3>${
        r.trigger === "explore" ? "An exploration has findings, not test results"
        : r.trigger === "generate" ? "Generating writes draft tests"
        : ["queued", "running"].includes(r.status) ? "Still running…" : "No test results"}</h3><p>${
        r.trigger === "explore" ? "Open Findings or the Full report above."
        : r.trigger === "generate" ? "Review them on the Tests tab."
        : ["queued", "running"].includes(r.status) ? "This page updates by itself." : esc(r.message || "The run did not finish.")}</p></div>`}`;
  action(document.getElementById("accept-visual"), () => api("POST", `/api/projects/${slug}/runs/${id}/accept-visual`),
    (res) => `${plural(res.accepted, "new look")} approved`);
  action(document.getElementById("jira"), () => api("POST", `/api/projects/${slug}/runs/${id}/jira`),
    (res) => (res.filed.length ? `Filed ${res.filed.map((f) => f.key).join(", ")}` : "No bugs to file in this run"));
  action(document.getElementById("slack"), () => api("POST", `/api/projects/${slug}/runs/${id}/slack`), "Posted to Slack");
  if (["queued", "running"].includes(r.status)) poll = setTimeout(route, 4000);
}

async function testsTab(section, slug, data, editing) {
  const draft = (editing || "").startsWith("draft:");
  const name = draft ? editing.slice(6) : editing || "";
  const missing = data.missing.map((m) => `<li><span class="mono">${esc(m.test)}</span> needs <span class="mono">${esc(m.secret)}</span></li>`).join("");
  const drafts = data.drafts.map((d) => `
    <tr>
      <td class="status">${statusIcon("info")}</td>
      <td><div class="cell-title">${esc(d.name)}</div>
        ${d.review.map((r) => `<div class="muted small">Check: ${esc(r)}</div>`).join("")}
        ${d.needs.length ? `<div class="warn small">Needs secrets: ${d.needs.map(esc).join(", ")}</div>` : ""}</td>
      <td class="narrow"><div class="links">
        <a class="btn ghost small" href="#/p/${esc(slug)}/tests/draft:${esc(d.name)}">${icon("edit")}Review</a>
        <button type="button" class="secondary small" data-accept="${esc(d.name)}">${icon("check")}Accept</button>
        <button type="button" class="ghost small" data-drop="${esc(d.name)}" title="Delete">${icon("trash")}</button></div></td>
    </tr>`).join("");
  const tests = data.specs.map((n) => `
    <tr><td class="status"><span class="status-icon off">${icon("file")}</span></td><td><div class="cell-title"><a href="#/p/${esc(slug)}/tests/${esc(n)}">${esc(n)}</a></div></td>
      <td class="narrow"><div class="links"><a class="btn ghost small" href="#/p/${esc(slug)}/tests/${esc(n)}">${icon("edit")}Edit</a>
        <button type="button" class="ghost small" data-delete="${esc(n)}" title="Delete">${icon("trash")}</button></div></td></tr>`).join("");
  section.innerHTML = `
    ${missing ? `<div class="card warn-card"><strong>${icon("key")} Set these secrets before running</strong> <span class="muted">(Settings → Secrets)</span><ul>${missing}</ul></div>` : ""}
    <div class="two">
      <form class="card stack" id="generate">
        <div class="card-head">${icon("sparkles")}<h3>Generate tests with AI</h3></div>
        <p class="muted small">It explores the site like a new user, then writes draft tests for you to review. A few minutes.</p>
        <div class="fields">
          <label>Website <input name="url" type="url" required value="${esc(data.project.base_url)}" placeholder="https://academybugs.com/"></label>
          <label>How many tests <span class="hint">1 to 10</span><input name="count" type="number" min="1" max="10" value="5"></label>
        </div>
        <label>What is the site for? <span class="hint">optional, but it helps</span>
          <textarea name="about" placeholder="${lines("An online shop: people search for products, add them to a cart and check out.")}"></textarea></label>
        <details class="more"><summary>More options</summary>
          <label>Explore first <span class="hint">clicks it may use before writing, up to 40</span><input name="steps" type="number" min="0" max="40" value="20"></label></details>
        <p class="error" role="alert"></p>
        <div class="actions"><button type="submit">${icon("sparkles")}Generate tests</button></div>
      </form>
      <form class="card stack" id="gherkin">
        <div class="card-head">${icon("upload")}<h3>Import Cucumber tests</h3></div>
        <p class="muted small">Paste a .feature file. Each Scenario becomes a draft: Given/When are steps, Then is what should happen.</p>
        <label>Website <span class="hint">if the feature doesn't say</span><input name="url" type="url" value="${esc(data.project.base_url)}" placeholder="https://staging.example.com/"></label>
        <label>Feature <textarea name="text" class="mono" spellcheck="false" placeholder="${lines("Feature: Checkout\n  Scenario: Pay by card\n    Given I am on the shop\n    When I add a T-shirt to the cart\n    Then the cart shows 1 item")}"></textarea></label>
        <p class="error" role="alert"></p>
        <div class="actions"><button type="submit" class="secondary">${icon("upload")}Import as drafts</button></div>
      </form>
    </div>
    ${drafts ? `
    <div class="section-title"><h2>Drafts to review <span class="count">${data.drafts.length}</span></h2>
      <button type="button" class="secondary small" id="accept-all">${icon("check")}Accept all</button></div>
    <p class="muted small">Written from what the AI saw or imported. Read each one before accepting it: a wrong test reports wrong bugs.</p>
    <div class="table-wrap"><table><tbody>${drafts}</tbody></table></div>` : ""}
    <div class="section-title"><h2>Tests <span class="count">${data.specs.length}</span></h2>
      ${name ? `<a class="btn secondary small" href="#/p/${esc(slug)}/tests">${icon("plus")}New test</a>` : ""}</div>
    ${tests ? `<div class="table-wrap"><table><tbody>${tests}</tbody></table></div>`
      : `<div class="card empty">${icon("list")}<h3>No tests yet</h3><p>Generate some with AI above, import Cucumber features, or write one below.</p></div>`}
    <form class="card stack" id="spec">
      <div class="card-head">${icon(name ? "edit" : "plus")}<h3>${draft ? `Review draft: ${esc(name)}` : name ? `Edit: ${esc(name)}` : "Write a test"}</h3></div>
      <div class="fields">
        <label>Test name <span class="hint">lowercase, digits and dashes</span><input name="name" required pattern="[a-z0-9][a-z0-9-]*" ${name ? "readonly" : ""} placeholder="checkout"></label>
        <label>Website <input name="url" type="url" required placeholder="https://staging.example.com/"></label>
      </div>
      <div class="fields">
        <label>Steps <span class="hint">what a person does, one per line</span>
          <textarea name="steps" placeholder="${lines("log in with the test account\nadd the Blue Top to the cart\nopen the cart")}"></textarea></label>
        <label>What should happen <span class="hint">one per line; empty for a one-sentence goal</span>
          <textarea name="expect" placeholder="${lines("the cart lists the Blue Top\nthe total is correct")}"></textarea></label>
      </div>
      <label>Test data <span class="hint">name = value, one per line. The AI sees only the name. For passwords, add a secret and write \${NAME}.</span>
        <textarea name="data" class="mono" placeholder="${lines("email = qa@example.com\npassword = ${SHOP_PASSWORD}")}"></textarea></label>
      <details class="more"><summary>More options</summary>
        <div class="fields">
          <label>Step limit <input name="max_steps" type="number" min="1" max="60" value="30"></label>
          <label class="check"><input type="checkbox" name="js_errors_warn" value="1"> Background JavaScript errors are only warnings</label>
        </div>
      </details>
      <p class="error" role="alert"></p>
      <div class="actions">
        <button type="submit">${icon("check")}${draft ? "Save draft" : "Save test"}</button>
        ${draft ? `<button type="button" class="secondary" id="save-accept">Save and accept</button>` : ""}
        <button type="button" class="ghost" id="as-yaml">Edit as YAML</button>
      </div>
    </form>
    <form class="card stack" id="spec-yaml" hidden>
      <div class="card-head">${icon("file")}<h3>Edit as YAML</h3></div>
      <label>Test name <input name="name" required pattern="[a-z0-9][a-z0-9-]*" ${name ? "readonly" : ""}></label>
      <label>YAML <textarea name="text" class="code" spellcheck="false"></textarea></label>
      <p class="error" role="alert"></p>
      <div class="actions"><button type="submit">Save</button><button type="button" class="ghost" id="as-form">Back to the form</button></div>
    </form>`;

  const form = document.getElementById("spec");
  const yamlForm = document.getElementById("spec-yaml");
  const base = draft ? `/api/projects/${slug}/drafts` : `/api/projects/${slug}/specs`;
  let loaded = { text: "", form: null };
  const token = routeToken;
  if (name) loaded = await api("GET", `${base}/${name}`);
  if (stale(token)) return;
  form.elements.name.value = yamlForm.elements.name.value = name;
  yamlForm.elements.text.value = loaded.text;
  const f = loaded.form || { url: data.project.base_url || "", max_steps: 30 };
  if (f.advanced) {  // an API test: only YAML describes it
    form.hidden = true;
    yamlForm.hidden = false;
    document.getElementById("as-form").hidden = true;
  } else {
    for (const key of ["url", "steps", "expect", "data", "max_steps"]) form.elements[key].value = f[key] ?? "";
    form.elements.js_errors_warn.checked = Boolean(f.js_errors_warn);
  }
  if (name) form.scrollIntoView({ block: "start" });
  const fields = () => ({
    url: form.elements.url.value, steps: form.elements.steps.value, expect: form.elements.expect.value,
    data: form.elements.data.value, max_steps: form.elements.max_steps.value, js_errors_warn: form.elements.js_errors_warn.checked,
  });
  const done = (message) => { toast(message); location.hash = `#/p/${slug}/tests`; route(); };
  onSubmit(form, async () => { await api("PUT", `${base}/${form.elements.name.value}`, { form: fields() }); done("Test saved"); });
  onSubmit(yamlForm, async (values) => { await api("PUT", `${base}/${values.name}`, { text: values.text }); done("Test saved"); });
  document.getElementById("save-accept")?.addEventListener("click", async () => {
    const error = form.querySelector(".error");
    try {
      await api("PUT", `${base}/${name}`, { form: fields() });
      await api("POST", `/api/projects/${slug}/drafts/${name}/accept`);
      done("Draft accepted as a test");
    } catch (exc) { error.textContent = exc.message; }
  });
  document.getElementById("as-yaml").addEventListener("click", () => {
    yamlForm.elements.name.value = form.elements.name.value;
    form.hidden = true;
    yamlForm.hidden = false;
  });
  document.getElementById("as-form").addEventListener("click", () => { yamlForm.hidden = true; form.hidden = false; });

  onSubmit(document.getElementById("gherkin"), async (values) => {
    const result = await api("POST", `/api/projects/${slug}/import-gherkin`, values);
    toast(`${plural(result.imported, "draft")} imported: review them below`);
    route();
  });
  onSubmit(document.getElementById("generate"), async (values) => {
    await api("POST", `/api/projects/${slug}/generate`, values);
    toast("Writing tests: follow it on the Runs tab");
    location.hash = `#/p/${slug}/runs`;
  });
  section.querySelectorAll("[data-accept]").forEach((button) => action(button,
    () => api("POST", `/api/projects/${slug}/drafts/${button.dataset.accept}/accept`), "Draft accepted"));
  section.querySelectorAll("[data-drop]").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm(`Delete the draft ${button.dataset.drop}?`)) return;
    await api("DELETE", `/api/projects/${slug}/drafts/${button.dataset.drop}`);
    toast("Draft deleted");
    route();
  }));
  // Accept-all takes every draft it can; the ones it couldn't stay as drafts, with the reason shown.
  action(document.getElementById("accept-all"), () => api("POST", `/api/projects/${slug}/drafts/accept-all`), (result) => {
    if (!result.problems.length) return "All drafts accepted";
    toast(`Some stayed drafts: ${result.problems.join(" · ")}`, "bad");
    return "";
  });
  section.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm(`Delete the test ${button.dataset.delete}?`)) return;
    await api("DELETE", `/api/projects/${slug}/specs/${button.dataset.delete}`);
    toast("Test deleted");
    route();
  }));
}

function settingsTab(section, slug, data) {
  const p = data.project;
  const chosen = (p.targets || "chrome").split(",");
  const secrets = data.secrets.map((name) => `
    <tr><td class="status">${statusIcon("ok")}</td><td><div class="cell-title mono">${esc(name)}</div><div class="muted small">set (values can't be read back)</div></td>
      <td class="narrow"><button type="button" class="ghost small" data-secret="${esc(name)}" title="Delete">${icon("trash")}</button></td></tr>`).join("");
  section.innerHTML = `
    <form class="card stack" id="settings">
      <div class="card-head">${icon("sliders")}<h3>Client</h3></div>
      <div class="fields">
        <label>Client name <input name="client" required value="${esc(p.client)}"></label>
        <label>Website <span class="hint">optional: every test runs against it, keeping its own path</span>
          <input name="base_url" value="${esc(p.base_url)}" placeholder="https://staging.acme.example"></label>
        <label>Reports prepared by <input name="brand" value="${esc(p.brand)}" placeholder="Your QA Co"></label>
        <label>Nightly run at <span class="hint">24-hour, server time; empty for none</span><input name="nightly" value="${esc(p.nightly)}" placeholder="02:30"></label>
      </div>
      <div><h3>Run every test on</h3><p class="muted small">Each test runs once on each, with results side by side.</p></div>
      <div class="targets">${Object.entries(TARGETS).map(([key, [label, ic]]) => `
        <label class="target"><input type="checkbox" name="target" value="${key}" ${chosen.includes(key) ? "checked" : ""}>${icon(ic)}${label}</label>`).join("")}</div>
      <p class="error" role="alert"></p>
      <div class="actions"><button type="submit">${icon("check")}Save settings</button></div>
    </form>
    <div class="section-title"><h2>Secrets <span class="count">${data.secrets.length}</span></h2></div>
    <p class="muted small">Test passwords and keys. A test uses one as \${NAME} in its data. Jira and Slack read JIRA_URL, JIRA_PROJECT, JIRA_EMAIL, JIRA_API_TOKEN and SLACK_WEBHOOK_URL.</p>
    ${secrets ? `<div class="table-wrap"><table><tbody>${secrets}</tbody></table></div>` : ""}
    <form class="card stack" id="secret">
      <div class="card-head">${icon("key")}<h3>Add a secret</h3></div>
      <div class="fields">
        <label>Name <input name="name" required pattern="[A-Z_][A-Z0-9_]*" placeholder="SHOP_PASSWORD"></label>
        <label>Value <input name="value" type="password" autocomplete="new-password" required></label>
      </div>
      <p class="error" role="alert"></p>
      <div class="actions"><button type="submit">${icon("plus")}Set secret</button></div>
    </form>`;
  onSubmit(document.getElementById("settings"), async (fields) => {
    const targets = [...document.querySelectorAll("#settings input[name=target]:checked")].map((box) => box.value).join(",");
    delete fields.target;
    await api("PUT", `/api/projects/${slug}`, { ...fields, targets });
    toast("Settings saved");
    route();
  });
  onSubmit(document.getElementById("secret"), async (fields) => {
    await api("PUT", `/api/projects/${slug}/secrets/${fields.name}`, { value: fields.value });
    toast(`${fields.name} set`);
    route();
  });
  section.querySelectorAll("[data-secret]").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm(`Delete the secret ${button.dataset.secret}?`)) return;
    await api("DELETE", `/api/projects/${slug}/secrets/${button.dataset.secret}`);
    toast("Secret deleted");
    route();
  }));
}

async function usersPage(made) {
  const token = routeToken;
  const [{ users }, { invites }] = await Promise.all([api("GET", "/api/users"), api("GET", "/api/invites"), renderSide("users")]);
  if (stale(token)) return;
  const pending = invites.map((i) => `
    <tr><td class="status">${statusIcon("info")}</td>
      <td><div class="cell-title">${esc(i.note || "Invite")}</div><div class="muted small">${i.admin ? "Admin" : "Staff"} · not used yet · expires ${esc(new Date(i.expires * 1000).toLocaleDateString())}</div></td>
      <td class="narrow"><button type="button" class="ghost small" data-revoke="${i.id}" title="Cancel this invite">${icon("x")}Cancel</button></td></tr>`).join("");
  const rows = users.map((u) => `
    <tr><td class="status"><span class="avatar">${esc(initials(u.name || u.email))}</span></td>
      <td><div class="cell-title">${esc(u.name || u.email)}${u.id === me.id ? '<span class="chip">you</span>' : ""}</div><div class="muted small">${esc(u.email)}</div></td>
      <td class="narrow"><span class="pill ${u.admin ? "run" : "off"}">${u.admin ? "Admin" : "Staff"}</span></td>
      <td class="narrow">${u.id === me.id ? "" : `<button type="button" class="ghost small" data-user="${u.id}" title="Delete">${icon("trash")}</button>`}</td></tr>`).join("");
  view.innerHTML = `
    <div class="page-head"><div><h1>Team</h1><div class="sub">Everyone in ${esc(workspace?.name || "your workspace")}. Admins manage clients, team and secrets; staff write tests and start runs.</div></div></div>
    <div class="table-wrap"><table><tbody>${rows}</tbody></table></div>
    ${made ? inviteResult(made.result, made.who) : ""}
    <form class="card stack" id="new-invite">
      <div class="card-head">${icon("plus")}<h3>Invite someone</h3></div>
      <p class="muted small">You get a link to send them; they choose their own name and password.</p>
      <div class="fields">
        <label>Who is it for? <span class="hint">so you know which invite is whose</span><input name="note" placeholder="Ravi, manual tester"></label>
      </div>
      <label class="check"><input name="admin" type="checkbox" value="1"> Make them an admin</label>
      <p class="error" role="alert"></p>
      <div class="actions"><button type="submit">${icon("plus")}Create invite link</button></div>
    </form>
    ${pending ? `<div class="section-title"><h2>Invites not used yet <span class="count">${invites.length}</span></h2></div>
      <div class="table-wrap"><table><tbody>${pending}</tbody></table></div>` : ""}
    <form class="card stack" id="workspace-name">
      <div class="card-head">${icon("sliders")}<h3>Workspace name</h3></div>
      <p class="muted small">Your agency's name. New clients' reports say "prepared by" it.</p>
      <div class="fields"><label>Name <input name="name" required value="${esc(workspace?.name || "")}"></label></div>
      <p class="error" role="alert"></p>
      <div class="actions"><button type="submit" class="secondary">Save name</button></div>
    </form>`;
  wireCopy();
  onSubmit(document.getElementById("new-invite"), async (fields) => {
    const result = await api("POST", "/api/invites", { note: fields.note, admin: fields.admin === "1" });
    await usersPage({ result, who: fields.note });
  });
  onSubmit(document.getElementById("workspace-name"), async (fields) => {
    workspace = (await api("PUT", "/api/workspace", fields)).workspace;
    toast("Workspace renamed");
    route();
  });
  view.querySelectorAll("[data-revoke]").forEach((button) => action(button,
    () => api("DELETE", `/api/invites/${button.dataset.revoke}`), "Invite cancelled"));
  view.querySelectorAll("[data-user]").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm("Remove this person from the team?")) return;
    await api("DELETE", `/api/users/${button.dataset.user}`);
    toast("Removed");
    route();
  }));
}

// The owner's page: a workspace per agency, made with the invite for its first admin. Names and
// sizes only; what's inside each workspace stays with that agency.
async function workspacesPage(made) {
  const token = routeToken;
  const [{ workspaces }] = await Promise.all([api("GET", "/api/workspaces"), renderSide("workspaces")]);
  if (stale(token)) return;
  const rows = workspaces.map((w) => `
    <tr><td class="status"><span class="avatar">${esc(initials(w.name))}</span></td>
      <td><div class="cell-title">${esc(w.name)}${w.id === me.workspace_id ? '<span class="chip">yours</span>' : ""}</div>
        <div class="muted small">since ${esc(new Date(w.created).toLocaleDateString())}</div></td>
      <td class="narrow"><div class="small">${plural(w.members, "member")}</div></td>
      <td class="narrow"><div class="small">${plural(w.clients, "client")}</div></td>
      <td class="narrow">${w.pending ? `<span class="pill run">${plural(w.pending, "invite")} open</span>` : ""}</td></tr>`).join("");
  view.innerHTML = `
    <div class="page-head"><div><h1>Workspaces</h1><div class="sub">One per agency. Each sees only its own clients, tests and team.</div></div></div>
    <div class="table-wrap"><table><tbody>${rows}</tbody></table></div>
    ${made ? inviteResult(made.result, made.who) : ""}
    <form class="card stack" id="new-workspace">
      <div class="card-head">${icon("plus")}<h3>New workspace for an agency</h3></div>
      <p class="muted small">You get an invite link for their first admin, who then invites the rest of their team.</p>
      <div class="fields">
        <label>Agency name <input name="name" required placeholder="Acme QA Services"></label>
        <label>Invite for <span class="hint">their admin's name</span><input name="note" placeholder="Priya Sharma"></label>
      </div>
      <p class="error" role="alert"></p>
      <div class="actions"><button type="submit">${icon("plus")}Create workspace and invite</button></div>
    </form>`;
  wireCopy();
  onSubmit(document.getElementById("new-workspace"), async (fields) => {
    const result = await api("POST", "/api/workspaces", fields);
    toast(`${fields.name} created`);
    await workspacesPage({ result, who: fields.note || `${fields.name}'s admin` });
  });
}

async function accountPage() {
  await renderSide("account");
  view.innerHTML = `
    <div class="page-head"><div class="head-left"><span class="avatar lg">${esc(initials(me.name || me.email))}</span>
      <div><h1>${esc(me.name || "Account")}</h1><div class="sub">${esc(me.email)} · ${me.admin ? "Admin" : "Staff"}</div></div></div></div>
    <form class="card stack" id="password">
      <div class="card-head">${icon("key")}<h3>Change password</h3></div>
      <div class="fields">
        <label>Current password <input name="current" type="password" autocomplete="current-password" required></label>
        <label>New password <span class="hint">10+ characters</span><input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
      </div>
      <p class="error" role="alert"></p>
      <div class="actions"><button type="submit">Change password</button></div>
    </form>`;
  onSubmit(document.getElementById("password"), async (fields) => {
    await api("PUT", "/api/me/password", fields);
    me = workspace = null;
    toast("Password changed: log in again");
    location.hash = "#/login";
  });
}

// --- routing -------------------------------------------------------------------------------

async function route() {
  routeToken += 1;
  clearTimeout(poll);
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "login") return loginPage();
  if (parts[0] === "join" && parts[1]) return joinPage(parts[1]);
  view.classList.remove("bare");
  try {
    if (!me || !workspace) ({ user: me, workspace } = await api("GET", "/api/me"));
  } catch {
    return;  // api() sent us to the login page
  }
  try {
    if (parts[0] === "p" && parts[1]) return await projectPage(parts[1], parts[2] || "runs", parts[3]);
    if (parts[0] === "users" && me.admin) return await usersPage();
    if (parts[0] === "workspaces" && me.owner) return await workspacesPage();
    if (parts[0] === "account") return await accountPage();
    return await projectsPage();
  } catch (exc) {
    view.innerHTML = `<div class="card empty">${icon("alert")}<h3>Something went wrong</h3><p>${esc(exc.message)}</p>
      <div class="actions"><a class="btn secondary" href="#/">Back to clients</a></div></div>`;
  }
}

window.addEventListener("hashchange", route);
route();
