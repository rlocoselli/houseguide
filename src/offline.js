const PREFIX='hg-guide-';
export async function saveOffline(p){
 if(!('serviceWorker'in navigator)||!('caches'in window))throw Error('offline_unavailable');
 await navigator.serviceWorker.ready;
 const cache=await caches.open(PREFIX+p.code);const expires=String(Date.now()+24*3600*1000);
 const store=async(path,key=path)=>{const r=await fetch(path);if(!r.ok)throw Error('error');const headers=new Headers(r.headers);headers.set('X-HG-Expires',expires);await cache.put(key,new Response(await r.blob(),{status:200,headers}))};
 try{
  await store('/api/guides/'+p.code);
  await store('/','/offline-shell');
  const assets=[...document.querySelectorAll('script[src],link[rel="stylesheet"]')].map(x=>x.src||x.href).filter(x=>new URL(x,location.origin).origin===location.origin);
  for(const url of assets)await store(url);
  for(const url of [...new Set([p.image,...(p.images||[])])].filter(x=>x?.startsWith('/media/')))await store(url);
  // Local bundled fonts used by the saved page.
  for(const entry of performance.getEntriesByType('resource').filter(x=>new URL(x.name).origin===location.origin&&/\.woff2?(\?|$)/.test(x.name)))await store(entry.name);
 }catch(e){await caches.delete(PREFIX+p.code);throw e}
}
export async function removeOffline(code){await caches.delete(PREFIX+code)}
export async function hasOffline(code){if(!('caches'in window))return false;const c=await caches.open(PREFIX+code);const r=await c.match('/api/guides/'+code);return !!r&&Number(r.headers.get('X-HG-Expires'))>Date.now()}
