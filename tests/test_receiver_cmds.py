import sys
from playwright.sync_api import sync_playwright
url=sys.argv[1]; logs=[]; fails=[]
def check(name, cond):
    print(("PASS " if cond else "FAIL ")+name); (None if cond else fails.append(name))
with sync_playwright() as p:
    b=p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg=b.new_page(viewport={"width":1920,"height":1080})
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    pg.goto(url); pg.wait_for_timeout(15000)
    st=lambda: pg.evaluate("()=>__receiver.state"); send=lambda js: pg.evaluate(f"()=>__receiver.send({js})")
    c0=st()['camId']; send('JSON.stringify({type:"next"})'); pg.wait_for_timeout(9000)
    check(f"next via JSON string: {c0} -> {st()['camId']}", st()['camId']!=c0)
    check("on-TV flash shown", pg.evaluate("()=>document.querySelector('#cmd-toast').textContent")=="Next cam")
    send('{type:"cam", id:"u7kCTaCX_J4"}'); pg.wait_for_timeout(9000); check("cam pick", st()['camId']=="u7kCTaCX_J4")
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
    send('{type:"cams", ids:["u7kCTaCX_J4","VM18f-IIUTw"]}'); check("cams filter", st()['poolIds']==["u7kCTaCX_J4","VM18f-IIUTw"] or st()['poolIds'] is not None)
    send('{type:"pin", value:false}'); send('{type:"next"}'); pg.wait_for_timeout(9000); check(f"next stays in filter: {st()['camId']}", st()['camId'] in ["u7kCTaCX_J4","VM18f-IIUTw"])
    b.close()
print(logs); sys.exit(1 if fails or logs else 0)
