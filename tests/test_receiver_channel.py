"""Receiver message channel with a mocked CAF framework (the real receiver.html, not debug mode).
Proves: listener registered right after start() (the real CAF rejects the other order), namespace declared, string/object/double-encoded
commands applied, direct and broadcast replies sent as JSON text, debug strip updated, and all of it
works even when YouTube is blocked."""
import sys, os, json
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts
url = sys.argv[1]
MOCK = open(os.path.join(os.path.dirname(__file__), "mock_caf_receiver.js")).read()
NS = "urn:x-cast:com.windowseat"
fails, logs = [], []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    pg.route("**/cast_receiver_framework.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.route("**/iframe_api", lambda r: r.abort())  # YouTube unavailable: the channel must still work
    pg.on("pageerror", lambda e: logs.append(str(e)))
    pg.goto(url + ("&" if "?" in url else "?") + "strip=1"); pg.wait_for_timeout(2500)  # the strip is hidden unless Debug mode; ?strip=1 still starts CAF
    calls = pg.evaluate("()=>__caf.calls")
    print("calls:", calls)
    check("listener registered", f"addCustomMessageListener:{NS}" in calls)
    check("listener registered right after start()", calls.index(f"addCustomMessageListener:{NS}") == calls.index("start") + 1)
    check("namespace declared in options", pg.evaluate("()=>__caf.options.customNamespaces")== {NS: "STRING"})
    check("idle timeout disabled", pg.evaluate("()=>__caf.options.disableIdleTimeout") is True)
    strip = lambda: pg.text_content("#debug-strip")
    check("strip shows build and ready", "build " in strip() and "CAF ready" in strip() and "listener ready" in strip())
    pg.evaluate("()=>__caf.connect()")
    check("broadcast on sender connect", any(m["senderId"] is None for m in pg.evaluate("()=>__caf.sent")))
    for label, payload in [("string", json.dumps({"type": "interval", "value": 30})), ("object", {"type": "pin", "value": True}), ("double-encoded", json.dumps(json.dumps({"type": "volume", "value": 25})))]:
        pg.evaluate("()=>__caf.sent.length=0")
        pg.evaluate("([ns,d])=>__caf.message(ns,d,'sender-1')", [NS, payload])
        sent = pg.evaluate("()=>__caf.sent")
        direct = [m for m in sent if m["senderId"] == "sender-1"]
        check(f"{label} payload gets a direct reply", len(direct) == 1)
        check(f"{label} replies are JSON text", all(isinstance(m["data"], str) and json.loads(m["data"])["type"] == "state" for m in sent))
    st = json.loads(pg.evaluate("()=>__caf.sent")[-1]["data"])
    check("state applied (interval 30, pinned, volume 25)", st["interval"] == 30 and st["pinned"] is True and st["volume"] == 25)
    check("state carries build", bool(st.get("build")))
    check("strip shows last in and reply", "last in: volume" in strip() and "last reply: direct" in strip())
    pg.evaluate("([ns])=>__caf.message(ns,'not json','sender-1')", [NS])
    check("garbage payload does not crash, strip notes it", "unreadable" in strip())
    pg.evaluate("([ns])=>__caf.message(ns,JSON.stringify({type:'debug',value:false}),'sender-1')", [NS])
    check("debug command hides the strip and it stays hidden", pg.evaluate("()=>document.querySelector('#debug-strip').hidden"))
    b.close()
print("page errors:", logs)
sys.exit(1 if fails or logs else 0)
