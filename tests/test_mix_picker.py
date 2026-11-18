"""Mix picker (sheet) and shuffle: Shuffle is on at every load (session only), a random mix plays from the beginning,
a mix that ends goes to another random one (never the same twice in a row), Shuffle off plays in sheet order, the
sheet follows v2 / Mix picker (opens at the top with Shuffle and the volume line, typographic rows, Playing stacked
over the duration) and lists every mix with exactly one Playing row, a tap switches the mix from 0, and while casting a tap only sends
the mix command (no local playback). The receiver starts every mix from 0 and honors the shuffle command.
Runs against the stubbed YouTube IFrame API (tests/fake_youtube.js) and the mock Cast sender.
Usage: test_mix_picker.py BASE_URL   (the site root, for example http://localhost:8000/)"""
import sys, os, re, json
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
here = os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(here)
FAKE_YT = open(os.path.join(here, "fake_youtube.js")).read()
MOCK = open(os.path.join(here, "mock_cast_sender.js")).read()
# Mix ids in sheet order, straight from the built-in list.
DATA = open(os.path.join(root, "js", "data.js"), encoding="utf-8").read()
MIX_IDS = re.findall(r'\{ id: "([^"]+)", title: "[^"]*", en: "[^"]*", minutes: \d+ \}', DATA.split("export const MIXES")[1])
N = len(MIX_IDS)
# Math.random with a test-controlled queue: values pushed to window.__randQ are used first, then the real one.
RAND = "(()=>{const r=Math.random.bind(Math); window.__randQ=window.__randQ||[]; Math.random=()=>window.__randQ.length?window.__randQ.shift():r();})();"
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
    pg.route("**/gviz/tq*", lambda r: r.abort())  # no sheet: the built-in list is used
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))


def new_page(b, init=None, **ctx):
    c = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True, **ctx)
    c.add_init_script(RAND)
    if init:
        c.add_init_script(init)
    pg = c.new_page()
    route(pg)
    return pg


def mix_id(pg):
    return pg.evaluate("()=>__windowSeat.currentMixId()")


def music(pg):
    return pg.evaluate("()=>{const m=__yt.music(); return m && {vid:m.vid, state:m.state, time:m.getCurrentTime()}}")


def music_loads(pg):
    """Every load or cue on every music player: [name, videoId, startSeconds]."""
    return pg.evaluate("""()=>__yt.players.filter(p=>p.kind==='music').flatMap(p=>p.calls)
        .filter(c=>c[0]==='loadVideoById'||c[0]==='cueVideoById')
        .map(c=>[c[0], typeof c[1]==='object'?c[1].videoId:c[1], typeof c[1]==='object'?c[1].startSeconds:null])""")


def start(pg):
    wait_until(pg, "()=>window.__windowSeat && window.__yt && __yt.music()", 8000)
    pg.click("#start-btn")
    ok = wait_until(pg, "()=>__windowSeat.currentCamId() && __yt.music() && __yt.music().state===1", 15000)
    if not ok:
        print("  start diagnostics:", pg.evaluate("""()=>({started:__windowSeat.state.started, api:__windowSeat.state.apiReady,
            cam:__windowSeat.currentCamId(), pending:JSON.stringify(__windowSeat.state.pending), music:__yt.music()&&__yt.music().state,
            mcalls:__yt.music()&&__yt.music().calls.map(c=>c[0]).join(',')})"""))
    return ok


def open_mix(pg, opener="#bar [data-action=open-music]"):
    pg.click(opener)
    pg.wait_for_selector("#panel-music:not([hidden])")


def row(mid):
    return f'#mix-list .mix-btn[data-id="{mid}"]'


def end_mix(pg):
    """The playing mix reaches its end (ENDED); returns the next mix once it plays."""
    before = mix_id(pg)
    pg.evaluate("()=>__yt.music()._set(0)")
    wait_until(pg, "(m)=>__windowSeat.currentMixId()!==m && __yt.music().state===1", 3000, before)
    return mix_id(pg)


