/* RiskDesk service worker: lets the browser install the page as an app. It deliberately caches nothing, so a
   new version is always the one you get and your journal is never kept anywhere but the server. */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function () { /* the browser handles it: straight to the network */ });
