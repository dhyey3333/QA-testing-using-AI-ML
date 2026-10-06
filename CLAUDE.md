# CLAUDE.md

## Project
Nightshift (working name): an AI QA tester. Plain-English specs (`specs/*.yaml`) run by an agent in a real browser (Playwright); a grounded judge must prove every expected result from the page before a pass counts. Also: exploratory testing, spec generation, self-healing replay, Playwright export, bug reports, CI integration.

Design reasoning: `docs/DECISIONS.md`.

## Layout
- `nightshift/`: the product. `runner.py` (replay, agent loop, judge gate, retries), `judge.py`, `observe.py`, `locators.py`, `recording.py`, `checks.py`, `explore.py`, `generate.py`, `export.py`, `report.py`, `notify.py`, `prompts.py` (every prompt), `cli.py`.
- `demo_shop/`: Kulhad & Co., the development app. 23 planted bugs in `server.py` -> `BUGS`; the `redesign` variant tests self-healing.
- `holdout/`: Sehat Clinic, the holdout app. 8 planted bugs, specs in `holdout/specs/`.
- `benchmark/`: `python -m benchmark [--app shop|clinic]`.
- `realworld/bakerydemo/`: Wagtail's real bakerydemo site: specs, 8 bug patches for its real source, and `eval.py`. Needs a checkout at `../realapps/bakerydemo` (or `BAKERYDEMO_DIR`) set up per its README (Python 3.12 venv in `.venv`, migrate, load_initial_data). The harness resets the checkout and database before every run and refuses to run over uncommitted edits.
- `tests/`: unit tests plus end-to-end tests with a scripted model (`tests/scripted.py`), no LLM needed.
- `action.yml`, `examples/github-workflow.yml`, `Dockerfile`: CI. `.github/workflows/nightshift.yml` runs this repo's own PR check: the demo shop, `specs/api` and the shop's UI specs replayed from `ci/recordings` with no model; it comments on the PR (`--pr-comment`, `report.pr_comment`). Regenerate `ci/recordings` with the local model when those specs change.

