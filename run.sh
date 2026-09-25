#!/usr/bin/env bash
# JobScraper launcher (Linux): start the stack, open the default browser.
# Usage: ./run.sh [--native]  (--native reuses systemd/nohup like run_and_open.sh)
set -e
cd "$(dirname "$0")"
if [ "${1:-}" = "--native" ]; then
  exec python3 launcher.py --native-fallback
fi
exec python3 launcher.py
