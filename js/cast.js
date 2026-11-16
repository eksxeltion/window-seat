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
let remote = { camId: null, camTitle: "", mixId: null, mixTitle: "", pinned: false, interval: 60, volume: 60 };
let hooks = null;
const ui = {};

function send(cmd) {
  if (session) quietly(() => session.sendMessage(RECEIVER_NAMESPACE, cmd));
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
    <ol class="remote-mixes" id="remote-mixes"></ol>
    <button class="btn btn-primary" type="button" data-cmd="stop">Stop casting</button>`;
  document.body.append(root);
  ui.root = root;
  ui.cam = $("#remote-cam");
  ui.mix = $("#remote-mix");
  ui.vol = $("#remote-vol");
  ui.pin = $("#remote-pin");
  ui.device = $("#remote-device");
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
  root.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.mix) return send({ type: "mix", id: b.dataset.mix });
    switch (b.dataset.cmd) {
      case "prev":
        return send({ type: "prev" });
      case "next":
        return send({ type: "next" });
      case "pin":
        return send({ type: "pin", value: !remote.pinned });
      case "i30":
        return send({ type: "interval", value: 30 });
      case "i60":
        return send({ type: "interval", value: 60 });
      case "vol-down":
        return send({ type: "volume", value: Math.max(0, remote.volume - 10) });
      case "vol-up":
        return send({ type: "volume", value: Math.min(100, remote.volume + 10) });
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
}

function onMessage(_ns, raw) {
  const msg = typeof raw === "string" ? quietly(() => JSON.parse(raw)) : raw;
  if (!msg || msg.type !== "state") return;
  remote = { ...remote, ...msg };
  renderRemote();
}

function onSessionStarted(s, resumed) {
  session = s;
  quietly(() => s.addMessageListener(RECEIVER_NAMESPACE, onMessage));
  ui.device.textContent = quietly(() => s.getCastDevice().friendlyName) || "TV";
  document.body.classList.add("casting");
  ui.root.hidden = false;
  hooks.onStart();
  renderRemote();
  if (resumed) {
    send({ type: "getState" }); // reconnecting to a TV that is already playing: do not override it
    return;
  }
  const local = hooks.getLocal();
  if (local.camId && CAMS.some((c) => c.id === local.camId)) send({ type: "cam", id: local.camId });
  send({ type: "pin", value: false });
  send({ type: "interval", value: local.interval });
  send({ type: "volume", value: local.volume });
  send({ type: "mix", id: local.mixId });
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
