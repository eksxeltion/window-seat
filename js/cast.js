// Sender side of Google Cast. The phone page starts a session, then becomes a remote for receiver.html.
import { CAST_APP_ID, RECEIVER_NAMESPACE } from "./cast-config.js";
import { CAMS, MIXES } from "./data.js";

const SDK_URL = "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1";
export const castConfigured = CAST_APP_ID !== "REPLACE_WITH_APP_ID";

const $ = (sel) => document.querySelector(sel);
const quietly = (fn) => {
  try {
    return fn();
  } catch {
    return undefined;
  }
};

let session = null;
let remote = { camId: null, camTitle: "", mixId: null, mixTitle: "", pinned: false, interval: 60, volume: 60, muted: false, musicPaused: false };
let lastReplyAt = 0; // last state message from the TV
let replyTimer = 0;
let warnedNoReply = false;
let hooks = null;
const ui = {};

// Stage 2 failures: the TV app launched, but a command could not be delivered to it.
const SEND_ERRORS = {
  timeout: "the TV app did not take the message in time (its Window Seat channel is not answering)",
  channel_error: "the connection to the TV dropped",
  session_error: "the Cast session ended",
  invalid_parameter: "the message was rejected as invalid",
  api_not_initialized: "Cast is still starting",
};

/* What the session says about the TV app: right app id, and does it declare our channel? */
function channelCheck() {
  const obj = quietly(() => session.getSessionObj());
  const appId = obj && obj.appId;
  const names = ((obj && obj.namespaces) || []).map((n) => n.name);
  if (appId && appId !== CAST_APP_ID) return `Connected to a different TV app (${appId}), not Window Seat (${CAST_APP_ID}).`;
  if (obj && !names.includes(RECEIVER_NAMESPACE)) return "The TV app is running but has not opened the Window Seat channel yet (receiver not started, or an old version).";
  return "";
}

/* Send one command to the TV over the custom namespace. Errors and missing replies are shown, never swallowed. */
function send(cmd, opts = {}) {
  if (!session) {
    if (!opts.silent) say("Not connected to the TV. Tap Cast again.");
    return;
  }
  const sentAt = Date.now();
  let p;
  try {
    // JSON text: the receiver declares this namespace as JSON and parses it.
    p = session.sendMessage(RECEIVER_NAMESPACE, JSON.stringify(cmd));
  } catch (e) {
    say(`Could not send to the TV: ${(e && e.message) || e}`);
    return;
  }
  const fail = (raw) => {
    const code = (raw && raw.code) || raw;
    const hint = channelCheck();
    say(`Send failed (${code}): ${SEND_ERRORS[code] || code}.${hint ? " " + hint : ""}`);
    setRemoteStatus(`Send failed: ${code}${hint ? ". " + hint : ""}`);
  };
  Promise.resolve(p).then((code) => code && fail(code), fail);
  if (opts.silent) return;
  clearTimeout(replyTimer);
  replyTimer = setTimeout(() => {
    if (lastReplyAt >= sentAt) return;
    const hint = channelCheck();
    setRemoteStatus(`Sent, but no reply from the TV app after 4s.${hint ? " " + hint : ""}`);
    if (!warnedNoReply) {
      warnedNoReply = true;
      say(`Sent, but the TV app has not replied after 4s.${hint ? " " + hint : " Check the green line at the bottom of the TV."}`);
    }
  }, 4000);
}

function setRemoteStatus(text) {
  if (ui.status) ui.status.textContent = text;
}

/* Update the phone UI right away; the TV's state reply then confirms or corrects it. */
function act(cmd, patch) {
  if (patch) {
    remote = { ...remote, ...patch };
    renderRemote();
  }
  send(cmd);
}

