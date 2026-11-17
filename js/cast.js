// Sender side of Google Cast. The phone page starts a session, then becomes a remote for receiver.html.
import { CAST_APP_ID, RECEIVER_NAMESPACE } from "./cast-config.js";
import { CAMS, MIXES } from "./data.js";
import { listInfo, currentConfig } from "./config.js";
import { BUILD } from "./build.js";
import { logEvent, isDebugOn, refreshDebug } from "./debug.js";

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
let remote = { camId: null, camTitle: "", mixId: null, mixTitle: "", pinned: false, interval: 60, volume: 60, muted: false, paused: false, shuffle: true, pauseWarning: false };
let lastReplyAt = 0; // last state message from the TV
let replyTimer = 0;
let warnedNoReply = false;
let hooks = null;
let remoteOn = false; // remote mode is on (a session, or a connection in progress)
const dbg = { last: "-", sessionState: "-", tvBuild: "?", lastErr: "" };
// Debug mode only (observation, no effect on behavior): message counts and the TV's own diagnostics from its state reply.
const io = { tx: 0, rx: 0 };
let tvDebug = {};
const ui = {};

// Pause timeout while casting (the TV owns the timer; see receiver.js). Copy from docs/design.md.
const PAUSE_HAIRLINE = "Paused. The TV rests after 30 minutes paused.";
const PAUSE_WARNING = "The TV will go back to its home screen in 5 minutes. Tap play to keep watching.";
const PAUSE_ENDED = "Paused for 30 minutes, so the TV went back to its home screen. Cast again whenever you like.";
let pauseEnded = false; // the TV said it is ending the session after 30 minutes paused
let stopRequested = false; // the user tapped Stop casting on this phone

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
  if (obj && !names.includes(RECEIVER_NAMESPACE)) return "The TV app is running but has not opened the Window Seat channel yet (receiver not started, or an old version). The phone's Chrome does not list the Window Seat channel for this session yet.";
  return "";
}

/** Namespace view of the live session: ok, plus the names without the urn:x-cast: prefix. */
function nsInfo() {
  const obj = quietly(() => session.getSessionObj());
  if (!obj) return { ok: false, names: [] };
  const names = (obj.namespaces || []).map((n) => String((n && n.name) || n));
  return { ok: names.includes(RECEIVER_NAMESPACE), names: names.map((n) => n.replace(/^urn:x-cast:/, "")) };
}

/** Compact text of a send rejection: code plus description/details when present. */
function errText(raw) {
  if (raw == null) return "";
  if (typeof raw !== "object") return String(raw).slice(0, 80);
  const parts = [raw.code, raw.description, raw.details].filter((x) => x != null && x !== "").map((x) => (typeof x === "object" ? quietly(() => JSON.stringify(x)) : String(x)));
  return (parts.length ? parts.join(" / ") : quietly(() => JSON.stringify(raw)) || "?").slice(0, 120);
}

/* Send one command to the TV over the custom namespace. Errors and missing replies are shown, never swallowed. */
const lastSendOf = {}; // command type -> generation, so a late retry never overrides a newer command
let sendGen = 0;

