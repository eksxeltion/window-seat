// Window Seat: rotating live cams with Tokyo Chill Lab mixes underneath.
import { CAMS, MUSIC_CHANNEL, MIXES } from "./data.js";
import qrcode from "./qr.js";
import { initCast, castConfigured, sendConfigToTv, sendCamsToTv, isCasting, syncRemoteMode, remoteCmd, remoteMix, remoteCam, remoteVolume, remoteState, castDebugInfo, sendDebugToTv, sendShuffleToTv, telemetryCastState, telemetryCastSessionId, castAvailable } from "./cast.js";
import { initDebug, isDebugOn } from "./debug.js";
import { BUILD } from "./build.js";
import { refreshFromSheet, listLabel, listInfo } from "./config.js";
import { initTelemetry, capture, captureError, setTelemetryEnabled, isTelemetryOn, telemetryForcedOff, onTelemetryChange } from "./telemetry.js";
import { localTime, camLocalTime, awakeCams, placeLine, partOfDay, isSleeping, rotationPool, DEFAULT_TIME_RULES } from "./localtime.js";

/* ---------------------------------------------------------------- config */

const YT_API_URL = "https://www.youtube.com/iframe_api";
const YT_HOST = "https://www.youtube-nocookie.com";
const LOAD_TIMEOUT_MS = 15000; // a cam that is not PLAYING by then is marked unavailable
const COLD_LOAD_TIMEOUT_MS = 8000; // same deadline while no cam is on screen (cold start or current cam failed)
const STUCK_MS = 7000; // loaded but still UNSTARTED/CUED this long: treat as unplayable
const COLD_STUCK_MS = 4000; // same, while no cam is on screen
const FADE_MS = 400; // black overlay fade when switching cams
const GRACE_MS = 8000; // stall watchdog stays quiet this long after the tab becomes visible
const MUSIC_CHECK_MS = 5000;
const MUSIC_READY_MS = 15000; // music iframe must report ready by then
const API_TIMEOUT_MS = 20000;
const VOLUME_STEP = 10;
const ERROR_CONFIRM_MS = 2500; // a player error only fails a cam if the requested video is still not playing this long after
const TV_URL = "https://window-seat-lemon.vercel.app/?tv=1";
const CHROME_HOLD_MS = 6000; // local player: chrome stays up this long after a tap, then fades (not while paused or a sheet is open)
const THEME_CANVAS = "#F3F5F7"; // --color-canvas: Home, sheets, remote
const THEME_NIGHT = "#0A1A26"; // --color-night-field: the full-screen local player
const FONTS_URL =
  "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Plus+Jakarta+Sans:wght@500;600;700&family=Noto+Sans+JP:wght@400;500&display=swap";

const YS = { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 };

/* ---------------------------------------------------------------- storage */

const store = {
  get(key, fallback) {
    try {
      const raw = window.localStorage.getItem("windowSeat." + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem("windowSeat." + key, JSON.stringify(value));
    } catch {
      /* storage unavailable: settings just won't persist */
    }
  },
  remove(key) {
    try {
      window.localStorage.removeItem("windowSeat." + key);
    } catch {
      /* ignore */
    }
  },
};

function loadOverrides() {
  const raw = store.get("enabled", null);
  const out = {};
  if (raw && typeof raw === "object") {
    for (const cam of CAMS) if (typeof raw[cam.id] === "boolean") out[cam.id] = raw[cam.id];
  }
  return out;
}

/** TV mode: ?tv=1 forces it on, ?tv=0 forces it off, otherwise guess from the user agent and screen. */
function detectTv() {
  const q = new URLSearchParams(location.search).get("tv");
  if (q === "1") return true;
  if (q === "0") return false;
  if (/Android TV|GoogleTV|AFT|BRAVIA|SMART-TV|SmartTV|Tizen|Web0S|CrKey/i.test(navigator.userAgent || "")) return true;
  const mq = (query) => !!(window.matchMedia && window.matchMedia(query).matches);
  return window.screen.width >= 1600 && (mq("(pointer: coarse)") || mq("(hover: none)"));
}
const isTv = detectTv();

/* Telemetry (docs/telemetry.md): started first so early errors are kept; the SDKs load after first paint. */
function deviceClass() {
  if (isTv) return "tv";
  const w = Math.min(window.screen.width || 0, window.screen.height || 0);
  const coarse = !!(window.matchMedia && (window.matchMedia("(pointer: coarse)").matches || window.matchMedia("(hover: none)").matches));
  if (w && w < 600) return "phone";
  return coarse ? "tablet" : "desktop";
}
initTelemetry({ surface: "web", build: BUILD, deviceClass, castState: telemetryCastState, castSessionId: telemetryCastSessionId });
const cmdMode = () => (isCasting() ? "cast" : "local");
/** One `command` event for a local action (the cast path reports from js/cast.js act()). */
const localCommand = (type) => capture("command", { type, mode: "local" });
const AUTO_REASONS = new Set(["start", "rotate", "skip", "retry", "list"]);
const storedLite = store.get("lite", null); // null: the user never chose

const savedInterval = store.get("interval", 60);
const savedMix = store.get("mix", null);
const savedVolume = Number(store.get("volume", 60));
store.remove("shuffle"); // Shuffle is session-only now (on at every load); drop the value older builds stored

/** A random mix from the list, never `avoidId` when there is another one to choose. */
function randomMixId(avoidId) {
  if (!MIXES.length) return null;
  if (MIXES.length === 1) return MIXES[0].id;
  let m;
  do m = MIXES[Math.floor(Math.random() * MIXES.length)];
  while (m.id === avoidId);
  return m.id;
}

/* ---------------------------------------------------------------- state */

const state = {
  apiReady: false,
  apiFailed: false,
  started: false,
  // cams
  intervalSec: savedInterval === 30 ? 30 : 60,
  enabledOverrides: loadOverrides(), // which cams are checked, relative to the sheet's in_rotation set (localStorage)
  // Time of day (Cams sheet): session-only, never stored, so every page load starts with Skip on and Only daytime off.
  skipSleeping: DEFAULT_TIME_RULES.skipSleeping,
  onlyDaytime: DEFAULT_TIME_RULES.onlyDaytime,
  failed: new Set(),
  pinned: false,
  feedPaused: false, // Pause stops the whole feed (cam and music); independent of pinned
  pausedRemaining: 0, // ms left on the rotation clock when the feed was paused (frozen while paused)
  currentCamId: null,
  pending: null, // { camId, startedAt, dir, fails }
  nextSwapAt: 0,
  allFailed: false,
  lite: storedLite === null ? isTv : storedLite === true,
  // music
  // Shuffle is on at every load, so the first mix is a random one (not the last one played), from its beginning.
  mixId: randomMixId(savedMix),
  volume: Number.isFinite(savedVolume) ? Math.min(100, Math.max(0, Math.round(savedVolume))) : 60,
  muted: store.get("muted", false) === true,
  shuffle: true, // session-only, like Debug mode: on at every load, never stored
  musicWantsPlay: true,
  musicState: YS.UNSTARTED,
  musicErrors: 0,
  musicUnavailable: false,
  // ui
  chromeHidden: false, // local player chrome (top scrim and bottom glass) faded out; the floating toggle stays
  openPanel: null,
  casting: false,
};

const reducedMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

/* While Cast is connecting or connected this page is only a remote: ask the Cast framework (not just our own flag),
   show the remote, and send the control to the TV. Returns true when the caller must not act locally. */
function viaTv(send) {
  if (!isCasting()) return false;
  syncRemoteMode();
  if (send) send();
  return true;
}

/* ---------------------------------------------------------------- dom */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const body = document.body;

const el = {
  start: $("#start"),
  startBtn: $("#start-btn"),
  startStatus: $("#start-status"),
  bar: $("#bar"),
  top: $("#player-top"),
  chromeToggle: $("#chrome-toggle"),
  fullscreenBtn: $("#lp-fullscreen"),
  statusText: $("#lp-status-text"),
  hairline: $("#lp-hairline-fill"),
  nextBtn: $("#btn-next"),
  pinPill: $("#pin-pill"),
  caption: $("#caption"),
  capTitle: $(".cap-title"),
  capMeta: $(".cap-meta"),
  hero: { img: $("#hero-img"), time: $("#hero-time"), line: $("#hero-line"), cam: $("#hero-cam") },
  themeColor: document.querySelector('meta[name="theme-color"]'),
  tapMusic: $("#tap-music"),
  tuning: $("#tuning"),
  blackout: $("#blackout"),
  notice: $("#notice"),
  noticeTitle: $("#notice-title"),
  noticeText: $("#notice-text"),
  noticeBtn: $("#notice-btn"),
  toast: $("#toast"),
  camsList: $("#cams-list"),
  mixList: $("#mix-list"),
  volume: $("#music-volume"),
  volumeOut: $("#music-volume-out"),
  mixVolume: $("#mix-volume"),
  mixVolumeOut: $("#mix-volume-out"),
  panels: { cams: $("#panel-cams"), music: $("#panel-music"), tv: $("#panel-tv"), about: $("#panel-about") },
};

const quietly = (fn) => {
  try {
    return fn();
  } catch {
    return undefined;
  }
};

const camById = (id) => CAMS.find((c) => c.id === id) || null;
const mixById = (id) => MIXES.find((m) => m.id === id) || null;
const isEnabled = (id) => {
  if (id in state.enabledOverrides) return state.enabledOverrides[id];
  return !!camById(id)?.default;
};

const timeRules = () => ({ skipSleeping: state.skipSleeping, onlyDaytime: state.onlyDaytime });
const checkedCams = () => CAMS.filter((c) => isEnabled(c.id));

/** The cams auto-rotation may pick right now. Inclusion is the checked usable (not failed) cams, or every usable
 *  cam when none is checked. The time-of-day rules then apply to that set (or the set is used anyway when the rules
 *  leave none), the same as the TV does with the ids from tvCamIds(). */
function rotationCams(now = new Date()) {
  const usable = CAMS.filter((c) => !state.failed.has(c.id));
  const checked = usable.filter((c) => isEnabled(c.id));
  return rotationPool(checked.length ? checked : usable, timeRules(), now);
}

function rotationIds(now = new Date()) {
  return rotationCams(now).map((c) => c.id);
}

/** The cam ids a casting TV should rotate: the checked cams, or every cam when none is checked (as on the phone).
 *  Never an empty list, which the receiver reads as "use its default cams". */
function tvCamIds() {
  const checked = checkedCams();
  return (checked.length ? checked : CAMS).map((c) => c.id);
}

function formatLength(minutes) {
  return `${minutes} min`;
}

/* ---------------------------------------------------------------- toast */

let toastTimer = 0;
function toast(text, ms = 2200) {
  el.toast.textContent = text;
  el.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove("show"), ms);
}

/* ================================================================ cams */

// One cam player. To switch: fade a black overlay in, load the new cam, fade out once it is PLAYING.
const cp = { el: $("#cam"), player: null, ready: false, waiters: [], camId: null, seq: 0, loadedAt: 0, loadSeq: 0, nudged: false, deferred: null, errSeq: 0, errAt: 0 };
let activeStall = null; // { at, tried } while the on-screen cam is not PLAYING
let graceUntil = 0; // the stall watchdog stays quiet until then (right after the tab becomes visible)
let covered = true; // black overlay is up (the HTML starts with it on)

