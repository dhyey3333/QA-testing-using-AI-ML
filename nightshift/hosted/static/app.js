// Nightshift hosted: login, projects (one per client), their tests, runs and client reports.
// Plain JS, no build step. Every request sends X-Nightshift: 1, which the server requires for
// changes (a form on another site can't send it).

const view = document.getElementById("view");
let me = null;
let poll = null;
// Each page load gets a number. A load that finishes after the user moved on (an auto-refresh still in
// flight, a slow request) is stale and must not paint over the page they are on now.
let routeToken = 0;
const stale = (token) => token !== routeToken;

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const when = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleString() : "");
const took = (run) => (run.started && run.finished ? `${Math.round(run.finished - run.started)} s` : "");

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
    } finally {
      if (button) button.disabled = false;
    }
  });
}

// --- pages ---------------------------------------------------------------------------

function loginPage() {
  document.getElementById("bar").hidden = true;
  view.innerHTML = `
    <div class="card narrow">
      <h1>Nightshift</h1>
      <p class="muted">AI QA runs for your clients.</p>
      <form class="stack" id="login">
        <label>Email <input name="email" type="email" autocomplete="username" required></label>
        <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
        <p class="error" role="alert"></p>
        <button type="submit">Log in</button>
      </form>
    </div>`;
  onSubmit(document.getElementById("login"), async (form) => {
    me = (await api("POST", "/api/login", form)).user;
    location.hash = "#/";
  });
}

async function projectsPage() {
  const token = routeToken;
  const { projects } = await api("GET", "/api/projects");
  if (stale(token)) return;
  const cards = projects.map((p) => `
    <div class="card">
      <div class="row spread"><a href="#/p/${esc(p.slug)}"><strong>${esc(p.client)}</strong></a>
        ${p.last_run ? `<span class="badge ${esc(p.last_run.status)}">${esc(p.last_run.status)}</span>` : ""}</div>
      <div class="muted mono">${esc(p.base_url) || "each test's own URL"}</div>
      <div class="muted">${p.nightly ? `Nightly at ${esc(p.nightly)}` : "No schedule"}</div>
      ${p.last_run ? `<div class="counts">${counts(p.last_run)}</div>` : '<div class="muted">No runs yet</div>'}
    </div>`).join("");
  view.innerHTML = `
    <div class="row spread"><h1>Projects</h1></div>
    <p class="muted">One project per client: its tests, a nightly schedule, and a client report for every run.</p>
    <div class="grid">${cards || '<p class="muted">No projects yet.</p>'}</div>
    ${me.admin ? `
    <h2>New project</h2>
    <form class="card stack" id="new-project">
      <label>Client <input name="client" required placeholder="Acme Retail"></label>
      <label>Base URL (optional) <input name="base_url" placeholder="https://staging.acme.example"></label>
      <label>Report prepared by (your agency) <input name="brand" placeholder="Your QA Co"></label>
      <label>Nightly run at (24-hour, server time; empty for none) <input name="nightly" placeholder="02:30"></label>
      <p class="error" role="alert"></p>
      <div><button type="submit">Create project</button></div>
    </form>` : ""}`;
  const form = document.getElementById("new-project");
  if (form) onSubmit(form, async (fields) => {
    const { project } = await api("POST", "/api/projects", fields);
    location.hash = `#/p/${project.slug}/tests`;
  });
}

function counts(run) {
  if (run.status !== "done" || ["explore", "generate"].includes(run.trigger)) return `<span class="muted">${esc(run.message || "")}</span>`;
  return `<span class="n-pass">${run.passed} passed</span>` + (run.failed ? `<span class="n-fail">${run.failed} failed</span>` : "")
    + (run.flaky ? `<span class="n-flaky">${run.flaky} flaky</span>` : "") + (run.errors ? `<span class="n-error">${run.errors} errors</span>` : "");
}

