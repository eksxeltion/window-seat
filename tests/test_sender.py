import sys, json
from playwright.sync_api import sync_playwright
url=sys.argv[1]; logs=[]; fails=[]
MOCK=open('' + __import__('os').path.join(__import__('os').path.dirname(__file__), 'mock_cast_sender.js') + '').read()
def check(name, cond):
    print(("PASS " if cond else "FAIL ")+name); (None if cond else fails.append(name))
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":412,"height":915}, has_touch=True, is_mobile=True)
    pg=ctx.new_page()
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    pg.goto(url); pg.wait_for_timeout(2500)
    yt = "()=>document.querySelectorAll('iframe[src*=youtube]').length"
    pg.click("#start-btn"); pg.wait_for_timeout(4000)
    check(f"playing locally before casting ({pg.evaluate(yt)} YouTube iframes)", pg.evaluate(yt) == 2)
    pg.evaluate("()=>__mockCast.start()"); pg.wait_for_timeout(300)
    check("on connect, before any TV reply: local YouTube players destroyed", pg.evaluate(yt) == 0)
    check("on connect, before any TV reply: remote shown, page in casting mode", pg.is_visible("#remote") and pg.evaluate("()=>document.body.classList.contains('casting')"))
    check("Start does nothing while casting", pg.evaluate("()=>{__windowSeat.start(); return document.querySelectorAll('iframe[src*=youtube]').length}") == 0)
    init=[json.loads(m['data']) for m in pg.evaluate("()=>__mockCast.sent")]
    print("connect messages:", init)
    check("connect sends cams filter", any(m['type']=='cams' and len(m['ids'])>0 for m in init))
    check("all messages are JSON strings on the namespace", all(isinstance(m['data'],str) and m['ns']=='urn:x-cast:com.windowseat' for m in pg.evaluate("()=>__mockCast.sent")))
    check("remote visible", pg.is_visible("#remote"))
    expect=[('[data-cmd="next"]',{'type':'next'}),('[data-cmd="prev"]',{'type':'prev'}),('[data-cmd="pin"]',{'type':'pin','value':True}),
            ('[data-cmd="i30"]',{'type':'interval','value':30}),('[data-cmd="vol-up"]',{'type':'volume','value':70}),('[data-cmd="vol-up"]',{'type':'volume','value':80}),
            ('[data-cmd="vol-down"]',{'type':'volume','value':70}),('[data-cmd="mute"]',{'type':'mute','value':True}),('[data-cmd="mute"]',{'type':'mute','value':False}),
            ('[data-cmd="play"]',{'type':'pause','value':True}),('[data-cmd="play"]',{'type':'pause','value':False}),('[data-cmd="next-mix"]',{'type':'nextMix'}),
            ('[data-mix]:nth-of-type(1)',None),('#remote-cams [data-cam]',None)]
    for sel,exp in expect:
        pg.evaluate("()=>__mockCast.sent.length=0")
        pg.locator("#remote "+sel).last.tap(); pg.wait_for_timeout(120)
        got=[json.loads(m['data']) for m in pg.evaluate("()=>__mockCast.sent")]
        if exp is None: check(f"tap {sel} -> {got}", len(got)==1 and got[0]['type'] in ('mix','cam') and got[0].get('id'))
        else: check(f"tap {sel} -> {exp}", got==[exp])
    check("optimistic pin shown", pg.get_attribute("#remote-pin","aria-pressed")=="true")
    pg.evaluate("()=>__mockCast.reply({type:'state',camId:'5B4LmH4JbnY',camTitle:'Shibuya Scramble Crossing',mixId:'nCtOCLv6eFY',mixTitle:'X',pinned:false,interval:60,volume:40,muted:false,musicPaused:false})"); pg.wait_for_timeout(100)
    check("TV state reflected (cam title)", pg.text_content("#remote-cam")=="Shibuya Scramble Crossing")
    check("TV state reflected (volume)", pg.text_content("#remote-vol")=="40")
    check("status says connected", "Connected" in pg.text_content("#remote-status"))
    pg.evaluate("()=>__mockCast.sent.length=0"); pg.locator('#remote [data-cmd="next"]').tap(); pg.wait_for_timeout(4500)
    check("no-reply warning after 4s", "no reply" in pg.text_content("#remote-status"))
    pg.evaluate("()=>{window.__mockFail='timeout'; window.__mockNs=[{name:'urn:x-cast:com.google.cast.media'}]}")
    pg.locator('#remote [data-cmd="pin"]').tap(); pg.wait_for_timeout(1500)
    t = pg.text_content("#toast")
    check("timeout is retried once before reporting", sum(1 for m in pg.evaluate("()=>__mockCast.sent") if json.loads(m['data'])['type']=='pin') >= 2)
    print("timeout toast:", t)
    check("timeout error names the stage and the missing channel", "Send failed (timeout)" in t and "has not opened the Window Seat channel" in t)
    pg.evaluate("()=>{window.__mockFail=null; window.__mockNs=null; window.__mockAppId='CC1AD845'}")
    pg.locator('#remote [data-cmd="next"]').tap(); pg.wait_for_timeout(4500)
    check("wrong app id is called out", "different TV app" in pg.text_content("#remote-status"))
    pg.evaluate("()=>window.__mockAppId=null")
    pg.evaluate("()=>cast.framework.CastContext.getInstance().endCurrentSession(true)"); pg.wait_for_timeout(2500)
    check("session ended: remote hidden, players recreated, back at Start", (not pg.is_visible("#remote")) and pg.evaluate(yt) == 2 and pg.is_visible("#start-btn"))
    b.close()
    # Reload while the TV is still casting: auto-join resumes the session with no event we can rely on.
    b = p.chromium.launch(); pg = b.new_context(viewport={"width":412,"height":915}, has_touch=True, is_mobile=True).new_page()
    pg.add_init_script("window.__mockResume = true")
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    pg.goto(url); pg.wait_for_timeout(3000)
    check("reload during a cast: remote shown at once", pg.is_visible("#remote"))
    check("reload during a cast: no local YouTube players", pg.evaluate(yt) == 0)
    check("reload during a cast: asks the TV for its state", any(json.loads(m['data'])['type']=='getState' for m in pg.evaluate("()=>__mockCast.sent")))
    b.close()
print(logs); sys.exit(1 if fails or logs else 0)
