"""Receiver load-failure diagnostics: cam_failed (load timeout) carries the per-load trace (trace_scope "load"), a
PLAYING event or an error for another video id is recorded without failing the cam early, a cam that stalls after it
was committed reports only the live player snapshot (trace_scope "snapshot"), cam_shown for cast carries load_ms, and
no event carries an author or channel name.
Runs against receiver.html?debug=1&telemetry=1 with the stubbed YouTube API (tests/fake_youtube.js). No telemetry
message is sent, so events stay in the receiver buffer (read through window.__telemetry.queue()) and nothing leaves
the page; telemetry_guard stubs the real hosts anyway.
Usage: test_receiver_diag.py BASE_URL"""
import json
import os
import re
import sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].split("?")[0].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]

# The fake player, plus two test behaviours on the cam player:
#   window.__ytStuck: ids that stay BUFFERING forever (never reach PLAYING, never fire onError), even when nudged
#     with playVideo or reloaded, so the receiver's one retry also times out.
#   window.__ytAd: ids that report PLAYING while getVideoData().video_id is another id (an ad), and stay there.
PATCH = """
(function () {
  const P = window.YT.Player.prototype, load = P.loadVideoById, play = P.playVideo;
  P.playVideo = function () {
    if (this.kind === "cam" && (window.__ytStuck || []).includes(this.vid)) return this._rec("playVideo", arguments);
    return play.apply(this, arguments);
  };
  P.loadVideoById = function (arg) {
    const id = typeof arg === "object" && arg ? arg.videoId : arg;
    const stuck = this.kind === "cam" && (window.__ytStuck || []).includes(id);
    const ad = this.kind === "cam" && (window.__ytAd || []).includes(id);
    if (!stuck && !ad) return load.apply(this, arguments);
    this._rec("loadVideoById", arguments);
    this.vid = ad ? "AdVideo0001" : id;
    this._set(3);
    if (ad) setTimeout(() => this.vid === "AdVideo0001" && this._set(1), 150);
  };
})();
"""
FAKE_YT = open(os.path.join(HERE, "fake_youtube.js")).read() + PATCH

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


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 720})
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("**/cast_receiver_framework.js", lambda r: r.abort())
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    pg.goto(base + "/receiver.html?debug=1&telemetry=1")
    pg.wait_for_function("()=>window.__receiver && __receiver.state.camId", timeout=15000)
    check("telemetry buffers (off, no phone message yet)", pg.evaluate("()=>__telemetry.state().on===false && __telemetry.state().receiver.bufferOpen"))

    ids = pg.evaluate("async()=>(await import('./js/data.js')).CAMS.map(c=>c.id)")
    cur = pg.evaluate("()=>__receiver.state.camId")
    stuck, ad, errd = [i for i in ids if i != cur][:3]
    pg.evaluate("([s,a,e])=>{window.__ytStuck=[s]; window.__ytAd=[a,e];}", [stuck, ad, errd])
    LOAD_ONLY = ("ms", "states", "ignored_playing", "other_vid", "errors")

    # (a) a cam that never reaches PLAYING (stuck BUFFERING on its own id, no error): nudged, reloaded once at
    # CAM_LOAD_MS, then load_timeout with the trace after the retry window
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", stuck)
    pg.wait_for_timeout(13500)
    check("(a) not failed at CAM_LOAD_MS: retried instead", not queued(pg, "cam_failed", stuck)
          and pg.evaluate("(id)=>__yt.cam().calls.filter(c=>c[0]==='loadVideoById' && c[1]===id).length", stuck) == 2)
    f = wait_for(pg, "cam_failed", stuck, 18000)
    print("stuck cam_failed:", f)
    check("(a) cam_failed why=load_timeout", bool(f) and f.get("why") == "load_timeout")
    f = f or {}
    check("(a) trace_scope load", f.get("trace_scope") == "load")
    check("(a) diag_v 1, ms over CAM_LOAD_MS", f.get("diag_v") == 1 and isinstance(f.get("ms"), int) and f["ms"] >= 12000)
    check(f"(a) ms is the total over both windows ({f.get('ms')})", isinstance(f.get("ms"), int) and f["ms"] >= 24000)
    check("(a) retried=1 and nudged=1", f.get("retried") == 1 and f.get("nudged") == 1)
    check("(a) playVideo nudged the stuck load in both windows", pg.evaluate("(id)=>__receiver.log.filter(e=>e.type==='loadNudge' && e.detail.camId===id).length", stuck) == 2
          and pg.evaluate("()=>__yt.cam().calls.some(c=>c[0]==='playVideo')"))
    check("(a) only one retry: two loadVideoById calls for it", pg.evaluate("(id)=>__yt.cam().calls.filter(c=>c[0]==='loadVideoById' && c[1]===id).length", stuck) == 2)
    check(f"(a) states string starts with BUFFERING ({f.get('states')})", isinstance(f.get("states"), str) and f["states"].startswith("3@"))
    check("(a) last_state BUFFERING, vid is the cam, vid_match true", f.get("last_state") == 3 and f.get("vid") == stuck and f.get("vid_match") is True)
    check("(a) is_live, muted, music_state present; ignored_playing 0", f.get("is_live") is True and f.get("muted") is True and "music_state" in f and f.get("ignored_playing") == 0)
    check("(a) no undefined-valued keys", all(v is not None for v in f.values()))
    diag_log = pg.evaluate("(id)=>__receiver.log.filter(e=>e.type==='camDiag' && e.detail.camId===id).map(e=>e.detail)", stuck)
    check("(a) camDiag in the debug log", len(diag_log) == 1 and diag_log[0].get("diag_v") == 1 and diag_log[0].get("why") == "load timeout")

    # (b) PLAYING for another video id: counted, and the cam still times out as before
    pg.wait_for_timeout(1500)  # the automatic skip after (a) settles
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", ad)
    f = wait_for(pg, "cam_failed", ad, 18000) or {}
    print("ad cam_failed:", f)
    check("(b) cam_failed why=load_timeout, trace_scope load", f.get("why") == "load_timeout" and f.get("trace_scope") == "load")
    check(f"(b) not recoverable: failed at CAM_LOAD_MS with no nudge and no retry ({f.get('ms')})",
          isinstance(f.get("ms"), int) and 12000 <= f["ms"] < 16000 and f.get("retried") == 0 and f.get("nudged") == 0
          and pg.evaluate("(id)=>__yt.cam().calls.filter(c=>c[0]==='loadVideoById' && c[1]===id).length", ad) == 1)
    check("(b) ignored_playing counted, other_vid is the ad id", f.get("ignored_playing", 0) >= 1 and f.get("other_vid") == "AdVideo0001")
    check("(b) vid is the ad, vid_match false, last_state PLAYING", f.get("vid") == "AdVideo0001" and f.get("vid_match") is False and f.get("last_state") == 1)
    states = f.get("states", "").split(",")
    i_buf = next((i for i, x in enumerate(states) if x.startswith("3@")), -1)
    i_play = next((i for i, x in enumerate(states) if x.startswith("1@")), -1)
    check(f"(b) states has BUFFERING before PLAYING ({f.get('states')})", 0 <= i_buf < i_play)

    # (c) an error for another video id during the pending load: kept for the trace, the cam is not failed early
    pg.wait_for_timeout(1500)
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", errd)
    pg.wait_for_function("(id)=>__yt.cam().calls.some(c=>c[0]==='loadVideoById' && c[1]===id)", arg=errd, timeout=5000)
    pg.wait_for_timeout(400)
    pg.evaluate("()=>{const c=__yt.cam(); c.opts.events.onError({data:150, target:c});}")
    pg.wait_for_timeout(1000)
    check("(c) not failed early: no cam_failed and no player_error yet", not queued(pg, "cam_failed", errd) and not queued(pg, "player_error", errd))
    check("(c) no camError logged for it yet", not pg.evaluate("(id)=>__receiver.log.some(e=>e.type==='camError' && e.detail.camId===id)", errd))
    f = wait_for(pg, "cam_failed", errd, 18000) or {}
    print("error cam_failed:", f)
    check("(c) cam_failed why=load_timeout, trace_scope load", f.get("why") == "load_timeout" and f.get("trace_scope") == "load")
    check("(c) errors carries the other video's code", f.get("errors") == "150")
    check("(c) ms over CAM_LOAD_MS", isinstance(f.get("ms"), int) and f["ms"] >= 12000)
    check(f"(c) not recoverable: no retry ({f.get('ms')}, retried={f.get('retried')})", f.get("retried") == 0 and f["ms"] < 16000
          and pg.evaluate("(id)=>__yt.cam().calls.filter(c=>c[0]==='loadVideoById' && c[1]===id).length", errd) == 1)

    # (d) a normal pick: a fresh cam_shown (reason pick) for cast carries load_ms
    pg.wait_for_timeout(1500)
    now = pg.evaluate("()=>__receiver.state.camId")
    ok = [i for i in ids if i not in (stuck, ad, errd, now)][-1]
    before = len(queued(pg, "cam_shown", ok))
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", ok)
    sh = {}
    for _ in range(24):
        got = [e for e in queued(pg, "cam_shown", ok)[before:] if e.get("reason") == "pick"]
        if got:
            sh = got[-1]
            break
        pg.wait_for_timeout(250)
    print("cam_shown:", sh)
    check("(d) fresh cam_shown reason pick, mode cast, load_ms and ignored_playing 0", sh.get("reason") == "pick" and sh.get("mode") == "cast" and isinstance(sh.get("load_ms"), int) and 0 <= sh["load_ms"] < 5000 and sh.get("ignored_playing") == 0)
    check("(d) a normal load is neither nudged nor retried", sh.get("nudged") == 0 and sh.get("retried") == 0)
    check("(d) cam was committed", pg.evaluate("()=>__receiver.state.camId") == ok)

    # (e) the committed cam stalls (stays BUFFERING, the nudge does not help): snapshot fields only
    pg.evaluate("()=>{const c=__yt.cam(); c.playVideo=function(){this._rec('playVideo', arguments);}; c._set(3);}")
    f = wait_for(pg, "cam_failed", ok, 45000) or {}
    print("stalled cam_failed:", f)
    check("(e) cam_failed why=stalled, trace_scope snapshot", f.get("why") == "stalled" and f.get("trace_scope") == "snapshot")
    check("(e) no load-trace fields", not any(k in f for k in LOAD_ONLY))
    check("(e) live snapshot: last_state BUFFERING, vid matches", f.get("last_state") == 3 and f.get("vid") == ok and f.get("vid_match") is True and f.get("diag_v") == 1)

    # (f) note() never throws: with JSON.stringify throwing (debug logging), a failing cam still advances
    pg.wait_for_timeout(1500)
    pg.wait_for_function("()=>__receiver.state.camId", timeout=5000)
    victim = pg.evaluate("()=>__receiver.state.camId")
    n_calls = pg.evaluate("()=>__yt.cam().calls.length")
    res = pg.evaluate("""(id)=>{
      const orig = JSON.stringify;
      JSON.stringify = () => { throw new Error("stringify blocked"); };
      let err = null;
      try {
        const c = __yt.cam(); c.vid = id; c.opts.events.onError({data:150, target:c});
      } catch (e) { err = String(e); } finally { JSON.stringify = orig; }
      return err;
    }""", victim)
    try:  # the next load starts after the fade
        pg.wait_for_function("([n,id])=>__yt.cam().calls.slice(n).some(c=>c[0]==='loadVideoById' && c[1]!==id)", arg=[n_calls, victim], timeout=5000)
        adv = True
    except Exception:
        adv = False
    check(f"(f) failing cam still advances while JSON.stringify throws (err={res})", res is None and adv)
    check("(f) camError for it is in the log", pg.evaluate("(id)=>__receiver.log.some(e=>e.type==='camError' && e.detail.camId===id)", victim))

    vid_re = re.compile(r"^[A-Za-z0-9_-]{11}$")
    qualities = {"tiny", "small", "medium", "large", "hd720", "hd1080", "hd1440", "hd2160", "highres", "auto", "default", "unknown"}
    failed = queued(pg, "cam_failed")
    check("vid/other_vid are 11-char ids where present", all(vid_re.match(e[k]) for e in failed for k in ("vid", "other_vid") if e.get(k) is not None))
    check("quality, if present, is allowlisted", all(e["quality"] in qualities for e in failed if e.get("quality") is not None))

    events = pg.evaluate("()=>__telemetry.queue()")
    logged = pg.evaluate("()=>__receiver.log")
    check("no author key in any captured event or log entry", "author" not in json.dumps(events) and '"author"' not in json.dumps(logged))
    check("nothing left the page", not telemetry_guard.hits)
    b.close()

print(logs)
sys.exit(1 if fails or logs else 0)