async function projectPage(slug, tab, extra) {
  const token = routeToken;
  const data = await api("GET", `/api/projects/${slug}`);
  if (stale(token)) return;
  const p = data.project;
  const active = data.runs.find((r) => ["queued", "running"].includes(r.status));
  const tabs = [["runs", "Runs"], ["tests", `Tests (${data.specs.length})`], ...(me.admin ? [["settings", "Settings"]] : [])]
    .map(([key, label]) => `<a href="#/p/${esc(slug)}/${key}" class="${tab === key || (tab === "run" && key === "runs") ? "on" : ""}">${label}</a>`).join("");
  view.innerHTML = `
    <div class="row spread">
      <div><h1>${esc(p.client)}</h1>
        <div class="muted">${p.nightly ? `Runs nightly at ${esc(p.nightly)}` : "No nightly schedule"} · ${data.parallel} tests at a time
        · on ${esc((p.targets || "chrome").split(",").map((t) => (TARGET_LABELS.find(([k]) => k === t) || [t, t])[1]).join(", "))}
        ${p.base_url ? ` · <span class="mono">${esc(p.base_url)}</span>` : ""}</div></div>
      <div class="row">
        ${active ? `<span class="badge ${esc(active.status)}">${esc(active.status)}</span>
          <button type="button" class="danger" id="stop" data-run="${active.id}">Stop</button>`
          : `<button type="button" id="run-now" ${data.specs.length ? "" : "disabled"}>Run now</button>`}
      </div>
    </div>
    <p class="error" id="project-error" role="alert"></p>
    <nav class="tabs">${tabs}</nav>
    <section id="tab"></section>`;
  const error = document.getElementById("project-error");
  document.getElementById("run-now")?.addEventListener("click", async () => {
    try { await api("POST", `/api/projects/${slug}/runs`); route(); } catch (exc) { error.textContent = exc.message; }
  });
  document.getElementById("stop")?.addEventListener("click", async (event) => {
    try { await api("POST", `/api/projects/${slug}/runs/${event.target.dataset.run}/stop`); route(); } catch (exc) { error.textContent = exc.message; }
  });
  const section = document.getElementById("tab");
  if (tab === "run") return runDetail(section, slug, extra);
  if (tab === "tests") return testsTab(section, slug, data, extra);
  if (tab === "settings" && me.admin) return settingsTab(section, slug, data);
  runsTab(section, slug, data, Boolean(active));
  if (active) poll = setTimeout(route, 4000);  // follow the run until it finishes
}

const VISUAL = { baseline: "saved as the approved look", same: "looks as approved", changed: "looks different (judged harmless)",
  "visual bug": "visual bug", skipped: "not checked (no approved look yet)" };

