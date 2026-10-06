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
error message about that value. `nightshift run ... --edge-cases` generates and runs them in one go;
with `--url`, `--goal` and `--data` that is a whole negative suite from one sentence. They are
drafts: read a failing one before filing it.

## Real sites: popups, phone logins, staying logged in

**Ads are blocked.** Ad networks' requests never load in a test's browser: a full-page ad whose close
button sits in the ad's own frame made a working site look broken. `--allow-ads` turns that off for a
site whose ads are the product.

**Popups and banners.** A cookie banner, sign-up popup or ad is closed and the test carries on; it
is not reported as a bug. Close controls that are plain `<span>`, `<div>` or `<p>` elements ("×",
"Close", "No thanks") are listed like buttons, and a click that something covers says what covers it.

**Phone number and OTP logins.** A row of one-digit OTP boxes gets the code one digit per box, and a
field that formats as you type ("98765 43210") is typed key by key when filling it at once doesn't
take. For the code itself: if your staging uses a fixed test OTP, put it in the spec's data. To read
real text messages, the agent types `{{sms_code}}` and Nightshift fetches the newest SMS to the
test number (data called `phone` or `mobile`) from `sms_inbox:` in the spec or `SMS_INBOX_URL`: an
endpoint with Mailpit's message API. The demo shop's `/sms` is one (`specs/login-with-phone.yaml`);
for a real SMS gateway that is a small adapter over its message log.

**Staying logged in.** `session_from: login` in a spec starts it with the browser session the
`login` spec ended with (cookies, localStorage and sessionStorage), so it has no login steps. The
login spec runs first, once per run, or is found next to the spec if it isn't in the run. Sessions
stay in memory and are never written to disk. A test that logs out should log in by itself.

**Background JavaScript errors.** An uncaught JS error fails a test by default. On a site whose
analytics or ad scripts throw on every page, `js_errors: warn` in the spec (or `--js-errors warn`
for the run) turns them into warnings in the report. An HTTP 5xx from the app still fails.

**Web components.** Controls and text inside open shadow roots are read and driven like the rest of
the page, and saved paths find them again (`benchmark/public/polymer-add-to-cart.yaml`).

