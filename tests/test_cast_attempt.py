"""A requestSession rejection stays visible as 'Last cast attempt ...' on the Start screen, player glass and debug line."""
import sys, os, re
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts
url = sys.argv[1]
MOCK = open(os.path.join(os.path.dirname(__file__), "mock_cast_sender.js")).read()
fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.goto(url); pg.wait_for_timeout(2000)
    pg.evaluate("()=>{const c=cast.framework.CastContext.getInstance(); c.requestSession=()=>Promise.reject('session_error')}")
    pg.locator("button.cast-start").click(); pg.wait_for_timeout(500)
    st = pg.text_content("#start-screen .cast-status") if pg.locator("#start-screen .cast-status").count() else pg.locator("button.cast-start ~ .cast-status").first.text_content()
    print("start status:", st)
    check("Start screen shows last attempt with code and reason", re.search(r"Last cast attempt \d\d:\d\d: session_error \(TV found, but the receiver failed to load\)", st or "") is not None)
    check("receiver failed text still shown", "receiver failed to load" in (st or ""))
    bar = pg.evaluate("()=>[...document.querySelectorAll('.bar-status')].map(e=>e.textContent).join('')")
    check("player glass status line shows it too", "Last cast attempt" in bar and "session_error" in bar)
    check("Cast control marked failed, still labeled Cast to your TV", pg.get_attribute("button.cast-start", "data-state") == "failed" and pg.text_content("button.cast-start .cast-label") == "Cast to your TV")
    pg.evaluate("()=>__mockCast.start()"); pg.wait_for_timeout(300)
    check("remote debug line includes it", "Last cast attempt" in pg.text_content("#remote-debug"))
    b.close()
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails else 0)