## Commands (keep current)
- `uv sync`, `uv run pytest` (about 2 minutes)
- `uv run python -m demo_shop [--bugs NAME,... | all] [--variant redesign] [--list-bugs]` (port 5180); `uv run python -m holdout.server [--bugs ...]` (port 5190)
- `uv run nightshift run specs/ [--headed] [--no-vision] [--base-url URL] [--retries N] [--device NAME] [--video] [--junit PATH]`
- `uv run nightshift explore URL [--steps N] [--focus TEXT] [--data k=v]`
- `uv run nightshift generate --story TEXT [--from discovered.json] [--url URL]`
- `uv run nightshift export specs/`, `uv run nightshift report`, `uv run nightshift init`
- `uv run nightshift validate reqs.md --url URL [--data k=v] [--design-only] [--redesign R5,R6] [--from discovered.json | --explore-steps N]`: requirements -> designed test cases (specs/requirements/) -> run -> traceability.html, defects.html, test-cases.csv
- `uv run nightshift cases specs/ [--run runs/<run>]` (test-case document), `uv run nightshift triage runs/<run> [--file-github owner/repo] [--file-jira]` (defect analysis)
- `uv run python -m realworld.bakerydemo.validate_demo`: requirement validation on the real app, clean then with 3 bugs (about 20 min)
- API specs (`requests:` instead of steps, `nightshift/api.py`): `uv run nightshift run specs/api`. No model.
- Email codes: `{{email_code}}` / `{{email_link}}` in specs, inbox from the spec's `inbox:` or `INBOX_URL` (Mailpit API) or `INBOX_IMAP_*` (`nightshift/inbox.py`). The demo shop's outbox is at `/mail`.
- Jira: `--file-jira` on run/validate/triage, configured by `JIRA_URL`, `JIRA_PROJECT`, `JIRA_EMAIL` + `JIRA_API_TOKEN` (Cloud) or `JIRA_TOKEN` (Data Center) (`nightshift/jira.py`).
- `uv run nightshift serve [--port 8765] [--specs DIR]`: the local dashboard (`nightshift/dashboard/`: server.py + static/ plain JS). Keep its guards: 127.0.0.1 only, Host check, token on every change, paths confined to the workspace.
- `uv run python -m benchmark [--app clinic] [--only bug1,bug2] [--no-judge] [--no-vision] [--clean-only]` writes `runs/bench-*/bench.md`
- `uv run python -m benchmark.public_sites [--only a,b] [--rerun] [--resume runs/public-X]`: 27 specs on public practice sites (`benchmark/public/`), verdict, time, model calls and model cost in INR per run; every verdict must still be checked by hand. Signups use `${NS_RUN}`.
- `uv run nightshift run --url URL --goal "..." [--data k=v] [--edge-cases]`: a goal-only spec, saved to `specs/goals/`.
- `uv run nightshift client-report runs/<run> --client NAME --brand NAME [--logo F] [--specs specs/]` (or `run --client/--brand`): one self-contained HTML report for a client (`nightshift/client_report.py`); ticket links from `issues.json`.
- `uv run nightshift edge-cases specs/x.yaml [--to specs/edge-cases]`: generated negative variants (`nightshift/edgecases.py`).
- Real sites (`tests/test_real_sites.py`): open shadow roots are read (`observe.DEEP_JS`), styled checkboxes are listed by their label, popup close controls that are plain elements are listed; `{{sms_code}}` from `sms_inbox:` / `SMS_INBOX_URL` (Mailpit-shaped; the demo shop's is `/sms`); `session_from: <spec>` starts logged in (`nightshift/sessions.py`, in memory only, includes sessionStorage); `js_errors: warn` or `--js-errors warn`.
- Hosted product (`nightshift/hosted/`: store.py SQLite, files.py per-project folders and write-only secrets, jobs.py queue + workers + nightly `tick`, server.py stdlib HTTP + static/ plain JS; `tests/test_hosted.py`): `uv run nightshift hosted add-user --data DIR --email E --admin`, `uv run nightshift hosted serve --data DIR [--port 8080 --public-url URL --max-runs 2 --parallel 2 --keep-runs 60]` (older runs' files are pruned, rows kept: `Runner.prune`). Deploy: `deploy/oracle/` (setup.sh, nightshift.service, Caddyfile, update.sh, README.md). Keep its guards: X-Nightshift header + Origin on changes, login lockout, run files confined to their run folder, secrets never returned. Tests are a form (`files.form_of` / `files.yaml_of`, YAML one click away); "Generate tests" runs `nightshift generate` into `<project>/drafts/` for a person to accept; "Explore" runs `nightshift explore`.
- Visual checks (`nightshift/visual.py`, `tests/test_visual.py`): a passing run with saved paths compares its final screen with `<recordings>/../visual/<spec>@<browser>-<viewport>.png`; `--visual off|warn|fail`, spec `visual:`, `--update-visual`. Measure with `uv run python -m benchmark.visual` (tuned page) and `--holdout` (never tune on it). Probable cause per failure: `nightshift/cause.py`, from recorded evidence only.
- `nightshift run --on chrome,firefox,safari,iphone,android` (`runner.TARGETS`, `expand_targets`, `browser_pool`; chrome keeps the spec's name, others are `spec@target`); hosted project setting `targets`. Firefox/WebKit: `uv run playwright install firefox webkit`.
- Cucumber: `nightshift gherkin import X.feature [--url] [--to specs/imported]`, `nightshift gherkin export specs/ [--to features]` (`nightshift/gherkin.py`); hosted Tests tab imports as drafts.
- One-click start on Windows: `launch/Start Nightshift.bat` (runs `launch/start.ps1`: Ollama check, first-start admin, cloudflared quick tunnel + clipboard, browser; `-Port -NoTunnel -NoBrowser -Data`); `launch/make-shortcut.ps1` makes the desktop icon (conhost --headless + `start.ps1 -Background`: no window, log in runs/nightshift.log; clicking it while running asks Open / Stop). `nightshift hosted users` lists users.
- Website (`site/`, static, GitHub Pages at nightshift-qa.github.io): preview `python -m http.server 8095 --directory site`; pictures `uv run python deploy/site/make_assets.py` (NS_EMAIL/NS_PASSWORD of a fake-data app); publish per `deploy/site/README.md` (git subtree split, no force push). Only measured claims; no fake testimonials or logos. The launcher prefers Tailscale Funnel (fixed link) over the cloudflared quick tunnel.
- `nightshift run --parallel N`: N specs at a time, a browser and a model client per worker; session providers run first.
- Bigger model for free: `ollama signin`, then `MODEL_NAME=gemma4:31b-cloud` (Ollama Cloud free plan; the other cloud vision models are paid). The user does not want to spend money on models.
- Payments: demo shop's `gateway.html` stand-in (cross-origin iframe, Razorpay test UPI IDs); TOTP via `totp_secret` data and `{{totp_code}}` (`nightshift/totp.py`).
- `uv run python -m realworld.bakerydemo.eval [--only bug,...] [--clean-only] [--skip-clean]` (real app; about 12 min)
- A new planted bug must be checked to actually show on the site before scoring anything against it.
- Model: any OpenAI-compatible endpoint via `MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY`; judge override `JUDGE_NAME` etc. Default: local Ollama `qwen3-vl:4b-instruct`.

## Rules
- **Holdout rule:** never tune a prompt, threshold, rule or spec against `holdout/`. Don't open its bug list while changing the agent. Run it only to report a number, and report that number as is.
- Verdicts stay honest: `fail` = the app has a bug, `error` = the tester couldn't finish, `flaky` = failed then passed. The benchmark counts a `fail` on a clean app as a false alarm.
- A pass needs proof: never weaken the judge's quote check to make a benchmark number better.
- A new planted bug goes in `BUGS` with the spec that should catch it. Re-run the benchmark before and after any prompt change and report both.
- Test data reaches the model only as `{{placeholders}}`. Demo data is fake.
