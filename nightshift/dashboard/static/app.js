// Nightshift dashboard. Plain JS, no build step. Talks to nightshift/dashboard/server.py.
"use strict";

const TOKEN = document.querySelector('meta[name="ns-token"]').content;
const page = document.getElementById("page");
const jobBox = document.getElementById("job");
let state = null;
let job = null; // the current or last job
let jobLines = [];
let jobOffset = 0;
let followJob = true; // keep the log scrolled to the bottom

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pill = (verdict) => `<span class="pill ${esc(verdict || "none")}">${esc((verdict || "not run").toUpperCase())}</span>`;
const fileUrl = (path) => `/files/${path.split("/").map(encodeURIComponent).join("/")}`;

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (method !== "GET") headers["X-Nightshift-Token"] = TOKEN;
  const response = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

let toastTimer;
function toast(message, bad = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = `show${bad ? " bad" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = ""), bad ? 6000 : 3000);
}

async function startJob(kind, params) {
  try {
    await api("POST", "/api/job", { kind, params });
    jobLines = [];
    jobOffset = 0;
    followJob = true;
    toast("Started");
    await pollJob();
    jobBox.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    toast(error.message, true);
  }
}

// --- the header chips and the job panel ----------------------------------------

function renderChips() {
  if (!state) return;
  const m = state.model;
  const j = state.job;
  const d = state.demo;
  document.getElementById("chips").innerHTML = [
    `<span class="chip ${m.ok ? "ok" : "bad"}" title="${esc(m.base_url)}"><span class="dot"></span>Model ${esc(m.name)}: ${m.ok ? "ready" : "not reachable"}</span>`,
    j && j.status === "running"
      ? `<span class="chip busy"><span class="dot"></span>${esc(j.title)}${j.current ? `: ${esc(j.current)}` : ""}</span>`
      : `<span class="chip"><span class="dot"></span>Idle</span>`,
    d.available ? `<span class="chip ${d.running ? "ok" : ""}"><span class="dot"></span>Demo shop ${d.running ? `on${d.bugs.length ? ` (${d.bugs.length} bug${d.bugs.length > 1 ? "s" : ""})` : ""}` : "off"}</span>` : "",
  ].join("");
}

// One poll at a time: two in flight would both append the same new log lines.
let polling = false;
async function pollJob() {
  if (polling) return;
  polling = true;
  try {
    let view = await api("GET", `/api/job?since=${jobOffset}`);
    if (!view.id) {
      job = null;
      jobBox.hidden = true;
      return;
    }
    if (!job || view.id !== job.id) {
      jobLines = [];
      jobOffset = 0;
      view = await api("GET", "/api/job?since=0");
    }
    jobLines.push(...view.lines);
    jobOffset = view.offset;
    const wasRunning = job && job.status === "running";
    job = view;
    renderJob(view.lines);
    if (wasRunning && job.status !== "running") {
      toast(job.status === "stopped" ? "Stopped" : `Finished${job.exit_code ? ` (exit ${job.exit_code})` : ""}`);
      refreshState();
      if (current === "tests" || current === "runs" || current === "overview" || current === "validate") route();
    }
  } finally {
    polling = false;
  }
}

function lineClass(line) {
  if (/\bPASS\b|-> changed/.test(line)) return "pass";
  if (/\bFAIL\b|BUG|ERROR|error:/.test(line)) return "fail";
  if (/FLAKY|warning|SUSPECTED|review:/.test(line)) return "warn";
  return "";
}

// The panel is built once per job, then updated in place: new log lines are appended
// and the screenshot only changes when there is a new one, so nothing flickers.
function renderJob(newLines = []) {
  if (!job) return;
  if (jobBox.dataset.jobId !== job.id) {
    jobBox.dataset.jobId = job.id;
    jobBox.dataset.shot = "";
    jobBox.innerHTML = `
      <div class="panel-head">
        <h2 style="margin:0">${esc(job.title)}</h2>
        <span class="pill" id="job-status"></span>
        <span class="muted" id="job-current"></span>
        <span class="spacer"></span>
        <span class="row" id="job-links"></span>
        <span id="job-actions"></span>
      </div>
      <div class="job-grid">
        <div class="log" id="log"></div>
        <div>
          <div class="shot empty" id="shot-empty">The agent's view appears here</div>
          <img class="shot" id="shot" alt="What the agent saw last" hidden>
          <p class="muted" id="shot-note" style="margin:6px 0 0" hidden>What the agent saw last. Red numbers are the element ids it acts on.</p>
        </div>
      </div>`;
    const logEl = document.getElementById("log");
    logEl.onscroll = () => (followJob = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 30);
    newLines = jobLines;
  }
  jobBox.hidden = false;
  const running = job.status === "running";
  const status = document.getElementById("job-status");
  status.className = `pill ${running ? "flaky" : job.status === "stopped" || job.exit_code ? "fail" : "pass"}`;
  status.textContent = running ? "RUNNING" : job.status === "stopped" ? "STOPPED" : `DONE${job.exit_code ? ` · EXIT ${job.exit_code}` : ""}`;
  document.getElementById("job-current").textContent = running && job.current ? `now: ${job.current}` : "";

  const links = Object.entries(job.links || {}).map(([name, path]) =>
    `<a class="button" href="${fileUrl(path)}" target="_blank" rel="noopener">${esc({ report: "Report", defects: "Defects", traceability: "Traceability matrix", findings: "Findings" }[name] || name)}</a>`).join("");
  const linkBox = document.getElementById("job-links");
  if (linkBox.dataset.html !== links) { linkBox.innerHTML = links; linkBox.dataset.html = links; }
  const actions = document.getElementById("job-actions");
  if (actions.dataset.running !== String(running)) {
    actions.dataset.running = String(running);
    actions.innerHTML = running ? `<button class="danger" id="stop">Stop</button>` : `<button id="hide-job">Hide</button>`;
    const stop = document.getElementById("stop");
    if (stop) stop.onclick = async () => { await api("POST", "/api/job/stop"); toast("Stopping..."); };
    const hide = document.getElementById("hide-job");
    if (hide) hide.onclick = () => (jobBox.hidden = true);
  }

  const logEl = document.getElementById("log");
  if (newLines.length) {
    logEl.insertAdjacentHTML("beforeend", newLines.map((line) => `<div class="${lineClass(line)}">${esc(line)}</div>`).join(""));
    while (logEl.childElementCount > 800) logEl.firstElementChild.remove();
  }
  if (!logEl.childElementCount) logEl.innerHTML = '<span class="muted">Starting...</span>';
  else logEl.querySelector("span.muted")?.remove();
  if (followJob) logEl.scrollTop = logEl.scrollHeight;

  if (job.screenshot && jobBox.dataset.shot !== job.screenshot) {
    jobBox.dataset.shot = job.screenshot;
    const img = document.getElementById("shot");
    img.onload = () => { img.hidden = false; document.getElementById("shot-empty").hidden = true; document.getElementById("shot-note").hidden = false; };
    img.src = fileUrl(job.screenshot);
  }
}

// --- pages ------------------------------------------------------------------------

let current = "overview";
const pages = { overview, tests, run, validate, explore, runs };

async function route() {
  const name = location.hash.replace(/^#\/?/, "").split("?")[0] || "overview";
  current = pages[name] ? name : "overview";
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === current));
  try {
    await pages[current]();
  } catch (error) {
    page.innerHTML = `<div class="card note bad">${esc(error.message)}</div>`;
  }
}

async function overview() {
  const runList = await api("GET", "/api/runs");
  const testRuns = runList.filter((r) => r.kind === "run");
  const last = testRuns[0];
  const recent = testRuns.slice(0, 12).reverse();
  const bars = recent.map((r) => {
    const passed = (r.counts.pass || 0) + (r.counts.flaky || 0);
    const rate = r.total ? passed / r.total : 0;
    return `<span class="${rate < 0.8 ? "low" : ""}" style="height:${Math.max(4, rate * 100)}%" title="${esc(r.time)}: ${passed}/${r.total} passed"></span>`;
  }).join("");
  const defects = last && last.defects ? last.defects : [];
  const unanalysed = last && last.defects === null;
  page.innerHTML = `
    <h1>Overview</h1>
    <div class="cards">
      <section class="card">
        <h2>Last test run</h2>
        ${last ? `<div class="muted">${esc(last.time)} · ${last.total} test case(s)</div>
          <div class="row" style="margin:10px 0">${["pass", "fail", "flaky", "error"].filter((v) => last.counts[v]).map((v) => `${pill(v)} <span class="big">${last.counts[v]}</span>`).join(" ")}</div>
          <a href="#/runs?id=${encodeURIComponent(last.id)}">Open the reports</a>` : `<p class="muted">No runs yet.</p>`}
      </section>
      <section class="card">
        <h2>Pass rate, last ${recent.length} runs</h2>
        ${recent.length ? `<div class="bars">${bars}</div><p class="muted" style="margin:6px 0 0">Red: under 80% passed.</p>` : `<p class="muted">No runs yet.</p>`}
      </section>
      <section class="card">
        <h2>Defects in the last run</h2>
        ${defects.length ? defects.map((d) => `<div><span class="sev-${esc(d.severity)}">${esc(d.id)} ${esc(d.severity)}</span> ${esc(d.title)} <span class="muted">(${esc(d.area)})</span></div>`).join("")
          : unanalysed ? `<p class="muted">This run has failures but no defect analysis yet.</p><button id="analyse-last">Analyse it</button>`
          : `<p class="muted">${last ? "None." : "No runs yet."}</p>`}
      </section>
    </div>
    <div class="cards">
      <section class="card">
        <h2>Start</h2>
        <div class="row">
          <button class="primary" id="run-all">Run every test case</button>
          <a class="button" href="#/validate">Validate requirements</a>
          <a class="button" href="#/explore">Explore an app</a>
        </div>
        <p class="muted">Model: ${esc(state.model.name)} at ${esc(state.model.base_url)}${state.model.ok ? "" : ". Not reachable: start Ollama or set MODEL_BASE_URL."}</p>
      </section>
      ${demoCard()}
    </div>
    <h3>Recent runs</h3>
    ${runsTable(runList.slice(0, 8))}`;
  document.getElementById("run-all").onclick = () => startJob("run", {});
  const analyse = document.getElementById("analyse-last");
  if (analyse) analyse.onclick = () => startJob("triage", { run: last.id });
  wireDemo();
  wireRunsTable();
}

function demoCard() {
  const d = state.demo;
  if (!d.available) return "";
  const bugs = (d.all_bugs || []).map((b) => `<label class="inline"><input type="checkbox" name="bug" value="${esc(b.name)}" ${d.bugs.includes(b.name) ? "checked" : ""}> <span><code>${esc(b.name)}</code> <span class="muted">${esc(b.description)}</span></span></label>`).join("");
  return `<section class="card">
    <h2>Demo shop</h2>
    <p class="muted" style="margin-top:0">A fake shop at <a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.url)}</a> for trying Nightshift. Plant bugs, run the tests in <code>specs/</code>, watch them get caught.</p>
    <div class="row">
      <button id="demo-start" class="${d.running ? "" : "primary"}">${d.running ? "Restart with these bugs" : "Start the shop"}</button>
      ${d.running ? `<button id="demo-stop">Stop</button>` : ""}
      <span class="muted">${d.running ? (d.bugs.length ? `Planted: ${d.bugs.map(esc).join(", ")}` : "No bugs planted") : ""}</span>
    </div>
    <details style="margin-top:10px"><summary>Choose bugs to plant (${(d.all_bugs || []).length})</summary><div class="bug-list" id="bug-list">${bugs}</div></details>
  </section>`;
}

function wireDemo() {
  const start = document.getElementById("demo-start");
  if (!start) return;
  start.onclick = async () => {
    const bugs = [...document.querySelectorAll('#bug-list input[name="bug"]:checked')].map((i) => i.value);
    try {
      state.demo = await api("POST", "/api/demo", { action: "start", bugs });
      toast(bugs.length ? `Shop started with ${bugs.length} bug(s)` : "Shop started");
      renderChips();
      route();
    } catch (error) { toast(error.message, true); }
  };
  const stop = document.getElementById("demo-stop");
  if (stop) stop.onclick = async () => { state.demo = await api("POST", "/api/demo", { action: "stop" }); renderChips(); route(); };
}

function runsTable(list) {
  if (!list.length) return `<div class="card empty">No runs yet. Start one from here or from the Run tests page.</div>`;
  const rows = list.map((r) => {
    const what = r.kind === "explore"
      ? `Exploration of ${esc(r.url)} <span class="sub">${r.pages} pages · ${r.bugs} bugs proven · ${r.suspected} suspected</span>`
      : `${r.total} test case(s) <span class="sub">${esc(r.specs.slice(0, 4).join(", "))}${r.specs.length > 4 ? "..." : ""}</span>`;
    const verdicts = r.kind === "explore" ? "" : ["pass", "fail", "flaky", "error"].filter((v) => r.counts[v]).map((v) => `${pill(v)} ${r.counts[v]}`).join(" ");
    const defects = r.defects ? `${r.defects.length} defect(s)` : r.defects === null ? `<span class="muted">not analysed</span>` : "";
    return `<tr class="clickable" data-run="${esc(r.id)}"><td>${esc(r.time)}</td><td>${what}</td><td>${verdicts}</td><td>${defects}</td></tr>`;
  }).join("");
  return `<div class="table-wrap"><table><tr><th>When</th><th>What</th><th>Results</th><th>Defects</th></tr>${rows}</table></div>`;
}

function wireRunsTable() {
  document.querySelectorAll("tr[data-run]").forEach((tr) => (tr.onclick = () => (location.hash = `#/runs?id=${encodeURIComponent(tr.dataset.run)}`)));
}

