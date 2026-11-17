"""Keeps every Playwright test away from the real telemetry hosts (PostHog and Sentry).

Import it before using Playwright (`import telemetry_guard`). It patches the sync API so every new browser context
(and every Browser.new_page, which makes its own context) answers requests to *.posthog.com and *.sentry.io with a
local stub. Nothing reaches the real services. Requests are recorded in `telemetry_guard.hits` (url, method, body).
"""
import re
from playwright.sync_api import Browser, BrowserType

HOSTS = re.compile(r"^https?://([a-z0-9-]+\.)*(posthog\.com|sentry\.io)(:\d+)?/", re.I)
hits = []  # every telemetry request seen by any guarded context: {"url", "method", "body"}


def _stub(route):
    req = route.request
    try:
        body = req.post_data_buffer
    except Exception:
        body = None
    hits.append({"url": req.url, "method": req.method, "body": body})
    if "posthog" in req.url:
        route.fulfill(status=200, content_type="application/json", body='{"status":1}', headers={"access-control-allow-origin": "*"})
    else:
        route.fulfill(status=200, content_type="application/json", body="{}", headers={"access-control-allow-origin": "*"})


def guard(ctx):
    """Route the telemetry hosts of a context (or page) to the stub. Safe to call twice."""
    if getattr(ctx, "_telemetry_guarded", False):
        return ctx
    ctx.route(HOSTS, _stub)
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
