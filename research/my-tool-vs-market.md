# Nightshift vs the market: Part B

Date: 2026-10-02. This builds on `research/market-report.md` (Part A) and `research/competitors.csv`.

## Conclusions

- **Already better:**
  - Proof-before-pass verdicts: 0 false passes in 21 checked passes.
  - Cost per run: ₹0 locally, ₹0.17 on a hosted 8B model.
  - Open, portable tests: plain-English YAML plus Playwright export.
  - Fully private: runs on your own machine.
  - Requirements → traceability → Jira in one tool.
- **Behind:**
  - Reliability: 84% correct on 25 runs on working public sites. Every miss was the tester's mistake, not the judge's.
  - No mobile.
  - No iframes, so no payment widgets.
  - No parallel runs or PR comments.
  - No hosted team product.
  - No track record.
- **Recommended niche:** Indian QA service agencies, as a tool that makes their testers cover more client projects.

The full benchmark table, comparison table, niches and pitch follow.

## B1. How Nightshift works today

**Stack.**
- Python 3.11 with Playwright (sync API) as the browser driver: Chromium by default, Firefox and WebKit as options.
- No LLM framework. A plain HTTP client calls any OpenAI-compatible chat endpoint (`nightshift/model.py`).
- Default model: `qwen3-vl:4b-instruct` running locally in Ollama (a 4-billion-parameter vision model, on a 6 GB laptop GPU). `MODEL_BASE_URL` / `MODEL_NAME` switch it to vLLM, OpenRouter, Groq and others. The judge can use a separate model (`JUDGE_*`).
- Tests are YAML files.
- The core product is about 5,000 lines. There are 120 automated tests, all running against local apps with a scripted stand-in model, so no real model is needed.

**How a test is created** (four ways, all producing the same YAML spec):

| Way | What happens |
|---|---|
| Write it by hand | Plain-English `steps`, `expect` (what must be true at the end), and `data` (test values, typed as `{{placeholders}}`). |
| `nightshift generate` | Writes specs from a user story, or from a map of the app made by `nightshift explore`. |
| `nightshift validate` | Turns a requirements document into designed test cases (positive, negative, boundary) and links each one back to its requirement. |
| Dashboard | A form on the "new test" page. |

**How a test runs** (`runner.py`):
1. **Replay.** If an earlier run of the same spec passed, its path was saved with several ways to find each element: test id, then role and name, label, placeholder, text, and a CSS path last (`locators.py`). Replay walks that path with no model calls, which makes it fast and free. If a step can't be found (a renamed button or a redesign), the AI takes over from that step.
2. **Agent loop.** Each turn:
   - Nightshift reads the page (`observe.py`): visible text, plus up to 80 visible buttons and fields, each given a number, with red number badges drawn on a screenshot.
   - The model replies with one JSON action: click, type, select, press, scroll, wait, back, go to, pass or fail.
   - Code checks the action and runs it, then waits for the network to go quiet (`actions.py`).
   - A page fingerprint tells the model whether its last action changed anything. Three identical actions with no effect count as a dead control.
3. **The judge.** A pass counts only if, for every expected result, the model quotes text and code finds that quote on the page (`judge.py`). It also rejects:
   - made-up numbers
   - named values missing from the quotes
   - text claimed absent that is actually present

   It gets one chance to answer again.
4. **Browser signals fail a run by themselves:** an uncaught JavaScript error, or an HTTP 5xx from the app's own origin.
5. **Retries.** A failure is re-run by a fresh agent, and fail-then-pass is reported as `flaky`.
6. **Other kinds of test:**
   - **API specs** (`requests:`) are plain HTTP checks with no model.
   - **Email codes and magic links** are read from a Mailpit-style inbox or IMAP and typed in by code. The model never sees the inbox.

