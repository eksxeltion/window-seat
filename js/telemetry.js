// Window Seat telemetry: PostHog (usage) and Sentry (errors), behind one gate. See docs/telemetry.md.
// Rules this module keeps: it never throws into app code, it never blocks first paint (the SDKs load after the
// page has loaded, and only when telemetry is on), and when it is off nothing reaches either host.
// Phone page: on by default, the About switch turns it off (saved in localStorage), Do Not Track and Global
// Privacy Control force it off. TV receiver: off until the phone says on with a {type:"telemetry"} message.
import { POSTHOG_KEY, POSTHOG_HOST, SENTRY_DSN, POSTHOG_SCRIPT, SENTRY_SCRIPT, DEFAULT_ON } from "./telemetry-config.js";

const APP = "window-seat";
const PREF_KEY = "windowSeat.telemetry"; // JSON true or false; absent means DEFAULT_ON
const QUEUE_MAX = 100; // phone: events captured before PostHog loads
const RX_BUFFER_MAX = 50; // receiver: events captured before the phone's first telemetry message
const RX_BUFFER_MS = 2 * 60 * 1000; // ...dropped after this long without a message
const EARLY_ERRORS_MAX = 10;
const PH_PER_MIN = 60;
const SENTRY_PER_MIN = 10;
const SENTRY_PER_PAGE = 25;
const LOG_MAX = 200; // test hook ring buffers

// Only our own events go to PostHog. Anything the SDK adds by itself ($pageview, $opt_in, $autocapture...) is dropped.
const EVENTS = new Set([
  "cast_connect_attempt", "cast_connect_success", "cast_connect_fail", "cast_resume", "cast_first_state",
  "cast_channel_missing", "cast_send_failed", "cast_no_reply", "cast_session_end",
  "receiver_ready", "receiver_start_failed", "receiver_first_frame", "receiver_sender_left", "receiver_stop", "receiver_error",
  "app_open", "local_playback_started", "local_playback_ended", "cam_shown", "mix_started", "command", "panel_opened",
  "cam_pool_changed", "pause_warning_shown", "pause_timeout_fired", "debug_mode_toggled", "telemetry_enabled",
  "player_error", "cam_failed", "cams_all_failed", "music_unavailable", "list_load",
]);

const safe = (fn, fallback) => {
  try {
    return fn();
  } catch {
    return fallback;
  }
};

const params = safe(() => new URLSearchParams(location.search), new URLSearchParams());

/** Do Not Track or Global Privacy Control: telemetry is off and no URL parameter can change that. */
function privacySignal() {
  return safe(() => {
    const yes = (v) => v === "1" || v === "yes";
    return yes(navigator.doNotTrack) || yes(window.doNotTrack) || yes(navigator.msDoNotTrack) || navigator.globalPrivacyControl === true;
  }, false);
}

function readPref() {
  return safe(() => {
    const raw = localStorage.getItem(PREF_KEY);
    return raw === null ? null : JSON.parse(raw) === true;
  }, null);
}

function writePref(on) {
  safe(() => localStorage.setItem(PREF_KEY, JSON.stringify(!!on)));
}

/** Remove every key PostHog keeps in localStorage (its ID and session), and its session keys in sessionStorage. */
function removePosthogKeys() {
  safe(() => {
    const doomed = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && (k.startsWith("ph_") || k.startsWith("__ph_"))) doomed.push(k);
    }
    doomed.forEach((k) => localStorage.removeItem(k));
  });
  // sessionStorage: PostHog's session keys go too; its opt-out flag stays for this tab (it holds no ID).
  safe(() => {
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const k = sessionStorage.key(i);
      if (k && k.startsWith("ph_")) sessionStorage.removeItem(k);
    }
  });
}

