"""Loads the live receiver.html (NO ?debug) so the REAL gstatic CAF receiver framework runs. Needs network.
On the old code (listener before start) the strip said "CAF FAILED ... Invalid messageType for the namespace"."""
import sys
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts
url = sys.argv[1]
fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    pg.goto(url.rstrip("/") + "/receiver.html?strip=1"); pg.wait_for_timeout(6000)  # no ?debug; ?strip=1 shows the strip and still starts CAF
    strip = pg.text_content("#debug-strip") or ""
    print("strip:", strip)
    check("real CAF framework loaded", pg.evaluate("()=>!!(window.cast && cast.framework && cast.framework.CastReceiverContext)"))
    check("strip has no FAILED", "FAILED" not in strip)
    check("no Invalid messageType", "Invalid messageType" not in strip)
    check("listener registered (or ready)", "listener registered" in strip or "listener ready" in strip)
    check("strip shows build 2026-11-17.18", "build 2026-11-17.18" in strip)
    b.close()
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails else 0)
