#!/bin/bash
# Double-click this file in Finder to start JobScraper.
# It runs the app and opens your default browser. To stop it, use the
# power button in the dashboard header (or just close this window).
cd "$(dirname "$0")" || exit 1
exec ./run.sh
