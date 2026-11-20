"""Real gstatic CAF receiver against a fake Cast platform (ws://localhost:8008/v2/ipc). Needs network and port 8008.
Checks the whole receiver path: ready lists our namespace, app state refresh, getState/next reach the listener."""
import sys, os, socket, time
sys.path.insert(0, os.path.dirname(__file__))
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts
from fake_cast_platform import FakePlatform, NS, SYS, PORT
import yt_mode  # WS_FAKE_YOUTUBE=1 swaps in tests/fake_youtube.js (CI); the CAF receiver stays real
yt_mode.announce()
url = sys.argv[1]
fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)
s = socket.socket()
try:
    s.bind(("localhost", PORT)); s.close()
except OSError:
    print(f"SKIP: port {PORT} is busy, cannot run the fake Cast platform"); sys.exit(0)
fp = FakePlatform("sender-1")
if not fp.start():
    print(f"SKIP: could not start fake platform: {fp.error}"); sys.exit(0)
def states(): return [d for ns, sid, d in fp.parsed() if ns == NS and isinstance(d, dict) and d.get("type") == "state"]
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    yt_mode.route(pg)
    pg.goto(url.rstrip("/") + "/receiver.html?strip=1")  # no ?debug; ?strip=1 shows the strip and still starts CAF
    if not fp.connected.wait(20):
        print("FAIL CAF never connected to the fake platform"); sys.exit(1)
    pg.wait_for_timeout(1500)
    fp.system_ready(); pg.wait_for_timeout(500)
    fp.sender_connected(); pg.wait_for_timeout(1500)
    sys_msgs = [d for ns, sid, d in fp.parsed() if ns == SYS and isinstance(d, dict)]
    readies = [d for d in sys_msgs if d.get("type") == "ready"]
    check("(a) outgoing system ready lists our namespace", any(NS in (d.get("activeNamespaces") or []) for d in readies))
    check("(b) receiver sends system setappstate after ready + senderconnected", any(d.get("type") == "setappstate" for d in sys_msgs))
    apps = [d for d in sys_msgs if d.get("type") == "setappstate"]
    texts = [d.get("statusText") for d in apps]
    print("setappstate texts:", texts)
    check("(b2) two distinct setappstate statusText values, both differ from initial", len(set(texts)) >= 2 and "Window Seat" not in texts)
    fp.to_receiver({"type": "getState"}); pg.wait_for_timeout(1500)
    strip = pg.text_content("#debug-strip") or ""; print("strip:", strip)
    import re
    m = re.search(r"rx (\d+)", strip)
    check("(c) getState reached the listener (strip rx >= 1)", bool(m) and int(m.group(1)) >= 1)
    st = states()
    check("(c) state reply with build 2026-11-20.35", any(d.get("build") == "2026-11-20.35" for d in st))
    check("(c) a state reply is addressed to sender-1", any(ns == NS and sid == "sender-1" and isinstance(d, dict) and d.get("type") == "state" for ns, sid, d in fp.parsed()))
    before = [d.get("camId") for d in states()]
    n_before = len(before)
    fp.to_receiver({"type": "next"}); pg.wait_for_timeout(6000)
    after = [d.get("camId") for d in states()][n_before:]
    print("cams before:", before[-1:], "after:", after)
    check("(d) next changes the cam in a later state message", len(after) > 0 and any(c and c != (before[-1] if before else None) for c in after))
    check("(e) strip shows ready-ns yes", "ready-ns yes" in strip)
    b.close()
fp.stop()
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails else 0)