function send(cmd, opts = {}) {
  if (!session) {
    if (!opts.silent) say(remoteOn ? "Still connecting to the TV..." : "Not connected to the TV. Tap Cast again.");
    return;
  }
  const sentAt = Date.now();
  const gen = opts.gen || ++sendGen;
  if (!opts.retried) lastSendOf[cmd.type] = gen;
  const target = session;
  let p;
  try {
    // JSON text: the receiver declares this namespace as STRING and parses it itself.
    p = session.sendMessage(RECEIVER_NAMESPACE, JSON.stringify(cmd));
  } catch (e) {
    logEvent(`tx ${cmd.type} threw: ${errText((e && e.message) || e)}`);
    say(`Could not send to the TV: ${(e && e.message) || e}`);
    return;
  }
  io.tx++;
  const fail = (raw) => {
    const code = (raw && raw.code) || raw;
    // Only state-setting commands are safe to resend (a resent "next" or "mix" would act twice).
    const repeatable = ["pin", "interval", "volume", "mute", "pause", "playMusic", "cams", "getState", "shuffle", "keepPaused"].includes(cmd.type);
    if (code === "timeout" && repeatable && !opts.retried) {
      // The channel can lag right after connecting: try once more before reporting,
      // but only on the same session and only if no newer command of this type went out.
      setTimeout(() => {
        if (session === target && lastSendOf[cmd.type] === gen) send(cmd, { ...opts, retried: true, gen });
      }, 1000);
      return;
    }
    dbg.last = `send ${code}`;
    dbg.lastErr = errText(raw);
    renderDebug();
    const hint = channelCheck();
    say(`Send failed (${code}): ${SEND_ERRORS[code] || code}.${hint ? " " + hint : ""}`);
    setRemoteStatus(`Send failed: ${code}${hint ? ". " + hint : ""}`);
  };
  Promise.resolve(p).then((code) => code && fail(code), fail);
  // Debug log only: a separate observer on the same promise, registered after the handler above so it never changes it.
  const tag = `tx ${cmd.type}${opts.retried ? " (retry)" : ""}`;
  Promise.resolve(p).then(
    (code) => logEvent(code ? `${tag} FAILED ${errText(code)}` : `${tag} ok`),
    (raw) => logEvent(`${tag} FAILED ${errText(raw) || "?"}`),
  );
  if (opts.silent) return;
  clearTimeout(replyTimer);
  replyTimer = setTimeout(() => {
    if (lastReplyAt >= sentAt) return;
    const hint = channelCheck();
    setRemoteStatus(`Sent, but no reply from the TV app after 4s.${hint ? " " + hint : ""}`);
    if (!warnedNoReply) {
      warnedNoReply = true;
      say(`Sent, but the TV app has not replied after 4s.${hint ? " " + hint : " Turn on Debug mode in About to see Cast details on the TV."}`);
    }
  }, 4000);
}

function setRemoteStatus(text) {
  if (ui.status) ui.status.textContent = text;
}

/* Update the phone UI right away; the TV's state reply then confirms or corrects it. */
function act(cmd, patch) {
  // Any command restarts the TV's pause timer (or resumes the feed), so the warning goes until the TV says otherwise.
  if (remote.pauseWarning && cmd.type !== "getState") patch = { ...patch, pauseWarning: false };
  if (patch) {
    remote = { ...remote, ...patch };
    renderRemote();
  }
  send(cmd);
}

/* One remote command by name: the panel buttons and the phone's own controls and keys all go through here. */
function runCmd(name) {
  switch (name) {
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
    case "interval":
      return runCmd(remote.interval === 30 ? "i60" : "i30");
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
    // Pause is the whole feed on the TV: the cam and the music. The receiver still accepts the older playMusic.
    case "play-on":
      return act({ type: "pause", value: false }, { paused: false });
    case "play-off":
      return act({ type: "pause", value: true }, { paused: true });
    case "play":
      return act({ type: "pause", value: !remote.paused }, { paused: !remote.paused });
    case "next-mix":
      return act({ type: "nextMix" });
    case "keep-paused":
      // Restart the TV's full 30 minute timer without resuming.
      return act({ type: "keepPaused" }, { pauseWarning: false });
    case "stop":
      stopRequested = true;
      return quietly(() => cast.framework.CastContext.getInstance().endCurrentSession(true));
    default:
  }
}

