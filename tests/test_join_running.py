"""Joining a running TV: a NEW Cast session (SESSION_STARTED, not resumed) on a receiver another sender already set up
adopts the TV's state instead of pushing this phone's choices. The phone runs tests/mock_cast_sender.js; the TV is the
real receiver.html?debug=1 with a mocked CAF context, and this script relays messages between the two pages.
Usage: test_join_running.py BASE_URL
Optional: WS_CAST_JS=/path/to/cast.js serves that file as js/cast.js (to show a case failing against an older sender)."""
import sys, os, json, time
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
here = os.path.dirname(os.path.abspath(__file__))
FAKE_YT = open(os.path.join(here, "fake_youtube.js")).read()
MOCK = open(os.path.join(here, "mock_cast_sender.js")).read()
CAST_JS = open(os.environ["WS_CAST_JS"]).read() if os.environ.get("WS_CAST_JS") else None
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
NOT_A_BOT = "Object.defineProperty(Navigator.prototype,'webdriver',{get:()=>false});Object.defineProperty(Navigator.prototype,'userAgentData',{get:()=>undefined});"
SETUP = ("cams", "cam", "pin", "interval", "volume", "mute", "mix")
MOCK_CTX = """()=>{
  window.__ctx = { sent: [], stops: 0,
    sendCustomMessage(ns, senderId, data) { this.sent.push({ ns, senderId, msg: JSON.parse(data) }); },
    stop() { this.stops++; } };
  __receiver.useContext(window.__ctx);
}"""
END_REASONS = ("()=>[...__telemetry.queue().filter(e=>e.name==='cast_session_end').map(e=>e.props.end_reason),"
               " ...__telemetry.sent().filter(e=>e.event==='cast_session_end').map(e=>e.properties.end_reason)]")
CHECKED = "()=>[...document.querySelectorAll('#cams-list [data-action=toggle-cam][aria-checked=true]')].map(b=>b.dataset.id).sort()"
FIRST_STATE = ("()=>[...__telemetry.queue().filter(e=>e.name==='cast_first_state').map(e=>e.props),"
               " ...__telemetry.sent().filter(e=>e.event==='cast_first_state').map(e=>e.properties)]")
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


def route(pg):
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.route("**/cast_receiver_framework.js", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())
    if CAST_JS:
        pg.route("**/js/cast.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=CAST_JS))
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))


class Link:
    """Relays phone -> TV (mock sender's sent list into __receiver.send) and TV -> phone (__ctx.sent into reply)."""

    def __init__(self, pg, rx, strip=()):
        self.pg, self.rx, self.strip, self.p, self.t = pg, rx, strip, 0, rx.evaluate("()=>__ctx.sent.length")

    def pump(self, ms):
        end = time.time() + ms / 1000
        while True:
            out = self.pg.evaluate("(i)=>__mockCast.sent.slice(i).map(m=>m.data)", self.p)
            self.p += len(out)
            for data in out:
                self.rx.evaluate("(d)=>__receiver.send(d, 'phone')", data)
            back = self.rx.evaluate("(i)=>__ctx.sent.slice(i).map(s=>s.msg)", self.t)
            self.t += len(back)
            for m in back:
                for k in self.strip:
                    m.pop(k, None)
                self.pg.evaluate("(m)=>__mockCast.reply(m)", m)
            if time.time() >= end:
                return
            self.pg.wait_for_timeout(50)


