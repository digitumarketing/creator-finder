// Digitum Markdown Viewer — service worker
// Only exists to make the app installable and usable offline once visited.
// It never touches files the user opens; those stay entirely in the page's memory.

var CACHE = "digitum-md-viewer-v5";
var CORE_ASSETS = [
  "/",
  "/index.html",
  "/manifest.json",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/maskable-192.png",
  "/icons/maskable-512.png",
  "/brand/logo-charcoal.png",
  "/brand/logo-lime-cream.png",
  "/brand/mark-charcoal.png",
  "/brand/mark-lime.png"
];

self.addEventListener("install", function(event){
  event.waitUntil(
    caches.open(CACHE)
      .then(function(cache){ return cache.addAll(CORE_ASSETS); })
      .catch(function(){ /* offline first install is fine, just skip pre-warming */ })
  );
  self.skipWaiting();
});

self.addEventListener("activate", function(event){
  event.waitUntil(
    caches.keys().then(function(keys){
      return Promise.all(
        keys.filter(function(k){ return k !== CACHE; })
            .map(function(k){ return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

function putInCache(req, resp){
  if(resp && resp.ok){
    var copy = resp.clone();
    caches.open(CACHE).then(function(cache){ cache.put(req, copy); });
  }
  return resp;
}

self.addEventListener("fetch", function(event){
  var req = event.request;
  if(req.method !== "GET") return;
  if(new URL(req.url).origin !== self.location.origin) return;

  // Page loads go to the network first so a fresh deploy is picked up
  // immediately. Cache is only the offline fallback.
  if(req.mode === "navigate" || (req.headers.get("accept") || "").indexOf("text/html") > -1){
    event.respondWith(
      fetch(req)
        .then(function(resp){ return putInCache(req, resp); })
        .catch(function(){
          return caches.match(req).then(function(c){ return c || caches.match("/index.html"); });
        })
    );
    return;
  }

  // Static assets: serve from cache, refresh in the background.
  event.respondWith(
    caches.match(req).then(function(cached){
      var network = fetch(req)
        .then(function(resp){ return putInCache(req, resp); })
        .catch(function(){ return cached; });
      return cached || network;
    })
  );
});