const rotateSec = () => (state.lite ? 60 : state.intervalSec);
const camQuality = () => (state.lite ? "small" : "medium"); // best-effort hint only; YouTube decides the real quality
const animMs = () => (reducedMotion.matches || state.lite ? 0 : FADE_MS);
const watchdogOn = () => !document.hidden && Date.now() >= graceUntil;

function playerOrigin() {
  return /^https?:$/.test(location.protocol) ? location.origin : undefined;
}

function cover(on) {
  if (covered === on) return;
  covered = on;
  el.blackout.classList.toggle("on", on);
}

/**
 * Size the cam iframe to cover the viewport, but never render it larger than 1920x1080 CSS px
 * (1280x720 in Lite). A smaller real viewport nudges YouTube's automatic quality choice down;
 * the page scales it back up with a CSS transform.
 */
function sizeCam() {
  const cap = state.lite ? 1280 : 1920;
  const w = Math.max(window.innerWidth, (window.innerHeight * 16) / 9);
  const s = Math.max(1, w / cap);
  const root = document.documentElement.style;
  root.setProperty("--cw", Math.round(w / s) + "px");
  root.setProperty("--ch", Math.round(((w / s) * 9) / 16) + "px");
  root.setProperty("--cs", String(s));
}

function setQuality() {
  // Best effort: current players may ignore this.
  quietly(() => cp.player.setPlaybackQuality(camQuality()));
}

// Cam audio is always off: there is no cam volume and no cam mute toggle. Every cam play path mutes first.
function hardMute() {
  quietly(() => cp.player.mute());
  quietly(() => cp.player.setVolume(0));
}

function camPlay() {
  hardMute();
  quietly(() => cp.player.playVideo());
  hardMute();
}

/**
 * Resume the cam. A live stream jumps to the live edge (its duration), not where it froze.
 * A recording (archive, VOD) resumes from the pause point: seeking it to its duration would land
 * on the end, fire ENDED and get the cam skipped as failed, even when pinned.
 * Live check: getVideoData().isLive (exposed by the IFrame player, not documented). Only an explicit
 * true counts as live; a missing or non-boolean flag is treated as a recording, so a finite video is
 * never sent to its end. The worst case for a live cam is resuming slightly behind the edge.
 */
function camIsLive() {
  const data = quietly(() => cp.player.getVideoData());
  return !!data && data.isLive === true;
}
function camToLiveEdge() {
  hardMute();
  if (camIsLive()) {
    const d = quietly(() => cp.player.getDuration());
    if (typeof d === "number" && d > 0) quietly(() => cp.player.seekTo(d, true));
  }
  camPlay();
}

function createCamPlayer() {
  const vars = {
    autoplay: 0,
    mute: 1,
    controls: 0,
    disablekb: 1,
    fs: 0,
    iv_load_policy: 3,
    modestbranding: 1,
    rel: 0,
    playsinline: 1,
    vq: camQuality(),
  };
  const origin = playerOrigin();
  if (origin) vars.origin = origin;
  cp.player = new window.YT.Player(cp.el.querySelector(".cam-player"), {
    host: YT_HOST,
    width: "1920",
    height: "1080",
    playerVars: vars,
    events: {
      onReady: () => {
        cp.ready = true;
        hardMute();
        setQuality();
        const frame = quietly(() => cp.player.getIframe());
        if (frame) {
          frame.setAttribute("tabindex", "-1");
          frame.setAttribute("title", "Live cam");
        }
        cp.waiters.splice(0).forEach((fn) => fn());
      },
      onStateChange: (e) => onCamState(e.data),
      onError: (e) => onCamError(e && e.data),
    },
  });
}

function loadCam(camId, delay) {
  cp.camId = camId;
  cp.loadedAt = 0;
  cp.nudged = false;
  cp.deferred = null;
  cp.errSeq = 0;
  const seq = ++cp.seq;
  const go = () => {
    if (state.casting || cp.camId !== camId || cp.seq !== seq) return;
    const run = () => {
      if (state.casting || cp.camId !== camId || cp.seq !== seq) return;
      if (document.hidden) {
        cp.deferred = run; // never start playback in a hidden tab; onVisibility issues it once
        return;
      }
      cp.deferred = null;
      if (state.pending && state.pending.camId === camId) state.pending.startedAt = Date.now();
      cp.loadedAt = Date.now();
      cp.loadSeq = seq; // load generation: terminal events are only trusted while no newer load was issued
      cp.nudged = false;
      hardMute();
      quietly(() => cp.player.loadVideoById({ videoId: camId, suggestedQuality: camQuality() }));
      hardMute();
    };
    if (cp.ready) run();
    else cp.waiters.push(run);
  };
  if (delay > 0) setTimeout(go, delay);
  else go();
}

function shows(camId) {
  const data = quietly(() => cp.player.getVideoData());
  return !data || !data.video_id || data.video_id === camId;
}

/** True when the latest load call is still the newest one. */
function hasCurrentLoad() {
  return !!cp.camId && !!cp.loadedAt && cp.loadSeq === cp.seq;
}

/** True when an ENDED event belongs to the video we meant to show (current load and matching id). */
function isCurrentLoad() {
  if (!hasCurrentLoad()) return false;
  const data = quietly(() => cp.player.getVideoData());
  return !!data && data.video_id === cp.camId;
}

/** Pick the next cam id from `fromId` in direction `dir`, skipping failed ones and (by the time-of-day rules)
 *  sleeping or non-daytime cams. A current cam that has fallen asleep is simply not picked again, so rotation
 *  leaves it at the next scheduled swap (no extra cut). Pin stops rotation, so a pinned cam stays past midnight. */
function pickCam(fromId, dir = 1) {
  const pool = rotationCams();
  if (!pool.length) return null;
  if (pool.length === 1) return pool[0].id;
  const n = CAMS.length;
  const start = CAMS.findIndex((c) => c.id === fromId);
  for (let k = 1; k <= n; k++) {
    const base = start === -1 ? (dir > 0 ? -1 : 0) : start;
    const cam = CAMS[(((base + dir * k) % n) + n) % n];
    if (cam.id !== fromId && pool.includes(cam)) return cam.id;
  }
  return pool[0].id;
}

/** Start switching to `camId`: cover the screen, then load it on the single player. */
function beginLoad(camId, dir = 1, fails = 0, reason) {
  const delay = covered ? 0 : animMs();
  // Telemetry reason for cam_shown; a restart of the same pending cam keeps its reason.
  const why = reason || (state.pending && state.pending.camId === camId ? state.pending.reason : "rotate");
  state.pending = { camId, startedAt: Date.now() + delay, dir, fails, reason: why };
  cover(true);
  loadCam(camId, delay);
  render();
}

function onCamPlaying(p) {
  state.pending = null;
  activeStall = null;
  state.currentCamId = p.camId;
  state.nextSwapAt = Date.now() + rotateSec() * 1000;
  if (state.feedPaused) {
    // Changed cam while paused: show its first frame, then hold it (no autoplay).
    state.pausedRemaining = rotateSec() * 1000;
    hardMute();
    quietly(() => cp.player.pauseVideo());
  }
  setAllFailed(false);
  setQuality();
  cover(false);
  render();
  const reason = p.reason || "rotate";
  const user = !AUTO_REASONS.has(reason);
  if (user || Math.random() < 0.25) capture("cam_shown", { cam_id: p.camId, reason, mode: "local", sampled: user ? 1 : 4 });
}

function markFailed(camId, why) {
  if (!camId) return;
  if (!state.failed.has(camId)) {
    // The YouTube code of this load, if the player reported one.
    if (cp.errSeq && cp.errSeq === cp.seq && cp.camId === camId) capture("player_error", { player: "cam", cam_id: camId, code: cp.errCode, mode: "local" });
    capture("cam_failed", { cam_id: camId, why: why || "error" });
  }
  state.failed.add(camId);
}

function everythingFailed() {
  return CAMS.every((c) => state.failed.has(c.id));
}

/** Pending load failed (error or timeout): mark it, keep the overlay, try the next candidate. */
function failPending(why) {
  const p = state.pending;
  if (!p) return;
  state.pending = null;
  markFailed(p.camId, why);
  const next = pickCam(p.camId, p.dir);
  if (!next) {
    state.currentCamId = null;
    setAllFailed(true);
    render();
    return;
  }
  beginLoad(next, p.dir, p.fails + 1, "skip");
}

/** The cam on screen broke (error or stream ended). */
function failActive(why) {
  markFailed(state.currentCamId, why);
  activeStall = null;
  const next = pickCam(state.currentCamId, 1);
  if (!next || next === state.currentCamId) {
    state.currentCamId = null;
    cover(true);
    setAllFailed(true);
    render();
    return;
  }
  beginLoad(next, 1, 1, "skip");
}

/** Unplayable-video check for a cam that was told to load but is not progressing. */
function looksUnplayable(limitMs) {
  if (!cp.camId || !cp.loadedAt) return false;
  const waited = Date.now() - cp.loadedAt;
  if (waited < Math.min(limitMs, 2500)) return false;
  const live = quietly(() => cp.player.getPlayerState());
  if (live !== YS.UNSTARTED && live !== YS.CUED) return false;
  if (waited < limitMs) {
    // Give a CUED video one nudge in case autoplay just needs a kick.
    if (live === YS.CUED && !cp.nudged) {
      cp.nudged = true;
      camPlay();
    }
    return false;
  }
  return true;
}

// A matching cam that reached PLAYING proves any earlier error marker was stale.
function clearStaleError() {
  if (cp.errSeq && isCurrentLoad()) cp.errSeq = 0;
}

function onCamState(s) {
  if (s === YS.PLAYING || s === YS.BUFFERING) hardMute();
  if (s === YS.PLAYING) clearStaleError();
  const p = state.pending;
  if (p) {
    if (s === YS.PLAYING && shows(p.camId)) onCamPlaying(p);
    else if (s === YS.ENDED && isCurrentLoad()) failPending("ended");
    return;
  }
  if (!state.currentCamId || state.feedPaused) return;
  if (s === YS.PLAYING) {
    activeStall = null;
    setQuality();
  } else if (s === YS.BUFFERING || s === YS.PAUSED) {
    if (!activeStall && watchdogOn()) activeStall = { at: Date.now(), tried: false };
  } else if (s === YS.ENDED && isCurrentLoad()) failActive("ended");
}

// YouTube error codes 2, 5, 100, 101, 150 and 153 all mean the video cannot be played here.
// The error event carries no load generation, so a delayed error from an older cam could hit the one
// that replaced it. Never fail on the event alone: only remember it, then confirm by polling that the
// requested video is the one loaded and is still not playing (see errorConfirmed). Errors that cannot
// be confirmed this way (an invalid id often reports an empty or old video id) fall to the load timeouts.
function onCamError(code) {
  if (!hasCurrentLoad()) return;
  if (cp.errSeq !== cp.seq) {
    cp.errSeq = cp.seq;
    cp.errAt = Date.now();
    cp.errCode = code; // telemetry only (player_error)
  }
}

