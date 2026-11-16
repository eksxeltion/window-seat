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
    pg.evaluate("()=>__mockCast.start()"); pg.wait_for_timeout(300)
    init=[json.loads(m['data']) for m in pg.evaluate("()=>__mockCast.sent")]
    print("connect messages:", init)
    check("connect sends cams filter", any(m['type']=='cams' and len(m['ids'])>0 for m in init))
    check("all messages are JSON strings on the namespace", all(isinstance(m['data'],str) and m['ns']=='urn:x-cast:com.windowseat' for m in pg.evaluate("()=>__mockCast.sent")))
    check("remote visible", pg.is_visible("#remote"))
    expect=[('[data-cmd="next"]',{'type':'next'}),('[data-cmd="prev"]',{'type':'prev'}),('[data-cmd="pin"]',{'type':'pin','value':True}),
            ('[data-cmd="i30"]',{'type':'interval','value':30}),('[data-cmd="vol-up"]',{'type':'volume','value':70}),('[data-cmd="vol-up"]',{'type':'volume','value':80}),
            ('[data-cmd="vol-down"]',{'type':'volume','value':70}),('[data-cmd="mute"]',{'type':'mute','value':True}),('[data-cmd="mute"]',{'type':'mute','value':False}),
            ('[data-cmd="play"]',{'type':'playMusic','value':False}),('[data-cmd="play"]',{'type':'playMusic','value':True}),('[data-cmd="next-mix"]',{'type':'nextMix'}),
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
    pg.locator('#remote [data-cmd="next"]').tap(); pg.wait_for_timeout(300)
    t = pg.text_content("#toast")
    print("timeout toast:", t)
    check("timeout error names the stage and the missing channel", "Send failed (timeout)" in t and "has not opened the Window Seat channel" in t)
    pg.evaluate("()=>{window.__mockFail=null; window.__mockNs=null; window.__mockAppId='CC1AD845'}")
    pg.locator('#remote [data-cmd="next"]').tap(); pg.wait_for_timeout(4500)
    check("wrong app id is called out", "different TV app" in pg.text_content("#remote-status"))
    b.close()
print(logs); sys.exit(1 if fails or logs else 0)
