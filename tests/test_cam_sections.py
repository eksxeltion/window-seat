"""Cams sheet country sections: order and counts, open or closed saved per country, the Playing section opening on its own,
search, bulk Add all / Remove all with Undo, and the rows still working inside a section (local player and casting remote).
No live sheet: gviz is aborted, so the built-in list (or a saved copy) is used.
Usage: test_cam_sections.py BASE_URL   (the site root, for example http://localhost:8000/)"""
import sys, os, json
from playwright.sync_api import sync_playwright
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import telemetry_guard  # noqa: E402,F401  stubs PostHog and Sentry: tests never reach the real hosts

base = sys.argv[1].rstrip("/")
if base.endswith(".html"):
    base = base.rsplit("/", 1)[0]
here = os.path.dirname(os.path.abspath(__file__))
FAKE_YT = open(os.path.join(here, "fake_youtube.js")).read()
MOCK = open(os.path.join(here, "mock_cast_sender.js")).read()

NOON_EU = "2026-11-17T12:30:00Z"
SHINJUKU = "u7kCTaCX_J4"
EIFFEL = "QI-PPEW2Bgc"
fails, logs = [], []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def wait_until(pg, js, ms=5000):
    try:
        pg.wait_for_function(js, timeout=ms)
        return True
    except Exception:
        return False


def route(pg):
    pg.route("**/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=MOCK))
    pg.route("**/cast_receiver_framework.js", lambda r: r.abort())
    pg.route("https://i.ytimg.com/**", lambda r: r.abort())
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    pg.route("**/gviz/tq*", lambda r: r.abort())
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))


def ws(pg, expr):
    return pg.evaluate(f"()=>__windowSeat.{expr}")


def start(pg):
    pg.click("#start-btn")
    return wait_until(pg, "()=>__windowSeat.currentCamId() && !__windowSeat.state.pending && __yt.cam().state===1", 8000)


def open_cams(pg, remote=False):
    pg.click(("#remote" if remote else "#bar") + " [data-action=open-cams]")
    pg.wait_for_selector("#panel-cams:not([hidden])")


def close_cams(pg):
    pg.click("#panel-cams [data-action=close-panel]")
    pg.wait_for_selector("#panel-cams", state="hidden")


def sec(c):
    return f'#cams-list .cam-country[data-country="{c}"]'


def head(c):
    return f'#cams-list .cam-country-head[data-country="{c}"]'


def is_open(pg, c):
    return pg.get_attribute(head(c), "aria-expanded") == "true"


def settle(pg):
    pg.wait_for_timeout(280)  # the 200ms height animation


def open_section(pg, c):
    if not is_open(pg, c):
        pg.click(head(c))
        settle(pg)


def row(cid):
    return f'#cams-list .cam-row[data-id="{cid}"]'


def checked(pg, cid):
    return pg.get_attribute(f'{row(cid)} [data-action="toggle-cam"]', "aria-checked") == "true"


def count_text(pg, c):
    return pg.text_content(f"{head(c)} .cc-count")


def in_view(pg, selector):
    return pg.evaluate("""(s)=>{const b=document.querySelector('#panel-cams .panel-body').getBoundingClientRect(); const r=document.querySelector(s).getBoundingClientRect();
      return r.height>0 && r.top>=b.top-1 && r.bottom<=b.bottom+1}""", selector)


def play_cam(pg, cid, country, remote=False):
    """Play a cam from its row (opening its section first) and wait until it is the one showing."""
    open_section(pg, country)
    pg.click(f"{row(cid)} [data-action=watch]")
    if remote:
        pg.wait_for_timeout(150)
        return True
    ok = wait_until(pg, f"()=>__windowSeat.currentCamId()==='{cid}' && !__windowSeat.state.pending && __yt.cam().state===1", 6000)
    open_cams(pg)
    return ok


