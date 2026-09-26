"""Cross-platform JobScraper launcher (default browser, beginner-safe).

Ensures .env/config.yaml (runs setup_wizard.py if missing), ensures the
backend is up, waits for GET /health, then opens the dashboard in the OS
default browser. Blocking: Ctrl+C or the dashboard power button stops it.
"""
import argparse, os, subprocess, sys, time, urllib.request, webbrowser
from pathlib import Path


def base_dir():
    # PyInstaller onedir: data files sit next to the exe, __file__ points
    # into the temp _MEI bundle dir -- use the exe's dir when frozen.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = base_dir()
API_HEALTH = "http://127.0.0.1:8000/health"
DASH_URL = "http://localhost:5173"


def api_alive():
    try:
        with urllib.request.urlopen(API_HEALTH, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def wait_ready(timeout=60):
    for _ in range(timeout):
        if api_alive():
            return True
        time.sleep(1)
    return False


def ensure_config():
    if not (ROOT / ".env").exists():
        print("[launcher] first run -- opening setup wizard.")
        subprocess.run([sys.executable, str(ROOT / "setup_wizard.py")], check=False)


def compose_up():
    subprocess.run(["docker", "compose", "up", "-d"], cwd=ROOT, check=False)


def check_updates():
    import json
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/needlehmbl/job-scraper/releases/latest",
            headers={"User-Agent": "jobscraper-launcher"})
        with urllib.request.urlopen(req, timeout=10) as r:
            latest = json.load(r)["tag_name"]
        print(f"latest release: {latest}")
    except Exception as e:
        print(f"[launcher] update check failed: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-updates", action="store_true")
    ap.add_argument("--native-fallback", action="store_true",
                    help="skip docker, expect API started externally")
    args = ap.parse_args()
    if args.check_updates:
        return check_updates()
    ensure_config()
    api_proc = None
    if not args.native_fallback:
        try:
            subprocess.run(["docker", "info"], capture_output=True, check=True)
            compose_up()
        except Exception:
            # Docker not available — start the API ourselves (packaged exe / native fallback).
            api_proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api.main:app",
                 "--host", "127.0.0.1", "--port", "8000"],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(3)
    if not wait_ready():
        print("[launcher] API did not become ready -- see api.log.")
        if api_proc:
            api_proc.terminate()
        return 1
    print(f"[launcher] opening {DASH_URL} in your default browser.")
    webbrowser.open(DASH_URL)
    print("[launcher] running -- close this window or use the dashboard power button to stop.")
    try:
        while True:
            time.sleep(5)
            if not api_alive():
                # The dashboard power button (POST /shutdown) stops the API, so
                # this is how the launcher learns it should exit.
                print("[launcher] API stopped -- shutting down.")
                break
    except KeyboardInterrupt:
        pass
    finally:
        if api_proc:
            api_proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
