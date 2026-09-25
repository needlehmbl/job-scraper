#!/usr/bin/env bash
# JobScraper first-time setup (Linux): wizard -> desktop shortcut -> DB.
# Usage: ./setup.sh [--dir PATH] | ./setup.sh --check | ./setup.sh --upgrade TARBALL
set -e
cd "$(dirname "$0")"
python3 setup_wizard.py "$@"
if [ "${1:-}" != "--check" ]; then
  sed "s|^Exec=.*|Exec=$(pwd)/run.sh|" JobScraper.desktop > ~/.local/share/applications/JobScraper.desktop
fi
