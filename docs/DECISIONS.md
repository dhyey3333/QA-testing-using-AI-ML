# Design decisions

The choices that shape Nightshift, and why. Each one came from a failure or a measurement.

## D1. The model acts by element id, never by pixel coordinates
Every visible interactive element gets a number, which is drawn on the screenshot and listed
in the prompt. The model says `click 12`. Small vision models are poor at coordinates, and ids
also make every action checkable: an id that isn't on the page is rejected before anything runs.

## D2. A pass needs proof: the grounded judge
**Why:** on the first benchmark the 4B model looked at a cart whose total read ₹180 and passed it
as "₹600, the sum of the line totals". The agent that ran the steps knows what the page *should*
say, and it reports that.

**What:** a pass triggers a separate judge call that must quote the page for every expected
result. Code checks each quote is really on the page (case, spacing and line breaks ignored). A
claim with any made-up quote is demoted to "does not hold", so one real quote can't carry a fake
one. The judge gets one retry with the reason it was rejected.

**Result:** the same 4B model, as judge, now writes "180 + 420 = 600, but the page says ₹180".

## D3. Absence is checked too
"Masala Chai is no longer in the cart" can't be proven with a quote. The judge lists text that
must be absent, and code checks it really isn't on the page. Found by a false alarm on the clean
shop: the judge knew the item was gone, but the evidence rule wouldn't let it say so.

## D4. The spec is the contract, so an agent's "fail" can be overruled
**Why:** in a negative test ("an error says the pincode must be 6 digits"), the error *is* the
expected result, and a small model's instinct calls any error a bug. It failed the clean shop
while writing "order placement fails as expected".

**What:** when the agent says fail, the judge checks the page. If every expected result is
proven, the test passes and the agent's complaint is kept as a warning. A real failure mid-flow
(a dead button, the wrong product) leaves the expected results unproven, so it still fails,
described in the agent's own words.

## D5. Some failures need no model at all
An uncaught JavaScript error, or an HTTP 5xx from the app's own origin, fails the run even when
the page looks fine. The same action with no visible effect three times in a row fails it too:
the model's own rule says to give up after two, but small models keep clicking. Page state is
fingerprinted (URL, text, element values, scroll), so "no change" is a fact, not an opinion.

