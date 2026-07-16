const CACHE_NAME = 'LUMIDM-static-v1';
const CORE = [
  '/',
  '/static/index.html',
  '/static/app.css',
  '/static/app.js',
  '/static/manifest.webmanifest',
  '/static/favicon-192.png',
  '/static/favicon-512.png',
  '/static/preview.html'
];

self.addEventListener('install', (evt) => {
  self.skipWaiting();
  evt.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(CORE)).catch(() => {})
  );
});

self.addEventListener('activate', (evt) => {
  evt.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.map(k => { if (k !== CACHE_NAME) return caches.delete(k); })
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (evt) => {
  if (evt.request.method !== 'GET') return;
  evt.respondWith(
    caches.match(evt.request).then(res => res || fetch(evt.request).then(fetchRes => {
      try { if (fetchRes && fetchRes.status === 200) caches.open(CACHE_NAME).then(c => c.put(evt.request, fetchRes.clone())); } catch (e) {}
      return fetchRes;
    }).catch(() => caches.match('/static/index.html')))
  );
});
