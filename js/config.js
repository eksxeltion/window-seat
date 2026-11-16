// Optional Google Sheet config: the cam and mix lists can come from a sheet anyone with the link can view.
// js/data.js stays the built-in fallback. Importing this module applies the last good sheet config
// (saved in localStorage) to the CAMS and MIXES arrays right away, before the app reads them.
import { CAMS, MIXES } from "./data.js";

export const SHEET_ID = "1tw0g6C7S2HftYwe0hgKdAVgFFBeXga6uNIJNdD_JN94";
export const SHEET_URL = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/edit`;
export const csvUrl = (tab) => `https://docs.google.com/spreadsheets/d/${SHEET_ID}/gviz/tq?tqx=out:csv&sheet=${encodeURIComponent(tab)}`;

const STORE_KEY = "windowSeat.sheetConfig";
const FETCH_MS = 8000;
const ID_RE = /^[A-Za-z0-9_-]{11}$/;

const BUILTIN = { cams: CAMS.map((c) => ({ ...c })), mixes: MIXES.map((m) => ({ ...m })) };

/** RFC 4180 style CSV: quoted fields, doubled quotes, commas and newlines inside quotes, CRLF. */
export function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  const s = String(text || "").replace(/^\uFEFF/, "");
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (quoted) {
      if (ch === '"') {
        if (s[i + 1] === '"') {
          field += '"';
          i++;
        } else quoted = false;
      } else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && s[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += ch;
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c.trim() !== ""));
}

/** Header row to objects keyed by lowercased, trimmed header names. */
function toObjects(rows) {
  if (!rows.length) return [];
  const head = rows[0].map((h) => h.trim().toLowerCase().replace(/\s+/g, "_"));
  return rows.slice(1).map((r) => Object.fromEntries(head.map((h, i) => [h, (r[i] || "").trim()])));
}

const truthy = (v, dflt) => {
  const t = String(v ?? "").trim().toLowerCase();
  if (!t) return dflt;
  return ["true", "yes", "y", "1", "x", "on"].includes(t);
};

/** Build validated lists from the two CSV texts. Bad rows (missing or malformed IDs, duplicates) are skipped. */
export function buildConfig(camsCsv, mixesCsv) {
  const known = new Map(BUILTIN.cams.map((c) => [c.id, c]));
  const knownMix = new Map(BUILTIN.mixes.map((m) => [m.id, m]));
  const cams = [];
  const seen = new Set();
  let skipped = 0;
  for (const r of toObjects(parseCsv(camsCsv))) {
    const id = r.youtube_id || r.id || "";
    if (!ID_RE.test(id) || seen.has(id)) {
      skipped++;
      continue;
    }
    seen.add(id);
    const base = known.get(id) || {};
    const title = r.name || r.title || base.title || id;
    cams.push({
      id,
      title,
      place: r.city || base.place || "Japan",
      region: r.region || "",
      channel: r.channel || base.channel || "YouTube",
      channelUrl: r.channel_url || base.channelUrl || `https://www.youtube.com/watch?v=${id}`,
      default: truthy(r.in_rotation, true),
    });
  }
  const mixes = [];
  const seenMix = new Set();
  for (const r of toObjects(parseCsv(mixesCsv))) {
    const id = r.youtube_id || r.id || "";
    if (!ID_RE.test(id) || seenMix.has(id)) {
      skipped++;
      continue;
    }
    seenMix.add(id);
    if (!truthy(r.enabled, true)) continue;
    const base = knownMix.get(id) || {};
    const minutes = Number(r.minutes) || base.minutes || 60;
    mixes.push({ id, title: r.title || base.title || id, en: r.title_en || base.en || r.title || id, minutes });
  }
  return { cams, mixes, skipped };
}

/** A config is usable only with at least one cam in rotation and one mix. */
export function isUsable(cfg) {
  return !!cfg && Array.isArray(cfg.cams) && Array.isArray(cfg.mixes) && cfg.cams.some((c) => c.default) && cfg.mixes.length > 0;
}

