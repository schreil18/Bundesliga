/* Service Worker der Bundesliga-App.
   - Seite und news.json: erst aus dem Netz (damit Updates sofort ankommen), ohne Netz aus dem Speicher.
   - Symbole, Wappen, Manifest: aus dem Speicher, beim ersten Abruf dort abgelegt.
   - Live-Daten (OpenLigaDB, ESPN, News aus dem Repository), Schriften und fremde Bilder laufen nie über
     diesen Speicher, damit Ergebnisse immer frisch sind. */
const CACHE = "bundesliga-app-v2";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icons/icon-192.png", "./icons/icon-512.png", "./icons/apple-touch-icon.png", "./icons/favicon-32.png"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k.startsWith("bundesliga-app-") && k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function networkFirst(req, key) {
  return fetch(req)
    .then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(key, copy)); }
      return res;
    })
    .catch(() => caches.match(key).then(hit => hit || Response.error()));
}

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.mode === "navigate" || url.pathname.endsWith("/") || url.pathname.endsWith("/index.html")) {
    event.respondWith(networkFirst(req, "./index.html"));
    return;
  }
  if (url.pathname.endsWith("/news.json")) {
    event.respondWith(networkFirst(req, "./news.json"));
    return;
  }
  event.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res.ok && /\/(crests|icons)\//.test(url.pathname)) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return res;
    }))
  );
});
