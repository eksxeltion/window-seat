"""Receiver pending-load patience and the no-media fast fail (build 2026-11-19.33). A cam that stays UNSTARTED or
BUFFERING on its own id while something is loading (loaded fraction above 0, current time advancing, or a hidden
pre-roll ad) is waited out for CAM_PATIENT_MS (60s) with no playVideo and no reload; it then commits (cam_shown,
load_ms, no cam_failed, no "window closed") or fails with why=load_timeout. A cam that loads nothing at all (loaded
fraction 0, time frozen, the server-stitched ad live cams on Cast) fails after CAM_NO_MEDIA_MS (8s) with
why=no_media_fast_fail and the next cam loads at once, with no "window closed" message. An error 5 reload restarts
the 8s clock. YouTube errors 2 and
150 fail at once. Error 5 reloads the same id once after about 2s; a second error 5 fails; it cannot loop. After 3s
pending the "Loading cam..." card shows in place of the black field. cam_shown and cam_failed carry load_ms, cur_time,
loaded_frac, states, ad_suspected and err5_retried.
Runs against receiver.html?debug=1&telemetry=1 with the stubbed YouTube API (tests/fake_youtube.js). No telemetry
message is sent, so events stay in the receiver buffer (window.__telemetry.queue()) and nothing leaves the page;
telemetry_guard aborts the real hosts anyway.
Usage: test_receiver_stall_retry.py BASE_URL"""
import os
import re
import sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import telemetry_guard  # noqa: E402,F401  aborts PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].split("?")[0].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]

# Test behaviours on the cam player, on top of the fake:
#   window.__ytStuck[id] = { state, until }: loads of id stay in `state` (-1 or 3) until Date.now() >= until (then PLAYING).
#     playVideo does nothing while stuck. until 0 means forever.
#   window.__ytErr[id] = { code, times, state, delay }: the first `times` loads of id go to `state` (default -1) and fire
#     onError(code) after `delay` ms (default 200), later loads play.
PATCH = """
(function () {
  const P = window.YT.Player.prototype, load = P.loadVideoById, play = P.playVideo;
  P.loadVideoById = function (arg) {
    const id = typeof arg === "object" && arg ? arg.videoId : arg;
    const st = this.kind === "cam" && (window.__ytStuck || {})[id];
    const err = this.kind === "cam" ? (window.__ytErr || {})[id] : undefined;
    if (err && err.times > 0) {
      err.times--;
      this._rec("loadVideoById", arguments);
      this.vid = id;
      this._set(err.state === undefined ? -1 : err.state);
      setTimeout(() => this.vid === id && this.opts.events.onError({ data: err.code, target: this }), err.delay || 200);
      return;
    }
    if (!st) return load.apply(this, arguments);
    window.__firstLoadAt = window.__firstLoadAt || {};
    if (!window.__firstLoadAt[id]) window.__firstLoadAt[id] = Date.now();
    this._rec("loadVideoById", arguments);
    this.vid = id;
    this._set(st.state);
    if (st.until) setTimeout(() => this.vid === id && this._set(1), Math.max(0, st.until - Date.now()));
  };
  P.playVideo = function () {
    const st = this.kind === "cam" && (window.__ytStuck || {})[this.vid];
    if (st && (!st.until || Date.now() < st.until)) return this._rec("playVideo", arguments);
    return play.apply(this, arguments);
  };
})();
"""
FAKE_YT = open(os.path.join(HERE, "fake_youtube.js")).read() + PATCH
MSG = "This window is closed for now. Next window in 5 seconds."

fails, logs = [], []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def queued(pg, name, cam_id=None):
    return pg.evaluate("([n,c])=>__telemetry.queue().filter(e=>e.name===n && (!c || e.props.cam_id===c)).map(e=>e.props)", [name, cam_id])


