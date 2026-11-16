// Window Seat Cast receiver: one full-screen cam that rotates, plus a visible music player.
// Runs on the Cast device. With ?debug=1 it runs in any browser without starting the Cast context.
import { CAMS, MIXES } from "./data.js";
import { applyConfig, refreshFromSheet, listInfo } from "./config.js";

export const BUILD = "2026-11-16.3"; // shown on the TV; bump on every receiver change
const NAMESPACE = "urn:x-cast:com.windowseat";
const CAF_URL = "https://www.gstatic.com/cast/sdk/libs/caf_receiver/v3/cast_receiver_framework.js";
const YT_URL = "https://www.youtube.com/iframe_api";
const YS = { ENDED: 0, PLAYING: 1, BUFFERING: 3 };

const FADE_MS = 400;
const CAM_LOAD_MS = 12000; // a new cam must reach PLAYING by then or it is skipped
const STALL_NUDGE_MS = 20000; // visible cam not PLAYING this long: nudge playVideo
const STALL_SKIP_MS = 15000; // still not PLAYING this long after the nudge: skip it
const RETRY_ALL_FAILED_MS = 60000;

const DEBUG = new URLSearchParams(location.search).get("debug") === "1";
const $ = (id) => document.getElementById(id);
const quietly = (fn) => {
  try {
    return fn();
  } catch {
    return undefined;
  }
};

/* ---------------------------------------------------------------- state and log */

const s = {
  camId: null,
  pinned: false,
  interval: 60,
  mixId: MIXES[0].id,
  volume: 60,
  muted: false,
  musicPaused: false,
  poolIds: null, // cam ids chosen on the phone (Cams filters); null = the default cams
  failed: new Set(),
};
const log = [];
function note(type, detail) {
  log.push({ t: Date.now(), type, detail });
  if (log.length > 500) log.shift();
  if (DEBUG) console.log("[receiver]", type, detail === undefined ? "" : JSON.stringify(detail));
}

const camById = (id) => CAMS.find((c) => c.id === id) || null;
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
    interval: s.interval,
    volume: s.volume,
    muted: s.muted,
    musicPaused: s.musicPaused,
    poolIds: s.poolIds,
    failedCams: Array.from(s.failed),
    list: { source: listInfo.source, cams: listInfo.cams, mixes: listInfo.mixes },
  };
}

let context = null; // CastReceiverContext, only when running on a Cast device
/* ---------------------------------------------------------------- on-TV debug strip */

// cafError is its own field: later errors (broadcast, handle, sheet) never overwrite the CAF start failure.
const dbg = { caf: "loading", cafError: "", listener: "no", senders: 0, lastIn: "none", lastOut: "none", error: "" };
let injected = false; // test hook: a mocked context stands in for CAF
let cafStarted = false; // context.start() returned without throwing (or a test injected a context)
let stripOn = new URLSearchParams(location.search).get("strip") !== "0";
function hhmmss() {
  return new Date().toTimeString().slice(0, 8);
}
function renderStrip() {
  const el = $("debug-strip");
  if (!el) return;
  el.hidden = !stripOn;
  const caf = dbg.cafError ? `CAF FAILED (${dbg.cafError})` : `CAF ${dbg.caf}`;
  const out = !cafStarted && dbg.lastOut === "none" ? "none (CAF not started)" : dbg.lastOut;
  el.textContent =
    `build ${BUILD} | list ${listInfo.source} ${listInfo.cams}/${listInfo.mixes} | ${caf} | listener ${dbg.listener} | senders ${dbg.senders} | ` +
    `last in: ${dbg.lastIn} | last reply: ${out}` + (dbg.error ? ` | error: ${dbg.error}` : "");
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
    setDbg({ lastOut: `${kind} ${hhmmss()}` });
  } catch (e) {
    setDbg({ lastOut: `${kind} FAILED ${hhmmss()}`, error: String((e && e.message) || e).slice(0, 80) });
  }
}

function broadcast() {
  reply(undefined);
}

/* Small on-TV confirmation for every phone command, so a tap is visibly received. */
let cmdTimer = 0;
function flash(text) {
  const el = $("cmd-toast");
  if (!el || !text) return;
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(cmdTimer);
  cmdTimer = setTimeout(() => el.classList.remove("show"), 1800);
}

/* ---------------------------------------------------------------- cam */

let camPlayer = null;
let camReady = false;
let token = 0; // bumps on every load so stale events are ignored
let pending = null; // { camId, startedAt, loading }
let nextSwapAt = 0;
let stall = null; // { at, nudged } while the visible cam is not PLAYING
let retryTimer = 0;
let rotateReason = "start";
let wantedCam = null; // camera requested before the YouTube player was ready
let awaitingRetry = false; // all cams failed: camTick stays idle until the retry timer fires