**Styled checkboxes.** A checkbox or radio whose real input is hidden behind a styled label ("I
agree to the Privacy Policy") is listed by its label, with its checked state.

## Visual checks and why a test failed

**Visual checks.** A page can say all the right things and still look broken: a price printed
white on white, two cards drawn on top of each other, a missing image. When a test with saved paths
passes, its final screen is compared with the approved look from an earlier pass. A real change
goes to the model, zoomed in on where it changed, and the model says whether a person would call
it broken or whether it's just different content (another product, a new date).

- **A visual bug is a warning by default.** Use `--visual fail` or `visual: fail` in a spec to make it
  fail the test, or `--visual off` to switch the check off.
- **A new look is never approved by itself.** Use `--update-visual`, or "Accept the new look" in the
  web app.
- **Measured** (`uv run python -m benchmark.visual`):
  - 6 / 6 on the page it was tuned on.
  - 4 / 5 on a held-out page it never saw. It missed form fields drawn over their labels.

**Why it failed.** Every failure gets one plain sentence, built only from what the browser recorded:
the server error and its request, the script that crashed, the element covering a button, the
value the judge found instead of the expected one, or the agent's own description of the bug. It
also notes any API call the app refused. It's in each report, in `bug.md` and in the web app.

## One click on Windows

The desktop icon (`launch\make-shortcut.ps1` puts it there) starts Nightshift QA with no window and
opens it in the browser; double-click it again while it runs to **Open** or **Stop** it. Messages go to
`runs
ightshift.log`. `launch\Start Nightshift.bat` does the same in a visible window. Either way it:
- checks Ollama and uses the free cloud model;
- on first start, creates your admin login in its window;
- starts the app and opens your browser;
- with Tailscale installed and signed in (free, no card), opens your **fixed** public link with
  Tailscale Funnel: the same https address every time, so it can go on the website;
- otherwise, with `cloudflared` installed (`winget install Cloudflare.cloudflared`), opens a free
  public link that changes on every start;
- copies the link to the clipboard, to paste to anyone, and opens it.

Stop it with the icon's **Stop** (or, for the .bat, by closing its window).

The public website is in `site/`; see `deploy/site/README.md` to preview and publish it.

## Browsers, phones and Cucumber

**Browsers and phones.** `nightshift run specs/ --on chrome,firefox,safari,iphone,android` runs
every test on each one: Firefox, WebKit (Safari's engine), an iPhone in WebKit and an Android phone
in Chrome. Results sit side by side as `checkout`, `checkout@iphone` and so on, each with its own
saved path and approved look. In the web app it's a project setting: Settings → "Run every test
on". Firefox and WebKit need one download: `uv run playwright install firefox webkit`.

**Cucumber.** `nightshift gherkin import checkout.feature --url https://staging.example/` turns
each Scenario into a draft test:
- Given and When become the steps, and Then becomes what should happen.
- Background steps come first in every scenario.
- An Outline becomes one test per Examples row.

No step definitions are needed. `nightshift gherkin export specs/` writes tests back out as
`.feature` files. In the web app, the Tests tab has "Import Cucumber tests".

## Hosted, for a QA agency

`nightshift hosted serve` is the web app an agency's staff log in to. Each client is a project:
its tests, its saved paths, its test secrets, a nightly run time, and a history of runs, each with
its **client report**, full report and log.

Nobody writes YAML:
- **A test is a form:** the website, the steps one per line, what should happen one per line, and
  test data as `name = value`. A single sentence with nothing under "what should happen" becomes a
  goal the AI has to prove it reached.
- **"Generate tests with AI"** takes a URL (and, optionally, what the site is for), explores the site,
  and writes draft tests. Each draft lists what to double-check and the secrets it needs. A person
  reviews, edits and accepts them.
- **"Explore a website for bugs"** needs no tests at all: the AI uses the site and lists what it
  found broken, with screenshots.

A run that would stop on a missing secret is refused up front, naming the test and the secret.

Each project keeps the screenshots, traces and reports of its newest 60 runs (`--keep-runs`, 0 keeps
everything); older runs keep their summary in the history, so the disk doesn't fill up over months.

Each run has a details page: every test, its result, why it failed and its visual check, plus
**Accept the new look**, **File bugs in Jira** and **Post to Slack**. Jira and Slack read the
project's secrets: `JIRA_URL`, `JIRA_PROJECT`, `JIRA_EMAIL`, `JIRA_API_TOKEN` and
`SLACK_WEBHOOK_URL`. With `SLACK_WEBHOOK_URL` set, a run that finds problems posts by itself. Runs go in the background,
two at a time and two tests at a time within a run (`--max-runs`, `--parallel`); a nightly suite
of saved paths replays with no model calls.

It runs on Oracle Cloud's free server behind Caddy for HTTPS: `deploy/oracle/README.md` is the
whole setup, one script on a fresh Ubuntu server. Locally:

```bash
uv run nightshift hosted add-user --email you@agency.example --admin   # asks for a password
uv run nightshift hosted serve --data hosted-data                     # http://127.0.0.1:8080
```

Admins manage users, projects and secrets; staff edit tests and start runs. Passwords are scrypt
hashes, sessions are stored as hashes, every change needs a custom header and this site's origin,
five wrong passwords lock an address out for 15 minutes, secrets can't be read back, and report
files are served only from inside their own run's folder.

`nightshift run --parallel N` runs N specs at a time, each in its own browser, outside the hosted
app too: the 10 saved shop paths replay in 26 s instead of 55 s with `--parallel 3`.

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
  the demo shop with any of its 23 bugs planted
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

**A bigger model for free.** Ollama Cloud's free plan runs `gemma4:31b-cloud` through the Ollama app
you already have: run `ollama signin` once, then set `MODEL_NAME=gemma4:31b-cloud` (no API key).
On the benchmarks it got 24 of 25 reachable public sites right against 21 for the local 4B, at about
1 s a step (`research/part-c-results.md`, phase 1). The free plan's allowance isn't published; see
your usage at ollama.com/settings. Ollama says cloud prompts are not logged or trained on.

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

On every pull request, the GitHub Action runs the specs and leaves **one comment** on the PR (updated
on every push, not repeated): what passed, each failure with its cause (BUG, FLAKY, TEST_OUTDATED,
ENV_ISSUE) and reason, the defects, and a link to the reports, traces and bug reports.

```yaml
permissions:
  contents: read
  pull-requests: write
steps:
  - uses: actions/checkout@v4
  # start your app here, or set base-url to a preview deployment
  - uses: dhyey3333/QA-testing-using-AI-ML@main
    with:
      specs: specs/
      recordings: .nightshift/recordings   # commit this folder
      # Only needed for specs without a saved path, or when a saved path breaks:
      model-base-url: https://openrouter.ai/api/v1
      model-name: qwen/qwen3-vl-8b-instruct
      model-api-key: ${{ secrets.MODEL_API_KEY }}
```

Commit `.nightshift/recordings/`. A spec with a saved path is replayed and its recorded evidence
re-checked with **no model call**, so a PR check of saved paths needs no model, no GPU and costs
nothing. The agent (and the model) is only needed when the UI changes and a path must be healed;
with no model configured, those tests are reported as not tested (ENV_ISSUE) rather than passing.

This repository runs exactly that on its own pull requests ([.github/workflows/nightshift.yml](.github/workflows/nightshift.yml)):
the demo shop, its API tests, and its UI tests replayed from `ci/recordings/`. Run the workflow by
hand with bugs planted to see a failing report. For Jenkins or GitLab there's a `Dockerfile`.

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
demo_shop/      Kulhad & Co.: the development app, 23 planted bugs
holdout/        Sehat Clinic: the holdout app, 8 planted bugs, never tuned on
benchmark/      scores the tester against either app
```

## Known limits

- Visible iframes and open shadow roots are read and driven; closed shadow roots can't be. A step
  inside an iframe isn't saved for replay, so those flows use the agent every run.
- Clickable `<div>`s with no role, no tabindex and no inline handler are invisible to the element
  list, unless they look like a popup's close control.
- On the 4B model every agent step takes about 2 s, so a 14-step checkout takes about 40 s. Replay
  makes repeat runs cheap. A bigger hosted model is more accurate and not much slower.
- Firefox, WebKit, an iPhone and an Android phone are tested on the demo shop's login. The Docker
  image is wired up but untested.
- It checks what the page shows. It reads a test inbox for email codes and links, and an SMS inbox
  for text-message codes, but not the database or the payment provider. Authenticator-app codes are
  supported. A real SMS gateway needs a small adapter to Mailpit's message API.
