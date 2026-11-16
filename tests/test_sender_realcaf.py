"""Phone with the REAL gstatic cast_framework.js on top of tests/fake_chrome_cast.js (needs network). Usage: test_sender_realcaf.py PHONE_URL"""
import sys, os, json
from playwright.sync_api import sync_playwright
url = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
FAKE = open(os.path.join(here, "fake_chrome_cast.js")).read()
LOADER = """(function(){const cb=window.__onGCastApiAvailable;
const s1=document.createElement('script');s1.src='/__rc/fake_chrome_cast.js';
s1.onload=()=>{const s2=document.createElement('script');s2.src='https://www.gstatic.com/cast/sdk/libs/sender/1.0/cast_framework.js';
s2.onload=()=>(window.__onGCastApiAvailable||cb)(true);document.head.append(s2);};document.head.append(s1);})();"""
UA = "Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36"
YT = "()=>document.querySelectorAll('iframe[src*=youtube]').length"
fails, logs = [], []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)
sent = lambda pg: [json.loads(m["data"])["type"] for m in pg.evaluate("()=>__fakeSent")]

with sync_playwright() as p:
    b = p.chromium.launch()
    def page():
        pg = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True, user_agent=UA).new_page()
        pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=LOADER))
        pg.route("**/__rc/fake_chrome_cast.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE))
        pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
        pg.goto(url); pg.wait_for_timeout(3000)
        return pg
    def tap_cast(pg, sel):
        l = pg.locator(sel + " google-cast-launcher")
        if l.count() and l.first.is_visible(): l.first.tap()
        else: pg.evaluate("(s)=>document.querySelector(s).click()", sel)
        pg.wait_for_timeout(300)
    def controls(pg, label):
        pg.evaluate("()=>__fakeSent.length=0")
        pg.locator('#remote [data-cmd="next"]').tap(); pg.wait_for_timeout(200)
        pg.keyboard.press("n"); pg.wait_for_timeout(200)
        check(f"{label}: controls go through session.sendMessage", sent(pg) == ["next", "next"])

    # Flow A: playing, then the bar Cast button
    pg = page()
    check("A: real CAF loaded", pg.evaluate("()=>!!(window.cast && cast.framework && cast.framework.CastContext)"))
    pg.click("#start-btn"); pg.wait_for_timeout(4000)
    check("A: playing locally first", pg.evaluate(YT) == 2)
    tap_cast(pg, "#bar [data-cast]")
    pg.evaluate("()=>__fake.pick()"); pg.wait_for_timeout(1000)
    check("A: remote mode, zero local iframes", pg.is_visible("#remote") and pg.evaluate(YT) == 0)
    controls(pg, "A")
    pg.context.close()

    # Flow B: Cast to TV from the Start screen, then try Start
    pg = page()
    tap_cast(pg, ".cast-start")
    pg.evaluate("()=>__fake.pick()"); pg.wait_for_timeout(1000)
    check("B: remote mode after pick", pg.is_visible("#remote") and pg.evaluate(YT) == 0)
    pg.click("#start-btn", force=True); pg.wait_for_timeout(800)
    pg.evaluate("()=>__windowSeat.start()"); pg.wait_for_timeout(1500)
    check("B: Start (button and API) creates no local iframe", pg.evaluate(YT) == 0 and pg.is_visible("#remote"))
    controls(pg, "B")
    pg.context.close()

    # Flow C: a session with no request (auto-join)
    pg = page()
    pg.evaluate("()=>__fake.join()"); pg.wait_for_timeout(1000)
    check("C: remote mode on join", pg.is_visible("#remote") and pg.evaluate(YT) == 0)
    pg.evaluate("()=>__windowSeat.start()"); pg.wait_for_timeout(1500)
    check("C: Start creates no local iframe", pg.evaluate(YT) == 0)
    controls(pg, "C")
    pg.context.close()

    # Flow D: Stop casting via the remote button (session.leave/stop), then Start is local again
    # Flow E: the receiver side disconnects (session status -> disconnected, update listeners fire)
    for flow in "DE":
        pg = page()
        pg.evaluate("()=>__fake.join()"); pg.wait_for_timeout(1000)
        check(f"{flow}: remote shown", pg.is_visible("#remote"))
        if flow == "D":
            pg.locator('#remote [data-cmd="stop"]').tap()
        else:
            pg.evaluate("()=>{const s=__fakeSession; s.status='disconnected'; s._upd.forEach(f=>f(false));}")
        pg.wait_for_timeout(800)
        check(f"{flow}: remote hidden, Start screen back", (not pg.is_visible("#remote")) and pg.is_visible("#start-btn"))
        check(f"{flow}: isCasting() false", pg.evaluate("()=>__windowSeat.isCasting()") is False)
        pg.click("#start-btn"); pg.wait_for_timeout(4000)
        check(f"{flow}: Start creates local YouTube iframes again", pg.evaluate(YT) == 2 and not pg.is_visible("#remote"))
        pg.context.close()
    b.close()
print(logs); sys.exit(1 if fails or logs else 0)