function buildRemote() {
  const root = document.createElement("section");
  root.id = "remote";
  root.hidden = true;
  root.innerHTML = `
    <h2 class="remote-pill"><svg class="icon" aria-hidden="true"><use href="#i-cast-connected"></use></svg><span>Casting to <span id="remote-device"></span></span></h2>
    <div class="remote-card">
      <p class="remote-now"><span class="remote-label">Cam</span> <strong id="remote-cam">...</strong></p>
      <p class="remote-now"><span class="remote-label">Music</span> <strong id="remote-mix">...</strong></p>
      <p class="remote-hairline" id="remote-hairline" hidden>${PAUSE_HAIRLINE}</p>
    </div>
    <div class="remote-pause-warn" id="remote-pause-warn" role="alert" hidden>
      <p id="remote-pause-warn-text">${PAUSE_WARNING}</p>
      <button class="btn" type="button" data-cmd="keep-paused" id="remote-keep-paused">Keep paused</button>
    </div>
    <div class="remote-row">
      <button class="btn" type="button" data-cmd="prev">Prev cam</button>
      <button class="btn" type="button" data-cmd="next">Next cam</button>
      <button class="btn" type="button" data-cmd="pin" id="remote-pin" aria-pressed="false">Pin cam</button>
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
      <button class="btn" type="button" data-cmd="play" id="remote-play">Pause</button>
      <button class="btn" type="button" data-cmd="next-mix">Next mix</button>
    </div>
    <p class="remote-status" id="remote-status" role="status">Waiting for the TV...</p>
    <p class="remote-debug" id="remote-debug"></p>
    <h3 class="remote-head">Music</h3>
    <button class="btn remote-cams-btn" type="button" data-action="open-music" aria-haspopup="dialog">Mix, shuffle and volume</button>
    <ol class="remote-mixes" id="remote-mixes"></ol>
    <button class="btn remote-cams-btn" type="button" data-action="open-cams" aria-haspopup="dialog">Cams and time of day</button>
    <h3 class="remote-head">Show a cam now</h3>
    <ol class="remote-mixes" id="remote-cams"></ol>
    <button class="btn btn-primary remote-stop" type="button" data-cmd="stop">Stop casting</button>`;
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
  ui.debug = $("#remote-debug");
  ui.hairline = $("#remote-hairline");
  ui.pauseWarn = $("#remote-pause-warn");
  fillRemoteLists();
  root.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.mix) {
      const m = MIXES.find((x) => x.id === b.dataset.mix);
      return act({ type: "mix", id: b.dataset.mix }, { mixId: b.dataset.mix, mixTitle: m ? m.en : "", paused: false });
    }
    if (b.dataset.cam) {
      const c = CAMS.find((x) => x.id === b.dataset.cam);
      return act({ type: "cam", id: b.dataset.cam }, { camId: b.dataset.cam, camTitle: c ? c.title : "" });
    }
    runCmd(b.dataset.cmd);
  });
}

/* Remote cam and mix buttons, rebuilt whenever the list changes (sheet config). */
function fillRemoteLists() {
  const fill = (ol, items, key, label) => {
    if (!ol) return;
    ol.textContent = "";
    for (const it of items) {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn";
      b.dataset[key] = it.id;
      b.textContent = label(it);
      li.append(b);
      ol.append(li);
    }
  };
  fill($("#remote-mixes"), MIXES, "mix", (m) => m.en);
  fill($("#remote-cams"), CAMS, "cam", (c) => `${c.title} (${c.place})`);
}

/** Route a phone control to the TV: prev, next, pin, interval, vol-up, vol-down, mute, play, next-mix. */
export function remoteCmd(name) {
  runCmd(name);
}

export function remoteMix(id) {
  const m = MIXES.find((x) => x.id === id);
  act({ type: "mix", id }, { mixId: id, mixTitle: m ? m.en : "", paused: false });
}

/** Music volume from the Mix sheet slider while casting: one absolute value (a state command, safe to resend). */
export function remoteVolume(v) {
  const n = Math.min(100, Math.max(0, Math.round(Number(v))));
  if (!Number.isFinite(n)) return;
  act({ type: "volume", value: n }, { volume: n, muted: n === 0 ? remote.muted : false });
}