// One run: every test with its result, why it failed, and its visual check; and what to do next.
async function runDetail(section, slug, id) {
  const token = routeToken;
  const d = await api("GET", `/api/projects/${slug}/runs/${id}`);
  if (stale(token)) return;
  const r = d.run;
  const changes = d.tests.filter((t) => ["changed", "visual bug"].includes(t.visual.status)).length;
  const rows = d.tests.map((t) => `
    <tr>
      <td class="mono">${esc(t.spec)}${t.mode === "replay" ? '<div class="muted">replayed, no AI</div>' : ""}</td>
      <td><span class="badge ${t.verdict === "pass" ? "done" : "failed"}">${esc(t.verdict)}</span>
        ${t.category ? `<div class="muted">${esc(t.category)}</div>` : ""}</td>
      <td>${t.cause ? esc(t.cause) : t.verdict === "pass" ? '<span class="muted">Every expected result was proven on the page.</span>' : esc(t.reason)}
        ${t.visual.status ? `<div class="muted">Visual check: ${esc(VISUAL[t.visual.status] || t.visual.status)}${t.visual.what ? `: ${esc(t.visual.what)}` : ""}
          ${t.visual.picture ? ` · <a href="${esc(t.visual.picture)}" target="_blank" rel="noopener">compare</a>` : ""}</div>` : ""}</td>
      <td>${[["report", "Report"], ["bug", "Bug report"]].filter(([k]) => t.links[k])
        .map(([k, label]) => `<a href="${esc(t.links[k])}" target="_blank" rel="noopener">${label}</a>`).join(" · ")}</td>
    </tr>`).join("");
  const reports = [["client_report", "Client report"], ["report", "Full report"], ["findings", "Findings"], ["log", "Log"]]
    .filter(([k]) => r.links[k])
    .map(([k, label]) => `<a href="${esc(r.links[k])}" target="_blank" rel="noopener">${label}</a>`).join(" · ");
  section.innerHTML = `
    <p><a href="#/p/${esc(slug)}/runs">← All runs</a></p>
    <h2>Run #${r.id} · ${esc(r.trigger)} · ${esc(when(r.queued))}</h2>
    <div class="row spread"><div class="counts">${counts(r)}</div><div>${reports}</div></div>
    <div class="row">
      ${changes ? `<button type="button" class="secondary" id="accept-visual">Accept the new look (${changes})</button>` : ""}
      <button type="button" class="secondary" id="jira" ${d.jira ? "" : "disabled"}>File bugs in Jira</button>
      <button type="button" class="secondary" id="slack" ${d.slack ? "" : "disabled"}>Post to Slack</button>
    </div>
    ${d.jira && d.slack ? "" : `<p class="muted">To file in Jira or post to Slack, add these under Settings → Secrets:
      ${d.jira ? "" : "JIRA_URL, JIRA_PROJECT, JIRA_EMAIL and JIRA_API_TOKEN"}${!d.jira && !d.slack ? "; " : ""}${d.slack ? "" : "SLACK_WEBHOOK_URL"}.</p>`}
    ${d.issues.length ? `<p>Filed: ${d.issues.map((i) => `<a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.key)}</a>`).join(", ")}</p>` : ""}
    <p class="error" id="run-error" role="alert"></p>
    <table><thead><tr><th>Test</th><th>Result</th><th>What happened</th><th>Reports</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="4" class="muted">${
        r.trigger === "explore" ? "An exploration has findings, not test results: open Findings or the Full report above."
        : r.trigger === "generate" ? "Generating writes draft tests: review them on the Tests tab."
        : ["queued", "running"].includes(r.status) ? "Still running…"
        : esc(r.message || "No test results: the run did not finish.")}</td></tr>`}</tbody></table>`;
  const error = document.getElementById("run-error");
  const act = (buttonId, path, done) => document.getElementById(buttonId)?.addEventListener("click", async (event) => {
    event.target.disabled = true;
    error.textContent = "";
    try { done(await api("POST", `/api/projects/${slug}/runs/${id}/${path}`)); }
    catch (exc) { error.textContent = exc.message; event.target.disabled = false; }
  });
  act("accept-visual", "accept-visual", (res) => { error.textContent = ""; alert(`${res.accepted} new look(s) approved. Later runs compare against them.`); route(); });
  act("jira", "jira", (res) => { alert(res.filed.length ? `Filed: ${res.filed.map((f) => f.key).join(", ")}` : "No bugs to file in this run."); route(); });
  act("slack", "slack", () => alert("Posted to Slack."));
  if (["queued", "running"].includes(r.status)) poll = setTimeout(route, 4000);
}

function runsTab(section, slug, data, busy) {
  const rows = data.runs.map((run) => `
    <tr>
      <td><a href="#/p/${esc(slug)}/run/${run.id}">#${run.id}</a></td>
      <td>${esc(when(run.queued))}<div class="muted">${esc(run.trigger)} · ${esc(took(run))}</div>
        ${run.target ? `<div class="muted mono">${esc(run.target)}</div>` : ""}</td>
      <td><span class="badge ${esc(run.status)}">${esc(run.status)}</span></td>
      <td class="counts">${counts(run)}</td>
      <td>${[["client_report", "Client report"], ["report", "Full report"], ["findings", "Findings"], ["log", "Log"]]
        .filter(([key]) => run.links[key]).map(([key, label]) => `<a href="${esc(run.links[key])}" target="_blank" rel="noopener">${label}</a>`).join(" · ")}</td>
    </tr>`).join("");
  section.innerHTML = `
    <form class="card stack" id="explore">
      <strong>Explore a website for bugs</strong>
      <span class="muted">No tests needed: paste an address and the AI uses the site like a curious user, then lists what it
        found broken. Only sites you own or are allowed to test.</span>
      <div class="row">
        <label>Website <input name="url" type="url" required placeholder="https://academybugs.com/" value="${esc(data.project.base_url)}"></label>
        <label>Actions <input name="steps" type="number" min="5" max="60" value="25"></label>
        <label>Focus on (optional) <input name="focus" placeholder="the checkout"></label>
      </div>
      <p class="error" role="alert"></p>
      <div><button type="submit" ${busy ? "disabled" : ""}>Explore</button></div>
    </form>
    ${rows
      ? `<table><thead><tr><th>Run</th><th>When</th><th>Status</th><th>Results</th><th>Reports</th></tr></thead><tbody>${rows}</tbody></table>`
      : '<p class="muted">No runs yet. Add tests and press Run now, or explore a website above.</p>'}
    ${data.keep_runs ? `<p class="muted">The newest ${data.keep_runs} runs keep their screenshots and reports; older runs keep only their summary.</p>` : ""}`;
  onSubmit(document.getElementById("explore"), async (fields) => {
    await api("POST", `/api/projects/${slug}/explore`, fields);
    route();
  });
}

// Multi-line placeholders: a newline in an attribute is &#10;.
const lines = (text) => esc(text).replace(/\n/g, "&#10;");

async function testsTab(section, slug, data, editing) {
  const draft = (editing || "").startsWith("draft:");
  const name = draft ? editing.slice(6) : editing || "";
  const missing = data.missing.map((m) => `<li><span class="mono">${esc(m.test)}</span> needs <span class="mono">${esc(m.secret)}</span></li>`).join("");
  const drafts = data.drafts.map((d) => `
    <tr><td class="mono">${esc(d.name)}</td>
      <td>${d.review.map((r) => `<div class="muted">Check: ${esc(r)}</div>`).join("")}
        ${d.needs.length ? `<div class="n-flaky">Needs secrets: ${d.needs.map(esc).join(", ")}</div>` : ""}</td>
      <td class="row"><a href="#/p/${esc(slug)}/tests/draft:${esc(d.name)}">Review</a>
        <button type="button" class="link" data-accept="${esc(d.name)}">Accept</button>
        <button type="button" class="link" data-drop="${esc(d.name)}">Delete</button></td></tr>`).join("");
  const tests = data.specs.map((n) => `
    <tr><td class="mono">${esc(n)}</td>
      <td class="row"><a href="#/p/${esc(slug)}/tests/${esc(n)}">Edit</a>
      <button type="button" class="link" data-delete="${esc(n)}">Delete</button></td></tr>`).join("");
  section.innerHTML = `
    ${missing ? `<div class="card warn"><strong>Set these secrets before running</strong> (Settings → Secrets):<ul>${missing}</ul></div>` : ""}
    <form class="card stack" id="generate">
      <strong>Generate tests with AI</strong>
      <span class="muted">Give it the website. It explores the site like a new user, then writes tests for you to review
        below. Takes a few minutes; follow it on the Runs tab. Only sites you own or are allowed to test.</span>
      <div class="row">
        <label class="grow">Website <input name="url" type="url" required value="${esc(data.project.base_url)}" placeholder="https://academybugs.com/"></label>
        <label>Tests <input name="count" type="number" min="1" max="10" value="5"></label>
        <label>Explore first (actions) <input name="steps" type="number" min="0" max="40" value="20"></label>
      </div>
      <label>What is the site for? (optional, but it helps)
        <textarea name="about" class="short" placeholder="${lines("An online shop: people search for products, add them to a cart and check out.")}"></textarea></label>
      <p class="error" role="alert"></p>
      <div><button type="submit">Generate tests</button></div>
    </form>
    ${drafts ? `
    <h2>Drafts to review (${data.drafts.length})</h2>
    <p class="muted">Written by the AI from what it saw. Read each one and fix what's wrong before you accept it: a wrong test reports wrong bugs.</p>
    <table><tbody>${drafts}</tbody></table>
    <p class="error" id="draft-error" role="alert"></p>
    <div class="row"><button type="button" class="secondary" id="accept-all">Accept all</button></div>` : ""}
    <h2>Tests (${data.specs.length})</h2>
    <table><tbody>${tests || '<tr><td class="muted">No tests yet: generate some above, or write one below.</td></tr>'}</tbody></table>
    <h2>${draft ? `Review draft: ${esc(name)}` : name ? `Edit test: ${esc(name)}` : "New test"}</h2>
    <form class="card stack" id="spec">
      <label>Test name <input name="name" required pattern="[a-z0-9][a-z0-9-]*" ${name ? "readonly" : ""}
        placeholder="checkout" title="lowercase letters, digits and dashes"></label>
      <label>Website <input name="url" type="url" required placeholder="https://staging.example.com/"></label>
      <label>Steps: what a person does, one per line
        <textarea name="steps" class="short" placeholder="${lines("log in with the test account\nadd the Blue Top to the cart\nopen the cart")}"></textarea></label>
      <label>What should happen: one expected result per line
        <textarea name="expect" class="short" placeholder="${lines("the cart lists the Blue Top\nthe total is correct")}"></textarea>
        <span class="muted">Leave it empty when the test is one sentence (like "subscribe to the newsletter"): the AI then has to prove it was done.</span></label>
      <label>Test data: one per line, as name = value
        <textarea name="data" class="short mono" placeholder="${lines("email = qa@example.com\npassword = ${SHOP_PASSWORD}")}"></textarea>
        <span class="muted">The AI only ever sees the name, never the value. For passwords, add a secret under Settings and write \${NAME} here.</span></label>
      <details><summary>More options</summary>
        <div class="stack">
          <label>Step limit <input name="max_steps" type="number" min="1" max="60" value="30"></label>
          <label class="inline"><input type="checkbox" name="js_errors_warn" value="1"> Background JavaScript errors are only warnings</label>
        </div>
      </details>
      <p class="error" role="alert"></p>
      <div class="row">
        <button type="submit">${draft ? "Save draft" : "Save test"}</button>
        ${draft ? '<button type="button" id="save-accept">Save and accept</button>' : ""}
        ${name ? `<a href="#/p/${esc(slug)}/tests">New test instead</a>` : ""}
        <button type="button" class="link" id="as-yaml">Edit as YAML instead</button>
      </div>
    </form>
    <form class="card stack" id="spec-yaml" hidden>
      <label>Test name <input name="name" required pattern="[a-z0-9][a-z0-9-]*" ${name ? "readonly" : ""}></label>
      <label>YAML (advanced) <textarea name="text" spellcheck="false"></textarea></label>
      <p class="error" role="alert"></p>
      <div class="row"><button type="submit">Save</button><button type="button" class="link" id="as-form">Back to the form</button></div>
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
  const fields = () => ({
    url: form.elements.url.value, steps: form.elements.steps.value, expect: form.elements.expect.value,
    data: form.elements.data.value, max_steps: form.elements.max_steps.value, js_errors_warn: form.elements.js_errors_warn.checked,
  });
  const done = () => { location.hash = `#/p/${slug}/tests`; route(); };
  onSubmit(form, async () => { await api("PUT", `${base}/${form.elements.name.value}`, { form: fields() }); done(); });
  onSubmit(yamlForm, async (values) => { await api("PUT", `${base}/${values.name}`, { text: values.text }); done(); });
  document.getElementById("save-accept")?.addEventListener("click", async () => {
    const error = form.querySelector(".error");
    try {
      await api("PUT", `${base}/${name}`, { form: fields() });
      await api("POST", `/api/projects/${slug}/drafts/${name}/accept`);
      done();
    } catch (exc) { error.textContent = exc.message; }
  });
  document.getElementById("as-yaml").addEventListener("click", () => {
    yamlForm.elements.name.value = form.elements.name.value;
    form.hidden = true;
    yamlForm.hidden = false;
  });
  document.getElementById("as-form").addEventListener("click", () => { yamlForm.hidden = true; form.hidden = false; });

  onSubmit(document.getElementById("generate"), async (values) => {
    await api("POST", `/api/projects/${slug}/generate`, values);
    location.hash = `#/p/${slug}/runs`;
  });
  const draftError = document.getElementById("draft-error");
  section.querySelectorAll("[data-accept]").forEach((button) => button.addEventListener("click", async () => {
    try { await api("POST", `/api/projects/${slug}/drafts/${button.dataset.accept}/accept`); route(); }
    catch (exc) { draftError.textContent = exc.message; }
  }));
  section.querySelectorAll("[data-drop]").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm(`Delete the draft ${button.dataset.drop}?`)) return;
    await api("DELETE", `/api/projects/${slug}/drafts/${button.dataset.drop}`);
    route();
  }));
  document.getElementById("accept-all")?.addEventListener("click", async () => {
    const result = await api("POST", `/api/projects/${slug}/drafts/accept-all`);
    if (result.problems.length) draftError.textContent = result.problems.join(" · ");
    else route();
  });
  section.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm(`Delete the test ${button.dataset.delete}?`)) return;
    await api("DELETE", `/api/projects/${slug}/specs/${button.dataset.delete}`);
    route();
  }));
}

