"""Receiver load-failure diagnostics: cam_failed (load timeout) carries the per-load trace, a PLAYING event for another
video id is counted, and cam_shown for cast carries load_ms.
Runs against receiver.html?debug=1&telemetry=1 with the stubbed YouTube API (tests/fake_youtube.js). No telemetry
message is sent, so events stay in the receiver buffer (read through window.__telemetry.queue()) and nothing leaves
the page; telemetry_guard stubs the real hosts anyway.
Usage: test_receiver_diag.py BASE_URL"""
import os
import sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].split("?")[0].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]

# The fake player, plus two test behaviours on the cam player:
#   window.__ytStuck: ids that stay BUFFERING forever (never reach PLAYING, never fire onError).
#   window.__ytAd: ids that report PLAYING while getVideoData().video_id is another id (an ad), and stay there.
PATCH = """
(function () {
  const P = window.YT.Player.prototype, load = P.loadVideoById;
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
    stuck, ad = [i for i in ids if i != cur][:2]
    pg.evaluate("([s,a])=>{window.__ytStuck=[s]; window.__ytAd=[a];}", [stuck, ad])

    # (a) a cam that never reaches PLAYING: load_timeout with the trace
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", stuck)
    f = wait_for(pg, "cam_failed", stuck, 18000)
    print("stuck cam_failed:", f)
    check("(a) cam_failed why=load_timeout", bool(f) and f.get("why") == "load_timeout")
    f = f or {}
    check("(a) diag_v 1, ms over CAM_LOAD_MS", f.get("diag_v") == 1 and isinstance(f.get("ms"), int) and f["ms"] >= 12000)
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
    check("(b) cam_failed why=load_timeout", f.get("why") == "load_timeout")
    check("(b) ignored_playing counted, other_vid is the ad id", f.get("ignored_playing", 0) >= 1 and f.get("other_vid") == "AdVideo0001")
    check("(b) vid is the ad, vid_match false, last_state PLAYING", f.get("vid") == "AdVideo0001" and f.get("vid_match") is False and f.get("last_state") == 1)
    check(f"(b) states has BUFFERING then PLAYING ({f.get('states')})", "3@" in f.get("states", "") and "1@" in f.get("states", ""))

    # (c) a normal pick: cam_shown for cast carries load_ms
    pg.wait_for_timeout(1500)
    now = pg.evaluate("()=>__receiver.state.camId")
    ok = [i for i in ids if i not in (stuck, ad, now)][-1]
    pg.evaluate("(id)=>__receiver.send({type:'cam', id})", ok)
    sh = wait_for(pg, "cam_shown", ok, 6000) or {}
    print("cam_shown:", sh)
    check("(c) cam_shown mode cast with load_ms and ignored_playing 0", sh.get("mode") == "cast" and isinstance(sh.get("load_ms"), int) and 0 <= sh["load_ms"] < 5000 and sh.get("ignored_playing") == 0)
    check("(c) cam was committed", pg.evaluate("()=>__receiver.state.camId") == ok)
    check("nothing left the page", not telemetry_guard.hits)
    b.close()

print(logs)
sys.exit(1 if fails or logs else 0)
