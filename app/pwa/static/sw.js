// Astro Cortex - Service Worker
// Provides offline-first PWA behavior: serves app shell from cache when offline.
// Network-first for API calls (always try fresh data, fall back to last cached).

const CACHE_VERSION = 'astro-cortex-v1';
const APP_SHELL = [
    '/',
    '/static/index.html',
    '/static/manifest.json',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_VERSION).then((cache) => cache.addAll(APP_SHELL))
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))
            )
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Network-first for API calls
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(
            fetch(event.request).catch(() =>
                caches.match(event.request).then(
                    (cached) => cached || new Response('{"error": "offline"}', {
                        status: 503,
                        headers: { 'Content-Type': 'application/json' }
                    })
                )
            )
        );
        return;
    }

    // Cache-first for app shell
    event.respondWith(
        caches.match(event.request).then(
            (cached) => cached || fetch(event.request)
        )
    );
});