const TARGET_LABELS = [["chrome", "Chrome"], ["firefox", "Firefox"], ["safari", "Safari (WebKit)"],
  ["iphone", "iPhone"], ["android", "Android phone"]];

function settingsTab(section, slug, data) {
  const p = data.project;
  const secrets = data.secrets.map((name) => `
    <tr><td class="mono">${esc(name)}</td><td>set</td>
      <td><button type="button" class="link" data-secret="${esc(name)}">Delete</button></td></tr>`).join("");
  section.innerHTML = `
    <form class="card stack" id="settings">
      <label>Client <input name="client" required value="${esc(p.client)}"></label>
      <label>Base URL (optional: runs every test against this deployment, keeping each test's path)
        <input name="base_url" value="${esc(p.base_url)}" placeholder="https://staging.acme.example"></label>
      <label>Report prepared by <input name="brand" value="${esc(p.brand)}"></label>
      <label>Nightly run at (24-hour, server time; empty for none) <input name="nightly" value="${esc(p.nightly)}" placeholder="02:30"></label>
      <fieldset class="targets"><legend>Run every test on</legend>
        ${TARGET_LABELS.map(([key, label]) => `<label class="inline"><input type="checkbox" name="target" value="${key}"
          ${(p.targets || "chrome").split(",").includes(key) ? "checked" : ""}> ${label}</label>`).join("")}
      </fieldset>
      <p class="error" role="alert"></p>
      <div><button type="submit">Save settings</button></div>
    </form>
    <h2>Secrets</h2>
    <p class="muted">Test passwords and keys. A test uses one as \${NAME} in its data. Values can't be read back.</p>
    <table><tbody>${secrets || '<tr><td class="muted">None yet.</td></tr>'}</tbody></table>
    <form class="card row" id="secret">
      <label>Name <input name="name" required pattern="[A-Z_][A-Z0-9_]*" placeholder="SHOP_PASSWORD"></label>
      <label>Value <input name="value" type="password" autocomplete="new-password" required></label>
      <button type="submit">Set secret</button>
      <p class="error" role="alert"></p>
    </form>`;
  onSubmit(document.getElementById("settings"), async (fields) => {
    const targets = [...document.querySelectorAll("#settings input[name=target]:checked")].map((box) => box.value).join(",");
    delete fields.target;
    await api("PUT", `/api/projects/${slug}`, { ...fields, targets });
    route();
  });
  onSubmit(document.getElementById("secret"), async (fields) => {
    await api("PUT", `/api/projects/${slug}/secrets/${fields.name}`, { value: fields.value });
    route();
  });
  section.querySelectorAll("[data-secret]").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm(`Delete the secret ${button.dataset.secret}?`)) return;
    await api("DELETE", `/api/projects/${slug}/secrets/${button.dataset.secret}`);
    route();
  }));
}

