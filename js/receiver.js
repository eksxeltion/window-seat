// Window Seat Cast receiver: one full-screen cam that rotates, plus a visible music player.
// Runs on the Cast device. With ?debug=1 it runs in any browser without starting the Cast context.
// The debug strip is hidden unless Debug mode is on: {type:"debug", on:true} from the phone, ?debug=1, or ?strip=1
// (a test hook that shows the strip but still starts CAF).
// Launch custom data: the web sender (CastContext.requestSession) has no way to pass custom launch data to the
// receiver without changing how sessions are created, so a TV whose channel never connects is diagnosed by
// loading receiver.html?debug=1 (or ?strip=1) instead. The registered receiver URL stays unchanged.
import { CAMS, MIXES } from "./data.js";
import { applyConfig, refreshFromSheet, listInfo } from "./config.js";
import { camLocalTime, placeLine, rotationPool } from "./localtime.js";
import { initTelemetry, capture, captureError, receiverTelemetryMessage, flushTelemetry, telemetryActive } from "./telemetry.js";

export const BUILD = "2026-11-19.33"; // shown on the TV; bump on every receiver change
const NAMESPACE = "urn:x-cast:com.windowseat";
const CAF_URL = "https://www.gstatic.com/cast/sdk/libs/caf_receiver/v3/cast_receiver_framework.js";
const YT_URL = "https://www.youtube.com/iframe_api";
const YS = { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 };

const FADE_MS = 400;
const CAM_LOAD_MS = 12000; // pending cam in a state that is not a wait (not UNSTARTED, BUFFERING or an ad) this long: skipped
const CAM_PATIENT_MS_DEFAULT = 60000; // pending cam still UNSTARTED or BUFFERING on its own id (or playing an ad): wait this long in total
// Pending cam that has loaded nothing at all (getVideoLoadedFraction exactly 0, current time not advancing, no ad) while
// UNSTARTED or BUFFERING on its own id: fail it after this long and go straight to the next cam. Seen on Cast for every
// server-stitched ad (SSDAI) live cam, which never plays in the IFrame player there (docs/cast-ssdai.md). Anything that
// shows media is moving (loaded fraction above 0, current time advancing, an ad) keeps the CAM_PATIENT_MS ceiling.
const CAM_NO_MEDIA_MS_DEFAULT = 8000;
const LOADING_CARD_MS = 3000; // pending this long: the "Loading cam..." card replaces the black field
const ERR5_RETRY_MS = 2000; // error 5 while pending: reload the same id once after this delay
const STALL_NUDGE_MS = 20000; // visible cam not PLAYING this long: nudge playVideo
const STALL_SKIP_MS = 15000; // still not PLAYING this long after the nudge: skip it
const RETRY_ALL_FAILED_MS = 60000;
const FLASH_MS = 1800; // on-TV confirmation of a phone command
const PIN_FLASH_MS = 5000; // the pin-on toast stays longer so it can be read from the couch
const LOWER_THIRD_MS = 10000; // the lower third shows this long after a cam loads, then fades (800ms) to a quiet TV
const PAUSE_WARN_MS_DEFAULT = 25 * 60 * 1000; // paused this long while casting: warn the phone and the TV pill
const PAUSE_END_MS_DEFAULT = 30 * 60 * 1000; // paused this long while casting: end the session, the TV goes home
const PAUSE_STOP_DELAY_MS = 600; // the pauseTimeout message goes out first, then the receiver stops
const FONTS_URL = "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT,WONK@9..144,600,0,0&family=Plus+Jakarta+Sans:wght@500;600&display=swap";

const PARAMS = new URLSearchParams(location.search);
const DEBUG = PARAMS.get("debug") === "1";
const STRIP_FORCED = DEBUG || PARAMS.get("strip") === "1"; // strip stays on after a session ends
/* Test override for the pause timeout: ?pauseWarnMs=...&pauseEndMs=... (positive numbers), or __receiver.setPauseTimeout. */
const PAUSE_OVERRIDE = PARAMS.has("pauseWarnMs") || PARAMS.has("pauseEndMs"); // shown on the strip: a short-timer session
function msParam(name, fallback) {
  const n = Number(PARAMS.get(name));
  return PARAMS.has(name) && Number.isFinite(n) && n > 0 ? n : fallback;
}
/* Test override for the pending-load patience: ?camPatientMs=... Only a URL that carries the parameter changes it
   (the registered receiver URL never does), same pattern as the pause timeout above. */
const CAM_PATIENT_MS = msParam("camPatientMs", CAM_PATIENT_MS_DEFAULT);
/* Same pattern for the no-media fast fail: ?camNoMediaMs=... (tests only). */
const CAM_NO_MEDIA_MS = msParam("camNoMediaMs", CAM_NO_MEDIA_MS_DEFAULT);
const $ = (id) => document.getElementById(id);
const quietly = (fn) => {
  try {
    return fn();
  } catch {
    return undefined;
  }
};

/* ---------------------------------------------------------------- telemetry */

// Off until a phone sends {type:"telemetry", on:true}; see js/telemetry.js and docs/telemetry.md section 5.
let context = null; // CastReceiverContext, only when running on a Cast device
initTelemetry({
  surface: "receiver",
  build: BUILD,
  castSessionId: () => quietly(() => context.getApplicationData().sessionId),
});
const msSinceLoad = () => Math.round(performance.now());
let firstSenderAt = 0; // first phone connected (seconds_in_session counts from here, else from load)
const secondsInSession = () => Math.round((Date.now() - (firstSenderAt || performance.timeOrigin || Date.now())) / 1000);
let firstFrameSent = false;
let pausedSince = 0;
let shownReason = "start"; // why the pending cam loads, in the telemetry vocabulary
let mixStart = null; // { mixId, userChoice } until that mix first reaches PLAYING
let mixChoice = false; // set by a phone command just before playMix
const AUTO_REASONS = new Set(["start", "rotate", "skip", "retry", "list"]);

/** cam_shown: user navigation always, automatic reasons 1 in 4 with sampled: 4. */
function camShown(camId, reason, extra) {
  const user = !AUTO_REASONS.has(reason);
  if (!user && Math.random() >= 0.25) return;
  capture("cam_shown", { cam_id: camId, reason, mode: "cast", sampled: user ? 1 : 4, ...extra });
}

const LIST_ERRORS = [
  [/too long|timeout/i, "timeout"],
  [/HTTP|private/i, "http"],
  [/not CSV/i, "parse"],
  [/no usable/i, "unusable"],
  [/could not read|network|fetch/i, "network"],
];
function listError(text) {
  for (const [re, code] of LIST_ERRORS) if (re.test(String(text || ""))) return code;
  return "parse";
}

/** Stop the receiver after telling telemetry (sent with sendBeacon, then a flush that also lets a still loading
 *  PostHog send what it queued). No wait when it is off. One stop at a time: a repeated stop while the flush runs
 *  neither schedules a second stop nor counts a second receiver_stop. */
let stopInFlight = false;
function stopWithTelemetry(reason, stop) {
  if (stopInFlight) return;
  capture("receiver_stop", { reason, seconds_in_session: secondsInSession() }, { beacon: true });
  if (!telemetryActive()) return stop();
  stopInFlight = true;
  const done = () => {
    stopInFlight = false;
    stop();
  };
  flushTelemetry(600, 2000).then(done, done);
}

/* ---------------------------------------------------------------- state and log */

let configured = false; // see CONFIGURES and snapshot()
const s = {
  camId: null,
  pinned: false,
  interval: 60,
  mixId: MIXES[0].id,
  volume: 60,
  muted: false,
  paused: false, // the whole feed (cam and music) is paused; independent of pinned
  shuffle: true, // the phone's session-only Shuffle: when a mix ends, pick a random other one (else list order)
  poolIds: null, // cam ids chosen on the phone (Cams filters); null = the default cams
  skipSleeping: true, // time-of-day rules from the phone's Cams sheet (session-only there too)
  onlyDaytime: false,
  failed: new Set(),
};
const log = [];
/* Never throws: callers such as failCam rely on it to reach the skip and the retry timer. */
function note(type, detail) {
  try {
    log.push({ t: Date.now(), type, detail });
    if (log.length > 500) log.shift();
  } catch {
    // logging never blocks the caller
  }
  if (!DEBUG) return;
  try {
    console.log("[receiver]", type, detail === undefined ? "" : JSON.stringify(detail));
  } catch {
    // logging never blocks the caller
  }
}

const camById = (id) => CAMS.find((c) => c.id === id) || null;
/* A web-only cam (the sheet's cast column, js/config.js parseCastable) never loads on the TV: not in rotation, not as
   the start cam, and a phone request for one is ignored. It still plays on the phone. */
const isCastable = (c) => !!c && c.castable !== false;
const castableById = (id) => isCastable(camById(id));
const mixById = (id) => MIXES.find((m) => m.id === id) || null;

