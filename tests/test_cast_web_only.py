"""Web-only cams (the sheet's `cast` column, build 2026-11-19.33). Server-stitched ad (SSDAI) live cams never play in
the IFrame player on Cast, so the sheet marks them `web-only` and the TV never tries them:
  (1) parsing: `cast` = web-only / Web Only / webonly / web_only / NO means castable:false; blank, `yes`, unknown and a
      sheet without the column mean castable:true; sanitize (Cast channel and localStorage copies) keeps the flag;
      the built-in list is all castable.
  (2) receiver: the start cam, in-order next/prev, the automatic rotation and the time-of-day pool never pick a web-only
      cam; a phone request for one is ignored quietly (castSkip note, no load); a list where every cam is web-only rests
      on the closed message with no tight loop, and a new castable list starts again.
  (3) phone remote: while casting, web-only rows show "Phone only", the play circle is aria-disabled and sends nothing
      to the TV; the local phone player still plays them.
Runs against the stubbed YouTube API (tests/fake_youtube.js) and the mock Cast sender. telemetry_guard aborts the real
telemetry hosts. Usage: test_cast_web_only.py BASE_URL"""
import re
import json
import os
import sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import telemetry_guard  # noqa: E402,F401  aborts PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].split("?")[0].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
FAKE_YT = open(os.path.join(HERE, "fake_youtube.js")).read()
MOCK = open(os.path.join(HERE, "mock_cast_sender.js")).read()
STORE = "windowSeat.sheetConfig"
fails, logs = [], []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def wait_until(pg, js, ms=5000, arg=None):
    try:
        pg.wait_for_function(js, arg=arg, timeout=ms)
        return True
    except Exception:
        return False


def route(pg):
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.route("**/cast_receiver_framework.js", lambda r: r.abort())
    pg.route("https://i.ytimg.com/**", lambda r: r.abort())
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())  # no sheet: the saved copy below is the list
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))


def saved_config(pg_any, castable_ids):
    """The built-in list as a saved sheet copy, with only castable_ids castable."""
    cams = pg_any.evaluate("async()=>(await import('./js/data.js')).CAMS.map(c=>({...c}))")
    for c in cams:
        c["castable"] = c["id"] in castable_ids
    return {"at": 1, "cams": cams, "mixes": [{"id": "nCtOCLv6eFY", "title": "M1", "en": "Mix one", "minutes": 60}]}


