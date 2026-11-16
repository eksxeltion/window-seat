// Window Seat Cast receiver: one full-screen cam that rotates, plus a visible music player.
// Runs on the Cast device. With ?debug=1 it runs in any browser without starting the Cast context.
import { CAMS, MIXES } from "./data.js";

const NAMESPACE = "urn:x-cast:com.windowseat";
const CAF_URL = "//www.gstatic.com/cast/sdk/libs/caf_receiver/v3/cast_receiver_framework.js";
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
    camId: s.camId,
    camTitle: cam ? cam.title : "",
    mixId: s.mixId,
    mixTitle: mix ? mix.en : "",
    pinned: s.pinned,
    interval: s.interval,
    volume: s.volume,
    failedCams: Array.from(s.failed),
  };
}

let context = null; // CastReceiverContext, only when running on a Cast device
function broadcast() {
  if (!context) return;
  quietly(() => context.sendCustomMessage(NAMESPACE, undefined, snapshot()));
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

const blackout = $("blackout");
const fadeTo = (on) => blackout.classList.toggle("on", on);

function pool() {
  return CAMS.filter((c) => c.default && !s.failed.has(c.id));
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
  if (!camReady || !camById(camId)) return;
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
  const camId = pending ? pending.camId : s.camId;
  if (camId) failCam(camId, "error " + e.data);
}

function camTick() {
  if (!camReady) return;
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
        beginLoad((list[Math.floor(Math.random() * list.length)] || CAMS[0]).id, "start");
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

function showMixTitle() {
  const mix = mixById(s.mixId);
  $("music-title").textContent = mix ? mix.en : "";
}

function playMix(id) {
  if (!mixById(id)) return;
  s.mixId = id;
  showMixTitle();
  if (musicReady) {
    quietly(() => musicPlayer.unMute());
    quietly(() => musicPlayer.setVolume(s.volume));
    quietly(() => musicPlayer.loadVideoById(id));
  }
  broadcast();
}

function nextMix() {
  const i = MIXES.findIndex((m) => m.id === s.mixId);
  playMix(MIXES[(i + 1) % MIXES.length].id);
}

function onMusicState(e) {
  if (e.data === YS.PLAYING) musicErrors = 0;
  if (e.data === YS.ENDED) {
    note("mixEnded", { mixId: s.mixId });
    nextMix();
  }
}

function onMusicError(e) {
  note("mixError", { mixId: s.mixId, code: e.data });
  musicErrors++;
  if (musicErrors >= MIXES.length) {
    // Every mix failed in a row: wait a minute before trying again.
    musicErrors = 0;
    setTimeout(nextMix, RETRY_ALL_FAILED_MS);
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
        playMix(s.mixId);
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
  if (musicReady) {
    quietly(() => musicPlayer.setVolume(s.volume));
    if (s.volume > 0) quietly(() => musicPlayer.unMute());
  }
  note("volume", { value: s.volume });
}

function handle(cmd) {
  if (!cmd || typeof cmd !== "object") return;
  note("command", cmd);
  switch (cmd.type) {
    case "next":
    case "prev": {
      const dir = cmd.type === "next" ? 1 : -1;
      const target = pick(pending ? pending.camId : s.camId, dir);
      if (target) beginLoad(target, cmd.type);
      break;
    }
    case "pin":
      s.pinned = !!cmd.value;
      if (!s.pinned) nextSwapAt = Date.now() + s.interval * 1000;
      break;
    case "mix":
      playMix(cmd.id);
      break;
    case "nextMix":
      nextMix();
      break;
    case "interval":
      s.interval = Number(cmd.value) === 30 ? 30 : 60;
      nextSwapAt = Date.now() + s.interval * 1000;
      break;
    case "volume":
      setVolume(cmd.value);
      break;
    case "cam":
      if (camById(cmd.id)) {
        s.failed.delete(cmd.id);
        if (cmd.id !== s.camId || pending) beginLoad(cmd.id, "cam");
      }
      break;
    case "stop":
      // The only way to end the receiver: an explicit command from the remote.
      if (context) quietly(() => context.stop());
      break;
    case "getState":
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

function startKeepAlive(playerManager) {
  const video = document.createElement("video");
  video.src = silentWavUrl();
  video.loop = true;
  video.muted = true;
  video.autoplay = true;
  video.playsInline = true;
  video.style.cssText = "position:fixed;width:1px;height:1px;opacity:0;pointer-events:none";
  document.body.append(video);
  quietly(() => video.play());
  if (playerManager && typeof playerManager.setMediaElement === "function") quietly(() => playerManager.setMediaElement(video));
}

function startCast() {
  const cast = window.cast;
  if (!cast || !cast.framework) {
    note("castUnavailable");
    return;
  }
  context = cast.framework.CastReceiverContext.getInstance();
  const options = new cast.framework.CastReceiverOptions();
  options.disableIdleTimeout = true;
  options.maxInactivity = 3600;
  options.customNamespaces = { [NAMESPACE]: cast.framework.system.MessageType.JSON };
  context.addCustomMessageListener(NAMESPACE, (event) => {
    let data = event.data;
    if (typeof data === "string") data = quietly(() => JSON.parse(data));
    handle(data);
  });
  // A sender leaving (phone asleep, app closed, user disconnects) never stops playback.
  // Only an explicit {type:'stop'} command calls context.stop().
  context.addEventListener(cast.framework.system.EventType.SENDER_DISCONNECTED, (event) => {
    note("senderDisconnected", { reason: event && event.reason });
  });
  context.addEventListener(cast.framework.system.EventType.SENDER_CONNECTED, () => broadcast());
  startKeepAlive(context.getPlayerManager());
  context.start(options);
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

showMixTitle();
setInterval(camTick, 1000);
startYouTube();

if (DEBUG) {
  // No Cast context in debug mode: same player logic, driven through window.__receiver.send.
  Object.defineProperty(window, "__receiver", {
    value: { get state() { return snapshot(); }, send: handle, log },
  });
} else {
  loadScript(CAF_URL).then(startCast, () => note("castUnavailable"));
}