/** What the TV reports (or the phone just asked for): read only, for the Mix sheet while casting. */
export function remoteState() {
  return { mixId: remote.mixId, mixTitle: remote.mixTitle, volume: remote.volume, muted: !!remote.muted, paused: !!remote.paused, shuffle: remote.shuffle !== false };
}

/** Shuffle for this session: the TV picks the next mix itself when one ends, so it follows the phone's switch.
 *  {type:"shuffle", value:true|false}. An older receiver ignores it and plays in list order. */
export function sendShuffleToTv(on, s = session) {
  if (!s || s !== session) return;
  remote = { ...remote, shuffle: !!on };
  send({ type: "shuffle", value: !!on, silent: true }, { silent: true });
}

export function remoteCam(id) {
  const c = CAMS.find((x) => x.id === id);
  act({ type: "cam", id }, { camId: id, camTitle: c ? c.title : "" });
}

/** The phone's rotation pool changed (Cams sheet): checked cam ids plus the session-only time-of-day rules.
 *  Same "cams" command as on connect, with two optional fields an older receiver simply ignores. */
export function sendCamsToTv(ids, rules = {}) {
  if (!session) return;
  send({ type: "cams", ids, skipSleeping: rules.skipSleeping !== false, onlyDaytime: !!rules.onlyDaytime }, { silent: true });
}

/** Send the current cam and mix lists to the TV (only when they come from the sheet). */
export function sendConfigToTv() {
  if (ui.root) fillRemoteLists();
  if (!session || listInfo.source === "built-in") return;
  send({ type: "config", source: listInfo.source, ...currentConfig(), silent: true }, { silent: true });
}

function liveCastState() {
  return quietly(() => cast.framework.CastContext.getInstance().getCastState()) || "?";
}

function renderDebug() {
  if (!ui.debug) return;
  const ns = session ? nsInfo() : null;
  const nsText = ns ? ` | ns ${ns.ok ? "ok" : "missing"} [${ns.names.join(",")}]` : "";
  const errPart = dbg.lastErr ? ` | err: ${dbg.lastErr}` : "";
  ui.debug.textContent = `Phone build ${BUILD} | TV build ${dbg.tvBuild} | cast ${liveCastState()} | session ${dbg.sessionState} | last: ${dbg.last}${nsText}${errPart}${lastAttempt ? ` | ${lastAttempt}` : ""}`;
}

function renderRemote() {
  renderDebug();
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
  ui.pin.textContent = remote.pinned ? "Pinned" : "Pin cam";
  ui.play.textContent = remote.paused ? "Play" : "Pause";
  ui.hairline.hidden = !remote.paused;
  ui.pauseWarn.hidden = !(remote.paused && remote.pauseWarning);
  // The phone's own views of the TV (the Cams sheet "Now showing") follow every confirmed or optimistic change.
  if (hooks && hooks.onRemoteChange) quietly(() => hooks.onRemoteChange());
}

function onMessage(_ns, raw) {
  const msg = typeof raw === "string" ? quietly(() => JSON.parse(raw)) : raw;
  io.rx++;
  logEvent(`rx ${(msg && msg.type) || "unreadable"}`);
  if (msg && msg.debug && typeof msg.debug === "object") tvDebug = msg.debug;
  if (msg && msg.type === "pauseTimeout") return onPauseTimeout();
  if (!msg || msg.type !== "state") return;
  lastReplyAt = Date.now();
  warnedNoReply = false;
  if (msg.build) dbg.tvBuild = msg.build;
  remote = { ...remote, ...msg, pauseWarning: msg.pauseWarning === true };
  setRemoteStatus(`Connected. The TV confirms each tap.${msg.build ? ` (TV build ${msg.build})` : ""}`);
  renderRemote();
}

/* Remote mode first, unconditionally: stop local playback and show the remote,
   before anything that talks to the TV (which may fail or never answer). */
