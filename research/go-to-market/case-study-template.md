# Pilot case study (template)

Fill it in from the pilot's own numbers at the end of the 30 days. Only measured facts; the
agency approves every word before it is published, and the client is never named without their
written OK. Fake or anonymised names are fine ("a Pune e-commerce client").

## The agency
- Name (or "a {city} QA agency of {n} testers"), what they test, for whom.

## Before Nightshift
- Regression per release: {n} flows, {h} tester-hours, how often ({releases per month}).
- How bugs were reported to the client.

## The pilot
- Dates, one client project, staging site.
- Tests: {n} written by the agency, {m} drafted by the AI and accepted after review.
- Runs: {nightly runs}, {manual runs}; share replayed with no AI: {x}%.

## Results (from the run history and the agency's own log)
| Measure | Number | How it was measured |
|---|---|---|
| Tester-hours of regression per release, before → after | {h1} → {h2} | the agency's timesheets |
| Real bugs caught and confirmed by the agency | {n} | each one checked by a tester |
| False alarms (failures that weren't bugs) | {n} of {runs} test runs ({%}) | each failure reviewed |
| False passes found later (a bug the tests said was fine) | {n} | bugs found by other means, checked against the run history |
| Median run time | {s} s per test | run history |
| Cost | ₹{x} model cost; ₹{y} server | Settings and the provider's bill |

## In their words
> "{quote}" ({name, role}, approved {date})

## What didn't go well
At least one honest line: a flow it couldn't test, a false alarm pattern, a setup step that took
too long. Readers trust a case study more when it has one.
