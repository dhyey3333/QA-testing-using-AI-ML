# Part C: results after each step

Every step is measured three ways, on the same laptop (RTX 3050, local `qwen3-vl:4b-instruct`):

1. **Public demo sites** (`python -m benchmark.public_sites`): 27 tests on 11 sites, one retry per failure, every verdict checked by hand. This is the B2 benchmark.
2. **Demo shop** (`python -m benchmark`): 10 tests on the bug-free shop, where a failure is a false alarm, and 21 planted bugs.
3. **Holdout clinic** (`python -m benchmark --app clinic`): 5 clean tests and 8 planted bugs. Never tuned on; run only to report.

Model cost is ₹0 locally. Hosted prices are OpenRouter's published Qwen3-VL prices at ₹96/USD.

The baseline is B2 in `my-tool-vs-market.md`: 21 of 25 runs on reachable sites correct, 0 false passes, median 27 s, ₹0.17 a run at hosted 8B.

## Step 1: any website

**What changed:**
- **Buttons with the same label carry their item's name.** "Add to cart" becomes "Add to cart — Blue Top", using the nearest product card or table row.
- **Test data is masked as whole words only.**
- **A wrong button number is answered with the real ones.**
- **Three identical actions in a row get a nudge.**
- **The judge reads 12,000 characters** instead of 6,000.
- **`run --url --goal`** takes a URL and a plain-English goal.
- **Every pass is saved as a Playwright test.** A rerun that finds the recorded evidence again passes with no model call.

**Public demo sites** (`runs/public-20261002011747`), checked by hand:

| | Before (B2) | Step 1 |
|---|---|---|
| Sites reachable | 25 runs (one site down, one bot wall) | 26 runs (one bot wall) |
| Correct verdicts on reachable sites | 21 / 25 (84%) | **23 / 26 (88%)** |
| False alarms (blamed a working site) | 2 (8%) | **1 (3.8%)** |
| Tester gave up (`error`) | 2 | 2 |
| False passes | 0 | **0** (every pass quotes real evidence) |
| Flaky | 2 | 1 |
| Median time, fresh run | 27 s | 27 s |
| Model cost per fresh run (median) | ₹0.17 (8B hosted) / ₹0.33 (235B) | ₹0.16 / ₹0.32 |
| **Rerun from the saved path** | n/a | **20 / 20 pass, median 6 s, 0 model calls, ₹0** |

**What the fixes did:**
- **Fixed:** GreenKart add-to-cart. It was clicking the wrong "ADD TO CART" before; it now passes.
- **Still wrong:**
  - **LambdaTest signup** never ticked the privacy box (step 2 targets this).
  - **GlobalSQA withdraw:** the model asks for a button [9] that doesn't exist even when told the real ones. A 4B-model quirk.
  - **automationexercise add-to-cart** failed because of a regression from this step. Product names in the labels pushed one prompt to 4,119 tokens, over local Ollama's 4,096 limit.
- **The regression also exposed an older bug.** The rejected request was taken as "this server doesn't support JSON mode", which switched JSON mode off for the rest of that run. So 26 of these 27 tests ran without JSON mode, and these step 1 numbers are, if anything, pessimistic. Both are fixed in step 2 (D25).
- **One flaky result:** GreenKart checkout's first attempt invented a URL (`/greenkart-checkout`). The retry passed.

**Demo shop:**
- Planted bugs caught: **21 / 21** before and after.
- False alarms on the clean shop: **2 / 10 → 1 / 10.**
- The one left is the known `checkout-validation` step-skip. It's a single run each, so this is within normal variation, not proof of a gain.

**Holdout clinic:** **8 / 8** bugs caught, **0 / 5** false alarms.

**Known limits after step 1:**
- Iframes, shadow DOM and payment widgets are still not read.
- Two of four public misses are 4B model quirks (an invented element number, an invented URL).
- Signup flows can't be rerun from a saved path, because the same account can't register twice.

## Step 2: reliability

**What changed:**
- **Every result that isn't a pass is labelled** BUG, FLAKY, TEST_OUTDATED or ENV_ISSUE. Gateway errors (502/503/504/52x), bot checks, unreachable sites and an unreachable model are `error` + ENV_ISSUE, never a failure of the app.
- **Checks before a verdict:** test data never typed, a field typed into but never submitted, and one second look before a failure stands.
- **One automatic retry** for an action that failed for timing reasons.
- **The token-limit and JSON-mode bug from step 1 is fixed** (D25).

