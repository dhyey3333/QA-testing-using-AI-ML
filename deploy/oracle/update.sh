#!/usr/bin/env bash
# Puts the latest Nightshift from GitHub on the server and restarts it. Runs already going are
# marked failed ("the server restarted during this run"), so run it outside the nightly window.
set -euo pipefail
sudo -u nightshift git -C /opt/nightshift pull --ff-only
sudo -u nightshift env HOME=/home/nightshift /home/nightshift/.local/bin/uv --directory /opt/nightshift sync --no-dev -q
sudo cp /opt/nightshift/deploy/oracle/nightshift.service /etc/systemd/system/nightshift.service
sudo systemctl daemon-reload
sudo systemctl restart nightshift
systemctl --no-pager --lines=5 status nightshift