/** Validate lists that arrived over the Cast channel or from storage (same rules as the sheet). */
export function sanitize(cfg) {
  if (!cfg || !Array.isArray(cfg.cams) || !Array.isArray(cfg.mixes)) return null;
  const cams = [];
  const seen = new Set();
  for (const c of cfg.cams) {
    if (!c || !ID_RE.test(c.id) || seen.has(c.id)) continue;
    seen.add(c.id);
    cams.push({
      id: c.id,
      title: String(c.title || c.id).slice(0, 120),
      place: String(c.place || "Japan").slice(0, 60),
      region: String(c.region || "").slice(0, 60),
      channel: String(c.channel || "YouTube").slice(0, 120),
      channelUrl: /^https:\/\//.test(c.channelUrl || "") ? c.channelUrl : `https://www.youtube.com/watch?v=${c.id}`,
      default: c.default !== false,
    });
  }
  const mixes = [];
  const seenMix = new Set();
  for (const m of cfg.mixes) {
    if (!m || !ID_RE.test(m.id) || seenMix.has(m.id)) continue;
    seenMix.add(m.id);
    mixes.push({ id: m.id, title: String(m.title || m.id).slice(0, 200), en: String(m.en || m.title || m.id).slice(0, 200), minutes: Number(m.minutes) || 60 });
  }
  const out = { cams, mixes };
  return isUsable(out) ? out : null;
}

/* ---- current list, applied in place so every module that imported CAMS/MIXES sees it ---- */

export const listInfo = { source: "built-in", cams: CAMS.length, mixes: MIXES.length, at: 0 };

function replaceAll(arr, items) {
  arr.splice(0, arr.length, ...items);
}

/** Apply a validated config. source: "sheet", "saved" (localStorage copy) or "phone" (sent over Cast). */
let generation = 0; // bumps on every applied list, so a slow fetch cannot undo a newer one

export function applyConfig(cfg, source) {
  const clean = sanitize(cfg);
  if (!clean) return false;
  generation++;
  replaceAll(CAMS, clean.cams);
  replaceAll(MIXES, clean.mixes);
  Object.assign(listInfo, { source, cams: CAMS.length, mixes: MIXES.length, at: Date.now() });
  return true;
}

export function listLabel() {
  if (listInfo.source === "built-in") return "List: built-in";
  const src = { sheet: "Sheet", saved: "Sheet (saved copy)", phone: "Sheet (from phone)" }[listInfo.source] || listInfo.source;
  return `List: ${src}, ${listInfo.cams} cams, ${listInfo.mixes} mixes`;
}

/** The current lists, for sending to the TV. */
export function currentConfig() {
  return { cams: CAMS.map((c) => ({ ...c })), mixes: MIXES.map((m) => ({ ...m })) };
}

function readSaved() {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function save(cfg) {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify({ at: Date.now(), cams: cfg.cams, mixes: cfg.mixes }));
  } catch {
    /* storage unavailable */
  }
}

async function fetchText(url, ms) {
  const ctrl = typeof AbortController === "function" ? new AbortController() : null;
  const timer = setTimeout(() => ctrl && ctrl.abort(), ms);
  try {
    const res = await fetch(url, { cache: "no-store", credentials: "omit", signal: ctrl ? ctrl.signal : undefined });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const type = res.headers.get("content-type") || "";
    if (!/csv|text\/plain/.test(type)) throw new Error(`not CSV (${type || "no type"}): is the sheet shared as Anyone with the link?`);
    return await res.text();
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Fetch the sheet. On success the lists are applied and saved; on failure the current lists stay
 * (saved copy or built-in). Resolves { ok, changed, error }.
 */
export async function refreshFromSheet(ms = FETCH_MS) {
  try {
    const startGen = generation;
    const [camsCsv, mixesCsv] = await Promise.all([fetchText(csvUrl("Cams"), ms), fetchText(csvUrl("Mixes"), ms)]);
    const cfg = buildConfig(camsCsv, mixesCsv);
    if (!isUsable(cfg)) return { ok: false, changed: false, error: "sheet has no usable cams or mixes" };
    // A newer list arrived meanwhile (from the phone): keep it, it came from the same sheet.
    if (generation !== startGen) return { ok: true, changed: false, superseded: true, skipped: cfg.skipped };
    const before = JSON.stringify(currentConfig());
    applyConfig(cfg, "sheet");
    save(cfg);
    return { ok: true, changed: JSON.stringify(currentConfig()) !== before, skipped: cfg.skipped };
  } catch (e) {
    return { ok: false, changed: false, error: String((e && e.message) || e) };
  }
}

// Module start: use the last good sheet copy right away (the fresh fetch runs later, without blocking).
const saved = readSaved();
if (saved) applyConfig(saved, "saved");