function enterRemote(status) {
  remoteOn = true;
  document.body.classList.add("casting");
  ui.root.hidden = false;
  try {
    hooks.onStart();
  } catch (e) {
    say(`Could not stop local playback: ${(e && e.message) || e}`);
  }
  if (status) setRemoteStatus(status);
  renderRemote();
  refreshDebug();
  renderCastButtons();
}

/* The TV ended the session after 30 minutes paused: end it here too, so the phone goes idle right away
   (the TV is already stopping). leaveRemote explains why; nothing starts playing on the phone. */
function onPauseTimeout() {
  pauseEnded = true;
  logEvent("TV pause timeout");
  quietly(() => cast.framework.CastContext.getInstance().endCurrentSession(true));
}

function leaveRemote() {
  if (!remoteOn && !session) return;
  // A session that ends while the TV's warning was up (and not by Stop casting) also ended by the timeout,
  // in case the phone slept through the pauseTimeout message.
  const byTimeout = pauseEnded || (remote.paused && remote.pauseWarning && !stopRequested);
  pauseEnded = false;
  stopRequested = false;
  remote = { ...remote, pauseWarning: false };
  session = null;
  remoteOn = false;
  dbg.tvBuild = "?";
  tvDebug = {};
  document.body.classList.remove("casting");
  ui.root.hidden = true;
  hooks.onEnd();
  logEvent("remote off");
  refreshDebug();
  renderCastButtons();
  if (byTimeout) say(PAUSE_ENDED);
}

/* Initial setup is held back until the session lists our channel, then sent exactly once (never replayed). */
function flushInit(s) {
  if (session !== s || !s.__wsPending || !nsInfo().ok) return;
  const mode = s.__wsPending;
  s.__wsPending = null;
  if (mode === "push") {
    pushChoices();
  } else {
    sendConfigToTv();
    // Shuffle only steers the next mix, so it is safe to send on resume: the TV keeps the mix it is playing.
    sendShuffleToTv(hooks.getLocal().shuffle !== false, s);
    send({ type: "getState" }); // reconnecting to a TV that is already playing: do not override it
  }
}

/* Play services can refresh the app's namespace list after the session started. */
function watchNamespaces(s) {
  s.__wsNsOk = nsInfo().ok;
  s.__wsNsNames = nsInfo().names.join(",");
  const onChange = (e) => {
    if (session !== s) return;
    const info = nsInfo();
    const ok = info.ok;
    const appeared = ok && !s.__wsNsOk;
    const names = info.names.join(",");
    if (names !== s.__wsNsNames || ok !== s.__wsNsOk) logEvent(`ns ${(e && e.type) || "change"}: windowseat ${ok ? "listed" : "not listed"} [${names}]`);
    s.__wsNsNames = names;
    s.__wsNsOk = ok;
    renderDebug();
    if (appeared) setRemoteStatus("Connected. The Window Seat channel is now open.");
    flushInit(s);
    if (appeared) sendDebugToTv(s);
  };
  const T = (window.cast && cast.framework && cast.framework.SessionEventType) || {};
  for (const type of [T.APPLICATION_METADATA_CHANGED || "applicationmetadatachanged", T.APPLICATION_STATUS_CHANGED || "applicationstatuschanged"]) {
    quietly(() => s.addEventListener(type, onChange));
  }
}

