"""Pause timeout while casting: the receiver owns the timer, warns at 25 minutes and ends the session at 30.
Short durations through receiver.html?debug=1&pauseWarnMs=...&pauseEndMs=... and a mocked receiver context.
The phone side runs against tests/mock_cast_sender.js and the stubbed YouTube API (tests/fake_youtube.js).
Usage: test_pause_timeout.py BASE_URL   (BASE_URL serves index.html and receiver.html)"""
import sys, os, json
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
here = os.path.dirname(os.path.abspath(__file__))
FAKE_YT = open(os.path.join(here, "fake_youtube.js")).read()
MOCK = open(os.path.join(here, "mock_cast_sender.js")).read()
WARN_MS, END_MS = 1500, 3000
HAIRLINE = "Paused. The TV rests after 30 minutes paused."
WARNING = "The TV will go back to its home screen in 5 minutes. Tap play to keep watching."
ENDED = "Paused for 30 minutes, so the TV went back to its home screen. Cast again whenever you like."
fails, logs = [], []
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
NOT_A_BOT = "Object.defineProperty(Navigator.prototype,'webdriver',{get:()=>false});Object.defineProperty(Navigator.prototype,'userAgentData',{get:()=>undefined});"

# A stand-in for the CAF receiver context: records what the receiver sends and whether it stops.
MOCK_CTX = """()=>{
  window.__ctx = { sent: [], stops: 0,
    sendCustomMessage(ns, senderId, data) { this.sent.push({ ns, senderId, msg: JSON.parse(data) }); },
    stop() { this.stops++; } };
  __receiver.useContext(window.__ctx);
}"""


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


def route(pg):
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.route("**/cast_receiver_framework.js", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))


