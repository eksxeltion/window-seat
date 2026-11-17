"""Telemetry (PostHog and Sentry) on the phone page and the receiver, with both hosts stubbed.
Usage: test_telemetry.py BASE_URL   (the site root, for example http://localhost:8000/)

Covers docs/telemetry.md section 10: the About switch, Do Not Track and Global Privacy Control, "off means no requests
to either host" (and no SDK download), queued events dropped when turning off, super properties and the test flag,
and the receiver gate (nothing before a telemetry message, sends after on, stops after off)."""
import sys, os, json, re, time
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402  stubs *.posthog.com and *.sentry.io

base = sys.argv[1].rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
MOCK_SENDER = open(os.path.join(HERE, "mock_cast_sender.js")).read()
FAKE_YT = open(os.path.join(HERE, "fake_youtube.js")).read()
BUILD = "2026-11-17.13"
NS = "urn:x-cast:com.windowseat"
# PostHog drops events from browsers that look automated (webdriver flag, HeadlessChrome brand). These tests need it
# to behave as in a normal Chrome, so they present one. Existing tests keep the headless defaults.
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
NOT_A_BOT = "Object.defineProperty(Navigator.prototype,'webdriver',{get:()=>false});Object.defineProperty(Navigator.prototype,'userAgentData',{get:()=>undefined});"
TELEMETRY_HOST = re.compile(r"^https?://([a-z0-9-]+\.)*(posthog\.com|sentry\.io)(:\d+)?/", re.I)

fails, page_errors = [], []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def wait_until(pg, js, ms=8000):
    end = time.time() + ms / 1000
    while time.time() < end:
        try:
            if pg.evaluate(js):
                return True
        except Exception:
            pass
        pg.wait_for_timeout(100)
    return False


def open_phone(b, query="", init="", mobile=False):
    """A phone page with the mock Cast sender and a fake YouTube. Returns (page, requests seen)."""
    kw = {"user_agent": UA, "locale": "en-US"}
    kw.update({"viewport": {"width": 412, "height": 915}, "has_touch": True, "is_mobile": True} if mobile else {"viewport": {"width": 1280, "height": 800}})
    ctx = b.new_context(**kw)
    ctx.add_init_script(NOT_A_BOT + init)
    reqs = []
    ctx.on("request", lambda r: reqs.append(r.url))
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: page_errors.append(f"{e}") if "telemetry test error" not in str(e) else None)
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK_SENDER))
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route(re.compile(r"docs\.google\.com"), lambda r: r.abort())  # no sheet: the built-in list
    pg.goto(f"{base}/{query}")
    return pg, reqs


def host_hits(reqs):
    return [u for u in reqs if TELEMETRY_HOST.match(u)]


def vendor_loads(reqs):
    return [u for u in reqs if "/js/vendor/" in u]


def ph_keys(pg):
    return pg.evaluate("()=>Object.keys(localStorage).filter(k=>k.startsWith('ph_')||k.startsWith('__ph_'))")


def do_things(pg):
    """Start, next, pin, open and close panels, a thrown error and a rejected promise, then cast and stop."""
    pg.evaluate("()=>__windowSeat.start()")
    wait_until(pg, "()=>__windowSeat.currentCamId()", 6000)
    pg.evaluate("()=>__windowSeat.next()")
    pg.keyboard.press("p")
    pg.evaluate("()=>{setTimeout(()=>{throw new Error('telemetry test error')},0); Promise.reject(new Error('telemetry test error (promise)'))}")
    pg.wait_for_timeout(1500)
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(1000)
    pg.evaluate("()=>__mockCast.reply({type:'state', build:'%s', camId:null, mixId:null, paused:false})" % BUILD)
    pg.wait_for_timeout(500)


