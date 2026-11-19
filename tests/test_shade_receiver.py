"""Shade rollout PR 2, the TV receiver: build stamp, night overlay tokens, the lower third on the TV type scale
(48/24/24/24 at 1920x1080, scaled with vh so 720p keeps the same proportions and the box stays about a quarter of
the screen height), the sentence-case brand row with the Shade mark, the overscaled cam iframe with no pointer
events, the pause cover, and the unavailable message (YouTube errors 100, 101, 150 and a load that never plays).
Runs against receiver.html?debug=1 with the stubbed YouTube API (tests/fake_youtube.js). Fonts are aborted.
Usage: test_shade_receiver.py BASE_URL   (the site root, for example http://localhost:8000/)"""
import sys, os, re, json
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAKE_YT = open(os.path.join(HERE, "fake_youtube.js")).read()
BUILD = re.search(r'BUILD = "([^"]+)"', open(os.path.join(ROOT, "js/build.js")).read()).group(1)
MSG = "This window is closed for now. Next window in 5 seconds."
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


def read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


def px(v):
    return float(str(v).rstrip("px"))


# The next load on the cam player fails: with a YouTube error code, or (code None) it buffers and never plays, even
# when nudged with playVideo or reloaded. The buffering one reports a small loaded fraction (slow but loading), so it gets
# the 60s patience rather than the 8s no-media fast fail (that one is covered by test_receiver_stall_retry.py).
FAIL_NEXT = """(code) => {
  const yt = __yt.cam();
  const cam = __receiver.state.camId;
  window.__failTarget = null;
  yt.loadVideoById = function (arg) {
    const v = typeof arg === 'object' && arg ? arg.videoId : arg;
    const again = code === null && v === window.__failTarget;
    if (!again && (window.__failTarget !== null || v === cam)) return Object.getPrototypeOf(this).loadVideoById.apply(this, arguments);
    window.__failTarget = v;
    this._rec('loadVideoById', arguments);
    this.vid = v;
    if (code === null) { (window.__ytMedia = window.__ytMedia || {})[v] = { loaded: 0.02 }; this._set(3); return; }
    this.state = -1;
    setTimeout(() => { delete yt.loadVideoById; this.opts.events.onError({ data: code }); }, 0);
  };
  yt.playVideo = function () {
    if (code === null && this.vid === window.__failTarget) return this._rec('playVideo', arguments);
    return Object.getPrototypeOf(this).playVideo.apply(this, arguments);
  };
  __receiver.send({ type: 'next' });
  return cam;
}"""

# The topmost full-screen layer at a point (the overlays take no pointer events, so walk every element there).
# The transparent #safe wrapper is skipped: only the cam and the opaque layers over it count.
TOP_LAYER = """window.topLayer = (x, y) => {
  const layers = ['cam', 'cam-player', 'blackout', 'pause-cover', 'unavailable', 'unavailable-msg', 'splash', 'splash-img'];
  const st = document.createElement('style'); st.textContent = '* { pointer-events: auto !important }'; document.head.append(st);
  const hit = document.elementsFromPoint(x, y).find((e) => layers.includes(e.id) && getComputedStyle(e).opacity !== '0' && getComputedStyle(e).display !== 'none');
  st.remove();
  return hit || null;
};"""

# What is on top at the center of the screen, and the unavailable layer's geometry and colors.
COVER = """() => {
  const el = topLayer(innerWidth / 2, innerHeight / 2);
  const u = document.querySelector('#unavailable'), m = document.querySelector('#unavailable-msg');
  const r = u.getBoundingClientRect(), cs = getComputedStyle(u), ms = getComputedStyle(m);
  return { top: el ? el.id : '', hidden: u.hidden, visible: cs.display !== 'none' && cs.opacity === '1',
           w: r.width, h: r.height, bg: cs.backgroundColor, text: m.textContent, fs: ms.fontSize, lh: ms.lineHeight, fw: ms.fontWeight,
           color: ms.color, align: ms.textAlign }
}"""