async function usersPage() {
  const token = routeToken;
  const { users } = await api("GET", "/api/users");
  if (stale(token)) return;
  const rows = users.map((u) => `
    <tr><td>${esc(u.email)}</td><td>${esc(u.name)}</td><td>${u.admin ? "admin" : "staff"}</td>
      <td>${u.id === me.id ? "" : `<button type="button" class="link" data-user="${u.id}">Delete</button>`}</td></tr>`).join("");
  view.innerHTML = `
    <h1>Users</h1>
    <table><thead><tr><th>Email</th><th>Name</th><th>Role</th><th></th></tr></thead><tbody>${rows}</tbody></table>
    <h2>Add a user</h2>
    <form class="card stack" id="new-user">
      <label>Email <input name="email" type="email" required></label>
      <label>Name <input name="name"></label>
      <label>First password (they change it under Account) <input name="password" type="password" autocomplete="new-password" minlength="10" required></label>
      <label class="inline"><input name="admin" type="checkbox" value="1"> Admin (manages users, projects and secrets)</label>
      <p class="error" role="alert"></p>
      <div><button type="submit">Add user</button></div>
    </form>`;
  onSubmit(document.getElementById("new-user"), async (fields) => {
    await api("POST", "/api/users", { ...fields, admin: fields.admin === "1" });
    route();
  });
  view.querySelectorAll("[data-user]").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm("Delete this user?")) return;
    await api("DELETE", `/api/users/${button.dataset.user}`);
    route();
  }));
}

