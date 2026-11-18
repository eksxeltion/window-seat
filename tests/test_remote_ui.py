"""Every way into remote mode shows the v2 remote: casting pill, cam still, large title, place and time, pin chip,
large transport, music line, Cams and Mix above the fold, footer, no old card or inline lists, and the sheets with
their own controls (mock CAF). Usage: test_remote_ui.py PHONE_URL"""
import sys, os, re
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts
url = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
MOCK = open(os.path.join(here, "mock_cast_sender.js")).read()
fails, logs = [], []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

IN_VIEW = """(sel)=>{const e=document.querySelector(sel); if(!e) return false; const r=e.getBoundingClientRect();
  return r.width>0 && r.height>0 && r.top>=0 && r.bottom<=window.innerHeight && r.left>=0 && r.right<=window.innerWidth}"""

BOX = "(sel)=>{const e=document.querySelector(sel); if(!e) return null; const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height,top:r.top,bottom:r.bottom}}"
near = lambda a, b, tol=2: abs(a - b) <= tol
FOOT = "Phone is a remote. Video and music play on the TV."

def assert_current_remote(pg, tag, timed_cam=False):
    check(f"{tag}: #remote visible, body.casting on", pg.is_visible("#remote") and pg.evaluate("()=>document.body.classList.contains('casting')"))
    check(f"{tag}: casting pill with #remote-device", pg.is_visible("#remote .remote-pill") and pg.locator("#remote .remote-pill #remote-device").count() == 1
          and pg.text_content("#remote .remote-pill").startswith("Casting to"))
    still = pg.evaluate(BOX, "#remote .remote-still")
    check(f"{tag}: 160x90 cam still ({still})", still is not None and near(still["w"], 160) and near(still["h"], 90))
    check(f"{tag}: #remote-cam is the visible title, not in a label card",
          pg.is_visible("#remote-cam") and pg.locator("#remote .remote-card, #remote .remote-label").count() == 0
          and pg.evaluate("()=>parseFloat(getComputedStyle(document.querySelector('#remote-cam')).fontSize)") == 22)
    if timed_cam:
        meta = pg.text_content("#remote-meta")
        check(f"{tag}: place and local time under the title ({meta!r})", pg.is_visible("#remote-meta") and re.fullmatch(r".+ · \d{1,2}:\d\d [AP]M", meta or "") is not None)
    for a, label in (("open-cams", "Cams"), ("open-music", "Mix")):
        sel = f'#remote [data-action="{a}"]'
        check(f"{tag}: {a} visible, above the fold, labeled {label}", pg.is_visible(sel) and pg.evaluate(IN_VIEW, sel) and pg.text_content(sel).strip() == label)
    check(f"{tag}: no old inline lists", pg.locator("#remote-mixes").count() == 0 and pg.locator("#remote-cams").count() == 0)
    check(f"{tag}: no old interval or volume rows on the remote",
          pg.locator('#remote [data-cmd="i30"], #remote [data-cmd="i60"], #remote [data-cmd="vol-up"], #remote [data-cmd="vol-down"], #remote-vol, #remote-mute').count() == 0)
    for sel in ("#remote-cam", "#remote-play", "#remote-pin", '#remote [data-cmd="prev"]', '#remote [data-cmd="next"]', '#remote [data-cmd="stop"]', "#remote-volume"):
        check(f"{tag}: {sel} present", pg.is_visible(sel))
    check(f"{tag}: #remote-mix in the DOM", pg.locator("#remote-mix").count() == 1)
    check(f"{tag}: #remote-status and #remote-debug present", pg.locator("#remote-status").count() == 1 and pg.locator("#remote-debug").count() == 1)
    check(f"{tag}: Stop casting present", pg.text_content('#remote [data-cmd="stop"]').strip() == "Stop casting")
    check(f"{tag}: footer", pg.is_visible("#remote .remote-foot") and pg.text_content("#remote .remote-foot") == FOOT)
    # Transport: prev, a 72px filled circle, next, in one row; the pin chip is not in that row.
    play = pg.evaluate(BOX, "#remote-play")
    look = pg.evaluate("()=>{const cs=getComputedStyle(document.querySelector('#remote-play')); return {bg:cs.backgroundColor, r:cs.borderTopLeftRadius}}")
    check(f"{tag}: play is a 72px filled circle ({play}, {look})", near(play["w"], 72) and near(play["h"], 72) and look["bg"] == "rgb(16, 37, 54)" and look["r"] in ("50%", "36px"))
    for sel in ('#remote [data-cmd="prev"]', '#remote [data-cmd="next"]'):
        r = pg.evaluate(BOX, sel)
        check(f"{tag}: {sel} on the play row, icon only", abs((r["y"] + r["h"] / 2) - (play["y"] + play["h"] / 2)) < 4 and pg.evaluate("(s)=>[...document.querySelector(s).children].every(c=>c.tagName.toLowerCase()==='svg'||c.classList.contains('visually-hidden'))", sel))
    pin = pg.evaluate(BOX, "#remote-pin")
    check(f"{tag}: pin chip is not in the transport row ({pin['y']} vs {play['y']})", pin["bottom"] <= play["top"] and pg.evaluate("()=>!document.querySelector('#remote-pin').closest('.remote-transport')"))
    check(f"{tag}: pin chip is 36px ({pin['h']})", near(pin["h"], 36))
    check(f"{tag}: no visible 'Prev cam' text", "Prev cam" not in pg.inner_text("#remote"))
    # Cams sheet with its extra controls
    pg.locator('#remote [data-action="open-cams"]').tap(); pg.wait_for_timeout(300)
    check(f"{tag}: Cams sheet open", pg.is_visible("#panel-cams"))
    check(f"{tag}: Cams sheet has skip sleeping, only daytime, interval",
          pg.is_visible("#cams-skip-sleeping") and pg.is_visible("#cams-only-daytime") and pg.locator("#cams-interval").count() == 1)
    check(f"{tag}: Cams sheet has cam checkboxes and play controls",
          pg.locator('#cams-list [data-action="toggle-cam"]').count() > 0 and pg.locator('#cams-list [data-action="watch"]').count() > 0)
    before = pg.text_content("#cams-interval")
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.locator("#cams-interval").tap(); pg.wait_for_timeout(200)
    sent = [m["data"] for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"{tag}: Cams sheet interval goes to the TV and its label follows ({before!r} -> {pg.text_content('#cams-interval')!r})",
          any('"interval"' in d for d in sent) and pg.text_content("#cams-interval") != before)
    pg.locator('#panel-cams [data-action="close-panel"]').tap(); pg.wait_for_timeout(300)
    # Mix sheet with its extra controls
    pg.locator('#remote [data-action="open-music"]').tap(); pg.wait_for_timeout(300)
    check(f"{tag}: Mix sheet open", pg.is_visible("#panel-music"))
    check(f"{tag}: Mix sheet has shuffle, mute, volume, next mix",
          pg.is_visible("#music-shuffle") and pg.is_visible("#music-mute") and pg.is_visible("#mix-volume") and pg.is_visible("#music-next"))
    check(f"{tag}: Mix sheet lists the mixes", pg.locator("#mix-list .mix-btn").count() > 0)
    pg.locator('#panel-music [data-action="close-panel"]').tap(); pg.wait_for_timeout(300)
    check(f"{tag}: back on the remote", pg.is_visible("#remote") and not pg.is_visible("#panel-music"))