**How results are reported:**
- **Per test:** `report.html` (each step with its screenshot and the judge's evidence), `bug.md` (steps to reproduce, expected vs actual, suggested severity), `trace.zip` (Playwright trace), and `result.json`.
- **Per run:** an index page, a history page, JUnit XML, a GitHub Actions summary, and Slack.
- **Defect analysis** groups failures by cause and compares them with the last passing run.
- **Issue filing** to GitHub Issues, and to Jira (verified on a live Jira Cloud site).
- **Test-case CSV** and a **traceability matrix**.
- A **local dashboard**.
- `nightshift export` turns a saved path into a plain Playwright TypeScript test, so customers aren't locked in.

### Built-in assumptions that would break on other websites

From reading the code. Each is a constant or a design choice, not a bug in one site's handling.

| # | Assumption | Where | What breaks |
|---|---|---|---|
| 1 | The agent sees only the first **2,500 characters** of page text; the judge sees **6,000** | `prompts.py` `PROMPT_TEXT_LIMIT`, `JUDGE_TEXT_LIMIT` | On long pages (catalogues, articles, dashboards), an expected result below that point can't be proven: a false failure. The agent also misses content further down. |
| 2 | At most **80** buttons and fields per turn, on-screen first | `observe.py` `MAX_ELEMENTS` | Big menus, long product grids and long tables lose elements. |
| 3 | Reads the **main document only**: no iframes, no shadow DOM, no canvas | `observe.py` (`document.querySelectorAll`) | Payment widgets (Razorpay checkout, Stripe Elements) are iframes. So are embedded forms and many chat and support widgets. Web-component design systems (shadow DOM) and canvas apps (Flutter web, editors) are invisible. |
| 4 | Network "settled" means **quiet for 0.3 s, waiting at most 3 s**; actions time out after 5 s | `actions.py` `SETTLE_MAX_S`, `ACTION_TIMEOUT_MS` | On slow backends the agent reads the old page, decides nothing changed, and repeats the click. Sites with polling or live content never go quiet. |
| 5 | "Did my action change anything" hashes **all** page text | `observe.py` `fingerprint()` | Carousels, timers, ads and live feeds make every action look like it changed something, so dead buttons are never detected there. |
| 6 | **English** judge rules: capitalised words as names, negation words, reporting verbs; English Playwright error hints | `judge.py`, `actions.py` | Hindi or regional-language interfaces weaken the evidence checks. |
| 7 | Amounts recognised only with **₹ $ € £** | `judge.py` `_AMOUNT` | "Rs. 500" or "INR 500" is only checked as a bare number. |
| 8 | Every `confirm()`/`alert()` dialog is **accepted**, and its text goes to warnings, not the page | `checks.py` | A test can't take the "Cancel" path, and the judge can't quote an alert ("Product added"). |
| 9 | Server errors and 404s count only on the **start URL's origin**, and `goto` stays on that site | `checks.py`, `actions.py` | An app whose API is on another subdomain (`api.example.com`) has its 500s ignored. |
| 10 | **Desktop 1280×800** by default | `runner.py` `VIEWPORT` | Mobile layouts are tested only when `--device` is passed. |
| 11 | Test data is **fixed per spec** (only `${ENV}` expands) | `spec.py` | Signup tests fail the second time ("email already registered") unless the run supplies a fresh value. The benchmark works around this with `${NS_RUN}`. |
| 12 | Masking swaps any test value of 4+ characters for its placeholder **everywhere on the page** | `prompts.py` `mask()` | A common value such as a city name is hidden in unrelated text too. |
| 13 | **No actions** for file upload, hover menus, drag and drop, or switching into frames | `actions.py` | Upload forms, hover-only menus, kanban boards and payment iframes can't be driven. |
| 14 | **No reuse of a logged-in session**, and no authenticator-app (TOTP) or SMS codes | `runner.py`, `inbox.py` | Every test logs in from scratch (slow), and 2FA apps beyond email codes can't be tested. |
| 15 | **One test at a time**, one browser | `cli.py`, `runner.py` | 100 tests at ~30 s each take about 50 minutes. Competitors run in parallel in the cloud. |
| 16 | Bot protection and CAPTCHAs are **not handled**, by design | n/a | Sites behind Cloudflare challenges (for example demo.opencart.com) can't be tested from the outside. |

## B2. Benchmark: 27 runs on 11 public demo sites

**Method.**
- **Command:** `uv run python -m benchmark.public_sites`, run on 2026-10-02 from Bengaluru.
- **Model:** `qwen3-vl:4b-instruct`, local on an RTX 3050 (6 GB).
- **Setup:** one retry per failure, as `nightshift run` does. No saved paths, so every test is a fresh AI run.
- **Specs:** in `benchmark/public/`, written before the run and not tuned on these sites. Signups use fresh fake emails. No card or payment details were entered anywhere: the checkouts tested take a name and address only.
- **Checking verdicts:** none of these sites has planted bugs, so a correct result on a working site is a pass. I read every run (steps, screenshots, the judge's quotes) and marked whether its verdict was right.

**Results** (raw output in `runs/public-20261002000943/public-sites.md`):

| Site | Flow | Verdict | Right? | Time | Calls | ₹ at 8B hosted | ₹ at 235B hosted | What happened |
|---|---|---|---|---|---|---|---|---|
| saucedemo | login | pass | ✅ | 19 s | 5 | 0.12 | 0.25 | "Products" shown |
| saucedemo | form validation | pass | ✅ | 19 s | 4 | 0.09 | 0.19 | "Epic sadface: Username is required" |
| saucedemo | add to cart | pass | ✅ | 39 s | 8 | 0.21 | 0.42 | Backpack, $29.99 |
| saucedemo | checkout | pass | ✅ | 51 s | 13 | 0.34 | 0.67 | "Thank you for your order!" |
| the-internet | login | pass | ✅ | 23 s | 5 | 0.11 | 0.23 | |
| the-internet | form validation | pass | ✅ | 26 s | 5 | 0.12 | 0.23 | "Your password is invalid!" |
| practicetestautomation | login | pass | ✅ | 25 s | 5 | 0.13 | 0.26 | |
| practicetestautomation | form validation | pass | ✅ | 19 s | 5 | 0.14 | 0.28 | |
| expandtesting | login | pass | ✅ | 29 s | 5 | 0.15 | 0.31 | |
| expandtesting | signup | pass | ✅ | 30 s | 6 | 0.17 | 0.33 | "Successfully registered…" |
| expandtesting | form validation | pass | ✅ | 27 s | 6 | 0.17 | 0.34 | "Passwords do not match." |
| demoblaze | signup (+ login) | pass | ✅ | 78 s | 12 | 0.41 | 0.81 | "Welcome ns2026…" |
| demoblaze | add to cart | pass | ✅ | 35 s | 5 | 0.13 | 0.28 | Galaxy S6, 360 |
| parabank | login | flaky | ✅ final, label wrong | 33 s | 11 | 0.29 | 0.58 | First judge answer gave no quote; the retry passed. Labelled "flaky", which blames the site. |
| parabank | form validation | pass | ✅ | 13 s | 3 | 0.07 | 0.16 | |
| globalsqa | login | pass | ✅ | 19 s | 5 | 0.11 | 0.23 | "Welcome Harry Potter !!" |
| globalsqa | form validation (withdraw) | error | ❌ tester | 27 s | 16 | 0.39 | 0.78 | Model kept naming an element that doesn't exist |
| greenkart | search | pass | ✅ | 22 s | 4 | 0.13 | 0.27 | |
| greenkart | add to cart | fail | ❌ false alarm | 21 s | 8 | 0.26 | 0.52 | Clicked "Flight Booking" instead of the cart, then blamed the site |
| greenkart | checkout | flaky | ✅ final, label wrong | 20 s | 15 | 0.50 | 0.99 | Same mistake first; the retry placed the order |
| lambdatest playground | search | pass | ✅ | 27 s | 4 | 0.16 | 0.31 | |
| lambdatest playground | add to cart | pass | ✅ | 56 s | 8 | 0.31 | 0.60 | |
| lambdatest playground | signup | fail | ❌ false alarm | 47 s | 21 | 0.72 | 1.41 | Never ticked the privacy-policy box |
| automationexercise | search | pass | ✅ | 28 s | 4 | 0.16 | 0.30 | |
| automationexercise | add to cart | error | ❌ tester | 127 s | 24 | 1.07 | 2.07 | Clicked the search button repeatedly; every product's button says just "Add to cart" |
| automationexercise | signup | fail | ⚠️ site down | 25 s | 12 | 0.45 | 0.89 | Cloudflare 522 (origin timed out). Reported as a server bug. |
| demo.opencart.com | search | fail | ⚠️ blocked | 38 s | 12 | 0.27 | 0.55 | Cloudflare bot check. Reported as a failure. |

**Summary:**

| Measure | Result |
|---|---|
| Correct verdict, on the 25 runs where the site was up and reachable | **21 / 25 (84%)** |
| False alarms (blamed a working site) | 2 / 25 (8%) |
| Tester gave up (`error`) | 2 / 25 (8%) |
| False passes | **0 / 21** (every pass quotes real evidence) |
| Site outages or bot walls reported correctly as environment problems | 0 / 2 (both were called failures) |
| Time per test | median **27 s**, mean 34 s, max 127 s |
| Model calls per test | median 6 |
| Tokens per test | median ~14,000 |
| Model cost per test | **₹0 API cost** locally (electricity not counted). Hosted Qwen3-VL-8B: median **₹0.17** (mean ₹0.27). Hosted Qwen3-VL-235B: median **₹0.33** (mean ₹0.53). |
| All 27 tests | ₹7.19 (8B hosted) or ₹14.26 (235B hosted) |

**By flow** (reachable sites only):

| Flow | Right / tried | Misses |
|---|---|---|
| Login | 6 / 6 | 1 needed a retry |
| Form validation | 5 / 6 | Tester error |
| Signup | 2 / 3 | Tester skipped a checkbox |
| Search | 3 / 3 | |
| Add to cart | 3 / 5 | Both misses: identical "Add to cart" buttons |
| Checkout | 2 / 2 | 1 needed a retry |

**What the failures have in common.** All 4 wrong verdicts were the tester's mistakes, and none were the judge's:
- **Identical labels:** "Add to cart" ×N, with nothing saying which product a button belongs to.
- **Skipped steps:** an unticked checkbox, unused test data.
- **Wandering:** clicking an unrelated link.
- **Invalid element numbers.**

These are the same weaknesses found on the demo shop. The audit also explains why they weren't caught sooner: live page content hides "no change", so the dead-click detector never fires.

**How these costs compare to published prices.**
- **Momentic:** $125/month for about 1,000 runs, so about ₹12 per run.
- **QA Wolf platform:** $0.15 per runner-minute, so a 27-second run costs about ₹6.5 before AI credits.

Both of those include browsers and margin, and Nightshift's figure is model cost only. So the honest claim is that Nightshift's AI costs are low enough to price well below them, not that it already is cheaper end to end.

**Caveat.** This is one run per test. A pass rate from 27 runs moves a lot between runs. Part C reruns will show how much.

## B3. Feature comparison with the top 5

The verdict column is Nightshift against the five as a group. Competitor facts are from `research/competitors.csv`, and "not verified" means I found no source.

| Must-have | Nightshift today | QA Wolf | Momentic | testRigor | mabl | BrowserStack (AI) | Verdict | Why |
|---|---|---|---|---|---|---|---|---|
| **Trustworthy results** | A pass needs quoted proof found on the page; 0 false passes in 21 checked here. But 16% of runs on working sites ended wrong (8% false alarms). | "Guaranteed zero flakes" (managed), human-verified bugs | AI assertions; no public data | Reviews: flaky/false failures | Reviews: slow, some flakiness | Reviews: false positives from network issues | **Worse** | Proof-before-pass is a better design, but buyers judge the false-failure rate, and 8–16% is far from QA Wolf's guarantee or a <2% target |
| **Low maintenance** | Saved paths with test-id/role/label locators; AI takes over from a broken step, and the healed run is still judged | Human team maintains | AI self-healing | Plain English, less breakage | Auto-heal | Self-healing agent | **Same** | Everyone has healing. Nightshift's is checked by the judge, but that isn't proven at scale. |
| **Fits CI** | CLI exit codes, JUnit, GitHub Action, job summary. Never run in a real CI pipeline; no PR comment; no parallel runs. | Webhooks, CI, parallel | CI, PR testing | CI integrations | CI, unlimited concurrency | CI, parallel grid | **Worse** | No PR comments, no parallelism, Action unproven |
| **Hard flows** (OTP, 2FA, payments, API) | Email codes and magic links (tested); API tests. No SMS, no authenticator app, no iframes, so no Razorpay/Stripe widgets. | Humans handle anything | not verified | Built-in SMS, TOTP, email | API tests | n/a (you write the code) | **Worse** | testRigor covers SMS and TOTP; payment widgets need iframes |
| **Actionable bug reports, Jira/GitHub** | Steps, expected vs actual, severity, screenshots, Playwright trace; defects grouped; Jira (verified live) and GitHub Issues | Human-verified reports with video, traces, logs | not verified | not verified | Jira integration (not verified) | Test management | **Same** | Comparable output; QA Wolf's human check is stronger |
| **Predictable, low price** | Free locally; model cost ₹0.17–0.33 per run hosted | 1¢/AI credit + 15¢/runner-min; managed by quote | Free; $125/mo; credits | Not published (~$900/mo third-party) | Quote (~$450/mo third-party) | From $59/mo per parallel | **Better** | Lowest cost per run by a wide margin (model cost basis) |
| **Platforms** | Web + API | Web; managed adds iOS, Android | Web, Android, iOS | Web, mobile, desktop, API | Web, mobile, API | Web + real devices | **Missing** (mobile) | No native mobile at all |
| **Owning your tests** (lock-in) | YAML in your repo; export to Playwright; open source | Playwright code | No export | No export | Proprietary | Your own code | **Better** | Only one with open source + plain-English + Playwright export |
| **Speed** | 27 s median per test, one at a time; replay ~8–17 s | Unlimited parallel | 25 browsers in parallel (paid) | Parallel by plan | Unlimited concurrency | Parallel by plan | **Worse** | No parallelism |
| **Tests from requirements + traceability** | `validate`: requirements → designed cases → traceability matrix + test-case CSV | Done by their team | not verified | not verified | not verified | Test-case generator from PRDs | **Same** | BrowserStack and TestMu also generate from PRDs |
| **Exploratory testing** | `explore` exists; weak on the 4B model | Their team | not verified | not verified | not verified | not verified | **Worse** | Weakest feature in earlier tests |
| **Data stays private** | Model and browser run on your machine | SaaS | SaaS | SaaS | SaaS | SaaS | **Better** | Only fully local option |
| **Visual regression** | None (text only); basic accessibility warnings | not verified | not verified | not verified | Accessibility testing | Percy (separate) | **Missing** | |
| **Team product** (hosted, multi-user, SSO, support) | Local single-user dashboard | Full service | Yes | Yes | Yes | Yes | **Missing** | |
| **Track record** (customers, reviews) | None | 4.8/5 on G2 (182 reviews) | Funded, claimed usage | Named customers (unverified) | Named customers | Market leader | **Missing** | |

## B4. Where Nightshift stands

### Where it's already better
1. **A pass has to be proven.** The judge must quote the page, and code checks the quote. In this benchmark, 0 of 21 passes were false.

   The market's #1 pain is not trusting results. The usual answer, self-healing, can turn a real bug into a pass. Nightshift's design does the opposite, and it can be demonstrated.
2. **Cost per run.** Model cost is ₹0 locally and ₹0.17 per run on a hosted 8B model. That leaves room to undercut every published price in A1.
3. **No lock-in, and private.** Tests are plain-English YAML in the customer's repo, export to Playwright, and the whole thing runs on their own machine.

   Momentic, testRigor and Virtuoso can't offer the first. None of the five offers the second.
4. **The whole QA workflow in one tool.** Requirements → designed test cases → runs → traceability matrix → grouped defects → Jira. That's the paperwork QA teams and agencies produce by hand.

### Where it's behind, and how hard each gap is to close

| Gap | Evidence | Difficulty |
|---|---|---|
| Wrong outcomes from tester mistakes (16% on public sites) | B2 | **Medium.** Fixes for each cause are known: product context on buttons, a reminder about unused data and steps, better dead-click detection. A bigger hosted model is one setting, but needs a benchmark run. |
| Site outages and bot walls reported as bugs; "flaky" blames the site | B2 | **Easy** |
| No native mobile | B3 | **Hard** (a new driver, such as Appium or Maestro; months) |
| Can't see inside iframes or shadow DOM, so no payment widgets | B1 | **Medium** |
| No authenticator-app or SMS codes | B3 | TOTP **easy**; SMS **medium** (Twilio) |
| No parallel runs | B2 | **Medium** |
| No PR comments in CI | B3 | **Easy** |
| No hosted team product | B3 | **Hard** |
| No visual regression | B3 | **Medium** |
| No track record | B3 | Takes time and pilots |

### Three niches where it could win

| | 1. Indian web startups and D2C brands with no QA hire | 2. Indian QA service agencies | 3. Privacy-first teams (fintech, BFSI, health) |
|---|---|---|---|
| **Target customer** | 5–50 engineer startups and Shopify/WooCommerce brands with a web checkout | Agencies of 10–200 testers selling manual and regression testing at $15–35/hour | Mid-size companies whose security review blocks sending screens and data to a US SaaS |
| **Pain existing tools handle badly** | USD prices, often hidden behind sales calls; nothing for Razorpay/UPI test mode; nobody to maintain scripts | Regression runs and report writing eat billable hours. Clients demand test cases, traceability matrices and defect reports, which today's AI tools don't produce. Proprietary test formats can't be handed to the client. | Every tool in A1 is cloud SaaS; on-premise options exist only in enterprise tiers |
| **Suggested INR pricing** | Free / ₹2,999 / ₹9,999 / ₹24,999 a month (A4). Note: TestSprite ($19, ₹1.8k) is already cheaper at the bottom. | ₹4,999 per tester seat a month (about 1.5–3.5 billed tester-hours), or ₹49,999 a month for an agency with up to 15 testers, with white-label reports | ₹6–12 lakh a year per organisation, on-premise, with support |
| **Main risk** | Needs hands-off reliability, and today's 16% would break trust. UPI demand is unproven. | Automation cuts into the hours agencies sell, so it fits fixed-price contracts best | Long sales cycles; needs SSO, audit logs and support |

### My one recommendation: niche 2, Indian QA agencies

**Why:**
1. **It fits the product as it is today.** An agency tester reviews every result, so an 84% hands-off success rate is a productivity gain rather than a broken promise. Niche 1 would hold the same 16% against Nightshift.
2. **It uses what's unique.** The traceability matrix, test-case documents, grouped defects and Jira filing are exactly what agencies deliver to clients. Plain-English YAML plus Playwright export means the client can take the tests with them.
3. **It's away from funded competitors.** They sell to product companies. Service firms are their competitors, not their customers.
4. **The price anchor is generous.** A tester-hour sells for $15–35 (₹1,400–3,400). A ₹4,999 seat pays for itself if it saves 1.5–3.5 hours a month.
5. **Distribution multiplies.** One agency means many client projects.

**Validate before building more:** talk to 10 agencies, and run one pilot on one client's regression suite for a month. Measure hours saved and wrong-verdict rate. If agencies won't adopt because it cuts billable hours, fall back to niche 1. The Part C reliability work serves both.

**Pitch:** *"Nightshift turns your testers' written test cases into automated runs on every client release: every pass is proven with evidence, and every failure comes back as a Jira-ready bug with a traceability matrix, so each tester covers more client projects without writing a single script."*

## Sources

**Benchmark data:**
- `runs/public-20261002000943/public-sites.md` and `.json`: per-test results, screenshots and traces. The specs are in `benchmark/public/`.

**Model prices:**
- OpenRouter's public model list, https://openrouter.ai/api/v1/models, fetched 2026-10-01.
- qwen/qwen3-vl-8b-instruct: $0.117 per million input tokens, $0.455 output.
- qwen/qwen3-vl-235b-a22b-instruct: $0.21 input, $1.90 output.

**Exchange rate:**
- ₹96 per USD (96.11 on 2026-09-30), https://tradingeconomics.com/india/currency

**Competitor facts:**
- `research/competitors.csv`, with source URLs per row. Mainly:
  - https://www.qawolf.com/pricing
  - https://momentic.ai/pricing
  - https://www.mabl.com/pricing
  - https://www.browserstack.com/pricing
  - https://www.g2.com/products/testrigor/reviews?qs=pros-and-cons
  - https://testrigor.com/how-to-articles/how-to-do-sms-2fa-and-phone-call-testing-using-testrigor/
  - https://getautonoma.com/blog/testrigor-pricing (unverified)
  - https://www.drizz.dev/post/mabl-testing (unverified)
  - https://www.g2.com/products/qa-wolf/reviews?qs=pros-and-cons
  - https://www.browserstack.com/press/browserstack-launches-suite-of-ai-agents-to-redefine-software-quality-at-scale
  - https://www.testmuai.com/pricing/
  - https://www.testsprite.com/pricing

**QA agency rates:**
- https://www.qamentor.com/packages-prices/economy-package/
- https://www.vervali.com/blog/how-much-does-qa-outsourcing-to-india-cost-in-2026-pricing-by-role-city-and-engagement-model/