# ------------------------------------------------------------ static: build stamp
build_js = re.search(r'BUILD = "([^"]+)"', read("js/build.js")).group(1)
recv_js = re.search(r'BUILD = "([^"]+)"', read("js/receiver.js")).group(1)
sw = re.search(r'CACHE_VERSION = "([^"]+)"', read("sw.js")).group(1)
rx_v = set(re.findall(r"\?v=([\d.-]+)", read("receiver.html")))
check(f"stamp: build.js, receiver.js, sw.js and receiver.html ?v= all {BUILD}", build_js == recv_js == sw == BUILD and rx_v == {BUILD})
check("receiver.js: CAM_LOAD_MS 12000 and CAM_PATIENT_MS_DEFAULT 60000", "const CAM_LOAD_MS = 12000;" in read("js/receiver.js") and "const CAM_PATIENT_MS_DEFAULT = 60000;" in read("js/receiver.js"))
pv = re.search(r'camPlayer = new window\.YT\.Player\("cam-player", \{.*?playerVars: \{([^}]*)\}', read("js/receiver.js"), re.S).group(1)
check("cam playerVars keep controls 0, disablekb 1, fs 0, iv_load_policy 3, modestbranding 1, rel 0, playsinline 1",
      all(k in pv for k in ("controls: 0", "disablekb: 1", "fs: 0", "iv_load_policy: 3", "modestbranding: 1", "rel: 0", "playsinline: 1")))
check("no em dashes in the receiver files", all(chr(0x2014) not in read(f) for f in ("receiver.html", "css/receiver.css", "js/receiver.js", "tests/test_shade_receiver.py")))


def route(pg, tag):
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("**/cast_receiver_framework.js", lambda r: r.abort())
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR {tag}: {e}"))



# Force a two-line title plus place, time, mix and brand, show the lower third, and measure it.
TWO_LINE = """()=>{const c=document.querySelector('#caption');
  document.querySelector('#cap-title').textContent='The long way home along the harbour at golden hour';
  const meta=document.querySelector('#cap-meta');
  if(!meta.querySelector('.cap-place')) meta.innerHTML='<span class="cap-place">Kyoto, Japan</span><span class="cap-time">6:42 pm</span>';
  const mu=document.querySelector('#cap-music'); if(!mu.textContent.trim()) mu.textContent='Mix: Lofi Girl, beats to relax to';
  c.style.transition='none'; c.classList.remove('quiet'); void c.offsetWidth;
  const t=document.querySelector('#cap-title'), ts=getComputedStyle(t), r=c.getBoundingClientRect();
  return {h:r.height, w:r.width, vh:innerHeight, vw:innerWidth, fs:parseFloat(ts.fontSize), lines:Math.round(t.getBoundingClientRect().height/parseFloat(ts.lineHeight)),
          clipped:t.scrollHeight>t.clientHeight+1}}"""


