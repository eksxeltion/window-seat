// Window Seat service worker: caches only the app shell so the page opens fast and installs as an app.
// Bump CACHE_VERSION whenever a shell file changes, and keep it equal to BUILD in js/build.js (all same-origin GETs are network-first with cache fallback).
const CACHE_VERSION = "2026-11-16.5";

const CACHE = "window-seat-" + CACHE_VERSION;
const SHELL = [
  "/",
  "/index.html",
  "/css/style.css",
  "/js/app.js",
  "/js/data.js",
  "/js/qr.js",
  "/js/cast.js",
  "/js/build.js",
  "/js/cast-config.js",
  "/js/config.js",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/maskable-512.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("window-seat-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  // Never touch cross-origin traffic (YouTube player, API, thumbnails, video): no respondWith at all.
  if (url.origin !== self.location.origin || req.method !== "GET") return;
  // The Cast receiver runs on the Cast device, not in the PWA: leave its files alone.
  if (url.pathname === "/receiver.html" || url.pathname === "/js/receiver.js" || url.pathname === "/css/receiver.css") return;

  // Network first for every same-origin GET so HTML, JS and CSS always come from the same deploy.
  // Fresh responses refresh the cache; the cache (precached shell) is the offline fallback.
  event.respondWith(
    // no-cache: the browser HTTP cache must revalidate, so a deploy is never masked by a stale copy.
    fetch(req, { cache: "no-cache" })
      .then((res) => {
        if (res && res.ok && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy)).catch(() => {});
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then((hit) => hit || (req.mode === "navigate" ? caches.match("/index.html").then((r) => r || caches.match("/")) : undefined)),
      ),
  );
});
