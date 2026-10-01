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
