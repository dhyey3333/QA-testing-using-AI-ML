# Pricing in INR (for QA agencies)

## What it replaces (from Part A)

| Anchor | Monthly cost |
|---|---|
| One junior manual tester in India (₹4–8 LPA) | ₹33,000 – 67,000 |
| An outsourced manual tester at published rates (160 h × $17–29) | ₹2.6 – 4.5 lakh |
| What agencies bill clients | ₹1,400 – 3,400 per tester-hour ($15–35) |
| Self-serve AI testing tools (entry plans) | ₹1,600 – 43,000 ($17–450) |

## What it costs us to serve (measured)

| Item | Cost |
|---|---|
| A fresh AI run (one test, the agent plus the judge) | **₹0** on Ollama Cloud's free plan. At hosted Qwen3-VL prices: **₹0.12 – 0.25** a run (median, measured on the public benchmark) |
| A replayed run (saved path, no model) | **₹0** |
| Server | ₹0 on a laptop. About ₹400 – 2,500 a month for a small cloud server running 2 to 4 tests at a time. |
| Per agency, worst case | 10,000 fresh AI runs ≈ ₹2,500, plus a server ≈ ₹2,500, so **about ₹5,000 a month**. Most nightly runs are replays, so it's usually far less. |

## The plans

**Pricing basis:**
- Per agency, with tester seats.
- Unlimited client projects within a plan, so taking on a new client never costs the agency more.
- AI runs are capped, so there are no surprise bills. Replays are always unlimited and free.

| Plan | ₹ / month | Testers | AI runs / month | Includes |
|---|---|---|---|---|
| **Pilot** | ₹0 for 30 days | up to 3 | 1,500 | One client project, set up with us |
| **Agency Starter** | **₹14,999** | up to 5 | 3,000 | Unlimited projects and replays, nightly runs, AI test generation and explore, white-label client reports, Playwright export |
| **Agency Pro** | **₹34,999** | up to 15 | 10,000 | Everything in Starter, plus Jira and Slack filing\*, requirements → test cases → traceability matrix\*, priority support (same business day) |
| **Agency Scale** | from **₹69,999** | 16+ | custom | Everything in Pro, plus your own server or on-premise install, and a named contact. SSO is built on request (not built yet). |

\* Built and tested on the command line today (`--file-jira`, `--slack-webhook`, `nightshift validate`).
They aren't buttons in the web app yet. Add them there before selling Pro.

- **Extra tester:** ₹2,999 a month. **Extra 1,000 AI runs:** ₹999.
- **Annual:** pay for 10 months, get 12.
- **GST (18%) is extra.** Pay by UPI, bank transfer or card.

## Why these numbers

- **Starter at ₹14,999** is about half of one junior manual tester (₹33k+). It pays for itself if it
  saves that tester half their regression time on a single client.
- **Per-seat cost goes down as the agency grows:** ₹3,000 a tester on Starter, ₹2,333 on Pro. That's
  below Part B's earlier ₹4,999 per seat, which was set when the tool was right 84% of the time
  hands-off. It's now 28 of 28, but an unproven brand still has to win its first agencies on price.
- **Margin:** even the worst case (₹5,000 to serve a Starter agency that uses every AI run) leaves
  about 67%. Typical use, mostly replays, leaves 85% or more.
- **Selling it on:** an agency can charge its client a fixed **nightly regression package** of
  ₹15,000 to ₹40,000 a month per client. One Pro plan covers many such clients. That answers the
  "automation cuts our billable hours" objection.

## Before charging anyone (practical checklist)

- [ ] A payment link: UPI, or a payment gateway. Payment gateways need your PAN and bank KYC.
- [ ] A one-page terms of service and a privacy note: what is stored, where, for how long; that
  secrets stay on the server; and that reports belong to the agency.
- [ ] GST registration, once you cross the threshold or need to bill registered businesses. Ask a
  CA; this sheet isn't tax advice.
- [ ] A server that is on all night: a laptop works for a pilot; a paying agency needs a small
  cloud server.
- [ ] A paid model plan, ready for when the free plan's limits run out.
