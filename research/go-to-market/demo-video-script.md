# 2-minute demo video (script)

For the outreach messages and the website. Record the screen at 1920x1080 with your voice (OBS
Studio or the Windows Snipping Tool's recorder, both free). Use the demo shop or a practice site
and the fake "Acme Retail (fake)" client: no real client, no real data, nobody else's site without
permission. Say only measured numbers.

| Time | On screen | You say |
|---|---|---|
| 0:00 to 0:10 | The Clients page with two or three fake clients, each with its run-history strip | "This is Nightshift QA. Each of your agency's clients is a card here: its tests, a nightly run, and a report you can send." |
| 0:10 to 0:35 | A client → Tests → New test: type three steps and two expected results in plain English, save | "A tester writes a test the way they'd explain it to a colleague: what to do, and what the page must show. No code. Passwords go in Secrets, encrypted; the AI never sees them." |
| 0:35 to 0:50 | Generate tests: paste the demo shop's URL, 5 tests; show the drafts arriving for review | "Or paste the site's address and the AI explores it and drafts tests. A person reviews each before it counts." |
| 0:50 to 1:15 | Run now; the run page fills in; open one passing test and show the quoted proof | "It runs every test in a real browser. A pass needs proof: the judge must quote the page, and code checks the quote is really there. On sites it had never seen, it made no false passes." |
| 1:15 to 1:35 | A failing test (the demo shop with a planted bug): the screenshot, the reason, the steps to reproduce | "When something breaks you get what a developer needs: what happened, a screenshot, and the steps, ready for Jira." |
| 1:35 to 1:50 | The client report, with the agency's name on it | "Every run ends with a report in your agency's name, ready to send to the client." |
| 1:50 to 2:00 | Settings → nightly time 02:30; then the website's pilot page | "Set a time and it runs every night. Try it free for 30 days on one client: nightshift-qa.github.io/pilot." |

Before recording: start the demo shop (`uv run python -m demo_shop --bugs order-total-mismatch` for
the failing run), log in as a fake admin, and run the suite once so the cards have history.