**Step 2b** came out of step 2's own benchmark, where the model still left forms half filled:
- The prompt marks each test value "(typed)" or "(not typed yet)".
- A submit button that "does nothing" is first checked for empty fields or unticked boxes in its form.
- The checks no longer use up the step budget.

**Public demo sites**, checked by hand:

| | Before | Step 1 | Step 2 | Step 2b |
|---|---|---|---|---|
| Runs on reachable sites | 25 | 26 | 26 | 24 (the-internet was down) |
| Correct verdicts | 21 (84%) | 23 (88%) | **24 (92%)** | **22 (92%)** |
| False alarms | 2 (8%) | 1 (3.8%) | 1 (3.8%) | 1 (4.2%) |
| Tester gave up | 2 | 2 | 1 | 1 |
| False passes | 0 | 0 | **0** | **0** |
| Outages and bot walls labelled ENV_ISSUE | 0/2 | 0/1 | **1/1** | **3/3** |
| Median time, fresh run | 27 s | 27 s | 34 s | 28 s |
| Model cost per run (median, 8B / 235B hosted) | ₹0.17 / ₹0.33 | ₹0.16 / ₹0.32 | ₹0.17 / ₹0.34 | ₹0.16 / ₹0.31 |

**Demo shop:**

| | Before | Step 1 | Step 2 | Step 2b |
|---|---|---|---|---|
| Planted bugs caught | 21/21 | 21/21 | 20/21 | **21/21** |
| False alarms on the clean shop | 2/10 | 1/10 | **0/10** | 1/10 |

- **Step 2's miss:** pincode-accepts-5 ran out of steps. It had been "caught" only by the same half-filled-form mistake that caused the clean-shop false alarm.
- **Step 2b catches it for the right reason:** the order goes through with a 5-digit pincode, and the error message is missing.

**Holdout clinic:** 8/8 bugs and 0/5 false alarms after step 2; 8/8 and 1/5 after step 2b. Reported as is; nothing is tuned on the holdout.

**What the remaining public and shop misses were** (all tester errors, none from the judge):

| Where | What the tester did |
|---|---|
| Shop checkout-validation (2b) | Replaced the spec's 5-digit `{{bad_pincode}}` with a made-up `411000`, then reported the order going through as a bug |
| LambdaTest signup (2b) | Copied the masked `********` from the password field into "Password Confirm", then waited three times, which counted as a dead control |
| GlobalSQA withdraw | Kept naming a button that doesn't exist (4B model quirk) |

**Fixed after 2b, measured with step 3:**
- A masked value copied off the page, or a made-up value typed into a field that already got test data, is refused.
- Waiting or scrolling never counts as a dead control.

**Reading these numbers:** the clean-app samples are small (10 shop, 5 clinic, about 25 public runs). One run moves a rate by 4–20 points, so the step-to-step differences are within noise except where a cause was fixed and its run then passed. The stable results across all four runs are:
- 0 false passes
- every planted bug caught for a stated reason
- environment problems labelled as such from step 2 on

The under-2% false-failure target has not been reached. Every remaining case is the 4B model making a mistake. A larger model is a configuration change, not code, and is the obvious next lever. It needs a hosted endpoint.

## Step 3: hard flows

**What changed:**
- **Visible iframes are read and driven**, including cross-origin ones.
- **A stand-in test-mode payment gateway** in the demo shop, using Razorpay's published test UPI IDs, plus the planted bug `payment-failure-ignored`.
- **`{{totp_code}}` for authenticator apps**, checked against the RFC 6238 test vectors.
- **`nightshift edge-cases`:** empty, wrong-format and too-long variants of every typed value.

