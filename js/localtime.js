// Local time at a cam, from the sheet's timezone column (an IANA name such as Asia/Tokyo).
// Shared by the phone (Home hero, local player) and the TV lower third. Only a valid IANA zone counts:
// a blank or unknown value gives no local time, and such a cam is never treated as sleeping.

const ZONE_RE = /^[A-Za-z][A-Za-z0-9_+-]*(\/[A-Za-z0-9_+-]+)+$/;
const zoneCache = new Map(); // zone -> Intl.DateTimeFormat, or null when the runtime rejects it

function formatterFor(tz) {
  if (typeof tz !== "string") return null;
  const zone = tz.trim();
  if (!ZONE_RE.test(zone)) return null;
  if (!zoneCache.has(zone)) {
    let fmt = null;
    try {
      fmt = new Intl.DateTimeFormat("en-US", { timeZone: zone, hour: "numeric", minute: "2-digit", hour12: true });
    } catch {
      fmt = null; // not a zone this runtime knows
    }
    zoneCache.set(zone, fmt);
  }
  return zoneCache.get(zone);
}

/** True for an IANA zone name the runtime accepts (Asia/Tokyo), false for blanks, cities, or junk. */
export function isValidZone(tz) {
  return !!formatterFor(tz);
}

/** { hour (0 to 23), minute, text ("9:51 AM") } in that zone, or null when the zone is not valid. */
export function localTime(tz, now = new Date()) {
  const fmt = formatterFor(tz);
  if (!fmt) return null;
  let parts;
  try {
    parts = fmt.formatToParts(now);
  } catch {
    return null;
  }
  const get = (type) => (parts.find((p) => p.type === type) || {}).value || "";
  const h12 = Number(get("hour"));
  const minute = Number(get("minute"));
  const pm = /p/i.test(get("dayPeriod"));
  if (!Number.isFinite(h12) || !Number.isFinite(minute)) return null;
  const hour = (h12 % 12) + (pm ? 12 : 0);
  return { hour, minute, text: `${h12}:${String(minute).padStart(2, "0")} ${pm ? "PM" : "AM"}` };
}

/** "9:51 AM" at the cam, or "" when its timezone is missing or invalid. */
export function camLocalTime(cam, now = new Date()) {
  const t = cam ? localTime(cam.timezone, now) : null;
  return t ? t.text : "";
}

/** A cam is sleeping from local midnight to 6 AM. Without a valid timezone it is never sleeping. */
export function isSleeping(cam, now = new Date()) {
  const t = cam ? localTime(cam.timezone, now) : null;
  return !!t && t.hour < 6;
}

/** Outside 7 AM to 7 PM local. Without a valid timezone this is never true, so such a cam is never excluded. */
export function isOutsideDaytime(cam, now = new Date()) {
  const t = cam ? localTime(cam.timezone, now) : null;
  return !!t && (t.hour < 7 || t.hour >= 19);
}

/** Session-only time-of-day rules for auto-rotation: Skip sleeping cams is on and Only daytime is off by default. */
export const DEFAULT_TIME_RULES = Object.freeze({ skipSleeping: true, onlyDaytime: false });

/** True when the time-of-day rules let this cam take part in auto-rotation right now. Play now and Pin ignore this. */
export function rotationAllows(cam, rules = DEFAULT_TIME_RULES, now = new Date()) {
  if (!cam) return false;
  if (rules.skipSleeping !== false && isSleeping(cam, now)) return false;
  if (rules.onlyDaytime && isOutsideDaytime(cam, now)) return false;
  return true;
}

/**
 * The auto-rotation pool: the checked cams that the time-of-day rules allow right now. When the rules leave
 * nothing (every checked cam is asleep), the checked cams are used anyway so the window never goes dark.
 */
export function rotationPool(checked, rules = DEFAULT_TIME_RULES, now = new Date()) {
  const list = (Array.isArray(checked) ? checked : []).filter(Boolean);
  const awake = list.filter((c) => rotationAllows(c, rules, now));
  return awake.length ? awake : list;
}

/** In-rotation cams that are not sleeping right now ("windows awake"). */
export function awakeCams(cams, now = new Date()) {
  return (Array.isArray(cams) ? cams : []).filter((c) => c && c.default && !isSleeping(c, now));
}

/** "Tokyo, Japan", or just the city (or country) when one of them is missing. */
export function placeLine(cam) {
  if (!cam) return "";
  const city = cam.city || cam.place || "";
  const country = cam.country || "";
  if (city && country && city !== country) return `${city}, ${country}`;
  return city || country;
}

/** Morning, Afternoon, Evening, Night, or Late night for a local hour. */
export function partOfDay(hour) {
  if (hour < 6) return "Late night";
  if (hour < 12) return "Morning";
  if (hour < 17) return "Afternoon";
  if (hour < 21) return "Evening";
  return "Night";
}
