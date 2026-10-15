// Minimal service worker: lets phones install Price Sniper as an app.
// It deliberately caches nothing, so updates always show up right away.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});   // network as usual
