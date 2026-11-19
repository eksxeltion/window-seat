"""Keeps every Playwright test away from the real telemetry hosts (PostHog and Sentry).

Import it before using Playwright (`import telemetry_guard`). It patches the sync API so every new browser context
(and every Browser.new_page, which makes its own context) ABORTS requests to any PostHog host (posthog.com and every
subdomain, e.g. us.i.posthog.com, us-assets.i.posthog.com) and to Sentry ingest (sentry.io and every subdomain, e.g.
o123.ingest.us.sentry.io). Nothing is fulfilled and nothing reaches the network: the browser gets a failed request.
Every attempt is recorded in `telemetry_guard.hits` (url, method, body); `aborted` lists the URLs the browser reported
as failed and `leaked` lists any that got a real response (must stay empty; `assert_no_leaks()` checks it).
"""
import re
from playwright.sync_api import Browser, BrowserType

HOSTS = re.compile(r"^https?://([a-z0-9-]+\.)*(posthog\.com|sentry\.io)(:\d+)?(/|$|\?)", re.I)
# Glob forms anchored on the URL authority (a path like /fixtures/i.posthog.com/ must not match). They cover
# https://*.i.posthog.com/** (us.i.posthog.com, eu.i.posthog.com), every other PostHog host, and Sentry ingest.
GLOBS = ("http*://*.posthog.com/**", "http*://posthog.com/**", "http*://*.sentry.io/**", "http*://sentry.io/**")
hits = []  # every telemetry request seen by any guarded context: {"url", "method", "body"}
aborted = []  # urls the browser reported as failed (the abort took effect)
leaked = []  # urls of telemetry hosts that received any response (must stay empty)


def _abort(route):
    req = route.request
    try:
        body = req.post_data_buffer
    except Exception:
        body = None
    hits.append({"url": req.url, "method": req.method, "body": body})
    route.abort()


def assert_no_leaks():
    """No telemetry request got a response (each one is aborted in the route; tests/test_telemetry_guard.py proves the
    browser reports every abort as requestfailed. A request in flight at context close may not emit that event)."""
    assert not leaked, f"telemetry request got a real response: {leaked[:3]}"


def guard(ctx):
    """Abort the telemetry hosts of a context (or page). Safe to call twice."""
    if getattr(ctx, "_telemetry_guarded", False):
        return ctx
    ctx.route(HOSTS, _abort)
    for g in GLOBS:
        ctx.route(g, _abort)
    ctx.on("requestfailed", lambda r: HOSTS.match(r.url) and aborted.append(r.url))
    ctx.on("response", lambda r: HOSTS.match(r.url) and leaked.append(r.url))
    try:
        ctx._telemetry_guarded = True
    except Exception:
        pass
    return ctx


_new_context = Browser.new_context
_new_page = Browser.new_page
_persistent = BrowserType.launch_persistent_context


def _guarded_new_context(self, *a, **kw):
    return guard(_new_context(self, *a, **kw))


def _guarded_new_page(self, *a, **kw):
    page = _new_page(self, *a, **kw)
    guard(page.context)
    return page


def _guarded_persistent(self, *a, **kw):
    return guard(_persistent(self, *a, **kw))


Browser.new_context = _guarded_new_context
Browser.new_page = _guarded_new_page
BrowserType.launch_persistent_context = _guarded_persistent
