"""Opt-in Debug mode: About switch, phone panel, Copy details, Cast debug command, TV strip, event logs, timezone coverage.
Usage: test_debug_mode.py BASE_URL (the site root, for example http://localhost:8000/)."""
import sys, os, json
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts
base = sys.argv[1].rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
SENDER = open(os.path.join(HERE, "mock_cast_sender.js")).read()
CAF = open(os.path.join(HERE, "mock_caf_receiver.js")).read()
NS = "urn:x-cast:com.windowseat"
BUILD = "2026-11-17.22"
# Clipboard stub: records what Copy details writes (headless clipboard permissions vary by platform).
CLIP = "window.__copied=null; try{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:(t)=>{window.__copied=t;return Promise.resolve();}}});}catch(e){}"
fails, logs = [], []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

def sent(pg): return [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
def toast(pg): return pg.text_content("#toast") or ""

with sync_playwright() as p:
    b = p.chromium.launch()

    def phone():
        ctx = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
        ctx.add_init_script(CLIP)
        pg = ctx.new_page()
        pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=SENDER))
        pg.route("**/iframe_api", lambda r: r.abort())
        pg.route("**/gviz/tq*", lambda r: r.abort())
        pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
        pg.goto(base + "/"); pg.wait_for_timeout(1500)
        return pg

    def open_about(pg):
        pg.click("#start-about"); pg.wait_for_timeout(200)

    def close_about(pg):
        pg.click("#panel-about [data-action=close-panel]"); pg.wait_for_timeout(200)

    def expand(pg):
        if pg.get_attribute("#debug-pill", "aria-expanded") != "true":
            pg.click("#debug-pill"); pg.wait_for_timeout(150)

    # ---- fresh load: off everywhere
    pg = phone()
    check("fresh load: Debug is off", pg.evaluate("()=>__windowSeat.isDebugOn()") is False)
    check("fresh load: phone debug panel hidden", not pg.is_visible("#debug-panel"))
    open_about(pg)
    check("About: This copy card with build and list lines", pg.is_visible(".this-copy") and pg.text_content("#about-build") == f"Build {BUILD}" and "List:" in pg.text_content(".this-copy .list-status"))
    check("About: Debug mode switch is a labelled switch, off", pg.get_attribute("#debug-toggle", "role") == "switch" and not pg.is_checked("#debug-toggle")
          and pg.evaluate("()=>document.querySelector('#debug-toggle').labels[0].textContent.includes('Debug mode')"))
    check("About: helper text", "It resets when you reload the app." in pg.text_content("#debug-help"))

    # ---- turn it on
    pg.check("#debug-toggle"); pg.wait_for_timeout(150)
    check(f"switch on: toast 'Debug on.' ({toast(pg)!r})", toast(pg) == "Debug on.")
    check("switch on: Debug is on", pg.evaluate("()=>__windowSeat.isDebugOn()") is True)
    close_about(pg)
    check("panel visible on the Start screen after About closes", pg.is_visible("#debug-panel") and pg.is_visible("#debug-pill"))
    pill = pg.text_content("#debug-pill")
    check(f"collapsed pill reads DEBUG and the build ({pill!r})", "DEBUG" in pill and BUILD in pill)
    check("panel starts collapsed", not pg.is_visible("#debug-body"))
    expand(pg)
    body = pg.text_content("#debug-body")
    check("expanded: APP / NOW / CAST groups", all(k in body for k in ("APP", "NOW", "CAST", "EVENTS")))
    check("expanded: build field", BUILD in pg.text_content("[data-section=app]"))
    check("expanded: list and tz coverage line", "built-in" in body and " · tz " in body)
    check("expanded: CastContext state shown", "NOT_CONNECTED" in pg.text_content("[data-section=cast]"))
    pg.click("#debug-copy"); pg.wait_for_timeout(200)
    copied = pg.evaluate("()=>window.__copied") or ""
    check(f"Copy details: toast 'Copied.' ({toast(pg)!r})", toast(pg) == "Copied.")
    check("Copy details: multi-line dump with build, list, Cast and Events", copied.count("\n") > 10 and BUILD in copied and "tz " in copied and "CAST" in copied and "Events" in copied)
    check("Copy details: event log has the Cast SDK and debug events", "Cast SDK available" in copied and "debug on" in copied)
    check("no debug value in localStorage", "debug" not in (pg.evaluate("()=>Object.keys(localStorage).join(',')") or "").lower())

    # ---- turn it off, then on again, then reload
    open_about(pg); pg.uncheck("#debug-toggle"); pg.wait_for_timeout(150)
    check(f"switch off: toast 'Debug off.' ({toast(pg)!r})", toast(pg) == "Debug off.")
    close_about(pg)
    check("switch off: panel hidden", not pg.is_visible("#debug-panel"))
    open_about(pg); pg.check("#debug-toggle"); close_about(pg)
    pg.reload(); pg.wait_for_timeout(1500)
    check("reload: Debug off again", pg.evaluate("()=>__windowSeat.isDebugOn()") is False and not pg.is_visible("#debug-panel"))
    open_about(pg)
    check("reload: About switch off", not pg.is_checked("#debug-toggle"))
    close_about(pg)

    # ---- timezone coverage (config.js)
    tz = pg.evaluate("""async () => {
      const c = await import('/js/config.js');
      const cfg = c.buildConfig('youtube_id,name,city,in_rotation,timezone\\n5B4LmH4JbnY,A,Tokyo,TRUE, Asia/Tokyo \\nu7kCTaCX_J4,B,X,TRUE,America/Indiana/Indianapolis\\nnCtOCLv6eFY,C,X,TRUE,Tokyo\\nEYAWYK4Eqxc,D,X,FALSE,\\n',
                                'youtube_id,title,enabled\\nnCtOCLv6eFY,M1,TRUE\\n');
      const san = c.sanitize(cfg);
      return { tz: cfg.cams.map(x => x.timezone), cov: c.tzCoverage(cfg.cams), san: c.tzCoverage(san.cams), rot: cfg.cams.map(x => x.default), mixes: cfg.mixes.length };
    }""")
    check(f"timezone column passed through, trimmed ({tz['tz']})", tz["tz"] == ["Asia/Tokyo", "America/Indiana/Indianapolis", "Tokyo", ""])
    check("tz coverage counts valid IANA names only", tz["cov"] == {"valid": 2, "total": 4} and tz["san"] == {"valid": 2, "total": 4})
    check("in_rotation and mixes still parsed", tz["rot"] == [True, True, True, False] and tz["mixes"] == 1)
    pg.context.close()

    # ---- casting: debug on before the session, then session start sends it
    pg = phone()
    open_about(pg); pg.check("#debug-toggle"); close_about(pg)
    pg.evaluate("()=>__mockCast.start()"); pg.wait_for_timeout(400)
    pg.evaluate("()=>__mockCast.reply({type:'state',configured:false})"); pg.wait_for_timeout(100)  # probe reply: a fresh TV
    msgs = sent(pg)
    dbgs = [m for m in msgs if m["type"] == "debug"]
    check("session start sends debug on:true over the namespace", dbgs and dbgs[-1].get("on") is True and all(m["ns"] == NS for m in pg.evaluate("()=>__mockCast.sent")))
    check("setup order kept: first message is still not the debug command", msgs and msgs[0]["type"] != "debug")
    check("panel moves into the remote while casting", pg.evaluate("()=>!!document.querySelector('#remote #debug-panel')") and pg.is_visible("#debug-panel"))
    pg.evaluate("()=>__mockCast.reply({type:'state',build:'2026-11-17.22',camId:'5B4LmH4JbnY',mixId:'nCtOCLv6eFY',debug:{caf:'ready',readyNs:'yes',rx:7,tx:9,appState:'ok',strip:true}})")
    pg.wait_for_timeout(1200)
    expand(pg)
    cast_txt = pg.text_content("[data-section=cast]")
    print("cast section:", cast_txt)
    check("panel shows session state and namespace listed", "SESSION_STARTED" in cast_txt and "windowseat STRING" in cast_txt and "listed" in cast_txt)
    check("panel mirrors TV CAF, ready-ns, appstate, rx/tx", "CAF ready" in cast_txt and "ready-ns yes" in cast_txt and "appstate ok" in cast_txt and "TV rx 7 tx 9" in cast_txt)
    check("panel shows phone and TV builds", f"{BUILD} · TV {BUILD}" in pg.text_content("[data-section=app]"))
    check("panel NOW shows the TV cam and mix ids", "5B4LmH4JbnY" in pg.text_content("[data-section=now]") and "nCtOCLv6eFY" in pg.text_content("[data-section=now]"))
    check("#remote-debug line still there", "Phone build 2026-11-17.22 | TV build 2026-11-17.22" in pg.text_content("#remote-debug"))
    # toggling while connected
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.evaluate("()=>{const t=document.querySelector('#debug-toggle'); t.checked=false; t.dispatchEvent(new Event('change'))}"); pg.wait_for_timeout(200)
    check("switch off while casting sends debug on:false", [m for m in sent(pg) if m["type"] == "debug"] == [{"type": "debug", "on": False, "value": False, "silent": True}])
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.evaluate("()=>{const t=document.querySelector('#debug-toggle'); t.checked=true; t.dispatchEvent(new Event('change'))}"); pg.wait_for_timeout(200)
    check(f"switch on while casting: TV toast ({toast(pg)!r})", toast(pg) == "Debug on. Showing details on the TV too.")
    check("switch on while casting sends debug on:true", any(m["type"] == "debug" and m["on"] is True for m in sent(pg)))
    # event log: a failed send and a successful one
    pg.evaluate("()=>{window.__mockFail={code:'session_error',description:'gone'}}")
    pg.locator('#remote [data-cmd="next"]').tap(); pg.wait_for_timeout(300)
    pg.evaluate("()=>{window.__mockFail=null}")
    expand(pg)
    ev = pg.text_content("#debug-events")
    check("event log shows a successful debug send", "tx debug ok" in ev)
    check("event log shows the failed send with its code", "tx next FAILED session_error" in ev)
    # The log keeps 20 lines; the new-session probe adds a few, so SESSION_STARTED may have scrolled out by now.
    check("event log shows session and rx events", ("session SESSION_STARTED" in ev or "new TV session: sending this phone's choices" in ev) and "rx state" in ev)
    pg.click("#debug-copy"); pg.wait_for_timeout(200)
    copied = pg.evaluate("()=>window.__copied") or ""
    check("Copy details includes the failure and TV build", "tx next FAILED session_error" in copied and "TV 2026-11-17.22" in copied)
    print("pill:", repr(pg.text_content("#debug-pill")))
    check("error turns the pill red: DEBUG  error", "DEBUG  error" in pg.text_content("#debug-pill") and pg.evaluate("()=>document.querySelector('#debug-panel').classList.contains('has-error')"))
    # session ends: panel goes back to the page
    pg.evaluate("()=>cast.framework.CastContext.getInstance().endCurrentSession(true)"); pg.wait_for_timeout(1500)
    check("after the session ends the panel is back on the page", pg.evaluate("()=>document.querySelector('#debug-panel').parentNode===document.body") and pg.is_visible("#debug-panel"))
    pg.context.close()

    # ---- resume sends the debug command again (only when Debug is on, value false otherwise)
    ctx = b.new_context(viewport={"width": 412, "height": 915}); ctx.add_init_script("window.__mockResume = true")
    pg = ctx.new_page(); pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=SENDER))
    pg.route("**/iframe_api", lambda r: r.abort()); pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.goto(base + "/"); pg.wait_for_timeout(1500)
    d = [m for m in sent(pg) if m["type"] == "debug"]
    check("resumed session gets the current Debug value (off after a reload)", d and d[-1]["on"] is False)
    ctx.close()

    # ---- TV receiver
    def receiver(query=""):
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.route("**/cast_receiver_framework.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=CAF))
        pg.route("**/iframe_api", lambda r: r.abort()); pg.route("**/gviz/tq*", lambda r: r.abort())
        pg.on("pageerror", lambda e: logs.append(f"PAGEERROR rx: {e}"))
        pg.goto(base + "/receiver.html" + query); pg.wait_for_timeout(1500)
        return pg
    hidden = lambda pg: pg.evaluate("()=>document.querySelector('#debug-strip').hidden")
    strip = lambda pg: pg.text_content("#debug-strip") or ""
    msg = lambda pg, obj: pg.evaluate("([ns,d])=>__caf.message(ns,d,'sender-1')", [NS, json.dumps(obj)])

    rx = receiver()
    check("receiver (no query): strip hidden", hidden(rx))
    check("receiver: CAF started and event log recorded it", any("CAF start" in e["text"] for e in rx.evaluate("()=>__receiverDebug.events")))
    rx.evaluate("()=>__caf.connect()")
    check("TV event log records senderConnected", any(e["text"].startswith("senderConnected") for e in rx.evaluate("()=>__receiverDebug.events")))
    msg(rx, {"type": "debug", "on": True})
    check("debug on:true shows the strip", not hidden(rx) and f"build {BUILD}" in strip(rx))
    s = strip(rx)
    print("strip:", s.replace("\n", " / "))
    check("strip keeps every field", all(k in s for k in ("list ", "CAF ready", "listener ready", "senders", "last in: debug", "last reply:", "ns windowseat STRING", "ready-ns", "rx 1", "tx ", "appstate")))
    check("strip adds cam, mix and the latest event", "cam " in s and "mix " in s and "ev " in s)
    st = json.loads(rx.evaluate("()=>__caf.sent")[-1]["data"])
    check("state reply carries TV diagnostics for the phone", st.get("debug", {}).get("strip") is True and st["debug"].get("rx") == 1)
    msg(rx, {"type": "debug", "on": False})
    check("debug on:false hides the strip", hidden(rx))
    msg(rx, {"type": "debug", "value": True}); check("older value:true still shows it", not hidden(rx))
    msg(rx, {"type": "debug", "value": False}); check("older value:false still hides it", hidden(rx))
    msg(rx, {"type": "debug", "on": True})
    rx.evaluate("()=>__caf.disconnect()"); rx.wait_for_timeout(100)
    check("session end (last sender left) hides the strip", hidden(rx))
    evs = [e["text"] for e in rx.evaluate("()=>__receiverDebug.events")]
    print("TV events:", evs)
    check("TV event ring buffer: rx, tx, sender connect/disconnect, at most 20", any(e.startswith("rx debug") for e in evs) and any(e.startswith("tx direct ok") for e in evs)
          and any(e.startswith("senderDisconnected") for e in evs) and len(evs) <= 20)
    check("TV events have HH:MM:SS times", all(len(e["t"]) == 8 and e["t"][2] == ":" for e in rx.evaluate("()=>__receiverDebug.events")))
    rx.close()

    rx = receiver("?strip=1")
    check("receiver ?strip=1: strip visible with the build", not hidden(rx) and f"build {BUILD}" in strip(rx))
    check("receiver ?strip=1: CAF still starts", "CAF ready" in strip(rx))
    rx.evaluate("()=>__caf.connect()"); rx.evaluate("()=>__caf.disconnect()")
    check("receiver ?strip=1: strip stays after the session ends", not hidden(rx))
    rx.close()

    rx = receiver("?debug=1")
    check("receiver ?debug=1: strip visible without CAF", not hidden(rx) and "CAF debug (off)" in strip(rx) and rx.evaluate("()=>!window.cast"))
    rx.evaluate("()=>__receiver.send({type:'debug', on:false})")
    check("receiver ?debug=1: debug off still hides on request", hidden(rx))
    rx.close()
    b.close()
print("page errors:", logs)
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails or logs else 0)