function buildRemote() {
  const root = document.createElement("section");
  root.id = "remote";
  root.hidden = true;
  root.innerHTML = `
    <h2>Casting to <span id="remote-device"></span></h2>
    <p class="remote-now"><span class="remote-label">Cam</span> <strong id="remote-cam">...</strong></p>
    <p class="remote-now"><span class="remote-label">Music</span> <strong id="remote-mix">...</strong></p>
    <div class="remote-row">
      <button class="btn" type="button" data-cmd="prev">Prev cam</button>
      <button class="btn" type="button" data-cmd="next">Next cam</button>
      <button class="btn" type="button" data-cmd="pin" id="remote-pin" aria-pressed="false">Pin</button>
    </div>
    <div class="remote-row">
      <button class="btn" type="button" data-cmd="i30" aria-pressed="false">Every 30s</button>
      <button class="btn" type="button" data-cmd="i60" aria-pressed="false">Every 60s</button>
    </div>
    <div class="remote-row">
      <button class="btn" type="button" data-cmd="vol-down">Volume -</button>
      <output id="remote-vol">60</output>
      <button class="btn" type="button" data-cmd="vol-up">Volume +</button>
    </div>
    <div class="remote-row">
      <button class="btn" type="button" data-cmd="mute" id="remote-mute" aria-pressed="false">Mute</button>
      <button class="btn" type="button" data-cmd="play" id="remote-play">Pause music</button>
      <button class="btn" type="button" data-cmd="next-mix">Next mix</button>
    </div>
    <p class="remote-status" id="remote-status" role="status">Waiting for the TV...</p>
    <h3 class="remote-head">Music</h3>
    <ol class="remote-mixes" id="remote-mixes"></ol>
    <h3 class="remote-head">Show a cam now</h3>
    <ol class="remote-mixes" id="remote-cams"></ol>
    <button class="btn btn-primary" type="button" data-cmd="stop">Stop casting</button>`;
  document.body.append(root);
  ui.root = root;
  ui.cam = $("#remote-cam");
  ui.mix = $("#remote-mix");
  ui.vol = $("#remote-vol");
  ui.pin = $("#remote-pin");
  ui.device = $("#remote-device");
  ui.mute = $("#remote-mute");
  ui.play = $("#remote-play");
  ui.status = $("#remote-status");
  const list = $("#remote-mixes");
  for (const m of MIXES) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.dataset.mix = m.id;
    b.textContent = m.en;
    li.append(b);
    list.append(li);
  }
  const camList = $("#remote-cams");
  for (const c of CAMS) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.dataset.cam = c.id;
    b.textContent = `${c.title} (${c.place})`;
    li.append(b);
    camList.append(li);
  }
  root.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.mix) {
      const m = MIXES.find((x) => x.id === b.dataset.mix);
      return act({ type: "mix", id: b.dataset.mix }, { mixId: b.dataset.mix, mixTitle: m ? m.en : "", musicPaused: false });
    }
    if (b.dataset.cam) {
      const c = CAMS.find((x) => x.id === b.dataset.cam);
      return act({ type: "cam", id: b.dataset.cam }, { camId: b.dataset.cam, camTitle: c ? c.title : "" });
    }
    switch (b.dataset.cmd) {
      case "prev":
        return act({ type: "prev" });
      case "next":
        return act({ type: "next" });
      case "pin":
        return act({ type: "pin", value: !remote.pinned }, { pinned: !remote.pinned });
      case "i30":
        return act({ type: "interval", value: 30 }, { interval: 30 });
      case "i60":
        return act({ type: "interval", value: 60 }, { interval: 60 });
      case "vol-down": {
        const v = Math.max(0, remote.volume - 10);
        return act({ type: "volume", value: v }, { volume: v, muted: v === 0 ? remote.muted : false });
      }
      case "vol-up": {
        const v = Math.min(100, remote.volume + 10);
        return act({ type: "volume", value: v }, { volume: v, muted: false });
      }
      case "mute":
        return act({ type: "mute", value: !remote.muted }, { muted: !remote.muted });
      case "play":
        return act({ type: "playMusic", value: remote.musicPaused }, { musicPaused: !remote.musicPaused });
      case "next-mix":
        return act({ type: "nextMix" });
      case "stop":
        return quietly(() => cast.framework.CastContext.getInstance().endCurrentSession(true));
      default:
    }
  });
}

