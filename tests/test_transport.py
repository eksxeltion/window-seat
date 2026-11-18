"""Transport: Pause pauses the whole feed, Pin is a separate pill, the volume slider is music only, the cam is always muted.
Runs against a stubbed YouTube IFrame API (tests/fake_youtube.js), so every assertion is deterministic.
Usage: test_transport.py BASE_URL   (BASE_URL serves index.html and receiver.html)"""
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
PIN_COPY = "Pinned. Staying on this cam."
ARCHIVE = "JXY8_HX99q8"  # "Tokaido Shinkansen, Odawara (archive)": a recording, not a live stream
fails, logs = [], []


# Start an automatic rotation, then tap Pin while it is still loading: before the swap's load call
# (during the fade) or after it (the player already holds the new video). Runs in the page so the tap
# lands inside the swap window every time. Returns {cam, target} or an error string.
PIN_MID_SWAP = """async ([phone, afterLoad]) => {
  const until = async (f) => { const t0 = Date.now(); while (!f()) { if (Date.now() - t0 > 5000) return false; await new Promise((r) => setTimeout(r, 5)); } return true; };
  const cam = phone ? __windowSeat.currentCamId() : __receiver.state.camId;
  if (phone) __windowSeat.state.nextSwapAt = 0; else __receiver.expireRotation();
  const fading = () => document.querySelector('#blackout').classList.contains('on');
  if (!(await until(() => (phone ? __windowSeat.state.pending && __windowSeat.state.pending.reason === 'rotate' : fading())))) return 'no rotation started';
  if (afterLoad ? !(await until(() => __yt.cam().vid !== cam)) : __yt.cam().vid !== cam) return 'load timing missed';
  const target = __yt.cam().vid !== cam ? __yt.cam().vid : phone ? __windowSeat.state.pending.camId : null;
  const loads = __yt.cam().calls.filter((c) => c[0] === 'loadVideoById').length;
  if (phone) document.querySelector('#pin-pill').click(); else __receiver.send({ type: 'pin', value: true });
  return { cam, target, loads };
}"""

# Same, but the rotation's target fails (player error) and Pin lands while the fallback candidate is loading.
PIN_IN_FALLBACK = """async ([phone]) => {
  const until = async (f) => { const t0 = Date.now(); while (!f()) { if (Date.now() - t0 > 8000) return false; await new Promise((r) => setTimeout(r, 5)); } return true; };
  const yt = __yt.cam();
  const cam = phone ? __windowSeat.currentCamId() : __receiver.state.camId;
  let target = null;
  yt.loadVideoById = function (arg) {
    const v = typeof arg === 'object' && arg ? arg.videoId : arg;
    if (target !== null || v === cam) return Object.getPrototypeOf(this).loadVideoById.apply(this, arguments);
    target = v;
    this._rec('loadVideoById', arguments);
    this.vid = v;
    this.state = -1;
    setTimeout(() => this.opts.events.onError({ data: 150 }), 0);
  };
  if (phone) __windowSeat.state.nextSwapAt = 0; else __receiver.expireRotation();
  const ok = await until(() => (phone ? __windowSeat.state.pending && __windowSeat.state.pending.reason === 'skip' : target && __receiver.state.failedCams.includes(target)));
  delete yt.loadVideoById;
  if (!ok) return 'fallback never started';
  if (phone) document.querySelector('#pin-pill').click(); else __receiver.send({ type: 'pin', value: true });
  if (phone) __windowSeat.state.failed.delete(target);
  return { cam, target };
}"""

CAM_LOADS = "()=>__yt.cam().calls.filter((c)=>c[0]==='loadVideoById').length"


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def calls(pg, kind):
    return pg.evaluate(f"()=>__yt.{kind}() ? __yt.{kind}().calls.map(c=>c[0]) : []")


def ystate(pg, kind):
    return pg.evaluate(f"()=>__yt.{kind}().state")


def cam_never_unmuted(pg):
    """No cam player ever got unMute() or a non-zero volume, across every cam player created."""
    return pg.evaluate("""()=>__yt.players.filter(p=>p.kind==='cam').every(p=>p.calls.every(c=>c[0]!=='unMute' && !(c[0]==='setVolume' && c[1]!==0)))""")


def wait_until(pg, js, ms=4000):
    try:
        pg.wait_for_function(js, timeout=ms)
        return True
    except Exception:
        return False


def tap(pg, sel):
    """Click a local player control. The chrome fades 6s after the last tap, so bring it back first if needed
    (the floating show / hide button, as a person would)."""
    if pg.evaluate("()=>!!(window.__windowSeat && __windowSeat.chromeHidden && __windowSeat.chromeHidden())"):
        pg.click("#chrome-toggle")
        pg.wait_for_timeout(250)
    pg.click(sel)


def route(pg):
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.route("**/cast_receiver_framework.js", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))


