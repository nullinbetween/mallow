// Mallow service worker.
//
// It caches the shell and the artwork so the meadow opens instantly and works
// with no signal. It deliberately does NOT cache or queue anything a person
// says: a note that appears to have been filed while offline, and then is not,
// is worse than a note that was never taken. Every POST goes to the network or
// fails visibly.
/* 🔴 v4 retires the v3 cache that served an old auth.js beside new HTML.
   Mutable code is network-first below, so a future auth release no longer
   depends on somebody remembering to move this number before it can work. */
const CACHE = "mallow-shell-v4";
const SHELL = [
  "/", "/manifest.webmanifest", "/static/auth.js",
  "/art/background_day.webp", "/art/background_night.webp",
  "/art/rabbit_idle.webp", "/art/rabbit_listening.webp", "/art/rabbit_grass.webp",
  "/art/rabbit_carrot.webp", "/art/rabbit_sleeping.webp",
  "/art/basket.webp", "/art/leaf.webp",
  "/static/icon-192.png", "/static/icon-512.png"
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;

  // Navigation is network-first. The cached root is only an offline shell;
  // online visits must never keep serving yesterday's HTML and JavaScript.
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match("/")));
    return;
  }

  // Identity, records, garden state, settings and exports are never cached.
  const dynamic = ["/auth", "/garden", "/records", "/voice", "/say",
                   "/export", "/settings", "/tasks", "/whoami"];
  if (dynamic.some(path => url.pathname === path || url.pathname.startsWith(path + "/")))
    return;

  // auth.js changes with the product. Online must mean current; the installed
  // copy is only an offline fallback. Updating the cache after a successful
  // fetch also makes that fallback the newest code this browser has seen.
  if (url.pathname === "/static/auth.js"
      || url.pathname === "/manifest.webmanifest") {
    e.respondWith(fetch(e.request).then(response => {
      const copy = response.clone();
      e.waitUntil(caches.open(CACHE).then(c => c.put(e.request, copy)));
      return response;
    }).catch(() => caches.match(e.request)));
    return;
  }

  // Only immutable, content-named artwork and fixed app icons are cache-first.
  // Do not widen this back to all of /static/: that is how stale auth code
  // disabled the gate for every returning browser.
  if (url.pathname.startsWith("/art/")
      || /^\/static\/icon-(192|512)\.png$/.test(url.pathname)) {
    e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request)));
  }
});
