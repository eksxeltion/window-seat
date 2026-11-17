"""Bright Home with the Right now hero, the full-screen local player (chrome, auto-hide, floating toggle, Home,
transport, pin, volume, Debug), the bright casting remote, and the TV lower third / quiet TV.
Runs against a stubbed YouTube IFrame API (tests/fake_youtube.js) and the mock Cast sender, so it is deterministic.
Usage: test_local_player.py BASE_URL   (the site root, for example http://localhost:8000/)"""
import sys, os, re, json, base64
from playwright.sync_api import sync_playwright

base = sys.argv[1].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
here = os.path.dirname(os.path.abspath(__file__))
FAKE_YT = open(os.path.join(here, "fake_youtube.js")).read()
MOCK = open(os.path.join(here, "mock_cast_sender.js")).read()
BUILD = "2026-11-16.11"
TAGLINE = "Live windows onto the world, with soft music, made for your TV."
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
# Every page denies the Fullscreen API: the player must stay full bleed and keep playing anyway.
DENY_FS = """Element.prototype.requestFullscreen = function(){ window.__fsAsked = (window.__fsAsked||0)+1; return Promise.reject(new TypeError('denied')); };
Element.prototype.webkitRequestFullscreen = undefined;"""
# Saved sheet copy (applied by js/config.js at module start): a deterministic list with timezones.
SAVED = {
    "at": 1,
    "cams": [
        {"id": "hlbh4P0Mz8M", "title": "Odaiba and Rainbow Bridge", "country": "Japan", "city": "Tokyo", "place": "Tokyo", "type": "harbor", "channel": "ODAIBA TOKYO LIVE", "default": True, "timezone": "Asia/Tokyo"},
        {"id": "0F-vs59QJas", "title": "Sydney Harbour (Bridge and Opera House)", "country": "Australia", "city": "Sydney", "place": "Sydney", "type": "harbor", "channel": "Sydney Harbour LIVE", "default": True, "timezone": "Australia/Sydney"},
        {"id": "QI-PPEW2Bgc", "title": "Eiffel Tower and Tour Montparnasse", "country": "France", "city": "Paris", "place": "Paris", "type": "skyline", "channel": "Paris75Webcam", "default": True, "timezone": "Europe/Paris"},
        {"id": "Yzl9offu5g8", "title": "Naha Airport", "country": "Japan", "city": "Naha", "place": "Naha", "type": "airport", "channel": "RBC NEWS", "default": True, "timezone": ""},
        {"id": "z3OUPSoABJk", "title": "Sapporo City", "country": "Japan", "city": "Sapporo", "place": "Sapporo", "type": "skyline", "channel": "STV", "default": False, "timezone": "Asia/Tokyo"},
    ],
    "mixes": [{"id": "nCtOCLv6eFY", "title": "M1", "en": "Mix one", "minutes": 60}],
}
# Monday 16 November 2026, 4:51 PM Pacific: 9:51 AM in Tokyo, 11:51 AM in Sydney, 1:51 AM in Paris (sleeping).
MOCK_NOW = "2026-11-17T00:51:00Z"
fails, logs = [], []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def wait_until(pg, js, ms=4000):
    try:
        pg.wait_for_function(js, timeout=ms)
        return True
    except Exception:
        return False


def box(pg, sel):
    return pg.locator(sel).first.bounding_box()


def overlaps(a, b):
    return a and b and not (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"] or a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])


def route(pg, sheet=None):
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.route("**/cast_receiver_framework.js", lambda r: r.abort())
    pg.route("https://i.ytimg.com/**", lambda r: r.fulfill(status=200, content_type="image/png", body=PNG))
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())  # fonts must be optional
    if sheet:
        pg.route("**/gviz/tq*", sheet)
    else:
        pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))


