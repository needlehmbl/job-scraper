#!/usr/bin/env bash
# Stop the job-scraper dashboard stack (API + dev server) and clean up any
# leftover apply_helper.py / Playwright browser processes.
#
# The API + dashboard run as systemd user units (job-dashboard-api.service,
# job-dashboard-web.service). This script stops them (they'll restart with
# the next `systemctl --user start` / login).
#
# Usage:
#   ./stop.sh                     # stop everything
#   ./stop.sh --keep-log          # same, but leave api.log/dashboard.log alone
set -e

cd "$(dirname "$0")"

stopped=0

# 0. Compose stack (no-op when Docker or the stack is absent)
if docker compose ps >/dev/null 2>&1; then
    echo "[stop] stopping compose stack"
    docker compose down 2>/dev/null || true
    stopped=1
fi

# 1. systemd user units for the API + dashboard (disables auto-restart while
#    stopped; re-enable with run_and_open.sh, systemctl --user start, or login)
for unit in job-dashboard-api job-dashboard-web; do
    if systemctl --user is-active --quiet "$unit.service" 2>/dev/null; then
        echo "[stop] stopping systemd unit $unit.service"
        systemctl --user stop "$unit.service" 2>/dev/null || true
        stopped=1
    fi
done

# 2. Fallback: kill any manually-started copies (nohup/legacy run_and_open.sh)
for pid in $(pgrep -f "uvicorn api.main:app" || true); do
    echo "[stop] stopping API (pid $pid)"
    kill "$pid" 2>/dev/null || true
    stopped=1
done
for pid in $(pgrep -f "node .*vite" || true); do
    echo "[stop] stopping dashboard dev server (pid $pid)"
    kill "$pid" 2>/dev/null || true
    stopped=1
done

# 3. apply_helper.py + the Playwright browser it opens
for pid in $(pgrep -f "apply_helper.py" || true); do
    echo "[stop] stopping apply_helper (pid $pid)"
    kill "$pid" 2>/dev/null || true
    stopped=1
done
for pid in $(pgrep -f "playwright_chromiumdev_profile" || true); do
    echo "[stop] stopping Playwright browser (pid $pid)"
    kill -9 "$pid" 2>/dev/null || true
    stopped=1
done

if [ "$stopped" = "0" ]; then
    echo "[stop] nothing was running -- dashboard stack is already stopped."
else
    echo "[stop] done. Restart with: systemctl --user start job-dashboard-api job-dashboard-web"
fi