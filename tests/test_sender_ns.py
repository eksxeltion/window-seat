"""Phone side: namespace missing is shown, and when it appears (session metadata event) getState is re-sent."""
import sys, os, json
from playwright.sync_api import sync_playwright
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
    pg.evaluate("()=>{window.__mockNs=[{name:'urn:x-cast:com.google.cast.media'}]; window.__mockFail={code:'timeout',description:'no ack'}}")
    pg.evaluate("()=>__mockCast.start()"); pg.wait_for_timeout(2500)
    line = pg.text_content("#remote-debug"); print("debug:", line)
    check("debug shows ns missing", "ns missing" in line)
    check("debug lists short namespace names", "com.google.cast.media" in line and "urn:x-cast:" not in line)
    check("hint says Chrome does not list the channel", "phone's Chrome does not list the Window Seat channel" in (pg.text_content("#remote-status") or "") + (pg.text_content("#remote-debug") or "") + pg.evaluate("()=>document.body.innerText"))
    setup = lambda sent: [t for t in sent if t in ("mix", "volume", "interval", "mute", "pin", "config", "cams", "cam")]
    before = [json.loads(m["data"])["type"] for m in pg.evaluate("()=>__mockCast.sent")]
    check("no setup commands sent while ns missing", not setup(before))
    pg.evaluate("()=>{window.__mockFail=null; window.__mockNs=[{name:'urn:x-cast:com.google.cast.media'},{name:'urn:x-cast:com.windowseat'}]; __mockCast.sent.length=0}")
    # an unsolicited state broadcast arrives first; it must not cancel the pending initialisation
    pg.evaluate("()=>__mockCast.reply({type:'state',build:'x'})")
    pg.evaluate("()=>__mockCast.fireSession('applicationmetadatachanged')")
    pg.wait_for_timeout(400)
    sent = [json.loads(m["data"])["type"] for m in pg.evaluate("()=>__mockCast.sent")]
    print("sent after event:", sent)
    check("exactly one getState after namespace appears", sent.count("getState") == 1)
    check("exactly one mix and volume after namespace appears", sent.count("mix") == 1 and sent.count("volume") == 1)
    pg.evaluate("()=>{__mockCast.sent.length=0; __mockCast.fireSession('applicationstatuschanged')}")
    pg.wait_for_timeout(400)
    sent2 = [json.loads(m["data"])["type"] for m in pg.evaluate("()=>__mockCast.sent")]
    check("no extra setup on a second event", not setup(sent2) and "getState" not in sent2)
    check("debug now shows ns ok", "ns ok" in pg.text_content("#remote-debug"))
    b.close()
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails else 0)
