# Outreach messages for QA agencies

**Who to contact:**
- **The agency:** Indian QA service agencies of 10 to 200 testers that sell manual and regression
  testing to web clients. Find them with LinkedIn searches such as "QA testing services" +
  Ahmedabad / Pune / Bengaluru / Hyderabad / Noida, plus Clutch and GoodFirms listings.
- **The person:** the founder, delivery head or QA manager, not a recruiter.

**Rules:**
- Short, specific, one ask.
- Never send them a "test" of their client's site they didn't ask for, and never claim numbers
  beyond `pitch.md`.
- Send 10 to 15 a day, by hand. No mass tools.

## 1. LinkedIn connection note (under 300 characters)

> Hi {first name}, I've built an AI tester that runs plain-English regression tests nightly and writes a client-ready report, with proof for every pass. Looking for 2 QA agencies to pilot it free for a month. Open to a quick look?

## 2. LinkedIn message after they accept

> Thanks for connecting, {first name}.
>
> Short version: Nightshift lets your testers write regression tests in plain English (or have the AI draft them from the client's URL). It runs them in a real browser every night and sends a report with your agency's name on it.
>
> What makes it different: it never marks a test passed without quoting the page as proof. Across almost 400 benchmark runs, it has had zero false passes.
>
> I'm offering a free 30-day pilot on one of your client projects. I'll set it up with your team, and we'll measure hours saved together.
>
> Worth a 20-minute call this week? I can show it running on a live demo store.

## 3. Cold email

**Subject options** (pick one):
- Nightly regression for {agency}'s clients, without scripts
- A free month of AI regression testing for one {agency} client
- {first name}, a tester that proves every pass

> Hi {first name},
>
> {Agency} sells testing to {their client type, from their website}, so you know how much of each release goes into re-clicking the same flows.
>
> I built Nightshift for that part:
> - **Tests in plain English:** your tester writes steps and expected results in a form, or the AI drafts them from the client's URL.
> - **Every night, in a real browser,** with a branded client report per run: what passed, what broke, steps to reproduce, screenshots.
> - **Every pass is proven** by quoting the page. Benchmarks: 23 of 23 planted bugs caught, zero false passes across almost 400 runs.
> - **Saved paths replay with no AI,** so a stable suite runs in seconds.
>
> I'd like to run a free 30-day pilot on one of your client projects (their staging site, with their OK). If it doesn't save your team real hours, you keep the tests and we part as friends.
>
> Can I show you a 20-minute demo this week?
>
> {your name}
> {phone} · {link to a 2-minute demo video}

## 4. WhatsApp (only when someone shared the number or introduced you)

> Hi {first name}, {referrer} said you run QA at {agency}. I've built an AI regression tester: plain-English tests, nightly runs, a branded client report, and proof for every pass. Looking for one agency to pilot it free for a month. Can I send a 2-minute demo video?

## 5. Follow-ups

**After 4 days, no reply:**
> Hi {first name}, a 2-minute video of it testing a demo store end to end, in case it's easier than a call: {link}. Happy to set up the free pilot whenever suits.

**After 10 days, last one:**
> Last note from me, {first name}. If nightly regression isn't a pain right now, no problem. If it becomes one, the free pilot offer stands.

## 6. Answers to the usual objections

| They say | Answer |
|---|---|
| "We already use Selenium / Playwright." | Keep them. Nightshift covers the flows that keep breaking your scripts, and its passing paths export as Playwright tests your team can keep. |
| "Automation will cut our billable hours." | Sell it as a fixed-price nightly-regression package. Your margin goes up, and testers move to exploratory and client work, which bills better. |
| "AI makes things up." | That's why every pass must quote the page, checked in code. It has had zero false passes in our benchmarks, and a person reviews any AI-written test before it counts. |
| "How does it do on a site it has never seen?" | Measured on practice sites it had never seen: no false passes. After fixing what the first blind test found, 1 false alarm in 20 working flows (it can't hover the mouse yet). That's why your tester checks failures in the first weeks on a new site. |
| "Is our client's data safe with you?" | Test passwords are encrypted on the server and never shown back or sent to the AI; two-factor login; an activity log of every change; nightly backups; each agency sees only its own clients; delete a client's data any time. See docs/business/security-overview.md. |
| "Our client won't allow a third-party tool." | The model never sees passwords or test data values, only placeholders. It can run on your own machine or server, and secrets stay on it. |
| "What does it cost after the pilot?" | See the pricing sheet: from ₹14,999/month for up to 5 testers, well under one junior tester's salary. |
| "Does it work on {big site}?" | It's for your clients' staging and test environments. Big consumer sites block automated browsers, and it reports that as "blocked", not as a bug. |

## Demo script for the call (15 minutes)

1. **Two minutes:** ask how they run regression today, and how many hours a release takes.
2. **Five minutes:** in the hosted app, paste a practice store's URL, **Generate tests**, accept one,
   **Run now**, and open the **client report**.
3. **Three minutes:** show a caught bug with its proof (AcademyBugs' wrong total), and a replayed run
   with no AI.
4. **Five minutes:** propose the pilot. Agree on the client project, the success metrics and a start
   date (`pilot-plan.md`).
