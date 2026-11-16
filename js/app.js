// Window Seat: rotating Japan live cams with Tokyo Chill Lab mixes underneath.
import { CAMS, MUSIC_CHANNEL, MIXES } from "./data.js";

/* ---------------------------------------------------------------- config */

const YT_API_URL = "https://www.youtube.com/iframe_api";
const YT_HOST = "https://www.youtube-nocookie.com";
const PRELOAD_LEAD_MS = 6000; // start loading the next cam this long before the switch
const LOAD_TIMEOUT_MS = 15000; // a cam that is not PLAYING by then is marked unavailable
const COLD_LOAD_TIMEOUT_MS = 8000; // same deadline while no cam is on screen (cold start or current cam failed)
const STUCK_MS = 7000; // loaded but still UNSTARTED/CUED this long: treat as unplayable
const COLD_STUCK_MS = 4000; // same, while no cam is on screen
const FADE_MS = 1500;
const IDLE_MS = 4000;
const PANEL_IDLE_MS = 45000;
const MUSIC_CHECK_MS = 5000;
const MUSIC_READY_MS = 15000; // music iframe must report ready by then
const STALE_EVENT_MS = 300; // ignore terminal events this soon after a load call (they belong to the old video)
const API_TIMEOUT_MS = 20000;
const VOLUME_STEP = 10;

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
  currentCamId: null,
  pending: null, // { camId, slot, startedAt, ready, swapNow, dir }
  nextSwapAt: 0,
  allFailed: false,
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
  uiHidden: false,
  openPanel: null,
};

const reducedMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
const fadeMs = () => (reducedMotion.matches ? 0 : FADE_MS);

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
  caption: $("#caption"),
  capTitle: $(".cap-title"),
  capMeta: $(".cap-meta"),
  capMusic: $(".cap-music"),
  tapMusic: $("#tap-music"),
  tuning: $("#tuning"),
  notice: $("#notice"),
  noticeTitle: $("#notice-title"),
  noticeText: $("#notice-text"),
  noticeBtn: $("#notice-btn"),
  toast: $("#toast"),
  camsList: $("#cams-list"),
  mixList: $("#mix-list"),
  volume: $("#music-volume"),
  volumeOut: $("#music-volume-out"),
  panels: { cams: $("#panel-cams"), music: $("#panel-music"), about: $("#panel-about") },
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
function toast(text) {
  el.toast.textContent = text;
  el.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove("show"), 2200);
}

/* ================================================================ cams */

const slots = [0, 1].map((i) => ({
  index: i,
  el: document.getElementById("cam-" + i),
  player: null,
  ready: false,
  waiters: [],
  camId: null,
  seq: 0,
  loadedAt: 0,
}));
let activeStall = null; // { at, tried } while the on-screen cam is not PLAYING
let activeSlot = null;

function playerOrigin() {
  return /^https?:$/.test(location.protocol) ? location.origin : undefined;
}

function createCamPlayer(slot) {
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
  };
  const origin = playerOrigin();
  if (origin) vars.origin = origin;
  slot.player = new window.YT.Player(slot.el.querySelector(".cam-player"), {
    host: YT_HOST,
    width: "1920",
    height: "1080",
    playerVars: vars,
    events: {
      onReady: () => {
        slot.ready = true;
        hardMute(slot);
        const frame = quietly(() => slot.player.getIframe());
        if (frame) {
          frame.setAttribute("tabindex", "-1");
          frame.setAttribute("title", "Live cam");
        }
        const waiters = slot.waiters.splice(0);
        waiters.forEach((fn) => fn());
      },
      onStateChange: (e) => onCamState(slot, e.data),
      onError: (e) => onCamError(slot, e.data),
    },
  });
}

function hardMute(slot) {
  quietly(() => {
    slot.player.mute();
    slot.player.setVolume(0);
  });
}

function whenReady(slot, fn) {
  if (slot.ready) fn();
  else slot.waiters.push(fn);
}