## D6. Four verdicts, never blurred
`pass`, `fail` (the app has a bug), `flaky` (failed, then passed on a retry), `error` (the tester
couldn't finish). The benchmark counts a `fail` on a bug-free app as a false alarm, so "the model
got confused" must never be reported as a bug. CI exit codes follow: 0 pass or flaky, 1 fail, 2 error.

## D7. Replay the path, pay for the agent only when it breaks
A passing run saves each acted-on element as a ranked list of ways to find it again (test id,
role + accessible name, label, placeholder, text, CSS path), keeping only those that matched
exactly that element when recorded. Later runs replay with no model calls except the judge. When
a step can't be found (a redesign), the agent takes over from that step and the recording is
updated: self-healing. A recording is dropped when the spec's steps, expectations or data keys
change. Retries never replay: a failure is only confirmed by an agent finding the path itself.

## D8. Test data reaches the model as placeholders
The model types `{{password}}`; the runner fills the value in at the last moment and masks values
back into placeholders in page text and logs. Secrets stay out of prompts and reports, and small
models type placeholders more reliably than they copy strings character by character.

## D9. Two benchmark apps: one to build against, one never touched
The demo shop is the development set: prompts and rules were fixed against its failures, so its
score flatters. The clinic (`holdout/`) is built in different idioms (server-rendered forms,
`<div role="button">`, `<select>`, date inputs, `confirm()` dialogs) and nothing is ever tuned
on it. Its score is the honest estimate of how Nightshift does on an app it has never seen.

## D10. What the holdout caught: dropdowns and date fields
The first blind run on the clinic scored **4 false alarms out of 5** clean runs, while the shop
looked nearly perfect. Every one had the same cause: the model *clicked* native `<select>`
dropdowns and date fields like buttons. Browsers draw those pickers outside the page, so the click
changes nothing, the model clicks again, and the stuck rule reports a working form as broken. Its
"8/8 bugs caught" was mostly the same flaw, not detection. The shop has no dropdowns, so no amount
of tuning there would have shown it.

The fix is general, not clinic-specific: an action that can't work on a control is refused with
the one that does ("[5] 'Doctor' is a dropdown; use select with one of: ..."), option matching
forgives partial names, and the prompt says how dropdowns and dates work. After this, the clinic
has been seen once, so its later numbers are reported as "seen once", not blind.

## D11. "Settled" means the requests an action set off have come back
Playwright's `networkidle` only describes the initial page load: once a page has loaded, waiting
for it returns immediately, even while a click's fetch is still in flight. So after "Log in" the
page was read before the login response re-rendered it. The agent saw "no change", clicked again
into a re-rendering page, got confused and went on to add an item twice. Found while running a
generated spec. Nightshift now counts in-flight requests itself and waits for 300 ms of quiet
(capped at 3 s). The demo shop's `slow-api` variant (1.2 s responses) keeps it tested.

## D12. What a real app taught: Wagtail's bakerydemo
The first run on a real open-source app (specs written before Nightshift ever saw it, bugs
planted in its real source) caught 6 of 8 bugs for real, one by luck, and missed one. It had 2
false alarms in 9 clean specs. Four general gaps, none visible on the apps built for testing:

- **Claims about specific values need those values in the evidence.** The judge passed "the
  origin is France" on the evidence `["Origin", "Yeast bread"]`. Every quote was real, but none
  said France. Now the names, amounts, numbers and quoted text an expected result names must
  appear in its evidence (or its "absent" list). Codes like `KC-12345` are left out because
  "looks like KC-12345" names a shape, not a value.
- **Typography.** The page said "didn’t", the model quoted "didn't", and a true claim failed the
  quote check. Curly quotes and dashes are now normalised before comparing.
- **The browser's own form validation.** A bad value in an `type="email"` field is rejected by a
  browser tooltip that isn't part of the page. An init script marks the fields the browser fires
  `invalid` on (it does that only when it blocks a submit), and observe() adds their messages as
  `[browser says] Email: Please include an '@'...`, which the agent reads and the judge can quote.
- **New tabs.** Wagtail's "Visit the live page" opens a new tab. Nightshift stayed on the old one
  and reported a working flow as broken. It now carries on in the new tab, as a person would.

The first fixes overshot, and the second run showed it: false alarms went *up* (bakery 2 to 3,
clinic 1 to 2). Refinements, each from a specific run:

- The value rule demanded that "SC-1001 is no longer listed" cite "SC". But that claim is proven
  by what the page shows instead ("You have no appointments"). Claims about absence are now exempt,
  where absence means a negation *before* the reporting verb: "a message says no results were
  found" is about a message, not an absence.
- "One made-up quote sinks the claim" threw out a dashboard proven by five real quotes because of
  one embellished label. Now a made-up quote *with a number in it* still sinks the claim (that is
  where hallucinations do harm), while a made-up label is dropped and the real evidence has to
  stand on its own.
- The judge quoted the browser-tab title, which it is shown, and the quote check didn't include
  it. Title and URL are now quotable.
- `<input type="submit">` with no value: the browser draws "Submit", Nightshift read "". The agent
  saw an unlabelled [4].
- The no-effect rule ("the same action 3 times with no change") now goes through the judge like
  an agent's fail. In a negative test the browser blocking Submit *is* the expected result, and a
  small model keeps pressing it.

Planted-bug patches were checked to actually show before any model ran. That check caught two
"bugs" that did nothing (a Django filter that silently ignores negative slices, and a template
branch that page never uses). Scoring a tester against a bug that can't be seen would have been
unfair to it.

## D13. Test design is reviewed in code, like verdicts are
`nightshift validate` takes a requirements document, designs test cases per requirement, runs
them, and writes the traceability matrix. On the real bakery site, the 4B model's first designs
failed **5 of 7 requirements on the untouched site** and missed a planted bug. Hand-written specs
on the same site scored 8/8 with 0 false alarms, so the design was the weak link, not the running.
Reading the designs showed why:

- It ignored the requirement's own example (tested Anadama with Baguette's ingredients), and it
  dropped the example that would have caught the bug ("but not Desserts with Benefits").
- It invented exact wording ("Thank you for your message! We'll get back to you soon."), which
  fails on any real site that says it differently.
- It didn't know the test-data names, so it typed `'staff'` and `'password'` as literal values.
- It wrote "Navigate to /admin/", and the agent had no way to type a URL.

Fixes: the designer is told the data names and that the example *is* the expected result; code
reviews each design for values the requirement names but no case checks, and for quoted wording
the requirement doesn't contain, and sends it back once with those findings; a case can start at a
path on the same site; the agent can `goto` a path on the same site; and re-selecting a dropdown's
current option is refused, so it doesn't look like a dead control.

With those fixes, 6 of 8 designed cases were usable as designed; a reviewer edited 2
(`realworld/bakerydemo/REVIEW.md`). So `validate --design-only` exists: with a small model,
test design is drafting, and a person reviews the draft, as a QA lead would.