function errorConfirmed() {
  if (!hasCurrentLoad() || cp.errSeq !== cp.seq) return false;
  if (Date.now() - cp.errAt < ERROR_CONFIRM_MS) return false;
  const live = quietly(() => cp.player.getPlayerState());
  if (live === YS.PLAYING || live === YS.BUFFERING) return false;
  return isCurrentLoad();
}

function tick() {
  if (state.casting || !state.started || !state.apiReady || document.hidden) return;
  const now = Date.now();
  const p = state.pending;
  if (p) {
    if (now < graceUntil || cp.deferred) return; // grace after resume covers pending loads too
    const cold = !state.currentCamId || p.fails > 0;
    const deadline = cold ? COLD_LOAD_TIMEOUT_MS : LOAD_TIMEOUT_MS;
    if (now - p.startedAt > deadline) failPending("load_timeout");
    else if (errorConfirmed()) failPending("error");
    else if (looksUnplayable(cold ? COLD_STUCK_MS : STUCK_MS)) failPending("unplayable");
    return;
  }
  if (!state.currentCamId || state.allFailed) return;
  if (state.feedPaused) return; // paused: no stall watchdog and no rotation (next and previous still work)
  if (hasCurrentLoad() && now >= graceUntil) {
    // Poll the visible cam: any non-PLAYING state (UNSTARTED, CUED, BUFFERING, PAUSED, ENDED) counts as a stall.
    const live = quietly(() => cp.player.getPlayerState());
    if (live === YS.PLAYING) {
      activeStall = null;
      clearStaleError();
    } else if (live != null && !activeStall) activeStall = { at: now, tried: false };
    if (errorConfirmed()) {
      failActive("error");
      return;
    }
  }
  if (activeStall && now - activeStall.at > LOAD_TIMEOUT_MS) {
    if (!activeStall.tried) {
      activeStall = { at: now, tried: true };
      camPlay();
    } else {
      failActive("stalled"); // works while pinned too
    }
    return;
  }
  if (state.pinned || now < state.nextSwapAt) return;
  const next = pickCam(state.currentCamId, 1);
  if (next && next !== state.currentCamId) beginLoad(next, 1, 0, "rotate");
  else state.nextSwapAt = now + rotateSec() * 1000;
}

/** Pause the cam while the tab is hidden (music keeps playing); resume it when visible again. */
function onVisibility() {
  if (state.casting || !state.started || !state.apiReady) return;
  if (document.hidden) {
    activeStall = null;
    quietly(() => cp.player.pauseVideo());
    return;
  }
  const now = Date.now();
  graceUntil = now + GRACE_MS;
  activeStall = null;
  if (!state.feedPaused) state.nextSwapAt = now + rotateSec() * 1000;
  const p = state.pending;
  if (p) {
    if (cp.deferred) {
      cp.deferred(); // the load never started while hidden: issue it now (also resets the pending clock)
      return;
    }
    beginLoad(p.camId, p.dir, p.fails); // it was paused mid-load: start it over
    return;
  }
  if (!state.currentCamId || state.allFailed) return;
  if (state.feedPaused) return; // stays paused; Play resumes at the live edge
  camPlay();
  const seq = cp.seq;
  // Destructive recovery waits for the grace period to end; a cam that is just buffering is left alone
  // (the stall watchdog handles it after grace).
  setTimeout(() => {
    if (document.hidden || cp.seq !== seq || state.pending || !state.currentCamId || state.feedPaused) return;
    if (Date.now() < graceUntil) return;
    const live = quietly(() => cp.player.getPlayerState());
    if (live !== YS.PLAYING && live !== YS.BUFFERING) beginLoad(state.currentCamId, 1, 0, "retry");
  }, GRACE_MS + 250);
}

/* --- cam actions --- */

function skip(dir = 1) {
  if (viaTv(() => remoteCmd(dir < 0 ? "prev" : "next"))) return;
  if (state.casting || !state.started || !state.apiReady) return;
  localCommand(dir < 0 ? "prev" : "next");
  const from = state.pending ? state.pending.camId : state.currentCamId;
  const target = pickCam(from, dir);
  if (!target) return;
  if (state.pending && state.pending.camId === target) return;
  if (!state.pending && target === state.currentCamId) {
    state.nextSwapAt = Date.now() + rotateSec() * 1000;
    return;
  }
  beginLoad(target, dir, 0, dir < 0 ? "prev" : "next");
}

function watchNow(camId) {
  if (!camById(camId)) return;
  state.failed.delete(camId);
  setAllFailed(false);
  if (viaTv(() => remoteCam(camId))) return;
  if (state.casting || !state.started) return;
  localCommand("cam");
  if (state.pending ? state.pending.camId === camId : camId === state.currentCamId) return;
  beginLoad(camId, 1, 0, "pick");
}

function togglePin() {
  if (viaTv(() => remoteCmd("pin"))) return;
  state.pinned = !state.pinned;
  localCommand(state.pinned ? "pin" : "unpin");
  if (!state.pinned) {
    state.nextSwapAt = Date.now() + rotateSec() * 1000;
    state.pausedRemaining = rotateSec() * 1000;
  }
  toast(state.pinned ? "Pinned. Staying on this cam." : "Unpinned. Rotation is back on.");
  render();
}

function toggleInterval() {
  if (viaTv(() => remoteCmd("interval"))) return;
  if (state.lite) return;
  state.intervalSec = state.intervalSec === 60 ? 30 : 60;
  localCommand("interval");
  store.set("interval", state.intervalSec);
  state.nextSwapAt = Date.now() + rotateSec() * 1000;
  if (state.feedPaused) state.pausedRemaining = rotateSec() * 1000; // resume uses the new period
  toast(`New cam every ${state.intervalSec} seconds`);
  render();
}

function toggleLite() {
  state.lite = !state.lite;
  store.set("lite", state.lite);
  body.classList.toggle("lite", state.lite);
  sizeCam();
  setQuality();
  state.nextSwapAt = Date.now() + rotateSec() * 1000;
  if (state.feedPaused) state.pausedRemaining = rotateSec() * 1000; // resume uses the new period
  toast(state.lite ? "Lite mode on" : "Lite mode off");
  render();
}

function setEnabled(camId, on) {
  setManyEnabled([camId], on, "toggle");
}

/** Check or uncheck a group of cams; the checked set persists, and a casting TV gets the new pool. */
function setManyEnabled(ids, on, via = "toggle", shortcut) {
  for (const id of ids) {
    if (!camById(id)) continue;
    state.enabledOverrides[id] = on;
    if (on === !!camById(id).default) delete state.enabledOverrides[id];
  }
  saveOverrides();
  poolChanged(via, shortcut);
}

/** The rotation pool changed (checks or time-of-day rules): redraw and keep a casting TV on the same pool. */
function poolChanged(via, shortcut) {
  render();
  if (isCasting()) sendCamsToTv(tvCamIds(), timeRules());
  if (via) poolTelemetry(via, shortcut);
}

/* command (type cams) once per user action, with the mode at that moment. cam_pool_changed once a change settles
   (2s), flushed on page hide; the last way of changing it wins. Only changes made while telemetry is on are kept,
   and turning it off drops a pending one, so nothing crosses an off/on boundary. */
let poolTimer = 0;
let poolPending = null;
function poolTelemetry(via, shortcut) {
  if (!isTelemetryOn()) return dropPoolTelemetry();
  capture("command", { type: "cams", mode: cmdMode() });
  poolPending = { via, shortcut };
  clearTimeout(poolTimer);
  poolTimer = setTimeout(flushPoolTelemetry, 2000);
}
function dropPoolTelemetry() {
  clearTimeout(poolTimer);
  poolPending = null;
}
onTelemetryChange(() => !isTelemetryOn() && dropPoolTelemetry());
function flushPoolTelemetry(beacon = false) {
  clearTimeout(poolTimer);
  if (!poolPending) return;
  const { via, shortcut } = poolPending;
  poolPending = null;
  if (!isTelemetryOn()) return;
  const props = { enabled_count: checkedCams().length, effective_count: rotationIds().length, total: CAMS.length, via, skip_night: state.skipSleeping };
  if (shortcut) props.shortcut = shortcut;
  capture("cam_pool_changed", props, { beacon });
}

function toggleTimeRule(key) {
  state[key] = !state[key];
  poolChanged("time_rule");
}

/* Quick picks. "all" and "none", or "country:Japan" and "type:street". They change checks, never which rows show. */
function groupCams(group) {
  if (group === "all" || group === "none") return CAMS.slice();
  const [kind, value] = String(group).split(/:(.*)/s);
  if (kind === "country") return CAMS.filter((c) => c.country === value);
  if (kind === "type") return CAMS.filter((c) => c.type === value);
  return [];
}

/** A filled chip: every cam in the group is checked (None: no cam is checked). */
function groupFilled(group) {
  const cams = groupCams(group);
  if (!cams.length) return false;
  if (group === "none") return cams.every((c) => !isEnabled(c.id));
  return cams.every((c) => isEnabled(c.id));
}

function pickGroup(group) {
  const ids = groupCams(group).map((c) => c.id);
  if (group === "all") return setManyEnabled(ids, true, "shortcut", group);
  if (group === "none") return setManyEnabled(ids, false, "shortcut", group);
  // A filled country or type chip unchecks its cams; otherwise it checks them all.
  setManyEnabled(ids, !groupFilled(group), "shortcut", String(group).split(":")[0]);
}

function saveOverrides() {
  if (Object.keys(state.enabledOverrides).length) store.set("enabled", state.enabledOverrides);
  else store.remove("enabled");
}

function setAllFailed(on) {
  if (state.allFailed === on) return;
  state.allFailed = on;
  if (on) capture("cams_all_failed", { mode: "local" });
  if (on) {
    showNotice(
      "The cams are resting",
      "None of the live cams could be reached right now. Music keeps playing. Try again in a little while.",
      "Try again",
      retryCams,
    );
  } else if (el.notice.dataset.kind === "cams") {
    hideNotice();
  }
  if (on) el.notice.dataset.kind = "cams";
}

function retryCams() {
  state.failed.clear();
  setAllFailed(false);
  beginLoad(pickCam(state.currentCamId, 1) || CAMS[0].id, 1, 0, "retry");
}

function startCams() {
  const checked = checkedCams();
  const list = rotationPool(checked.length ? checked : CAMS, timeRules());
  beginLoad(list[Math.floor(Math.random() * list.length)].id, 1, 0, "start");
}

/* ================================================================ music */

let musicPlayer = null;
let musicReady = false;
let musicCheckTimer = 0;
let musicReadyTimer = 0;
let musicStuck = false; // iframe never reported ready; Retry recreates it

function createMusicPlayer() {
  const vars = {
    autoplay: 0,
    controls: 0,
    disablekb: 1,
    fs: 0,
    iv_load_policy: 3,
    modestbranding: 1,
    rel: 0,
    playsinline: 1,
    vq: "tiny",
  };
  const origin = playerOrigin();
  if (origin) vars.origin = origin;
  clearTimeout(musicReadyTimer);
  musicReadyTimer = setTimeout(onMusicReadyTimeout, MUSIC_READY_MS);
  musicPlayer = new window.YT.Player("music-player", {
    host: YT_HOST,
    width: "200", // YouTube requires at least 200x200; CSS keeps it hidden behind the cam layer
    height: "200",
    videoId: state.mixId,
    playerVars: vars,
    events: {
      onReady: () => {
        clearTimeout(musicReadyTimer);
        musicReady = true;
        musicStuck = false;
        const frame = quietly(() => musicPlayer.getIframe());
        if (frame) {
          frame.setAttribute("tabindex", "-1");
          frame.setAttribute("title", "Music player");
        }
        quietly(() => musicPlayer.setPlaybackQuality("tiny"));
        applyVolume();
        if (state.started) startMusic();
      },
      onStateChange: (e) => onMusicState(e.data),
      onError: (e) => onMusicError(e && e.data),
    },
  });
}

