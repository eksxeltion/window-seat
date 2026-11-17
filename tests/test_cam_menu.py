"""Cams sheet (cam menu): time of day (Skip sleeping cams, Only daytime), quick picks, rows with local time and a
Sleeping mark, Play now, Pin over the night rule, persistence of checks only, and the same pool on the TV.
Runs against the stubbed YouTube IFrame API (tests/fake_youtube.js) and the mock Cast sender, with a fixed clock.
Usage: test_cam_menu.py BASE_URL   (the site root, for example http://localhost:8000/)"""
import sys, os, re, json
from playwright.sync_api import sync_playwright

base = sys.argv[1].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
here = os.path.dirname(os.path.abspath(__file__))
FAKE_YT = open(os.path.join(here, "fake_youtube.js")).read()
MOCK = open(os.path.join(here, "mock_cast_sender.js")).read()

# Monday 16 November 2026, 4:51 PM Pacific: 9:51 AM in Tokyo, 11:51 AM in Sydney, 8:51 AM in Hong Kong,
# 1:51 AM in Paris and Rome (sleeping), 12:51 AM in London (sleeping).
NIGHT_EU = "2026-11-17T00:51:00Z"
# 12:30 UTC: 9:30 PM in Tokyo and 11:30 PM in Sydney (awake, not daytime), 1:30 PM in Paris and 12:30 PM in London.
NOON_EU = "2026-11-17T12:30:00Z"
SHINJUKU = "u7kCTaCX_J4"  # Tokyo, street
SHIODOME = "VM18f-IIUTw"  # Tokyo, skyline
EIFFEL = "QI-PPEW2Bgc"  # Paris, skyline
SAVED = {
    "at": 1,
    "cams": [
        {"id": SHINJUKU, "title": "Shinjuku Station", "country": "Japan", "city": "Tokyo", "place": "Tokyo", "type": "street", "channel": "C", "default": True, "timezone": "Asia/Tokyo"},
        {"id": EIFFEL, "title": "Eiffel Tower", "country": "France", "city": "Paris", "place": "Paris", "type": "skyline", "channel": "C", "default": True, "timezone": "Europe/Paris"},
        {"id": "Yzl9offu5g8", "title": "Naha Airport", "country": "Japan", "city": "Naha", "place": "Naha", "type": "airport", "channel": "C", "default": True, "timezone": ""},
        {"id": "z3OUPSoABJk", "title": "Sapporo City", "country": "Japan", "city": "Sapporo", "place": "Sapporo", "type": "skyline", "channel": "C", "default": True, "timezone": "Mars/Olympus"},
    ],
    "mixes": [{"id": "nCtOCLv6eFY", "title": "M1", "en": "Mix one", "minutes": 60}],
}
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


def route(pg):
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.route("**/cast_receiver_framework.js", lambda r: r.abort())
    pg.route("https://i.ytimg.com/**", lambda r: r.abort())
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())  # no sheet: the built-in list (or a saved copy) is used
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))


def ws(pg, expr):
    return pg.evaluate(f"()=>__windowSeat.{expr}")


def start(pg):
    pg.click("#start-btn")
    return wait_until(pg, "()=>__windowSeat.currentCamId() && !__windowSeat.state.pending && __yt.cam().state===1", 8000)


def open_cams(pg):
    pg.click("#bar [data-action=open-cams]")
    pg.wait_for_selector("#panel-cams:not([hidden])")


def row(pg, cam_id):
    return f'#cams-list .cam-row[data-id="{cam_id}"]'


def checked(pg, cam_id):
    return pg.get_attribute(f'{row(pg, cam_id)} [data-action="toggle-cam"]', "aria-checked") == "true"


def pressed(pg, sel):
    return pg.get_attribute(sel, "aria-pressed") == "true"


def all_rows_visible(pg):
    return pg.evaluate("()=>[...document.querySelectorAll('#cams-list .cam-row')].every(r=>r.offsetParent!==null && !r.hidden)")


