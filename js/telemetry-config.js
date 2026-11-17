// Telemetry keys and vendored SDK files. See docs/telemetry.md.
// The PostHog project API key and the Sentry DSN are public client keys (they only allow sending events).
// Personal API tokens and auth tokens are never committed.
//
// Vendored SDK builds in js/vendor/ (pinned; loaded with a script tag after first paint, only when telemetry is on):
// - posthog-js 1.450.1: dist/array.no-external.js (the browser array build without external script loading;
//   the "full" build only adds replay, surveys and other features we keep off). Two edits, no behavior change:
//   the source map comment is removed (the map is not vendored) and one em dash in a log message is a hyphen.
// - @sentry/browser 11.14.0: Sentry no longer publishes CDN bundles to npm, so js/vendor/sentry-11.14.0.min.js
//   is a minified IIFE built from the package's ESM build with bun 1.4.4 (bun build --minify --format=iife),
//   exposing window.Sentry with only init, captureException, withScope, flush, getClient, SDK_VERSION and the
//   integrations js/telemetry.js uses (global handlers, dedupe, linked errors, http context, event filters,
//   breadcrumbs). No tracing, replay, feedback or session code is included.
// PostHog: the shared "Eksxel" project (one project for all Eksxel apps); APP in js/telemetry.js tags this app.
export const POSTHOG_KEY = "phc_wAS6anbDpDdDV38556hJmNKFj5YjXjtytkYiZsJzvkSp";
export const POSTHOG_HOST = "https://us.i.posthog.com";
export const SENTRY_DSN = "https://d45b5d881fd906ee2192aacac8cbced7@o4512448506298368.ingest.us.sentry.io/4512448557154304";
export const POSTHOG_SCRIPT = "vendor/posthog-1.450.1.no-external.min.js"; // relative to js/
export const SENTRY_SCRIPT = "vendor/sentry-11.14.0.min.js";
// On by default with an off switch in About (docs/telemetry.md section 2, open question 1). One constant.
export const DEFAULT_ON = true;
