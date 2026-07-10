/* Polis — service worker: app disponibile offline, dati sempre freschi quando c'è rete */
const VERSIONE='polis-v1';
const SHELL=['./','index.html','manifest.webmanifest','icon-192.png','icon-512.png'];

self.addEventListener('install',e=>{
  e.waitUntil(caches.open(VERSIONE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting()));
});
self.addEventListener('activate',e=>{
  e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==VERSIONE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));
});
self.addEventListener('fetch',e=>{
  const url=new URL(e.request.url);
  if(url.origin!==location.origin) return; // le API esterne non passano dal SW
  if(url.pathname.includes('/dati/')){
    // dati: prima la rete (freschezza), cache come riserva offline
    e.respondWith(fetch(e.request).then(r=>{
      const cp=r.clone(); caches.open(VERSIONE).then(c=>c.put(e.request,cp)); return r;
    }).catch(()=>caches.match(e.request)));
  } else {
    // app: prima la cache (velocità), rete come aggiornamento
    e.respondWith(caches.match(e.request).then(hit=>hit||fetch(e.request).then(r=>{
      const cp=r.clone(); caches.open(VERSIONE).then(c=>c.put(e.request,cp)); return r;
    })));
  }
});
