#!/usr/bin/env bash
# Puts the latest Nightshift from GitHub on the server and restarts it. Runs already going are
# marked failed ("the server restarted during this run"), so run it outside the nightly window.
# It re-runs setup.sh, which only does what is missing (new folders, the secrets key, settings).
set -euo pipefail
sudo -u nightshift git -C /opt/nightshift pull --ff-only
DOMAIN="$(sudo sed -n 's#^PUBLIC_URL=https://##p' /etc/nightshift.env)" bash /opt/nightshift/deploy/server/setup.sh
systemctl --no-pager --lines=5 status nightshift