function snapshot() {
  const cam = camById(s.camId);
  const mix = mixById(s.mixId);
  return {
    type: "state",
    build: BUILD,
    camId: s.camId,
    camTitle: cam ? cam.title : "",
    mixId: s.mixId,
    mixTitle: mix ? mix.en : "",
    pinned: s.pinned,
    // True once any sender set up or changed playback: a new phone session then joins instead of pushing its choices.
    configured,
    interval: s.interval,
    volume: s.volume,
    muted: s.muted,
    paused: s.paused,
    shuffle: s.shuffle,
    musicPaused: s.paused, // older name, kept for senders that still read it
    poolIds: s.poolIds,
    skipSleeping: s.skipSleeping,
    onlyDaytime: s.onlyDaytime,
    failedCams: Array.from(s.failed),
    // Pause timeout (receiver owned): true from the warning point until resume, Keep paused or another command.
    pauseWarning: pt.phase === "warning",
    pauseEndsInMs: pt.phase === "running" || pt.phase === "warning" ? Math.max(0, pt.endAt - Date.now()) : null,
    list: { source: listInfo.source, cams: listInfo.cams, mixes: listInfo.mixes },
    // Diagnostics mirrored on the phone's debug panel (small, always sent).
    debug: { strip: stripOn, pause: pauseText(), caf: dbg.caf, cafError: dbg.cafError, readyNs: dbg.readyNs, rx: dbg.rx, tx: dbg.tx, appState: dbg.appState, senders: dbg.senders, error: dbg.error },
  };
}

/* ---------------------------------------------------------------- on-TV debug strip */

// cafError is its own field: later errors (broadcast, handle, sheet) never overwrite the CAF start failure.
const dbg = { caf: "loading", appId: "?", cafError: "", listener: "no", readyNs: "?", rx: 0, tx: 0, appState: "-", senders: 0, lastIn: "none", lastOut: "none", error: "" };
let injected = false; // test hook: a mocked context stands in for CAF
let cafStarted = false; // context.start() returned without throwing (or a test injected a context)
let stripOn = STRIP_FORCED; // hidden by default: Debug mode on the phone turns it on
function hhmmss() {
  return new Date().toTimeString().slice(0, 8);
}

/* Ring buffer of the last Cast events (readable by tests through window.__receiverDebug). Repeats collapse. */
const EVENTS_MAX = 20;
const events = [];
function castEvent(text) {
  const t = hhmmss();
  const last = events[events.length - 1];
  if (last && last.text === text) {
    last.n = (last.n || 1) + 1;
    last.t = t;
  } else {
    events.push({ t, text: String(text).slice(0, 120) });
    if (events.length > EVENTS_MAX) events.shift();
  }
  if (DEBUG) console.log("[receiver event]", t, text);
}

function renderStrip() {
  const el = $("debug-strip");
  if (!el) return;
  el.hidden = !stripOn;
  if (!stripOn) return;
  const caf = dbg.cafError ? `CAF FAILED (${dbg.cafError})` : `CAF ${dbg.caf}`;
  const out = !cafStarted && dbg.lastOut === "none" ? "none (CAF not started)" : dbg.lastOut;
  const ev = events[events.length - 1];
  const lines = [
    "WINDOW SEAT · DEBUG",
    `build ${BUILD} | app ${dbg.appId} | list ${listInfo.source} ${listInfo.cams}/${listInfo.mixes}`,
    `${caf} | listener ${dbg.listener} | senders ${dbg.senders} | ns windowseat STRING | ready-ns ${dbg.readyNs} | appstate ${dbg.appState}`,
    `rx ${dbg.rx} | tx ${dbg.tx} | last in: ${dbg.lastIn} | last reply: ${out}`,
    `cam ${s.camId || "-"} | mix ${s.mixId || "-"} | pause timer ${pauseText()}${PAUSE_OVERRIDE ? ` | override warn ${mmss(pt.warnMs)} end ${mmss(pt.endMs)}` : ""}`,
  ];
  if (ev) lines.push(`ev ${ev.t} ${ev.text}${ev.n ? ` x${ev.n}` : ""}`);
  if (dbg.error) lines.push(`error: ${dbg.error}`);
  el.textContent = lines.join("\n");
  el.classList.toggle("has-error", !!(dbg.error || dbg.cafError));
}
function setDbg(patch) {
  Object.assign(dbg, patch);
  renderStrip();
}

/* Replies go out as JSON text: the namespace is declared as STRING (see startCast). */
function reply(senderId) {
  if (!context || !cafStarted) return; // no sends before CAF started: they would only add noise
  // The real framework throws on a broadcast before READY (its transport is not up yet); the next one works.
  if (!senderId && dbg.caf !== "ready" && !injected) return;
  const kind = senderId ? "direct" : "broadcast";
  try {
    context.sendCustomMessage(NAMESPACE, senderId, JSON.stringify(snapshot()));
    dbg.tx++;
    castEvent(`tx ${kind} ok`);
    setDbg({ lastOut: `${kind} ${hhmmss()}` });
  } catch (e) {
    castEvent(`tx ${kind} FAILED ${String((e && e.message) || e).slice(0, 60)}`);
    setDbg({ lastOut: `${kind} FAILED ${hhmmss()}`, error: String((e && e.message) || e).slice(0, 80) });
  }
}

function broadcast() {
  reply(undefined);
}

/* One extra message (not the state) to every sender, with the same guards as a broadcast. */
function sendJson(obj) {
  if (!context || !cafStarted) return;
  if (dbg.caf !== "ready" && !injected) return;
  try {
    context.sendCustomMessage(NAMESPACE, undefined, JSON.stringify(obj));
    dbg.tx++;
    castEvent(`tx ${obj.type} ok`);
  } catch (e) {
    castEvent(`tx ${obj.type} FAILED ${String((e && e.message) || e).slice(0, 60)}`);
  }
}

/* Small on-TV confirmation for every phone command, so a tap is visibly received. */
let cmdTimer = 0;
function flash(text, ms = FLASH_MS) {
  const el = $("cmd-toast");
  if (!el || !text) return;
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(cmdTimer);
  cmdTimer = setTimeout(() => el.classList.remove("show"), ms);
}

/* Lower third: cam name, then "{city}, {country}" and the local time in accent-night after a 24px gap (time only
   with a valid IANA timezone; the gap is CSS, and a plain space keeps the text readable). No type tag, no channel.
   It shows for 10 seconds after a cam is committed, then fades; the quiet TV is video only. */
let lowerThirdTimer = 0;
function setCaption(cam) {
  $("cap-title").textContent = cam.title;
  const meta = $("cap-meta");
  const place = document.createElement("span");
  place.className = "cap-place";
  place.textContent = placeLine(cam);
  meta.replaceChildren(place);
  const time = camLocalTime(cam);
  if (time) {
    const t = document.createElement("span");
    t.className = "cap-time";
    t.textContent = time;
    meta.append(" ", t);
  }
}
function showLowerThird() {
  const el = $("caption");
  if (!el) return;
  clearTimeout(lowerThirdTimer);
  el.classList.remove("quiet");
  el.removeAttribute("aria-hidden");
  lowerThirdTimer = setTimeout(() => {
    el.classList.add("quiet");
    el.setAttribute("aria-hidden", "true");
  }, LOWER_THIRD_MS);
}

/* Persistent "Paused" pill while the feed is paused, so a frozen frame never looks like a crash. */
function showPausedPill(on) {
  const el = $("paused-pill");
  if (el) el.hidden = !on;
  const closing = $("paused-closing");
  // Emptied (not just hidden) so the pill reads exactly "Paused" until the warning point.
  if (closing) closing.textContent = on && pt.phase === "warning" ? "Closing in 5 minutes" : "";
}

/* ---------------------------------------------------------------- pause timeout */

// A paused Cast session rests after 30 minutes: warning at 25, then the receiver stops so the TV goes home.
// The receiver owns the timer, so it runs even while the phone sleeps. Resume, the stop command or an explicit
// disconnect from the phone cancels it; a phone command while paused (and Keep paused) restarts it. A phone that
// drops off or sleeps (ERROR or UNKNOWN disconnect) does not cancel it, so a TV paused and forgotten still rests.
const pt = {
  warnMs: msParam("pauseWarnMs", PAUSE_WARN_MS_DEFAULT),
  endMs: msParam("pauseEndMs", PAUSE_END_MS_DEFAULT),
  phase: "off", // off | running | warning | ended
  startedAt: 0,
  warnAt: 0,
  endAt: 0,
  restarts: 0,
  stops: 0, // receiver stops requested by the timeout (tests)
};
let ptWarnTimer = 0;
let ptEndTimer = 0;
let ptStopTimer = 0; // the delayed stopReceiver after pauseTimeout was sent
let ptGen = 0; // bumps on every start or cancel, so a superseded timeout never stops the receiver

function mmss(ms) {
  const sec = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}
function pauseText() {
  if (pt.phase === "running" || pt.phase === "warning") return `${pt.phase} ${mmss(pt.endAt - Date.now())}`;
  return pt.phase;
}

function clearPauseTimers() {
  clearTimeout(ptWarnTimer);
  clearTimeout(ptEndTimer);
  clearTimeout(ptStopTimer);
}

/** Start (or restart) the full timer. Only while the feed is paused. */
function startPauseTimer(why) {
  if (!s.paused) return;
  clearPauseTimers();
  ptGen++;
  const now = Date.now();
  if (pt.phase !== "off") pt.restarts++;
  pt.phase = "running";
  pt.startedAt = now;
  pt.warnAt = now + Math.min(pt.warnMs, pt.endMs);
  pt.endAt = now + pt.endMs;
  ptWarnTimer = setTimeout(pauseWarn, pt.warnAt - now);
  ptEndTimer = setTimeout(pauseEnd, pt.endAt - now);
  note("pauseTimer", { action: "start", why, warnMs: pt.warnMs, endMs: pt.endMs });
  showPausedPill(true);
  renderStrip();
}