with sync_playwright() as p:
    b = p.chromium.launch()

    def phone(clock, init=None):
        ctx = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
        if init:
            ctx.add_init_script(init)
        pg = ctx.new_page()
        pg.clock.set_fixed_time(clock)
        route(pg)
        pg.goto(base + "/index.html")
        pg.wait_for_timeout(800)
        return pg

    # ------------------------------------------------------------ built-in list carries timezones
    pg = phone(NIGHT_EU)
    zones = pg.evaluate("""async()=>{const {CAMS}=await import('/js/data.js'); const lt=await import('/js/localtime.js');
      return {n: CAMS.length, typed: CAMS.every(c=>typeof c.timezone==='string' && c.timezone.length>0), valid: CAMS.filter(c=>lt.isValidZone(c.timezone)).length,
              shinjuku: CAMS.find(c=>c.id==='u7kCTaCX_J4').timezone, eiffel: CAMS.find(c=>c.id==='QI-PPEW2Bgc').timezone}}""")
    check(f"data.js: every fallback cam has an IANA timezone string ({zones})", zones["typed"] and zones["valid"] == zones["n"] and zones["shinjuku"] == "Asia/Tokyo" and zones["eiffel"] == "Europe/Paris")
    hero = pg.text_content("#hero-time")
    check(f"home: hero shows local time from the built-in list without a sheet ({hero})", re.fullmatch(r"It's \d{1,2}:\d\d (AM|PM) in .+\.", hero) is not None)
    check("home: list is the built-in one", "built-in" in pg.text_content("#panel-cams .list-status"))

    # ------------------------------------------------------------ fresh load defaults
    check("player: started", start(pg))
    open_cams(pg)
    check("sheet: header reads Cams with a Done control", pg.text_content("#cams-title") == "Cams" and pg.text_content("#panel-cams [data-action=close-panel]").strip() == "Done")
    check("fresh load: Skip sleeping cams is on", pressed(pg, "#cams-skip-sleeping"))
    check("fresh load: Only daytime is off", not pressed(pg, "#cams-only-daytime"))
    exp = pg.evaluate("""async()=>{const {CAMS}=await import('/js/data.js'); const lt=await import('/js/localtime.js'); const d=new Date();
      const on=CAMS.filter(c=>c.default); return {awake: on.filter(c=>!lt.isSleeping(c,d)).length, asleep: on.filter(c=>lt.isSleeping(c,d)).length}}""")
    count = pg.text_content("#cams-count")
    check(f"sheet: count '{exp['awake']} in rotation now. {exp['asleep']} checked cams are sleeping.' ({count})", count == f"{exp['awake']} in rotation now. {exp['asleep']} checked cams are sleeping." and exp["asleep"] > 0)
    check("sheet: Interval and Lite stay in the sheet", pg.locator("#panel-cams [data-action=interval]").count() == 1 and pg.locator("#panel-cams [data-action=lite]").count() == 1)

    # ------------------------------------------------------------ rows
    check("rows: the whole list in list order (44)", pg.locator("#cams-list .cam-row").count() == 44 and all_rows_visible(pg))
    check("rows: name", "Shinjuku Station" in pg.text_content(f"{row(pg, SHINJUKU)} .cam-title"))
    check(f"rows: country and type ({pg.text_content(row(pg, SHINJUKU) + ' .cam-where')})", pg.text_content(f"{row(pg, SHINJUKU)} .cam-where") == "Japan · Street")
    check("rows: local time 9:51 AM in Tokyo", pg.text_content(f"{row(pg, SHINJUKU)} .cam-clock") == "9:51 AM")
    check("rows: an awake cam has no Sleeping mark", not pg.is_visible(f"{row(pg, SHINJUKU)} .sleep-mark"))
    check("rows: Paris shows 1:51 AM and Sleeping", pg.text_content(f"{row(pg, EIFFEL)} .cam-clock") == "1:51 AM" and pg.is_visible(f"{row(pg, EIFFEL)} .sleep-mark") and pg.text_content(f"{row(pg, EIFFEL)} .sleep-mark") == "Sleeping")
    check("rows: a Play now button on every row", pg.locator("#cams-list .cam-row [data-action=watch]").count() == 44 and pg.text_content(f"{row(pg, SHINJUKU)} [data-action=watch]") == "Play now")
    check("rows: the sleeping cam stays checked", checked(pg, EIFFEL))

    pool = ws(pg, "rotationIds()")
    check("pool: Skip on keeps the sleeping Paris cam out of rotation", EIFFEL not in pool and SHINJUKU in pool)
    pg.click("#cams-skip-sleeping")
    check("pool: Skip off puts the sleeping cam back", not pressed(pg, "#cams-skip-sleeping") and EIFFEL in ws(pg, "rotationIds()"))
    check("sheet: row still visible and still labeled Sleeping with Skip off", pg.is_visible(f"{row(pg, EIFFEL)} .sleep-mark"))
    pg.click("#cams-skip-sleeping")
    check("pool: Skip on again takes it out", pressed(pg, "#cams-skip-sleeping") and EIFFEL not in ws(pg, "rotationIds()"))

    # ------------------------------------------------------------ Only daytime (7 AM to 7 PM local)
    pg.clock.set_fixed_time(NOON_EU)
    pg.click("#cams-only-daytime")
    pool = ws(pg, "rotationIds()")
    check("daytime: on", pressed(pg, "#cams-only-daytime"))
    check("daytime: Tokyo at 9:30 PM is out, Paris at 1:30 PM is in", SHINJUKU not in pool and SHIODOME not in pool and EIFFEL in pool)
    check("daytime: rows are not hidden", pg.locator("#cams-list .cam-row").count() == 44 and all_rows_visible(pg))
    pg.click("#cams-only-daytime")
    check("daytime: off puts the evening Tokyo cams back", SHINJUKU in ws(pg, "rotationIds()"))
    pg.clock.set_fixed_time(NIGHT_EU)

    # ------------------------------------------------------------ quick picks
    jp_chip = '#cams-quick [data-group="country:Japan"]'
    au_chip = '#cams-quick [data-group="country:Australia"]'
    check("quick picks: All, None and country chips", pg.locator('#cams-quick [data-group="all"]').count() == 1 and pg.locator('#cams-quick [data-group="none"]').count() == 1 and pg.locator(jp_chip).count() == 1)
    pg.click('#cams-quick [data-group="none"]')
    check("None: every checkbox off, every row still visible", pg.evaluate("()=>[...document.querySelectorAll('#cams-list [data-action=toggle-cam]')].every(b=>b.getAttribute('aria-checked')==='false')") and all_rows_visible(pg))
    check("None: chip filled, All and Japan not", pressed(pg, '#cams-quick [data-group="none"]') and not pressed(pg, '#cams-quick [data-group="all"]') and not pressed(pg, jp_chip))
    all_ids = pg.evaluate("async()=>(await import('/js/data.js')).CAMS.map(c=>c.id)")
    check(f"None: sheet says rotation uses every cam ({pg.text_content('#cams-count')})", pg.text_content("#cams-count") == "No cams checked. Rotation uses every cam.")
    pool = ws(pg, "rotationIds()")
    check(f"None: phone pool is every cam, not empty and not only defaults ({len(pool)} of {len(all_ids)})", sorted(pool) == sorted(all_ids) and len(all_ids) == 44)

    # ------------------------------------------------------------ fallback pool count: every checked cam is out by the rules
    pg.click(f"{row(pg, EIFFEL)} [data-action=toggle-cam]")
    pool = ws(pg, "rotationIds()")
    count = pg.text_content("#cams-count")
    check(f"fallback: only the sleeping Paris cam checked, Skip on, it still rotates ({pool})", pool == [EIFFEL] and pressed(pg, "#cams-skip-sleeping"))
    check(f"fallback: count matches the fallback pool, not 0 ({count})", count == "1 in rotation now. 1 checked cam is sleeping.")
    pg.click(f"{row(pg, EIFFEL)} [data-action=toggle-cam]")
    pg.clock.set_fixed_time(NOON_EU)
    pg.click(f"{row(pg, SHINJUKU)} [data-action=toggle-cam]")
    pg.click(f"{row(pg, SHIODOME)} [data-action=toggle-cam]")
    pg.click("#cams-only-daytime")
    pool = ws(pg, "rotationIds()")
    count = pg.text_content("#cams-count")
    check(f"fallback: Only daytime with only evening Tokyo cams checked keeps both rotating ({pool})", sorted(pool) == sorted([SHINJUKU, SHIODOME]))
    check(f"fallback: daytime count matches the fallback pool, not 0 ({count})", count == "2 in rotation now.")
    pg.click("#cams-only-daytime")
    pg.click('#cams-quick [data-group="none"]')
    pg.clock.set_fixed_time(NIGHT_EU)
    pg.click(jp_chip)
    jp = pg.evaluate("""()=>{const r=[...document.querySelectorAll('#cams-list .cam-row')]; return r.map(x=>({c: x.querySelector('.cam-where').textContent.startsWith('Japan ·'), on: x.querySelector('[data-action=toggle-cam]').getAttribute('aria-checked')==='true'}))}""")
    check("Japan chip: checks exactly the Japan cams", all(x["on"] == x["c"] for x in jp) and any(x["c"] for x in jp))
    check("Japan chip: filled once every Japan cam is checked", pressed(pg, jp_chip) and not pressed(pg, au_chip))
    pg.click(f"{row(pg, SHINJUKU)} [data-action=toggle-cam]")
    check("Japan chip: not filled after one Japan cam is unchecked", not pressed(pg, jp_chip))
    pg.click('#cams-quick [data-group="all"]')
    check("All: every checkbox on, All, Japan and Australia chips filled", pg.evaluate("()=>[...document.querySelectorAll('#cams-list [data-action=toggle-cam]')].every(b=>b.getAttribute('aria-checked')==='true')") and pressed(pg, '#cams-quick [data-group="all"]') and pressed(pg, jp_chip) and pressed(pg, au_chip))
    pg.click(jp_chip)
    check("filled Japan chip unchecks Japan only, rows stay", not checked(pg, SHINJUKU) and checked(pg, EIFFEL) and all_rows_visible(pg) and not pressed(pg, jp_chip))
    pg.click(jp_chip)
    check("Japan chip again checks Japan", checked(pg, SHINJUKU) and pressed(pg, jp_chip))

    # ------------------------------------------------------------ persistence: checks survive, toggles do not
    pg.click(f"{row(pg, SHINJUKU)} [data-action=toggle-cam]")
    check("uncheck Shinjuku from the row", not checked(pg, SHINJUKU) and SHINJUKU not in ws(pg, "rotationIds()"))
    pg.click("#cams-skip-sleeping")
    pg.click("#cams-only-daytime")
    check("toggles flipped before reload", not pressed(pg, "#cams-skip-sleeping") and pressed(pg, "#cams-only-daytime"))
    keys = pg.evaluate("()=>Object.keys(localStorage)")
    check(f"no time-of-day key in localStorage ({keys})", not any(re.search(r"sleep|daytime", k, re.I) for k in keys))
    pg.reload()
    pg.wait_for_timeout(800)
    check("reload: started again", start(pg))
    open_cams(pg)
    check("reload: Skip sleeping is on again", pressed(pg, "#cams-skip-sleeping"))
    check("reload: Only daytime is off again", not pressed(pg, "#cams-only-daytime"))
    check("reload: unchecked Shinjuku stays unchecked, All picks stay checked", not checked(pg, SHINJUKU) and checked(pg, EIFFEL) and checked(pg, SHIODOME))

    # ------------------------------------------------------------ Play now
    before_eiffel = checked(pg, EIFFEL)
    pg.click(f"{row(pg, EIFFEL)} [data-action=watch]")
    check("Play now: plays the sleeping Paris cam", wait_until(pg, f"()=>__windowSeat.currentCamId()==='{EIFFEL}' && !__windowSeat.state.pending && __yt.cam().state===1", 6000))
    check("Play now: checkbox unchanged", ws(pg, f"isEnabled('{EIFFEL}')") == before_eiffel)
    open_cams(pg)
    pg.click(f"{row(pg, SHINJUKU)} [data-action=watch]")
    check("Play now: plays an unchecked cam", wait_until(pg, f"()=>__windowSeat.currentCamId()==='{SHINJUKU}' && !__windowSeat.state.pending && __yt.cam().state===1", 6000))
    check("Play now: unchecked cam stays unchecked", ws(pg, f"isEnabled('{SHINJUKU}')") is False)

    # ------------------------------------------------------------ Pin wins over the night rule
    open_cams(pg)
    pg.click(f"{row(pg, EIFFEL)} [data-action=watch]")
    wait_until(pg, f"()=>__windowSeat.currentCamId()==='{EIFFEL}' && !__windowSeat.state.pending", 6000)
    pg.click("#pin-pill")
    pg.evaluate("()=>{__windowSeat.state.nextSwapAt=0}")
    pg.wait_for_timeout(2200)
    check("pin: the sleeping cam stays on screen with Skip on", ws(pg, "currentCamId()") == EIFFEL and ws(pg, "pinned()") is True)
    pg.click("#pin-pill")
    pg.evaluate("()=>{__windowSeat.state.nextSwapAt=0}")
    check("unpinned: rotation leaves the sleeping cam", wait_until(pg, f"()=>__windowSeat.currentCamId()!=='{EIFFEL}' && !__windowSeat.state.pending", 5000))
    check("unpinned: the next cam is awake and in the pool", ws(pg, "currentCamId()") in ws(pg, "rotationIds()"))

    # ------------------------------------------------------------ casting: the TV gets the same pool
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(400)
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    cams = [m for m in msgs if m.get("type") == "cams"]
    check(f"cast: connect sends the checked pool and Skip on ({cams[:1]})", cams and cams[0].get("skipSleeping") is True and cams[0].get("onlyDaytime") is False and SHINJUKU not in cams[0]["ids"] and EIFFEL in cams[0]["ids"])
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click("#remote [data-action=open-cams]")
    pg.wait_for_selector("#panel-cams:not([hidden])")
    check("cast: the Cams sheet opens over the remote", pg.is_visible("#cams-skip-sleeping"))
    pg.click("#cams-skip-sleeping")
    pg.click(f"{row(pg, SHINJUKU)} [data-action=toggle-cam]")
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    cams = [m for m in msgs if m.get("type") == "cams"]
    check(f"cast: Skip off and a check go to the TV ({[ (c['skipSleeping'], len(c['ids'])) for c in cams]})", len(cams) == 2 and cams[0]["skipSleeping"] is False and cams[1]["skipSleeping"] is False and SHINJUKU in cams[1]["ids"])
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click(f"{row(pg, EIFFEL)} [data-action=watch]")
    pg.wait_for_timeout(200)
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"cast: Play now plays on the TV ({msgs})", any(m.get("type") == "cam" and m.get("id") == EIFFEL for m in msgs))
    check("cast: Play now leaves the checkbox alone", ws(pg, f"isEnabled('{EIFFEL}')") is True)
    pg.evaluate("()=>__mockCast.sent.length=0")
    if not pg.is_visible("#panel-cams"):
        pg.click("#remote [data-action=open-cams]")
        pg.wait_for_selector("#panel-cams:not([hidden])")
    pg.click('#cams-quick [data-group="none"]')
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    cams = [m for m in msgs if m.get("type") == "cams"]
    check(f"cast: None sends every cam id, not an empty list ({[len(c['ids']) for c in cams]})", len(cams) == 1 and sorted(cams[0]["ids"]) == sorted(all_ids))
    pg.context.close()

    # ------------------------------------------------------------ missing or invalid timezone errs toward showing the cam
    pg = phone(NIGHT_EU, init=f"localStorage.setItem('windowSeat.sheetConfig', {json.dumps(json.dumps(SAVED))})")
    check("saved list: started", start(pg))
    open_cams(pg)
    for cid, label in (("Yzl9offu5g8", "blank"), ("z3OUPSoABJk", "invalid")):
        check(f"{label} timezone: no local time and no Sleeping mark", pg.text_content(f"{row(pg, cid)} .cam-clock") == "" and not pg.is_visible(f"{row(pg, cid)} .sleep-mark"))
    pg.click("#cams-only-daytime")
    pool = ws(pg, "rotationIds()")
    check(f"blank and invalid timezones stay in rotation with both rules on ({pool})", "Yzl9offu5g8" in pool and "z3OUPSoABJk" in pool and EIFFEL not in pool)
    check("count: 3 in rotation now. 1 checked cam is sleeping.", pg.text_content("#cams-count") == "3 in rotation now. 1 checked cam is sleeping.")
    pg.context.close()

    # ------------------------------------------------------------ TV honors the pool and the rules
    rx = b.new_context(viewport={"width": 1920, "height": 1080}).new_page()
    rx.clock.set_fixed_time(NIGHT_EU)
    route(rx)
    rx.goto(base + "/receiver.html?debug=1")
    check("tv: a cam is playing", wait_until(rx, "()=>__receiver.state.camId && __yt.cam() && __yt.cam().state===1", 8000))
    rx.evaluate(f"()=>__receiver.send({{type:'cams', ids:['{SHINJUKU}','{SHIODOME}','{EIFFEL}'], skipSleeping:true, onlyDaytime:false}})")
    st = rx.evaluate("()=>__receiver.state")
    check("tv: state reports the rules", st["skipSleeping"] is True and st["onlyDaytime"] is False)
    seen = set()
    for _ in range(4):
        cur = rx.evaluate("()=>__receiver.state.camId")
        rx.evaluate("()=>__receiver.send({type:'next'})")
        wait_until(rx, f"()=>__receiver.state.camId!=='{cur}' && __yt.cam().state===1", 4000)
        seen.add(rx.evaluate("()=>__receiver.state.camId"))
    check(f"tv: next stays on awake checked cams, never the sleeping one ({seen})", seen <= {SHINJUKU, SHIODOME} and seen)
    rx.evaluate(f"()=>__receiver.send({{type:'cams', ids:['{SHINJUKU}','{SHIODOME}','{EIFFEL}'], skipSleeping:false, onlyDaytime:false}})")
    seen = set()
    for _ in range(4):
        cur = rx.evaluate("()=>__receiver.state.camId")
        rx.evaluate("()=>__receiver.send({type:'next'})")
        wait_until(rx, f"()=>__receiver.state.camId!=='{cur}' && __yt.cam().state===1", 4000)
        seen.add(rx.evaluate("()=>__receiver.state.camId"))
    check(f"tv: Skip off lets the sleeping cam rotate in ({seen})", EIFFEL in seen)
    rx.evaluate(f"()=>__receiver.send({{type:'cams', ids:['{SHINJUKU}','{SHIODOME}','{EIFFEL}'], skipSleeping:true}})")
    rx.evaluate(f"()=>__receiver.send({{type:'cam', id:'{EIFFEL}'}})")
    check("tv: a picked sleeping cam plays (Play now)", wait_until(rx, f"()=>__receiver.state.camId==='{EIFFEL}' && __yt.cam().state===1", 4000))
    rx.evaluate("()=>__receiver.send({type:'next'})")
    check("tv: next from the sleeping cam goes to an awake one", wait_until(rx, f"()=>['{SHINJUKU}','{SHIODOME}'].includes(__receiver.state.camId) && __yt.cam().state===1", 4000))
    rx.context.close()
    b.close()

print("FAILED:", fails) if fails else print("ALL PASS")
if logs:
    print(logs)
sys.exit(1 if fails or logs else 0)
