# Human review of the designed test cases

`nightshift validate --design-only` designed 8 test cases from `requirements.md` with the
4B model (`qwen3-vl:4b-instruct`). A reviewer then read them, as a QA lead reads a junior's
test cases. Recorded here so the validation numbers are honest about what a person did.

| Case | Accepted as designed? | What the reviewer changed |
|---|---|---|
| r1-1 Baguette page | yes | |
| r2-1 search for Baguette | yes | |
| r3-1 search with no results | yes | |
| r4-1 Yeast tag | yes | |
| r5-1 send a contact message | yes | |
| r6-1 invalid email | **edited** | the draft never filled the other required fields or pressed Submit; steps added. A first edit expected "no thank-you message is shown", which the 4B judge could not prove (absence needs a word to look for); reworded to "the contact form is still shown, so the message was not sent" |
| r7-1 sign in | **edited** | the draft expected a "welcome message" nobody specified; changed to "the admin dashboard is shown" |
| r7-2 wrong password | yes | |

6 of 8 accepted as designed (after two redesign rounds, see docs/DECISIONS.md, D13), 2 edited.
The first design round, before the design review existed, had 5 of 9 cases unusable
(kept in runs/validate-bakerydemo/designed-v1).
