// Nightshift hosted: login, projects (one per client), their tests, runs and client reports.
// Plain JS, no build step. Every request sends X-Nightshift: 1, which the server requires for
// changes (a form on another site can't send it).

const view = document.getElementById("view");
let me = null;
let poll = null;

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
  const { projects } = await api("GET", "/api/projects");
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
  if (run.status !== "done") return `<span class="muted">${esc(run.message || "")}</span>`;
  return `<span class="n-pass">${run.passed} passed</span>` + (run.failed ? `<span class="n-fail">${run.failed} failed</span>` : "")
    + (run.flaky ? `<span class="n-flaky">${run.flaky} flaky</span>` : "") + (run.errors ? `<span class="n-error">${run.errors} errors</span>` : "");
}

async function projectPage(slug, tab, extra) {
  const data = await api("GET", `/api/projects/${slug}`);
  const p = data.project;
  const active = data.runs.find((r) => ["queued", "running"].includes(r.status));
  const tabs = [["runs", "Runs"], ["tests", `Tests (${data.specs.length})`], ...(me.admin ? [["settings", "Settings"]] : [])]
    .map(([key, label]) => `<a href="#/p/${esc(slug)}/${key}" class="${tab === key ? "on" : ""}">${label}</a>`).join("");
  view.innerHTML = `
    <div class="row spread">
      <div><h1>${esc(p.client)}</h1>
        <div class="muted">${p.nightly ? `Runs nightly at ${esc(p.nightly)}` : "No nightly schedule"} · ${data.parallel} tests at a time
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
  if (tab === "tests") return testsTab(section, slug, data, extra);
  if (tab === "settings" && me.admin) return settingsTab(section, slug, data);
  runsTab(section, data);
  if (active) poll = setTimeout(route, 4000);  // follow the run until it finishes
}

function runsTab(section, data) {
  const rows = data.runs.map((run) => `
    <tr>
      <td>#${run.id}</td>
      <td>${esc(when(run.queued))}<div class="muted">${esc(run.trigger)} · ${esc(took(run))}</div></td>
      <td><span class="badge ${esc(run.status)}">${esc(run.status)}</span></td>
      <td class="counts">${counts(run)}</td>
      <td>${[["client_report", "Client report"], ["report", "Full report"], ["log", "Log"]]
        .filter(([key]) => run.links[key]).map(([key, label]) => `<a href="${esc(run.links[key])}" target="_blank" rel="noopener">${label}</a>`).join(" · ")}</td>
    </tr>`).join("");
  section.innerHTML = rows
    ? `<table><thead><tr><th>Run</th><th>When</th><th>Status</th><th>Results</th><th>Reports</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<p class="muted">No runs yet. Add tests, then press Run now.</p>';
}

const TEMPLATE = `name: login
url: https://staging.example.com/
steps:
  - log in with the test account
expect:
  - the dashboard greets the user by name
data:
  email: qa@example.com
  password: \${SHOP_PASSWORD}   # a secret: set it under Settings
max_steps: 15
`;

async function testsTab(section, slug, data, editing) {
  const list = data.specs.map((name) => `
    <tr><td class="mono">${esc(name)}.yaml</td>
      <td class="row"><a href="#/p/${esc(slug)}/tests/${esc(name)}">Edit</a>
      <button type="button" class="link" data-delete="${esc(name)}">Delete</button></td></tr>`).join("");
  section.innerHTML = `
    <table><tbody>${list || '<tr><td class="muted">No tests yet.</td></tr>'}</tbody></table>
    <h2>${editing ? `Edit ${esc(editing)}.yaml` : "New test"}</h2>
    <form class="card stack" id="spec">
      <label>File name <input name="name" required pattern="[a-z0-9][a-z0-9-]*" value="${esc(editing || "")}"
        ${editing ? "readonly" : ""} placeholder="checkout"></label>
      <label>Test (plain-English steps and expected results, YAML) <textarea name="text" spellcheck="false" required></textarea></label>
      <p class="error" role="alert"></p>
      <div class="row"><button type="submit">Save</button>${editing ? `<a href="#/p/${esc(slug)}/tests">New test instead</a>` : ""}</div>
    </form>`;
  const form = document.getElementById("spec");
  form.elements.text.value = editing ? (await api("GET", `/api/projects/${slug}/specs/${editing}`)).text : TEMPLATE;
  onSubmit(form, async (fields) => {
    await api("PUT", `/api/projects/${slug}/specs/${fields.name}`, { text: fields.text });
    location.hash = `#/p/${slug}/tests`;
    route();
  });
  section.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm(`Delete ${button.dataset.delete}.yaml?`)) return;
    await api("DELETE", `/api/projects/${slug}/specs/${button.dataset.delete}`);
    route();
  }));
}

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
  onSubmit(document.getElementById("settings"), async (fields) => { await api("PUT", `/api/projects/${slug}`, fields); route(); });
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
  const { users } = await api("GET", "/api/users");
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
