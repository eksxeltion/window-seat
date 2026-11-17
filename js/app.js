// Window Seat: rotating Japan live cams with Tokyo Chill Lab mixes underneath.
import { CAMS, MUSIC_CHANNEL, MIXES } from "./data.js";
import qrcode from "./qr.js";
import { initCast, castConfigured, sendConfigToTv, isCasting, syncRemoteMode, remoteCmd, remoteMix, remoteCam, castDebugInfo, sendDebugToTv } from "./cast.js";
import { initDebug, isDebugOn } from "./debug.js";
import { BUILD } from "./build.js";
import { refreshFromSheet, listLabel } from "./config.js";

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
const storedLite = store.get("lite", null); // null: the user never chose

const savedInterval = store.get("interval", 60);
const savedMix = store.get("mix", null);
const savedVolume = Number(store.get("volume", 60));

/* ---------------------------------------------------------------- state */

const state = {
  apiReady: false,
  apiFailed: false,
  started: false,
  // cams
  intervalSec: savedInterval === 30 ? 30 : 60,
  enabledOverrides: loadOverrides(),
  failed: new Set(),
  pinned: false,
  feedPaused: false, // Pause stops the whole feed (cam and music); independent of pinned
  pausedRemaining: 0, // ms left on the rotation clock when the feed was paused (frozen while paused)
  currentCamId: null,
  pending: null, // { camId, startedAt, dir, fails }
  nextSwapAt: 0,
  allFailed: false,
  lite: storedLite === null ? isTv : storedLite === true,
  showCaption: store.get("caption", true) !== false,
  // music
  mixId: MIXES.some((m) => m.id === savedMix) ? savedMix : MIXES[0].id,
  volume: Number.isFinite(savedVolume) ? Math.min(100, Math.max(0, Math.round(savedVolume))) : 60,
  muted: store.get("muted", false) === true,
  shuffle: store.get("shuffle", false) === true,
  musicWantsPlay: true,
  musicState: YS.UNSTARTED,
  musicErrors: 0,
  musicUnavailable: false,
  // ui
  uiHidden: !store.get("menuOpen", true), // "menu closed"; the control bar never auto-hides
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
  nextBtn: $("#btn-next"),
  pinPill: $("#pin-pill"),
  menuBtn: $("#menu-btn"),
  caption: $("#caption"),
  capTitle: $(".cap-title"),
  capMeta: $(".cap-meta"),
  capMusic: $(".cap-music"),
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

function formatLength(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
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

/** Resume a live cam at the live edge, not where it froze. */
function camToLiveEdge() {
  hardMute();
  const d = quietly(() => cp.player.getDuration());
  if (typeof d === "number" && d > 0) quietly(() => cp.player.seekTo(d, true));
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
      onError: () => onCamError(),
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

/** Pick the next cam id from `fromId` in direction `dir`, skipping failed ones. */
function pickCam(fromId, dir = 1) {
  const usable = (c) => !state.failed.has(c.id);
  let pool = CAMS.filter((c) => usable(c) && isEnabled(c.id));
  if (!pool.length) pool = CAMS.filter(usable);
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
function beginLoad(camId, dir = 1, fails = 0) {
  const delay = covered ? 0 : animMs();
  state.pending = { camId, startedAt: Date.now() + delay, dir, fails };
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
}

function markFailed(camId) {
  if (camId) state.failed.add(camId);
}

function everythingFailed() {
  return CAMS.every((c) => state.failed.has(c.id));
}

/** Pending load failed (error or timeout): mark it, keep the overlay, try the next candidate. */
function failPending() {
  const p = state.pending;
  if (!p) return;
  state.pending = null;
  markFailed(p.camId);
  const next = pickCam(p.camId, p.dir);
  if (!next) {
    state.currentCamId = null;
    setAllFailed(true);
    render();
    return;
  }
  beginLoad(next, p.dir, p.fails + 1);
}

/** The cam on screen broke (error or stream ended). */
function failActive() {
  markFailed(state.currentCamId);
  activeStall = null;
  const next = pickCam(state.currentCamId, 1);
  if (!next || next === state.currentCamId) {
    state.currentCamId = null;
    cover(true);
    setAllFailed(true);
    render();
    return;
  }
  beginLoad(next, 1, 1);
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
    else if (s === YS.ENDED && isCurrentLoad()) failPending();
    return;
  }
  if (!state.currentCamId || state.feedPaused) return;
  if (s === YS.PLAYING) {
    activeStall = null;
    setQuality();
  } else if (s === YS.BUFFERING || s === YS.PAUSED) {
    if (!activeStall && watchdogOn()) activeStall = { at: Date.now(), tried: false };
  } else if (s === YS.ENDED && isCurrentLoad()) failActive();
}

// YouTube error codes 2, 5, 100, 101, 150 and 153 all mean the video cannot be played here.
// The error event carries no load generation, so a delayed error from an older cam could hit the one
// that replaced it. Never fail on the event alone: only remember it, then confirm by polling that the
// requested video is the one loaded and is still not playing (see errorConfirmed). Errors that cannot
// be confirmed this way (an invalid id often reports an empty or old video id) fall to the load timeouts.
function onCamError() {
  if (!hasCurrentLoad()) return;
  if (cp.errSeq !== cp.seq) {
    cp.errSeq = cp.seq;
    cp.errAt = Date.now();
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
    if (now - p.startedAt > deadline || errorConfirmed() || looksUnplayable(cold ? COLD_STUCK_MS : STUCK_MS)) failPending();
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
      failActive();
      return;
    }
  }
  if (activeStall && now - activeStall.at > LOAD_TIMEOUT_MS) {
    if (!activeStall.tried) {
      activeStall = { at: now, tried: true };
      camPlay();
    } else {
      failActive(); // works while pinned too
    }
    return;
  }
  if (state.pinned || now < state.nextSwapAt) return;
  const next = pickCam(state.currentCamId, 1);
  if (next && next !== state.currentCamId) beginLoad(next);
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
    if (live !== YS.PLAYING && live !== YS.BUFFERING) beginLoad(state.currentCamId);
  }, GRACE_MS + 250);
}

