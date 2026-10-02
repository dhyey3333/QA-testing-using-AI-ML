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
