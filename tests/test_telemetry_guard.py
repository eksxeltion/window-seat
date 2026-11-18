"""Proof that no test can reach PostHog or Sentry. Opens a page through the shared guard and fires requests at the
real hosts (harmless GETs plus a POST to a PostHog capture path with an empty body). Each one must be aborted by the
route: the page sees a network failure, the browser reports requestfailed, and no response event ever arrives
(telemetry_guard.leaked stays empty). Also covers new_page, new_context and a persistent context.
Usage: test_telemetry_guard.py BASE_URL (unused, accepted for the shared runner)"""
import os
import sys
import tempfile
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import telemetry_guard  # noqa: E402

URLS = [
    "https://us.i.posthog.com/decide/?v=3",
    "https://eu.i.posthog.com/e/",
    "https://us-assets.i.posthog.com/static/array.js",
    "https://app.posthog.com/ping",
    "https://posthog.com/ping",
    "https://o1.ingest.us.sentry.io/api/1/envelope/",
    "https://o1.ingest.sentry.io/api/1/store/",
    "https://sentry.io/ping",
]
fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def probe(pg, label):
    n_hits, n_ab = len(telemetry_guard.hits), len(telemetry_guard.aborted)
    res = pg.evaluate("""async (urls) => Promise.all(urls.map(async (u) => {
      try { const r = await fetch(u, {method: u.includes('/e/') || u.includes('envelope') ? 'POST' : 'GET', body: u.includes('/e/') || u.includes('envelope') ? '{}' : undefined, mode: 'no-cors'}); return 'RESPONSE ' + r.status; }
      catch (e) { return 'FAILED'; }
    }))""", URLS)
    pg.wait_for_timeout(300)
    check(f"{label}: every request to the real hosts failed in the page ({res.count('FAILED')}/{len(URLS)})", all(r == "FAILED" for r in res))
    check(f"{label}: all {len(URLS)} were seen by the guard route", len(telemetry_guard.hits) - n_hits == len(URLS))
    check(f"{label}: the browser reported all {len(URLS)} as aborted (requestfailed)", len(telemetry_guard.aborted) - n_ab == len(URLS))


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    # a fulfilled local origin, so the page can make cross-origin requests
    pg.route("http://guard.test/", lambda r: r.fulfill(status=200, content_type="text/html", body="<html></html>"))
    pg.goto("http://guard.test/")
    probe(pg, "new_page")
    ctx = b.new_context()
    pg2 = ctx.new_page()
    pg2.route("http://guard.test/", lambda r: r.fulfill(status=200, content_type="text/html", body="<html></html>"))
    pg2.goto("http://guard.test/")
    probe(pg2, "new_context")
    b.close()
    with tempfile.TemporaryDirectory() as d:
        pc = p.chromium.launch_persistent_context(d)
        pg3 = pc.new_page()
        pg3.route("http://guard.test/", lambda r: r.fulfill(status=200, content_type="text/html", body="<html></html>"))
        pg3.goto("http://guard.test/")
        probe(pg3, "persistent")
        pc.close()

check("no telemetry request received any response", not telemetry_guard.leaked)
try:
    telemetry_guard.assert_no_leaks()
    check("assert_no_leaks passes", True)
except AssertionError as e:
    check(f"assert_no_leaks passes ({e})", False)
print("FAILED:" if fails else "ALL PASS", fails)
sys.exit(1 if fails else 0)