const blackout = $("blackout");
const fadeTo = (on) => blackout.classList.toggle("on", on);

function pool() {
  const chosen = s.poolIds && s.poolIds.length ? CAMS.filter((c) => s.poolIds.includes(c.id)) : CAMS.filter((c) => c.default);
  return chosen.filter((c) => !s.failed.has(c.id));
}

/** Next usable cam after fromId in the given direction (1 or -1). */
function pick(fromId, dir) {
  const list = pool();
  if (!list.length) return null;
  const i = list.findIndex((c) => c.id === fromId);
  if (i === -1) return (dir > 0 ? list[0] : list[list.length - 1]).id;
  return list[(i + dir + list.length) % list.length].id;
}

function beginLoad(camId, reason = "rotate") {
  if (!camById(camId)) return;
  if (!camReady) {
    wantedCam = camId; // YouTube not ready yet: onReady loads this instead of a random cam
    return;
  }
  wantedCam = null;
  awaitingRetry = false;
  clearTimeout(retryTimer);
  const my = ++token;
  rotateReason = reason;
  pending = { camId, startedAt: Date.now(), loading: false };
  stall = null;
  fadeTo(true);
  setTimeout(() => {
    if (my !== token) return;
    pending.loading = true;
    pending.startedAt = Date.now();
    quietly(() => camPlayer.mute());
    quietly(() => camPlayer.loadVideoById(camId));
  }, s.camId ? FADE_MS : 0);
}

function commitPending() {
  const cam = camById(pending.camId);
  if (!cam) { // left the list while loading
    pending = null;
    return;
  }
  s.camId = cam.id;
  pending = null;
  stall = null;
  nextSwapAt = Date.now() + s.interval * 1000;
  $("cap-title").textContent = cam.title;
  $("cap-meta").textContent = `${cam.place} · ${cam.channel}`;
  fadeTo(false);
  note("rotate", { camId: cam.id, reason: rotateReason });
  broadcast();
}

function failCam(camId, why) {
  note("camError", { camId, why });
  s.failed.add(camId);
  pending = null;
  stall = null;
  const next = pick(camId, 1);
  if (next) {
    beginLoad(next, "skip");
  } else {
    // Every default cam failed: black screen, forget the failures and try again in a minute.
    note("allFailed");
    s.failed.clear();
    token++;
    awaitingRetry = true;
    fadeTo(true);
    retryTimer = setTimeout(() => beginLoad(pick(s.camId, 1) || CAMS[0].id, "retry"), RETRY_ALL_FAILED_MS);
  }
  broadcast();
}

function onCamState(e) {
  const code = e.data;
  if (pending && pending.loading && code === YS.PLAYING) {
    const vid = quietly(() => camPlayer.getVideoData().video_id);
    if (!vid || vid === pending.camId) commitPending();
  }
}

function onCamError(e) {
  // 2, 5, 100, 101, 150, 153 and anything else: the cam cannot play, skip it.
  // Ignore late errors while waiting out the all-failed backoff.
  if (awaitingRetry) return;
  const camId = pending ? pending.camId : s.camId;
  // Only fail the cam the player actually reports, so a late error from a replaced video is ignored.
  const reported = quietly(() => camPlayer.getVideoData().video_id);
  if (reported && camId && reported !== camId) return;
  if (camId) failCam(camId, "error " + e.data);
}