function setTapMusicLabel(text) {
  const span = el.tapMusic.querySelector("span");
  if (span) span.textContent = text;
}

function onMusicReadyTimeout() {
  if (musicReady) return;
  musicStuck = true;
  setTapMusicLabel("Music did not load. Retry");
  if (state.started) showTapMusic(true);
}

/** Recreate the music iframe after it never became ready. */
function retryMusicPlayer() {
  const host = document.getElementById("music-wrap");
  quietly(() => musicPlayer.destroy());
  musicPlayer = null;
  musicReady = false;
  musicStuck = false;
  if (host && !document.getElementById("music-player")) {
    const d = document.createElement("div");
    d.id = "music-player";
    host.append(d);
  }
  state.musicWantsPlay = true;
  state.musicErrors = 0;
  state.musicUnavailable = false;
  setTapMusicLabel("Tap to play music");
  showTapMusic(false);
  if (window.YT && window.YT.Player) createMusicPlayer();
  render();
}

/** Explicit retry after every mix errored: reset the error state and load another mix. */
function retryUnavailableMusic() {
  state.musicUnavailable = false;
  state.musicErrors = 0;
  nextMix();
}

function applyVolume() {
  if (!musicReady) return;
  quietly(() => {
    musicPlayer.setVolume(state.volume);
    if (state.muted) musicPlayer.mute();
    else musicPlayer.unMute();
  });
}

function startMusic() {
  if (musicStuck) showTapMusic(true);
  if (!musicReady) return; // onReady will call again
  applyVolume();
  if (state.musicWantsPlay) {
    if (!mixStart) mixStart = { mixId: state.mixId, userChoice: false };
    quietly(() => musicPlayer.playVideo());
  }
  scheduleMusicCheck();
}

let mixStart = null; // telemetry: { mixId, userChoice } until that mix first reaches PLAYING

function scheduleMusicCheck() {
  clearTimeout(musicCheckTimer);
  musicCheckTimer = setTimeout(() => {
    if (!state.musicWantsPlay || state.musicUnavailable) return;
    const s = quietly(() => musicPlayer.getPlayerState());
    if (s !== YS.PLAYING) showTapMusic(true);
  }, MUSIC_CHECK_MS);
}

function showTapMusic(on) {
  const wasHidden = el.tapMusic.hidden;
  el.tapMusic.hidden = !on;
  if (on && wasHidden && !state.openPanel && state.started) {
    el.tapMusic.focus();
  } else if (!on && document.activeElement === el.tapMusic) {
    el.nextBtn.focus();
  }
}

function playMix(mixId, { userChoice = false } = {}) {
  if (!mixById(mixId)) return;
  if (viaTv(() => remoteMix(mixId))) return;
  if (state.feedPaused && !userChoice) {
    // Paused (an error moved to the next mix): cue it, keep everything paused.
    state.mixId = mixId;
    store.set("mix", mixId);
    mixStart = { mixId, userChoice: false };
    if (musicReady) quietly(() => musicPlayer.cueVideoById({ videoId: mixId, startSeconds: 0 }));
    return render();
  }
  if (state.feedPaused) resumeCam(); // choosing a mix plays the whole feed again
  state.mixId = mixId;
  store.set("mix", mixId);
  state.musicWantsPlay = true;
  if (userChoice) localCommand("mix");
  mixStart = { mixId, userChoice };
  if (userChoice) {
    state.musicErrors = 0;
    state.musicUnavailable = false;
  }
  if (state.casting) return render();
  if (musicReady && state.started) {
    applyVolume();
    // Every mix starts at its beginning (a tap, shuffle, or the next one in order), never partway.
    quietly(() => musicPlayer.loadVideoById({ videoId: mixId, startSeconds: 0 }));
    scheduleMusicCheck();
  } else if (musicReady) {
    quietly(() => musicPlayer.cueVideoById({ videoId: mixId, startSeconds: 0 }));
  }
  render();
}

function nextMix() {
  if (viaTv(() => remoteCmd("next-mix"))) return;
  // Shuffle: a random mix, never the same one twice in a row. Off: the next one in sheet order.
  if (state.shuffle) return playMix(randomMixId(state.mixId));
  const i = MIXES.findIndex((m) => m.id === state.mixId);
  playMix(MIXES[(i + 1) % MIXES.length].id);
}

function onMusicState(s) {
  state.musicState = s;
  if (s === YS.PLAYING) {
    if (mixStart && mixStart.mixId === state.mixId) {
      capture("mix_started", { mix_id: state.mixId, shuffle: state.shuffle, user_choice: mixStart.userChoice, mode: "local" });
      mixStart = null;
    }
    quietly(() => musicPlayer.setPlaybackQuality("tiny"));
    state.musicErrors = 0;
    state.musicUnavailable = false;
    showTapMusic(false);
  } else if (s === YS.ENDED) {
    nextMix();
    return;
  }
  render();
}

function onMusicError(code) {
  capture("player_error", { player: "music", mix_id: state.mixId, code, mode: "local" });
  state.musicErrors++;
  if (state.musicErrors >= MIXES.length) {
    if (!state.musicUnavailable) capture("music_unavailable");
    state.musicUnavailable = true;
    showTapMusic(false);
    toast("Music is unavailable right now");
    render();
    return;
  }
  nextMix();
}

/* Pause pauses the whole feed: the cam and the music, here and on the TV. Mute stays music only. */
function pauseCam() {
  state.feedPaused = true;
  state.pausedRemaining = Math.max(0, state.nextSwapAt - Date.now());
  activeStall = null;
  if (cp.player && state.currentCamId && !state.pending) {
    hardMute();
    quietly(() => cp.player.pauseVideo());
  }
}

/* The cam part of resume: back to the live edge, rotation clock picks up where it froze. */
function resumeCam() {
  state.feedPaused = false;
  const now = Date.now();
  state.nextSwapAt = now + state.pausedRemaining;
  activeStall = null;
  graceUntil = now + GRACE_MS; // give the live edge a moment before the stall watchdog looks
  if (cp.player && state.currentCamId && !state.pending && !state.allFailed) camToLiveEdge();
}

function pauseMusic() {
  state.musicWantsPlay = false;
  clearTimeout(musicCheckTimer);
  if (musicReady) quietly(() => musicPlayer.pauseVideo());
  showTapMusic(false);
}

function resumeMusic() {
  state.musicWantsPlay = true;
  if (musicStuck) return retryMusicPlayer();
  if (!musicReady) return; // onReady starts it
  if (state.musicUnavailable) return retryUnavailableMusic();
  applyVolume();
  quietly(() => musicPlayer.playVideo()); // a paused mix continues where it stopped
  scheduleMusicCheck();
}

function setFeedPaused(paused) {
  if (viaTv(() => remoteCmd(paused ? "play-off" : "play-on"))) return;
  if (paused === state.feedPaused) return;
  localCommand(paused ? "pause" : "play");
  if (paused) {
    pauseCam();
    pauseMusic();
  } else {
    resumeCam();
    resumeMusic();
  }
  render();
  if (localPlayerOn()) showChrome(); // paused: chrome stays up; resumed: the 6 second hold starts again
}

function togglePause() {
  if (viaTv(() => remoteCmd("play"))) return;
  setFeedPaused(!state.feedPaused);
}

function setVolume(v, announce = true) {
  if (viaTv(() => remoteVolume(v))) return;
  state.volume = Math.min(100, Math.max(0, Math.round(v)));
  if (state.volume > 0 && state.muted) state.muted = false;
  store.set("volume", state.volume);
  store.set("muted", state.muted);
  applyVolume();
  if (announce) toast(`Music volume ${state.volume}`);
  render();
}

function toggleMute() {
  if (viaTv(() => remoteCmd("mute"))) return;
  state.muted = !state.muted;
  localCommand(state.muted ? "mute" : "unmute");
  store.set("muted", state.muted);
  applyVolume();
  toast(state.muted ? "Music muted" : `Music on, volume ${state.volume}`);
  render();
}

/* Shuffle is this session only. While casting the TV picks the next mix itself, so the switch flips the TV's
   reported setting (another sender may have changed it) and the phone follows. */
function toggleShuffle() {
  state.shuffle = !(isCasting() ? remoteState().shuffle : state.shuffle);
  capture("command", { type: "shuffle", mode: cmdMode() });
  if (isCasting()) sendShuffleToTv(state.shuffle);
  toast(state.shuffle ? "Shuffle on. A random mix plays next." : "Shuffle off. Mixes play in list order.");
  render();
}

/** A new local start (Home, or back from casting) with shuffle on gets a fresh random mix, from the beginning. */
function reshuffleForStart() {
  if (!state.shuffle) return;
  state.mixId = randomMixId(state.mixId);
  store.set("mix", state.mixId);
}

function tapToPlay() {
  if (viaTv()) return;
  if (state.feedPaused) return setFeedPaused(false);
  if (musicStuck) return retryMusicPlayer();
  state.musicWantsPlay = true;
  if (!musicReady) return;
  if (state.musicUnavailable) return retryUnavailableMusic();
  applyVolume();
  quietly(() => musicPlayer.playVideo());
  scheduleMusicCheck();
}

/* ================================================================ render */

function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

function setBarLabel(action, text) {
  const btn = $(`.bar-btn[data-action="${action}"]`, el.bar);
  if (btn) {
    setText($(".label", btn), text);
    if (btn.getAttribute("aria-label") !== text) btn.setAttribute("aria-label", text);
  }
  return btn;
}

/** Height of the bottom glass, so the debug panel can stop above it. */
function measureBar() {
  const h = el.bar.offsetHeight;
  if (h) document.documentElement.style.setProperty("--glass-h", h + "px");
}

const mmss = (ms) => {
  const t = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
};

/** "{city}, {country} · 9:51 AM", or without the time when the cam has no valid timezone. */
function camMeta(cam) {
  const time = camLocalTime(cam);
  const place = placeLine(cam);
  return time ? `${place} · ${time}` : place;
}

/* Rotation line under the cam name: "Next cam in M:SS" with a hairline, "Pinned. Rotation off.", or "Paused" (frozen hairline). */
function renderStatus() {
  if (!state.started || state.casting) return;
  const period = rotateSec() * 1000;
  let text;
  let left = period;
  if (state.pinned) {
    text = state.feedPaused ? "Paused. Pinned, rotation off." : "Pinned. Rotation off.";
  } else if (state.feedPaused) {
    text = "Paused";
    left = state.pausedRemaining;
  } else if (state.pending || !state.currentCamId) {
    text = state.allFailed ? "The cams are resting" : "Tuning in...";
  } else {
    left = Math.max(0, state.nextSwapAt - Date.now());
    text = `Next cam in ${mmss(left)}`;
  }
  setText(el.statusText, text);
  const frac = state.pinned ? 0 : Math.min(1, Math.max(0, 1 - left / period));
  el.hairline.style.transform = `scaleX(${frac.toFixed(3)})`;
  el.bar.classList.toggle("is-pinned", state.pinned);
  el.bar.classList.toggle("is-paused", state.feedPaused);
  // The cam's local time moves on while it plays.
  const cam = camById(state.currentCamId);
  if (cam && !el.caption.hidden) setText(el.capMeta, camMeta(cam));
}