with sync_playwright() as p:
    b = p.chromium.launch()

    def tv(query=""):
        rx = b.new_context(viewport={"width": 1920, "height": 1080}).new_page()
        route(rx)
        rx.goto(base + "/receiver.html?debug=1" + query)
        check("tv: a cam is playing", wait_until(rx, "()=>__receiver.state.camId && __yt.cam() && __yt.cam().state===1", 6000))
        rx.evaluate(MOCK_CTX)
        return rx

    def phone():
        ctx = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True, user_agent=UA)
        pg = ctx.new_page()
        pg.add_init_script(NOT_A_BOT)  # PostHog drops headless events, so __telemetry.sent would stay empty
        route(pg)
        pg.goto(base + "/index.html")
        pg.wait_for_timeout(800)
        pg.click("#start-btn")
        wait_until(pg, "()=>__windowSeat.currentCamId() && __windowSeat.currentMixId()", 6000)
        return pg

    tvst = lambda rx: rx.evaluate("()=>__receiver.state")
    sent = lambda pg: [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    setup_of = lambda msgs: [m["type"] for m in msgs if m["type"] in SETUP]
    ids = None

    def other_sender(rx, ph_cam, ph_mix):
        """Another sender sets the TV up: a cam and mix the phone does not have, pinned, paused, volume 25."""
        global ids
        ids = ids or rx.evaluate("async()=>{const d=await import('./js/data.js'); return {cams:d.CAMS.map(c=>c.id), mixes:d.MIXES.map(m=>m.id)}}")
        cam = next(c for c in ids["cams"] if c != ph_cam and c != tvst(rx)["camId"])
        mix = next(m for m in ids["mixes"] if m != ph_mix and m != tvst(rx)["mixId"])
        for cmd in ({"type": "cam", "id": cam}, {"type": "mix", "id": mix}, {"type": "pin", "value": True}, {"type": "volume", "value": 25}, {"type": "pause", "value": True}):
            rx.evaluate("(c)=>__receiver.send(c, 'other')", cmd)
        return cam, mix

    # ------------------------------------------------------------ receiver: configured
    rx = tv()
    send = lambda c: rx.evaluate("(c)=>__receiver.send(c)", c)
    check("tv: a fresh launch reports configured false", tvst(rx)["configured"] is False)
    for c in ({"type": "getState"}, {"type": "config", "source": "sheet", "cams": [], "mixes": []}, {"type": "debug", "on": False},
              {"type": "keepPaused"}, {"type": "shuffle", "value": True, "silent": True}, {"type": "mix", "id": "not-a-mix"}):
        send(c)
        check(f"tv: {c['type']} alone leaves configured false", tvst(rx)["configured"] is False)
    send({"type": "mix", "id": tvst(rx)["mixId"], "silent": True})
    check("tv: the first mix command sets configured", tvst(rx)["configured"] is True)
    send({"type": "getState"})
    send({"type": "config", "source": "sheet", "cams": [], "mixes": []})
    check("tv: configured never goes back to false", tvst(rx)["configured"] is True)
    check("tv: the state broadcast carries configured", rx.evaluate("()=>__ctx.sent.some(s=>s.msg.type==='state' && s.msg.configured===true)"))
    rx.close()
    rx = tv()
    rx.evaluate("(id)=>__receiver.send({type:'cam', id, silent:true})", tvst(rx)["camId"])
    check("tv: the first cam command sets configured", tvst(rx)["configured"] is True)
    rx.close()

    # ------------------------------------------------------------ (a) fresh TV: the phone pushes its choices
    rx, pg = tv(), phone()
    ph_cam, ph_mix = pg.evaluate("()=>[__windowSeat.currentCamId(), __windowSeat.currentMixId()]")
    link = Link(pg, rx)
    pg.evaluate("()=>__mockCast.start()")
    link.pump(1200)
    msgs = sent(pg)
    st = tvst(rx)
    print("(a) sent:", [m["type"] for m in msgs])
    check("(a) asks the TV first: getState is the first message", msgs and msgs[0]["type"] == "getState")
    check(f"(a) pushes the phone's choices once ({setup_of(msgs)})", all(setup_of(msgs).count(t) == 1 for t in SETUP))
    check(f"(a) TV plays the phone's cam and mix ({st['camId']}, {st['mixId']})", st["camId"] == ph_cam and st["mixId"] == ph_mix)
    check("(a) TV now configured, not pinned, not paused", st["configured"] is True and st["pinned"] is False and st["paused"] is False)
    check("(a) status: the TV confirms each tap", "The TV confirms each tap" in pg.text_content("#remote-status"))
    got = wait_until(pg, f"()=>({FIRST_STATE})().length>0", 10000)
    fs = pg.evaluate(FIRST_STATE)
    check(f"(a) cast_first_state joined_running false ({[{k: e.get(k) for k in ('resumed', 'joined_running')} for e in fs]})", got and len(fs) == 1 and fs[0].get("joined_running") is False and fs[0].get("resumed") is False)
    rx.close(); pg.context.close()

    # ------------------------------------------------------------ (b) TV set up by another sender: join, do not push
    rx, pg = tv(), phone()
    ph_cam, ph_mix = pg.evaluate("()=>[__windowSeat.currentCamId(), __windowSeat.currentMixId()]")
    tv_cam, tv_mix = other_sender(rx, ph_cam, ph_mix)
    check("(b) TV configured by the other sender", tvst(rx)["configured"] is True)
    link = Link(pg, rx)
    pg.evaluate("()=>__mockCast.start()")
    link.pump(1200)
    pg.evaluate("()=>__mockCast.start()")  # SESSION_STARTED again for the same session: never replays setup
    link.pump(3000)  # past the probe timeout
    msgs = sent(pg)
    st = tvst(rx)
    print("(b) sent:", [m["type"] for m in msgs])
    check(f"(b) no cam, mix, pin, volume, cams, interval or mute sent ({setup_of(msgs)})", setup_of(msgs) == [])
    check("(b) sends the resume set: shuffle and a second getState", [m["type"] for m in msgs].count("shuffle") == 1 and [m["type"] for m in msgs].count("getState") == 2)
    check(f"(b) TV keeps its cam ({st['camId']} vs {tv_cam})", st["camId"] == tv_cam)
    check(f"(b) TV keeps its mix ({st['mixId']})", st["mixId"] == tv_mix)
    check("(b) TV still pinned and paused, volume 25", st["pinned"] is True and st["paused"] is True and st["volume"] == 25)
    titles = rx.evaluate("([c,m])=>import('./js/data.js').then(d=>[d.CAMS.find(x=>x.id===c).title, d.MIXES.find(x=>x.id===m).en])", [tv_cam, tv_mix])
    check(f"(b) phone remote shows the TV's cam ({pg.text_content('#remote-cam')!r})", pg.text_content("#remote-cam") == titles[0])
    check(f"(b) phone remote shows the TV's mix ({pg.text_content('#remote-mix')!r})", pg.text_content("#remote-mix") == titles[1])
    check("(b) phone remote shows paused and pinned", pg.text_content("#remote-play") == "Play" and pg.get_attribute("#remote-pin", "aria-pressed") == "true")
    check(f"(b) status: the TV kept what it was playing ({pg.text_content('#remote-status')!r})", "The TV kept what it was playing" in pg.text_content("#remote-status"))
    got = wait_until(pg, f"()=>({FIRST_STATE})().length>0", 10000)
    fs = pg.evaluate(FIRST_STATE)
    check(f"(b) cast_first_state joined_running true ({[{k: e.get(k) for k in ('resumed', 'joined_running')} for e in fs]})", got and len(fs) == 1 and fs[0].get("joined_running") is True)
    rx.close(); pg.context.close()

    # ------------------------------------------------------------ (c) older receiver (no configured field): push as before
    rx, pg = tv(), phone()
    ph_cam, ph_mix = pg.evaluate("()=>[__windowSeat.currentCamId(), __windowSeat.currentMixId()]")
    other_sender(rx, ph_cam, ph_mix)
    link = Link(pg, rx, strip=("configured",))
    pg.evaluate("()=>__mockCast.start()")
    link.pump(1200)
    msgs = sent(pg)
    st = tvst(rx)
    check(f"(c) pushes the phone's choices once ({setup_of(msgs)})", all(setup_of(msgs).count(t) == 1 for t in SETUP))
    check(f"(c) TV takes the phone's mix, unpinned ({st['mixId']})", st["mixId"] == ph_mix and st["pinned"] is False)
    rx.close(); pg.context.close()

    # ------------------------------------------------------------ (d) no state before the timeout: push once
    pg = phone()
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(1500)
    check(f"(d) nothing pushed while waiting for the TV ({setup_of(sent(pg))})", setup_of(sent(pg)) == [] and [m["type"] for m in sent(pg)].count("getState") == 1)
    pg.wait_for_timeout(1500)
    check(f"(d) pushed after the timeout ({setup_of(sent(pg))})", all(setup_of(sent(pg)).count(t) == 1 for t in SETUP))
    pg.evaluate("()=>__mockCast.reply({type:'state', configured:true})")  # a late reply does not turn it into a join
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(3000)
    check(f"(d) exactly once, never replayed ({setup_of(sent(pg))})", all(setup_of(sent(pg)).count(t) == 1 for t in SETUP))
    pg.context.close()

    # ------------------------------------------------------------ (e) join with an older saved sheet: the TV keeps its lists
    rx, pg = tv(), phone()
    ph_cam, ph_mix = pg.evaluate("()=>[__windowSeat.currentCamId(), __windowSeat.currentMixId()]")
    tv_cam, tv_mix = other_sender(rx, ph_cam, ph_mix)
    pool = [tv_cam] + [c for c in ids["cams"] if c not in (tv_cam, ph_cam)][:2]
    rx.evaluate("(ids)=>__receiver.send({type:'cams', ids}, 'other')", pool)
    tv_lists = rx.evaluate("()=>import('./js/data.js').then(d=>[d.CAMS.length, d.MIXES.length])")
    # The phone's saved copy lacks the TV's cam and mix (an older sheet).
    pg.evaluate("([c,m])=>Promise.all([import('./js/config.js'), import('./js/data.js')]).then(([cfg,d])=>cfg.applyConfig({"
                "cams:d.CAMS.filter(x=>x.id!==c).map(x=>({...x})), mixes:d.MIXES.filter(x=>x.id!==m).map(x=>({...x}))}, 'saved'))", [tv_cam, tv_mix])
    link = Link(pg, rx)
    pg.evaluate("()=>__mockCast.start()")
    link.pump(1200)
    msgs = sent(pg)
    st = tvst(rx)
    print("(e) sent:", [m["type"] for m in msgs])
    check("(e) no config sent on join", "config" not in [m["type"] for m in msgs])
    now_lists = rx.evaluate("()=>import('./js/data.js').then(d=>[d.CAMS.length, d.MIXES.length])")
    check(f"(e) TV keeps its lists ({now_lists} vs {tv_lists})", now_lists == tv_lists)
    check(f"(e) TV keeps its cam, mix and pool ({st['camId']}, {st['mixId']}, {st['poolIds']})", st["camId"] == tv_cam and st["mixId"] == tv_mix and st["poolIds"] == pool)
    rx.close(); pg.context.close()

    # ------------------------------------------------------------ (f) after a join the Cams sheet edits the TV's pool and rules
    rx, pg = tv(), phone()
    ph_cam, ph_mix = pg.evaluate("()=>[__windowSeat.currentCamId(), __windowSeat.currentMixId()]")
    other_sender(rx, ph_cam, ph_mix)
    pool = ids["cams"][-3:]
    rx.evaluate("(ids)=>__receiver.send({type:'cams', ids, skipSleeping:false, onlyDaytime:true}, 'other')", pool)
    before = pg.evaluate(CHECKED)
    link = Link(pg, rx)
    pg.evaluate("()=>__mockCast.start()")
    link.pump(1200)
    check(f"(f) phone checks are the TV's pool ({pg.evaluate(CHECKED)} vs {sorted(pool)}, before {len(before)})", pg.evaluate(CHECKED) == sorted(pool))
    rules = pg.evaluate("()=>[__windowSeat.state.skipSleeping, __windowSeat.state.onlyDaytime]")
    check(f"(f) phone rules are the TV's ({rules})", rules == [False, True])
    check("(f) adopting sends no cams command", [m["type"] for m in sent(pg)].count("cams") == 0)
    extra = ids["cams"][0]
    pg.evaluate("(id)=>document.querySelector(`#cams-list [data-action=toggle-cam][data-id=\"${id}\"]`).click()", extra)
    link.pump(600)
    cams = [m for m in sent(pg) if m["type"] == "cams"]
    want = sorted(pool + [extra])
    check(f"(f) one toggle sends the TV's pool plus that cam ({cams and sorted(cams[-1]['ids'])})", len(cams) == 1 and sorted(cams[0]["ids"]) == want
          and cams[0]["skipSleeping"] is False and cams[0]["onlyDaytime"] is True)
    st = tvst(rx)
    check(f"(f) TV pool and rules after the toggle ({st['poolIds']}, {st['skipSleeping']}, {st['onlyDaytime']})", sorted(st["poolIds"]) == want and st["skipSleeping"] is False and st["onlyDaytime"] is True)
    rx.close(); pg.context.close()

    # ------------------------------------------------------------ (g) a tap while the phone asks a fresh TV is not undone by the push
    rx, pg = tv(), phone()
    pg.evaluate("()=>{__windowSeat.state.volume = 35}")
    link = Link(pg, rx)
    pg.evaluate("()=>__mockCast.start()")
    # Before the TV's reply: the Mix sheet slider, released at 70.
    pg.evaluate("""()=>{document.querySelector('#remote [data-action=open-music]').click();
      const r=document.querySelector('#mix-volume'); r.value='70'; r.dispatchEvent(new Event('input',{bubbles:true})); r.dispatchEvent(new Event('change',{bubbles:true}))}""")
    link.pump(1200)
    vols = [(m["value"], m.get("silent") is True) for m in sent(pg) if m["type"] == "volume"]
    print("(g) sent:", [m["type"] for m in sent(pg)], vols)
    check(f"(g) the push still ran, then the tap went out after it ({vols})", vols == [(35, True), (70, False)])
    check(f"(g) TV volume is the tap ({tvst(rx)['volume']})", tvst(rx)["volume"] == 70)
    rx.close(); pg.context.close()

    # ------------------------------------------------------------ (h) configured broadcast before the namespace: telemetry follows the decision
    pg = phone()
    pg.evaluate("()=>{window.__mockNs=[{name:'urn:x-cast:com.google.cast.media'}]}")
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(300)
    pg.evaluate("()=>__mockCast.reply({type:'state', configured:true})")  # unsolicited, before the channel is listed
    pg.evaluate("()=>{window.__mockNs=[{name:'urn:x-cast:com.windowseat'}]; __mockCast.fireSession('applicationmetadatachanged')}")
    pg.wait_for_timeout(300)
    pg.evaluate("()=>__mockCast.reply({type:'state', configured:true})")  # the probe reply: join
    pg.wait_for_timeout(300)
    check(f"(h) joined, nothing pushed ({setup_of(sent(pg))})", setup_of(sent(pg)) == [])
    got = wait_until(pg, f"()=>({FIRST_STATE})().length>0", 10000)
    fs = pg.evaluate(FIRST_STATE)
    check(f"(h) one cast_first_state, joined_running true ({[e.get('joined_running') for e in fs]})", got and len(fs) == 1 and fs[0].get("joined_running") is True)
    pg.context.close()

    # ------------------------------------------------------------ (i) join during the TV's pause warning: same deadline, ends as a timeout
    WARN_MS, END_MS = 1500, 5000
    rx, pg = tv(f"&pauseWarnMs={WARN_MS}&pauseEndMs={END_MS}"), phone()
    ph_cam, ph_mix = pg.evaluate("()=>[__windowSeat.currentCamId(), __windowSeat.currentMixId()]")
    other_sender(rx, ph_cam, ph_mix)
    check("(i) TV shows the pause warning", wait_until(rx, "()=>__receiver.state.pauseWarning===true", WARN_MS + 1500))
    deadline = rx.evaluate("()=>Date.now() + __receiver.state.pauseEndsInMs")
    link = Link(pg, rx)
    pg.evaluate("()=>__mockCast.start()")
    link.pump(800)
    check(f"(i) joined, nothing pushed ({setup_of(sent(pg))})", setup_of(sent(pg)) == [])
    after = rx.evaluate("()=>Date.now() + __receiver.state.pauseEndsInMs")
    check(f"(i) TV deadline unchanged by the join ({after - deadline} ms)", abs(after - deadline) < 300)
    check("(i) phone shows the warning", pg.is_visible("#remote-pause-warn"))
    while rx.evaluate("()=>__ctx.stops") == 0 and time.time() * 1000 < deadline + 4000:
        link.pump(200)
    link.pump(200)
    check("(i) TV stopped at its deadline", rx.evaluate("()=>__ctx.stops") == 1)
    pg.evaluate("()=>cast.framework.CastContext.getInstance().endCurrentSession()")  # the TV ending the session
    got = wait_until(pg, f"()=>({END_REASONS})().length>0", 10000)
    reasons = pg.evaluate(END_REASONS)
    check(f"(i) cast_session_end end_reason pause_timeout ({reasons})", got and reasons == ["pause_timeout"])
    rx.close(); pg.context.close()

    # ------------------------------------------------------------ (j) a late sheet fetch after a join does not replace the TV's lists
    rx, pg = tv(), phone()
    ph_cam, ph_mix = pg.evaluate("()=>[__windowSeat.currentCamId(), __windowSeat.currentMixId()]")
    tv_cam, tv_mix = other_sender(rx, ph_cam, ph_mix)
    pool = [tv_cam] + [c for c in ids["cams"] if c not in (tv_cam, ph_cam)][:2]
    rx.evaluate("(ids)=>__receiver.send({type:'cams', ids}, 'other')", pool)
    tv_lists = rx.evaluate("()=>import('./js/data.js').then(d=>[d.CAMS.length, d.MIXES.length])")
    link = Link(pg, rx)
    pg.evaluate("()=>__mockCast.start()")
    link.pump(1200)
    check("(j) joined, nothing pushed", setup_of(sent(pg)) == [])
    n_cfg = [m["type"] for m in sent(pg)].count("config")
    # The phone later "fetches" a sheet that lacks the TV's cam and mix, then tries to push it.
    pg.evaluate("([c,m])=>Promise.all([import('./js/config.js'), import('./js/data.js'), import('./js/cast.js')]).then(([cfg,d,cast])=>{cfg.applyConfig({cams:d.CAMS.filter(x=>x.id!==c).map(x=>({...x})), mixes:d.MIXES.filter(x=>x.id!==m).map(x=>({...x}))}, 'sheet'); cast.sendConfigToTv()})", [tv_cam, tv_mix])
    link.pump(600)
    st = tvst(rx)
    now_lists = rx.evaluate("()=>import('./js/data.js').then(d=>[d.CAMS.length, d.MIXES.length])")
    print("(j) sent:", [m["type"] for m in sent(pg)])
    check("(j) late sendConfigToTv sends no config", [m["type"] for m in sent(pg)].count("config") == n_cfg)
    check(f"(j) TV keeps its lists after the late fetch ({now_lists} vs {tv_lists})", now_lists == tv_lists)
    check(f"(j) TV keeps its cam, mix and pool ({st['camId']}, {st['mixId']}, {st['poolIds']})", st["camId"] == tv_cam and st["mixId"] == tv_mix and st["poolIds"] == pool)
    rx.close(); pg.context.close()
    b.close()

print(logs)
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails or logs else 0)
