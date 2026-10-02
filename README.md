# Nightshift

**An AI QA tester.** Describe a user flow in plain English. An agent runs it in a real browser,
clicking and typing like a person, and a second model has to *prove* the result from the page
before anything counts as a pass. When something breaks, you get a bug report with steps to
reproduce, screenshots, and a replayable trace.

It covers the day-to-day of a QA tester:

| The job | Nightshift |
|---|---|
| Application validation | `nightshift validate reqs.md`: tests every requirement, writes the traceability matrix |
| Test case design | test cases per requirement (positive, negative, boundary), reviewed in code; `nightshift cases` writes the test-case document |
| Defect analysis | failures grouped by root cause, classified, diffed against the last pass (`defects.html`), optionally filed as GitHub issues |
| Write test cases from requirements | `nightshift generate --story "..."`, or from a crawl of the app |
| Run the regression suite | `nightshift run specs/`: agent on the first run, free replay after that |
| Decide pass or fail honestly | a grounded judge: every claim must quote the page, and code checks the quotes |
| Exploratory testing | `nightshift explore URL`: roams the app, tries bad input, reports what breaks |
| File bug reports | `bug.md` per failure: steps to reproduce, expected vs actual, severity, evidence |
| Re-test before crying wolf | failures are re-run by a fresh agent; flaky tests are labelled, not reported as bugs |
| Keep tests alive through redesigns | self-healing replay: when a saved step breaks, the agent takes over from there |
| Cross-device, a11y, broken links | `--device "iPhone 13"`, accessibility and 404 checks on every page |
| Report to the team | HTML reports, a history dashboard, JUnit for CI, a GitHub Action, Slack alerts |
| Hand tests to developers | `nightshift export`: plain Playwright tests, no AI needed to run them |

```yaml
# specs/checkout.yaml
name: checkout
url: http://localhost:5180/
steps:
  - log in with the test account
  - add Filter Coffee to the cart
  - open the cart and proceed to checkout
  - fill in the delivery details with the test data
  - choose cash on delivery and place the order
expect:
  - a page says the order was placed
  - it shows an order number that looks like KC-12345
  - the amount to pay is ₹240
data:
  email: shopper@kulhad.test
  password: ${SHOP_PASSWORD}      # read from the environment; the model only ever sees {{password}}
```

## Does it work? Measured, not claimed

Two apps with planted bugs. The **demo shop** is the one Nightshift was developed against. The
**clinic** is a holdout: built in different idioms (server-rendered forms, `<div role="button">`,
dropdowns, date pickers, `confirm()` dialogs), and nothing was ever tuned on it. Model: `qwen3-vl:4b-instruct`,
a 4B open-weights model on a 6 GB laptop GPU (RTX 3050), no retries.

