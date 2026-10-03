#!/usr/bin/env bash
# JobScraper first-time setup (Linux): wizard -> desktop shortcut -> DB.
# Usage: ./scripts/setup.sh [--dir PATH] | ./scripts/setup.sh --check | ./scripts/setup.sh --upgrade TARBALL
set -e
cd "$(dirname "$0")/.."
python3 setup_wizard.py "$@"
if [ "${1:-}" != "--check" ]; then
  sed "s|^Exec=.*|Exec=$(pwd)/scripts/run.sh|" scripts/JobScraper.desktop > ~/.local/share/applications/JobScraper.desktop
fi
