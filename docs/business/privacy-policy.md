# Nightshift QA app: Privacy Policy

*Draft, not legal advice. Last updated: [date].* This covers the web app the agencies' staff log in
to. The public website has its own short privacy page (nightshift-qa.github.io/privacy/).

**Who we are:** [Your legal name / business name], [address], [email]. For questions or requests
about your data, write to [email]; we answer within 7 days.

## What we collect about the people who use the app
| What | Why | Kept |
|---|---|---|
| Name, work email, and your agency | To give you an account in your agency's workspace | While your account exists |
| Your password | Only as a slow one-way hash (scrypt); nobody can read it back | While your account exists |
| A two-factor secret, if you turn it on | To check the codes from your authenticator app; stored encrypted | Until you turn it off |
| A login cookie | To keep you logged in; only its hash is stored | 14 days, or until you log out |
| Activity log: what you changed, when, and the IP address it came from | So your agency's admins can see who did what, and to investigate misuse | 1 year |
| Failed logins per IP address | To lock out password guessing | 15 minutes, in memory only |

We don't use advertising or analytics cookies or trackers in the app, and we don't sell or share
personal data.

## What agencies put in
Tests, test accounts, client website addresses, screenshots and reports belong to the agency and
its clients. We process them only to run the agency's tests, as the
[Data Processing Agreement](data-processing-agreement.md) sets out. Tests should use fake data; the
app is not meant for real customers' personal data.

## Who else handles data
- **Hosting:** [provider, country], where the app and its data are stored.
- **AI model provider:** [provider], which receives page screenshots and page text during test
  runs to decide each step. [Its data terms: link.]
- **Backups:** [where off-server backups are kept].
We tell agencies before adding or changing one of these.

## Your rights (Digital Personal Data Protection Act, 2023)
You can ask for a summary of the personal data we hold about you, to correct it, or to delete it
(for example by having your agency's admin remove your account, which deletes it), and you can
nominate someone to act for you. Write to [email]. If you're not satisfied with our answer, you can
complain to the Data Protection Board of India.

## Security
Passwords are hashed, secrets and two-factor secrets are encrypted at rest, every change is
logged, and data is backed up nightly. Details: [security overview](security-overview.md). If a
breach affects your personal data, we will tell you and the Data Protection Board as the law
requires.

## Changes
We will post changes here and email account holders about important ones.
