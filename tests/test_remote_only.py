"""The phone is only a remote while Cast is connecting or connected (mock CAF). Usage: test_remote_only.py PHONE_URL"""
import sys, os, json, re
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts
url = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(here)
MOCK = open(os.path.join(here, "mock_cast_sender.js")).read()
# The phone build the app should show, read from js/build.js so this does not go stale on every build bump.
PHONE_BUILD = re.search(r'BUILD = "([^"]+)"', open(os.path.join(root, "js", "build.js")).read()).group(1)
fails, logs = [], []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)
YT = "()=>document.querySelectorAll('iframe[src*=youtube]').length"
def types(pg): return [json.loads(m["data"])["type"] for m in pg.evaluate("()=>__mockCast.sent")]
def clear(pg): pg.evaluate("()=>__mockCast.sent.length=0")

with sync_playwright() as p:
    b = p.chromium.launch()
    def page():
        pg = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True).new_page()
        pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
        pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
        pg.goto(url); pg.wait_for_timeout(2500)
        return pg

    # (i) + (ii) cast from the Start screen
    pg = page()
    pg.evaluate("()=>__mockCast.connecting()"); pg.wait_for_timeout(300)
    check("i: CONNECTING shows the remote", pg.is_visible("#remote") and "Connecting" in pg.text_content("#remote-status"))
    pg.click("#start-btn", force=True); pg.wait_for_timeout(1500)
    pg.evaluate("()=>__windowSeat.start()"); pg.wait_for_timeout(500)
    check("i: Start while connecting creates no YouTube iframe", pg.evaluate(YT) == 0)
    pg.evaluate("()=>__mockCast.start()"); pg.wait_for_timeout(500)
    pg.evaluate("()=>__mockCast.reply({type:'state',configured:false})"); pg.wait_for_timeout(200)  # probe reply: a fresh TV gets the phone's choices
    check("ii: STARTED pushes the phone state to the TV", "getState" in types(pg))
    clear(pg)
    for sel, t in [('[data-cmd="next"]', "next"), ('[data-cmd="prev"]', "prev"), ('[data-cmd="vol-up"]', "volume"), ('[data-cmd="next-mix"]', "nextMix")]:
        clear(pg); pg.locator("#remote " + sel).tap(); pg.wait_for_timeout(150)
        check(f"ii: remote {t} goes to the TV", types(pg) == [t])
    check("ii: no local iframe after controls", pg.evaluate(YT) == 0)
    check("debug line shows builds and states", re.search(r"Phone build " + re.escape(PHONE_BUILD) + r" \| TV build \S+ \| cast CONNECTED \| session SESSION_STARTED", pg.text_content("#remote-debug")) is not None)
    # (vi) keyboard and media keys go to the TV
    for key, t in [("n", "next"), ("p", "pin"), ("u", "mute"), ("MediaTrackNext", "next"), ("MediaTrackPrevious", "prev"), ("MediaPlayPause", "pause")]:
        clear(pg); pg.keyboard.press(key); pg.wait_for_timeout(150)
        check(f"vi: key {key} -> {t}", types(pg) == [t])
    check("vi: still no local iframe", pg.evaluate(YT) == 0)
    # explicit MediaPlay / MediaPause are absolute, whatever the remote's toggle state is.
    # They pause or resume the whole feed on the TV: {type:"pause", value:true} means paused.
    for key, val in [("MediaPlay", False), ("MediaPlay", False), ("MediaPause", True), ("MediaPause", True)]:
        clear(pg); pg.evaluate("(k)=>document.dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true}))", key); pg.wait_for_timeout(150)
        sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
        check(f"vi: {key} sends pause {val}", len(sent) == 1 and sent[0]["type"] == "pause" and sent[0]["value"] is val)
    # (vii) build stamps
    pg.evaluate("()=>document.querySelector('[data-action=open-about]').click()"); pg.wait_for_timeout(200)
    check("vii: About shows the build", pg.text_content("#about-build") == "Build 2026-11-17.24")
    check("vii: window.__windowSeat.build", pg.evaluate("()=>__windowSeat.build") == "2026-11-17.24")
    # (viii) ending the session (real CAF order) returns to the Start screen
    pg.locator('#remote [data-cmd="stop"]').tap(); pg.wait_for_timeout(500)
    check("viii: Stop casting hides the remote, Start screen back", (not pg.is_visible("#remote")) and pg.is_visible("#start-btn"))
    check("viii: isCasting() false after end", pg.evaluate("()=>__windowSeat.isCasting()") is False)
    pg.click("#start-btn", force=True); pg.wait_for_timeout(4000)
    check("viii: Start creates local iframes again", pg.evaluate(YT) == 2)
    pg.context.close()

    # (iii) playing locally, then CONNECTING
    pg = page()
    pg.click("#start-btn"); pg.wait_for_timeout(4000)
    check("iii: playing locally first", pg.evaluate(YT) == 2)
    pg.evaluate("()=>__mockCast.connecting()"); pg.wait_for_timeout(300)
    check("iii: CONNECTING destroys local iframes before any session", pg.evaluate(YT) == 0 and pg.is_visible("#remote"))
    # (v) START_FAILED returns to Start and local play works again
    pg.evaluate("()=>__mockCast.fail()"); pg.wait_for_timeout(500)
    check("v: START_FAILED leaves remote mode, Start screen back", (not pg.is_visible("#remote")) and pg.is_visible("#start-btn"))
    pg.click("#start-btn"); pg.wait_for_timeout(4000)
    check("v: Start plays locally again", pg.evaluate(YT) == 2)
    pg.context.close()

    # (iv) silent session (no events): visibilitychange
    pg = page()
    pg.click("#start-btn"); pg.wait_for_timeout(4000)
    pg.evaluate("()=>__mockCast.silent()")
    pg.evaluate("()=>document.dispatchEvent(new Event('visibilitychange'))"); pg.wait_for_timeout(500)
    check("iv: silent session adopted on visibilitychange, no local player", pg.is_visible("#remote") and pg.evaluate(YT) == 0)
    pg.context.close()
    # (iv) silent session: Start tap
    pg = page()
    pg.evaluate("()=>__mockCast.silent()")
    pg.evaluate("()=>__windowSeat.start()"); pg.wait_for_timeout(1500)
    check("iv: silent session adopted on Start, no local player", pg.is_visible("#remote") and pg.evaluate(YT) == 0)
    pg.context.close()
    b.close()

# (vii) the three stamps agree
read = lambda *a: open(os.path.join(root, *a)).read()
build = re.search(r'BUILD = "([^"]+)"', read("js", "build.js")).group(1)
recv = re.search(r'BUILD = "([^"]+)"', read("js", "receiver.js")).group(1)
sw = re.search(r'CACHE_VERSION = "([^"]+)"', read("sw.js")).group(1)
check(f"vii: build.js == receiver.js == sw.js ({build}, {recv}, {sw})", build == recv == sw == "2026-11-17.24")
check("vii: receiver.html ?v= matches", set(re.findall(r"\?v=([\d.-]+)", read("receiver.html"))) == {build})
check("vii: sw.js precaches build.js", '"/js/build.js"' in read("sw.js"))
print(logs); sys.exit(1 if fails or logs else 0)