with sync_playwright() as p:
    b = p.chromium.launch()
    def page(init=None, wait=2500):
        pg = b.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True).new_page()
        if init: pg.add_init_script(init)
        pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
        pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
        pg.goto(url); pg.wait_for_timeout(wait)
        return pg

    # Cast fresh
    pg = page()
    pg.evaluate("()=>__mockCast.start()"); pg.wait_for_timeout(300)
    pg.evaluate("()=>__mockCast.reply({type:'state',configured:false})"); pg.wait_for_timeout(300)
    assert_current_remote(pg, "fresh cast")
    pg.context.close()

    # Reload while casting
    pg = page(init="window.__mockResume = true", wait=3000)
    assert_current_remote(pg, "reload while casting")
    pg.context.close()

    # Join a running session: the probe reply says the TV is already set up
    pg = page()
    pg.evaluate("()=>__mockCast.start()"); pg.wait_for_timeout(300)
    pg.evaluate("()=>__mockCast.reply({type:'state',configured:true,camId:'5B4LmH4JbnY',camTitle:'Shibuya Scramble Crossing',mixId:'nCtOCLv6eFY',mixTitle:'Running mix',volume:40})")
    pg.wait_for_timeout(400)
    check("join running: now playing shows the TV's cam", pg.text_content("#remote-cam") == "Shibuya Scramble Crossing")
    check("join running: mix title readable in #remote-mix", pg.text_content("#remote-mix") == "Running mix")
    check("join running: still asks for the cam's hqdefault",
          pg.get_attribute("#remote-still", "src") == "https://i.ytimg.com/vi/5B4LmH4JbnY/hqdefault.jpg" and pg.get_attribute("#remote-still", "referrerpolicy") == "no-referrer")
    check("join running: music volume follows the TV (40)", pg.input_value("#remote-volume") == "40")
    # Pin chip follows the TV state
    check("unpinned: chip reads Pin cam, note hidden",
          pg.text_content("#remote-pin") == "Pin cam" and pg.get_attribute("#remote-pin", "aria-pressed") == "false" and not pg.is_visible("#remote-pinned-note"))
    pg.evaluate("()=>__mockCast.reply({type:'state',camId:'5B4LmH4JbnY',camTitle:'Shibuya Scramble Crossing',pinned:true,volume:40})"); pg.wait_for_timeout(200)
    check("pinned: chip reads Pinned, aria-pressed true, 'Pinned. Rotation off.' visible",
          pg.text_content("#remote-pin") == "Pinned" and pg.get_attribute("#remote-pin", "aria-pressed") == "true"
          and pg.is_visible("#remote-pinned-note") and pg.text_content("#remote-pinned-note") == "Pinned. Rotation off.")
    pg.evaluate("()=>__mockCast.reply({type:'state',camId:'5B4LmH4JbnY',camTitle:'Shibuya Scramble Crossing',pinned:false,volume:40})"); pg.wait_for_timeout(200)
    check("unpinned again: Pin cam, note hidden", pg.text_content("#remote-pin") == "Pin cam" and not pg.is_visible("#remote-pinned-note"))
    # Play button: icon and accessible name follow paused
    pg.evaluate("()=>__mockCast.reply({type:'state',paused:true})"); pg.wait_for_timeout(200)
    check("paused: play button says Play with the play glyph",
          pg.text_content("#remote-play") == "Play" and pg.get_attribute("#remote-play use", "href") == "#i-play")
    pg.evaluate("()=>__mockCast.reply({type:'state',paused:false})"); pg.wait_for_timeout(200)
    check("playing: play button says Pause with the pause glyph",
          pg.text_content("#remote-play") == "Pause" and pg.get_attribute("#remote-play use", "href") == "#i-pause")
    # Music volume slider sends one volume command, and follows a later TV volume
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.evaluate("()=>{const i=document.querySelector('#remote-volume'); i.value='75'; i.dispatchEvent(new Event('input',{bubbles:true})); i.dispatchEvent(new Event('change',{bubbles:true}))}")
    pg.wait_for_timeout(200)
    sent = [m["data"] for m in pg.evaluate("()=>__mockCast.sent")]
    check(f"volume slider sends volume 75 ({sent})", any('"volume"' in d and '75' in d for d in sent))
    pg.evaluate("()=>document.activeElement && document.activeElement.blur()")
    pg.evaluate("()=>__mockCast.reply({type:'state',volume:25})"); pg.wait_for_timeout(200)
    check(f"volume slider follows the TV (25, got {pg.input_value('#remote-volume')})", pg.input_value("#remote-volume") == "25")
    # A built-in cam with a timezone: place and local time under the title, and its still
    pg.evaluate("()=>__mockCast.reply({type:'state',camId:'hlbh4P0Mz8M',camTitle:'Odaiba and Rainbow Bridge'})"); pg.wait_for_timeout(200)
    check("cam change: title and still follow", pg.text_content("#remote-cam") == "Odaiba and Rainbow Bridge"
          and pg.get_attribute("#remote-still", "src") == "https://i.ytimg.com/vi/hlbh4P0Mz8M/hqdefault.jpg")
    assert_current_remote(pg, "join running", timed_cam=True)
    check(f"join running: place line starts with Tokyo, Japan ({pg.text_content('#remote-meta')!r})", pg.text_content("#remote-meta").startswith("Tokyo, Japan · "))
    pg.context.close()

    # Return to the tab with a session the page has not heard about: pageshow, focus, visibilitychange
    for ev, js in [("pageshow", "()=>window.dispatchEvent(new Event('pageshow'))"),
                   ("focus", "()=>window.dispatchEvent(new Event('focus'))"),
                   ("visibilitychange", "()=>document.dispatchEvent(new Event('visibilitychange'))")]:
        pg = page()
        pg.evaluate("()=>__mockCast.silent()")
        check(f"{ev}: not in remote mode before the event", not pg.is_visible("#remote"))
        pg.evaluate(js); pg.wait_for_timeout(500)
        assert_current_remote(pg, ev)
        pg.context.close()
    b.close()
print(logs); sys.exit(1 if fails or logs else 0)