with sync_playwright() as p:
    b = p.chromium.launch()

    # ------------------------------------------------------------ receiver: production defaults
    rx = b.new_context(viewport={"width": 1920, "height": 1080}).new_page()
    route(rx)
    rx.goto(base + "/receiver.html?debug=1")
    rx.wait_for_timeout(300)
    t = rx.evaluate("()=>__receiver.pauseTimer()")
    check(f"tv: default warning is 25 minutes, end 30 minutes ({t['warnMs']}, {t['endMs']})", t["warnMs"] == 25 * 60000 and t["endMs"] == 30 * 60000)
    check("tv: timer off while playing", t["phase"] == "off" and rx.evaluate("()=>__receiver.state.pauseWarning") is False)
    rx.close()

    # ------------------------------------------------------------ receiver: short durations
    rx = b.new_context(viewport={"width": 1920, "height": 1080}).new_page()
    route(rx)
    rx.goto(base + f"/receiver.html?debug=1&pauseWarnMs={WARN_MS}&pauseEndMs={END_MS}")
    check("tv: a cam is playing", wait_until(rx, "()=>__receiver.state.camId && __yt.cam() && __yt.cam().state===1", 6000))
    rx.evaluate(MOCK_CTX)
    t = rx.evaluate("()=>__receiver.pauseTimer()")
    check(f"tv: URL overrides the durations ({t['warnMs']}, {t['endMs']})", t["warnMs"] == WARN_MS and t["endMs"] == END_MS)
    send = lambda obj: rx.evaluate("(o)=>__receiver.send(o)", obj)
    phase = lambda: rx.evaluate("()=>__receiver.pauseTimer().phase")
    pill = lambda: rx.text_content("#paused-pill")
    tv_msgs = lambda: rx.evaluate("()=>__ctx.sent.map(s=>s.msg)")

    # Warns at the warning point, ends at the end point.
    send({"type": "pause", "value": True})
    check("tv: pause starts the timer", phase() == "running" and rx.evaluate("()=>__receiver.state.pauseEndsInMs") > END_MS - 500)
    check(f"tv: pill reads exactly Paused before the warning ({pill()!r})", pill() == "Paused")
    check("tv: debug strip shows the pause timer", "pause timer running" in rx.evaluate("()=>__receiverDebug.strip"))
    rx.wait_for_timeout(WARN_MS - 600)
    check("tv: no warning before the warning point", phase() == "running" and rx.evaluate("()=>__receiver.state.pauseWarning") is False)
    check("tv: warning at the warning point", wait_until(rx, "()=>__receiver.pauseTimer().phase==='warning'", 1500))
    check("tv: state reports pauseWarning", rx.evaluate("()=>__receiver.state.pauseWarning") is True)
    check(f"tv: pill adds Closing in 5 minutes ({pill()!r})", pill().startswith("Paused") and "Closing in 5 minutes" in pill() and rx.is_visible("#paused-closing"))
    check("tv: the warning was broadcast to the phone", any(m.get("type") == "state" and m.get("pauseWarning") is True for m in tv_msgs()))
    check("tv: not stopped at the warning", rx.evaluate("()=>__ctx.stops") == 0)
    check("tv: session ends at the end point", wait_until(rx, "()=>__ctx.stops===1", END_MS))
    check("tv: pauseTimeout sent to the phone before the stop", any(m.get("type") == "pauseTimeout" for m in tv_msgs()))
    check("tv: timer phase is ended", phase() == "ended")

    # Resume cancels.
    send({"type": "pause", "value": False})
    check("tv: resume turns the timer off", phase() == "off" and rx.evaluate("()=>__receiver.state.pauseEndsInMs") is None)
    rx.evaluate("()=>{__ctx.sent.length=0; __ctx.stops=0}")
    send({"type": "pause", "value": True})
    rx.wait_for_timeout(800)
    send({"type": "pause", "value": False})
    check("tv: resume cancels a running timer", phase() == "off")
    rx.wait_for_timeout(END_MS + 800)
    check("tv: no warning and no stop after resume", rx.evaluate("()=>__ctx.stops") == 0 and not any(m.get("pauseWarning") or m.get("type") == "pauseTimeout" for m in tv_msgs()))
    check("tv: pill hidden after resume", not rx.is_visible("#paused-pill"))

    # Resume also cancels during the warning, and the pill drops the closing line.
    send({"type": "pause", "value": True})
    wait_until(rx, "()=>__receiver.pauseTimer().phase==='warning'", WARN_MS + 1000)
    send({"type": "pause", "value": False})
    check("tv: resume during the warning cancels it", phase() == "off" and rx.evaluate("()=>__receiver.state.pauseWarning") is False)
    rx.wait_for_timeout(END_MS - WARN_MS + 800)
    check("tv: no stop after resuming from the warning", rx.evaluate("()=>__ctx.stops") == 0)

    # Keep paused restarts the full timer, still paused.
    send({"type": "pause", "value": True})
    check("tv: warning again", wait_until(rx, "()=>__receiver.pauseTimer().phase==='warning'", WARN_MS + 1000))
    send({"type": "keepPaused"})
    t = rx.evaluate("()=>__receiver.pauseTimer()")
    check(f"tv: keepPaused restarts the full timer ({t['remainingMs']}ms left)", t["phase"] == "running" and t["remainingMs"] > END_MS - 300)
    check("tv: keepPaused keeps the feed paused", rx.evaluate("()=>__receiver.state.paused") is True and rx.is_visible("#paused-pill"))
    check(f"tv: keepPaused clears the warning ({pill()!r})", rx.evaluate("()=>__receiver.state.pauseWarning") is False and pill() == "Paused")
    rx.wait_for_timeout(END_MS - WARN_MS + 500)  # past the original end point
    check("tv: no stop at the original end point after keepPaused", rx.evaluate("()=>__ctx.stops") == 0 and phase() in ("running", "warning"))
    send({"type": "pause", "value": False})

    # A phone command while paused restarts the timer (next, volume, pin); a reconnect's silent setup does not.
    for cmd in [{"type": "next"}, {"type": "volume", "value": 40}, {"type": "pin", "value": True}]:
        send({"type": "pause", "value": True})
        rx.wait_for_timeout(1000)
        before = rx.evaluate("()=>__receiver.pauseTimer().remainingMs")
        send(cmd)
        t = rx.evaluate("()=>__receiver.pauseTimer()")
        check(f"tv: {cmd['type']} while paused restarts the timer ({before} -> {t['remainingMs']}ms)", t["phase"] == "running" and t["remainingMs"] > END_MS - 300 and before < END_MS - 700)
        check(f"tv: {cmd['type']} keeps the feed paused", rx.evaluate("()=>__receiver.state.paused") is True)
        send({"type": "pause", "value": False})
    send({"type": "pin", "value": False})
    send({"type": "pause", "value": True})
    wait_until(rx, "()=>__receiver.pauseTimer().phase==='warning'", WARN_MS + 1000)
    send({"type": "shuffle", "value": True, "silent": True})
    send({"type": "getState"})
    check("tv: reconnect setup (silent shuffle, getState) keeps the warning", phase() == "warning" and rx.evaluate("()=>__receiver.state.pauseWarning") is True)
    send({"type": "mix", "id": rx.evaluate("()=>__receiver.state.mixId")})
    check("tv: a mix pick resumes the feed and cancels the timer", phase() == "off" and rx.evaluate("()=>__receiver.state.paused") is False)

    # Play or Keep paused arriving in the end delay (pauseTimeout sent, stop pending) prevents the stop.
    for cmd, label in [({"type": "pause", "value": False}, "play"), ({"type": "keepPaused"}, "keepPaused")]:
        rx.evaluate("()=>{__ctx.sent.length=0; __ctx.stops=0}")
        send({"type": "pause", "value": True})
        ended = wait_until(rx, "()=>__receiver.pauseTimer().phase==='ended'", END_MS + 1000)
        check(f"tv: {label} in the end delay: pauseTimeout sent, stop still pending", ended and rx.evaluate("()=>__ctx.stops") == 0 and any(m.get("type") == "pauseTimeout" for m in tv_msgs()))
        send(cmd)
        rx.wait_for_timeout(1200)  # well past the 600 ms end delay
        check(f"tv: {label} in the end delay prevents the stop", rx.evaluate("()=>__ctx.stops") == 0)
        check(f"tv: {label} in the end delay leaves the timer {'off' if label == 'play' else 'running'}", phase() == ("off" if label == "play" else "running"))
        send({"type": "pause", "value": False})

    # An explicit disconnect from the phone cancels the timer; a dropped or sleeping phone does not.
    rx.evaluate("()=>{__ctx.stops=0}")
    send({"type": "pause", "value": True})
    for reason in ["ERROR", "UNKNOWN", None]:
        r = rx.evaluate("(r)=>__receiver.senderDisconnected(r ? {reason: r} : {})", reason)
        check(f"tv: {reason or 'no reason'} disconnect keeps the timer ({r})", r == "running" and phase() == "running")
    r = rx.evaluate("()=>__receiver.senderDisconnected({reason: 'REQUESTED_BY_SENDER'})")
    check(f"tv: explicit sender disconnect cancels the timer ({r})", r == "off" and rx.evaluate("()=>__receiver.state.pauseEndsInMs") is None)
    check("tv: still paused after the disconnect", rx.evaluate("()=>__receiver.state.paused") is True)
    rx.wait_for_timeout(END_MS + 800)
    check("tv: no stop after an explicit disconnect", rx.evaluate("()=>__ctx.stops") == 0)
    send({"type": "pause", "value": False})
    send({"type": "pause", "value": True})
    rx.evaluate("()=>__receiver.senderDisconnected({reason: 'ERROR'})")
    check("tv: an error disconnect still ends the session", wait_until(rx, "()=>__ctx.stops===1", END_MS + 1500))
    send({"type": "pause", "value": False})

    # The test hook overrides the durations too.
    r = rx.evaluate("()=>__receiver.setPauseTimeout({warnMs: 700, endMs: 1400})")
    check("tv: __receiver.setPauseTimeout overrides", r == {"warnMs": 700, "endMs": 1400})
    rx.evaluate("()=>{__ctx.stops=0}")
    send({"type": "pause", "value": True})
    check("tv: hook durations end the session", wait_until(rx, "()=>__ctx.stops===1", 3000))
    rx.close()

    # ------------------------------------------------------------ phone: local playback has no timeout
    def phone(resume=False, not_bot=False):
        kw = {"user_agent": UA} if not_bot else {}
        ctx = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True, **kw)
        pg = ctx.new_page()
        if not_bot:  # PostHog drops events from headless browsers, so before_send (__telemetry.sent) would never run
            pg.add_init_script(NOT_A_BOT)
        if resume:
            pg.add_init_script("window.__mockResume = true")
        route(pg)
        pg.goto(base + "/index.html")
        pg.wait_for_timeout(800)
        return pg

    pg = phone()
    pg.click("#start-btn")
    check("local: a cam is playing", wait_until(pg, "()=>__windowSeat.currentCamId() && __yt.cam() && __yt.cam().state===1", 6000))
    pg.evaluate("()=>__windowSeat.state.feedPaused || document.querySelector('#bar [data-action=play-pause]').click()")
    check("local: paused", wait_until(pg, "()=>__windowSeat.feedPaused()===true", 2000))
    pg.wait_for_timeout(END_MS + 1000)
    check("local: still started and paused, no timeout", pg.evaluate("()=>__windowSeat.state.started && __windowSeat.feedPaused()") is True)
    check("local: no home screen toast", "home screen" not in (pg.text_content("#toast") or ""))
    check("local: the rotation line says Paused, not the TV copy", pg.text_content("#lp-status-text") == "Paused")
    pg.context.close()

    # ------------------------------------------------------------ phone: remote
    pg = phone()
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(400)
    check("remote: shown", pg.is_visible("#remote"))
    sent = lambda: [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    clear = lambda: pg.evaluate("()=>__mockCast.sent.length=0")
    state = lambda **kw: pg.evaluate("(o)=>__mockCast.reply(Object.assign({type:'state', build:'2026-11-18.32', camId:'5B4LmH4JbnY', mixId:'nCtOCLv6eFY', paused:false, pauseWarning:false}, o))", kw)
    state()
    check("remote: no hairline or warning while playing", not pg.is_visible("#remote-hairline") and not pg.is_visible("#remote-pause-warn"))
    pg.locator('#remote [data-cmd="play"]').tap()
    pg.wait_for_timeout(150)
    check(f"remote: hairline while paused ({pg.text_content('#remote-hairline')!r})", pg.is_visible("#remote-hairline") and pg.text_content("#remote-hairline") == HAIRLINE)
    check("remote: no warning before the TV says so", not pg.is_visible("#remote-pause-warn"))
    state(paused=True, pauseWarning=True)
    pg.wait_for_timeout(100)
    check(f"remote: exact warning text ({pg.text_content('#remote-pause-warn-text')!r})", pg.is_visible("#remote-pause-warn") and pg.text_content("#remote-pause-warn-text") == WARNING)
    check("remote: Keep paused button", pg.is_visible("#remote-keep-paused") and pg.text_content("#remote-keep-paused") == "Keep paused")
    clear()
    pg.locator("#remote-keep-paused").tap()
    pg.wait_for_timeout(150)
    check(f"remote: Keep paused sends keepPaused ({sent()})", sent() == [{"type": "keepPaused"}])
    check("remote: Keep paused hides the warning, still paused", not pg.is_visible("#remote-pause-warn") and pg.text_content("#remote-play") == "Play" and pg.is_visible("#remote-hairline"))
    state(paused=True, pauseWarning=False)
    state(paused=True, pauseWarning=True)
    clear()
    pg.locator('#remote [data-cmd="play"]').tap()
    pg.wait_for_timeout(150)
    check(f"remote: play during the warning resumes ({sent()})", sent() == [{"type": "pause", "value": False}])
    check("remote: play hides the warning and the hairline", not pg.is_visible("#remote-pause-warn") and not pg.is_visible("#remote-hairline"))

    # The TV ends the session after 30 minutes paused. The TV owns the stop: pauseTimeout alone keeps the session.
    state(paused=True, pauseWarning=True)
    pg.evaluate("()=>__mockCast.reply({type:'pauseTimeout'})")
    pg.wait_for_timeout(400)
    check("remote: pauseTimeout alone keeps the session until the TV ends it", pg.is_visible("#remote") and pg.evaluate("()=>__windowSeat.isCasting()") is True)
    check("remote: no end toast before the session ends", (pg.text_content("#toast") or "") != ENDED)
    pg.evaluate("()=>cast.framework.CastContext.getInstance().endCurrentSession()")  # the TV stopped
    pg.wait_for_timeout(400)
    check("remote: the remote closes", not pg.is_visible("#remote") and pg.evaluate("()=>__windowSeat.isCasting()") is False)
    check(f"remote: end toast ({pg.text_content('#toast')!r})", pg.text_content("#toast") == ENDED and pg.evaluate("()=>document.querySelector('#toast').classList.contains('show')"))
    check("remote: Cast pill back to idle", pg.evaluate("()=>[...document.querySelectorAll('[data-cast]')].every(b=>b.dataset.state==='idle')"))
    check("remote: Start screen back", pg.is_visible("#start-btn"))
    pg.wait_for_timeout(1500)
    check("remote: no local playback after the timeout", pg.evaluate("()=>!__windowSeat.state.started && __yt.players.filter(p=>!p.destroyed).every(p=>p.state!==1 && p.state!==3 && !p.calls.some(c=>c[0]==='loadVideoById'||c[0]==='playVideo'))"))
    pg.context.close()

    # Safety net: the session has not ended 5 s after pauseTimeout and no newer state cancelled it, so the phone ends its side.
    pg = phone()
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(400)
    state = lambda **kw: pg.evaluate("(o)=>__mockCast.reply(Object.assign({type:'state', build:'2026-11-18.32', camId:'5B4LmH4JbnY', mixId:'nCtOCLv6eFY', paused:false, pauseWarning:false}, o))", kw)
    state()  # probe reply (no configured field): the phone pushes its choices now, not 2.5 s later
    state(paused=True, pauseWarning=True)
    pg.evaluate("()=>__mockCast.reply({type:'pauseTimeout'})")
    pg.wait_for_timeout(4000)
    check("safety net: still casting before 5 s", pg.evaluate("()=>__windowSeat.isCasting()") is True)
    check("safety net: the phone ends its side after 5 s", wait_until(pg, "()=>!__windowSeat.isCasting() && document.querySelector('#remote').hidden", 2500))
    check(f"safety net: end toast ({pg.text_content('#toast')!r})", pg.text_content("#toast") == ENDED)
    pg.context.close()

    # How an unasked end is classified: only pauseTimeout, or the TV's timer really past its end, is a timeout.
    END_REASONS = ("()=>[...__telemetry.queue().filter(e=>e.name==='cast_session_end').map(e=>e.props.end_reason),"
                   " ...__telemetry.sent().filter(e=>e.event==='cast_session_end').map(e=>e.properties.end_reason)]")

    def end_case(label, setup, reason, toast, tv_ends=True):
        pg = phone(not_bot=True)
        pg.evaluate("()=>__mockCast.start()")
        pg.wait_for_timeout(400)
        st = lambda **kw: pg.evaluate("(o)=>__mockCast.reply(Object.assign({type:'state', build:'2026-11-18.32', camId:'5B4LmH4JbnY', mixId:'nCtOCLv6eFY', paused:false, pauseWarning:false}, o))", kw)
        st()  # probe reply (no configured field): the phone pushes its choices now, not 2.5 s later
        setup(pg, st)
        if tv_ends:
            pg.evaluate("()=>cast.framework.CastContext.getInstance().endCurrentSession()")  # the session ends, not asked by this phone
        pg.wait_for_timeout(400)
        check(f"{label}: idle (remote hidden, Cast pill idle, Start screen)", not pg.is_visible("#remote") and pg.evaluate("()=>__windowSeat.isCasting()") is False
              and pg.is_visible("#start-btn") and pg.evaluate("()=>[...document.querySelectorAll('[data-cast]')].every(b=>b.dataset.state==='idle')"))
        shown = pg.text_content("#toast") or ""
        check(f"{label}: {'end toast' if toast else 'no timeout toast'} ({shown!r})", (shown == ENDED) == toast)
        got = wait_until(pg, f"()=>({END_REASONS})().length>0", 10000)  # PostHog batches every few seconds
        reasons = pg.evaluate(END_REASONS)
        check(f"{label}: cast_session_end end_reason {reason} ({reasons})", got and reasons == [reason])
        pg.wait_for_timeout(800)
        check(f"{label}: no local playback after the end", pg.evaluate("()=>!__windowSeat.state.started && __yt.players.filter(p=>!p.destroyed).every(p=>p.state!==1 && p.state!==3)"))
        pg.context.close()

    # (a) The session drops during the warning, well before the TV's end time, with no pauseTimeout: a disconnect.
    end_case("drop in warning", lambda pg, st: (st(paused=True, pauseWarning=True, pauseEndsInMs=300000), pg.wait_for_timeout(100)), "disconnect", False)
    # (a2) Same, but 1.5 s before the TV's end time (more than the 500 ms tolerance): still a disconnect.
    end_case("drop just before end", lambda pg, st: (st(paused=True, pauseWarning=True, pauseEndsInMs=3000), pg.wait_for_timeout(1500)), "disconnect", False)
    # (b) The TV's end time passes, then the session ends without pauseTimeout reaching the phone (it slept): a timeout.
    end_case("slept through", lambda pg, st: (st(paused=True, pauseWarning=True, pauseEndsInMs=1500), pg.wait_for_timeout(1700)), "pause_timeout", True)
    # (b2) The phone slept before the warning went out: it only knows the running timer's end time. Past it: a timeout.
    end_case("slept before warning", lambda pg, st: (st(paused=True, pauseWarning=False, pauseEndsInMs=1500), pg.wait_for_timeout(1700)), "pause_timeout", True)
    # (c) pauseTimeout arrives, then the TV ends the session: a timeout.
    end_case("pauseTimeout then end", lambda pg, st: (st(paused=True, pauseWarning=True, pauseEndsInMs=60000),
                                                      pg.evaluate("()=>__mockCast.reply({type:'pauseTimeout'})"), pg.wait_for_timeout(200)), "pause_timeout", True)
    # (d) pauseTimeout arrives, then Stop casting is tapped before the TV stops: the timeout wins (reason and toast agree).
    end_case("pauseTimeout then Stop", lambda pg, st: (st(paused=True, pauseWarning=True, pauseEndsInMs=60000),
                                                       pg.evaluate("()=>__mockCast.reply({type:'pauseTimeout'})"), pg.wait_for_timeout(200),
                                                       pg.locator('#remote [data-cmd="stop"]').tap()), "pause_timeout", True, tv_ends=False)

    # ------------------------------------------------------------ phone and TV together (mock sender, mocked receiver context)
    # Messages are relayed both ways; the receiver's stop() ends the phone's mock session, like the real TV.
    def pair():
        tv = b.new_context(viewport={"width": 1920, "height": 1080}).new_page()
        route(tv)
        tv.goto(base + f"/receiver.html?debug=1&pauseWarnMs={WARN_MS}&pauseEndMs={END_MS}")
        wait_until(tv, "()=>__receiver.state.camId && __yt.cam() && __yt.cam().state===1", 6000)
        tv.evaluate(MOCK_CTX)
        ph = phone()
        ph.evaluate("()=>{ const c = cast.framework.CastContext.getInstance(), end = c.endCurrentSession; window.__phoneEnds = 0;"
                    " c.endCurrentSession = function () { if (!window.__tvEnding) window.__phoneEnds++; return end.apply(this, arguments); }; }")
        ph.evaluate("()=>__mockCast.start()")
        ph.wait_for_timeout(300)
        seen = {"tv": 0, "ph": 0, "ended": False}

        def pump():
            for m in tv.evaluate("(n)=>__ctx.sent.slice(n).map(s=>s.msg)", seen["tv"]):
                seen["tv"] += 1
                ph.evaluate("(o)=>__mockCast.reply(o)", m)
            for m in ph.evaluate("(n)=>__mockCast.sent.slice(n).map(m=>m.data)", seen["ph"]):
                seen["ph"] += 1
                tv.evaluate("(o)=>__receiver.send(o)", json.loads(m))
            if not seen["ended"] and tv.evaluate("()=>__ctx.stops") > 0:
                seen["ended"] = True
                ph.evaluate("()=>{ window.__tvEnding = true; cast.framework.CastContext.getInstance().endCurrentSession(); window.__tvEnding = false; }")

        def pump_for(ms, until=None):
            for _ in range(ms // 50):
                pump()
                if until and until():
                    return True
                ph.wait_for_timeout(50)
            return False

        return tv, ph, pump, pump_for

    # Play or Keep paused reaches the TV in its stop delay: the phone keeps the session and shows no toast.
    for cmd, label in [("play", "Play"), ("keep-paused", "Keep paused")]:
        tv, ph, pump, pump_for = pair()
        pump_for(300)
        ph.locator('#remote [data-cmd="play"]').tap()
        pump_for(300)
        check(f"pair {label}: paused on both", tv.evaluate("()=>__receiver.state.paused") is True and ph.text_content("#remote-play") == "Play")
        got = pump_for(END_MS + 1500, lambda: tv.evaluate("()=>__receiver.pauseTimer().phase") == "ended")
        check(f"pair {label}: TV sent pauseTimeout, stop pending", got and tv.evaluate("()=>__ctx.stops") == 0)
        pump()  # pauseTimeout reaches the phone
        check(f"pair {label}: phone still casting after pauseTimeout", ph.evaluate("()=>__windowSeat.isCasting()") is True)
        if cmd == "play":
            ph.locator('#remote [data-cmd="play"]').tap()
        else:
            # The restarted timer must outlast the 6 s check below (the hook applies to the next start).
            tv.evaluate("()=>__receiver.setPauseTimeout({warnMs: 20000, endMs: 30000})")
            ph.locator("#remote-keep-paused").tap()
        pump_for(300)
        t = tv.evaluate("()=>__receiver.pauseTimer()")
        check(f"pair {label}: TV cancelled its stop ({t['phase']})", t["phase"] == ("off" if cmd == "play" else "running"))
        pump_for(6000)  # past the TV's stop delay and the phone's 5 s safety net
        check(f"pair {label}: TV never stopped", tv.evaluate("()=>__ctx.stops") == 0)
        check(f"pair {label}: phone kept the session", ph.evaluate("()=>__windowSeat.isCasting()") is True and ph.is_visible("#remote") and ph.evaluate("()=>__phoneEnds") == 0)
        check(f"pair {label}: no end toast", (ph.text_content("#toast") or "") != ENDED)
        tv.context.close()
        ph.context.close()

    # Normal path: pauseTimeout, then the TV ends the session: end toast, idle, no local playback.
    tv, ph, pump, pump_for = pair()
    pump_for(300)
    ph.locator('#remote [data-cmd="play"]').tap()
    got = pump_for(END_MS + 2500, lambda: not ph.evaluate("()=>__windowSeat.isCasting()"))
    check("pair end: the TV stopped and the phone left the session", got and tv.evaluate("()=>__ctx.stops") == 1)
    check("pair end: the phone did not end the session itself", ph.evaluate("()=>__phoneEnds") == 0)
    check(f"pair end: end toast ({ph.text_content('#toast')!r})", ph.text_content("#toast") == ENDED)
    check("pair end: idle (remote hidden, Cast pill idle, Start screen)", not ph.is_visible("#remote") and ph.is_visible("#start-btn")
          and ph.evaluate("()=>[...document.querySelectorAll('[data-cast]')].every(b=>b.dataset.state==='idle')"))
    ph.wait_for_timeout(1000)
    check("pair end: no local iframe or playback", ph.evaluate("()=>!__windowSeat.state.started && document.querySelectorAll('iframe').length===0"
                                                             " && __yt.players.filter(p=>!p.destroyed).every(p=>p.state!==1 && p.state!==3)"))
    tv.context.close()
    ph.context.close()

    # Reconnecting (session resumed) while the TV is in the warning shows it.
    pg = phone(resume=True)
    check("reconnect: remote shown on resume", wait_until(pg, "()=>!document.querySelector('#remote').hidden", 3000))
    check("reconnect: asks the TV for its state", any(json.loads(m["data"])["type"] == "getState" for m in pg.evaluate("()=>__mockCast.sent")))
    pg.evaluate("()=>__mockCast.reply({type:'state', camId:'5B4LmH4JbnY', mixId:'nCtOCLv6eFY', paused:true, pauseWarning:true})")
    pg.wait_for_timeout(100)
    check("reconnect: warning shown with Keep paused", pg.is_visible("#remote-pause-warn") and pg.text_content("#remote-pause-warn-text") == WARNING and pg.is_visible("#remote-keep-paused"))
    # Stop casting by hand is not a timeout: no end toast.
    pg.evaluate("()=>__mockCast.reply({type:'state', paused:true, pauseWarning:false})")
    pg.locator('#remote [data-cmd="stop"]').tap()
    pg.wait_for_timeout(300)
    check("reconnect: Stop casting shows no timeout toast", (pg.text_content("#toast") or "") != ENDED)
    pg.context.close()
    b.close()

for line in logs:
    print(line)
print(f"{len(fails)} failed" if fails else "all passed")
sys.exit(1 if fails or logs else 0)
