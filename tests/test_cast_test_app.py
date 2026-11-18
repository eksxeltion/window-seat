"""Cast test receiver opt-in: ?castApp=test and the About "Cast test receiver" switch pick the unpublished test app
(CEB296EE) over production (48BEB59E), persist it, and every session start or app check uses the active ID.
Receiver side: receiver.html?strip=1&pauseWarnMs=...&pauseEndMs=... (no debug=1) starts CAF, shows the app ID,
and the short pause timer warns and stops through the mocked CAF channel.
Usage: test_cast_test_app.py BASE_URL (the site root, for example http://localhost:8000/)."""
import sys, os, json
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts
base = sys.argv[1].split("?")[0].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
HERE = os.path.dirname(os.path.abspath(__file__))
SENDER = open(os.path.join(HERE, "mock_cast_sender.js")).read()
CAF = open(os.path.join(HERE, "mock_caf_receiver.js")).read()
NS = "urn:x-cast:com.windowseat"
PROD, TEST = "48BEB59E", "CEB296EE"
fails, logs = [], []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

def wait_until(pg, js, ms=5000):
    try:
        pg.wait_for_function(js, timeout=ms)
        return True
    except Exception:
        return False

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
    pg = ctx.new_page()
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=SENDER))
    pg.route("**/iframe_api", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))

    def load(path="/"):
        pg.goto(base + path); pg.wait_for_timeout(1200)

    def app_id(): return (pg.evaluate("()=>__mockCast.options") or {}).get("receiverApplicationId")
    def stored(): return pg.evaluate("()=>localStorage.getItem('windowSeat.castApp')")
    def toggle_on(): return pg.is_checked("#cast-app-toggle")
    def open_about(): pg.click("#start-about"); pg.wait_for_timeout(200)

    # ---- default: production, switch off
    load()
    check(f"default: setOptions uses production ({app_id()})", app_id() == PROD)
    check("default: setOptions keeps origin scoped auto-join", (pg.evaluate("()=>__mockCast.options") or {}).get("autoJoinPolicy") == "origin_scoped")
    open_about()
    check("About: Cast test receiver switch visible with Debug off", pg.is_visible("#cast-app-toggle") and pg.evaluate("()=>__windowSeat.isDebugOn()") is False)
    check("About: switch is labelled 'Cast test receiver'", pg.evaluate("()=>document.querySelector('#cast-app-toggle').labels[0].textContent.includes('Cast test receiver')"))
    check("default: switch off", not toggle_on())
    check("default: help names the production app", PROD in pg.text_content("#cast-app-help"))

    # ---- ?castApp=test: test app, switch on, saved
    load("/?castApp=test")
    check(f"?castApp=test: setOptions uses the test app ({app_id()})", app_id() == TEST)
    check("?castApp=test: saved in localStorage", stored() == "test")
    open_about()
    check("?castApp=test: switch on", toggle_on())
    # Phone debug panel: CAST section shows the active app ID.
    pg.check("#debug-toggle"); pg.wait_for_timeout(150)
    pg.click("#panel-about [data-action=close-panel]"); pg.wait_for_timeout(200)
    if pg.get_attribute("#debug-pill", "aria-expanded") != "true":
        pg.click("#debug-pill"); pg.wait_for_timeout(150)
    cast_txt = pg.text_content("[data-section=cast]") or ""
    check(f"debug panel CAST shows the test app ({cast_txt[:80]!r})", TEST in cast_txt and "test receiver" in cast_txt)

    # ---- later load without the param: still the test app
    load("/")
    check(f"no param, saved test: setOptions uses the test app ({app_id()})", app_id() == TEST)

    # A session on the test app is the right app; one on production is called out as a different app.
    pg.evaluate("()=>__mockCast.start()"); pg.wait_for_timeout(300)
    check("test session: mock session appId follows setOptions", pg.evaluate("()=>cast.framework.CastContext.getInstance().getCurrentSession().getSessionObj().appId") == TEST)
    pg.locator('#remote [data-cmd="next"]').tap(); pg.wait_for_timeout(4500)
    status = pg.text_content("#remote-status") or ""
    check(f"test session: not called a different TV app ({status!r})", "different TV app" not in status)
    pg.evaluate(f"()=>window.__mockAppId='{PROD}'")
    pg.locator('#remote [data-cmd="next"]').tap(); pg.wait_for_timeout(4500)
    status = pg.text_content("#remote-status") or ""
    check(f"test app active, production session: called out ({status!r})", f"different TV app ({PROD})" in status and TEST in status)
    pg.evaluate("()=>{window.__mockAppId=null; cast.framework.CastContext.getInstance().endCurrentSession(true)}"); pg.wait_for_timeout(500)

    # ---- switch off from ?castApp=test: reload on production, param dropped
    load("/?castApp=test")
    open_about()
    with pg.expect_navigation():
        pg.click("label[for=cast-app-toggle]")
    pg.wait_for_timeout(1200)
    check(f"switch off: URL has no castApp=test ({pg.url})", "castApp=test" not in pg.url)
    check(f"switch off: setOptions uses production ({app_id()})", app_id() == PROD)
    check("switch off: saved prod", stored() == "prod")
    open_about()
    check("switch off: switch shows off after reload", not toggle_on())
    load("/")
    check("switch off: next plain load stays production", app_id() == PROD)

    # ---- switch on: reload on the test app
    open_about()
    with pg.expect_navigation():
        pg.click("label[for=cast-app-toggle]")
    pg.wait_for_timeout(1200)
    check(f"switch on: setOptions uses the test app ({app_id()})", app_id() == TEST and stored() == "test")
    open_about()
    check("switch on: switch shows on after reload", toggle_on())

    # ---- other spellings
    for q, want in [("CEB296EE", TEST), ("prod", PROD), ("test", TEST), ("main", PROD), ("48BEB59E", PROD), ("bogus", PROD)]:
        load(f"/?castApp={q}")
        check(f"?castApp={q}: {want}", app_id() == want)
    ctx.close()

    # ---- receiver: strip=1 with short pause overrides, real CAF path (mocked framework), no debug=1
    rx = b.new_page(viewport={"width": 1920, "height": 1080})
    rx.route("**/cast_receiver_framework.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=CAF))
    rx.route("**/iframe_api", lambda r: r.abort())
    rx.route("**/gviz/tq*", lambda r: r.abort())
    rx.add_init_script(f"window.__cafAppId='{TEST}'")
    rx.on("pageerror", lambda e: logs.append(f"RX PAGEERROR: {e}"))
    rx.goto(base + "/receiver.html?strip=1&pauseWarnMs=1500&pauseEndMs=3000"); rx.wait_for_timeout(1500)
    calls = rx.evaluate("()=>__caf.calls")
    check(f"rx: CAF started ({calls})", "start" in calls and f"addCustomMessageListener:{NS}" in calls)
    check("rx: no debug harness on this path", rx.evaluate("()=>typeof window.__receiver") == "undefined")
    strip = rx.text_content("#debug-strip") or ""
    print("strip:", strip)
    check("rx: strip visible", rx.is_visible("#debug-strip"))
    check(f"rx: strip shows the app ID from CAF", f"app {TEST}" in strip)
    check("rx: strip shows the override durations", "override warn 0:02 end 0:03" in strip)
    pz = rx.evaluate("()=>__receiverDebug.pause")
    check(f"rx: URL durations honored ({pz})", pz["warnMs"] == 1500 and pz["endMs"] == 3000 and pz["phase"] == "off")
    rx.evaluate("()=>__caf.connect()")
    rx.evaluate("([ns])=>__caf.message(ns, JSON.stringify({type:'pause', value:true}), 'sender-1')", [NS])
    check("rx: pause starts the timer", rx.evaluate("()=>__receiverDebug.pause.phase") == "running")
    check("rx: warning fires at the short warn time", wait_until(rx, "()=>__receiverDebug.pause.phase==='warning'", 2500))
    msgs = lambda: [json.loads(m["data"]) for m in rx.evaluate("()=>__caf.sent")]
    check("rx: warning broadcast as state pauseWarning", any(m.get("type") == "state" and m.get("pauseWarning") is True for m in msgs()))
    check("rx: end fires and the receiver stops", wait_until(rx, "()=>__caf.calls.includes('stop')", 3500))
    check("rx: pauseTimeout sent before the stop", any(m.get("type") == "pauseTimeout" for m in msgs()))
    check("rx: phase ended", rx.evaluate("()=>__receiverDebug.pause.phase") == "ended")

    # Default receiver: app ID shows "?" until CAF knows it.
    rx2 = b.new_page(viewport={"width": 1920, "height": 1080})
    rx2.route("**/cast_receiver_framework.js", lambda r: r.abort())
    rx2.route("**/iframe_api", lambda r: r.abort())
    rx2.route("**/gviz/tq*", lambda r: r.abort())
    rx2.goto(base + "/receiver.html?strip=1"); rx2.wait_for_timeout(1000)
    s2 = rx2.text_content("#debug-strip") or ""
    check("rx: no CAF, app shows ?", "app ?" in s2 and "override" not in s2)
    b.close()

print("page errors:", logs)
print("FAILED:" if fails or logs else "ALL PASS", fails)
sys.exit(1 if fails or logs else 0)