function onSessionStarted(s, resumed) {
  if (!s) return;
  const same = session === s;
  session = s;
  enterRemote();
  if (!same) {
    if (!s.__wsListener) {
      s.__wsListener = true;
      quietly(() => s.addMessageListener(RECEIVER_NAMESPACE, onMessage));
      watchNamespaces(s);
    }
    ui.device.textContent = quietly(() => s.getCastDevice().friendlyName) || "TV";
    lastReplyAt = 0;
    warnedNoReply = false;
    const hint = quietly(channelCheck) || "";
    setRemoteStatus(hint || "Waiting for the TV app to reply...");
    renderRemote();
    renderCastButtons();
  }
  if (resumed) {
    if (!s.__wsInit) {
      s.__wsInit = true;
      s.__wsPending = "resume";
    }
  } else if (!s.__wsPushed) {
    s.__wsPushed = true; // a real start: push the phone's choices once, even if the session was adopted first
    s.__wsInit = true;
    s.__wsPending = "push";
  }
  const ns = quietly(nsInfo) || { ok: false, names: [] };
  logEvent(`${resumed ? "session resumed" : "session started"}: windowseat ${ns.ok ? "listed" : "not listed"} [${ns.names.join(",")}]`);
  flushInit(s);
  sendDebugToTv(s); // the TV strip follows this phone's Debug switch on every start or resume
}

/** Send the Debug switch to the TV. Waits for the namespace like the other setup messages (no new gating).
 *  `value` is sent too so an older receiver (which read cmd.value) follows the switch as well. */
export function sendDebugToTv(s = session) {
  if (!s || s !== session) return false;
  if (!nsInfo().ok) {
    logEvent(`tx debug deferred: windowseat not listed`);
    return false;
  }
  const v = isDebugOn();
  send({ type: "debug", on: v, value: v, silent: true }, { silent: true });
  return true;
}

/** Everything the debug panel shows about Cast. Read only. */
export function castDebugInfo() {
  let castState = "not configured";
  if (castConfigured) castState = castFailed ? "unavailable" : castReady ? liveCastState() : "loading";
  const attemptErr = lastAttemptCode && lastAttemptCode !== "cancel" ? `cast attempt ${lastAttemptCode}` : "";
  const tvErr = tvDebug.cafError || tvDebug.error ? `TV ${tvDebug.cafError || tvDebug.error}` : "";
  return {
    casting: remoteOn || !!session,
    connected: !!session,
    castState,
    sessionState: dbg.sessionState,
    ns: session ? nsInfo() : null,
    tvBuild: dbg.tvBuild,
    tv: tvDebug,
    tx: io.tx,
    rx: io.rx,
    last: dbg.last,
    lastAttempt,
    now: { camId: remote.camId, camTitle: remote.camTitle, mixId: remote.mixId, mixTitle: remote.mixTitle },
    error: dbg.lastErr ? `send ${dbg.lastErr}` : attemptErr || tvErr,
  };
}

/** Push the phone's choices to the TV (first connect), then ask for its state. */
function pushChoices() {
  const local = hooks.getLocal();
  const mix = MIXES.find((m) => m.id === local.mixId);
  remote = { ...remote, interval: local.interval, volume: local.volume, muted: !!local.muted, mixId: local.mixId, mixTitle: mix ? mix.en : "", pinned: false, paused: false, shuffle: local.shuffle !== false };
  renderRemote();
  const quiet = { silent: true };
  sendConfigToTv(); // first, so the cam filter and picks below refer to the same list on the TV
  if (Array.isArray(local.camIds)) send({ type: "cams", ids: local.camIds, skipSleeping: local.skipSleeping !== false, onlyDaytime: !!local.onlyDaytime, silent: true }, quiet);
  if (local.camId && CAMS.some((c) => c.id === local.camId)) send({ type: "cam", id: local.camId, silent: true }, quiet);
  send({ type: "pin", value: false, silent: true }, quiet);
  send({ type: "interval", value: local.interval, silent: true }, quiet);
  send({ type: "volume", value: local.volume, silent: true }, quiet);
  send({ type: "mute", value: !!local.muted, silent: true }, quiet);
  send({ type: "shuffle", value: local.shuffle !== false, silent: true }, quiet); // before mix: the TV knows what comes next
  send({ type: "mix", id: local.mixId, silent: true }, quiet);
  send({ type: "getState" });
}

/** CAF dispatches SESSION_ENDED while getCurrentSession() still returns the ending session, so a current
 *  session only counts if its state is not terminal. */
