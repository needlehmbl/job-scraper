#!/bin/bash
# One-time setup for macOS (Apple Silicon and Intel both work).
# Run it from Terminal:
#   ./setup-mac.sh
set -e
cd "$(dirname "$0")" || exit 1

say() { printf '%s\n' "$*"; }

say ""
say "JobScraper setup for macOS"
say "============================"
say ""

# --- 1. Homebrew (the package manager every other step leans on) ---------
# Needed for Python and for Postgres when Docker isn't present. Installed
# here so a beginner never has to visit brew.sh by hand.
if ! command -v brew >/dev/null 2>&1; then
  say "Installing Homebrew (this is the macOS package manager)..."
  say "You may be asked for your Mac password -- that is normal."
  say ""
  if ! command -v curl >/dev/null 2>&1; then
    say "curl is missing, so we can't fetch Homebrew automatically."
    say "Open https://brew.sh, paste the line it gives you, then re-run this script."
    exit 1
  fi
  NONINTERACTIVE=1 /bin/bash -c \
    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    || {
      say "Homebrew install did not finish (it may have asked for a password you missed)."
      say "Open https://brew.sh and paste the install line, then re-run this script."
      exit 1
    }
  # A just-installed brew isn't on PATH in this shell yet. Apple Silicon puts
  # it in /opt/homebrew, Intel in /usr/local.
  for p in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$p" ]; then
      eval "$("$p" shellenv)"
      break
    fi
  done
  if ! command -v brew >/dev/null 2>&1; then
    say "Homebrew installed but isn't on PATH in this shell."
    say "Open a new Terminal window and run this script again."
    exit 1
  fi
  say "Homebrew installed."
  say ""
else
  say "Homebrew found."
  say ""
fi

# --- 2. Python 3.12+ ------------------------------------------------------
PY=""
for cand in python3.12 python3.13 python3.14 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' 2>/dev/null; then
      PY="$cand"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  say "Installing Python 3.12 (JobScraper needs 3.12 or newer)..."
  brew install python@3.12 || {
    say "Python install failed. Try again, or ask for help."
    exit 1
  }
  PY="python3.12"
fi

say "Using $($PY --version) from $(command -v "$PY")"
say ""

# --- 3. Python packages ---------------------------------------------------
say "Installing Python packages (first run takes a few minutes)..."
if [ ! -d venv ]; then
  "$PY" -m venv venv
fi
./venv/bin/python -m pip install --quiet --upgrade pip
./venv/bin/python -m pip install --quiet -r requirements.txt
say "Python packages are ready."
say ""

# --- 4. The wizard (search terms, location, boards, database) ------------
say "Now the setup wizard -- it asks a few questions and sets up the database."
say "You can skip the API key; the scraper works without one."
say ""
if ! ./venv/bin/python setup_wizard.py --dir "$(pwd)"; then
  say ""
  say "The wizard didn't finish, so the shortcut wasn't created. Fix what it"
  say "reported above, then run ./setup-mac.sh again -- nothing is lost."
  exit 1
fi

# --- 5. Desktop shortcut --------------------------------------------------
APPS="$HOME/Applications"
mkdir -p "$APPS"
cp -f JobScraper.command "$APPS/JobScraper.command"
chmod +x "$APPS/JobScraper.command"
# Drop the quarantine flag so Finder opens it without a Gatekeeper prompt.
xattr -cr "$APPS/JobScraper.command" 2>/dev/null || true
say ""
say "Added a launcher:  $APPS/JobScraper.command"
say ""

# --- 6. Done -------------------------------------------------------------
say "============================================"
say " Setup finished. You're ready to go."
say "============================================"
say ""
say "START IT:   double-click JobScraper in your Applications folder"
say "            (Finder -> Go -> Applications, or press Cmd+Shift+A)"
say "            It opens in your default browser."
say ""
say "STOP IT:    the power button in the dashboard header,"
say "            or quit the Terminal window it opened."
say ""
say "First time? macOS may ask you to allow Terminal to reach your folder"
say "or the network -- choose OK/Allow when it appears."
say ""
