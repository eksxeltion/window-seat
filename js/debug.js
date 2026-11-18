// Opt-in Debug mode for the phone page: a switch in About, a collapsible panel, and a ring buffer of Cast events.
// The on/off value lives only in this module (in memory). It is never written to storage, so every page load
// starts with Debug off.
import { BUILD } from "./build.js";
import { CAMS, MIXES } from "./data.js";
import { listInfo, tzCoverage } from "./config.js";
import { capture } from "./telemetry.js";

const MAX_EVENTS = 20;
const events = []; // { t: "HH:MM:SS", text }
let on = false;
let expanded = false;
let providers = { now: () => ({}), cast: () => ({}), toast: () => {}, onChange: () => {} };
const ui = {};
let timer = 0;

const MONO_FONT_URL = "https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@500&display=swap";

/* Roboto Mono 500 for the debug panel, requested the first time Debug is turned on. Optional: a failed or slow
   request never blocks anything, and the --font-mono stack falls back to the system monospace. */
function loadMonoFont() {
  if (document.querySelector("link[data-font-mono]")) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = MONO_FONT_URL;
  link.dataset.fontMono = "";
  document.head.append(link);
}

export const hhmmss = (d = new Date()) => d.toTimeString().slice(0, 8);

/** Add one Cast event to the ring buffer. Repeats of the same text collapse into one line with a count. */
export function logEvent(text) {
  const t = hhmmss();
  const last = events[events.length - 1];
  if (last && last.text === text) {
    last.n = (last.n || 1) + 1;
    last.t = t;
  } else {
    events.push({ t, text: String(text).slice(0, 160) });
    if (events.length > MAX_EVENTS) events.shift();
  }
  if (on) render();
}

export function getEvents() {
  return events.map((e) => ({ ...e }));
}

export function isDebugOn() {
  return on;
}

const camById = (id) => CAMS.find((c) => c.id === id) || null;
const mixById = (id) => MIXES.find((m) => m.id === id) || null;

function listText() {
  const tz = tzCoverage(CAMS);
  return `${listInfo.source} ${listInfo.cams}/${listInfo.mixes} · tz ${tz.valid}/${tz.total}`;
}

/** Everything the panel shows, as label/value rows grouped into sections. */
function collect() {
  const c = providers.cast() || {};
  const n = (c.casting && c.now ? c.now : providers.now()) || {};
  const cam = camById(n.camId);
  const mix = mixById(n.mixId);
  const camText = n.camId ? `${(cam && cam.title) || n.camTitle || "?"} (${n.camId})` : "none";
  let mixText = "none";
  if (n.mixId) {
    const en = (mix && mix.en) || n.mixTitle || "?";
    const ja = mix && mix.title && mix.title !== en ? ` / ${mix.title}` : "";
    mixText = `${en}${ja} (${n.mixId})`;
  }
  const tv = c.tv || {};
  return {
    error: c.error || "",
    sections: [
      ["APP", [
        ["Build", c.casting ? `${BUILD} · TV ${c.tvBuild || "?"}` : BUILD],
        ["List", listText()],
      ]],
      ["NOW", [
        ["Cam", camText],
        ["Mix", mixText],
      ]],
      ["CAST", [
        ["App", c.appId ? `${c.appId} (${c.appKind === "test" ? "test receiver" : "production"})` : "?"],
        ["Context", c.castState || "-"],
        ["Session", c.sessionState || "-"],
        ["Namespace", c.ns ? `windowseat STRING · ${c.ns.ok ? "listed" : "not listed"}${c.ns.names.length ? ` [${c.ns.names.join(",")}]` : ""}` : "no session"],
        ["TV", c.casting ? `CAF ${tv.caf || "?"} · ready-ns ${tv.readyNs || "?"} · appstate ${tv.appState || "?"}${tv.error ? ` · error ${tv.error}` : ""}` : "-"],
        ["Messages", `phone tx ${c.tx || 0} rx ${c.rx || 0} · TV rx ${tv.rx ?? "?"} tx ${tv.tx ?? "?"}`],
        ["Last", c.last || "-"],
        ["Attempt", c.lastAttempt || "none"],
        ["Error", c.error || "none"],
      ]],
    ],
  };
}

/** Plain-text dump of every field plus the event log, for Copy details. */
export function detailsText() {
  const d = collect();
  const lines = [`Window Seat debug ${new Date().toISOString()}`];
  for (const [title, rows] of d.sections) {
    lines.push("", title);
    for (const [k, v] of rows) lines.push(`${k}: ${v}`);
  }
  lines.push("", `Events (last ${MAX_EVENTS})`);
  if (!events.length) lines.push("none");
  for (const e of events) lines.push(`${e.t} ${e.text}${e.n ? ` x${e.n}` : ""}`);
  lines.push("", `UA: ${navigator.userAgent}`);
  return lines.join("\n");
}