function liveSession(ctx) {
  const cur = quietly(() => ctx.getCurrentSession());
  if (!cur) return null;
  if (quietly(() => cur.getSessionState()) === "SESSION_ENDED") return null;
  const cs = quietly(() => ctx.getSessionState());
  if (cs === "SESSION_ENDED" || cs === "NO_SESSION" || cs === "SESSION_START_FAILED") return null;
  return cur;
}

/** The single source of truth: is a Cast session active or connecting? Safe before the SDK loads. */
export function isCasting() {
  if (!castConfigured) return !!session;
  const ctx = quietly(() => cast.framework.CastContext.getInstance());
  if (!ctx) return !!session;
  if (session && quietly(() => session.getSessionState()) === "SESSION_ENDED") return false;
  if (session) return true;
  if (liveSession(ctx)) return true;
  if (quietly(() => ctx.getSessionState()) === "SESSION_ENDED") return false;
  const st = quietly(() => ctx.getCastState());
  return st === "CONNECTING" || st === "CONNECTED";
}

/** Derive remote mode from the live Cast framework state. Safe to call at any time, as often as needed. */
export function syncRemoteMode() {
  if (!hooks || !ui.root) return;
  const ctx = quietly(() => cast.framework.CastContext.getInstance());
  if (!ctx) return;
  const cur = liveSession(ctx);
  const st = quietly(() => ctx.getCastState());
  if (cur) {
    if (cur !== session) onSessionStarted(cur, true); // adopt a session we missed the event for
    else if (!remoteOn) enterRemote();
  } else if (!session && !remoteOn && (st === "CONNECTING" || st === "CONNECTED")) {
    ui.device.textContent = "TV";
    enterRemote("Connecting to the TV...");
  } else if (remoteOn && (st === "NOT_CONNECTED" || st === "NO_DEVICES_AVAILABLE")) {
    leaveRemote();
  }
  renderDebug();
}

function onSessionEnded() {
  const ctx = quietly(() => cast.framework.CastContext.getInstance());
  const other = ctx && liveSession(ctx);
  if (other && other !== session) return syncRemoteMode(); // CAF reports a different live session
  leaveRemote();
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

/* The last requestSession result stays visible (Start screen, menu bar, remote debug line). */
let lastAttemptCode = ""; // "" while the last attempt succeeded
let lastAttempt = ""; // e.g. "Last cast attempt 14:31: session_error (TV found, but the receiver failed to load)"
let statusText = "";

function recordAttempt(code) {
  const hhmm = new Date().toTimeString().slice(0, 5);
  const reason = code ? String(ERROR_TEXT[code] || "").replace(/^Cast:\s*/, "").replace(/\s*\([a-z_]+\)$/, "") : "";
  lastAttempt = `Last cast attempt ${hhmm}: ${code || "ok"}${reason ? ` (${reason})` : ""}`;
  lastAttemptCode = code || "";
  logEvent(`requestSession ${code || "ok"}`);
  setStatus(statusText);
  renderDebug();
}

/* Cast control labels and glyph (official Material cast / cast_connected). Presentation only. */
function renderCastButtons() {
  const st = castReady ? liveCastState() : "";
  let mode = "idle";
  let text = "Cast to your TV";
  if (session || st === "CONNECTED") {
    mode = "connected";
    text = `Casting to ${(ui.device && ui.device.textContent) || "TV"}`;
  } else if (remoteOn || st === "CONNECTING") {
    mode = "connecting";
    text = "Connecting to TV...";
  } else if (castFailed || (lastAttemptCode && lastAttemptCode !== "cancel")) {
    mode = "failed";
  }
  document.querySelectorAll("[data-cast]").forEach((b) => {
    b.dataset.state = mode;
    const label = b.querySelector(".cast-label");
    if (label && label.textContent !== text) label.textContent = text;
    if (b.hasAttribute("aria-label")) b.setAttribute("aria-label", text);
    const use = b.querySelector(".cast-glyph use");
    if (use) use.setAttribute("href", mode === "connected" ? "#i-cast-connected" : "#i-cast");
  });
}

function setStatus(text) {
  statusText = text;
  const line = text && lastAttempt ? `${text} | ${lastAttempt}` : text || lastAttempt;
  document.querySelectorAll(".cast-status").forEach((el) => {
    el.textContent = line;
    el.hidden = !line;
  });
  document.querySelectorAll("[data-cast]").forEach((b) => (b.title = text));
  renderCastButtons();
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
    const msg = (e && e.message) || e;
    recordAttempt(`exception: ${msg}`);
    return say(`Cast error: ${msg}`);
  }
  Promise.resolve(p).then(
    (code) => {
      recordAttempt(code || "");
      if (code) say(ERROR_TEXT[code] || `Cast: ${code}`);
      else say(STATE_TEXT[ctx.getCastState()] || "Cast: connected");
    },
    (code) => {
      const c = (code && code.code) || code;
      recordAttempt(String(c));
      say(ERROR_TEXT[c] || `Cast: ${c}`);
    },
  );
}