function renderRemote() {
  ui.cam.textContent = remote.camTitle || "Tuning in...";
  ui.mix.textContent = remote.mixTitle || "...";
  ui.vol.textContent = remote.volume;
  ui.pin.setAttribute("aria-pressed", String(remote.pinned));
  ui.root.querySelector('[data-cmd="i30"]').setAttribute("aria-pressed", String(remote.interval === 30));
  ui.root.querySelector('[data-cmd="i60"]').setAttribute("aria-pressed", String(remote.interval !== 30));
  ui.root.querySelectorAll("[data-mix]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mix === remote.mixId)));
  ui.root.querySelectorAll("[data-cam]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.cam === remote.camId)));
  ui.mute.setAttribute("aria-pressed", String(!!remote.muted));
  ui.mute.textContent = remote.muted ? "Unmute" : "Mute";
  ui.play.textContent = remote.musicPaused ? "Play music" : "Pause music";
}

function onMessage(_ns, raw) {
  const msg = typeof raw === "string" ? quietly(() => JSON.parse(raw)) : raw;
  if (!msg || msg.type !== "state") return;
  lastReplyAt = Date.now();
  warnedNoReply = false;
  remote = { ...remote, ...msg };
  setRemoteStatus(`Connected. The TV confirms each tap.${msg.build ? ` (TV build ${msg.build})` : ""}`);
  renderRemote();
}

function onSessionStarted(s, resumed) {
  session = s;
  quietly(() => s.addMessageListener(RECEIVER_NAMESPACE, onMessage));
  ui.device.textContent = quietly(() => s.getCastDevice().friendlyName) || "TV";
  document.body.classList.add("casting");
  ui.root.hidden = false;
  lastReplyAt = 0;
  warnedNoReply = false;
  const hint = channelCheck();
  setRemoteStatus(hint || "Waiting for the TV app to reply...");
  hooks.onStart();
  renderRemote();
  if (resumed) {
    send({ type: "getState" }); // reconnecting to a TV that is already playing: do not override it
    return;
  }
  const local = hooks.getLocal();
  const mix = MIXES.find((m) => m.id === local.mixId);
  remote = { ...remote, interval: local.interval, volume: local.volume, muted: !!local.muted, mixId: local.mixId, mixTitle: mix ? mix.en : "", pinned: false, musicPaused: false };
  renderRemote();
  const quiet = { silent: true };
  if (Array.isArray(local.camIds)) send({ type: "cams", ids: local.camIds, silent: true }, quiet);
  if (local.camId && CAMS.some((c) => c.id === local.camId)) send({ type: "cam", id: local.camId, silent: true }, quiet);
  send({ type: "pin", value: false, silent: true }, quiet);
  send({ type: "interval", value: local.interval, silent: true }, quiet);
  send({ type: "volume", value: local.volume, silent: true }, quiet);
  send({ type: "mute", value: !!local.muted, silent: true }, quiet);
  send({ type: "mix", id: local.mixId, silent: true }, quiet);
  send({ type: "getState" });
}

function onSessionEnded() {
  if (!session) return;
  session = null;
  document.body.classList.remove("casting");
  ui.root.hidden = true;
  hooks.onEnd();
}

/* ---- visible status, so a tap never does "nothing" ---- */
let castReady = false; // SDK loaded and Cast available
let castFailed = false; // SDK missing or Cast not available in this browser

const STATE_TEXT = {
  NO_DEVICES_AVAILABLE: "Cast: no devices found",
  NOT_CONNECTED: "Cast: TV found, not connected",
  CONNECTING: "Cast: connecting...",
  CONNECTED: "Cast: connected",
};
const ERROR_TEXT = {
  cancel: "Cast: picker closed (cancel)",
  receiver_unavailable: "Cast: no TV found (receiver_unavailable)",
  session_error: "Cast: TV found, but the receiver failed to load (session_error)",
  timeout: "Cast: TV found, but the receiver timed out (timeout)",
  channel_error: "Cast: connection to the TV failed (channel_error)",
  extension_missing: "Cast not available in this browser (extension_missing)",
  api_not_initialized: "Cast: still starting, try again (api_not_initialized)",
};
const UNAVAILABLE = "Cast not available in this browser";

function setStatus(text) {
  document.querySelectorAll(".cast-status").forEach((el) => {
    el.textContent = text;
    el.hidden = !text;
  });
  document.querySelectorAll("[data-cast]").forEach((b) => (b.title = text));
}

function say(text) {
  setStatus(text);
  if (hooks && hooks.toast) hooks.toast(text);
}

function markUnavailable() {
  castFailed = true;
  setStatus(UNAVAILABLE);
}

function onCastTap() {
  if (castFailed) return say(UNAVAILABLE);
  if (!castReady) return say("Cast: still loading, try again in a moment");
  const ctx = cast.framework.CastContext.getInstance();
  if (session) return say(STATE_TEXT.CONNECTED + ". Use Stop casting to end.");
  say("Cast: looking for your TV...");
  let p;
  try {
    p = ctx.requestSession();
  } catch (e) {
    return say(`Cast error: ${(e && e.message) || e}`);
  }
  Promise.resolve(p).then(
    (code) => {
      if (code) say(ERROR_TEXT[code] || `Cast: ${code}`);
      else say(STATE_TEXT[ctx.getCastState()] || "Cast: connected");
    },
    (code) => {
      const c = (code && code.code) || code;
      say(ERROR_TEXT[c] || `Cast: ${c}`);
    },
  );
}

function initFramework() {
  const ctx = cast.framework.CastContext.getInstance();
  ctx.setOptions({
    receiverApplicationId: CAST_APP_ID,
    autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED,
  });
  buildRemote();
  castReady = true;
  setStatus(STATE_TEXT[ctx.getCastState()] || "");
  ctx.addEventListener(cast.framework.CastContextEventType.CAST_STATE_CHANGED, (e) => {
    setStatus(STATE_TEXT[e.castState] || `Cast: ${e.castState}`);
  });
  const T = cast.framework.SessionState;
  ctx.addEventListener(cast.framework.CastContextEventType.SESSION_STATE_CHANGED, (e) => {
    if (e.sessionState === T.SESSION_STARTED) onSessionStarted(e.session, false);
    else if (e.sessionState === T.SESSION_RESUMED) onSessionStarted(e.session, true);
    else if (e.sessionState === T.SESSION_ENDED) onSessionEnded();
  });
}

/** hooks: { getLocal() -> {camId, mixId, interval, volume}, onStart(), onEnd() } */
export function initCast(h) {
  if (!castConfigured) return; // placeholder App ID: no SDK, no button, nothing to break
  hooks = h;
  // Must exist before the SDK script runs.
  // Show the buttons right away; a tap always explains what happened.
  document.querySelectorAll("[data-cast]").forEach((b) => {
    b.hidden = false;
    b.addEventListener("click", onCastTap);
  });
  window.__onGCastApiAvailable = (available) => {
    if (!available) return markUnavailable();
    try {
      initFramework();
    } catch (e) {
      markUnavailable();
    }
  };
  const script = document.createElement("script");
  script.src = SDK_URL;
  script.async = true;
  script.onerror = markUnavailable; // offline or blocked
  document.head.append(script);
}
