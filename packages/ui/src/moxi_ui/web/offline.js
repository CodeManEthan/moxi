function s(e,r,t){let o=t===null?"":` Moxi last answered at ${t.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}.`;return e==="away"?`Can't reach the computer (${r}). It may be asleep or off, or Tailscale may be off on this phone or on the computer.${o}`:e==="down"?`The computer (${r}) answered, but Moxi isn't running or the gateway is off.${o}`:""}var d={bg:["#ffffff","#14171c"],"bg-raised":["#ffffff","#1a1e25"],border:["#d9dde3","#2b313b"],text:["#14181f","#e6e9ee"],"text-dim":["#5b6472","#a0a9b6"],accent:["#2f6fd0","#6ea8ff"],error:["#c0392b","#ff8a7a"]};function n(e){let[r,t]=d[e];return`light-dark(${r}, ${t})`}var f=`
:root { color-scheme: light dark; }
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0; padding: 16px; background: ${n("bg")}; color: ${n("text")};
  font: 16px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 28rem; margin: 0 auto; display: grid; gap: 16px; }
h1 { margin: 0; font-size: 1.4rem; }
p { margin: 0; overflow-wrap: anywhere; }
.dim { color: ${n("text-dim")}; font-size: 0.9rem; }
.problem { color: ${n("error")}; }
label { display: grid; gap: 4px; }
input {
  font: inherit; width: 100%; padding: 10px 12px; border: 1px solid ${n("border")};
  border-radius: 6px; background: ${n("bg-raised")}; color: inherit;
}
.buttons { display: grid; gap: 12px; }
button {
  font: inherit; min-height: 48px; padding: 10px 16px; border: 1px solid ${n("accent")};
  border-radius: 10px; background: ${n("bg")}; color: ${n("accent")}; cursor: pointer;
}
button.go { background: ${n("accent")}; color: ${n("bg")}; }
button:disabled { opacity: 0.6; cursor: not-allowed; }
:focus-visible { outline: 2px solid ${n("accent")}; outline-offset: 2px; }
[hidden] { display: none !important; }
`;function a(){let e=new CSSStyleSheet;e.replaceSync(f),document.adoptedStyleSheets=[...document.adoptedStyleSheets,e]}function i(e,r,t){let o=document.createElement(e);return r!==void 0&&(o.textContent=r),t!==void 0&&(o.className=t),o}var p="/offline",h="/offline.js",P=Object.freeze(["/m/ui/client.js","/m/ui/theme.css","/manifest.webmanifest","/icon-192.png","/icon-512.png",p,h]);var L=Object.freeze(["/pair","/signin"]),u=3e4;function c(e){let r=e.reach==="down"?"down":"away",t=Number(e.lastAnswered),o=e.lastAnswered!==void 0&&e.lastAnswered!==""&&Number.isFinite(t)?new Date(t):null;return{reach:r,last:o}}a();var l=document.getElementById("moxi-offline");if(l!==null){let{reach:e,last:r}=c(document.documentElement.dataset),t=i("button","Try again","go");t.type="button",t.addEventListener("click",()=>location.reload());let o=i("div",void 0,"buttons");o.append(t),l.append(i("p",s(e,location.hostname,r)),o,i("p","This page tries again every 30 seconds while it's open.","dim")),setInterval(()=>{document.visibilityState==="visible"&&location.reload()},u)}