function cancelPauseTimer(why) {
  if (pt.phase === "off") return;
  clearPauseTimers();
  ptGen++;
  pt.phase = "off";
  note("pauseTimer", { action: "cancel", why });
  showPausedPill(s.paused);
  renderStrip();
}

function pauseWarn() {
  if (!s.paused || pt.phase !== "running") return;
  pt.phase = "warning";
  note("pauseTimer", { action: "warn" });
  capture("pause_warning_shown", { seconds_paused: pausedSince ? Math.round((Date.now() - pausedSince) / 1000) : undefined });
  castEvent("pause warning");
  showPausedPill(true);
  renderStrip();
  broadcast(); // the phone shows the warning from pauseWarning in the state
}

/* Stop the receiver so the TV returns to its home screen. Guarded: the debug page has no CAF. */
function stopReceiver() {
  const ctx = context || quietly(() => window.cast.framework.CastReceiverContext.getInstance());
  if (ctx && typeof ctx.stop === "function") quietly(() => ctx.stop());
}

function pauseEnd() {
  if (!s.paused || (pt.phase !== "running" && pt.phase !== "warning")) return;
  clearPauseTimers();
  pt.phase = "ended";
  pt.stops++;
  note("pauseTimer", { action: "end" });
  castEvent("pause timeout: stopping");
  // Tell the phone first (it goes idle and explains why), then stop the session.
  sendJson({ type: "pauseTimeout" });
  if (!STRIP_FORCED) stripOn = false;
  renderStrip();
  const gen = ptGen;
  ptStopTimer = setTimeout(() => {
    // Play, Keep paused or stop casting in the delay cancelled or restarted the timer: do not stop.
    if (gen !== ptGen || pt.phase !== "ended") return;
    capture("pause_timeout_fired", { seconds_paused: pausedSince ? Math.round((Date.now() - pausedSince) / 1000) : undefined }, { beacon: true });
    stopWithTelemetry("pause_timeout", stopReceiver);
  }, PAUSE_STOP_DELAY_MS);
}

/* An explicit disconnect from the phone (REQUESTED_BY_SENDER) cancels the pause timer. A phone that drops off
   or sleeps (ERROR, UNKNOWN) does not, so a paused and forgotten TV still rests. Guarded: no CAF on the debug page. */
function isExplicitDisconnect(event) {
  const reasons = quietly(() => window.cast.framework.system.DisconnectReason);
  const explicit = (reasons && reasons.REQUESTED_BY_SENDER) || "REQUESTED_BY_SENDER";
  return !!event && event.reason === explicit;
}

/* ---------------------------------------------------------------- cam */

let camPlayer = null;
let camReady = false;
let token = 0; // bumps on every load so stale events are ignored
let pending = null; // { camId, startedAt, loading, err5 (0 none, 1 retry waiting, 2 reloaded), adSeen, lastPatientAt, cardShown,
//                     mediaAt (no-media clock start), mediaSeen (something loaded: the 60s ceiling applies), noMedia, t0 }
let nextSwapAt = 0;
let stall = null; // { at, nudged } while the visible cam is not PLAYING
let retryTimer = 0;
let rotateReason = "start";
let rotateOrigin = "start"; // what started the current swap; a failed candidate's fallback keeps it
let wantedCam = null; // camera requested before the YouTube player was ready
let awaitingRetry = false; // all cams failed: camTick stays idle until the retry timer fires
let noCastableIdle = false; // resting because no cam in the list is castable (a new list looks again at once)
let pausedRemaining = 0; // ms left on the rotation clock when the feed was paused (frozen while paused)

/* Load diagnostics: what the cam player did between loadVideoById and commit or failure, so a cam that times
   out on the TV says why (cam_failed and the camDiag log entry). Reset on every beginLoad; never throws.
   Every SDK value is reduced to an allowlisted primitive first: finite numbers, 11-char video ids, known quality
   names, integer state codes, short numeric error codes. No free text, no author or channel names
   (docs/telemetry.md, privacy rules). */
let trace = null; // { at (null until loadVideoById), states: [], ignoredPlaying, otherVid, errors: [] }
const VID_RE = /^[A-Za-z0-9_-]{11}$/;
const QUALITIES = new Set(["tiny", "small", "medium", "large", "hd720", "hd1080", "hd1440", "hd2160", "highres", "auto", "default", "unknown"]);
const vidOr = (v) => (typeof v === "string" && VID_RE.test(v) ? v : undefined);
const qualityOr = (v) => (typeof v === "string" && QUALITIES.has(v) ? v : undefined);
const fin = (v) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const roundTo = (v, k) => (v === undefined ? undefined : fin(Math.round(v * k) / k));
const intOr = (v, other) => (Number.isInteger(v) ? v : other);
const errCode = (v) => {
  try {
    const str = String(v);
    return /^\d{1,4}$/.test(str) ? str : "?";
  } catch {
    return "?";
  }
};
const sinceLoad = () => (trace && trace.at !== null ? fin(Date.now() - trace.at) : undefined);
/* scope "load": the failure is the pending load of this cam, so the load trace belongs to it.
   scope "snapshot": only the live player state (for example a committed cam that stalled later). */
function camDiag(camId, scope) {
  const data = quietly(() => camPlayer.getVideoData()) || {};
  const vid = vidOr(quietly(() => data.video_id));
  const isLive = quietly(() => data.isLive);
  const t = fin(quietly(() => camPlayer.getCurrentTime()));
  const loadedRaw = fin(quietly(() => camPlayer.getVideoLoadedFraction()));
  const loaded = loadedRaw === undefined ? undefined : Math.min(1, Math.max(0, loadedRaw));
  const muted = quietly(() => camPlayer.isMuted());
  const diag = {
    diag_v: 1,
    trace_scope: scope,
    last_state: intOr(quietly(() => camPlayer.getPlayerState()), undefined),
    vid,
    vid_match: vid !== undefined && vid === camId,
    is_live: typeof isLive === "boolean" ? isLive : undefined,
    t: roundTo(t, 10),
    loaded: roundTo(loaded, 100),
    quality: qualityOr(quietly(() => camPlayer.getPlaybackQuality())),
    levels: intOr(quietly(() => camPlayer.getAvailableQualityLevels().length), undefined),
    muted: typeof muted === "boolean" ? muted : undefined,
    music_state: intOr(quietly(() => musicPlayer.getPlayerState()), undefined),
  };
  if (scope === "load" && trace) {
    diag.ms = sinceLoad();
    diag.states = trace.states.join(",");
    diag.ignored_playing = intOr(trace.ignoredPlaying, 0);
    diag.other_vid = vidOr(trace.otherVid);
    diag.errors = trace.errors.length ? trace.errors.join(",") : undefined;
  }
  return diag;
}

const blackout = $("blackout");
/* Lifting the field (a cam is on screen again) also clears the unavailable message. */
const fadeTo = (on) => {
  blackout.classList.toggle("on", on);
  if (!on) {
    showUnavailable(false);
    showLoading(false);
  }
};

/* Unavailable stream: our own message on the night field, in place of YouTube's error card. Shown when a cam fails
   (player error, load timeout or stall), cleared when the next cam is committed. All cams failed: no countdown,
   since the retry is a minute away. */
const UNAVAILABLE_TEXT = "This window is closed for now. Next window in 5 seconds.";
const ALL_UNAVAILABLE_TEXT = "This window is closed for now.";
let unavailableShown = false;
function showUnavailable(on, text = UNAVAILABLE_TEXT) {
  const el = $("unavailable");
  if (!el || (!on && !unavailableShown)) return;
  unavailableShown = !!on;
  if (on) {
    $("unavailable-msg").textContent = text;
    hideSplash(); // first-cam failures must not stay under the launch splash
  }
  el.hidden = !on;
}

/* "Loading cam..." card: after LOADING_CARD_MS of a pending load the black field gets a calm card, so a slow cam (a
   hidden ad, a live stream that takes its time) does not look like a dead TV. Not over the launch splash (it already
   says "Opening a window"). Cleared with the field when the cam commits, and swapped for the unavailable message on a
   real failure. The player itself stays hidden: no YouTube spinner, ad or error card ever shows. */
function showLoading(on) {
  const el = $("loading");
  if (on) {
    // The previous cam's lower third sits above the card (same z-index, later in the DOM): fade it out now.
    clearTimeout(lowerThirdTimer);
    const cap = $("caption");
    if (cap) {
      cap.classList.add("quiet");
      cap.setAttribute("aria-hidden", "true");
    }
  }
  if (el) el.hidden = !on;
}

/* Pause cover: the night field over the cam while the feed is paused, so YouTube's paused UI never shows. On resume
   it stays until the cam reports PLAYING again (or a new cam is committed). */
function setPauseCover(on) {
  const el = $("pause-cover");
  if (el) el.classList.toggle("on", !!on);
}

/* Launch splash: fades out (1200ms, CSS) the first time a cam is committed, then is removed from layout for good.
   Later cam swaps only use the blackout above. */
const SPLASH_FADE_MS = 1200;
let splashDone = false;
function hideSplash() {
  if (splashDone) return;
  splashDone = true;
  const el = $("splash");
  if (!el) return;
  el.classList.add("gone");
  setTimeout(() => { el.hidden = true; }, SPLASH_FADE_MS + 100);
}