function render() {
  // Pin pill and rotation
  setText(el.pinPill, state.pinned ? "Pinned" : "Pin cam");
  el.pinPill.setAttribute("aria-pressed", String(state.pinned));
  const secs = rotateSec();
  for (const b of $$('[data-action="interval"]')) b.disabled = state.lite;
  setText($("#cams-interval"), `New cam every ${secs}s`);
  for (const b of $$('[data-action="lite"]')) b.setAttribute("aria-pressed", String(state.lite));

  // Pause / Play is the whole feed (cam and music).
  const playing = !state.feedPaused;
  const ppBtn = setBarLabel("play-pause", playing ? "Pause" : "Play");
  $("use", ppBtn).setAttribute("href", playing ? "#i-pause" : "#i-play");

  const fs = isFullscreen();
  el.fullscreenBtn.setAttribute("aria-label", fs ? "Exit full screen" : "Full screen");
  $("use", el.fullscreenBtn).setAttribute("href", fs ? "#i-fullscreen-exit" : "#i-fullscreen");

  // Now playing, in the bottom glass
  const cam = camById(state.currentCamId);
  if (cam && state.started) {
    el.caption.hidden = false;
    setText(el.capTitle, cam.title);
    setText(el.capMeta, camMeta(cam));
  } else {
    el.caption.hidden = true;
  }
  renderStatus();

  // "Tuning in" indicator while no cam is on screen
  el.tuning.hidden = !(state.started && !state.allFailed && covered && (!state.currentCamId || (state.pending && state.pending.fails > 0)));

  renderCamsPanel();

  renderMusicPanel();
  syncChrome();
}

/** The mix, volume, pause and shuffle to show: the TV's reported state while casting (this phone plays nothing then), else local. */
function shownMusic() {
  if (isCasting()) {
    const r = remoteState();
    return { mixId: r.mixId || null, volume: Number.isFinite(r.volume) ? r.volume : state.volume, muted: !!r.muted, paused: !!r.paused, shuffle: r.shuffle };
  }
  return { mixId: state.mixId, volume: state.volume, muted: state.muted, paused: state.feedPaused, shuffle: state.shuffle };
}

function showVolume(input, out, value) {
  if (!input) return;
  if (document.activeElement !== input) input.value = String(value);
  setText(out, String(value));
  input.style.setProperty("--vol", `${input.value}%`);
}

/* Mix sheet: Shuffle, the music volume, and every mix with one Playing row (the TV's mix while casting). */
function renderMusicPanel() {
  const shown = shownMusic();
  $("#music-mute").setAttribute("aria-pressed", String(shown.muted));
  $("#music-shuffle").setAttribute("aria-pressed", String(shown.shuffle));
  setText($("#music-play"), shown.paused ? "Play" : "Pause");
  showVolume(el.volume, el.volumeOut, state.volume);
  showVolume(el.mixVolume, el.mixVolumeOut, shown.volume);
  for (const btn of $$(".mix-btn", el.mixList)) {
    const current = btn.dataset.id === shown.mixId;
    btn.classList.toggle("is-current", current);
    if (current) btn.setAttribute("aria-current", "true");
    else btn.removeAttribute("aria-current");
    const mark = $(".mix-playing", btn);
    if (mark) mark.hidden = !current;
  }
}

/** The cam on screen right now: the TV's current cam while casting (local playback is stopped then), else the local one. */
function showingCamId() {
  return isCasting() ? castDebugInfo().now.camId || null : state.currentCamId;
}

/* Cams sheet: count, time of day, quick picks, and the full list in list order. Local times move on, so the
   rows are refreshed on every render and every 30 seconds while the sheet is open. */
function renderCamsPanel(now = new Date()) {
  const showing = showingCamId();
  const checked = checkedCams();
  // The real pool, including the fallback to the checked cams when every one of them is asleep.
  const pool = new Set(rotationIds(now));
  const inRotation = pool.size;
  const sleeping = checked.filter((c) => isSleeping(c, now)).length;
  let count;
  if (!checked.length) count = "No cams checked. Rotation uses every cam.";
  else {
    count = `${inRotation} in rotation now.`;
    if (sleeping) count += sleeping === 1 ? " 1 checked cam is sleeping." : ` ${sleeping} checked cams are sleeping.`;
  }
  setText($("#cams-count"), count);
  $("#cams-skip-sleeping").setAttribute("aria-pressed", String(state.skipSleeping));
  $("#cams-only-daytime").setAttribute("aria-pressed", String(state.onlyDaytime));
  for (const c of $$('[data-action="pick-group"]', $("#cams-quick"))) c.setAttribute("aria-pressed", String(groupFilled(c.dataset.group)));

  for (const row of $$(".cam-row", el.camsList)) {
    const id = row.dataset.id;
    const cam = camById(id);
    const on = isEnabled(id);
    const failed = state.failed.has(id);
    const current = id === showing;
    const asleep = isSleeping(cam, now);
    row.classList.toggle("is-current", current);
    row.classList.toggle("is-failed", failed);
    row.classList.toggle("is-sleeping", asleep);
    row.classList.toggle("is-off", !on);
    row.dataset.inPool = String(pool.has(id));
    $('[data-action="toggle-cam"]', row).setAttribute("aria-checked", String(on));
    setText($(".cam-clock", row), camLocalTime(cam, now));
    $(".sleep-mark", row).hidden = !asleep;
    const badge = $(".badge", row);
    badge.hidden = !(current || failed);
    badge.className = "badge " + (failed ? "badge-failed" : "badge-now");
    badge.textContent = failed ? "Unavailable right now" : "Now showing";
  }
}

const capital = (t) => (t ? t[0].toUpperCase() + t.slice(1) : "");

/** The most common values of a field (two or more cams), in list order on ties: the country and type shortcuts. */
function topValues(field, max) {
  const counts = new Map();
  for (const cam of CAMS) if (cam[field]) counts.set(cam[field], (counts.get(cam[field]) || 0) + 1);
  return Array.from(counts.entries())
    .filter(([, n]) => n > 1)
    .sort((a, b) => b[1] - a[1])
    .slice(0, max)
    .map(([v]) => v);
}

function chip(group, label) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "chip";
  b.dataset.action = "pick-group";
  b.dataset.group = group;
  b.setAttribute("aria-pressed", "false");
  b.textContent = label;
  return b;
}

function buildCamsPanel() {
  const quick = $("#cams-quick");
  quick.textContent = "";
  quick.append(chip("all", "All"), chip("none", "None"));
  for (const country of topValues("country", 4)) quick.append(chip(`country:${country}`, country));
  for (const type of topValues("type", 3)) quick.append(chip(`type:${type}`, capital(type)));

  const frag = document.createDocumentFragment();
  for (const cam of CAMS) {
    const li = document.createElement("li");
    li.className = "cam-row";
    li.dataset.id = cam.id;

    const check = document.createElement("button");
    check.type = "button";
    check.className = "cam-check";
    check.setAttribute("role", "checkbox");
    check.setAttribute("aria-checked", "false");
    check.dataset.action = "toggle-cam";
    check.dataset.id = cam.id;
    const box = document.createElement("span");
    box.className = "check-box";
    box.setAttribute("aria-hidden", "true");

    const info = document.createElement("span");
    info.className = "cam-info";
    const title = document.createElement("span");
    title.className = "cam-title";
    title.textContent = cam.title;
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.hidden = true;
    title.append(badge);
    const sub = document.createElement("span");
    sub.className = "cam-sub";
    const where = document.createElement("span");
    where.className = "cam-where";
    where.textContent = [cam.country, capital(cam.type)].filter(Boolean).join(" · ");
    const clock = document.createElement("span");
    clock.className = "cam-clock";
    const sleep = document.createElement("span");
    sleep.className = "sleep-mark";
    sleep.textContent = "Sleeping";
    sleep.hidden = true;
    sub.append(where, clock, sleep);
    info.append(title, sub);
    check.append(box, info);

    const play = document.createElement("button");
    play.type = "button";
    play.className = "btn btn-play";
    play.dataset.action = "watch";
    play.dataset.id = cam.id;
    play.textContent = "Play now";
    play.setAttribute("aria-label", `Play ${cam.title} now`);

    li.append(check, play);
    frag.append(li);
  }
  el.camsList.append(frag);
}

function buildMusicPanel() {
  for (const mix of MIXES) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mix-btn";
    btn.dataset.action = "play-mix";
    btn.dataset.id = mix.id;
    const en = document.createElement("span");
    en.className = "mix-en";
    en.textContent = mix.en;
    const ja = document.createElement("span");
    ja.className = "mix-ja";
    ja.lang = "ja";
    ja.textContent = mix.title;
    const side = document.createElement("span");
    side.className = "mix-side";
    const mark = document.createElement("span");
    mark.className = "mix-playing";
    mark.textContent = "Playing";
    mark.hidden = true;
    const len = document.createElement("span");
    len.className = "mix-len";
    len.textContent = formatLength(mix.minutes);
    side.append(mark, len);
    btn.append(en, ja, side);
    li.append(btn);
    el.mixList.append(li);
  }
  const credit = $("#music-credit");
  credit.href = MUSIC_CHANNEL.url;
  credit.textContent = MUSIC_CHANNEL.name;
}

/* Rebuild every list-driven part of the page after the cam or mix list changed (sheet config). */
function rebuildLists() {
  el.camsList.textContent = "";
  el.mixList.textContent = "";
  $("#credits-cams").textContent = "";
  $("#credits-music").textContent = "";
  buildCamsPanel();
  buildMusicPanel();
  buildCredits();
  // The playing mix left the list: switch the player too, keeping play or pause as it was.
  if (!mixById(state.mixId)) {
    if (state.musicWantsPlay) playMix(MIXES[0].id);
    else {
      state.mixId = MIXES[0].id;
      store.set("mix", state.mixId);
      if (musicReady) quietly(() => musicPlayer.cueVideoById(state.mixId));
    }
  }
  // The showing or loading cam left the list (even if pinned): move to one that exists.
  const gone = state.pending ? !camById(state.pending.camId) : state.currentCamId && !camById(state.currentCamId);
  if (gone && state.started && state.apiReady && !state.casting) {
    const t = pickCam(null, 1);
    if (t) beginLoad(t, 1);
  }
  render();
  renderHero(); // the sheet can bring timezones and a different list after first paint
}

function showListStatus(error) {
  document.querySelectorAll(".list-status").forEach((n) => {
    n.textContent = listLabel();
    n.title = error ? `Sheet not loaded: ${error}` : "";
  });
}

const LIST_ERRORS = [
  [/too long|timeout/i, "timeout"],
  [/HTTP|private/i, "http"],
  [/not CSV/i, "parse"],
  [/no usable/i, "unusable"],
  [/could not read|network|fetch/i, "network"],
];
/** A fixed code for list_load (the error text itself is never sent). */
function listError(text) {
  for (const [re, code] of LIST_ERRORS) if (re.test(String(text || ""))) return code;
  return "parse";
}