def wait_for(pg, name, cam_id, ms):
    for _ in range(ms // 250):
        got = queued(pg, name, cam_id)
        if got:
            return got[-1]
        pg.wait_for_timeout(250)
    return None


def mark(pg):
    return pg.evaluate("()=>__yt.cam().calls.length")


def loads(pg, cam_id, since=0):
    return pg.evaluate("([id,n])=>__yt.cam().calls.slice(n).filter(c=>c[0]==='loadVideoById' && c[1]===id).length", [cam_id, since])


def plays(pg, since=0):
    return pg.evaluate("(n)=>__yt.cam().calls.slice(n).filter(c=>c[0]==='playVideo').length", since)


def settle(pg):
    pg.wait_for_function("()=>__receiver.state.camId && __yt.cam().state===1 && document.querySelector('#unavailable').hidden && document.querySelector('#loading').hidden", timeout=15000)
    pg.wait_for_timeout(500)


def vis(pg):
    """(unavailable visible, loading card visible)"""
    return pg.evaluate("()=>[!document.querySelector('#unavailable').hidden, !document.querySelector('#loading').hidden]")


NEW_FIELDS = ("load_ms", "cur_time", "loaded_frac", "states", "ad_suspected", "err5_retried")

src = open(os.path.join(ROOT, "js/receiver.js"), encoding="utf-8").read()
check("CAM_PATIENT_MS_DEFAULT is 60000, CAM_LOAD_MS 12000", "const CAM_PATIENT_MS_DEFAULT = 60000;" in src and "const CAM_LOAD_MS = 12000;" in src)
check("CAM_NO_MEDIA_MS_DEFAULT is 8000", "const CAM_NO_MEDIA_MS_DEFAULT = 8000;" in src)
check("no LOAD_NUDGE_MS any more", "LOAD_NUDGE_MS" not in src)
check("post-commit stall watchdog unchanged (STALL_NUDGE_MS 20000, STALL_SKIP_MS 15000)",
      "const STALL_NUDGE_MS = 20000;" in src and "const STALL_SKIP_MS = 15000;" in src)
check("no em dashes in receiver.js or this test", all(chr(0x2014) not in open(os.path.join(ROOT, f), encoding="utf-8").read()
                                                       for f in ("js/receiver.js", "tests/test_receiver_stall_retry.py")))


def new_page(b, query=""):
    pg = b.new_page(viewport={"width": 1280, "height": 720})
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("**/cast_receiver_framework.js", lambda r: r.abort())
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    pg.goto(base + "/receiver.html?debug=1&telemetry=1" + query)
    pg.wait_for_function("()=>window.__receiver && __receiver.state.camId", timeout=15000)
    settle(pg)
    return pg


def targets(pg, n):
    ids = pg.evaluate("async()=>(await import('./js/data.js')).CAMS.map(c=>c.id)")
    cur = pg.evaluate("()=>__receiver.state.camId")
    t = [i for i in ids if i != cur][:n]
    # Controlled pool without the targets: the automatic skip after a failure never loads one of them.
    pg.evaluate("(ids)=>__receiver.send({type:'cams', ids, skipSleeping:false, onlyDaytime:false})", [i for i in ids if i not in t])
    return t


with sync_playwright() as p:
    b = p.chromium.launch()

    # ---- a, d, e: a long buffering wait (22s UNSTARTED/BUFFERING on its own id, slowly loading) that then plays
    pg = new_page(b)
    long_id, = targets(pg, 1)
    until = pg.evaluate("()=>Date.now()+22000")
    pg.evaluate("([id,u])=>{window.__ytStuck={[id]:{state:-1, until:u}}; window.__ytMedia={[id]:{loaded:0.03}};}", [long_id, until])
    n0 = mark(pg)
    t0 = pg.evaluate("(id)=>{__receiver.send({type:'cam', id}); return Date.now();}", long_id)
    pg.wait_for_timeout(2000)
    check("(e) at 2s: black field (blackout opaque), no Loading card yet, no message",
          vis(pg) == [False, False] and pg.evaluate("()=>getComputedStyle(document.querySelector('#blackout')).opacity") == "1")
    pg.wait_for_timeout(3000)
    check(f"(e) at about 5s: the Loading card shows, no 'window closed' ({vis(pg)})", vis(pg) == [False, True])
    lc = pg.evaluate("""()=>{const m=document.querySelector('#loading-msg'), cs=getComputedStyle(m), r=document.querySelector('#loading').getBoundingClientRect();
      return {text:m.textContent, fs:parseFloat(cs.fontSize), z:getComputedStyle(document.querySelector('#loading')).zIndex, w:r.width, h:r.height}}""")
    check(f"(e) card text 'Loading cam...', large, over the whole screen ({lc})", lc["text"] == "Loading cam..." and lc["fs"] >= 32 and lc["w"] == 1280 and lc["h"] == 720 and lc["z"] == "3")
    pg.wait_for_timeout(13000)
    check(f"(a) at 17s: still waiting, not failed, not skipped, card up, no message ({vis(pg)})",
          vis(pg) == [False, True] and not queued(pg, "cam_failed", long_id) and pg.evaluate("(id)=>__receiver.state.camId!==id", long_id))
    check("(d) no playVideo and no second loadVideoById during the whole wait", plays(pg, n0) == 0 and loads(pg, long_id, n0) == 1)
    shown = wait_for(pg, "cam_shown", long_id, 12000) or {}
    print("long wait cam_shown:", shown)
    check("(a) cam_shown for it, reason pick, committed and PLAYING", shown.get("reason") == "pick" and pg.evaluate("(id)=>__receiver.state.camId===id && __yt.cam().state===1", long_id))
    check(f"(a) load_ms reflects the wait ({shown.get('load_ms')})", isinstance(shown.get("load_ms"), int) and 21000 <= shown["load_ms"] < 27000)
    check("(a) all new fields present, err5_retried 0, ad_suspected 0, nudged 0, retried 0",
          all(k in shown for k in NEW_FIELDS) and shown.get("err5_retried") == 0 and shown.get("ad_suspected") == 0 and shown.get("nudged") == 0 and shown.get("retried") == 0)
    check("(a) states starts UNSTARTED and ends PLAYING", isinstance(shown.get("states"), str) and shown["states"].startswith("-1@") and re.search(r"1@\d+$", shown["states"]) is not None)
    check("(a) no cam_failed, not in failedCams, no message ever", not queued(pg, "cam_failed", long_id) and not pg.evaluate("(id)=>__receiver.state.failedCams.includes(id)", long_id)
          and not pg.evaluate("()=>__receiver.log.some(e=>e.type==='camError')"))
    pg.wait_for_timeout(600)
    check("(e) the normal view takes over: card and message hidden", vis(pg) == [False, False])
    check("(d) still no playVideo after the commit", plays(pg, n0) == 0)
    pg.close()

    # ---- b: errors 2 and 150 fail at once, no wait, no retry
    pg = new_page(b)
    e2, e150 = targets(pg, 2)
    pg.evaluate("([a,c])=>{window.__ytErr={[a]:{code:2,times:99}, [c]:{code:150,times:99}};}", [e2, e150])
    for cid, code in ((e2, "2"), (e150, "150")):
        settle(pg)
        n0 = mark(pg)
        t0 = pg.evaluate("(id)=>{__receiver.send({type:'cam', id}); return Date.now();}", cid)
        f = wait_for(pg, "cam_failed", cid, 4000) or {}
        dt = pg.evaluate("()=>Date.now()") - t0
        pe = queued(pg, "player_error", cid)
        print("error cam_failed:", f)
        check(f"(b) error {code}: cam_failed why=error at once ({dt} ms), player_error code {code}", f.get("why") == "error" and pe and pe[-1].get("code") == code and dt < 4000)
        check(f"(b) error {code}: no reload, no wait", loads(pg, cid, n0) == 1)
        check(f"(b) error {code}: fields present, err5_retried 0", all(k in f for k in NEW_FIELDS) and f.get("err5_retried") == 0 and f.get("retried") == 0 and f.get("nudged") == 0)
        pg.wait_for_timeout(3500)
        check(f"(b) error {code}: still one load after the err5 delay", loads(pg, cid, n0) == 1 and pg.evaluate("(id)=>__receiver.state.failedCams.includes(id)", cid))
    pg.close()

    # ---- c: error 5 during pending: one reload of the same id after about 2s
    pg = new_page(b)
    ok5, bad5, sw5, late5 = targets(pg, 4)
    pg.evaluate("([a,c,d,l])=>{window.__ytErr={[a]:{code:5,times:1}, [c]:{code:5,times:99}, [d]:{code:5,times:99}, [l]:{code:5,times:1,state:2,delay:11000}};}", [ok5, bad5, sw5, late5])
    n0 = mark(pg)
    t0 = pg.evaluate("(id)=>{__receiver.send({type:'cam', id}); return Date.now();}", ok5)
    pg.wait_for_function("(id)=>__receiver.log.some(e=>e.type==='err5Retry' && e.detail.camId===id)", arg=ok5, timeout=4000)
    check("(c) error 5: not failed at once, no message", not queued(pg, "cam_failed", ok5) and vis(pg)[0] is False and loads(pg, ok5, n0) == 1)
    shown = wait_for(pg, "cam_shown", ok5, 8000) or {}
    print("err5 cam_shown:", shown)
    t_shown = pg.evaluate("()=>Date.now()")
    check(f"(c) the same id was loaded a second time ({loads(pg, ok5, n0)})", loads(pg, ok5, n0) == 2)
    check(f"(c) recovered cam_shown err5_retried=1 retried=1 nudged=0 ({t_shown - t0} ms)",
          shown.get("err5_retried") == 1 and shown.get("retried") == 1 and shown.get("nudged") == 0 and 1500 <= shown.get("load_ms", 0) < 5000)
    check("(c) the reload came after a short delay, not at once (second load gap)", shown.get("load_ms", 0) >= 2000 and shown.get("load_ms", 0) < 4000)
    check("(c) no cam_failed for it, one player_error recorded", not queued(pg, "cam_failed", ok5) and len(queued(pg, "player_error", ok5)) == 1)
    # second error 5 fails: exactly two loads, then failure, no loop
    settle(pg)
    n0 = mark(pg)
    t0 = pg.evaluate("(id)=>{__receiver.send({type:'cam', id}); return Date.now();}", bad5)
    f = wait_for(pg, "cam_failed", bad5, 9000) or {}
    dt = pg.evaluate("()=>Date.now()") - t0
    print("err5 twice cam_failed:", f)
    check(f"(c) a second error 5 fails: cam_failed why=error, err5_retried=1 ({dt} ms)", f.get("why") == "error" and f.get("err5_retried") == 1 and f.get("retried") == 1 and 2000 <= dt < 5500)
    check("(c) exactly two loadVideoById for it", loads(pg, bad5, n0) == 2)
    pg.wait_for_timeout(5000)
    check("(c) no third load (cannot loop), failed once, the next cam plays",
          loads(pg, bad5, n0) == 2 and len(queued(pg, "cam_failed", bad5)) == 1 and pg.evaluate("(id)=>__receiver.state.camId!==id", bad5))

    # switching cams during the retry delay: the old cam's reload timer never loads it again
    settle(pg)
    n0 = mark(pg)
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", sw5)
    pg.wait_for_function("(id)=>__receiver.log.some(e=>e.type==='err5Retry' && e.detail.camId===id)", arg=sw5, timeout=4000)
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", ok5)
    pg.wait_for_timeout(4000)
    check(f"(c) a pick during the retry delay: the old cam is not reloaded ({loads(pg, sw5, n0)} loads)", loads(pg, sw5, n0) == 1)
    settle(pg)

    # an error 5 at 11s while PAUSED: the 12s state cutoff must not cancel the scheduled reload
    n0 = mark(pg)
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", late5)
    shown = wait_for(pg, "cam_shown", late5, 20000) or {}
    print("late err5 cam_shown:", shown)
    check(f"(c) error 5 at 11s in PAUSED: still reloaded once and plays, not failed ({loads(pg, late5, n0)} loads)",
          loads(pg, late5, n0) == 2 and shown.get("err5_retried") == 1 and not queued(pg, "cam_failed", late5))
    pg.close()

    # ---- f: a cam stuck forever while loading a little fails at about 60s (real wait, no override), with the new fields
    pg = new_page(b)
    dead, = targets(pg, 1)
    pg.evaluate("(id)=>{window.__ytStuck={[id]:{state:3, until:0}}; window.__ytMedia={[id]:{loaded:0.01}};}", dead)
    n0 = mark(pg)
    t0 = pg.evaluate("(id)=>{__receiver.send({type:'cam', id}); return Date.now();}", dead)
    pg.wait_for_timeout(50000)
    check(f"(f) at 50s: still waiting, card up, no message, no nudge, one load ({vis(pg)})",
          vis(pg) == [False, True] and not queued(pg, "cam_failed", dead) and plays(pg, n0) == 0 and loads(pg, dead, n0) == 1)
    f = wait_for(pg, "cam_failed", dead, 20000) or {}
    dt = pg.evaluate("()=>Date.now()") - t0
    print("stuck cam_failed:", f)
    check(f"(f) cam_failed why=load_timeout at about 60s ({dt} ms)", f.get("why") == "load_timeout" and 59000 <= dt < 66000)
    check(f"(f) load_ms about 60s ({f.get('load_ms')}), trace_scope load", isinstance(f.get("load_ms"), int) and 59500 <= f["load_ms"] < 65000 and f.get("trace_scope") == "load")
    check("(f) new fields present: cur_time, loaded_frac, states, ad_suspected 0, err5_retried 0",
          all(k in f for k in NEW_FIELDS) and f.get("ad_suspected") == 0 and f.get("err5_retried") == 0 and f.get("nudged") == 0)
    check("(f) one load, no playVideo, then the message on the night field and the card gone",
          loads(pg, dead, n0) == 1 and plays(pg, n0) == 0 and pg.evaluate("(id)=>__receiver.state.failedCams.includes(id)", dead))
    u = pg.evaluate("()=>document.querySelector('#unavailable-msg').textContent")
    check(f"(f) the failure swaps the card for the message: message visible, Loading card cleared ({vis(pg)})", vis(pg)[0] is True and vis(pg)[1] is False)
    check(f"(f) 'window closed' text only appears on this real failure ({u!r})", u == MSG)
    pg.close()

    # ---- g: nothing loads at all (loaded fraction 0, time frozen, BUFFERING forever): fast fail at about 8s, straight on
    pg = new_page(b)
    nm, = targets(pg, 1)
    pg.evaluate("(id)=>{window.__ytStuck={[id]:{state:3, until:0}}; window.__ytMedia={[id]:{loaded:0, advance:0}};}", nm)
    n0 = mark(pg)
    t0 = pg.evaluate("(id)=>{__receiver.send({type:'cam', id}); return Date.now();}", nm)
    pg.wait_for_timeout(6500)
    check(f"(g) at 6.5s: still waiting, not failed, no message ({vis(pg)})", not queued(pg, "cam_failed", nm) and vis(pg)[0] is False)
    # Watch the screen every 100ms from before the fail until the next cam plays: never the 'window closed' message.
    seen = pg.evaluate("""(id)=>new Promise(res=>{const out={msg:false, failedAt:0, nextAt:0};
      const iv=setInterval(()=>{ if(!document.querySelector('#unavailable').hidden) out.msg=true;
        if(!out.failedAt && __receiver.state.failedCams.includes(id)) out.failedAt=Date.now();
        if(out.failedAt && __receiver.state.camId && __receiver.state.camId!==id && __yt.cam().state===1){out.nextAt=Date.now(); clearInterval(iv); res(out);} },100);
      setTimeout(()=>{clearInterval(iv); res(out);},9000);})""", nm)
    f = queued(pg, "cam_failed", nm)
    f = f[-1] if f else {}
    print("no-media cam_failed:", f, seen)
    dt = seen["failedAt"] - t0 if seen["failedAt"] else -1
    check(f"(g) cam_failed why=no_media_fast_fail at about 8s ({dt} ms)", f.get("why") == "no_media_fast_fail" and 7500 <= dt < 10500)
    check(f"(g) load_ms about 8s, trace_scope load, loaded_frac 0, ad_suspected 0 ({f.get('load_ms')})",
          isinstance(f.get("load_ms"), int) and 7500 <= f["load_ms"] < 10500 and f.get("trace_scope") == "load" and f.get("loaded_frac") == 0 and f.get("ad_suspected") == 0)
    check("(g) no 'reason' field on cam_failed (why carries it, docs/telemetry.md)", "reason" not in f)
    check(f"(g) the next cam was picked and plays within 2s of the fail ({seen['nextAt'] - seen['failedAt'] if seen['nextAt'] else None} ms)",
          seen["nextAt"] and seen["nextAt"] - seen["failedAt"] < 2000)
    check("(g) no 'window closed' message at any point (no long hold)", seen["msg"] is False and vis(pg) == [False, False])
    check("(g) one load, no playVideo, no reload for the dead cam", loads(pg, nm, n0) == 1 and plays(pg, n0) == 0)
    check("(g) camDiag logged with why no_media_fast_fail", pg.evaluate("(id)=>__receiver.log.some(e=>e.type==='camDiag' && e.detail.camId===id && e.detail.why==='no_media_fast_fail')", nm))
    pg.close()

    # ---- h: loaded fraction 0 but current time advancing (media is flowing): no fast fail, it commits at 14s
    pg = new_page(b)
    tm, unk = targets(pg, 2)
    until = pg.evaluate("()=>Date.now()+14000")
    pg.evaluate("([id,u,k])=>{window.__ytStuck={[id]:{state:3, until:u}, [k]:{state:3, until:0}}; window.__ytMedia={[id]:{loaded:0, advance:1}, [k]:{loaded:null}};}", [tm, until, unk])
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", tm)
    pg.wait_for_timeout(11000)
    check(f"(h) time advancing: at 11s still waiting, not failed ({vis(pg)})", not queued(pg, "cam_failed", tm) and vis(pg) == [False, True])
    shown = wait_for(pg, "cam_shown", tm, 8000) or {}
    check(f"(h) time advancing: commits at about 14s ({shown.get('load_ms')})", isinstance(shown.get("load_ms"), int) and 13000 <= shown["load_ms"] < 18000 and not queued(pg, "cam_failed", tm))
    # ---- i: a player that reports no loaded fraction at all (undefined): never fast-failed, the 60s ceiling applies
    settle(pg)
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", unk)
    pg.wait_for_timeout(12000)
    check(f"(i) loaded fraction unknown: at 12s still waiting, not failed ({vis(pg)})", not queued(pg, "cam_failed", unk) and vis(pg) == [False, True]
          and pg.evaluate("(id)=>__receiver.state.camId!==id && !__receiver.state.failedCams.includes(id)", unk))
    pg.evaluate("()=>__receiver.send({type:'next'})")
    pg.close()

    # ---- j: error 5 at 6s, then the reload loads nothing: the 8s clock restarts at the reload (fail at about 16s)
    pg = new_page(b)
    e5, = targets(pg, 1)
    pg.evaluate("(id)=>{window.__ytErr={[id]:{code:5,times:1,state:-1,delay:6000}}; window.__ytStuck={[id]:{state:3, until:0}}; window.__ytMedia={[id]:{loaded:0}};}", e5)
    n0 = mark(pg)
    t0 = pg.evaluate("(id)=>{__receiver.send({type:'cam', id}); return Date.now();}", e5)
    pg.wait_for_timeout(9500)
    check(f"(j) at 9.5s: the error 5 wait and reload kept it from the 8s fast fail ({loads(pg, e5, n0)} loads)", not queued(pg, "cam_failed", e5) and loads(pg, e5, n0) == 2)
    f = wait_for(pg, "cam_failed", e5, 12000) or {}
    dt = pg.evaluate("()=>Date.now()") - t0
    print("err5 then no-media cam_failed:", f)
    check(f"(j) then fast fail about 8s after the reload, err5_retried 1 ({dt} ms, load_ms {f.get('load_ms')})",
          f.get("why") == "no_media_fast_fail" and f.get("err5_retried") == 1 and isinstance(f.get("load_ms"), int) and 15000 <= f["load_ms"] < 19500)
    check("(j) exactly two loads (no third), no message", loads(pg, e5, n0) == 2 and vis(pg)[0] is False)
    pg.close()

    check("nothing left the page", not telemetry_guard.hits)
    telemetry_guard.assert_no_leaks()
    b.close()

print("page errors:", logs)
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails or logs else 0)