/* Cam audio is always off: every cam play path goes through these. */
function camMute() {
  quietly(() => camPlayer.mute());
  quietly(() => camPlayer.setVolume(0));
}
function camPlay() {
  camMute();
  quietly(() => camPlayer.playVideo());
  camMute();
}
/*
 * Resume the cam. A live stream jumps to the live edge (its duration), not where it froze.
 * A recording (archive, VOD) resumes from the pause point: seeking it to its duration would land
 * on the end, fire ENDED and get the cam skipped as failed, even when pinned.
 * Live check: getVideoData().isLive (exposed by the IFrame player, not documented). Only an explicit
 * true counts as live; a missing or non-boolean flag is treated as a recording, so a finite video is
 * never sent to its end. The worst case for a live cam is resuming slightly behind the edge.
 */
function camIsLive() {
  const data = quietly(() => camPlayer.getVideoData());
  return !!data && data.isLive === true;
}
function camToLiveEdge() {
  camMute();
  if (camIsLive()) {
    const d = quietly(() => camPlayer.getDuration());
    if (typeof d === "number" && d > 0) quietly(() => camPlayer.seekTo(d, true));
  }
  camPlay();
}

/* The rotation pool: the phone's checked cams (or the defaults), minus failed ones, minus sleeping cams (and
   non-daytime cams when Only daytime is on). If the rules leave nothing, the checked cams are used anyway.
   A cam that falls asleep while on screen is left at the next scheduled swap; a pinned cam never rotates. */
function pool() {
  const picked = s.poolIds && s.poolIds.length ? CAMS.filter((c) => s.poolIds.includes(c.id)) : CAMS.filter((c) => c.default);
  // Web-only cams never rotate on the TV (shuffle start, in-order, time-of-day fallback alike). If every chosen cam is
  // web-only, the castable default cams stand in, then any castable cam; with none at all the pool is empty.
  let chosen = picked.filter(isCastable);
  if (!chosen.length) chosen = CAMS.filter((c) => c.default && isCastable(c));
  if (!chosen.length) chosen = CAMS.filter(isCastable);
  return rotationPool(chosen.filter((c) => !s.failed.has(c.id)), { skipSleeping: s.skipSleeping, onlyDaytime: s.onlyDaytime });
}

/* No castable cam to show (every cam in the list is web-only): the night field with the calm closed message, and one
   new look a minute later (a newer list may have arrived). Never a tight loop: camTick stays idle meanwhile. */
function idleNoCastable() {
  note("noCastableCams", { cams: CAMS.length });
  noCastableIdle = true;
  castEvent("no castable cams");
  token++;
  pending = null;
  stall = null;
  awaitingRetry = true;
  clearTimeout(retryTimer);
  fadeTo(true);
  showLoading(false);
  showUnavailable(true, ALL_UNAVAILABLE_TEXT);
  retryTimer = setTimeout(() => loadNextCastable("retry", s.camId), RETRY_ALL_FAILED_MS);
}

/** Load the next castable cam after fromId, or rest on the night field when there is none. */
function loadNextCastable(reason, fromId) {
  const next = pick(fromId, 1);
  if (next) beginLoad(next, reason);
  else idleNoCastable();
}

/** Next usable cam after fromId in the given direction (1 or -1), in list order. */
function pick(fromId, dir) {
  const list = pool();
  if (!list.length) return null;
  const i = list.findIndex((c) => c.id === fromId);
  if (i !== -1) return list[(i + dir + list.length) % list.length].id;
  // fromId is outside the pool (asleep, unchecked or failed): continue from its place in the full list.
  const n = CAMS.length;
  const at = CAMS.findIndex((c) => c.id === fromId);
  if (at === -1) return (dir > 0 ? list[0] : list[list.length - 1]).id;
  for (let k = 1; k <= n; k++) {
    const cam = CAMS[(((at + dir * k) % n) + n) % n];
    if (list.includes(cam)) return cam.id;
  }
  return list[0].id;
}

function beginLoad(camId, reason = "rotate", origin = reason) {
  if (!camById(camId)) return;
  if (!castableById(camId)) {
    // Every caller picks from pool() or checks first; this is the last guard so a web-only cam is never even tried.
    note("castSkip", { camId, why: "web-only", reason });
    return;
  }
  if (!camReady) {
    wantedCam = camId; // YouTube not ready yet: onReady loads this instead of a random cam
    return;
  }
  wantedCam = null;
  awaitingRetry = false;
  noCastableIdle = false;
  clearTimeout(retryTimer);
  const my = ++token;
  rotateReason = reason;
  rotateOrigin = origin;
  shownReason = reason === "cam" ? "pick" : reason;
  pending = { camId, startedAt: Date.now(), loading: false, err5: 0, adSeen: false, lastPatientAt: Date.now(), cardShown: false, mediaAt: Date.now(), mediaSeen: false, noMedia: false, t0: undefined };
  trace = { at: null, states: [], ignoredPlaying: 0, otherVid: undefined, errors: [] };
  stall = null;
  showLoading(false);
  fadeTo(true);
  setTimeout(() => {
    if (my !== token) return;
    pending.loading = true;
    pending.startedAt = Date.now();
    pending.lastPatientAt = pending.startedAt;
    pending.mediaAt = pending.startedAt;
    camMute();
    trace.at = Date.now();
    quietly(() => camPlayer.loadVideoById(camId));
    camMute();
  }, s.camId ? FADE_MS : 0);
}

/* The wait, as telemetry fields for cam_shown and cam_failed (never throws). */
function loadFields(mine) {
  const t = fin(quietly(() => camPlayer.getCurrentTime()));
  const loadedRaw = fin(quietly(() => camPlayer.getVideoLoadedFraction()));
  const loaded = loadedRaw === undefined ? undefined : Math.min(1, Math.max(0, loadedRaw));
  return {
    load_ms: sinceLoad(),
    cur_time: roundTo(t, 10),
    loaded_frac: roundTo(loaded, 100),
    states: trace ? trace.states.join(",") : undefined,
    ad_suspected: mine && (mine.adSeen || (trace && trace.ignoredPlaying > 0)) ? 1 : 0,
    err5_retried: mine && mine.err5 === 2 ? 1 : 0, // 2 = the reload was actually made
    nudged: 0, // no playVideo while pending any more; the field stays for continuity
    retried: mine && mine.err5 === 2 ? 1 : 0, // the only reload is the error 5 one (same as err5_retried)
  };
}

function commitPending() {
  const cam = camById(pending.camId);
  if (!cam) { // left the list while loading
    pending = null;
    return;
  }
  const loadDiag = { ...loadFields(pending), ignored_playing: trace ? intOr(trace.ignoredPlaying, 0) : undefined };
  s.camId = cam.id;
  pending = null;
  stall = null;
  nextSwapAt = Date.now() + s.interval * 1000;
  if (s.paused) {
    // Changed cam while paused: show its first frame, then hold it (no autoplay).
    pausedRemaining = s.interval * 1000;
    camMute();
    quietly(() => camPlayer.pauseVideo());
  }
  setCaption(cam);
  showLowerThird();
  fadeTo(false);
  if (!s.paused) setPauseCover(false);
  hideSplash();
  note("rotate", { camId: cam.id, reason: rotateReason });
  camShown(cam.id, shownReason, loadDiag);
  // Time to first frame: the first cam after load that is not the random start cam (TV clock).
  if (!firstFrameSent && rotateReason !== "start") {
    firstFrameSent = true;
    capture("receiver_first_frame", { ms_since_load: msSinceLoad(), cam_id: cam.id });
  }
  renderStrip();
  broadcast();
}

/* why: "error", "load timeout", "stalled" or "no_media_fast_fail". The fast fail goes straight to the next cam: no
   "window closed" message (the Loading card stays up when it already was, else the black field or the splash). */
function failCam(camId, why) {
  note("camError", { camId, why });
  const fast = why === "no_media_fast_fail";
  const mine = pending && pending.camId === camId ? pending : null;
  const recovery = mine && mine.loading ? quietly(() => loadFields(mine)) : { nudged: 0, retried: 0 };
  const isLoadWait = why === "load timeout" || fast;
  const scope = isLoadWait && pending && pending.camId === camId ? "load" : "snapshot";
  const diag = isLoadWait || why === "stalled" ? quietly(() => camDiag(camId, scope)) : undefined;
  if (diag) {
    try {
      note("camDiag", { camId, why, ...diag });
    } catch {
      // diagnostics never block the skip
    }
  }
  if (!s.failed.has(camId)) {
    try {
      capture("cam_failed", { ...diag, ...recovery, cam_id: camId, why: /^error/.test(why) ? "error" : String(why).replace(/\s+/g, "_") });
    } catch {
      // diagnostics never block the skip
    }
  }
  s.failed.add(camId);
  const from = pending && pending.camId === camId ? rotateOrigin : "skip";
  pending = null;
  stall = null;
  const next = pick(camId, 1);
  const cardWasUp = !!(mine && mine.cardShown);
  showLoading(false);
  if (fast && next) showUnavailable(false);
  else showUnavailable(true, next ? UNAVAILABLE_TEXT : ALL_UNAVAILABLE_TEXT);
  if (next) {
    beginLoad(next, "skip", from);
    if (fast && cardWasUp && pending && pending.camId === next) {
      // Keep the calm Loading card on screen through the swap instead of flashing back to black.
      pending.cardShown = true;
      showLoading(true);
    }
  } else {
    // Every default cam failed: black screen, forget the failures and try again in a minute.
    note("allFailed");
    capture("cams_all_failed", { mode: "cast" });
    s.failed.clear();
    token++;
    awaitingRetry = true;
    fadeTo(true);
    retryTimer = setTimeout(() => loadNextCastable("retry", s.camId), RETRY_ALL_FAILED_MS);
  }
  broadcast();
}

