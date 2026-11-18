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
BUILD = "2026-11-17.23"
NS = "urn:x-cast:com.windowseat"
# PostHog drops events from browsers that look automated (webdriver flag, HeadlessChrome brand). These tests need it
# to behave as in a normal Chrome, so they present one. Existing tests keep the headless defaults.
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
NOT_A_BOT = "Object.defineProperty(Navigator.prototype,'webdriver',{get:()=>false});Object.defineProperty(Navigator.prototype,'userAgentData',{get:()=>undefined});"
TELEMETRY_HOST = re.compile(r"^https?://([a-z0-9-]+\.)*(posthog\.com|sentry\.io)(:\d+)?/", re.I)

fails, page_errors = [], []


def expected_error(e):
    """Errors the tests throw on purpose (a rejected string can come through as its tail only)."""
    return "telemetry test error" in str(e) or "example.net/r" in str(e)


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
    pg.on("pageerror", lambda e: page_errors.append(f"{e}") if not expected_error(e) else None)
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


def sentry_events(match=None):
    """Every Sentry event envelope item the stub received (optionally only those whose text contains `match`)."""
    out = []
    for h in telemetry_guard.hits:
        if "sentry.io" not in h["url"] or not h["body"]:
            continue
        lines = h["body"].split(b"\n")
        for i in range(1, len(lines) - 1):
            try:
                hdr = json.loads(lines[i])
            except Exception:
                continue
            if isinstance(hdr, dict) and hdr.get("type") == "event":
                ev = json.loads(lines[i + 1])
                if match is None or match in json.dumps(ev):
                    out.append(ev)
    return out


