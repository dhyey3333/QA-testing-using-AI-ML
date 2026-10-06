# Nightshift: AI regression testing for QA agencies

**One page, for the owner or delivery head of an Indian QA agency.**

## The problem you already have

- Your testers spend most of each release re-clicking the same flows: login, search, cart, checkout, forms.
- Automation scripts break whenever a button moves, so someone maintains Selenium instead of testing.
- Clients ask "what exactly did you test, and how do you know it works?" and you write that report by hand.

## What Nightshift does

1. **Tests in plain English.** A tester writes the steps and what should happen, in a form. No code,
   no selectors. Or paste the client's URL and the AI explores the site and drafts the tests.
2. **An AI runs them in a real browser, every night,** and on demand. Several at a time.
3. **Every pass is proven.** The judge must quote the page for every expected result, and code
   checks each quote is really there. It never says "works" on a guess.
4. **Every failure is sorted:** BUG, FLAKY, TEST_OUTDATED, or ENV_ISSUE (site down, bot wall). Your
   tester only looks at real bugs.
5. **A client report per run,** branded with your agency's name: what was tested, what failed, steps
   to reproduce, screenshots. Ready to send.
6. **Passing runs are saved and replayed with no AI.** A stable nightly suite runs in seconds and
   costs nothing; the AI steps in only when the app changed.

## Measured, not claimed

Same free model throughout; every number is in the repository with its run logs.

| | Result |
|---|---|
| Planted bugs caught in our test shop | **23 / 23** |
| False passes (said "works" when broken) | **0**, across almost 400 runs in five benchmark rounds |
| Public practice sites judged correctly | **28 / 28** reachable sites |
| False alarms on bug-free runs | 1 in 45 (2.2%), on a holdout app it was never tuned on |
| Median time per test | 14 to 17 s |

**Real bugs it found on real platforms' public demo stores:**
- **Medusa:** the server crashes (HTTP 500) when adding an out-of-stock item, so the cart stays empty.
- **Saleor:** the homepage throws a React rendering error on every load.
- **AcademyBugs:** the cart's grand total is $123.13 for $15.14 + $7.99.
- **Parabank:** the server returns 500 after a successful login.

## Why agencies, specifically

- **More clients per tester.** The repetitive regression pass becomes a nightly job; your testers
  review results and do the exploratory work clients actually value.
- **Sell fixed-price regression packages** ("nightly regression + weekly report, ₹X/month") instead of
  hours, at a better margin.
- **Your brand on every report.** The client sees your agency, not ours.
- **No lock-in for your client.** Tests are plain text, and passing paths export as standard
  Playwright tests.

## What it doesn't do (yet)

- **Bot-protected sites** (Amazon, Flipkart-style) block automated browsers. It reports those as
  "blocked", not as bugs. It's for your clients' staging or test environments.
- **AI-written tests are drafts.** A tester reviews them before they count.
- **Native mobile apps** aren't covered; mobile web is (device emulation).

## The offer

**A free 30-day pilot on one client project.** We set it up with you, you run it nightly, and we
measure hours saved and bugs found together. If it doesn't pay for itself, you walk away with
your tests. See `pilot-plan.md` and `pricing.md`.
