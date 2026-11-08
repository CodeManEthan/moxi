function d(e,t){return e===0?"away":(e===502||e===503||e===504)&&!t?"down":"up"}var f="/m/ui/api/push";var o="moxi-gateway",x="/offline",a="/offline.js",k=Object.freeze([["Content-Security-Policy","default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; require-trusted-types-for 'script'; manifest-src 'self'; worker-src 'self'"],["X-Content-Type-Options","nosniff"],["Referrer-Policy","no-referrer"],["Cross-Origin-Opener-Policy","same-origin"],["Cross-Origin-Resource-Policy","same-origin"],["Permissions-Policy","camera=(), microphone=(), geolocation=(), usb=(), payment=()"],["Cache-Control","no-store"]]);function c(e){let t=new Headers(k);return t.set("Content-Type",e),t}var i="/offline/answered",h="X-Moxi-Navigation",E=Object.freeze(["/pair","/signin"]);function p(e,t,n,r){return e!=="GET"||t.origin!==n?"pass":t.pathname===x||t.pathname===a?"own":r==="navigate"?E.includes(t.pathname)?"pass":"navigation":t.pathname===f?"pass":"get"}function m(e,t){return t==="opaqueredirect"?"up":e===502||e===503||e===504?"down":"up"}function g(e,t){return d(e,(t??"").split(";")[0].trim().toLowerCase()==="application/json")}var T=Date.UTC(2020,0,1),S=1440*60*1e3;function y(e,t){if(typeof e!="string"||!/^[0-9]{1,15}$/.test(e))return null;let n=Number(e);return n>=T&&n<=t+S?n:null}function w(e,t,n){let r=n===null?"":` data-last-answered="${Math.trunc(n)}"`;return e.replace(/<html\b/i,`<html data-reach="${t==="down"?"down":"away"}"${r}`)}var s=self,R=1e4,u=null,v=0;async function b(){let e=Date.now();if(u=e,e-v<R)return;v=e,await(await caches.open(o)).put(i,new Response(String(e)))}async function O(){if(u!==null)return u;try{let e=await(await caches.open(o)).match(i);return e===void 0?null:y(await e.text(),Date.now())}catch{return null}}async function l(e){let t=w(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Moxi: offline</title>
<script type="module" src="/offline.js"><\/script>
</head>
<body>
<main id="moxi-offline">
<h1>Moxi is offline</h1>
<noscript><p>The phone can't reach Moxi on the computer. Reload the page to try again.</p></noscript>
</main>
</body>
</html>
`,e,await O());return new Response(t,{status:200,headers:c("text/html; charset=utf-8")})}async function A(e){return e===a?new Response(`function s(e,n,t){let r=t===null?"":\` Moxi last answered at \${t.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}.\`;return e==="away"?\`Can't reach the computer (\${n}). It may be asleep or off, or Tailscale may be off on this phone or on the computer.\${r}\`:e==="down"?\`The computer (\${n}) answered, but Moxi isn't running or the gateway is off.\${r}\`:""}var d={bg:["#f6f4f1","#0a0918"],"bg-raised":["#fffefd","#141228"],border:["#e4dfd7","#2a2650"],text:["#1e1a22","#eeeaf8"],"text-dim":["#4f4856","#aca5cb"],accent:["#5a3ccf","#a78bfa"],error:["#a63a26","#ff7461"]};function o(e){let[n,t]=d[e];return\`light-dark(\${n}, \${t})\`}var f=\`
:root { color-scheme: light dark; }
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0; padding: 16px; background: \${o("bg")}; color: \${o("text")};
  font: 16px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 28rem; margin: 0 auto; display: grid; gap: 16px; }
h1 { margin: 0; font-size: 1.4rem; }
p { margin: 0; overflow-wrap: anywhere; }
.dim { color: \${o("text-dim")}; font-size: 0.9rem; }
.problem { color: \${o("error")}; }
label { display: grid; gap: 4px; }
input {
  font: inherit; width: 100%; padding: 10px 12px; border: 1px solid \${o("border")};
  border-radius: 6px; background: \${o("bg-raised")}; color: inherit;
}
.buttons { display: grid; gap: 12px; }
button {
  font: inherit; min-height: 48px; padding: 10px 16px; border: 1px solid \${o("accent")};
  border-radius: 10px; background: \${o("bg")}; color: \${o("accent")}; cursor: pointer;
}
button.go { background: \${o("accent")}; color: \${o("bg")}; }
button:disabled { opacity: 0.6; cursor: not-allowed; }
:focus-visible { outline: 2px solid \${o("accent")}; outline-offset: 2px; }
[hidden] { display: none !important; }
\`;function a(){let e=new CSSStyleSheet;e.replaceSync(f),document.adoptedStyleSheets=[...document.adoptedStyleSheets,e]}function i(e,n,t){let r=document.createElement(e);return n!==void 0&&(r.textContent=n),t!==void 0&&(r.className=t),r}var L=Object.freeze([["Content-Security-Policy","default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; require-trusted-types-for 'script'; manifest-src 'self'; worker-src 'self'"],["X-Content-Type-Options","nosniff"],["Referrer-Policy","no-referrer"],["Cross-Origin-Opener-Policy","same-origin"],["Cross-Origin-Resource-Policy","same-origin"],["Permissions-Policy","camera=(), microphone=(), geolocation=(), usb=(), payment=()"],["Cache-Control","no-store"]]);var D=Object.freeze(["/pair","/signin"]),u=3e4;var p=Date.UTC(2020,0,1),h=1440*60*1e3;function g(e,n){if(typeof e!="string"||!/^[0-9]{1,15}$/.test(e))return null;let t=Number(e);return t>=p&&t<=n+h?t:null}function c(e,n=Date.now()){let t=e.reach==="down"?"down":"away",r=g(e.lastAnswered,n);return{reach:t,last:r===null?null:new Date(r)}}a();var l=document.getElementById("moxi-offline");if(l!==null){let{reach:e,last:n}=c(document.documentElement.dataset),t=i("button","Try again","go");t.type="button",t.addEventListener("click",()=>location.reload());let r=i("div",void 0,"buttons");r.append(t),l.append(i("p",s(e,location.hostname,n)),r,i("p","This page tries again every 30 seconds while it's open.","dim")),setInterval(()=>{document.visibilityState==="visible"&&location.reload()},u)}
`,{status:200,headers:c("text/javascript; charset=utf-8")}):await l("away")}async function _(e){let t=new Headers(e.request.headers);t.set(h,"1");let n;try{n=await fetch(new Request(e.request,{cache:"reload",redirect:"manual",headers:t}))}catch{return await l("away")}let r=m(n.status,n.type);return r!=="up"?await l(r):(e.waitUntil(b()),n)}async function C(e){let t=e.request,n=await fetch(new Request(t,{cache:t.cache==="no-store"?"no-store":"reload"}));return g(n.status,n.headers.get("Content-Type"))==="up"&&e.waitUntil(b()),n}s.addEventListener("install",e=>{e.waitUntil(s.skipWaiting())});async function L(){for(let t of await caches.keys())t!==o&&await caches.delete(t);let e=await caches.open(o);for(let t of await e.keys())new URL(t.url).pathname!==i&&await e.delete(t)}s.addEventListener("activate",e=>{e.waitUntil(L().catch(()=>{}).then(()=>s.clients.claim()))});s.addEventListener("fetch",e=>{let t=e.request,n=new URL(t.url),r=p(t.method,n,s.location.origin,t.mode);r!=="pass"&&(r==="own"?e.respondWith(A(n.pathname)):e.respondWith(r==="navigation"?_(e):C(e)))});
