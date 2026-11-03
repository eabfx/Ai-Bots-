#!/usr/bin/env bash
# Gets the newest Kelly Terminal from GitHub and restarts it. Run by hand, or by the hourly timer that setup-server.sh installs.
# If the new version does not come up healthy, it goes back to the one that was running. Your journal is never touched.
set -uo pipefail
APP=/opt/riskdesk
cd "$APP"
old=$(git rev-parse HEAD)
git fetch -q origin || { echo "could not reach GitHub, staying on $old"; exit 0; }
if ! git merge --ff-only -q origin/main 2>/dev/null; then echo "cannot fast-forward, staying on $old"; exit 0; fi
new=$(git rev-parse HEAD)
[ "$old" = "$new" ] && { echo "already up to date ($new)"; exit 0; }

host=$(python3 - <<'PY'
import json
try:
    print((json.load(open("/opt/riskdesk/trader/config.json", encoding="utf-8-sig")).get("allowed_hosts") or ["127.0.0.1:8765"])[0])
except Exception:
    print("127.0.0.1:8765")
PY
)
healthy() {                                # the page answers with a page or a redirect to sign in
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        code=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $host" http://127.0.0.1:8765/ || true)
        case "$code" in 200|302) return 0 ;; esac
        sleep 2
    done
    return 1
}
systemctl restart riskdesk
if healthy; then
    echo "updated $old -> $new"
else
    echo "the new version did not start, going back to $old" >&2
    git reset --hard -q "$old"
    systemctl restart riskdesk
    exit 1
fi
