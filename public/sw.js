self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('houseguide-')).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
async function saved(request){for(const name of await caches.keys()){if(!name.startsWith('hg-guide-'))continue;const cache=await caches.open(name);const r=await cache.match(request);if(r){if(Number(r.headers.get('X-HG-Expires'))>Date.now())return r;await caches.delete(name)}}return null}
self.addEventListener('fetch',event=>{
 const url=new URL(event.request.url);
 if(event.request.method!=='GET'||url.origin!==self.location.origin)return;
 if(url.pathname.startsWith('/api/')&&!url.pathname.startsWith('/api/guides/'))return;
 event.respondWith((async()=>{try{const r=await fetch(event.request);if(url.pathname.startsWith('/api/guides/')&&(r.status===404||r.status===403))await caches.delete('hg-guide-'+url.pathname.split('/').pop());return r}catch(e){const cache=await saved(event.request);if(cache)return cache;if(event.request.mode==='navigate'){const shell=await saved('/offline-shell');if(shell)return shell}throw e}})());
});