with sync_playwright() as p:
    b = p.chromium.launch()

    # ---------------------------------------------------------------- 1. Switch OFF: nothing to either host, no SDK
    pg, reqs = open_phone(b, init="localStorage.setItem('windowSeat.telemetry','false');localStorage.setItem('ph_old_posthog','x');")
    pg.wait_for_timeout(2500)
    st = pg.evaluate("()=>__telemetry.state()")
    check("off: wrapper state is off", st["on"] is False and st["posthog"] == "idle" and st["sentry"] == "idle")
    do_things(pg)
    pg.wait_for_timeout(4000)
    check(f"off: no request to PostHog or Sentry ({host_hits(reqs)[:3]})", not host_hits(reqs))
    check(f"off: vendor SDK scripts never requested ({vendor_loads(reqs)})", not vendor_loads(reqs))
    check("off: no SDK globals", pg.evaluate("()=>typeof window.posthog==='undefined' && typeof window.Sentry==='undefined'"))
    check("off: nothing queued or sent", pg.evaluate("()=>__telemetry.queue().length===0 && __telemetry.sent().length===0 && __telemetry.errorsSent().length===0"))
    check(f"off: old PostHog keys removed ({ph_keys(pg)})", ph_keys(pg) == [])
    tel = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent") if json.loads(m["data"]).get("type") == "telemetry"]
    check(f"off: the TV is told off ({tel})", len(tel) == 1 and tel[0]["on"] is False and tel[0]["installId"] is None)
    pg.evaluate("()=>__mockCast.reply({type:'state'})")
    pg.locator("#remote [data-cmd='stop']").click()
    pg.wait_for_timeout(1500)
    check("off: still no request after stop", not host_hits(reqs) and not vendor_loads(reqs))
    pg.context.close()

    # About switch reflects the setting
    pg, reqs = open_phone(b, init="localStorage.setItem('windowSeat.telemetry','false');")
    pg.evaluate("()=>document.querySelector('[data-action=open-about]').click()")
    sw = pg.locator("#telemetry-toggle")
    check("about: switch is there with its label and help", sw.count() == 1 and "Share anonymous usage and errors" in pg.text_content("label[for=telemetry-toggle]") and pg.text_content("#telemetry-help") == "Helps fix Cast problems. No personal data. Turn off anytime.")
    check("about: switch off when saved off, enabled, no DNT note", not sw.is_checked() and sw.is_enabled() and pg.locator("#telemetry-dnt").is_hidden())
    check("about: same markup as Debug mode", pg.evaluate("()=>{const a=document.querySelector('label[for=debug-toggle]'),b=document.querySelector('label[for=telemetry-toggle]');return a.className===b.className&&b.querySelector('input[role=switch]')&&b.querySelector('.debug-switch-track .debug-switch-thumb')&&getComputedStyle(a).display===getComputedStyle(b).display}"))
    pg.context.close()

    # ---------------------------------------------------------------- 2. DNT and GPC force off, even with ?telemetry_test=1
    for label, init in [("DNT", "Object.defineProperty(Navigator.prototype,'doNotTrack',{get:()=>'1'});"),
                        ("GPC", "Object.defineProperty(Navigator.prototype,'globalPrivacyControl',{get:()=>true});"),
                        ("window.doNotTrack", "Object.defineProperty(window,'doNotTrack',{get:()=>'yes'});")]:
        pg, reqs = open_phone(b, query="?telemetry_test=1", init=init + "localStorage.setItem('windowSeat.telemetry','true');")
        pg.wait_for_timeout(2500)
        st = pg.evaluate("()=>__telemetry.state()")
        check(f"{label}: forced off", st["on"] is False and st["forcedOff"] is True)
        pg.evaluate("()=>document.querySelector('[data-action=open-about]').click()")
        sw = pg.locator("#telemetry-toggle")
        check(f"{label}: switch off and disabled", not sw.is_checked() and sw.is_disabled())
        check(f"{label}: note shown", pg.locator("#telemetry-dnt").is_visible() and pg.text_content("#telemetry-dnt") == "Off because your browser asks sites not to track you.")
        check(f"{label}: the switch cannot turn it on", pg.evaluate("()=>{const t=document.getElementById('telemetry-toggle');t.checked=true;t.dispatchEvent(new Event('change'));return __telemetry.state().on}") is False)
        pg.evaluate("()=>document.querySelector('#panel-about [data-action=close-panel]').click()")
        do_things(pg)
        pg.wait_for_timeout(2500)
        check(f"{label}: no request to either host, no SDK, no ID", not host_hits(reqs) and not vendor_loads(reqs) and ph_keys(pg) == [])
        pg.context.close()

    # ---------------------------------------------------------------- 3. Switch ON (default): super properties, test flag
    pg, reqs = open_phone(b, query="?telemetry_test=1&x=secret#frag", mobile=True)
    check("on: SDKs load after first paint", wait_until(pg, "()=>__telemetry.state().posthog==='ready' && __telemetry.state().sentry==='ready'", 10000))
    check("on: switch on by default", pg.evaluate("()=>document.getElementById('telemetry-toggle').checked") is True)
    do_things(pg)
    check("on: events reached the (stubbed) PostHog host", wait_until(pg, "()=>__telemetry.sent().length>=4", 8000))
    pg.wait_for_timeout(4000)  # PostHog batches every few seconds
    sent = pg.evaluate("()=>__telemetry.sent()")
    names = [e["event"] for e in sent]
    print("on: events:", names)
    for name in ["app_open", "local_playback_started", "command", "cast_connect_success", "cast_first_state", "local_playback_ended"]:
        check(f"on: {name} sent", name in names)
    check("on: only our own events (no $pageview, $opt_in...)", all(not n.startswith("$") for n in names))
    install = pg.evaluate("()=>__telemetry.state().installId")
    bad = []
    for e in sent:
        pr = e["properties"]
        want = {"app": "window-seat", "build": BUILD, "surface": "web", "environment": "verification", "test": True, "$geoip_disable": True, "locale_region": "US"}
        if any(pr.get(k) != v for k, v in want.items()) or pr.get("device_class") not in ("phone", "tablet", "desktop", "tv") or pr.get("cast_state") not in ("none", "connecting", "connected"):
            bad.append((e["event"], {k: pr.get(k) for k in list(want) + ["device_class", "cast_state"]}))
        if any(k in pr for k in ("$referrer", "$referring_domain", "$timezone")) or any(k.startswith("$initial_") for k in pr):
            bad.append((e["event"], "untrimmed"))
        if "?" in str(pr.get("$current_url", "")) or "#" in str(pr.get("$current_url", "")):
            bad.append((e["event"], pr.get("$current_url")))
        if pr.get("distinct_id") != install:
            bad.append((e["event"], "distinct_id"))
    check(f"on: every event has the super properties, test flag and trimming ({bad[:2]})", not bad)
    phone_pd = [e["properties"] for e in sent if e["event"] == "cam_shown"] + [e["properties"] for e in sent if e["event"] == "command"]
    check("on: command events carry a type and mode", all(x.get("mode") in ("local", "cast") for x in phone_pd))
    cast_ok = [e["properties"] for e in sent if e["event"] == "cast_connect_success"]
    check(f"on: cast events carry cast_state and trigger ({cast_ok})", cast_ok and cast_ok[0]["trigger"] == "adopted" and cast_ok[0]["cast_state"] == "connected")
    hits = host_hits(reqs)
    check("on: PostHog requests went to us.i.posthog.com (stubbed)", any(u.startswith("https://us.i.posthog.com/") for u in hits))
    check("on: nothing requested from us-assets.i.posthog.com", not any("us-assets.i.posthog.com" in u for u in reqs))
    errs = pg.evaluate("()=>__telemetry.errorsSent()")
    check(f"on: the test error went to Sentry with release, environment and tags ({errs[:1]})", any("telemetry test error" in e["value"] and e["release"] == f"window-seat@{BUILD}" and e["environment"] == "verification" and e["tags"]["surface"] == "web" and e["tags"]["build"] == BUILD for e in errs))
    check("on: Sentry requests went to the (stubbed) ingest host", any("sentry.io" in u for u in hits))
    bodies = b"".join(h["body"] or b"" for h in telemetry_guard.hits)
    check("on: no query string or fragment from the page URL in what was sent", b"x=secret" not in bodies and b"#frag" not in bodies and b"telemetry_test=1" not in bodies)
    tel = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent") if json.loads(m["data"]).get("type") == "telemetry"]
    check(f"on: the TV is told on with the install ID, test flag and environment ({tel})", tel and tel[-1] == {"type": "telemetry", "on": True, "installId": install, "test": True, "environment": "verification", "silent": True})
    check("on: PostHog keeps its ID in localStorage", any(k.startswith("ph_") for k in ph_keys(pg)))

    # Turning it off while running: gate first, queue dropped, opt out, keys removed, the TV told, nothing sent.
    pg.evaluate("()=>__mockCast.sent.length=0")
    n_hits = len(host_hits(reqs))
    pg.evaluate("()=>{const t=document.getElementById('telemetry-toggle');t.checked=false;t.dispatchEvent(new Event('change'))}")
    st = pg.evaluate("()=>__telemetry.state()")
    check("off while running: gate off, queue empty", st["on"] is False and st["queued"] == 0)
    check("off while running: PostHog opted out", pg.evaluate("()=>posthog.has_opted_out_capturing()") is True)
    check(f"off while running: PostHog keys removed ({ph_keys(pg)})", not any(k.startswith("ph_") for k in ph_keys(pg)))
    check("off while running: saved", pg.evaluate("()=>localStorage.getItem('windowSeat.telemetry')") == "false")
    tel = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent") if json.loads(m["data"]).get("type") == "telemetry"]
    check(f"off while running: the TV is told off ({tel})", tel and tel[-1]["on"] is False)
    n_sent = len(pg.evaluate("()=>__telemetry.sent()"))
    pg.evaluate("()=>__mockCast.reply({type:'state', build:'%s', paused:false})" % BUILD)
    pg.locator("#remote [data-cmd='next']").click()
    pg.evaluate("()=>{setTimeout(()=>{throw new Error('telemetry test error after off')},0)}")
    pg.wait_for_timeout(5000)
    check(f"off while running: no new request to either host ({host_hits(reqs)[n_hits:]})", len(host_hits(reqs)) == n_hits)
    check("off while running: nothing passed before_send", len(pg.evaluate("()=>__telemetry.sent()")) == n_sent)
    check("off while running: no telemetry_disabled style event", not any(e["event"].endswith("disabled") for e in pg.evaluate("()=>__telemetry.sent()")))

    # On again: opt in and telemetry_enabled.
    pg.evaluate("()=>{const t=document.getElementById('telemetry-toggle');t.checked=true;t.dispatchEvent(new Event('change'))}")
    check("on again: telemetry_enabled sent", wait_until(pg, "()=>__telemetry.sent().some(e=>e.event==='telemetry_enabled')", 3000))
    check("on again: opted in", pg.evaluate("()=>posthog.has_opted_out_capturing()") is False and pg.evaluate("()=>localStorage.getItem('windowSeat.telemetry')") == "true")
    pg.context.close()

    # ---------------------------------------------------------------- 4. Turning off drops events queued before the SDK loads
    ctx = b.new_context(user_agent=UA, locale="en-US", viewport={"width": 1280, "height": 800})
    ctx.add_init_script(NOT_A_BOT)
    reqs = []
    ctx.on("request", lambda r: reqs.append(r.url))
    held = []
    ctx.route("**/js/vendor/posthog-*", lambda r: held.append(r))  # hold the SDK download
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: page_errors.append(str(e)))
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK_SENDER))
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.goto(f"{base}/?telemetry_test=1")
    wait_until(pg, "()=>__telemetry.state().posthog==='loading'", 8000)
    pg.evaluate("()=>__windowSeat.start()")
    pg.wait_for_timeout(1500)
    pg.evaluate("()=>__windowSeat.next()")
    q = pg.evaluate("()=>__telemetry.state().queued")
    check(f"queue: events wait for the SDK ({q} queued)", q >= 2)
    pg.evaluate("()=>{const t=document.getElementById('telemetry-toggle');t.checked=false;t.dispatchEvent(new Event('change'))}")
    check("queue: turning off drops the queue", pg.evaluate("()=>__telemetry.state().queued") == 0)
    for r in held:
        r.continue_()
    ctx.unroute("**/js/vendor/posthog-*")
    wait_until(pg, "()=>__telemetry.state().posthog==='ready'", 5000)
    pg.wait_for_timeout(4000)
    check(f"queue: nothing sent once the SDK arrived ({host_hits(reqs)})", not any("posthog" in u for u in host_hits(reqs)) and pg.evaluate("()=>__telemetry.sent().length") == 0)
    check("queue: the late SDK starts opted out", pg.evaluate("()=>window.posthog && posthog.has_opted_out_capturing()") is True)
    ctx.close()

    # ---------------------------------------------------------------- 5. SDK blocked (ad blocker): the app keeps working
    ctx = b.new_context(user_agent=UA)
    ctx.route("**/js/vendor/**", lambda r: r.abort())
    pg = ctx.new_page()
    errs5 = []
    pg.on("pageerror", lambda e: errs5.append(str(e)))
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK_SENDER))
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.goto(f"{base}/")
    check("blocked: wrapper gives up quietly", wait_until(pg, "()=>__telemetry.state().posthog==='failed' && __telemetry.state().sentry==='failed'", 10000))
    pg.evaluate("()=>__windowSeat.start()")
    check("blocked: the app still plays", wait_until(pg, "()=>__windowSeat.currentCamId()", 8000))
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(800)
    tel = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent") if json.loads(m["data"]).get("type") == "telemetry"]
    check(f"blocked: the TV is still told on, without an ID ({tel})", tel and tel[-1]["on"] is True and tel[-1]["installId"] is None)
    check(f"blocked: no page errors ({errs5})", not errs5)
    ctx.close()

    # ---------------------------------------------------------------- 6. Receiver gate
    def open_receiver(query):
        ctx = b.new_context(user_agent=UA, viewport={"width": 1920, "height": 1080})
        ctx.add_init_script(NOT_A_BOT)
        rq = []
        ctx.on("request", lambda r: rq.append(r.url))
        rx = ctx.new_page()
        rx.on("pageerror", lambda e: page_errors.append("receiver: " + str(e)))
        rx.route("**/iframe_api", lambda r: r.abort())  # no YouTube needed for the gate
        rx.route(re.compile(r"docs\.google\.com"), lambda r: r.abort())
        rx.goto(f"{base}/receiver.html{query}")
        rx.wait_for_timeout(1500)
        return rx, rq

    msg = lambda rx, obj, sender: rx.evaluate("([d,s])=>__receiver.message({data:d, senderId:s})", [obj if isinstance(obj, str) else json.dumps(obj), sender])

    rx, rq = open_receiver("?debug=1&telemetry=1")
    st = rx.evaluate("()=>__telemetry.state()")
    check("receiver: off before any telemetry message", st["on"] is False and st["surface"] == "receiver")
    msg(rx, "not json", "s1")  # receiver_error message_parse, kept in the buffer
    rx.evaluate("()=>__receiver.send({type:'next'})")
    rx.wait_for_timeout(2500)
    check(f"receiver: nothing sent and no SDK before the message ({host_hits(rq)}, {vendor_loads(rq)})", not host_hits(rq) and not vendor_loads(rq))
    buffered = rx.evaluate("()=>__telemetry.state().queued")
    check(f"receiver: events kept in the buffer meanwhile ({buffered})", buffered >= 2)
    msg(rx, {"type": "telemetry", "on": True, "installId": "phone-install-1", "test": True, "environment": "verification"}, "s1")
    check("receiver: on after the phone's message, SDK loaded", wait_until(rx, "()=>__telemetry.state().on && __telemetry.state().posthog==='ready'", 10000))
    check("receiver: buffered events sent", wait_until(rx, "()=>__telemetry.sent().some(e=>e.event==='receiver_error' && e.properties.kind==='message_parse')", 5000))
    rx.wait_for_timeout(1000)
    sent = rx.evaluate("()=>__telemetry.sent()")
    pr = [e for e in sent if e["event"] == "receiver_error" and e["properties"].get("kind") == "message_parse"][0]["properties"]
    check(f"receiver: phone's ID, test flag and environment, surface receiver ({ {k: pr.get(k) for k in ('distinct_id','test','environment','surface','device_class','cast_state')} })",
          pr.get("distinct_id") == "phone-install-1" and pr.get("test") is True and pr.get("environment") == "verification" and pr.get("surface") == "receiver" and pr.get("device_class") == "tv" and pr.get("cast_state") == "connected" and pr.get("kind") == "message_parse")
    check("receiver: requests went to the (stubbed) PostHog host", any("posthog" in u for u in host_hits(rq)))
    check("receiver: nothing stored on the TV", rx.evaluate("()=>Object.keys(localStorage).filter(k=>k.startsWith('ph_')||k.startsWith('__ph_')).length") == 0)
    n = len(host_hits(rq))
    msg(rx, "still not json", "s1")
    check("receiver: later events go out one by one", wait_until(rx, "()=>__telemetry.sent().filter(e=>e.event==='receiver_error' && e.properties.kind==='message_parse').length>=2", 4000))
    rx.wait_for_timeout(500)
    check("receiver: and each one is a request (no batching)", len(host_hits(rq)) > n)
    # A second phone says off: off for the rest of this receiver session.
    msg(rx, {"type": "telemetry", "on": False, "installId": None, "test": False, "environment": "production"}, "s2")
    check("receiver: a phone saying off turns it off", rx.evaluate("()=>__telemetry.state().on") is False)
    n, n_sent = len(host_hits(rq)), len(rx.evaluate("()=>__telemetry.sent()"))
    msg(rx, "garbage again", "s1")
    msg(rx, {"type": "telemetry", "on": True, "installId": "phone-install-1", "test": True, "environment": "verification"}, "s1")
    rx.wait_for_timeout(2500)
    check("receiver: stays off for the session, even if the first phone says on again", rx.evaluate("()=>__telemetry.state().on") is False)
    check(f"receiver: nothing sent after off ({host_hits(rq)[n:]})", len(host_hits(rq)) == n and len(rx.evaluate("()=>__telemetry.sent()")) == n_sent)
    check("receiver: the telemetry message changes no player state", rx.evaluate("()=>__receiver.state.type") == "state")
    rx.context.close()

    # receiver.html?debug=1 without ?telemetry=1 sends nothing, even after an on message.
    rx, rq = open_receiver("?debug=1")
    msg(rx, {"type": "telemetry", "on": True, "installId": "phone-install-1", "test": True, "environment": "verification"}, "s1")
    msg(rx, "not json", "s1")
    rx.wait_for_timeout(3000)
    st = rx.evaluate("()=>__telemetry.state()")
    check("receiver debug page: blocked without ?telemetry=1", st["blocked"] is True and st["on"] is False and st["queued"] == 0)
    check("receiver debug page: no SDK and no request", not vendor_loads(rq) and not host_hits(rq))
    rx.context.close()

    # An older phone never sends the message: the receiver keeps working and sends nothing.
    rx, rq = open_receiver("?debug=1&telemetry=1")
    for c in ["{type:'pin', value:true}", "{type:'interval', value:30}", "{type:'getState'}"]:
        rx.evaluate(f"()=>__receiver.send({c})")
    rx.wait_for_timeout(2000)
    check("receiver: works with a phone that never sends the message", rx.evaluate("()=>__receiver.state.pinned && __receiver.state.interval===30"))
    check("receiver: and sends nothing", not vendor_loads(rq) and not host_hits(rq))
    rx.context.close()

    b.close()

real = [h["url"] for h in telemetry_guard.hits if not TELEMETRY_HOST.match(h["url"])]
check("no request escaped the stub", not real)
print("page errors:", page_errors)
check("no page errors", not page_errors)
sys.exit(1 if fails else 0)
