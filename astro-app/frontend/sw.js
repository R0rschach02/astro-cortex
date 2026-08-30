/* Service Worker v15 - RainViewer Radar/Satellit-Layer mit Animation. */
"use strict";

const SHELL_CACHE = "astro-shell-v15";
const API_CACHE = "astro-api-v15";
const SHELL = [
  ".", "index.html", "app.js", "style.css", "manifest.webmanifest",
  "vendor/leaflet.js", "vendor/leaflet.css",
  "icons/icon.svg", "icons/icon-192.png", "icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL_CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    // loescht astro-shell-v1, astro-api-v1 und alles andere Veraltete
    keys.filter(k => k !== SHELL_CACHE && k !== API_CACHE).map(k => caches.delete(k))
  )).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;

  // Netzwerk zuerst, Cache als Fallback (offline):
  // - API: frische Werte, sonst letzter bekannter Stand
  // - Shell/Tiles: Updates greifen nach einem Reload, offline bleibt nutzbar
  e.respondWith(
    fetch(e.request).then((res) => {
      if (res && res.status === 200 && res.type === "basic") {
        const copy = res.clone();
        caches.open(e.request.url.includes("/api/") ? API_CACHE : SHELL_CACHE)
              .then(c => c.put(e.request, copy));
      }
      return res;
    }).catch(() => caches.match(e.request))
  );
});