function onCamState(e) {
  const code = e.data;
  if (code === YS.PLAYING || code === YS.BUFFERING) camMute();
  if (code === YS.PLAYING && !s.paused) setPauseCover(false);
  if (pending && pending.loading && trace && trace.states.length < 24) trace.states.push(intOr(code, "?") + "@" + sinceLoad());
  if (pending && pending.loading && code === YS.PLAYING) {
    const vid = quietly(() => camPlayer.getVideoData().video_id);
    if (!vid || vid === pending.camId) commitPending();
    else if (trace) {
      trace.ignoredPlaying++;
      trace.otherVid = vidOr(vid);
      pending.adSeen = true;
    }
  }
}

function onCamError(e) {
  // 2, 5, 100, 101, 150, 153 and anything else: the cam cannot play, skip it.
  // Ignore late errors while waiting out the all-failed backoff.
  if (awaitingRetry) return;
  const camId = pending ? pending.camId : s.camId;
  // Only fail the cam the player actually reports, so a late error from a replaced video is ignored.
  const reported = quietly(() => camPlayer.getVideoData().video_id);
  const code = errCode(quietly(() => e.data));
  if (reported && camId && reported !== camId) {
    // Not captured as player_error: keep it for the load diagnostics.
    if (pending && pending.loading && trace && trace.errors.length < 8) trace.errors.push(code);
    return;
  }
  if (camId) {
    try {
      capture("player_error", { player: "cam", cam_id: camId, code, mode: "cast" });
    } catch {
      // telemetry never blocks the skip
    }
    // Error 5 (HTML5 player error) while this cam loads: reload the same id once after a short delay. A second error 5
    // fails the cam (err5 only ever goes 0 -> 1 -> 2, so it cannot loop). 2, 100, 101, 150, 153 and the rest fail at once.
    if (code === "5" && pending && pending.loading && pending.camId === camId) {
      if (pending.err5 === 0) {
        const mine = pending;
        mine.err5 = 1;
        note("err5Retry", { camId, ms: sinceLoad() });
        setTimeout(() => {
          if (pending !== mine || mine.err5 !== 1) return;
          mine.err5 = 2;
          // The reload is a fresh attempt: it gets its own CAM_NO_MEDIA_MS no-media clock (the 60s cap still counts
          // from the first load). mediaSeen stays: a stream that already showed media keeps the patient ceiling.
          mine.mediaAt = Date.now();
          mine.noMedia = false;
          mine.t0 = undefined;
          camMute();
          quietly(() => camPlayer.loadVideoById(camId));
          camMute();
        }, ERR5_RETRY_MS);
        return;
      }
      if (pending.err5 === 1) return; // a repeat while the reload is waiting
    }
    failCam(camId, "error");
  }
}

/* A pending load that is stuck rather than broken: the player is UNSTARTED or BUFFERING on this cam's own video id
   (or reports none). Seen on the TV for some live cams that never leave BUFFERING or UNSTARTED until poked.
   PLAYING for another id (an ad) and other states do not qualify. An error for this cam's own id fails the cam at
   once in onCamError, so it never gets here; trace.errors only holds errors for OTHER ids (a late error from the
   previous cam) and must not veto the retry. */
function pendingStalled() {
  const st = quietly(() => camPlayer.getPlayerState());
  if (st !== YS.UNSTARTED && st !== YS.BUFFERING) return false;
  const vid = quietly(() => camPlayer.getVideoData().video_id);
  if (vid && vid !== pending.camId) return false;
  return true;
}

/* An ad (or anything that is not this cam) is playing in the player: it reports another video id while PLAYING or
   BUFFERING. The player is hidden behind the field, so the ad plays unseen and must be waited out. */
function pendingOtherVideo() {
  const st = quietly(() => camPlayer.getPlayerState());
  if (st !== YS.PLAYING && st !== YS.BUFFERING) return false;
  const vid = quietly(() => camPlayer.getVideoData().video_id);
  return !!vid && vid !== pending.camId;
}

/* Pending load watchdog. No playVideo while pending (it kills the pre-roll ad some live cams start with). A load that
   is UNSTARTED or BUFFERING on its own id, or playing an ad, is waited out for CAM_PATIENT_MS in total, measured from
   the first loadVideoById (the trace clock). A load in any other state (PAUSED, ENDED, CUED, PLAYING on its own id
   is a commit already) for CAM_LOAD_MS in a row is not going to recover and fails at once. No timeout reload: it
   would restart the ad and the buffering; the only reload is the single error 5 one in onCamError. */
/* Sample whether the pending cam shows any media (only called while it is UNSTARTED or BUFFERING on its own id).
   Loaded fraction above 0, or current time moving past its first sample, marks mediaSeen for the rest of the load.
   noMedia is true only when the player positively reports a loaded fraction of exactly 0: a player without the call
   (or one that throws) never fast-fails, it keeps the 60s ceiling. */
function sampleMedia(mine) {
  const loaded = fin(quietly(() => camPlayer.getVideoLoadedFraction()));
  const t = fin(quietly(() => camPlayer.getCurrentTime()));
  if (t !== undefined) {
    if (mine.t0 === undefined) mine.t0 = t;
    else if (t > mine.t0 + 0.05) mine.mediaSeen = true;
  }
  if (loaded !== undefined && loaded > 0) mine.mediaSeen = true;
  mine.noMedia = loaded === 0;
}

function pendingTick(now) {
  if (!pending.loading) return;
  const age = now - pending.startedAt;
  const ad = pendingOtherVideo();
  if (ad) pending.adSeen = true;
  if (ad) pending.mediaSeen = true; // an ad is media: wait it out under the patient ceiling
  const stalled = !ad && pendingStalled();
  if (stalled) sampleMedia(pending);
  // A scheduled error 5 reload has priority over the state cutoff (it must get to run); the total cap still applies.
  if (ad || pending.err5 === 1 || stalled) pending.lastPatientAt = now;
  // Nothing at all after CAM_NO_MEDIA_MS (never while an error 5 reload waits): fail fast, next cam at once.
  if (stalled && !pending.mediaSeen && pending.noMedia && pending.err5 !== 1 && now - pending.mediaAt >= CAM_NO_MEDIA_MS) {
    failCam(pending.camId, "no_media_fast_fail");
    return;
  }
  if (age > CAM_PATIENT_MS || now - pending.lastPatientAt > Math.min(CAM_LOAD_MS, CAM_PATIENT_MS)) {
    failCam(pending.camId, "load timeout");
    return;
  }
  if (!pending.cardShown && age >= LOADING_CARD_MS && splashDone) {
    pending.cardShown = true;
    showUnavailable(false);
    showLoading(true);
  }
}

function camTick() {
  if (!camReady || awaitingRetry) return;
  const now = Date.now();
  if (pending) {
    pendingTick(now);
    return;
  }
  if (!s.camId) return;
  if (s.paused) return; // paused: no stall watchdog and no rotation
  const live = quietly(() => camPlayer.getPlayerState());
  if (live === YS.PLAYING) {
    stall = null;
  } else if (live != null) {
    if (!stall) stall = { at: now, nudged: false };
    if (!stall.nudged && now - stall.at > STALL_NUDGE_MS) {
      stall = { at: now, nudged: true };
      note("stall", { camId: s.camId, action: "nudge" });
      camPlay();
    } else if (stall.nudged && now - stall.at > STALL_SKIP_MS) {
      note("stall", { camId: s.camId, action: "skip" });
      failCam(s.camId, "stalled");
    }
    return;
  }
  if (!s.pinned && now >= nextSwapAt) {
    const next = pick(s.camId, 1);
    if (next && next !== s.camId) beginLoad(next);
    else nextSwapAt = now + s.interval * 1000;
  }
}

function createCamPlayer() {
  camPlayer = new window.YT.Player("cam-player", {
    host: "https://www.youtube-nocookie.com",
    width: "100%",
    height: "100%",
    playerVars: { autoplay: 1, mute: 1, controls: 0, disablekb: 1, fs: 0, iv_load_policy: 3, modestbranding: 1, rel: 0, playsinline: 1, origin: location.origin },
    events: {
      onReady: () => {
        camReady = true;
        camMute();
        const list = pool();
        const wanted = wantedCam && castableById(wantedCam) ? wantedCam : null;
        const first = wanted || (list[Math.floor(Math.random() * list.length)] || {}).id;
        if (first) beginLoad(first, wanted ? "cam" : "start");
        else idleNoCastable();
      },
      onStateChange: onCamState,
      onError: onCamError,
    },
  });
}

/* ---------------------------------------------------------------- music */

let musicPlayer = null;
let musicReady = false;
let musicErrors = 0;
let musicRetryTimer = 0;

function showMixTitle() {
  const mix = mixById(s.mixId);
  $("music-title").textContent = mix ? mix.en : "";
  $("cap-music").textContent = mix ? `Now playing: ${mix.en}` : "";
}

