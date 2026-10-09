# Security overview (for agencies' questionnaires)

Honest answers, as of [date]. Where something is not done yet, it says so.

## Accounts and access
- Accounts only from single-use invite links (7 days); no open sign-up.
- Passwords: at least 10 characters, stored as scrypt hashes.
- Two-factor login with any authenticator app (TOTP), for anyone who turns it on; each code works
  once. **Not yet enforced for admins:** each admin turns it on.
- Login lockout: 5 wrong passwords or codes from one address lock it out for 15 minutes.
- Forgotten passwords: an admin makes a single-use reset link (24 hours); using it ends every login.
- Sessions: a random token in an HttpOnly, SameSite=Lax cookie (Secure over HTTPS); only its hash is
  stored; 14 days.
- Roles: owner (runs the deployment), admin (clients, team, secrets), staff (tests and runs).

## Separation between agencies
Each agency has its own workspace. Every project, run and report file is looked up through the
user's workspace; another workspace's answers "not found". Covered by automated tests.

## Data protection
- In transit: HTTPS (Let's Encrypt via Caddy, HSTS).
- At rest: client secrets (test passwords, API keys) and two-factor secrets are encrypted with
  Fernet (AES-128-CBC + HMAC-SHA256) under a key stored apart from the data and the backups.
  The database and reports are on the server's disk, which is [encrypted / not separately
  encrypted] by the hosting provider.
- Secrets can be set and deleted in the app but never read back.
- Test data reaches the AI model only as placeholders like {{password}}, never the value.
- Page screenshots and page text are sent to the AI model provider ([provider]) during runs.

## Logging and monitoring
- An activity log per workspace: who changed what, when and from which IP; never a value. Kept a
  year; the workspace's admins can read it.
- The server keeps no access log of request paths.
- An outside uptime monitor checks the health endpoint every 5 minutes.

## Backups and recovery
Nightly backups of the database and every project's tests and encrypted secrets, 14 kept, copied
[off the server to ___]. Restores are tested [date of last test].

## Application security
- Every change needs a custom header and this site's Origin (blocks cross-site requests).
- A strict Content Security Policy on the app's pages: no third-party scripts.
- Files are served only from inside their own run's folder; body size limits.
- Dependencies: Python standard library, Playwright, PyYAML, httpx, cryptography.
- About 250 automated tests run on every change, including the security behaviour above.

## Not done yet (be upfront)
- No independent penetration test yet ([planned for date]).
- No SOC 2 / ISO 27001 certification.
- Single region, single server: no automatic failover. Recovery is from the nightly backup.
- [Two-factor not yet enforced for admins.]

## Contact for security issues
[security email]. We acknowledge within 2 working days.
