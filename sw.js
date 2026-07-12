/* Polis — service worker v2
   STRATEGIA (corretta dopo feedback founder):
   - App e dati: PRIMA LA RETE (ogni apertura prende la versione nuova dal sito),
     cache solo come riserva quando sei offline.
   - Icone/manifest: prima la cache (non cambiano quasi mai).
   La versione v2 elimina la vecchia cache v1 che bloccava gli aggiornamenti. */
const VERSIONE='polis-v2';
const SHELL=['./','index.html','manifest.webmanifest','icon-192.png','icon-512.png'];
const STATICI=['icon-192.png','icon-512.png','manifest.webmanifest'];

self.addEventListener('install',e=>{
  e.waitUntil(caches.open(VERSIONE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting()));
});
self.addEventListener('activate',e=>{
  e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==VERSIONE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));
});
self.addEventListener('fetch',e=>{
  const url=new URL(e.request.url);
  if(url.origin!==location.origin) return; // API esterne fuori dal SW
  const isStatico=STATICI.some(s=>url.pathname.endsWith(s));
  if(isStatico){
    // cache-first solo per le risorse immutabili
    e.respondWith(caches.match(e.request).then(hit=>hit||fetch(e.request).then(r=>{
      const cp=r.clone(); caches.open(VERSIONE).then(c=>c.put(e.request,cp)); return r;
    })));
  } else {
    // app e dati: network-first, cache come riserva offline
    e.respondWith(fetch(e.request).then(r=>{
      const cp=r.clone(); caches.open(VERSIONE).then(c=>c.put(e.request,cp)); return r;
    }).catch(()=>caches.match(e.request).then(hit=>hit||caches.match('index.html'))));
  }
});