function loadCamInto(slot, camId) {
  slot.camId = camId;
  slot.loadedAt = 0;
  slot.sawState = false;
  slot.nudged = false;
  const seq = ++slot.seq;
  whenReady(slot, () => {
    if (slot.camId !== camId || slot.seq !== seq) return;
    slot.loadedAt = Date.now();
    slot.sawState = false;
    slot.nudged = false;
    hardMute(slot);
    quietly(() => slot.player.loadVideoById(camId));
    hardMute(slot);
  });
}

function stopSlot(slot) {
  slot.seq++;
  slot.camId = null;
  if (slot.ready) quietly(() => slot.player.stopVideo());
}

function slotShows(slot, camId) {
  const data = quietly(() => slot.player.getVideoData());
  return !data || !data.video_id || data.video_id === camId;
}

/** True when a terminal event (ended/error) really belongs to the video this slot is meant to show. */
function isCurrentLoad(slot) {
  if (!slot.camId || !slot.loadedAt) return false;
  if (Date.now() - slot.loadedAt < STALE_EVENT_MS) return false;
  return slotShows(slot, slot.camId);
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

function inactiveSlot() {
  return activeSlot === slots[0] ? slots[1] : slots[0];
}

function cancelPending() {
  const p = state.pending;
  if (!p) return;
  state.pending = null;
  if (p.slot !== activeSlot) stopSlot(p.slot);
}

function beginLoad(camId, swapNow, dir = 1) {
  cancelPending();
  const slot = inactiveSlot();
  state.pending = { camId, slot, startedAt: Date.now(), ready: false, swapNow, dir };
  loadCamInto(slot, camId);
}

function swapIn() {
  const p = state.pending;
  if (!p) return;
  const live = quietly(() => p.slot.player.getPlayerState());
  if (live !== YS.PLAYING || !slotShows(p.slot, p.camId)) {
    // Not actually playing (anymore): wait for PLAYING, keeping the stall deadline.
    if (p.ready) {
      p.ready = false;
      p.startedAt = Date.now();
    }
    return;
  }
  state.pending = null;
  activeStall = null;
  const incoming = p.slot;
  const outgoing = activeSlot;
  activeSlot = incoming;
  state.currentCamId = p.camId;
  state.nextSwapAt = Date.now() + state.intervalSec * 1000;
  setAllFailed(false);

  incoming.el.classList.remove("is-leaving");
  if (outgoing && outgoing !== incoming) {
    outgoing.el.classList.remove("is-active");
    outgoing.el.classList.add("is-leaving");
    const seq = outgoing.seq;
    setTimeout(() => {
      outgoing.el.classList.remove("is-leaving");
      if (outgoing !== activeSlot && outgoing.seq === seq) stopSlot(outgoing);
    }, fadeMs() + 100);
  }
  // Force a style flush so the opacity transition runs (rAF would stall in background tabs).
  void incoming.el.offsetWidth;
  incoming.el.classList.add("is-active");
  render();
}

function markFailed(camId) {
  if (!camId) return;
  state.failed.add(camId);
}

/** True while there is no usable cam on screen (nothing loaded yet, or the current cam failed). */
function noCamVisible() {
  return !activeSlot || !state.currentCamId || state.failed.has(state.currentCamId);
}

function everythingFailed() {
  return CAMS.every((c) => state.failed.has(c.id));
}

/** Pending load failed (error or timeout): mark it and move on. */
function failPending() {
  const p = state.pending;
  if (!p) return;
  state.pending = null;
  markFailed(p.camId);
  if (p.slot !== activeSlot) stopSlot(p.slot);
  const next = pickCam(p.camId, p.dir);
  const currentOk = activeSlot && state.currentCamId && !state.failed.has(state.currentCamId);
  if (!next) {
    if (!currentOk) setAllFailed(true);
    render();
    return;
  }
  if (next === state.currentCamId && currentOk) {
    state.nextSwapAt = Date.now() + state.intervalSec * 1000;
    render();
    return;
  }
  beginLoad(next, p.swapNow || !currentOk, p.dir);
  render();
}

/** The cam on screen broke (error or stream ended). */
function failActive() {
  markFailed(state.currentCamId);
  const p = state.pending;
  if (p) {
    p.swapNow = true;
    if (p.ready) swapIn();
    render();
    return;
  }
  activeStall = null;
  const next = pickCam(state.currentCamId, 1);
  if (!next || next === state.currentCamId) {
    if (everythingFailed() || !next) setAllFailed(true);
    render();
    return;
  }
  beginLoad(next, true);
  render();
}

/** Unplayable-video check for a slot that was told to load a cam but is not progressing. */
function slotLooksUnplayable(slot, limitMs) {
  if (!slot.camId || !slot.loadedAt) return false;
  const waited = Date.now() - slot.loadedAt;
  if (waited < Math.min(limitMs, 2500)) return false;
  const live = quietly(() => slot.player.getPlayerState());
  if (live !== YS.UNSTARTED && live !== YS.CUED) return false;
  if (waited < limitMs) {
    // Give a CUED video one nudge in case autoplay just needs a kick.
    if (live === YS.CUED && !slot.nudged) {
      slot.nudged = true;
      quietly(() => slot.player.playVideo());
    }
    return false;
  }
  return true;
}

function onCamState(slot, s) {
  if (slot.loadedAt && Date.now() - slot.loadedAt >= 20) slot.sawState = true;
  if (s === YS.PLAYING || s === YS.BUFFERING) hardMute(slot);
  const p = state.pending;
  if (p && p.slot === slot) {
    if (s === YS.PLAYING) {
      if (slotShows(slot, p.camId)) {
        p.ready = true;
        if (p.swapNow) swapIn();
      }
    } else {
      if (p.ready) {
        // Lost playback after preloading: not ready any more, restart the stall deadline.
        p.ready = false;
        p.startedAt = Date.now();
      }
      if (s === YS.ENDED && isCurrentLoad(slot)) failPending();
    }
    return;
  }
  if (slot !== activeSlot) return;
  if (s === YS.PLAYING) activeStall = null;
  else if (s === YS.BUFFERING || s === YS.PAUSED) {
    if (!activeStall) activeStall = { at: Date.now(), tried: false };
  } else if (s === YS.ENDED && isCurrentLoad(slot)) failActive();
}

// YouTube error codes 2, 5, 100, 101, 150 and 153 all mean the video cannot be played here,
// so any error for the video we just asked for is a prompt failure.
function onCamError(slot) {
  // Not using slotShows(): for an invalid id the player may still report the previous video id.
  // Invalid ids report the error within ~150ms of the load call, so the usual stale-event
  // window is skipped once the slot has already reacted to the new load.
  if (!slot.camId || !slot.loadedAt) return;
  if (!slot.sawState && Date.now() - slot.loadedAt < STALE_EVENT_MS) return;
  const p = state.pending;
  if (p && p.slot === slot) failPending();
  else if (slot === activeSlot) failActive();
}

function tick() {
  if (!state.started || !state.apiReady) return;
  const now = Date.now();
  const p = state.pending;
  if (p && !p.ready) {
    const cold = noCamVisible();
    const deadline = cold ? COLD_LOAD_TIMEOUT_MS : LOAD_TIMEOUT_MS;
    if (now - p.startedAt > deadline || slotLooksUnplayable(p.slot, cold ? COLD_STUCK_MS : STUCK_MS)) {
      failPending();
      return;
    }
  }
  if (activeStall && activeSlot && !state.allFailed && now - activeStall.at > LOAD_TIMEOUT_MS) {
    if (!activeStall.tried) {
      activeStall = { at: now, tried: true };
      quietly(() => activeSlot.player.playVideo());
    } else {
      activeStall = null;
      failActive(); // works while pinned too
      return;
    }
  }
  if (state.pinned || !activeSlot || state.allFailed) return;
  if (!p && now >= state.nextSwapAt - PRELOAD_LEAD_MS) {
    const next = pickCam(state.currentCamId, 1);
    if (next && next !== state.currentCamId) beginLoad(next, false);
    else state.nextSwapAt = now + state.intervalSec * 1000;
  } else if (p && !p.swapNow && now >= state.nextSwapAt) {
    p.swapNow = true;
    if (p.ready) swapIn();
  }
}

/* --- cam actions --- */

function skip(dir = 1) {
  if (!state.started || !state.apiReady) return;
  const from = state.pending ? state.pending.camId : state.currentCamId;
  const target = pickCam(from, dir);
  if (!target) return;
  if (target === state.currentCamId) {
    cancelPending();
    state.nextSwapAt = Date.now() + state.intervalSec * 1000;
    render();
    return;
  }
  if (state.pending && state.pending.camId === target) {
    state.pending.swapNow = true;
    if (state.pending.ready) swapIn();
    return;
  }
  beginLoad(target, true, dir);
  render();
}

function watchNow(camId) {
  if (!camById(camId)) return;
  state.failed.delete(camId);
  setAllFailed(false);
  if (!state.started) return;
  if (camId === state.currentCamId && !state.pending) return;
  if (state.pending && state.pending.camId === camId) {
    state.pending.swapNow = true;
    if (state.pending.ready) swapIn();
    return;
  }
  beginLoad(camId, true);
  render();
}

function togglePin() {
  state.pinned = !state.pinned;
  if (state.pinned) {
    if (state.pending && !state.pending.swapNow) cancelPending();
  } else {
    state.nextSwapAt = Date.now() + state.intervalSec * 1000;
  }
  toast(state.pinned ? "Pinned. This cam stays on." : "Unpinned. Rotation is back on.");
  render();
}

function toggleInterval() {
  state.intervalSec = state.intervalSec === 60 ? 30 : 60;
  store.set("interval", state.intervalSec);
  state.nextSwapAt = Date.now() + state.intervalSec * 1000;
  if (state.pending && !state.pending.swapNow) cancelPending();
  toast(`New cam every ${state.intervalSec} seconds`);
  render();
}

function setEnabled(camId, on) {
  state.enabledOverrides[camId] = on;
  if (on === !!camById(camId)?.default) delete state.enabledOverrides[camId];
  saveOverrides();
  if (!on && state.pending && state.pending.camId === camId && !state.pending.swapNow) cancelPending();
  render();
}

function saveOverrides() {
  if (Object.keys(state.enabledOverrides).length) store.set("enabled", state.enabledOverrides);
  else store.remove("enabled");
}

function selectDefaults() {
  state.enabledOverrides = {};
  saveOverrides();
  if (state.pending && !state.pending.swapNow && !isEnabled(state.pending.camId)) cancelPending();
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
  const first = pickCam(state.currentCamId, 1) || CAMS[0].id;
  beginLoad(first, true);
  render();
}

function startCams() {
  const pool = CAMS.filter((c) => isEnabled(c.id));
  const list = pool.length ? pool : CAMS;
  const first = list[Math.floor(Math.random() * list.length)].id;
  beginLoad(first, true);
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
  };
  const origin = playerOrigin();
  if (origin) vars.origin = origin;
  clearTimeout(musicReadyTimer);
  musicReadyTimer = setTimeout(onMusicReadyTimeout, MUSIC_READY_MS);
  musicPlayer = new window.YT.Player("music-player", {
    host: YT_HOST,
    width: "200",
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
    showUI();
    el.tapMusic.focus();
  } else if (!on && document.activeElement === el.tapMusic) {
    el.nextBtn.focus();
  }
}

function playMix(mixId, { userChoice = false } = {}) {
  if (!mixById(mixId)) return;
  state.mixId = mixId;
  store.set("mix", mixId);
  state.musicWantsPlay = true;
  if (userChoice) {
    state.musicErrors = 0;
    state.musicUnavailable = false;
  }
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

function musicIsPlaying() {
  return state.musicState === YS.PLAYING || state.musicState === YS.BUFFERING;
}

function togglePlayMusic() {
  if (musicStuck) return retryMusicPlayer();
  if (!musicReady) return;
  if (state.musicUnavailable) {
    state.musicWantsPlay = true;
    retryUnavailableMusic();
    return;
  }
  if (musicIsPlaying() && state.musicWantsPlay) {
    state.musicWantsPlay = false;
    quietly(() => musicPlayer.pauseVideo());
    showTapMusic(false);
  } else {
    state.musicWantsPlay = true;
    applyVolume();
    quietly(() => musicPlayer.playVideo());
    scheduleMusicCheck();
  }
  render();
}

function setVolume(v, announce = true) {
  state.volume = Math.min(100, Math.max(0, Math.round(v)));
  if (state.volume > 0 && state.muted) state.muted = false;
  store.set("volume", state.volume);
  store.set("muted", state.muted);
  applyVolume();
  if (announce) toast(`Music volume ${state.volume}`);
  render();
}

function toggleMute() {
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
  if (musicStuck) return retryMusicPlayer();
  state.musicWantsPlay = true;
  if (!musicReady) return;
  if (state.musicUnavailable) return retryUnavailableMusic();
  applyVolume();
  quietly(() => musicPlayer.playVideo());
  scheduleMusicCheck();
}

/* ================================================================ render */

function setBarLabel(action, text) {
  const btn = $(`.bar-btn[data-action="${action}"]`, el.bar);
  if (btn) $(".label", btn).textContent = text;
  return btn;
}

function measureBar() {
  const h = el.bar.offsetHeight;
  if (h) document.documentElement.style.setProperty("--bar-h", h + "px");
}

function render() {
  // Bar
  const pinBtn = setBarLabel("pin", state.pinned ? "Unpin" : "Pin");
  pinBtn.setAttribute("aria-pressed", String(state.pinned));
  setBarLabel("interval", `Every ${state.intervalSec}s`);
  $("#cams-interval").textContent = `New cam every ${state.intervalSec}s`;

  const playing = musicIsPlaying() && state.musicWantsPlay;
  const ppBtn = setBarLabel("play-pause", playing ? "Pause" : "Play");
  $("use", ppBtn).setAttribute("href", playing ? "#i-pause" : "#i-play");
  $("#music-play").textContent = playing ? "Pause music" : "Play music";

  setBarLabel("fullscreen", isFullscreen() ? "Exit fullscreen" : "Fullscreen");

  // Caption
  const cam = camById(state.currentCamId);
  if (cam && state.started) {
    el.caption.hidden = false;
    el.capTitle.textContent = cam.title;
    el.capMeta.textContent = `${cam.place} · ${cam.channel}`;
    if (state.pinned) {
      const pin = document.createElement("span");
      pin.className = "cap-pin";
      pin.textContent = " · Pinned";
      el.capMeta.append(pin);
    }
  } else {
    el.caption.hidden = true;
  }
  const mix = mixById(state.mixId);
  el.capMusic.textContent = state.musicUnavailable ? "Music unavailable" : mix ? `♪ ${mix.en}` : "";
  body.classList.toggle("no-caption", !state.showCaption);

  // "Tuning in" indicator while no cam is on screen
  el.tuning.hidden = !(state.started && !state.allFailed && noCamVisible());

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
  el.volumeOut.textContent = String(state.volume);
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
    showUI();
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
  [el.bar, el.notice, el.tapMusic, el.start].forEach((n) => n && (n.inert = on));
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
  if (!target) target = $(FOCUSABLE, panel);
  if (target) {
    target.focus();
    quietly(() => target.scrollIntoView({ block: "center" }));
  }
  showUI();
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
  showUI();
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
  if (state.started) return;
  if (state.apiFailed) {
    location.reload();
    return;
  }
  state.started = true;
  body.classList.remove("is-starting");
  el.start.classList.add("leaving");
  setTimeout(() => {
    el.start.hidden = true;
  }, reducedMotion.matches ? 0 : 650);
  if (state.openPanel) closePanel(false);
  el.nextBtn.focus();
  measureBar();
  showUI();
  if (state.apiReady) begin();
  else el.startStatus.textContent = "Connecting to YouTube...";
}

function begin() {
  if (begun) return;
  begun = true;
  startCams();
  startMusic();
  render();
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
  slots.forEach(createCamPlayer);
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

let idleTimer = 0;

function showUI() {
  clearTimeout(idleTimer);
  if (state.uiHidden) {
    state.uiHidden = false;
    body.classList.remove("ui-hidden");
  }
  idleTimer = setTimeout(hideUI, state.openPanel ? PANEL_IDLE_MS : IDLE_MS);
}

function hideUI() {
  if (!state.started) return;
  if (state.openPanel) closePanel();
  clearTimeout(idleTimer);
  state.uiHidden = true;
  body.classList.add("ui-hidden");
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
  scopes.push(el.bar);
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
  const fallback = state.started && !state.openPanel ? el.nextBtn : focusablesIn(scopes)[0];
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

  // Media keys work any time once started.
  if (MEDIA_KEYS.has(key)) {
    if (!state.started) return;
    e.preventDefault();
    if (key === "MediaTrackNext") skip(1);
    else if (key === "MediaTrackPrevious") skip(-1);
    else if (key === "MediaPlay") {
      if (!musicIsPlaying()) togglePlayMusic();
    } else if (key === "MediaPause") {
      if (musicIsPlaying()) togglePlayMusic();
    } else togglePlayMusic();
    showUI();
    return;
  }

  // First press while hidden only reveals the controls.
  if (state.started && state.uiHidden) {
    if (["Shift", "Control", "Alt", "Meta"].includes(key)) return;
    e.preventDefault();
    e.stopPropagation();
    showUI();
    ensureFocus();
    return;
  }
  showUI();

  if (BACK_KEYS.has(key)) {
    if (state.openPanel) {
      e.preventDefault();
      closePanel();
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

  if (!state.started) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  const k = key.length === 1 ? key.toLowerCase() : key;
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
      toggleMute();
      break;
    case "f":
      e.preventDefault();
      toggleFullscreen();
      break;
    case " ":
      if (isInteractive(document.activeElement)) return; // let Space press the focused button
      e.preventDefault();
      togglePlayMusic();
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
    case "open-cams":
      return openPanel("cams", btn);
    case "open-music":
      return openPanel("music", btn);
    case "open-about":
      return openPanel("about", btn);
    case "close-panel":
      return closePanel();
    case "play-pause":
      return togglePlayMusic();
    case "vol-down":
      return setVolume(state.volume - VOLUME_STEP);
    case "vol-up":
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

let suppressClick = false;
let lastPointer = { x: -1, y: -1 };

function bindInput() {
  window.addEventListener("keydown", onKeyDown, true);

  document.addEventListener("click", (e) => {
    if (suppressClick) {
      suppressClick = false;
      e.preventDefault();
      e.stopPropagation();
      return;
    }
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
      if (state.started && state.uiHidden) {
        suppressClick = true;
        setTimeout(() => (suppressClick = false), 600);
        showUI();
        return;
      }
      showUI();
    },
    true,
  );

  window.addEventListener(
    "mousemove",
    (e) => {
      if (Math.abs(e.clientX - lastPointer.x) + Math.abs(e.clientY - lastPointer.y) < 4) return;
      lastPointer = { x: e.clientX, y: e.clientY };
      body.classList.remove("kbd");
      showUI();
    },
    { passive: true },
  );

  window.addEventListener("wheel", () => showUI(), { passive: true });
  window.addEventListener("resize", measureBar);
  document.addEventListener("fullscreenchange", render);
  document.addEventListener("webkitfullscreenchange", render);
}

/* ================================================================ boot */

function boot() {
  buildCamsPanel();
  buildMusicPanel();
  buildCredits();
  bindInput();
  render();
  el.startBtn.focus();
  setInterval(tick, 500);
  loadYouTubeApi();

  window.__windowSeat = {
    state,
    next: () => skip(1),
    prev: () => skip(-1),
    start,
    currentCamId: () => state.currentCamId,
    currentMixId: () => state.mixId,
  };
}

boot();
