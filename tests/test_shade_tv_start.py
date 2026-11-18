"""Shade rollout PR 3: the 10-foot start screen for the TV browser and ?tv=1 (DESIGN.md 5.3 and 8.8).
?tv=1 at 1920x1080 gets a two-pane start (live still left at 16:9, title, support line and two 88px pills right,
"Cast from your phone" opens a QR, D-pad focus ring 4px #8CC3D6). ?tv=0 on a phone keeps the current Home card.
Usage: test_shade_tv_start.py BASE_URL   (the site root, for example http://localhost:8000/)"""
import sys, os, base64
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
HERE = os.path.dirname(os.path.abspath(__file__))
SENDER = open(os.path.join(HERE, "mock_cast_sender.js")).read()
FAKE_YT = open(os.path.join(HERE, "fake_youtube.js")).read()
BUILD = "2026-11-17.28"
# 1x1 PNG served for every cam still, so #hero-img loads without the network.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
fails, logs = [], []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def wait_until(pg, js, ms=5000):
    try:
        pg.wait_for_function(js, timeout=ms)
        return True
    except Exception:
        return False


def setup(pg):
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=SENDER))
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("https://i.ytimg.com/**", lambda r: r.fulfill(status=200, content_type="image/png", body=PNG))
    pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))


RECT = "(s)=>{const e=document.querySelector(s); if(!e) return null; const r=e.getBoundingClientRect(); return {x:r.left,y:r.top,w:r.width,h:r.height,r:r.right,b:r.bottom}}"