with sync_playwright() as p:
    b = p.chromium.launch()
    check(f"setup: {N} mixes in the built-in list", N == 16)

    # ------------------------------------------------------------ Shuffle is on at every load, never stored
    pg = new_page(b)
    pg.goto(base + "/")
    pg.wait_for_timeout(800)
    check("shuffle: on by default on the first load", pg.evaluate("()=>__windowSeat.shuffle()") is True)
    check("shuffle: started", start(pg))
    open_mix(pg)
    check("shuffle: switch reads on", pg.get_attribute("#music-shuffle", "aria-pressed") == "true")
    pg.click("#music-shuffle")
    check("shuffle: switch turns off for this session", pg.evaluate("()=>__windowSeat.shuffle()") is False and pg.get_attribute("#music-shuffle", "aria-pressed") == "false")
    check("shuffle: not written to localStorage", pg.evaluate("()=>Object.keys(localStorage).filter(k=>/shuffle/i.test(k)).length") == 0)
    pg.reload()
    pg.wait_for_timeout(800)
    check("shuffle: on again after reload", pg.evaluate("()=>__windowSeat.shuffle()") is True)
    check("shuffle: still on after reload with sessionStorage intact", pg.evaluate("()=>Object.keys(sessionStorage).filter(k=>/shuffle/i.test(k)).length") == 0)
    start(pg)
    open_mix(pg)
    check("shuffle: switch reads on after reload", pg.get_attribute("#music-shuffle", "aria-pressed") == "true")
    pg.context.close()

    # An older build stored shuffle off in localStorage: it no longer matters (and is removed).
    pg = new_page(b, init="try{localStorage.setItem('windowSeat.shuffle','false')}catch(e){}")
    pg.goto(base + "/")
    pg.wait_for_timeout(800)
    check("shuffle: an old stored 'off' is ignored", pg.evaluate("()=>__windowSeat.shuffle()") is True)
    pg.context.close()

    # ------------------------------------------------------------ random start mix, from 0
    starts = []
    for r, label in ((0.3, "a"), (0.8, "b")):
        pg = new_page(b, init=f"window.__randQ=[{r}];")
        pg.goto(base + "/")
        pg.wait_for_timeout(500)
        expect = MIX_IDS[int(r * N)]
        check(f"start {label}: the random pick is the start mix (random {r} -> index {int(r * N)})", mix_id(pg) == expect)
        check(f"start {label}: started", start(pg))
        m = music(pg)
        starts.append(mix_id(pg))
        check(f"start {label}: the music player plays that mix", m["vid"] == mix_id(pg) and m["state"] == 1)
        check(f"start {label}: from the beginning (time 0, no seek)", m["time"] == 0 and pg.evaluate("()=>!__yt.music().calls.some(c=>c[0]==='seekTo')"))
        check(f"start {label}: player created at the mix, not partway", pg.evaluate("()=>{const o=__yt.music().opts; return !(o.playerVars&&o.playerVars.start>0)}"))
        pg.context.close()
    check(f"start: different random values give different start mixes ({starts})", len(set(starts)) == 2 and MIX_IDS[0] not in starts)

    # Unseeded loads still vary (not stuck on the first mix or the last one played).
    pg = new_page(b)
    seen = set()
    for _ in range(5):
        pg.goto(base + "/")
        pg.wait_for_timeout(300)
        seen.add(mix_id(pg))
    check(f"start: unseeded loads pick varied mixes ({len(seen)} distinct of 5)", len(seen) >= 2)
    pg.context.close()

    # ------------------------------------------------------------ the sheet
    pg = new_page(b)
    pg.goto(base + "/")
    check("sheet: started", start(pg))
    open_mix(pg)
    check("sheet: header reads Mix", pg.text_content("#music-title").strip() == "Mix")
    check("sheet: subhead reads Tokyo Chill Lab", pg.text_content("#music-sub").strip() == "Tokyo Chill Lab")
    check("sheet: Shuffle switch present and on", pg.is_visible("#music-shuffle") and "Shuffle" in pg.text_content("#music-shuffle") and pg.get_attribute("#music-shuffle", "aria-pressed") == "true")
    check("sheet: music volume slider in the sheet", pg.is_visible("#panel-music input[type=range]") and pg.get_attribute("#mix-volume", "aria-label") == "Music volume")
    check("sheet: helper 'Cam sound is always off.'", pg.text_content("#mix-volume-help").strip() == "Cam sound is always off.")
    check("sheet: Shuffle caption is the v2 line", pg.text_content("#shuffle-hint").strip() == "On for every session, so it starts somewhere new." and pg.get_attribute("#music-shuffle", "aria-describedby") == "shuffle-hint")
    check("sheet: the old long shuffle hint is gone", "Off, mixes play in list order" not in pg.text_content("#panel-music"))
    check("sheet: volume line shows the numeric value", pg.text_content("#mix-volume-out").strip() == pg.evaluate("()=>document.querySelector('#mix-volume').value"))
    # The sheet opens at the top: Shuffle and the volume line are on screen, focus is on Done, not the Playing row.
    top = pg.evaluate("""()=>{const body=document.querySelector('#panel-music .panel-body'), br=body.getBoundingClientRect();
        const inView=(s)=>{const r=document.querySelector(s).getBoundingClientRect(); return r.top>=br.top-1 && r.bottom<=br.bottom+1};
        return {scroll: body.scrollTop, shuffle: inView('#music-shuffle'), volume: inView('#mix-volume'),
            done: document.activeElement === document.querySelector('#panel-music .btn-done')}}""")
    check(f"sheet: opens at the top with Shuffle and volume visible ({top})", top["scroll"] == 0 and top["shuffle"] and top["volume"] and top["done"])
    # Head of the sheet follows the artboard: Shuffle, then the volume line, then the quiet Mute / Pause / Next mix row.
    order = pg.evaluate("""()=>['#music-shuffle','#mix-volume','#mix-volume-help','#music-mute','#music-play','#music-next','#mix-list']
        .map(s=>document.querySelector(s).getBoundingClientRect().top)""")
    check(f"sheet: Shuffle, volume, caption, then Mute / Pause / Next mix, then the list ({order})", order[0] < order[1] < order[2] < order[3] and order[3] == order[4] == order[5] and order[5] < order[6])
    check("sheet: Mute, Pause, Next mix stay visible", all(pg.is_visible(s) for s in ("#music-mute", "#music-play", "#music-next")))
    css = pg.evaluate("""()=>{const g=(s)=>getComputedStyle(document.querySelector(s)); const t=g('#music-title'), d=g('#panel-music .btn-done'),
        sub=g('#music-sub'), tr=g('#music-shuffle .switch-track'), cap=g('#shuffle-hint'), help=g('#mix-volume-help'), out=g('#mix-volume-out');
        return {title:[t.fontSize,t.lineHeight,t.fontWeight,t.fontFamily.split(',')[0]], sub:[sub.fontSize,sub.lineHeight,sub.fontWeight],
            done:[d.fontSize,d.fontWeight,d.color], toggle:[tr.width,tr.height], cap:[cap.fontSize,cap.lineHeight,cap.color],
            help:[help.fontSize,help.lineHeight,help.color], out:[out.fontSize,out.lineHeight,out.color]}}""")
    check(f"sheet: title Jakarta 22/28 600 ({css['title']})", css["title"][:3] == ["22px", "28px", "600"] and "Fraunces" not in css["title"][3])
    check(f"sheet: subtitle 14/20 500 ({css['sub']})", css["sub"] == ["14px", "20px", "500"])
    check(f"sheet: Done 16 600 accent ({css['done']})", css["done"] == ["16px", "600", "rgb(47, 111, 138)"])
    check(f"sheet: Shuffle toggle 44x26 ({css['toggle']})", css["toggle"] == ["44px", "26px"])
    check(f"sheet: Shuffle caption 12/16 ink-secondary ({css['cap']})", css["cap"][:2] == ["12px", "16px"])
    check(f"sheet: volume caption 12/16 ink-tertiary ({css['help']})", css["help"] == ["12px", "16px", "rgb(90, 107, 118)"])
    check(f"sheet: volume value 14/20 ({css['out']})", css["out"][:2] == ["14px", "20px"])
    rows = pg.evaluate("""()=>[...document.querySelectorAll('#mix-list .mix-btn')].map(b=>({id:b.dataset.id,
        en:(b.querySelector('.mix-en')||{}).textContent, ja:(b.querySelector('.mix-ja')||{}).textContent,
        jaLang:(b.querySelector('.mix-ja')||{}).lang, len:(b.querySelector('.mix-len')||{}).textContent}))""")
    check(f"sheet: lists all {N} mixes in sheet order", [r["id"] for r in rows] == MIX_IDS)
    check("sheet: every row has English and Japanese titles", all(r["en"] and r["ja"] and r["jaLang"] == "ja" for r in rows))
    check("sheet: durations are trailing minutes ('141 min')", all(re.fullmatch(r"\d+ min", r["len"] or "") for r in rows) and rows[0]["len"] == "141 min")

    def playing_rows():
        return pg.evaluate("""()=>({cur:[...document.querySelectorAll('#mix-list .mix-btn[aria-current]')].map(b=>b.dataset.id),
            marks:[...document.querySelectorAll('#mix-list .mix-playing')].filter(m=>!m.hidden && m.offsetParent).map(m=>m.closest('.mix-btn').dataset.id),
            soft:[...document.querySelectorAll('#mix-list .mix-btn.is-current')].map(b=>getComputedStyle(b).backgroundColor)})""")

    pr = playing_rows()
    check(f"sheet: exactly one Playing row, the current mix ({pr['cur']})", pr["cur"] == [mix_id(pg)] and pr["marks"] == [mix_id(pg)])
    check("sheet: Playing mark reads Playing", pg.text_content(row(mix_id(pg)) + " .mix-playing").strip() == "Playing")
    check(f"sheet: playing row uses accent-soft ({pr['soft']})", pr["soft"] == ["rgb(228, 240, 245)"])
    rs = pg.evaluate("""(cur)=>{const g=(e)=>getComputedStyle(e); const rows=[...document.querySelectorAll('#mix-list .mix-btn')];
        const p=rows.find(b=>b.dataset.id===cur), o=rows.find(b=>b.dataset.id!==cur);
        const mk=p.querySelector('.mix-playing'), ln=p.querySelector('.mix-len');
        const en=g(o.querySelector('.mix-en')), ja=g(o.querySelector('.mix-ja')), len=g(o.querySelector('.mix-len')), m=g(mk);
        return {play:[g(p).borderTopLeftRadius,g(p).paddingTop,g(p).paddingLeft],
            other:[g(o).backgroundColor,g(o).borderTopWidth,g(o).boxShadow],
            en:[en.fontSize,en.lineHeight,en.fontWeight], ja:[ja.fontSize,ja.lineHeight,ja.fontWeight,ja.fontFamily.split(',')[0]],
            len:[len.fontSize,len.lineHeight,len.color], mark:[m.fontSize,m.lineHeight,m.fontWeight,m.color,m.backgroundColor],
            stacked: mk.getBoundingClientRect().bottom <= ln.getBoundingClientRect().top + 1}}""", mix_id(pg))
    check(f"rows: Playing row radius 16, padding 12/8 ({rs['play']})", rs["play"] == ["16px", "12px", "8px"])
    check(f"rows: other rows are typographic, no card fill, border or shadow ({rs['other']})", rs["other"] == ["rgba(0, 0, 0, 0)", "0px", "none"])
    check(f"rows: English 15/20 600 ({rs['en']})", rs["en"] == ["15px", "20px", "600"])
    check(f"rows: Japanese Noto Sans JP 13/18 500 ({rs['ja']})", rs["ja"][:3] == ["13px", "18px", "500"] and "Noto Sans JP" in rs["ja"][3])
    check(f"rows: duration 12/16 ink-tertiary ({rs['len']})", rs["len"] == ["12px", "16px", "rgb(90, 107, 118)"])
    check(f"rows: Playing 12/16 600 accent, no pill ({rs['mark']})", rs["mark"] == ["12px", "16px", "600", "rgb(47, 111, 138)", "rgba(0, 0, 0, 0)"])
    check("rows: Playing sits stacked above the duration", rs["stacked"])

    # Tap a row: that mix, now, from the beginning.
    cur = mix_id(pg)
    target = next(m for m in MIX_IDS if m != cur)
    pg.evaluate("()=>{__yt.music().time=1234}")
    n_loads = len(music_loads(pg))
    pg.click(row(target))
    check("tap: the mix switches immediately", wait_until(pg, "(t)=>__windowSeat.currentMixId()===t && __yt.music().vid===t && __yt.music().state===1", 3000, target))
    loads = music_loads(pg)[n_loads:]
    check(f"tap: one load with startSeconds 0 ({loads})", loads == [["loadVideoById", target, 0]])
    check("tap: playhead at 0", music(pg)["time"] == 0)
    pr = playing_rows()
    check("tap: the Playing row moves to the tapped mix", pr["cur"] == [target] and pr["marks"] == [target])
    # Tapping the playing row starts it over from the beginning.
    pg.evaluate("()=>{__yt.music().time=600}")
    n_loads = len(music_loads(pg))
    pg.click(row(target))
    pg.wait_for_timeout(300)
    check("tap: the playing row restarts from 0", music_loads(pg)[n_loads:] == [["loadVideoById", target, 0]] and music(pg)["time"] == 0)

    # Sheet slider sets the music volume (the cam stays silent).
    pg.evaluate("()=>{const r=document.querySelector('#mix-volume'); r.value='35'; r.dispatchEvent(new Event('input',{bubbles:true}))}")
    pg.wait_for_timeout(150)
    check("volume: sheet slider sets the music volume", pg.evaluate("()=>__yt.music().getVolume()") == 35 and pg.evaluate("()=>__windowSeat.state.volume") == 35)
    check("volume: the player slider follows", pg.evaluate("()=>document.querySelector('#music-volume').value") == "35")
    check("volume: cam still muted", pg.evaluate("()=>__yt.cam().isMuted() && __yt.cam().getVolume()===0"))

    # ------------------------------------------------------------ shuffle on: when a mix ends
    seq = [mix_id(pg)]
    for _ in range(12):
        seq.append(end_mix(pg))
    check(f"ended: shuffle plays another mix every time ({len(seq)} mixes)", all(a != b for a, b in zip(seq, seq[1:])))
    check("ended: shuffle is random, not sheet order", any(MIX_IDS.index(b) != (MIX_IDS.index(a) + 1) % N for a, b in zip(seq, seq[1:])))
    loads = music_loads(pg)[-12:]
    check("ended: every shuffled mix starts at 0", all(l[0] == "loadVideoById" and l[2] == 0 for l in loads))
    # Force the random pick onto the playing mix: it must still move to a different one.
    cur = mix_id(pg)
    i = MIX_IDS.index(cur)
    pg.evaluate(f"()=>{{window.__randQ=[{(i + 0.5) / N},{(i + 0.5) / N},{(i + 0.5) / N}]}}")
    nxt = end_mix(pg)
    check(f"ended: never the same mix twice in a row, even when the dice say so ({cur} -> {nxt})", nxt != cur and nxt in MIX_IDS)

    # ------------------------------------------------------------ shuffle off: sheet order from the current one
    pg.click("#music-shuffle")
    check("shuffle off: switch off", pg.evaluate("()=>__windowSeat.shuffle()") is False and pg.get_attribute("#music-shuffle", "aria-pressed") == "false")
    cur = mix_id(pg)
    i = MIX_IDS.index(cur)
    got = [end_mix(pg) for _ in range(3)]
    check(f"shuffle off: next mixes follow the sheet ({i} -> {[MIX_IDS.index(g) for g in got]})", got == [MIX_IDS[(i + k) % N] for k in (1, 2, 3)])
    pg.click(row(MIX_IDS[-1]))
    wait_until(pg, f"()=>__windowSeat.currentMixId()==='{MIX_IDS[-1]}' && __yt.music().state===1", 3000)
    check("shuffle off: after the last mix comes the first", end_mix(pg) == MIX_IDS[0])
    check("shuffle off: from 0", music_loads(pg)[-1] == ["loadVideoById", MIX_IDS[0], 0] and music(pg)["time"] == 0)
    pg.click("#music-shuffle")
    check("shuffle: back on", pg.evaluate("()=>__windowSeat.shuffle()") is True)

    # Home and Start again: shuffle picks a fresh mix and plays it from the beginning.
    before = mix_id(pg)
    pg.click("#panel-music [data-action=close-panel]")
    pg.evaluate("()=>document.querySelector('#lp-home').click()")
    pg.wait_for_selector("#start-btn", state="visible")
    check("home: a new random mix for the next start", mix_id(pg) != before)
    check("home: started again", start(pg))
    check("home: the new mix plays from 0", music(pg)["vid"] == mix_id(pg) and music(pg)["time"] == 0)
    pg.context.close()

    # ------------------------------------------------------------ casting: the phone is a remote
    pg = new_page(b)
    pg.goto(base + "/")
    pg.wait_for_timeout(1200)
    phone_mix = mix_id(pg)
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(500)
    pg.evaluate("()=>__mockCast.reply({type:'state',configured:false})")  # probe reply: a fresh TV gets the phone's choices
    pg.wait_for_timeout(200)
    sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    types = [m["type"] for m in sent]
    sh = [m for m in sent if m["type"] == "shuffle"]
    check(f"cast: session start syncs shuffle on ({sh})", len(sh) == 1 and sh[0]["value"] is True)
    check("cast: shuffle goes before the mix in the start sync", "mix" in types and types.index("shuffle") < types.index("mix"))
    check("cast: the start sync sends the phone's mix", any(m["type"] == "mix" and m["id"] == phone_mix for m in sent))
    check("cast: remote is up, no local YouTube", pg.is_visible("#remote") and pg.evaluate("()=>document.querySelectorAll('iframe[src*=youtube]').length") == 0)
    n_players = pg.evaluate("()=>__yt.players.length")
    loads_before = len(music_loads(pg))
    open_mix(pg, "#remote [data-action=open-music]")
    check("cast: the Mix sheet opens over the remote", pg.is_visible("#panel-music") and pg.evaluate("()=>getComputedStyle(document.querySelector('#panel-music')).zIndex") == "55")

    # The TV reports a mix: the Playing row follows the TV, not the phone.
    tv_mix = next(m for m in MIX_IDS if m != phone_mix)
    pg.evaluate(f"()=>__mockCast.reply({{type:'state',camId:null,mixId:'{tv_mix}',mixTitle:'X',pinned:false,interval:60,volume:45,muted:false,paused:false,shuffle:true}})")
    pg.wait_for_timeout(150)
    pr = playing_rows()
    check(f"cast: the Playing row is the TV's mix ({pr['cur']})", pr["cur"] == [tv_mix] and pr["marks"] == [tv_mix])
    check("cast: the sheet slider shows the TV's volume", pg.evaluate("()=>document.querySelector('#mix-volume').value") == "45")

    # The TV pauses with the sheet open: the sheet's Play/Pause follows the TV, not the phone's own feed state.
    check("cast: sheet reads Pause while the TV plays", pg.text_content("#music-play").strip() == "Pause")
    pg.evaluate(f"()=>__mockCast.reply({{type:'state',mixId:'{tv_mix}',mixTitle:'X',volume:45,paused:true,shuffle:true}})")
    pg.wait_for_timeout(150)
    check("cast: TV paused, sheet reads Play", pg.text_content("#music-play").strip() == "Play")
    check("cast: the phone's own feed stays unpaused", pg.evaluate("()=>__windowSeat.feedPaused()") is False)
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click("#music-play")
    pg.wait_for_timeout(150)
    sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"cast: Play on a paused TV sends pause false ({sent})", [m for m in sent if m["type"] == "pause"] == [{"type": "pause", "value": False}])
    check("cast: sheet reads Pause right after the tap", pg.text_content("#music-play").strip() == "Pause")
    pg.evaluate(f"()=>__mockCast.reply({{type:'state',mixId:'{tv_mix}',mixTitle:'X',volume:45,paused:true}})")
    pg.wait_for_timeout(150)
    check("cast: a TV paused reply turns it back to Play", pg.text_content("#music-play").strip() == "Play")
    pg.evaluate(f"()=>__mockCast.reply({{type:'state',mixId:'{tv_mix}',mixTitle:'X',volume:45,paused:false}})")
    pg.wait_for_timeout(150)
    check("cast: TV playing again, sheet reads Pause", pg.text_content("#music-play").strip() == "Pause")

    # Another sender turns shuffle off on the TV: the switch follows, and a tap turns it on (not a resend of off).
    pg.evaluate(f"()=>__mockCast.reply({{type:'state',mixId:'{tv_mix}',mixTitle:'X',volume:45,paused:false,shuffle:false}})")
    pg.wait_for_timeout(150)
    check("cast: TV reports shuffle off, switch reads off", pg.get_attribute("#music-shuffle", "aria-pressed") == "false")
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click("#music-shuffle")
    pg.wait_for_timeout(150)
    sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"cast: tap after a TV shuffle off sends shuffle true ({sent})", [m["value"] for m in sent if m["type"] == "shuffle"] == [True])
    check("cast: switch reads on after the tap", pg.get_attribute("#music-shuffle", "aria-pressed") == "true")
    # And the other way: TV reports on while the phone's own setting is off.
    pg.click("#music-shuffle")
    pg.wait_for_timeout(150)
    check("cast: phone switched shuffle off", pg.evaluate("()=>__windowSeat.shuffle()") is False)
    pg.evaluate(f"()=>__mockCast.reply({{type:'state',mixId:'{tv_mix}',mixTitle:'X',volume:45,paused:false,shuffle:true}})")
    pg.wait_for_timeout(150)
    check("cast: TV reports shuffle on, switch reads on", pg.get_attribute("#music-shuffle", "aria-pressed") == "true")
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click("#music-shuffle")
    pg.wait_for_timeout(150)
    sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"cast: tap after a TV shuffle on sends shuffle false ({sent})", [m["value"] for m in sent if m["type"] == "shuffle"] == [False])
    pg.evaluate(f"()=>__mockCast.reply({{type:'state',mixId:'{tv_mix}',mixTitle:'X',volume:45,paused:false,shuffle:true}})")
    pg.wait_for_timeout(150)

    # Tap a row: only the mix command goes to the TV.
    target = next(m for m in MIX_IDS if m not in (phone_mix, tv_mix))
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click(row(target))
    pg.wait_for_timeout(200)
    sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"cast: tap sends the mix command ({sent})", sent == [{"type": "mix", "id": target}])
    check("cast: no local playback (no new player, no load, no iframe)", pg.evaluate("()=>__yt.players.length") == n_players and len(music_loads(pg)) == loads_before and pg.evaluate("()=>document.querySelectorAll('iframe[src*=youtube]').length") == 0)
    check("cast: still casting, remote still up", pg.evaluate("()=>__windowSeat.isCasting()") is True and pg.is_visible("#remote"))
    check("cast: the phone's own mix is untouched", mix_id(pg) == phone_mix)
    check("cast: Playing row moves to the tapped mix right away", playing_rows()["cur"] == [target])
    pg.evaluate(f"()=>__mockCast.reply({{type:'state',mixId:'{tv_mix}',mixTitle:'X',volume:45,paused:false}})")
    pg.wait_for_timeout(150)
    check("cast: a TV state reply corrects the Playing row", playing_rows()["cur"] == [tv_mix])

    # Shuffle switch while casting goes to the TV.
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click("#music-shuffle")
    pg.wait_for_timeout(150)
    sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"cast: Shuffle off sends shuffle false ({sent})", [m for m in sent if m["type"] == "shuffle"] == [{"type": "shuffle", "value": False, "silent": True}])
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click("#music-shuffle")
    pg.wait_for_timeout(150)
    sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check("cast: Shuffle on sends shuffle true", [m["value"] for m in sent if m["type"] == "shuffle"] == [True])

    # Sheet slider while casting: one absolute volume on release, nothing local.
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.evaluate("()=>{const r=document.querySelector('#mix-volume'); r.value='70'; r.dispatchEvent(new Event('input',{bubbles:true})); r.dispatchEvent(new Event('change',{bubbles:true}))}")
    pg.wait_for_timeout(150)
    sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"cast: sheet slider sends volume 70 ({sent})", sent == [{"type": "volume", "value": 70}])

    # The Mix sheet's "Next mix" goes to the TV too (the TV picks it with its shuffle).
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click("#music-next")
    pg.wait_for_timeout(150)
    check("cast: Next mix sends nextMix only", [json.loads(m["data"])["type"] for m in pg.evaluate("()=>__mockCast.sent")] == ["nextMix"])
    pg.click("#panel-music [data-action=close-panel]")
    check("cast: still no local playback", len(music_loads(pg)) == loads_before and pg.evaluate("()=>__yt.players.length") == n_players)
    pg.context.close()

    # A resumed session gets the shuffle setting too (without overriding the mix the TV plays).
    pg = new_page(b, init="window.__mockResume=true;")
    pg.goto(base + "/")
    pg.wait_for_timeout(1500)
    sent = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"cast resume: shuffle sent, no mix override ({[m['type'] for m in sent]})", any(m["type"] == "shuffle" and m["value"] is True for m in sent) and not any(m["type"] == "mix" for m in sent))
    pg.context.close()

    # ------------------------------------------------------------ the TV receiver
    rc = b.new_context(viewport={"width": 1920, "height": 1080})
    rc.add_init_script(RAND)
    rx = rc.new_page()
    route(rx)
    rx.goto(base + "/receiver.html?debug=1")
    check("tv: music playing", wait_until(rx, "()=>window.__receiver && __yt.music() && __yt.music().state===1", 8000))
    st = lambda: rx.evaluate("()=>__receiver.state")
    send = lambda obj: rx.evaluate("(o)=>__receiver.send(o)", obj)
    rx_mix = lambda: st()["mixId"]
    rx_loads = lambda: rx.evaluate("""()=>__yt.music().calls.filter(c=>c[0]==='loadVideoById').map(c=>[typeof c[1]==='object'?c[1].videoId:c[1], typeof c[1]==='object'?c[1].startSeconds:null])""")
    check("tv: shuffle on by default and reported in state", st().get("shuffle") is True)

    def rx_end():
        before = rx_mix()
        rx.evaluate("()=>__yt.music()._set(0)")
        wait_until(rx, "(m)=>__receiver.state.mixId!==m && __yt.music().state===1", 3000, before)
        return rx_mix()

    # mix command: that mix from 0.
    target = next(m for m in MIX_IDS if m != rx_mix())
    rx.evaluate("()=>{__yt.music().time=900}")
    send({"type": "mix", "id": target})
    check("tv: mix command switches the mix", wait_until(rx, "(t)=>__receiver.state.mixId===t && __yt.music().vid===t && __yt.music().state===1", 3000, target))
    check(f"tv: mix command loads from 0 ({rx_loads()[-1]})", rx_loads()[-1] == [target, 0] and rx.evaluate("()=>__yt.music().getCurrentTime()") == 0)

    # Shuffle on: ended -> random other mix, from 0.
    seq = [rx_mix()]
    for _ in range(10):
        seq.append(rx_end())
    check("tv: shuffle never repeats a mix on end", all(a != b for a, b in zip(seq, seq[1:])))
    check("tv: shuffle is random, not sheet order", any(MIX_IDS.index(b) != (MIX_IDS.index(a) + 1) % N for a, b in zip(seq, seq[1:])))
    check("tv: every shuffled mix starts at 0", all(l[1] == 0 for l in rx_loads()[-10:]))
    cur = rx_mix()
    i = MIX_IDS.index(cur)
    rx.evaluate(f"()=>{{window.__randQ=[{(i + 0.5) / N},{(i + 0.5) / N}]}}")
    check("tv: never the same mix twice in a row, even when the dice say so", rx_end() != cur)

    # Shuffle off: sheet order.
    send({"type": "shuffle", "value": False, "silent": True})
    check("tv: shuffle false is stored and reported", st()["shuffle"] is False)
    cur = rx_mix()
    i = MIX_IDS.index(cur)
    got = [rx_end() for _ in range(2)]
    check(f"tv: shuffle off plays the sheet order ({[MIX_IDS.index(g) for g in got]})", got == [MIX_IDS[(i + 1) % N], MIX_IDS[(i + 2) % N]])
    send({"type": "shuffle", "value": True})
    check("tv: shuffle true again", st()["shuffle"] is True)
    rc.close()

    b.close()

for line in logs:
    print(line)
check("no page errors", not any(l.startswith("PAGEERROR") for l in logs))
print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
