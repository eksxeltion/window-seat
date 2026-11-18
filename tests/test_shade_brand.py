"""Shade brand rollout (PR 1): build stamp, PWA icons and favicons, Home wordmark, contrast tokens, type weights,
sentence-case "Right now", Roboto Mono only with Debug on, and the Cast receiver launch splash.
Fonts stay optional: fonts.googleapis.com is aborted throughout.
Usage: test_shade_brand.py BASE_URL   (the site root, for example http://localhost:8000/)"""
import sys, os, re, json, urllib.request
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAKE_YT = open(os.path.join(HERE, "fake_youtube.js")).read()
SENDER = open(os.path.join(HERE, "mock_cast_sender.js")).read()
# The fake YouTube API, held until the test calls window.__ytGo(): the receiver stays on its launch splash until then.
HELD_YT = "window.__ytGo = function () {\n" + FAKE_YT + "\n};"
BUILD = "2026-11-17.25"
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


def fetch(path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, r.read()
    except Exception as e:  # noqa: BLE001
        return getattr(e, "code", 0), b""


def read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


# ------------------------------------------------------------ static: stamps, manifest, icons, CSS source
build_js = re.search(r'BUILD = "([^"]+)"', read("js/build.js")).group(1)
recv_js = re.search(r'BUILD = "([^"]+)"', read("js/receiver.js")).group(1)
sw = re.search(r'CACHE_VERSION = "([^"]+)"', read("sw.js")).group(1)
rx_v = set(re.findall(r"\?v=([\d.-]+)", read("receiver.html")))
check(f"stamp: build.js, receiver.js, sw.js and receiver.html ?v= all {BUILD}", build_js == recv_js == sw == BUILD and rx_v == {BUILD})

manifest = json.loads(fetch("/manifest.webmanifest")[1] or b"{}")
icons = {(i["sizes"], i.get("purpose", "any")): i["src"] for i in manifest.get("icons", [])}
check("manifest: 192, 512 and maskable 512 entries", {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= set(icons))
check("manifest: theme and background #F3F5F7", manifest.get("theme_color", "").upper() == "#F3F5F7" and manifest.get("background_color", "").upper() == "#F3F5F7")
for key, src in icons.items():
    st, body = fetch(src)
    check(f"manifest icon {src} returns a PNG", st == 200 and body[:8] == b"\x89PNG\r\n\x1a\n")
for path in ("/favicon.svg", "/favicon-32.png", "/favicon.ico", "/icons/apple-touch-icon.png", "/icons/shade/mark.svg",
             "/icons/shade/mark-night.svg", "/icons/shade/receiver-splash-1920x1080.png", "/icons/shade/receiver-splash-1280x720.png",
             "/icons/shade/receiver-splash-1920x1080.svg", "/icons/shade/receiver-splash-1280x720.svg"):
    check(f"{path} returns 200", fetch(path)[0] == 200)
fav = fetch("/favicon.svg")[1].decode("utf-8", "replace")
# Shade mark: the 52 x 74 window outline and the blind. The old four-pane favicon drew the panes with M32 14v36M16 32h32.
check("favicon.svg is the Shade mark", "M42,11H54A20,20 0 0 1 74,31V65" in fav and "#2F6F8A" in fav and "M32 14v36" not in fav)
for png in ("/icons/icon-512.png", "/icons/maskable-512.png", "/icons/apple-touch-icon.png", "/icons/icon-192.png"):
    check(f"{png} matches the Shade handoff (not the old gold art)", fetch(png)[1] == open(os.path.join(ROOT, png.lstrip("/")), "rb").read())
sw_src = read("sw.js")
check("sw.js precaches the root favicons and keeps /icons/*", all(f'"{p}"' in sw_src for p in ("/favicon.svg", "/favicon-32.png", "/favicon.ico", "/icons/icon-192.png", "/icons/maskable-512.png", "/icons/apple-touch-icon.png")))
style = read("css/style.css")
check("style.css: no font-weight 650 left", "650" not in re.sub(r"\s+", " ", " ".join(re.findall(r"font-weight:\s*\d+", style))) and not re.search(r"font:\s*650", style))
check("style.css and receiver.css: --color-overlay-accent retired (not declared or used)", not re.search(r"--color-overlay-accent\s*:|var\(--color-overlay-accent", style + read("css/receiver.css")))
index = read("index.html")
check("index.html: no data-URI favicon", "data:image/svg+xml" not in index)
check("no shipped file points at the context library", all("context-library" not in read(f) for f in ("index.html", "receiver.html", "css/style.css", "css/receiver.css", "js/receiver.js", "sw.js")))

with sync_playwright() as p:
    b = p.chromium.launch()

    # ------------------------------------------------------------ phone Home
    ctx = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
    pg = ctx.new_page()
    font_reqs = []
    pg.on("request", lambda r: font_reqs.append(r.url) if "fonts.googleapis.com" in r.url else None)
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())  # fonts must be optional
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=SENDER))
    pg.route("**/iframe_api", lambda r: r.abort())
    pg.route("https://i.ytimg.com/**", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    pg.goto(base + "/")
    pg.wait_for_timeout(1200)

    check("home: page renders with fonts aborted", pg.is_visible("#start-btn") and pg.text_content("#start h1") == "Window Seat")
    check(f"home: __windowSeat.build is {BUILD}", pg.evaluate("()=>__windowSeat.build") == BUILD)
    pg.click("#start-about")
    pg.wait_for_timeout(200)
    check(f"About: shows Build {BUILD}", pg.text_content("#about-build") == f"Build {BUILD}")
    pg.click("#panel-about [data-action=close-panel]")
    pg.wait_for_timeout(200)

    links = pg.evaluate("()=>[...document.querySelectorAll('link[rel~=icon], link[rel=apple-touch-icon]')].map(l=>[l.rel, l.getAttribute('href'), l.type, l.sizes.value])")
    check(f"home: favicon links are ico, png 32, then svg any, plus apple-touch ({links})",
          ["icon", "/favicon.ico", "", "16x16 32x32 48x48"] in links and ["icon", "/favicon-32.png", "image/png", "32x32"] in links
          and ["icon", "/favicon.svg", "image/svg+xml", "any"] in links and ["apple-touch-icon", "/icons/apple-touch-icon.png", "", ""] in links)
    hrefs = [l[1] for l in links if l[0] == "icon"]
    check(f"home: favicon order is ico, png, then svg so browsers pick the adaptive SVG ({hrefs})",
          hrefs[:3] == ["/favicon.ico", "/favicon-32.png", "/favicon.svg"])

    mark = pg.evaluate("""()=>{const s=document.querySelector('.wordmark-mark'); const r=s.getBoundingClientRect();
        return {fills:[...s.querySelectorAll('path')].map(p=>p.getAttribute('fill')), d:s.querySelector('path').getAttribute('d'), h:r.height, w:r.width}}""")
    check(f"home: wordmark mark is the Shade mark in day colors ({mark['fills']})", mark["fills"] == ["#102536", "#2F6F8A", "#2F6F8A"] and mark["d"].startswith("M42,11H54A20,20"))
    check(f"home: mark about 48px tall, window proportions ({mark['h']:.1f}x{mark['w']:.1f})", 44 <= mark["h"] <= 52 and abs(mark["w"] / mark["h"] - 52 / 74) < 0.03)
    check("home: h1 'Window Seat' still visible in the display face", pg.is_visible("#start h1") and "Fraunces" in pg.evaluate("()=>getComputedStyle(document.querySelector('#start h1')).fontFamily"))

    tok = pg.evaluate("""()=>{const cs=getComputedStyle(document.documentElement); const g=n=>cs.getPropertyValue(n).trim().toLowerCase();
        const probe=document.createElement('div'); probe.style.borderRadius='var(--radius-xl)'; document.body.append(probe);
        const rx=getComputedStyle(probe).borderTopLeftRadius; probe.remove();
        return {tert:g('--color-ink-tertiary'), sleep:g('--color-sleeping'), night:g('--color-accent-night'), overlay:g('--color-overlay-accent'), rx}}""")
    check(f"tokens: ink-tertiary #5a6b76 ({tok['tert']})", tok["tert"].startswith("#5a6b76"))
    check(f"tokens: sleeping #5a6b76 ({tok['sleep']})", tok["sleep"].startswith("#5a6b76"))
    check(f"tokens: accent-night #8cc3d6 ({tok['night']})", tok["night"] == "#8cc3d6")
    check(f"tokens: no --color-overlay-accent ({tok['overlay']!r})", tok["overlay"] == "")
    check(f"tokens: --radius-xl computes to 28px ({tok['rx']})", tok["rx"] == "28px")
    check("tokens: a caption in ink-tertiary renders rgb(90, 107, 118)", pg.evaluate("()=>getComputedStyle(document.querySelector('.panel-foot, .fineprint, .hero-cam')).color") == "rgb(90, 107, 118)")

    weights = pg.evaluate("""()=>Object.fromEntries(['.panel-head h2', '.cap-title', '.tv-url', '.prose h3:not(.this-copy-title)', '.remote-now strong', '#start h1']
        .map(s=>[s, (document.querySelector(s) ? getComputedStyle(document.querySelector(s)).fontWeight : 'missing')]))""")
    check(f"type: titles are 600, none 650 ({weights})", all(w == "600" for w in weights.values()))
    sheet_650 = pg.evaluate("""()=>[...document.styleSheets].flatMap(s=>{try{return [...s.cssRules]}catch(e){return []}})
        .filter(r=>r.style && r.style.fontWeight==='650').map(r=>r.selectorText)""")
    check(f"type: no CSS rule sets font-weight 650 ({sheet_650})", sheet_650 == [])

    rn = pg.evaluate("""()=>{const e=document.querySelector('#right-now-label'), cs=getComputedStyle(e);
        const probe=document.createElement('span'); probe.style.color='var(--color-ink-secondary)'; document.body.append(probe);
        const sec=getComputedStyle(probe).color; probe.style.color='var(--color-accent)'; const acc=getComputedStyle(probe).color; probe.remove();
        return {text:e.textContent, tt:cs.textTransform, color:cs.color, sec, acc, ls:cs.letterSpacing, fs:cs.fontSize}}""")
    check(f"Right now: text 'Right now', sentence case ({rn['text']!r}, {rn['tt']})", rn["text"] == "Right now" and rn["tt"] == "none")
    check(f"Right now: ink-secondary, not accent ({rn['color']})", rn["color"] == rn["sec"] and rn["color"] != rn["acc"])
    check(f"Right now: tracking 0.02em ({rn['ls']} at {rn['fs']})", abs(float(rn["ls"].rstrip("px")) - 0.02 * float(rn["fs"].rstrip("px"))) < 0.05)

    pg.wait_for_timeout(300)  # main fonts link is added after load
    main = pg.evaluate("()=>(document.querySelector('link[data-fonts]')||{}).href||''")
    check("fonts: main request has no 650 or 700 and no Roboto Mono", main and "700" not in main and "650" not in main and "Roboto" not in main and "Plus+Jakarta+Sans:wght@500;600" in main)
    check("fonts: Fraunces 600 only, Noto Sans JP 500 only", "Fraunces:opsz,wght,SOFT,WONK@9..144,600,0,0" in main and "Noto+Sans+JP:wght@500&" in main)
    check("fonts: no Roboto Mono requested while Debug is off", not any("Roboto" in u for u in font_reqs) and pg.locator("link[data-font-mono]").count() == 0)
    pg.click("#start-about")
    pg.wait_for_timeout(200)
    pg.check("#debug-toggle")
    pg.wait_for_timeout(300)
    mono = pg.evaluate("()=>(document.querySelector('link[data-font-mono]')||{}).href||''")
    check(f"fonts: Debug on adds a Roboto Mono 500 stylesheet ({mono})", "family=Roboto+Mono:wght@500" in mono)
    check("fonts: the Roboto Mono request was made (and aborted without breaking the page)", any("Roboto+Mono" in u for u in font_reqs) and pg.evaluate("()=>__windowSeat.isDebugOn()") is True)
    pg.uncheck("#debug-toggle"); pg.wait_for_timeout(100); pg.check("#debug-toggle"); pg.wait_for_timeout(100)
    check("fonts: Roboto Mono link added only once", pg.locator("link[data-font-mono]").count() == 1)
    ctx.close()

    # ------------------------------------------------------------ receiver launch splash
    ctx = b.new_context(viewport={"width": 1920, "height": 1080})
    rx = ctx.new_page()
    rx.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=HELD_YT))
    rx.route("**/cast_receiver_framework.js", lambda r: r.abort())
    rx.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    rx.route("**/gviz/tq*", lambda r: r.abort())
    rx.on("pageerror", lambda e: logs.append(f"PAGEERROR rx: {e}"))
    rx.goto(base + "/receiver.html?debug=1")
    wait_until(rx, "()=>window.__ytGo && document.querySelector('#splash-img').complete", 5000)
    rx.wait_for_timeout(300)
    check(f"receiver: debug strip shows build {BUILD}", f"build {BUILD}" in rx.text_content("#debug-strip"))
    sp = rx.evaluate("""()=>{const s=document.querySelector('#splash'), i=document.querySelector('#splash-img'), cs=getComputedStyle(s);
        return {hidden:s.hidden, op:cs.opacity, bg:cs.backgroundColor, disp:cs.display, nat:i.naturalWidth, src:i.currentSrc, z:cs.zIndex,
                bo:getComputedStyle(document.querySelector('#blackout')).backgroundColor}}""")
    check(f"receiver: splash visible before any cam plays ({sp})", rx.is_visible("#splash") and not sp["hidden"] and sp["op"] == "1" and sp["disp"] != "none")
    check("receiver: splash image loaded (PNG)", sp["nat"] in (1280, 1920) and sp["src"].endswith(".png"))
    check("receiver: splash ground and blackout are the night field #0A1A26", sp["bg"] == "rgb(10, 26, 38)" and sp["bo"] == "rgb(10, 26, 38)")
    check("receiver: no extra 'Opening a window' text on top of the image", "Opening a window" not in (rx.evaluate("()=>document.body.innerText") or ""))
    check("receiver: no cam committed yet", not rx.evaluate("()=>__receiver.state.camId") and rx.evaluate("()=>__receiverDebug.splashDone") is False)

    rx.evaluate("()=>window.__ytGo()")
    check("receiver: first cam reaches PLAYING", wait_until(rx, "()=>__receiver.state.camId && __yt.cam() && __yt.cam().state===1", 6000))
    rx.wait_for_timeout(200)
    check("receiver: splash starts fading once the first cam plays", rx.evaluate("()=>__receiverDebug.splashDone && document.querySelector('#splash').classList.contains('gone')"))
    check("receiver: fade is 1200ms", rx.evaluate("()=>getComputedStyle(document.querySelector('#splash')).transitionDuration") == "1.2s")
    check("receiver: splash gone after the fade", wait_until(rx, "()=>document.querySelector('#splash').hidden", 2500) and not rx.is_visible("#splash"))

    # Cam to cam: blackout only, the splash never comes back.
    first = rx.evaluate("()=>__receiver.state.camId")
    rx.evaluate("()=>__receiver.send({type:'next'})")
    seen = rx.evaluate("""()=>new Promise(res=>{let shown=false; const t0=Date.now(); const tick=()=>{
        if(!document.querySelector('#splash').hidden) shown=true;
        if(Date.now()-t0>1500) return res(shown); requestAnimationFrame(tick)}; tick()})""")
    check("receiver: next cam plays", wait_until(rx, f"()=>__receiver.state.camId && __receiver.state.camId!=={json.dumps(first)} && __yt.cam().state===1", 6000))
    check("receiver: splash not shown again on a cam swap", seen is False and not rx.is_visible("#splash"))
    ctx.close()

    # Reduced motion: no fade on the splash.
    ctx = b.new_context(viewport={"width": 1280, "height": 720}, reduced_motion="reduce")
    rx = ctx.new_page()
    rx.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    rx.route("**/cast_receiver_framework.js", lambda r: r.abort())
    rx.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    rx.route("**/gviz/tq*", lambda r: r.abort())
    rx.on("pageerror", lambda e: logs.append(f"PAGEERROR rx-rm: {e}"))
    rx.goto(base + "/receiver.html?debug=1")
    check("receiver (reduced motion): splash transition off", rx.evaluate("()=>getComputedStyle(document.querySelector('#splash')).transitionDuration") == "0s")
    check("receiver (reduced motion): 720p splash picked", wait_until(rx, "()=>document.querySelector('#splash-img').currentSrc.includes('1280x720')", 3000))
    check("receiver (reduced motion): splash leaves once a cam plays", wait_until(rx, "()=>__receiver.state.camId && __yt.cam().state===1 && document.querySelector('#splash').hidden", 6000))
    ctx.close()
    b.close()

print("page errors:", logs)
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails or logs else 0)
