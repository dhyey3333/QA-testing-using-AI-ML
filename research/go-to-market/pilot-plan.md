# The 30-day pilot

**Goal:** prove, with the agency's own numbers, that Nightshift saves tester hours on one real client
project without missing bugs or crying wolf. Then convert to paid, or part as friends.

## Scope and ground rules

| | |
|---|---|
| Who | One QA agency, one client project, 1 to 3 of the agency's testers |
| What | The client's **staging or test environment**, with the client's written OK. Never production with real customer data. |
| Tests | 10 to 25 regression flows the agency already runs by hand every release (login, search, cart, checkout, forms, account pages) |
| Data | Test accounts only. Passwords and keys go in the project's secrets, which the AI never sees. |
| Where it runs | The hosted app on our laptop or server, behind HTTPS; or on the agency's own machine if their client requires it |
| Cost | ₹0. No card, no commitment. |
| At the end | The agency keeps its tests (plain-text YAML) and its saved paths as Playwright files, whatever it decides. |

## Timeline

| When | Agency does | We do |
|---|---|---|
| **Day 0** (45-min call) | Picks the client project, shares a staging URL and test accounts, and writes down today's numbers (baseline below) | Creates their project and logins, and sets up secrets |
| **Week 1** | Generates tests from the URL or writes them in the form; reviews and accepts 10 to 25 | Joins one working session, fixes anything that blocks a test, and tunes the step limits |
| **Weeks 2–4** | Nightly runs on, plus Run now before each client release. The tester reviews each failure, marks it real bug / not a bug, and files the real ones | Weekly 20-min check-in; fixes the tool where it was wrong, and records each fix |
| **Day 30** | Reviews the numbers below with us | Writes the pilot report: before and after, using their own run history |

## Baseline to write down on day 0

- **Hours for one manual regression pass** on this project, counting all testers.
- **Regression passes per month.**
- **Bugs found by regression in the last 3 releases,** if known.
- **Hours a month spent writing the client's test report.**

## Success metrics (all read from the app's run history, not from opinions)

| Metric | How it is measured | Pilot passes if |
|---|---|---|
| **Tester hours saved** | Hours per regression pass, before vs. with Nightshift. "With" means the tester's review time, counted honestly | **≥ 50% less** |
| **Real bugs caught** | Failures the tester confirmed as real bugs | **≥ 1 real bug** the team hadn't found yet, or every bug manual regression found in the same weeks |
| **False alarms** | Failures the tester marked "not a bug" ÷ all runs | **≤ 5%** |
| **False passes** | Spot check: the tester re-checks 10 random passes by hand | **0** |
| **Nightly reliability** | Nights the suite ran to the end ÷ nights scheduled | **≥ 90%** (a site outage counts as ENV_ISSUE, not against it) |
| **Adoption** | Client reports the agency actually sent to its client | **≥ 2** |

**Decision rule:** the pilot is a success if **4 of the 6** are met, including **false passes = 0**.
A false pass is the one failure an agency can't sell around.

## What could go wrong, and what we do about it

| Risk | Plan |
|---|---|
| The client's staging site has a bot wall or a CAPTCHA | Ask the client to allow-list the test machine's IP, or use a test mode. Nightshift reports a block as ENV_ISSUE, never as a bug. |
| Login needs an SMS OTP | Use the staging environment's fixed test OTP, or connect its SMS log as the test inbox (`{{sms_code}}`) |
| The AI writes a wrong test | Drafts are reviewed before they count, and the tester edits them in the form |
| The free model's limits run out | Switch the project to a paid model plan for the pilot. Measured cost: about ₹0.12 to ₹0.25 per AI run, and ₹0 for replays. |
| The agency fears lost billable hours | Agree up front to price the client a fixed nightly-regression package (`pricing.md`) |

## After a successful pilot

1. A written pilot report with the six numbers.
2. A paid plan from the next month (`pricing.md`), with the pilot project carried over as it is.
3. Ask for a two-line quote and permission to name the agency. The first reference customer matters
   more than the first invoice.
