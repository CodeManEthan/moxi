var ot=globalThis,at=ot.ShadowRoot&&(ot.ShadyCSS===void 0||ot.ShadyCSS.nativeShadow)&&"adoptedStyleSheets"in Document.prototype&&"replace"in CSSStyleSheet.prototype,Mt=Symbol(),me=new WeakMap,J=class{constructor(e,t,n){if(this._$cssResult$=!0,n!==Mt)throw Error("CSSResult is not constructable. Use `unsafeCSS` or `css` instead.");this.cssText=e,this.t=t}get styleSheet(){let e=this.o,t=this.t;if(at&&e===void 0){let n=t!==void 0&&t.length===1;n&&(e=me.get(t)),e===void 0&&((this.o=e=new CSSStyleSheet).replaceSync(this.cssText),n&&me.set(t,e))}return e}toString(){return this.cssText}},Y=s=>new J(typeof s=="string"?s:s+"",void 0,Mt),_=(s,...e)=>{let t=s.length===1?s[0]:e.reduce((n,i,r)=>n+(o=>{if(o._$cssResult$===!0)return o.cssText;if(typeof o=="number")return o;throw Error("Value passed to 'css' function must be a 'css' function result: "+o+". Use 'unsafeCSS' to pass non-literal values, but take care to ensure page security.")})(i)+s[r+1],s[0]);return new J(t,s,Mt)},ge=(s,e)=>{if(at)s.adoptedStyleSheets=e.map(t=>t instanceof CSSStyleSheet?t:t.styleSheet);else for(let t of e){let n=document.createElement("style"),i=ot.litNonce;i!==void 0&&n.setAttribute("nonce",i),n.textContent=t.cssText,s.appendChild(n)}},Ot=at?s=>s:s=>s instanceof CSSStyleSheet?(e=>{let t="";for(let n of e.cssRules)t+=n.cssText;return Y(t)})(s):s;var{is:ss,defineProperty:is,getOwnPropertyDescriptor:rs,getOwnPropertyNames:os,getOwnPropertySymbols:as,getPrototypeOf:ls}=Object,lt=globalThis,xe=lt.trustedTypes,ds=xe?xe.emptyScript:"",us=lt.reactiveElementPolyfillSupport,Q=(s,e)=>s,Nt={toAttribute(s,e){switch(e){case Boolean:s=s?ds:null;break;case Object:case Array:s=s==null?s:JSON.stringify(s)}return s},fromAttribute(s,e){let t=s;switch(e){case Boolean:t=s!==null;break;case Number:t=s===null?null:Number(s);break;case Object:case Array:try{t=JSON.parse(s)}catch{t=null}}return t}},$e=(s,e)=>!ss(s,e),be={attribute:!0,type:String,converter:Nt,reflect:!1,useDefault:!1,hasChanged:$e};Symbol.metadata??=Symbol("metadata"),lt.litPropertyMetadata??=new WeakMap;var E=class extends HTMLElement{static addInitializer(e){this._$Ei(),(this.l??=[]).push(e)}static get observedAttributes(){return this.finalize(),this._$Eh&&[...this._$Eh.keys()]}static createProperty(e,t=be){if(t.state&&(t.attribute=!1),this._$Ei(),this.prototype.hasOwnProperty(e)&&((t=Object.create(t)).wrapped=!0),this.elementProperties.set(e,t),!t.noAccessor){let n=Symbol(),i=this.getPropertyDescriptor(e,n,t);i!==void 0&&is(this.prototype,e,i)}}static getPropertyDescriptor(e,t,n){let{get:i,set:r}=rs(this.prototype,e)??{get(){return this[t]},set(o){this[t]=o}};return{get:i,set(o){let l=i?.call(this);r?.call(this,o),this.requestUpdate(e,l,n)},configurable:!0,enumerable:!0}}static getPropertyOptions(e){return this.elementProperties.get(e)??be}static _$Ei(){if(this.hasOwnProperty(Q("elementProperties")))return;let e=ls(this);e.finalize(),e.l!==void 0&&(this.l=[...e.l]),this.elementProperties=new Map(e.elementProperties)}static finalize(){if(this.hasOwnProperty(Q("finalized")))return;if(this.finalized=!0,this._$Ei(),this.hasOwnProperty(Q("properties"))){let t=this.properties,n=[...os(t),...as(t)];for(let i of n)this.createProperty(i,t[i])}let e=this[Symbol.metadata];if(e!==null){let t=litPropertyMetadata.get(e);if(t!==void 0)for(let[n,i]of t)this.elementProperties.set(n,i)}this._$Eh=new Map;for(let[t,n]of this.elementProperties){let i=this._$Eu(t,n);i!==void 0&&this._$Eh.set(i,t)}this.elementStyles=this.finalizeStyles(this.styles)}static finalizeStyles(e){let t=[];if(Array.isArray(e)){let n=new Set(e.flat(1/0).reverse());for(let i of n)t.unshift(Ot(i))}else e!==void 0&&t.push(Ot(e));return t}static _$Eu(e,t){let n=t.attribute;return n===!1?void 0:typeof n=="string"?n:typeof e=="string"?e.toLowerCase():void 0}constructor(){super(),this._$Ep=void 0,this.isUpdatePending=!1,this.hasUpdated=!1,this._$Em=null,this._$Ev()}_$Ev(){this._$ES=new Promise(e=>this.enableUpdating=e),this._$AL=new Map,this._$E_(),this.requestUpdate(),this.constructor.l?.forEach(e=>e(this))}addController(e){(this._$EO??=new Set).add(e),this.renderRoot!==void 0&&this.isConnected&&e.hostConnected?.()}removeController(e){this._$EO?.delete(e)}_$E_(){let e=new Map,t=this.constructor.elementProperties;for(let n of t.keys())this.hasOwnProperty(n)&&(e.set(n,this[n]),delete this[n]);e.size>0&&(this._$Ep=e)}createRenderRoot(){let e=this.shadowRoot??this.attachShadow(this.constructor.shadowRootOptions);return ge(e,this.constructor.elementStyles),e}connectedCallback(){this.renderRoot??=this.createRenderRoot(),this.enableUpdating(!0),this._$EO?.forEach(e=>e.hostConnected?.())}enableUpdating(e){}disconnectedCallback(){this._$EO?.forEach(e=>e.hostDisconnected?.())}attributeChangedCallback(e,t,n){this._$AK(e,n)}_$ET(e,t){let n=this.constructor.elementProperties.get(e),i=this.constructor._$Eu(e,n);if(i!==void 0&&n.reflect===!0){let r=(n.converter?.toAttribute!==void 0?n.converter:Nt).toAttribute(t,n.type);this._$Em=e,r==null?this.removeAttribute(i):this.setAttribute(i,r),this._$Em=null}}_$AK(e,t){let n=this.constructor,i=n._$Eh.get(e);if(i!==void 0&&this._$Em!==i){let r=n.getPropertyOptions(i),o=typeof r.converter=="function"?{fromAttribute:r.converter}:r.converter?.fromAttribute!==void 0?r.converter:Nt;this._$Em=i;let l=o.fromAttribute(t,r.type);this[i]=l??this._$Ej?.get(i)??l,this._$Em=null}}requestUpdate(e,t,n,i=!1,r){if(e!==void 0){let o=this.constructor;if(i===!1&&(r=this[e]),n??=o.getPropertyOptions(e),!((n.hasChanged??$e)(r,t)||n.useDefault&&n.reflect&&r===this._$Ej?.get(e)&&!this.hasAttribute(o._$Eu(e,n))))return;this.C(e,t,n)}this.isUpdatePending===!1&&(this._$ES=this._$EP())}C(e,t,{useDefault:n,reflect:i,wrapped:r},o){n&&!(this._$Ej??=new Map).has(e)&&(this._$Ej.set(e,o??t??this[e]),r!==!0||o!==void 0)||(this._$AL.has(e)||(this.hasUpdated||n||(t=void 0),this._$AL.set(e,t)),i===!0&&this._$Em!==e&&(this._$Eq??=new Set).add(e))}async _$EP(){this.isUpdatePending=!0;try{await this._$ES}catch(t){Promise.reject(t)}let e=this.scheduleUpdate();return e!=null&&await e,!this.isUpdatePending}scheduleUpdate(){return this.performUpdate()}performUpdate(){if(!this.isUpdatePending)return;if(!this.hasUpdated){if(this.renderRoot??=this.createRenderRoot(),this._$Ep){for(let[i,r]of this._$Ep)this[i]=r;this._$Ep=void 0}let n=this.constructor.elementProperties;if(n.size>0)for(let[i,r]of n){let{wrapped:o}=r,l=this[i];o!==!0||this._$AL.has(i)||l===void 0||this.C(i,void 0,r,l)}}let e=!1,t=this._$AL;try{e=this.shouldUpdate(t),e?(this.willUpdate(t),this._$EO?.forEach(n=>n.hostUpdate?.()),this.update(t)):this._$EM()}catch(n){throw e=!1,this._$EM(),n}e&&this._$AE(t)}willUpdate(e){}_$AE(e){this._$EO?.forEach(t=>t.hostUpdated?.()),this.hasUpdated||(this.hasUpdated=!0,this.firstUpdated(e)),this.updated(e)}_$EM(){this._$AL=new Map,this.isUpdatePending=!1}get updateComplete(){return this.getUpdateComplete()}getUpdateComplete(){return this._$ES}shouldUpdate(e){return!0}update(e){this._$Eq&&=this._$Eq.forEach(t=>this._$ET(t,this[t])),this._$EM()}updated(e){}firstUpdated(e){}};E.elementStyles=[],E.shadowRootOptions={mode:"open"},E[Q("elementProperties")]=new Map,E[Q("finalized")]=new Map,us?.({ReactiveElement:E}),(lt.reactiveElementVersions??=[]).push("2.1.2");var Lt=globalThis,ve=s=>s,dt=Lt.trustedTypes,ye=dt?dt.createPolicy("lit-html",{createHTML:s=>s}):void 0,Ht="$lit$",R=`lit$${Math.random().toFixed(9).slice(2)}$`,zt="?"+R,cs=`<${zt}>`,D=document,Z=()=>D.createComment(""),tt=s=>s===null||typeof s!="object"&&typeof s!="function",Dt=Array.isArray,Ee=s=>Dt(s)||typeof s?.[Symbol.iterator]=="function",Pt=`[ 	
\f\r]`,X=/<(?:(!--|\/[^a-zA-Z])|(\/?[a-zA-Z][^>\s]*)|(\/?$))/g,we=/-->/g,ke=/>/g,H=RegExp(`>|${Pt}(?:([^\\s"'>=/]+)(${Pt}*=${Pt}*(?:[^ 	
\f\r"'\`<>=]|("|')|))|$)`,"g"),Se=/'/g,Ae=/"/g,Re=/^(?:script|style|textarea|title)$/i,Ut=s=>(e,...t)=>({_$litType$:s,strings:e,values:t}),u=Ut(1),Si=Ut(2),Ai=Ut(3),b=Symbol.for("lit-noChange"),h=Symbol.for("lit-nothing"),_e=new WeakMap,z=D.createTreeWalker(D,129);function Te(s,e){if(!Dt(s)||!s.hasOwnProperty("raw"))throw Error("invalid template strings array");return ye!==void 0?ye.createHTML(e):e}var Ce=(s,e)=>{let t=s.length-1,n=[],i,r=e===2?"<svg>":e===3?"<math>":"",o=X;for(let l=0;l<t;l++){let a=s[l],d,c,p=-1,f=0;for(;f<a.length&&(o.lastIndex=f,c=o.exec(a),c!==null);)f=o.lastIndex,o===X?c[1]==="!--"?o=we:c[1]!==void 0?o=ke:c[2]!==void 0?(Re.test(c[2])&&(i=RegExp("</"+c[2],"g")),o=H):c[3]!==void 0&&(o=H):o===H?c[0]===">"?(o=i??X,p=-1):c[1]===void 0?p=-2:(p=o.lastIndex-c[2].length,d=c[1],o=c[3]===void 0?H:c[3]==='"'?Ae:Se):o===Ae||o===Se?o=H:o===we||o===ke?o=X:(o=H,i=void 0);let m=o===H&&s[l+1].startsWith("/>")?" ":"";r+=o===X?a+cs:p>=0?(n.push(d),a.slice(0,p)+Ht+a.slice(p)+R+m):a+R+(p===-2?l:m)}return[Te(s,r+(s[t]||"<?>")+(e===2?"</svg>":e===3?"</math>":"")),n]},et=class s{constructor({strings:e,_$litType$:t},n){let i;this.parts=[];let r=0,o=0,l=e.length-1,a=this.parts,[d,c]=Ce(e,t);if(this.el=s.createElement(d,n),z.currentNode=this.el.content,t===2||t===3){let p=this.el.content.firstChild;p.replaceWith(...p.childNodes)}for(;(i=z.nextNode())!==null&&a.length<l;){if(i.nodeType===1){if(i.hasAttributes())for(let p of i.getAttributeNames())if(p.endsWith(Ht)){let f=c[o++],m=i.getAttribute(p).split(R),g=/([.?@])?(.*)/.exec(f);a.push({type:1,index:r,name:g[2],strings:m,ctor:g[1]==="."?ct:g[1]==="?"?pt:g[1]==="@"?ht:I}),i.removeAttribute(p)}else p.startsWith(R)&&(a.push({type:6,index:r}),i.removeAttribute(p));if(Re.test(i.tagName)){let p=i.textContent.split(R),f=p.length-1;if(f>0){i.textContent=dt?dt.emptyScript:"";for(let m=0;m<f;m++)i.append(p[m],Z()),z.nextNode(),a.push({type:2,index:++r});i.append(p[f],Z())}}}else if(i.nodeType===8)if(i.data===zt)a.push({type:2,index:r});else{let p=-1;for(;(p=i.data.indexOf(R,p+1))!==-1;)a.push({type:7,index:r}),p+=R.length-1}r++}}static createElement(e,t){let n=D.createElement("template");return n.innerHTML=e,n}};function U(s,e,t=s,n){if(e===b)return e;let i=n!==void 0?t._$Co?.[n]:t._$Cl,r=tt(e)?void 0:e._$litDirective$;return i?.constructor!==r&&(i?._$AO?.(!1),r===void 0?i=void 0:(i=new r(s),i._$AT(s,t,n)),n!==void 0?(t._$Co??=[])[n]=i:t._$Cl=i),i!==void 0&&(e=U(s,i._$AS(s,e.values),i,n)),e}var ut=class{constructor(e,t){this._$AV=[],this._$AN=void 0,this._$AD=e,this._$AM=t}get parentNode(){return this._$AM.parentNode}get _$AU(){return this._$AM._$AU}u(e){let{el:{content:t},parts:n}=this._$AD,i=(e?.creationScope??D).importNode(t,!0);z.currentNode=i;let r=z.nextNode(),o=0,l=0,a=n[0];for(;a!==void 0;){if(o===a.index){let d;a.type===2?d=new V(r,r.nextSibling,this,e):a.type===1?d=new a.ctor(r,a.name,a.strings,this,e):a.type===6&&(d=new ft(r,this,e)),this._$AV.push(d),a=n[++l]}o!==a?.index&&(r=z.nextNode(),o++)}return z.currentNode=D,i}p(e){let t=0;for(let n of this._$AV)n!==void 0&&(n.strings!==void 0?(n._$AI(e,n,t),t+=n.strings.length-2):n._$AI(e[t])),t++}},V=class s{get _$AU(){return this._$AM?._$AU??this._$Cv}constructor(e,t,n,i){this.type=2,this._$AH=h,this._$AN=void 0,this._$AA=e,this._$AB=t,this._$AM=n,this.options=i,this._$Cv=i?.isConnected??!0}get parentNode(){let e=this._$AA.parentNode,t=this._$AM;return t!==void 0&&e?.nodeType===11&&(e=t.parentNode),e}get startNode(){return this._$AA}get endNode(){return this._$AB}_$AI(e,t=this){e=U(this,e,t),tt(e)?e===h||e==null||e===""?(this._$AH!==h&&this._$AR(),this._$AH=h):e!==this._$AH&&e!==b&&this._(e):e._$litType$!==void 0?this.$(e):e.nodeType!==void 0?this.T(e):Ee(e)?this.k(e):this._(e)}O(e){return this._$AA.parentNode.insertBefore(e,this._$AB)}T(e){this._$AH!==e&&(this._$AR(),this._$AH=this.O(e))}_(e){this._$AH!==h&&tt(this._$AH)?this._$AA.nextSibling.data=e:this.T(D.createTextNode(e)),this._$AH=e}$(e){let{values:t,_$litType$:n}=e,i=typeof n=="number"?this._$AC(e):(n.el===void 0&&(n.el=et.createElement(Te(n.h,n.h[0]),this.options)),n);if(this._$AH?._$AD===i)this._$AH.p(t);else{let r=new ut(i,this),o=r.u(this.options);r.p(t),this.T(o),this._$AH=r}}_$AC(e){let t=_e.get(e.strings);return t===void 0&&_e.set(e.strings,t=new et(e)),t}k(e){Dt(this._$AH)||(this._$AH=[],this._$AR());let t=this._$AH,n,i=0;for(let r of e)i===t.length?t.push(n=new s(this.O(Z()),this.O(Z()),this,this.options)):n=t[i],n._$AI(r),i++;i<t.length&&(this._$AR(n&&n._$AB.nextSibling,i),t.length=i)}_$AR(e=this._$AA.nextSibling,t){for(this._$AP?.(!1,!0,t);e!==this._$AB;){let n=ve(e).nextSibling;ve(e).remove(),e=n}}setConnected(e){this._$AM===void 0&&(this._$Cv=e,this._$AP?.(e))}},I=class{get tagName(){return this.element.tagName}get _$AU(){return this._$AM._$AU}constructor(e,t,n,i,r){this.type=1,this._$AH=h,this._$AN=void 0,this.element=e,this.name=t,this._$AM=i,this.options=r,n.length>2||n[0]!==""||n[1]!==""?(this._$AH=Array(n.length-1).fill(new String),this.strings=n):this._$AH=h}_$AI(e,t=this,n,i){let r=this.strings,o=!1;if(r===void 0)e=U(this,e,t,0),o=!tt(e)||e!==this._$AH&&e!==b,o&&(this._$AH=e);else{let l=e,a,d;for(e=r[0],a=0;a<r.length-1;a++)d=U(this,l[n+a],t,a),d===b&&(d=this._$AH[a]),o||=!tt(d)||d!==this._$AH[a],d===h?e=h:e!==h&&(e+=(d??"")+r[a+1]),this._$AH[a]=d}o&&!i&&this.j(e)}j(e){e===h?this.element.removeAttribute(this.name):this.element.setAttribute(this.name,e??"")}},ct=class extends I{constructor(){super(...arguments),this.type=3}j(e){this.element[this.name]=e===h?void 0:e}},pt=class extends I{constructor(){super(...arguments),this.type=4}j(e){this.element.toggleAttribute(this.name,!!e&&e!==h)}},ht=class extends I{constructor(e,t,n,i,r){super(e,t,n,i,r),this.type=5}_$AI(e,t=this){if((e=U(this,e,t,0)??h)===b)return;let n=this._$AH,i=e===h&&n!==h||e.capture!==n.capture||e.once!==n.once||e.passive!==n.passive,r=e!==h&&(n===h||i);i&&this.element.removeEventListener(this.name,this,n),r&&this.element.addEventListener(this.name,this,e),this._$AH=e}handleEvent(e){typeof this._$AH=="function"?this._$AH.call(this.options?.host??this.element,e):this._$AH.handleEvent(e)}},ft=class{constructor(e,t,n){this.element=e,this.type=6,this._$AN=void 0,this._$AM=t,this.options=n}get _$AU(){return this._$AM._$AU}_$AI(e){U(this,e)}},Me={M:Ht,P:R,A:zt,C:1,L:Ce,R:ut,D:Ee,V:U,I:V,H:I,N:pt,U:ht,B:ct,F:ft},ps=Lt.litHtmlPolyfillSupport;ps?.(et,V),(Lt.litHtmlVersions??=[]).push("3.3.3");var Oe=(s,e,t)=>{let n=t?.renderBefore??e,i=n._$litPart$;if(i===void 0){let r=t?.renderBefore??null;n._$litPart$=i=new V(e.insertBefore(Z(),r),r,void 0,t??{})}return i._$AI(s),i};var It=globalThis,$=class extends E{constructor(){super(...arguments),this.renderOptions={host:this},this._$Do=void 0}createRenderRoot(){let e=super.createRenderRoot();return this.renderOptions.renderBefore??=e.firstChild,e}update(e){let t=this.render();this.hasUpdated||(this.renderOptions.isConnected=this.isConnected),super.update(e),this._$Do=Oe(t,this.renderRoot,this.renderOptions)}connectedCallback(){super.connectedCallback(),this._$Do?.setConnected(!0)}disconnectedCallback(){super.disconnectedCallback(),this._$Do?.setConnected(!1)}render(){return b}};$._$litElement$=!0,$.finalized=!0,It.litElementHydrateSupport?.({LitElement:$});var hs=It.litElementPolyfillSupport;hs?.({LitElement:$});(It.litElementVersions??=[]).push("4.2.2");import{api as fi,components as he,gateway as Tt,installed as es,navigate as mi,place as gi,subscribe as xi}from"/m/ui/client.js";import{api as mt}from"/m/ui/client.js";var Ne=[-1,10,16,26,18,24,16,18,22,22,26,30,22,22,24,24,28,28,26,26,26,26,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28],Pe=[-1,1,1,1,2,2,4,4,4,5,5,5,8,9,9,10,10,11,13,14,16,17,17,18,20,21,23,25,26,28,29,31,33,35,37,38,40,43,45,47,49],fs=0;function A(s,e){return(s>>>e&1)!==0}function Le(s){let e=(16*s+128)*s+64;if(s>=2){let t=Math.floor(s/7)+2;e-=(25*t-10)*t-55,s>=7&&(e-=36)}return e}function ms(s){return Math.floor(Le(s)/8)-Ne[s]*Pe[s]}function gs(s){if(s===1)return[];let e=Math.floor(s/7)+2,t=Math.floor((s*8+e*3+5)/(e*4-4))*2,n=[6];for(let i=s*4+10;n.length<e;i-=t)n.splice(1,0,i);return n}function jt(s,e){let t=0;for(let n=7;n>=0;n--)t=t<<1^(t>>>7)*285,t^=(e>>>n&1)*s;return t}function xs(s){let e=new Array(s).fill(0);e[s-1]=1;let t=1;for(let n=0;n<s;n++){for(let i=0;i<e.length;i++)e[i]=jt(e[i],t),i+1<e.length&&(e[i]^=e[i+1]);t=jt(t,2)}return e}function bs(s,e){let t=new Array(e.length).fill(0);for(let n of s){let i=n^t.shift();t.push(0),e.forEach((r,o)=>{t[o]^=jt(r,i)})}return t}function $s(s,e){let t=Pe[e],n=Ne[e],i=Math.floor(Le(e)/8),r=t-i%t,o=Math.floor(i/t),l=xs(n),a=[];for(let c=0,p=0;c<t;c++){let f=s.slice(p,p+o-n+(c<r?0:1));p+=f.length;let m=bs(f,l);c<r&&f.push(0),a.push(f.concat(m))}let d=[];for(let c=0;c<a[0].length;c++)a.forEach((p,f)=>{(c!==o-n||f>=r)&&d.push(p[c])});return d}function vs(s,e){let t=ms(e)*8,n=[],i=(l,a)=>{for(let d=a-1;d>=0;d--)n.push(l>>>d&1)},r=e<=9?8:16;if(s.length>=2**r)return null;i(4,4),i(s.length,r);for(let l of s)i(l,8);if(n.length>t)return null;i(0,Math.min(4,t-n.length)),i(0,(8-n.length%8)%8);let o=[];for(let l=0;l<n.length;l+=8)o.push(n.slice(l,l+8).reduce((a,d)=>a<<1|d,0));for(let l=236;o.length<t/8;l^=253)o.push(l);return o}var Vt=class{constructor(e){this.version=e,this.size=e*4+17,this.dark=Array.from({length:this.size},()=>new Array(this.size).fill(!1)),this.fixed=Array.from({length:this.size},()=>new Array(this.size).fill(!1))}set(e,t,n){this.dark[t][e]=n,this.fixed[t][e]=!0}patterns(){let{size:e}=this;for(let i=0;i<e;i++)this.set(6,i,i%2===0),this.set(i,6,i%2===0);for(let[i,r]of[[3,3],[e-4,3],[3,e-4]])for(let o=-4;o<=4;o++)for(let l=-4;l<=4;l++){let a=i+l,d=r+o,c=Math.max(Math.abs(l),Math.abs(o));a>=0&&a<e&&d>=0&&d<e&&this.set(a,d,c!==2&&c!==4)}let t=gs(this.version),n=t.length-1;if(t.forEach((i,r)=>t.forEach((o,l)=>{if(!(r===0&&l===0||r===0&&l===n||r===n&&l===0))for(let a=-2;a<=2;a++)for(let d=-2;d<=2;d++)this.set(i+d,o+a,Math.max(Math.abs(d),Math.abs(a))!==1)})),this.format(0),this.version>=7){let i=this.version;for(let o=0;o<12;o++)i=i<<1^(i>>>11)*7973;let r=this.version<<12|i;for(let o=0;o<18;o++){let l=e-11+o%3,a=Math.floor(o/3);this.set(l,a,A(r,o)),this.set(a,l,A(r,o))}}}format(e){let t=fs<<3|e,n=t;for(let o=0;o<10;o++)n=n<<1^(n>>>9)*1335;let i=(t<<10|n)^21522,{size:r}=this;for(let o=0;o<=5;o++)this.set(8,o,A(i,o));this.set(8,7,A(i,6)),this.set(8,8,A(i,7)),this.set(7,8,A(i,8));for(let o=9;o<15;o++)this.set(14-o,8,A(i,o));for(let o=0;o<8;o++)this.set(r-1-o,8,A(i,o));for(let o=8;o<15;o++)this.set(8,r-15+o,A(i,o));this.set(8,r-8,!0)}place(e){let{size:t}=this,n=0;for(let i=t-1;i>=1;i-=2){i===6&&(i=5);for(let r=0;r<t;r++)for(let o=0;o<2;o++){let l=i-o,a=(i+1&2)===0?t-1-r:r;!this.fixed[a][l]&&n<e.length*8&&(this.dark[a][l]=A(e[n>>>3],7-(n&7)),n++)}}}mask(e){for(let t=0;t<this.size;t++)for(let n=0;n<this.size;n++)!this.fixed[t][n]&&ys[e](n,t)&&(this.dark[t][n]=!this.dark[t][n])}},ys=[(s,e)=>(s+e)%2===0,(s,e)=>e%2===0,s=>s%3===0,(s,e)=>(s+e)%3===0,(s,e)=>(Math.floor(s/3)+Math.floor(e/2))%2===0,(s,e)=>s*e%2+s*e%3===0,(s,e)=>(s*e%2+s*e%3)%2===0,(s,e)=>((s+e)%2+s*e%3)%2===0];function ws(s){let e=s.length,t=0,n=[];for(let l=0;l<e;l++)n.push(s[l]),n.push(s.map(a=>a[l]));let i=[!0,!1,!0,!0,!0,!1,!0];for(let l of n){for(let a=0;a<e;){let d=a;for(;d<e&&l[d]===l[a];)d++;d-a>=5&&(t+=d-a-2),a=d}for(let a=0;a+7<=e;a++){if(!i.every((c,p)=>l[a+p]===c))continue;let d=(c,p)=>{for(let f=1;f<=4;f++){let m=c+p*f;if(m>=0&&m<e&&l[m])return!1}return!0};d(a,-1)&&(t+=40),d(a+6,1)&&(t+=40)}}for(let l=0;l+1<e;l++)for(let a=0;a+1<e;a++){let d=s[l][a];d===s[l][a+1]&&d===s[l+1][a]&&d===s[l+1][a+1]&&(t+=3)}let r=e*e,o=s.reduce((l,a)=>l+a.filter(Boolean).length,0);return t+=Math.max(0,Math.ceil(Math.abs(o*20-r*10)/r)-1)*10,t}function He(s,e){let t=new TextEncoder().encode(s);for(let n=1;n<=40;n++){let i=vs(t,n);if(i===null)continue;let r=$s(i,n),o=e===void 0?[0,1,2,3,4,5,6,7]:[e],l=null;for(let d of o){let c=new Vt(n);c.patterns(),c.place(r),c.mask(d),c.format(d);let p=o.length===1?0:ws(c.dark);(l===null||p<l.score)&&(l={mask:d,score:p,dark:c.dark})}let a=l;return{version:n,mask:a.mask,size:n*4+17,dark:a.dark}}throw new RangeError("the text is too long for a QR code")}function ze(s){let e=[];return s.dark.forEach((t,n)=>t.forEach((i,r)=>{i&&e.push(`M${r} ${n}h1v1h-1z`)})),e.join("")}var De="moxi-ui-gateway",Ue=12e4,ks=3e3;function Ie(s){return!Number.isFinite(s)||s<=0?"never":new Date(s*1e3).toLocaleString([],{dateStyle:"medium",timeStyle:"short"})}function je(s){let e=Date.parse(s);return Number.isFinite(e)?new Date(e).toLocaleString([],{dateStyle:"medium",timeStyle:"medium"}):s}function gt(s,e,t){if(s===0)return`No answer from the shell, so ${t} failed.`;if(s===403)return"This browser is signed out.";let n=e?.refused??e?.conflict??e?.error;return typeof n=="string"&&n?n:`The shell answered ${s} for ${t}.`}var Bt=class extends ${constructor(){super(...arguments);this.devices=null;this.listError=null;this.code=null;this.note=null;this.busy=!1;this.rows=null;this.signins=new Map;this.logError=null;this.#t=null;this.#l=new Set}static{this.styles=_`
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
  `}static{this.properties={devices:{state:!0},listError:{state:!0},code:{state:!0},note:{state:!0},busy:{state:!0},rows:{state:!0},signins:{state:!0},logError:{state:!0}}}#t;#l;connectedCallback(){super.connectedCallback(),this.#c(),this.#u()}disconnectedCallback(){super.disconnectedCallback(),this.#e()}render(){let t=this.devices??[];return u`<section class="card" data-section="gateway">
      <h2>Gateway</h2>
      <p class="dim small">Pair a phone to use Moxi from it through the tunnel. The phone scans
        the code with its camera, then makes a passkey.</p>
      <div class="actions">
        <button type="button" ?disabled=${this.busy} @click=${()=>this.#o()}>
          ${this.code?"New pairing code":"Pair a phone"}</button>
      </div>
      ${this.code?this.#s(this.code):h}
      ${this.note?u`<p class="small ${this.note.tone}" role="status">${this.note.text}</p>`:h}
      ${this.listError?u`<p class="small fail">${this.listError}</p>`:h}
      ${this.devices!==null&&t.length===0?u`<p class="dim small">No phone is paired.</p>`:h}
      ${t.length?u`<ul>${t.map(n=>this.#d(n))}</ul>`:h}
      ${this.#r()}
    </section>`}#r(){return this.logError?u`<p class="small fail">${this.logError}</p>`:this.rows===null?h:u`<details class="log" @toggle=${t=>{t.target.open&&this.#u()}}>
      <summary class="small">Activity: the audit log's last ${this.rows.length} rows, from today and yesterday</summary>
      ${this.rows.length?u`<ul class="small">${this.rows.map(t=>u`<li>
          <span class="dim">${je(t.at)}</span> <strong>${t.kind}</strong> <code>${t.detail}</code></li>`)}</ul>`:u`<p class="dim small">Nothing yet.</p>`}
    </details>`}#s(t){let n=t.size+8;return u`<div class="qr">
      ${t.size?u`<svg viewBox="-4 -4 ${n} ${n}" role="img" aria-label="QR code of the pairing link"
          shape-rendering="crispEdges">
        <rect x="-4" y="-4" width=${n} height=${n} fill="#ffffff"></rect><!-- colour-fixed: scanners need dark on light -->
        <path d=${t.path} fill="#000000"></path><!-- colour-fixed: scanners need dark on light --></svg>`:h}
      <p class="small"><code>${t.url}</code></p>
      <p class="dim small">It works once, for two minutes.</p>
    </div>`}#d(t){return u`<li class=${t.stale?"stale":""} data-device=${t.id}>
      <div class="head"><strong>${t.name}</strong><span class="grow"></span>
        <button type="button" ?disabled=${this.busy} @click=${()=>this.#n(t)}>Revoke</button></div>
      <p class="small dim">Paired ${Ie(t.created)}; last signed in ${Ie(t.used)}.</p>
      ${this.#i(t.id)}
      ${t.passkey?h:u`<p class="small dim">No passkey: it signs in again only by a new QR pairing.</p>`}
      ${t.stale?u`<p class="small warn">The gateway's address changed since it paired, so it
        signs in to nothing. Pair it again, then revoke this entry.</p>`:h}
    </li>`}#i(t){let n=this.signins.get(t)??[];return n.length?u`<p class="small dim">Sign-ins: ${n.map(je).join(", ")}.</p>`:h}async#u(){let t=await mt("ui","api/gateway/log");t.status===200&&Array.isArray(t.data?.rows)&&Array.isArray(t.data?.signins)?(this.rows=t.data.rows,this.signins=new Map(t.data.signins.map(n=>[n.device,n.at])),this.logError=null):this.logError=gt(t.status,t.data,"reading the audit log")}async#c(){let t=await mt("ui","api/gateway/devices");if(t.status===200&&Array.isArray(t.data?.devices)){let n=t.data.devices;if(this.code!==null){let i=n.find(r=>!this.#l.has(r.id));i!==void 0&&(this.code=null,this.#e(),this.note={text:`Paired: ${i.name}.`,tone:""})}this.devices=n,this.listError=null}else this.listError=gt(t.status,t.data,"reading the devices")}async#o(){this.busy=!0,this.note=null;let t=await mt("ui","api/gateway/pair",{});if(this.busy=!1,t.status!==200||typeof t.data?.url!="string"){this.code=null,this.#e(),this.note={text:gt(t.status,t.data,"pairing"),tone:"fail"};return}let n=t.data.url,i;try{i=He(n)}catch{this.note={text:"The pairing link is too long for a QR code; open it on the phone by hand.",tone:"warn"},this.code={url:n,path:"",size:0,until:Date.now()+Ue};return}this.#l=new Set((this.devices??[]).map(r=>r.id)),this.code={url:n,path:ze(i),size:i.size,until:Date.now()+Ue},this.#e(),this.#t=setInterval(()=>{if(this.code===null||Date.now()>this.code.until){this.code!==null&&(this.note={text:"That pairing code has expired.",tone:"dim"}),this.code=null,this.#e();return}this.#c()},ks)}async#n(t){this.busy=!0;let n=await mt("ui","api/gateway/revoke",{id:t.id});this.busy=!1,n.status===200?this.note={text:t.passkey?`Revoked ${t.name}. Its passkey stays on the phone until you remove it in the phone's settings.`:`Revoked ${t.name}.`,tone:""}:this.note={text:gt(n.status,n.data,"revoking"),tone:"fail"},await this.#c(),await this.#u()}#e(){this.#t!==null&&clearInterval(this.#t),this.#t=null}};customElements.get(De)||customElements.define(De,Bt);var S={ATTRIBUTE:1,CHILD:2,PROPERTY:3,BOOLEAN_ATTRIBUTE:4,EVENT:5,ELEMENT:6},B=s=>(...e)=>({_$litDirective$:s,values:e}),N=class{constructor(e){}get _$AU(){return this._$AM._$AU}_$AT(e,t,n){this._$Ct=e,this._$AM=t,this._$Ci=n}_$AS(e,t){return this.update(e,t)}update(e,t){return this.render(...t)}};var{I:Ss}=Me,Ve=s=>s;var qe=s=>s.strings===void 0,Be=()=>document.createComment(""),q=(s,e,t)=>{let n=s._$AA.parentNode,i=e===void 0?s._$AB:e._$AA;if(t===void 0){let r=n.insertBefore(Be(),i),o=n.insertBefore(Be(),i);t=new Ss(r,o,s,s.options)}else{let r=t._$AB.nextSibling,o=t._$AM,l=o!==s;if(l){let a;t._$AQ?.(s),t._$AM=s,t._$AP!==void 0&&(a=s._$AU)!==o._$AU&&t._$AP(a)}if(r!==i||l){let a=t._$AA;for(;a!==r;){let d=Ve(a).nextSibling;Ve(n).insertBefore(a,i),a=d}}}return t},P=(s,e,t=s)=>(s._$AI(e,t),s),As={},xt=(s,e=As)=>s._$AH=e,Ke=s=>s._$AH,bt=s=>{s._$AR(),s._$AA.remove()};var Fe=(s,e,t)=>{let n=new Map;for(let i=e;i<=t;i++)n.set(s[i],i);return n},We=B(class extends N{constructor(s){if(super(s),s.type!==S.CHILD)throw Error("repeat() can only be used in text expressions")}dt(s,e,t){let n;t===void 0?t=e:e!==void 0&&(n=e);let i=[],r=[],o=0;for(let l of s)i[o]=n?n(l,o):o,r[o]=t(l,o),o++;return{values:r,keys:i}}render(s,e,t){return this.dt(s,e,t).values}update(s,[e,t,n]){let i=Ke(s),{values:r,keys:o}=this.dt(e,t,n);if(!Array.isArray(i))return this.ut=o,r;let l=this.ut??=[],a=[],d,c,p=0,f=i.length-1,m=0,g=r.length-1;for(;p<=f&&m<=g;)if(i[p]===null)p++;else if(i[f]===null)f--;else if(l[p]===o[m])a[m]=P(i[p],r[m]),p++,m++;else if(l[f]===o[g])a[g]=P(i[f],r[g]),f--,g--;else if(l[p]===o[g])a[g]=P(i[p],r[g]),q(s,a[g+1],i[p]),p++,g--;else if(l[f]===o[m])a[m]=P(i[f],r[m]),q(s,i[p],i[f]),f--,m++;else if(d===void 0&&(d=Fe(o,m,g),c=Fe(l,p,f)),d.has(l[p]))if(d.has(l[f])){let x=c.get(o[m]),y=x!==void 0?i[x]:null;if(y===null){let w=q(s,i[p]);P(w,r[m]),a[m]=w}else a[m]=P(y,r[m]),q(s,i[p],y),i[x]=null;m++}else bt(i[f]),f--;else bt(i[p]),p++;for(;m<=g;){let x=q(s,a[g+1]);P(x,r[m]),a[m++]=x}for(;p<=f;){let x=i[p++];x!==null&&bt(x)}return this.ut=o,xt(s,a),b}});import{components as fn,gateway as dn,installed as un,navigate as Rs,place as Ts,subscribe as Cs}from"/m/ui/client.js";function Ge(s){if(typeof s!="object"||s===null)return null;let e=s;if(!Array.isArray(e.parts))return null;let t=[];for(let r of e.parts)typeof r?.text!="string"||r.text===""||t.push({text:r.text,heavy:r.heavy===!0});let n=typeof e.needs=="number"&&Number.isFinite(e.needs)&&e.needs>0?e.needs:0,i=e.opens===!0;return{parts:t,time:typeof e.time=="string"?e.time:"",needs:n,opens:i,open:i&&e.open===!0}}function qt(s){let e=s.filter(n=>n.report!==null),t=n=>n.report?.needs||1/0;return e.map((n,i)=>({entry:n,at:i})).sort((n,i)=>{let[r,o]=[t(n.entry),t(i.entry)];return(r===o?0:r<o?-1:1)||n.entry.order-i.entry.order||n.at-i.at}).map(n=>n.entry)}function Je(s){return[...qt(s),...s.filter(e=>e.report===null)]}function Ye(s){return s.some(e=>(e.report?.needs??0)>0)}function Qe(s){return s.opens?s.open?"held":"toggle":"link"}function $t(s,e){return s===null||!s.opens?!1:s.open||e}var Xe=1100,_s=340;function Ze(s){if(typeof s!="object"||s===null)return 0;let e=s.width;return typeof e=="number"&&Number.isFinite(e)&&e>0?e:0}function tn(s){return s>0?`calc(50% - ${_s+s/2}px)`:""}function en(s){return s.state==="present"?{tone:"ok",text:"installed"}:{tone:"fail",text:s.reason?`${s.state}: ${s.reason}`:s.state}}function nn(s){let e=[];s.page&&s.export_error?e.push({tone:"fail",text:`Its page can't be served: ${s.export_error}`}):s.page&&!s.mounted&&s.state==="present"&&e.push({tone:"warn",text:"Its page is not mounted yet."});let t=s.components.map(n=>n.title||n.name);t.length&&e.push({tone:null,text:`Components: ${t.join(", ")}`});for(let n of s.refused)e.push({tone:"warn",text:`${n.name} is not shown: ${n.reason}`});return e}function Ft(s){let e=t=>t.name==="ui"?0:t.state==="present"?1:2;return[...s].sort((t,n)=>e(t)-e(n)||(t.name<n.name?-1:t.name>n.name?1:0))}function sn(s){let e=[];for(let t of Ft(s))if(t.state==="present")for(let n of[...t.components].sort((i,r)=>i.name<r.name?-1:1))n.home&&n.place==="block"&&!n.home_as&&e.push({module:t.name,name:n.name,title:n.title});return e}var Es=500;function rn(s,e){let t=[];for(let i of s)if(i.state==="present")for(let r of i.components)!r.home||r.place!=="block"||r.home_as!==e||t.push({module:i.name,name:r.name,title:r.title,order:typeof r.order=="number"?r.order:Es});let n=(i,r)=>i<r?-1:i>r?1:0;return t.sort((i,r)=>i.order-r.order||n(i.module,r.module)||n(i.name,r.name))}function on(s){return rn(s,"row")}function an(s){return rn(s,"main")[0]??null}var Kt={fail:0,warn:1,off:2,ok:3};function ln(s){return s.map(e=>({...e,tone:e.state in Kt?e.state:"warn"})).sort((e,t)=>Kt[e.tone]-Kt[t.tone])}function Wt(s,e,t){let n=typeof t=="object"&&t!==null?t:{},i=typeof n.log=="string"&&n.log?n.log:null,r=i?[`Its log: ${i}`]:[];switch(e===0?"network":n.error){case"start":{let o=typeof n.exit=="number"?n.exit:null;return{title:`${s}'s page server did not start`,lines:[o===null?"It gave no ready line within 10 s and was stopped.":`It exited with code ${o}.`,...r,"The next try is at the first request after 10 s."]}}case"unreachable":return{title:`${s}'s page server can't be reached`,lines:["The shell started it but could not connect to it.",...r]};case"upstream":return{title:`${s}'s page server gave no answer`,lines:[...r]};case"timeout":return{title:`${s}'s page server is not answering`,lines:["No answer came within 30 s."]};case"stopping":return{title:"The shell is stopping",lines:["Run moxi ui open to start it again."]};case"network":return{title:"No answer from the shell",lines:["The shell may have stopped. Run moxi ui open to start it again."]};default:return null}}async function vt(s,e,t){let n=async()=>{let l=null;try{l=await s()}catch(a){console.error(a)}if(!t()){l?.remove();return}return l},i=await n();if(i===void 0)return null;if(i!==null)return{placed:i};let r=await e();if(!t())return null;if(r!==null)return{failure:r};let o=await n();return o===void 0?null:o!==null?{placed:o}:{failure:null}}async function yt(s,e){let t;try{t=await fetch(e,{credentials:"same-origin",cache:"no-store"})}catch{return Wt(s,0,null)}if(t.ok)return null;let n=null;try{n=await t.json()}catch{n=null}return Wt(s,t.status,n)}var cn="moxi-ui-home",Ms="moxi-home-row",Os="moxi-home-side",Gt="--moxi-home-side-margin";function Jt(s){return fn(s.module).find(e=>e.name===s.name)?.url??""}async function pn(s,e,t){let n=Jt(e),i=await vt(()=>Ts(s,e.module,e.name),async()=>n===""?"no script":await yt(e.module,n),t);return i!==null&&"placed"in i}function hn(s){let e=s.activeElement;for(;e?.shadowRoot?.activeElement;)e=e.shadowRoot.activeElement;return e}var Yt=class extends ${constructor(){super();this.modules=null;this.rows=[];this.main=null;this.loading=!1;this.#t=document.createElement("div");this.#l="";this.#r=0;this.#s=null;this.#d=null;this.#i=null;this.#t.addEventListener(Os,t=>{t.stopPropagation(),this.#p(t.target,Ze(t.detail))}),new MutationObserver(()=>{this.#d!==null&&!this.#t.contains(this.#d)&&this.#p(null,0)}).observe(this.#t,{childList:!0,subtree:!0})}static{this.styles=_`
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
    @media (min-width: ${Y(Xe)}px) {
      :host { margin-left: var(${Y(Gt)}, auto); }
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
  `}static{this.properties={modules:{state:!0},rows:{state:!0},main:{state:!0},loading:{state:!0}}}#t;#l;#r;#s;#d;#i;connectedCallback(){super.connectedCallback(),this.#s=Cs("ui","modules",t=>{this.modules=Array.isArray(t?.modules)?t.modules:[]})}disconnectedCallback(){super.disconnectedCallback(),this.#s?.(),this.#s=null}willUpdate(t){this.#i=this.#u(),!(!t.has("modules")||this.modules===null)&&(this.#c(on(this.modules).filter(n=>un(n.module))),dn()||this.#f(an(this.modules.filter(n=>un(n.name)))))}updated(){let t=this.#i;this.#i=null,t!==null&&t.isConnected&&hn(document)!==t&&t.focus()}#u(){let t=this.renderRoot,n=t.activeElement;if(n===null||!t.querySelector(".rows")?.contains(n))return null;let i=hn(t);return i instanceof HTMLElement?i:null}#c(t){let n=new Map(this.rows.map(i=>[i.key,i]));this.rows=t.map(i=>{let r=`${i.module}.${i.name}`,o=Jt(i),l=n.get(r);if(l!==void 0&&l.url===o)return Object.assign(l,i);let a=document.createElement("div");a.className="seat",a.hidden=!0;let d={...i,key:r,url:o,host:a,report:null,clicked:!1};return a.addEventListener(Ms,c=>{c.stopPropagation(),this.#o(d,c.detail)}),pn(a,i,()=>this.rows.includes(d)),d})}#o(t,n){t.report=Ge(n),(t.report===null||!t.report.opens||t.report.open)&&(t.clicked=!1),this.#n(t),this.requestUpdate()}#n(t){let n=$t(t.report,t.clicked);t.host.hidden=!n,t.host.firstElementChild?.toggleAttribute("open",n)}#e(t){t.clicked=!t.clicked,this.#n(t),this.requestUpdate()}#p(t,n){this.#d=n>0?t:null,n>0?this.style.setProperty(Gt,tn(n)):this.style.removeProperty(Gt)}async#f(t){let n=t===null?"":`${t.module}.${t.name} ${Jt(t)}`;if(n===this.#l)return;this.#l=n;let i=this.#r+=1;if(this.#t.replaceChildren(),this.main=null,this.loading=t!==null,t===null)return;let r=await pn(this.#t,t,()=>i===this.#r);i===this.#r&&(this.loading=!1,r&&(this.main=n))}render(){if(this.modules===null)return h;let t=qt(this.rows),n=dn();return u`
      ${n?h:u`<section class="spot">${this.#t}${this.main===null&&!this.loading&&!Ye(this.rows)?u`<p class="quiet">Nothing needs you.</p>`:h}</section>`}
      <nav class=${n?"rows phone":"rows"} aria-label="Now" ?hidden=${t.length===0}>
        ${We(Je(this.rows),i=>i.key,i=>u`<div
          class=${$t(i.report,i.clicked)?"entry open":"entry"} data-row=${i.key}
          ?hidden=${i.report===null}>
          ${i.report===null?h:this.#m(i,i.report)}${i.host}</div>`)}
      </nav>`}#m(t,n){let i=Qe(n),r=$t(n,t.clicked),o=u`<span class="label">${t.title}</span><span class="fact">${n.parts.map(a=>a.heavy?u`<span class="heavy">${a.text}</span>`:a.text)}</span><span class="time">${i==="toggle"&&r?"Hide":n.time}</span>`;if(i==="toggle")return u`<button type="button" class="row" aria-expanded=${r?"true":"false"}
        @click=${()=>this.#e(t)}>${o}</button>`;let l=t.module==="ui"?"modules":"";return i==="held"||!fn(t.module).some(a=>a.name==="page")?u`<div class="row">${o}</div>`:u`<a class="row" href=${`/m/${t.module}/${l}`} @click=${a=>{a.button!==0||a.metaKey||a.ctrlKey||a.shiftKey||a.altKey||(a.preventDefault(),Rs(t.module,l))}}>${o}</a>`}};customElements.get(cn)||customElements.define(cn,Yt);var T=B(class extends N{constructor(s){if(super(s),s.type!==S.PROPERTY&&s.type!==S.ATTRIBUTE&&s.type!==S.BOOLEAN_ATTRIBUTE)throw Error("The `live` directive is not allowed on child or event bindings");if(!qe(s))throw Error("`live` bindings can only contain a single expression")}render(s){return s}update(s,[e]){if(e===b||e===h)return e;let t=s.element,n=s.name;if(s.type===S.PROPERTY){if(e===t[n])return b}else if(s.type===S.BOOLEAN_ATTRIBUTE){if(!!e===t.hasAttribute(n))return b}else if(s.type===S.ATTRIBUTE&&t.getAttribute(n)===e+"")return b;return xt(s),e}});var mn="important",Ns=" !"+mn,gn=B(class extends N{constructor(s){if(super(s),s.type!==S.ATTRIBUTE||s.name!=="style"||s.strings?.length>2)throw Error("The `styleMap` directive must be used in the `style` attribute and must be the only part in the attribute.")}render(s){return Object.keys(s).reduce((e,t)=>{let n=s[t];return n==null?e:e+`${t=t.includes("-")?t:t.replace(/(?:^(webkit|moz|ms|o)|)(?=[A-Z])/g,"-$&").toLowerCase()}:${n};`},"")}update(s,[e]){let{style:t}=s.element;if(this.ft===void 0)return this.ft=new Set(Object.keys(e)),this.render(e);for(let n of this.ft)e[n]==null&&(this.ft.delete(n),n.includes("-")?t.removeProperty(n):t[n]=null);for(let n in e){let i=e[n];if(i!=null){this.ft.add(n);let r=typeof i=="string"&&i.endsWith(Ns);n.includes("-")||r?t.setProperty(n,r?i.slice(0,-11):i,r?mn:""):t[n]=i}}return b}});import{api as Xn,components as ni,holdLeave as si,installed as ii,setAddress as ri,setTheme as oi}from"/m/ui/client.js";var C="moxi";var Ps="gateway.",Ls="applies now",Hs={"applies at next start":"Applies at the next start.","turns the daemon on or off now":"Turns its daemon on or off."},Xt="Locked for this run of the shell.",bn="an absolute path",zs=80,Ds=24,Us=5;function $n(s){if(typeof s=="string")try{return JSON.parse(s)}catch{return}}function v(s){return $n(s.json)}function K(s){return $n(s.default_json)}function k(s){return typeof s=="string"&&s!==""?s:null}function vn(s,e){return s==="ui"&&e.name.startsWith(Ps)}function F(s,e,t){return e.filter(n=>typeof n?.name=="string"&&n.needs_absent!==!0&&!k(n.elsewhere)&&(t||!vn(s,n)))}function wt(s,e){return F(s,e,!1)}function Zt(s,e){let t=Number.isInteger(s.count)?s.count:wt(s.module,s.keys).length;return e?t+F(s.module,s.keys,!0).filter(n=>vn(s.module,n)).length:t}function kt(s,e){return F(s.module,s.keys,e).filter(t=>t.changed===!0).length}function j(s){let e=[...new Set(s.filter(t=>t!==C&&t!=="ui"))].sort();return[C,...s.includes("ui")?["ui"]:[],...e]}function yn(s,e,t){let n=0;for(let i of new Set(s)){let r=e.get(i);if(r===void 0)return null;r.error===null&&(n+=kt(r,t))}return n}function nt(s){return s.length<=1?s.join(""):`${s.slice(0,-1).join(", ")} and ${s[s.length-1]}`}function Qt(s){if(typeof s=="string")return s;if(s!==null&&typeof s=="object"&&!Array.isArray(s)){let e=s.name;if(typeof e=="string"&&e!=="")return e}return JSON.stringify(s)??String(s)}function W(s,e){if(e==null||e==="")return"not set";if(typeof e=="boolean")return e?"on":"off";if(typeof e=="number")return String(e);if(typeof e=="string"){let t=(s.choices??[]).find(n=>n?.value===e);return t?t.words:e}if(Array.isArray(e))return e.length===0?"none":nt(e.map(Qt));if(typeof e=="object"){let t=Object.entries(e);if(s.switch_table===!0){let n=t.filter(([,i])=>i===!0).map(([i])=>i);return n.length===0?"none":nt(n)}return t.length===0?"none":t.map(([n,i])=>`${n} = ${Qt(i)}`).join(", ")}return String(e)}function L(s){return k(s.label)??s.name}function wn(s){if(typeof s!="string"||s===""||s===Ls)return null;let e=Hs[s];if(e)return e;let t=s.replaceAll("`","");return`${t[0].toUpperCase()}${t.slice(1)}${/[.!?]$/.test(t)?"":"."}`}function te(s){let e=s.terminal;return e&&typeof e.on=="string"&&typeof e.off=="string"?e:null}function Is(s){return s.map(e=>typeof e=="string"?e:Qt(e))}function xn(s){return s!==null&&typeof s=="object"&&!Array.isArray(s)}function js(s){let e=[];for(let t of s)for(let n of Object.keys(t))e.includes(n)||e.push(n);return e}function Vs(s,e){let t=Array.isArray(s.values)?s.values.filter(o=>typeof o=="string"):[],n=new Map((s.choices??[]).map(o=>[o.value,o.words])),i=t.map(o=>({value:o,words:n.get(o)??o})),r=typeof e=="string"?e:"";return{kind:s.pick===!0||t.length>=Us?"pick":"choices",options:i,selected:r}}function Bs(s,e){let t=Array.isArray(e)?e:[],n=K(s),i=t.length>0?t:Array.isArray(n)?n:[];if(i.length>0&&i.every(xn))return{kind:"rows",columns:js(i),rows:t.filter(xn).map(o=>({...o}))};let r=Is(t);return r.every(o=>o.length<=Ds&&!/\s/.test(o))?{kind:"names",items:r}:{kind:"lines",items:r,first:r[0]??""}}function qs(s){let e=v(s);if(te(s))return{kind:"word",text:W(s,e)};if(s.switch_table===!0)return{kind:"switches",toggles:(Array.isArray(s.entries)?s.entries:[]).filter(n=>typeof n?.key=="string").map(n=>({key:n.key,label:n.name||n.key,on:n.value===!0,line:wn(n.mark),locked:n.locked===!0}))};switch(s.type){case"bool":return{kind:"switch",on:e===!0};case"enum":return Vs(s,e);case"int":case"float":return{kind:"number",text:typeof e=="number"||typeof e=="string"?String(e):"",unit:k(s.unit),whole:s.type==="int"};case"list":return Bs(s,e);case"table":return{kind:"toml",text:typeof s.value=="string"?s.value:""};default:{let t=typeof e=="string"?e:"";return t.length>zs||/[\r\n]/.test(t)?{kind:"long",text:t,first:t.split(/\r?\n/)[0]}:{kind:"text",text:t,path:s.type==="path"}}}}function ee(s){let e=[],t=k(s.label)?k(s.line):k(s.line)??k(s.doc);t&&e.push(t);let n=wn(s.mark);return n&&e.push(n),s.source==="env"&&k(s.env)&&e.push(`Set by ${s.env} in the shell's environment, which wins over the file.`),s.switch_table===!0&&s.declarations==="unavailable"&&k(s.reason)&&e.push(s.reason),s.locked===!0&&s.switch_table!==!0&&e.push(Xt),e}function Ks(s){return s.changed!==!0?null:{words:W(s,K(s)),reset:s.source==="file"&&s.locked!==!0&&te(s)===null}}function Fs(s){let e=te(s);return e?v(s)===!0?{text:`Turned off at the terminal: ${e.off}`,command:e.off}:{text:`Turned on at the terminal: ${e.on}`,command:e.on}:null}function kn(s){return{name:s.name,label:L(s),labelled:k(s.label)!==null,control:qs(s),lines:ee(s),terminal:Fs(s),defaults:Ks(s),locked:s.locked===!0&&s.switch_table!==!0}}function Sn(s){if(!s.some(t=>t.common===!0))return{first:[...s],more:null};let e=s.filter(t=>t.common!==!0);return{first:s.filter(t=>t.common===!0),more:e.length>0?e:null}}function An(s){let e=[];for(let t of s){let n=k(t.group),i=e.find(r=>r.group===n);i?i.keys.push(t):e.push({group:n,keys:[t]})}return e}function _n(s){let e=[],t=[];for(let r of s){let o=k(r.group);o?e.includes(o)||e.push(o):t.push(L(r))}let n=[...e,...t].map(r=>r.toLowerCase()).join(", "),i=s.filter(r=>r.changed===!0).length;return{text:`${s.length} ${s.length===1?"setting":"settings"}: ${n}`,changed:i>0?`${i} changed`:null}}function En(s,e,t){return j(s).map(n=>{let i=e.get(n),r=i!==void 0&&i.error!==null,o=i===void 0||r?0:kt(i,t);return{module:n,changed:o>0?o:null,unreadable:r}})}function Rn(s){return s===void 0?"reading":s.error!==null?"unreadable":wt(s.module,s.keys).length===0?"empty":"rows"}var Tn="Nothing here can be changed on this page.",Cn=400,ne="Reading…";function Mn(s,e){if(s===0)return"No answer from the shell, so the settings could not be read.";if(s===403)return"This browser is signed out.";let t=e!==null&&typeof e=="object"?e:{},n=t.refused??t.conflict??t.error;return typeof n=="string"&&n!==""?n:`The shell answered ${s} for the settings.`}function On(s){return`${s.module}'s settings can't be read: ${s.error??""}`}var se=4,Pn="names, keys, descriptions and values",Ln="Search reads every installed module's settings. Pick a module at the left to see all of its settings.",st=(s,e)=>`${s} ${e}${s===1?"":"s"}`,Ws=0,Gs=1,Js=2;function ie(s){return typeof s=="string"?s.toLowerCase():""}function Nn(s,e){return ie(L(s)).includes(e)?Ws:ie(s.name).includes(e)?Gs:[...ee(s),W(s,v(s))].some(n=>ie(n).includes(e))?Js:null}function Hn(s,e){let t=e.trim().toLowerCase();if(t==="")return[{text:s,hit:!1}];let n=[],i=s.toLowerCase(),r=0;for(let o=i.indexOf(t);o>=0;o=i.indexOf(t,r))o>r&&n.push({text:s.slice(r,o),hit:!1}),n.push({text:s.slice(o,o+t.length),hit:!0}),r=o+t.length;return r<s.length&&n.push({text:s.slice(r),hit:!1}),n}function zn(s,e){let t=[...new Set(s)].filter(n=>!e.has(n)).length;return t===0?null:`still reading ${st(t,"module")}`}function Dn(s,e,t,n,i=new Set){let r=s.trim().toLowerCase(),o=[],l=[],a=0;for(let c of j(e)){let p=t.get(c);if(!p||p.error!==null)continue;for(let g of p.keys){let x=typeof g.elsewhere=="string"?g.elsewhere:"";x!==""&&Nn(g,r)!==null&&!l.includes(x)&&l.push(x)}let f=F(c,p.keys,n).map((g,x)=>({key:g,i:x,rank:Nn(g,r)})).filter(g=>g.rank!==null).sort((g,x)=>g.rank-x.rank||g.i-x.i).map(g=>g.key);if(f.length===0)continue;a+=f.length;let m=i.has(c)||f.length<=se;o.push({module:c,tally:`${f.length} of ${Zt(p,n)}`,keys:m?f:f.slice(0,se),more:m?null:`${f.length-se} more in ${c} · Show them`})}let d=zn(e,t);return{count:`${st(a,"setting")} in ${st(o.length,"module")}`,groups:o,elsewhere:l,none:a===0&&d===null?`No setting matches “${s.trim()}”`:null,reading:d}}function Un(s,e,t,n){let i=[],r=0;for(let o of j(s)){let l=e.get(o);if(!l||l.error!==null)continue;let a=F(o,l.keys,t).filter(d=>d.changed===!0);a.length!==0&&(r+=a.length,i.push({module:o,tally:`${kt(l,t)} of ${Zt(l,t)}`,keys:a,more:null}))}return{count:`${st(r,"setting")} changed from the default, in ${st(i.length,"module")}`,back:`Back to ${n}`,groups:i,reading:zn(s,e)}}var In="changed";function oe(s,e){let t=s.replace(/^\/+|\/+$/g,"").split("/").filter(Boolean),n={view:"module",module:C,more:!1,pair:!1,missing:null};if(t[0]!=="settings"||t.length===1)return n;let i=t[1],r=t[2]??null;return e.includes(i)?{...n,module:i,more:r==="more",pair:r==="pair"&&i==="ui"}:i===In&&t.length===2?{...n,view:"changed"}:{...n,missing:`${i} has no settings here.`}}function ae(s){return s.view==="changed"?`settings/${In}`:s.module===C&&!s.more?"settings":s.pair&&s.module==="ui"?`settings/${"ui"}/pair`:`settings/${s.module}${s.more?"/more":""}`}function jn(s){let e=s===null?NaN:Date.parse(s);return Number.isNaN(e)?"":new Date(e).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}function M(s,e){if(s===e)return!0;if(Array.isArray(s)||Array.isArray(e))return Array.isArray(s)&&Array.isArray(e)&&s.length===e.length&&s.every((t,n)=>M(t,e[n]));if(s!==null&&e!==null&&typeof s=="object"&&typeof e=="object"){let t=s,n=e,i=Object.keys(t);return i.length===Object.keys(n).length&&i.every(r=>r in n&&M(t[r],n[r]))}return!1}function de(s){try{return JSON.parse(s)}catch{return}}function _t(s){let e=s.trim(),t=Number(e);return e!==""&&Number.isFinite(t)?t:s}function G(s,e){let t=0;for(let n of s){if(n.name===e)return{key:n,entry:null,value:v(n),position:t};if(n.switch_table===!0&&e.startsWith(`${n.name}.`)){let i=Array.isArray(n.entries)?n.entries:[],r=i.findIndex(o=>o?.key===e);if(r>=0)return{key:n,entry:i[r].name,value:i[r].value===!0,position:t+r/1e3}}t+=1}return null}function it(s,e){let t=G(s,e);return t?t.entry===null?L(t.key):`${L(t.key)} ${t.entry}`:e}function ue(s,e,t){let n=G(s,e);return!n||n.entry!==null?typeof t=="boolean"?t?"on":"off":String(t):W(n.key,t)}function St(s){return typeof s=="string"?s:JSON.stringify(s)}function Bn(s,e){let t=s.hurts;if(!t||e.name!==s.name)return[];let n=L(s),i=v(s),r={name:s.name,confirm:t.confirm,says:t.says};if(s.type==="bool"){let o=t.when==="on";if((t.when==="on"||t.when==="off")&&e.value===o&&i!==o){let l=o?"Turn on":"Turn off";return[{...r,act:l,item:null,ask:`${l} ${n}?`}]}return[]}if(s.type==="list"){let o=Array.isArray(e.value)?e.value:[],l=Array.isArray(i)?i:[];if(t.when==="add")return o.filter(a=>!l.some(d=>M(d,a))).map(a=>({...r,act:"Add",item:St(a),ask:`Add ${St(a)} to ${n}?`}));if(t.when==="remove")return l.filter(a=>!o.some(d=>M(d,a))).map(a=>({...r,act:"Remove",item:St(a),ask:`Remove ${St(a)} from ${n}?`}))}return[]}function qn(s,e){let t=s.hurts;return!t||t.stays!==!0||s.type!=="bool"?!1:t.when==="on"&&e===!0||t.when==="off"&&e===!1}var Ys="Nothing else in this save can hurt.";function ce(s,e){let t=e.flatMap(i=>{let r=G(s,i.name);return r&&r.entry===null?Bn(r.key,i):[]});if(t.length===0)return null;let n=[...new Set(t.map(i=>i.act))];return{asks:t.map(i=>({ask:i.ask,confirm:i.confirm})),rest:Ys,button:n.length===1?`${n[0]} and save`:"Save anyway",back:"Go back"}}var At=class{constructor(){this.held=new Map;this.notes=new Map}module(e){let t=this.held.get(e);return t||this.held.set(e,t=new Map),t}put(e,t){this.module(e).set(t.name,t),this.notes.get(e)?.delete(t.name)}edit(e,t,n){M(n,v(t))?this.undo(e,t.name):this.put(e,{name:t.name,value:n,reset:!1,was:t.json??"null"})}editEntry(e,t,n,i){let r=(Array.isArray(t.entries)?t.entries:[]).find(a=>a?.name===n),o=r?.value===!0,l=r?.key??`${t.name}.${n}`;i===o?this.undo(e,l):this.put(e,{name:l,value:i,reset:!1,was:JSON.stringify(o)})}reset(e,t){this.put(e,{name:t.name,value:K(t),reset:!0,was:t.json??"null"})}undo(e,t){this.held.get(e)?.delete(t),this.notes.get(e)?.delete(t)}discard(e){this.held.delete(e),this.notes.delete(e)}changeOf(e,t){return this.held.get(e)?.get(t)}noteOf(e,t){return this.notes.get(e)?.get(t)}changes(e,t=[]){let n=[...this.held.get(e)?.values()??[]],i=r=>G(t,r)?.position??Number.MAX_SAFE_INTEGER;return n.map((r,o)=>({change:r,i:o})).sort((r,o)=>i(r.change.name)-i(o.change.name)||r.i-o.i).map(({change:r})=>r)}count(e){return this.held.get(e)?.size??0}modules(){return[...this.held].filter(([,e])=>e.size>0).map(([e])=>e)}total(){return this.modules().reduce((e,t)=>e+this.count(t),0)}body(e,t){let n=this.changes(e,t);return{module:e,keys:n.map(i=>i.name),values:n.map(i=>i.reset?"":JSON.stringify(i.value??null)),resets:n.map(i=>i.reset),was:n.map(i=>i.was)}}refresh(e,t){for(let n of[...this.held.get(e)?.values()??[]]){let i=G(t,n.name);if(i&&!M(i.value,de(n.was))){if(!n.reset&&M(i.value,n.value)){this.undo(e,n.name);continue}n.was=JSON.stringify(i.value??null),this.note(e,n.name,{text:Fn(ue(t,n.name,i.value)),copy:null,still:!1})}}}note(e,t,n){let i=this.notes.get(e);i||this.notes.set(e,i=new Map),i.set(t,n)}answer(e,t,n){for(let i of n){let r=this.changeOf(e,i.key),o=ti(e,t,i);Qs(i)?(this.held.get(e)?.delete(i.key),this.notes.get(e)?.delete(i.key)):r&&i.outcome==="moved"&&typeof i.problem=="string"&&(r.was=i.problem),o&&this.note(e,i.key,o)}}heldLine(e,t,n){let i=this.changeOf(e,n);if(!i)return null;let r=ue(t,n,de(i.was));if(this.noteOf(e,n)?.still)return{text:`still ${r}`,hurts:!1};let o=G(t,n),l=o&&o.entry===null?Bn(o.key,i):[];return l.length>0?{text:`not saved · ${l[0].says}`,hurts:!0}:{text:`not saved · was ${r}`,hurts:!1}}};function Et(s,e,t){if(t.switch_table===!0){let r=Array.isArray(t.entries)?t.entries:[];return r.some(o=>s.changeOf(e,o?.key)!==void 0)?{...t,entries:r.map(o=>{let l=s.changeOf(e,o?.key);return l===void 0?o:{...o,value:l.value===!0}})}:t}let n=s.changeOf(e,t.name);if(n===void 0)return t;let i={...t,json:JSON.stringify(n.value??null)};return t.type==="table"&&(n.reset?i.value=t.default??"":typeof n.value=="string"&&(i.value=n.value)),i}function Rt(s){return s.outcome==="saved"||s.outcome==="unchanged"||s.outcome==="failed"&&s.finished!==!1&&typeof s.warning=="string"}function Qs(s){return Rt(s)||s.outcome==="failed"&&s.finished===!1}function Kn(s){return s.every(e=>e.outcome==="saved"||e.outcome==="unchanged")}function le(s){let e=s.trim().replace(/[.\s]+$/,"");return e===""?"":`${e[0].toUpperCase()}${e.slice(1)}.`}function Fn(s){return`Not saved: it changed since this page read it, to ${s}. Save again to write yours.`}var Xs="The settings file was busy. Save again.",Zs="May not be saved: moxi config did not finish. The row shows what the file holds now.";function ti(s,e,t){switch(t.outcome){case"saved":case"unchanged":return null;case"refused":return{text:["Not saved.",le(t.problem??""),le(t.fix??"")].filter(Boolean).join(" "),copy:null,still:!0};case"moved":{let n=typeof t.problem=="string"?de(t.problem):void 0;return{text:Fn(ue(e,t.key,n)),copy:null,still:!1}}case"busy":return{text:Xs,copy:null,still:!1};default:if(t.finished===!1)return{text:Zs,copy:null,still:!1};if(typeof t.warning=="string"){let n=`moxi install ${s}`;return{text:`${it(e,t.key)} is saved, and applying its daemons failed. Run ${n}.`,copy:n,still:!1}}return{text:["Not saved.",le(t.problem??"")].filter(Boolean).join(" "),copy:null,still:!0}}}function ei(s){let e=(s??"applies now").replaceAll("`","");if(e==="applies at next start")return["applies at the next start","apply at the next start"];if(e==="turns the daemon on or off now")return["turns its daemon on or off now","turn their daemons on or off now"];let t=e.replace(/^applies\b/,"apply").replace(/^turns\b/,"turn").replace(/^starts\b/,"start");return[e,t]}function Wn(s){return jn(new Date(s*1e3).toISOString())}function Gn(s,e,t){let n=l=>it(s,l.key),i=e.filter(l=>!Rt(l));if(i.length===0){let l=new Map;for(let d of e){let c=d.mark??"applies now";l.set(c,[...l.get(c)??[],n(d)])}let a=[...l].map(([d,c])=>{let[p,f]=ei(d);return`${nt(c)} ${c.length===1?p:f}`});return[`Saved ${t}`,...a].join(" · ")}let r=i.every(l=>l.outcome==="refused"),o=i.length===1?`${n(i[0])} was ${r?"refused":"not saved"}: it says why beside the field`:`${nt(i.map(n))} were ${r?"refused":"not saved"}: each says why beside its field`;return`${e.length-i.length} of ${e.length} saved at ${t} · ${o}`}function Jn(s){return s===0?"No answer from the shell; nothing may have been saved.":s===403?"This browser is signed out.":`The shell answered ${s}.`}function Yn(s,e,t){return t?`Saving ${e.length}…`:e.length===0?"Nothing to save":`${e.length} not saved · ${e.map(n=>it(s,n.name)).join(", ")}`}var Vn=(s,e)=>`${s} ${e}${s===1?"":"s"}`;function Qn(s,e,t){let n=s.modules();if(n.length===0)return null;let i=s.total();return n.length===1&&n[0]===e?{lines:[`Leave Settings with ${Vn(i,"change")} in ${e} not saved? ${t(e).join(", ")}.`],buttons:["Stay","Leave without saving","Save and leave"]}:{lines:[`Leave Settings with ${Vn(i,"change")} not saved?`,...n.map(r=>`${s.count(r)} in ${r}: ${t(r).join(", ")}.`),"Save writes one module at a time."],buttons:["Leave without saving","Stay"]}}var Zn="moxi-ui-settings",ai=2,li=2e3,di=["theme.mode","theme.palette"],ui=["system","light","dark"];function ts(){return{listed:null,sections:new Map,error:null,address:null,pending:new At,said:new Map,query:"",showAll:new Set}}var rt=null,ci=_`
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
  .foot.after { border-top: 0; margin-top: 0; }
  .set.un .lb { font-weight: 600; }
  .set.un .lb .key { font-weight: 400; }
  .set.un .fld { border-color: var(--moxi-border-strong); }
  .set.dg .lb, .l2 .w { color: var(--moxi-warn); }
  .set.dg .lb .key { color: var(--moxi-text-dim); }
  .set.dg .sw[aria-checked="true"] { background: var(--moxi-warn); border-color: var(--moxi-warn); }
  .l2 b { font-weight: 600; color: var(--moxi-text); }
  .l2 .w b { color: var(--moxi-warn); }
  .entry.un > span:first-child { font-weight: 600; }
  .mods a.u { font-weight: 600; color: var(--moxi-text); }
  .tok { display: inline-flex; align-items: center; gap: 4px; }
  .tok .link { color: var(--moxi-text-dim); font-size: 12px; line-height: 1; }
  .fld.add { width: 120px; font-size: 13px; padding: 3px 7px; text-align: left; }
  textarea.fld { display: block; width: 100%; min-height: 5rem; margin: 3px 0 2px; font-family: var(--moxi-font-mono);
                 font-size: 13px; line-height: 18px; resize: vertical; }
  .items { display: grid; gap: 4px; margin: 3px 0 2px; }
  .items .item { display: flex; align-items: center; gap: 8px; }
  .items .fld { flex: 1 1 auto; min-width: 0; font-size: 13px; }
  .save { position: sticky; bottom: 0; z-index: 1; display: flex; flex-wrap: wrap; justify-content: space-between;
          align-items: center; gap: 8px 18px; border-top: 1px solid var(--moxi-border); margin-top: 6px; padding: 12px 0;
          font-size: 14px; line-height: 20px; color: var(--moxi-text-dim); background: var(--moxi-bg); }
  .save.conf { align-items: flex-start; }
  .save .msg { flex: 1 1 240px; min-width: 0; overflow-wrap: anywhere; }
  .save .msg b { font-weight: 600; color: var(--moxi-text); }
  .save .msg b.w { color: var(--moxi-warn); }
  .save .msg span { display: block; }
  .save .btns { display: flex; flex-wrap: wrap; align-items: center; gap: 18px; flex: none; }
  .btn { font-weight: 600; font-size: 14px; line-height: 1; border: 1px solid var(--moxi-border);
         border-radius: var(--moxi-radius); padding: 8px 16px; background: none; color: var(--moxi-text-dim);
         cursor: pointer; }
  .btn.acc { background: var(--moxi-accent); border-color: var(--moxi-accent); color: var(--moxi-on-accent); }
  .btn.wn { color: var(--moxi-warn); border-color: var(--moxi-warn); }
  .btn.stay { color: var(--moxi-text); }
  .missing { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); padding-bottom: 28px; }
  .failed { display: grid; gap: var(--moxi-gap); justify-items: start; padding: 12px 0; }
  .failed button { padding: 2px 10px; border: 1px solid var(--moxi-border-strong); border-radius: var(--moxi-radius);
                   background: none; cursor: pointer; }
  .find { display: flex; align-items: center; gap: 12px; border: 1px solid var(--moxi-border);
          border-radius: var(--moxi-radius-lg); padding: 10px 14px; margin-bottom: 16px; font-size: 15px; line-height: 22px; }
  .find:focus-within { border-color: var(--moxi-border-strong); }
  .find input { flex: 1 1 auto; min-width: 0; border: 0; padding: 0; background: none; font: inherit; color: var(--moxi-text); }
  .find input::placeholder { color: var(--moxi-text-dim); }
  .find input:focus-visible { outline: none; }
  .find .link { font-size: 14px; }
  .resh { display: flex; flex-wrap: wrap; justify-content: space-between; column-gap: 18px; margin-top: -6px;
          padding: 0 2px 10px; font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); }
  .resh a { color: var(--moxi-text); text-decoration: none; }
  .cols.res .mods { position: static; padding-top: 12px; line-height: 20px; color: var(--moxi-text-dim); }
  .cols.res .mods a { display: block; line-height: 20px; color: var(--moxi-text-dim); }
  .cols.res .mods small { display: block; font-size: 12px; line-height: 16px; }
  .cols.res .pane { padding-bottom: 5px; }
  .cols.res .ln { border-top: 1px solid var(--moxi-border); }
  .cols.res .save { margin-bottom: 6px; }
  .morein { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); padding: 4px 0 8px; }
  .help { max-width: 440px; padding-top: 10px; font-size: 13px; line-height: 18px; }
  .aside { font-size: 13px; line-height: 18px; color: var(--moxi-text-dim); border-top: 1px solid var(--moxi-border);
           padding: 10px 2px 0; }
  .hit { color: var(--moxi-text); text-decoration: underline; text-decoration-color: var(--moxi-text-dim);
         text-underline-offset: 3px; }
  @media (max-width: 720px) {
    .cols { grid-template-columns: minmax(0, 1fr); }
    .cols.res .mods { display: flex; column-gap: 10px; align-items: baseline; border-bottom: 0; padding: 10px 0 0; }
    .cols.res .ln + .pane { border-top: 0; }
    .mods { position: static; display: flex; flex-wrap: wrap; column-gap: 14px; padding: 8px 0;
            border-bottom: 1px solid var(--moxi-border); }
    .mods a { line-height: 24px; }
    .mods .under { margin: 0; align-self: center; }
    .mods .total, .mods .error { flex-basis: 100%; margin-top: 4px; padding-top: 4px; }
  }
`;function pi(s){let e=s.replace(/^\/+|\/+$/g,"").split("/").filter(Boolean);return e[0]==="settings"&&e[1]?e[1]:C}var pe=class extends ${constructor(){super(...arguments);this.route="settings";this.#t=ts();this.#l=null;this.#r=!1;this.#s=null;this.#d=null;this.#i=null;this.#u="";this.#c=0;this.#o=null;this.#n=null;this.#e=null;this.#p=null;this.#f=new Set;this.#m=null;this.#y=new Map;this.#h=null;this.#C=()=>{document.visibilityState==="visible"&&this.#t.listed!==null&&this.#O()}}static{this.styles=ci}static{this.properties={route:{type:String}}}#t;#l;#r;#s;#d;#i;#u;#c;#o;#n;#e;#p;#f;#m;#y;#h;#C;connectedCallback(){super.connectedCallback();let t=(this.getAttribute("route")??this.route??"settings").replace(/^\/+|\/+$/g,"");this.route=t,this.#u=location.pathname,document.addEventListener("visibilitychange",this.#C);let n=rt!==null&&rt.path===location.pathname?rt.held:null;if(rt=null,n!==null&&n.listed!==null){let i=n.address!==null&&ae(n.address)===t?n.address:null;this.#t={...n,address:i??oe(t,n.listed)},this.#b(),this.#O()}else this.#M()}disconnectedCallback(){super.disconnectedCallback(),document.removeEventListener("visibilitychange",this.#C);for(let n of[this.#s,this.#i])n!==null&&clearTimeout(n);let t=this.#h;this.#h=null,t?.(),rt=location.pathname===this.#u?{path:this.#u,held:this.#t}:null}async#M(){if(this.#l=null,this.#Y(),!await this.#x(pi(this.route)))return;let n=this.#t.listed??[];this.#t.address=oe(this.route,n),this.requestUpdate(),!(!this.#t.sections.has(this.#t.address.module)&&!await this.#x(this.#t.address.module))&&await this.#N(j(n).filter(i=>!this.#t.sections.has(i)))}async#O(){let t=this.#t.address?.module??C;if(!await this.#x(t))return;let n=this.#t.listed??[];await this.#N(j(n).filter(i=>i!==t))}async#N(t){let n=[...t],i=async()=>{for(let r=n.shift();r!==void 0;r=n.shift())if(!await this.#x(r))return};await Promise.all(Array.from({length:ai},i))}async#x(t){this.#c+=1;let n=await Xn("ui",`api/settings?module=${encodeURIComponent(t)}`);this.#c-=1;let i=n.data;if(n.status!==200||!Array.isArray(i?.sections)||!Array.isArray(i?.listed))return this.#t.listed===null&&(this.#l=Mn(n.status,i)),this.requestUpdate(),!1;let r=i.listed.filter(o=>typeof o=="string");this.#t.listed=r,this.#t.error=typeof i.error=="string"&&i.error!==""?i.error:null;for(let o of i.sections)typeof o?.module=="string"&&(this.#t.sections.set(o.module,o),o.error===null&&Array.isArray(o.keys)&&this.#t.pending.refresh(o.module,o.keys));for(let o of[...this.#t.sections.keys()])r.includes(o)||this.#t.sections.delete(o);return this.#b(),this.requestUpdate(),!0}#Y(){this.#r=!1,this.#s!==null&&clearTimeout(this.#s),this.#s=setTimeout(()=>{this.#s=null,this.#r=!0,this.requestUpdate()},Cn)}#Q(){this.#t=ts(),this.#b(),this.requestUpdate(),this.#M()}#w(t){this.#t.address=t,this.#t.query="",this.#t.showAll.clear(),this.#n=null;try{ri("ui",ae(t)),this.#u=location.pathname}catch{}this.requestUpdate()}#k(t,n){t.button!==0||t.metaKey||t.ctrlKey||t.shiftKey||t.altKey||(t.preventDefault(),this.#w({view:"module",module:n,more:!1,pair:!1,missing:null}))}#X(t){t.button!==0||t.metaKey||t.ctrlKey||t.shiftKey||t.altKey||(t.preventDefault(),this.#w({view:"changed",module:this.#t.address?.module??C,more:!1,pair:!1,missing:null}))}#Z(t){this.#t.query=t,this.#n=null,this.requestUpdate()}#P(){this.#t.query="",this.#t.showAll.clear(),this.requestUpdate(),this.updateComplete.then(()=>this.renderRoot.querySelector("[data-part=find] input")?.focus())}#tt(){let t=this.#t.address;t!==null&&this.#w({...t,more:!t.more,missing:null})}#S(t){navigator.clipboard?.writeText(t).catch(()=>{}),this.#d=t,this.#i!==null&&clearTimeout(this.#i),this.#i=setTimeout(()=>{this.#i=null,this.#d=null,this.requestUpdate()},li),this.requestUpdate()}#b(){let t=this.#t.pending.total()>0;if(t&&this.#h===null)this.#h=si(n=>this.#at(n));else if(!t&&this.#h!==null){let n=this.#h;this.#h=null,this.#e=null,n()}}#g(t){this.#t.said.delete(t),this.#n===t&&(this.#n=null),this.#b(),this.requestUpdate()}#a(t,n,i){this.#t.pending.edit(t,n,i),this.#g(t)}#et(t,n,i){i===(typeof n.value=="string"?n.value:"")?this.#t.pending.undo(t,n.name):this.#t.pending.edit(t,n,i),this.#g(t)}#$(t,n){let i=v(Et(this.#t.pending,t,n));return Array.isArray(i)?[...i]:[]}#L(t,n){return t.items==="int"||t.items==="float"?_t(n):n}#H(t,n,i){if(this.#m=null,i.trim()===""){this.requestUpdate();return}this.#a(t,n,[...this.#$(t,n),this.#L(n,i.trim())])}#nt(t){this.#f.has(t)?this.#f.delete(t):this.#f.add(t),this.requestUpdate()}async#A(t,n=!1){let i=this.#t.sections.get(t);if(i===void 0||this.#o!==null)return null;let r=i.keys,o=this.#t.pending.changes(t,r);if(o.length===0)return null;if(!n&&ce(r,o)!==null)return this.#n=t,this.requestUpdate(),null;this.#n=null,this.#o=t,this.#t.said.delete(t),this.requestUpdate();let l=await Xn("ui","api/settings/save",this.#t.pending.body(t,r)),a=l.data,d=null;if(l.status===200&&Array.isArray(a?.results)){d=a.results,this.#t.pending.answer(t,r,d);let c=typeof a.saved_at=="number"?a.saved_at:Date.now()/1e3;this.#t.said.set(t,Gn(r,d,Wn(c)))}else this.#t.said.set(t,Jn(l.status));return this.#o=null,this.requestUpdate(),await this.#x(t),t==="ui"&&d?.some(c=>Rt(c)&&di.includes(c.key))&&this.#st(),this.#b(),this.requestUpdate(),d}#st(){let t=this.#t.sections.get("ui")?.keys??[],n=r=>{let o=t.find(a=>a.name===r),l=o===void 0?void 0:v(o)??K(o);return typeof l=="string"?l:void 0},i=n("theme.mode");if(!(i===void 0||!ui.includes(i)))try{oi(i,n("theme.palette"))}catch{}}async#it(t){let n=await this.#A(t,!0);this.#U(n)}#rt(){this.#n=null,this.#p=null,this.requestUpdate()}#ot(t){this.#t.pending.discard(t),this.#g(t)}#at(t){this.#e=t,this.#n=null,this.requestUpdate(),this.updateComplete.then(()=>{this.renderRoot.querySelector("[data-part=save]")?.scrollIntoView({block:"nearest"})})}#z(){this.#e=null,this.requestUpdate()}#D(){let t=this.#e;this.#e=null;for(let n of this.#t.pending.modules())this.#t.pending.discard(n);this.#t.said.clear(),this.#h=null,t?.()}async#lt(t){this.#p=this.#e,this.#e=null;let n=await this.#A(t);this.#U(n)}#U(t){if(this.#n!==null)return;let n=this.#p;this.#p=null,n!==null&&t!==null&&Kn(t)&&this.#t.pending.total()===0&&n(),this.requestUpdate()}updated(t){super.updated(t),this.#t.address!==null&&this.#s!==null&&this.#t.sections.has(this.#t.address.module)&&(clearTimeout(this.#s),this.#s=null)}render(){if(this.#l!==null)return u`<div class="failed" data-part="failed">
        <p data-line="failed">${this.#l}</p>
        <button type="button" @click=${()=>this.#Q()}>Try again</button></div>`;let t=this.#t.address,n=this.#t.query.trim(),i=this.#t.listed,r;return i!==null&&n!==""?r=this.#ut(n,i):i!==null&&t?.view==="changed"?r=this.#ct(i,t.module):r=u`<div class="cols">
        ${this.#B(!0)}
        <section class="pane" data-part="pane" data-module=${t?.module??h}>${this.#ft()}</section>
      </div>`,u`${this.#dt()}${r}`}#dt(){let t=this.#t.query;return u`<div class="find" data-part="find">
      <input type="text" aria-label="Find a setting" placeholder="Find a setting" autocomplete="off" spellcheck="false"
        .value=${T(t)} @input=${n=>this.#Z(n.target.value)}
        @keydown=${n=>{n.key==="Escape"&&t!==""&&(n.preventDefault(),this.#P())}}>
      ${t!==""?u`<button type="button" class="link dim" data-act="clear" @click=${()=>this.#P()}>Clear</button>`:h}
    </div>`}#I(t){return t!==null?u` · <span data-line="partial">${t}</span>`:h}#ut(t,n){let{sections:i,showAll:r}=this.#t,o=Dn(t,n,i,!1,r),l=a=>u`<div class="resh" data-part="found-head">
      <span data-line="count">${a}</span><span data-line="reads">${Pn}</span></div>`;return o.none!==null?u`${l(o.none)}<div class="cols">
        ${this.#B(!1)}
        <section class="pane" data-part="help"><p class="note help" data-line="help">${Ln}</p>
          ${this.#e!==null?this.#v(null):h}</section>
      </div>`:u`${l(u`${o.count}${this.#I(o.reading)}`)}
      ${this.#j(o.groups,t,"found")}
      ${o.elsewhere.map(a=>u`<p class="aside" data-line="elsewhere">${a}</p>`)}
      ${this.#V(o.groups)}`}#ct(t,n){let i=Un(t,this.#t.sections,!1,n);return u`<div class="resh" data-part="found-head">
        <span data-line="count">${i.count}${this.#I(i.reading)}</span>
        <a href=${`/m/ui/settings/${n}`} class="back" data-act="back"
          @click=${r=>this.#k(r,n)}>${i.back}</a></div>
      ${this.#j(i.groups,"","changed")}
      ${this.#V(i.groups)}`}#j(t,n,i){return t.length===0?h:u`<div class="cols res" data-part=${i}>${t.map((r,o)=>{let l=this.#t.sections.get(r.module),a=l.readonly||this.#o===r.module,d=o>0?"ln":"";return u`<div class=${`mods ${d}`} data-group-of=${r.module}>
          <a href=${`/m/ui/settings/${r.module}`} data-open=${r.module}
            @click=${c=>this.#k(c,r.module)}>${r.module}</a>
          <small data-line="tally">${r.tally}</small></div>
        <section class=${`pane ${d}`} data-part="group" data-module=${r.module}>
          ${r.keys.map(c=>this.#q(l,c,a,n))}
          ${r.more!==null?u`<p class="morein" data-line="more-in">${r.more.replace(/ · Show them$/,"")} · <button
              type="button" class="link" data-act="show-all" @click=${()=>this.#pt(r.module)}>Show them</button></p>`:h}
          ${this.#ht(l)}
        </section>`})}</div>`}#pt(t){this.#t.showAll.add(t),this.requestUpdate()}#ht(t){let n=t.module;return this.#e!==null?h:this.#t.pending.count(n)>0||this.#o===n||this.#n===n||this.#t.said.has(n)?this.#J(t,!0):h}#V(t){if(this.#e===null)return h;let n=this.#t.pending.modules(),i=n.length===1&&t.some(r=>r.module===n[0])?n[0]:null;return this.#v(i)}#B(t){let{listed:n,sections:i,error:r,address:o,pending:l}=this.#t;if(n===null)return u`<nav class="mods" data-part="list" aria-label="Modules" data-reading></nav>`;let a=yn(n,i,!1);return u`<nav class="mods" data-part="list" aria-label="Modules" ?data-reading=${a===null}>
      ${En(n,i,!1).map(d=>{let c=t&&o?.view==="module"&&o.module===d.module,p=l.count(d.module);return u`<a href=${`/m/ui/settings/${d.module}`} data-list=${d.module} class=${p>0?"u":""}
            aria-current=${c?"page":h} @click=${f=>this.#k(f,d.module)}
          >${d.module}${d.changed!==null?u`<i title=${`${d.changed} changed from the default`}>${d.changed}</i>`:h}</a>
          ${p>0?u`<span class="under" data-line="unsaved">${p} not saved</span>`:h}
          ${d.unreadable?u`<span class="under" data-line="unreadable">can't be read</span>`:h}`})}
      ${a!==null&&a>0?u`<a class="total" href="/m/ui/settings/changed" data-line="changed"
          @click=${d=>this.#X(d)}>${a} changed</a>`:h}
      ${r!==null?u`<p class="error" data-line="error">${r}</p>`:h}
    </nav>`}#ft(){let t=this.#t.address;if(t===null)return this.#r?u`<p class="note" data-line="reading">${ne}</p>`:h;let n=this.#t.sections.get(t.module),i=t.missing!==null?u`<p class="missing" data-line="missing">${t.missing}</p>`:h,r=this.#e!==null?this.#v(t.module):h;switch(Rn(n)){case"reading":return u`${this.#r?u`<p class="note" data-line="reading">${ne}</p>`:h}${r}${i}`;case"unreadable":{let o=n;return u`<div class="unreadable" data-part="unreadable">
            <p data-line="unreadable">${On(o)}</p>
            ${o.fix?u`<p class="fix" data-line="fix">Fix: <code>${o.fix}</code>
              ${this.#R(o.fix)}</p>`:h}
          </div>${r}${this.#T(o,r!==h)}${i}`}case"empty":return u`<p class="note" data-line="empty">${Tn}</p>${r}${this.#T(n,r!==h)}${i}`;default:return u`${this.#mt(n,t)}${this.#J(n)}${this.#T(n,!0)}${i}`}}#mt(t,n){let i=wt(t.module,t.keys),{first:r,more:o}=Sn(i),l=t.readonly||this.#o===t.module;if(o===null)return this.#_(t,r,l);let a=_n(o);return u`${this.#_(t,r,l)}
      <div class="more" data-part="more" ?data-open=${n.more}>
        <span><b>More</b><span data-line="more">${a.text}${a.changed?` · ${a.changed}`:""}</span></span>
        <button type="button" class="link" aria-expanded=${String(n.more)} @click=${()=>this.#tt()}
          >${n.more?"Hide":"Show"}</button>
      </div>
      ${n.more?this.#_(t,o,l):h}`}#_(t,n,i){return An(n).map(r=>u`
      ${r.group?u`<div class="subh" data-group=${r.group}>${r.group}</div>`:h}
      ${r.keys.map(o=>this.#q(t,o,i))}`)}#q(t,n,i,r=""){let o=t.module,l=this.#t.pending,a=Et(l,o,n),d=kn(a),c=i||d.locked,p=l.heldLine(o,t.keys,d.name),f=l.noteOf(o,d.name),m=p?.hurts===!0||qn(n,v(a)),g=this.#d===d.name?u`<span class="copied" data-line="copied">Copied</span>`:h,x=u`<code class=${d.labelled?"key":"key own"} title="Copy the key" role="button" tabindex="0"
        data-line="key" @click=${()=>this.#S(d.name)}
        @keydown=${O=>{(O.key==="Enter"||O.key===" ")&&(O.preventDefault(),this.#S(d.name))}}
      >${this.#E(d.name,r)}</code>`,y=f!==void 0||d.lines.length>0||d.defaults!==null||p!==null,w=["set",p!==null?"un":"",m?"dg":""].filter(Boolean).join(" ");return u`<div class=${w} data-key=${d.name} ?data-locked=${c} ?data-held=${p!==null}>
      <div class="l1">
        <span class="lb">${d.labelled?u`<span data-line="label">${this.#E(d.label,r)}</span>${x}`:x}${g}</span>
        <span class="ctl">${this.#gt(o,n,d,c)}</span>
      </div>
      ${d.control.kind==="switches"?h:this.#G(o,n,d,c)}
      ${y?u`<div class="l2">
        <span class="said">${f!==void 0?this.#K(f.text,f.copy):d.lines.map((O,Ct)=>u`${Ct>0?" ":""}<span data-line="line">${this.#E(O,r)}</span>`)}</span>
        ${p!==null?this.#F(o,d.name,p.text,p.hurts):d.defaults!==null?u`<span class="nt" data-line="default">default ${d.defaults.words}${d.defaults.reset?u` · <button type="button" class="link" data-act="reset" ?disabled=${c}
              @click=${()=>{l.reset(o,n),this.#g(o)}}>Reset</button>`:h}</span>`:h}
      </div>`:h}
      ${d.terminal!==null?u`<div class="l2"><span class="said" data-line="terminal">${d.terminal.text}</span>
        ${this.#R(d.terminal.command)}</div>`:h}
      ${d.control.kind==="switches"?this.#G(o,n,d,c):h}
    </div>`}#E(t,n){return n===""?t:Hn(t,n).map(i=>i.hit?u`<span class="hit">${i.text}</span>`:i.text)}#K(t,n){let i=t.startsWith("Not saved.")?"Not saved.":null;return u`<span data-line="note">${i!==null?u`<b>${i}</b>${t.slice(i.length)}`:t}</span>${n?u` ${this.#R(n)}`:h}`}#F(t,n,i,r){let[o,...l]=i.split(" · "),a=o==="not saved"?u`<b>${o}</b>${l.length>0?` · ${l.join(" · ")}`:""}`:i;return u`<span class="nt" data-line="held"><span class=${r?"w":""}>${a}</span> · <button type="button"
      class="link" data-act="undo" ?disabled=${this.#o===t}
      @click=${()=>{this.#t.pending.undo(t,n),this.#g(t)}}>Undo</button></span>`}#R(t){return this.#d===t?u`<span class="copied" data-line="copied">Copied</span>`:u`<button type="button" class="link" data-act="copy" @click=${()=>this.#S(t)}>Copy</button>`}#gt(t,n,i,r){let o=i.control,l=i.name,a=`${t}/${l}`;switch(o.kind){case"switch":return u`<button type="button" class="sw" role="switch" aria-checked=${String(o.on)} aria-label=${i.label}
          data-control=${l} ?disabled=${r} @click=${()=>this.#a(t,n,!o.on)}></button>`;case"choices":return u`<span class="tri" role="radiogroup" aria-label=${i.label} data-control=${l}>
          ${o.options.map(d=>u`<button type="button" role="radio" data-value=${d.value}
            aria-checked=${String(d.value===o.selected)} ?disabled=${r}
            @click=${()=>this.#a(t,n,d.value)}>${d.words}</button>`)}</span>`;case"pick":return u`<select class="fld" aria-label=${i.label} data-control=${l} ?disabled=${r}
          .value=${T(o.selected)} @change=${d=>this.#a(t,n,d.target.value)}>${o.options.map(d=>u`<option
            value=${d.value} ?selected=${d.value===o.selected}>${d.words}</option>`)}</select>`;case"number":return u`<input class="fld num" type="text" inputmode=${o.whole?"numeric":"decimal"}
          aria-label=${i.label} data-control=${l} ?disabled=${r}
          .value=${T(this.#W(a,_t,v(Et(this.#t.pending,t,n)),o.text))}
          @input=${d=>{let c=d.target.value;this.#y.set(a,c),this.#a(t,n,_t(c))}}>
          ${o.unit?u`<span class="unit">${o.unit}</span>`:h}`;case"text":return u`<input class="fld txt" type="text" aria-label=${i.label} data-control=${l}
          placeholder=${o.path?bn:h} .value=${T(o.text)} ?disabled=${r}
          @input=${d=>this.#a(t,n,d.target.value)}>`;case"long":case"lines":case"toml":{let d=this.#f.has(a),c=u`<button type="button" class="link" data-act="edit" aria-expanded=${String(d)}
          ?disabled=${r&&!d} @click=${()=>this.#nt(a)}>${d?"Done":"Edit"}</button>`;return o.kind==="lines"?u`<span class="dim" data-control=${l}>${o.items.length}
            ${o.items.length===1?"line":"lines"} · ${c}</span>`:u`<span data-control=${l}>${c}</span>`}case"names":{let d=this.#$(t,n);return u`<span class="toks" data-control=${l}>${o.items.length===0?u`<span class="dim" data-line="none">none</span>`:o.items.map((c,p)=>u`<span class="tok" data-item=${c}>${c}<button type="button"
              class="link" aria-label=${`Remove ${c}`} data-act="remove" ?disabled=${r}
              @click=${()=>this.#a(t,n,d.filter((f,m)=>m!==p))}>×</button></span>`)}
          ${this.#m===a?u`<input class="fld add" type="text" aria-label=${`Add to ${i.label}`} data-act="item"
                @keydown=${c=>this.#bt(c,t,n)}
                @blur=${c=>this.#H(t,n,c.target.value)}>`:u`<button type="button" class="link dim" data-act="add" ?disabled=${r}
                @click=${()=>this.#xt(a)}>Add</button>`}</span>`}case"rows":return u`<button type="button" class="link dim" data-control=${l} data-act="add" ?disabled=${r}
          @click=${()=>this.#a(t,n,[...this.#$(t,n),Object.fromEntries(o.columns.map(d=>[d,""]))])}>Add a row</button>`;case"switches":return h;case"word":return u`<span data-control=${l} data-line="word">${o.text}</span>`}}#W(t,n,i,r){let o=this.#y.get(t);return o!==void 0&&M(n(o),i)?o:r}#xt(t){this.#m=t,this.requestUpdate(),this.updateComplete.then(()=>this.renderRoot.querySelector("input[data-act=item]")?.focus())}#bt(t,n,i){let r=t.target;if(t.key==="Enter"){t.preventDefault();let o=r.value;r.value="",this.#H(n,i,o)}else t.key==="Escape"&&(t.preventDefault(),r.value="",this.#m=null,this.requestUpdate())}#G(t,n,i,r){let o=i.control,l=this.#f.has(`${t}/${i.name}`);switch(o.kind){case"long":return l?u`<textarea class="fld" aria-label=${i.label} data-field=${i.name} ?disabled=${r}
            .value=${T(o.text)} @input=${a=>this.#a(t,n,a.target.value)}></textarea>`:u`<span class="wide" data-line="first">${o.first}</span>`;case"toml":return l?u`<textarea class="fld" aria-label=${i.label} data-field=${i.name} ?disabled=${r}
            .value=${T(o.text)} @input=${a=>this.#et(t,n,a.target.value)}></textarea>`:h;case"lines":{if(!l)return o.first?u`<span class="wide" data-line="first">${o.first}</span>`:h;let a=this.#$(t,n);return u`<div class="items" data-field=${i.name}>${o.items.map((d,c)=>u`<div class="item">
            <input class="fld" type="text" aria-label=${`${i.label}, line ${c+1}`} ?disabled=${r}
              .value=${T(d)} @input=${p=>this.#a(t,n,a.map((f,m)=>m===c?this.#L(n,p.target.value):f))}>
            <button type="button" class="link dim" data-act="remove" ?disabled=${r}
              @click=${()=>this.#a(t,n,a.filter((p,f)=>f!==c))}>Remove</button></div>`)}
          <span><button type="button" class="link dim" data-act="add" ?disabled=${r}
            @click=${()=>this.#a(t,n,[...a,""])}>Add a line</button></span></div>`}case"rows":{let a=this.#$(t,n),d=n.json!==void 0?v(n):null,c=(f,m)=>g=>{let x=Array.isArray(d)?d[f]?.[m]:void 0;if(x===void 0||typeof x=="string")return g;try{return JSON.parse(g)}catch{return g}},p=(f,m,g)=>{this.#y.set(`${t}/${i.name}/${f}.${m}`,g);let x=c(f,m)(g);this.#a(t,n,a.map((y,w)=>w===f?{...y,[m]:x}:y))};return u`<div class="table" role="table" aria-label=${i.label}
            style=${gn({"grid-template-columns":`repeat(${o.columns.length}, minmax(0, 1fr)) auto`})}>
          ${o.columns.map(f=>u`<span class="h" role="columnheader">${f}</span>`)}<span></span>
          ${o.rows.map((f,m)=>u`${o.columns.map(g=>u`<input class="fld" type="text"
              aria-label=${`${g}, row ${m+1}`} data-cell=${`${m}.${g}`} ?disabled=${r}
              .value=${T(this.#W(`${t}/${i.name}/${m}.${g}`,c(m,g),f[g],hi(f[g])))}
              @input=${x=>p(m,g,x.target.value)}>`)}
            <button type="button" class="link dim" data-act="remove" ?disabled=${r}
              @click=${()=>this.#a(t,n,a.filter((g,x)=>x!==m))}>Remove</button>`)}
        </div>`}case"switches":return u`<div class="entries">${o.toggles.map(a=>this.#$t(t,n,i,a,r))}</div>`;default:return h}}#$t(t,n,i,r,o){let l=this.#t.pending,a=this.#t.sections.get(t),d=l.heldLine(t,a?.keys??[n],r.key),c=l.noteOf(t,r.key),p=(Array.isArray(n.entries)?n.entries:[]).find(m=>m?.key===r.key),f=[r.line,r.locked?Xt:null].filter(m=>m!==null);return u`<div class=${d!==null?"entry un":"entry"} data-entry=${r.key}>
      <span>${r.label}</span>
      <button type="button" class="sw" role="switch" aria-checked=${String(r.on)} aria-label=${`${i.label} ${r.label}`}
        data-control=${r.key} ?disabled=${o||r.locked}
        @click=${()=>{l.editEntry(t,n,p?.name??r.label,!r.on),this.#g(t)}}
      ></button>
      ${f.length>0||d!==null||c!==void 0?u`<span class="l2">
        <span class="said">${c!==void 0?this.#K(c.text,c.copy):f.map(m=>u`<span data-line="line">${m}</span> `)}</span>
        ${d!==null?this.#F(t,r.key,d.text,d.hurts):h}</span>`:h}
    </div>`}#J(t,n=!1){let i=t.module;if(this.#e!==null)return this.#v(i);let r=t.keys,l=this.#t.pending.changes(i,r);if(this.#n===i){let w=ce(r,l);if(w!==null)return u`<div class="save conf" data-part="save" data-state="confirm">
          <p class="msg" data-line="save">${w.asks.map((O,Ct)=>u`${Ct>0?" ":""}<b class="w"
            data-line="ask">${O.ask}</b> ${O.confirm}`)} ${w.rest}</p>
          <span class="btns"><button type="button" class="link" @click=${()=>this.#rt()}>${w.back}</button>
            <button type="button" class="btn wn" @click=${()=>{this.#it(i)}}>${w.button}</button></span>
        </div>`}let a=this.#o===i,d=this.#t.said.get(i),c=a||d===void 0?Yn(r,l,a):d,[p,...f]=c.split(" · "),m=/^\d+ (not saved|of \d+ saved)/.exec(p),g=n&&m?.[1]==="not saved"?` in ${i}`:"",x=m!==null?u`<b>${m[0]}</b>${g}${p.slice(m[0].length)}${f.length>0?` · ${f.join(" · ")}`:""}`:c,y=l.length>0&&!a;return u`<div class="save" data-part="save" data-state=${a?"saving":y?"held":"rest"}>
      <p class="msg" data-line="save">${x}</p>
      <span class="btns">${y?u`<button type="button" class="link" @click=${()=>this.#ot(i)}
          >Discard</button>`:h}
        <button type="button" class=${y?"btn acc":"btn"} ?disabled=${!y} @click=${()=>{this.#A(i)}}
          >Save</button></span>
    </div>`}#v(t){let n=this.#t.pending,r=Qn(n,t,p=>{let f=this.#t.sections.get(p)?.keys??[];return n.changes(p,f).map(m=>it(f,m.name))});if(r===null)return h;let[o,...l]=r.lines,a=r.buttons.includes("Save and leave"),[d,...c]=a?o.split("? "):[o.replace(/\?$/,""),""];return u`<div class="save conf" data-part="save" data-state="leave">
      <p class="msg" data-line="save"><b>${d}?</b>${a?` ${c.join("? ")}`:h}
        ${l.map(p=>u`<span data-line="leave">${p}</span>`)}</p>
      <span class="btns">${a?u`<button type="button" class="link" @click=${()=>this.#z()}>Stay</button>
          <button type="button" class="link" @click=${()=>this.#D()}>Leave without saving</button>
          <button type="button" class="btn acc" @click=${()=>{this.#lt(t)}}>Save and leave</button>`:u`<button type="button" class="link" @click=${()=>this.#D()}>Leave without saving</button>
          <button type="button" class="btn stay" @click=${()=>this.#z()}>Stay</button>`}</span>
    </div>`}#T(t,n=!1){let i=this.#vt(t.module);return u`<div class=${n?"foot after":"foot"} data-part="foot">
      <span class="file" data-line="file">${t.file?`Kept in ${t.file}`:""}</span>
      ${i!==null?u`<a href=${`/m/${t.module}/${i.route}`} data-module=${t.module}
        data-route=${i.route} data-line="page">${i.title}</a>`:h}
    </div>`}#vt(t){if(t==="ui")return{route:"modules",title:"Modules"};if(!ii(t))return null;let n=ni(t).find(i=>i.name==="page");return n?{route:"",title:n.title}:null}};function hi(s){return s==null?"":typeof s=="string"?s:JSON.stringify(s)}customElements.get(Zn)||customElements.define(Zn,pe);var ns="moxi-ui-page";function bi(s,e,t){if(s===0)return`No answer from the shell, so ${t} could not be read.`;if(s===403)return"This browser is signed out.";let n=e?.refused??e?.conflict??e?.error;return typeof n=="string"&&n?n:`The shell answered ${s} for ${t}.`}var $i=_`
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
`,fe=class extends ${constructor(){super(...arguments);this.route="";this.modules=null;this.checks=new Map;this.#t="none";this.#l="";this.#r=null;this.#s="";this.#d=0}static{this.styles=$i}static{this.properties={route:{type:String},modules:{state:!0},checks:{state:!0}}}#t;#l;#r;#s;#d;connectedCallback(){super.connectedCallback();let t=(this.getAttribute("route")??this.route??"").replace(/^\/+|\/+$/g,"");t===""?this.#t="home":t==="modules"?(this.#t="modules",this.#r=xi("ui","modules",n=>{this.modules=n})):t==="settings"||t.startsWith("settings/")?(this.#t="settings",this.#l=t):this.#t="none"}disconnectedCallback(){super.disconnectedCallback(),this.#r?.(),this.#r=null}render(){return this.#t==="home"?u`<moxi-ui-home></moxi-ui-home>`:this.#t==="modules"?this.#u():this.#t==="settings"?this.#p():u`<h1>Nothing is here</h1>
      <p>${this.#i("ui","modules","Open the modules view")}.</p>`}updated(t){super.updated(t),this.#t==="modules"&&t.has("modules")&&this.#e()}#i(t,n,i){return u`<a href=${`/m/${t}/${n}`} @click=${r=>{r.button!==0||r.metaKey||r.ctrlKey||r.shiftKey||r.altKey||(r.preventDefault(),mi(t,n))}}>${i}</a>`}#u(){let t=this.modules,n=t===null?[]:Ft(t.modules);return u`
      ${Tt()?h:u`<div class="bar">${this.#i("ui","settings","Settings")}</div>`}
      ${t?.error?u`<p class="problem">${t.error}</p>`:h}
      <div class="grid" id="home"></div>
      <h2>Installed</h2>
      ${t===null?u`<p class="dim">Reading the modules…</p>`:h}
      ${t!==null&&n.length===0?u`<p class="dim">No module is listed.</p>`:h}
      <div class="list">${n.map(i=>this.#c(i))}</div>
      ${Tt()?h:u`<moxi-ui-gateway></moxi-ui-gateway>`}`}#c(t){let n=en(t),i=this.checks.get(t.name),r=t.name!=="ui"&&es(t.name)&&he(t.name).some(o=>o.name==="page");return u`<div class="card" data-module=${t.name}>
      <div class="head">
        <h3>${t.name}</h3>
        ${t.version?u`<span class="dim small">${t.version}</span>`:h}
        <span class="grow"></span>
        <span class="tag ${n.tone}">${n.text}</span>
      </div>
      ${t.summary?u`<p class="dim">${t.summary}</p>`:h}
      ${nn(t).map(o=>u`<p class="small ${o.tone??""}">${o.text}</p>`)}
      <div class="actions">
        ${r?this.#i(t.name,"","Open its panel"):h}
        ${Tt()?h:u`<button type="button" ?disabled=${i?.busy===!0}
          @click=${()=>this.#n(t.name)}>${i?.busy?"Checking…":"Check"}</button>`}
      </div>
      ${i!==void 0&&!i.busy?this.#o(i):h}
    </div>`}#o(t){return t.error?u`<p class="small fail">${t.error}</p>`:t.rows.length===0?u`<p class="small dim">The doctor has no rows for it.</p>`:u`<ul class="rows">${ln(t.rows).map(n=>u`
      <li class=${n.tone}>
        <div class="head"><span class="tag ${n.tone}">${n.tone}</span><code>${n.name}</code></div>
        <p class="small">${n.detail}</p>
        ${n.fix?u`<p class="small dim">Fix: <code>${n.fix}</code></p>`:h}
      </li>`)}</ul>`}async#n(t){this.checks=new Map(this.checks).set(t,{busy:!0,rows:[],error:null});let n=await fi("ui",`api/doctor?module=${encodeURIComponent(t)}`),i;n.status===200&&Array.isArray(n.data?.rows)?i={busy:!1,rows:n.data.rows,error:n.data.error??null}:i={busy:!1,rows:[],error:bi(n.status,n.data,"the doctor")},this.checks=new Map(this.checks).set(t,i)}async#e(){let t=this.renderRoot.querySelector("#home");if(t===null||this.modules===null)return;let n=sn(this.modules.modules).filter(o=>es(o.module)),i=JSON.stringify(n.map(o=>[o.module,o.name,he(o.module).find(l=>l.name===o.name)?.url??""]));if(i===this.#s)return;this.#s=i;let r=this.#d+=1;t.replaceChildren();for(let o of n){let l=document.createElement("section");l.className="card",l.dataset.block=`${o.module}.${o.name}`;let a=document.createElement("h2");a.textContent=o.title;let d=document.createElement("div");l.append(a,d),t.append(l);let c=he(o.module).find(f=>f.name===o.name)?.url,p=await vt(()=>gi(d,o.module,o.name),async()=>c===void 0?"no script":await yt(o.module,c),()=>r===this.#d);if(p===null)return;if(!("placed"in p)){let f=document.createElement("p");f.className="small warn",f.textContent=`${o.module}'s ${o.name} could not be shown.`,d.append(f)}}}#p(){return Tt()?u`<p class="dim" data-line="desk">Settings are changed at the desk, on the computer's Moxi page.</p>
        <div class="bar">${this.#i("ui","","Home")}</div>`:u`<moxi-ui-settings route=${this.#l}></moxi-ui-settings>`}};customElements.get(ns)||customElements.define(ns,fe);export{fe as UiPage};
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
