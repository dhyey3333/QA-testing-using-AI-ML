# Nightshift hosted on Oracle Cloud's free server

One small server runs the web app for one QA agency: staff log in, each client is a project,
tests run nightly and on demand, and every run ends with a client report. Cost: ₹0 on Oracle's
"Always Free" Ampere server and Ollama Cloud's free plan.

## 1. The server (Oracle console, about 15 minutes)

1. Sign up at https://signup.cloud.oracle.com. Home region: India West (Mumbai) or India South
   (Hyderabad); it can't be changed later. Oracle asks for a credit card to verify you, and
   doesn't accept PIN-based debit, virtual or prepaid cards.
2. Compute → Instances → **Create instance**:
   - Image: **Canonical Ubuntu 24.04** (aarch64).
   - Shape: **Ampere → VM.Standard.A1.Flex, 2 OCPU, 12 GB** (inside the Always Free allowance).
   - SSH keys: paste the public key (`~/.ssh/oracle_nightshift.pub` on the laptop that will manage it).
   - "Out of capacity": pick another availability domain, or try again later.
3. On the instance's subnet → Security List → **Add Ingress Rules**: source `0.0.0.0/0`, TCP,
   destination ports **80** and **443**. (Port 22 is open already.)
4. Note the instance's **public IP**.

## 2. Install (one command on the server)

```bash
ssh -i ~/.ssh/oracle_nightshift ubuntu@<public IP>
curl -fsSL https://raw.githubusercontent.com/dhyey3333/QA-testing-using-AI-ML/main/deploy/oracle/setup.sh -o setup.sh
bash setup.sh
```

It serves `https://<ip-with-dashes>.sslip.io`, a free name that points at the IP, with a real
HTTPS certificate from Let's Encrypt. With your own domain, point an A record at the IP and run
`DOMAIN=qa.youragency.example bash setup.sh` instead.

Then the two steps it prints:

```bash
ollama signin                      # open the link on your laptop and approve it
sudo -u nightshift /opt/nightshift/.venv/bin/nightshift hosted add-user --data /var/lib/nightshift --email you@youragency.example --admin
```

## 3. Use it

Open the address, log in, **New project** for a client, add its tests under **Tests**, put test
passwords under **Settings → Secrets** (a test uses one as `${NAME}`), set a nightly time, and
press **Run now**. Each run lists its **Client report** (the file to send the client), the full
report, and the log.

The first run of a test uses the model; a pass saves its path, and later runs replay it with no
model calls, so a stable nightly suite costs nothing and finishes fast.

## Running it

| | |
|---|---|
| Logs | `journalctl -u nightshift -f` |
| Restart | `sudo systemctl restart nightshift` |
| Update to the latest code | `bash /opt/nightshift/deploy/oracle/update.sh` |
| Data (database, tests, reports, secrets) | `/var/lib/nightshift` (back it up) |
| Disk use | Each project keeps the files of its newest 60 runs; change it with `--keep-runs` in `nightshift.service` |
| Settings (public URL, model) | `/etc/nightshift.env`, then restart |
| Time zone of nightly runs | `Asia/Kolkata`, in `nightshift.service` |

Sizing: two runs at once, two tests at a time in each (`--max-runs 2 --parallel 2` in
`nightshift.service`). Each browser takes 300 to 500 MB of the 12 GB. Ollama Cloud's free plan
answers one model request at a time, so parallel tests take turns at the model; replays don't
need it.
