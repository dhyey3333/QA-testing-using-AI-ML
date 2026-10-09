# Nightshift hosted on your own server

One small server runs the web app for one or more QA agencies: staff log in, each client is a
project, tests run nightly and on demand, and every run ends with a client report. Everything
below is one script on a fresh Ubuntu 24.04 server.

## 1. The server

Any provider works. What it needs: Ubuntu 24.04, 4 GB RAM or more (each test browser takes 300 to
500 MB), ports 80 and 443 open.

| Provider | Plan | Price (checked Oct 2026, before tax) |
|---|---|---|
| Hetzner Cloud | CPX22 (2 vCPU, 4 GB) | about €19.99 a month (~₹2,200). The cheaper CX23 (€5.99) was not orderable then |
| Oracle Cloud | Ampere A1, 2 OCPU / 12 GB | free ("Always Free"), but needs a card to sign up and capacity is often short |
| DigitalOcean, AWS Lightsail, an Indian VPS | 4 GB | similar; any of them |

Prices change: check the provider's page before ordering. Every paid provider needs a card or UPI
and your details; there is no way around that for a paid server.

In the provider's console: create the server with Ubuntu 24.04, add your SSH key, and allow TCP
80 and 443 in its firewall (Oracle: the subnet's Security List; Hetzner: a Firewall, or none).
Note its public IP.

## 2. Install (one command on the server)

```bash
ssh root@<public IP>          # or ubuntu@ on Oracle; any user with sudo
curl -fsSL https://raw.githubusercontent.com/dhyey3333/QA-testing-using-AI-ML/main/deploy/server/setup.sh -o setup.sh
bash setup.sh
```

It serves `https://<ip-with-dashes>.sslip.io`, a free name that points at the IP, with a real
HTTPS certificate from Let's Encrypt. With your own domain, point an A record at the IP and run
`DOMAIN=qa.youragency.example bash setup.sh` instead.

Then the three steps it prints:

```bash
ollama signin                      # open the link on your laptop and approve it
sudo -u nightshift /opt/nightshift/.venv/bin/nightshift hosted add-user --data /var/lib/nightshift --email you@youragency.example --admin
sudo cat /etc/nightshift/secret.key   # copy it into your password manager, OFF the server
```

## 3. Before the first client

- **Turn on two-factor login** (Account → Two-factor login) for yourself and every admin.
- **Copy the secrets key off the server** (step 2). Client test passwords are encrypted with it;
  backups don't contain it, on purpose. Lose it and a restore brings back everything except the
  secrets, which the client then has to give you again.
- **Set up monitoring** (below), so you hear about an outage before your client does.
- **Choose the model.** The default is Ollama Cloud's free plan: rate-limited, and page screenshots
  go to Ollama. For client work use a provider whose terms you can show the client (MODEL_BASE_URL,
  MODEL_NAME, MODEL_API_KEY in `/etc/nightshift.env`, then restart).

## Monitoring

`https://<your address>/healthz` answers `{"ok": true, "database": "ok", ...}` with no login. It
holds counts only, never a name. Point a free uptime monitor at it, for example UptimeRobot:
new monitor → type "Keyword" → URL `https://<your address>/healthz` → keyword `"ok": true` →
every 5 minutes → alert to your email or phone. It also says when the last backup was made
(`last_backup`): if that date stops moving, backups have stopped.

## Backups

The app writes one backup a night (03:15, server time) to `/var/backups/nightshift` and keeps the
newest 14: the database and every client's tests, drafts, saved paths, approved looks and
encrypted secrets. Run reports are left out (they are large, and the newest 60 per client stay on
the server).

A backup on the same server doesn't survive the server. Copy them somewhere else too, for
example with rclone to Google Drive or any storage you have, from a daily cron job:

```bash
rclone copy /var/backups/nightshift remote:nightshift-backups
```

Make one now, or restore one:

```bash
sudo -u nightshift /opt/nightshift/.venv/bin/nightshift hosted backup --data /var/lib/nightshift --to /var/backups/nightshift
sudo systemctl stop nightshift
sudo -u nightshift /opt/nightshift/.venv/bin/nightshift hosted restore /var/backups/nightshift/nightshift-backup-<date>.zip --data /var/lib/nightshift --force
sudo systemctl start nightshift
```

On a new server, put the old key back in `/etc/nightshift/secret.key` (owner `nightshift`, mode 600)
before starting it, or the restored secrets can't be read.

Test a restore once, on a spare folder, before you need it:
`nightshift hosted restore <zip> --data /tmp/restore-test`.

## Locked out

- Someone forgot their password: an admin opens **Team → Reset link** and sends them the link
  (single use, 24 hours).
- Someone lost the phone with their codes: **Team → Turn off 2FA**, then they set it up again.
- The only admin is locked out: on the server,
  `sudo -u nightshift /opt/nightshift/.venv/bin/nightshift hosted reset-link --data /var/lib/nightshift --email <them> [--no-2fa]`
  prints a reset link to open on the app's address.

## Deleting data

A client that leaves, or asks for its data to be deleted: **Settings → Delete this client** (type
its name). An agency that leaves: the owner's **Workspaces** page, the bin icon (type its name).
Both remove the database rows and the folders on disk. Backups made before still hold the data
until they age out (14 nights); say so in your terms.

## Running it

| | |
|---|---|
| Logs | `journalctl -u nightshift -f` |
| Restart | `sudo systemctl restart nightshift` |
| Update to the latest code | `bash /opt/nightshift/deploy/server/update.sh` (outside the nightly window) |
| Data (database, tests, reports, secrets) | `/var/lib/nightshift` |
| Backups | `/var/backups/nightshift`; time and count in `nightshift.service` |
| Secrets key | `/etc/nightshift/secret.key` |
| Activity log | Team → Activity log (each workspace's own, for its admins) |
| Disk use | Each project keeps the files of its newest 60 runs; change it with `--keep-runs` in `nightshift.service` |
| Settings (public URL, model) | `/etc/nightshift.env`, then restart |
| Time zone of nightly runs | `Asia/Kolkata`, in `nightshift.service` |

Sizing: two runs at once, two tests at a time in each (`--max-runs 2 --parallel 2` in
`nightshift.service`). Ollama Cloud's free plan answers one model request at a time, so parallel
tests take turns at the model; replays of saved paths don't need it.