**Results:**
- **Demo shop:** 0 / 12 false alarms. The two new payment tests passed with the real 4B model, which paid inside the cross-origin iframe. 21 / 22 planted bugs caught, including `payment-failure-ignored`.
- **The one miss, `cart-link-404`, was a step 2 side effect.** The second look sent the agent back and forth until it ran out of steps. Fixed: a failure that reproduces after the second look stands. Rerun: caught in 6 steps.
- **Holdout clinic:** 8 / 8 bugs, 0 / 5 false alarms. One run ended ENV_ISSUE when the local model server crashed.
- **Public sites:** 21 / 25 correct (84%), 3 false alarms, 0 false passes. No public site had an iframe read, so step 3's code didn't change behaviour there. The misses are the usual 4B-model slips: an ad covering a button, a wander to an unrelated page, a box left unticked.
- **Timing isn't comparable.** Runs were slower because the local model server was slow and crashed once, and my own test runs overlapped part of the shop and clinic runs.
- **Edge cases on saucedemo:**
  - The first try hit an internet outage: 9 of 10 runs were correctly labelled ENV_ISSUE.
  - The one that ran was a false alarm caused by my wording ("this does not happen: …"). The model misread it.
  - Reworded to a single expectation ("an error message about the first name is shown"), the "first name empty" case passes, because saucedemo does refuse it.

## Step 4: reports

- **`client-report.html`**, written by `nightshift client-report` or `run --client --brand`:
  - one self-contained file with screenshots embedded, for a QA agency to send its client
  - a release recommendation, defects with severity, steps to reproduce, screenshot and ticket link
  - requirements coverage, and every result
  - white-label with `--brand`
- **Jira and GitHub filing now record each ticket**, so the report links to it.

## Step 5: CI

- **The Action no longer requires a model.** Saved paths replay with no model call (D20), so a pull-request check of saved paths needs no GPU and costs nothing.
- **One results comment per PR**, updated on every push, with bugs first.
- **This repo's own workflow runs on its PRs:** the demo shop, its API tests, and its UI tests from `ci/recordings`.
- **Verified on GitHub:** PR #1 passed 13 of 13 (3 API tests and 10 UI tests replayed from `ci/recordings`), with 0 model calls and a job time of about 1 minute.
  - The bot comment read: "✅ 13 passed · 10 replayed from saved paths with no model call · 0 model calls in all".
- **Two fixes came out of that run:**
  - A replay now tolerates a value that changes on every run, like a new order number, but still fails on a changed total.
  - The demo shop starts without `uv run`. A running `uv` process held the cache lock, so setup-uv's end-of-job cleanup waited 5 minutes and then failed the job.
- **Not yet replayed in CI:** the two payment specs, because a saved path can't act inside an iframe yet.

## Phase 1: a bigger model, for free

**Model:** `gemma4:31b-cloud` (Gemma 4 31B, about 8x the local 4B) through Ollama Cloud's free plan. It runs through the Ollama app you already have, after `ollama signin`, with `MODEL_NAME=gemma4:31b-cloud`. No API key goes into Nightshift. Ollama says cloud prompts are never logged or trained on.

- **Which cloud models are free:** I tried six cloud vision models. Gemma 4 was the only one included in the free plan; GLM 5.3 Flash, DeepSeek V4.1 Flash, Kimi K3 and MiniMax M3 returned HTTP 402, and Mistral Large 3 had no cloud manifest.
- **Other free options:** OpenRouter's free models allow 50 requests a day without bought credits, and a full benchmark takes about 1,000 calls. Gemini's free daily limit is unclear (reports range from 20 to 1,500).
- **No code changed.** These are the same specs and the same code as step 5.

