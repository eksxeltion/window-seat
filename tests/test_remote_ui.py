"""Every way into remote mode shows the current remote: the Cams and Mix buttons above the fold, no old inline
cam or mix lists, and the sheets with their own controls (mock CAF). Usage: test_remote_ui.py PHONE_URL"""
import sys, os
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

def assert_current_remote(pg, tag):
    check(f"{tag}: #remote visible, body.casting on", pg.is_visible("#remote") and pg.evaluate("()=>document.body.classList.contains('casting')"))
    for a in ("open-cams", "open-music"):
        sel = f'#remote [data-action="{a}"]'
        check(f"{tag}: {a} visible and above the fold", pg.is_visible(sel) and pg.evaluate(IN_VIEW, sel))
    check(f"{tag}: no old inline lists", pg.locator("#remote-mixes").count() == 0 and pg.locator("#remote-cams").count() == 0)
    check(f"{tag}: no old interval or volume rows on the remote",
          pg.locator('#remote [data-cmd="i30"], #remote [data-cmd="i60"], #remote [data-cmd="vol-up"], #remote [data-cmd="vol-down"], #remote-vol, #remote-mute').count() == 0)
    for sel in ("#remote-cam", "#remote-mix", "#remote-play", "#remote-pin", '#remote [data-cmd="prev"]', '#remote [data-cmd="next"]', '#remote [data-cmd="stop"]'):
        check(f"{tag}: {sel} present", pg.is_visible(sel))
    # Cams sheet with its extra controls
    pg.locator('#remote [data-action="open-cams"]').tap(); pg.wait_for_timeout(300)
    check(f"{tag}: Cams sheet open", pg.is_visible("#panel-cams"))
    check(f"{tag}: Cams sheet has skip sleeping, only daytime, interval",
          pg.is_visible("#cams-skip-sleeping") and pg.is_visible("#cams-only-daytime") and pg.locator("#cams-interval").count() == 1)
    check(f"{tag}: Cams sheet has cam checkboxes and Play now",
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
    assert_current_remote(pg, "join running")
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