The last false alarm on the validation run taught three more things:
- The judge wasn't told what `[browser says]` lines are, so it didn't count a browser's
  validation message as an error message. It is told now, and so is the agent ("pressing submit
  again changes nothing").
- The judge reads masked text (`'{{invalid_email}}' is missing an '@'`) but its screenshot shows
  the real value (`not-an-email`). It quoted what it saw, and a true quote failed. Quotes are now
  masked the same way before they are checked.
- "No thank-you message is shown" can't be proven by a small judge without a word to look for.
  Code could guess one, but a guessed word ("greets" for "no longer greets the user") would pass
  a header that still greets the user. So code never proves absence on its own. The retry says
  exactly what to put in "absent", and specs prefer positive wording ("the form is still shown").

One process lesson: a background run reported as failed was still running. It applied planted
bugs to the app while other work used it, and died without its cleanup. The harness now refuses
to start if anything is already serving on its port.

## D14. A local dashboard, not a hosted one
`nightshift serve` puts every command behind a web page, but on the user's own machine. A hosted
"paste any URL" service would pay for a browser and a model on every run, invite pointing it at
sites the user doesn't own, and hold customers' test credentials, all before the tool is
reliable enough for strangers. Locally, none of that applies.

A local server that can start browsers still needs guarding, because any website open in the
same browser can send requests to 127.0.0.1:
- it binds to 127.0.0.1 only;
- it rejects requests whose Host header isn't localhost, which defeats DNS rebinding;
- every request that changes something needs a token that exists only inside the dashboard page.
  Another origin can't read the page to get it, and can't set the header without a CORS
  preflight that the server never approves;
- it reads and writes only inside the workspace, specs only in the spec folders, reports only
  under runs/.

Jobs run one at a time (one GPU, one model) as a `nightshift` subprocess in its own process
group, so Stop also stops the browser it started. The page is plain HTML, CSS and JS with no build step,
and no new dependencies.

## D15. API tests use no model
A backend check is deterministic: send this, expect that status and these fields. A model would
add cost, latency and doubt for nothing, so `requests:` specs are plain HTTP with declarative
checks, stop at the first request that fails (later ones usually depend on it), and are never
retried (a retry repeats the same answer). They still produce ordinary RunResults, so reports,
defect analysis and the dashboard treat them like browser tests.

A live run taught one thing: on Windows, "localhost" is tried over IPv6 first, with a two-second
wait before falling back to IPv4. A `max_ms` check blamed the app for that. When an IPv4 server is
listening on localhost, API tests now connect over IPv4.

## D16. The model never reads the inbox
For a sign-in code, the agent only says where the code goes (`{{email_code}}`). Code fetches the
newest email to the test address *since this test started* (an old code from an earlier run is
wrong by design), finds the code (a 4 to 8 digit number, preferring a line that mentions a code)
and types it. Small models copy six digits badly, and an inbox is full of things that are none of
the model's business. Magic links must stay on the app's own site.

## D17. Jira: one defect, one ticket
Each defect's signature (the same one defect analysis groups by) is hashed into a label. Before
filing, Nightshift searches for an open issue with that label: if there is one, it comments
("seen again") instead of filing a duplicate. A defect whose issue was closed and that comes back
is filed again, as a regression. Jira Cloud moved JQL search to `/rest/api/3/search/jql`; Data
Center still has `/rest/api/2/search`, so both are tried. Descriptions are converted to Jira's wiki
markup.

The first run against a real Jira Cloud site found what the stand-in server could not: the
address pasted from the browser was a board page (now cut back to the site), and a new Scrum
project has no Bug issue type (now: the project's types are read, and a Task is filed when there
is no Bug). The stand-in now refuses unknown issue types the way Jira does.

## D18. Any OpenAI-compatible model, local by default
A plain HTTP POST to `/chat/completions`: Ollama, vLLM, llama.cpp, OpenRouter, Groq. The default
is a 4B vision model on a 6 GB laptop GPU, so the whole thing runs offline for free. The judge can
be a different, bigger model (`JUDGE_NAME`), since it runs once per test instead of once per step.

## D19. A control with a shared label is named by its item
Public demo shops show a dozen buttons all called "Add to cart". The 4B model clicked the wrong
product's button, or the search button eleven times. Now a control whose label repeats on the page
gets the name of its own item: the closest ancestor holding no other control with that label (a
product card, a table row), named by its heading or first text line, skipping prices. "Add to cart
— Blue Top". This is generic: no site's class names are used, only headings, `name`/`title`
classes and text.

## D20. A replay re-checks the recorded evidence instead of asking the judge
A passing run records, per expected result, the quotes that proved it. A replay that walks the same
path and finds every one of those quotes on the page again (and none of the text proven absent)
passes with no model call: the same thing an exported Playwright test asserts. The quote check is
the same code the judge's answers go through; what is skipped is only the model choosing the
quotes. Anything missing, such as a changed total or a dynamic order number, falls back to the
judge, so a regression is never passed on old evidence (tested: a replay with the order number
removed still fails). Every passing run is also written as a Playwright test.

## D21. A URL and a goal are enough
`nightshift run --url URL --goal "..."` writes a spec with the goal as its one step and, unless
expected results are given, "the page shows this was done: <goal>" as its check. The judge must
still quote proof, and capitalised names in the goal must appear in it. The spec file is kept so the
next run of the same goal replays its saved path.

## D22. Four kinds of not-passing, and an outage is not a failure
Every result that isn't a pass gets one label: BUG, FLAKY, TEST_OUTDATED or ENV_ISSUE
(`outcome.py`). On public demo sites a Cloudflare 522 and a bot-check page were both reported as
app failures. Now a gateway or CDN error (502, 503, 504, 52x), a bot check, an unreachable host or
an unreachable model makes the run an `error` labelled ENV_ISSUE: nothing was tested, so nothing
failed. A 500 stays a BUG, because it is the app's own error. TEST_OUTDATED means the test couldn't
be carried out as written: a saved path that broke and couldn't be healed, or steps the tester
couldn't follow.

## D23. A tester checks before deciding
Most false alarms on the demo shop and public sites were the 4B model deciding too early: it typed
only the pincode of a delivery form, clicked "Log in" before typing the password, or left a
privacy box unticked, then blamed the app. Before a verdict is accepted, the runner asks once
each: is there test data never typed, and was a field typed into after the last click with nothing
submitting it? After that, a failure gets one second look: if the judge can't find what the agent
claimed, or the agent says the app is broken, it is told what is missing and may fix a skipped
step or a wrong turn. Each check costs at most one extra turn. A real bug stays a bug: the agent
says fail again, or the judge still finds nothing (tested against planted bugs).

## D24. One more try for a slow page
A click, type or select that fails because the element is covered, not visible yet, still moving
or was re-rendered is tried again after one second before the model hears it failed. Playwright
already waits up to five seconds per action; this covers loading overlays and slow renders that
take longer.

## D25. A prompt that doesn't fit is sent again, smaller
A local Ollama defaults to a 4,096-token context, and the screenshot alone takes about 1,300 of
them. On a public demo shop's product grid the agent's prompt came to 4,119 tokens and the model
server refused it. Worse, that 400 was read as "this server doesn't support JSON mode", which
switched JSON mode off for every later call in the run. Now a context-size error is recognised as
such: JSON mode stays on, and the prompt is sent once more in compact form (1,200 characters of page
text, only the elements on screen, the last 6 steps; 4,000 characters for the judge). Setting
`OLLAMA_CONTEXT_LENGTH=8192` avoids the compact path entirely.

## D26. Iframes are read like the page
Payment widgets (Razorpay's checkout, Stripe Elements), embedded forms and many sign-in boxes are
iframes, often on another origin, and `document.querySelectorAll` never sees inside them. Now every
visible iframe is read with the same script, its elements numbered after the page's, and its text
added under "[inside a frame: ...]" so the judge can quote it. Actions are sent to the frame that
owns the element. Ad and tracker frames, and frames under 40 px, are skipped. A step inside a
frame is not saved for replay yet, so a flow that pays is driven by the agent on every run.

## D27. A stand-in gateway, not a real one
Testing against Razorpay or Stripe test mode needs an account and test keys, which the user creates,
not the tester. The demo shop has "Kulhad Pay", a stand-in that behaves the way gateway checkouts
do where it matters for a tester: an iframe on another origin (localhost vs 127.0.0.1), Razorpay's
published test UPI IDs (`success@razorpay` pays, `failure@razorpay` is declined), and a message to
the parent page with the result. The planted bug `payment-failure-ignored` places the order anyway.
The same specs, pointed at an app using real test mode, should work unchanged, but that has not
been tried.

## D28. Authenticator codes are computed, edge cases are generated by rules
`{{totp_code}}` is computed from the test account's `totp_secret` right before typing (RFC 6238,
standard library only); the model never sees the secret, and typing `{{totp_secret}}` is refused.
`nightshift edge-cases` and `run --edge-cases` write, for each value a positive test types, an empty
variant and a wrong-format (or 300-character) variant that must be refused with a message. Fixed
rules, not a model, so the same spec always gives the same cases. They are drafts: an app that
accepts a 300-character name may be right to.

## D29. A report for the client, not for us
A QA team's deliverable is a report its client reads: can we release, what is broken and how badly,
which requirements are covered, and the evidence. `client-report.html` is that, one file per run:
a release recommendation (worded as a recommendation, not a sign-off), counts by cause, each defect
with severity, steps to reproduce, the failing screenshot and its ticket link, a requirements table
when specs carry requirement ids, and every test's result. Screenshots are embedded, so it can be
emailed or printed to PDF as one file. Light theme only, because it is printed. With `--brand` it
names the agency that prepared it and never mentions Nightshift. Ticket links come from
`issues.json`, written whenever Jira or GitHub filing succeeds.