function loadSheetConfig() {
  showListStatus();
  const startedAt = Date.now();
  refreshFromSheet().then((r) => {
    if (r.changed) rebuildLists();
    showListStatus(r.ok ? "" : r.error);
    if (r.ok) sendConfigToTv(); // keep a casting TV on the same list
    const props = { ok: !!r.ok, source: listInfo.source, ms: Date.now() - startedAt, cams: listInfo.cams, mixes: listInfo.mixes };
    if (!r.ok) props.error = listError(r.error);
    capture("list_load", props);
    // A list that fails to parse with an exception is a bug (Sentry); a sheet that is down or private is not.
    if (!r.ok && props.error === "parse" && !/not CSV/i.test(String(r.error))) captureError(new Error("List parse failed"), { kind: "list_parse" });
  });
}

/* ================================================================ home: Right now */

// Where the window looks, by cam type, for the hero line ("Morning on the harbor.").
const SETTING = {
  harbor: "on the harbor",
  river: "on the river",
  street: "on the street",
  crossing: "at the crossing",
  skyline: "over the skyline",
  mountain: "on the mountain",
  airport: "at the airport",
  train: "by the tracks",
  temple: "at the temple",
  nature: "in the trees",
  landscape: "over the hills",
  beach: "on the beach",
};

let heroCamId = null;

/** A current in-rotation cam for the hero: awake and with a local time when possible. Stays put while it qualifies. */
function pickHeroCam(now) {
  const awake = awakeCams(CAMS, now);
  const timed = awake.filter((c) => localTime(c.timezone, now));
  const pool = timed.length ? timed : awake.length ? awake : CAMS.filter((c) => c.default);
  if (!pool.length) return null;
  const keep = pool.find((c) => c.id === heroCamId);
  if (keep) return keep;
  return pool[Math.floor(now.getTime() / 3600000) % pool.length];
}

function renderHero() {
  const now = new Date();
  const cam = pickHeroCam(now);
  const h = el.hero;
  if (!cam) {
    setText(h.time, "");
    setText(h.line, "");
    setText(h.cam, "");
    h.img.hidden = true;
    return;
  }
  const awake = awakeCams(CAMS, now).length;
  const count = awake === 1 ? "1 window is awake." : awake ? `${awake} windows are awake.` : "All the windows are asleep.";
  const city = cam.city || cam.place || cam.country || "";
  const t = localTime(cam.timezone, now);
  if (t) {
    setText(h.time, `It's ${t.text} in ${city}.`);
    setText(h.line, `${partOfDay(t.hour)} ${SETTING[cam.type] || `in ${city}`}. ${count}`);
  } else {
    setText(h.time, `Live from ${city}.`);
    setText(h.line, count);
  }
  setText(h.cam, `${cam.title} · ${placeLine(cam)}`);
  h.img.alt = `Live cam: ${cam.title}`;
  if (heroCamId !== cam.id) {
    heroCamId = cam.id;
    h.img.dataset.id = cam.id;
    h.img.hidden = true; // shown once it loads; a failed still leaves the navy panel
    h.img.src = `https://i.ytimg.com/vi/${encodeURIComponent(cam.id)}/hqdefault.jpg`;
  }
}

function buildCredits() {
  const byChannel = new Map();
  for (const cam of CAMS) {
    if (!byChannel.has(cam.channelUrl)) byChannel.set(cam.channelUrl, { name: cam.channel, titles: [] });
    byChannel.get(cam.channelUrl).titles.push(cam.title);
  }
  const camsUl = $("#credits-cams");
  for (const [url, info] of byChannel) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = info.name;
    const span = document.createElement("span");
    span.textContent = `: ${info.titles.join(", ")}`;
    li.append(a, span);
    camsUl.append(li);
  }
  const musicUl = $("#credits-music");
  const li = document.createElement("li");
  const a = document.createElement("a");
  a.href = MUSIC_CHANNEL.url;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = MUSIC_CHANNEL.name;
  const span = document.createElement("span");
  span.textContent = `: all ${MIXES.length} mixes`;
  li.append(a, span);
  musicUl.append(li);
}

/* ================================================================ notice */

let noticeAction = null;
function showNotice(title, text, buttonText, action) {
  el.noticeTitle.textContent = title;
  el.noticeText.textContent = text;
  el.noticeBtn.textContent = buttonText;
  noticeAction = action;
  el.notice.hidden = false;
  if (!state.openPanel && state.started) {
    el.noticeBtn.focus();
  }
}
function hideNotice() {
  const hadFocus = el.notice.contains(document.activeElement);
  el.notice.hidden = true;
  delete el.notice.dataset.kind;
  noticeAction = null;
  if (hadFocus) el.nextBtn.focus();
}

/* ================================================================ panels */

let panelOpener = null;

/** Background controls cannot take focus while a dialog is open. */
function setBackgroundInert(on) {
  [el.bar, el.top, el.chromeToggle, el.notice, el.tapMusic, el.start].forEach((n) => n && (n.inert = on));
}

function openPanel(name, opener) {
  if (state.openPanel === name) {
    closePanel();
    return;
  }
  if (state.openPanel) closePanel(false);
  const panel = el.panels[name];
  if (!panel) return;
  panelOpener = opener || null;
  state.openPanel = name;
  panel.hidden = false;
  capture("panel_opened", { panel: name, mode: cmdMode() });
  body.classList.add("panel-open");
  setBackgroundInert(true);
  showChrome(); // a sheet keeps the chrome up
  render();
  let target = null;
  if (name === "cams") target = $(`.cam-row[data-id="${showingCamId()}"] [data-action="watch"]`, panel);
  if (name === "music") target = $(`.mix-btn[data-id="${shownMusic().mixId}"]`, panel);
  if (name === "tv") renderTvQr();
  if (!target) target = $(FOCUSABLE, panel);
  if (target) {
    target.focus();
    quietly(() => target.scrollIntoView({ block: "center" }));
  }
}

let qrDone = false;

/** Draw the QR code for TV_URL into the panel canvas, with a white quiet zone of 4 modules. */
function renderTvQr() {
  $("#tv-url").textContent = TV_URL;
  if (qrDone) return;
  const qr = qrcode(0, "M");
  qr.addData(TV_URL);
  qr.make();
  const n = qr.getModuleCount();
  const cell = 8;
  const quiet = 4;
  const size = (n + quiet * 2) * cell;
  const canvas = $("#tv-qr");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, size, size);
  ctx.fillStyle = "#000";
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) ctx.fillRect((c + quiet) * cell, (r + quiet) * cell, cell, cell);
  qrDone = true;
}

function closePanel(restoreFocus = true) {
  const name = state.openPanel;
  if (!name) return;
  el.panels[name].hidden = true;
  state.openPanel = null;
  body.classList.remove("panel-open");
  setBackgroundInert(false);
  holdChrome(); // the 6 second hold starts again once the sheet is gone
  if (restoreFocus) {
    const target =
      panelOpener && isVisible(panelOpener)
        ? panelOpener
        : state.started
          ? $(`[data-action="open-${name}"]`, el.bar)
          : el.startBtn;
    target?.focus();
  }
  panelOpener = null;
}

/* ================================================================ fullscreen */

function isFullscreen() {
  return !!(document.fullscreenElement || document.webkitFullscreenElement);
}

/* Best effort: the player is already full bleed in the layout, so a denied request changes nothing else. */
function requestFullscreen(onFail) {
  const docEl = document.documentElement;
  const req = docEl.requestFullscreen || docEl.webkitRequestFullscreen;
  if (!req) {
    if (onFail) onFail();
    return;
  }
  let p;
  try {
    p = req.call(docEl);
  } catch {
    if (onFail) onFail();
    return;
  }
  Promise.resolve(p).catch(() => onFail && onFail());
}

function exitFullscreen() {
  if (!isFullscreen()) return;
  const exit = document.exitFullscreen || document.webkitExitFullscreen;
  if (exit) Promise.resolve(quietly(() => exit.call(document))).catch(() => {});
}

function toggleFullscreen() {
  if (isFullscreen()) exitFullscreen();
  else requestFullscreen(() => toast("Full screen is not available here"));
}

/* ================================================================ start */

let begun = false;
let startHideTimer = null; // pending hide of #start after its leave animation; cancelled when Home returns

function start() {
  if (viaTv() || state.started || state.casting) return;
  if (state.apiFailed) {
    location.reload();
    return;
  }
  state.started = true;
  // The local player hides system bars where the browser allows it (TV browsers and touch phones). Denied is fine.
  if ((isTv || prefersFullscreen()) && !isFullscreen()) requestFullscreen();
  body.classList.remove("is-starting");
  el.start.classList.add("leaving");
  clearTimeout(startHideTimer);
  startHideTimer = setTimeout(() => {
    startHideTimer = null;
    if (state.started) el.start.hidden = true;
  }, reducedMotion.matches ? 0 : 650);
  if (state.openPanel) closePanel(false);
  state.chromeHidden = false;
  syncChrome();
  holdChrome();
  el.nextBtn.focus();
  measureBar();
  if (state.apiReady) begin();
  else el.startStatus.textContent = "Connecting to YouTube...";
}

let localStartedAt = 0; // telemetry: local playback began (begin), until it ends

function begin() {
  if (begun || viaTv()) return;
  begun = true;
  localStartedAt = Date.now();
  capture("local_playback_started", { mix_id: state.mixId, cam_count: rotationIds().length });
  startCams();
  startMusic();
  render();
}

function endLocalPlayback(reason, beacon = false) {
  if (!localStartedAt) return;
  capture("local_playback_ended", { seconds: Math.round((Date.now() - localStartedAt) / 1000), reason }, { beacon });
  localStartedAt = 0;
}

/* Home from the local player: stop local playback (players destroyed and recreated idle, as after casting)
   and show the Start screen. Nothing plays until Start is pressed again. */
function goHome() {
  if (!state.started || state.casting) return;
  endLocalPlayback("home");
  if (state.openPanel) closePanel(false);
  cp.seq++; // drop queued cam loads
  cp.waiters.length = 0;
  cp.deferred = null;
  destroyLocalPlayers();
  state.started = false;
  begun = false;
  state.pending = null;
  state.currentCamId = null;
  state.feedPaused = false;
  state.pinned = false;
  state.musicWantsPlay = true;
  activeStall = null;
  setAllFailed(false);
  hideNotice();
  showTapMusic(false);
  cover(true);
  exitFullscreen();
  reshuffleForStart();
  if (state.apiReady) {
    createCamPlayer();
    createMusicPlayer();
  }
  showStartScreen();
}

function showStartScreen() {
  clearTimeout(chromeTimer);
  clearTimeout(startHideTimer); // Home pressed during the leave animation must not hide Start later
  startHideTimer = null;
  state.chromeHidden = false;
  body.classList.add("is-starting");
  el.start.hidden = false;
  el.start.classList.remove("leaving");
  el.startStatus.textContent = "";
  renderHero();
  render();
  el.startBtn.focus();
}

/* ================================================================ cast */