function playMix(id) {
  if (!mixById(id)) return;
  clearTimeout(musicRetryTimer); // a newer selection supersedes any pending all-failed retry
  s.mixId = id;
  showMixTitle();
  mixStart = { mixId: id, userChoice: mixChoice };
  mixChoice = false;
  if (musicReady) {
    applyAudio();
    // Every mix starts at its beginning, never partway (a pick from the phone, shuffle, or the next in order).
    quietly(() => musicPlayer.loadVideoById({ videoId: id, startSeconds: 0 }));
  }
  broadcast();
}

/** Shuffle: a random mix, never the current one twice in a row. Off: the next one in sheet order. */
function nextMix() {
  if (s.shuffle && MIXES.length > 1) {
    let m;
    do m = MIXES[Math.floor(Math.random() * MIXES.length)];
    while (m.id === s.mixId);
    return playMix(m.id);
  }
  const i = MIXES.findIndex((m) => m.id === s.mixId);
  playMix(MIXES[(i + 1) % MIXES.length].id);
}

function onMusicState(e) {
  if (e.data === YS.PLAYING) {
    musicErrors = 0;
    clearTimeout(musicRetryTimer);
    if (mixStart && mixStart.mixId === s.mixId) {
      capture("mix_started", { mix_id: s.mixId, shuffle: s.shuffle, user_choice: mixStart.userChoice, mode: "cast" });
      mixStart = null;
    }
  }
  if (e.data === YS.ENDED) {
    note("mixEnded", { mixId: s.mixId });
    nextMix();
  }
}

function onMusicError(e) {
  note("mixError", { mixId: s.mixId, code: e.data });
  capture("player_error", { player: "music", mix_id: s.mixId, code: e.data, mode: "cast" });
  if (s.paused) return; // paused: Play restarts the mix
  musicErrors++;
  if (musicErrors >= MIXES.length) {
    // Every mix failed in a row: wait a minute before trying again.
    musicErrors = 0;
    clearTimeout(musicRetryTimer);
    musicRetryTimer = setTimeout(nextMix, RETRY_ALL_FAILED_MS);
    return;
  }
  nextMix();
}

function createMusicPlayer() {
  musicPlayer = new window.YT.Player("music-player", {
    host: "https://www.youtube-nocookie.com",
    width: 356,
    height: 200,
    playerVars: { autoplay: 1, controls: 0, disablekb: 1, fs: 0, iv_load_policy: 3, modestbranding: 1, rel: 0, playsinline: 1, origin: location.origin },
    events: {
      onReady: () => {
        musicReady = true;
        if (s.paused) {
          // Paused from the phone before the player was ready: load the mix without starting it.
          applyAudio();
          mixStart = { mixId: s.mixId, userChoice: false };
          quietly(() => musicPlayer.cueVideoById({ videoId: s.mixId, startSeconds: 0 }));
        } else playMix(s.mixId);
      },
      onStateChange: onMusicState,
      onError: onMusicError,
    },
  });
}

/* ---------------------------------------------------------------- commands */

function setVolume(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return;
  s.volume = Math.min(100, Math.max(0, Math.round(n)));
  if (s.volume > 0) s.muted = false;
  applyAudio();
  note("volume", { value: s.volume });
}

function applyAudio() {
  if (!musicReady) return;
  quietly(() => musicPlayer.setVolume(s.volume));
  if (s.muted || s.volume === 0) quietly(() => musicPlayer.mute());
  else quietly(() => musicPlayer.unMute());
}

function prevMix() {
  const i = MIXES.findIndex((m) => m.id === s.mixId);
  playMix(MIXES[(i - 1 + MIXES.length) % MIXES.length].id);
}

/* Pause or resume the whole feed: the cam and the music. Pin is separate and untouched.
 * Idempotent: a command for the state the feed is already in (a Cast retry after a lost ack, a
 * second Play) only refreshes the pill. It never reloads the mix, seeks the cam or resets the
 * rotation clock. Returns true only when the state actually changed. */
function setPaused(on, { music = true } = {}) {
  if (!!on === s.paused) {
    showPausedPill(s.paused);
    note("pauseRepeat", { value: s.paused });
    return false;
  }
  const was = s.paused;
  s.paused = !!on;
  pausedSince = s.paused ? Date.now() : 0;
  if (s.paused) {
    clearTimeout(musicRetryTimer); // a paused player must not be restarted by an error retry
    if (music && musicReady) quietly(() => musicPlayer.pauseVideo());
    if (!was) pausedRemaining = Math.max(0, nextSwapAt - Date.now());
    stall = null;
    if (camReady && s.camId && !pending) {
      camMute();
      quietly(() => camPlayer.pauseVideo());
    }
  } else {
    if (music && musicReady) {
      const st = quietly(() => musicPlayer.getPlayerState());
      if (st === YS.PAUSED || st === YS.CUED) quietly(() => musicPlayer.playVideo()); // continue where it stopped
      else playMix(s.mixId); // nothing loaded (or it failed): start the mix again
    }
    if (was) nextSwapAt = Date.now() + pausedRemaining;
    stall = null;
    if (camReady && s.camId && !pending) camToLiveEdge();
  }
  // Cover the cam the moment it pauses; on resume the cover lifts when the cam is PLAYING (see onCamState).
  if (s.paused) setPauseCover(true);
  else if (!camReady || !s.camId || pending || quietly(() => camPlayer.getPlayerState()) === YS.PLAYING) setPauseCover(false);
  if (s.paused) startPauseTimer("pause");
  else cancelPauseTimer("resume");
  showPausedPill(s.paused);
  note("pause", { value: s.paused });
  return true;
}

/** Accepts a command object, or JSON text of one (from any sender or namespace type). */
export function parseCommand(raw) {
  let cmd = raw;
  if (typeof cmd === "string") cmd = quietly(() => JSON.parse(cmd));
  if (typeof cmd === "string") cmd = quietly(() => JSON.parse(cmd)); // double-encoded JSON text
  return cmd && typeof cmd === "object" && typeof cmd.type === "string" ? cmd : null;
}

// Commands that set up or change playback. The first one applied marks the receiver configured for the page's life.
const CONFIGURES = new Set(["next", "prev", "pin", "mix", "nextMix", "prevMix", "cam", "cams", "pause", "playMusic", "interval", "volume", "mute"]);

const RESTARTS_PAUSE_TIMER = new Set(["next", "prev", "pin", "mix", "nextMix", "prevMix", "volume", "mute", "cam", "cams", "interval", "shuffle", "keepPaused"]);

