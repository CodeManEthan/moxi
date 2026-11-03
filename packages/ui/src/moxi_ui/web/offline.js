function s(e,n,t){let r=t===null?"":` Moxi last answered at ${t.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}.`;return e==="away"?`Can't reach the computer (${n}). It may be asleep or off, or Tailscale may be off on this phone or on the computer.${r}`:e==="down"?`The computer (${n}) answered, but Moxi isn't running or the gateway is off.${r}`:""}var d={bg:["#ffffff","#14171c"],"bg-raised":["#ffffff","#1a1e25"],border:["#d9dde3","#2b313b"],text:["#14181f","#e6e9ee"],"text-dim":["#5b6472","#a0a9b6"],accent:["#2f6fd0","#6ea8ff"],error:["#c0392b","#ff8a7a"]};function o(e){let[n,t]=d[e];return`light-dark(${n}, ${t})`}var f=`
:root { color-scheme: light dark; }
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0; padding: 16px; background: ${o("bg")}; color: ${o("text")};
  font: 16px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 28rem; margin: 0 auto; display: grid; gap: 16px; }
h1 { margin: 0; font-size: 1.4rem; }
p { margin: 0; overflow-wrap: anywhere; }
.dim { color: ${o("text-dim")}; font-size: 0.9rem; }
.problem { color: ${o("error")}; }
label { display: grid; gap: 4px; }
input {
  font: inherit; width: 100%; padding: 10px 12px; border: 1px solid ${o("border")};
  border-radius: 6px; background: ${o("bg-raised")}; color: inherit;
}
.buttons { display: grid; gap: 12px; }
button {
  font: inherit; min-height: 48px; padding: 10px 16px; border: 1px solid ${o("accent")};
  border-radius: 10px; background: ${o("bg")}; color: ${o("accent")}; cursor: pointer;
}
button.go { background: ${o("accent")}; color: ${o("bg")}; }
button:disabled { opacity: 0.6; cursor: not-allowed; }
:focus-visible { outline: 2px solid ${o("accent")}; outline-offset: 2px; }
[hidden] { display: none !important; }
`;function a(){let e=new CSSStyleSheet;e.replaceSync(f),document.adoptedStyleSheets=[...document.adoptedStyleSheets,e]}function i(e,n,t){let r=document.createElement(e);return n!==void 0&&(r.textContent=n),t!==void 0&&(r.className=t),r}var L=Object.freeze([["Content-Security-Policy","default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; require-trusted-types-for 'script'; manifest-src 'self'; worker-src 'self'"],["X-Content-Type-Options","nosniff"],["Referrer-Policy","no-referrer"],["Cross-Origin-Opener-Policy","same-origin"],["Cross-Origin-Resource-Policy","same-origin"],["Permissions-Policy","camera=(), microphone=(), geolocation=(), usb=(), payment=()"],["Cache-Control","no-store"]]);var D=Object.freeze(["/pair","/signin"]),u=3e4;var p=Date.UTC(2020,0,1),h=1440*60*1e3;function g(e,n){if(typeof e!="string"||!/^[0-9]{1,15}$/.test(e))return null;let t=Number(e);return t>=p&&t<=n+h?t:null}function c(e,n=Date.now()){let t=e.reach==="down"?"down":"away",r=g(e.lastAnswered,n);return{reach:t,last:r===null?null:new Date(r)}}a();var l=document.getElementById("moxi-offline");if(l!==null){let{reach:e,last:n}=c(document.documentElement.dataset),t=i("button","Try again","go");t.type="button",t.addEventListener("click",()=>location.reload());let r=i("div",void 0,"buttons");r.append(t),l.append(i("p",s(e,location.hostname,n)),r,i("p","This page tries again every 30 seconds while it's open.","dim")),setInterval(()=>{document.visibilityState==="visible"&&location.reload()},u)}