// Test cases: list, edit, create, run.
let testsFolder = "";
async function tests() {
  const folders = state.folders;
  if (!folders.includes(testsFolder)) testsFolder = folders[0] || "";
  const specs = testsFolder ? await api("GET", `/api/specs?folder=${encodeURIComponent(testsFolder)}`) : [];
  const rows = specs.map((s) => `
    <tr class="clickable" data-path="${esc(s.path)}">
      <td><input type="checkbox" class="pick" value="${esc(s.path)}" aria-label="Select ${esc(s.name)}"></td>
      <td><strong>${esc(s.title || s.name)}</strong><span class="sub">${esc(s.name)}${s.url ? ` · ${esc(s.url)}` : ""}</span>
        ${s.problem ? `<span class="sub note bad">${esc(s.problem)}</span>` : ""}
        ${s.review && s.review.length ? `<span class="sub note">Review: ${esc(s.review.join(" "))}</span>` : ""}</td>
      <td>${esc((s.requirements || []).join(", "))}</td>
      <td>${esc(s.technique || "")}</td>
      <td>${esc(s.priority || "")}</td>
      <td title="${esc(s.last ? s.last.reason : "")}">${pill(s.last && s.last.verdict)}</td>
    </tr>`).join("");
  page.innerHTML = `
    <h1>Test cases</h1>
    <div class="row" style="margin-bottom:12px">
      <label class="inline">Folder <select id="folder">${folders.map((f) => `<option ${f === testsFolder ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>
      <span class="spacer"></span>
      <button id="new">New test case</button>
      <button id="doc" title="One row per case: title, requirements, technique, priority, steps, expected, last result">Test-case document</button>
      <button id="run-picked" disabled>Run selected</button>
      <button class="primary" id="run-folder">Run this folder</button>
    </div>
    <div class="split">
      <div>${specs.length ? `<div class="table-wrap"><table><tr><th></th><th>Test case</th><th>Requirements</th><th>Technique</th><th>Priority</th><th>Last result</th></tr>${rows}</table></div>` : `<div class="card empty">No test cases in ${esc(testsFolder || "the spec folders")} yet. Create one, or design them from requirements on the Validate page.</div>`}</div>
      <div class="card" id="editor"><p class="muted" style="margin:0">Pick a test case to read or edit it, or create a new one.</p></div>
    </div>`;
  document.getElementById("folder").onchange = (e) => { testsFolder = e.target.value; tests(); };
  document.getElementById("new").onclick = () => newCaseForm();
  document.getElementById("doc").onclick = () => startJob("cases", { paths: [testsFolder] });
  document.getElementById("run-folder").onclick = () => startJob("run", { paths: [testsFolder] });
  const picked = () => [...document.querySelectorAll(".pick:checked")].map((i) => i.value);
  const runPicked = document.getElementById("run-picked");
  document.querySelectorAll(".pick").forEach((box) => {
    box.onclick = (e) => e.stopPropagation();
    box.onchange = () => { const n = picked().length; runPicked.disabled = !n; runPicked.textContent = n ? `Run selected (${n})` : "Run selected"; };
  });
  runPicked.onclick = () => startJob("run", { paths: picked() });
  document.querySelectorAll("tr[data-path]").forEach((tr) => (tr.onclick = () => openCase(tr.dataset.path)));
}

async function openCase(path) {
  const spec = await api("GET", `/api/spec?path=${encodeURIComponent(path)}`);
  const editor = document.getElementById("editor");
  editor.innerHTML = `
    <div class="panel-head"><h2 style="margin:0">${esc(path.split("/").pop())}</h2></div>
    <div class="form">
      <textarea id="yaml" spellcheck="false" style="min-height:380px">${esc(spec.text)}</textarea>
      <div class="row">
        <button class="primary" id="save">Save</button>
        <button id="run-one">Run this test</button>
        <span class="spacer"></span>
        <button class="danger" id="delete">Delete</button>
      </div>
      <p class="hint">Steps are what a tester would do; expected results must be readable on the final page. Test data is typed as {{name}}, so values never reach the model.</p>
    </div>`;
  document.getElementById("save").onclick = async () => {
    try { await api("PUT", "/api/spec", { path, text: document.getElementById("yaml").value }); toast("Saved"); tests(); }
    catch (error) { toast(error.message, true); }
  };
  document.getElementById("run-one").onclick = () => startJob("run", { paths: [path] });
  document.getElementById("delete").onclick = async () => {
    if (!confirm(`Delete ${path}?`)) return;
    await api("DELETE", `/api/spec?path=${encodeURIComponent(path)}`);
    toast("Deleted");
    tests();
  };
}

function newCaseForm() {
  const editor = document.getElementById("editor");
  editor.innerHTML = `
    <h2>New test case</h2>
    <form class="form" id="new-form">
      <label>Title <input name="title" placeholder="Checkout with cash on delivery" required></label>
      <label>Start URL <input name="url" placeholder="http://localhost:5180/" required></label>
      <label>Steps <span class="hint">one per line, as you would brief a new tester</span><textarea name="steps" placeholder="log in with the test account&#10;add Filter Coffee to the cart" required></textarea></label>
      <label>Expected results <span class="hint">one per line, checkable on the final page</span><textarea name="expect" style="min-height:90px" placeholder="a page says the order was placed&#10;the amount to pay is ₹240" required></textarea></label>
      <label>Test data <span class="hint">name=value per line; use \${ENV_VAR} for secrets</span><textarea name="data" style="min-height:70px" placeholder="email=shopper@kulhad.test&#10;password=\${SHOP_PASSWORD}"></textarea></label>
      <div class="grid2">
        <label>Requirements <input name="requirements" placeholder="R1, R4"></label>
        <label>Technique <select name="technique"><option></option><option>positive</option><option>negative</option><option>boundary</option></select></label>
        <label>Priority <select name="priority"><option></option><option>high</option><option>medium</option><option>low</option></select></label>
      </div>
      <div class="row"><button class="primary">Create</button></div>
    </form>`;
  document.getElementById("new-form").onsubmit = async (event) => {
    event.preventDefault();
    const form = Object.fromEntries(new FormData(event.target));
    try {
      const created = await api("POST", "/api/spec", { ...form, folder: testsFolder });
      toast(`Created ${created.path}`);
      await tests();
      openCase(created.path);
    } catch (error) { toast(error.message, true); }
  };
}

// Run tests.
async function run() {
  page.innerHTML = `
    <h1>Run tests</h1>
    <form class="card form" id="run-form">
      <div class="grid2">
        <label>Which test cases <select name="folder"><option value="">every spec folder</option>${state.folders.map((f) => `<option>${esc(f)}</option>`).join("")}</select></label>
        <label>Run against <span class="hint">optional: another deployment, same paths</span><input name="base_url" placeholder="https://staging.example.com"></label>
        <label>Retries <span class="hint">a failure is re-run this many times before it is reported</span><select name="retries"><option>0</option><option selected>1</option><option>2</option></select></label>
      </div>
      <div class="row">
        <label class="inline"><input type="checkbox" name="headed"> Show the browser</label>
        <label class="inline"><input type="checkbox" name="fresh"> Fresh agent (ignore saved paths)</label>
        <label class="inline"><input type="checkbox" name="no_vision"> Text only (faster on small models)</label>
      </div>
      <div class="row"><button class="primary">Run</button><span class="muted">Results, bug reports and defect analysis appear below and under Runs &amp; reports.</span></div>
    </form>`;
  document.getElementById("run-form").onsubmit = (event) => {
    event.preventDefault();
    const f = new FormData(event.target);
    startJob("run", { paths: f.get("folder") ? [f.get("folder")] : [], base_url: f.get("base_url"), retries: Number(f.get("retries")),
      headed: f.has("headed"), fresh: f.has("fresh"), no_vision: f.has("no_vision") });
  };
}

// Validate requirements: requirements -> designed test cases (reviewed) -> traceability matrix.
let validateForm = { requirements: "requirements.md", url: "", username: "", password: "", cases: 2, explore_steps: 15, specs_out: "specs/requirements" };
async function validate() {
  let text = "";
  try { text = (await api("GET", `/api/text?path=${encodeURIComponent(validateForm.requirements)}`)).text; } catch (_) { /* new file */ }
  let designed = [];
  try { designed = await api("GET", `/api/specs?folder=${encodeURIComponent(validateForm.specs_out)}`); } catch (_) { /* none yet */ }
  const v = validateForm;
  page.innerHTML = `
    <h1>Validate requirements</h1>
    <p class="muted" style="margin-top:-8px">Write the requirements, let Nightshift design test cases for each one, review them, then validate the app. You get a traceability matrix: every requirement, the tests that cover it, pass or fail, and the defects behind each failure.</p>
    <div class="split">
      <form class="card form" id="val-form">
        <h2>1. Requirements</h2>
        <label>File <input name="requirements" value="${esc(v.requirements)}"></label>
        <label>One requirement per line or bullet <span class="hint">give examples: "for example, searching for Baguette lists Baguette"</span>
          <textarea name="text" style="min-height:220px">${esc(text)}</textarea></label>
        <h2>2. The app</h2>
        <div class="grid2">
          <label>Start URL <input name="url" value="${esc(v.url)}" placeholder="http://localhost:5180/" required></label>
          <label>Test cases per requirement <select name="cases">${[1, 2, 3].map((n) => `<option ${n == v.cases ? "selected" : ""}>${n}</option>`).join("")}</select></label>
          <label>Test account username <input name="username" value="${esc(v.username)}" autocomplete="off"></label>
          <label>Test account password <input name="password" type="password" value="${esc(v.password)}" autocomplete="off"></label>
          <label>Explore first <span class="hint">steps, so tests use the app's real labels</span><input name="explore_steps" type="number" min="0" max="60" value="${esc(v.explore_steps)}"></label>
          <label>Save designed tests in <input name="specs_out" value="${esc(v.specs_out)}"></label>
        </div>
        <div class="row">
          <button type="submit" name="step" value="design">3. Design test cases</button>
          <button type="submit" name="step" value="run" class="primary">4. Validate the app</button>
        </div>
        <p class="hint">Designing reuses test cases already in the folder; to design one requirement again, delete its cases on the Test cases page.</p>
      </form>
      <div class="card">
        <h2>Designed test cases (${designed.length})</h2>
        ${designed.length ? designed.map((s) => `<div style="padding:8px 0;border-bottom:1px solid var(--line)">
            <strong>${esc((s.requirements || []).join(", "))}</strong> ${esc(s.title || s.name)}
            <span class="sub muted">${esc(s.technique || "")} · ${esc(s.priority || "")} ${s.last ? pill(s.last.verdict) : ""}</span>
            ${s.review && s.review.length ? `<div class="note" style="margin-top:4px">Review: ${esc(s.review.join(" "))}</div>` : ""}
          </div>`).join("") + `<p><a href="#/tests" id="to-tests">Review and edit them on the Test cases page</a></p>`
          : `<p class="muted">None yet. Step 3 designs them; read them before step 4, as a QA lead would.</p>`}
      </div>
    </div>`;
  const toTests = document.getElementById("to-tests");
  if (toTests) toTests.onclick = () => { testsFolder = v.specs_out; };
  document.getElementById("val-form").onsubmit = async (event) => {
    event.preventDefault();
    const f = Object.fromEntries(new FormData(event.target));
    validateForm = { ...validateForm, ...f, cases: Number(f.cases), explore_steps: Number(f.explore_steps) };
    try { await api("PUT", "/api/text", { path: f.requirements, text: f.text }); } catch (error) { return toast(error.message, true); }
    const data = [f.username && `username=${f.username}`, f.password && `password=${f.password}`].filter(Boolean).join("\n");
    startJob("validate", { requirements: f.requirements, url: f.url, cases: Number(f.cases), explore_steps: Number(f.explore_steps),
      specs_out: f.specs_out, data, design_only: event.submitter && event.submitter.value === "design" });
  };
}

// Explore.
async function explore() {
  page.innerHTML = `
    <h1>Explore an app</h1>
    <p class="muted" style="margin-top:-8px">No test cases needed. The agent roams the app, tries the inputs users get wrong, and reports what breaks. Crashes and server errors are proven by the browser; what the model only suspects is kept separate.</p>
    <form class="card form" id="exp-form">
      <div class="grid2">
        <label>Start URL <input name="url" placeholder="http://localhost:5180/" required></label>
        <label>Actions <input name="steps" type="number" min="5" max="200" value="30"></label>
        <label>Focus <span class="hint">optional</span><input name="focus" placeholder="the checkout"></label>
      </div>
      <label>Test data it may use <span class="hint">name=value per line</span><textarea name="data" style="min-height:70px"></textarea></label>
      <div class="row"><label class="inline"><input type="checkbox" name="headed"> Show the browser</label><span class="spacer"></span><button class="primary">Explore</button></div>
    </form>`;
  document.getElementById("exp-form").onsubmit = (event) => {
    event.preventDefault();
    const f = new FormData(event.target);
    startJob("explore", { url: f.get("url"), steps: Number(f.get("steps")), focus: f.get("focus"), data: f.get("data"), headed: f.has("headed") });
  };
}

// Runs & reports.
async function runs() {
  const list = await api("GET", "/api/runs");
  const id = new URLSearchParams(location.hash.split("?")[1] || "").get("id") || (list[0] && list[0].id);
  const chosen = list.find((r) => r.id === id);
  let viewer = `<div class="card empty">No runs yet.</div>`;
  if (chosen) {
    const names = { "index.html": "Results", "defects.html": "Defects", "traceability.html": "Traceability matrix", "report.html": "Exploration report", "test-cases.csv": "Test cases (CSV)" };
    const tabs = chosen.files.map((file, i) => file.endsWith(".csv")
      ? `<a class="button" href="${fileUrl(`${chosen.id}/${file}`)}" download>${names[file]}</a>`
      : `<button data-file="${esc(file)}" class="${i === 0 ? "active" : ""}">${names[file] || file}</button>`).join("");
    viewer = `
      <div class="panel-head"><h2 style="margin:0">${esc(chosen.time)}</h2><span class="spacer"></span>
        ${chosen.kind === "run" ? `<button id="triage">Analyse defects again</button>` : ""}
        ${chosen.kind === "run" && chosen.defects && chosen.defects.length
          ? state.jira.configured
            ? `<button id="jira" title="${esc(state.jira.url)}, project ${esc(state.jira.project)}">File ${chosen.defects.length} defect(s) in Jira</button>`
            : `<button disabled title="Set JIRA_URL, JIRA_PROJECT and a token, then restart the dashboard">File in Jira (not set up)</button>`
          : ""}
        <a class="button" href="${fileUrl(`${chosen.id}/${chosen.files[0]}`)}" target="_blank" rel="noopener">Open in a new tab</a></div>
      <div class="tabs">${tabs}</div>
      <iframe class="report" id="frame" src="${fileUrl(`${chosen.id}/${chosen.files[0]}`)}" title="Report"></iframe>`;
  }
  page.innerHTML = `<h1>Runs &amp; reports</h1>${runsTable(list.slice(0, 15))}<div style="margin-top:16px">${viewer}</div>`;
  wireRunsTable();
  document.querySelectorAll("button[data-file]").forEach((b) => (b.onclick = () => {
    document.querySelectorAll("button[data-file]").forEach((x) => x.classList.toggle("active", x === b));
    document.getElementById("frame").src = fileUrl(`${chosen.id}/${b.dataset.file}`);
  }));
  const triage = document.getElementById("triage");
  if (triage) triage.onclick = () => startJob("triage", { run: chosen.id });
  const jira = document.getElementById("jira");
  if (jira) jira.onclick = () => startJob("triage", { run: chosen.id, jira: true });
}

// --- start ------------------------------------------------------------------------

async function refreshState() {
  try {
    state = await api("GET", "/api/state");
    renderChips();
  } catch (error) {
    document.getElementById("chips").innerHTML = `<span class="chip bad"><span class="dot"></span>Dashboard server not reachable</span>`;
  }
}

window.addEventListener("hashchange", route);
(async function start() {
  await refreshState();
  await route();
  await pollJob();
  setInterval(refreshState, 5000);
  setInterval(() => { if (!job || job.status === "running") pollJob().catch(() => {}); }, 1000);
})();