// While casting, this page is only a remote: both local YouTube players are destroyed
// (no audio, no video, no network on the phone) and recreated only when the session ends.
function destroyLocalPlayers() {
  quietly(() => cp.player.stopVideo());
  quietly(() => cp.player.destroy());
  cp.player = null;
  cp.ready = false;
  if (!cp.el.querySelector(".cam-player")) {
    cp.el.textContent = "";
    const d = document.createElement("div");
    d.className = "cam-player";
    cp.el.append(d);
  }
  clearTimeout(musicReadyTimer);
  clearTimeout(musicCheckTimer);
  quietly(() => musicPlayer.stopVideo());
  quietly(() => musicPlayer.destroy());
  musicPlayer = null;
  musicReady = false;
  musicStuck = false;
  const host = document.getElementById("music-wrap");
  if (host && !document.getElementById("music-player")) {
    host.textContent = "";
    const d = document.createElement("div");
    d.id = "music-player";
    host.append(d);
  }
}

function pauseLocalForCast() {
  if (state.casting) return;
  endLocalPlayback("cast");
  state.casting = true;
  state.pending = null;
  state.feedPaused = false;
  cp.seq++; // invalidate queued camera loads (delayed go() and waiters)
  cp.waiters.length = 0;
  cp.deferred = null;
  state.musicWantsPlay = false;
  destroyLocalPlayers();
  showTapMusic(false);
  if (state.openPanel) closePanel(false);
  clearTimeout(chromeTimer);
  syncChrome();
}

// Back to the normal page at the Start screen; nothing plays until the user presses Start.
function resumeAfterCast() {
  state.casting = false;
  state.started = false;
  state.feedPaused = false;
  begun = false;
  state.currentCamId = null;
  state.pending = null;
  state.musicWantsPlay = true;
  reshuffleForStart();
  // Fresh players; nothing plays until the user presses Start.
  if (state.apiReady && !cp.player) createCamPlayer();
  if (state.apiReady && !musicPlayer) createMusicPlayer();
  showStartScreen();
}

function setupCast() {
  const note = $("#about-cast");
  if (!castConfigured) {
    if (note) note.textContent = "Cast to TV: coming soon.";
    return;
  }
  if (note) note.textContent = "Tap the Cast button in Chrome and pick your TV. This page then becomes a remote.";
  initCast({
    getLocal: () => ({ camId: state.currentCamId, mixId: state.mixId, interval: state.intervalSec, volume: state.volume, muted: state.muted, shuffle: state.shuffle, camIds: tvCamIds(), ...timeRules() }),
    onStart: pauseLocalForCast,
    onEnd: resumeAfterCast,
    onRemoteChange: () => {
      if (state.openPanel === "cams") renderCamsPanel();
      if (state.openPanel === "music") renderMusicPanel(); // the Playing row follows the TV
    },
    toast: (text) => toast(text, 6000),
  });
}

/* ================================================================ debug mode */

// Opt-in diagnostics (About, This copy). Off on every page load; see js/debug.js.
/* ================================================================ telemetry switch */

// About, This copy: "Share anonymous usage and errors". Do Not Track or Global Privacy Control: off and disabled.
function setupTelemetrySwitch() {
  const toggle = document.getElementById("telemetry-toggle");
  const note = document.getElementById("telemetry-dnt");
  if (!toggle) return;
  const forced = telemetryForcedOff();
  toggle.checked = !forced && isTelemetryOn();
  toggle.disabled = forced;
  if (note) note.hidden = !forced;
  toggle.addEventListener("change", () => {
    const on = setTelemetryEnabled(toggle.checked);
    toggle.checked = on;
  });
  // Another open copy of the app can flip the setting: the switch here follows it.
  onTelemetryChange(() => {
    toggle.checked = !forced && isTelemetryOn();
  });
}

/** app_open once per page load, once the Cast SDK has answered (or after 5 seconds). */
function reportAppOpen() {
  const props = () => ({ installed: quietly(() => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true) === true, tv_mode: isTv });
  let sent = false;
  const send = () => {
    if (sent) return;
    const avail = castAvailable();
    sent = true;
    capture("app_open", { ...props(), cast_available: avail === true });
  };
  const started = Date.now();
  const wait = setInterval(() => {
    if (castAvailable() !== null || Date.now() - started > 5000) {
      clearInterval(wait);
      send();
    }
  }, 250);
}

function setupDebug() {
  initDebug({
    now: () => ({ camId: state.currentCamId || (state.pending && state.pending.camId) || null, mixId: state.mixId }),
    cast: castDebugInfo,
    toast: (text) => toast(text),
    onChange: (on) => {
      const connected = castDebugInfo().connected;
      sendDebugToTv();
      toast(on ? (connected ? "Debug on. Showing details on the TV too." : "Debug on.") : "Debug off.");
    },
  });
}

/* ================================================================ api */

let apiTimer = 0;

function onApiReady() {
  if (state.apiReady) return;
  clearTimeout(apiTimer);
  state.apiReady = true;
  if (state.apiFailed) {
    state.apiFailed = false;
    hideNotice();
    el.startStatus.textContent = "";
    el.startBtn.querySelector("span").textContent = "Start watching";
  }
  if (viaTv() || state.casting) return; // casting: no local players until the session ends
  createCamPlayer();
  createMusicPlayer();
  if (state.started) begin();
}

function onApiFailed() {
  if (state.apiReady || state.apiFailed) return;
  state.apiFailed = true;
  el.startStatus.textContent = "Could not reach YouTube. Check your connection, then reload.";
  el.startBtn.querySelector("span").textContent = "Reload";
  if (state.started) {
    showNotice("Could not reach YouTube", "The video player did not load. Check your connection, then reload the page.", "Reload", () =>
      location.reload(),
    );
  }
}

function loadYouTubeApi() {
  if (window.YT && window.YT.Player) {
    onApiReady();
    return;
  }
  const previous = window.onYouTubeIframeAPIReady;
  window.onYouTubeIframeAPIReady = () => {
    if (typeof previous === "function") quietly(previous);
    onApiReady();
  };
  const script = document.createElement("script");
  script.src = YT_API_URL;
  script.async = true;
  script.onerror = onApiFailed;
  document.head.append(script);
  apiTimer = setTimeout(onApiFailed, API_TIMEOUT_MS);
}

/* ================================================================ ui visibility */

// The full-screen local player: a top scrim (Home, Cast, full screen) and a bottom glass (now playing, transport,
// Music volume, Cams and Mix) over the picture. The chrome fades 6 seconds after the last tap, except while the
// feed is paused, a sheet is open, or a notice is up. A tap on the picture shows it again; the floating button
// at the bottom right (always on screen) shows or hides it. Casting never shows this player (the phone is a remote).
let chromeTimer = 0;

function localPlayerOn() {
  return state.started && !state.casting;
}

function prefersFullscreen() {
  return !!(window.matchMedia && (window.matchMedia("(pointer: coarse)").matches || window.matchMedia("(hover: none)").matches));
}

function chromeLocked() {
  return state.feedPaused || !!state.openPanel || !el.notice.hidden;
}

function syncChrome() {
  const on = localPlayerOn();
  body.classList.toggle("lp-on", on);
  body.classList.toggle("chrome-hidden", on && state.chromeHidden);
  el.chromeToggle.setAttribute("aria-expanded", String(!state.chromeHidden));
  const label = state.chromeHidden ? "Show controls" : "Hide controls";
  if (el.chromeToggle.getAttribute("aria-label") !== label) el.chromeToggle.setAttribute("aria-label", label);
  const theme = on ? THEME_NIGHT : THEME_CANVAS;
  if (el.themeColor && el.themeColor.content !== theme) el.themeColor.content = theme;
}

/** (Re)start the 6 second hold. The timer re-checks the locks, so a pause or an open sheet keeps the chrome up. */
function holdChrome() {
  clearTimeout(chromeTimer);
  if (!localPlayerOn() || state.chromeHidden) return;
  chromeTimer = setTimeout(autoHideChrome, CHROME_HOLD_MS);
}

function autoHideChrome() {
  if (!localPlayerOn() || state.chromeHidden) return;
  if (chromeLocked()) {
    holdChrome();
    return;
  }
  setChrome(false);
}

function chromeHasFocus() {
  const a = document.activeElement;
  return !!a && (el.top.contains(a) || el.bar.contains(a));
}

function setChrome(visible, { focus = false } = {}) {
  const hide = !visible;
  if (hide && chromeHasFocus()) el.chromeToggle.focus(); // never leave focus on a faded control
  state.chromeHidden = hide;
  syncChrome();
  if (hide) {
    clearTimeout(chromeTimer);
    if (focus) el.chromeToggle.focus();
    return;
  }
  measureBar();
  holdChrome();
  if (focus) el.nextBtn.focus();
}

function showChrome() {
  if (state.chromeHidden) setChrome(true);
  else holdChrome();
}

/* A tap on the picture brings the chrome back; any tap while it is up restarts the hold. The floating button
   handles its own click (show or hide), so it is ignored here. */
function onPlayerPointer(e) {
  if (!localPlayerOn()) return;
  const t = e.target;
  if (t && el.chromeToggle.contains(t)) return;
  if (!state.chromeHidden) {
    holdChrome();
    return;
  }
  if (t && t.closest && t.closest("#debug-panel, .panel, #notice, #tap-music")) return;
  setChrome(true);
}

/* ================================================================ focus / d-pad */

const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])';