| | Local 4B (step 3) | Gemma 4 31B cloud |
|---|---|---|
| Public sites: correct, on reachable sites | 21 / 25 (84%) | **24 / 25 (96%)** |
| Public sites: false alarms | 3 | 1 |
| Shop: false alarms | 0 / 12 | 0 / 12 |
| Shop: planted bugs caught | 21 / 22 | **22 / 22** |
| Clinic (holdout): bugs caught | 8 / 8 | 8 / 8 |
| Clinic (holdout): false alarms | 0 / 5 | 1 / 5 |
| **False alarms, all 42 clean runs** | 3 (7.1%) | **2 (4.8%)** |
| False passes | 0 | **0** (every pass's quote checked) |
| Median run time: public / shop / clinic | 45 s / 44 s / 50 s (slow-server day; 27 s public in B2) | **22 s / 16 s / 16 s** |
| Model time per step | about 2 s | about 1 s |
| Median tokens per run: shop / clinic | 18,367 / 29,129 | 10,807 / 16,307 |
| Model cost | ₹0 | ₹0 on the free plan; ₹0.12 to ₹0.25 a run at hosted Qwen3-VL rates |

**Target: under 2% false alarms. Not met (4.8%). The two false alarms:**

1. **`lambdatest-signup` (public site).** It has failed in every benchmark run, on both models. The agent can't tick the "I agree to the Privacy Policy" box, which is a styled checkbox whose real input is hidden, so Register never goes through. Gemma said so in its own thought, then clicked the wrong element. This is a tool gap, not a model slip, and it belongs in phase 2 (real sites).
   - **Also seen in this run:** one Gemma reply was cut off at the 300-token reply limit (a long "thought"), so its JSON was incomplete.
2. **`cancel-appointment` (holdout clinic).** The judge saw the list was empty, but the code couldn't confirm the absence on the page, so the check failed. It's the holdout set, so it's reported, not tuned. The same kind of check ("Brocolli is not shown") passed on greenkart.

**Not counted as false alarms:**
- **opencart:** landed on a bot check, labelled ENV_ISSUE.
- **the-internet:** one page-load timeout (ENV_ISSUE) and one timeout that passed on retry (FLAKY).

**Limit:** Ollama doesn't publish how many free credits there are or how much a run uses, so I don't know how many full benchmarks a month the free plan covers. The usage page at ollama.com/settings shows it.

## Phase 2: real sites

**What changed (commit a5517c0, 171 tests pass):**
- **Shadow DOM:** controls and text inside open shadow roots are read and driven, and saved paths find them again.
- **Styled checkboxes and radios:** listed by their label when the real input is hidden.
- **Popups:** close controls that are plain elements ("×", `<p>Close</p>`) are listed. A covered click names what covers it. The agent is told a popup is not a bug.
- **Phone and OTP logins:** six-box OTP inputs get one digit per box. Fields that format as you type are typed key by key. `{{sms_code}}` reads an SMS inbox. The demo shop gained a mobile-number login and the planted bug `phone-spaces-rejected`.
- **Staying logged in:** `session_from: <spec>` starts a test logged in. The session (cookies, localStorage and sessionStorage) is kept in memory only.
- **JS errors:** `js_errors: warn` per spec, or `--js-errors warn` per run, makes background JS errors warnings.
- **Agent replies** may be 600 tokens long, up from 300.

Same model as phase 1 (Gemma 4 31B, Ollama Cloud free plan), same benchmarks, plus four new practice specs:

| | Phase 1 | Phase 2 |
|---|---|---|
| Public sites (the original 27): correct on reachable sites | 24 / 25 (96%) | **24 / 24 (100%)** |
| `lambdatest-signup` (hidden privacy checkbox) | failed, as on every model before | **passes** |
| New practice specs: Polymer shop (shadow DOM), popup, JS-error page, logged-in cart | n/a | **4 / 4 pass** |
| Shop: planted bugs caught | 22 / 22 | **23 / 23** (with `phone-spaces-rejected`) |
| Shop: false alarms | 0 / 12 | 0 / 13 (with `login-with-phone`) |
| Clinic (holdout): bugs / false alarms | 8 / 8, 1 / 5 | 8 / 8, 1 / 5 |
| **False alarms, all clean runs** | 2 / 42 (4.8%) | **1 / 46 (2.2%)** |
| False passes | 0 | **0** (every pass's quote checked) |
| Median run time: public / shop / clinic | 22 s / 16 s / 16 s | 21 s / 14 s / 17 s |

**Notes:**
- **Unreachable sites:** opencart (a bot check) and the-internet's login pages (timed out; curl timed out too) were labelled ENV_ISSUE and are not counted.
- **The popup spec** timed out the same way in the main run. Rerun once the page answered, it passed in 10 s: the agent clicked the plain `<p>Close</p>`, and the judge checked "THIS IS A MODAL WINDOW" was gone.
- **The one false alarm left is the holdout clinic's `cancel-appointment`,** the same check in both Gemma runs. The judge says the list is empty, but its "no longer listed" claim can't be confirmed in code. The rule says report the holdout, never tune on it, so it stays. A fix has to come from a non-holdout case of the same pattern.
- **Target, under 2% false alarms:** 2.2%, so just short. That's one false alarm in 46 runs, and it's the holdout one.

**Phase 2 on practice sites:**
- **Shadow DOM:** before phase 2, Polymer's shop showed 0 elements and 0 text. Now its add-to-cart passes, quoting "Size: M" and "Total: $50.20".
- **Staying logged in:** `saucedemo-logged-in` ran with no login steps.
- **JS-error page:** passed, with the onload error kept as a warning.
- **Phone and OTP:** no public practice site sends real SMS, so this was tested on the demo shop only. A clean pass, plus the planted bug caught.

## Phase 3: hosted product

**What was built (commits 0cb8e61 to the ad-blocking fix, 188 tests pass):**
- **`nightshift hosted serve`:** a web app for a QA agency.
  - Staff logins, with admin and staff roles.
  - One project per client: tests edited in the browser, saved paths, write-only secrets and a nightly time.
  - Runs in the background, each ending with a client report.
  - An "Explore a website" box that needs no tests.
  - Built from the standard library only: SQLite and scrypt, behind Caddy for HTTPS.
- **`nightshift run --parallel N`:** each worker gets its own browser and model client. The 10 saved shop paths replay in 26 s instead of 55 s with 3 at a time.
- **A busy model is waited for:** the client retries on 429 and 503, since Ollama Cloud's free plan answers one request at a time.
- **Hosting:**
  - `deploy/oracle/` is ready: a setup script, systemd service, Caddyfile and guide.
  - **Not deployed:** Oracle's free plan needs card details, and the user chose not to give any.
  - **Free alternative used instead:** the user's laptop, plus a Cloudflare quick tunnel (no account) for a public link.
- **Ads are blocked** in test browsers (D39). This was found by this phase's benchmark, below.

**Bugs found by using it for real, all fixed:**
- **A relative `--data` folder** broke every run.
- **A refused request** was sometimes seen as a dropped connection.
- **A bot firewall's block page** (Spree's demo, on Vercel) was reported as a bug in the app. It's now ENV_ISSUE.

**On real e-commerce platforms' public demos, through the hosted app:**

| Site | Result |
|---|---|
| Saleor (demo.saleor.io) | Add-to-cart **passed**, after `js_errors: warn`. The homepage throws "Minified React error #419" in the background on every load. |
| Medusa (next.medusajs.com) | **Real bug caught, 6 runs:** adding the hoodie got an HTTP 500 from the server, and the cart stayed at 0. Products are out of stock, and the server crashes instead of saying so. |
| Spree (demo.spreecommerce.org) | Blocked by Vercel's firewall. Now reported as ENV_ISSUE, correctly. |
| AcademyBugs (a store with planted bugs) | **Explore, 25 actions:** 3 suspected bugs. One is the wrong grand total, $123.13 for $15.14 + $7.99 shipping. Turned into a test, it **fails as a BUG** on 2 of 2 runs, with the arithmetic in the judge's proof. |

**Benchmarks (Gemma 4 31B, Ollama Cloud free plan):**

| | Phase 2 | Phase 3 |
|---|---|---|
| Shop: bugs caught / false alarms | 23 / 23, 0 / 13 | 23 / 23, 0 / 13 |
| Clinic (holdout): bugs / false alarms | 8 / 8, 1 / 5 | 8 / 8, 1 / 5 (the same `cancel-appointment`) |
| Public sites: correct on reachable sites | 27 / 27 | 26 / 27 in the run; **27 / 27** after the ad fix |
| False passes | 0 | 0 |
| Median run time: public / shop / clinic | 21 s / 14 s / 17 s | 21 s / 13 s / 17 s |

- **The public-site miss** was `automationexercise-add-to-cart`: a full-page Google ad covered the page.
  - **After blocking ads,** all three automationexercise specs passed: add-to-cart 21 s, search 16 s, signup 32 s.
  - **Not rerun:** the other 28 specs. Nothing else in them touches ads.
- **Unreachable this run (ENV_ISSUE, not counted):** opencart (bot check) and the-internet's three pages (Heroku timed out).
- **False alarms over all clean runs:** 1 / 45 (2.2%) with the ad fix, the holdout one. The under-2% target is still just missed.

## Product: tests without YAML

**What changed (commits 4644353 to 194beee, 193 tests pass):**
- **Tests are a form in the hosted app:** website, steps, what should happen, and test data. YAML is one click away.
- **"Generate tests with AI":** it explores a site, then writes draft tests. A person reviews, edits and accepts them.
- **A run that would stop on a missing secret is refused first,** with the test and the secret named.
- **The judge can quote form fields:** what a field holds is added as `[field] <label>: <value>` lines (D41).
- **Both models are told** that a `{{placeholder}}` on the page is the real value, hidden on purpose (D42).

**End to end on AcademyBugs, a practice store with planted bugs:**
- "Generate tests" explored the site for 15 actions and wrote 3 sensible drafts in 95 s: quantity update, checkout, and sorting.
- I reviewed and accepted them in the UI, then ran them:
  - **First run:** 2 passed and 1 failed. The failure was "the quantity field shows 2", which the judge saw but couldn't quote.
  - **After the `[field]` lines:** 3 of 3 passed. Two of them replayed their saved paths with no model calls.

**Benchmarks after both changes (the judge's prompt changed twice, so all three were rerun):**

| | Before (phase 3) | After |
|---|---|---|
| Shop: bugs caught / false alarms | 23 / 23, 0 / 13 | 23 / 23, 0 / 13 |
| Clinic (holdout): bugs / false alarms | 8 / 8, 1 / 5 | 8 / 8, 1 / 5 (the same `cancel-appointment`) |
| Public sites: correct on reachable sites | 27 / 27 | **28 / 28** |
| False passes | 0 | 0 (every pass has quoted proof, checked in code) |
| Median run time: public / shop / clinic | 21 s / 13 s / 17 s | 17 s / 14 s / 16 s |

- **The benchmark after the `[field]` change alone** failed `demoblaze-signup`. The judge read the masked "Welcome {{username}}" literally. That's the reason for D42, and it passes now.
- **`parabank-login` failed correctly:** on both tries, Parabank's own server answered HTTP 500 with "Error! An internal error has occurred" after logging in. The site was broken; that's a true BUG, not a false alarm.
- **Unreachable (ENV_ISSUE, not counted):** opencart (bot check) and two of the-internet's pages (timeouts).

## Phase 4: go-to-market

Four documents in `research/go-to-market/`:

- **`pitch.md`:** one page for an agency's owner or delivery head.
  - Covers the problem, what Nightshift does, the measured numbers and the real bugs it found on platforms' public demos.
  - Says what it doesn't do yet, and makes the pilot offer.
- **`outreach.md`:**
  - A LinkedIn connection note and message, a cold email, a WhatsApp message and two follow-ups.
  - Answers to six usual objections, and a 15-minute demo script.
- **`pilot-plan.md`:** a free 30-day pilot on one client's staging site.
  - Day-by-day roles, and a baseline to record on day 0.
  - Six success metrics read from the app's run history: hours saved ≥ 50%, at least one real bug, false alarms ≤ 5%, false passes = 0, nightly reliability ≥ 90%, and at least 2 client reports sent.
  - Decision rule: 4 of the 6 must be met, including zero false passes.
- **`pricing.md`:** Agency Starter ₹14,999/month (5 testers, 3,000 AI runs), Pro ₹34,999 (15 testers, 10,000 AI runs), and Scale from ₹69,999.
  - Unlimited projects and replays on every plan.
  - Includes the measured cost to serve, margins, and a checklist for before charging anyone.

**Every number in them comes from this file's benchmarks.** "Almost 400 runs, zero false passes" is the 394 runs counted across the five benchmark rounds on Gemma.

**Features marked as command-line only, to build into the web app before selling Pro:** Jira and Slack filing, and the traceability matrix. SSO isn't built.

**No product code changed in phase 4,** so the tests (193 pass) and the latest benchmarks (commit c11c768) still stand.

## Closing the gaps against the market: visual checks and why it failed

Chosen by the user from the comparison with BlinqIO, testers.ai, Mabl, Katalon, Applitools, ACCELQ, BrowserStack, Testim, TestMu KaneAI, TestResults.io, Tricentis and Parasoft. The two gaps to close first:
- **Visual checks:** against Applitools and Mabl.
- **Root-cause analysis plus Jira/Slack:** against BrowserStack.

**Visual checks (`nightshift/visual.py`, D43):**
- **When it runs:** after a passing test with saved paths, the final screen is compared with its approved look, in the test's own browser.
- **What the model sees:** a real change goes to the model as clean screenshots, zoomed in and stacked when the change is small. The model says whether a person would call it broken.
- **Who decides:** a person approves every new look.

| Visual benchmark (`python -m benchmark.visual`, Gemma 4 31B) | Correct |
|---|---|
| First version (red change boxes, half-size, 0.2% threshold) | 4 / 6 |
| Clean pictures for the model | 5 / 6 |
| Plus zoom, stacking and catching small concentrated changes | **6 / 6, twice** |
| **Held-out page** (another design, other bugs, never tuned on) | **4 / 5**: it missed form fields drawn over their labels |

**End to end in the web app:**
1. The approved look was saved on the first run.
2. Then prices were printed white on white.
3. The test still passed its text checks with no AI (a replay in 1.2 s).
4. The visual check reported "the price for 'Filter Coffee' is missing", using one model call (614 tokens).
5. The run details page offers **Accept the new look**.

**Why it failed (`nightshift/cause.py`, D44):**
- **One sentence per failure,** built only from recorded evidence. The app's refused 4xx API calls are now recorded too.
- **Checked against all 23 planted-bug runs of the last shop benchmark.** Examples:
  - "The server crashed on POST /api/order (HTTP 500)"
  - "“Place order” can't be used: <div class="promo-layer"> sits on top of it"
  - "Incorrect order total: expected ₹240, but got ₹290"
  - "Adding 'Clay Kulhad (set of 6)' to the cart does nothing"
- **On public sites:**
  - "The server crashed on GET /parabank/overview.htm (HTTP 500)"
  - "Not the app: the site didn't load within 30 seconds"

**Web app:** each run has a details page listing every test with its result, probable cause and visual check. It has buttons for Accept the new look, File bugs in Jira and Post to Slack, which use the project's secrets.

**Benchmarks after both:**

| | Before | After |
|---|---|---|
| Shop: bugs / false alarms | 23 / 23, 0 / 13 | 23 / 23, 0 / 13 |
| Clinic (holdout) | 8 / 8, 1 / 5 | 8 / 8, 1 / 5 |
| Public sites, correct on reachable sites | 28 / 28 | 28 / 28 (Parabank's 500 again, reported correctly) |
| False passes | 0 | 0 |

**Tests:** 201 pass, plus 1 new for the plainer wording when a site is unreachable.

## Bug hunt

Two passes, three ways of looking (D45). **Ten bugs found and fixed,** each with a test (commits 2f95ab9 and fe6e51b, 207 tests pass).

| Found by | Bug |
|---|---|
| Nightshift exploring its own web app | Fields in a closed `<details>` counted as visible (Chrome gives them a size), so the agent could pick hidden controls and the a11y check warned falsely |
| Nightshift exploring its own dashboard | Status polls stalled up to 2 s while the model server was busy |
| API probe script | Behind an HTTPS tunnel without `--public-url`, every change was refused |
| API probe script | An oversized upload reset the connection instead of answering 413 |
| Reading the code | A double-click on Run now could queue two runs of one project |
| Reading the code | An explore or generate run's details page said the run hadn't finished |
| Reading the code | Expired login sessions were never deleted |
| `git status` after the tests | A read-only (`--no-record`) run wrote visual baselines into the repo |
| Earlier, during real use | A relative `--data` folder, a refused request seen as a dropped connection, a bot firewall's block page reported as a bug |

**Checked and fine:**
- Raw path tricks on `/static/`, `/files/` and the API are refused.
- Junk and non-object JSON bodies get clean 400s.
- Logged-out sessions can't be reused.
- Bad nightly times and duplicate test names are refused.
- Hindi, blank, 300-character and `<script>` client names are handled.

**Benchmarks after the fixes:**

| | Result |
|---|---|
| Shop: bugs / false alarms | 23 / 23, 0 / 13 |
| Clinic (holdout) | 8 / 8, 1 / 5 (the same `cancel-appointment`) |
| Public sites | **28 / 28** reachable sites passed (Parabank's server had recovered); 3 unreachable (ENV_ISSUE) |
| False passes | 0 (every pass has quoted proof) |
| Median run time, public | 15 s |
