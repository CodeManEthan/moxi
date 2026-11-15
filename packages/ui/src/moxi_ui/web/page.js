var W=globalThis,G=W.ShadowRoot&&(W.ShadyCSS===void 0||W.ShadyCSS.nativeShadow)&&"adoptedStyleSheets"in Document.prototype&&"replace"in CSSStyleSheet.prototype,ct=Symbol(),Wt=new WeakMap,z=class{constructor(e,t,n){if(this._$cssResult$=!0,n!==ct)throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");this.cssText=e,this.t=t}get styleSheet(){let e=this.o,t=this.t;if(G&&e===void 0){let n=t!==void 0&&t.length===1;n&&(e=Wt.get(t)),e===void 0&&((this.o=e=new CSSStyleSheet).replaceSync(this.cssText),n&&Wt.set(t,e))}return e}toString(){return this.cssText}},D=s=>new z(typeof s=="string"?s:s+"",void 0,ct),_=(s,...e)=>{let t=s.length===1?s[0]:e.reduce((n,i,r)=>n+(o=>{if(o._$cssResult$===!0)return o.cssText;if(typeof o=="number")return o;throw Error("Value passed to 'css' function must be a 'css' function result: "+o+". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.")})(i)+s[r+1],s[0]);return new z(t,s,ct)},Gt=(s,e)=>{if(G)s.adoptedStyleSheets=e.map(t=>t instanceof CSSStyleSheet?t:t.styleSheet);else for(let t of e){let n=document.createElement("style"),i=W.litNonce;i!==void 0&&n.setAttribute("nonce",i),n.textContent=t.cssText,s.appendChild(n)}},pt=G?s=>s:s=>s instanceof CSSStyleSheet?(e=>{let t="";for(let n of e.cssRules)t+=n.cssText;return D(t)})(s):s;var{is:bn,defineProperty:$n,getOwnPropertyDescriptor:yn,getOwnPropertyNames:vn,getOwnPropertySymbols:wn,getPrototypeOf:_n}=Object,Q=globalThis,Qt=Q.trustedTypes,An=Qt?Qt.emptyScript:"",kn=Q.reactiveElementPolyfillSupport,U=(s,e)=>s,ht={toAttribute(s,e){switch(e){case Boolean:s=s?An:null;break;case Object:case Array:s=s==null?s:JSON.stringify(s)}return s},fromAttribute(s,e){let t=s;switch(e){case Boolean:t=s!==null;break;case Number:t=s===null?null:Number(s);break;case Object:case Array:try{t=JSON.parse(s)}catch{t=null}}return t}},Jt=(s,e)=>!bn(s,e),Yt={attribute:!0,type:String,converter:ht,reflect:!1,useDefault:!1,hasChanged:Jt};Symbol.metadata??=Symbol("metadata"),Q.litPropertyMetadata??=new WeakMap;var A=class extends HTMLElement{static addInitializer(e){this._$Ei(),(this.l??=[]).push(e)}static get observedAttributes(){return this.finalize(),this._$Eh&&[...this._$Eh.keys()]}static createProperty(e,t=Yt){if(t.state&&(t.attribute=!1),this._$Ei(),this.prototype.hasOwnProperty(e)&&((t=Object.create(t)).wrapped=!0),this.elementProperties.set(e,t),!t.noAccessor){let n=Symbol(),i=this.getPropertyDescriptor(e,n,t);i!==void 0&&$n(this.prototype,e,i)}}static getPropertyDescriptor(e,t,n){let{get:i,set:r}=yn(this.prototype,e)??{get(){return this[t]},set(o){this[t]=o}};return{get:i,set(o){let l=i?.call(this);r?.call(this,o),this.requestUpdate(e,l,n)},configurable:!0,enumerable:!0}}static getPropertyOptions(e){return this.elementProperties.get(e)??Yt}static _$Ei(){if(this.hasOwnProperty(U("elementProperties")))return;let e=_n(this);e.finalize(),e.l!==void 0&&(this.l=[...e.l]),this.elementProperties=new Map(e.elementProperties)}static finalize(){if(this.hasOwnProperty(U("finalized")))return;if(this.finalized=!0,this._$Ei(),this.hasOwnProperty(U("properties"))){let t=this.properties,n=[...vn(t),...wn(t)];for(let i of n)this.createProperty(i,t[i])}let e=this[Symbol.metadata];if(e!==null){let t=litPropertyMetadata.get(e);if(t!==void 0)for(let[n,i]of t)this.elementProperties.set(n,i)}this._$Eh=new Map;for(let[t,n]of this.elementProperties){let i=this._$Eu(t,n);i!==void 0&&this._$Eh.set(i,t)}this.elementStyles=this.finalizeStyles(this.styles)}static finalizeStyles(e){let t=[];if(Array.isArray(e)){let n=new Set(e.flat(1/0).reverse());for(let i of n)t.unshift(pt(i))}else e!==void 0&&t.push(pt(e));return t}static _$Eu(e,t){let n=t.attribute;return n===!1?void 0:typeof n=="string"?n:typeof e=="string"?e.toLowerCase():void 0}constructor(){super(),this._$Ep=void 0,this.isUpdatePending=!1,this.hasUpdated=!1,this._$Em=null,this._$Ev()}_$Ev(){this._$ES=new Promise(e=>this.enableUpdating=e),this._$AL=new Map,this._$E_(),this.requestUpdate(),this.constructor.l?.forEach(e=>e(this))}addController(e){(this._$EO??=new Set).add(e),this.renderRoot!==void 0&&this.isConnected&&e.hostConnected?.()}removeController(e){this._$EO?.delete(e)}_$E_(){let e=new Map,t=this.constructor.elementProperties;for(let n of t.keys())this.hasOwnProperty(n)&&(e.set(n,this[n]),delete this[n]);e.size>0&&(this._$Ep=e)}createRenderRoot(){let e=this.shadowRoot??this.attachShadow(this.constructor.shadowRootOptions);return Gt(e,this.constructor.elementStyles),e}connectedCallback(){this.renderRoot??=this.createRenderRoot(),this.enableUpdating(!0),this._$EO?.forEach(e=>e.hostConnected?.())}enableUpdating(e){}disconnectedCallback(){this._$EO?.forEach(e=>e.hostDisconnected?.())}attributeChangedCallback(e,t,n){this._$AK(e,n)}_$ET(e,t){let n=this.constructor.elementProperties.get(e),i=this.constructor._$Eu(e,n);if(i!==void 0&&n.reflect===!0){let r=(n.converter?.toAttribute!==void 0?n.converter:ht).toAttribute(t,n.type);this._$Em=e,r==null?this.removeAttribute(i):this.setAttribute(i,r),this._$Em=null}}_$AK(e,t){let n=this.constructor,i=n._$Eh.get(e);if(i!==void 0&&this._$Em!==i){let r=n.getPropertyOptions(i),o=typeof r.converter=="function"?{fromAttribute:r.converter}:r.converter?.fromAttribute!==void 0?r.converter:ht;this._$Em=i;let l=o.fromAttribute(t,r.type);this[i]=l??this._$Ej?.get(i)??l,this._$Em=null}}requestUpdate(e,t,n,i=!1,r){if(e!==void 0){let o=this.constructor;if(i===!1&&(r=this[e]),n??=o.getPropertyOptions(e),!((n.hasChanged??Jt)(r,t)||n.useDefault&&n.reflect&&r===this._$Ej?.get(e)&&!this.hasAttribute(o._$Eu(e,n))))return;this.C(e,t,n)}this.isUpdatePending===!1&&(this._$ES=this._$EP())}C(e,t,{useDefault:n,reflect:i,wrapped:r},o){n&&!(this._$Ej??=new Map).has(e)&&(this._$Ej.set(e,o??t??this[e]),r!==!0||o!==void 0)||(this._$AL.has(e)||(this.hasUpdated||n||(t=void 0),this._$AL.set(e,t)),i===!0&&this._$Em!==e&&(this._$Eq??=new Set).add(e))}async _$EP(){this.isUpdatePending=!0;try{await this._$ES}catch(t){Promise.reject(t)}let e=this.scheduleUpdate();return e!=null&&await e,!this.isUpdatePending}scheduleUpdate(){return this.performUpdate()}performUpdate(){if(!this.isUpdatePending)return;if(!this.hasUpdated){if(this.renderRoot??=this.createRenderRoot(),this._$Ep){for(let[i,r]of this._$Ep)this[i]=r;this._$Ep=void 0}let n=this.constructor.elementProperties;if(n.size>0)for(let[i,r]of n){let{wrapped:o}=r,l=this[i];o!==!0||this._$AL.has(i)||l===void 0||this.C(i,void 0,r,l)}}let e=!1,t=this._$AL;try{e=this.shouldUpdate(t),e?(this.willUpdate(t),this._$EO?.forEach(n=>n.hostUpdate?.()),this.update(t)):this._$EM()}catch(n){throw e=!1,this._$EM(),n}e&&this._$AE(t)}willUpdate(e){}_$AE(e){this._$EO?.forEach(t=>t.hostUpdated?.()),this.hasUpdated||(this.hasUpdated=!0,this.firstUpdated(e)),this.updated(e)}_$EM(){this._$AL=new Map,this.isUpdatePending=!1}get updateComplete(){return this.getUpdateComplete()}getUpdateComplete(){return this._$ES}shouldUpdate(e){return!0}update(e){this._$Eq&&=this._$Eq.forEach(t=>this._$ET(t,this[t])),this._$EM()}updated(e){}firstUpdated(e){}};A.elementStyles=[],A.shadowRootOptions={mode:"open"},A[U("elementProperties")]=new Map,A[U("finalized")]=new Map,kn?.({ReactiveElement:A}),(Q.reactiveElementVersions??=[]).push("2.1.2");var ft=globalThis,Xt=s=>s,Y=ft.trustedTypes,Zt=Y?Y.createPolicy("lit-html",{createHTML:s=>s}):void 0,gt="$lit$",k=`lit$${Math.random().toFixed(9).slice(2)}$`,xt="?"+k,En=`<${xt}>`,M=document,j=()=>M.createComment(""),V=s=>s===null||typeof s!="object"&&typeof s!="function",bt=Array.isArray,re=s=>bt(s)||typeof s?.[Symbol.iterator]=="function",mt=`[ 	
\f\r]`,I=/<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g,te=/-->/g,ee=/>/g,C=RegExp(`>|${mt}(?:([^\\s"'>=/]+)(${mt}*=${mt}*(?:[^ 	
\f\r"'\`<>=]|("|')|))|$)`,"g"),ne=/'/g,se=/"/g,oe=/^(?:script|style|textarea|title)$/i,$t=s=>(e,...t)=>({_$litType$:s,strings:e,values:t}),d=$t(1),Rs=$t(2),Cs=$t(3),x=Symbol.for("lit-noChange"),u=Symbol.for("lit-nothing"),ie=new WeakMap,T=M.createTreeWalker(M,129);function ae(s,e){if(!bt(s)||!s.hasOwnProperty("raw"))throw Error("invalid template strings array");return Zt!==void 0?Zt.createHTML(e):e}var le=(s,e)=>{let t=s.length-1,n=[],i,r=e===2?"<svg>":e===3?"<math>":"",o=I;for(let l=0;l<t;l++){let a=s[l],c,h,p=-1,m=0;for(;m<a.length&&(o.lastIndex=m,h=o.exec(a),h!==null);)m=o.lastIndex,o===I?h[1]==="!--"?o=te:h[1]!==void 0?o=ee:h[2]!==void 0?(oe.test(h[2])&&(i=RegExp("</"+h[2],"g")),o=C):h[3]!==void 0&&(o=C):o===C?h[0]===">"?(o=i??I,p=-1):h[1]===void 0?p=-2:(p=o.lastIndex-h[2].length,c=h[1],o=h[3]===void 0?C:h[3]==='"'?se:ne):o===se||o===ne?o=C:o===te||o===ee?o=I:(o=C,i=void 0);let f=o===C&&s[l+1].startsWith("/>")?" ":"";r+=o===I?a+En:p>=0?(n.push(c),a.slice(0,p)+gt+a.slice(p)+k+f):a+k+(p===-2?l:f)}return[ae(s,r+(s[t]||"<?>")+(e===2?"</svg>":e===3?"</math>":"")),n]},B=class s{constructor({strings:e,_$litType$:t},n){let i;this.parts=[];let r=0,o=0,l=e.length-1,a=this.parts,[c,h]=le(e,t);if(this.el=s.createElement(c,n),T.currentNode=this.el.content,t===2||t===3){let p=this.el.content.firstChild;p.replaceWith(...p.childNodes)}for(;(i=T.nextNode())!==null&&a.length<l;){if(i.nodeType===1){if(i.hasAttributes())for(let p of i.getAttributeNames())if(p.endsWith(gt)){let m=h[o++],f=i.getAttribute(p).split(k),g=/([.?@])?(.*)/.exec(m);a.push({type:1,index:r,name:g[2],strings:f,ctor:g[1]==="."?X:g[1]==="?"?Z:g[1]==="@"?tt:O}),i.removeAttribute(p)}else p.startsWith(k)&&(a.push({type:6,index:r}),i.removeAttribute(p));if(oe.test(i.tagName)){let p=i.textContent.split(k),m=p.length-1;if(m>0){i.textContent=Y?Y.emptyScript:"";for(let f=0;f<m;f++)i.append(p[f],j()),T.nextNode(),a.push({type:2,index:++r});i.append(p[m],j())}}}else if(i.nodeType===8)if(i.data===xt)a.push({type:2,index:r});else{let p=-1;for(;(p=i.data.indexOf(k,p+1))!==-1;)a.push({type:7,index:r}),p+=k.length-1}r++}}static createElement(e,t){let n=M.createElement("template");return n.innerHTML=e,n}};function P(s,e,t=s,n){if(e===x)return e;let i=n!==void 0?t._$Co?.[n]:t._$Cl,r=V(e)?void 0:e._$litDirective$;return i?.constructor!==r&&(i?._$AO?.(!1),r===void 0?i=void 0:(i=new r(s),i._$AT(s,t,n)),n!==void 0?(t._$Co??=[])[n]=i:t._$Cl=i),i!==void 0&&(e=P(s,i._$AS(s,e.values),i,n)),e}var J=class{constructor(e,t){this._$AV=[],this._$AN=void 0,this._$AD=e,this._$AM=t}get parentNode(){return this._$AM.parentNode}get _$AU(){return this._$AM._$AU}u(e){let{el:{content:t},parts:n}=this._$AD,i=(e?.creationScope??M).importNode(t,!0);T.currentNode=i;let r=T.nextNode(),o=0,l=0,a=n[0];for(;a!==void 0;){if(o===a.index){let c;a.type===2?c=new N(r,r.nextSibling,this,e):a.type===1?c=new a.ctor(r,a.name,a.strings,this,e):a.type===6&&(c=new et(r,this,e)),this._$AV.push(c),a=n[++l]}o!==a?.index&&(r=T.nextNode(),o++)}return T.currentNode=M,i}p(e){let t=0;for(let n of this._$AV)n!==void 0&&(n.strings!==void 0?(n._$AI(e,n,t),t+=n.strings.length-2):n._$AI(e[t])),t++}},N=class s{get _$AU(){return this._$AM?._$AU??this._$Cv}constructor(e,t,n,i){this.type=2,this._$AH=u,this._$AN=void 0,this._$AA=e,this._$AB=t,this._$AM=n,this.options=i,this._$Cv=i?.isConnected??!0}get parentNode(){let e=this._$AA.parentNode,t=this._$AM;return t!==void 0&&e?.nodeType===11&&(e=t.parentNode),e}get startNode(){return this._$AA}get endNode(){return this._$AB}_$AI(e,t=this){e=P(this,e,t),V(e)?e===u||e==null||e===""?(this._$AH!==u&&this._$AR(),this._$AH=u):e!==this._$AH&&e!==x&&this._(e):e._$litType$!==void 0?this.$(e):e.nodeType!==void 0?this.T(e):re(e)?this.k(e):this._(e)}O(e){return this._$AA.parentNode.insertBefore(e,this._$AB)}T(e){this._$AH!==e&&(this._$AR(),this._$AH=this.O(e))}_(e){this._$AH!==u&&V(this._$AH)?this._$AA.nextSibling.data=e:this.T(M.createTextNode(e)),this._$AH=e}$(e){let{values:t,_$litType$:n}=e,i=typeof n=="number"?this._$AC(e):(n.el===void 0&&(n.el=B.createElement(ae(n.h,n.h[0]),this.options)),n);if(this._$AH?._$AD===i)this._$AH.p(t);else{let r=new J(i,this),o=r.u(this.options);r.p(t),this.T(o),this._$AH=r}}_$AC(e){let t=ie.get(e.strings);return t===void 0&&ie.set(e.strings,t=new B(e)),t}k(e){bt(this._$AH)||(this._$AH=[],this._$AR());let t=this._$AH,n,i=0;for(let r of e)i===t.length?t.push(n=new s(this.O(j()),this.O(j()),this,this.options)):n=t[i],n._$AI(r),i++;i<t.length&&(this._$AR(n&&n._$AB.nextSibling,i),t.length=i)}_$AR(e=this._$AA.nextSibling,t){for(this._$AP?.(!1,!0,t);e!==this._$AB;){let n=Xt(e).nextSibling;Xt(e).remove(),e=n}}setConnected(e){this._$AM===void 0&&(this._$Cv=e,this._$AP?.(e))}},O=class{get tagName(){return this.element.tagName}get _$AU(){return this._$AM._$AU}constructor(e,t,n,i,r){this.type=1,this._$AH=u,this._$AN=void 0,this.element=e,this.name=t,this._$AM=i,this.options=r,n.length>2||n[0]!==""||n[1]!==""?(this._$AH=Array(n.length-1).fill(new String),this.strings=n):this._$AH=u}_$AI(e,t=this,n,i){let r=this.strings,o=!1;if(r===void 0)e=P(this,e,t,0),o=!V(e)||e!==this._$AH&&e!==x,o&&(this._$AH=e);else{let l=e,a,c;for(e=r[0],a=0;a<r.length-1;a++)c=P(this,l[n+a],t,a),c===x&&(c=this._$AH[a]),o||=!V(c)||c!==this._$AH[a],c===u?e=u:e!==u&&(e+=(c??"")+r[a+1]),this._$AH[a]=c}o&&!i&&this.j(e)}j(e){e===u?this.element.removeAttribute(this.name):this.element.setAttribute(this.name,e??"")}},X=class extends O{constructor(){super(...arguments),this.type=3}j(e){this.element[this.name]=e===u?void 0:e}},Z=class extends O{constructor(){super(...arguments),this.type=4}j(e){this.element.toggleAttribute(this.name,!!e&&e!==u)}},tt=class extends O{constructor(e,t,n,i,r){super(e,t,n,i,r),this.type=5}_$AI(e,t=this){if((e=P(this,e,t,0)??u)===x)return;let n=this._$AH,i=e===u&&n!==u||e.capture!==n.capture||e.once!==n.once||e.passive!==n.passive,r=e!==u&&(n===u||i);i&&this.element.removeEventListener(this.name,this,n),r&&this.element.addEventListener(this.name,this,e),this._$AH=e}handleEvent(e){typeof this._$AH=="function"?this._$AH.call(this.options?.host??this.element,e):this._$AH.handleEvent(e)}},et=class{constructor(e,t,n){this.element=e,this.type=6,this._$AN=void 0,this._$AM=t,this.options=n}get _$AU(){return this._$AM._$AU}_$AI(e){P(this,e)}},de={M:gt,P:k,A:xt,C:1,L:le,R:J,D:re,V:P,I:N,H:O,N:Z,U:tt,B:X,F:et},Sn=ft.litHtmlPolyfillSupport;Sn?.(B,N),(ft.litHtmlVersions??=[]).push("3.3.3");var ue=(s,e,t)=>{let n=t?.renderBefore??e,i=n._$litPart$;if(i===void 0){let r=t?.renderBefore??null;n._$litPart$=i=new N(e.insertBefore(j(),r),r,void 0,t??{})}return i._$AI(s),i};var yt=globalThis,b=class extends A{constructor(){super(...arguments),this.renderOptions={host:this},this._$Do=void 0}createRenderRoot(){let e=super.createRenderRoot();return this.renderOptions.renderBefore??=e.firstChild,e}update(e){let t=this.render();this.hasUpdated||(this.renderOptions.isConnected=this.isConnected),super.update(e),this._$Do=ue(t,this.renderRoot,this.renderOptions)}connectedCallback(){super.connectedCallback(),this._$Do?.setConnected(!0)}disconnectedCallback(){super.disconnectedCallback(),this._$Do?.setConnected(!1)}render(){return x}};b._$litElement$=!0,b.finalized=!0,yt.litElementHydrateSupport?.({LitElement:b});var Rn=yt.litElementPolyfillSupport;Rn?.({LitElement:b});(yt.litElementVersions??=[]).push("4.2.2");import{api as bs,components as Kt,gateway as dt,installed as gn,navigate as $s,place as ys,subscribe as vs}from"/m/ui/client.js";import{api as nt}from"/m/ui/client.js";var ce=[-1,10,16,26,18,24,16,18,22,22,26,30,22,22,24,24,28,28,26,26,26,26,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28],pe=[-1,1,1,1,2,2,4,4,4,5,5,5,8,9,9,10,10,11,13,14,16,17,17,18,20,21,23,25,26,28,29,31,33,35,37,38,40,43,45,47,49],Cn=0;function v(s,e){return(s>>>e&1)!==0}function he(s){let e=(16*s+128)*s+64;if(s>=2){let t=Math.floor(s/7)+2;e-=(25*t-10)*t-55,s>=7&&(e-=36)}return e}function Tn(s){return Math.floor(he(s)/8)-ce[s]*pe[s]}function Mn(s){if(s===1)return[];let e=Math.floor(s/7)+2,t=Math.floor((s*8+e*3+5)/(e*4-4))*2,n=[6];for(let i=s*4+10;n.length<e;i-=t)n.splice(1,0,i);return n}function vt(s,e){let t=0;for(let n=7;n>=0;n--)t=t<<1^(t>>>7)*285,t^=(e>>>n&1)*s;return t}function Pn(s){let e=new Array(s).fill(0);e[s-1]=1;let t=1;for(let n=0;n<s;n++){for(let i=0;i<e.length;i++)e[i]=vt(e[i],t),i+1<e.length&&(e[i]^=e[i+1]);t=vt(t,2)}return e}function On(s,e){let t=new Array(e.length).fill(0);for(let n of s){let i=n^t.shift();t.push(0),e.forEach((r,o)=>{t[o]^=vt(r,i)})}return t}function Nn(s,e){let t=pe[e],n=ce[e],i=Math.floor(he(e)/8),r=t-i%t,o=Math.floor(i/t),l=Pn(n),a=[];for(let h=0,p=0;h<t;h++){let m=s.slice(p,p+o-n+(h<r?0:1));p+=m.length;let f=On(m,l);h<r&&m.push(0),a.push(m.concat(f))}let c=[];for(let h=0;h<a[0].length;h++)a.forEach((p,m)=>{(h!==o-n||m>=r)&&c.push(p[h])});return c}function Ln(s,e){let t=Tn(e)*8,n=[],i=(l,a)=>{for(let c=a-1;c>=0;c--)n.push(l>>>c&1)},r=e<=9?8:16;if(s.length>=2**r)return null;i(4,4),i(s.length,r);for(let l of s)i(l,8);if(n.length>t)return null;i(0,Math.min(4,t-n.length)),i(0,(8-n.length%8)%8);let o=[];for(let l=0;l<n.length;l+=8)o.push(n.slice(l,l+8).reduce((a,c)=>a<<1|c,0));for(let l=236;o.length<t/8;l^=253)o.push(l);return o}var wt=class{constructor(e){this.version=e,this.size=e*4+17,this.dark=Array.from({length:this.size},()=>new Array(this.size).fill(!1)),this.fixed=Array.from({length:this.size},()=>new Array(this.size).fill(!1))}set(e,t,n){this.dark[t][e]=n,this.fixed[t][e]=!0}patterns(){let{size:e}=this;for(let i=0;i<e;i++)this.set(6,i,i%2===0),this.set(i,6,i%2===0);for(let[i,r]of[[3,3],[e-4,3],[3,e-4]])for(let o=-4;o<=4;o++)for(let l=-4;l<=4;l++){let a=i+l,c=r+o,h=Math.max(Math.abs(l),Math.abs(o));a>=0&&a<e&&c>=0&&c<e&&this.set(a,c,h!==2&&h!==4)}let t=Mn(this.version),n=t.length-1;if(t.forEach((i,r)=>t.forEach((o,l)=>{if(!(r===0&&l===0||r===0&&l===n||r===n&&l===0))for(let a=-2;a<=2;a++)for(let c=-2;c<=2;c++)this.set(i+c,o+a,Math.max(Math.abs(c),Math.abs(a))!==1)})),this.format(0),this.version>=7){let i=this.version;for(let o=0;o<12;o++)i=i<<1^(i>>>11)*7973;let r=this.version<<12|i;for(let o=0;o<18;o++){let l=e-11+o%3,a=Math.floor(o/3);this.set(l,a,v(r,o)),this.set(a,l,v(r,o))}}}format(e){let t=Cn<<3|e,n=t;for(let o=0;o<10;o++)n=n<<1^(n>>>9)*1335;let i=(t<<10|n)^21522,{size:r}=this;for(let o=0;o<=5;o++)this.set(8,o,v(i,o));this.set(8,7,v(i,6)),this.set(8,8,v(i,7)),this.set(7,8,v(i,8));for(let o=9;o<15;o++)this.set(14-o,8,v(i,o));for(let o=0;o<8;o++)this.set(r-1-o,8,v(i,o));for(let o=8;o<15;o++)this.set(8,r-15+o,v(i,o));this.set(8,r-8,!0)}place(e){let{size:t}=this,n=0;for(let i=t-1;i>=1;i-=2){i===6&&(i=5);for(let r=0;r<t;r++)for(let o=0;o<2;o++){let l=i-o,a=(i+1&2)===0?t-1-r:r;!this.fixed[a][l]&&n<e.length*8&&(this.dark[a][l]=v(e[n>>>3],7-(n&7)),n++)}}}mask(e){for(let t=0;t<this.size;t++)for(let n=0;n<this.size;n++)!this.fixed[t][n]&&Hn[e](n,t)&&(this.dark[t][n]=!this.dark[t][n])}},Hn=[(s,e)=>(s+e)%2===0,(s,e)=>e%2===0,s=>s%3===0,(s,e)=>(s+e)%3===0,(s,e)=>(Math.floor(s/3)+Math.floor(e/2))%2===0,(s,e)=>s*e%2+s*e%3===0,(s,e)=>(s*e%2+s*e%3)%2===0,(s,e)=>((s+e)%2+s*e%3)%2===0];function zn(s){let e=s.length,t=0,n=[];for(let l=0;l<e;l++)n.push(s[l]),n.push(s.map(a=>a[l]));let i=[!0,!1,!0,!0,!0,!1,!0];for(let l of n){for(let a=0;a<e;){let c=a;for(;c<e&&l[c]===l[a];)c++;c-a>=5&&(t+=c-a-2),a=c}for(let a=0;a+7<=e;a++){if(!i.every((h,p)=>l[a+p]===h))continue;let c=(h,p)=>{for(let m=1;m<=4;m++){let f=h+p*m;if(f>=0&&f<e&&l[f])return!1}return!0};c(a,-1)&&(t+=40),c(a+6,1)&&(t+=40)}}for(let l=0;l+1<e;l++)for(let a=0;a+1<e;a++){let c=s[l][a];c===s[l][a+1]&&c===s[l+1][a]&&c===s[l+1][a+1]&&(t+=3)}let r=e*e,o=s.reduce((l,a)=>l+a.filter(Boolean).length,0);return t+=Math.max(0,Math.ceil(Math.abs(o*20-r*10)/r)-1)*10,t}function me(s,e){let t=new TextEncoder().encode(s);for(let n=1;n<=40;n++){let i=Ln(t,n);if(i===null)continue;let r=Nn(i,n),o=e===void 0?[0,1,2,3,4,5,6,7]:[e],l=null;for(let c of o){let h=new wt(n);h.patterns(),h.place(r),h.mask(c),h.format(c);let p=o.length===1?0:zn(h.dark);(l===null||p<l.score)&&(l={mask:c,score:p,dark:h.dark})}let a=l;return{version:n,mask:a.mask,size:n*4+17,dark:a.dark}}throw new RangeError("the text is too long for a QR code")}function fe(s){let e=[];return s.dark.forEach((t,n)=>t.forEach((i,r)=>{i&&e.push(`M${r} ${n}h1v1h-1z`)})),e.join("")}var ge="moxi-ui-gateway",xe=12e4,Dn=3e3;function be(s){return!Number.isFinite(s)||s<=0?"never":new Date(s*1e3).toLocaleString([],{dateStyle:"medium",timeStyle:"short"})}function $e(s){let e=Date.parse(s);return Number.isFinite(e)?new Date(e).toLocaleString([],{dateStyle:"medium",timeStyle:"medium"}):s}function st(s,e,t){if(s===0)return`No answer from the shell, so ${t} failed.`;if(s===403)return"This browser is signed out.";let n=e?.refused??e?.conflict??e?.error;return typeof n=="string"&&n?n:`The shell answered ${s} for ${t}.`}var _t=class extends b{constructor(){super(...arguments);this.devices=null;this.listError=null;this.code=null;this.note=null;this.busy=!1;this.rows=null;this.signins=new Map;this.logError=null;this.#t=null;this.#i=new Set}static{this.styles=_`
    :host { display: block; min-width: 0; }
    * { box-sizing: border-box; }
    h2 { margin: 0 0 var(--moxi-gap); font-size: 1.1em; }
    p { margin: 0; overflow-wrap: anywhere; }
    code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
    .card { display: grid; gap: var(--moxi-gap); padding: 10px 12px; border: 1px solid var(--moxi-border);
            border-radius: var(--moxi-radius-lg); background: var(--moxi-bg-raised); min-width: 0; }
    .dim { color: var(--moxi-text-dim); }
    .small { font-size: var(--moxi-font-size-sm); }
    .warn { color: var(--moxi-warn); }
    .fail { color: var(--moxi-error); }
    .actions { display: flex; flex-wrap: wrap; gap: var(--moxi-gap); align-items: center; }
    button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
             background: var(--moxi-bg); color: var(--moxi-text); font: inherit; cursor: pointer; }
    button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
    button:disabled { cursor: progress; }
    :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
    .qr { display: grid; gap: 6px; justify-items: start; }
    .qr svg { width: min(100%, 18rem); height: auto; display: block; }
    ul { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
    li { display: grid; gap: 2px; padding: 4px 8px; border-left: 3px solid var(--moxi-border); }
    li.stale { border-left-color: var(--moxi-warn); }
    .head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--moxi-gap); }
    .head .grow { flex: 1; }
    details.log summary { cursor: pointer; }
    details.log ul { margin-top: 6px; gap: 2px; }
    details.log li { display: block; padding: 0; border-left: none; }
  `}static{this.properties={devices:{state:!0},listError:{state:!0},code:{state:!0},note:{state:!0},busy:{state:!0},rows:{state:!0},signins:{state:!0},logError:{state:!0}}}#t;#i;connectedCallback(){super.connectedCallback(),this.#l(),this.#o()}disconnectedCallback(){super.disconnectedCallback(),this.#a()}render(){let t=this.devices??[];return d`<section class="card" data-section="gateway">
      <h2>Gateway</h2>
      <p class="dim small">Pair a phone to use Moxi from it through the tunnel. The phone scans
        the code with its camera, then makes a passkey.</p>
      <div class="actions">
        <button type="button" ?disabled=${this.busy} @click=${()=>this.#u()}>
          ${this.code?"New pairing code":"Pair a phone"}</button>
      </div>
      ${this.code?this.#e(this.code):u}
      ${this.note?d`<p class="small ${this.note.tone}" role="status">${this.note.text}</p>`:u}
      ${this.listError?d`<p class="small fail">${this.listError}</p>`:u}
      ${this.devices!==null&&t.length===0?d`<p class="dim small">No phone is paired.</p>`:u}
      ${t.length?d`<ul>${t.map(n=>this.#r(n))}</ul>`:u}
      ${this.#s()}
    </section>`}#s(){return this.logError?d`<p class="small fail">${this.logError}</p>`:this.rows===null?u:d`<details class="log" @toggle=${t=>{t.target.open&&this.#o()}}>
      <summary class="small">Activity: the audit log's last ${this.rows.length} rows, from today and yesterday</summary>
      ${this.rows.length?d`<ul class="small">${this.rows.map(t=>d`<li>
          <span class="dim">${$e(t.at)}</span> <strong>${t.kind}</strong> <code>${t.detail}</code></li>`)}</ul>`:d`<p class="dim small">Nothing yet.</p>`}
    </details>`}#e(t){let n=t.size+8;return d`<div class="qr">
      ${t.size?d`<svg viewBox="-4 -4 ${n} ${n}" role="img" aria-label="QR code of the pairing link"
          shape-rendering="crispEdges">
        <rect x="-4" y="-4" width=${n} height=${n} fill="#ffffff"></rect><!-- colour-fixed: scanners need dark on light -->
        <path d=${t.path} fill="#000000"></path><!-- colour-fixed: scanners need dark on light --></svg>`:u}
      <p class="small"><code>${t.url}</code></p>
      <p class="dim small">It works once, for two minutes.</p>
    </div>`}#r(t){return d`<li class=${t.stale?"stale":""} data-device=${t.id}>
      <div class="head"><strong>${t.name}</strong><span class="grow"></span>
        <button type="button" ?disabled=${this.busy} @click=${()=>this.#d(t)}>Revoke</button></div>
      <p class="small dim">Paired ${be(t.created)}; last signed in ${be(t.used)}.</p>
      ${this.#n(t.id)}
      ${t.passkey?u:d`<p class="small dim">No passkey: it signs in again only by a new QR pairing.</p>`}
      ${t.stale?d`<p class="small warn">The gateway's address changed since it paired, so it
        signs in to nothing. Pair it again, then revoke this entry.</p>`:u}
    </li>`}#n(t){let n=this.signins.get(t)??[];return n.length?d`<p class="small dim">Sign-ins: ${n.map($e).join(", ")}.</p>`:u}async#o(){let t=await nt("ui","api/gateway/log");t.status===200&&Array.isArray(t.data?.rows)&&Array.isArray(t.data?.signins)?(this.rows=t.data.rows,this.signins=new Map(t.data.signins.map(n=>[n.device,n.at])),this.logError=null):this.logError=st(t.status,t.data,"reading the audit log")}async#l(){let t=await nt("ui","api/gateway/devices");if(t.status===200&&Array.isArray(t.data?.devices)){let n=t.data.devices;if(this.code!==null){let i=n.find(r=>!this.#i.has(r.id));i!==void 0&&(this.code=null,this.#a(),this.note={text:`Paired: ${i.name}.`,tone:""})}this.devices=n,this.listError=null}else this.listError=st(t.status,t.data,"reading the devices")}async#u(){this.busy=!0,this.note=null;let t=await nt("ui","api/gateway/pair",{});if(this.busy=!1,t.status!==200||typeof t.data?.url!="string"){this.code=null,this.#a(),this.note={text:st(t.status,t.data,"pairing"),tone:"fail"};return}let n=t.data.url,i;try{i=me(n)}catch{this.note={text:"The pairing link is too long for a QR code; open it on the phone by hand.",tone:"warn"},this.code={url:n,path:"",size:0,until:Date.now()+xe};return}this.#i=new Set((this.devices??[]).map(r=>r.id)),this.code={url:n,path:fe(i),size:i.size,until:Date.now()+xe},this.#a(),this.#t=setInterval(()=>{if(this.code===null||Date.now()>this.code.until){this.code!==null&&(this.note={text:"That pairing code has expired.",tone:"dim"}),this.code=null,this.#a();return}this.#l()},Dn)}async#d(t){this.busy=!0;let n=await nt("ui","api/gateway/revoke",{id:t.id});this.busy=!1,n.status===200?this.note={text:t.passkey?`Revoked ${t.name}. Its passkey stays on the phone until you remove it in the phone's settings.`:`Revoked ${t.name}.`,tone:""}:this.note={text:st(n.status,n.data,"revoking"),tone:"fail"},await this.#l(),await this.#o()}#a(){this.#t!==null&&clearInterval(this.#t),this.#t=null}};customElements.get(ge)||customElements.define(ge,_t);var y={ATTRIBUTE:1,CHILD:2,PROPERTY:3,BOOLEAN_ATTRIBUTE:4,EVENT:5,ELEMENT:6},L=s=>(...e)=>({_$litDirective$:s,values:e}),E=class{constructor(e){}get _$AU(){return this._$AM._$AU}_$AT(e,t,n){this._$Ct=e,this._$AM=t,this._$Ci=n}_$AS(e,t){return this.update(e,t)}update(e,t){return this.render(...t)}};var{I:Un}=de,ye=s=>s;var we=s=>s.strings===void 0,ve=()=>document.createComment(""),H=(s,e,t)=>{let n=s._$AA.parentNode,i=e===void 0?s._$AB:e._$AA;if(t===void 0){let r=n.insertBefore(ve(),i),o=n.insertBefore(ve(),i);t=new Un(r,o,s,s.options)}else{let r=t._$AB.nextSibling,o=t._$AM,l=o!==s;if(l){let a;t._$AQ?.(s),t._$AM=s,t._$AP!==void 0&&(a=s._$AU)!==o._$AU&&t._$AP(a)}if(r!==i||l){let a=t._$AA;for(;a!==r;){let c=ye(a).nextSibling;ye(n).insertBefore(a,i),a=c}}}return t},S=(s,e,t=s)=>(s._$AI(e,t),s),In={},it=(s,e=In)=>s._$AH=e,_e=s=>s._$AH,rt=s=>{s._$AR(),s._$AA.remove()};var Ae=(s,e,t)=>{let n=new Map;for(let i=e;i<=t;i++)n.set(s[i],i);return n},ke=L(class extends E{constructor(s){if(super(s),s.type!==y.CHILD)throw Error("repeat() can only be used in text expressions")}dt(s,e,t){let n;t===void 0?t=e:e!==void 0&&(n=e);let i=[],r=[],o=0;for(let l of s)i[o]=n?n(l,o):o,r[o]=t(l,o),o++;return{values:r,keys:i}}render(s,e,t){return this.dt(s,e,t).values}update(s,[e,t,n]){let i=_e(s),{values:r,keys:o}=this.dt(e,t,n);if(!Array.isArray(i))return this.ut=o,r;let l=this.ut??=[],a=[],c,h,p=0,m=i.length-1,f=0,g=r.length-1;for(;p<=m&&f<=g;)if(i[p]===null)p++;else if(i[m]===null)m--;else if(l[p]===o[f])a[f]=S(i[p],r[f]),p++,f++;else if(l[m]===o[g])a[g]=S(i[m],r[g]),m--,g--;else if(l[p]===o[g])a[g]=S(i[p],r[g]),H(s,a[g+1],i[p]),p++,g--;else if(l[m]===o[f])a[f]=S(i[m],r[f]),H(s,i[p],i[m]),m--,f++;else if(c===void 0&&(c=Ae(o,f,g),h=Ae(l,p,m)),c.has(l[p]))if(c.has(l[m])){let w=h.get(o[f]),ut=w!==void 0?i[w]:null;if(ut===null){let qt=H(s,i[p]);S(qt,r[f]),a[f]=qt}else a[f]=S(ut,r[f]),H(s,i[p],ut),i[w]=null;f++}else rt(i[m]),m--;else rt(i[p]),p++;for(;f<=g;){let w=H(s,a[g+1]);S(w,r[f]),a[f++]=w}for(;p<=m;){let w=i[p++];w!==null&&rt(w)}return this.ut=o,it(s,a),x}});import{components as Fe,gateway as Ie,installed as je,navigate as Bn,place as Kn,subscribe as Fn}from"/m/ui/client.js";function Ee(s){if(typeof s!="object"||s===null)return null;let e=s;if(!Array.isArray(e.parts))return null;let t=[];for(let r of e.parts)typeof r?.text!="string"||r.text===""||t.push({text:r.text,heavy:r.heavy===!0});let n=typeof e.needs=="number"&&Number.isFinite(e.needs)&&e.needs>0?e.needs:0,i=e.opens===!0;return{parts:t,time:typeof e.time=="string"?e.time:"",needs:n,opens:i,open:i&&e.open===!0}}function At(s){let e=s.filter(n=>n.report!==null),t=n=>n.report?.needs||1/0;return e.map((n,i)=>({entry:n,at:i})).sort((n,i)=>{let[r,o]=[t(n.entry),t(i.entry)];return(r===o?0:r<o?-1:1)||n.entry.order-i.entry.order||n.at-i.at}).map(n=>n.entry)}function Se(s){return[...At(s),...s.filter(e=>e.report===null)]}function Re(s){return s.some(e=>(e.report?.needs??0)>0)}function Ce(s){return s.opens?s.open?"held":"toggle":"link"}function ot(s,e){return s===null||!s.opens?!1:s.open||e}var Te=1100,jn=340;function Me(s){if(typeof s!="object"||s===null)return 0;let e=s.width;return typeof e=="number"&&Number.isFinite(e)&&e>0?e:0}function Pe(s){return s>0?`calc(50% - ${jn+s/2}px)`:""}function Oe(s){return s.state==="present"?{tone:"ok",text:"installed"}:{tone:"fail",text:s.reason?`${s.state}: ${s.reason}`:s.state}}function Ne(s){let e=[];s.page&&s.export_error?e.push({tone:"fail",text:`Its page can't be served: ${s.export_error}`}):s.page&&!s.mounted&&s.state==="present"&&e.push({tone:"warn",text:"Its page is not mounted yet."});let t=s.components.map(n=>n.title||n.name);t.length&&e.push({tone:null,text:`Components: ${t.join(", ")}`});for(let n of s.refused)e.push({tone:"warn",text:`${n.name} is not shown: ${n.reason}`});return e}function Et(s){let e=t=>t.name==="ui"?0:t.state==="present"?1:2;return[...s].sort((t,n)=>e(t)-e(n)||(t.name<n.name?-1:t.name>n.name?1:0))}function Le(s){let e=[];for(let t of Et(s))if(t.state==="present")for(let n of[...t.components].sort((i,r)=>i.name<r.name?-1:1))n.home&&n.place==="block"&&!n.home_as&&e.push({module:t.name,name:n.name,title:n.title});return e}var Vn=500;function He(s,e){let t=[];for(let i of s)if(i.state==="present")for(let r of i.components)!r.home||r.place!=="block"||r.home_as!==e||t.push({module:i.name,name:r.name,title:r.title,order:typeof r.order=="number"?r.order:Vn});let n=(i,r)=>i<r?-1:i>r?1:0;return t.sort((i,r)=>i.order-r.order||n(i.module,r.module)||n(i.name,r.name))}function ze(s){return He(s,"row")}function De(s){return He(s,"main")[0]??null}var kt={fail:0,warn:1,off:2,ok:3};function Ue(s){return s.map(e=>({...e,tone:e.state in kt?e.state:"warn"})).sort((e,t)=>kt[e.tone]-kt[t.tone])}function St(s,e,t){let n=typeof t=="object"&&t!==null?t:{},i=typeof n.log=="string"&&n.log?n.log:null,r=i?[`Its log: ${i}`]:[];switch(e===0?"network":n.error){case"start":{let o=typeof n.exit=="number"?n.exit:null;return{title:`${s}'s page server did not start`,lines:[o===null?"It gave no ready line within 10 s and was stopped.":`It exited with code ${o}.`,...r,"The next try is at the first request after 10 s."]}}case"unreachable":return{title:`${s}'s page server can't be reached`,lines:["The shell started it but could not connect to it.",...r]};case"upstream":return{title:`${s}'s page server gave no answer`,lines:[...r]};case"timeout":return{title:`${s}'s page server is not answering`,lines:["No answer came within 30 s."]};case"stopping":return{title:"The shell is stopping",lines:["Run moxi ui open to start it again."]};case"network":return{title:"No answer from the shell",lines:["The shell may have stopped. Run moxi ui open to start it again."]};default:return null}}async function at(s,e,t){let n=async()=>{let l=null;try{l=await s()}catch(a){console.error(a)}if(!t()){l?.remove();return}return l},i=await n();if(i===void 0)return null;if(i!==null)return{placed:i};let r=await e();if(!t())return null;if(r!==null)return{failure:r};let o=await n();return o===void 0?null:o!==null?{placed:o}:{failure:null}}async function lt(s,e){let t;try{t=await fetch(e,{credentials:"same-origin",cache:"no-store"})}catch{return St(s,0,null)}if(t.ok)return null;let n=null;try{n=await t.json()}catch{n=null}return St(s,t.status,n)}var Ve="moxi-ui-home",qn="moxi-home-row",Wn="moxi-home-side",Rt="--moxi-home-side-margin";function Ct(s){return Fe(s.module).find(e=>e.name===s.name)?.url??""}async function Be(s,e,t){let n=Ct(e),i=await at(()=>Kn(s,e.module,e.name),async()=>n===""?"no script":await lt(e.module,n),t);return i!==null&&"placed"in i}function Ke(s){let e=s.activeElement;for(;e?.shadowRoot?.activeElement;)e=e.shadowRoot.activeElement;return e}var Tt=class extends b{constructor(){super();this.modules=null;this.rows=[];this.main=null;this.loading=!1;this.#t=document.createElement("div");this.#i="";this.#s=0;this.#e=null;this.#r=null;this.#n=null;this.#t.addEventListener(Wn,t=>{t.stopPropagation(),this.#c(t.target,Me(t.detail))}),new MutationObserver(()=>{this.#r!==null&&!this.#t.contains(this.#r)&&this.#c(null,0)}).observe(this.#t,{childList:!0,subtree:!0})}static{this.styles=_`
    :host { display: block; max-width: 680px; margin: 0 auto; padding-top: 138px; min-width: 0; }
    * { box-sizing: border-box; }
    .spot { min-height: 150px; }
    .quiet { margin: 0; font-size: 22px; line-height: 150px; text-align: center; letter-spacing: -0.005em; }
    .rows { display: flex; flex-direction: column; margin-top: 40px; border-top: 1px solid var(--moxi-border); }
    .row { display: grid; grid-template-columns: 96px 1fr auto; align-items: baseline; width: 100%;
           padding: 12px 2px; border: 0; border-bottom: 1px solid var(--moxi-border); background: none;
           color: var(--moxi-text); font: inherit; font-size: 14px; text-align: left; text-decoration: none; }
    a.row, button.row { cursor: pointer; }
    .label, .time { color: var(--moxi-text-dim); }
    .time { font-variant-numeric: tabular-nums; }
    .fact { min-width: 0; padding-right: 18px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .heavy { font-weight: 600; }
    .entry.open .row { border-bottom: 0; padding-bottom: 5px; }
    .seat { padding: 0 2px 7px 98px; border-bottom: 1px solid var(--moxi-border); }
    :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: -2px; }
    [hidden] { display: none; }
    @media (min-width: ${D(Te)}px) {
      :host { margin-left: var(${D(Rt)}, auto); }
    }
    @media (max-width: 720px) {
      :host { padding-top: 0; }
      .spot { margin-top: 10px; }
      .rows { margin-top: 10px; }
      .rows.phone { margin-top: 0; }
      .row { grid-template-columns: 1fr auto; row-gap: 2px; }
      .fact { grid-row: 2; grid-column: 1 / 3; padding-right: 0; white-space: normal; overflow-wrap: anywhere; }
      .fact:empty { display: none; }
      .time { grid-row: 1; grid-column: 2; }
      .seat { padding-left: 2px; }
    }
  `}static{this.properties={modules:{state:!0},rows:{state:!0},main:{state:!0},loading:{state:!0}}}#t;#i;#s;#e;#r;#n;connectedCallback(){super.connectedCallback(),this.#e=Fn("ui","modules",t=>{this.modules=Array.isArray(t?.modules)?t.modules:[]})}disconnectedCallback(){super.disconnectedCallback(),this.#e?.(),this.#e=null}willUpdate(t){this.#n=this.#o(),!(!t.has("modules")||this.modules===null)&&(this.#l(ze(this.modules).filter(n=>je(n.module))),Ie()||this.#p(De(this.modules.filter(n=>je(n.name)))))}updated(){let t=this.#n;this.#n=null,t!==null&&t.isConnected&&Ke(document)!==t&&t.focus()}#o(){let t=this.renderRoot,n=t.activeElement;if(n===null||!t.querySelector(".rows")?.contains(n))return null;let i=Ke(t);return i instanceof HTMLElement?i:null}#l(t){let n=new Map(this.rows.map(i=>[i.key,i]));this.rows=t.map(i=>{let r=`${i.module}.${i.name}`,o=Ct(i),l=n.get(r);if(l!==void 0&&l.url===o)return Object.assign(l,i);let a=document.createElement("div");a.className="seat",a.hidden=!0;let c={...i,key:r,url:o,host:a,report:null,clicked:!1};return a.addEventListener(qn,h=>{h.stopPropagation(),this.#u(c,h.detail)}),Be(a,i,()=>this.rows.includes(c)),c})}#u(t,n){t.report=Ee(n),(t.report===null||!t.report.opens||t.report.open)&&(t.clicked=!1),this.#d(t),this.requestUpdate()}#d(t){let n=ot(t.report,t.clicked);t.host.hidden=!n,t.host.firstElementChild?.toggleAttribute("open",n)}#a(t){t.clicked=!t.clicked,this.#d(t),this.requestUpdate()}#c(t,n){this.#r=n>0?t:null,n>0?this.style.setProperty(Rt,Pe(n)):this.style.removeProperty(Rt)}async#p(t){let n=t===null?"":`${t.module}.${t.name} ${Ct(t)}`;if(n===this.#i)return;this.#i=n;let i=this.#s+=1;if(this.#t.replaceChildren(),this.main=null,this.loading=t!==null,t===null)return;let r=await Be(this.#t,t,()=>i===this.#s);i===this.#s&&(this.loading=!1,r&&(this.main=n))}render(){if(this.modules===null)return u;let t=At(this.rows),n=Ie();return d`
      ${n?u:d`<section class="spot">${this.#t}${this.main===null&&!this.loading&&!Re(this.rows)?d`<p class="quiet">Nothing needs you.</p>`:u}</section>`}
      <nav class=${n?"rows phone":"rows"} aria-label="Now" ?hidden=${t.length===0}>
        ${ke(Se(this.rows),i=>i.key,i=>d`<div
          class=${ot(i.report,i.clicked)?"entry open":"entry"} data-row=${i.key}
          ?hidden=${i.report===null}>
          ${i.report===null?u:this.#h(i,i.report)}${i.host}</div>`)}
      </nav>`}#h(t,n){let i=Ce(n),r=ot(n,t.clicked),o=d`<span class="label">${t.title}</span><span class="fact">${n.parts.map(a=>a.heavy?d`<span class="heavy">${a.text}</span>`:a.text)}</span><span class="time">${i==="toggle"&&r?"Hide":n.time}</span>`;if(i==="toggle")return d`<button type="button" class="row" aria-expanded=${r?"true":"false"}
        @click=${()=>this.#a(t)}>${o}</button>`;let l=t.module==="ui"?"modules":"";return i==="held"||!Fe(t.module).some(a=>a.name==="page")?d`<div class="row">${o}</div>`:d`<a class="row" href=${`/m/${t.module}/${l}`} @click=${a=>{a.button!==0||a.metaKey||a.ctrlKey||a.shiftKey||a.altKey||(a.preventDefault(),Bn(t.module,l))}}>${o}</a>`}};customElements.get(Ve)||customElements.define(Ve,Tt);var K=L(class extends E{constructor(s){if(super(s),s.type!==y.PROPERTY&&s.type!==y.ATTRIBUTE&&s.type!==y.BOOLEAN_ATTRIBUTE)throw Error("The `live` directive is not allowed on child or event bindings");if(!we(s))throw Error("`live` bindings can only contain a single expression")}render(s){return s}update(s,[e]){if(e===x||e===u)return e;let t=s.element,n=s.name;if(s.type===y.PROPERTY){if(e===t[n])return x}else if(s.type===y.BOOLEAN_ATTRIBUTE){if(!!e===t.hasAttribute(n))return x}else if(s.type===y.ATTRIBUTE&&t.getAttribute(n)===e+"")return x;return it(s),e}});var qe="important",Gn=" !"+qe,We=L(class extends E{constructor(s){if(super(s),s.type!==y.ATTRIBUTE||s.name!=="style"||s.strings?.length>2)throw Error("The `styleMap` directive must be used in the `style` attribute and must be the only part in the attribute.")}render(s){return Object.keys(s).reduce((e,t)=>{let n=s[t];return n==null?e:e+`${t=t.includes("-")?t:t.replace(/(?:^(webkit|moz|ms|o)|)(?=[A-Z])/g,"-$&").toLowerCase()}:${n};`},"")}update(s,[e]){let{style:t}=s.element;if(this.ft===void 0)return this.ft=new Set(Object.keys(e)),this.render(e);for(let n of this.ft)e[n]==null&&(this.ft.delete(n),n.includes("-")?t.removeProperty(n):t[n]=null);for(let n in e){let i=e[n];if(i!=null){this.ft.add(n);let r=typeof i=="string"&&i.endsWith(Gn);n.includes("-")||r?t.setProperty(n,r?i.slice(0,-11):i,r?qe:""):t[n]=i}}return x}});import{api as ds,components as us,installed as cs,setAddress as ps}from"/m/ui/client.js";var R="moxi";var Qn="gateway.",Yn="applies now",Jn={"applies at next start":"Applies at the next start.","turns the daemon on or off now":"Turns its daemon on or off."},Pt="Locked for this run of the shell.",Ye="an absolute path",Xn=80,Zn=24,ts=5;function Je(s){if(typeof s=="string")try{return JSON.parse(s)}catch{return}}function Ot(s){return Je(s.json)}function Xe(s){return Je(s.default_json)}function $(s){return typeof s=="string"&&s!==""?s:null}function es(s,e){return s==="ui"&&e.name.startsWith(Qn)}function Nt(s,e,t){return e.filter(n=>typeof n?.name=="string"&&n.needs_absent!==!0&&!$(n.elsewhere)&&(t||!es(s,n)))}function Lt(s,e){return Nt(s,e,!1)}function Ht(s,e){return Nt(s.module,s.keys,e).filter(t=>t.changed===!0).length}function F(s){let e=[...new Set(s.filter(t=>t!==R&&t!=="ui"))].sort();return[R,...s.includes("ui")?["ui"]:[],...e]}function Ze(s,e,t){let n=0;for(let i of new Set(s)){let r=e.get(i);if(r===void 0)return null;r.error===null&&(n+=Ht(r,t))}return n}function Ge(s){return s.length<=1?s.join(""):`${s.slice(0,-1).join(", ")} and ${s[s.length-1]}`}function Mt(s){if(typeof s=="string")return s;if(s!==null&&typeof s=="object"&&!Array.isArray(s)){let e=s.name;if(typeof e=="string"&&e!=="")return e}return JSON.stringify(s)??String(s)}function zt(s,e){if(e==null||e==="")return"not set";if(typeof e=="boolean")return e?"on":"off";if(typeof e=="number")return String(e);if(typeof e=="string"){let t=(s.choices??[]).find(n=>n?.value===e);return t?t.words:e}if(Array.isArray(e))return e.length===0?"none":Ge(e.map(Mt));if(typeof e=="object"){let t=Object.entries(e);if(s.switch_table===!0){let n=t.filter(([,i])=>i===!0).map(([i])=>i);return n.length===0?"none":Ge(n)}return t.length===0?"none":t.map(([n,i])=>`${n} = ${Mt(i)}`).join(", ")}return String(e)}function Dt(s){return $(s.label)??s.name}function tn(s){if(typeof s!="string"||s===""||s===Yn)return null;let e=Jn[s];if(e)return e;let t=s.replaceAll("`","");return`${t[0].toUpperCase()}${t.slice(1)}${/[.!?]$/.test(t)?"":"."}`}function Ut(s){let e=s.terminal;return e&&typeof e.on=="string"&&typeof e.off=="string"?e:null}function ns(s){return s.map(e=>typeof e=="string"?e:Mt(e))}function Qe(s){return s!==null&&typeof s=="object"&&!Array.isArray(s)}function ss(s){let e=[];for(let t of s)for(let n of Object.keys(t))e.includes(n)||e.push(n);return e}function is(s,e){let t=Array.isArray(s.values)?s.values.filter(o=>typeof o=="string"):[],n=new Map((s.choices??[]).map(o=>[o.value,o.words])),i=t.map(o=>({value:o,words:n.get(o)??o})),r=typeof e=="string"?e:"";return{kind:s.pick===!0||t.length>=ts?"pick":"choices",options:i,selected:r}}function rs(s,e){let t=Array.isArray(e)?e:[],n=Xe(s),i=t.length>0?t:Array.isArray(n)?n:[];if(i.length>0&&i.every(Qe))return{kind:"rows",columns:ss(i),rows:t.filter(Qe).map(o=>({...o}))};let r=ns(t);return r.every(o=>o.length<=Zn&&!/\s/.test(o))?{kind:"names",items:r}:{kind:"lines",items:r,first:r[0]??""}}function os(s){let e=Ot(s);if(Ut(s))return{kind:"word",text:zt(s,e)};if(s.switch_table===!0)return{kind:"switches",toggles:(Array.isArray(s.entries)?s.entries:[]).filter(n=>typeof n?.key=="string").map(n=>({key:n.key,label:n.name||n.key,on:n.value===!0,line:tn(n.mark),locked:n.locked===!0}))};switch(s.type){case"bool":return{kind:"switch",on:e===!0};case"enum":return is(s,e);case"int":case"float":return{kind:"number",text:typeof e=="number"?String(e):"",unit:$(s.unit),whole:s.type==="int"};case"list":return rs(s,e);case"table":return{kind:"toml",text:typeof s.value=="string"?s.value:""};default:{let t=typeof e=="string"?e:"";return t.length>Xn||/[\r\n]/.test(t)?{kind:"long",text:t,first:t.split(/\r?\n/)[0]}:{kind:"text",text:t,path:s.type==="path"}}}}function en(s){let e=[],t=$(s.label)?$(s.line):$(s.line)??$(s.doc);t&&e.push(t);let n=tn(s.mark);return n&&e.push(n),s.source==="env"&&$(s.env)&&e.push(`Set by ${s.env} in the shell's environment, which wins over the file.`),s.switch_table===!0&&s.declarations==="unavailable"&&$(s.reason)&&e.push(s.reason),s.locked===!0&&s.switch_table!==!0&&e.push(Pt),e}function as(s){return s.changed!==!0?null:{words:zt(s,Xe(s)),reset:s.source==="file"&&s.locked!==!0&&Ut(s)===null}}function ls(s){let e=Ut(s);return e?Ot(s)===!0?{text:`Turned off at the terminal: ${e.off}`,command:e.off}:{text:`Turned on at the terminal: ${e.on}`,command:e.on}:null}function nn(s){return{name:s.name,label:Dt(s),labelled:$(s.label)!==null,control:os(s),lines:en(s),terminal:ls(s),defaults:as(s),locked:s.locked===!0&&s.switch_table!==!0}}function sn(s){if(!s.some(t=>t.common===!0))return{first:[...s],more:null};let e=s.filter(t=>t.common!==!0);return{first:s.filter(t=>t.common===!0),more:e.length>0?e:null}}function rn(s){let e=[];for(let t of s){let n=$(t.group),i=e.find(r=>r.group===n);i?i.keys.push(t):e.push({group:n,keys:[t]})}return e}function on(s){let e=[],t=[];for(let r of s){let o=$(r.group);o?e.includes(o)||e.push(o):t.push(Dt(r))}let n=[...e,...t].map(r=>r.toLowerCase()).join(", "),i=s.filter(r=>r.changed===!0).length;return{text:`${s.length} ${s.length===1?"setting":"settings"}: ${n}`,changed:i>0?`${i} changed`:null}}function an(s,e,t){return F(s).map(n=>{let i=e.get(n),r=i!==void 0&&i.error!==null,o=i===void 0||r?0:Ht(i,t);return{module:n,changed:o>0?o:null,unreadable:r}})}function ln(s){return s===void 0?"reading":s.error!==null?"unreadable":Lt(s.module,s.keys).length===0?"empty":"rows"}var dn="Nothing here can be changed on this page.",un=400,It="Reading…";function cn(s,e){if(s===0)return"No answer from the shell, so the settings could not be read.";if(s===403)return"This browser is signed out.";let t=e!==null&&typeof e=="object"?e:{},n=t.refused??t.conflict??t.error;return typeof n=="string"&&n!==""?n:`The shell answered ${s} for the settings.`}function pn(s){return`${s.module}'s settings can't be read: ${s.error??""}`}var hn="changed";function Vt(s,e){let t=s.replace(/^\/+|\/+$/g,"").split("/").filter(Boolean),n={view:"module",module:R,more:!1,pair:!1,missing:null};if(t[0]!=="settings"||t.length===1)return n;let i=t[1],r=t[2]??null;return e.includes(i)?{...n,module:i,more:r==="more",pair:r==="pair"&&i==="ui"}:i===hn&&t.length===2?{...n,view:"changed"}:{...n,missing:`${i} has no settings here.`}}function mn(s){return s.view==="changed"?`settings/${hn}`:s.module===R&&!s.more?"settings":s.pair&&s.module==="ui"?`settings/${"ui"}/pair`:`settings/${s.module}${s.more?"/more":""}`}var fn="moxi-ui-settings",hs=2,ms=2e3,q=null,fs=_`
  :host { display: block; min-width: 0; max-width: 680px; margin: 0 auto; font-size: var(--moxi-font-size); }
  * { box-sizing: border-box; }
  p { margin: 0; overflow-wrap: anywhere; }
  code { font-family: var(--moxi-font-mono); }
  button { font: inherit; color: inherit; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  .dim { color: var(--moxi-text-dim); }
  .link { padding: 0; border: 0; background: none; color: var(--moxi-text); cursor: pointer; }
  .link.dim { color: var(--moxi-text-dim); }
  .cols { display: grid; grid-template-columns: 96px minmax(0, 1fr); border-top: 1px solid var(--moxi-border);
          padding: 0 2px; }
  .mods { position: sticky; top: 8px; align-self: start; padding: 12px 12px 0 0; font-size: 14px; min-width: 0; }
  .mods a { display: flex; justify-content: space-between; align-items: baseline; gap: 6px; line-height: 28px;
            color: var(--moxi-text-dim); text-decoration: none; }
  .mods a[aria-current] { color: var(--moxi-text); }
  .mods a i { font-style: normal; font-size: 13px; color: var(--moxi-text-dim); font-variant-numeric: tabular-nums; }
  .mods .under { display: block; font-size: 12px; line-height: 16px; color: var(--moxi-text); margin: -5px 0 5px; }
  .mods .total { display: block; font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); margin-top: 10px;
                 padding-top: 10px; border-top: 1px solid var(--moxi-border); }
  .mods .error { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); margin-top: 10px; }
  .pane { min-width: 0; padding-top: 5px; }
  .set { padding: 7px 0; }
  .l1 { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; min-height: 28px;
        font-size: 14px; line-height: 20px; column-gap: 14px; row-gap: 4px; }
  .lb { min-width: 0; overflow-wrap: anywhere; }
  .key { font-size: 12px; line-height: 18px; color: var(--moxi-text-dim); margin-left: 10px; cursor: copy; }
  .key.own { font-size: 14px; line-height: 20px; color: var(--moxi-text); margin-left: 0; }
  .copied { font-size: 12px; margin-left: 8px; }
  .l2 { display: flex; flex-wrap: wrap; justify-content: space-between; column-gap: 18px; font-size: 13px;
        line-height: 18px; color: var(--moxi-text-dim); margin-top: -1px; }
  .l2 .said { max-width: 440px; flex: 0 1 auto; min-width: 0; overflow-wrap: anywhere; }
  .l2 .nt { margin-left: auto; }
  .l2 code { color: var(--moxi-text); font-size: 12px; overflow-wrap: anywhere; }
  .ctl { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
  .fld { border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius); padding: 3px 9px; font: inherit;
         font-size: 14px; line-height: 20px; background: none; color: var(--moxi-text); }
  .fld.num { width: 88px; text-align: right; font-variant-numeric: tabular-nums; }
  .fld.txt { width: min(190px, 100%); }
  .fld:disabled, button:disabled { opacity: 0.6; cursor: not-allowed; }
  select.fld { text-align: left; }
  .unit { color: var(--moxi-text-dim); font-size: 13px; }
  .tri { display: inline-flex; flex-wrap: wrap; font-size: 13px; line-height: 1; }
  .tri button { padding: 5px 7px; border: 1px solid transparent; border-radius: var(--moxi-radius); background: none;
                color: var(--moxi-text-dim); cursor: pointer; }
  .tri button[aria-checked="true"] { color: var(--moxi-text); border-color: var(--moxi-border-strong); }
  .sw { display: inline-block; position: relative; width: 26px; height: 14px; padding: 0; border-radius: 7px;
        border: 1px solid var(--moxi-text-dim); background: none; cursor: pointer; vertical-align: -2px; }
  .sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 8px; height: 8px; border-radius: 50%;
               background: var(--moxi-text-dim); }
  .sw[aria-checked="true"] { background: var(--moxi-text); border-color: var(--moxi-text); }
  .sw[aria-checked="true"]::after { left: 14px; background: var(--moxi-bg); }
  .toks { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 13px; }
  .tok { padding: 4px 7px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius); }
  .wide { display: block; border: 1px solid var(--moxi-border); border-radius: var(--moxi-radius); padding: 5px 9px;
          font-size: 13px; line-height: 18px; margin: 3px 0 2px; overflow-wrap: anywhere; }
  .entries { display: grid; gap: 2px; padding: 2px 0 2px 16px; }
  .entry { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; column-gap: 14px;
           font-size: 14px; line-height: 20px; min-height: 26px; }
  .entry .l2 { flex-basis: 100%; }
  .table { display: grid; column-gap: 8px; row-gap: 6px; align-items: center; font-size: 13px; line-height: 18px;
           padding: 4px 0; overflow-x: auto; }
  .table .h { color: var(--moxi-text-dim); }
  .table .fld { font-size: 13px; padding: 3px 8px; min-width: 0; width: 100%; }
  .subh { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); border-top: 1px solid var(--moxi-border);
          margin-top: 6px; padding: 11px 0 2px; }
  .pane > .subh:first-child { border-top: 0; margin-top: 0; padding-top: 7px; }
  .more { display: flex; flex-wrap: wrap; justify-content: space-between; column-gap: 14px;
          border-top: 1px solid var(--moxi-border); margin-top: 6px; padding: 11px 0 10px; font-size: 14px;
          line-height: 20px; color: var(--moxi-text-dim); }
  .more b { font-weight: 400; color: var(--moxi-text); margin-right: 10px; }
  .more[data-open] + .subh { border-top: 0; margin-top: 0; padding-top: 0; }
  .note { font-size: 14px; line-height: 20px; color: var(--moxi-text-dim); padding: 12px 0; }
  .unreadable { display: grid; gap: 6px; padding: 12px 0; font-size: 14px; line-height: 20px; }
  .unreadable .fix { color: var(--moxi-text-dim); }
  .unreadable code { color: var(--moxi-text); overflow-wrap: anywhere; }
  .foot { display: flex; flex-wrap: wrap; justify-content: space-between; column-gap: 18px; font-size: 13px;
          line-height: 18px; color: var(--moxi-text-dim); border-top: 1px solid var(--moxi-border); margin-top: 6px;
          padding: 12px 0 28px; }
  .foot .file { overflow-wrap: anywhere; min-width: 0; }
  .foot a { color: var(--moxi-text); text-decoration: none; }
  .missing { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); padding-bottom: 28px; }
  .failed { display: grid; gap: var(--moxi-gap); justify-items: start; padding: 12px 0; }
  .failed button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
                   background: none; cursor: pointer; }
  @media (max-width: 720px) {
    .cols { grid-template-columns: minmax(0, 1fr); }
    .mods { position: static; display: flex; flex-wrap: wrap; column-gap: 14px; padding: 8px 0;
            border-bottom: 1px solid var(--moxi-border); }
    .mods a { line-height: 24px; }
    .mods .under { margin: 0; align-self: center; }
    .mods .total, .mods .error { flex-basis: 100%; margin-top: 4px; padding-top: 4px; }
  }
`;function gs(s){let e=s.replace(/^\/+|\/+$/g,"").split("/").filter(Boolean);return e[0]==="settings"&&e[1]?e[1]:R}var Bt=class extends b{constructor(){super(...arguments);this.route="settings";this.#t={listed:null,sections:new Map,error:null,address:null};this.#i=null;this.#s=!1;this.#e=null;this.#r=null;this.#n=null;this.#o="";this.#l=0;this.#u=()=>{document.visibilityState==="visible"&&this.#t.listed!==null&&this.#a()}}static{this.styles=fs}static{this.properties={route:{type:String}}}#t;#i;#s;#e;#r;#n;#o;#l;#u;connectedCallback(){super.connectedCallback();let t=(this.getAttribute("route")??this.route??"settings").replace(/^\/+|\/+$/g,"");this.route=t,this.#o=location.pathname,document.addEventListener("visibilitychange",this.#u);let n=q!==null&&q.path===location.pathname?q.held:null;q=null,n!==null&&n.listed!==null?(this.#t={...n,address:Vt(t,n.listed)},this.#a()):this.#d()}disconnectedCallback(){super.disconnectedCallback(),document.removeEventListener("visibilitychange",this.#u);for(let t of[this.#e,this.#n])t!==null&&clearTimeout(t);q=location.pathname===this.#o?{path:this.#o,held:this.#t}:null}async#d(){if(this.#i=null,this.#h(),!await this.#p(gs(this.route)))return;let n=this.#t.listed??[];this.#t.address=Vt(this.route,n),this.requestUpdate(),!(!this.#t.sections.has(this.#t.address.module)&&!await this.#p(this.#t.address.module))&&await this.#c(F(n).filter(i=>!this.#t.sections.has(i)))}async#a(){let t=this.#t.address?.module??R;if(!await this.#p(t))return;let n=this.#t.listed??[];await this.#c(F(n).filter(i=>i!==t))}async#c(t){let n=[...t],i=async()=>{for(let r=n.shift();r!==void 0;r=n.shift())if(!await this.#p(r))return};await Promise.all(Array.from({length:hs},i))}async#p(t){this.#l+=1;let n=await ds("ui",`api/settings?module=${encodeURIComponent(t)}`);this.#l-=1;let i=n.data;if(n.status!==200||!Array.isArray(i?.sections)||!Array.isArray(i?.listed))return this.#t.listed===null&&(this.#i=cn(n.status,i)),this.requestUpdate(),!1;let r=i.listed.filter(o=>typeof o=="string");this.#t.listed=r,this.#t.error=typeof i.error=="string"&&i.error!==""?i.error:null;for(let o of i.sections)typeof o?.module=="string"&&this.#t.sections.set(o.module,o);for(let o of[...this.#t.sections.keys()])r.includes(o)||this.#t.sections.delete(o);return this.requestUpdate(),!0}#h(){this.#s=!1,this.#e!==null&&clearTimeout(this.#e),this.#e=setTimeout(()=>{this.#e=null,this.#s=!0,this.requestUpdate()},un)}#y(){this.#t={listed:null,sections:new Map,error:null,address:null},this.requestUpdate(),this.#d()}#x(t){this.#t.address=t;try{ps("ui",mn(t)),this.#o=location.pathname}catch{}this.requestUpdate()}#v(t,n){t.button!==0||t.metaKey||t.ctrlKey||t.shiftKey||t.altKey||(t.preventDefault(),this.#x({view:"module",module:n,more:!1,pair:!1,missing:null}))}#w(){let t=this.#t.address;t!==null&&this.#x({...t,more:!t.more,missing:null})}#m(t){navigator.clipboard?.writeText(t).catch(()=>{}),this.#r=t,this.#n!==null&&clearTimeout(this.#n),this.#n=setTimeout(()=>{this.#n=null,this.#r=null,this.requestUpdate()},ms),this.requestUpdate()}updated(t){super.updated(t),this.#t.address!==null&&this.#e!==null&&this.#t.sections.has(this.#t.address.module)&&(clearTimeout(this.#e),this.#e=null)}render(){if(this.#i!==null)return d`<div class="failed" data-part="failed">
        <p data-line="failed">${this.#i}</p>
        <button type="button" @click=${()=>this.#y()}>Try again</button></div>`;let t=this.#t.address;return d`<div class="cols">
      ${this.#_()}
      <section class="pane" data-part="pane" data-module=${t?.module??u}>${this.#A()}</section>
    </div>`}#_(){let{listed:t,sections:n,error:i,address:r}=this.#t;if(t===null)return d`<nav class="mods" data-part="list" aria-label="Modules" data-reading></nav>`;let o=Ze(t,n,!1);return d`<nav class="mods" data-part="list" aria-label="Modules" ?data-reading=${o===null}>
      ${an(t,n,!1).map(l=>{let a=r?.view==="module"&&r.module===l.module;return d`<a href=${`/m/ui/settings/${l.module}`} data-list=${l.module}
            aria-current=${a?"page":u} @click=${c=>this.#v(c,l.module)}
          >${l.module}${l.changed!==null?d`<i title=${`${l.changed} changed from the default`}>${l.changed}</i>`:u}</a>
          ${l.unreadable?d`<span class="under" data-line="unreadable">can't be read</span>`:u}`})}
      ${o!==null&&o>0?d`<span class="total" data-line="changed">${o} changed</span>`:u}
      ${i!==null?d`<p class="error" data-line="error">${i}</p>`:u}
    </nav>`}#A(){let t=this.#t.address;if(t===null)return this.#s?d`<p class="note" data-line="reading">${It}</p>`:u;let n=this.#t.sections.get(t.module),i=t.missing!==null?d`<p class="missing" data-line="missing">${t.missing}</p>`:u;switch(ln(n)){case"reading":return d`${this.#s?d`<p class="note" data-line="reading">${It}</p>`:u}${i}`;case"unreadable":{let r=n;return d`<div class="unreadable" data-part="unreadable">
            <p data-line="unreadable">${pn(r)}</p>
            ${r.fix?d`<p class="fix" data-line="fix">Fix: <code>${r.fix}</code>
              ${this.#b(r.fix)}</p>`:u}
          </div>${this.#g(r)}${i}`}case"empty":return d`<p class="note" data-line="empty">${dn}</p>${this.#g(n)}${i}`;default:return d`${this.#k(n,t)}${this.#g(n)}${i}`}}#k(t,n){let i=Lt(t.module,t.keys),{first:r,more:o}=sn(i),l=t.readonly;if(o===null)return this.#f(r,l);let a=on(o);return d`${this.#f(r,l)}
      <div class="more" data-part="more" ?data-open=${n.more}>
        <span><b>More</b><span data-line="more">${a.text}${a.changed?` · ${a.changed}`:""}</span></span>
        <button type="button" class="link" aria-expanded=${String(n.more)} @click=${()=>this.#w()}
          >${n.more?"Hide":"Show"}</button>
      </div>
      ${n.more?this.#f(o,l):u}`}#f(t,n){return rn(t).map(i=>d`
      ${i.group?d`<div class="subh" data-group=${i.group}>${i.group}</div>`:u}
      ${i.keys.map(r=>this.#E(nn(r),n))}`)}#E(t,n){let i=n||t.locked,r=this.#r===t.name?d`<span class="copied" data-line="copied">Copied</span>`:u,o=d`<code class=${t.labelled?"key":"key own"} title="Copy the key" role="button" tabindex="0"
        data-line="key" @click=${()=>this.#m(t.name)}
        @keydown=${a=>{(a.key==="Enter"||a.key===" ")&&(a.preventDefault(),this.#m(t.name))}}
      >${t.name}</code>`,l=t.lines.length>0||t.defaults!==null;return d`<div class="set" data-key=${t.name} ?data-locked=${i}>
      <div class="l1">
        <span class="lb">${t.labelled?d`<span data-line="label">${t.label}</span>${o}`:o}${r}</span>
        <span class="ctl">${this.#S(t,i)}</span>
      </div>
      ${t.control.kind==="switches"?u:this.#$(t,i)}
      ${l?d`<div class="l2">
        <span class="said">${t.lines.map((a,c)=>d`${c>0?" ":""}<span data-line="line">${a}</span>`)}</span>
        ${t.defaults!==null?d`<span class="nt" data-line="default">default ${t.defaults.words}${t.defaults.reset?d` · <button type="button" class="link" data-act="reset">Reset</button>`:u}</span>`:u}
      </div>`:u}
      ${t.terminal!==null?d`<div class="l2"><span class="said" data-line="terminal">${t.terminal.text}</span>
        ${this.#b(t.terminal.command)}</div>`:u}
      ${t.control.kind==="switches"?this.#$(t,i):u}
    </div>`}#b(t){return this.#r===t?d`<span class="copied" data-line="copied">Copied</span>`:d`<button type="button" class="link" data-act="copy" @click=${()=>this.#m(t)}>Copy</button>`}#S(t,n){let i=t.control,r=t.name;switch(i.kind){case"switch":return d`<button type="button" class="sw" role="switch" aria-checked=${String(i.on)} aria-label=${t.label}
          data-control=${r} ?disabled=${n}></button>`;case"choices":return d`<span class="tri" role="radiogroup" aria-label=${t.label} data-control=${r}>
          ${i.options.map(o=>d`<button type="button" role="radio" data-value=${o.value}
            aria-checked=${String(o.value===i.selected)} ?disabled=${n}>${o.words}</button>`)}</span>`;case"pick":return d`<select class="fld" aria-label=${t.label} data-control=${r} ?disabled=${n}
          .value=${K(i.selected)}>${i.options.map(o=>d`<option value=${o.value}
            ?selected=${o.value===i.selected}>${o.words}</option>`)}</select>`;case"number":return d`<input class="fld num" type="text" inputmode=${i.whole?"numeric":"decimal"}
          aria-label=${t.label} data-control=${r} .value=${K(i.text)} ?disabled=${n}>
          ${i.unit?d`<span class="unit">${i.unit}</span>`:u}`;case"text":return d`<input class="fld txt" type="text" aria-label=${t.label} data-control=${r}
          placeholder=${i.path?Ye:u} .value=${K(i.text)} ?disabled=${n}>`;case"long":return d`<button type="button" class="link" data-control=${r} data-act="edit" ?disabled=${n}>Edit</button>`;case"names":return d`<span class="toks" data-control=${r}>${i.items.length===0?d`<span class="dim" data-line="none">none</span>`:i.items.map(o=>d`<span class="tok" data-item=${o}>${o}</span>`)}
          <button type="button" class="link dim" data-act="add" ?disabled=${n}>Add</button></span>`;case"lines":return d`<span class="dim" data-control=${r}>${i.items.length} ${i.items.length===1?"line":"lines"}
          · <button type="button" class="link" data-act="edit" ?disabled=${n}>Edit</button></span>`;case"rows":return d`<button type="button" class="link dim" data-control=${r} data-act="add" ?disabled=${n}>Add a row</button>`;case"switches":return u;case"toml":return d`<button type="button" class="link" data-control=${r} data-act="edit" ?disabled=${n}>Edit</button>`;case"word":return d`<span data-control=${r} data-line="word">${i.text}</span>`}}#$(t,n){let i=t.control;switch(i.kind){case"long":return d`<span class="wide" data-line="first">${i.first}</span>`;case"lines":return i.first?d`<span class="wide" data-line="first">${i.first}</span>`:u;case"rows":return d`<div class="table" role="table" aria-label=${t.label}
            style=${We({"grid-template-columns":`repeat(${i.columns.length}, minmax(0, 1fr)) auto`})}>
          ${i.columns.map(r=>d`<span class="h" role="columnheader">${r}</span>`)}<span></span>
          ${i.rows.map((r,o)=>d`${i.columns.map(l=>d`<input class="fld" type="text"
              aria-label=${`${l}, row ${o+1}`} data-cell=${`${o}.${l}`} ?disabled=${n}
              .value=${K(xs(r[l]))}>`)}
            <button type="button" class="link dim" data-act="remove" ?disabled=${n}>Remove</button>`)}
        </div>`;case"switches":return d`<div class="entries">${i.toggles.map(r=>d`<div class="entry" data-entry=${r.key}>
          <span>${r.label}</span>
          <button type="button" class="sw" role="switch" aria-checked=${String(r.on)} aria-label=${`${t.label} ${r.label}`}
            data-control=${r.key} ?disabled=${n||r.locked}></button>
          ${r.line!==null||r.locked?d`<span class="l2">${[r.line,r.locked?Pt:null].filter(o=>o!==null).map(o=>d`<span data-line="line">${o}</span> `)}</span>`:u}
        </div>`)}</div>`;default:return u}}#g(t){let n=this.#R(t.module);return d`<div class="foot" data-part="foot">
      <span class="file" data-line="file">${t.file?`Kept in ${t.file}`:""}</span>
      ${n!==null?d`<a href=${`/m/${t.module}/${n.route}`} data-module=${t.module}
        data-route=${n.route} data-line="page">${n.title}</a>`:u}
    </div>`}#R(t){if(t==="ui")return{route:"modules",title:"Modules"};if(!cs(t))return null;let n=us(t).find(i=>i.name==="page");return n?{route:"",title:n.title}:null}};function xs(s){return s==null?"":typeof s=="string"?s:JSON.stringify(s)}customElements.get(fn)||customElements.define(fn,Bt);var xn="moxi-ui-page";function ws(s,e,t){if(s===0)return`No answer from the shell, so ${t} could not be read.`;if(s===403)return"This browser is signed out.";let n=e?.refused??e?.conflict??e?.error;return typeof n=="string"&&n?n:`The shell answered ${s} for ${t}.`}var _s=_`
  :host { display: block; min-width: 0; }
  * { box-sizing: border-box; }
  h1 { margin: 0 0 calc(var(--moxi-gap) * 2); font-size: 1.4em; }
  h2 { margin: 0 0 var(--moxi-gap); font-size: 1.1em; }
  h3 { margin: 0; font-size: 1em; }
  p { margin: 0; overflow-wrap: anywhere; }
  a { color: var(--moxi-accent); }
  code { font-family: var(--moxi-font-mono); font-size: var(--moxi-font-size-sm); overflow-wrap: anywhere; }
  section, .card { min-width: 0; }
  .bar { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--moxi-gap) calc(var(--moxi-gap) * 2);
         margin-bottom: calc(var(--moxi-gap) * 2); }
  .grid { display: grid; gap: calc(var(--moxi-gap) * 2); grid-template-columns: repeat(auto-fill, minmax(min(100%, 22rem), 1fr));
          margin-bottom: calc(var(--moxi-gap) * 3); }
  .list { display: grid; gap: var(--moxi-gap); margin-bottom: calc(var(--moxi-gap) * 3); }
  .card { display: grid; gap: 6px; padding: 10px 12px; border: 1px solid var(--moxi-border);
          border-radius: var(--moxi-radius-lg); background: var(--moxi-bg-raised); }
  .head { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--moxi-gap); }
  .head .grow { flex: 1; }
  .dim { color: var(--moxi-text-dim); }
  .small { font-size: var(--moxi-font-size-sm); }
  .tag { padding: 0 6px; border-radius: var(--moxi-radius); font-size: var(--moxi-font-size-sm); }
  .ok { color: var(--moxi-ok); }
  .off { color: var(--moxi-text-dim); }
  .warn { color: var(--moxi-warn); }
  .fail { color: var(--moxi-error); }
  .tag.ok { background: var(--moxi-add-soft); }
  .tag.warn { background: var(--moxi-warn-soft); }
  .tag.fail { background: var(--moxi-error-soft); }
  .tag.off { background: var(--moxi-bg-sunken); }
  .actions { display: flex; flex-wrap: wrap; gap: var(--moxi-gap); align-items: center; }
  button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
           background: var(--moxi-bg); color: var(--moxi-text); font: inherit; cursor: pointer; }
  button:hover:not(:disabled) { background: var(--moxi-bg-hover); }
  button:disabled { cursor: progress; }
  :focus-visible { outline: 2px solid var(--moxi-accent); outline-offset: 2px; }
  ul.rows { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
  ul.rows li { display: grid; gap: 2px; padding: 4px 8px; border-left: 3px solid var(--moxi-border); }
  ul.rows li.ok { border-left-color: var(--moxi-ok); }
  ul.rows li.warn { border-left-color: var(--moxi-warn); }
  ul.rows li.fail { border-left-color: var(--moxi-error); }
  ul.rows li > * { color: var(--moxi-text); }
  .problem { padding: 8px 12px; border-radius: var(--moxi-radius); background: var(--moxi-error-soft); color: var(--moxi-error); }
`,Ft=class extends b{constructor(){super(...arguments);this.route="";this.modules=null;this.checks=new Map;this.#t="none";this.#i="";this.#s=null;this.#e="";this.#r=0}static{this.styles=_s}static{this.properties={route:{type:String},modules:{state:!0},checks:{state:!0}}}#t;#i;#s;#e;#r;connectedCallback(){super.connectedCallback();let t=(this.getAttribute("route")??this.route??"").replace(/^\/+|\/+$/g,"");t===""?this.#t="home":t==="modules"?(this.#t="modules",this.#s=vs("ui","modules",n=>{this.modules=n})):t==="settings"||t.startsWith("settings/")?(this.#t="settings",this.#i=t):this.#t="none"}disconnectedCallback(){super.disconnectedCallback(),this.#s?.(),this.#s=null}render(){return this.#t==="home"?d`<moxi-ui-home></moxi-ui-home>`:this.#t==="modules"?this.#o():this.#t==="settings"?this.#c():d`<h1>Nothing is here</h1>
      <p>${this.#n("ui","modules","Open the modules view")}.</p>`}updated(t){super.updated(t),this.#t==="modules"&&t.has("modules")&&this.#a()}#n(t,n,i){return d`<a href=${`/m/${t}/${n}`} @click=${r=>{r.button!==0||r.metaKey||r.ctrlKey||r.shiftKey||r.altKey||(r.preventDefault(),$s(t,n))}}>${i}</a>`}#o(){let t=this.modules,n=t===null?[]:Et(t.modules);return d`
      ${dt()?u:d`<div class="bar">${this.#n("ui","settings","Settings")}</div>`}
      ${t?.error?d`<p class="problem">${t.error}</p>`:u}
      <div class="grid" id="home"></div>
      <h2>Installed</h2>
      ${t===null?d`<p class="dim">Reading the modules…</p>`:u}
      ${t!==null&&n.length===0?d`<p class="dim">No module is listed.</p>`:u}
      <div class="list">${n.map(i=>this.#l(i))}</div>
      ${dt()?u:d`<moxi-ui-gateway></moxi-ui-gateway>`}`}#l(t){let n=Oe(t),i=this.checks.get(t.name),r=t.name!=="ui"&&gn(t.name)&&Kt(t.name).some(o=>o.name==="page");return d`<div class="card" data-module=${t.name}>
      <div class="head">
        <h3>${t.name}</h3>
        ${t.version?d`<span class="dim small">${t.version}</span>`:u}
        <span class="grow"></span>
        <span class="tag ${n.tone}">${n.text}</span>
      </div>
      ${t.summary?d`<p class="dim">${t.summary}</p>`:u}
      ${Ne(t).map(o=>d`<p class="small ${o.tone??""}">${o.text}</p>`)}
      <div class="actions">
        ${r?this.#n(t.name,"","Open its panel"):u}
        ${dt()?u:d`<button type="button" ?disabled=${i?.busy===!0}
          @click=${()=>this.#d(t.name)}>${i?.busy?"Checking…":"Check"}</button>`}
      </div>
      ${i!==void 0&&!i.busy?this.#u(i):u}
    </div>`}#u(t){return t.error?d`<p class="small fail">${t.error}</p>`:t.rows.length===0?d`<p class="small dim">The doctor has no rows for it.</p>`:d`<ul class="rows">${Ue(t.rows).map(n=>d`
      <li class=${n.tone}>
        <div class="head"><span class="tag ${n.tone}">${n.tone}</span><code>${n.name}</code></div>
        <p class="small">${n.detail}</p>
        ${n.fix?d`<p class="small dim">Fix: <code>${n.fix}</code></p>`:u}
      </li>`)}</ul>`}async#d(t){this.checks=new Map(this.checks).set(t,{busy:!0,rows:[],error:null});let n=await bs("ui",`api/doctor?module=${encodeURIComponent(t)}`),i;n.status===200&&Array.isArray(n.data?.rows)?i={busy:!1,rows:n.data.rows,error:n.data.error??null}:i={busy:!1,rows:[],error:ws(n.status,n.data,"the doctor")},this.checks=new Map(this.checks).set(t,i)}async#a(){let t=this.renderRoot.querySelector("#home");if(t===null||this.modules===null)return;let n=Le(this.modules.modules).filter(o=>gn(o.module)),i=JSON.stringify(n.map(o=>[o.module,o.name,Kt(o.module).find(l=>l.name===o.name)?.url??""]));if(i===this.#e)return;this.#e=i;let r=this.#r+=1;t.replaceChildren();for(let o of n){let l=document.createElement("section");l.className="card",l.dataset.block=`${o.module}.${o.name}`;let a=document.createElement("h2");a.textContent=o.title;let c=document.createElement("div");l.append(a,c),t.append(l);let h=Kt(o.module).find(m=>m.name===o.name)?.url,p=await at(()=>ys(c,o.module,o.name),async()=>h===void 0?"no script":await lt(o.module,h),()=>r===this.#r);if(p===null)return;if(!("placed"in p)){let m=document.createElement("p");m.className="small warn",m.textContent=`${o.module}'s ${o.name} could not be shown.`,c.append(m)}}}#c(){return dt()?d`<p class="dim" data-line="desk">Settings are changed at the desk, on the computer's Moxi page.</p>
        <div class="bar">${this.#n("ui","","Home")}</div>`:d`<moxi-ui-settings route=${this.#i}></moxi-ui-settings>`}};customElements.get(xn)||customElements.define(xn,Ft);export{Ft as UiPage};
/*! Bundled license information:

@lit/reactive-element/css-tag.js:
  (**
   * @license
   * Copyright 2019 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

@lit/reactive-element/reactive-element.js:
lit-html/lit-html.js:
lit-element/lit-element.js:
lit-html/directive.js:
lit-html/directives/repeat.js:
  (**
   * @license
   * Copyright 2017 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

lit-html/is-server.js:
  (**
   * @license
   * Copyright 2022 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

lit-html/directive-helpers.js:
lit-html/directives/live.js:
  (**
   * @license
   * Copyright 2020 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)

lit-html/directives/style-map.js:
  (**
   * @license
   * Copyright 2018 Google LLC
   * SPDX-License-Identifier: BSD-3-Clause
   *)
*/