with sync_playwright() as p:
    b = p.chromium.launch()

    # ------------------------------------------------------------ (1) parsing
    pg = b.new_page()
    route(pg)
    pg.goto(base + "/receiver.html?debug=1")
    u = pg.evaluate(r'''async () => {
      const c = await import('./js/config.js');
      const head = 'youtube_id,name,in_rotation,timezone,cast\n';
      const rows = [['5B4LmH4JbnY','yes'],['u7kCTaCX_J4','web-only'],['VM18f-IIUTw','Web Only'],['hlbh4P0Mz8M',' WEBONLY '],
                    ['6OgYfTMOmhY','web_only'],['il6iGt3CuiE','NO'],['dOKOq-jU1b8',''],['JLlY6Cg17cc','maybe'],['J3xHBUgWRqc','Yes']];
      const cfg = c.buildConfig(head + rows.map(([id, v]) => `"${id}","N","TRUE","Asia/Tokyo","${v}"`).join('\n') + '\n', 'youtube_id,title\nnCtOCLv6eFY,M1\n');
      const old = c.buildConfig('youtube_id,name,in_rotation\n5B4LmH4JbnY,One,TRUE\nu7kCTaCX_J4,Two,TRUE\n', 'youtube_id,title\nnCtOCLv6eFY,M1\n');
      const short = c.buildConfig('youtube_id,name,in_rotation,cast\n5B4LmH4JbnY,One,TRUE\nu7kCTaCX_J4,Two,TRUE,web-only\n', 'youtube_id,title\nnCtOCLv6eFY,M1\n');
      const san = c.sanitize({cams:[{id:'5B4LmH4JbnY'},{id:'u7kCTaCX_J4',castable:false},{id:'VM18f-IIUTw',castable:true},{id:'hlbh4P0Mz8M',castable:'web-only'},{id:'6OgYfTMOmhY',castable:null}], mixes:[{id:'nCtOCLv6eFY'}]});
      const biList = (await import('./js/data.js')).CAMS;
      const bi = biList.length > 0 && biList.every(x => x.castable === true); // before applyConfig below replaces it in place
      // Cast channel copy: what the phone sends (currentConfig) and the TV applies (applyConfig) keeps the flag.
      const applied = c.applyConfig({cams:[{id:'5B4LmH4JbnY',default:true,castable:false},{id:'u7kCTaCX_J4',default:true}], mixes:[{id:'nCtOCLv6eFY'}]}, 'phone');
      const cur = c.currentConfig().cams.map(x => [x.id, x.castable]);
      return { cast: cfg.cams.map(x => x.castable), old: old.cams.map(x => x.castable), short: short.cams.map(x => x.castable),
               san: san.cams.map(x => x.castable), bi, applied, cur,
               parse: ['web-only','Web-Only','web only','webonly','web_only','no','No',' no ','', 'yes','maybe','true',undefined,null,false,true].map(c.parseCastable) };
    }''')
    print("parse:", u)
    check("(1) cast column: yes castable; web-only / Web Only / WEBONLY / web_only / NO not; blank and unknown castable",
          u["cast"] == [True, False, False, False, False, False, True, True, True])
    check("(1) a sheet without the cast column: every cam castable (backward compatible)", u["old"] == [True, True])
    check("(1) a row shorter than the header (cast cell missing): castable", u["short"] == [True, False])
    check("(1) parseCastable: only web-only spellings and no are false", u["parse"] == [False, False, False, False, False, False, False, False, True, True, True, True, True, True, False, True])
    check("(1) sanitize keeps castable (missing or null -> true, false and 'web-only' -> false)", u["san"] == [True, False, True, False, True])
    check("(1) built-in list: every cam castable:true", u["bi"])
    check("(1) Cast channel copy: applyConfig and currentConfig carry castable", u["applied"] and u["cur"] == [["5B4LmH4JbnY", False], ["u7kCTaCX_J4", True]])
    pg.close()

    # ------------------------------------------------------------ (2) receiver
    probe = b.new_page()
    route(probe)
    probe.goto(base + "/receiver.html?debug=1")
    all_ids = probe.evaluate("async()=>(await import('./js/data.js')).CAMS.filter(c=>c.default).map(c=>c.id)")
    ok_ids = all_ids[:3]
    cfg = saved_config(probe, ok_ids)
    web = [i for i in all_ids if i not in ok_ids]
    probe.close()

    rx = b.new_page(viewport={"width": 1280, "height": 720})
    route(rx)
    rx.add_init_script(f"localStorage.setItem({json.dumps(STORE)}, {json.dumps(json.dumps(cfg))})")
    rx.goto(base + "/receiver.html?debug=1")
    check("(2) receiver up with the saved list", wait_until(rx, "()=>window.__receiver && __receiver.state.camId && __yt.cam().state===1", 15000))
    check(f"(2) the random start cam is castable ({rx.evaluate('()=>__receiver.state.camId')})", rx.evaluate("()=>__receiver.state.camId") in ok_ids)
    LOADED = "()=>__yt.cam().calls.filter(c=>c[0]==='loadVideoById').map(c=>c[1])"
    seen = []
    for _ in range(4):
        rx.evaluate("()=>__receiver.send({type:'next'})")
        wait_until(rx, "()=>__receiver.state.camId && !document.querySelector('#blackout').classList.contains('on') && __yt.cam().state===1", 5000)
        rx.wait_for_timeout(700)
        seen.append(rx.evaluate("()=>__receiver.state.camId"))
    rx.evaluate("()=>__receiver.send({type:'prev'})")
    rx.wait_for_timeout(1200)
    seen.append(rx.evaluate("()=>__receiver.state.camId"))
    for _ in range(3):
        rx.evaluate("()=>__receiver.expireRotation()")
        rx.wait_for_timeout(1800)
        seen.append(rx.evaluate("()=>__receiver.state.camId"))
    loaded = rx.evaluate(LOADED)
    print("seen:", seen, "loaded:", loaded)
    check(f"(2) in-order next/prev and the automatic rotation only visit castable cams ({set(seen)})", set(seen) <= set(ok_ids) and len(set(seen)) >= 2)
    check("(2) no web-only cam was ever loaded", not any(i in web for i in loaded))
    # Time-of-day rules on and a phone pool of web-only cams plus one castable: only the castable one rotates.
    rx.evaluate("(ids)=>__receiver.send({type:'cams', ids, skipSleeping:true, onlyDaytime:true})", web[:5] + [ok_ids[1]])
    for _ in range(2):
        rx.evaluate("()=>__receiver.expireRotation()")
        rx.wait_for_timeout(1500)
    check("(2) pool of web-only plus one castable (time rules on): the castable cam stays, no web-only load",
          rx.evaluate("()=>__receiver.state.camId") == ok_ids[1] and not any(i in web for i in rx.evaluate(LOADED)))
    # A phone pool of only web-only cams: the castable defaults stand in.
    rx.evaluate("(ids)=>__receiver.send({type:'cams', ids, skipSleeping:false, onlyDaytime:false})", web[:4])
    rx.evaluate("()=>__receiver.expireRotation()")
    rx.wait_for_timeout(1800)
    check("(2) pool of only web-only cams: rotation falls back to castable cams", rx.evaluate("()=>__receiver.state.camId") in ok_ids and not any(i in web for i in rx.evaluate(LOADED)))
    # The phone asks for a web-only cam: ignored quietly.
    before = rx.evaluate("()=>__receiver.state.camId")
    n0 = rx.evaluate("()=>__yt.cam().calls.length")
    rx.evaluate("(id)=>__receiver.send({type:'cam', id})", web[0])
    rx.wait_for_timeout(1500)
    check("(2) a phone request for a web-only cam: no load, the cam on screen stays, no fade",
          rx.evaluate("(n)=>__yt.cam().calls.slice(n).filter(c=>c[0]==='loadVideoById').length", n0) == 0
          and rx.evaluate("()=>__receiver.state.camId") == before and not rx.evaluate("()=>document.querySelector('#blackout').classList.contains('on')"))
    check("(2) ... with a castSkip dbg note and no 'closed' message", rx.evaluate("(id)=>__receiver.log.some(e=>e.type==='castSkip' && e.detail.camId===id && e.detail.why==='web-only')", web[0])
          and rx.evaluate("()=>__receiverDebug.unavailable") is False)
    # Paused: an ignored web-only request does not restart the pause timer (Codex review note).
    rx.evaluate("()=>__receiver.send({type:'pause', value:true})")
    rx.wait_for_timeout(300)
    r0 = rx.evaluate("()=>__receiver.pauseTimer().restarts")
    rx.evaluate("(id)=>__receiver.send({type:'cam', id})", web[1])
    rx.wait_for_timeout(300)
    check("(2) paused: an ignored web-only request leaves the pause timer alone", rx.evaluate("()=>__receiver.pauseTimer().restarts") == r0)
    rx.evaluate("()=>__receiver.send({type:'pause', value:false})")
    rx.wait_for_timeout(800)
    # Pin during an automatic swap away from a cam the sheet just marked web-only: the swap goes on, the web-only cam
    # is never revealed and held (Codex review blocker).
    rx.evaluate("(ids)=>__receiver.send({type:'cams', ids, skipSleeping:false, onlyDaytime:false})", ok_ids)
    rx.wait_for_timeout(300)
    a = rx.evaluate("()=>__receiver.state.camId")
    flip = {"type": "config", "cams": [dict(c, castable=(c["id"] in ok_ids and c["id"] != a)) for c in cfg["cams"]], "mixes": cfg["mixes"]}
    rx.evaluate("""(flip)=>new Promise(res=>{__receiver.expireRotation();
      const iv=setInterval(()=>{ if(document.querySelector('#blackout').classList.contains('on')){ clearInterval(iv);
        __receiver.send(flip); __receiver.send({type:'pin', value:true}); res(true);} },10);
      setTimeout(()=>{clearInterval(iv); res(false);},3000);})""", flip)
    rx.wait_for_timeout(2500)
    pinned = rx.evaluate("()=>({cam:__receiver.state.camId, pinned:__receiver.state.pinned, field:document.querySelector('#blackout').classList.contains('on')})")
    print("pin during swap:", a, pinned)
    check(f"(2) pin during a swap away from a cam just marked web-only: the castable cam is on screen, pinned ({pinned})",
          pinned["cam"] != a and pinned["cam"] in ok_ids and pinned["pinned"] is True and pinned["field"] is False)
    rx.evaluate("()=>__receiver.send({type:'pin', value:false})")
    rx.evaluate("(c)=>__receiver.send(c)", {"type": "config", "cams": cfg["cams"], "mixes": cfg["mixes"]})
    rx.wait_for_timeout(800)
    # A list where every cam is web-only (sent by the phone over the Cast channel): rests, no tight loop.
    all_web = {"type": "config", "cams": [dict(c, castable=False) for c in cfg["cams"]], "mixes": cfg["mixes"]}
    n0 = rx.evaluate("()=>__yt.cam().calls.length")
    rx.evaluate("(c)=>__receiver.send(c)", all_web)
    rx.wait_for_timeout(4000)
    st = rx.evaluate("""(n)=>({loads:__yt.cam().calls.slice(n).filter(c=>c[0]==='loadVideoById').length,
      idle:__receiver.log.filter(e=>e.type==='noCastableCams').length, msg:document.querySelector('#unavailable-msg').textContent,
      shown:!document.querySelector('#unavailable').hidden, field:document.querySelector('#blackout').classList.contains('on')})""", n0)
    print("all web-only:", st)
    check(f"(2) every cam web-only: no load, one rest, the night field with the calm closed message ({st})",
          st["loads"] == 0 and st["idle"] == 1 and st["shown"] and st["field"] and st["msg"] == "This window is closed for now.")
    rx.evaluate("(c)=>__receiver.send(c)", {"type": "config", "cams": cfg["cams"], "mixes": cfg["mixes"]})
    check("(2) a castable list again: the TV starts a castable cam at once",
          wait_until(rx, "(ok)=>ok.includes(__receiver.state.camId) && __yt.cam().state===1 && document.querySelector('#unavailable').hidden", 6000, ok_ids))
    rx.close()

    # ------------------------------------------------------------ (3) phone remote
    probe = b.new_page()
    route(probe)
    probe.goto(base + "/receiver.html?debug=1")
    cams_all = probe.evaluate("async()=>(await import('./js/data.js')).CAMS.map(c=>c.id)")
    probe.close()
    WEB = cams_all[1]
    phone_cfg = {"at": 1, "cams": [], "mixes": [{"id": "nCtOCLv6eFY", "title": "M1", "en": "Mix one", "minutes": 60}]}
    pg = b.new_page(viewport={"width": 390, "height": 844})
    route(pg)
    pg.goto(base + "/receiver.html?debug=1")
    phone_cfg = saved_config(pg, [i for i in cams_all if i != WEB])
    pg.close()
    pg = b.new_page(viewport={"width": 390, "height": 844})
    route(pg)
    pg.add_init_script(f"localStorage.setItem({json.dumps(STORE)}, {json.dumps(json.dumps(phone_cfg))})")
    pg.goto(base + "/")
    pg.wait_for_timeout(1500)
    row = f'#cams-list .cam-row[data-id="{WEB}"]'
    # Local phone player: the web-only cam plays normally, no Phone only label.
    pg.click("#start-btn")
    check("(3) local: started", wait_until(pg, "()=>__windowSeat.currentCamId() && !__windowSeat.state.pending && __yt.cam().state===1", 8000))
    pg.click("#bar [data-action=open-cams]")
    pg.wait_for_selector("#panel-cams:not([hidden])")
    check("(3) local: no Phone only label, play circle enabled", pg.is_hidden(f"{row} .phone-mark") and pg.get_attribute(f"{row} [data-action=watch]", "aria-disabled") is None)
    pg.click(f"{row} [data-action=watch]")
    check("(3) local: the phone plays the web-only cam", wait_until(pg, f"()=>__windowSeat.currentCamId()==='{WEB}' && __yt.cam().state===1", 6000))
    # Casting: the remote shows it as Phone only and never sends it.
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(400)
    pg.evaluate("()=>__mockCast.reply({type:'state',configured:false})")
    pg.wait_for_timeout(300)
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"(3) connect: the phone's web-only cam is not pushed to the TV ({[m for m in msgs if m.get('type') == 'cam']})",
          not any(m.get("type") == "cam" and m.get("id") == WEB for m in msgs))
    cfg_msg = [m for m in msgs if m.get("type") == "config"]
    check("(3) connect: the list sent to the TV carries castable:false for it",
          bool(cfg_msg) and any(c["id"] == WEB and c.get("castable") is False for c in cfg_msg[0]["cams"]))
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.evaluate(f"()=>__mockCast.reply({{type:'state',camId:'{cams_all[0]}',camTitle:'X',mixId:'nCtOCLv6eFY',mixTitle:'X',pinned:false,interval:60,volume:60,muted:false,paused:false}})")
    if not pg.is_visible("#panel-cams"):
        pg.click("#remote [data-action=open-cams]")
        pg.wait_for_selector("#panel-cams:not([hidden])")
    look = pg.evaluate(f"""()=>{{const r=document.querySelector('{row}'), m=r.querySelector('.phone-mark'), b=r.querySelector('[data-action=watch]'), cs=getComputedStyle(m);
      return {{cls:r.classList.contains('is-phone-only'), text:m.textContent, shown:!m.hidden && m.offsetParent!==null, dis:b.getAttribute('aria-disabled'), label:b.getAttribute('aria-label'),
              fs:parseFloat(cs.fontSize), dot:getComputedStyle(r.querySelector('.cam-play-dot')).opacity}}}}""")
    other = pg.evaluate(f"()=>document.querySelector('#cams-list .cam-row[data-id=\"{cams_all[0]}\"] .phone-mark').hidden")
    print("phone only row:", look)
    check(f"(3) casting: the web-only row says Phone only, legible (>=11px), play dimmed and aria-disabled ({look})",
          look["cls"] and look["text"] == "Phone only" and look["shown"] and look["fs"] >= 11 and look["dis"] == "true" and float(look["dot"]) < 1 and "phone only" in look["label"])
    check("(3) casting: castable rows have no Phone only label", other is True)
    pg.click(f"{row} [data-action=watch]", force=True)  # aria-disabled: Playwright would wait for it to be enabled
    pg.wait_for_timeout(400)
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    toast = pg.text_content("#toast") or ""
    check(f"(3) casting: tapping its play circle sends nothing to the TV ({msgs})", not any(m.get("type") == "cam" for m in msgs))
    check(f"(3) casting: a calm toast explains it and the sheet stays open ({toast!r})", "Phone only" in toast and pg.is_visible("#panel-cams"))
    # Only the web-only cam checked: the TV falls back to castable cams, and the remote's count says so (Codex review).
    pg.click('#cams-quick [data-group="none"]')
    pg.click(f"{row} [data-action=toggle-cam]")
    pg.wait_for_timeout(200)
    count = pg.text_content("#cams-count") or ""
    print("count with only the web-only cam checked:", count)
    check(f"(3) casting: only a Phone only cam checked, the count shows the TV's castable fallback, not 0 ({count!r})",
          re.fullmatch(r"\d+ in rotation now(\. \d+ checked cams? (is|are) sleeping\.)?", count) and not count.startswith("0 ") and int(count.split()[0]) > 0)
    pg.click('#cams-quick [data-group="all"]')
    pg.wait_for_timeout(200)
    pg.evaluate("()=>__mockCast.sent.length=0")
    if not pg.is_visible("#panel-cams"):
        pg.click("#remote [data-action=open-cams]")
        pg.wait_for_selector("#panel-cams:not([hidden])")
    pg.click(f'#cams-list .cam-row[data-id="{cams_all[2]}"] [data-action=watch]')
    pg.wait_for_timeout(300)
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check("(3) casting: a castable cam's play circle still goes to the TV", any(m.get("type") == "cam" and m.get("id") == cams_all[2] for m in msgs))
    pg.close()

    src = {f: open(os.path.join(ROOT, f), encoding="utf-8").read() for f in ("js/config.js", "js/receiver.js", "js/app.js", "js/cast.js", "css/style.css", "tests/test_cast_web_only.py")}
    check("no em dashes in the changed sources", all(chr(0x2014) not in t for t in src.values()))
    check("nothing left the page", not telemetry_guard.hits)
    telemetry_guard.assert_no_leaks()
    b.close()

print("page errors:", logs)
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails or logs else 0)