function accountPage() {
  view.innerHTML = `
    <h1>Account</h1>
    <p class="muted">${esc(me.email)}</p>
    <form class="card stack narrow" id="password">
      <label>Current password <input name="current" type="password" autocomplete="current-password" required></label>
      <label>New password (10 characters or more) <input name="new" type="password" autocomplete="new-password" minlength="10" required></label>
      <p class="error" role="alert"></p>
      <div><button type="submit">Change password</button></div>
    </form>`;
  onSubmit(document.getElementById("password"), async (fields) => {
    await api("PUT", "/api/me/password", fields);
    me = null;
    location.hash = "#/login";
  });
}

// --- routing ---------------------------------------------------------------------------

async function route() {
  routeToken += 1;
  clearTimeout(poll);
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "login") return loginPage();
  try {
    if (!me) me = (await api("GET", "/api/me")).user;
  } catch {
    return;  // api() sent us to the login page
  }
  document.getElementById("bar").hidden = false;
  document.getElementById("nav-users").hidden = !me.admin;
  try {
    if (parts[0] === "p" && parts[1]) return await projectPage(parts[1], parts[2] || "runs", parts[3]);
    if (parts[0] === "users" && me.admin) return await usersPage();
    if (parts[0] === "account") return accountPage();
    return await projectsPage();
  } catch (exc) {
    view.innerHTML = `<p class="error">${esc(exc.message)}</p>`;
  }
}

document.getElementById("logout").addEventListener("click", async () => {
  await api("POST", "/api/logout").catch(() => {});
  me = null;
  location.hash = "#/login";
});
window.addEventListener("hashchange", route);
route();