function build() {
  const root = document.createElement("div");
  root.id = "debug-panel";
  root.className = "debug-panel";
  root.hidden = true;
  root.innerHTML = `
    <button type="button" class="debug-pill" id="debug-pill" aria-expanded="false" aria-controls="debug-body">
      <span class="debug-dot" aria-hidden="true"></span><span class="debug-pill-text">DEBUG</span><span class="debug-caret" aria-hidden="true"></span>
    </button>
    <div class="debug-body" id="debug-body" hidden>
      <div class="debug-sections"></div>
      <h4 class="debug-head">EVENTS</h4>
      <ol class="debug-events" id="debug-events"></ol>
      <button type="button" class="btn debug-copy" id="debug-copy">Copy details</button>
    </div>`;
  document.body.append(root);
  ui.root = root;
  ui.pill = root.querySelector("#debug-pill");
  ui.pillText = root.querySelector(".debug-pill-text");
  ui.body = root.querySelector("#debug-body");
  ui.sections = root.querySelector(".debug-sections");
  ui.events = root.querySelector("#debug-events");
  ui.pill.addEventListener("click", () => setExpanded(!expanded));
  root.querySelector("#debug-copy").addEventListener("click", copyDetails);
}

function setExpanded(v) {
  expanded = !!v;
  ui.pill.setAttribute("aria-expanded", String(expanded));
  ui.body.hidden = !expanded;
  render();
}

/* Casting: the panel sits in the remote's flow (under its heading), so it never covers transport or volume.
   Otherwise it floats at the top center of the page. */
function place() {
  const remote = document.getElementById("remote");
  const casting = document.body.classList.contains("casting") && remote && !remote.hidden;
  const pill = casting ? remote.querySelector("h2") : null;
  const target = casting ? (pill ? pill.parentNode : remote) : document.body;
  if (ui.root.parentNode !== target) {
    if (pill) pill.after(ui.root);
    else if (casting) remote.prepend(ui.root);
    else document.body.append(ui.root);
  }
  ui.root.classList.toggle("in-remote", !!casting);
}

function render() {
  if (!ui.root) return;
  ui.root.hidden = !on;
  if (!on) return;
  place();
  const d = collect();
  const c = providers.cast() || {};
  ui.root.classList.toggle("has-error", !!d.error);
  let pill = d.error ? "DEBUG  error" : `DEBUG  ${BUILD}`;
  if (!d.error && c.casting) pill += `  ·  TV ${c.tvBuild || "?"}`;
  ui.pillText.textContent = pill;
  if (!expanded) return;
  ui.sections.textContent = "";
  for (const [title, rows] of d.sections) {
    const h = document.createElement("h4");
    h.className = "debug-head";
    h.textContent = title;
    const dl = document.createElement("dl");
    dl.className = "debug-rows";
    dl.dataset.section = title.toLowerCase();
    for (const [k, v] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      dl.append(dt, dd);
    }
    ui.sections.append(h, dl);
  }
  ui.events.textContent = "";
  for (const e of events.slice().reverse()) {
    const li = document.createElement("li");
    li.textContent = `${e.t} ${e.text}${e.n ? ` x${e.n}` : ""}`;
    ui.events.append(li);
  }
  if (!events.length) {
    const li = document.createElement("li");
    li.textContent = "No Cast events yet";
    ui.events.append(li);
  }
}

async function copyDetails() {
  const text = detailsText();
  let ok = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch {
    ok = false;
  }
  if (!ok) {
    // Fallback for browsers without the async Clipboard API (or without permission).
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
    document.body.append(ta);
    ta.select();
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
  }
  providers.toast(ok ? "Copied." : "Could not copy. Long-press the panel to select the text.");
}

/** Turn Debug on or off for this page load only. */
export function setDebug(value) {
  const next = !!value;
  if (next === on) return;
  on = next;
  const toggle = document.getElementById("debug-toggle");
  if (toggle) toggle.checked = on;
  clearInterval(timer);
  if (on) loadMonoFont();
  if (on) timer = setInterval(render, 1000); // live values (cast state, counts) without wiring every change
  render();
  logEvent(`debug ${on ? "on" : "off"}`);
  providers.onChange(on);
}

/** providers: { now() -> {camId, mixId, camTitle, mixTitle}, cast() -> cast info, toast(text), onChange(on) } */
export function initDebug(p) {
  providers = { ...providers, ...p };
  build();
  const toggle = document.getElementById("debug-toggle");
  if (toggle) {
    toggle.checked = false; // never restored: some browsers keep form state across a reload
    toggle.addEventListener("change", () => {
      capture("debug_mode_toggled", { on: toggle.checked });
      setDebug(toggle.checked);
    });
  }
  render();
}

/** Re-render now (for example after the remote opens or closes). */
export function refreshDebug() {
  render();
}
