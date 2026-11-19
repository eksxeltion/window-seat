"""Receiver commands in debug mode (receiver.html?debug=1, driven through window.__receiver.send) against the REAL
YouTube IFrame API, so it needs network. With WS_FAKE_YOUTUBE=1 it uses tests/fake_youtube.js instead (tests/yt_mode.py).
Usage: test_receiver_cmds.py BASE_URL   (BASE_URL serves receiver.html; a receiver.html URL also works)"""
import sys
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts
import yt_mode  # noqa: E402  WS_FAKE_YOUTUBE=1 swaps in tests/fake_youtube.js (CI)
yt_mode.announce()
base = sys.argv[1].split("?")[0].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
url=base+"/receiver.html?debug=1"; logs=[]; fails=[]  # __receiver only exists in debug mode
def check(name, cond):
    print(("PASS " if cond else "FAIL ")+name); (None if cond else fails.append(name))
with sync_playwright() as p:
    b=p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg=b.new_page(viewport={"width":1920,"height":1080})
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    yt_mode.route(pg)
    pg.goto(url); pg.wait_for_timeout(15000)
    st=lambda: pg.evaluate("()=>__receiver.state"); send=lambda js: pg.evaluate(f"()=>__receiver.send({js})")
    c0=st()['camId']; send('JSON.stringify({type:"next"})'); pg.wait_for_timeout(9000)
    check(f"next via JSON string: {c0} -> {st()['camId']}", st()['camId']!=c0)
    check("on-TV flash shown", pg.evaluate("()=>document.querySelector('#cmd-toast').textContent")=="Next cam")
    # Castable cams from the list the receiver actually uses (the live sheet marks some cams web-only, build .33).
    ok=pg.evaluate("async()=>(await import('./js/data.js')).CAMS.filter(c=>c.castable!==false).map(c=>c.id)")
    pick=[i for i in ok if i!=st()['camId']][0]
    send(f'{{type:"cam", id:"{pick}"}}'); pg.wait_for_timeout(9000); check(f"cam pick {pick}", st()['camId']==pick)
    send('{type:"interval", value:30}'); check("interval 30", st()['interval']==30)
    send('{type:"pin", value:true}'); check("pin", st()['pinned'] is True)
    send('{type:"volume", value:35}'); pg.wait_for_timeout(500); check("volume 35 applied", pg.evaluate("()=>__receiver.music().vol")==35)
    send('{type:"mute", value:true}'); pg.wait_for_timeout(500); check("muted", st()['muted'] and pg.evaluate("()=>__receiver.music().muted"))
    send('{type:"mute", value:false}'); pg.wait_for_timeout(500); check("unmuted", not pg.evaluate("()=>__receiver.music().muted"))
    # playMusic is the older name for the whole-feed pause: music and the loaded cam both pause and resume.
    send('{type:"playMusic", value:false}'); pg.wait_for_timeout(1500); check("music paused", pg.evaluate("()=>__receiver.music().state")==2)
    check("cam paused too", pg.evaluate("()=>__receiver.cam().state")==2 and st()['paused'] is True and pg.is_visible("#paused-pill"))
    send('{type:"playMusic", value:true}'); pg.wait_for_timeout(3000); check("music playing again", pg.evaluate("()=>__receiver.music().state") in (1,3))
    check("cam playing again", pg.evaluate("()=>__receiver.cam().state") in (1,3) and st()['paused'] is False and not pg.is_visible("#paused-pill"))
    check("cam still muted", pg.evaluate("()=>__receiver.cam().muted") is True)
    m0=st()['mixId']; send('{type:"nextMix"}'); pg.wait_for_timeout(5000); check("next mix", st()['mixId']!=m0 and pg.evaluate("()=>__receiver.music().state") in (1,3))
    two=ok[:2]
    send(f'{{type:"cams", ids:{two!r}}}'.replace("'", '"')); check("cams filter", st()['poolIds']==two or st()['poolIds'] is not None)
    send('{type:"pin", value:false}'); send('{type:"next"}'); pg.wait_for_timeout(9000); check(f"next stays in filter: {st()['camId']}", st()['camId'] in two)
    b.close()
print(logs); sys.exit(1 if fails or logs else 0)