function camTick() {
  if (!camReady || awaitingRetry) return;
  const now = Date.now();
  if (pending) {
    if (pending.loading && now - pending.startedAt > CAM_LOAD_MS) failCam(pending.camId, "load timeout");
    return;
  }
  if (!s.camId) return;
  const live = quietly(() => camPlayer.getPlayerState());
  if (live === YS.PLAYING) {
    stall = null;
  } else if (live != null) {
    if (!stall) stall = { at: now, nudged: false };
    if (!stall.nudged && now - stall.at > STALL_NUDGE_MS) {
      stall = { at: now, nudged: true };
      note("stall", { camId: s.camId, action: "nudge" });
      quietly(() => camPlayer.playVideo());
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
        const list = pool();
        const first = wantedCam && camById(wantedCam) ? wantedCam : (list[Math.floor(Math.random() * list.length)] || CAMS[0]).id;
        beginLoad(first, wantedCam ? "cam" : "start");
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
  s.musicPaused = false;
  if (musicReady) {
    applyAudio();
    quietly(() => musicPlayer.loadVideoById(id));
  }
  broadcast();
}

function nextMix() {
  const i = MIXES.findIndex((m) => m.id === s.mixId);
  playMix(MIXES[(i + 1) % MIXES.length].id);
}

function onMusicState(e) {
  if (e.data === YS.PLAYING) {
    musicErrors = 0;
    clearTimeout(musicRetryTimer);
  }
  if (e.data === YS.ENDED) {
    note("mixEnded", { mixId: s.mixId });
    nextMix();
  }
}

function onMusicError(e) {
  note("mixError", { mixId: s.mixId, code: e.data });
  if (s.musicPaused) return; // paused: Play restarts the mix
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
        if (s.musicPaused) {
          // Paused from the phone before the player was ready: load the mix without starting it.
          applyAudio();
          quietly(() => musicPlayer.cueVideoById(s.mixId));
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

/** Accepts a command object, or JSON text of one (from any sender or namespace type). */
export function parseCommand(raw) {
  let cmd = raw;
  if (typeof cmd === "string") cmd = quietly(() => JSON.parse(cmd));
  if (typeof cmd === "string") cmd = quietly(() => JSON.parse(cmd)); // double-encoded JSON text
  return cmd && typeof cmd === "object" && typeof cmd.type === "string" ? cmd : null;
}

function handle(raw) {
  const cmd = parseCommand(raw);
  if (!cmd) return;
  note("command", cmd);
  const quiet = cmd.silent === true; // setup messages sent on connect: no on-TV flash
  switch (cmd.type) {
    case "next":
    case "prev": {
      const dir = cmd.type === "next" ? 1 : -1;
      const target = pick(pending ? pending.camId : s.camId, dir);
      if (target) beginLoad(target, cmd.type);
      if (!quiet) flash(cmd.type === "next" ? "Next cam" : "Previous cam");
      break;
    }
    case "pin":
      s.pinned = !!cmd.value;
      if (!s.pinned) nextSwapAt = Date.now() + s.interval * 1000;
      if (!quiet) flash(s.pinned ? "Pinned" : "Rotation on");
      break;
    case "mix":
      playMix(cmd.id);
      if (!quiet) flash(`Music: ${(mixById(cmd.id) || {}).en || ""}`);
      break;
    case "nextMix":
    case "prevMix":
      if (cmd.type === "nextMix") nextMix();
      else prevMix();
      if (!quiet) flash(`Music: ${(mixById(s.mixId) || {}).en || ""}`);
      break;
    case "interval":
      s.interval = Number(cmd.value) === 30 ? 30 : 60;
      nextSwapAt = Date.now() + s.interval * 1000;
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
    case "playMusic":
      s.musicPaused = !cmd.value;
      if (s.musicPaused) {
        clearTimeout(musicRetryTimer); // a paused player must not be restarted by an error retry
        if (musicReady) quietly(() => musicPlayer.pauseVideo());
      } else if (musicReady) {
        const st = quietly(() => musicPlayer.getPlayerState());
        if (st === 2 || st === 5) quietly(() => musicPlayer.playVideo());
        else playMix(s.mixId); // nothing loaded (or it failed): start the mix again
      }
      if (!quiet) flash(s.musicPaused ? "Music paused" : "Music playing");
      break;
    case "cams": {
      const ids = Array.isArray(cmd.ids) ? cmd.ids.filter((id) => camById(id)) : [];
      s.poolIds = ids.length ? ids : null;
      if (!quiet) flash(`${pool().length} cams in rotation`);
      break;
    }
    case "cam":
      if (camById(cmd.id)) {
        s.failed.delete(cmd.id);
        if (cmd.id !== s.camId || pending) beginLoad(cmd.id, "cam");
        if (!quiet) flash(camById(cmd.id).title);
      }
      break;
    case "stop":
      // The only way to end the receiver: an explicit command from the remote.
      if (context) quietly(() => context.stop());
      break;
    case "getState":
      break;
    case "config":
      // The phone fetched the sheet and passes the lists along (works even if the TV cannot reach Google).
      if (applyConfig(cmd, "phone")) afterListChange();
      break;
    case "debug":
      // Toggle the on-TV debug strip from the phone.
      stripOn = !!cmd.value;
      renderStrip();
      break;
    default:
      return;
  }
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
  const raw = event && event.data;
  const cmd = parseCommand(raw);
  setDbg({ lastIn: `${cmd ? cmd.type : "unreadable " + typeof raw} ${hhmmss()}` });
  try {
    handle(cmd);
  } catch (e) {
    setDbg({ error: `handle: ${String((e && e.message) || e).slice(0, 80)}` });
  }
  // Also answer the sender directly, in case a broadcast does not reach it.
  if (event && event.senderId) reply(event.senderId);
}

function startCast() {
  const cast = window.cast;
  if (!cast || !cast.framework) {
    note("castUnavailable");
    setDbg({ caf: "missing" });
    return;
  }
  try {
    context = cast.framework.CastReceiverContext.getInstance();
    const options = new cast.framework.CastReceiverOptions();
    options.mediaElement = $("keepalive");
    options.disableIdleTimeout = true;
    options.maxInactivity = 3600;
    // STRING: CAF hands us the raw text and we parse it ourselves (parseCommand accepts objects too),
    // so a JSON parse quirk in the framework can never drop a command.
    options.customNamespaces = { [NAMESPACE]: cast.framework.system.MessageType.STRING };
    // NOTE: the custom message listener goes on AFTER start() (see below). In the real framework a listener
    // added before start() creates the namespace as JSON, and start() then throws "Invalid messageType".
    // A sender leaving (phone asleep, app closed, user disconnects) never stops playback.
    // Only an explicit {type:'stop'} command calls context.stop().
    context.addEventListener(cast.framework.system.EventType.SENDER_DISCONNECTED, (event) => {
      note("senderDisconnected", { reason: event && event.reason });
      setDbg({ senders: quietly(() => context.getSenders().length) ?? dbg.senders });
    });
    context.addEventListener(cast.framework.system.EventType.SENDER_CONNECTED, () => {
      setDbg({ senders: quietly(() => context.getSenders().length) ?? dbg.senders + 1 });
      broadcast();
    });
    context.addEventListener(cast.framework.system.EventType.READY, () => setDbg({ caf: "ready", listener: "ready" }));
    // Keep-alive media element is optional: a failure here must never prevent start().
    try {
      const keepAlive = prepareKeepAlive();
      const playerManager = context.getPlayerManager();
      if (typeof playerManager.setMediaElement === "function") playerManager.setMediaElement(keepAlive);
    } catch (e) {
      setDbg({ error: `keepalive: ${String((e && e.message) || e).slice(0, 60)}` });
    }
    setDbg({ caf: "starting" });
    context.start(options);
    cafStarted = true;
    // Same synchronous block as start(): no message can arrive before the listener exists.
    context.addCustomMessageListener(NAMESPACE, onCustomMessage);
    setDbg({ listener: dbg.caf === "ready" ? "ready" : "registered" });
    if (dbg.caf === "starting") setDbg({ caf: "started (waiting for ready)" });
    setTimeout(() => {
      if (dbg.caf !== "ready") setDbg({ caf: "started, no READY after 10s" });
    }, 10000);
  } catch (e) {
    const name = (e && e.name) || "Error";
    setDbg({ caf: "FAILED", cafError: `${name}: ${String((e && e.message) || e).slice(0, 80)}` });
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
  loadScript(CAF_URL).then(startCast, () => {
    note("castUnavailable");
    setDbg({ caf: "load failed" });
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
    if (s.musicPaused) {
      // Paused stays paused: just cue the replacement.
      clearTimeout(musicRetryTimer);
      s.mixId = MIXES[0].id;
      if (musicReady) quietly(() => musicPlayer.cueVideoById(s.mixId));
      showMixTitle();
    } else playMix(MIXES[0].id);
  } else showMixTitle();
  const gone = pending ? !camById(pending.camId) : s.camId && !camById(s.camId);
  if (gone) {
    token++; // drop callbacks of a load for a cam that no longer exists
    pending = null;
    let next = pick(null, 1);
    if (!next) {
      // Only failed cams are left: forget the failures and try again now rather than stay black.
      s.failed.clear();
      next = pick(null, 1) || CAMS[0].id;
    }
    beginLoad(next, "list");
  } else if (s.camId && !pending) {
    const cam = camById(s.camId); // same cam, maybe renamed in the sheet
    $("cap-title").textContent = cam.title;
    $("cap-meta").textContent = `${cam.place} · ${cam.channel}`;
  }
  renderStrip();
  broadcast();
}

// The TV fetches the sheet itself too; a failure just keeps the saved copy or the built-in list.
refreshFromSheet().then((r) => {
  if (r.ok) {
    if (r.changed) afterListChange();
    else renderStrip();
  } else setDbg({ error: `sheet: ${String(r.error).slice(0, 60)}` });
});

showMixTitle();
setInterval(camTick, 1000);
try {
  startYouTube();
} catch (e) {
  setDbg({ error: `youtube: ${String((e && e.message) || e).slice(0, 60)}` });
}

if (DEBUG) {
  // No Cast context in debug mode: same player logic, driven through window.__receiver.send.
  Object.defineProperty(window, "__receiver", {
    value: {
      get state() { return snapshot(); },
      send: handle,
      // Test hooks: the real Cast message path with a mocked context.
      message: (event) => onCustomMessage(event),
      useContext: (ctx) => {
        context = ctx;
        cafStarted = true;
        injected = true;
      },
      log,
      // Test probes for the soak run.
      music: () => ({ state: quietly(() => musicPlayer.getPlayerState()), time: quietly(() => musicPlayer.getCurrentTime()), vol: quietly(() => musicPlayer.getVolume()), muted: quietly(() => musicPlayer.isMuted()) }),
      cam: () => ({ state: quietly(() => camPlayer.getPlayerState()) }),
    },
  });
}
