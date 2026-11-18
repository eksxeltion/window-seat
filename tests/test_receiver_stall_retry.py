"""Receiver pending-load recovery: a cam load that stalls in UNSTARTED or BUFFERING on its own video id (no player
error) is nudged with playVideo at about 4s, reloaded once (same id, fresh load timer) at CAM_LOAD_MS instead of
failing, committed if PLAYING arrives in the retry window (cam_shown retried=1 nudged=1), and failed after the retry
window if it never plays (cam_failed why=load_timeout retried=1, unavailable message, skip). A real YouTube error for
the cam still fails at once with no retry.
Runs against receiver.html?debug=1&telemetry=1 with the stubbed YouTube API (tests/fake_youtube.js). No telemetry
message is sent, so events stay in the receiver buffer (window.__telemetry.queue()) and nothing leaves the page;
telemetry_guard stubs the real hosts anyway.
Usage: test_receiver_stall_retry.py BASE_URL"""
import os
import re
import sys
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].split("?")[0].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]

# Test behaviours on the cam player, on top of the fake:
#   window.__ytStuck[id] = { state, loads }: the first `loads` loadVideoById calls of id stay in `state` (-1 or 3)
#     and playVideo does nothing while that id is stuck; later loads play normally.
#   window.__ytErr[id] = code: loading id fires onError(code) for it.
PATCH = """
(function () {
  const P = window.YT.Player.prototype, load = P.loadVideoById, play = P.playVideo;
  const stuckNow = (p) => p.kind === "cam" && p.__stuck && p.__stuck === p.vid;
  P.loadVideoById = function (arg) {
    const id = typeof arg === "object" && arg ? arg.videoId : arg;
    const st = this.kind === "cam" && (window.__ytStuck || {})[id];
    const err = this.kind === "cam" ? (window.__ytErr || {})[id] : undefined;
    this.__stuck = null;
    if (err !== undefined) {
      this._rec("loadVideoById", arguments);
      this.vid = id;
      this._set(-1);
      setTimeout(() => this.vid === id && this.opts.events.onError({ data: err, target: this }), 200);
      return;
    }
    if (!st || st.loads <= 0) return load.apply(this, arguments);
    st.loads--;
    this._rec("loadVideoById", arguments);
    this.vid = id;
    this.__stuck = id;
    this._set(st.state);
  };
  P.playVideo = function () {
    if (stuckNow(this)) return this._rec("playVideo", arguments);
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


def loads(pg, cam_id):
    return pg.evaluate("(id)=>__yt.cam().calls.filter(c=>c[0]==='loadVideoById' && c[1]===id).length", cam_id)


# Index of each call on the cam player, to order playVideo against the loads of one id.
CALLS = "(id)=>__yt.cam().calls.map((c,i)=>[c[0], c[1], i]).filter(c=>c[0]==='playVideo' || (c[0]==='loadVideoById' && c[1]===id)).map(c=>c[0])"


def settle(pg):
    pg.wait_for_function("()=>__receiver.state.camId && __yt.cam().state===1 && document.querySelector('#unavailable').hidden", timeout=10000)
    pg.wait_for_timeout(500)


src = open(os.path.join(ROOT, "js/receiver.js"), encoding="utf-8").read()
check("CAM_LOAD_MS still 12000", "const CAM_LOAD_MS = 12000;" in src)
check("post-commit stall watchdog unchanged (STALL_NUDGE_MS 20000, STALL_SKIP_MS 15000)",
      "const STALL_NUDGE_MS = 20000;" in src and "const STALL_SKIP_MS = 15000;" in src)
check("no em dashes in receiver.js or this test", all(chr(0x2014) not in open(os.path.join(ROOT, f), encoding="utf-8").read()
                                                       for f in ("js/receiver.js", "tests/test_receiver_stall_retry.py")))

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
    settle(pg)
    # Unavailable watcher: the first time the message is visible after window.__uFrom.
    pg.evaluate("""()=>{window.__uFrom=0; window.__uAt=0; const tick=()=>{const u=document.querySelector('#unavailable');
      if(window.__uFrom && !window.__uAt && !u.hidden){window.__uAt=Date.now(); window.__uText=document.querySelector('#unavailable-msg').textContent;}
      requestAnimationFrame(tick)}; tick();}""")

    ids = pg.evaluate("async()=>(await import('./js/data.js')).CAMS.map(c=>c.id)")
    cur = pg.evaluate("()=>__receiver.state.camId")
    rec, dead, err = [i for i in ids if i != cur][:3]
    pg.evaluate("([r,d,e])=>{window.__ytStuck={[r]:{state:-1, loads:1}, [d]:{state:3, loads:99}};}", [rec, dead, err])

    # 1-3: stuck UNSTARTED on the first load, nudged before 12s, retried at 12s, plays in the retry window
    t0 = pg.evaluate("(id)=>{__receiver.send({type:'cam', id}); return Date.now();}", rec)
    pg.wait_for_function("(id)=>__receiver.log.some(e=>e.type==='loadNudge' && e.detail.camId===id)", arg=rec, timeout=9000)
    nudge = pg.evaluate("(id)=>__receiver.log.find(e=>e.type==='loadNudge' && e.detail.camId===id)", rec)
    print("nudge:", nudge)
    check(f"(1) nudged at about 4s, before the 12s cutoff ({nudge['t'] - t0} ms after the pick)", 4000 <= nudge["t"] - t0 < 12000)
    seq = pg.evaluate(CALLS, rec)
    check(f"(1) playVideo recorded on the fake player after the first load ({seq})", seq[:2] == ["loadVideoById", "playVideo"])
    check("(1) still pending, not failed, no message", not queued(pg, "cam_failed", rec) and pg.evaluate("()=>__receiver.state.camId") != rec
          and pg.evaluate("()=>document.querySelector('#unavailable').hidden"))
    shown = wait_for(pg, "cam_shown", rec, 12000) or {}
    print("recovered cam_shown:", shown)
    t_shown = pg.evaluate("()=>Date.now()")
    check(f"(2) the same id was reloaded once at the cutoff ({loads(pg, rec)} loads)", loads(pg, rec) == 2)
    retry = pg.evaluate("(id)=>__receiver.log.find(e=>e.type==='loadRetry' && e.detail.camId===id)", rec)
    check(f"(2) retry happened at CAM_LOAD_MS, not before ({retry and retry['t'] - t0} ms)", bool(retry) and 12000 <= retry["t"] - t0 < 15000)
    check("(3) cam_shown for the recovered cam, reason pick, retried=1 nudged=1",
          shown.get("reason") == "pick" and shown.get("retried") == 1 and shown.get("nudged") == 1 and isinstance(shown.get("load_ms"), int) and shown["load_ms"] >= 12000)
    check("(3) committed: it is the current cam and PLAYING", pg.evaluate("(id)=>__receiver.state.camId===id && __yt.cam().state===1", rec))
    check("(3) no cam_failed for it, not in failedCams, no message", not queued(pg, "cam_failed", rec)
          and not pg.evaluate("(id)=>__receiver.state.failedCams.includes(id)", rec) and pg.evaluate("()=>document.querySelector('#unavailable').hidden"))
    check(f"(3) recovered within the retry window ({t_shown - t0} ms)", t_shown - t0 < 26000)

    # 4: stuck BUFFERING across the retry: one retry, then load_timeout and the unavailable / skip path
    settle(pg)
    t0 = pg.evaluate("(id)=>{window.__uAt=0; window.__uFrom=Date.now(); __receiver.send({type:'cam', id}); return Date.now();}", dead)
    pg.wait_for_timeout(14000)
    check(f"(4) at 12s: reloaded once, not failed, no message ({loads(pg, dead)} loads)",
          loads(pg, dead) == 2 and not queued(pg, "cam_failed", dead) and pg.evaluate("()=>window.__uAt") == 0)
    f = wait_for(pg, "cam_failed", dead, 16000) or {}
    print("dead cam_failed:", f)
    check("(4) cam_failed why=load_timeout, trace_scope load, retried=1 nudged=1",
          f.get("why") == "load_timeout" and f.get("trace_scope") == "load" and f.get("retried") == 1 and f.get("nudged") == 1)
    check(f"(4) ms is total time across both windows ({f.get('ms')})", isinstance(f.get("ms"), int) and 24000 <= f["ms"] < 28000)
    check(f"(4) one states timeline, BUFFERING in both windows ({f.get('states')})",
          isinstance(f.get("states"), str) and len(re.findall(r"(?:^|,)3@", f["states"])) >= 2)
    check("(4) last_state BUFFERING, vid_match true", f.get("last_state") == 3 and f.get("vid_match") is True)
    check("(4) nudged in both windows", pg.evaluate("(id)=>__receiver.log.filter(e=>e.type==='loadNudge' && e.detail.camId===id).length", dead) == 2)
    u = pg.evaluate("()=>({at: window.__uAt, text: window.__uText})")
    check(f"(4) the unavailable message shows only after the retry window ({u['at'] - t0 if u['at'] else None} ms, {u['text']!r})",
          u["at"] and u["at"] - t0 >= 24000 and u["text"] == MSG)
    check("(4) failed and skipped: in failedCams, the next cam plays", pg.evaluate("(id)=>__receiver.state.failedCams.includes(id)", dead))
    settle(pg)
    check("(4) next cam is another one", pg.evaluate("()=>__receiver.state.camId") != dead)
    check(f"(4) only one retry, not two ({loads(pg, dead)} loads)", loads(pg, dead) == 2)
    check("(4) one cam_failed for it", len(queued(pg, "cam_failed", dead)) == 1)

    # 5: a real YouTube error (150) for the cam: fails at once, no nudge, no retry
    pg.evaluate("(id)=>{window.__ytErr={[id]:150};}", err)  # set only now, so the skip in (4) cannot hit it first
    t0 = pg.evaluate("(id)=>{__receiver.send({type:'cam', id}); return Date.now();}", err)
    f = wait_for(pg, "cam_failed", err, 4000) or {}
    t_fail = pg.evaluate("()=>Date.now()")
    pe = queued(pg, "player_error", err)
    print("error cam_failed:", f, pe)
    check(f"(5) player_error code 150 and cam_failed why=error at once ({t_fail - t0} ms)",
          f.get("why") == "error" and pe and pe[-1].get("code") == "150" and t_fail - t0 < 4000)
    check("(5) no retry or nudge on a real error", f.get("retried") == 0 and f.get("nudged") == 0)
    pg.wait_for_timeout(13000)
    check(f"(5) still one loadVideoById for it after the cutoff ({loads(pg, err)})", loads(pg, err) == 1)
    check("(5) no load nudge or retry logged for it",
          not pg.evaluate("(id)=>__receiver.log.some(e=>(e.type==='loadNudge' || e.type==='loadRetry') && e.detail.camId===id)", err))

    check("nothing left the page", not telemetry_guard.hits)
    b.close()

print("page errors:", logs)
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails or logs else 0)
