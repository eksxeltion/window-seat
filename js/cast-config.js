// Google Cast settings. Registered Cast Application IDs (Google Cast SDK Developer Console).
// A leaf module: it must not import app.js or cast.js.
export const CAST_APP_ID_PROD = "48BEB59E"; // Window Seat, unpublished until phase 3
export const CAST_APP_ID_TEST = "CEB296EE"; // Window Seat (test), unpublished: registered test devices only
export const RECEIVER_NAMESPACE = "urn:x-cast:com.windowseat";
export const CAST_APP_STORAGE_KEY = "windowSeat.castApp"; // "test" or "prod"

/** "test", "prod", or null for a value that names neither app. */
export function castAppFrom(value) {
  const v = String(value == null ? "" : value).trim().replace(/^"|"$/g, "").toLowerCase();
  if (v === "test" || v === CAST_APP_ID_TEST.toLowerCase()) return "test";
  if (v === "prod" || v === "main" || v === CAST_APP_ID_PROD.toLowerCase()) return "prod";
  return null;
}

/** Remember the choice for later loads. The Cast SDK reads the app ID once, so it applies after a reload. */
export function saveCastApp(which) {
  try {
    window.localStorage.setItem(CAST_APP_STORAGE_KEY, which === "test" ? "test" : "prod");
  } catch {
    /* storage unavailable: the choice just won't persist */
  }
}

/* ?castApp= wins on this load and is saved; otherwise the saved choice; otherwise production. */
function resolve() {
  let which = null;
  try {
    const param = new URLSearchParams(window.location.search).get("castApp");
    which = castAppFrom(param);
    if (which) saveCastApp(which);
  } catch {
    /* no location: fall through */
  }
  if (!which) {
    try {
      which = castAppFrom(window.localStorage.getItem(CAST_APP_STORAGE_KEY));
    } catch {
      /* storage unavailable */
    }
  }
  return which || "prod";
}

/** The app this page load uses, fixed at load: "test" or "prod". */
export const CAST_APP = resolve();
/** The active Cast Application ID: every session start and app check uses this one. */
export const CAST_APP_ID = CAST_APP === "test" ? CAST_APP_ID_TEST : CAST_APP_ID_PROD;
