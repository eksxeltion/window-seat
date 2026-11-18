"""Shade rollout PR 4: the official Material Cast glyph always shows on the Home "Cast to your TV" CTA
(DESIGN.md 7 and 13.4, audit P1 #6). The glyph comes from our sprite at 20px and never depends on
<google-cast-launcher>, which may be defined but draw nothing. The player top bar keeps the launcher.
Usage: test_shade_cast_cta.py BASE_URL   (the site root, for example http://localhost:8000/)"""
import sys, os, base64
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
HERE = os.path.dirname(os.path.abspath(__file__))
SENDER = open(os.path.join(HERE, "mock_cast_sender.js")).read()
FAKE_YT = open(os.path.join(HERE, "fake_youtube.js")).read()
BUILD = "2026-11-17.30"
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


# Size and state of the sprite glyph inside the Home CTA, plus the launcher's box.
GLYPH = """()=>{
  const b=document.querySelector('.cast-start'); const g=b.querySelector('svg.cast-glyph'); const u=g&&g.querySelector('use');
  const r=g.getBoundingClientRect(); const cs=getComputedStyle(g);
  const l=b.querySelector('google-cast-launcher'); const lr=l?l.getBoundingClientRect():null;
  return {w:r.width,h:r.height,display:cs.display,visibility:cs.visibility,opacity:cs.opacity,href:u&&u.getAttribute('href'),
          launcherShown:!!(lr&&lr.width>0&&lr.height>0), defined:!!customElements.get('google-cast-launcher')};
}"""

with sync_playwright() as p:
    b = p.chromium.launch()

    # ------------------------------------------------------------ ?tv=0 phone Home
    ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    pg = ctx.new_page()
    setup(pg)
    pg.goto(base + "/?tv=0")
    pg.wait_for_timeout(1200)
    check(f"phone: build {BUILD}", pg.evaluate("()=>__windowSeat.build") == BUILD)
    check("phone: Cast to your TV visible", wait_until(pg, "()=>!document.querySelector('.cast-start').hidden", 4000) and pg.is_visible(".cast-start"))
    check("phone: label is 'Cast to your TV'", pg.text_content(".cast-start .cast-label") == "Cast to your TV")
    check("phone: button is enabled", pg.is_enabled(".cast-start"))
    g = pg.evaluate(GLYPH)
    check(f"phone: sprite glyph #i-cast visible at 20px before the launcher is defined ({g})",
          pg.is_visible(".cast-start svg.cast-glyph") and g["href"] == "#i-cast" and g["w"] == 20 and g["h"] == 20)

    # Define <google-cast-launcher> as CAF would, but drawing nothing: zero size, empty shadow root.
    pg.evaluate("""()=>{ if(!customElements.get('google-cast-launcher')) customElements.define('google-cast-launcher',
      class extends HTMLElement{ constructor(){ super(); this.attachShadow({mode:'open'}); } }); }""")
    pg.wait_for_timeout(100)
    g = pg.evaluate(GLYPH)
    check("phone: launcher now :defined", g["defined"] and pg.evaluate("()=>document.querySelector('.cast-start google-cast-launcher').matches(':defined')"))
    check(f"phone: sprite glyph still visible at 20px after the launcher is defined ({g})",
          pg.is_visible(".cast-start svg.cast-glyph") and g["display"] != "none" and g["visibility"] == "visible" and g["opacity"] == "1" and g["w"] == 20 and g["h"] == 20)
    check("phone: launcher never draws on the Home CTA (no double icon)", not g["launcherShown"])
    check("phone: glyph sits beside the label", pg.evaluate("""()=>{const g=document.querySelector('.cast-start svg.cast-glyph').getBoundingClientRect();
      const l=document.querySelector('.cast-start .cast-label').getBoundingClientRect(); return g.right<=l.left && Math.abs((g.top+g.bottom)/2-(l.top+l.bottom)/2)<4}"""))
    check("phone: player top bar keeps its launcher", pg.locator("#player-top [data-cast] google-cast-launcher").count() == 1)
    pg.screenshot(path="/tmp/shade-cast-cta-phone.png")

    # Connected: the existing mock starts a session and the buttons go data-state=connected.
    pg.evaluate("()=>__mockCast.start()")
    check("connected: Home CTA marked connected", wait_until(pg, "()=>document.querySelector('.cast-start').dataset.state==='connected'", 3000))
    g = pg.evaluate(GLYPH)
    check(f"connected: sprite uses #i-cast-connected ({g['href']})", g["href"] == "#i-cast-connected")
    check(f"connected: glyph still drawn at 20px ({g})", g["display"] != "none" and g["visibility"] == "visible" and g["opacity"] == "1")
    # The remote covers Home while casting; render the CTA's box to confirm its size in the connected state.
    size = pg.evaluate("""()=>{const s=document.querySelector('.cast-start'); const c=s.cloneNode(true); c.hidden=false;
      c.style.cssText='position:fixed;top:0;left:0;z-index:99999'; document.body.appendChild(c);
      const r=c.querySelector('svg.cast-glyph').getBoundingClientRect(); const out=[r.width,r.height, c.querySelector('use').getAttribute('href')]; c.remove(); return out}""")
    check(f"connected: glyph box 20x20 with #i-cast-connected ({size})", size == [20, 20, "#i-cast-connected"])
    ctx.close()

    # ------------------------------------------------------------ ?tv=1 10-foot start: unchanged
    ctx = b.new_context(viewport={"width": 1920, "height": 1080})
    pg = ctx.new_page()
    setup(pg)
    pg.goto(base + "/?tv=1")
    pg.wait_for_timeout(1200)
    check("tv: body has .tv", pg.evaluate("()=>document.body.classList.contains('tv')"))
    check("tv: .cast-start stays hidden", not pg.is_visible(".cast-start") and not pg.is_visible(".cast-start svg.cast-glyph"))
    ctx.close()
    b.close()

for line in logs:
    print(line)
print(f"\n{'FAILED: ' + str(len(fails)) if fails else 'ALL PASS'}")
sys.exit(1 if fails or logs else 0)