function initFramework() {
  const ctx = cast.framework.CastContext.getInstance();
  buildRemote();
  // Listeners go on before setOptions: auto-join can resume an existing session right away,
  // and a missed SESSION_RESUMED left the phone playing locally while the TV was casting.
  const T = cast.framework.SessionState;
  ctx.addEventListener(cast.framework.CastContextEventType.SESSION_STATE_CHANGED, (e) => {
    logEvent(`session ${e.sessionState}${e.errorCode ? ` ${e.errorCode}` : ""}`);
    dbg.sessionState = e.sessionState;
    dbg.last = e.sessionState + (e.errorCode ? ` ${e.errorCode}` : "");
    if (e.sessionState === T.SESSION_STARTED) onSessionStarted(e.session || quietly(() => ctx.getCurrentSession()), false);
    else if (e.sessionState === T.SESSION_RESUMED) onSessionStarted(e.session || quietly(() => ctx.getCurrentSession()), true);
    else if (e.sessionState === T.SESSION_ENDED) onSessionEnded();
    else syncRemoteMode(); // SESSION_STARTING, SESSION_START_FAILED, SESSION_ENDING
    renderDebug();
  });
  ctx.addEventListener(cast.framework.CastContextEventType.CAST_STATE_CHANGED, (e) => {
    logEvent(`cast ${e.castState}`);
    setStatus(STATE_TEXT[e.castState] || `Cast: ${e.castState}`);
    dbg.last = `cast ${e.castState}`;
    syncRemoteMode();
  });
  // Events can be missed or late (backgrounded tab, slow TV): re-derive from the live state whenever the page wakes.
  document.addEventListener("visibilitychange", () => !document.hidden && syncRemoteMode());
  window.addEventListener("pageshow", () => syncRemoteMode());
  window.addEventListener("focus", () => syncRemoteMode());
  ctx.setOptions({
    receiverApplicationId: CAST_APP_ID,
    autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED,
  });
  castReady = true;
  logEvent(`CastContext ready: ${quietly(() => ctx.getCastState()) || "?"}`);
  setStatus(STATE_TEXT[ctx.getCastState()] || "");
  syncRemoteMode("init");
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
    logEvent(`Cast SDK ${available ? "available" : "unavailable"}`);
    if (!available) return markUnavailable();
    try {
      initFramework();
    } catch (e) {
      logEvent(`CastContext setup failed: ${errText((e && e.message) || e)}`);
      markUnavailable();
    }
  };
  const script = document.createElement("script");
  script.src = SDK_URL;
  script.async = true;
  script.onerror = () => {
    logEvent("Cast SDK load failed");
    markUnavailable(); // offline or blocked
  };
  document.head.append(script);
}