with sync_playwright() as p:
    b = p.chromium.launch()

    # ------------------------------------------------------------ ?tv=1 at 1920x1080
    ctx = b.new_context(viewport={"width": 1920, "height": 1080})
    pg = ctx.new_page()
    setup(pg)
    pg.goto(base + "/?tv=1")
    pg.wait_for_timeout(1200)
    check("tv: body has .tv", pg.evaluate("()=>document.body.classList.contains('tv')"))
    check(f"tv: __windowSeat.build is {BUILD}", pg.evaluate("()=>__windowSeat.build") == BUILD)
    check("tv: Start watching is focused", pg.evaluate("()=>document.activeElement && document.activeElement.id") == "start-btn")

    media = pg.evaluate(RECT, ".hero-media")
    start = pg.evaluate(RECT, "#start-btn")
    phone = pg.evaluate(RECT, "#start-phone")
    h1 = pg.evaluate(RECT, "#start h1")
    check(f"tv: still is in the left pane ({media})", media and media["x"] >= 95 and media["r"] <= 960 + 1 and media["w"] >= 800)
    check(f"tv: still is 16:9 ({media['w']:.1f}x{media['h']:.1f})", abs(media["w"] / media["h"] - 16 / 9) < 0.01)
    check(f"tv: still is vertically inside the 54px safe inset ({media['y']:.0f}..{media['b']:.0f})", media["y"] >= 54 and media["b"] <= 1080 - 54)
    radius = pg.evaluate("()=>getComputedStyle(document.querySelector('.hero-media')).borderTopLeftRadius")
    check(f"tv: still radius 24px ({radius})", radius == "24px")
    check("tv: still image is shown edge to edge in its pane",
          pg.evaluate("()=>{const i=document.querySelector('#hero-img'), m=document.querySelector('.hero-media'); const a=i.getBoundingClientRect(), c=m.getBoundingClientRect(); return !i.hidden && Math.abs(a.width-c.width)<1 && Math.abs(a.height-c.height)<1}"))
    start = pg.evaluate("()=>{const e=document.querySelector('#start-btn'); return {x:e.offsetLeft, r:e.offsetLeft+e.offsetWidth}}")  # unscaled
    check(f"tv: title, Start and Cast from your phone sit in the right pane ({h1}, {start}, {phone})",
          all(r and r["x"] >= media["r"] + 48 and r["r"] <= 1920 - 96 + 1 for r in (h1, start, phone)))
    ty = pg.evaluate("()=>{const cs=getComputedStyle(document.querySelector('#start h1')); return [cs.fontSize, cs.lineHeight, cs.fontWeight]}")
    check(f"tv: start title 72/84 Fraunces 600 ({ty})", abs(float(ty[0][:-2]) - 72) < 1 and abs(float(ty[1][:-2]) - 84) < 1 and ty[2] == "600")
    tl = pg.evaluate("()=>{const cs=getComputedStyle(document.querySelector('.tagline')); return [cs.fontSize, cs.lineHeight]}")
    check(f"tv: support line 32/40 ({tl})", tl == ["32px", "40px"])
    hs = pg.evaluate("()=>['#start-btn','#start-phone'].map(s=>document.querySelector(s).offsetHeight)")  # layout size, before the focus scale
    check(f"tv: buttons are 88px tall ({hs})", hs == [88, 88])
    check("tv: Start watching label", pg.text_content("#start-btn").strip() == "Start watching")
    lab = pg.evaluate("()=>['#start-btn','#start-phone'].map(s=>{const cs=getComputedStyle(document.querySelector(s)); return [cs.fontSize, cs.lineHeight, cs.fontWeight, cs.borderRadius]})")
    check(f"tv: button labels 32/40 600, pill ({lab})", all(l[0] == "32px" and l[1] == "40px" and l[2] == "600" and l[3] == "999px" for l in lab))
    check("tv: 'Cast from your phone' present", pg.is_visible("#start-phone") and pg.text_content("#start-phone").strip() == "Cast from your phone")
    bg = pg.evaluate("()=>[getComputedStyle(document.querySelector('#start')).backgroundColor, getComputedStyle(document.querySelector('#start h1')).color]")
    check(f"tv: fog canvas with navy ink, not the night field ({bg})", bg == ["rgb(243, 245, 247)", "rgb(16, 37, 54)"])
    hidden = pg.evaluate("()=>['.hero-text','#right-now-label','.cast-start','#start-tv'].map(s=>{const e=document.querySelector(s); const r=e.getBoundingClientRect(); return r.width===0 && r.height===0})")
    check(f"tv: no Right now card text, no phone Cast CTA, no Open in TV browser ({hidden})", all(hidden))
    check("tv: About still reachable as a large control", pg.is_visible("#start-about") and pg.evaluate(RECT, "#start-about")["h"] >= 72)

    ring = pg.evaluate("()=>{const cs=getComputedStyle(document.querySelector('#start-btn')); return [cs.outlineStyle, cs.outlineWidth, cs.outlineColor, cs.outlineOffset, cs.transform]}")
    check(f"tv: focus ring on Start is 4px #8CC3D6, 2px offset ({ring})", ring[:4] == ["solid", "4px", "rgb(140, 195, 214)", "2px"])
    pg.wait_for_timeout(300)
    ring = pg.evaluate("()=>getComputedStyle(document.querySelector('#start-btn')).transform")
    check(f"tv: focused Start scales 1.04 ({ring})", ring.startswith("matrix(1.04, 0, 0, 1.04"))

    # D-pad: down from Start reaches Cast from your phone; OK opens the QR panel; Back returns focus.
    pg.keyboard.press("ArrowDown")
    pg.wait_for_timeout(250)
    check("tv: D-pad down moves to Cast from your phone", pg.evaluate("()=>document.activeElement.id") == "start-phone")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(300)
    check("tv: OK opens the Cast from your phone panel", pg.is_visible("#panel-phone") and pg.text_content("#phone-title") == "Cast from your phone")
    qr = pg.evaluate("()=>{const c=document.querySelector('#phone-qr'); const r=c.getBoundingClientRect(); const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; let dark=0; for(let i=0;i<d.length;i+=4) if(d[i]<128) dark++; return {w:c.width, cw:r.width, dark}}")
    check(f"tv: panel shows a drawn QR ({qr})", qr["w"] > 100 and qr["cw"] >= 300 and qr["dark"] > 1000)
    # PHONE_URL uses the current origin on https, and the production host on http (tests, local).
    expected_qr_host = urlparse(base).netloc if urlparse(base).scheme == "https" else "window-seat-lemon.vercel.app"
    check(f"tv: QR url is the live app ({expected_qr_host})", expected_qr_host in pg.text_content("#phone-url"))
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    check("tv: Back closes the panel and focus returns to Cast from your phone", not pg.is_visible("#panel-phone") and pg.evaluate("()=>document.activeElement.id") == "start-phone")
    pg.keyboard.press("ArrowDown")
    pg.wait_for_timeout(250)
    check("tv: D-pad down reaches About", pg.evaluate("()=>document.activeElement.id") == "start-about")
    pg.keyboard.press("ArrowUp"); pg.keyboard.press("ArrowUp")
    pg.wait_for_timeout(250)
    check("tv: D-pad up returns to Start watching", pg.evaluate("()=>document.activeElement.id") == "start-btn")
    pg.screenshot(path="/tmp/shade-tv-start-1080.png")
    ctx.close()

    # Reduced motion: ring stays, no scale.
    ctx = b.new_context(viewport={"width": 1920, "height": 1080}, reduced_motion="reduce")
    pg = ctx.new_page()
    setup(pg)
    pg.goto(base + "/?tv=1")
    pg.wait_for_timeout(800)
    t = pg.evaluate("()=>[document.activeElement.id, getComputedStyle(document.activeElement).transform, getComputedStyle(document.activeElement).outlineWidth]")
    check(f"tv reduced motion: focused Start has the ring but no scale ({t})", t[0] == "start-btn" and t[1] == "none" and t[2] == "4px")
    ctx.close()

    # 4K: everything scales up with the viewport.
    ctx = b.new_context(viewport={"width": 3840, "height": 2160})
    pg = ctx.new_page()
    setup(pg)
    pg.goto(base + "/?tv=1")
    pg.wait_for_timeout(800)
    k = pg.evaluate("()=>[parseFloat(getComputedStyle(document.querySelector('#start h1')).fontSize), document.querySelector('#start-btn').offsetHeight]")
    check(f"tv 4K: title 144px and buttons 176px ({k})", abs(k[0] - 144) < 1 and abs(k[1] - 176) < 1)
    ctx.close()

    # ------------------------------------------------------------ ?tv=0 on a phone: the current Home card
    ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    pg = ctx.new_page()
    setup(pg)
    pg.goto(base + "/?tv=0")
    pg.wait_for_timeout(1200)
    check("phone: no .tv", not pg.evaluate("()=>document.body.classList.contains('tv')"))
    check("phone: Right now hero card visible", pg.is_visible("#right-now") and pg.is_visible("#right-now-label") and pg.text_content("#right-now-label") == "Right now")
    check("phone: Start watching visible", pg.is_visible("#start-btn") and "Start watching" in pg.text_content("#start-btn"))
    check("phone: no 'Cast from your phone' button", not pg.is_visible("#start-phone"))
    media = pg.evaluate(RECT, ".hero-media")
    hero = pg.evaluate(RECT, "#right-now")
    start = pg.evaluate(RECT, "#start-btn")
    check(f"phone: single column, Start below the hero card ({hero}, {start})", start["y"] > hero["b"] and abs(media["w"] - hero["w"]) < 1 and hero["w"] <= 390)
    h1 = pg.evaluate("()=>getComputedStyle(document.querySelector('#start h1')).fontSize")
    check(f"phone: h1 stays 40px ({h1})", h1 == "40px")
    check(f"phone: Start watching stays 52px tall ({start['h']})", abs(start["h"] - 52) < 0.5)
    cast = pg.evaluate("()=>{const c=document.querySelector('.cast-start'); return [c.hidden, c.textContent.trim()]}")
    check(f"phone: 'Cast to your TV' is still the phone CTA ({cast})", cast[1] == "Cast to your TV")
    check("phone: Cast to your TV visible once the sender is ready", wait_until(pg, "()=>!document.querySelector('.cast-start').hidden", 4000) and pg.is_visible(".cast-start"))
    check("phone: Open in TV browser and About still there", pg.is_visible("#start-tv") and pg.is_visible("#start-about"))
    ring = pg.evaluate("()=>{const e=document.querySelector('#start-btn'); e.focus(); const cs=getComputedStyle(e); return [cs.outlineWidth, cs.outlineColor]}")
    check(f"phone: focus ring unchanged, 3px #2F6F8A ({ring})", ring == ["3px", "rgb(47, 111, 138)"])
    pg.screenshot(path="/tmp/shade-tv-start-phone.png")
    ctx.close()
    b.close()

for line in logs:
    print(line)
print(f"\n{'FAILED: ' + str(len(fails)) if fails else 'ALL PASS'}")
sys.exit(1 if fails or logs else 0)