function handle(raw, senderId) {
  const cmd = parseCommand(raw);
  if (!cmd) return;
  if (cmd.type === "telemetry") {
    // The phone's telemetry setting (docs/telemetry.md section 5). No state change, so no broadcast.
    note("command", { type: "telemetry", on: cmd.on === true });
    receiverTelemetryMessage(cmd, senderId);
    return;
  }
  note("command", cmd);
  const quiet = cmd.silent === true; // setup messages sent on connect: no on-TV flash
  // A phone command while paused restarts the pause timeout (setup messages sent on connect do not,
  // so a phone reconnecting during the warning still sees it). Commands that resume cancel it instead.
  if (s.paused && (RESTARTS_PAUSE_TIMER.has(cmd.type) && (!quiet || cmd.type === "cams"))) startPauseTimer(cmd.type);
  switch (cmd.type) {
    case "next":
    case "prev": {
      const dir = cmd.type === "next" ? 1 : -1;
      const target = pick(pending ? pending.camId : s.camId, dir);
      if (target) beginLoad(target, cmd.type);
      if (!quiet) flash(cmd.type === "next" ? "Next cam" : "Previous cam");
      break;
    }
    case "pin": {
      const was = s.pinned;
      s.pinned = !!cmd.value;
      // Pin holds the cam on screen: drop an automatic rotation (or its fallback) still loading. If the player still
      // has the cam (load call not made yet), just lift the fade; otherwise reload it. A cam the list dropped is left.
      if (s.pinned && pending && rotateOrigin === "rotate" && camById(s.camId)) {
        if (pending.loading || quietly(() => camPlayer.getVideoData().video_id) !== s.camId) beginLoad(s.camId, "retry");
        else {
          token++;
          pending = null;
          fadeTo(false);
        }
      }
      if (!s.pinned) {
        nextSwapAt = Date.now() + s.interval * 1000;
        pausedRemaining = s.interval * 1000;
      }
      if (!quiet && s.pinned && !was) flash("Pinned. Staying on this cam.", PIN_FLASH_MS);
      else if (!quiet) flash(s.pinned ? "Pinned" : "Rotation on");
      break;
    }
    case "mix":
      if (!mixById(cmd.id)) break;
      if (s.paused) setPaused(false, { music: false }); // choosing a mix plays the feed again
      mixChoice = !quiet; // the connect-time sync is not a choice
      playMix(cmd.id);
      if (!quiet) flash(`Music: ${(mixById(cmd.id) || {}).en || ""}`);
      break;
    case "nextMix":
    case "prevMix":
      if (s.paused) setPaused(false, { music: false });
      mixChoice = !quiet;
      if (cmd.type === "nextMix") nextMix();
      else prevMix();
      if (!quiet) flash(`Music: ${(mixById(s.mixId) || {}).en || ""}`);
      break;
    case "shuffle":
      // The phone's Shuffle switch (sent on connect, on resume and on every change). Steers only the next mix.
      s.shuffle = cmd.value !== false;
      if (!quiet) flash(s.shuffle ? "Shuffle on" : "Shuffle off");
      break;
    case "interval":
      s.interval = Number(cmd.value) === 30 ? 30 : 60;
      nextSwapAt = Date.now() + s.interval * 1000;
      if (s.paused) pausedRemaining = s.interval * 1000; // resume uses the new period
      if (!quiet) flash(`New cam every ${s.interval}s`);
      break;
    case "volume":
      setVolume(cmd.value);
      if (!quiet) flash(`Volume ${s.volume}`);
      break;
    case "mute":
      s.muted = !!cmd.value;
      applyAudio();
      if (!quiet) flash(s.muted ? "Music muted" : `Music on, volume ${s.volume}`);
      break;
    case "pause":
    case "playMusic": {
      // {type:"pause", value:true} pauses; the older {type:"playMusic", value:false} means the same.
      // Both pause or resume the whole feed (cam and music), so phone and TV can never disagree.
      const paused = cmd.type === "pause" ? !!cmd.value : !cmd.value;
      const changed = setPaused(paused);
      if (!quiet && changed && !paused) flash("Playing"); // pausing shows the persistent Paused pill instead
      break;
    }
    case "cams": {
      const ids = Array.isArray(cmd.ids) ? cmd.ids.filter((id) => camById(id)) : [];
      s.poolIds = ids.length ? ids : null;
      if (typeof cmd.skipSleeping === "boolean") s.skipSleeping = cmd.skipSleeping;
      if (typeof cmd.onlyDaytime === "boolean") s.onlyDaytime = cmd.onlyDaytime;
      if (!quiet) flash(`${pool().length} cams in rotation`);
      break;
    }
    case "cam":
      if (camById(cmd.id) && !castableById(cmd.id)) {
        // Web-only: the TV cannot play it (the phone shows it as Phone only and should not send it). Quietly ignored.
        note("castSkip", { camId: cmd.id, why: "web-only", reason: "phone" });
        castEvent("cam web-only: ignored");
        break;
      }
      if (camById(cmd.id)) {
        s.failed.delete(cmd.id);
        if (cmd.id !== s.camId || pending) beginLoad(cmd.id, "cam");
        if (quiet && pending && pending.camId === cmd.id) shownReason = "start"; // the phone's cam on connect, not a pick
        if (!quiet) flash(camById(cmd.id).title);
      }
      break;
    case "keepPaused":
      // "Keep paused" on the phone: the full timer again, still paused (restarted above). Never resumes.
      break;
    case "stop":
      // The only way to end the receiver: an explicit command from the remote.
      cancelPauseTimer("stop");
      if (!STRIP_FORCED) stripOn = false;
      if (context) stopWithTelemetry("sender_stop", () => quietly(() => context.stop()));
      break;
    case "getState":
      break;
    case "config":
      // The phone fetched the sheet and passes the lists along (works even if the TV cannot reach Google).
      if (applyConfig(cmd, "phone")) {
        capture("list_load", { ok: true, source: listInfo.source, cams: listInfo.cams, mixes: listInfo.mixes });
        afterListChange();
      }
      break;
    case "debug":
      // Toggle the on-TV debug strip from the phone: {on:true|false}, or the older {value:true|false}.
      stripOn = !!("on" in cmd ? cmd.on : cmd.value);
      castEvent(`debug ${stripOn ? "on" : "off"}`);
      renderStrip();
      break;
    default:
      return;
  }
  if (CONFIGURES.has(cmd.type) && !(cmd.type === "mix" && !mixById(cmd.id)) && !(cmd.type === "cam" && !castableById(cmd.id))) configured = true;
  broadcast();
}

/* ---------------------------------------------------------------- keep alive (Cast) */

// Known workaround: a Cast receiver with no media session is treated as idle and the device may close it,
// even with disableIdleTimeout. Playing a looping silent clip in a hidden <video> gives CAF something to
// play. YouTube iframes are not CAF media, so they do not count. Unverified here; test on a real device.
function silentWavUrl() {
  const rate = 8000;
  const n = rate; // 1 second, 8-bit mono, silence is 0x80
  const buf = new ArrayBuffer(44 + n);
  const v = new DataView(buf);
  const str = (o, t) => Array.from(t).forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + n, true);
  str(8, "WAVEfmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, rate, true);
  v.setUint32(28, rate, true);
  v.setUint16(32, 1, true);
  v.setUint16(34, 8, true);
  str(36, "data");
  v.setUint32(40, n, true);
  new Uint8Array(buf, 44).fill(0x80);
  return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
}

function prepareKeepAlive() {
  const video = $("keepalive");
  video.src = silentWavUrl();
  video.loop = true;
  video.muted = true;
  video.autoplay = true;
  video.playsInline = true;
  quietly(() => video.play());
  return video;
}

function onCustomMessage(event) {
  dbg.rx++;
  const raw = event && event.data;
  const cmd = parseCommand(raw);
  castEvent(`rx ${cmd ? cmd.type : "unreadable"}${event && event.senderId ? ` from ${String(event.senderId).slice(0, 24)}` : ""}`);
  setDbg({ lastIn: `${cmd ? cmd.type : "unreadable " + typeof raw} ${hhmmss()}` });
  if (!cmd) {
    capture("receiver_error", { kind: "message_parse", code: typeof raw });
    captureError(new Error(`Unreadable Cast message (${typeof raw})`), { kind: "message_parse" });
  }
  try {
    handle(cmd, event && event.senderId);
  } catch (e) {
    setDbg({ error: `handle: ${String((e && e.message) || e).slice(0, 80)}` });
  }
  // Also answer the sender directly, in case a broadcast does not reach it.
  if (event && event.senderId) reply(event.senderId);
}

/* Ask the platform to send a fresh receiver status, so senders re-read the app's namespace list. */
let appStateGeneration = 0;
function refreshAppState() {
  if (!context || !cafStarted) return;
  try {
    context.setApplicationState(`Window Seat ready ${++appStateGeneration}`);
    castEvent("appstate ok");
    setDbg({ appState: "ok" });
  } catch (e) {
    castEvent("appstate FAILED");
    setDbg({ appState: "fail", error: `appstate: ${String((e && e.message) || e).slice(0, 60)}` });
  }
}

/* Observe (never alter) the outgoing system "ready" message to see whether it lists our namespace. */
function watchOutgoing() {
  const look = (payload) => {
    try {
      const m = typeof payload === "string" ? JSON.parse(payload) : payload;
      if (!m || typeof m.namespace !== "string" || !m.namespace.endsWith("com.google.cast.system")) return;
      const d = typeof m.data === "string" ? JSON.parse(m.data) : m.data;
      if (d && d.type === "ready") {
        const list = Array.isArray(d.activeNamespaces) ? d.activeNamespaces : [];
        castEvent(`system ready: windowseat ${list.includes(NAMESPACE) ? "listed" : "not listed"} (${list.length} ns)`);
        setDbg({ readyNs: list.includes(NAMESPACE) ? "yes" : "no" });
      }
    } catch {
      /* diagnostics only */
    }
  };
  try {
    const ch = window.cast && window.cast.__platform__ && window.cast.__platform__.channel;
    if (ch && typeof ch.send === "function") {
      const orig = ch.send;
      ch.send = function (...args) {
        look(args[0]);
        return orig.apply(this, args);
      };
      return;
    }
    const proto = window.WebSocket && window.WebSocket.prototype;
    const orig = proto.send;
    proto.send = function (data) {
      try {
        if (String(this.url).includes("/v2/ipc")) look(data);
      } catch {
        /* ignore */
      }
      return orig.apply(this, arguments);
    };
  } catch {
    /* diagnostics only */
  }
}