function isVisible(node) {
  if (!node || node.closest("[hidden]")) return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

function currentScope() {
  if (state.openPanel) return [el.panels[state.openPanel]];
  if (!state.started) return [el.start];
  const scopes = [];
  if (!el.notice.hidden) scopes.push(el.notice);
  if (!el.tapMusic.hidden) scopes.push(el.tapMusic);
  if (!state.chromeHidden) scopes.push(el.top, el.bar);
  scopes.push(el.chromeToggle);
  const dbg = document.getElementById("debug-panel");
  if (dbg && !dbg.hidden) scopes.push(dbg);
  return scopes;
}

function focusablesIn(scopes) {
  const out = [];
  for (const scope of scopes) {
    if (scope.matches(FOCUSABLE)) out.push(scope);
    out.push(...$$(FOCUSABLE, scope));
  }
  return out.filter(isVisible);
}

function ensureFocus() {
  const scopes = currentScope();
  const active = document.activeElement;
  if (active && active !== body && scopes.some((s) => s.contains(active)) && isVisible(active)) return;
  const fallback = state.started && !state.openPanel ? (state.chromeHidden ? el.chromeToggle : el.nextBtn) : focusablesIn(scopes)[0];
  fallback?.focus();
}

function moveFocus(dir) {
  const items = focusablesIn(currentScope());
  const cur = document.activeElement;
  if (!items.includes(cur)) {
    ensureFocus();
    return;
  }
  const r = cur.getBoundingClientRect();
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  let best = null;
  let bestScore = Infinity;
  for (const node of items) {
    if (node === cur) continue;
    const b = node.getBoundingClientRect();
    const bx = b.left + b.width / 2;
    const by = b.top + b.height / 2;
    let primary;
    let gap;
    let drift;
    if (dir === "right" || dir === "left") {
      primary = dir === "right" ? b.left - r.right : r.left - b.right;
      if ((dir === "right" ? bx <= cx : bx >= cx) || primary < -r.width / 2) continue;
      gap = Math.max(0, b.top - r.bottom, r.top - b.bottom);
      drift = Math.abs(by - cy);
    } else {
      primary = dir === "down" ? b.top - r.bottom : r.top - b.bottom;
      if ((dir === "down" ? by <= cy : by >= cy) || primary < -r.height / 2) continue;
      gap = Math.max(0, b.left - r.right, r.left - b.right);
      drift = Math.abs(bx - cx);
    }
    const score = Math.max(0, primary) + gap * 3 + drift * 0.15;
    if (score < bestScore) {
      bestScore = score;
      best = node;
    }
  }
  // In long panels (About), scroll the text first instead of jumping past it.
  if ((dir === "up" || dir === "down") && state.openPanel) {
    const pane = $(".panel-body", el.panels[state.openPanel]);
    const pr = pane.getBoundingClientRect();
    const step = pr.height * 0.45;
    const canScroll = dir === "down" ? pane.scrollTop + pane.clientHeight < pane.scrollHeight - 2 : pane.scrollTop > 2;
    const far = !best || !pane.contains(best) || (dir === "down" ? best.getBoundingClientRect().top > pr.bottom + step * 0.2 : best.getBoundingClientRect().bottom < pr.top - step * 0.2);
    if (canScroll && far && (!best || pane.contains(cur) || dir === "down")) {
      pane.scrollBy({ top: dir === "down" ? step : -step, behavior: reducedMotion.matches ? "auto" : "smooth" });
      return;
    }
  }
  if (best) {
    best.focus();
    quietly(() => best.scrollIntoView({ block: "nearest", inline: "nearest" }));
  }
}

/* ================================================================ input */

const ARROWS = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
const BACK_KEYS = new Set(["Escape", "Backspace", "GoBack", "BrowserBack"]);
const MEDIA_KEYS = new Set(["MediaTrackNext", "MediaTrackPrevious", "MediaPlayPause", "MediaPlay", "MediaPause"]);

function isInteractive(node) {
  return !!node && node !== body && node.matches?.("button, a[href], input, select, textarea");
}

function onKeyDown(e) {
  const key = e.key;
  body.classList.add("kbd");
  const tv = isCasting(); // this page is only a remote while Cast is connecting or connected

  // Media keys work any time once started (or while casting).
  if (MEDIA_KEYS.has(key)) {
    if (!state.started && !tv) return;
    e.preventDefault();
    if (key === "MediaTrackNext") skip(1);
    else if (key === "MediaTrackPrevious") skip(-1);
    else if (key === "MediaPlay") setFeedPaused(false);
    else if (key === "MediaPause") setFeedPaused(true);
    else togglePause();
    return;
  }

  // Any key while playing locally keeps the chrome up (and restarts the 6 second hold).
  const local = localPlayerOn();
  if (local && !state.chromeHidden) holdChrome();

  // Enter/OK or an arrow with the chrome hidden brings it back (TV remotes).
  if (local && state.chromeHidden && !state.openPanel && (ARROWS[key] || (key === "Enter" && !isInteractive(document.activeElement)))) {
    e.preventDefault();
    setChrome(true, { focus: true });
    return;
  }

  if (BACK_KEYS.has(key)) {
    if (state.openPanel) {
      e.preventDefault();
      closePanel();
    } else if (local && !state.chromeHidden) {
      e.preventDefault();
      setChrome(false, { focus: true });
    }
    return;
  }

  if (key === "Tab" && state.openPanel) {
    const items = focusablesIn([el.panels[state.openPanel]]);
    if (items.length) {
      const i = items.indexOf(document.activeElement);
      if (i === -1 || (e.shiftKey && i === 0) || (!e.shiftKey && i === items.length - 1)) {
        e.preventDefault();
        (e.shiftKey ? items[items.length - 1] : items[0]).focus();
      }
    }
    return;
  }

  if (ARROWS[key]) {
    const t = e.target;
    if (t && t.type === "range" && (key === "ArrowLeft" || key === "ArrowRight")) return;
    e.preventDefault();
    moveFocus(ARROWS[key]);
    return;
  }

  if (!state.started && !tv) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  const k = key.length === 1 ? key.toLowerCase() : key;
  if (tv && !["n", "p", "u", " "].includes(k)) return; // casting: only the keys that map to a TV command
  switch (k) {
    case "n":
      e.preventDefault();
      skip(1);
      break;
    case "p":
      e.preventDefault();
      togglePin();
      break;
    case "m":
      e.preventDefault();
      if (local) setChrome(state.chromeHidden, { focus: true });
      break;
    case "u":
      e.preventDefault();
      toggleMute();
      break;
    case "f":
      e.preventDefault();
      toggleFullscreen();
      break;
    case " ":
      if (isInteractive(document.activeElement)) return; // let Space press the focused button
      e.preventDefault();
      togglePause();
      break;
    default:
  }
}

function handleAction(action, btn) {
  const id = btn?.dataset.id;
  switch (action) {
    case "prev":
      return skip(-1);
    case "next":
      return skip(1);
    case "pin":
      return togglePin();
    case "interval":
      return toggleInterval();
    case "lite":
      return toggleLite();
    case "open-cams":
      return openPanel("cams", btn);
    case "open-music":
      return openPanel("music", btn);
    case "open-tv":
      return openPanel("tv", btn);
    case "open-about":
      return openPanel("about", btn);
    case "toggle-chrome":
      return setChrome(state.chromeHidden, { focus: false });
    case "home":
      return goHome();
    case "close-panel":
      return closePanel();
    case "play-pause":
      return togglePause();
    case "vol-down":
      if (viaTv(() => remoteCmd("vol-down"))) return;
      localCommand("volume");
      return setVolume(state.volume - VOLUME_STEP);
    case "vol-up":
      if (viaTv(() => remoteCmd("vol-up"))) return;
      localCommand("volume");
      if (state.muted) {
        state.muted = false;
        return setVolume(Math.max(state.volume, VOLUME_STEP));
      }
      return setVolume(state.volume + VOLUME_STEP);
    case "fullscreen":
      return toggleFullscreen();
    case "mute":
      return toggleMute();
    case "shuffle":
      return toggleShuffle();
    case "toggle-cam":
      return setEnabled(id, !isEnabled(id));
    case "watch":
      closePanel();
      return watchNow(id);
    case "skip-sleeping":
      return toggleTimeRule("skipSleeping");
    case "only-daytime":
      return toggleTimeRule("onlyDaytime");
    case "pick-group":
      return pickGroup(btn.dataset.group);
    case "play-mix":
      return playMix(id, { userChoice: true });
    default:
  }
}

let lastPointer = { x: -1, y: -1 };

function bindInput() {
  window.addEventListener("keydown", onKeyDown, true);

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    handleAction(btn.dataset.action, btn);
  });

  el.startBtn.addEventListener("click", start);
  el.tapMusic.addEventListener("click", tapToPlay);
  el.noticeBtn.addEventListener("click", () => noticeAction && noticeAction());

  el.volume.addEventListener("input", () => setVolume(Number(el.volume.value), false));
  // The Mix sheet slider: local volume while playing here; while casting it sends one volume to the TV on release.
  el.mixVolume.addEventListener("input", () => {
    if (isCasting()) return showVolume(el.mixVolume, el.mixVolumeOut, Number(el.mixVolume.value));
    setVolume(Number(el.mixVolume.value), false);
  });
  el.mixVolume.addEventListener("change", () => isCasting() && setVolume(Number(el.mixVolume.value), false));
  // Telemetry: one local volume command per slider gesture (the cast path reports from js/cast.js).
  for (const input of [el.volume, el.mixVolume]) input.addEventListener("change", () => !isCasting() && localCommand("volume"));

  window.addEventListener(
    "pointerdown",
    (e) => {
      if (e.pointerType === "mouse") body.classList.remove("kbd");
      onPlayerPointer(e);
    },
    true,
  );

  window.addEventListener(
    "mousemove",
    (e) => {
      if (Math.abs(e.clientX - lastPointer.x) + Math.abs(e.clientY - lastPointer.y) < 4) return;
      lastPointer = { x: e.clientX, y: e.clientY };
      body.classList.remove("kbd");
    },
    { passive: true },
  );

  // The glass grows with a long cam name: keep --glass-h current so the debug panel stops above it.
  if (typeof ResizeObserver === "function") new ResizeObserver(() => measureBar()).observe(el.bar);
  window.addEventListener("resize", () => {
    measureBar();
    sizeCam();
  });
  document.addEventListener("fullscreenchange", render);
  document.addEventListener("webkitfullscreenchange", render);
  el.hero.img.addEventListener("load", () => (el.hero.img.hidden = false));
  el.hero.img.addEventListener("error", () => (el.hero.img.hidden = true));
}

/* ================================================================ boot */

/* Web fonts, added after load so a failed or slow request never blocks the page (CSS stacks fall back). */
function loadFonts() {
  const add = () => {
    if (document.querySelector("link[data-fonts]")) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = FONTS_URL;
    link.dataset.fonts = "";
    document.head.append(link);
  };
  if (document.readyState === "complete") setTimeout(add, 0);
  else window.addEventListener("load", () => setTimeout(add, 0), { once: true });
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator) || !/^https?:$/.test(location.protocol)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  });
}

function boot() {
  buildCamsPanel();
  buildMusicPanel();
  buildCredits();
  bindInput();
  render();
  body.classList.toggle("lite", state.lite);
  if (isTv) {
    body.classList.add("tv");
    // Keep the Start button focused until the user starts (TV browsers sometimes drop initial focus).
    const keepFocus = () => {
      if (!state.started && !state.openPanel && document.activeElement !== el.startBtn) el.startBtn.focus();
    };
    window.addEventListener("load", keepFocus);
    setTimeout(keepFocus, 300);
    setTimeout(keepFocus, 1500);
  }
  el.startBtn.focus();
  registerServiceWorker();
  sizeCam();
  renderHero();
  setInterval(() => {
    tick();
    renderStatus();
  }, 1000);
  setInterval(() => !state.started && renderHero(), 30000); // keep the Home clock and awake count current
  setInterval(() => state.openPanel === "cams" && renderCamsPanel(), 30000); // local times and Sleeping marks move on
  loadFonts();
  document.addEventListener("visibilitychange", onVisibility);
  loadYouTubeApi();
  setupDebug();
  setupTelemetrySwitch();
  setupCast();
  loadSheetConfig();
  reportAppOpen();
  // Page hide: end local playback and flush a pending cam pool change, both with sendBeacon.
  window.addEventListener("pagehide", () => {
    if (!state.casting) endLocalPlayback("page_hide", true);
    flushPoolTelemetry(true);
  });
  const buildLine = $("#about-build");
  if (buildLine) buildLine.textContent = `Build ${BUILD}`;

  window.__windowSeat = {
    state,
    next: () => skip(1),
    prev: () => skip(-1),
    start,
    build: BUILD,
    currentCamId: () => state.currentCamId,
    currentMixId: () => state.mixId,
    shuffle: () => state.shuffle,
    isTv,
    isCasting,
    isDebugOn,
    feedPaused: () => state.feedPaused,
    pinned: () => state.pinned,
    chromeHidden: () => state.chromeHidden,
    heroCamId: () => heroCamId,
    rotationIds: () => rotationIds(),
    isEnabled,
    renderHero,
  };
}

boot();