/** Strip the query string and fragment from a URL (any string that looks like one). */
function bareUrl(v) {
  if (typeof v !== "string") return v;
  return v.replace(/[?#].*$/s, "");
}

/**
 * Free text that may go to Sentry (exception values, messages, breadcrumb messages): URLs lose their query string
 * and fragment, email addresses and long token-like runs are masked, and the result is capped. Error text is not
 * ours to control (a rejection can carry a full URL or a server reply), so none of it leaves unscrubbed.
 */
const TEXT_MAX = 300;
function scrubText(v) {
  if (typeof v !== "string") return v;
  let s = v.replace(/\b[a-z][a-z0-9+.-]*:\/\/[^\s"'<>`)\]]+/gi, (u) => bareUrl(u));
  s = s.replace(/\[title="[^"]*"\]/g, "");
  s = s.replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, "[email]");
  // Long runs of letters, digits, - and _ with at least one digit: keys, tokens, IDs. Plain words and paths stay.
  s = s.replace(/[A-Za-z0-9_-]{24,}/g, (w) => (/\d/.test(w) ? "[redacted]" : w));
  return s.length > TEXT_MAX ? s.slice(0, TEXT_MAX) + "..." : s;
}

function hostEnvironment() {
  const h = safe(() => location.hostname, "");
  return h === "localhost" || h === "127.0.0.1" || h === "[::1]" || h === "::1" || h.endsWith(".localhost") ? "local" : "production";
}

const ENVIRONMENTS = new Set(["production", "local", "verification"]);

function localeRegion() {
  const lang = safe(() => navigator.language, "") || "";
  const m = /[-_]([A-Za-z]{2}|\d{3})(?:$|[-_])/.exec(lang);
  return m ? m[1].toUpperCase() : undefined;
}

/* ---------------------------------------------------------------- state */

const t = {
  started: false,
  surface: "web", // web | pwa | receiver
  build: "",
  receiver: false,
  forcedOff: privacySignal(),
  blocked: false, // receiver.html?debug=1 without ?telemetry=1
  on: false, // the gate: every capture and Sentry beforeSend checks it first
  test: params.get("telemetry_test") === "1",
  environment: "production",
  queue: [], // phone: before PostHog loads; receiver: before the first telemetry message
  earlyErrors: [],
  ph: null, // posthog instance once loaded and initialised
  phState: "idle", // idle | loading | ready | failed
  sentry: null,
  sentryState: "idle",
  installId: null,
  providers: { deviceClass: () => "desktop", castState: () => "none", castSessionId: () => undefined },
  subscribers: [],
  phTimes: [],
  sentryTimes: [],
  sentryCount: 0,
  fingerprints: new Set(),
  // receiver gate
  rx: { heard: false, bufferOpen: true, timer: 0, senders: {}, stickyOff: false, installId: null, test: false, environment: null },
  // test hook logs
  log: [], // what capture() did: { name, outcome }
  sent: [], // events PostHog was allowed to send (after before_send)
  errorsSent: [], // Sentry events allowed out (after beforeSend)
  loads: [], // vendor script URLs requested
};

function remember(list, item) {
  list.push(item);
  if (list.length > LOG_MAX) list.shift();
}

function notify() {
  for (const fn of t.subscribers) safe(fn);
}

/* ---------------------------------------------------------------- super properties */

/** The phone: its own. The receiver: once any phone marks the session as a test, it is a verification session
 *  (the same rule the phone applies to itself), so test errors never land in the production environment. */
function receiverEnvironment() {
  if (!t.receiver) return t.environment;
  if (t.rx.test || t.test) return "verification";
  return t.rx.environment || t.environment;
}

/** Computed at capture time (never posthog.register, which would persist across loads). */
function superProps() {
  const p = {
    app: APP,
    build: t.build,
    surface: t.surface,
    device_class: t.receiver ? "tv" : safe(t.providers.deviceClass, "desktop"),
    cast_state: t.receiver ? "connected" : safe(t.providers.castState, "none") || "none",
    environment: receiverEnvironment(),
    test: t.receiver ? !!(t.rx.test || t.test) : t.test,
    $geoip_disable: true,
  };
  const sid = safe(t.providers.castSessionId);
  if (sid) p.cast_session_id = String(sid);
  const region = localeRegion();
  if (region) p.locale_region = region;
  return p;
}

/* ---------------------------------------------------------------- rate limits */

function underLimit(times, perMin) {
  const now = Date.now();
  while (times.length && now - times[0] > 60000) times.shift();
  if (times.length >= perMin) return false;
  times.push(now);
  return true;
}

/* ---------------------------------------------------------------- PostHog */

/** PostHog's before_send: the gate, our event names only, and the trimming from docs/telemetry.md section 2. */
function beforeSend(ev) {
  try {
    if (!t.on || !ev || !EVENTS.has(ev.event)) return null;
    const trim = (props) => {
      if (!props || typeof props !== "object") return;
      for (const k of Object.keys(props)) {
        if (k === "$referrer" || k === "$referring_domain" || k.startsWith("$initial_") || k === "$timezone" || k === "$timezone_offset" || /referr/i.test(k)) delete props[k];
        else if (typeof props[k] === "string" && /^https?:\/\//i.test(props[k])) props[k] = bareUrl(props[k]);
      }
    };
    trim(ev.properties);
    trim(ev.$set);
    trim(ev.$set_once);
    if (ev.properties && ev.properties.$current_url) ev.properties.$current_url = bareUrl(ev.properties.$current_url);
    remember(t.sent, { event: ev.event, properties: JSON.parse(JSON.stringify(ev.properties || {})) });
    return ev;
  } catch {
    return null;
  }
}

function posthogConfig() {
  const cfg = {
    api_host: POSTHOG_HOST,
    autocapture: false,
    capture_pageview: false,
    capture_pageleave: false,
    disable_session_recording: true,
    capture_exceptions: false, // Sentry owns errors
    disable_surveys: true,
    enable_heatmaps: false,
    capture_dead_clicks: false,
    capture_performance: false,
    advanced_disable_flags: true,
    disable_external_dependency_loading: true,
    person_profiles: "never",
    respect_dnt: true,
    save_referrer: false,
    save_campaign_params: false,
    persistence: "localStorage",
    // The opt-out flag lives in sessionStorage, so turning off leaves no PostHog key in localStorage.
    opt_out_capturing_persistence_type: "sessionStorage",
    before_send: beforeSend,
  };
  if (t.receiver) {
    // Nothing is stored on the TV, events go out one by one so a stop never loses them, and the ID is the phone's.
    cfg.persistence = "memory";
    cfg.request_batching = false;
    if (t.rx.installId) cfg.bootstrap = { distinctID: t.rx.installId };
  }
  return cfg;
}

function sendToPosthog(item) {
  const opts = { timestamp: new Date(item.at) };
  if (item.beacon) {
    opts.transport = "sendBeacon";
    opts.send_instantly = true;
  }
  safe(() => t.ph.capture(item.name, item.props, opts));
}

function onPosthogLoaded() {
  const ph = safe(() => window.posthog);
  if (!ph || typeof ph.init !== "function") {
    t.phState = "failed";
    t.queue.length = 0;
    return;
  }
  try {
    ph.init(POSTHOG_KEY, posthogConfig());
    t.ph = ph;
    t.phState = "ready";
    // If telemetry was switched off while the script was loading, keep it opted out.
    if (!t.on) applyOffToSdks();
    else {
      t.installId = safe(() => ph.get_distinct_id()) || null;
      if (t.receiver) {
        // Kept before the phone spoke: mark them with the phone's test flag and environment.
        const sp = superProps();
        for (const item of t.queue) Object.assign(item.props, { test: sp.test, environment: sp.environment });
      }
      t.queue.splice(0).forEach(sendToPosthog);
    }
  } catch {
    t.phState = "failed";
    t.queue.length = 0;
  }
  notify();
}

/* ---------------------------------------------------------------- Sentry */

const BREADCRUMB_DROP = new Set(["fetch", "xhr", "navigation", "console"]);

function beforeBreadcrumb(b) {
  try {
    if (!t.on || !b || BREADCRUMB_DROP.has(b.category)) return null;
    if (typeof b.message === "string") b.message = scrubText(b.message);
    if (b.data && typeof b.data === "object") {
      for (const k of Object.keys(b.data)) if (typeof b.data[k] === "string") b.data[k] = bareUrl(b.data[k]);
    }
    return b;
  } catch {
    return null;
  }
}

function stripEventUrls(event) {
  if (event.request) {
    if (event.request.url) event.request.url = bareUrl(event.request.url);
    delete event.request.query_string;
    delete event.request.cookies;
    if (event.request.headers) {
      delete event.request.headers.Referer;
      delete event.request.headers.referer;
      delete event.request.headers.Cookie;
    }
  }
  // Free text: every exception value (linked errors too), the message forms Sentry uses, breadcrumb messages.
  if (typeof event.message === "string") event.message = scrubText(event.message);
  else if (event.message && typeof event.message === "object") {
    event.message.formatted = scrubText(event.message.formatted);
    event.message.message = scrubText(event.message.message);
    delete event.message.params;
  }
  if (event.logentry && typeof event.logentry === "object") {
    event.logentry.message = scrubText(event.logentry.message);
    event.logentry.formatted = scrubText(event.logentry.formatted);
    delete event.logentry.params;
  }
  delete event.extra; // e.g. __serialized__ of a non-Error rejection: arbitrary data, never ours to send
  if (event.transaction) event.transaction = bareUrl(event.transaction);
  const frames = [];
  for (const ex of (event.exception && event.exception.values) || []) {
    if (ex.value !== undefined) ex.value = scrubText(String(ex.value));
    for (const f of (ex.stacktrace && ex.stacktrace.frames) || []) {
      if (f.filename) f.filename = bareUrl(f.filename);
      if (f.abs_path) f.abs_path = bareUrl(f.abs_path);
      frames.push(f);
    }
  }
  for (const b of event.breadcrumbs || []) {
    if (typeof b.message === "string") b.message = scrubText(b.message);
    if (b.data && typeof b.data === "object") for (const k of Object.keys(b.data)) if (typeof b.data[k] === "string") b.data[k] = bareUrl(b.data[k]);
  }
  delete event.user;
  return frames;
}

function sentryBeforeSend(event) {
  try {
    if (!t.on || !event) return null;
    const frames = stripEventUrls(event);
    const ex = (event.exception && event.exception.values && event.exception.values[0]) || {};
    // Browser extensions and cross-origin "Script error." without a stack are not ours.
    if (frames.some((f) => /^(chrome|moz|safari(-web)?|ms-browser)-extension:\/\//.test(f.filename || ""))) return null;
    const m = event.message;
    const msg = String(ex.value || (m && typeof m === "object" ? m.formatted || m.message : m) || "");
    if (/^Script error\.?$/.test(msg) && !frames.length) return null;
    const top = frames[frames.length - 1] || {};
    const fp = `${ex.type || ""}|${msg.slice(0, 120)}|${top.filename || ""}:${top.lineno || ""}`;
    if (t.fingerprints.has(fp)) return null;
    if (t.sentryCount >= SENTRY_PER_PAGE || !underLimit(t.sentryTimes, SENTRY_PER_MIN)) return null;
    t.fingerprints.add(fp);
    t.sentryCount++;
    const sp = superProps();
    event.tags = { ...(event.tags || {}), surface: sp.surface, build: sp.build, cast_state: sp.cast_state, device_class: sp.device_class, test: String(sp.test) };
    event.environment = sp.environment;
    remember(t.errorsSent, { type: ex.type || "", value: msg.slice(0, 200), tags: event.tags, environment: event.environment, release: event.release });
    return event;
  } catch {
    return null;
  }
}

function onSentryLoaded() {
  const S = safe(() => window.Sentry);
  if (!S || typeof S.init !== "function") {
    t.sentryState = "failed";
    t.earlyErrors.length = 0;
    return;
  }
  try {
    const integrations = [
      S.eventFiltersIntegration && S.eventFiltersIntegration(),
      S.globalHandlersIntegration && S.globalHandlersIntegration({ onerror: true, onunhandledrejection: true }),
      S.dedupeIntegration && S.dedupeIntegration(),
      S.linkedErrorsIntegration && S.linkedErrorsIntegration(),
      S.httpContextIntegration && S.httpContextIntegration(),
      S.breadcrumbsIntegration && S.breadcrumbsIntegration({ console: false, fetch: false, xhr: false, history: false, dom: true, sentry: false }),
    ].filter(Boolean);
    S.init({
      dsn: SENTRY_DSN,
      release: `${APP}@${t.build}`,
      environment: superProps().environment,
      sendDefaultPii: false,
      tracesSampleRate: 0,
      defaultIntegrations: false, // no browserApiErrors (would wrap CAF timers and listeners), no session tracking
      integrations,
      sendClientReports: false,
      maxBreadcrumbs: 30,
      beforeBreadcrumb,
      beforeSend: sentryBeforeSend,
    });
    t.sentry = S;
    t.sentryState = "ready";
    if (!t.on) applyOffToSdks();
    else t.earlyErrors.splice(0).forEach((e) => safe(() => S.captureException(e)));
  } catch {
    t.sentryState = "failed";
    t.earlyErrors.length = 0;
  }
}

/* ---------------------------------------------------------------- loading */

function loadScript(file, onload, onfail) {
  try {
    const src = new URL(file, import.meta.url).href;
    remember(t.loads, src);
    const el = document.createElement("script");
    el.src = src;
    el.async = true;
    el.crossOrigin = "anonymous";
    el.onload = () => safe(onload);
    el.onerror = () => safe(onfail);
    document.head.append(el);
  } catch {
    safe(onfail);
  }
}

/** After first paint: the page's load event, then an idle moment. */
function afterPaint(fn) {
  const go = () => {
    const idle = window.requestIdleCallback ? (f) => window.requestIdleCallback(f, { timeout: 3000 }) : (f) => setTimeout(f, 500);
    idle(() => safe(fn));
  };
  if (document.readyState === "complete") go();
  else window.addEventListener("load", go, { once: true });
}

let loadScheduled = false;
function loadSdks() {
  if (loadScheduled || !t.on) return;
  loadScheduled = true;
  afterPaint(() => {
    if (!t.on) {
      loadScheduled = false; // switched off before the load: never download
      return;
    }
    if (t.phState === "idle") {
      t.phState = "loading";
      loadScript(POSTHOG_SCRIPT, onPosthogLoaded, () => {
        t.phState = "failed"; // ad blocker or offline: telemetry quietly does nothing
        t.queue.length = 0;
        notify();
      });
    }
    if (t.sentryState === "idle") {
      t.sentryState = "loading";
      loadScript(SENTRY_SCRIPT, onSentryLoaded, () => {
        t.sentryState = "failed";
        t.earlyErrors.length = 0;
      });
    }
  });
}

/* ---------------------------------------------------------------- the gate */

function applyOffToSdks() {
  if (t.ph) {
    safe(() => t.ph.opt_out_capturing());
    safe(() => t.ph.set_config({ disable_persistence: true }));
  }
  // Sentry: beforeSend already returns nothing; also disable the client. Never close(), which would flush.
  safe(() => {
    const c = t.sentry && t.sentry.getClient();
    if (c && c.getOptions) c.getOptions().enabled = false;
  });
  if (!t.receiver) removePosthogKeys();
}

function turnOff() {
  t.on = false; // first: every capture and beforeSend now returns nothing
  t.queue.length = 0;
  t.earlyErrors.length = 0;
  applyOffToSdks();
  notify();
}

function turnOn() {
  t.on = true;
  if (t.ph) {
    safe(() => t.ph.set_config({ disable_persistence: false }));
    safe(() => t.ph.opt_in_capturing({ captureEventName: false }));
    if (!t.receiver) {
      safe(() => t.ph.reset()); // a fresh ID: nothing links the time before the switch was off
      t.installId = safe(() => t.ph.get_distinct_id()) || null;
    }
  }
  safe(() => {
    const c = t.sentry && t.sentry.getClient();
    if (c && c.getOptions) c.getOptions().enabled = true;
  });
  loadSdks();
  notify();
}

/* ---------------------------------------------------------------- early errors */

function onEarlyError(e) {
  if (t.sentryState === "ready" || t.sentryState === "failed") return;
  if (!t.on && !(t.receiver && t.rx.bufferOpen && !t.blocked && !t.forcedOff)) return;
  const err = e && (e.error || e.reason);
  if (!err || t.earlyErrors.length >= EARLY_ERRORS_MAX) return;
  t.earlyErrors.push(err);
}
safe(() => {
  window.addEventListener("error", onEarlyError);
  window.addEventListener("unhandledrejection", onEarlyError);
});

/* ---------------------------------------------------------------- public API */

/**
 * Start telemetry for this page. opts: { surface: "web" | "receiver", build, deviceClass(), castState(), castSessionId() }.
 * The phone passes "web" and the wrapper tells a browser tab from the installed app.
 */
export function initTelemetry(opts = {}) {
  try {
    if (t.started) return;
    t.started = true;
    t.build = String(opts.build || "");
    t.receiver = opts.surface === "receiver";
    for (const k of ["deviceClass", "castState", "castSessionId"]) if (typeof opts[k] === "function") t.providers[k] = opts[k];
    if (t.receiver) {
      t.surface = "receiver";
      t.blocked = params.get("debug") === "1" && params.get("telemetry") !== "1";
      t.on = false; // until the phone says so
      t.rx.timer = setTimeout(() => {
        if (t.rx.heard) return;
        t.rx.bufferOpen = false; // two minutes without a message: drop what was kept
        t.queue.length = 0;
        t.earlyErrors.length = 0;
      }, RX_BUFFER_MS);
      if (t.blocked || t.forcedOff) t.rx.bufferOpen = false;
    } else {
      const standalone = safe(() => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true, false);
      t.surface = standalone ? "pwa" : "web";
      t.environment = t.test ? "verification" : hostEnvironment();
      const pref = readPref();
      t.on = !t.forcedOff && (pref === null ? DEFAULT_ON : pref);
      if (t.on) loadSdks();
      else removePosthogKeys(); // off: no ID kept from an earlier run
    }
  } catch {
    t.on = false;
  }
}

/**
 * Record one event. Never throws. opts: { beacon: true } sends it right away with sendBeacon (page hide, receiver stop).
 */
export function capture(name, props = {}, opts = {}) {
  try {
    if (!EVENTS.has(name)) return;
    const receiverBuffering = t.receiver && !t.rx.heard && t.rx.bufferOpen;
    if (!t.on && !receiverBuffering) return;
    if (!underLimit(t.phTimes, PH_PER_MIN)) {
      remember(t.log, { name, outcome: "rate_limited" });
      return;
    }
    const clean = {};
    for (const [k, v] of Object.entries(props || {})) if (v !== undefined) clean[k] = v;
    const item = { name, props: { ...clean, ...superProps() }, at: Date.now(), beacon: !!opts.beacon };
    if (!t.on) {
      // Receiver before the phone's message: keep at most 50 for up to 2 minutes.
      if (t.queue.length >= RX_BUFFER_MAX) t.queue.shift();
      t.queue.push(item);
      remember(t.log, { name, outcome: "buffered" });
      return;
    }
    if (t.phState === "ready" && t.ph) {
      sendToPosthog(item);
      remember(t.log, { name, outcome: "sent" });
      return;
    }
    if (t.phState === "failed") return;
    if (t.queue.length >= QUEUE_MAX) t.queue.shift();
    t.queue.push(item);
    remember(t.log, { name, outcome: "queued" });
  } catch {
    /* never into app code */
  }
}

/** Send a caught error to Sentry (only for the short allowlist of catch blocks that mean a real bug). */
export function captureError(err, tags) {
  try {
    const e = err instanceof Error ? err : new Error(String(err));
    if (t.on && t.sentryState === "ready" && t.sentry) {
      if (tags) t.sentry.withScope((scope) => {
        for (const [k, v] of Object.entries(tags)) scope.setTag(k, String(v));
        t.sentry.captureException(e);
      });
      else t.sentry.captureException(e);
      return;
    }
    const buffering = t.on || (t.receiver && t.rx.bufferOpen && !t.rx.heard);
    if (buffering && t.sentryState !== "failed" && t.earlyErrors.length < EARLY_ERRORS_MAX) t.earlyErrors.push(e);
  } catch {
    /* never into app code */
  }
}

/** The phone's About switch. */
export function setTelemetryEnabled(on) {
  try {
    if (t.receiver || t.forcedOff) return false;
    const next = !!on;
    writePref(next);
    if (next === t.on) return t.on;
    if (next) {
      turnOn();
      capture("telemetry_enabled");
    } else turnOff();
    return t.on;
  } catch {
    return false;
  }
}

export function isTelemetryOn() {
  return t.on;
}

export function telemetryForcedOff() {
  return t.forcedOff;
}

/** Called whenever the gate or the install ID changes (the phone then tells the TV). Returns an unsubscribe. */
export function onTelemetryChange(fn) {
  if (typeof fn !== "function") return () => {};
  t.subscribers.push(fn);
  return () => {
    t.subscribers = t.subscribers.filter((f) => f !== fn);
  };
}

/**
 * The phone's message for the TV, or null while it is on but the install ID is not known yet
 * (PostHog still loading). If PostHog cannot load (blocked), the TV is told on without an ID.
 */
export function telemetryMessage() {
  try {
    if (t.receiver) return null;
    if (!t.on) return { type: "telemetry", on: false, installId: null, test: t.test, environment: t.environment };
    if (t.phState !== "ready" && t.phState !== "failed") return null;
    return { type: "telemetry", on: true, installId: t.installId || null, test: t.test, environment: t.environment };
  } catch {
    return null;
  }
}

/**
 * Receiver: a {type:"telemetry"} message from a phone. Every phone that has spoken must be on; any phone saying
 * off turns it off for the rest of this receiver session. The last phone leaving changes nothing.
 */
export function receiverTelemetryMessage(msg, senderId = "local") {
  try {
    if (!t.receiver || !msg) return;
    if (t.blocked || t.forcedOff) return;
    t.rx.heard = true;
    clearTimeout(t.rx.timer);
    const on = msg.on === true;
    t.rx.senders[String(senderId)] = on;
    if (!on) t.rx.stickyOff = true;
    if (t.rx.stickyOff) {
      if (t.on || t.queue.length) turnOff();
      t.rx.bufferOpen = false;
      return;
    }
    if (!t.rx.installId && typeof msg.installId === "string" && /^[\w.-]{1,200}$/.test(msg.installId)) t.rx.installId = msg.installId;
    if (msg.test === true) t.rx.test = true;
    if (!t.rx.environment && ENVIRONMENTS.has(msg.environment)) t.rx.environment = msg.environment;
    if (t.on) return;
    turnOn();
    // The buffered events go out once PostHog is ready (they are already in the queue).
  } catch {
    /* never into app code */
  }
}

/** Send what is pending (receiver, before context.stop()). Resolves within `ms`, never rejects. */
export function flushTelemetry(ms = 600) {
  try {
    if (!t.on || t.sentryState !== "ready" || !t.sentry) return Promise.resolve(false);
    return Promise.race([Promise.resolve(t.sentry.flush(ms)).catch(() => false), new Promise((r) => setTimeout(() => r(false), ms + 50))]);
  } catch {
    return Promise.resolve(false);
  }
}

/** True when a flush could matter (telemetry on and an SDK loaded), so callers can skip waiting otherwise. */
export function telemetryActive() {
  return t.on && (t.phState === "ready" || t.sentryState === "ready");
}

/* ---------------------------------------------------------------- test hook (read only) */

safe(() =>
  Object.defineProperty(window, "__telemetry", {
    value: {
      state: () => ({
        on: t.on,
        forcedOff: t.forcedOff,
        blocked: t.blocked,
        surface: t.surface,
        environment: t.environment,
        test: t.test,
        posthog: t.phState,
        sentry: t.sentryState,
        installId: t.installId,
        queued: t.queue.length,
        earlyErrors: t.earlyErrors.length,
        receiver: t.receiver ? { heard: t.rx.heard, bufferOpen: t.rx.bufferOpen, stickyOff: t.rx.stickyOff, senders: { ...t.rx.senders }, installId: t.rx.installId } : null,
      }),
      superProps: () => superProps(),
      queue: () => t.queue.map((i) => ({ name: i.name, props: { ...i.props } })),
      log: () => t.log.slice(),
      sent: () => t.sent.slice(),
      errorsSent: () => t.errorsSent.slice(),
      loads: () => t.loads.slice(),
      message: () => telemetryMessage(),
    },
  }),
);