SAVED = {
    "at": 1,
    "cams": [
        {"id": SHINJUKU, "title": "Shinjuku Station", "country": "Japan", "city": "Tokyo", "place": "Tokyo", "type": "street", "channel": "C", "default": True, "timezone": "Asia/Tokyo"},
        {"id": EIFFEL, "title": "Eiffel Tower", "country": "France", "city": "Paris", "place": "Paris", "type": "skyline", "channel": "C", "default": True, "timezone": "Europe/Paris"},
        {"id": "Yzl9offu5g8", "title": "Naha Airport", "country": "Japan", "city": "Naha", "place": "Naha", "type": "airport", "channel": "C", "default": True, "timezone": "Asia/Tokyo"},
    ],
    "mixes": [{"id": "nCtOCLv6eFY", "title": "M1", "en": "Mix one", "minutes": 60}],
}

with sync_playwright() as p:
    b = p.chromium.launch()

    def phone(init=None):
        ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        if init:
            ctx.add_init_script(init)
        pg = ctx.new_page()
        pg.clock.set_fixed_time(NOON_EU)
        route(pg)
        pg.goto(base + "/index.html")
        pg.wait_for_timeout(800)
        return pg

    # ------------------------------------------------------------ 1. order, counts, chrome
    pg = phone()
    cams = pg.evaluate("async()=>(await import('/js/data.js')).CAMS.map(c=>({id:c.id,country:c.country,default:!!c.default,title:c.title,city:c.city,region:c.region||'',type:c.type||'',tags:c.tags||[]}))")
    order = []
    for c in cams:
        if c["country"] not in order:
            order.append(c["country"])
    sizes = {c: sum(1 for x in cams if x["country"] == c) for c in order}
    check("started", start(pg))
    open_cams(pg)
    names = pg.evaluate("()=>[...document.querySelectorAll('#cams-list .cam-country')].map(s=>s.dataset.country)")
    check(f"sections follow CAMS first-seen country order ({names})", names == order and names[0] == "Japan" and len(names) == 9)
    check("every row is in the DOM under #cams-list .cam-row (44)", pg.locator("#cams-list .cam-row").count() == 44)
    counts = pg.evaluate("()=>[...document.querySelectorAll('#cams-list .cc-count')].map(n=>n.textContent)")
    want_counts = [f"{sum(1 for x in cams if x['country'] == c and x['default'])} of {sizes[c]} in rotation" for c in order]
    check(f"counts read 'N of M in rotation' from the checked cams ({counts})", counts == want_counts)
    check("Japan counts 27 of 28 (one archive cam is off by default) and Italy 3 of 3", count_text(pg, "Japan") == "27 of 28 in rotation" and count_text(pg, "Italy") == "3 of 3 in rotation")
    pg.click(f"{head('Japan')}")
    settle(pg)
    pg.evaluate("()=>document.querySelector('#panel-cams .panel-body').scrollTop=0")
    pg.wait_for_timeout(250)
    chrome = pg.evaluate("""()=>{const h=document.querySelector('#cams-list .cam-country-head[data-country=Japan]'); const cs=getComputedStyle(h); const n=getComputedStyle(h.querySelector('.cc-name')); const c=getComputedStyle(h.querySelector('.cc-meta'));
      const secs=[...document.querySelectorAll('#cams-list .cam-country')]; const a=secs[0].getBoundingClientRect(), b2=secs[1].getBoundingClientRect(); const cr=getComputedStyle(secs[0]).borderRadius;
      return {h: h.getBoundingClientRect().height, w: h.getBoundingClientRect().width===secs[0].getBoundingClientRect().width-2, tag: h.tagName, name: [n.fontSize, n.fontWeight, n.lineHeight], meta: [c.fontSize, c.fontWeight, c.lineHeight, c.fontVariantNumeric], gap: b2.top-a.bottom, radius: cr,
        controls: h.getAttribute('aria-controls'), region: document.getElementById(h.getAttribute('aria-controls')).getAttribute('role'), by: document.getElementById(h.getAttribute('aria-controls')).getAttribute('aria-labelledby')===h.id,
        sticky: getComputedStyle(h.parentElement).position, chev: h.querySelector('.cc-chevron').getBoundingClientRect().width, flag: /\\p{Regional_Indicator}/u.test(h.textContent)}}""")
    check(f"header: 56px button, name 16/24 600, count 14/20 500 tabular, 24px chevron, 24px between sections, radius 16, sticky ({chrome})",
          chrome["h"] == 56 and chrome["tag"] == "BUTTON" and chrome["name"] == ["16px", "600", "24px"] and chrome["meta"][:3] == ["14px", "500", "20px"] and "tabular-nums" in chrome["meta"][3]
          and chrome["gap"] == 24 and chrome["radius"] == "16px" and chrome["sticky"] == "sticky" and chrome["chev"] == 24 and not chrome["flag"] and chrome["region"] == "region" and chrome["by"])
    check("tapping the header closed Japan", not is_open(pg, "Japan"))
    tc = next(c for c in order if not is_open(pg, c))  # a closed section (the Playing one is always open)
    chev = lambda: pg.evaluate("(c)=>getComputedStyle(document.querySelector(`#cams-list .cam-country[data-country=\"${c}\"] .cc-chevron`)).transform", tc)
    rot = chev()
    open_section(pg, tc)
    rot_open = chev()
    check(f"chevron rotates 180deg when open ({rot} -> {rot_open})", rot in ("none", "matrix(1, 0, 0, 1, 0, 0)") and rot_open.startswith("matrix(-1"))
    check("a collapsed section's rows are not visible, an open one's are", not pg.is_visible(f"{sec(next(c for c in order if c != tc and not is_open(pg, c)))} .cam-row") and pg.is_visible(f"{sec(tc)} .cam-row"))
    anim = pg.evaluate("()=>{const b=getComputedStyle(document.querySelector('#cams-list .cam-country-body')); return [b.transitionDuration, b.transitionTimingFunction]}")
    check(f"height animates 200ms with cubic-bezier(0.05, 0.7, 0.1, 1) ({anim})", "0.2s" in anim[0] and "cubic-bezier(0.05, 0.7, 0.1, 1)" in anim[1])
    pg.context.close()

    # ------------------------------------------------------------ 2. default and saved open / closed
    pg = phone()
    check("fresh: started", start(pg))
    open_cams(pg)
    playing_country = pg.evaluate("()=>document.querySelector('#cams-list .cam-row.is-current').closest('.cam-country').dataset.country")
    want = {c: (c == "Japan" or c == playing_country) for c in order}
    check(f"default: Japan and the Playing cam's section ({playing_country}) open, the rest collapsed", {c: is_open(pg, c) for c in order} == want)
    picks = [c for c in order if c not in (playing_country, "Japan")][:2]
    for c in picks:
        open_section(pg, c)
    pg.click(head("Japan"))
    settle(pg)
    saved = pg.evaluate("()=>JSON.parse(localStorage.getItem('windowSeat.camsOpen'))")
    check(f"saved under windowSeat.camsOpen ({saved})", saved.get("Japan") is False and all(saved.get(c) is True for c in picks))
    pg.reload()
    pg.wait_for_timeout(800)
    check("reload: started", start(pg))
    open_cams(pg)
    pc = pg.evaluate("()=>document.querySelector('#cams-list .cam-row.is-current').closest('.cam-country').dataset.country")
    state = {c: is_open(pg, c) for c in order}
    exp = {c: (c == pc or c in picks) for c in order}
    check(f"reload: open/closed survives, only the Playing section ({pc}) is forced open ({state})", state == exp)
    check("reload: Japan stays closed when the Playing cam is elsewhere (or is open because it plays)", is_open(pg, "Japan") == (pc == "Japan"))
    pg.context.close()

    # ------------------------------------------------------------ 3. the Playing section opens on its own and scrolls into view
    pg = phone()
    check("playing: started", start(pg))
    open_cams(pg)
    check("playing: play the Paris cam from its row", play_cam(pg, EIFFEL, "France"))
    check("playing: the Playing section is open and the row is in view on open", is_open(pg, "France") and in_view(pg, row(EIFFEL)))
    pg.click(head("France"))
    settle(pg)
    check("playing: tapping the header closes it (saved collapsed)", not is_open(pg, "France") and pg.evaluate("()=>JSON.parse(localStorage.getItem('windowSeat.camsOpen')).France") is False)
    check("playing: a small accent dot marks the collapsed Playing section", pg.is_visible(f"{head('France')} .cc-dot") and pg.evaluate("()=>getComputedStyle(document.querySelector('#cams-list .cc-dot:not([hidden])')).backgroundColor") == "rgb(47, 111, 138)")
    pg.click("#panel-cams [data-action=close-panel]")
    pg.wait_for_selector("#panel-cams", state="hidden")
    open_cams(pg)
    check("playing: reopening forces France open even though it was saved collapsed, and scrolls it into view", is_open(pg, "France") and in_view(pg, row(EIFFEL)))
    check("playing: the saved choice itself is untouched", pg.evaluate("()=>JSON.parse(localStorage.getItem('windowSeat.camsOpen')).France") is False)
    pg.context.close()

    # ------------------------------------------------------------ 4. rows inside a section: local player
    pg = phone()
    check("local: started", start(pg))
    open_cams(pg)
    open_section(pg, "Japan")
    before = checked(pg, SHINJUKU)
    pg.click(f"{row(SHINJUKU)} [data-action=toggle-cam]")
    check("local: the rotation checkbox in a section toggles", checked(pg, SHINJUKU) != before and not ws(pg, f"isEnabled('{SHINJUKU}')") and count_text(pg, "Japan") == "26 of 28 in rotation")
    pg.click(f"{row(SHINJUKU)} [data-action=toggle-cam]")
    check("local: and toggles back", checked(pg, SHINJUKU) and count_text(pg, "Japan") == "27 of 28 in rotation")
    check("local: play now from a section row", play_cam(pg, SHINJUKU, "Japan"))
    close_cams(pg)
    pg.click("#pin-pill")
    check("local: pin from the player still works", ws(pg, "pinned()") is True)
    pg.click("#pin-pill")
    pg.context.close()

    # ------------------------------------------------------------ 4b. rows inside a section: casting remote
    pg = phone()
    check("remote: started", start(pg))
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(400)
    pg.evaluate("()=>__mockCast.reply({type:'state',camId:'" + SHINJUKU + "',camTitle:'Shinjuku',mixId:'nCtOCLv6eFY',mixTitle:'X',pinned:false,interval:60,volume:60,muted:false,paused:false})")
    pg.wait_for_timeout(200)
    open_cams(pg, remote=True)
    check("remote: the TV's cam is in an open, in-view section", is_open(pg, "Japan") and in_view(pg, row(SHINJUKU)))
    open_section(pg, "Italy")
    ital = pg.evaluate("()=>document.querySelector('#cams-list .cam-country[data-country=Italy] .cam-row').dataset.id")
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click(f"{row(ital)} [data-action=toggle-cam]")
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    cm = [m for m in msgs if m.get("type") == "cams"]
    check(f"remote: unchecking a cam in a section sends the pool to the TV ({len(cm)})", len(cm) == 1 and ital not in cm[0]["ids"] and not checked(pg, ital))
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click(f"{row(ital)} [data-action=watch]")
    pg.wait_for_timeout(200)
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check("remote: play now from a section row plays on the TV", any(m.get("type") == "cam" and m.get("id") == ital for m in msgs))
    pg.context.close()

    # ------------------------------------------------------------ 5. search
    pg = phone()
    check("search: started", start(pg))
    open_cams(pg)
    pg.evaluate("()=>document.querySelector('#panel-cams .panel-body').scrollTop=0")
    check("search: the field sits above the sections", pg.evaluate("()=>document.querySelector('#cams-search').getBoundingClientRect().bottom <= document.querySelector('#cams-list').getBoundingClientRect().top"))
    open_section(pg, "Italy")  # saved open, to be ignored while searching and back afterwards
    pg.click(head("Italy"))
    settle(pg)
    before = {c: is_open(pg, c) for c in order}
    q = "harbor"

    def matches(c):
        return sum(1 for x in cams if x["country"] == c and q in " ".join([x["title"], x["city"], x["region"], x["country"], x["type"], *x["tags"]]).lower())
    exp_counts = {c: matches(c) for c in order}
    pg.fill("#cams-search", q)
    pg.wait_for_timeout(300)
    vis = {c: pg.is_visible(sec(c)) for c in order}
    check(f"search '{q}': only sections with matches show ({exp_counts})", vis == {c: n > 0 for c, n in exp_counts.items()} and sum(vis.values()) >= 2)
    check("search: every section with matches is open, whatever was saved", all(is_open(pg, c) for c in order if exp_counts[c]))
    heads = {c: count_text(pg, c) for c in order if exp_counts[c]}
    check(f"search: header counts read 'N matches' ({heads})", all(t == (f"{exp_counts[c]} matches" if exp_counts[c] != 1 else "1 match") for c, t in heads.items()) and any(t.endswith("matches") for t in heads.values()))
    shown = pg.evaluate("()=>[...document.querySelectorAll('#cams-list .cam-row')].filter(r=>!r.hidden).length")
    check(f"search: only the matching rows show ({shown})", shown == sum(exp_counts.values()))
    check("search: no empty line while there are matches and no Add all control", not pg.is_visible("#cams-empty") and not pg.is_visible(f"{sec('Japan')} [data-action=bulk-country]"))
    pg.click('#cams-quick [data-group="country:Japan"]')
    check("search: quick picks still change checks only, not which rows show", pg.evaluate("()=>[...document.querySelectorAll('#cams-list .cam-row')].filter(r=>!r.hidden).length") == shown)
    pg.click('#cams-quick [data-group="all"]')
    pg.fill("#cams-search", "")
    pg.wait_for_timeout(300)
    after = {c: is_open(pg, c) for c in order}
    check(f"search: clearing restores the saved open/closed state ({after})", after == before and all(pg.is_visible(sec(c)) for c in order))
    pg.fill("#cams-search", "zzzq")
    pg.wait_for_timeout(200)
    check(f"search: empty line reads No cams match \"zzzq\" ({pg.text_content('#cams-empty')!r})", pg.is_visible("#cams-empty") and pg.text_content("#cams-empty") == 'No cams match "zzzq"' and not any(pg.is_visible(sec(c)) for c in order))
    pg.fill("#cams-search", "  ZZZQ ")
    check("search: the empty line shows the query as typed, trimmed", pg.text_content("#cams-empty") == 'No cams match "ZZZQ"')
    pg.fill("#cams-search", "")
    pg.focus("#cams-search")
    pg.keyboard.type("m n p")
    check("search: typing letters does not trigger player shortcuts or close the sheet", pg.is_visible("#panel-cams") and pg.input_value("#cams-search") == "m n p" and not ws(pg, "pinned()"))
    pg.context.close()

    # ------------------------------------------------------------ 6. bulk Add all / Remove all and Undo
    pg = phone("localStorage.setItem('windowSeat.sheetConfig', " + json.dumps(json.dumps(SAVED)) + ")")
    check("bulk: started", start(pg))
    pg.evaluate("()=>__mockCast.start()")
    pg.wait_for_timeout(400)
    pg.evaluate("()=>__mockCast.reply({type:'state',camId:'" + SHINJUKU + "',camTitle:'S',mixId:'nCtOCLv6eFY',mixTitle:'X',pinned:false,interval:60,volume:60,muted:false,paused:false})")
    pg.wait_for_timeout(200)
    open_cams(pg, remote=True)
    check("bulk: saved copy gives Japan and France sections", pg.evaluate("()=>[...document.querySelectorAll('#cams-list .cam-country')].map(s=>s.dataset.country)") == ["Japan", "France"])
    open_section(pg, "France")
    bulk = lambda c: f"{sec(c)} [data-action=bulk-country]"
    check("bulk: every cam on reads 'Remove all'", pg.text_content(bulk("Japan")) == "Remove all" and pg.text_content(bulk("France")) == "Remove all")
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click(bulk("Japan"))
    check("bulk: Remove all unchecks the section", not checked(pg, SHINJUKU) and not checked(pg, "Yzl9offu5g8") and checked(pg, EIFFEL) and count_text(pg, "Japan") == "0 of 2 in rotation")
    check("bulk: a zero count uses the tertiary ink", pg.evaluate("()=>getComputedStyle(document.querySelector('#cams-list .cam-country[data-country=Japan] .cc-count')).color") == "rgb(90, 107, 118)")
    snack = pg.text_content("#cams-snack-text")
    check(f"bulk: snackbar says '2 Japan cams removed.' with Undo ({snack})", pg.is_visible("#cams-snack") and snack == "2 Japan cams removed." and pg.is_visible("#cams-snack [data-action=undo-bulk]"))
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    cm = [m for m in msgs if m.get("type") == "cams"]
    check(f"bulk: one pool message to the TV, the setManyEnabled path ({len(cm)})", len(cm) == 1 and SHINJUKU not in cm[0]["ids"] and EIFFEL in cm[0]["ids"])
    check("bulk: the button now reads 'Add all to rotation'", pg.text_content(bulk("Japan")) == "Add all to rotation")
    pg.click("#cams-snack [data-action=undo-bulk]")
    check("bulk: Undo restores the previous checks and hides the snackbar", checked(pg, SHINJUKU) and checked(pg, "Yzl9offu5g8") and not pg.is_visible("#cams-snack") and count_text(pg, "Japan") == "2 of 2 in rotation")
    pg.click(row(SHINJUKU) + " [data-action=toggle-cam]")  # now Japan is 1 of 2
    pg.click(bulk("Japan"))
    check(f"bulk: Add all on a partly checked section adds the rest, singular line ({pg.text_content('#cams-snack-text')!r})", pg.text_content("#cams-snack-text") == "1 Japan cam added." and checked(pg, SHINJUKU))
    pg.click("#cams-snack [data-action=undo-bulk]")
    check("bulk: Undo of an Add all unchecks only the cam it added, the first stays checked", not checked(pg, SHINJUKU) and checked(pg, "Yzl9offu5g8") and not pg.is_visible("#cams-snack"))
    pg.click(bulk("Japan"))
    pg.click(bulk("France"))
    check(f"bulk: one pending Undo at a time, the newest wins ({pg.text_content('#cams-snack-text')!r})", pg.text_content("#cams-snack-text") == "1 France cam removed." and pg.locator("#cams-snack").count() == 1)
    pg.evaluate("()=>__mockCast.sent.length=0")
    pg.click("#cams-snack [data-action=undo-bulk]")
    cm = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    cm = [m for m in cm if m.get("type") == "cams"]
    check(f"bulk: Undo sends the restored pool to the TV once ({len(cm)})", len(cm) == 1 and EIFFEL in cm[0]["ids"])
    check("bulk: that Undo only restores France", checked(pg, EIFFEL) and checked(pg, SHINJUKU))
    pg.click(bulk("France"))
    pg.focus("#cams-snack [data-action=undo-bulk]")
    pg.wait_for_timeout(5400)
    still = pg.is_visible("#cams-snack")
    pg.wait_for_timeout(900)
    check(f"bulk: the snackbar stays about 6 seconds then goes ({still})", still and not pg.is_visible("#cams-snack"))
    check("bulk: focus on Undo returns to the section button when it goes", pg.evaluate("()=>document.activeElement.dataset.action")  in ("bulk-country", "close-panel"))
    check("bulk: Undo is a no-op once it is gone, checks stay", not checked(pg, EIFFEL))
    pg.context.close()

    # ------------------------------------------------------------ 6b. whole-row toggle, play does not toggle, type-only subtitles
    pg = phone()
    check("rowtap: started", start(pg))
    open_cams(pg)
    open_section(pg, "Japan")
    tgl = f'{row(SHINJUKU)} [data-action="toggle-cam"]'
    box = lambda sel: pg.locator(sel).bounding_box()
    r0 = box(row(SHINJUKU))
    for label, pos in (("title", None), ("clock", None), ("left edge", {"x": 1, "y": r0["height"] / 2})):
        before = checked(pg, SHINJUKU)
        sel = {"title": f"{row(SHINJUKU)} .cam-title", "clock": f"{row(SHINJUKU)} .cam-clock"}.get(label, row(SHINJUKU))
        pg.locator(sel).click(position=pos) if pos else pg.locator(sel).click()
        check(f"rowtap: a tap on the {label} flips the check", checked(pg, SHINJUKU) != before)
    pb = box(f"{row(SHINJUKU)} [data-action=watch]")
    check(f"rowtap: the play hit area is 48dp ({pb['width']}x{pb['height']})", abs(pb["width"] - 48) < 0.6 and abs(pb["height"] - 48) < 0.6)
    dot = box(f"{row(SHINJUKU)} .cam-play-dot")
    check(f"rowtap: the visual play circle stays 32px ({dot['width']})", abs(dot["width"] - 32) < 0.6)
    check("rowtap: one toggle control per row, play is not nested in it", pg.evaluate("(s)=>{const r=document.querySelector(s);return r.querySelectorAll('[data-action=toggle-cam]').length===1 && !r.querySelector('[data-action=toggle-cam] [data-action=watch]')}", row(SHINJUKU)))
    before = checked(pg, SHINJUKU)
    pg.click(f"{row(SHINJUKU)} [data-action=watch]")
    ok = wait_until(pg, f"()=>__windowSeat.currentCamId()==='{SHINJUKU}' && !__windowSeat.state.pending", 6000)
    open_cams(pg)
    check("rowtap: play plays the cam and leaves the check alone", ok and checked(pg, SHINJUKU) == before)
    for c in ("Japan", "France"):
        open_section(pg, c)
    wh = pg.evaluate("()=>[...document.querySelectorAll('#cams-list .cam-where')].map(x=>x.textContent)")
    check("subtitle: inside a section it is the type only, no country", wh and all("·" not in w for w in wh) and "Street" in wh and "Harbor" in wh)
    check("subtitle: Japan Street and France Harbor never appear", "Japan · Street" not in wh and "France · Harbor" not in wh)
    check("copy: standalone count has no trailing period", not pg.text_content("#cams-count").endswith("."))
    pg.context.close()

    # ------------------------------------------------------------ 6c. pinned search, stuck headers
    pg = phone()
    check("stuck: started", start(pg))
    open_cams(pg)
    pg.evaluate("()=>document.querySelector('#panel-cams .panel-body').scrollTop=0")
    wait_until(pg, "()=>!document.querySelector('.cam-country.is-stuck')", 3000)
    check("stuck: at the top no section is stuck, 16px radius, no hairline", pg.evaluate("()=>[...document.querySelectorAll('.cam-country')].every(s=>!s.classList.contains('is-stuck') && ['16px','16px 16px 16px 16px'].includes(getComputedStyle(s).borderRadius) && getComputedStyle(s.querySelector('.cam-country-title')).boxShadow==='none')"))
    pg.evaluate("()=>{const b=document.querySelector('#panel-cams .panel-body'); b.scrollTo(0, document.querySelector('#cams-list').offsetTop + 300)}")
    pg.wait_for_timeout(400)
    st = pg.evaluate("""()=>{const s=document.querySelector('.cam-country.is-stuck'); if(!s) return null; const t=s.querySelector('.cam-country-title'); const cs=getComputedStyle(t);
      const sr=document.querySelector('.cams-search').getBoundingClientRect(); return {bg:cs.backgroundColor, shadow:cs.boxShadow, tr:cs.borderRadius, sr:getComputedStyle(s).borderRadius, below: Math.abs(t.getBoundingClientRect().top-sr.bottom)<2.5,
        canvas:getComputedStyle(document.documentElement).getPropertyValue('--color-canvas').trim()}}""")
    check(f"stuck: a scrolled section is stuck, opaque canvas, hairline, square ({st})", st and st["bg"] == "rgb(243, 245, 247)" and st["shadow"] != "none" and st["tr"] == "0px" and st["sr"] == "0px" and st["below"])
    sb = pg.evaluate("()=>document.querySelector('.cams-search').getBoundingClientRect().top - document.querySelector('#panel-cams .panel-body').getBoundingClientRect().top")
    check(f"search: still pinned at the top of the scrolled sheet ({sb})", abs(sb) < 1.5 and pg.is_visible("#cams-search"))
    pg.fill("#cams-search", "harbor")
    pg.evaluate("()=>{const b=document.querySelector('#panel-cams .panel-body'); b.scrollTo(0, b.scrollHeight)}")
    pg.wait_for_timeout(300)
    check("search: the field and its query stay visible after scrolling matches", pg.is_visible("#cams-search") and pg.input_value("#cams-search") == "harbor" and in_view(pg, "#cams-search"))
    # a header that is sliding out under the search (section end closer than a title height) is no longer stuck
    pg.evaluate("""()=>{const b=document.querySelector('#panel-cams .panel-body'); const s=[...document.querySelectorAll('.cam-country')].find(x=>x.classList.contains('is-stuck')) || document.querySelector('.cam-country'); const p=document.querySelector('.cams-search').getBoundingClientRect().bottom; b.scrollTop += s.getBoundingClientRect().bottom - p - 20}""")
    pg.wait_for_timeout(200)
    check("stuck: a section ending within a title height of the pin is not stuck", pg.evaluate("""()=>[...document.querySelectorAll('.cam-country.is-stuck')].every(s=>Math.abs(s.querySelector('.cam-country-title').getBoundingClientRect().top-document.querySelector('.cams-search').getBoundingClientRect().bottom)<=1)"""))
    pg.context.close()

    # ------------------------------------------------------------ 7. screenshots (4, optional: WS_SHOT_DIR)
    pg = phone()
    check("shots: started", start(pg))
    shot_dir = os.environ.get("WS_SHOT_DIR")
    shot = lambda name: shot_dir and pg.screenshot(path=os.path.join(shot_dir, name))  # opt-in, so CI never needs a path
    body_js = "document.querySelector('#panel-cams .panel-body')"
    open_cams(pg)
    pg.wait_for_timeout(400)
    cur = ws(pg, "currentCamId()")
    cc = pg.evaluate("(id)=>document.querySelector(`#cams-list .cam-row[data-id=\"${id}\"]`).closest('.cam-country').dataset.country", cur)
    # collapsed-scrolled: the other sections are collapsed; scroll so one open section's header is pinned with its first row
    # sliding under the opaque header
    scroll_into = lambda c, dy: pg.evaluate("([c,dy])=>{const b=document.querySelector('#panel-cams .panel-body'); const s=document.querySelector(`#cams-list .cam-country[data-country=\"${c}\"]`); b.scrollTop += s.getBoundingClientRect().top - document.querySelector('.cams-search').getBoundingClientRect().bottom + dy}", [c, dy])
    scroll_into(cc, 78)
    ok = wait_until(pg, "()=>document.querySelectorAll('.cam-country.is-stuck').length===1", 3000)
    check("shots: exactly one stuck header after scrolling", ok)
    shot("cams-rams34-collapsed-scrolled.png")
    # playing-collapsed: collapse the cam that is actually playing
    pg.evaluate(f"()=>{body_js}.scrollTop=0")
    check("shots: scrolling back to the top clears the stuck header", wait_until(pg, "()=>!document.querySelector('.cam-country.is-stuck')", 3000))
    if is_open(pg, cc):
        pg.click(head(cc)); settle(pg)
    check("shots: playing country collapsed with its Playing dot visible", not is_open(pg, cc) and pg.evaluate("(c)=>!document.querySelector(`#cams-list .cam-country-head[data-country=\"${c}\"] .cc-dot`).hidden", cc))
    pg.evaluate("(c)=>document.querySelector(`#cams-list .cam-country-head[data-country=\"${c}\"]`).scrollIntoView({block:'center'})", cc)
    pg.wait_for_timeout(300)
    shot("cams-rams34-playing-collapsed.png")
    # searching
    pg.fill("#cams-search", "harbor")
    pg.wait_for_timeout(300)
    scroll_into("Australia", 70)
    wait_until(pg, "()=>document.querySelectorAll('.cam-country.is-stuck').length===1", 2000)
    pg.wait_for_timeout(300)
    check("shots: searching, field visible with its query", pg.input_value("#cams-search") == "harbor" and in_view(pg, "#cams-search"))
    shot("cams-rams34-searching.png")
    # empty
    pg.fill("#cams-search", "zzzz")
    pg.evaluate(f"()=>{body_js}.scrollTop=0")
    pg.wait_for_timeout(300)
    check("shots: empty state quotes the query", pg.text_content("#cams-empty") == 'No cams match "zzzz"')
    shot("cams-rams34-empty.png")
    pg.context.close()
    b.close()

print("FAILED:", fails) if fails else print("ALL PASS")
if logs:
    print(logs)
sys.exit(1 if fails or logs else 0)
