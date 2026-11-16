// Window Seat service worker: caches only the app shell so the page opens fast and installs as an app.
// Bump CACHE_VERSION whenever a shell file changes (shell files are served cache-first).
const CACHE_VERSION = "v1";

const CACHE = "window-seat-" + CACHE_VERSION;
const SHELL = [
  "/",
  "/index.html",
  "/css/style.css",
  "/js/app.js",
  "/js/data.js",
  "/js/qr.js",
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

  if (req.mode === "navigate") {
    // Network first so a deploy shows up right away; fall back to the cached shell when offline.
    event.respondWith(
      fetch(req).catch(() => caches.match("/index.html").then((r) => r || caches.match("/"))),
    );
    return;
  }

  if (SHELL.includes(url.pathname)) {
    event.respondWith(caches.match(url.pathname).then((hit) => hit || fetch(req)));
  }
});