with sync_playwright() as p:
    b = p.chromium.launch()

    # ------------------------------------------------------------ phone
    pg = b.new_context(viewport={"width": 1280, "height": 800}).new_page()
    route(pg)
    pg.goto(base + "/index.html")
    pg.wait_for_timeout(800)
    pg.click("#start-btn")
    check("phone: a cam is playing after Start", wait_until(pg, "()=>__windowSeat.currentCamId() && __yt.cam() && __yt.cam().state===1", 6000))
    wait_until(pg, "()=>__yt.music() && __yt.music().state===1")
    pg.wait_for_timeout(500)
    ws = lambda k: pg.evaluate(f"()=>__windowSeat.{k}()")

    # Layout: transport cluster is previous, pause/play, next; pin is its own pill in the info row, outside the transport.
    order = pg.evaluate("()=>[...document.querySelectorAll('#bar .lp-transport .bar-btn')].map(b=>b.dataset.action)")
    check(f"phone: transport cluster reads prev, play-pause, next ({order})", order == ["prev", "play-pause", "next"])
    check("phone: no pin button inside the transport cluster", pg.locator("#bar .lp-transport [data-action=pin]").count() == 0)
    check("phone: pin chip sits in the Paused / Pin cam row, outside the transport cluster", pg.locator("[data-action=pin]").count() == 1 and pg.evaluate("()=>!document.querySelector('#bar .lp-transport').contains(document.querySelector('[data-action=pin]')) && !!document.querySelector('#bar .lp-row [data-action=pin]')"))
    check("phone: pin pill starts as 'Pin cam', aria-pressed false", pg.text_content("#pin-pill") == "Pin cam" and pg.get_attribute("#pin-pill", "aria-pressed") == "false")
    check("phone: pin control never says pause", "pause" not in pg.text_content("#pin-pill").lower())
    check("phone: bar play-pause label is 'Pause' (whole feed)", pg.text_content("#bar [data-action=play-pause] .label") == "Pause")

    # Pause via the bar button: cam and music both pause.
    cam0 = ws("currentCamId")
    n_music_load = calls(pg, "music").count("loadVideoById")
    tap(pg, "#bar [data-action=play-pause]")
    pg.wait_for_timeout(300)
    check("phone: bar Pause sets feedPaused", ws("feedPaused") is True)
    check("phone: bar Pause pauses the cam", "pauseVideo" in calls(pg, "cam") and ystate(pg, "cam") == 2)
    check("phone: bar Pause pauses the music", ystate(pg, "music") == 2)
    check("phone: labels read Play (bar and Music panel)", pg.text_content("#bar [data-action=play-pause] .label") == "Play" and pg.text_content("#music-play") == "Play")
    check("phone: rotation status says Paused", pg.text_content("#lp-status-text") == "Paused")

    # Auto-rotation is frozen while paused.
    frozen = pg.evaluate("()=>__windowSeat.state.pausedRemaining")
    pg.evaluate("()=>{__windowSeat.state.nextSwapAt=0; __windowSeat.state.pausedRemaining=0}")
    pg.wait_for_timeout(2200)
    check("phone: auto-rotation does not advance while paused", ws("currentCamId") == cam0 and not pg.evaluate("()=>__windowSeat.state.pending"))
    # Put the frozen clock back. Left at 0, resume would make a rotation due at once and it would race the checks below.
    pg.evaluate("(ms)=>{__windowSeat.state.pausedRemaining=ms}", frozen)

    # Resume: cam to the live edge, music continues (no reload).
    before = len(calls(pg, "cam"))
    tap(pg, "#bar [data-action=play-pause]")
    pg.wait_for_timeout(300)
    after = calls(pg, "cam")[before:]
    check("phone: resume clears feedPaused", ws("feedPaused") is False)
    check(f"phone: resume seeks the cam to the live edge then plays ({after})", "seekTo" in after and "playVideo" in after and after.index("seekTo") < after.index("playVideo"))
    check("phone: live edge is the duration", pg.evaluate("()=>{const c=__yt.cam().calls.filter(c=>c[0]==='seekTo').pop(); return c[1]===5000 && c[2]===true}"))
    check("phone: cam playing again", ystate(pg, "cam") == 1)
    check("phone: music resumes without reloading the mix", ystate(pg, "music") == 1 and calls(pg, "music").count("loadVideoById") == n_music_load)
    check("phone: cam still muted after resume", pg.evaluate("()=>__yt.cam().isMuted() && __yt.cam().getVolume()===0"))

    # Music panel button is the same whole-feed pause.
    tap(pg, "#bar [data-action=open-music]")
    pg.wait_for_timeout(200)
    check("phone: volume slider is labeled Music", pg.evaluate("()=>document.querySelector('#music-volume').closest('label').querySelector('span').textContent") == "Music")
    pg.click("#music-play")
    pg.wait_for_timeout(250)
    check("phone: #music-play pauses cam and music", ws("feedPaused") is True and ystate(pg, "cam") == 2 and ystate(pg, "music") == 2)
    pg.click("#music-play")
    pg.wait_for_timeout(250)
    check("phone: #music-play resumes cam and music", ws("feedPaused") is False and ystate(pg, "cam") == 1 and ystate(pg, "music") == 1)

    # Volume slider: music only.
    cam_calls_before = len(calls(pg, "cam"))
    pg.evaluate("()=>{const r=document.querySelector('#music-volume'); r.value='30'; r.dispatchEvent(new Event('input',{bubbles:true}))}")
    pg.wait_for_timeout(150)
    check("phone: slider sets the music volume", pg.evaluate("()=>__yt.music().getVolume()") == 30)
    check("phone: slider never touches the cam volume", "setVolume" not in calls(pg, "cam")[cam_calls_before:] or pg.evaluate("()=>__yt.cam().getVolume()") == 0)
    # The Mix sheet and the casting remote have their own Music slider (same volume); every range control is the music volume, none is a cam volume.
    check("phone: every range control is the music volume (no cam volume)", pg.evaluate("()=>[...document.querySelectorAll('input[type=range]')].every(r=>r.getAttribute('aria-label')==='Music volume')") and pg.locator("input[type=range]").count() == 3)
    check("phone: no cam mute or cam volume control in the DOM", pg.evaluate("()=>!/cam (volume|mute|sound)/i.test([...document.querySelectorAll('button,input,label')].map(n=>n.textContent+' '+(n.getAttribute('aria-label')||'')).join('|'))"))
    pg.click("#panel-music [data-action=close-panel]")
    pg.wait_for_timeout(150)

    # Space (focus not on a button) toggles the whole feed.
    pg.evaluate("()=>document.activeElement && document.activeElement.blur()")
    pg.keyboard.press(" ")
    pg.wait_for_timeout(250)
    check("phone: Space pauses cam and music", ws("feedPaused") is True and ystate(pg, "cam") == 2 and ystate(pg, "music") == 2)
    pg.evaluate("()=>document.activeElement && document.activeElement.blur()")
    before = len(calls(pg, "cam"))
    pg.keyboard.press(" ")
    pg.wait_for_timeout(250)
    check("phone: Space resumes at the live edge", ws("feedPaused") is False and "seekTo" in calls(pg, "cam")[before:] and ystate(pg, "music") == 1)
    pg.keyboard.press("MediaPlayPause")
    pg.wait_for_timeout(200)
    check("phone: MediaPlayPause pauses the whole feed", ws("feedPaused") is True and ystate(pg, "cam") == 2)
    pg.keyboard.press("MediaPlayPause")
    pg.wait_for_timeout(200)
    check("phone: MediaPlayPause resumes the whole feed", ws("feedPaused") is False and ystate(pg, "cam") == 1)
    pg.keyboard.press("u")
    pg.wait_for_timeout(150)
    check("phone: U mutes music only (feed keeps playing)", pg.evaluate("()=>__yt.music().isMuted()") and ws("feedPaused") is False and ystate(pg, "cam") == 1)
    pg.keyboard.press("u")

    # Pin: separate pill, toast, music keeps playing, rotation frozen, next/prev keep the pin.
    check("phone: no cam load in flight before pinning", not pg.evaluate("()=>__windowSeat.state.pending"))
    tap(pg, "#pin-pill")
    pg.wait_for_timeout(200)
    check("phone: pin pill reads Pinned with aria-pressed true", pg.text_content("#pin-pill") == "Pinned" and pg.get_attribute("#pin-pill", "aria-pressed") == "true")
    check(f"phone: pin toast '{PIN_COPY}'", pg.text_content("#toast") == PIN_COPY)
    check("phone: pin does not pause music or cam", ws("feedPaused") is False and ystate(pg, "music") == 1 and ystate(pg, "cam") == 1)
    cam1 = ws("currentCamId")
    pg.evaluate("()=>{__windowSeat.state.nextSwapAt=0}")
    pg.wait_for_timeout(2200)
    check("phone: auto-rotation does not advance while pinned", ws("currentCamId") == cam1)
    pg.evaluate("()=>__windowSeat.next()")
    check("phone: next while pinned changes the cam", wait_until(pg, f"()=>__windowSeat.currentCamId()!=={json.dumps(cam1)} && !__windowSeat.state.pending"))
    check("phone: next while pinned keeps the pin", ws("pinned") is True and pg.get_attribute("#pin-pill", "aria-pressed") == "true")
    cam2 = ws("currentCamId")
    pg.evaluate("()=>__windowSeat.prev()")
    check("phone: prev while pinned changes the cam", wait_until(pg, f"()=>__windowSeat.currentCamId()!=={json.dumps(cam2)} && !__windowSeat.state.pending"))
    check("phone: prev while pinned keeps the pin", ws("pinned") is True)

    # Pause and pin compose.
    tap(pg, "#bar [data-action=play-pause]")
    pg.wait_for_timeout(200)
    check("phone: pause while pinned (Paused and pinned)", ws("feedPaused") is True and ws("pinned") is True)
    tap(pg, "#pin-pill")
    pg.wait_for_timeout(200)
    check("phone: unpin while paused keeps the feed paused", ws("pinned") is False and ws("feedPaused") is True and pg.text_content("#pin-pill") == "Pin cam")
    check("phone: unpin toast is clear and is not the pin copy", pg.text_content("#toast") == "Unpinned. Rotation is back on.")
    cam3 = ws("currentCamId")
    pg.evaluate("()=>{__windowSeat.state.nextSwapAt=0; __windowSeat.state.pausedRemaining=0}")
    pg.wait_for_timeout(2200)
    check("phone: unpinned but paused, rotation still frozen", ws("currentCamId") == cam3)
    pg.evaluate("()=>__windowSeat.next()")
    check("phone: next while paused changes the cam", wait_until(pg, f"()=>__windowSeat.currentCamId()!=={json.dumps(cam3)} && !__windowSeat.state.pending"))
    pg.wait_for_timeout(300)
    check("phone: new cam stays paused (no autoplay)", ws("feedPaused") is True and ystate(pg, "cam") == 2 and ystate(pg, "music") == 2)
    check("phone: cam muted after a swap while paused", pg.evaluate("()=>__yt.cam().isMuted() && __yt.cam().getVolume()===0"))
    tap(pg, "#bar [data-action=play-pause]")
    pg.wait_for_timeout(300)
    check("phone: resume after the swap plays cam and music", ystate(pg, "cam") == 1 and ystate(pg, "music") == 1)

    # Pin during an automatic rotation that is still loading: the cam on screen at the tap stays, music plays on.
    for after_load in (False, True):
        tag = "after the load call" if after_load else "during the fade"
        r = pg.evaluate(PIN_MID_SWAP, [True, after_load])
        check(f"phone pin mid-swap ({tag}): rotation was loading at the tap ({r})", isinstance(r, dict))
        if isinstance(r, dict):
            pg.wait_for_timeout(1500)
            check(f"phone pin mid-swap ({tag}): the cam at the tap stays current", ws("currentCamId") == r["cam"] and not pg.evaluate("()=>__windowSeat.state.pending"))
            check(f"phone pin mid-swap ({tag}): the player shows that cam, playing", pg.evaluate("()=>__yt.cam().vid") == r["cam"] and ystate(pg, "cam") == 1)
            check(f"phone pin mid-swap ({tag}): pinned, music keeps playing", ws("pinned") is True and ws("feedPaused") is False and ystate(pg, "music") == 1)
            if not after_load:
                check("phone pin mid-swap (during the fade): no cam load call (playback not restarted)", pg.evaluate(CAM_LOADS) == r["loads"])
        tap(pg, "#pin-pill")
        pg.wait_for_timeout(200)
    r = pg.evaluate(PIN_IN_FALLBACK, [True])
    check(f"phone pin in a rotation's fallback: fallback was loading at the tap ({r})", isinstance(r, dict))
    if isinstance(r, dict):
        pg.wait_for_timeout(1500)
        check("phone pin in a rotation's fallback: the cam at the tap stays, playing", ws("currentCamId") == r["cam"] and pg.evaluate("()=>__yt.cam().vid") == r["cam"] and ystate(pg, "cam") == 1 and not pg.evaluate("()=>__windowSeat.state.pending"))
    tap(pg, "#pin-pill")
    pg.wait_for_timeout(200)

    # Control: playing and unpinned, rotation does advance (so the freezes above mean something).
    cam4 = ws("currentCamId")
    pg.evaluate("()=>{__windowSeat.state.nextSwapAt=0}")
    check("phone: rotation advances when playing and unpinned", wait_until(pg, f"()=>__windowSeat.currentCamId()!=={json.dumps(cam4)} && !__windowSeat.state.pending", 4000))
    check("phone: every cam player load and play stayed muted (no unMute, volume only 0)", cam_never_unmuted(pg))
    loads_muted = pg.evaluate("""()=>{const c=__yt.cam().calls; return c.every((x,i)=>!['loadVideoById','playVideo'].includes(x[0]) || c.slice(0,i).some(y=>y[0]==='mute'))}""")
    check("phone: every cam load/play is preceded by mute()", loads_muted)

    # Archived cam (a recording): resume plays from the pause point and never seeks to the end.
    # The fake fires ENDED when a recording is seeked to its duration, so a wrong seek would skip the cam.
    pg.evaluate(f"()=>{{window.__ytArchive=[{json.dumps(ARCHIVE)}]}}")
    pg.evaluate(f"()=>document.querySelector('[data-action=watch][data-id={json.dumps(ARCHIVE)}]').click()")
    check("phone archive: Watch now plays the archived cam", wait_until(pg, f"()=>__windowSeat.currentCamId()==={json.dumps(ARCHIVE)} && !__windowSeat.state.pending && __yt.cam().state===1", 6000))
    check("phone archive: fake reports isLive false", pg.evaluate("()=>__yt.cam().getVideoData().isLive") is False)

    def archive_round(label):
        pg.evaluate("()=>{__yt.cam().time=1234}")
        tap(pg, "#bar [data-action=play-pause]")
        pg.wait_for_timeout(250)
        check(f"phone archive{label}: pause pauses the archived cam", ws("feedPaused") is True and ystate(pg, "cam") == 2)
        before = len(calls(pg, "cam"))
        tap(pg, "#bar [data-action=play-pause]")
        pg.wait_for_timeout(1500)
        after = pg.evaluate(f"()=>__yt.cam().calls.slice({before})")
        names = [c[0] for c in after]
        check(f"phone archive{label}: resume plays without any seek ({names})", "playVideo" in names and "seekTo" not in names)
        check(f"phone archive{label}: never seeks to the duration", not any(c[0] == "seekTo" and c[1] == 5000 for c in after))
        check(f"phone archive{label}: resumes from the pause position", pg.evaluate("()=>__yt.cam().getCurrentTime()") == 1234)
        check(f"phone archive{label}: still on the archived cam and playing", ws("currentCamId") == ARCHIVE and ystate(pg, "cam") == 1 and not pg.evaluate("()=>__windowSeat.state.pending"))
        check(f"phone archive{label}: not marked failed", pg.evaluate(f"()=>!__windowSeat.state.failed.has({json.dumps(ARCHIVE)}) && !__windowSeat.state.allFailed"))
        check(f"phone archive{label}: cam still muted", pg.evaluate("()=>__yt.cam().isMuted() && __yt.cam().getVolume()===0"))

    archive_round("")
    tap(pg, "#pin-pill")
    pg.wait_for_timeout(200)
    check("phone archive: pinned", ws("pinned") is True)
    archive_round(" (pinned)")
    tap(pg, "#pin-pill")
    pg.wait_for_timeout(200)
    pg.evaluate("()=>{window.__ytArchive=[]}")

    # Interval changed while paused: resume uses the new period, not the clock frozen at pause time.
    remaining = lambda: pg.evaluate("()=>__windowSeat.state.nextSwapAt - Date.now()")
    check("phone interval: starts at 60s, Lite off", pg.evaluate("()=>__windowSeat.state.intervalSec===60 && !__windowSeat.state.lite"))
    tap(pg, "#bar [data-action=play-pause]")
    pg.wait_for_timeout(200)
    pg.evaluate("()=>{__windowSeat.state.pausedRemaining=58000}")  # paused just after a rotation at 60s
    pg.evaluate("()=>document.querySelector('[data-action=interval]').click()")
    pg.wait_for_timeout(150)
    check("phone interval: toggled to 30s while paused", pg.evaluate("()=>__windowSeat.state.intervalSec") == 30 and ws("feedPaused") is True)
    tap(pg, "#bar [data-action=play-pause]")
    pg.wait_for_timeout(200)
    r = remaining()
    check(f"phone interval: after resume the next swap is about 30s away, not 58s ({r}ms)", ws("feedPaused") is False and 28000 <= r <= 30000)
    # Lite while paused: the effective period becomes 60s (Lite rotates every 60s).
    tap(pg, "#bar [data-action=play-pause]")
    pg.wait_for_timeout(200)
    pg.evaluate("()=>{__windowSeat.state.pausedRemaining=5000}")
    pg.evaluate("()=>document.querySelector('#cams-lite').click()")
    pg.wait_for_timeout(150)
    check("phone lite: Lite on while paused", pg.evaluate("()=>__windowSeat.state.lite") is True and ws("feedPaused") is True)
    tap(pg, "#bar [data-action=play-pause]")
    pg.wait_for_timeout(200)
    r = remaining()
    check(f"phone lite: after resume the next swap is about 60s away, not 5s ({r}ms)", ws("feedPaused") is False and 58000 <= r <= 60000)
    pg.evaluate("()=>document.querySelector('#cams-lite').click()")
    pg.evaluate("()=>document.querySelector('[data-action=interval]').click()")
    pg.wait_for_timeout(150)
    check("phone interval: restored to 60s, Lite off", pg.evaluate("()=>__windowSeat.state.intervalSec===60 && !__windowSeat.state.lite"))

    # About keyboard help.
    help_text = pg.evaluate("()=>[...document.querySelectorAll('#panel-about li')].map(l=>l.textContent).join(' ')")
    check("phone: About says Space plays or pauses the cam and the music", "Space play or pause (the cam and the music)" in help_text and "pause music" not in help_text)
    pg.context.close()

    # ------------------------------------------------------------ phone as a remote (mock Cast)
    pg = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True).new_page()
    route(pg)
    pg.goto(base + "/index.html")
    pg.wait_for_timeout(800)
    pg.evaluate("()=>__mockCast.start()")
    # Reply only after the probe getState goes out: a state that arrives before __wsProbe is set is ignored.
    check("cast: new session asks the TV first", wait_until(pg, "()=>__mockCast.sent.some(m=>{try{return JSON.parse(m.data).type==='getState'}catch(e){return false}})", 2000))
    pg.evaluate("()=>__mockCast.reply({type:'state',configured:false})")  # probe reply: a fresh TV (taps are held until it settles)
    pg.wait_for_timeout(400)
    connect = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check("cast: connect sends no pause command", not any(m["type"] in ("pause", "playMusic") for m in connect))
    check("cast: remote play button reads Pause, pin reads Pin cam", pg.text_content("#remote-play") == "Pause" and pg.text_content("#remote-pin") == "Pin cam")
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.locator('#remote [data-cmd="play"]').tap()
    pg.wait_for_timeout(150)
    sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"cast: remote Pause sends pause true ({sent})", sent == [{"type": "pause", "value": True}])
    check("cast: remote label flips to Play", pg.text_content("#remote-play") == "Play")
    pg.locator('#remote [data-cmd="pin"]').tap()
    pg.wait_for_timeout(150)
    check("cast: remote pin label reads Pinned", pg.text_content("#remote-pin") == "Pinned")
    pg.context.close()

    # ------------------------------------------------------------ TV receiver
    rx = b.new_context(viewport={"width": 1920, "height": 1080}).new_page()
    route(rx)
    rx.goto(base + "/receiver.html?debug=1")
    check("tv: a cam is playing", wait_until(rx, "()=>__receiver.state.camId && __yt.cam() && __yt.cam().state===1", 6000))
    wait_until(rx, "()=>__yt.music() && __yt.music().state===1")
    rx.wait_for_timeout(300)
    st = lambda: rx.evaluate("()=>__receiver.state")
    send = lambda obj: rx.evaluate("(o)=>__receiver.send(o)", obj)
    pill = lambda: rx.is_visible("#paused-pill")
    toast_on = lambda: rx.evaluate("()=>document.querySelector('#cmd-toast').classList.contains('show')")
    toast_text = lambda: rx.text_content("#cmd-toast")

    check("tv: Paused pill hidden while playing", not pill())
    check("tv: cam muted after load", rx.evaluate("()=>__receiver.cam().muted===true && __receiver.cam().vol===0"))
    n_mload = calls(rx, "music").count("loadVideoById")
    send({"type": "pause", "value": True})
    rx.wait_for_timeout(200)
    check("tv: pause pauses the cam", ystate(rx, "cam") == 2 and st()["paused"] is True)
    check("tv: pause pauses the music", ystate(rx, "music") == 2)
    check("tv: Paused pill visible", pill() and rx.text_content("#paused-pill") == "Paused")
    check("tv: state mirrors paused in musicPaused for older senders", st()["musicPaused"] is True)
    rx.wait_for_timeout(3000)
    check("tv: Paused pill is persistent (still up after 3s)", pill())
    cam0 = st()["camId"]
    rx.evaluate("()=>__receiver.expireRotation()")
    rx.wait_for_timeout(2200)
    check("tv: auto-rotation frozen while paused", st()["camId"] == cam0)
    send({"type": "next"})
    check("tv: next while paused changes the cam", wait_until(rx, f"()=>__receiver.state.camId!=={json.dumps(cam0)}"))
    rx.wait_for_timeout(300)
    check("tv: new cam stays paused, pill still up", ystate(rx, "cam") == 2 and ystate(rx, "music") == 2 and pill() and st()["paused"] is True)
    cam1 = st()["camId"]
    send({"type": "prev"})
    check("tv: prev while paused changes the cam", wait_until(rx, f"()=>__receiver.state.camId!=={json.dumps(cam1)}"))
    rx.wait_for_timeout(300)
    check("tv: cam muted after swaps while paused", rx.evaluate("()=>__receiver.cam().muted===true && __receiver.cam().vol===0"))
    before = len(calls(rx, "cam"))
    send({"type": "pause", "value": False})
    rx.wait_for_timeout(300)
    after = calls(rx, "cam")[before:]
    check("tv: resume hides the Paused pill", not pill() and st()["paused"] is False)
    check(f"tv: resume seeks the cam to the live edge then plays ({after})", "seekTo" in after and "playVideo" in after and after.index("seekTo") < after.index("playVideo"))
    check("tv: cam playing after resume", ystate(rx, "cam") == 1)
    check("tv: music resumed without reloading the mix", ystate(rx, "music") == 1 and calls(rx, "music").count("loadVideoById") == n_mload)
    check("tv: cam muted after resume", rx.evaluate("()=>__receiver.cam().muted===true && __receiver.cam().vol===0"))

    # A repeated resume (Cast retry after a lost ack) is a no-op: no mix reload, no second seek.
    def snap():
        return rx.evaluate("()=>({mix: __receiver.state.mixId, mvid: __yt.music().vid, mt: __yt.music().getCurrentTime(), ct: __yt.cam().getCurrentTime(), cvid: __yt.cam().vid})")
    rx.evaluate("()=>{__yt.cam().time=4321; __yt.music().time=777}")
    for repeat in ({"type": "pause", "value": False}, {"type": "playMusic", "value": True}):
        s0, cb, mb = snap(), len(calls(rx, "cam")), len(calls(rx, "music"))
        send(repeat)
        rx.wait_for_timeout(300)
        cnew, mnew = calls(rx, "cam")[cb:], calls(rx, "music")[mb:]
        tag = f"{repeat['type']} {repeat['value']}"
        check(f"tv: repeated resume ({tag}) does not reload the mix ({mnew})", "loadVideoById" not in mnew and "playVideo" not in mnew)
        check(f"tv: repeated resume ({tag}) does not seek the cam again ({cnew})", "seekTo" not in cnew and "playVideo" not in cnew)
        check(f"tv: repeated resume ({tag}) leaves mix, playheads and cam unchanged", snap() == s0)
        check(f"tv: repeated resume ({tag}) keeps playing, pill hidden", ystate(rx, "cam") == 1 and ystate(rx, "music") == 1 and not pill() and st()["paused"] is False)
    # And a repeated pause does not pause again or touch the players.
    send({"type": "pause", "value": True})
    rx.wait_for_timeout(200)
    cb, mb = len(calls(rx, "cam")), len(calls(rx, "music"))
    send({"type": "pause", "value": True})
    rx.wait_for_timeout(200)
    check("tv: repeated pause leaves the players alone", calls(rx, "cam")[cb:] == [] and calls(rx, "music")[mb:] == [] and pill() and st()["paused"] is True)
    cb = len(calls(rx, "cam"))
    send({"type": "pause", "value": False})
    rx.wait_for_timeout(300)
    check("tv: first resume after a repeated pause still seeks the live edge once", calls(rx, "cam")[cb:].count("seekTo") == 1 and ystate(rx, "cam") == 1)

    # Older playMusic command is still the whole-feed pause.
    send({"type": "playMusic", "value": False})
    rx.wait_for_timeout(200)
    check("tv: playMusic false pauses cam and music, pill up", ystate(rx, "cam") == 2 and ystate(rx, "music") == 2 and pill())
    send({"type": "playMusic", "value": True})
    rx.wait_for_timeout(200)
    check("tv: playMusic true resumes cam and music, pill hidden", ystate(rx, "cam") == 1 and ystate(rx, "music") == 1 and not pill())

    # Silent setup messages never flash a pause toast.
    rx.wait_for_timeout(2000)
    send({"type": "pause", "value": False, "silent": True})
    rx.wait_for_timeout(150)
    check("tv: silent pause message shows no toast", not toast_on())

    # Pin on: 5 second toast with the exact copy.
    send({"type": "pin", "value": True})
    rx.wait_for_timeout(150)
    check(f"tv: pin-on toast reads '{PIN_COPY}'", toast_text() == PIN_COPY and toast_on())
    check("tv: pin does not pause anything", ystate(rx, "music") == 1 and ystate(rx, "cam") == 1 and not pill())
    rx.wait_for_timeout(2900)
    check("tv: pin-on toast still shown after ~3s", toast_on() and toast_text() == PIN_COPY)
    rx.wait_for_timeout(2400)
    check("tv: pin-on toast hidden after a little more than 5s", not toast_on())
    cam2 = st()["camId"]
    rx.evaluate("()=>__receiver.expireRotation()")
    rx.wait_for_timeout(2200)
    check("tv: auto-rotation frozen while pinned", st()["camId"] == cam2)
    send({"type": "next"})
    check("tv: next while pinned changes the cam", wait_until(rx, f"()=>__receiver.state.camId!=={json.dumps(cam2)}"))
    check("tv: next while pinned keeps the pin", st()["pinned"] is True)
    cam3 = st()["camId"]
    send({"type": "prev"})
    check("tv: prev while pinned changes the cam", wait_until(rx, f"()=>__receiver.state.camId!=={json.dumps(cam3)}"))
    check("tv: prev while pinned keeps the pin", st()["pinned"] is True)
    send({"type": "pause", "value": True})
    rx.wait_for_timeout(150)
    check("tv: paused and pinned", st()["paused"] is True and st()["pinned"] is True and pill())
    send({"type": "pin", "value": False})
    rx.wait_for_timeout(150)
    check("tv: unpin while paused keeps the feed paused", st()["pinned"] is False and st()["paused"] is True and pill())
    check("tv: unpin toast is not the pin-on copy", toast_text() != PIN_COPY and toast_on())
    send({"type": "pause", "value": False})
    rx.wait_for_timeout(200)
    cam4 = st()["camId"]
    rx.evaluate("()=>__receiver.expireRotation()")
    check("tv: rotation advances when playing and unpinned", wait_until(rx, f"()=>__receiver.state.camId!=={json.dumps(cam4)}", 4000))

    # Pin during an automatic rotation that is still loading: the cam on screen at the tap stays, music plays on.
    for after_load in (False, True):
        tag = "after the load call" if after_load else "during the fade"
        rx.wait_for_timeout(300)
        r = rx.evaluate(PIN_MID_SWAP, [False, after_load])
        check(f"tv pin mid-swap ({tag}): rotation was loading at the tap ({r})", isinstance(r, dict))
        if isinstance(r, dict):
            rx.wait_for_timeout(1500)
            check(f"tv pin mid-swap ({tag}): the cam at the tap stays current", st()["camId"] == r["cam"])
            check(f"tv pin mid-swap ({tag}): the player shows that cam, playing, no blackout", rx.evaluate("()=>__yt.cam().vid") == r["cam"] and ystate(rx, "cam") == 1 and not rx.evaluate("()=>document.querySelector('#blackout').classList.contains('on')"))
            check(f"tv pin mid-swap ({tag}): pinned, music keeps playing", st()["pinned"] is True and st()["paused"] is False and ystate(rx, "music") == 1)
            if not after_load:
                check("tv pin mid-swap (during the fade): no cam load call (playback not restarted)", rx.evaluate(CAM_LOADS) == r["loads"])
        send({"type": "pin", "value": False})
    rx.wait_for_timeout(300)
    r = rx.evaluate(PIN_IN_FALLBACK, [False])
    check(f"tv pin in a rotation's fallback: fallback was loading at the tap ({r})", isinstance(r, dict))
    if isinstance(r, dict):
        rx.wait_for_timeout(1500)
        check("tv pin in a rotation's fallback: the cam at the tap stays, playing, no blackout", st()["camId"] == r["cam"] and rx.evaluate("()=>__yt.cam().vid") == r["cam"] and ystate(rx, "cam") == 1 and not rx.evaluate("()=>document.querySelector('#blackout').classList.contains('on')"))
    send({"type": "pin", "value": False})

    # Interval changed while paused: resume uses the new period, not the clock frozen at pause time.
    rx.wait_for_timeout(300)
    send({"type": "pause", "value": True})
    rx.wait_for_timeout(150)
    r0 = rx.evaluate("()=>__receiver.rotationRemaining()")
    check(f"tv interval: paused just after a rotation, frozen clock near 60s ({r0}ms)", st()["paused"] is True and r0 > 50000)
    send({"type": "interval", "value": 30})
    rx.wait_for_timeout(150)
    check("tv interval: 30s while paused", st()["interval"] == 30 and st()["paused"] is True)
    send({"type": "pause", "value": False})
    rx.wait_for_timeout(200)
    r = rx.evaluate("()=>__receiver.rotationRemaining()")
    check(f"tv interval: after resume the next swap is about 30s away, not ~60s ({r}ms)", st()["paused"] is False and 28000 <= r <= 30000)
    send({"type": "interval", "value": 60, "silent": True})
    rx.wait_for_timeout(150)

    # Archived cam on the TV: resume from the pause point, no seek to the end, no skip (also when pinned).
    rx.evaluate(f"()=>{{window.__ytArchive=[{json.dumps(ARCHIVE)}]}}")
    send({"type": "cam", "id": ARCHIVE})
    check("tv archive: cam command plays the archived cam", wait_until(rx, f"()=>__receiver.state.camId==={json.dumps(ARCHIVE)} && __yt.cam().vid==={json.dumps(ARCHIVE)} && __yt.cam().state===1", 6000))
    rx.wait_for_timeout(300)

    def tv_archive_round(label):
        rx.evaluate("()=>{__yt.cam().time=1234}")
        send({"type": "pause", "value": True})
        rx.wait_for_timeout(200)
        check(f"tv archive{label}: pause pauses the archived cam", ystate(rx, "cam") == 2 and st()["paused"] is True)
        before = len(calls(rx, "cam"))
        send({"type": "pause", "value": False})
        rx.wait_for_timeout(1500)
        after = rx.evaluate(f"()=>__yt.cam().calls.slice({before})")
        names = [c[0] for c in after]
        check(f"tv archive{label}: resume plays without any seek ({names})", "playVideo" in names and "seekTo" not in names)
        check(f"tv archive{label}: never seeks to the duration", not any(c[0] == "seekTo" and c[1] == 5000 for c in after))
        check(f"tv archive{label}: resumes from the pause position", rx.evaluate("()=>__yt.cam().getCurrentTime()") == 1234)
        check(f"tv archive{label}: still on the archived cam and playing", st()["camId"] == ARCHIVE and ystate(rx, "cam") == 1)
        check(f"tv archive{label}: not marked failed", ARCHIVE not in st()["failedCams"])
        check(f"tv archive{label}: cam still muted", rx.evaluate("()=>__receiver.cam().muted===true && __receiver.cam().vol===0"))

    tv_archive_round("")
    send({"type": "pin", "value": True})
    rx.wait_for_timeout(150)
    check("tv archive: pinned", st()["pinned"] is True)
    tv_archive_round(" (pinned)")
    send({"type": "pin", "value": False})
    rx.evaluate("()=>{window.__ytArchive=[]}")
    check("tv: no cam player was ever unmuted", cam_never_unmuted(rx))
    rx.context.close()
    b.close()

print(logs)
sys.exit(1 if fails or logs else 0)
