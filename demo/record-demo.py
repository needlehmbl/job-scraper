"""Scripted video demo of the job-scraper dashboard (single take).

Drives a real Chromium (1366x768, Playwright video recording) through a
non-mutating tour, then clicks "Scrape new jobs", waits for the real
scrape to finish, and tours the result modal. Post-production
(see demo/assemble.sh) cuts the long scraping middle into a time-lapse
jump cut.

Usage:
    venv/bin/python demo/record-demo.py                # full take (tour + scrape + modal)
    venv/bin/python demo/record-demo.py --tour-only    # tour scenes only (~3 min)
    venv/bin/python demo/record-demo.py --scrape-only  # scrape click + wait + modal only

Output: demo/takes/full.webm / tour.webm / scrape.webm. A FULL_DONE
sentinel file is written next to the take when the script exits cleanly
(video finalized) -- poll for it when running detached via nohup.
"""
import re
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

DASH = "http://localhost:5173"
API = "http://127.0.0.1:8000"
W, H = 1366, 768

TAKES = Path(__file__).resolve().parent / "takes"
TAKES.mkdir(parents=True, exist_ok=True)


def glide(page, locator, steps: int = 12, pause_ms: int = 25):
    """Move the cursor to an element in small steps (human-like)."""
    box = locator.first.bounding_box()
    if not box:
        return
    x1, y1 = page.mouse._x if hasattr(page.mouse, "_x") else 0, 0
    tx, ty = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    for i in range(1, steps + 1):
        page.mouse.move(x1 + (tx - x1) * i / steps, y1 + (ty - y1) * i / steps)
        page.wait_for_timeout(pause_ms)


def click(page, locator, after_ms: int = 1200):
    glide(page, locator)
    locator.first.click()
    page.wait_for_timeout(after_ms)


def tour(page):
    page.goto(DASH, wait_until="networkidle")
    page.wait_for_timeout(2500)

    # 1. Header + stats overview, slow scroll through the tables.
    page.wait_for_timeout(1500)
    page.mouse.wheel(0, 500)
    page.wait_for_timeout(1200)
    page.mouse.wheel(0, 500)
    page.wait_for_timeout(1200)
    page.mouse.wheel(0, -1000)
    page.wait_for_timeout(1000)

    # 2. Search + hide-pill filter (local UI state only).
    search = page.locator("input[type=search]").first
    click(page, search, after_ms=600)
    search.fill("python")
    page.wait_for_timeout(2500)
    search.fill("")
    page.wait_for_timeout(1200)

    # 3. Sort by fit, then page forward and back.
    try:
        page.locator("button[title^='Sort by fit']").first.scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        click(page, page.locator("button[title^='Sort by fit']"), after_ms=1500)
    except Exception as e:
        print("skip FIT sort:", e)
    for name in ("Next", "Prev"):
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^{name}"))
            if btn.count():
                click(page, btn.first, after_ms=1500)
        except Exception as e:
            print(f"skip {name}:", e)

    # 4. Duplicate scan.
    click(page, page.get_by_role("button", name=re.compile(r"Scan for duplicates")), after_ms=2500)
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(2500)
    page.mouse.wheel(0, -400)
    page.wait_for_timeout(800)

    # 5. Filtered tab.
    click(page, page.get_by_role("button", name=re.compile(r"Filtered for review")), after_ms=2500)
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(1500)

    # 6. Applications + Resumes tabs.
    for pat in (r"Applications", r"Resumes"):
        try:
            btn = page.get_by_role("button", name=re.compile(pat))
            if btn.count():
                click(page, btn.first, after_ms=2500)
        except Exception as e:
            print(f"skip tab {pat}:", e)

    # 7. Back to Jobs, dark mode on/off, download xlsx.
    click(page, page.get_by_role("button", name=re.compile(r"^Jobs \(")), after_ms=1500)
    try:
        dark = page.locator("button[title^='Switch to']").first
        dark.scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        click(page, page.locator("button[title^='Switch to']"), after_ms=1500)
        click(page, page.locator("button[title^='Switch to']"), after_ms=1200)
    except Exception as e:
        print("skip dark mode:", e)
    try:
        dl = page.get_by_role("button", name="Download xlsx")
        glide(page, dl)
        page.wait_for_timeout(800)
        # Don't consume the download on camera; just showcase the button.
    except Exception as e:
        print("skip download hover:", e)
    page.wait_for_timeout(1000)


def scrape_status() -> dict:
    with urllib.request.urlopen(API + "/scrape/status", timeout=15) as r:
        import json
        return json.loads(r.read().decode())


def run(mode: str):
    names = {"full": "full.webm", "tour": "tour.webm", "scrape": "scrape.webm"}
    out = TAKES / names[mode]
    if out.exists():
        out.unlink()
    sentinel = TAKES / f"{mode.upper()}_DONE"
    if sentinel.exists():
        sentinel.unlink()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": W, "height": H},
            record_video_dir=str(TAKES),
            record_video_size={"width": W, "height": H},
        )
        page = ctx.new_page()
        if mode in ("full", "tour"):
            tour(page)

        if mode in ("full", "scrape"):
            if mode == "scrape":
                # No tour ran, so navigate here (tour() does this itself).
                page.goto(DASH, wait_until="networkidle")
                page.wait_for_timeout(2500)
            # 8. Real scrape, on camera.
            st = scrape_status()
            assert st.get("state") != "running", "a scrape is already running"
            click(page, page.get_by_role("button", name="Scrape new jobs"), after_ms=2000)
            # ~60s of live progress UI (stage / term / progress bar).
            page.wait_for_timeout(60000)
            # 9. Wait out the rest off-camera (footage gets cut in post).
            print("scrape running; waiting for completion...", flush=True)
            deadline = time.time() + 90 * 60
            while time.time() < deadline:
                page.wait_for_timeout(60000)
                try:
                    st = scrape_status()
                except Exception as e:
                    print("status poll failed:", e, flush=True)
                    continue
                print(" ...still", st.get("state"), flush=True)
                if st.get("state") not in ("running", "idle"):
                    break
            # 10. Result modal tour (auto-opened on completion).
            page.wait_for_timeout(3000)
            try:
                details = page.get_by_role("dialog", name=re.compile(r"Scrape"))
                if details.count():
                    glide(page, details)
                    page.wait_for_timeout(1500)
                    page.mouse.wheel(0, 400)
                    page.wait_for_timeout(2000)
                    page.mouse.wheel(0, 400)
                    page.wait_for_timeout(2000)
            except Exception as e:
                print("modal tour skipped:", e)
            page.wait_for_timeout(2000)

        # Finalize recording, rename the take.
        vpath = page.video.path()
        ctx.close()
        browser.close()
        Path(vpath).rename(out)
        sentinel.write_text("ok\n")
        print("saved", out, flush=True)


if __name__ == "__main__":
    mode = "full"
    if "--tour-only" in sys.argv:
        mode = "tour"
    elif "--scrape-only" in sys.argv:
        mode = "scrape"
    run(mode)
