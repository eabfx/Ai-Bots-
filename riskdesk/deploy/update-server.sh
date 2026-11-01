#!/usr/bin/env bash
# Run as root on the server to get the newest RiskDesk from GitHub and restart it. Your journal is not touched.
set -euo pipefail
git -C /opt/riskdesk pull --ff-only
systemctl restart riskdesk
sleep 2
systemctl is-active riskdesk