function startCast() {
  const cast = window.cast;
  if (!cast || !cast.framework) {
    note("castUnavailable");
    castEvent("CAF missing");
    setDbg({ caf: "missing" });
    return;
  }
  try {
    context = cast.framework.CastReceiverContext.getInstance();
    const options = new cast.framework.CastReceiverOptions();
    options.mediaElement = $("keepalive");
    options.disableIdleTimeout = true;
    options.maxInactivity = 3600;
    options.statusText = "Window Seat";
    // STRING: CAF hands us the raw text and we parse it ourselves (parseCommand accepts objects too),
    // so a JSON parse quirk in the framework can never drop a command.
    options.customNamespaces = { [NAMESPACE]: cast.framework.system.MessageType.STRING };
    // NOTE: the custom message listener goes on AFTER start() (see below). In the real framework a listener
    // added before start() creates the namespace as JSON, and start() then throws "Invalid messageType".
    // A sender leaving (phone asleep, app closed, user disconnects) never stops playback.
    // Only an explicit {type:'stop'} command calls context.stop().
    context.addEventListener(cast.framework.system.EventType.SENDER_DISCONNECTED, (event) => {
      note("senderDisconnected", { reason: event && event.reason });
      castEvent(`senderDisconnected ${(event && event.reason) || ""}`.trim());
      const left = quietly(() => context.getSenders().length) ?? dbg.senders;
      // The Cast session is over for this TV: the debug strip goes back to hidden (unless forced by the URL).
      if (left === 0 && !STRIP_FORCED) stripOn = false;
      setDbg({ senders: left });
      capture("receiver_sender_left", { reason: String((event && event.reason) || "unknown"), senders_left: left, seconds_in_session: secondsInSession(), paused: s.paused });
      if (isExplicitDisconnect(event)) cancelPauseTimer("disconnect");
    });
    context.addEventListener(cast.framework.system.EventType.SENDER_CONNECTED, (event) => {
      castEvent(`senderConnected ${String((event && event.senderId) || "").slice(0, 24)}`.trim());
      if (!firstSenderAt) firstSenderAt = Date.now();
      setDbg({ senders: quietly(() => context.getSenders().length) ?? dbg.senders + 1 });
      refreshAppState();
      broadcast();
    });
    context.addEventListener(cast.framework.system.EventType.READY, () => {
      castEvent("CAF ready");
      // The registered app this receiver runs as (production or the test app), for the strip only.
      const appId = quietly(() => context.getApplicationData().id);
      setDbg({ caf: "ready", listener: "ready", ...(appId ? { appId: String(appId) } : {}) });
      capture("receiver_ready", { ms_since_load: msSinceLoad() });
      refreshAppState();
    });
    // Keep-alive media element is optional: a failure here must never prevent start().
    try {
      const keepAlive = prepareKeepAlive();
      const playerManager = context.getPlayerManager();
      if (typeof playerManager.setMediaElement === "function") playerManager.setMediaElement(keepAlive);
    } catch (e) {
      setDbg({ error: `keepalive: ${String((e && e.message) || e).slice(0, 60)}` });
    }
    setDbg({ caf: "starting" });
    watchOutgoing();
    castEvent("CAF start");
    context.start(options);
    cafStarted = true;
    // Same synchronous block as start(): no message can arrive before the listener exists.
    context.addCustomMessageListener(NAMESPACE, onCustomMessage);
    setDbg({ listener: dbg.caf === "ready" ? "ready" : "registered" });
    if (dbg.caf === "starting") setDbg({ caf: "started (waiting for ready)" });
    setTimeout(() => {
      if (dbg.caf !== "ready") {
        castEvent("CAF no READY after 10s");
        setDbg({ caf: "started, no READY after 10s" });
        capture("receiver_start_failed", { kind: "no_ready" });
      }
    }, 10000);
  } catch (e) {
    const name = (e && e.name) || "Error";
    castEvent(`CAF FAILED ${name}: ${String((e && e.message) || e).slice(0, 60)}`);
    setDbg({ caf: "FAILED", cafError: `${name}: ${String((e && e.message) || e).slice(0, 80)}` });
    capture("receiver_start_failed", { kind: "start_exception" });
    captureError(e, { kind: "caf_start" });
  }
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.async = true;
    el.onload = resolve;
    el.onerror = reject;
    document.head.append(el);
  });
}

/* ---------------------------------------------------------------- boot */

function startYouTube() {
  const previous = window.onYouTubeIframeAPIReady;
  window.onYouTubeIframeAPIReady = () => {
    if (typeof previous === "function") quietly(previous);
    createCamPlayer();
    createMusicPlayer();
  };
  if (window.YT && window.YT.Player) window.onYouTubeIframeAPIReady();
  else loadScript(YT_URL).catch(() => note("ytLoadFailed"));
}

renderStrip();
window.addEventListener("error", (e) => setDbg({ error: String(e.message || "script error").slice(0, 80) }));

// Cast first: the message channel must come up even if YouTube is slow or failing.
if (!DEBUG) {
  castEvent("CAF loading");
  loadScript(CAF_URL).then(startCast, () => {
    note("castUnavailable");
    castEvent("CAF load failed");
    setDbg({ caf: "load failed" });
    capture("receiver_start_failed", { kind: "framework_load" });
  });
} else {
  setDbg({ caf: "debug (off)" });
}

/* After the cam or mix list changed: drop vanished picks, keep playing what still exists. */
function afterListChange() {
  if (s.poolIds) s.poolIds = s.poolIds.filter((id) => camById(id));
  if (!s.poolIds || !s.poolIds.length) s.poolIds = null;
  for (const id of Array.from(s.failed)) if (!camById(id)) s.failed.delete(id);
  if (!mixById(s.mixId)) {
    if (s.paused) {
      // Paused stays paused: just cue the replacement.
      clearTimeout(musicRetryTimer);
      s.mixId = MIXES[0].id;
      if (musicReady) quietly(() => musicPlayer.cueVideoById(s.mixId));
      showMixTitle();
    } else playMix(MIXES[0].id);
  } else showMixTitle();
  // A cam that left the list, or that the sheet now marks web-only, is replaced like a cam that is gone.
  const gone = pending ? !castableById(pending.camId) : s.camId && !castableById(s.camId);
  if (gone) {
    token++; // drop callbacks of a load for a cam that no longer exists
    pending = null;
    let next = pick(null, 1);
    if (!next) {
      // Only failed cams are left: forget the failures and try again now rather than stay black.
      s.failed.clear();
      next = pick(null, 1);
    }
    if (next) beginLoad(next, "list");
    else idleNoCastable();
  } else if (noCastableIdle && camReady) {
    loadNextCastable("list", s.camId); // a new list while resting with nothing castable: look again now
  } else if (s.camId && !pending) {
    setCaption(camById(s.camId)); // same cam, maybe renamed in the sheet (the lower third is not shown again)
  }
  renderStrip();
  broadcast();
}

// The TV fetches the sheet itself too; a failure just keeps the saved copy or the built-in list.
const listStartedAt = Date.now();
refreshFromSheet().then((r) => {
  const listProps = { ok: !!r.ok, source: listInfo.source, ms: Date.now() - listStartedAt, cams: listInfo.cams, mixes: listInfo.mixes };
  if (!r.ok) listProps.error = listError(r.error);
  capture("list_load", listProps);
  if (!r.ok) capture("receiver_error", { kind: "list_load", code: listProps.error });
  if (r.ok) {
    if (r.changed) afterListChange();
    else renderStrip();
  } else setDbg({ error: `sheet: ${String(r.error).slice(0, 60)}` });
});

showMixTitle();
setInterval(camTick, 1000);
// Web fonts after load, so a failed or slow request never holds up the TV (the CSS stacks fall back).
window.addEventListener("load", () => {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = FONTS_URL;
  document.head.append(link);
});
try {
  startYouTube();
} catch (e) {
  setDbg({ error: `youtube: ${String((e && e.message) || e).slice(0, 60)}` });
}

// Read-only diagnostics for tests and remote debugging (also without ?debug=1).
Object.defineProperty(window, "__receiverDebug", {
  value: {
    events,
    dbg,
    get stripOn() { return stripOn; },
    get strip() { return ($("debug-strip") || {}).textContent || ""; },
    // Pause timeout durations and phase (also with ?strip=1, where __receiver does not exist).
    get pause() { return { warnMs: pt.warnMs, endMs: pt.endMs, phase: pt.phase, stops: pt.stops }; },
    // Launch splash: true once the first cam was committed and the splash started fading out.
    get splashDone() { return splashDone; },
    // Unavailable message and pause cover currently on screen.
    get unavailable() { return unavailableShown; },
    get pauseCover() { return !!($("pause-cover") && $("pause-cover").classList.contains("on")); },
  },
});

if (DEBUG) {
  // No Cast context in debug mode: same player logic, driven through window.__receiver.send.
  Object.defineProperty(window, "__receiver", {
    value: {
      get state() { return snapshot(); },
      send: (raw, senderId) => handle(raw, senderId),
      // Test hooks: the real Cast message path with a mocked context.
      message: (event) => onCustomMessage(event),
      useContext: (ctx) => {
        context = ctx;
        cafStarted = true;
        injected = true;
      },
      log,
      events,
      // Test probes for the soak run.
      music: () => ({ state: quietly(() => musicPlayer.getPlayerState()), time: quietly(() => musicPlayer.getCurrentTime()), vol: quietly(() => musicPlayer.getVolume()), muted: quietly(() => musicPlayer.isMuted()) }),
      cam: () => ({ state: quietly(() => camPlayer.getPlayerState()), muted: quietly(() => camPlayer.isMuted()), vol: quietly(() => camPlayer.getVolume()) }),
      // Test hook: make the rotation clock due now (as if the interval passed).
      expireRotation: () => {
        nextSwapAt = 0;
        pausedRemaining = 0;
      },
      // Test probe: ms left on the rotation clock (the frozen value while paused).
      rotationRemaining: () => (s.paused ? pausedRemaining : nextSwapAt - Date.now()),
      // Pause timeout: current timer state, and an override of the durations (applies to the next start).
      pauseTimer: () => ({ ...pt, remainingMs: pt.phase === "running" || pt.phase === "warning" ? Math.max(0, pt.endAt - Date.now()) : null }),
      // Test hook: the pause timer side of SENDER_DISCONNECTED (the CAF listener is not registered here).
      senderDisconnected: (event) => {
        if (isExplicitDisconnect(event)) cancelPauseTimer("disconnect");
        return pt.phase;
      },
      setPauseTimeout: ({ warnMs, endMs } = {}) => {
        if (Number.isFinite(warnMs) && warnMs > 0) pt.warnMs = warnMs;
        if (Number.isFinite(endMs) && endMs > 0) pt.endMs = endMs;
        return { warnMs: pt.warnMs, endMs: pt.endMs };
      },
    },
  });
}