with sync_playwright() as p:
    b = p.chromium.launch()

    def phone(init=None, clock=None, sheet=None):
        ctx = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
        ctx.add_init_script(DENY_FS)
        if init:
            ctx.add_init_script(init)
        pg = ctx.new_page()
        if clock:
            pg.clock.set_fixed_time(clock)
        route(pg, sheet)
        pg.goto(base + "/index.html")
        pg.wait_for_timeout(1200)
        return pg

    # ------------------------------------------------------------ Home: bright, copy, hero from the real list
    # Built-in list only (no sheet, no saved copy) at the fixed clock: data.js carries the sheet's timezones.
    pg = phone(clock=MOCK_NOW)
    bg = pg.evaluate("()=>getComputedStyle(document.querySelector('#start')).backgroundColor")
    check(f"home: bright canvas #F3F5F7 ({bg})", bg == "rgb(243, 245, 247)")
    check("home: theme-color is the canvas", pg.get_attribute('meta[name="theme-color"]', "content").upper() == "#F3F5F7")
    check("home: wordmark 'Window Seat' in the display face", pg.text_content("#start h1") == "Window Seat" and "Fraunces" in pg.evaluate("()=>getComputedStyle(document.querySelector('#start h1')).fontFamily"))
    check("home: UI face is Plus Jakarta Sans with a system-ui fallback", re.search(r"Plus Jakarta Sans.*system-ui", pg.evaluate("()=>getComputedStyle(document.body).fontFamily")) is not None)
    check("home: tagline", pg.text_content(".tagline") == TAGLINE)
    check("home: #start-btn reads 'Start watching'", pg.inner_text("#start-btn").strip() == "Start watching")
    sb = box(pg, "#start-btn")
    check(f"home: Start watching is a full-width 52px pill ({sb})", sb and abs(sb["height"] - 52) <= 1 and sb["width"] >= 412 - 2 * 24 - 2)
    check("home: Cast to your TV control present with the cast launcher", pg.is_visible(".cast-start") and pg.text_content(".cast-start .cast-label") == "Cast to your TV" and pg.locator(".cast-start google-cast-launcher").count() == 1)
    check("home: Open in TV browser and About and credits present", pg.is_visible("#start-tv") and pg.text_content("#start-tv") == "Open in TV browser" and pg.is_visible("#start-about") and pg.text_content("#start-about") == "About and credits")
    order = pg.evaluate("()=>['#right-now','#start-btn','.cast-start','#start-tv','#start-about'].map(s=>document.querySelector(s).getBoundingClientRect().top)")
    check(f"home: hero, Start watching, Cast, then ghost actions in order ({order})", order == sorted(order))
    check("home: Right now hero visible", pg.is_visible("#right-now") and pg.text_content("#right-now-label") == "Right now")
    hero = pg.evaluate("""async()=>{const {CAMS}=await import('/js/data.js'); const lt=await import('/js/localtime.js'); const id=__windowSeat.heroCamId(); const c=CAMS.find(x=>x.id===id);
      return {id, inList: !!c, inRotation: !!(c&&c.default), city: c&&c.city, title: c&&c.title, time: document.querySelector('#hero-time').textContent,
              line: document.querySelector('#hero-line').textContent, cam: document.querySelector('#hero-cam').textContent,
              awake: lt.awakeCams(CAMS, new Date()).length, zones: CAMS.every(x=>lt.isValidZone(x.timezone)), img: document.querySelector('#hero-img').getAttribute('src')}}""")
    print("hero:", hero)
    check("home: hero cam is an in-rotation cam from the real list", hero["inList"] and hero["inRotation"])
    check("home: hero names that cam's real city and title", hero["city"] in hero["time"] and hero["title"] in hero["cam"] and hero["city"] in hero["cam"])
    check("home: every built-in cam has a valid IANA timezone", hero["zones"] is True)
    check(f"home: built-in list shows local time: 'It's ... in {hero['city']}.' ({hero['time']})", re.fullmatch(r"It's \d{1,2}:\d\d (AM|PM) in " + re.escape(hero["city"]) + r"\.", hero["time"]) is not None)
    check(f"home: awake count comes from the list ({hero['awake']})", f"{hero['awake']} windows are awake." in hero["line"])
    check("home: hero still is the cam's YouTube thumbnail", hero["img"] == f"https://i.ytimg.com/vi/{hero['id']}/hqdefault.jpg")
    check("home: hero still shown once loaded", wait_until(pg, "()=>!document.querySelector('#hero-img').hidden", 2000))
    pg.context.close()

    # Saved sheet copy with timezones and a fixed clock: "It's 9:51 AM in Tokyo." shape, sleeping rule, awake count.
    pg = phone(init=f"localStorage.setItem('windowSeat.sheetConfig', {json.dumps(json.dumps(SAVED))})", clock=MOCK_NOW)
    t, line, cam = pg.text_content("#hero-time"), pg.text_content("#hero-line"), pg.text_content("#hero-cam")
    print("hero (saved list):", t, "|", line, "|", cam)
    check(f"hero: 'It's 9:51 AM in Tokyo.' or 'It's 11:51 AM in Sydney.' ({t})", t in ("It's 9:51 AM in Tokyo.", "It's 11:51 AM in Sydney."))
    check(f"hero: line is 'Morning on the harbor. 3 windows are awake.' ({line})", line == "Morning on the harbor. 3 windows are awake.")
    check("hero: never picks the sleeping Paris cam, the cam without a timezone, or a cam out of rotation", not any(x in cam for x in ("Paris", "Naha", "Sapporo")))
    lt = pg.evaluate("""async()=>{const m=await import('/js/localtime.js'); const d=new Date('2026-11-17T00:51:00Z');
      return {tokyo: m.camLocalTime({timezone:'Asia/Tokyo'}, d), sleepParis: m.isSleeping({timezone:'Europe/Paris'}, d), sleepBlank: m.isSleeping({timezone:''}, d),
              sleepBad: m.isSleeping({timezone:'Tokyo'}, d), badTime: m.camLocalTime({timezone:'Mars/Olympus'}, d), valid: m.isValidZone('America/Indiana/Indianapolis')}}""")
    check(f"localtime helper: valid IANA only, midnight to 6 AM sleeps ({lt})", lt == {"tokyo": "9:51 AM", "sleepParis": True, "sleepBlank": False, "sleepBad": False, "badTime": "", "valid": True})
    pg.context.close()

    # The hero updates when the sheet arrives after first paint.
    SHEET_CAMS = "youtube_id,name,country,city,type,in_rotation,timezone\nhlbh4P0Mz8M,Odaiba and Rainbow Bridge,Japan,Tokyo,harbor,TRUE,Asia/Tokyo\n"
    SHEET_MIXES = "youtube_id,title,enabled\nnCtOCLv6eFY,M1,TRUE\n"

    def sheet(r):
        body = SHEET_MIXES if "sheet=Mixes" in r.request.url else SHEET_CAMS
        r.fulfill(status=200, content_type="text/csv", body=body)

    ctx = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
    ctx.add_init_script(DENY_FS)
    pg = ctx.new_page()
    route(pg)
    pg.unroute("**/gviz/tq*")
    pending = []
    pg.route("**/gviz/tq*", lambda r: pending.append(r))  # held until the first paint is checked
    pg.goto(base + "/index.html")
    pg.wait_for_timeout(1000)
    before = pg.text_content("#hero-time")
    for r in pending:
        sheet(r)
    check("hero: after the sheet loads it shows the sheet cam with its local time", wait_until(pg, "()=>/^It's \\d{1,2}:\\d\\d (AM|PM) in Tokyo\\.$/.test(document.querySelector('#hero-time').textContent)", 4000))
    check(f"hero: first paint came from the built-in list, with its local time ({before})", re.fullmatch(r"It's \d{1,2}:\d\d (AM|PM) in .+\.", before) is not None)
    check("hero: count follows the sheet (1 window)", pg.text_content("#hero-line").endswith("1 window is awake.") or pg.text_content("#hero-line").endswith("All the windows are asleep."))
    ctx.close()

    # ------------------------------------------------------------ Local player
    pg = phone()
    pg.click("#start-btn")
    check("player: a cam is playing after Start", wait_until(pg, "()=>__windowSeat.currentCamId() && __yt.cam() && __yt.cam().state===1", 6000))
    wait_until(pg, "()=>__yt.music() && __yt.music().state===1")
    pg.wait_for_timeout(300)
    ws = lambda k: pg.evaluate(f"()=>__windowSeat.{k}()")
    check("player: Fullscreen API was asked and denied, playback continues", pg.evaluate("()=>window.__fsAsked") >= 1 and pg.evaluate("()=>__yt.cam().state") == 1)
    st = box(pg, "#stage")
    check(f"player: cam stage is the full viewport ({st})", st == {"x": 0, "y": 0, "width": 412, "height": 915})
    check("player: night field and theme-color", pg.evaluate("()=>getComputedStyle(document.body).backgroundColor") == "rgb(10, 26, 38)" and pg.get_attribute('meta[name="theme-color"]', "content").upper() == "#0A1A26")
    check("player: Start screen gone", wait_until(pg, "()=>document.querySelector('#start').hidden", 2000))
    check("player: top scrim with Home, Cast and full screen", all(pg.is_visible(s) for s in ("#lp-home", "#player-top [data-cast]", "#lp-fullscreen")))
    check("player: Cast control is the official launcher", pg.locator("#player-top [data-cast] google-cast-launcher").count() == 1)
    check("player: bottom glass visible", pg.is_visible("#bar"))
    cam = pg.evaluate("async()=>{const {CAMS}=await import('/js/data.js'); return CAMS.find(c=>c.id===__windowSeat.currentCamId())}")
    check("player: glass shows the cam name", pg.text_content(".cap-title") == cam["title"])
    check(f"player: meta is '{{city}}, {{country}} · {{local time}}' ({pg.text_content('.cap-meta')})", re.fullmatch(re.escape(f"{cam['city']}, {cam['country']}") + r" · \d{1,2}:\d\d (AM|PM)", pg.text_content(".cap-meta")) is not None)
    check("player: meta has no channel or type", cam["channel"] not in pg.text_content(".cap-meta"))
    check(f"player: rotation status 'Next cam in M:SS' ({pg.text_content('#lp-status-text')})", re.fullmatch(r"Next cam in \d:\d\d", pg.text_content("#lp-status-text")) is not None)
    sizes = {s: box(pg, f"#bar [data-action={s}]") for s in ("prev", "play-pause", "next")}
    check(f"player: transport 64 / 72 / 64 ({[(k, v['width'], v['height']) for k, v in sizes.items()]})",
          sizes["prev"]["width"] == 64 and sizes["play-pause"]["width"] == 72 and sizes["next"]["width"] == 64 and sizes["prev"]["x"] < sizes["play-pause"]["x"] < sizes["next"]["x"])
    order = pg.evaluate("()=>[...document.querySelectorAll('#bar .lp-transport [data-action]')].map(b=>b.dataset.action)")
    check(f"player: transport row is only prev, play-pause, next ({order})", order == ["prev", "play-pause", "next"])
    check("player: Interval and Lite are not in the primary transport", pg.locator("#bar [data-action=interval], #bar [data-action=lite]").count() == 0 and pg.locator("#panel-cams [data-action=interval]").count() == 1 and pg.locator("#panel-cams [data-action=lite]").count() == 1)
    small = pg.evaluate("()=>[...document.querySelectorAll('#player-top button, #bar button, #chrome-toggle')].filter(b=>b.offsetParent).map(b=>{const r=b.getBoundingClientRect(); return [b.id||b.dataset.action||b.className, r.width, r.height]}).filter(x=>x[1]<48||x[2]<40)")
    check(f"player: controls have 48px hit targets ({small})", small == [] or all(x[0] == "pin-pill" for x in small))
    fab = box(pg, "#chrome-toggle")
    check(f"player: 52px floating button at the bottom right ({fab})", fab["width"] == 52 and fab["height"] == 52 and abs(fab["x"] + 52 - (412 - 16)) <= 1 and abs(fab["y"] + 52 - (915 - 16)) <= 1)
    check("player: floating button does not cover the glass controls", not any(overlaps(fab, box(pg, s)) for s in ("[data-action=open-cams]", "[data-action=open-music]", "#music-volume", "#pin-pill", "#bar [data-action=next]")))

    # Floating button toggles the chrome.
    pg.click("#chrome-toggle")
    pg.wait_for_timeout(300)
    check("player: floating button hides the chrome", ws("chromeHidden") is True and not pg.is_visible("#bar") and not pg.is_visible("#lp-home") and pg.is_visible("#chrome-toggle"))
    check("player: floating button stays in its corner while hidden", box(pg, "#chrome-toggle") == fab)
    pg.click("#chrome-toggle")
    pg.wait_for_timeout(300)
    check("player: floating button shows the chrome again", ws("chromeHidden") is False and pg.is_visible("#bar") and pg.get_attribute("#chrome-toggle", "aria-expanded") == "true")

    # Auto-hide after 6 seconds without a tap; a tap on the picture brings it back.
    pg.wait_for_timeout(5000)
    check("player: chrome still up at 5s", ws("chromeHidden") is False)
    pg.wait_for_timeout(1600)
    check("player: chrome hides about 6s after the last tap", ws("chromeHidden") is True and not pg.is_visible("#bar"))
    check("player: floating button remains when the chrome is hidden", pg.is_visible("#chrome-toggle"))
    check("player: cam keeps playing with the chrome hidden", pg.evaluate("()=>__yt.cam().state") == 1)
    pg.mouse.click(200, 300)
    pg.wait_for_timeout(300)
    check("player: a tap on the video shows the chrome", ws("chromeHidden") is False and pg.is_visible("#bar"))

    # Paused: chrome stays past 6s, status reads Paused.
    pg.click("#bar [data-action=play-pause]")
    pg.wait_for_timeout(300)
    check("player: pause pauses the whole feed", ws("feedPaused") is True and pg.evaluate("()=>__yt.cam().state") == 2 and pg.evaluate("()=>__yt.music().state") == 2)
    check("player: status reads Paused", pg.text_content("#lp-status-text") == "Paused")
    check("player: play button label flips to Play", pg.get_attribute("#bar [data-action=play-pause]", "aria-label") == "Play")
    pg.wait_for_timeout(6800)
    check("player: chrome stays visible past 6s while paused", ws("chromeHidden") is False and pg.is_visible("#bar"))
    pg.click("#bar [data-action=play-pause]")
    pg.wait_for_timeout(300)
    check("player: resume plays cam and music", ws("feedPaused") is False and pg.evaluate("()=>__yt.cam().state") == 1 and pg.evaluate("()=>__yt.music().state") == 1)

    # An open sheet keeps the chrome up.
    pg.click("#bar [data-action=open-cams]")
    pg.wait_for_timeout(6800)
    check("player: chrome stays visible past 6s while a sheet is open", ws("chromeHidden") is False and pg.is_visible("#panel-cams"))
    check("player: Cams sheet is bright", pg.evaluate("()=>getComputedStyle(document.querySelector('#panel-cams')).backgroundColor") == "rgb(243, 245, 247)")
    check("player: list status lives in the Cams sheet", "List:" in pg.text_content("#panel-cams .list-status"))
    pg.click("#panel-cams [data-action=close-panel]")
    pg.wait_for_timeout(300)
    pg.click("#bar [data-action=open-music]")
    pg.wait_for_timeout(200)
    check("player: Mix opens the existing Music sheet", pg.is_visible("#panel-music") and pg.locator("#panel-music .mix-btn").count() > 0)
    pg.click("#panel-music [data-action=close-panel]")
    pg.wait_for_timeout(300)

    # Pin, next and prev from the glass.
    pg.click("#pin-pill")
    pg.wait_for_timeout(200)
    check("player: pin pill reads Pinned", pg.text_content("#pin-pill") == "Pinned" and pg.get_attribute("#pin-pill", "aria-pressed") == "true")
    check("player: status reads 'Pinned. Rotation off.'", pg.text_content("#lp-status-text") == "Pinned. Rotation off.")
    c1 = ws("currentCamId")
    pg.click("#bar [data-action=next]")
    check("player: next changes the cam and keeps the pin", wait_until(pg, f"()=>__windowSeat.currentCamId()!=={json.dumps(c1)} && !__windowSeat.state.pending") and ws("pinned") is True)
    c2 = ws("currentCamId")
    pg.click("#bar [data-action=prev]")
    check("player: prev changes the cam", wait_until(pg, f"()=>__windowSeat.currentCamId()!=={json.dumps(c2)} && !__windowSeat.state.pending"))
    pg.click("#pin-pill")
    pg.wait_for_timeout(200)
    check("player: unpinned, status back to the rotation clock", ws("pinned") is False and pg.text_content("#lp-status-text").startswith("Next cam in"))

    # Music volume on the glass: mix only.
    pg.evaluate("()=>{const r=document.querySelector('#bar #music-volume'); r.value='40'; r.dispatchEvent(new Event('input',{bubbles:true}))}")
    pg.wait_for_timeout(150)
    check("player: Music slider on the glass sets the mix volume", pg.evaluate("()=>__yt.music().getVolume()") == 40 and pg.evaluate("()=>__yt.cam().getVolume()") == 0)
    check("player: slider labeled Music", pg.evaluate("()=>document.querySelector('#music-volume').closest('label').querySelector('span').textContent") == "Music")

    # Full screen control: denied request is harmless.
    pg.click("#lp-fullscreen")
    pg.wait_for_timeout(300)
    check("player: full screen control asks, denial keeps playing", pg.evaluate("()=>window.__fsAsked") >= 2 and pg.evaluate("()=>__yt.cam().state") == 1 and ws("currentCamId"))

    # Home: back to Start, local playback stopped.
    pg.click("#lp-home")
    pg.wait_for_timeout(500)
    check("player: Home returns to the Start screen", pg.is_visible("#start") and pg.is_visible("#start-btn") and ws("chromeHidden") is False)
    check("player: Home stops local playback", pg.evaluate("()=>!__windowSeat.state.started && !__windowSeat.currentCamId()") and pg.evaluate("()=>__yt.players.filter(p=>p.kind==='cam' && !p.destroyed).every(p=>p.state!==1) && __yt.players.filter(p=>p.kind==='music' && !p.destroyed).every(p=>p.state!==1)"))
    check("player: Home hides the player chrome and restores the canvas theme", not pg.is_visible("#bar") and not pg.is_visible("#chrome-toggle") and pg.get_attribute('meta[name="theme-color"]', "content").upper() == "#F3F5F7")
    pg.click("#start-btn")
    check("player: Start again plays locally", wait_until(pg, "()=>__windowSeat.currentCamId() && __yt.cam().state===1", 6000))
    pg.context.close()

    # Home pressed during the Start leave animation (well under 650 ms): the delayed hide must not fire later.
    pg = phone()
    pg.evaluate("""async()=>{document.querySelector('#start-btn').click();
      await new Promise(r=>setTimeout(r,80));
      window.__startedBeforeHome = __windowSeat.state.started;
      document.querySelector('#lp-home').click()}""")
    check("early home: Start had begun before Home", pg.evaluate("()=>window.__startedBeforeHome") is True)
    pg.wait_for_timeout(1000)  # well past the 650 ms hide timeout
    check("early home: #start still visible after the timeout", pg.evaluate("()=>!document.querySelector('#start').hidden && !document.querySelector('#start').classList.contains('leaving')") and pg.is_visible("#start") and pg.is_visible("#start-btn"))
    check("early home: not left on an empty player", pg.evaluate("()=>!__windowSeat.state.started") and not pg.is_visible("#bar") and not pg.is_visible("#chrome-toggle"))
    pg.click("#start-btn")
    check("early home: Start watching works again", wait_until(pg, "()=>__windowSeat.currentCamId() && __yt.cam().state===1", 6000))
    check("early home: Start screen dismisses normally afterwards", wait_until(pg, "()=>document.querySelector('#start').hidden", 2000))
    pg.click("#lp-home")
    pg.wait_for_timeout(1000)
    check("early home: normal Home after a full Start still shows Start", pg.is_visible("#start") and pg.is_visible("#start-btn"))
    pg.context.close()

    # ------------------------------------------------------------ Debug on the local player
    pg = phone()
    check("debug: off on a fresh load", pg.evaluate("()=>__windowSeat.isDebugOn()") is False and not pg.is_visible("#debug-panel"))
    pg.click("#start-about")
    pg.wait_for_timeout(200)
    check("debug: About shows This copy with build .8", pg.text_content("#about-build") == f"Build {BUILD}" and pg.is_visible(".this-copy"))
    pg.check("#debug-toggle")
    pg.click("#panel-about [data-action=close-panel]")
    pg.wait_for_timeout(200)
    check("debug: pill reachable on Home", pg.is_visible("#debug-pill") and BUILD in pg.text_content("#debug-pill"))
    pg.click("#start-btn")
    wait_until(pg, "()=>__windowSeat.currentCamId() && __yt.cam().state===1", 6000)
    pg.wait_for_timeout(300)
    pill = box(pg, "#debug-pill")
    check(f"debug: pill visible after Start, in the top scrim ({pill})", pg.is_visible("#debug-pill") and pill["y"] < 80)
    check("debug: pill sits between Home and Cast", not overlaps(pill, box(pg, "#lp-home")) and not overlaps(pill, box(pg, "#player-top [data-cast]")) and not overlaps(pill, box(pg, "#lp-fullscreen")))
    pg.click("#debug-pill")
    pg.wait_for_timeout(200)
    body = box(pg, "#debug-body")
    check("debug: panel expands on the local player", pg.is_visible("#debug-body") and BUILD in pg.text_content("[data-section=app]"))
    check(f"debug: panel never covers the bottom glass or the floating button ({body}, glass {box(pg, '#bar')})", not overlaps(body, box(pg, "#bar")) and not overlaps(body, box(pg, "#chrome-toggle")))
    pg.click("#debug-pill")
    # Leave the pill alone (no hover, no focus) so the resting opacity shows.
    pg.mouse.move(200, 400)
    pg.evaluate("()=>document.activeElement && document.activeElement.blur()")
    pg.wait_for_timeout(6600)
    check("debug: chrome may auto-hide with Debug on", ws("chromeHidden") is True)
    op = pg.evaluate("()=>getComputedStyle(document.querySelector('#debug-panel')).opacity")
    check(f"debug: pill stays visible at 70% when the chrome hides ({op})", pg.is_visible("#debug-pill") and op == "0.7")
    pg.click("#debug-pill")
    pg.wait_for_timeout(200)
    check("debug: panel still reachable with the chrome hidden", pg.is_visible("#debug-body") and not overlaps(box(pg, "#debug-body"), box(pg, "#chrome-toggle")))
    pg.click("#chrome-toggle")
    pg.wait_for_timeout(300)
    check("debug: chrome can be shown again with the panel open", ws("chromeHidden") is False and pg.is_visible("#bar") and not overlaps(box(pg, "#debug-body"), box(pg, "#bar")))
    pg.reload()
    pg.wait_for_timeout(1200)
    check("debug: reload turns Debug off", pg.evaluate("()=>__windowSeat.isDebugOn()") is False and not pg.is_visible("#debug-panel"))
    pg.context.close()

    # ------------------------------------------------------------ Casting: bright remote, no live cam on the phone
    pg = phone()
    pg.click("#start-btn")
    wait_until(pg, "()=>__windowSeat.currentCamId() && __yt.cam().state===1", 6000)
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(500)
    check("cast: remote shown, no local iframes, no player chrome", pg.is_visible("#remote") and pg.evaluate("()=>document.querySelectorAll('iframe[src*=youtube]').length") == 0 and not pg.is_visible("#bar") and not pg.is_visible("#chrome-toggle"))
    check("cast: remote uses the bright canvas", pg.evaluate("()=>getComputedStyle(document.querySelector('#remote')).backgroundColor") == "rgb(243, 245, 247)")
    check("cast: 'Casting to {room}' pill with accent-soft fill", pg.text_content(".remote-pill").startswith("Casting to") and pg.evaluate("()=>getComputedStyle(document.querySelector('.remote-pill')).backgroundColor") == "rgb(228, 240, 245)")
    check("cast: Cast controls switch to the connected label", pg.evaluate("()=>[...document.querySelectorAll('[data-cast]')].every(b=>b.dataset.state==='connected')") and pg.text_content(".cast-start .cast-label").startswith("Casting to"))
    check("cast: #remote-debug and transport still there", pg.locator("#remote-debug").count() == 1 and pg.locator('#remote [data-cmd="play"]').count() == 1)
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.locator('#remote [data-cmd="next"]').tap()
    pg.wait_for_timeout(150)
    check("cast: remote next goes to the TV", [json.loads(m["data"])["type"] for m in pg.evaluate("()=>__mockCast.sent")] == ["next"])
    check("cast: theme-color back to the canvas", pg.get_attribute('meta[name="theme-color"]', "content").upper() == "#F3F5F7")
    pg.context.close()

    # ------------------------------------------------------------ TV lower third and quiet TV
    rx = b.new_context(viewport={"width": 1920, "height": 1080}).new_page()
    route(rx)
    rx.goto(base + "/receiver.html?debug=1")
    check("tv: a cam is playing", wait_until(rx, "()=>__receiver.state.camId && __yt.cam() && __yt.cam().state===1", 6000))
    rx.wait_for_timeout(400)
    cap_opacity = lambda: float(rx.evaluate("()=>getComputedStyle(document.querySelector('#caption')).opacity"))
    tcam = rx.evaluate("async()=>{const {CAMS}=await import('/js/data.js'); return CAMS.find(c=>c.id===__receiver.state.camId)}")
    meta = rx.text_content("#cap-meta")
    check("tv: lower third visible after the cam loads", cap_opacity() == 1 and rx.is_visible("#caption"))
    check(f"tv: title is the cam name ({rx.text_content('#cap-title')})", rx.text_content("#cap-title") == tcam["title"])
    check(f"tv: meta is city and country, then the built-in local time ({meta})", re.fullmatch(re.escape(f"{tcam['city']}, {tcam['country']}") + r" · \d{1,2}:\d\d (AM|PM)", meta) is not None)
    check("tv: no type tag and no channel in the lower third", tcam["channel"] not in rx.text_content("#caption") and (not tcam["type"] or tcam["type"] not in meta.lower()))
    cb = rx.locator("#caption").bounding_box()
    check(f"tv: lower third bottom left of the 5% safe area, at most ~46% wide ({cb})", abs(cb["x"] - 96) <= 1 and abs(cb["y"] + cb["height"] - (1080 - 54)) <= 1 and cb["width"] <= 0.46 * 1920 + 1)
    check("tv: debug strip on with ?debug=1", rx.evaluate("()=>!document.querySelector('#debug-strip').hidden"))
    rx.wait_for_timeout(9000)
    check("tv: still showing just before 10s", cap_opacity() > 0.9)
    rx.wait_for_timeout(2200)
    check("tv: quiet TV after about 10s (lower third, brand and mix line faded out)", cap_opacity() == 0 and rx.evaluate("()=>document.querySelector('#caption').classList.contains('quiet')"))
    check("tv: debug strip unchanged by the quiet TV", rx.evaluate("()=>!document.querySelector('#debug-strip').hidden") and f"build {BUILD}" in rx.text_content("#debug-strip"))

    # A cam with a timezone: city, country, mid-dot, local time. Shown again for the new cam.
    cfg = {"type": "config", "cams": SAVED["cams"], "mixes": SAVED["mixes"]}
    rx.evaluate("(c)=>__receiver.send(c)", cfg)
    rx.evaluate("()=>__receiver.send({type:'cam', id:'hlbh4P0Mz8M'})")
    check("tv: timezone cam is showing", wait_until(rx, "()=>__receiver.state.camId==='hlbh4P0Mz8M' && __yt.cam().state===1", 6000))
    rx.wait_for_timeout(400)
    meta = rx.text_content("#cap-meta")
    check(f"tv: meta with local time 'Tokyo, Japan · 9:51 AM' shape ({meta})", re.fullmatch(r"Tokyo, Japan · \d{1,2}:\d\d (AM|PM)", meta) is not None)
    check("tv: lower third shows again after a new cam loads", cap_opacity() == 1)
    rx.evaluate("()=>__receiver.send({type:'cam', id:'Yzl9offu5g8'})")
    wait_until(rx, "()=>__receiver.state.camId==='Yzl9offu5g8' && __yt.cam().state===1", 6000)
    rx.wait_for_timeout(400)
    check(f"tv: missing timezone omits the time ({rx.text_content('#cap-meta')})", rx.text_content("#cap-meta") == "Naha, Japan")

    # Paused pill and pin toast still work on the quiet TV.
    rx.evaluate("()=>__receiver.send({type:'pause', value:true})")
    rx.wait_for_timeout(200)
    check("tv: Paused pill visible, top right", rx.is_visible("#paused-pill") and rx.locator("#paused-pill").bounding_box()["x"] > 1920 / 2)
    rx.evaluate("()=>__receiver.send({type:'pause', value:false})")
    rx.evaluate("()=>__receiver.send({type:'pin', value:true})")
    rx.wait_for_timeout(200)
    check("tv: pin toast 'Pinned. Staying on this cam.'", rx.text_content("#cmd-toast") == "Pinned. Staying on this cam." and rx.evaluate("()=>document.querySelector('#cmd-toast').classList.contains('show')"))
    rx.context.close()

    # Strip rules unchanged: no strip without Debug mode (real receiver page, CAF blocked).
    rx = b.new_context(viewport={"width": 1920, "height": 1080}).new_page()
    route(rx)
    rx.goto(base + "/receiver.html")
    rx.wait_for_timeout(1500)
    check("tv: no debug strip unless Debug mode is on", rx.evaluate("()=>document.querySelector('#debug-strip').hidden"))
    rx.context.close()
    b.close()

print("page errors:", logs)
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails or logs else 0)