def two_line_check(rx, tag):
    m = rx.evaluate(TWO_LINE)
    check(f"{tag}: two-line caption is at most 26% of the screen height ({m['w']:.0f}x{m['h']:.0f}, {m['h'] / m['vh']:.1%}, title {m['fs']:.1f}px, {m['lines']} lines)",
          m["lines"] == 2 and not m["clipped"] and m["h"] <= 0.26 * m["vh"])
    return m

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1920, "height": 1080})
    rx = ctx.new_page()
    route(rx, "1080p")
    rx.goto(base + "/receiver.html?debug=1")
    rx.add_init_script(TOP_LAYER)
    rx.reload()
    check("a cam is playing", wait_until(rx, "()=>__receiver.state.camId && __yt.cam() && __yt.cam().state===1", 6000))
    check("splash gone", wait_until(rx, "()=>document.querySelector('#splash').hidden", 3000))
    check(f"debug strip shows build {BUILD}", f"build {BUILD}" in rx.text_content("#debug-strip"))
    z = rx.evaluate("()=>({u:+getComputedStyle(document.querySelector('#unavailable')).zIndex, s:+getComputedStyle(document.querySelector('#splash')).zIndex})")
    check(f"unavailable sits above the launch splash so a first-cam failure is visible ({z})", z["u"] > z["s"])

    # ------------------------------------------------------------ tokens
    tok = rx.evaluate("""()=>{const cs=getComputedStyle(document.documentElement); const probe=document.createElement('div'); document.body.append(probe);
        const c=n=>{probe.style.color='var('+n+')'; return getComputedStyle(probe).color};
        const out={overlay:c('--color-overlay'), strong:c('--color-overlay-strong'), dim:c('--color-overlay-dim'), dimRaw:cs.getPropertyValue('--color-overlay-dim').trim().toLowerCase(),
                   capBg:getComputedStyle(document.querySelector('#caption')).backgroundColor, pillBg:getComputedStyle(document.querySelector('#paused-pill')).backgroundColor};
        probe.remove(); return out}""")
    check(f"--color-overlay is rgba(10, 26, 38, 0.78) ({tok['overlay']})", tok["overlay"] == "rgba(10, 26, 38, 0.78)")
    check(f"--color-overlay-strong is rgba(10, 26, 38, 0.86) ({tok['strong']})", tok["strong"] == "rgba(10, 26, 38, 0.86)")
    check(f"--color-overlay-dim is #c9d3da ({tok['dimRaw']}, {tok['dim']})", tok["dimRaw"] == "#c9d3da" and tok["dim"] == "rgb(201, 211, 218)")
    check(f"lower third glass uses --color-overlay ({tok['capBg']})", tok["capBg"] == "rgba(10, 26, 38, 0.78)")
    check(f"Paused pill uses --color-overlay-strong ({tok['pillBg']})", tok["pillBg"] == "rgba(10, 26, 38, 0.86)")

    # ------------------------------------------------------------ cam iframe: 112%, centered, no pointer events
    cam = rx.evaluate("""()=>{const e=document.querySelector('#cam-player'), r=e.getBoundingClientRect(), cs=getComputedStyle(e);
        return {x:r.x, y:r.y, w:r.width, h:r.height, pe:cs.pointerEvents, ov:getComputedStyle(document.querySelector('#cam')).overflow,
                rule:[...document.styleSheets].flatMap(s=>{try{return [...s.cssRules]}catch(e){return []}}).some(r=>r.selectorText && r.selectorText.includes('#cam iframe') && r.style.width==='112%' && r.style.pointerEvents==='none')}}""")
    check(f"cam player is 112% of 1920x1080 ({cam['w']:.1f}x{cam['h']:.1f})", abs(cam["w"] - 1920 * 1.12) <= 1 and abs(cam["h"] - 1080 * 1.12) <= 1)
    check(f"cam player is centered ({cam['x']:.1f}, {cam['y']:.1f})", abs(cam["x"] + 1920 * 0.06) <= 1 and abs(cam["y"] + 1080 * 0.06) <= 1)
    check(f"cam player has pointer-events none, #cam clips ({cam['pe']}, {cam['ov']})", cam["pe"] == "none" and cam["ov"] == "hidden")
    check("the #cam iframe rule itself is 112% with pointer-events none (the real API swaps in an iframe)", cam["rule"])
    music = rx.evaluate("()=>{const r=document.querySelector('#music-player').getBoundingClientRect(); return [r.width, r.height]}")
    check(f"music player untouched at 356x200 ({music})", music == [356, 200])

    # ------------------------------------------------------------ lower third on a cam with a timezone
    tz_cam = rx.evaluate("async()=>{const {CAMS}=await import('/js/data.js'); const c=CAMS.find(c=>c.timezone && c.id!==__receiver.state.camId); return c ? c.id : null}")
    check("a built-in cam with a timezone exists", bool(tz_cam))
    rx.evaluate("(id)=>__receiver.send({type:'cam', id})", tz_cam)
    check("timezone cam is showing", wait_until(rx, f"()=>__receiver.state.camId==={json.dumps(tz_cam)} && __yt.cam().state===1", 6000))
    rx.wait_for_timeout(600)
    ty = rx.evaluate("""()=>{const g=s=>{const e=document.querySelector(s), cs=getComputedStyle(e); return {fs:cs.fontSize, lh:cs.lineHeight, fw:cs.fontWeight, ff:cs.fontFamily, color:cs.color, tt:cs.textTransform, ls:cs.letterSpacing, gap:cs.columnGap}};
        const c=document.querySelector('#caption'), cs=getComputedStyle(c), r=c.getBoundingClientRect();
        const mark=document.querySelector('#cap-brand svg.cap-mark'), mr=mark ? mark.getBoundingClientRect() : null;
        const place=document.querySelector('#cap-meta .cap-place').getBoundingClientRect(), time=document.querySelector('#cap-meta .cap-time');
        return {title:g('#cap-title'), meta:g('#cap-meta'), music:g('#cap-music'), brand:g('#cap-brand'), time:time ? {color:getComputedStyle(time).color, gap:time.getBoundingClientRect().x-place.right, top:time.getBoundingClientRect().y-place.y} : null,
                cap:{pt:cs.paddingTop, pl:cs.paddingLeft, rad:cs.borderTopLeftRadius, maxw:cs.maxWidth, x:r.x, bottom:r.bottom, w:r.width, op:cs.opacity, clamp:getComputedStyle(document.querySelector('#cap-title')).webkitLineClamp},
                brandText:document.querySelector('#cap-brand').textContent, mark:mr ? {h:mr.height, w:mr.width, fills:[...mark.querySelectorAll('path')].map(p=>p.getAttribute('fill'))} : null,
                metaText:document.querySelector('#cap-meta').textContent}}""")
    near = lambda v, want: abs(px(v) - want) <= 1
    check(f"lower third visible ({ty['cap']['op']})", ty["cap"]["op"] == "1")
    check(f"title 48/56 Fraunces 600 ({ty['title']['fs']}/{ty['title']['lh']} {ty['title']['fw']})", near(ty["title"]["fs"], 48) and near(ty["title"]["lh"], 56) and ty["title"]["fw"] == "600" and "Fraunces" in ty["title"]["ff"])
    check(f"title clamps at 2 lines ({ty['cap']['clamp']})", ty["cap"]["clamp"] == "2")
    check(f"place 24/32 Jakarta 500 ({ty['meta']['fs']}/{ty['meta']['lh']} {ty['meta']['fw']})", near(ty["meta"]["fs"], 24) and near(ty["meta"]["lh"], 32) and ty["meta"]["fw"] == "500" and "Plus Jakarta Sans" in ty["meta"]["ff"])
    check(f"local time in accent-night after a 16px gap, no mid-dot ({ty['time']}, {ty['metaText']!r})",
          ty["time"] and ty["time"]["color"] == "rgb(140, 195, 214)" and abs(ty["time"]["gap"] - 16) <= 1 and abs(ty["time"]["top"]) <= 1 and "·" not in ty["metaText"])
    check(f"mix line 24/32 Jakarta 500 ({ty['music']['fs']}/{ty['music']['lh']} {ty['music']['fw']})", near(ty["music"]["fs"], 24) and near(ty["music"]["lh"], 32) and ty["music"]["fw"] == "500")
    check(f"brand 24/32 Jakarta 600 overlay-dim ({ty['brand']['fs']}/{ty['brand']['lh']} {ty['brand']['fw']} {ty['brand']['color']})",
          near(ty["brand"]["fs"], 24) and near(ty["brand"]["lh"], 32) and ty["brand"]["fw"] == "600" and ty["brand"]["color"] == "rgb(201, 211, 218)")
    check(f"brand is sentence case 'Window Seat', not uppercase or tracked ({ty['brandText']!r}, {ty['brand']['tt']}, {ty['brand']['ls']})",
          ty["brandText"] == "Window Seat" and ty["brand"]["tt"] == "none" and ty["brand"]["ls"] in ("normal", "0px"))
    check("no 'Street' in the lower third", "Street" not in rx.text_content("#caption"))
    check(f"brand row has a 24px Shade mark in night colors ({ty['mark']})",
          ty["mark"] and abs(ty["mark"]["h"] - 24) <= 1 and abs(ty["mark"]["w"] / ty["mark"]["h"] - 52 / 74) < 0.03 and ty["mark"]["fills"] == ["#F3F5F7", "#8CC3D6", "#8CC3D6"])
    check(f"lower third padding 24 x 32, radius 24, max 960px ({ty['cap']})",
          near(ty["cap"]["pt"], 24) and near(ty["cap"]["pl"], 32) and near(ty["cap"]["rad"], 24) and ty["cap"]["maxw"] == "960px" and ty["cap"]["w"] <= 961)
    check(f"lower third bottom left inside the 5% safe area ({ty['cap']['x']}, {ty['cap']['bottom']})", abs(ty["cap"]["x"] - 96) <= 1 and abs(ty["cap"]["bottom"] - (1080 - 54)) <= 1)
    tr = rx.evaluate("""()=>{const c=document.querySelector('#caption'); const on=getComputedStyle(c); const a={d:on.transitionDuration, t:on.transform};
        c.classList.add('quiet'); const q=getComputedStyle(c); const out={on:a, quiet:{d:q.transitionDuration, delay:q.transitionDelay}};
        c.style.transition='none'; out.quiet.t=getComputedStyle(c).transform; c.classList.remove('quiet'); void c.offsetWidth; c.style.transition=''; return out}""")
    check(f"enter 400ms, leave 800ms fade with the 16px offset applied after the fade ({tr})",
          tr["on"]["d"] == "0.4s, 0.4s" and tr["on"]["t"] == "none" and tr["quiet"]["d"].startswith("0.8s") and "0.8s" in tr["quiet"]["delay"] and abs(float(tr["quiet"]["t"].rstrip(")").split(",")[-1]) - 16) <= 1)
    small = rx.evaluate("""()=>[...document.querySelectorAll('#caption, #caption *, #cmd-toast, #paused-pill, #paused-pill *, #unavailable-msg')]
        .filter(e=>[...e.childNodes].some(n=>n.nodeType===3 && n.textContent.trim())).map(e=>[e.id||e.className, parseFloat(getComputedStyle(e).fontSize)]).filter(x=>x[1]<24)""")
    check(f"nothing in the TV overlay is under 24px at 1080p ({small})", small == [])
    tp = rx.evaluate("()=>['#cmd-toast','#paused-pill'].map(s=>{const cs=getComputedStyle(document.querySelector(s)); return [cs.fontSize, cs.lineHeight, cs.fontWeight]})")
    check(f"toast and Paused pill 32/40 600 ({tp})", all(near(f, 32) and near(l, 40) and w == "600" for f, l, w in tp))
    two_line_check(rx, "1080p")

    # ------------------------------------------------------------ pause: covered at once, uncovered when playing again
    rx.evaluate("()=>__receiver.send({type:'pause', value:true})")
    now = rx.evaluate("()=>({cover:__receiverDebug.pauseCover, op:getComputedStyle(document.querySelector('#pause-cover')).opacity})")
    check(f"pause: the night field covers the cam immediately ({now})", now["cover"] is True and now["op"] == "1")
    rx.wait_for_timeout(200)
    pz = rx.evaluate("()=>({cam:__yt.cam().state, top:(()=>{const e=topLayer(960, 300); return e && e.id})(), bg:getComputedStyle(document.querySelector('#pause-cover')).backgroundColor})")
    check(f"pause: cam paused under the cover, Paused pill shown ({pz})", pz["cam"] == 2 and pz["top"] == "pause-cover" and pz["bg"] == "rgb(10, 26, 38)" and rx.is_visible("#paused-pill"))
    check("pause: no unavailable message", not rx.is_visible("#unavailable"))
    rx.evaluate("()=>__receiver.send({type:'pause', value:false})")
    check("resume: cover lifts once the cam is PLAYING", wait_until(rx, "()=>__yt.cam().state===1 && !__receiverDebug.pauseCover", 3000))
    rx.wait_for_timeout(500)
    check("resume: cover fully faded, Paused pill hidden", rx.evaluate("()=>getComputedStyle(document.querySelector('#pause-cover')).opacity") == "0" and not rx.is_visible("#paused-pill"))

    # ------------------------------------------------------------ ordinary rotation: no unavailable message
    first = rx.evaluate("()=>__receiver.state.camId")
    rx.evaluate("()=>{window.__seenU=false; const t0=Date.now(); const tick=()=>{ if(!document.querySelector('#unavailable').hidden) window.__seenU=true; if(Date.now()-t0<2000) requestAnimationFrame(tick)}; tick(); __receiver.send({type:'next'})}")
    check("next: a new cam plays", wait_until(rx, f"()=>__receiver.state.camId!=={json.dumps(first)} && __yt.cam().state===1", 6000))
    rx.wait_for_timeout(2100)
    check("next: the unavailable message never showed on an ordinary swap", rx.evaluate("()=>window.__seenU") is False)

    # ------------------------------------------------------------ YouTube errors 100, 101, 150
    for code in (100, 101, 150):
        rx.evaluate(FAIL_NEXT, code)
        ok = wait_until(rx, "()=>window.__failTarget && __receiver.state.failedCams.includes(window.__failTarget)", 4000)
        cv = rx.evaluate(COVER)
        check(f"error {code}: cam failed and the message shows at once ({cv['text']!r})", ok and cv["visible"] and not cv["hidden"] and cv["text"] == MSG)
        check(f"error {code}: night field covers the whole screen, YouTube's error card is not visible ({cv['top']}, {cv['w']}x{cv['h']}, {cv['bg']})",
              cv["top"] in ("unavailable", "unavailable-msg") and cv["w"] == 1920 and cv["h"] == 1080 and cv["bg"] == "rgb(10, 26, 38)")
        check(f"error {code}: message 32/40 Jakarta 500, overlay-ink, centered ({cv['fs']}/{cv['lh']} {cv['fw']} {cv['color']} {cv['align']})",
              near(cv["fs"], 32) and near(cv["lh"], 40) and cv["fw"] == "500" and cv["color"] == "rgb(243, 245, 247)" and cv["align"] == "center")
        check(f"error {code}: the next cam plays and the message is gone",
              wait_until(rx, "()=>__receiver.state.camId && __receiver.state.camId!==window.__failTarget && __yt.cam().state===1 && document.querySelector('#unavailable').hidden", 6000)
              and not rx.is_visible("#unavailable") and rx.evaluate("()=>__receiverDebug.unavailable") is False)

    # ------------------------------------------------------------ a slow load that never reaches PLAYING (CAM_PATIENT_MS, 60s)
    # Waited out with no playVideo, no reload and no message; after 3s the "Loading cam..." card replaces the black field.
    # (The 60s failure itself is covered by test_receiver_stall_retry.py.)
    rx.evaluate(FAIL_NEXT, None)
    n_calls = rx.evaluate("()=>__yt.cam().calls.length")
    check("never playing: the field is up while it loads, no message yet", wait_until(rx, "()=>window.__failTarget!==null", 3000) and not rx.is_visible("#unavailable"))
    LOADS = "()=>__yt.cam().calls.filter(c=>c[0]==='loadVideoById' && c[1]===window.__failTarget).length"
    rx.wait_for_timeout(14000)
    check("never playing: past the old 12s cutoff: still one load, no nudge, no message, not failed",
          rx.evaluate(LOADS) == 1 and not rx.evaluate("(n)=>__yt.cam().calls.slice(n).some(c=>c[0]==='playVideo')", n_calls)
          and not rx.is_visible("#unavailable") and not rx.evaluate("()=>__receiver.state.failedCams.includes(window.__failTarget)"))
    lc = rx.evaluate("""()=>{const e=document.querySelector('#loading'), m=document.querySelector('#loading-msg'), r=m.getBoundingClientRect(), cs=getComputedStyle(m);
      return {hidden:e.hidden, text:m.textContent, fs:parseFloat(cs.fontSize), color:cs.color, align:cs.textAlign, w:r.width>0}}""")
    check(f"never playing: the Loading card shows, large and centered ({lc})",
          not lc["hidden"] and lc["text"] == "Loading cam..." and near(lc["fs"], 32) and lc["color"] == "rgb(243, 245, 247)" and lc["align"] == "center" and lc["w"])
    rx.evaluate("()=>{ delete __yt.cam().loadVideoById; __receiver.send({type:'next'}); }")
    check("never playing: a pick clears the card and the next cam plays",
          wait_until(rx, "()=>__receiver.state.camId!==window.__failTarget && __yt.cam().state===1 && document.querySelector('#loading').hidden && document.querySelector('#unavailable').hidden", 8000))
    ctx.close()

    # ------------------------------------------------------------ 720p and 540p: sizes scale with vh, no 1080p px floors; reduced motion
    ctx = b.new_context(viewport={"width": 1280, "height": 720}, reduced_motion="reduce")
    rx = ctx.new_page()
    route(rx, "720p")
    rx.goto(base + "/receiver.html?debug=1")
    wait_until(rx, "()=>__receiver.state.camId && __yt.cam() && __yt.cam().state===1", 6000)
    rx.wait_for_timeout(300)
    fl = rx.evaluate("()=>['#cap-title','#cap-meta','#cap-music','#cap-brand'].map(s=>getComputedStyle(document.querySelector(s)).fontSize)")
    want = [v * 720 / 1080 for v in (48, 24, 24, 24)]
    check(f"720p: sizes scale with the viewport, title about 32px ({fl})", all(abs(px(v) - w) <= 1 for v, w in zip(fl, want)))
    mw = rx.evaluate("()=>document.querySelector('#caption').getBoundingClientRect().width")
    check(f"720p: lower third at most 640px wide (50vw) ({mw})", mw <= 641)
    two_line_check(rx, "720p")
    rm = rx.evaluate("()=>{const c=document.querySelector('#caption'); c.classList.add('quiet'); const t=getComputedStyle(c).transform; c.classList.remove('quiet'); return t}")
    check(f"reduced motion: no rise on the lower third ({rm})", rm == "none")
    ctx.close()

    ctx = b.new_context(viewport={"width": 960, "height": 540})
    rx = ctx.new_page()
    route(rx, "540p")
    rx.goto(base + "/receiver.html?debug=1")
    wait_until(rx, "()=>__receiver.state.camId && __yt.cam() && __yt.cam().state===1", 6000)
    rx.wait_for_timeout(300)
    two_line_check(rx, "540p")
    ctx.close()
    b.close()

print("page errors:", logs)
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails or logs else 0)