| | Demo shop (development set) | Clinic (holdout, seen once) |
|---|---|---|
| Planted bugs caught | **20/20** | **8/8** |
| False alarms on the bug-free app, retries off | 1/9 | 1/5 |
| False alarms as `nightshift run` works (one retry) | 1/9 | 0/5 |
| Tester errors (couldn't finish) | 0/29 | 0/13 |
| Median time per run | 22 s | 31 s |
| Median model time per step | 2.5 s | 2.8 s |

The shop's 21st bug (an emailed sign-in code that is never accepted) came after this run. It was
checked on its own: `login-with-code` passes on the clean shop and fails with the bug planted.

Both remaining false alarms are the agent not doing a step as written. It filled in only the
pincode, or it ticked a box it was told to leave empty (and said so: "the checkbox was accidentally
ticked, violating the test requirement"). The judge was right that the expected result wasn't on
the page, but the tester made the mistake, not the app. The retry clears the clinic's slip. The
shop's one does not: on `checkout-validation`, where a single step says both "fill in the delivery
details" and "use the 5-digit pincode", the 4B model skips the other fields every time. That is the
current limit of a 4B model, not something to hide by rewording the spec.

Read the reasons, not only the scores. A few bugs were caught with muddled reasoning. For example,
`consent-ignored` failed because the expected error never appeared (correct), but the agent's
explanation was that the checkbox was missing (wrong). The benchmark tables list every reason.

**The holdout earned its keep.** Its first blind run scored **4 false alarms out of 5**, while the
shop looked nearly perfect. The model was clicking native dropdowns and date fields like buttons,
and the shop has no dropdowns. That was fixed in a way that applies to any app (see
[docs/DECISIONS.md](docs/DECISIONS.md), D10), so the clinic is now "seen once", not blind.

How the numbers moved while building it (same 4B model, retries off):

| Version | Shop: caught | Shop: false alarms | Clinic: caught | Clinic: false alarms |
|---|---|---|---|---|
| Week 1: agent decides alone (6 bugs) | 5/6 | 0/3 | | |
| + grounded judge (20 bugs) | 20/20 | 2/9 | | |
| + absence checks, the judge reviews the agent's fails | 20/20 | 1/9 | 8/8* | 4/5 (first blind run) |
| + dropdowns and date fields handled | 20/20 | 1/9 | 8/8 | 0/5 |
| + wait for the requests a click sets off | 20/20 | 1/9 | 8/8 | 1/5 |
| + the fixes from the real app (see below) | 20/20 | 1/9 | 8/8 | 1/5 |

\* mostly hollow: the same dropdown flaw that caused the false alarms. The last two rows are run-to-run
variation on the clinic (0/5, then 1/5), not a regression.

Full tables with every run's reason: `runs/bench-*/bench.md` (`uv run python -m benchmark`).

The judge is what makes these numbers possible. Here is the agent, a small model, declaring a
broken cart "PASS: totaling ₹600". The judge had to quote the page, found `Total: ₹180`, did the
arithmetic, and failed it:

> **not shown:** the total equals the sum of the line totals (The line totals are ₹180 and ₹420,
> summing to ₹600, but the total shown is ₹180, which does not match.)

### On a real app: Wagtail's bakerydemo

The demo shop and clinic were built for this, so they flatter. The real test is
[bakerydemo](https://github.com/wagtail/bakerydemo), the official demo site of the Wagtail CMS: a
real Django codebase with search, a tagged blog, a validated contact form and a full admin panel.
Nine specs were written from browsing the site *before* Nightshift first ran on it. Eight bugs were
planted in its real source code (a dropped search result, an unassigned filter, a copy-paste slip
in a template, a crashing form hook...), each one checked to actually show on the site first.
`realworld/bakerydemo/` has the specs, the bug patches and the harness
(`uv run python -m realworld.bakerydemo.eval`).

| Run | Bugs caught | False alarms (9 clean specs) | What changed before it |
|---|---|---|---|
| 1. First blind run | 6/8 (+1 by luck, 1 missed) | 2/9 | nothing: specs written, app never seen |
| 2 | 7/8 | 3/9 | value rule, typography, browser validation messages, following new tabs |
| 3 | 7/8 | 1/9 | refinements: absence claims, made-up labels, default Submit label, no-effect rule reviewed by the judge |
| 4 | **8/8** | **0/9** | the judge proves claims from the page, not the browser-tab title |

Run 1 is the honest *blind* number. After it the app counts as seen, like the clinic. Every fix is
general and is explained in [docs/DECISIONS.md](docs/DECISIONS.md) (D12). Single runs vary: the
hardest flow, editing and publishing a page in Wagtail's admin, passed on the bug-free site in only
one of the four runs. That is the edge of a 4B model.

The misses and false alarms taught more than the passes. The judge passed "the origin is France"
on quotes that never said France. A browser tooltip rejecting a bad email is invisible to page text.
Wagtail's "Visit the live page" opens a new tab. `<input type="submit">` has no text of its own. And
a heading bug hid behind a correct browser-tab title. None of these showed up on the apps built for
testing.

### Validating the real app against its requirements

`realworld/bakerydemo/requirements.md` is a 7-line requirements document, written the way a
product manager would. `nightshift validate` designed test cases from it (after exploring the
site), ran them, and wrote the traceability matrix. Then the same tests ran with three bugs
planted in the site's source code (`uv run python -m realworld.bakerydemo.validate_demo`):

| | Untouched site | 3 bugs planted |
|---|---|---|
| Requirements passed | **7/7** (one test flaky: failed once, passed on retry) | 4/7 |
| Requirements failed | 0 | **3: exactly the three the bugs break** (search, blog tag filter, contact form) |
| Defects reported | none (one low "flaky" note) | **3, one per bug**: a backend server error on `POST /contact-us/`, and two wrong results, each diffed against the clean run ("You searched for Baguette, 1 result found" became "No results found") |

The honest caveat is test design. With the 4B model, 6 of the 8 designed test cases were used as
designed and a reviewer edited 2 ([REVIEW.md](realworld/bakerydemo/REVIEW.md)). The first design
round, before the design review in code existed, had 5 of 9 cases unusable and flagged a correct
site as failing 5 of 7 requirements. So `validate --design-only` is the intended workflow: the
model drafts the test cases, a person reads them, then Nightshift runs and reports.

### Replay: pay for the agent once

The same checkout spec on the same laptop, live:

| Run | Time | Model calls | Tokens |
|---|---|---|---|
| First run: the agent finds the path, which is then saved | 73 s | 17 | 36,480 |
| Every run after: replay the saved path, only the judge is called | **12.5 s** | **1** | **1,787** |
| After a redesign renames a button: replay breaks at step 7, the agent heals it | 38 s | 9 | 19,199 |

### Explore, generate, run

`nightshift explore` on the shop with three hidden bugs proved one of them (a JavaScript crash on
the cart page) and missed two. Its "suspected" findings were mostly wrong, which is why they are
kept apart from proven ones. Exploring is where a 4B model is weakest: use a bigger model for it.

`nightshift generate`, given that exploration and one sentence ("a customer can remove an item
from the cart, and the total updates"), wrote a spec that passed on the clean shop and caught the
planted `remove-wrong-item` bug. Its first drafts were wrong (they assumed a full cart and copied
a buggy total as the expected value). The prompt now says every test starts in a fresh browser and
never trusts numbers seen while exploring.

## Quickstart

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/), and a vision model behind an
OpenAI-compatible API. The default is a local [Ollama](https://ollama.com) with
`qwen3-vl:4b-instruct` (about 3 GB).

```bash
uv sync
uv run playwright install chromium
ollama pull qwen3-vl:4b-instruct
```

Start the demo shop (in its own terminal), then run the specs:

```bash
uv run python -m demo_shop
```

```bash
uv run nightshift run specs/ --headed
```

Plant a bug and watch it get caught (`--list-bugs` shows all 22):

```bash
uv run python -m demo_shop --bugs checkout-500
```

In your own project:

```bash
uv run nightshift init --url http://localhost:3000/
```

## API tests

A spec with `requests:` instead of steps tests the backend directly: no browser, no model,
deterministic, and fast (a few hundred milliseconds).

```yaml
name: api-order
url: http://localhost:5180/
requests:
  - name: place an order
    post: /api/order
    json: {items: [{id: coffee, qty: 2}], name: T, address: A, city: C, pincode: "411001", payment: cod}
    expect:
      status: 201
      json: {orderId: /^KC-\d+$/, total: 480}   # /.../ is a regular expression; * means "present"
      max_ms: 2000
    save: {order: orderId}                      # use {{order}} in later requests
```

Checks: `status`, `json` (paths like `user.name`, `items[0].id`, `items.length`; a value,
`/regex/`, `*` for present, `<missing>` for absent), `headers`, `contains`, `max_ms`. Results
go into the same reports, defect analysis, JUnit and dashboard as browser tests, and a 5xx is
classified as a server error just the same. `specs/api/` has three for the demo shop. They catch
its five backend bugs, for example `total is 480 (got 530)`.

## Email codes and magic links

For flows that email the user, the agent types `{{email_code}}` (or does `goto {{email_link}}`).
Right before acting, Nightshift reads the newest email sent to the test address since the test
started, and fills in the code or link. The model never sees the inbox. The inbox is either:

- a Mailpit-style HTTP API: `inbox: http://localhost:8025` in the spec, or `INBOX_URL`
  ([Mailpit](https://mailpit.axllent.org) is the usual mail catcher for development and CI), or
- an IMAP mailbox: `INBOX_IMAP_HOST`, `INBOX_IMAP_USER`, `INBOX_IMAP_PASSWORD`.

The demo shop has "Sign in with an email code" with an outbox at `/mail` that speaks Mailpit's
API, so `specs/login-with-code.yaml` runs out of the box. With the real 4B model it signed in
using a code it never saw, and caught the planted `otp-wrong-code` bug. SMS codes are not supported.

## Payments, two-factor codes and edge cases

**Payments in test mode.** Payment widgets live in iframes on the gateway's own origin; Nightshift
reads and acts inside visible iframes like the page itself. The demo shop's "Pay online now" opens
**Kulhad Pay**, a stand-in gateway in a cross-origin iframe that takes Razorpay's published test UPI
IDs: `success@razorpay` pays, `failure@razorpay` is declined. `specs/pay-online.yaml` and
`specs/pay-online-failure.yaml` test both, and the planted bug `payment-failure-ignored` (the order
goes through after a declined payment) is caught. Use the same IDs against your own app's Razorpay
test mode; that has not been tried here, since it needs your test account.

**Authenticator-app codes.** Put the base32 secret behind the test account's QR code in the spec's
data as `totp_secret` (as `${ENV_VAR}`), and the agent types `{{totp_code}}`: the current six-digit
code, computed right before typing. The model never sees the secret.

**Edge cases from one test.** `nightshift edge-cases specs/signup.yaml` writes, for each value the
test types, an empty version and a wrong-format one (an email that isn't one, letters in a phone
number, a two-digit PIN code, a one-character password) or a 300-character one, each expecting an
error message and no success. `nightshift run ... --edge-cases` generates and runs them in one go;
with `--url`, `--goal` and `--data` that is a whole negative suite from one sentence. They are
drafts: read a failing one before filing it.

## A report for your client

```bash
uv run nightshift run specs/ --client "Acme Retail" --brand "Your QA Co" --logo logo.png
uv run nightshift client-report runs/<run> --client "Acme Retail" --brand "Your QA Co" --specs specs/
```

`client-report.html` is one self-contained file, with screenshots embedded, to email, attach or print
to PDF. It opens with a release recommendation ("Not ready to release: 2 defects found, 1 of high
severity"), then counts by cause, each defect with its severity, steps to reproduce, the failing
screenshot and its Jira or GitHub ticket, a requirements table when specs list requirement ids, and
every test's result. With `--brand` it carries your company's name and not Nightshift's. The
dashboard's Runs page links to it.

## Filing defects in Jira

```bash
set JIRA_URL=https://yourteam.atlassian.net
set JIRA_PROJECT=SHOP
set JIRA_EMAIL=you@company.com
set JIRA_API_TOKEN=your-token
uv run nightshift run specs/ --file-jira
```

(Jira Data Center: set `JIRA_TOKEN` to a personal access token instead of the email and API
token.) Each defect becomes one Bug (a Task if the project has no Bug type), with the write-up as its description, labels `nightshift` and
its category, and the failing screenshot and bug report attached. If the same defect is already
open, from an earlier run, it gets a comment instead of a duplicate ticket; if its issue was
closed and the defect comes back, it is filed again. Also available as `nightshift triage
runs/<run> --file-jira` and as a button on the dashboard's Runs page. Tested on a live Jira Cloud
site: the first run filed the shop's wrong-total bug as a ticket with its evidence attached, and
the second run commented on that ticket instead of filing it again. `JIRA_URL` can be any Jira
address copied from the browser, a board's included.

## The dashboard

```bash
uv run nightshift serve
```

A web page on your own machine (http://127.0.0.1:8765) for everything the commands do:

- **Overview**: the last run, pass rate across recent runs, open defects, and a switch to start
  the demo shop with any of its 22 bugs planted
- **Test cases**: every spec with its requirements, technique, priority and last result; read,
  edit (checked before saving), create, run one or a folder; export the test-case document
- **Run tests**: watch it live, with the log and the screenshot the agent is looking at; stop it
- **Validate requirements**: write requirements, have test cases designed, review them, run
  them, get the traceability matrix
- **Explore**: point it at an app and let it look for bugs
- **Runs & reports**: every run's results, defects (grouped, diffed against the last pass) and
  traceability matrix, side by side

It only answers to this computer: it binds to 127.0.0.1, refuses requests that don't name
localhost (DNS rebinding), and anything that changes something needs a token that only the
dashboard page has, so another website open in your browser can't start a run.

## Commands

| Command | What it does |
|---|---|
| `nightshift run specs/` | Run specs. Replays saved paths, judges every pass, re-runs failures once (`--retries`), writes reports. |
| `nightshift run --url URL --goal "..."` | No spec to write: a start page and a plain-English goal. The spec is saved to `specs/goals/`, so the next run of the same goal replays its path. |
| `nightshift explore URL` | No spec: roam the app for `--steps` actions, try bad input, report bugs. Writes `findings.md` and a map of the app. |
| `nightshift generate --story "..."` | Draft specs from a requirement. Add `--from discovered.json` (from explore) so it uses the app's real labels. |
| `nightshift export specs/` | Turn saved paths into `@playwright/test` files with role-based locators and assertions from the judge's evidence. |
| `nightshift report` | Rebuild `runs/index.html`: every spec's verdict across the last 30 runs, so flaky specs stand out. |
| `nightshift init` | Starter spec, GitHub workflow, `.gitignore` entry. |
| `nightshift serve` | The dashboard (see above). `--specs` adds folders of test cases to show. |
| `nightshift validate reqs.md --url URL` | Application validation: design test cases for each requirement (positive, negative, boundary), run them, and write the **traceability matrix** (requirement, tests, result, defects). `--design-only` stops after design so a person can review the cases first. |
| `nightshift cases specs/` | The **test-case document**: one row per case (title, requirements, technique, priority, steps, expected, last result), as CSV for Excel or a test-management import, plus Markdown. |
| `nightshift triage runs/<run>` | **Defect analysis**: groups failures with one root cause into one defect, classifies it (server error, frontend crash, dead control, wrong result), points at the step that broke, and diffs the page against the last time the test passed. `--file-github owner/repo` files each defect as an issue. Runs automatically after any run with failures. |

Useful `run` options: `--base-url` (run the same specs against staging), `--device "Pixel 7"`,
`--browser firefox|webkit`, `--video`, `--junit results.xml`, `--no-vision` (text only, faster on
small models), `--slack-webhook` (or `SLACK_WEBHOOK_URL`).

Exit codes: `0` passed (flaky counts as passed, with a warning), `1` found a bug, `2` the tester
couldn't finish.

Every result that isn't a pass is labelled with its cause:

| Label | Meaning |
|---|---|
| `BUG` | The app is wrong: a proven wrong or missing result, a dead control, a crash, an HTTP 500. |
| `FLAKY` | Failed, then passed for a fresh agent. The label says when the failing try showed no app error. |
| `TEST_OUTDATED` | The test couldn't be carried out as written: its saved path broke and couldn't be healed, or the tester couldn't follow its steps. |
| `ENV_ISSUE` | Nothing was tested: the site or a gateway in front of it was down (502/503/504/52x), a bot check was in the way, or the model couldn't be reached. Reported as an error, never as a failure of the app. |

### Models

With a local Ollama, start it with `OLLAMA_CONTEXT_LENGTH=8192`. Its default 4,096-token context is
tight once a screenshot is attached; a prompt that doesn't fit is sent again in a compact form, which
works but shows the model less of the page.

| Variable | Default |
|---|---|
| `MODEL_BASE_URL` | `http://localhost:11434/v1` (Ollama) |
| `MODEL_NAME` | `qwen3-vl:4b-instruct` |
| `MODEL_API_KEY` | empty |
| `JUDGE_NAME`, `JUDGE_BASE_URL`, `JUDGE_API_KEY` | same as the agent. The judge runs once per test, so a bigger model is cheap here. |

## How it works

```
             ┌─ saved path? ── replay it, no model ──┐  (a step can't be found? the agent takes over: self-healing)
spec ──> open page                                   ├──> judge: quote the page for every expected result
             └─ agent: read page -> pick ONE action -> act -> repeat ┘       code checks every quote
                        ^                                                      │
                        └──── did the page change? (fingerprint) ─────┘        v
                                                          pass / fail / flaky / error + report + bug.md
```

1. **Read the page.** Every visible interactive element gets a number, drawn on the screenshot as a
   red badge. The model acts by id (`click 12`), never by pixel coordinates.
2. **Act.** One JSON action per turn. After each one the page is fingerprinted, so "no change" is a
   fact the model is told, not a guess.
3. **Free signals.** An uncaught JS error or an HTTP 5xx fails the test even if the page looks fine.
   Three identical actions with no effect fail it too (a dead button). 404s, slow calls, console
   errors and accessibility gaps become warnings.
4. **Judge.** A pass (and an agent's fail) goes to a judge that must quote the page for every expected
   result, or name text that must be absent. Code checks every quote. A made-up quote can't pass.
5. **Save.** A passing path is saved as ranked, verified locators. The next run replays it with no
   model calls except the judge.

The reasoning behind each choice is in [docs/DECISIONS.md](docs/DECISIONS.md).

## Output

Every run writes `runs/<timestamp>/`:

- `index.html`: the run at a glance. `runs/index.html` is the history across runs.
- `<spec>/report.html`: verdict, the judge's evidence, every step with the screenshot the model saw
- `<spec>/bug.md`: for failures. Paste it into Jira or GitHub.
- `<spec>/trace.zip`: `uv run playwright show-trace <path>` for DOM, network and console at every action
- `<spec>/video.webm` with `--video`
- `summary.json` and, with `--junit`, JUnit XML

## CI

Use the GitHub Action (see [examples/github-workflow.yml](examples/github-workflow.yml)):

```yaml
- uses: dhyey3333/nightshift@main
  with:
    specs: specs/
    base-url: ${{ vars.STAGING_URL }}
    model-base-url: https://openrouter.ai/api/v1
    model-name: qwen/qwen3-vl-8b-instruct
    model-api-key: ${{ secrets.MODEL_API_KEY }}
    slack-webhook: ${{ secrets.SLACK_WEBHOOK_URL }}
```

Commit `.nightshift/recordings/`. CI then replays saved paths for free and only pays for the
agent when the UI changes. For Jenkins or GitLab there's a `Dockerfile`.

## Development

```bash
uv run pytest
```

The end-to-end tests drive the real browser against the real demo shop and clinic, with a scripted
model in place of the LLM (`tests/scripted.py`), so they take about a minute and need no GPU.

```bash
uv run python -m benchmark
```

```bash
uv run python -m benchmark --app clinic
```

```
nightshift/
  runner.py     replay, the agent loop, the judge gate, retries
  judge.py      grounded verdicts: quotes and absences checked against the page
  observe.py    numbers the elements, reads the page, draws the badges
  locators.py   finding an element again on a later run
  recording.py  saved paths
  checks.py     what the browser proves without a model: JS errors, 5xx, 404s, a11y
  explore.py    exploratory testing
  generate.py   specs from requirements or an explored app
  export.py     Playwright test export
  report.py     HTML reports, bug reports, history, JUnit
  prompts.py    every prompt, in one place
demo_shop/      Kulhad & Co.: the development app, 22 planted bugs
holdout/        Sehat Clinic: the holdout app, 8 planted bugs, never tuned on
benchmark/      scores the tester against either app
```

## Known limits

- Visible iframes are read and driven (payment widgets, embedded forms); shadow DOM isn't read yet.
  A step inside an iframe isn't saved for replay, so those flows use the agent every run.
- Clickable `<div>`s with no role, no tabindex and no inline handler are invisible to the element list.
- On the 4B model every agent step takes about 2 s, so a 14-step checkout takes about 40 s. Replay
  makes repeat runs cheap. A bigger hosted model is more accurate and not much slower.
- Firefox, WebKit and the Docker image are wired up but only Chromium was tested here.
- It checks what the page shows. It reads a test inbox for email codes and links, but not the
  database or the payment provider. Authenticator-app codes are supported; SMS codes aren't.