/* --- cam actions --- */

function skip(dir = 1) {
  if (viaTv(() => remoteCmd(dir < 0 ? "prev" : "next"))) return;
  if (state.casting || !state.started || !state.apiReady) return;
  const from = state.pending ? state.pending.camId : state.currentCamId;
  const target = pickCam(from, dir);
  if (!target) return;
  if (state.pending && state.pending.camId === target) return;
  if (!state.pending && target === state.currentCamId) {
    state.nextSwapAt = Date.now() + rotateSec() * 1000;
    return;
  }
  beginLoad(target, dir);
}

function watchNow(camId) {
  if (!camById(camId)) return;
  state.failed.delete(camId);
  setAllFailed(false);
  if (viaTv(() => remoteCam(camId))) return;
  if (state.casting || !state.started) return;
  if (state.pending ? state.pending.camId === camId : camId === state.currentCamId) return;
  beginLoad(camId);
}

function togglePin() {
  if (viaTv(() => remoteCmd("pin"))) return;
  state.pinned = !state.pinned;
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
  store.set("interval", state.intervalSec);
  state.nextSwapAt = Date.now() + rotateSec() * 1000;
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
  toast(state.lite ? "Lite mode on" : "Lite mode off");
  render();
}

function setEnabled(camId, on) {
  state.enabledOverrides[camId] = on;
  if (on === !!camById(camId)?.default) delete state.enabledOverrides[camId];
  saveOverrides();
  render();
}

function saveOverrides() {
  if (Object.keys(state.enabledOverrides).length) store.set("enabled", state.enabledOverrides);
  else store.remove("enabled");
}

function selectDefaults() {
  state.enabledOverrides = {};
  saveOverrides();
  toast("Default cams selected");
  render();
}

function selectAll() {
  state.enabledOverrides = {};
  for (const cam of CAMS) if (!cam.default) state.enabledOverrides[cam.id] = true;
  saveOverrides();
  toast("All cams selected");
  render();
}

function setAllFailed(on) {
  if (state.allFailed === on) return;
  state.allFailed = on;
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
  beginLoad(pickCam(state.currentCamId, 1) || CAMS[0].id);
}

function startCams() {
  const pool = CAMS.filter((c) => isEnabled(c.id));
  const list = pool.length ? pool : CAMS;
  beginLoad(list[Math.floor(Math.random() * list.length)].id);
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
      onError: () => onMusicError(),
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
  if (state.musicWantsPlay) quietly(() => musicPlayer.playVideo());
  scheduleMusicCheck();
}

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
    if (musicReady) quietly(() => musicPlayer.cueVideoById(mixId));
    return render();
  }
  if (state.feedPaused) resumeCam(); // choosing a mix plays the whole feed again
  state.mixId = mixId;
  store.set("mix", mixId);
  state.musicWantsPlay = true;
  if (userChoice) {
    state.musicErrors = 0;
    state.musicUnavailable = false;
  }
  if (state.casting) return render();
  if (musicReady && state.started) {
    applyVolume();
    quietly(() => musicPlayer.loadVideoById(mixId));
    scheduleMusicCheck();
  } else if (musicReady) {
    quietly(() => musicPlayer.cueVideoById(mixId));
  }
  render();
}

