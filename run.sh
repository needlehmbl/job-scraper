#!/usr/bin/env bash
# JobScraper launcher (Linux + macOS): start the stack, open the default browser.
# Usage: ./run.sh [--native]  (--native reuses systemd/nohup like run_and_open.sh)
set -e
cd "$(dirname "$0")"
# Prefer the venv the setup script created (macOS system python3 is often 3.9).
if [ -x venv/bin/python ]; then
  PY=venv/bin/python
else
  PY=python3
fi
if [ "${1:-}" = "--native" ]; then
  exec "$PY" launcher.py --native-fallback
fi
exec "$PY" launcher.py
