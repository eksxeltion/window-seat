"""Sheet config: CSV parsing and validation, page and receiver integration, cache fallback, phone to TV.
The Google Sheet CSV endpoints are intercepted with fixtures (gviz style: every field quoted)."""
import sys, os, json
from playwright.sync_api import sync_playwright
base = sys.argv[1].rstrip("/")
HERE = os.path.dirname(__file__)
CAMS = open(os.path.join(HERE, "fixture_cams.csv")).read()
MIXES = open(os.path.join(HERE, "fixture_mixes.csv")).read()
LIVE_CAMS = open(os.path.join(HERE, "snapshot_cams.csv")).read()  # copy of the live sheet, 2026-11-16
LIVE_MIXES = open(os.path.join(HERE, "snapshot_mixes.csv")).read()
EXTRA = CAMS + '"TESTROW0001","Test Row, with comma","Japan","Testcity","Kanto","Street","a|b","TRUE","Tester","https://www.youtube.com/channel/x",""\n' + '"bad-id","Bad","Japan","X","","","","TRUE","","",""\n'
CAF = open(os.path.join(HERE, "mock_caf_receiver.js")).read()
SENDER = open(os.path.join(HERE, "mock_cast_sender.js")).read()
fails, logs = [], []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

def sheet(route, cams=CAMS, status=200):
    def handler(r):
        if status != 200: return r.fulfill(status=status, content_type="text/html", body="<html>sign in</html>")
        tab = "Mixes" if "sheet=Mixes" in r.request.url else "Cams"
        r.fulfill(status=200, content_type="text/csv; charset=utf-8", headers={"access-control-allow-origin": "*"}, body=cams if tab == "Cams" else MIXES)
    route("**/gviz/tq*", handler)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page(); pg.on("pageerror", lambda e: logs.append(str(e)))
    pg.route("**/iframe_api", lambda r: r.abort())
    sheet(pg.route, EXTRA)
    pg.goto(base + "/"); pg.wait_for_timeout(2500)

    # Unit level, in the page: parser and validation.
    u = pg.evaluate(r'''async (arg) => {
      const c = await import('/js/config.js');
      const rows = c.parseCsv('a,"b, with comma","say ""hi""","multi\nline"\r\n1,2,3,4\n\n');
      const cfg = c.buildConfig('youtube_id,name,city,in_rotation\n5B4LmH4JbnY,One,Tokyo,FALSE\nshort,Bad,X,TRUE\n5B4LmH4JbnY,Dup,Tokyo,TRUE\n"u7kCTaCX_J4","Two, quoted","Tokyo",""\n',
                                'youtube_id,title,enabled\nnCtOCLv6eFY,M1,TRUE\nEYAWYK4Eqxc,M2,FALSE\n');
      const nc = c.buildConfig('youtube_id,name,country,city,region,type,tags,in_rotation,channel,channel_url,notes\n"5B4LmH4JbnY","Shib","Japan","Tokyo","Kanto","Street","Street, Train","TRUE","Ch","https://example.com/c","n"\n"u7kCTaCX_J4","Port","Japan","Yokohama","Kanto","SKYLINE","skyline|Harbor| |","","","",""\n', 'youtube_id,title,enabled\nnCtOCLv6eFY,M1,TRUE\n');
      const san = c.sanitize({cams:[{id:'5B4LmH4JbnY',title:'T',country:'Japan',city:'Tokyo',type:'Street',tags:['A',' b ','']}], mixes:[{id:'nCtOCLv6eFY',title:'m'}]}).cams[0];
      const live = c.buildConfig(arg.cams, arg.mixes);
      const bi = (await import('/js/data.js?fresh=1')).CAMS;
      return { nc: nc.cams.map(x => [x.country, x.city, x.place, x.type, x.tags, x.channel]), san: [san.country, san.city, san.type, san.tags],
               live: [live.cams.length, live.cams.filter(x => x.default).length, live.mixes.length, live.cams.every(x => x.country && x.type && x.city), live.skipped],
               bi: [bi.length, bi.filter(x => x.default).length, bi.every(x => x.country && x.type && Array.isArray(x.tags))], rows, cams: cfg.cams.map(x => [x.id, x.title, x.default]), mixes: cfg.mixes.map(m => m.id), skipped: cfg.skipped,
               usable: c.isUsable(cfg), bad: c.sanitize({cams:[{id:'<script>xx'}], mixes:[]}) };
    }''', {"cams": LIVE_CAMS, "mixes": LIVE_MIXES})
    check("parseCsv: quotes, commas, doubled quotes, newlines, CRLF, blank lines", u["rows"] == [["a", "b, with comma", 'say "hi"', "multi\nline"], ["1", "2", "3", "4"]])
    check("buildConfig: bad and duplicate ids skipped, quoted fields, in_rotation default TRUE", u["cams"] == [["5B4LmH4JbnY", "One", False], ["u7kCTaCX_J4", "Two, quoted", True]] and u["skipped"] == 2)
    check("buildConfig: disabled mixes dropped", u["mixes"] == ["nCtOCLv6eFY"])
    check("buildConfig: country, city, type, tags (comma and pipe, lowercased)", u["nc"] == [["Japan", "Tokyo", "Tokyo", "street", ["street", "train"], "Ch"], ["Japan", "Yokohama", "Yokohama", "skyline", ["skyline", "harbor"], "TBS NEWS DIG Powered by JNN"]])
    check("sanitize keeps country, city, type, clean tags", u["san"] == ["Japan", "Tokyo", "street", ["a", "b"]])
    check("live sheet CSVs parse to 44 cams, 43 default, 16 mixes, country/type/city filled, none skipped", u["live"] == [44, 43, 16, True, 0])
    check("built-in list: 44 cams, 43 default, country/type/tags present", u["bi"] == [44, 43, True])
    check("sanitize rejects junk", u["bad"] is None)
    r = pg.evaluate("""async () => {
      const c = await import('/js/config.js');
      const run = c.refreshFromSheet();
      c.applyConfig({cams:[{id:'5B4LmH4JbnY',title:'P',place:'X',default:true}], mixes:[{id:'nCtOCLv6eFY',title:'m',en:'m'}]}, 'phone');
      const res = await run;
      return { res, src: c.listInfo.source, n: c.listInfo.cams };
    }""")
    check("a slow sheet fetch does not undo a newer phone list", r["res"].get("superseded") and r["src"] == "phone" and r["n"] == 1)
    pg.reload(); pg.wait_for_timeout(2500)

    # Page integration.
    label = lambda: pg.text_content("#bar .list-status")
    check(f"menu shows sheet list: {label()!r}", label() == "List: Sheet, 23 cams, 16 mixes")
    check("cams panel rebuilt with the extra sheet row", pg.locator('#cams-list [data-id="TESTROW0001"]').count() > 0)
    check("credits include the new channel", "Tester" in pg.text_content("#credits-cams"))
    check("config saved for next time", json.loads(pg.evaluate("()=>localStorage.getItem('windowSeat.sheetConfig')"))["cams"][-1]["id"] == "TESTROW0001")

    # Reload with the sheet unreachable: the saved copy is used.
    pg.unroute("**/gviz/tq*"); sheet(pg.route, status=401)
    pg.reload(); pg.wait_for_timeout(2500)
    check(f"sheet unreachable, saved copy used: {label()!r}", label() == "List: Sheet (saved copy), 23 cams, 16 mixes")
    hover = pg.get_attribute("#bar .list-status", "title") or ""
    check(f"error explained on hover, with a sharing hint: {hover!r}", "Sheet not loaded" in hover and "Anyone with the link" in hover)
    pg.evaluate("()=>localStorage.clear()"); pg.reload(); pg.wait_for_timeout(2500)
    check(f"no sheet and no copy: built-in: {label()!r}", label() == "List: built-in" and pg.locator("#cams-list .cam-row").count() == 44)

    # Phone to TV: the list goes over the Cast channel first on connect.
    pg.unroute("**/gviz/tq*"); sheet(pg.route, EXTRA)
    pg.route("**/cast_sender.js*", lambda r: r.fulfill(status=200, content_type="text/javascript", body=SENDER))
    pg.reload(); pg.wait_for_timeout(3000)
    pg.evaluate("()=>__mockCast.start()"); pg.wait_for_timeout(300)
    msgs = [json.loads(m["data"]) for m in pg.evaluate("()=>__mockCast.sent")]
    check("on connect the phone sends the sheet list first", msgs and msgs[0]["type"] == "config" and len(msgs[0]["cams"]) == 23 and len(msgs[0]["mixes"]) == 16)
    check("remote lists show the sheet rows", pg.locator('#remote-cams [data-cam="TESTROW0001"]').count() == 1)
    pg.close()

    # Receiver: fetches the sheet itself, and accepts the list from the phone.
    rx = ctx.new_page(); rx.on("pageerror", lambda e: logs.append(str(e)))
    rx.route("**/iframe_api", lambda r: r.abort())
    rx.route("**/cast_receiver_framework.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=CAF))
    rx.goto(base + "/receiver.html"); rx.evaluate("()=>localStorage.clear()")
    sheet(rx.route, CAMS); rx.reload(); rx.wait_for_timeout(2500)
    strip = lambda: rx.text_content("#debug-strip")
    check(f"TV strip shows its own sheet fetch: {strip()[:60]!r}", "list sheet 22/16" in strip())
    cfg = {"type": "config", "cams": [{"id": "TESTROW0001", "title": "T", "place": "X", "default": True}, {"id": "5B4LmH4JbnY", "title": "Shibuya", "place": "Tokyo", "default": True}], "mixes": [{"id": "nCtOCLv6eFY", "title": "m", "en": "m"}]}
    rx.evaluate("([d])=>__caf.message('urn:x-cast:com.windowseat', d, 'sender-1')", [json.dumps(cfg)])
    check("TV applies the list from the phone", "list phone 2/1" in strip())
    st = json.loads(rx.evaluate("()=>__caf.sent")[-1]["data"])
    check("TV reports the list in its state", st["list"] == {"source": "phone", "cams": 2, "mixes": 1})
    # Remove the cam that is showing or loading: the TV moves on without errors.
    rx.evaluate("([d])=>__caf.message('urn:x-cast:com.windowseat', d, 'sender-1')", [json.dumps({"type": "cam", "id": "TESTROW0001"})])
    rx.wait_for_timeout(300)
    cfg2 = dict(cfg, cams=cfg["cams"][1:])
    rx.evaluate("([d])=>__caf.message('urn:x-cast:com.windowseat', d, 'sender-1')", [json.dumps(cfg2)])
    rx.wait_for_timeout(500)
    check("removing the current cam is handled", "list phone 1/1" in strip())
    rx.evaluate("([d])=>__caf.message('urn:x-cast:com.windowseat', d, 'sender-1')", [json.dumps({"type": "config", "cams": [], "mixes": []})])
    check("empty list from phone is rejected", "list phone 1/1" in strip())
    b.close()
print("page errors:", logs)
sys.exit(1 if fails or logs else 0)