function nextMix() {
  if (viaTv(() => remoteCmd("next-mix"))) return;
  const i = MIXES.findIndex((m) => m.id === state.mixId);
  let j;
  if (state.shuffle && MIXES.length > 1) {
    do j = Math.floor(Math.random() * MIXES.length);
    while (j === i);
  } else {
    j = (i + 1) % MIXES.length;
  }
  playMix(MIXES[j].id);
}

function onMusicState(s) {
  state.musicState = s;
  if (s === YS.PLAYING) {
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

function onMusicError() {
  state.musicErrors++;
  if (state.musicErrors >= MIXES.length) {
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
  if (paused) {
    pauseCam();
    pauseMusic();
  } else {
    resumeCam();
    resumeMusic();
  }
  render();
}

function togglePause() {
  if (viaTv(() => remoteCmd("play"))) return;
  setFeedPaused(!state.feedPaused);
}

function setVolume(v, announce = true) {
  if (viaTv(() => remoteCmd(v > state.volume ? "vol-up" : "vol-down"))) return;
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
  store.set("muted", state.muted);
  applyVolume();
  toast(state.muted ? "Music muted" : `Music on, volume ${state.volume}`);
  render();
}

function toggleShuffle() {
  state.shuffle = !state.shuffle;
  store.set("shuffle", state.shuffle);
  render();
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
  if (btn) setText($(".label", btn), text);
  return btn;
}

function measureBar() {
  const h = el.bar.offsetHeight;
  if (h) document.documentElement.style.setProperty("--bar-h", h + "px");
}

function render() {
  // Bar
  setText(el.pinPill, state.pinned ? "Pinned" : "Pin cam");
  el.pinPill.setAttribute("aria-pressed", String(state.pinned));
  const secs = rotateSec();
  for (const b of $$('[data-action="interval"]')) b.disabled = state.lite;
  setBarLabel("interval", `Every ${secs}s`);
  setText($("#cams-interval"), `New cam every ${secs}s`);
  $('.bar-btn[data-action="lite"]', el.bar).setAttribute("aria-pressed", String(state.lite));

  // Pause / Play is the whole feed (cam and music).
  const playing = !state.feedPaused;
  const ppBtn = setBarLabel("play-pause", playing ? "Pause" : "Play");
  $("use", ppBtn).setAttribute("href", playing ? "#i-pause" : "#i-play");
  setText($("#music-play"), playing ? "Pause" : "Play");

  setBarLabel("fullscreen", isFullscreen() ? "Exit fullscreen" : "Fullscreen");

  // Caption
  const cam = camById(state.currentCamId);
  if (cam && state.started) {
    el.caption.hidden = false;
    setText(el.capTitle, cam.title);
    setText(el.capMeta, `${cam.place} · ${cam.channel}${state.pinned ? " · Pinned" : ""}${state.feedPaused ? " · Paused" : ""}`);
  } else {
    el.caption.hidden = true;
  }
  const mix = mixById(state.mixId);
  setText(el.capMusic, state.musicUnavailable ? "Music unavailable" : mix ? `♪ ${mix.en}` : "");
  body.classList.toggle("no-caption", !state.showCaption);

  // "Tuning in" indicator while no cam is on screen
  el.tuning.hidden = !(state.started && !state.allFailed && covered && (!state.currentCamId || (state.pending && state.pending.fails > 0)));

  // Cams panel
  $$('[data-action="toggle-caption"]').forEach((b) => b.setAttribute("aria-pressed", String(state.showCaption)));
  for (const row of $$(".cam-row", el.camsList)) {
    const id = row.dataset.id;
    const on = isEnabled(id);
    const failed = state.failed.has(id);
    const current = id === state.currentCamId;
    row.classList.toggle("is-current", current);
    row.classList.toggle("is-failed", failed);
    const toggle = $('[data-action="toggle-cam"]', row);
    toggle.setAttribute("aria-pressed", String(on));
    $(".switch-text", toggle).textContent = on ? "In rotation" : "Not in rotation";
    const badge = $(".badge", row);
    badge.hidden = !(current || failed);
    badge.className = "badge " + (failed ? "badge-failed" : "badge-now");
    badge.textContent = failed ? "Unavailable right now" : "Now showing";
  }

  // Music panel
  $("#music-mute").setAttribute("aria-pressed", String(state.muted));
  $("#music-shuffle").setAttribute("aria-pressed", String(state.shuffle));
  if (document.activeElement !== el.volume) el.volume.value = String(state.volume);
  setText(el.volumeOut, String(state.volume));
  for (const btn of $$(".mix-btn", el.mixList)) {
    const current = btn.dataset.id === state.mixId;
    btn.classList.toggle("is-current", current);
    if (current) btn.setAttribute("aria-current", "true");
    else btn.removeAttribute("aria-current");
  }
}

function buildCamsPanel() {
  const places = [];
  for (const cam of CAMS) if (!places.includes(cam.place)) places.push(cam.place);
  const frag = document.createDocumentFragment();
  for (const place of places) {
    const h = document.createElement("h3");
    h.className = "place";
    h.textContent = place;
    const ul = document.createElement("ul");
    ul.className = "cam-list";
    for (const cam of CAMS.filter((c) => c.place === place)) {
      const li = document.createElement("li");
      li.className = "cam-row";
      li.dataset.id = cam.id;

      const info = document.createElement("div");
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
      sub.textContent = cam.channel;
      info.append(title, sub);

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "switch";
      toggle.dataset.action = "toggle-cam";
      toggle.dataset.id = cam.id;
      toggle.setAttribute("aria-label", `${cam.title}: in rotation`);
      toggle.innerHTML = '<span class="switch-track"><span class="switch-thumb"></span></span><span class="switch-text"></span>';

      const watch = document.createElement("button");
      watch.type = "button";
      watch.className = "btn";
      watch.dataset.action = "watch";
      watch.dataset.id = cam.id;
      watch.textContent = "Watch now";
      watch.setAttribute("aria-label", `Watch ${cam.title} now`);

      li.append(info, toggle, watch);
      ul.append(li);
    }
    frag.append(h, ul);
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
    const len = document.createElement("span");
    len.className = "mix-len";
    len.textContent = formatLength(mix.minutes);
    btn.append(en, ja, len);
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
}

function showListStatus(error) {
  document.querySelectorAll(".list-status").forEach((n) => {
    n.textContent = listLabel();
    n.title = error ? `Sheet not loaded: ${error}` : "";
  });
}

function loadSheetConfig() {
  showListStatus();
  refreshFromSheet().then((r) => {
    if (r.changed) rebuildLists();
    showListStatus(r.ok ? "" : r.error);
    if (r.ok) sendConfigToTv(); // keep a casting TV on the same list
  });
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
  [el.bar, el.pinPill, el.notice, el.tapMusic, el.start].forEach((n) => n && (n.inert = on));
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
  body.classList.add("panel-open");
  setBackgroundInert(true);
  render();
  let target = null;
  if (name === "cams") target = $(`.cam-row[data-id="${state.currentCamId}"] [data-action="watch"]`, panel);
  if (name === "music") target = $(`.mix-btn[data-id="${state.mixId}"]`, panel);
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
  if (restoreFocus) {
    const target =
      panelOpener && isVisible(panelOpener)
        ? panelOpener
        : state.started
          ? $(`.bar-btn[data-action="open-${name}"]`, el.bar)
          : el.startBtn;
    target?.focus();
  }
  panelOpener = null;
}

/* ================================================================ fullscreen */

function isFullscreen() {
  return !!(document.fullscreenElement || document.webkitFullscreenElement);
}

function toggleFullscreen() {
  const docEl = document.documentElement;
  if (isFullscreen()) {
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if (exit) Promise.resolve(quietly(() => exit.call(document))).catch(() => {});
  } else {
    const req = docEl.requestFullscreen || docEl.webkitRequestFullscreen;
    if (req) Promise.resolve(quietly(() => req.call(docEl))).catch(() => toast("Fullscreen is not available here"));
    else toast("Fullscreen is not available here");
  }
}

/* ================================================================ start */

let begun = false;

function start() {
  if (viaTv() || state.started || state.casting) return;
  if (state.apiFailed) {
    location.reload();
    return;
  }
  state.started = true;
  if (isTv) {
    const docEl = document.documentElement;
    const req = docEl.requestFullscreen || docEl.webkitRequestFullscreen;
    if (req && !isFullscreen()) Promise.resolve(quietly(() => req.call(docEl))).catch(() => {});
  }
  body.classList.remove("is-starting");
  el.start.classList.add("leaving");
  setTimeout(() => {
    el.start.hidden = true;
  }, reducedMotion.matches ? 0 : 650);
  if (state.openPanel) closePanel(false);
  syncMenu();
  (state.uiHidden ? el.menuBtn : el.nextBtn).focus();
  measureBar();
  if (state.apiReady) begin();
  else el.startStatus.textContent = "Connecting to YouTube...";
}

function begin() {
  if (begun || viaTv()) return;
  begun = true;
  startCams();
  startMusic();
  render();
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
  // Fresh players; nothing plays until the user presses Start.
  if (state.apiReady && !cp.player) createCamPlayer();
  if (state.apiReady && !musicPlayer) createMusicPlayer();
  body.classList.add("is-starting");
  el.start.hidden = false;
  el.start.classList.remove("leaving");
  el.startStatus.textContent = "";
  el.startBtn.focus();
  render();
}

function setupCast() {
  const note = $("#about-cast");
  if (!castConfigured) {
    if (note) note.textContent = "Cast to TV: coming soon.";
    return;
  }
  if (note) note.textContent = "Tap the Cast button in Chrome and pick your TV. This page then becomes a remote.";
  initCast({
    getLocal: () => ({ camId: state.currentCamId, mixId: state.mixId, interval: state.intervalSec, volume: state.volume, muted: state.muted, camIds: CAMS.filter((c) => isEnabled(c.id)).map((c) => c.id) }),
    onStart: pauseLocalForCast,
    onEnd: resumeAfterCast,
    toast: (text) => toast(text, 6000),
  });
}

/* ================================================================ debug mode */

// Opt-in diagnostics (About, This copy). Off on every page load; see js/debug.js.
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
    el.startBtn.querySelector("span").textContent = "Start";
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

// The control bar is opened and closed explicitly (Menu button, Enter/OK, M, Close, Escape/Back).
// It never hides on a timer. The open/closed choice is remembered.
function syncMenu() {
  body.classList.toggle("ui-hidden", state.uiHidden);
  el.menuBtn.setAttribute("aria-expanded", String(!state.uiHidden));
}

function setMenu(open) {
  state.uiHidden = !open;
  store.set("menuOpen", open);
  if (!open && state.openPanel) closePanel(false);
  syncMenu();
  if (open) el.nextBtn.focus();
  else el.menuBtn.focus();
}

function toggleMenu() {
  setMenu(state.uiHidden);
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
  scopes.push(el.menuBtn);
  if (!state.uiHidden) {
    if (!el.caption.hidden) scopes.push(el.pinPill);
    scopes.push(el.bar);
  }
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
  const fallback = state.started && !state.openPanel ? (state.uiHidden ? el.menuBtn : el.nextBtn) : focusablesIn(scopes)[0];
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

  // Enter/OK with nothing focused opens the menu (TV remotes).
  if (state.started && state.uiHidden && !state.openPanel && key === "Enter" && !isInteractive(document.activeElement)) {
    e.preventDefault();
    setMenu(true);
    return;
  }

  if (BACK_KEYS.has(key)) {
    if (state.openPanel) {
      e.preventDefault();
      closePanel();
    } else if (state.started && !state.uiHidden) {
      e.preventDefault();
      setMenu(false);
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
      toggleMenu();
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
    case "toggle-menu":
      return toggleMenu();
    case "close-menu":
      return setMenu(false);
    case "close-panel":
      return closePanel();
    case "play-pause":
      return togglePause();
    case "vol-down":
      if (viaTv(() => remoteCmd("vol-down"))) return;
      return setVolume(state.volume - VOLUME_STEP);
    case "vol-up":
      if (viaTv(() => remoteCmd("vol-up"))) return;
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
    case "toggle-caption":
      state.showCaption = !state.showCaption;
      store.set("caption", state.showCaption);
      return render();
    case "toggle-cam":
      return setEnabled(id, !isEnabled(id));
    case "watch":
      closePanel();
      return watchNow(id);
    case "select-defaults":
      return selectDefaults();
    case "select-all":
      return selectAll();
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

  window.addEventListener(
    "pointerdown",
    (e) => {
      if (e.pointerType === "mouse") body.classList.remove("kbd");
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

  window.addEventListener("resize", () => {
    measureBar();
    sizeCam();
  });
  document.addEventListener("fullscreenchange", render);
  document.addEventListener("webkitfullscreenchange", render);
}

/* ================================================================ boot */

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
  setInterval(tick, 1000);
  document.addEventListener("visibilitychange", onVisibility);
  loadYouTubeApi();
  setupDebug();
  setupCast();
  loadSheetConfig();
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
    isTv,
    isCasting,
    isDebugOn,
    feedPaused: () => state.feedPaused,
    pinned: () => state.pinned,
  };
}

boot();