def infer_never(ev):
    return ((ev.get("sdk") or {}).get("settings") or {}).get("infer_ip") == "never"


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
    # Error text is scrubbed too: a URL's query and fragment, an email address and a token never reach Sentry.
    pg.evaluate("""()=>{
      setTimeout(()=>{throw new Error('telemetry test error scrub https://example.com/p/q?token=SECRETQ1#fragZ9 mail me@example.org key abcdefghijklmnop1234567890')},0);
      Promise.reject('telemetry test error scrub2 https://example.net/r?sig=SECRETQ2#fragY8');
    }""")
    # A user with an IP and geo deliberately seeded on one event each: none of it may leave. The vendor bundle is
    # trimmed (no setUser), so each case adds an event processor to its own scope. One event carries a literal
    # ip_address plus geo, the other "{{auto}}", so each raw value reaches our scrubber unaltered.
    seeded = pg.evaluate("""()=>{
      const geo={country_code:'NL', city:'Amsterdam', region:'Noord-Holland'};
      let ok=0;
      const seed=(user, msg)=>Sentry.withScope((scope)=>{
        if (typeof scope.addEventProcessor!=='function') return;
        scope.addEventProcessor((e)=>{ e.user={...(e.user||{}), ...user}; return e; });
        ok++;
        Sentry.captureException(new Error(msg));
      });
      seed({id:'seeded-user', ip_address:'198.51.100.77', geo}, 'telemetry test error seeded user literal');
      seed({id:'seeded-user', ip_address:'{{auto}}'}, 'telemetry test error seeded user auto');
      return ok===2;
    }""")
    check("seed: an event-scoped processor could be added for both cases", seeded)
    # IP literals in error text and as URL hosts become [ip]; times and line:col stay.
    pg.evaluate("""()=>{setTimeout(()=>{throw new Error('telemetry test error ip client IP: 203.0.113.42 v6 2001:db8:85a3::8a2e:370:7334 hosts http://198.51.100.23:8080/a?k=1 http://[2001:db8::9]:8443/b at 12:30:45 app.js:12:34')},0)}""")
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
    scrubbed = [e["value"] for e in errs if "scrub" in e["value"]]
    check(f"scrub: both errors with URLs in their text went to Sentry, URLs kept bare ({scrubbed})",
          any("https://example.com/p/q" in v and "[email]" in v and "[redacted]" in v for v in scrubbed) and any("https://example.net/r" in v for v in scrubbed))
    leaked = [s for s in (b"SECRETQ1", b"fragZ9", b"me@example.org", b"abcdefghijklmnop1234567890", b"SECRETQ2", b"fragY8", b"token=", b"sig=") if s in bodies]
    check(f"scrub: no query, fragment, email or token from error text in any request body ({leaked})", not leaked and b"example.com/p/q" in bodies)
    # No IP storage: every Sentry event envelope tells ingestion not to infer the IP, and carries no user at all.
    evs = sentry_events()
    no_ip = [infer_never(ev) and "user" not in ev for ev in evs]
    check(f"no IP: Sentry envelopes say infer_ip never and carry no user ({len(no_ip)} events, {[((ev.get('sdk') or {}).get('settings'), ev.get('user')) for ev in evs][:2]})", no_ip and all(no_ip))
    seeded_evs = sentry_events("seeded user")
    check(f"no IP: both events with a seeded user (literal ip_address and geo, {{{{auto}}}}) went out with no user and infer_ip never ({[(ev.get('user'), (ev.get('sdk') or {}).get('settings')) for ev in seeded_evs]})",
          len(seeded_evs) == 2 and all("user" not in ev and infer_never(ev) for ev in seeded_evs))
    seed_leaks = [x for x in (b"198.51.100.77", b"{{auto}}", b"Amsterdam", b"Noord-Holland", b"seeded-user") if x in bodies]
    check(f"no IP: nothing of the seeded user in any request body ({seed_leaks})", not seed_leaks)
    ip_evs = [e["value"] for e in errs if "error ip" in e["value"]]
    check(f"scrub: IPv4, IPv6 and IP URL hosts in error text become [ip], times and line:col stay ({ip_evs})",
          len(ip_evs) == 1 and ip_evs[0].count("[ip]") == 4 and "http://[ip]:8080/a" in ip_evs[0] and "http://[ip]:8443/b" in ip_evs[0] and "12:30:45" in ip_evs[0] and "app.js:12:34" in ip_evs[0])
    ip_leaks = [x for x in (b"203.0.113.42", b"2001:db8", b"198.51.100.23") if x in bodies]
    check(f"scrub: no IP literal from error text in any request body ({ip_leaks})", not ip_leaks)
    cases = {
        "client IP: 203.0.113.42.": "client IP: [ip].",
        "via ::1 and fe80::1%eth0 and ::ffff:192.0.2.1": "via [ip] and [ip] and [ip]",
        "full 2001:0db8:0000:0000:0000:ff00:0042:8329 x": "full [ip] x",
        "bracketed [2001:db8::1] here": "bracketed [ip] here",
        "http://u@10.0.0.1:81/x?q=1": "http://u@[ip]:81/x",
        "at 12:30:45 app.js:12:34 http://localhost:8792/js/app.js:12:34": "at 12:30:45 app.js:12:34 http://localhost:8792/js/app.js:12:34",
        "mapped 2001:db8::192.0.2.1 x": "mapped [ip] x",
        "http://192.0.2.1./a http://127.1/ http://0x7f.1/ http://2130706433/b http://0X7F000001:80/": "http://[ip]/a http://[ip]/ http://[ip]/ http://[ip]/b http://[ip]:80/",
        "http://example.com/ http://1.example.com/ http://v1.2/": "http://example.com/ http://1.example.com/ http://v1.2/",
        "version 1.2.3.4": "version [ip]",
        "std::vector Foo::bar a :: b 1.2.3.4.5 999.1.1.1 v1.2.3": "std::vector Foo::bar a :: b 1.2.3.4.5 999.1.1.1 v1.2.3",
    }
    got = pg.evaluate("(xs)=>xs.map(x=>__telemetry.scrub(x))", list(cases))
    bad_cases = [(k, g) for (k, v), g in zip(cases.items(), got) if g != v]
    check(f"scrub: IP literal cases ({bad_cases})", not bad_cases)
    # Huge error text is cut before any regex runs: 200,000 characters without "@" scrub fast, and a URL cut at the
    # scan limit still loses its query.
    big = pg.evaluate("""()=>{
      const s='a.'.repeat(100000), t0=performance.now(), out=__telemetry.scrub(s), ms=performance.now()-t0;
      const u=__telemetry.scrub('[title="'+'z'.repeat(1160)+'"] https://example.com/p?token=SECRETQ3'+'y'.repeat(500));
      return {ms, len: out.length, u};
    }""")
    check(f"scrub: 200,000 characters scrub in under 200 ms and are capped ({big['ms']:.1f} ms, {big['len']} chars)", big["ms"] < 200 and big["len"] <= 303)
    check(f"scrub: a URL across the scan limit loses its query ({big['u']})", "example.com/p" in big["u"] and "SECRETQ3" not in big["u"] and "token" not in big["u"])
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
        rx.on("pageerror", lambda e: page_errors.append("receiver: " + str(e)) if not expected_error(e) else None)
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
    # Checked after the later event too: everything PostHog was allowed to send (after before_send) so far.
    sent = rx.evaluate("()=>__telemetry.sent()")
    check(f"receiver: every event passed to PostHog so far, buffered and later, has app == window-seat ({len(sent)} events, {sorted({e['properties'].get('app') for e in sent}, key=str)})",
          len(sent) >= 3 and all(e["properties"].get("app") == "window-seat" for e in sent))
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

    # ---------------------------------------------------------------- 7. A verification phone joining a production receiver
    rx, rq = open_receiver("?debug=1&telemetry=1")
    msg(rx, {"type": "telemetry", "on": True, "installId": "phone-install-1", "test": False, "environment": "production"}, "s1")
    check("env: receiver on for a production phone", wait_until(rx, "()=>__telemetry.state().sentry==='ready' && __telemetry.state().posthog==='ready'", 10000))
    check("env: production while only a production phone spoke", rx.evaluate("()=>__telemetry.superProps().environment") == "production")
    msg(rx, {"type": "telemetry", "on": True, "installId": "phone-install-2", "test": True, "environment": "verification"}, "s2")
    sp = rx.evaluate("()=>__telemetry.superProps()")
    check(f"env: a test phone joining makes it a verification session ({sp['environment']}, {sp['test']})", sp["environment"] == "verification" and sp["test"] is True)
    rx.evaluate("()=>{setTimeout(()=>{throw new Error('telemetry test error on the TV')},0)}")
    check("env: the TV error went to Sentry", wait_until(rx, "()=>__telemetry.errorsSent().some(e=>e.value.includes('on the TV'))", 5000))
    e7 = [e for e in rx.evaluate("()=>__telemetry.errorsSent()") if "on the TV" in e["value"]][0]
    check(f"env: test errors are never in production ({e7['environment']}, test {e7['tags']['test']})", e7["environment"] == "verification" and e7["tags"]["test"] == "true")
    rx.wait_for_timeout(1000)
    tv_evs = sentry_events("on the TV")
    check(f"no IP: the receiver's Sentry event says infer_ip never and carries no user ({[(ev.get('user'), (ev.get('sdk') or {}).get('settings')) for ev in tv_evs]})",
          tv_evs and all(infer_never(ev) and "user" not in ev for ev in tv_evs))
    rx.context.close()

    # ---------------------------------------------------------------- 8. Receiver stop waits for PostHog to send what it queued
    ctx = b.new_context(user_agent=UA, viewport={"width": 1920, "height": 1080})
    ctx.add_init_script(NOT_A_BOT)
    held = []
    ctx.route("**/js/vendor/posthog-*", lambda r: held.append(r))  # PostHog still loading when the stop comes
    rx = ctx.new_page()
    rx.on("pageerror", lambda e: page_errors.append("receiver: " + str(e)))
    rx.route("**/iframe_api", lambda r: r.abort())
    rx.route(re.compile(r"docs\.google\.com"), lambda r: r.abort())
    rx.goto(f"{base}/receiver.html?debug=1&telemetry=1")
    rx.wait_for_timeout(1000)
    # A Cast context that records each stop and what PostHog had been given by then.
    rx.evaluate("""()=>{window.__stops=[];__receiver.useContext({
      stop(){ window.__stops.push(__telemetry.sent().map(e=>e.event)); },
      sendCustomMessage(){}, getSenders(){ return [{id:'s1'}]; }, setApplicationState(){}, getApplicationData(){ return null; },
    })}""")
    msg(rx, {"type": "telemetry", "on": True, "installId": "phone-install-1", "test": True, "environment": "verification"}, "s1")
    wait_until(rx, "()=>__telemetry.state().posthog==='loading'", 8000)
    rx.evaluate("()=>__receiver.send({type:'stop'})")
    rx.evaluate("()=>__receiver.send({type:'stop'})")
    rx.wait_for_timeout(500)
    check(f"stop: not stopped while PostHog still holds the queued receiver_stop ({rx.evaluate('()=>__stops')})", rx.evaluate("()=>__stops.length") == 0)
    check("stop: one receiver_stop queued for two stop commands", [q["name"] for q in rx.evaluate("()=>__telemetry.queue()")].count("receiver_stop") == 1)
    for r in held:
        r.continue_()
    ctx.unroute("**/js/vendor/posthog-*")
    check("stop: stopped once PostHog had sent the queue", wait_until(rx, "()=>__stops.length>=1", 5000))
    rx.wait_for_timeout(1500)
    stops = rx.evaluate("()=>__stops")
    check(f"stop: exactly one context.stop() ({len(stops)})", len(stops) == 1)
    check(f"stop: receiver_stop went out before the stop, once ({stops[:1]})", stops and stops[0].count("receiver_stop") == 1)
    check("stop: no second receiver_stop afterwards", [e["event"] for e in rx.evaluate("()=>__telemetry.sent()")].count("receiver_stop") == 1)
    ctx.close()

    # ---------------------------------------------------------------- 9. A failed telemetry send to the TV is not marked sent
    pg, reqs = open_phone(b, query="?telemetry_test=1")
    wait_until(pg, "()=>__telemetry.state().posthog==='ready'", 10000)
    pg.evaluate("()=>__mockCast.start()")
    tel_msgs = lambda: [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent") if json.loads(m["data"]).get("type") == "telemetry"]
    check("tv consent: on sent at connect", wait_until(pg, "()=>__mockCast.sent.some(m=>JSON.parse(m.data).type==='telemetry')", 3000) and tel_msgs()[-1]["on"] is True)
    status_before = pg.text_content("#remote-status")
    pg.evaluate("()=>{window.__mockFail={code:'channel_error'}; const t=document.getElementById('telemetry-toggle');t.checked=false;t.dispatchEvent(new Event('change'))}")
    pg.wait_for_timeout(300)
    offs = [m for m in tel_msgs() if m["on"] is False]
    check(f"tv consent: off attempted ({len(offs)})", len(offs) == 1)
    check(f"tv consent: a telemetry failure leaves the Cast status and toast alone ({pg.text_content('#remote-status')!r}, {pg.text_content('#toast')!r})",
          pg.text_content("#remote-status") == status_before and "Send failed" not in (pg.text_content("#toast") or "") and "Send failed" not in (pg.text_content(".cast-status") or ""))
    check("tv consent: failed off retried", wait_until(pg, "()=>__mockCast.sent.filter(m=>JSON.parse(m.data).type==='telemetry' && JSON.parse(m.data).on===false).length>=2", 2500))
    pg.evaluate("()=>{window.__mockFail=null}")
    n_off = len([m for m in tel_msgs() if m["on"] is False])
    pg.evaluate("()=>__mockCast.start()")  # the same session object again (as on resume)
    pg.wait_for_timeout(300)
    check(f"tv consent: the same session sends the undelivered off again ({n_off} before)", len([m for m in tel_msgs() if m["on"] is False]) == n_off + 1)
    pg.wait_for_timeout(9000)  # past every retry
    n_off = len([m for m in tel_msgs() if m["on"] is False])
    pg.evaluate("()=>__mockCast.start()")
    pg.evaluate("()=>__mockCast.fireSession('applicationstatuschanged')")
    pg.wait_for_timeout(500)
    check("tv consent: once delivered, it is not sent again", len([m for m in tel_msgs() if m["on"] is False]) == n_off)
    pg.context.close()

    # ---------------------------------------------------------------- 10. Off in one open copy turns the others off
    pg, reqs = open_phone(b, query="?telemetry_test=1")
    other = pg.context.new_page()
    other.on("pageerror", lambda e: page_errors.append("other tab: " + str(e)) if not expected_error(e) else None)
    other.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK_SENDER))
    other.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    other.route(re.compile(r"docs\.google\.com"), lambda r: r.abort())
    other.goto(f"{base}/?telemetry_test=1")
    check("tabs: both copies on", wait_until(pg, "()=>__telemetry.state().posthog==='ready'", 10000) and wait_until(other, "()=>__telemetry.state().posthog==='ready'", 10000))
    other.evaluate("()=>__mockCast.start()")
    wait_until(other, "()=>__mockCast.sent.some(m=>JSON.parse(m.data).type==='telemetry' && JSON.parse(m.data).on===true)", 3000)
    pg.evaluate("()=>{const t=document.getElementById('telemetry-toggle');t.checked=false;t.dispatchEvent(new Event('change'))}")
    check("tabs: the other copy turns off", wait_until(other, "()=>__telemetry.state().on===false", 3000))
    check("tabs: its switch follows, and its PostHog is opted out", other.evaluate("()=>!document.getElementById('telemetry-toggle').checked && posthog.has_opted_out_capturing()"))
    check("tabs: its TV is told off", wait_until(other, "()=>__mockCast.sent.some(m=>JSON.parse(m.data).type==='telemetry' && JSON.parse(m.data).on===false)", 3000))
    n_hits, n_sent = len(host_hits(reqs)), len(other.evaluate("()=>__telemetry.sent()"))
    other.evaluate("()=>__mockCast.reply({type:'state', build:'%s', paused:false})" % BUILD)
    other.locator("#remote [data-cmd='next']").click()
    other.evaluate("()=>{setTimeout(()=>{throw new Error('telemetry test error in the other tab')},0)}")
    other.wait_for_timeout(4000)
    check(f"tabs: the other copy sends nothing after off ({host_hits(reqs)[n_hits:]})", len(host_hits(reqs)) == n_hits and len(other.evaluate("()=>__telemetry.sent()")) == n_sent)
    pg.evaluate("()=>{const t=document.getElementById('telemetry-toggle');t.checked=true;t.dispatchEvent(new Event('change'))}")
    check("tabs: on again follows too", wait_until(other, "()=>__telemetry.state().on===true && document.getElementById('telemetry-toggle').checked", 3000))
    other.wait_for_timeout(1500)
    check("tabs: only the copy that was switched sends telemetry_enabled", not any(e["event"] == "telemetry_enabled" for e in other.evaluate("()=>__telemetry.sent()")))
    pg.context.close()

    # ---------------------------------------------------------------- 11. Cast funnel: late session callback, a channel that never opens
    pg, reqs = open_phone(b, query="?telemetry_test=1", init="window.__mockNs=[];")
    wait_until(pg, "()=>__telemetry.state().posthog==='ready'", 10000)
    wait_until(pg, "()=>document.querySelector('[data-cast]') && !document.querySelector('[data-cast]').hidden && __windowSeat && true", 3000)
    pg.wait_for_timeout(500)
    # requestSession resolves first; the session callback comes 300 ms later.
    pg.evaluate("()=>{const c=cast.framework.CastContext.getInstance(); c.requestSession=()=>{setTimeout(()=>__mockCast.start(),300); return Promise.resolve();}; document.querySelector('[data-cast]').click();}")
    check("funnel: success counted", wait_until(pg, "()=>__telemetry.sent().some(e=>e.event==='cast_connect_success')", 5000))
    sent = pg.evaluate("()=>__telemetry.sent()")
    att = [e["properties"] for e in sent if e["event"] == "cast_connect_attempt"]
    ok = [e["properties"] for e in sent if e["event"] == "cast_connect_success"]
    check(f"funnel: a tap whose promise resolved first is still a tap with its attempt_id ({ok})", att and ok and ok[0]["trigger"] == "tap" and ok[0].get("attempt_id") == att[0]["attempt_id"] and isinstance(ok[0].get("ms_to_connect"), int))
    check("funnel: and not a resume", not any(e["event"] == "cast_resume" for e in sent))
    check("funnel: a channel that never opens is reported without any command", wait_until(pg, "()=>__telemetry.sent().some(e=>e.event==='cast_channel_missing')", 11000))
    pg.wait_for_timeout(5000)
    check("funnel: once per session", [e["event"] for e in pg.evaluate("()=>__telemetry.sent()")].count("cast_channel_missing") == 1)
    pg.context.close()

    # ---------------------------------------------------------------- 12. Cam pool: one command per action, nothing across off and on
    pg, reqs = open_phone(b, query="?telemetry_test=1")
    wait_until(pg, "()=>__telemetry.state().posthog==='ready'", 10000)
    pg.evaluate("()=>document.querySelector('[data-action=open-cams]').click()")
    pg.wait_for_timeout(300)
    toggle_cam = lambda i: pg.evaluate(f"()=>document.querySelectorAll('[data-action=toggle-cam]')[{i}].click()")
    set_tel = lambda on: pg.evaluate("(on)=>{const t=document.getElementById('telemetry-toggle');t.checked=on;t.dispatchEvent(new Event('change'))}", on)
    toggle_cam(0)
    toggle_cam(1)
    pg.wait_for_timeout(3000)
    names = [e["event"] for e in pg.evaluate("()=>__telemetry.sent()")]
    cams_cmds = [e["properties"] for e in pg.evaluate("()=>__telemetry.sent()") if e["event"] == "command" and e["properties"].get("type") == "cams"]
    check(f"pool: one command per action, one settled cam_pool_changed ({len(cams_cmds)}, {names.count('cam_pool_changed')})", len(cams_cmds) == 2 and names.count("cam_pool_changed") == 1 and all(c["mode"] == "local" for c in cams_cmds))
    n_sent = len(pg.evaluate("()=>__telemetry.sent()"))
    set_tel(False)
    toggle_cam(2)  # changed while off
    set_tel(True)
    pg.wait_for_timeout(3000)
    after = [e["event"] for e in pg.evaluate("()=>__telemetry.sent()")[n_sent:]]
    check(f"pool: a change made while off is never sent after turning on ({after})", "cam_pool_changed" not in after and "command" not in after)
    n_sent = len(pg.evaluate("()=>__telemetry.sent()"))
    toggle_cam(3)
    set_tel(False)
    set_tel(True)
    pg.wait_for_timeout(3000)
    after = [e["event"] for e in pg.evaluate("()=>__telemetry.sent()")[n_sent:]]
    check(f"pool: an off and on cycle drops the pending change ({after})", "cam_pool_changed" not in after)
    pg.context.close()

    b.close()

real = [h["url"] for h in telemetry_guard.hits if not TELEMETRY_HOST.match(h["url"])]
check("no request escaped the stub", not real)
print("page errors:", page_errors)
check("no page errors", not page_errors)
sys.exit(1 if fails else 0)
