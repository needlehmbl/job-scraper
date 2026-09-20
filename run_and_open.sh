#!/usr/bin/env bash
# Make sure API + dashboard are up and open the dashboard in the default
# browser. Open-only: scraping is done from the dashboard's
# "Scrape new jobs" button (same pipeline as `venv/bin/python main.py`).
#
# Usage:
#   ./run_and_open.sh
#
# Scheduled (timer) auto-open is intentionally NOT wired up anymore -- the
# user scrapped scheduled fires. Run this manually whenever you want to
# review postings right away.
set -e

cd "$(dirname "$0")"

API_URL="http://127.0.0.1:8000"
DASH_URL="http://localhost:5173"
API_LOG="api.log"
DASH_LOG="dashboard.log"

if [ "$#" -gt 0 ]; then
    echo "[run_and_open] note: scraping moved to the dashboard's 'Scrape new jobs' button;" >&2
    echo "[run_and_open] note: ignoring command-line args: $*" >&2
fi

# --- start the API if it isn't already serving ---
if ! curl -sf "$API_URL/health" >/dev/null 2>&1; then
    echo "[run_and_open] starting API (systemd: job-dashboard-api) -> $API_URL"
    if systemctl --user start job-dashboard-api.service 2>/dev/null; then
        : # managed by systemd now
    elif [ -x venv/bin/uvicorn ]; then
        echo "[run_and_open] systemd unavailable -- falling back to nohup uvicorn"
        nohup venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 \
            >> "$API_LOG" 2>&1 &
    else
        nohup python3 -m uvicorn api.main:app --host 127.0.0.1 --port 8000 \
            >> "$API_LOG" 2>&1 &
    fi
fi

# --- start the dashboard dev server if it isn't already serving ---
if ! curl -sf "$DASH_URL" >/dev/null 2>&1; then
    echo "[run_and_open] starting dashboard (systemd: job-dashboard-web) -> $DASH_URL"
    if systemctl --user start job-dashboard-web.service 2>/dev/null; then
        : # managed by systemd now
    elif command -v npm >/dev/null 2>&1; then
        echo "[run_and_open] systemd unavailable -- falling back to nohup vite"
        (cd dashboard && nohup npm run dev > "../$DASH_LOG" 2>&1 &)
    else
        echo "WARNING: npm not found; can't start the dashboard." >&2
    fi
fi

# --- wait briefly for the servers, then open the dashboard ---
for i in $(seq 1 30); do
    if curl -sf "$DASH_URL" >/dev/null 2>&1 && curl -sf "$API_URL/health" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "[run_and_open] opening $DASH_URL"
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$DASH_URL" >/dev/null 2>&1 || true
else
    echo "xdg-open not found -- open $DASH_URL yourself."
fi

echo "[run_and_open] done."