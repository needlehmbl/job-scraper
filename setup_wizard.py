"""JobScraper first-time setup wizard (beginner-friendly).

Tkinter GUI on Windows (or anywhere a display exists), CLI prompts on
Linux. Invoked by setup.sh, the Inno finish page, and launcher.py when
.env is missing. Same validators + config round-trip in both modes.

Usage:
  python3 setup_wizard.py [--dir PATH]
  python3 setup_wizard.py --check
  python3 setup_wizard.py --upgrade TARBALL
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import webbrowser
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog
except ImportError:
    tk = None

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"
KEY_URL = "https://openrouter.ai/keys"
KEY_TEST_URL = "https://openrouter.ai/api/v1/auth/key"

BOARDS = ["indeed", "linkedin", "jobstreet", "glassdoor", "trabajo"]

BOARD_HINTS = {
    "indeed": "Indeed -- biggest listings",
    "linkedin": "LinkedIn -- professional roles",
    "jobstreet": "JobStreet -- Philippines-focused",
    "glassdoor": "Glassdoor -- company reviews + jobs",
    "trabajo": "Trabajo.org -- aggregator, extra leads",
}

PROVIDER_KEYS = ["OPENROUTER_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "GEMINI_API_KEY"]


def validate_terms_location_boards(terms, location, boards):
    """Return True when valid, else a plain-language error string."""
    if not [t for t in terms if t.strip()]:
        return "Add at least one job title, e.g. Junior Developer."
    if not location.strip():
        return "Type the location as you'd type it on a job site, e.g. Metro Manila, Philippines."
    if not [b for b in boards if b in BOARDS]:
        return "Tick at least one job board."
    return True


def validate_key(key):
    """Return True when the key looks usable, else a plain-language error."""
    if not key.strip():
        return True  # empty + Skip = AI filter stays off, not an error
    if len(key.strip()) < 20:
        return "That key looks too short -- copy the full key from the OpenRouter keys page."
    return True


def load_config(path=None):
    path = Path(path) if path else CONFIG_PATH
    if yaml is None:
        print("PyYAML is missing -- install it with: pip install pyyaml")
        sys.exit(1)
    with open(path) as f:
        return yaml.safe_load(f) or {}


def save_config(cfg, path=None):
    path = Path(path) if path else CONFIG_PATH
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)


def load_env(path=None):
    path = Path(path) if path else ENV_PATH
    vals = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def save_env_key(key_name, value, path=None):
    """Write one provider key, preserving every other line in .env."""
    path = Path(path) if path else ENV_PATH
    lines = path.read_text().splitlines() if path.exists() else []
    out, written = [], False
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == key_name:
            out.append(f"{key_name}={value}")
            written = True
        else:
            out.append(line)
    if not written:
        out.append(f"{key_name}={value}")
    path.write_text("\n".join(out) + "\n")


def test_key_live(key):
    """GET the OpenRouter key-auth endpoint. Returns (ok, message)."""
    req = urllib.request.Request(KEY_TEST_URL, headers={"Authorization": f"Bearer {key.strip()}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status == 200:
                return True, "Key works"
            return False, f"OpenRouter said no (HTTP {r.status}) -- check the key and try again."
    except Exception as e:
        return False, f"Could not check the key ({e}) -- check your internet and try again."


def check_db():
    """Try DATABASE_URL, else a local Postgres socket/port. Returns (ok, message)."""
    url = os.environ.get("DATABASE_URL", "")
    host, port = "127.0.0.1", 5432
    if url:
        try:
            rest = url.split("@")[-1].split("/")[0]
            if ":" in rest:
                host, port = rest.split(":", 1)
                port = int(port)
            else:
                host = rest or host
        except Exception:
            pass
    try:
        socket.create_connection((host, port), timeout=3).close()
        return True, f"Database reachable at {host}:{port}."
    except Exception:
        pass
    if Path("/var/run/postgresql/.s.PGSQL.5432").exists():
        return True, "Database reachable via local socket."
    return False, "No database found -- run the Dependencies step (docker compose up -d db) first."


def do_check():
    """Validate .env + config.yaml + DB, print each result, exit 1 on failure."""
    ok = True
    if not ENV_PATH.exists():
        print("No .env yet -- run ./setup.sh")
        return 1
    env = load_env()
    key = env.get("OPENROUTER_API_KEY", "")
    r = validate_key(key)
    if r is True:
        print("API key: OK (looks usable)" if key.strip() else "API key: skipped -- AI filter stays off.")
    else:
        print(f"API key: PROBLEM -- {r}")
        ok = False
    if not CONFIG_PATH.exists():
        print("config.yaml: PROBLEM -- file not found.")
        return 1
    try:
        cfg = load_config()
    except Exception as e:
        print(f"config.yaml: PROBLEM -- could not read it ({e})")
        return 1
    search = cfg.get("search", {}) if isinstance(cfg, dict) else {}
    r = validate_terms_location_boards(
        search.get("search_terms", []), str(search.get("location", "")), search.get("site_names", []))
    if r is True:
        print("Search terms / location / boards: OK")
    else:
        print(f"Search config: PROBLEM -- {r}")
        ok = False
    db_ok, db_msg = check_db()
    print(f"Database: {'OK' if db_ok else 'PROBLEM'} -- {db_msg}")
    ok = ok and db_ok
    return 0 if ok else 1


def _safe_members(tf):
    """Yield tarball members, rejecting absolute paths, '..' escapes, and links.

    Fallback for system Pythons older than 3.12 that lack TarFile.extractall(filter=).
    """
    for member in tf.getmembers():
        if os.path.isabs(member.name) or ".." in Path(member.name).parts:
            raise ValueError(f"Unsafe file in update: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"Unsafe link in update: {member.name}")
        yield member


def do_upgrade(tarball, dest_dir=None):
    """Extract tarball over dest_dir, preserving .env + config.yaml."""
    dest = Path(dest_dir) if dest_dir else ROOT
    tarball = Path(tarball)
    if not tarball.exists():
        print(f"Update file not found: {tarball}")
        return 1
    backup = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in (".env", "config.yaml"):
            src = dest / name
            if src.exists():
                dst = Path(tmp) / name
                shutil.copy2(src, dst)
                backup[name] = dst.read_bytes()
    try:
        with tarfile.open(tarball, "r:*") as tf:
            try:
                tf.extractall(dest, filter="data")
            except TypeError:  # system Python older than 3.12: no filter= support
                for member in _safe_members(tf):
                    tf.extract(member, dest)
    except Exception as e:
        print(f"Could not unpack the update ({e})")
        return 1
    for name, data in backup.items():
        (dest / name).write_bytes(data)
    print("Update keeps everything -- your jobs, key, and search terms stay.")
    return 0


def ensure_deps_cli(install_dir, say=None, confirm_retry=None, ask_text=None):
    """Dependencies step: Docker -> winget -> native instructions. Returns True on OK.

    say/confirm_retry/ask_text are UI hooks so the Tkinter wizard reuses this
    same routine: CLI defaults are print/input; the GUI passes a status-label
    updater and messagebox dialogs instead.
    """
    say = say or print
    _cli_retry = lambda msg: retry_prompt()  # noqa: E731
    confirm_retry = confirm_retry or _cli_retry
    ask_text = ask_text or (lambda prompt: input(prompt))
    say("\n--- Dependencies: getting the database and browser ready ---")
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True)
        say("Docker found -- starting the database...")
        subprocess.run(["docker", "compose", "up", "-d", "db"], cwd=str(install_dir), check=True)
        say("Waiting for the database to be ready...")
        for _ in range(30):
            db_ok, _ = check_db()
            if db_ok:
                break
            import time
            time.sleep(2)
        schema = install_dir / "schema.sql"
        if schema.exists():
            subprocess.run(["docker", "compose", "exec", "-T", "db",
                            "psql", "-U", "postgres", "-d", "job_scraper",
                            "-f", "/schema.sql"], cwd=str(install_dir), check=False)
        say("Database is ready.")
    except Exception:
        if os.name == "nt":
            say("No Docker -- installing Postgres via winget...")
            try:
                subprocess.run(["winget", "install", "--accept-source-agreements",
                                "PostgreSQL.PostgreSQL.16"], check=True)
                subprocess.run(["createdb", "job_scraper"], check=False)
                subprocess.run(["psql", "-d", "job_scraper", "-f", str(install_dir / "schema.sql")],
                               check=False)
                say("Postgres installed and database created.")
            except Exception as e:
                say(f"Postgres install failed ({e}). Try again or ask for help.")
                return confirm_retry("Something didn't finish. Retry?")
        else:
            say("No Docker here, so this machine needs its own Postgres.")
            say("Follow the native-Postgres steps in README.md section 2 (Setup),")
            say("then re-run this wizard. Press Enter when done, or type 'skip'.")
            ans = ask_text("> ").strip().lower()
            if ans != "skip":
                db_ok, db_msg = check_db()
                say(db_msg)
                if not db_ok:
                    return confirm_retry("Something didn't finish. Retry?")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                       check=True, capture_output=True)
        say("Browser engine (Chromium) is ready.")
    except Exception:
        say("Chromium is not installed yet -- the first scrape will install it,")
        say("or run: python3 -m playwright install chromium")
        return confirm_retry("Something didn't finish. Retry?")
    return True


def retry_prompt():
    ans = input("Something didn't finish. Retry? [Y/n] ").strip().lower()
    return ans in ("", "y", "yes")


def run_cli(install_dir):
    if not sys.stdin.isatty():
        print("This wizard needs a terminal. Run it with: ./setup.sh")
        return 1
    print("=== Welcome to JobScraper ===")
    print("Finds junior developer job posts for you. Takes about 5 minutes:")
    print("an API key (optional), what jobs you want, and where to save things.")
    input("Press Enter to start...")

    env = load_env()
    print("\n--- Step 1: OpenRouter key (optional, powers the AI filter) ---")
    print("No key yet? Press 'g' and we open the free key page in your browser.")
    while True:
        cur = env.get("OPENROUTER_API_KEY", "")
        ans = input(f"Paste your key{' [saved]' if cur else ''} (Enter to keep/skip, 'g' to get one, 't' to test): ").strip()
        if ans.lower() == "g":
            webbrowser.open(KEY_URL)
            print("Opened the key page in your default browser -- copy the key and paste it here.")
            continue
        if ans.lower() == "t":
            ok, msg = test_key_live(cur)
            print(msg)
            continue
        key = ans if ans else cur
        r = validate_key(key)
        if r is not True:
            print(r)
            continue
        if key.strip() and key != cur:
            ok, msg = test_key_live(key)
            print(msg)
            if not ok and not retry_prompt():
                continue
        save_env_key("OPENROUTER_API_KEY", key.strip())
        if not key.strip():
            print("Skipped -- the AI filter stays off. You can add a key later.")
        break

    cfg = load_config()
    search = cfg.setdefault("search", {})
    print("\n--- Step 2: What jobs do you want? ---")
    terms = search.get("search_terms", []) or []
    print("Current titles:", ", ".join(terms) if terms else "(none yet)")
    ans = input("Type job titles separated by commas (Enter to keep): ").strip()
    if ans:
        terms = [t.strip() for t in ans.split(",") if t.strip()]
    loc = str(search.get("location", "") or "")
    ans = input(f"Location as you'd type it on a job site [{loc or 'Metro Manila, Philippines'}]: ").strip()
    if ans:
        loc = ans
    if not loc:
        loc = "Metro Manila, Philippines"

    print("\n--- Step 3: Which job boards? ---")
    current = search.get("site_names", []) or []
    for b in BOARDS:
        mark = "[x]" if b in current else "[ ]"
        print(f"  {mark} {b} -- {BOARD_HINTS[b]}")
    ans = input("Type boards separated by commas (Enter to keep): ").strip()
    boards = [b.strip().lower() for b in ans.split(",") if b.strip()] if ans else current
    r = validate_terms_location_boards(terms, loc, boards)
    while r is not True:
        print(r)
        terms_in = input("Job titles (comma-separated): ").strip()
        if terms_in:
            terms = [t.strip() for t in terms_in.split(",") if t.strip()]
        loc_in = input("Location: ").strip()
        if loc_in:
            loc = loc_in
        boards_in = input("Boards (comma-separated): ").strip()
        if boards_in:
            boards = [b.strip().lower() for b in boards_in.split(",") if b.strip()]
        r = validate_terms_location_boards(terms, loc, boards)
    search["search_terms"] = terms
    search["location"] = loc
    search["site_names"] = [b for b in boards if b in BOARDS]
    save_config(cfg)
    print("Saved your search.")

    print("\n--- Step 4: Where to install ---")
    print(f"Install folder: {install_dir}")
    install_dir.mkdir(parents=True, exist_ok=True)

    if not ensure_deps_cli(install_dir):
        print("Setup stopped -- run ./setup.sh again when ready.")
        return 1

    print("\n--- Done! ---")
    print("Open JobScraper with ./run.sh (it opens in your default browser).")
    return 0


def run_gui(install_dir):
    """Tkinter wizard: 6 pages with Back/Next per spec section 4."""
    if tk is None:
        print("No graphical toolkit found -- run this wizard with: ./setup.sh")
        return 1
    env = load_env()
    cfg = load_config()
    search = cfg.setdefault("search", {})
    state = {
        "key": env.get("OPENROUTER_API_KEY", ""),
        "terms": list(search.get("search_terms", []) or []),
        "location": str(search.get("location", "") or "Metro Manila, Philippines"),
        "boards": {b: (b in (search.get("site_names", []) or [])) for b in BOARDS},
        "page": 0,
    }

    root = tk.Tk()
    root.title("JobScraper setup")
    root.geometry("520x420")
    frames = []

    def show(i):
        state["page"] = i
        for j, f in enumerate(frames):
            if j == i:
                f.pack(fill="both", expand=True, padx=16, pady=12)
            else:
                f.pack_forget()
        back_btn["state"] = "normal" if i > 0 else "disabled"
        next_btn["text"] = "Finish" if i == len(frames) - 1 else "Next >"

    def on_next():
        i = state["page"]
        if i == 1:
            r = validate_key(key_var.get())
            if r is not True:
                messagebox.showerror("Key problem", r)
                return
            state["key"] = key_var.get().strip()
        if i == 2:
            terms = [t.strip() for t in terms_box.get("1.0", "end").splitlines() if t.strip()]
            r = validate_terms_location_boards(terms, loc_var.get(), selected_boards())
            if r is not True:
                messagebox.showerror("Check this", r)
                return
            state["terms"] = terms
            state["location"] = loc_var.get()
        if i == 3:
            if not selected_boards():
                messagebox.showerror("Check this", "Tick at least one job board.")
                return
        if i == len(frames) - 1:
            finish()
            return
        show(i + 1)

    def selected_boards():
        return [b for b in BOARDS if state["boards"].get(b)]

    def finish():
        save_env_key("OPENROUTER_API_KEY", state["key"])
        search["search_terms"] = state["terms"]
        search["location"] = state["location"]
        search["site_names"] = selected_boards()
        save_config(cfg)
        install_dir.mkdir(parents=True, exist_ok=True)
        # Dependencies step: same routine the text wizard uses, with progress
        # shown on the install page and plain-language Retry dialogs on failure.
        show(4)
        next_btn["state"] = "disabled"
        back_btn["state"] = "disabled"
        deps_status.set("Starting...")
        root.update_idletasks()

        def gui_say(msg):
            deps_status.set((deps_status.get() + "\n" + msg).strip())
            root.update_idletasks()

        ok = ensure_deps_cli(
            install_dir,
            say=gui_say,
            confirm_retry=lambda msg: messagebox.askretrycancel("Setup", msg + "\nRetry?"),
            ask_text=lambda prompt: simpledialog.askstring("Setup", prompt) or "",
        )
        next_btn["state"] = "normal"
        back_btn["state"] = "normal"
        if not ok:
            messagebox.showinfo("Setup", "Setup stopped -- run the wizard again when ready.")
            return
        messagebox.showinfo("Done", "Setup saved! Open JobScraper from the desktop shortcut.")
        root.destroy()

    # Page 0: Welcome
    f0 = tk.Frame(root)
    tk.Label(f0, text="Welcome to JobScraper", font=("TkDefaultFont", 14, "bold")).pack(pady=8)
    tk.Label(f0, text="Finds junior developer job posts for you.\nTakes about 5 minutes:\n"
                      "an API key (optional), what jobs you want,\nand where to save things.",
             justify="left").pack()
    frames.append(f0)

    # Page 1: key
    f1 = tk.Frame(root)
    tk.Label(f1, text="OpenRouter key (optional)", font=("TkDefaultFont", 12, "bold")).pack(pady=4)
    tk.Label(f1, text="Powers the AI filter. Skip and it stays off.").pack()
    key_var = tk.StringVar(value=state["key"])
    tk.Entry(f1, textvariable=key_var, show="*", width=46).pack(pady=6)
    btnrow = tk.Frame(f1)
    btnrow.pack()
    tk.Button(btnrow, text="Get a free key",
              command=lambda: webbrowser.open(KEY_URL)).pack(side="left", padx=4)
    def do_test():
        ok, msg = test_key_live(key_var.get())
        (messagebox.showinfo if ok else messagebox.showerror)("Key test", msg)
    tk.Button(btnrow, text="Test", command=do_test).pack(side="left", padx=4)
    frames.append(f1)

    # Page 2: terms + location
    f2 = tk.Frame(root)
    tk.Label(f2, text="What jobs do you want?", font=("TkDefaultFont", 12, "bold")).pack(pady=4)
    tk.Label(f2, text="One job title per line:").pack()
    terms_box = tk.Text(f2, height=8, width=50)
    terms_box.insert("1.0", "\n".join(state["terms"]))
    terms_box.pack()
    tk.Label(f2, text="Location as you'd type it on a job site:").pack(pady=(6, 0))
    loc_var = tk.StringVar(value=state["location"])
    tk.Entry(f2, textvariable=loc_var, width=46).pack()
    frames.append(f2)

    # Page 3: boards
    f3 = tk.Frame(root)
    tk.Label(f3, text="Which job boards?", font=("TkDefaultFont", 12, "bold")).pack(pady=4)
    board_vars = {}
    for b in BOARDS:
        v = tk.BooleanVar(value=state["boards"][b])
        board_vars[b] = v
        tk.Checkbutton(f3, text=f"{b} -- {BOARD_HINTS[b]}", variable=v,
                       command=lambda b=b: state["boards"].__setitem__(b, board_vars[b].get())
                       ).pack(anchor="w")
    frames.append(f3)

    # Page 4: install dir + deps
    f4 = tk.Frame(root)
    tk.Label(f4, text="Where to install", font=("TkDefaultFont", 12, "bold")).pack(pady=4)
    tk.Label(f4, text=f"Install folder:\n{install_dir}", justify="left").pack()
    tk.Label(f4, text="Next, the wizard gets the database and browser engine ready.\n"
                      "This can take a few minutes.", justify="left").pack(pady=8)
    deps_status = tk.StringVar(value="")
    tk.Label(f4, textvariable=deps_status, justify="left", wraplength=480).pack(pady=4)
    frames.append(f4)

    # Page 5: finish
    f5 = tk.Frame(root)
    tk.Label(f5, text="All set!", font=("TkDefaultFont", 14, "bold")).pack(pady=8)
    tk.Label(f5, text="Press Finish to save, then open JobScraper\nfrom the desktop shortcut.").pack()
    frames.append(f5)

    nav = tk.Frame(root)
    nav.pack(side="bottom", fill="x", padx=16, pady=12)
    back_btn = tk.Button(nav, text="< Back", command=lambda: show(state["page"] - 1))
    back_btn.pack(side="left")
    next_btn = tk.Button(nav, text="Next >", command=on_next)
    next_btn.pack(side="right")

    show(0)
    root.mainloop()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="JobScraper setup wizard")
    ap.add_argument("--check", action="store_true", help="validate .env/config.yaml/DB")
    ap.add_argument("--upgrade", metavar="TARBALL", help="install update, keep config")
    ap.add_argument("--dir", default=str(ROOT), help="install directory")
    args = ap.parse_args(argv)
    if args.check:
        return do_check()
    if args.upgrade:
        return do_upgrade(args.upgrade, args.dir if args.dir != str(ROOT) else None)
    install_dir = Path(args.dir).expanduser()
    if tk is not None and (os.name == "nt" or os.environ.get("DISPLAY")):
        try:
            return run_gui(install_dir)
        except Exception as e:
            print(f"Could not open the window ({e}) -- using text mode.")
    return run_cli(install_dir)


if __name__ == "__main__":
    raise SystemExit(main())
