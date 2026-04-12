(function(){const G=document.createElement("link").relList;if(G&&G.supports&&G.supports("modulepreload"))return;for(const B of document.querySelectorAll('link[rel="modulepreload"]'))f(B);new MutationObserver(B=>{for(const H of B)if(H.type==="childList")for(const q of H.addedNodes)q.tagName==="LINK"&&q.rel==="modulepreload"&&f(q)}).observe(document,{childList:!0,subtree:!0});function U(B){const H={};return B.integrity&&(H.integrity=B.integrity),B.referrerPolicy&&(H.referrerPolicy=B.referrerPolicy),B.crossOrigin==="use-credentials"?H.credentials="include":B.crossOrigin==="anonymous"?H.credentials="omit":H.credentials="same-origin",H}function f(B){if(B.ep)return;B.ep=!0;const H=U(B);fetch(B.href,H)}})();var $o={exports:{}},vl={};var $d;function Dm(){if($d)return vl;$d=1;var E=Symbol.for("react.transitional.element"),G=Symbol.for("react.fragment");function U(f,B,H){var q=null;if(H!==void 0&&(q=""+H),B.key!==void 0&&(q=""+B.key),"key"in B){H={};for(var ue in B)ue!=="key"&&(H[ue]=B[ue])}else H=B;return B=H.ref,{$$typeof:E,type:f,key:q,ref:B!==void 0?B:null,props:H}}return vl.Fragment=G,vl.jsx=U,vl.jsxs=U,vl}var Jd;function Bm(){return Jd||(Jd=1,$o.exports=Dm()),$o.exports}var I=Bm(),Jo={exports:{}},bl={},Fo={exports:{}},es={};var Fd;function _m(){return Fd||(Fd=1,(function(E){function G(v,R){var z=v.length;v.push(R);e:for(;0<z;){var re=z-1>>>1,u=v[re];if(0<B(u,R))v[re]=R,v[z]=u,z=re;else break e}}function U(v){return v.length===0?null:v[0]}function f(v){if(v.length===0)return null;var R=v[0],z=v.pop();if(z!==R){v[0]=z;e:for(var re=0,u=v.length,w=u>>>1;re<w;){var M=2*(re+1)-1,O=v[M],_=M+1,$=v[_];if(0>B(O,z))_<u&&0>B($,O)?(v[re]=$,v[_]=z,re=_):(v[re]=O,v[M]=z,re=M);else if(_<u&&0>B($,z))v[re]=$,v[_]=z,re=_;else break e}}return R}function B(v,R){var z=v.sortIndex-R.sortIndex;return z!==0?z:v.id-R.id}if(E.unstable_now=void 0,typeof performance=="object"&&typeof performance.now=="function"){var H=performance;E.unstable_now=function(){return H.now()}}else{var q=Date,ue=q.now();E.unstable_now=function(){return q.now()-ue}}var A=[],S=[],D=1,Z=null,F=3,ye=!1,Be=!1,Le=!1,Ae=!1,vt=typeof setTimeout=="function"?setTimeout:null,nt=typeof clearTimeout=="function"?clearTimeout:null,Te=typeof setImmediate<"u"?setImmediate:null;function ht(v){for(var R=U(S);R!==null;){if(R.callback===null)f(S);else if(R.startTime<=v)f(S),R.sortIndex=R.expirationTime,G(A,R);else break;R=U(S)}}function W(v){if(Le=!1,ht(v),!Be)if(U(A)!==null)Be=!0,Qe||(Qe=!0,_e());else{var R=U(S);R!==null&&xe(W,R.startTime-v)}}var Qe=!1,Ke=-1,Xe=5,bt=-1;function Ca(){return Ae?!0:!(E.unstable_now()-bt<Xe)}function Rt(){if(Ae=!1,Qe){var v=E.unstable_now();bt=v;var R=!0;try{e:{Be=!1,Le&&(Le=!1,nt(Ke),Ke=-1),ye=!0;var z=F;try{t:{for(ht(v),Z=U(A);Z!==null&&!(Z.expirationTime>v&&Ca());){var re=Z.callback;if(typeof re=="function"){Z.callback=null,F=Z.priorityLevel;var u=re(Z.expirationTime<=v);if(v=E.unstable_now(),typeof u=="function"){Z.callback=u,ht(v),R=!0;break t}Z===U(A)&&f(A),ht(v)}else f(A);Z=U(A)}if(Z!==null)R=!0;else{var w=U(S);w!==null&&xe(W,w.startTime-v),R=!1}}break e}finally{Z=null,F=z,ye=!1}R=void 0}}finally{R?_e():Qe=!1}}}var _e;if(typeof Te=="function")_e=function(){Te(Rt)};else if(typeof MessageChannel<"u"){var ma=new MessageChannel,pa=ma.port2;ma.port1.onmessage=Rt,_e=function(){pa.postMessage(null)}}else _e=function(){vt(Rt,0)};function xe(v,R){Ke=vt(function(){v(E.unstable_now())},R)}E.unstable_IdlePriority=5,E.unstable_ImmediatePriority=1,E.unstable_LowPriority=4,E.unstable_NormalPriority=3,E.unstable_Profiling=null,E.unstable_UserBlockingPriority=2,E.unstable_cancelCallback=function(v){v.callback=null},E.unstable_forceFrameRate=function(v){0>v||125<v?console.error("forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported"):Xe=0<v?Math.floor(1e3/v):5},E.unstable_getCurrentPriorityLevel=function(){return F},E.unstable_next=function(v){switch(F){case 1:case 2:case 3:var R=3;break;default:R=F}var z=F;F=R;try{return v()}finally{F=z}},E.unstable_requestPaint=function(){Ae=!0},E.unstable_runWithPriority=function(v,R){switch(v){case 1:case 2:case 3:case 4:case 5:break;default:v=3}var z=F;F=v;try{return R()}finally{F=z}},E.unstable_scheduleCallback=function(v,R,z){var re=E.unstable_now();switch(typeof z=="object"&&z!==null?(z=z.delay,z=typeof z=="number"&&0<z?re+z:re):z=re,v){case 1:var u=-1;break;case 2:u=250;break;case 5:u=1073741823;break;case 4:u=1e4;break;default:u=5e3}return u=z+u,v={id:D++,callback:R,priorityLevel:v,startTime:z,expirationTime:u,sortIndex:-1},z>re?(v.sortIndex=z,G(S,v),U(A)===null&&v===U(S)&&(Le?(nt(Ke),Ke=-1):Le=!0,xe(W,z-re))):(v.sortIndex=u,G(A,v),Be||ye||(Be=!0,Qe||(Qe=!0,_e()))),v},E.unstable_shouldYield=Ca,E.unstable_wrapCallback=function(v){var R=F;return function(){var z=F;F=R;try{return v.apply(this,arguments)}finally{F=z}}}})(es)),es}var ef;function xm(){return ef||(ef=1,Fo.exports=_m()),Fo.exports}var ts={exports:{}},V={};var tf;function Cm(){if(tf)return V;tf=1;var E=Symbol.for("react.transitional.element"),G=Symbol.for("react.portal"),U=Symbol.for("react.fragment"),f=Symbol.for("react.strict_mode"),B=Symbol.for("react.profiler"),H=Symbol.for("react.consumer"),q=Symbol.for("react.context"),ue=Symbol.for("react.forward_ref"),A=Symbol.for("react.suspense"),S=Symbol.for("react.memo"),D=Symbol.for("react.lazy"),Z=Symbol.iterator;function F(u){return u===null||typeof u!="object"?null:(u=Z&&u[Z]||u["@@iterator"],typeof u=="function"?u:null)}var ye={isMounted:function(){return!1},enqueueForceUpdate:function(){},enqueueReplaceState:function(){},enqueueSetState:function(){}},Be=Object.assign,Le={};function Ae(u,w,M){this.props=u,this.context=w,this.refs=Le,this.updater=M||ye}Ae.prototype.isReactComponent={},Ae.prototype.setState=function(u,w){if(typeof u!="object"&&typeof u!="function"&&u!=null)throw Error("takes an object of state variables to update or a function which returns an object of state variables.");this.updater.enqueueSetState(this,u,w,"setState")},Ae.prototype.forceUpdate=function(u){this.updater.enqueueForceUpdate(this,u,"forceUpdate")};function vt(){}vt.prototype=Ae.prototype;function nt(u,w,M){this.props=u,this.context=w,this.refs=Le,this.updater=M||ye}var Te=nt.prototype=new vt;Te.constructor=nt,Be(Te,Ae.prototype),Te.isPureReactComponent=!0;var ht=Array.isArray,W={H:null,A:null,T:null,S:null,V:null},Qe=Object.prototype.hasOwnProperty;function Ke(u,w,M,O,_,$){return M=$.ref,{$$typeof:E,type:u,key:w,ref:M!==void 0?M:null,props:$}}function Xe(u,w){return Ke(u.type,w,void 0,void 0,void 0,u.props)}function bt(u){return typeof u=="object"&&u!==null&&u.$$typeof===E}function Ca(u){var w={"=":"=0",":":"=2"};return"$"+u.replace(/[=:]/g,function(M){return w[M]})}var Rt=/\/+/g;function _e(u,w){return typeof u=="object"&&u!==null&&u.key!=null?Ca(""+u.key):w.toString(36)}function ma(){}function pa(u){switch(u.status){case"fulfilled":return u.value;case"rejected":throw u.reason;default:switch(typeof u.status=="string"?u.then(ma,ma):(u.status="pending",u.then(function(w){u.status==="pending"&&(u.status="fulfilled",u.value=w)},function(w){u.status==="pending"&&(u.status="rejected",u.reason=w)})),u.status){case"fulfilled":return u.value;case"rejected":throw u.reason}}throw u}function xe(u,w,M,O,_){var $=typeof u;($==="undefined"||$==="boolean")&&(u=null);var Y=!1;if(u===null)Y=!0;else switch($){case"bigint":case"string":case"number":Y=!0;break;case"object":switch(u.$$typeof){case E:case G:Y=!0;break;case D:return Y=u._init,xe(Y(u._payload),w,M,O,_)}}if(Y)return _=_(u),Y=O===""?"."+_e(u,0):O,ht(_)?(M="",Y!=null&&(M=Y.replace(Rt,"$&/")+"/"),xe(_,w,M,"",function(Gt){return Gt})):_!=null&&(bt(_)&&(_=Xe(_,M+(_.key==null||u&&u.key===_.key?"":(""+_.key).replace(Rt,"$&/")+"/")+Y)),w.push(_)),1;Y=0;var Ie=O===""?".":O+":";if(ht(u))for(var ce=0;ce<u.length;ce++)O=u[ce],$=Ie+_e(O,ce),Y+=xe(O,w,M,$,_);else if(ce=F(u),typeof ce=="function")for(u=ce.call(u),ce=0;!(O=u.next()).done;)O=O.value,$=Ie+_e(O,ce++),Y+=xe(O,w,M,$,_);else if($==="object"){if(typeof u.then=="function")return xe(pa(u),w,M,O,_);throw w=String(u),Error("Objects are not valid as a React child (found: "+(w==="[object Object]"?"object with keys {"+Object.keys(u).join(", ")+"}":w)+"). If you meant to render a collection of children, use an array instead.")}return Y}function v(u,w,M){if(u==null)return u;var O=[],_=0;return xe(u,O,"","",function($){return w.call(M,$,_++)}),O}function R(u){if(u._status===-1){var w=u._result;w=w(),w.then(function(M){(u._status===0||u._status===-1)&&(u._status=1,u._result=M)},function(M){(u._status===0||u._status===-1)&&(u._status=2,u._result=M)}),u._status===-1&&(u._status=0,u._result=w)}if(u._status===1)return u._result.default;throw u._result}var z=typeof reportError=="function"?reportError:function(u){if(typeof window=="object"&&typeof window.ErrorEvent=="function"){var w=new window.ErrorEvent("error",{bubbles:!0,cancelable:!0,message:typeof u=="object"&&u!==null&&typeof u.message=="string"?String(u.message):String(u),error:u});if(!window.dispatchEvent(w))return}else if(typeof process=="object"&&typeof process.emit=="function"){process.emit("uncaughtException",u);return}console.error(u)};function re(){}return V.Children={map:v,forEach:function(u,w,M){v(u,function(){w.apply(this,arguments)},M)},count:function(u){var w=0;return v(u,function(){w++}),w},toArray:function(u){return v(u,function(w){return w})||[]},only:function(u){if(!bt(u))throw Error("React.Children.only expected to receive a single React element child.");return u}},V.Component=Ae,V.Fragment=U,V.Profiler=B,V.PureComponent=nt,V.StrictMode=f,V.Suspense=A,V.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE=W,V.__COMPILER_RUNTIME={__proto__:null,c:function(u){return W.H.useMemoCache(u)}},V.cache=function(u){return function(){return u.apply(null,arguments)}},V.cloneElement=function(u,w,M){if(u==null)throw Error("The argument must be a React element, but you passed "+u+".");var O=Be({},u.props),_=u.key,$=void 0;if(w!=null)for(Y in w.ref!==void 0&&($=void 0),w.key!==void 0&&(_=""+w.key),w)!Qe.call(w,Y)||Y==="key"||Y==="__self"||Y==="__source"||Y==="ref"&&w.ref===void 0||(O[Y]=w[Y]);var Y=arguments.length-2;if(Y===1)O.children=M;else if(1<Y){for(var Ie=Array(Y),ce=0;ce<Y;ce++)Ie[ce]=arguments[ce+2];O.children=Ie}return Ke(u.type,_,void 0,void 0,$,O)},V.createContext=function(u){return u={$$typeof:q,_currentValue:u,_currentValue2:u,_threadCount:0,Provider:null,Consumer:null},u.Provider=u,u.Consumer={$$typeof:H,_context:u},u},V.createElement=function(u,w,M){var O,_={},$=null;if(w!=null)for(O in w.key!==void 0&&($=""+w.key),w)Qe.call(w,O)&&O!=="key"&&O!=="__self"&&O!=="__source"&&(_[O]=w[O]);var Y=arguments.length-2;if(Y===1)_.children=M;else if(1<Y){for(var Ie=Array(Y),ce=0;ce<Y;ce++)Ie[ce]=arguments[ce+2];_.children=Ie}if(u&&u.defaultProps)for(O in Y=u.defaultProps,Y)_[O]===void 0&&(_[O]=Y[O]);return Ke(u,$,void 0,void 0,null,_)},V.createRef=function(){return{current:null}},V.forwardRef=function(u){return{$$typeof:ue,render:u}},V.isValidElement=bt,V.lazy=function(u){return{$$typeof:D,_payload:{_status:-1,_result:u},_init:R}},V.memo=function(u,w){return{$$typeof:S,type:u,compare:w===void 0?null:w}},V.startTransition=function(u){var w=W.T,M={};W.T=M;try{var O=u(),_=W.S;_!==null&&_(M,O),typeof O=="object"&&O!==null&&typeof O.then=="function"&&O.then(re,z)}catch($){z($)}finally{W.T=w}},V.unstable_useCacheRefresh=function(){return W.H.useCacheRefresh()},V.use=function(u){return W.H.use(u)},V.useActionState=function(u,w,M){return W.H.useActionState(u,w,M)},V.useCallback=function(u,w){return W.H.useCallback(u,w)},V.useContext=function(u){return W.H.useContext(u)},V.useDebugValue=function(){},V.useDeferredValue=function(u,w){return W.H.useDeferredValue(u,w)},V.useEffect=function(u,w,M){var O=W.H;if(typeof M=="function")throw Error("useEffect CRUD overload is not enabled in this build of React.");return O.useEffect(u,w)},V.useId=function(){return W.H.useId()},V.useImperativeHandle=function(u,w,M){return W.H.useImperativeHandle(u,w,M)},V.useInsertionEffect=function(u,w){return W.H.useInsertionEffect(u,w)},V.useLayoutEffect=function(u,w){return W.H.useLayoutEffect(u,w)},V.useMemo=function(u,w){return W.H.useMemo(u,w)},V.useOptimistic=function(u,w){return W.H.useOptimistic(u,w)},V.useReducer=function(u,w,M){return W.H.useReducer(u,w,M)},V.useRef=function(u){return W.H.useRef(u)},V.useState=function(u){return W.H.useState(u)},V.useSyncExternalStore=function(u,w,M){return W.H.useSyncExternalStore(u,w,M)},V.useTransition=function(){return W.H.useTransition()},V.version="19.1.0",V}var af;function ls(){return af||(af=1,ts.exports=Cm()),ts.exports}var as={exports:{}},Ue={};var nf;function zm(){if(nf)return Ue;nf=1;var E=ls();function G(A){var S="https://react.dev/errors/"+A;if(1<arguments.length){S+="?args[]="+encodeURIComponent(arguments[1]);for(var D=2;D<arguments.length;D++)S+="&args[]="+encodeURIComponent(arguments[D])}return"Minified React error #"+A+"; visit "+S+" for the full message or use the non-minified dev environment for full errors and additional helpful warnings."}function U(){}var f={d:{f:U,r:function(){throw Error(G(522))},D:U,C:U,L:U,m:U,X:U,S:U,M:U},p:0,findDOMNode:null},B=Symbol.for("react.portal");function H(A,S,D){var Z=3<arguments.length&&arguments[3]!==void 0?arguments[3]:null;return{$$typeof:B,key:Z==null?null:""+Z,children:A,containerInfo:S,implementation:D}}var q=E.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;function ue(A,S){if(A==="font")return"";if(typeof S=="string")return S==="use-credentials"?S:""}return Ue.__DOM_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE=f,Ue.createPortal=function(A,S){var D=2<arguments.length&&arguments[2]!==void 0?arguments[2]:null;if(!S||S.nodeType!==1&&S.nodeType!==9&&S.nodeType!==11)throw Error(G(299));return H(A,S,null,D)},Ue.flushSync=function(A){var S=q.T,D=f.p;try{if(q.T=null,f.p=2,A)return A()}finally{q.T=S,f.p=D,f.d.f()}},Ue.preconnect=function(A,S){typeof A=="string"&&(S?(S=S.crossOrigin,S=typeof S=="string"?S==="use-credentials"?S:"":void 0):S=null,f.d.C(A,S))},Ue.prefetchDNS=function(A){typeof A=="string"&&f.d.D(A)},Ue.preinit=function(A,S){if(typeof A=="string"&&S&&typeof S.as=="string"){var D=S.as,Z=ue(D,S.crossOrigin),F=typeof S.integrity=="string"?S.integrity:void 0,ye=typeof S.fetchPriority=="string"?S.fetchPriority:void 0;D==="style"?f.d.S(A,typeof S.precedence=="string"?S.precedence:void 0,{crossOrigin:Z,integrity:F,fetchPriority:ye}):D==="script"&&f.d.X(A,{crossOrigin:Z,integrity:F,fetchPriority:ye,nonce:typeof S.nonce=="string"?S.nonce:void 0})}},Ue.preinitModule=function(A,S){if(typeof A=="string")if(typeof S=="object"&&S!==null){if(S.as==null||S.as==="script"){var D=ue(S.as,S.crossOrigin);f.d.M(A,{crossOrigin:D,integrity:typeof S.integrity=="string"?S.integrity:void 0,nonce:typeof S.nonce=="string"?S.nonce:void 0})}}else S==null&&f.d.M(A)},Ue.preload=function(A,S){if(typeof A=="string"&&typeof S=="object"&&S!==null&&typeof S.as=="string"){var D=S.as,Z=ue(D,S.crossOrigin);f.d.L(A,D,{crossOrigin:Z,integrity:typeof S.integrity=="string"?S.integrity:void 0,nonce:typeof S.nonce=="string"?S.nonce:void 0,type:typeof S.type=="string"?S.type:void 0,fetchPriority:typeof S.fetchPriority=="string"?S.fetchPriority:void 0,referrerPolicy:typeof S.referrerPolicy=="string"?S.referrerPolicy:void 0,imageSrcSet:typeof S.imageSrcSet=="string"?S.imageSrcSet:void 0,imageSizes:typeof S.imageSizes=="string"?S.imageSizes:void 0,media:typeof S.media=="string"?S.media:void 0})}},Ue.preloadModule=function(A,S){if(typeof A=="string")if(S){var D=ue(S.as,S.crossOrigin);f.d.m(A,{as:typeof S.as=="string"&&S.as!=="script"?S.as:void 0,crossOrigin:D,integrity:typeof S.integrity=="string"?S.integrity:void 0})}else f.d.m(A)},Ue.requestFormReset=function(A){f.d.r(A)},Ue.unstable_batchedUpdates=function(A,S){return A(S)},Ue.useFormState=function(A,S,D){return q.H.useFormState(A,S,D)},Ue.useFormStatus=function(){return q.H.useHostTransitionStatus()},Ue.version="19.1.0",Ue}var lf;function Um(){if(lf)return as.exports;lf=1;function E(){if(!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__>"u"||typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE!="function"))try{__REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(E)}catch(G){console.error(G)}}return E(),as.exports=zm(),as.exports}var rf;function Lm(){if(rf)return bl;rf=1;var E=xm(),G=ls(),U=Um();function f(e){var t="https://react.dev/errors/"+e;if(1<arguments.length){t+="?args[]="+encodeURIComponent(arguments[1]);for(var a=2;a<arguments.length;a++)t+="&args[]="+encodeURIComponent(arguments[a])}return"Minified React error #"+e+"; visit "+t+" for the full message or use the non-minified dev environment for full errors and additional helpful warnings."}function B(e){return!(!e||e.nodeType!==1&&e.nodeType!==9&&e.nodeType!==11)}function H(e){var t=e,a=e;if(e.alternate)for(;t.return;)t=t.return;else{e=t;do t=e,(t.flags&4098)!==0&&(a=t.return),e=t.return;while(e)}return t.tag===3?a:null}function q(e){if(e.tag===13){var t=e.memoizedState;if(t===null&&(e=e.alternate,e!==null&&(t=e.memoizedState)),t!==null)return t.dehydrated}return null}function ue(e){if(H(e)!==e)throw Error(f(188))}function A(e){var t=e.alternate;if(!t){if(t=H(e),t===null)throw Error(f(188));return t!==e?null:e}for(var a=e,n=t;;){var l=a.return;if(l===null)break;var i=l.alternate;if(i===null){if(n=l.return,n!==null){a=n;continue}break}if(l.child===i.child){for(i=l.child;i;){if(i===a)return ue(l),e;if(i===n)return ue(l),t;i=i.sibling}throw Error(f(188))}if(a.return!==n.return)a=l,n=i;else{for(var r=!1,o=l.child;o;){if(o===a){r=!0,a=l,n=i;break}if(o===n){r=!0,n=l,a=i;break}o=o.sibling}if(!r){for(o=i.child;o;){if(o===a){r=!0,a=i,n=l;break}if(o===n){r=!0,n=i,a=l;break}o=o.sibling}if(!r)throw Error(f(189))}}if(a.alternate!==n)throw Error(f(190))}if(a.tag!==3)throw Error(f(188));return a.stateNode.current===a?e:t}function S(e){var t=e.tag;if(t===5||t===26||t===27||t===6)return e;for(e=e.child;e!==null;){if(t=S(e),t!==null)return t;e=e.sibling}return null}var D=Object.assign,Z=Symbol.for("react.element"),F=Symbol.for("react.transitional.element"),ye=Symbol.for("react.portal"),Be=Symbol.for("react.fragment"),Le=Symbol.for("react.strict_mode"),Ae=Symbol.for("react.profiler"),vt=Symbol.for("react.provider"),nt=Symbol.for("react.consumer"),Te=Symbol.for("react.context"),ht=Symbol.for("react.forward_ref"),W=Symbol.for("react.suspense"),Qe=Symbol.for("react.suspense_list"),Ke=Symbol.for("react.memo"),Xe=Symbol.for("react.lazy"),bt=Symbol.for("react.activity"),Ca=Symbol.for("react.memo_cache_sentinel"),Rt=Symbol.iterator;function _e(e){return e===null||typeof e!="object"?null:(e=Rt&&e[Rt]||e["@@iterator"],typeof e=="function"?e:null)}var ma=Symbol.for("react.client.reference");function pa(e){if(e==null)return null;if(typeof e=="function")return e.$$typeof===ma?null:e.displayName||e.name||null;if(typeof e=="string")return e;switch(e){case Be:return"Fragment";case Ae:return"Profiler";case Le:return"StrictMode";case W:return"Suspense";case Qe:return"SuspenseList";case bt:return"Activity"}if(typeof e=="object")switch(e.$$typeof){case ye:return"Portal";case Te:return(e.displayName||"Context")+".Provider";case nt:return(e._context.displayName||"Context")+".Consumer";case ht:var t=e.render;return e=e.displayName,e||(e=t.displayName||t.name||"",e=e!==""?"ForwardRef("+e+")":"ForwardRef"),e;case Ke:return t=e.displayName||null,t!==null?t:pa(e.type)||"Memo";case Xe:t=e._payload,e=e._init;try{return pa(e(t))}catch{}}return null}var xe=Array.isArray,v=G.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE,R=U.__DOM_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE,z={pending:!1,data:null,method:null,action:null},re=[],u=-1;function w(e){return{current:e}}function M(e){0>u||(e.current=re[u],re[u]=null,u--)}function O(e,t){u++,re[u]=e.current,e.current=t}var _=w(null),$=w(null),Y=w(null),Ie=w(null);function ce(e,t){switch(O(Y,t),O($,e),O(_,null),t.nodeType){case 9:case 11:e=(e=t.documentElement)&&(e=e.namespaceURI)?Od(e):0;break;default:if(e=t.tagName,t=t.namespaceURI)t=Od(t),e=Rd(t,e);else switch(e){case"svg":e=1;break;case"math":e=2;break;default:e=0}}M(_),O(_,e)}function Gt(){M(_),M($),M(Y)}function zi(e){e.memoizedState!==null&&O(Ie,e);var t=_.current,a=Rd(t,e.type);t!==a&&(O($,e),O(_,a))}function Tl(e){$.current===e&&(M(_),M($)),Ie.current===e&&(M(Ie),hl._currentValue=z)}var Ui=Object.prototype.hasOwnProperty,Li=E.unstable_scheduleCallback,Hi=E.unstable_cancelCallback,sf=E.unstable_shouldYield,uf=E.unstable_requestPaint,Tt=E.unstable_now,cf=E.unstable_getCurrentPriorityLevel,is=E.unstable_ImmediatePriority,rs=E.unstable_UserBlockingPriority,Sl=E.unstable_NormalPriority,df=E.unstable_LowPriority,os=E.unstable_IdlePriority,ff=E.log,hf=E.unstable_setDisableYieldValue,Sn=null,Pe=null;function jt(e){if(typeof ff=="function"&&hf(e),Pe&&typeof Pe.setStrictMode=="function")try{Pe.setStrictMode(Sn,e)}catch{}}var Ze=Math.clz32?Math.clz32:gf,mf=Math.log,pf=Math.LN2;function gf(e){return e>>>=0,e===0?32:31-(mf(e)/pf|0)|0}var wl=256,Al=4194304;function ga(e){var t=e&42;if(t!==0)return t;switch(e&-e){case 1:return 1;case 2:return 2;case 4:return 4;case 8:return 8;case 16:return 16;case 32:return 32;case 64:return 64;case 128:return 128;case 256:case 512:case 1024:case 2048:case 4096:case 8192:case 16384:case 32768:case 65536:case 131072:case 262144:case 524288:case 1048576:case 2097152:return e&4194048;case 4194304:case 8388608:case 16777216:case 33554432:return e&62914560;case 67108864:return 67108864;case 134217728:return 134217728;case 268435456:return 268435456;case 536870912:return 536870912;case 1073741824:return 0;default:return e}}function El(e,t,a){var n=e.pendingLanes;if(n===0)return 0;var l=0,i=e.suspendedLanes,r=e.pingedLanes;e=e.warmLanes;var o=n&134217727;return o!==0?(n=o&~i,n!==0?l=ga(n):(r&=o,r!==0?l=ga(r):a||(a=o&~e,a!==0&&(l=ga(a))))):(o=n&~i,o!==0?l=ga(o):r!==0?l=ga(r):a||(a=n&~e,a!==0&&(l=ga(a)))),l===0?0:t!==0&&t!==l&&(t&i)===0&&(i=l&-l,a=t&-t,i>=a||i===32&&(a&4194048)!==0)?t:l}function wn(e,t){return(e.pendingLanes&~(e.suspendedLanes&~e.pingedLanes)&t)===0}function yf(e,t){switch(e){case 1:case 2:case 4:case 8:case 64:return t+250;case 16:case 32:case 128:case 256:case 512:case 1024:case 2048:case 4096:case 8192:case 16384:case 32768:case 65536:case 131072:case 262144:case 524288:case 1048576:case 2097152:return t+5e3;case 4194304:case 8388608:case 16777216:case 33554432:return-1;case 67108864:case 134217728:case 268435456:case 536870912:case 1073741824:return-1;default:return-1}}function ss(){var e=wl;return wl<<=1,(wl&4194048)===0&&(wl=256),e}function us(){var e=Al;return Al<<=1,(Al&62914560)===0&&(Al=4194304),e}function qi(e){for(var t=[],a=0;31>a;a++)t.push(e);return t}function An(e,t){e.pendingLanes|=t,t!==268435456&&(e.suspendedLanes=0,e.pingedLanes=0,e.warmLanes=0)}function vf(e,t,a,n,l,i){var r=e.pendingLanes;e.pendingLanes=a,e.suspendedLanes=0,e.pingedLanes=0,e.warmLanes=0,e.expiredLanes&=a,e.entangledLanes&=a,e.errorRecoveryDisabledLanes&=a,e.shellSuspendCounter=0;var o=e.entanglements,s=e.expirationTimes,m=e.hiddenUpdates;for(a=r&~a;0<a;){var y=31-Ze(a),T=1<<y;o[y]=0,s[y]=-1;var p=m[y];if(p!==null)for(m[y]=null,y=0;y<p.length;y++){var g=p[y];g!==null&&(g.lane&=-536870913)}a&=~T}n!==0&&cs(e,n,0),i!==0&&l===0&&e.tag!==0&&(e.suspendedLanes|=i&~(r&~t))}function cs(e,t,a){e.pendingLanes|=t,e.suspendedLanes&=~t;var n=31-Ze(t);e.entangledLanes|=t,e.entanglements[n]=e.entanglements[n]|1073741824|a&4194090}function ds(e,t){var a=e.entangledLanes|=t;for(e=e.entanglements;a;){var n=31-Ze(a),l=1<<n;l&t|e[n]&t&&(e[n]|=t),a&=~l}}function Yi(e){switch(e){case 2:e=1;break;case 8:e=4;break;case 32:e=16;break;case 256:case 512:case 1024:case 2048:case 4096:case 8192:case 16384:case 32768:case 65536:case 131072:case 262144:case 524288:case 1048576:case 2097152:case 4194304:case 8388608:case 16777216:case 33554432:e=128;break;case 268435456:e=134217728;break;default:e=0}return e}function Vi(e){return e&=-e,2<e?8<e?(e&134217727)!==0?32:268435456:8:2}function fs(){var e=R.p;return e!==0?e:(e=window.event,e===void 0?32:Kd(e.type))}function bf(e,t){var a=R.p;try{return R.p=e,t()}finally{R.p=a}}var Qt=Math.random().toString(36).slice(2),Ce="__reactFiber$"+Qt,qe="__reactProps$"+Qt,za="__reactContainer$"+Qt,Gi="__reactEvents$"+Qt,Tf="__reactListeners$"+Qt,Sf="__reactHandles$"+Qt,hs="__reactResources$"+Qt,En="__reactMarker$"+Qt;function ji(e){delete e[Ce],delete e[qe],delete e[Gi],delete e[Tf],delete e[Sf]}function Ua(e){var t=e[Ce];if(t)return t;for(var a=e.parentNode;a;){if(t=a[za]||a[Ce]){if(a=t.alternate,t.child!==null||a!==null&&a.child!==null)for(e=Dd(e);e!==null;){if(a=e[Ce])return a;e=Dd(e)}return t}e=a,a=e.parentNode}return null}function La(e){if(e=e[Ce]||e[za]){var t=e.tag;if(t===5||t===6||t===13||t===26||t===27||t===3)return e}return null}function On(e){var t=e.tag;if(t===5||t===26||t===27||t===6)return e.stateNode;throw Error(f(33))}function Ha(e){var t=e[hs];return t||(t=e[hs]={hoistableStyles:new Map,hoistableScripts:new Map}),t}function Ee(e){e[En]=!0}var ms=new Set,ps={};function ya(e,t){qa(e,t),qa(e+"Capture",t)}function qa(e,t){for(ps[e]=t,e=0;e<t.length;e++)ms.add(t[e])}var wf=RegExp("^[:A-Z_a-z\\u00C0-\\u00D6\\u00D8-\\u00F6\\u00F8-\\u02FF\\u0370-\\u037D\\u037F-\\u1FFF\\u200C-\\u200D\\u2070-\\u218F\\u2C00-\\u2FEF\\u3001-\\uD7FF\\uF900-\\uFDCF\\uFDF0-\\uFFFD][:A-Z_a-z\\u00C0-\\u00D6\\u00D8-\\u00F6\\u00F8-\\u02FF\\u0370-\\u037D\\u037F-\\u1FFF\\u200C-\\u200D\\u2070-\\u218F\\u2C00-\\u2FEF\\u3001-\\uD7FF\\uF900-\\uFDCF\\uFDF0-\\uFFFD\\-.0-9\\u00B7\\u0300-\\u036F\\u203F-\\u2040]*$"),gs={},ys={};function Af(e){return Ui.call(ys,e)?!0:Ui.call(gs,e)?!1:wf.test(e)?ys[e]=!0:(gs[e]=!0,!1)}function Ol(e,t,a){if(Af(t))if(a===null)e.removeAttribute(t);else{switch(typeof a){case"undefined":case"function":case"symbol":e.removeAttribute(t);return;case"boolean":var n=t.toLowerCase().slice(0,5);if(n!=="data-"&&n!=="aria-"){e.removeAttribute(t);return}}e.setAttribute(t,""+a)}}function Rl(e,t,a){if(a===null)e.removeAttribute(t);else{switch(typeof a){case"undefined":case"function":case"symbol":case"boolean":e.removeAttribute(t);return}e.setAttribute(t,""+a)}}function Mt(e,t,a,n){if(n===null)e.removeAttribute(a);else{switch(typeof n){case"undefined":case"function":case"symbol":case"boolean":e.removeAttribute(a);return}e.setAttributeNS(t,a,""+n)}}var Qi,vs;function Ya(e){if(Qi===void 0)try{throw Error()}catch(a){var t=a.stack.trim().match(/\n( *(at )?)/);Qi=t&&t[1]||"",vs=-1<a.stack.indexOf(`
    at`)?" (<anonymous>)":-1<a.stack.indexOf("@")?"@unknown:0:0":""}return`
`+Qi+e+vs}var Ki=!1;function Xi(e,t){if(!e||Ki)return"";Ki=!0;var a=Error.prepareStackTrace;Error.prepareStackTrace=void 0;try{var n={DetermineComponentFrameRoot:function(){try{if(t){var T=function(){throw Error()};if(Object.defineProperty(T.prototype,"props",{set:function(){throw Error()}}),typeof Reflect=="object"&&Reflect.construct){try{Reflect.construct(T,[])}catch(g){var p=g}Reflect.construct(e,[],T)}else{try{T.call()}catch(g){p=g}e.call(T.prototype)}}else{try{throw Error()}catch(g){p=g}(T=e())&&typeof T.catch=="function"&&T.catch(function(){})}}catch(g){if(g&&p&&typeof g.stack=="string")return[g.stack,p.stack]}return[null,null]}};n.DetermineComponentFrameRoot.displayName="DetermineComponentFrameRoot";var l=Object.getOwnPropertyDescriptor(n.DetermineComponentFrameRoot,"name");l&&l.configurable&&Object.defineProperty(n.DetermineComponentFrameRoot,"name",{value:"DetermineComponentFrameRoot"});var i=n.DetermineComponentFrameRoot(),r=i[0],o=i[1];if(r&&o){var s=r.split(`
`),m=o.split(`
`);for(l=n=0;n<s.length&&!s[n].includes("DetermineComponentFrameRoot");)n++;for(;l<m.length&&!m[l].includes("DetermineComponentFrameRoot");)l++;if(n===s.length||l===m.length)for(n=s.length-1,l=m.length-1;1<=n&&0<=l&&s[n]!==m[l];)l--;for(;1<=n&&0<=l;n--,l--)if(s[n]!==m[l]){if(n!==1||l!==1)do if(n--,l--,0>l||s[n]!==m[l]){var y=`
`+s[n].replace(" at new "," at ");return e.displayName&&y.includes("<anonymous>")&&(y=y.replace("<anonymous>",e.displayName)),y}while(1<=n&&0<=l);break}}}finally{Ki=!1,Error.prepareStackTrace=a}return(a=e?e.displayName||e.name:"")?Ya(a):""}function Ef(e){switch(e.tag){case 26:case 27:case 5:return Ya(e.type);case 16:return Ya("Lazy");case 13:return Ya("Suspense");case 19:return Ya("SuspenseList");case 0:case 15:return Xi(e.type,!1);case 11:return Xi(e.type.render,!1);case 1:return Xi(e.type,!0);case 31:return Ya("Activity");default:return""}}function bs(e){try{var t="";do t+=Ef(e),e=e.return;while(e);return t}catch(a){return`
Error generating stack: `+a.message+`
`+a.stack}}function lt(e){switch(typeof e){case"bigint":case"boolean":case"number":case"string":case"undefined":return e;case"object":return e;default:return""}}function Ts(e){var t=e.type;return(e=e.nodeName)&&e.toLowerCase()==="input"&&(t==="checkbox"||t==="radio")}function Of(e){var t=Ts(e)?"checked":"value",a=Object.getOwnPropertyDescriptor(e.constructor.prototype,t),n=""+e[t];if(!e.hasOwnProperty(t)&&typeof a<"u"&&typeof a.get=="function"&&typeof a.set=="function"){var l=a.get,i=a.set;return Object.defineProperty(e,t,{configurable:!0,get:function(){return l.call(this)},set:function(r){n=""+r,i.call(this,r)}}),Object.defineProperty(e,t,{enumerable:a.enumerable}),{getValue:function(){return n},setValue:function(r){n=""+r},stopTracking:function(){e._valueTracker=null,delete e[t]}}}}function Ml(e){e._valueTracker||(e._valueTracker=Of(e))}function Ss(e){if(!e)return!1;var t=e._valueTracker;if(!t)return!0;var a=t.getValue(),n="";return e&&(n=Ts(e)?e.checked?"true":"false":e.value),e=n,e!==a?(t.setValue(e),!0):!1}function Nl(e){if(e=e||(typeof document<"u"?document:void 0),typeof e>"u")return null;try{return e.activeElement||e.body}catch{return e.body}}var Rf=/[\n"\\]/g;function it(e){return e.replace(Rf,function(t){return"\\"+t.charCodeAt(0).toString(16)+" "})}function Ii(e,t,a,n,l,i,r,o){e.name="",r!=null&&typeof r!="function"&&typeof r!="symbol"&&typeof r!="boolean"?e.type=r:e.removeAttribute("type"),t!=null?r==="number"?(t===0&&e.value===""||e.value!=t)&&(e.value=""+lt(t)):e.value!==""+lt(t)&&(e.value=""+lt(t)):r!=="submit"&&r!=="reset"||e.removeAttribute("value"),t!=null?Pi(e,r,lt(t)):a!=null?Pi(e,r,lt(a)):n!=null&&e.removeAttribute("value"),l==null&&i!=null&&(e.defaultChecked=!!i),l!=null&&(e.checked=l&&typeof l!="function"&&typeof l!="symbol"),o!=null&&typeof o!="function"&&typeof o!="symbol"&&typeof o!="boolean"?e.name=""+lt(o):e.removeAttribute("name")}function ws(e,t,a,n,l,i,r,o){if(i!=null&&typeof i!="function"&&typeof i!="symbol"&&typeof i!="boolean"&&(e.type=i),t!=null||a!=null){if(!(i!=="submit"&&i!=="reset"||t!=null))return;a=a!=null?""+lt(a):"",t=t!=null?""+lt(t):a,o||t===e.value||(e.value=t),e.defaultValue=t}n=n??l,n=typeof n!="function"&&typeof n!="symbol"&&!!n,e.checked=o?e.checked:!!n,e.defaultChecked=!!n,r!=null&&typeof r!="function"&&typeof r!="symbol"&&typeof r!="boolean"&&(e.name=r)}function Pi(e,t,a){t==="number"&&Nl(e.ownerDocument)===e||e.defaultValue===""+a||(e.defaultValue=""+a)}function Va(e,t,a,n){if(e=e.options,t){t={};for(var l=0;l<a.length;l++)t["$"+a[l]]=!0;for(a=0;a<e.length;a++)l=t.hasOwnProperty("$"+e[a].value),e[a].selected!==l&&(e[a].selected=l),l&&n&&(e[a].defaultSelected=!0)}else{for(a=""+lt(a),t=null,l=0;l<e.length;l++){if(e[l].value===a){e[l].selected=!0,n&&(e[l].defaultSelected=!0);return}t!==null||e[l].disabled||(t=e[l])}t!==null&&(t.selected=!0)}}function As(e,t,a){if(t!=null&&(t=""+lt(t),t!==e.value&&(e.value=t),a==null)){e.defaultValue!==t&&(e.defaultValue=t);return}e.defaultValue=a!=null?""+lt(a):""}function Es(e,t,a,n){if(t==null){if(n!=null){if(a!=null)throw Error(f(92));if(xe(n)){if(1<n.length)throw Error(f(93));n=n[0]}a=n}a==null&&(a=""),t=a}a=lt(t),e.defaultValue=a,n=e.textContent,n===a&&n!==""&&n!==null&&(e.value=n)}function Ga(e,t){if(t){var a=e.firstChild;if(a&&a===e.lastChild&&a.nodeType===3){a.nodeValue=t;return}}e.textContent=t}var Mf=new Set("animationIterationCount aspectRatio borderImageOutset borderImageSlice borderImageWidth boxFlex boxFlexGroup boxOrdinalGroup columnCount columns flex flexGrow flexPositive flexShrink flexNegative flexOrder gridArea gridRow gridRowEnd gridRowSpan gridRowStart gridColumn gridColumnEnd gridColumnSpan gridColumnStart fontWeight lineClamp lineHeight opacity order orphans scale tabSize widows zIndex zoom fillOpacity floodOpacity stopOpacity strokeDasharray strokeDashoffset strokeMiterlimit strokeOpacity strokeWidth MozAnimationIterationCount MozBoxFlex MozBoxFlexGroup MozLineClamp msAnimationIterationCount msFlex msZoom msFlexGrow msFlexNegative msFlexOrder msFlexPositive msFlexShrink msGridColumn msGridColumnSpan msGridRow msGridRowSpan WebkitAnimationIterationCount WebkitBoxFlex WebKitBoxFlexGroup WebkitBoxOrdinalGroup WebkitColumnCount WebkitColumns WebkitFlex WebkitFlexGrow WebkitFlexPositive WebkitFlexShrink WebkitLineClamp".split(" "));function Os(e,t,a){var n=t.indexOf("--")===0;a==null||typeof a=="boolean"||a===""?n?e.setProperty(t,""):t==="float"?e.cssFloat="":e[t]="":n?e.setProperty(t,a):typeof a!="number"||a===0||Mf.has(t)?t==="float"?e.cssFloat=a:e[t]=(""+a).trim():e[t]=a+"px"}function Rs(e,t,a){if(t!=null&&typeof t!="object")throw Error(f(62));if(e=e.style,a!=null){for(var n in a)!a.hasOwnProperty(n)||t!=null&&t.hasOwnProperty(n)||(n.indexOf("--")===0?e.setProperty(n,""):n==="float"?e.cssFloat="":e[n]="");for(var l in t)n=t[l],t.hasOwnProperty(l)&&a[l]!==n&&Os(e,l,n)}else for(var i in t)t.hasOwnProperty(i)&&Os(e,i,t[i])}function Zi(e){if(e.indexOf("-")===-1)return!1;switch(e){case"annotation-xml":case"color-profile":case"font-face":case"font-face-src":case"font-face-uri":case"font-face-format":case"font-face-name":case"missing-glyph":return!1;default:return!0}}var Nf=new Map([["acceptCharset","accept-charset"],["htmlFor","for"],["httpEquiv","http-equiv"],["crossOrigin","crossorigin"],["accentHeight","accent-height"],["alignmentBaseline","alignment-baseline"],["arabicForm","arabic-form"],["baselineShift","baseline-shift"],["capHeight","cap-height"],["clipPath","clip-path"],["clipRule","clip-rule"],["colorInterpolation","color-interpolation"],["colorInterpolationFilters","color-interpolation-filters"],["colorProfile","color-profile"],["colorRendering","color-rendering"],["dominantBaseline","dominant-baseline"],["enableBackground","enable-background"],["fillOpacity","fill-opacity"],["fillRule","fill-rule"],["floodColor","flood-color"],["floodOpacity","flood-opacity"],["fontFamily","font-family"],["fontSize","font-size"],["fontSizeAdjust","font-size-adjust"],["fontStretch","font-stretch"],["fontStyle","font-style"],["fontVariant","font-variant"],["fontWeight","font-weight"],["glyphName","glyph-name"],["glyphOrientationHorizontal","glyph-orientation-horizontal"],["glyphOrientationVertical","glyph-orientation-vertical"],["horizAdvX","horiz-adv-x"],["horizOriginX","horiz-origin-x"],["imageRendering","image-rendering"],["letterSpacing","letter-spacing"],["lightingColor","lighting-color"],["markerEnd","marker-end"],["markerMid","marker-mid"],["markerStart","marker-start"],["overlinePosition","overline-position"],["overlineThickness","overline-thickness"],["paintOrder","paint-order"],["panose-1","panose-1"],["pointerEvents","pointer-events"],["renderingIntent","rendering-intent"],["shapeRendering","shape-rendering"],["stopColor","stop-color"],["stopOpacity","stop-opacity"],["strikethroughPosition","strikethrough-position"],["strikethroughThickness","strikethrough-thickness"],["strokeDasharray","stroke-dasharray"],["strokeDashoffset","stroke-dashoffset"],["strokeLinecap","stroke-linecap"],["strokeLinejoin","stroke-linejoin"],["strokeMiterlimit","stroke-miterlimit"],["strokeOpacity","stroke-opacity"],["strokeWidth","stroke-width"],["textAnchor","text-anchor"],["textDecoration","text-decoration"],["textRendering","text-rendering"],["transformOrigin","transform-origin"],["underlinePosition","underline-position"],["underlineThickness","underline-thickness"],["unicodeBidi","unicode-bidi"],["unicodeRange","unicode-range"],["unitsPerEm","units-per-em"],["vAlphabetic","v-alphabetic"],["vHanging","v-hanging"],["vIdeographic","v-ideographic"],["vMathematical","v-mathematical"],["vectorEffect","vector-effect"],["vertAdvY","vert-adv-y"],["vertOriginX","vert-origin-x"],["vertOriginY","vert-origin-y"],["wordSpacing","word-spacing"],["writingMode","writing-mode"],["xmlnsXlink","xmlns:xlink"],["xHeight","x-height"]]),kf=/^[\u0000-\u001F ]*j[\r\n\t]*a[\r\n\t]*v[\r\n\t]*a[\r\n\t]*s[\r\n\t]*c[\r\n\t]*r[\r\n\t]*i[\r\n\t]*p[\r\n\t]*t[\r\n\t]*:/i;function kl(e){return kf.test(""+e)?"javascript:throw new Error('React has blocked a javascript: URL as a security precaution.')":e}var Wi=null;function $i(e){return e=e.target||e.srcElement||window,e.correspondingUseElement&&(e=e.correspondingUseElement),e.nodeType===3?e.parentNode:e}var ja=null,Qa=null;function Ms(e){var t=La(e);if(t&&(e=t.stateNode)){var a=e[qe]||null;e:switch(e=t.stateNode,t.type){case"input":if(Ii(e,a.value,a.defaultValue,a.defaultValue,a.checked,a.defaultChecked,a.type,a.name),t=a.name,a.type==="radio"&&t!=null){for(a=e;a.parentNode;)a=a.parentNode;for(a=a.querySelectorAll('input[name="'+it(""+t)+'"][type="radio"]'),t=0;t<a.length;t++){var n=a[t];if(n!==e&&n.form===e.form){var l=n[qe]||null;if(!l)throw Error(f(90));Ii(n,l.value,l.defaultValue,l.defaultValue,l.checked,l.defaultChecked,l.type,l.name)}}for(t=0;t<a.length;t++)n=a[t],n.form===e.form&&Ss(n)}break e;case"textarea":As(e,a.value,a.defaultValue);break e;case"select":t=a.value,t!=null&&Va(e,!!a.multiple,t,!1)}}}var Ji=!1;function Ns(e,t,a){if(Ji)return e(t,a);Ji=!0;try{var n=e(t);return n}finally{if(Ji=!1,(ja!==null||Qa!==null)&&(mi(),ja&&(t=ja,e=Qa,Qa=ja=null,Ms(t),e)))for(t=0;t<e.length;t++)Ms(e[t])}}function Rn(e,t){var a=e.stateNode;if(a===null)return null;var n=a[qe]||null;if(n===null)return null;a=n[t];e:switch(t){case"onClick":case"onClickCapture":case"onDoubleClick":case"onDoubleClickCapture":case"onMouseDown":case"onMouseDownCapture":case"onMouseMove":case"onMouseMoveCapture":case"onMouseUp":case"onMouseUpCapture":case"onMouseEnter":(n=!n.disabled)||(e=e.type,n=!(e==="button"||e==="input"||e==="select"||e==="textarea")),e=!n;break e;default:e=!1}if(e)return null;if(a&&typeof a!="function")throw Error(f(231,t,typeof a));return a}var Nt=!(typeof window>"u"||typeof window.document>"u"||typeof window.document.createElement>"u"),Fi=!1;if(Nt)try{var Mn={};Object.defineProperty(Mn,"passive",{get:function(){Fi=!0}}),window.addEventListener("test",Mn,Mn),window.removeEventListener("test",Mn,Mn)}catch{Fi=!1}var Kt=null,er=null,Dl=null;function ks(){if(Dl)return Dl;var e,t=er,a=t.length,n,l="value"in Kt?Kt.value:Kt.textContent,i=l.length;for(e=0;e<a&&t[e]===l[e];e++);var r=a-e;for(n=1;n<=r&&t[a-n]===l[i-n];n++);return Dl=l.slice(e,1<n?1-n:void 0)}function Bl(e){var t=e.keyCode;return"charCode"in e?(e=e.charCode,e===0&&t===13&&(e=13)):e=t,e===10&&(e=13),32<=e||e===13?e:0}function _l(){return!0}function Ds(){return!1}function Ye(e){function t(a,n,l,i,r){this._reactName=a,this._targetInst=l,this.type=n,this.nativeEvent=i,this.target=r,this.currentTarget=null;for(var o in e)e.hasOwnProperty(o)&&(a=e[o],this[o]=a?a(i):i[o]);return this.isDefaultPrevented=(i.defaultPrevented!=null?i.defaultPrevented:i.returnValue===!1)?_l:Ds,this.isPropagationStopped=Ds,this}return D(t.prototype,{preventDefault:function(){this.defaultPrevented=!0;var a=this.nativeEvent;a&&(a.preventDefault?a.preventDefault():typeof a.returnValue!="unknown"&&(a.returnValue=!1),this.isDefaultPrevented=_l)},stopPropagation:function(){var a=this.nativeEvent;a&&(a.stopPropagation?a.stopPropagation():typeof a.cancelBubble!="unknown"&&(a.cancelBubble=!0),this.isPropagationStopped=_l)},persist:function(){},isPersistent:_l}),t}var va={eventPhase:0,bubbles:0,cancelable:0,timeStamp:function(e){return e.timeStamp||Date.now()},defaultPrevented:0,isTrusted:0},xl=Ye(va),Nn=D({},va,{view:0,detail:0}),Df=Ye(Nn),tr,ar,kn,Cl=D({},Nn,{screenX:0,screenY:0,clientX:0,clientY:0,pageX:0,pageY:0,ctrlKey:0,shiftKey:0,altKey:0,metaKey:0,getModifierState:lr,button:0,buttons:0,relatedTarget:function(e){return e.relatedTarget===void 0?e.fromElement===e.srcElement?e.toElement:e.fromElement:e.relatedTarget},movementX:function(e){return"movementX"in e?e.movementX:(e!==kn&&(kn&&e.type==="mousemove"?(tr=e.screenX-kn.screenX,ar=e.screenY-kn.screenY):ar=tr=0,kn=e),tr)},movementY:function(e){return"movementY"in e?e.movementY:ar}}),Bs=Ye(Cl),Bf=D({},Cl,{dataTransfer:0}),_f=Ye(Bf),xf=D({},Nn,{relatedTarget:0}),nr=Ye(xf),Cf=D({},va,{animationName:0,elapsedTime:0,pseudoElement:0}),zf=Ye(Cf),Uf=D({},va,{clipboardData:function(e){return"clipboardData"in e?e.clipboardData:window.clipboardData}}),Lf=Ye(Uf),Hf=D({},va,{data:0}),_s=Ye(Hf),qf={Esc:"Escape",Spacebar:" ",Left:"ArrowLeft",Up:"ArrowUp",Right:"ArrowRight",Down:"ArrowDown",Del:"Delete",Win:"OS",Menu:"ContextMenu",Apps:"ContextMenu",Scroll:"ScrollLock",MozPrintableKey:"Unidentified"},Yf={8:"Backspace",9:"Tab",12:"Clear",13:"Enter",16:"Shift",17:"Control",18:"Alt",19:"Pause",20:"CapsLock",27:"Escape",32:" ",33:"PageUp",34:"PageDown",35:"End",36:"Home",37:"ArrowLeft",38:"ArrowUp",39:"ArrowRight",40:"ArrowDown",45:"Insert",46:"Delete",112:"F1",113:"F2",114:"F3",115:"F4",116:"F5",117:"F6",118:"F7",119:"F8",120:"F9",121:"F10",122:"F11",123:"F12",144:"NumLock",145:"ScrollLock",224:"Meta"},Vf={Alt:"altKey",Control:"ctrlKey",Meta:"metaKey",Shift:"shiftKey"};function Gf(e){var t=this.nativeEvent;return t.getModifierState?t.getModifierState(e):(e=Vf[e])?!!t[e]:!1}function lr(){return Gf}var jf=D({},Nn,{key:function(e){if(e.key){var t=qf[e.key]||e.key;if(t!=="Unidentified")return t}return e.type==="keypress"?(e=Bl(e),e===13?"Enter":String.fromCharCode(e)):e.type==="keydown"||e.type==="keyup"?Yf[e.keyCode]||"Unidentified":""},code:0,location:0,ctrlKey:0,shiftKey:0,altKey:0,metaKey:0,repeat:0,locale:0,getModifierState:lr,charCode:function(e){return e.type==="keypress"?Bl(e):0},keyCode:function(e){return e.type==="keydown"||e.type==="keyup"?e.keyCode:0},which:function(e){return e.type==="keypress"?Bl(e):e.type==="keydown"||e.type==="keyup"?e.keyCode:0}}),Qf=Ye(jf),Kf=D({},Cl,{pointerId:0,width:0,height:0,pressure:0,tangentialPressure:0,tiltX:0,tiltY:0,twist:0,pointerType:0,isPrimary:0}),xs=Ye(Kf),Xf=D({},Nn,{touches:0,targetTouches:0,changedTouches:0,altKey:0,metaKey:0,ctrlKey:0,shiftKey:0,getModifierState:lr}),If=Ye(Xf),Pf=D({},va,{propertyName:0,elapsedTime:0,pseudoElement:0}),Zf=Ye(Pf),Wf=D({},Cl,{deltaX:function(e){return"deltaX"in e?e.deltaX:"wheelDeltaX"in e?-e.wheelDeltaX:0},deltaY:function(e){return"deltaY"in e?e.deltaY:"wheelDeltaY"in e?-e.wheelDeltaY:"wheelDelta"in e?-e.wheelDelta:0},deltaZ:0,deltaMode:0}),$f=Ye(Wf),Jf=D({},va,{newState:0,oldState:0}),Ff=Ye(Jf),eh=[9,13,27,32],ir=Nt&&"CompositionEvent"in window,Dn=null;Nt&&"documentMode"in document&&(Dn=document.documentMode);var th=Nt&&"TextEvent"in window&&!Dn,Cs=Nt&&(!ir||Dn&&8<Dn&&11>=Dn),zs=" ",Us=!1;function Ls(e,t){switch(e){case"keyup":return eh.indexOf(t.keyCode)!==-1;case"keydown":return t.keyCode!==229;case"keypress":case"mousedown":case"focusout":return!0;default:return!1}}function Hs(e){return e=e.detail,typeof e=="object"&&"data"in e?e.data:null}var Ka=!1;function ah(e,t){switch(e){case"compositionend":return Hs(t);case"keypress":return t.which!==32?null:(Us=!0,zs);case"textInput":return e=t.data,e===zs&&Us?null:e;default:return null}}function nh(e,t){if(Ka)return e==="compositionend"||!ir&&Ls(e,t)?(e=ks(),Dl=er=Kt=null,Ka=!1,e):null;switch(e){case"paste":return null;case"keypress":if(!(t.ctrlKey||t.altKey||t.metaKey)||t.ctrlKey&&t.altKey){if(t.char&&1<t.char.length)return t.char;if(t.which)return String.fromCharCode(t.which)}return null;case"compositionend":return Cs&&t.locale!=="ko"?null:t.data;default:return null}}var lh={color:!0,date:!0,datetime:!0,"datetime-local":!0,email:!0,month:!0,number:!0,password:!0,range:!0,search:!0,tel:!0,text:!0,time:!0,url:!0,week:!0};function qs(e){var t=e&&e.nodeName&&e.nodeName.toLowerCase();return t==="input"?!!lh[e.type]:t==="textarea"}function Ys(e,t,a,n){ja?Qa?Qa.push(n):Qa=[n]:ja=n,t=Ti(t,"onChange"),0<t.length&&(a=new xl("onChange","change",null,a,n),e.push({event:a,listeners:t}))}var Bn=null,_n=null;function ih(e){Td(e,0)}function zl(e){var t=On(e);if(Ss(t))return e}function Vs(e,t){if(e==="change")return t}var Gs=!1;if(Nt){var rr;if(Nt){var or="oninput"in document;if(!or){var js=document.createElement("div");js.setAttribute("oninput","return;"),or=typeof js.oninput=="function"}rr=or}else rr=!1;Gs=rr&&(!document.documentMode||9<document.documentMode)}function Qs(){Bn&&(Bn.detachEvent("onpropertychange",Ks),_n=Bn=null)}function Ks(e){if(e.propertyName==="value"&&zl(_n)){var t=[];Ys(t,_n,e,$i(e)),Ns(ih,t)}}function rh(e,t,a){e==="focusin"?(Qs(),Bn=t,_n=a,Bn.attachEvent("onpropertychange",Ks)):e==="focusout"&&Qs()}function oh(e){if(e==="selectionchange"||e==="keyup"||e==="keydown")return zl(_n)}function sh(e,t){if(e==="click")return zl(t)}function uh(e,t){if(e==="input"||e==="change")return zl(t)}function ch(e,t){return e===t&&(e!==0||1/e===1/t)||e!==e&&t!==t}var We=typeof Object.is=="function"?Object.is:ch;function xn(e,t){if(We(e,t))return!0;if(typeof e!="object"||e===null||typeof t!="object"||t===null)return!1;var a=Object.keys(e),n=Object.keys(t);if(a.length!==n.length)return!1;for(n=0;n<a.length;n++){var l=a[n];if(!Ui.call(t,l)||!We(e[l],t[l]))return!1}return!0}function Xs(e){for(;e&&e.firstChild;)e=e.firstChild;return e}function Is(e,t){var a=Xs(e);e=0;for(var n;a;){if(a.nodeType===3){if(n=e+a.textContent.length,e<=t&&n>=t)return{node:a,offset:t-e};e=n}e:{for(;a;){if(a.nextSibling){a=a.nextSibling;break e}a=a.parentNode}a=void 0}a=Xs(a)}}function Ps(e,t){return e&&t?e===t?!0:e&&e.nodeType===3?!1:t&&t.nodeType===3?Ps(e,t.parentNode):"contains"in e?e.contains(t):e.compareDocumentPosition?!!(e.compareDocumentPosition(t)&16):!1:!1}function Zs(e){e=e!=null&&e.ownerDocument!=null&&e.ownerDocument.defaultView!=null?e.ownerDocument.defaultView:window;for(var t=Nl(e.document);t instanceof e.HTMLIFrameElement;){try{var a=typeof t.contentWindow.location.href=="string"}catch{a=!1}if(a)e=t.contentWindow;else break;t=Nl(e.document)}return t}function sr(e){var t=e&&e.nodeName&&e.nodeName.toLowerCase();return t&&(t==="input"&&(e.type==="text"||e.type==="search"||e.type==="tel"||e.type==="url"||e.type==="password")||t==="textarea"||e.contentEditable==="true")}var dh=Nt&&"documentMode"in document&&11>=document.documentMode,Xa=null,ur=null,Cn=null,cr=!1;function Ws(e,t,a){var n=a.window===a?a.document:a.nodeType===9?a:a.ownerDocument;cr||Xa==null||Xa!==Nl(n)||(n=Xa,"selectionStart"in n&&sr(n)?n={start:n.selectionStart,end:n.selectionEnd}:(n=(n.ownerDocument&&n.ownerDocument.defaultView||window).getSelection(),n={anchorNode:n.anchorNode,anchorOffset:n.anchorOffset,focusNode:n.focusNode,focusOffset:n.focusOffset}),Cn&&xn(Cn,n)||(Cn=n,n=Ti(ur,"onSelect"),0<n.length&&(t=new xl("onSelect","select",null,t,a),e.push({event:t,listeners:n}),t.target=Xa)))}function ba(e,t){var a={};return a[e.toLowerCase()]=t.toLowerCase(),a["Webkit"+e]="webkit"+t,a["Moz"+e]="moz"+t,a}var Ia={animationend:ba("Animation","AnimationEnd"),animationiteration:ba("Animation","AnimationIteration"),animationstart:ba("Animation","AnimationStart"),transitionrun:ba("Transition","TransitionRun"),transitionstart:ba("Transition","TransitionStart"),transitioncancel:ba("Transition","TransitionCancel"),transitionend:ba("Transition","TransitionEnd")},dr={},$s={};Nt&&($s=document.createElement("div").style,"AnimationEvent"in window||(delete Ia.animationend.animation,delete Ia.animationiteration.animation,delete Ia.animationstart.animation),"TransitionEvent"in window||delete Ia.transitionend.transition);function Ta(e){if(dr[e])return dr[e];if(!Ia[e])return e;var t=Ia[e],a;for(a in t)if(t.hasOwnProperty(a)&&a in $s)return dr[e]=t[a];return e}var Js=Ta("animationend"),Fs=Ta("animationiteration"),eu=Ta("animationstart"),fh=Ta("transitionrun"),hh=Ta("transitionstart"),mh=Ta("transitioncancel"),tu=Ta("transitionend"),au=new Map,fr="abort auxClick beforeToggle cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(" ");fr.push("scrollEnd");function mt(e,t){au.set(e,t),ya(t,[e])}var nu=new WeakMap;function rt(e,t){if(typeof e=="object"&&e!==null){var a=nu.get(e);return a!==void 0?a:(t={value:e,source:t,stack:bs(t)},nu.set(e,t),t)}return{value:e,source:t,stack:bs(t)}}var ot=[],Pa=0,hr=0;function Ul(){for(var e=Pa,t=hr=Pa=0;t<e;){var a=ot[t];ot[t++]=null;var n=ot[t];ot[t++]=null;var l=ot[t];ot[t++]=null;var i=ot[t];if(ot[t++]=null,n!==null&&l!==null){var r=n.pending;r===null?l.next=l:(l.next=r.next,r.next=l),n.pending=l}i!==0&&lu(a,l,i)}}function Ll(e,t,a,n){ot[Pa++]=e,ot[Pa++]=t,ot[Pa++]=a,ot[Pa++]=n,hr|=n,e.lanes|=n,e=e.alternate,e!==null&&(e.lanes|=n)}function mr(e,t,a,n){return Ll(e,t,a,n),Hl(e)}function Za(e,t){return Ll(e,null,null,t),Hl(e)}function lu(e,t,a){e.lanes|=a;var n=e.alternate;n!==null&&(n.lanes|=a);for(var l=!1,i=e.return;i!==null;)i.childLanes|=a,n=i.alternate,n!==null&&(n.childLanes|=a),i.tag===22&&(e=i.stateNode,e===null||e._visibility&1||(l=!0)),e=i,i=i.return;return e.tag===3?(i=e.stateNode,l&&t!==null&&(l=31-Ze(a),e=i.hiddenUpdates,n=e[l],n===null?e[l]=[t]:n.push(t),t.lane=a|536870912),i):null}function Hl(e){if(50<il)throw il=0,So=null,Error(f(185));for(var t=e.return;t!==null;)e=t,t=e.return;return e.tag===3?e.stateNode:null}var Wa={};function ph(e,t,a,n){this.tag=e,this.key=a,this.sibling=this.child=this.return=this.stateNode=this.type=this.elementType=null,this.index=0,this.refCleanup=this.ref=null,this.pendingProps=t,this.dependencies=this.memoizedState=this.updateQueue=this.memoizedProps=null,this.mode=n,this.subtreeFlags=this.flags=0,this.deletions=null,this.childLanes=this.lanes=0,this.alternate=null}function $e(e,t,a,n){return new ph(e,t,a,n)}function pr(e){return e=e.prototype,!(!e||!e.isReactComponent)}function kt(e,t){var a=e.alternate;return a===null?(a=$e(e.tag,t,e.key,e.mode),a.elementType=e.elementType,a.type=e.type,a.stateNode=e.stateNode,a.alternate=e,e.alternate=a):(a.pendingProps=t,a.type=e.type,a.flags=0,a.subtreeFlags=0,a.deletions=null),a.flags=e.flags&65011712,a.childLanes=e.childLanes,a.lanes=e.lanes,a.child=e.child,a.memoizedProps=e.memoizedProps,a.memoizedState=e.memoizedState,a.updateQueue=e.updateQueue,t=e.dependencies,a.dependencies=t===null?null:{lanes:t.lanes,firstContext:t.firstContext},a.sibling=e.sibling,a.index=e.index,a.ref=e.ref,a.refCleanup=e.refCleanup,a}function iu(e,t){e.flags&=65011714;var a=e.alternate;return a===null?(e.childLanes=0,e.lanes=t,e.child=null,e.subtreeFlags=0,e.memoizedProps=null,e.memoizedState=null,e.updateQueue=null,e.dependencies=null,e.stateNode=null):(e.childLanes=a.childLanes,e.lanes=a.lanes,e.child=a.child,e.subtreeFlags=0,e.deletions=null,e.memoizedProps=a.memoizedProps,e.memoizedState=a.memoizedState,e.updateQueue=a.updateQueue,e.type=a.type,t=a.dependencies,e.dependencies=t===null?null:{lanes:t.lanes,firstContext:t.firstContext}),e}function ql(e,t,a,n,l,i){var r=0;if(n=e,typeof e=="function")pr(e)&&(r=1);else if(typeof e=="string")r=ym(e,a,_.current)?26:e==="html"||e==="head"||e==="body"?27:5;else e:switch(e){case bt:return e=$e(31,a,t,l),e.elementType=bt,e.lanes=i,e;case Be:return Sa(a.children,l,i,t);case Le:r=8,l|=24;break;case Ae:return e=$e(12,a,t,l|2),e.elementType=Ae,e.lanes=i,e;case W:return e=$e(13,a,t,l),e.elementType=W,e.lanes=i,e;case Qe:return e=$e(19,a,t,l),e.elementType=Qe,e.lanes=i,e;default:if(typeof e=="object"&&e!==null)switch(e.$$typeof){case vt:case Te:r=10;break e;case nt:r=9;break e;case ht:r=11;break e;case Ke:r=14;break e;case Xe:r=16,n=null;break e}r=29,a=Error(f(130,e===null?"null":typeof e,"")),n=null}return t=$e(r,a,t,l),t.elementType=e,t.type=n,t.lanes=i,t}function Sa(e,t,a,n){return e=$e(7,e,n,t),e.lanes=a,e}function gr(e,t,a){return e=$e(6,e,null,t),e.lanes=a,e}function yr(e,t,a){return t=$e(4,e.children!==null?e.children:[],e.key,t),t.lanes=a,t.stateNode={containerInfo:e.containerInfo,pendingChildren:null,implementation:e.implementation},t}var $a=[],Ja=0,Yl=null,Vl=0,st=[],ut=0,wa=null,Dt=1,Bt="";function Aa(e,t){$a[Ja++]=Vl,$a[Ja++]=Yl,Yl=e,Vl=t}function ru(e,t,a){st[ut++]=Dt,st[ut++]=Bt,st[ut++]=wa,wa=e;var n=Dt;e=Bt;var l=32-Ze(n)-1;n&=~(1<<l),a+=1;var i=32-Ze(t)+l;if(30<i){var r=l-l%5;i=(n&(1<<r)-1).toString(32),n>>=r,l-=r,Dt=1<<32-Ze(t)+l|a<<l|n,Bt=i+e}else Dt=1<<i|a<<l|n,Bt=e}function vr(e){e.return!==null&&(Aa(e,1),ru(e,1,0))}function br(e){for(;e===Yl;)Yl=$a[--Ja],$a[Ja]=null,Vl=$a[--Ja],$a[Ja]=null;for(;e===wa;)wa=st[--ut],st[ut]=null,Bt=st[--ut],st[ut]=null,Dt=st[--ut],st[ut]=null}var He=null,he=null,ee=!1,Ea=null,St=!1,Tr=Error(f(519));function Oa(e){var t=Error(f(418,""));throw Ln(rt(t,e)),Tr}function ou(e){var t=e.stateNode,a=e.type,n=e.memoizedProps;switch(t[Ce]=e,t[qe]=n,a){case"dialog":X("cancel",t),X("close",t);break;case"iframe":case"object":case"embed":X("load",t);break;case"video":case"audio":for(a=0;a<ol.length;a++)X(ol[a],t);break;case"source":X("error",t);break;case"img":case"image":case"link":X("error",t),X("load",t);break;case"details":X("toggle",t);break;case"input":X("invalid",t),ws(t,n.value,n.defaultValue,n.checked,n.defaultChecked,n.type,n.name,!0),Ml(t);break;case"select":X("invalid",t);break;case"textarea":X("invalid",t),Es(t,n.value,n.defaultValue,n.children),Ml(t)}a=n.children,typeof a!="string"&&typeof a!="number"&&typeof a!="bigint"||t.textContent===""+a||n.suppressHydrationWarning===!0||Ed(t.textContent,a)?(n.popover!=null&&(X("beforetoggle",t),X("toggle",t)),n.onScroll!=null&&X("scroll",t),n.onScrollEnd!=null&&X("scrollend",t),n.onClick!=null&&(t.onclick=Si),t=!0):t=!1,t||Oa(e)}function su(e){for(He=e.return;He;)switch(He.tag){case 5:case 13:St=!1;return;case 27:case 3:St=!0;return;default:He=He.return}}function zn(e){if(e!==He)return!1;if(!ee)return su(e),ee=!0,!1;var t=e.tag,a;if((a=t!==3&&t!==27)&&((a=t===5)&&(a=e.type,a=!(a!=="form"&&a!=="button")||Lo(e.type,e.memoizedProps)),a=!a),a&&he&&Oa(e),su(e),t===13){if(e=e.memoizedState,e=e!==null?e.dehydrated:null,!e)throw Error(f(317));e:{for(e=e.nextSibling,t=0;e;){if(e.nodeType===8)if(a=e.data,a==="/$"){if(t===0){he=gt(e.nextSibling);break e}t--}else a!=="$"&&a!=="$!"&&a!=="$?"||t++;e=e.nextSibling}he=null}}else t===27?(t=he,oa(e.type)?(e=Vo,Vo=null,he=e):he=t):he=He?gt(e.stateNode.nextSibling):null;return!0}function Un(){he=He=null,ee=!1}function uu(){var e=Ea;return e!==null&&(je===null?je=e:je.push.apply(je,e),Ea=null),e}function Ln(e){Ea===null?Ea=[e]:Ea.push(e)}var Sr=w(null),Ra=null,_t=null;function Xt(e,t,a){O(Sr,t._currentValue),t._currentValue=a}function xt(e){e._currentValue=Sr.current,M(Sr)}function wr(e,t,a){for(;e!==null;){var n=e.alternate;if((e.childLanes&t)!==t?(e.childLanes|=t,n!==null&&(n.childLanes|=t)):n!==null&&(n.childLanes&t)!==t&&(n.childLanes|=t),e===a)break;e=e.return}}function Ar(e,t,a,n){var l=e.child;for(l!==null&&(l.return=e);l!==null;){var i=l.dependencies;if(i!==null){var r=l.child;i=i.firstContext;e:for(;i!==null;){var o=i;i=l;for(var s=0;s<t.length;s++)if(o.context===t[s]){i.lanes|=a,o=i.alternate,o!==null&&(o.lanes|=a),wr(i.return,a,e),n||(r=null);break e}i=o.next}}else if(l.tag===18){if(r=l.return,r===null)throw Error(f(341));r.lanes|=a,i=r.alternate,i!==null&&(i.lanes|=a),wr(r,a,e),r=null}else r=l.child;if(r!==null)r.return=l;else for(r=l;r!==null;){if(r===e){r=null;break}if(l=r.sibling,l!==null){l.return=r.return,r=l;break}r=r.return}l=r}}function Hn(e,t,a,n){e=null;for(var l=t,i=!1;l!==null;){if(!i){if((l.flags&524288)!==0)i=!0;else if((l.flags&262144)!==0)break}if(l.tag===10){var r=l.alternate;if(r===null)throw Error(f(387));if(r=r.memoizedProps,r!==null){var o=l.type;We(l.pendingProps.value,r.value)||(e!==null?e.push(o):e=[o])}}else if(l===Ie.current){if(r=l.alternate,r===null)throw Error(f(387));r.memoizedState.memoizedState!==l.memoizedState.memoizedState&&(e!==null?e.push(hl):e=[hl])}l=l.return}e!==null&&Ar(t,e,a,n),t.flags|=262144}function Gl(e){for(e=e.firstContext;e!==null;){if(!We(e.context._currentValue,e.memoizedValue))return!0;e=e.next}return!1}function Ma(e){Ra=e,_t=null,e=e.dependencies,e!==null&&(e.firstContext=null)}function ze(e){return cu(Ra,e)}function jl(e,t){return Ra===null&&Ma(e),cu(e,t)}function cu(e,t){var a=t._currentValue;if(t={context:t,memoizedValue:a,next:null},_t===null){if(e===null)throw Error(f(308));_t=t,e.dependencies={lanes:0,firstContext:t},e.flags|=524288}else _t=_t.next=t;return a}var gh=typeof AbortController<"u"?AbortController:function(){var e=[],t=this.signal={aborted:!1,addEventListener:function(a,n){e.push(n)}};this.abort=function(){t.aborted=!0,e.forEach(function(a){return a()})}},yh=E.unstable_scheduleCallback,vh=E.unstable_NormalPriority,Se={$$typeof:Te,Consumer:null,Provider:null,_currentValue:null,_currentValue2:null,_threadCount:0};function Er(){return{controller:new gh,data:new Map,refCount:0}}function qn(e){e.refCount--,e.refCount===0&&yh(vh,function(){e.controller.abort()})}var Yn=null,Or=0,Fa=0,en=null;function bh(e,t){if(Yn===null){var a=Yn=[];Or=0,Fa=No(),en={status:"pending",value:void 0,then:function(n){a.push(n)}}}return Or++,t.then(du,du),t}function du(){if(--Or===0&&Yn!==null){en!==null&&(en.status="fulfilled");var e=Yn;Yn=null,Fa=0,en=null;for(var t=0;t<e.length;t++)(0,e[t])()}}function Th(e,t){var a=[],n={status:"pending",value:null,reason:null,then:function(l){a.push(l)}};return e.then(function(){n.status="fulfilled",n.value=t;for(var l=0;l<a.length;l++)(0,a[l])(t)},function(l){for(n.status="rejected",n.reason=l,l=0;l<a.length;l++)(0,a[l])(void 0)}),n}var fu=v.S;v.S=function(e,t){typeof t=="object"&&t!==null&&typeof t.then=="function"&&bh(e,t),fu!==null&&fu(e,t)};var Na=w(null);function Rr(){var e=Na.current;return e!==null?e:se.pooledCache}function Ql(e,t){t===null?O(Na,Na.current):O(Na,t.pool)}function hu(){var e=Rr();return e===null?null:{parent:Se._currentValue,pool:e}}var Vn=Error(f(460)),mu=Error(f(474)),Kl=Error(f(542)),Mr={then:function(){}};function pu(e){return e=e.status,e==="fulfilled"||e==="rejected"}function Xl(){}function gu(e,t,a){switch(a=e[a],a===void 0?e.push(t):a!==t&&(t.then(Xl,Xl),t=a),t.status){case"fulfilled":return t.value;case"rejected":throw e=t.reason,vu(e),e;default:if(typeof t.status=="string")t.then(Xl,Xl);else{if(e=se,e!==null&&100<e.shellSuspendCounter)throw Error(f(482));e=t,e.status="pending",e.then(function(n){if(t.status==="pending"){var l=t;l.status="fulfilled",l.value=n}},function(n){if(t.status==="pending"){var l=t;l.status="rejected",l.reason=n}})}switch(t.status){case"fulfilled":return t.value;case"rejected":throw e=t.reason,vu(e),e}throw Gn=t,Vn}}var Gn=null;function yu(){if(Gn===null)throw Error(f(459));var e=Gn;return Gn=null,e}function vu(e){if(e===Vn||e===Kl)throw Error(f(483))}var It=!1;function Nr(e){e.updateQueue={baseState:e.memoizedState,firstBaseUpdate:null,lastBaseUpdate:null,shared:{pending:null,lanes:0,hiddenCallbacks:null},callbacks:null}}function kr(e,t){e=e.updateQueue,t.updateQueue===e&&(t.updateQueue={baseState:e.baseState,firstBaseUpdate:e.firstBaseUpdate,lastBaseUpdate:e.lastBaseUpdate,shared:e.shared,callbacks:null})}function Pt(e){return{lane:e,tag:0,payload:null,callback:null,next:null}}function Zt(e,t,a){var n=e.updateQueue;if(n===null)return null;if(n=n.shared,(te&2)!==0){var l=n.pending;return l===null?t.next=t:(t.next=l.next,l.next=t),n.pending=t,t=Hl(e),lu(e,null,a),t}return Ll(e,n,t,a),Hl(e)}function jn(e,t,a){if(t=t.updateQueue,t!==null&&(t=t.shared,(a&4194048)!==0)){var n=t.lanes;n&=e.pendingLanes,a|=n,t.lanes=a,ds(e,a)}}function Dr(e,t){var a=e.updateQueue,n=e.alternate;if(n!==null&&(n=n.updateQueue,a===n)){var l=null,i=null;if(a=a.firstBaseUpdate,a!==null){do{var r={lane:a.lane,tag:a.tag,payload:a.payload,callback:null,next:null};i===null?l=i=r:i=i.next=r,a=a.next}while(a!==null);i===null?l=i=t:i=i.next=t}else l=i=t;a={baseState:n.baseState,firstBaseUpdate:l,lastBaseUpdate:i,shared:n.shared,callbacks:n.callbacks},e.updateQueue=a;return}e=a.lastBaseUpdate,e===null?a.firstBaseUpdate=t:e.next=t,a.lastBaseUpdate=t}var Br=!1;function Qn(){if(Br){var e=en;if(e!==null)throw e}}function Kn(e,t,a,n){Br=!1;var l=e.updateQueue;It=!1;var i=l.firstBaseUpdate,r=l.lastBaseUpdate,o=l.shared.pending;if(o!==null){l.shared.pending=null;var s=o,m=s.next;s.next=null,r===null?i=m:r.next=m,r=s;var y=e.alternate;y!==null&&(y=y.updateQueue,o=y.lastBaseUpdate,o!==r&&(o===null?y.firstBaseUpdate=m:o.next=m,y.lastBaseUpdate=s))}if(i!==null){var T=l.baseState;r=0,y=m=s=null,o=i;do{var p=o.lane&-536870913,g=p!==o.lane;if(g?(P&p)===p:(n&p)===p){p!==0&&p===Fa&&(Br=!0),y!==null&&(y=y.next={lane:0,tag:o.tag,payload:o.payload,callback:null,next:null});e:{var L=e,x=o;p=t;var ie=a;switch(x.tag){case 1:if(L=x.payload,typeof L=="function"){T=L.call(ie,T,p);break e}T=L;break e;case 3:L.flags=L.flags&-65537|128;case 0:if(L=x.payload,p=typeof L=="function"?L.call(ie,T,p):L,p==null)break e;T=D({},T,p);break e;case 2:It=!0}}p=o.callback,p!==null&&(e.flags|=64,g&&(e.flags|=8192),g=l.callbacks,g===null?l.callbacks=[p]:g.push(p))}else g={lane:p,tag:o.tag,payload:o.payload,callback:o.callback,next:null},y===null?(m=y=g,s=T):y=y.next=g,r|=p;if(o=o.next,o===null){if(o=l.shared.pending,o===null)break;g=o,o=g.next,g.next=null,l.lastBaseUpdate=g,l.shared.pending=null}}while(!0);y===null&&(s=T),l.baseState=s,l.firstBaseUpdate=m,l.lastBaseUpdate=y,i===null&&(l.shared.lanes=0),na|=r,e.lanes=r,e.memoizedState=T}}function bu(e,t){if(typeof e!="function")throw Error(f(191,e));e.call(t)}function Tu(e,t){var a=e.callbacks;if(a!==null)for(e.callbacks=null,e=0;e<a.length;e++)bu(a[e],t)}var tn=w(null),Il=w(0);function Su(e,t){e=Yt,O(Il,e),O(tn,t),Yt=e|t.baseLanes}function _r(){O(Il,Yt),O(tn,tn.current)}function xr(){Yt=Il.current,M(tn),M(Il)}var Wt=0,j=null,ne=null,ve=null,Pl=!1,an=!1,ka=!1,Zl=0,Xn=0,nn=null,Sh=0;function pe(){throw Error(f(321))}function Cr(e,t){if(t===null)return!1;for(var a=0;a<t.length&&a<e.length;a++)if(!We(e[a],t[a]))return!1;return!0}function zr(e,t,a,n,l,i){return Wt=i,j=t,t.memoizedState=null,t.updateQueue=null,t.lanes=0,v.H=e===null||e.memoizedState===null?lc:ic,ka=!1,i=a(n,l),ka=!1,an&&(i=Au(t,a,n,l)),wu(e),i}function wu(e){v.H=ti;var t=ne!==null&&ne.next!==null;if(Wt=0,ve=ne=j=null,Pl=!1,Xn=0,nn=null,t)throw Error(f(300));e===null||Oe||(e=e.dependencies,e!==null&&Gl(e)&&(Oe=!0))}function Au(e,t,a,n){j=e;var l=0;do{if(an&&(nn=null),Xn=0,an=!1,25<=l)throw Error(f(301));if(l+=1,ve=ne=null,e.updateQueue!=null){var i=e.updateQueue;i.lastEffect=null,i.events=null,i.stores=null,i.memoCache!=null&&(i.memoCache.index=0)}v.H=Nh,i=t(a,n)}while(an);return i}function wh(){var e=v.H,t=e.useState()[0];return t=typeof t.then=="function"?In(t):t,e=e.useState()[0],(ne!==null?ne.memoizedState:null)!==e&&(j.flags|=1024),t}function Ur(){var e=Zl!==0;return Zl=0,e}function Lr(e,t,a){t.updateQueue=e.updateQueue,t.flags&=-2053,e.lanes&=~a}function Hr(e){if(Pl){for(e=e.memoizedState;e!==null;){var t=e.queue;t!==null&&(t.pending=null),e=e.next}Pl=!1}Wt=0,ve=ne=j=null,an=!1,Xn=Zl=0,nn=null}function Ve(){var e={memoizedState:null,baseState:null,baseQueue:null,queue:null,next:null};return ve===null?j.memoizedState=ve=e:ve=ve.next=e,ve}function be(){if(ne===null){var e=j.alternate;e=e!==null?e.memoizedState:null}else e=ne.next;var t=ve===null?j.memoizedState:ve.next;if(t!==null)ve=t,ne=e;else{if(e===null)throw j.alternate===null?Error(f(467)):Error(f(310));ne=e,e={memoizedState:ne.memoizedState,baseState:ne.baseState,baseQueue:ne.baseQueue,queue:ne.queue,next:null},ve===null?j.memoizedState=ve=e:ve=ve.next=e}return ve}function qr(){return{lastEffect:null,events:null,stores:null,memoCache:null}}function In(e){var t=Xn;return Xn+=1,nn===null&&(nn=[]),e=gu(nn,e,t),t=j,(ve===null?t.memoizedState:ve.next)===null&&(t=t.alternate,v.H=t===null||t.memoizedState===null?lc:ic),e}function Wl(e){if(e!==null&&typeof e=="object"){if(typeof e.then=="function")return In(e);if(e.$$typeof===Te)return ze(e)}throw Error(f(438,String(e)))}function Yr(e){var t=null,a=j.updateQueue;if(a!==null&&(t=a.memoCache),t==null){var n=j.alternate;n!==null&&(n=n.updateQueue,n!==null&&(n=n.memoCache,n!=null&&(t={data:n.data.map(function(l){return l.slice()}),index:0})))}if(t==null&&(t={data:[],index:0}),a===null&&(a=qr(),j.updateQueue=a),a.memoCache=t,a=t.data[t.index],a===void 0)for(a=t.data[t.index]=Array(e),n=0;n<e;n++)a[n]=Ca;return t.index++,a}function Ct(e,t){return typeof t=="function"?t(e):t}function $l(e){var t=be();return Vr(t,ne,e)}function Vr(e,t,a){var n=e.queue;if(n===null)throw Error(f(311));n.lastRenderedReducer=a;var l=e.baseQueue,i=n.pending;if(i!==null){if(l!==null){var r=l.next;l.next=i.next,i.next=r}t.baseQueue=l=i,n.pending=null}if(i=e.baseState,l===null)e.memoizedState=i;else{t=l.next;var o=r=null,s=null,m=t,y=!1;do{var T=m.lane&-536870913;if(T!==m.lane?(P&T)===T:(Wt&T)===T){var p=m.revertLane;if(p===0)s!==null&&(s=s.next={lane:0,revertLane:0,action:m.action,hasEagerState:m.hasEagerState,eagerState:m.eagerState,next:null}),T===Fa&&(y=!0);else if((Wt&p)===p){m=m.next,p===Fa&&(y=!0);continue}else T={lane:0,revertLane:m.revertLane,action:m.action,hasEagerState:m.hasEagerState,eagerState:m.eagerState,next:null},s===null?(o=s=T,r=i):s=s.next=T,j.lanes|=p,na|=p;T=m.action,ka&&a(i,T),i=m.hasEagerState?m.eagerState:a(i,T)}else p={lane:T,revertLane:m.revertLane,action:m.action,hasEagerState:m.hasEagerState,eagerState:m.eagerState,next:null},s===null?(o=s=p,r=i):s=s.next=p,j.lanes|=T,na|=T;m=m.next}while(m!==null&&m!==t);if(s===null?r=i:s.next=o,!We(i,e.memoizedState)&&(Oe=!0,y&&(a=en,a!==null)))throw a;e.memoizedState=i,e.baseState=r,e.baseQueue=s,n.lastRenderedState=i}return l===null&&(n.lanes=0),[e.memoizedState,n.dispatch]}function Gr(e){var t=be(),a=t.queue;if(a===null)throw Error(f(311));a.lastRenderedReducer=e;var n=a.dispatch,l=a.pending,i=t.memoizedState;if(l!==null){a.pending=null;var r=l=l.next;do i=e(i,r.action),r=r.next;while(r!==l);We(i,t.memoizedState)||(Oe=!0),t.memoizedState=i,t.baseQueue===null&&(t.baseState=i),a.lastRenderedState=i}return[i,n]}function Eu(e,t,a){var n=j,l=be(),i=ee;if(i){if(a===void 0)throw Error(f(407));a=a()}else a=t();var r=!We((ne||l).memoizedState,a);r&&(l.memoizedState=a,Oe=!0),l=l.queue;var o=Mu.bind(null,n,l,e);if(Pn(2048,8,o,[e]),l.getSnapshot!==t||r||ve!==null&&ve.memoizedState.tag&1){if(n.flags|=2048,ln(9,Jl(),Ru.bind(null,n,l,a,t),null),se===null)throw Error(f(349));i||(Wt&124)!==0||Ou(n,t,a)}return a}function Ou(e,t,a){e.flags|=16384,e={getSnapshot:t,value:a},t=j.updateQueue,t===null?(t=qr(),j.updateQueue=t,t.stores=[e]):(a=t.stores,a===null?t.stores=[e]:a.push(e))}function Ru(e,t,a,n){t.value=a,t.getSnapshot=n,Nu(t)&&ku(e)}function Mu(e,t,a){return a(function(){Nu(t)&&ku(e)})}function Nu(e){var t=e.getSnapshot;e=e.value;try{var a=t();return!We(e,a)}catch{return!0}}function ku(e){var t=Za(e,2);t!==null&&at(t,e,2)}function jr(e){var t=Ve();if(typeof e=="function"){var a=e;if(e=a(),ka){jt(!0);try{a()}finally{jt(!1)}}}return t.memoizedState=t.baseState=e,t.queue={pending:null,lanes:0,dispatch:null,lastRenderedReducer:Ct,lastRenderedState:e},t}function Du(e,t,a,n){return e.baseState=a,Vr(e,ne,typeof n=="function"?n:Ct)}function Ah(e,t,a,n,l){if(ei(e))throw Error(f(485));if(e=t.action,e!==null){var i={payload:l,action:e,next:null,isTransition:!0,status:"pending",value:null,reason:null,listeners:[],then:function(r){i.listeners.push(r)}};v.T!==null?a(!0):i.isTransition=!1,n(i),a=t.pending,a===null?(i.next=t.pending=i,Bu(t,i)):(i.next=a.next,t.pending=a.next=i)}}function Bu(e,t){var a=t.action,n=t.payload,l=e.state;if(t.isTransition){var i=v.T,r={};v.T=r;try{var o=a(l,n),s=v.S;s!==null&&s(r,o),_u(e,t,o)}catch(m){Qr(e,t,m)}finally{v.T=i}}else try{i=a(l,n),_u(e,t,i)}catch(m){Qr(e,t,m)}}function _u(e,t,a){a!==null&&typeof a=="object"&&typeof a.then=="function"?a.then(function(n){xu(e,t,n)},function(n){return Qr(e,t,n)}):xu(e,t,a)}function xu(e,t,a){t.status="fulfilled",t.value=a,Cu(t),e.state=a,t=e.pending,t!==null&&(a=t.next,a===t?e.pending=null:(a=a.next,t.next=a,Bu(e,a)))}function Qr(e,t,a){var n=e.pending;if(e.pending=null,n!==null){n=n.next;do t.status="rejected",t.reason=a,Cu(t),t=t.next;while(t!==n)}e.action=null}function Cu(e){e=e.listeners;for(var t=0;t<e.length;t++)(0,e[t])()}function zu(e,t){return t}function Uu(e,t){if(ee){var a=se.formState;if(a!==null){e:{var n=j;if(ee){if(he){t:{for(var l=he,i=St;l.nodeType!==8;){if(!i){l=null;break t}if(l=gt(l.nextSibling),l===null){l=null;break t}}i=l.data,l=i==="F!"||i==="F"?l:null}if(l){he=gt(l.nextSibling),n=l.data==="F!";break e}}Oa(n)}n=!1}n&&(t=a[0])}}return a=Ve(),a.memoizedState=a.baseState=t,n={pending:null,lanes:0,dispatch:null,lastRenderedReducer:zu,lastRenderedState:t},a.queue=n,a=tc.bind(null,j,n),n.dispatch=a,n=jr(!1),i=Zr.bind(null,j,!1,n.queue),n=Ve(),l={state:t,dispatch:null,action:e,pending:null},n.queue=l,a=Ah.bind(null,j,l,i,a),l.dispatch=a,n.memoizedState=e,[t,a,!1]}function Lu(e){var t=be();return Hu(t,ne,e)}function Hu(e,t,a){if(t=Vr(e,t,zu)[0],e=$l(Ct)[0],typeof t=="object"&&t!==null&&typeof t.then=="function")try{var n=In(t)}catch(r){throw r===Vn?Kl:r}else n=t;t=be();var l=t.queue,i=l.dispatch;return a!==t.memoizedState&&(j.flags|=2048,ln(9,Jl(),Eh.bind(null,l,a),null)),[n,i,e]}function Eh(e,t){e.action=t}function qu(e){var t=be(),a=ne;if(a!==null)return Hu(t,a,e);be(),t=t.memoizedState,a=be();var n=a.queue.dispatch;return a.memoizedState=e,[t,n,!1]}function ln(e,t,a,n){return e={tag:e,create:a,deps:n,inst:t,next:null},t=j.updateQueue,t===null&&(t=qr(),j.updateQueue=t),a=t.lastEffect,a===null?t.lastEffect=e.next=e:(n=a.next,a.next=e,e.next=n,t.lastEffect=e),e}function Jl(){return{destroy:void 0,resource:void 0}}function Yu(){return be().memoizedState}function Fl(e,t,a,n){var l=Ve();n=n===void 0?null:n,j.flags|=e,l.memoizedState=ln(1|t,Jl(),a,n)}function Pn(e,t,a,n){var l=be();n=n===void 0?null:n;var i=l.memoizedState.inst;ne!==null&&n!==null&&Cr(n,ne.memoizedState.deps)?l.memoizedState=ln(t,i,a,n):(j.flags|=e,l.memoizedState=ln(1|t,i,a,n))}function Vu(e,t){Fl(8390656,8,e,t)}function Gu(e,t){Pn(2048,8,e,t)}function ju(e,t){return Pn(4,2,e,t)}function Qu(e,t){return Pn(4,4,e,t)}function Ku(e,t){if(typeof t=="function"){e=e();var a=t(e);return function(){typeof a=="function"?a():t(null)}}if(t!=null)return e=e(),t.current=e,function(){t.current=null}}function Xu(e,t,a){a=a!=null?a.concat([e]):null,Pn(4,4,Ku.bind(null,t,e),a)}function Kr(){}function Iu(e,t){var a=be();t=t===void 0?null:t;var n=a.memoizedState;return t!==null&&Cr(t,n[1])?n[0]:(a.memoizedState=[e,t],e)}function Pu(e,t){var a=be();t=t===void 0?null:t;var n=a.memoizedState;if(t!==null&&Cr(t,n[1]))return n[0];if(n=e(),ka){jt(!0);try{e()}finally{jt(!1)}}return a.memoizedState=[n,t],n}function Xr(e,t,a){return a===void 0||(Wt&1073741824)!==0?e.memoizedState=t:(e.memoizedState=a,e=$c(),j.lanes|=e,na|=e,a)}function Zu(e,t,a,n){return We(a,t)?a:tn.current!==null?(e=Xr(e,a,n),We(e,t)||(Oe=!0),e):(Wt&42)===0?(Oe=!0,e.memoizedState=a):(e=$c(),j.lanes|=e,na|=e,t)}function Wu(e,t,a,n,l){var i=R.p;R.p=i!==0&&8>i?i:8;var r=v.T,o={};v.T=o,Zr(e,!1,t,a);try{var s=l(),m=v.S;if(m!==null&&m(o,s),s!==null&&typeof s=="object"&&typeof s.then=="function"){var y=Th(s,n);Zn(e,t,y,tt(e))}else Zn(e,t,n,tt(e))}catch(T){Zn(e,t,{then:function(){},status:"rejected",reason:T},tt())}finally{R.p=i,v.T=r}}function Oh(){}function Ir(e,t,a,n){if(e.tag!==5)throw Error(f(476));var l=$u(e).queue;Wu(e,l,t,z,a===null?Oh:function(){return Ju(e),a(n)})}function $u(e){var t=e.memoizedState;if(t!==null)return t;t={memoizedState:z,baseState:z,baseQueue:null,queue:{pending:null,lanes:0,dispatch:null,lastRenderedReducer:Ct,lastRenderedState:z},next:null};var a={};return t.next={memoizedState:a,baseState:a,baseQueue:null,queue:{pending:null,lanes:0,dispatch:null,lastRenderedReducer:Ct,lastRenderedState:a},next:null},e.memoizedState=t,e=e.alternate,e!==null&&(e.memoizedState=t),t}function Ju(e){var t=$u(e).next.queue;Zn(e,t,{},tt())}function Pr(){return ze(hl)}function Fu(){return be().memoizedState}function ec(){return be().memoizedState}function Rh(e){for(var t=e.return;t!==null;){switch(t.tag){case 24:case 3:var a=tt();e=Pt(a);var n=Zt(t,e,a);n!==null&&(at(n,t,a),jn(n,t,a)),t={cache:Er()},e.payload=t;return}t=t.return}}function Mh(e,t,a){var n=tt();a={lane:n,revertLane:0,action:a,hasEagerState:!1,eagerState:null,next:null},ei(e)?ac(t,a):(a=mr(e,t,a,n),a!==null&&(at(a,e,n),nc(a,t,n)))}function tc(e,t,a){var n=tt();Zn(e,t,a,n)}function Zn(e,t,a,n){var l={lane:n,revertLane:0,action:a,hasEagerState:!1,eagerState:null,next:null};if(ei(e))ac(t,l);else{var i=e.alternate;if(e.lanes===0&&(i===null||i.lanes===0)&&(i=t.lastRenderedReducer,i!==null))try{var r=t.lastRenderedState,o=i(r,a);if(l.hasEagerState=!0,l.eagerState=o,We(o,r))return Ll(e,t,l,0),se===null&&Ul(),!1}catch{}if(a=mr(e,t,l,n),a!==null)return at(a,e,n),nc(a,t,n),!0}return!1}function Zr(e,t,a,n){if(n={lane:2,revertLane:No(),action:n,hasEagerState:!1,eagerState:null,next:null},ei(e)){if(t)throw Error(f(479))}else t=mr(e,a,n,2),t!==null&&at(t,e,2)}function ei(e){var t=e.alternate;return e===j||t!==null&&t===j}function ac(e,t){an=Pl=!0;var a=e.pending;a===null?t.next=t:(t.next=a.next,a.next=t),e.pending=t}function nc(e,t,a){if((a&4194048)!==0){var n=t.lanes;n&=e.pendingLanes,a|=n,t.lanes=a,ds(e,a)}}var ti={readContext:ze,use:Wl,useCallback:pe,useContext:pe,useEffect:pe,useImperativeHandle:pe,useLayoutEffect:pe,useInsertionEffect:pe,useMemo:pe,useReducer:pe,useRef:pe,useState:pe,useDebugValue:pe,useDeferredValue:pe,useTransition:pe,useSyncExternalStore:pe,useId:pe,useHostTransitionStatus:pe,useFormState:pe,useActionState:pe,useOptimistic:pe,useMemoCache:pe,useCacheRefresh:pe},lc={readContext:ze,use:Wl,useCallback:function(e,t){return Ve().memoizedState=[e,t===void 0?null:t],e},useContext:ze,useEffect:Vu,useImperativeHandle:function(e,t,a){a=a!=null?a.concat([e]):null,Fl(4194308,4,Ku.bind(null,t,e),a)},useLayoutEffect:function(e,t){return Fl(4194308,4,e,t)},useInsertionEffect:function(e,t){Fl(4,2,e,t)},useMemo:function(e,t){var a=Ve();t=t===void 0?null:t;var n=e();if(ka){jt(!0);try{e()}finally{jt(!1)}}return a.memoizedState=[n,t],n},useReducer:function(e,t,a){var n=Ve();if(a!==void 0){var l=a(t);if(ka){jt(!0);try{a(t)}finally{jt(!1)}}}else l=t;return n.memoizedState=n.baseState=l,e={pending:null,lanes:0,dispatch:null,lastRenderedReducer:e,lastRenderedState:l},n.queue=e,e=e.dispatch=Mh.bind(null,j,e),[n.memoizedState,e]},useRef:function(e){var t=Ve();return e={current:e},t.memoizedState=e},useState:function(e){e=jr(e);var t=e.queue,a=tc.bind(null,j,t);return t.dispatch=a,[e.memoizedState,a]},useDebugValue:Kr,useDeferredValue:function(e,t){var a=Ve();return Xr(a,e,t)},useTransition:function(){var e=jr(!1);return e=Wu.bind(null,j,e.queue,!0,!1),Ve().memoizedState=e,[!1,e]},useSyncExternalStore:function(e,t,a){var n=j,l=Ve();if(ee){if(a===void 0)throw Error(f(407));a=a()}else{if(a=t(),se===null)throw Error(f(349));(P&124)!==0||Ou(n,t,a)}l.memoizedState=a;var i={value:a,getSnapshot:t};return l.queue=i,Vu(Mu.bind(null,n,i,e),[e]),n.flags|=2048,ln(9,Jl(),Ru.bind(null,n,i,a,t),null),a},useId:function(){var e=Ve(),t=se.identifierPrefix;if(ee){var a=Bt,n=Dt;a=(n&~(1<<32-Ze(n)-1)).toString(32)+a,t="«"+t+"R"+a,a=Zl++,0<a&&(t+="H"+a.toString(32)),t+="»"}else a=Sh++,t="«"+t+"r"+a.toString(32)+"»";return e.memoizedState=t},useHostTransitionStatus:Pr,useFormState:Uu,useActionState:Uu,useOptimistic:function(e){var t=Ve();t.memoizedState=t.baseState=e;var a={pending:null,lanes:0,dispatch:null,lastRenderedReducer:null,lastRenderedState:null};return t.queue=a,t=Zr.bind(null,j,!0,a),a.dispatch=t,[e,t]},useMemoCache:Yr,useCacheRefresh:function(){return Ve().memoizedState=Rh.bind(null,j)}},ic={readContext:ze,use:Wl,useCallback:Iu,useContext:ze,useEffect:Gu,useImperativeHandle:Xu,useInsertionEffect:ju,useLayoutEffect:Qu,useMemo:Pu,useReducer:$l,useRef:Yu,useState:function(){return $l(Ct)},useDebugValue:Kr,useDeferredValue:function(e,t){var a=be();return Zu(a,ne.memoizedState,e,t)},useTransition:function(){var e=$l(Ct)[0],t=be().memoizedState;return[typeof e=="boolean"?e:In(e),t]},useSyncExternalStore:Eu,useId:Fu,useHostTransitionStatus:Pr,useFormState:Lu,useActionState:Lu,useOptimistic:function(e,t){var a=be();return Du(a,ne,e,t)},useMemoCache:Yr,useCacheRefresh:ec},Nh={readContext:ze,use:Wl,useCallback:Iu,useContext:ze,useEffect:Gu,useImperativeHandle:Xu,useInsertionEffect:ju,useLayoutEffect:Qu,useMemo:Pu,useReducer:Gr,useRef:Yu,useState:function(){return Gr(Ct)},useDebugValue:Kr,useDeferredValue:function(e,t){var a=be();return ne===null?Xr(a,e,t):Zu(a,ne.memoizedState,e,t)},useTransition:function(){var e=Gr(Ct)[0],t=be().memoizedState;return[typeof e=="boolean"?e:In(e),t]},useSyncExternalStore:Eu,useId:Fu,useHostTransitionStatus:Pr,useFormState:qu,useActionState:qu,useOptimistic:function(e,t){var a=be();return ne!==null?Du(a,ne,e,t):(a.baseState=e,[e,a.queue.dispatch])},useMemoCache:Yr,useCacheRefresh:ec},rn=null,Wn=0;function ai(e){var t=Wn;return Wn+=1,rn===null&&(rn=[]),gu(rn,e,t)}function $n(e,t){t=t.props.ref,e.ref=t!==void 0?t:null}function ni(e,t){throw t.$$typeof===Z?Error(f(525)):(e=Object.prototype.toString.call(t),Error(f(31,e==="[object Object]"?"object with keys {"+Object.keys(t).join(", ")+"}":e)))}function rc(e){var t=e._init;return t(e._payload)}function oc(e){function t(d,c){if(e){var h=d.deletions;h===null?(d.deletions=[c],d.flags|=16):h.push(c)}}function a(d,c){if(!e)return null;for(;c!==null;)t(d,c),c=c.sibling;return null}function n(d){for(var c=new Map;d!==null;)d.key!==null?c.set(d.key,d):c.set(d.index,d),d=d.sibling;return c}function l(d,c){return d=kt(d,c),d.index=0,d.sibling=null,d}function i(d,c,h){return d.index=h,e?(h=d.alternate,h!==null?(h=h.index,h<c?(d.flags|=67108866,c):h):(d.flags|=67108866,c)):(d.flags|=1048576,c)}function r(d){return e&&d.alternate===null&&(d.flags|=67108866),d}function o(d,c,h,b){return c===null||c.tag!==6?(c=gr(h,d.mode,b),c.return=d,c):(c=l(c,h),c.return=d,c)}function s(d,c,h,b){var N=h.type;return N===Be?y(d,c,h.props.children,b,h.key):c!==null&&(c.elementType===N||typeof N=="object"&&N!==null&&N.$$typeof===Xe&&rc(N)===c.type)?(c=l(c,h.props),$n(c,h),c.return=d,c):(c=ql(h.type,h.key,h.props,null,d.mode,b),$n(c,h),c.return=d,c)}function m(d,c,h,b){return c===null||c.tag!==4||c.stateNode.containerInfo!==h.containerInfo||c.stateNode.implementation!==h.implementation?(c=yr(h,d.mode,b),c.return=d,c):(c=l(c,h.children||[]),c.return=d,c)}function y(d,c,h,b,N){return c===null||c.tag!==7?(c=Sa(h,d.mode,b,N),c.return=d,c):(c=l(c,h),c.return=d,c)}function T(d,c,h){if(typeof c=="string"&&c!==""||typeof c=="number"||typeof c=="bigint")return c=gr(""+c,d.mode,h),c.return=d,c;if(typeof c=="object"&&c!==null){switch(c.$$typeof){case F:return h=ql(c.type,c.key,c.props,null,d.mode,h),$n(h,c),h.return=d,h;case ye:return c=yr(c,d.mode,h),c.return=d,c;case Xe:var b=c._init;return c=b(c._payload),T(d,c,h)}if(xe(c)||_e(c))return c=Sa(c,d.mode,h,null),c.return=d,c;if(typeof c.then=="function")return T(d,ai(c),h);if(c.$$typeof===Te)return T(d,jl(d,c),h);ni(d,c)}return null}function p(d,c,h,b){var N=c!==null?c.key:null;if(typeof h=="string"&&h!==""||typeof h=="number"||typeof h=="bigint")return N!==null?null:o(d,c,""+h,b);if(typeof h=="object"&&h!==null){switch(h.$$typeof){case F:return h.key===N?s(d,c,h,b):null;case ye:return h.key===N?m(d,c,h,b):null;case Xe:return N=h._init,h=N(h._payload),p(d,c,h,b)}if(xe(h)||_e(h))return N!==null?null:y(d,c,h,b,null);if(typeof h.then=="function")return p(d,c,ai(h),b);if(h.$$typeof===Te)return p(d,c,jl(d,h),b);ni(d,h)}return null}function g(d,c,h,b,N){if(typeof b=="string"&&b!==""||typeof b=="number"||typeof b=="bigint")return d=d.get(h)||null,o(c,d,""+b,N);if(typeof b=="object"&&b!==null){switch(b.$$typeof){case F:return d=d.get(b.key===null?h:b.key)||null,s(c,d,b,N);case ye:return d=d.get(b.key===null?h:b.key)||null,m(c,d,b,N);case Xe:var Q=b._init;return b=Q(b._payload),g(d,c,h,b,N)}if(xe(b)||_e(b))return d=d.get(h)||null,y(c,d,b,N,null);if(typeof b.then=="function")return g(d,c,h,ai(b),N);if(b.$$typeof===Te)return g(d,c,h,jl(c,b),N);ni(c,b)}return null}function L(d,c,h,b){for(var N=null,Q=null,k=c,C=c=0,Me=null;k!==null&&C<h.length;C++){k.index>C?(Me=k,k=null):Me=k.sibling;var J=p(d,k,h[C],b);if(J===null){k===null&&(k=Me);break}e&&k&&J.alternate===null&&t(d,k),c=i(J,c,C),Q===null?N=J:Q.sibling=J,Q=J,k=Me}if(C===h.length)return a(d,k),ee&&Aa(d,C),N;if(k===null){for(;C<h.length;C++)k=T(d,h[C],b),k!==null&&(c=i(k,c,C),Q===null?N=k:Q.sibling=k,Q=k);return ee&&Aa(d,C),N}for(k=n(k);C<h.length;C++)Me=g(k,d,C,h[C],b),Me!==null&&(e&&Me.alternate!==null&&k.delete(Me.key===null?C:Me.key),c=i(Me,c,C),Q===null?N=Me:Q.sibling=Me,Q=Me);return e&&k.forEach(function(fa){return t(d,fa)}),ee&&Aa(d,C),N}function x(d,c,h,b){if(h==null)throw Error(f(151));for(var N=null,Q=null,k=c,C=c=0,Me=null,J=h.next();k!==null&&!J.done;C++,J=h.next()){k.index>C?(Me=k,k=null):Me=k.sibling;var fa=p(d,k,J.value,b);if(fa===null){k===null&&(k=Me);break}e&&k&&fa.alternate===null&&t(d,k),c=i(fa,c,C),Q===null?N=fa:Q.sibling=fa,Q=fa,k=Me}if(J.done)return a(d,k),ee&&Aa(d,C),N;if(k===null){for(;!J.done;C++,J=h.next())J=T(d,J.value,b),J!==null&&(c=i(J,c,C),Q===null?N=J:Q.sibling=J,Q=J);return ee&&Aa(d,C),N}for(k=n(k);!J.done;C++,J=h.next())J=g(k,d,C,J.value,b),J!==null&&(e&&J.alternate!==null&&k.delete(J.key===null?C:J.key),c=i(J,c,C),Q===null?N=J:Q.sibling=J,Q=J);return e&&k.forEach(function(km){return t(d,km)}),ee&&Aa(d,C),N}function ie(d,c,h,b){if(typeof h=="object"&&h!==null&&h.type===Be&&h.key===null&&(h=h.props.children),typeof h=="object"&&h!==null){switch(h.$$typeof){case F:e:{for(var N=h.key;c!==null;){if(c.key===N){if(N=h.type,N===Be){if(c.tag===7){a(d,c.sibling),b=l(c,h.props.children),b.return=d,d=b;break e}}else if(c.elementType===N||typeof N=="object"&&N!==null&&N.$$typeof===Xe&&rc(N)===c.type){a(d,c.sibling),b=l(c,h.props),$n(b,h),b.return=d,d=b;break e}a(d,c);break}else t(d,c);c=c.sibling}h.type===Be?(b=Sa(h.props.children,d.mode,b,h.key),b.return=d,d=b):(b=ql(h.type,h.key,h.props,null,d.mode,b),$n(b,h),b.return=d,d=b)}return r(d);case ye:e:{for(N=h.key;c!==null;){if(c.key===N)if(c.tag===4&&c.stateNode.containerInfo===h.containerInfo&&c.stateNode.implementation===h.implementation){a(d,c.sibling),b=l(c,h.children||[]),b.return=d,d=b;break e}else{a(d,c);break}else t(d,c);c=c.sibling}b=yr(h,d.mode,b),b.return=d,d=b}return r(d);case Xe:return N=h._init,h=N(h._payload),ie(d,c,h,b)}if(xe(h))return L(d,c,h,b);if(_e(h)){if(N=_e(h),typeof N!="function")throw Error(f(150));return h=N.call(h),x(d,c,h,b)}if(typeof h.then=="function")return ie(d,c,ai(h),b);if(h.$$typeof===Te)return ie(d,c,jl(d,h),b);ni(d,h)}return typeof h=="string"&&h!==""||typeof h=="number"||typeof h=="bigint"?(h=""+h,c!==null&&c.tag===6?(a(d,c.sibling),b=l(c,h),b.return=d,d=b):(a(d,c),b=gr(h,d.mode,b),b.return=d,d=b),r(d)):a(d,c)}return function(d,c,h,b){try{Wn=0;var N=ie(d,c,h,b);return rn=null,N}catch(k){if(k===Vn||k===Kl)throw k;var Q=$e(29,k,null,d.mode);return Q.lanes=b,Q.return=d,Q}}}var on=oc(!0),sc=oc(!1),ct=w(null),wt=null;function $t(e){var t=e.alternate;O(we,we.current&1),O(ct,e),wt===null&&(t===null||tn.current!==null||t.memoizedState!==null)&&(wt=e)}function uc(e){if(e.tag===22){if(O(we,we.current),O(ct,e),wt===null){var t=e.alternate;t!==null&&t.memoizedState!==null&&(wt=e)}}else Jt()}function Jt(){O(we,we.current),O(ct,ct.current)}function zt(e){M(ct),wt===e&&(wt=null),M(we)}var we=w(0);function li(e){for(var t=e;t!==null;){if(t.tag===13){var a=t.memoizedState;if(a!==null&&(a=a.dehydrated,a===null||a.data==="$?"||Yo(a)))return t}else if(t.tag===19&&t.memoizedProps.revealOrder!==void 0){if((t.flags&128)!==0)return t}else if(t.child!==null){t.child.return=t,t=t.child;continue}if(t===e)break;for(;t.sibling===null;){if(t.return===null||t.return===e)return null;t=t.return}t.sibling.return=t.return,t=t.sibling}return null}function Wr(e,t,a,n){t=e.memoizedState,a=a(n,t),a=a==null?t:D({},t,a),e.memoizedState=a,e.lanes===0&&(e.updateQueue.baseState=a)}var $r={enqueueSetState:function(e,t,a){e=e._reactInternals;var n=tt(),l=Pt(n);l.payload=t,a!=null&&(l.callback=a),t=Zt(e,l,n),t!==null&&(at(t,e,n),jn(t,e,n))},enqueueReplaceState:function(e,t,a){e=e._reactInternals;var n=tt(),l=Pt(n);l.tag=1,l.payload=t,a!=null&&(l.callback=a),t=Zt(e,l,n),t!==null&&(at(t,e,n),jn(t,e,n))},enqueueForceUpdate:function(e,t){e=e._reactInternals;var a=tt(),n=Pt(a);n.tag=2,t!=null&&(n.callback=t),t=Zt(e,n,a),t!==null&&(at(t,e,a),jn(t,e,a))}};function cc(e,t,a,n,l,i,r){return e=e.stateNode,typeof e.shouldComponentUpdate=="function"?e.shouldComponentUpdate(n,i,r):t.prototype&&t.prototype.isPureReactComponent?!xn(a,n)||!xn(l,i):!0}function dc(e,t,a,n){e=t.state,typeof t.componentWillReceiveProps=="function"&&t.componentWillReceiveProps(a,n),typeof t.UNSAFE_componentWillReceiveProps=="function"&&t.UNSAFE_componentWillReceiveProps(a,n),t.state!==e&&$r.enqueueReplaceState(t,t.state,null)}function Da(e,t){var a=t;if("ref"in t){a={};for(var n in t)n!=="ref"&&(a[n]=t[n])}if(e=e.defaultProps){a===t&&(a=D({},a));for(var l in e)a[l]===void 0&&(a[l]=e[l])}return a}var ii=typeof reportError=="function"?reportError:function(e){if(typeof window=="object"&&typeof window.ErrorEvent=="function"){var t=new window.ErrorEvent("error",{bubbles:!0,cancelable:!0,message:typeof e=="object"&&e!==null&&typeof e.message=="string"?String(e.message):String(e),error:e});if(!window.dispatchEvent(t))return}else if(typeof process=="object"&&typeof process.emit=="function"){process.emit("uncaughtException",e);return}console.error(e)};function fc(e){ii(e)}function hc(e){console.error(e)}function mc(e){ii(e)}function ri(e,t){try{var a=e.onUncaughtError;a(t.value,{componentStack:t.stack})}catch(n){setTimeout(function(){throw n})}}function pc(e,t,a){try{var n=e.onCaughtError;n(a.value,{componentStack:a.stack,errorBoundary:t.tag===1?t.stateNode:null})}catch(l){setTimeout(function(){throw l})}}function Jr(e,t,a){return a=Pt(a),a.tag=3,a.payload={element:null},a.callback=function(){ri(e,t)},a}function gc(e){return e=Pt(e),e.tag=3,e}function yc(e,t,a,n){var l=a.type.getDerivedStateFromError;if(typeof l=="function"){var i=n.value;e.payload=function(){return l(i)},e.callback=function(){pc(t,a,n)}}var r=a.stateNode;r!==null&&typeof r.componentDidCatch=="function"&&(e.callback=function(){pc(t,a,n),typeof l!="function"&&(la===null?la=new Set([this]):la.add(this));var o=n.stack;this.componentDidCatch(n.value,{componentStack:o!==null?o:""})})}function kh(e,t,a,n,l){if(a.flags|=32768,n!==null&&typeof n=="object"&&typeof n.then=="function"){if(t=a.alternate,t!==null&&Hn(t,a,l,!0),a=ct.current,a!==null){switch(a.tag){case 13:return wt===null?Ao():a.alternate===null&&me===0&&(me=3),a.flags&=-257,a.flags|=65536,a.lanes=l,n===Mr?a.flags|=16384:(t=a.updateQueue,t===null?a.updateQueue=new Set([n]):t.add(n),Oo(e,n,l)),!1;case 22:return a.flags|=65536,n===Mr?a.flags|=16384:(t=a.updateQueue,t===null?(t={transitions:null,markerInstances:null,retryQueue:new Set([n])},a.updateQueue=t):(a=t.retryQueue,a===null?t.retryQueue=new Set([n]):a.add(n)),Oo(e,n,l)),!1}throw Error(f(435,a.tag))}return Oo(e,n,l),Ao(),!1}if(ee)return t=ct.current,t!==null?((t.flags&65536)===0&&(t.flags|=256),t.flags|=65536,t.lanes=l,n!==Tr&&(e=Error(f(422),{cause:n}),Ln(rt(e,a)))):(n!==Tr&&(t=Error(f(423),{cause:n}),Ln(rt(t,a))),e=e.current.alternate,e.flags|=65536,l&=-l,e.lanes|=l,n=rt(n,a),l=Jr(e.stateNode,n,l),Dr(e,l),me!==4&&(me=2)),!1;var i=Error(f(520),{cause:n});if(i=rt(i,a),ll===null?ll=[i]:ll.push(i),me!==4&&(me=2),t===null)return!0;n=rt(n,a),a=t;do{switch(a.tag){case 3:return a.flags|=65536,e=l&-l,a.lanes|=e,e=Jr(a.stateNode,n,e),Dr(a,e),!1;case 1:if(t=a.type,i=a.stateNode,(a.flags&128)===0&&(typeof t.getDerivedStateFromError=="function"||i!==null&&typeof i.componentDidCatch=="function"&&(la===null||!la.has(i))))return a.flags|=65536,l&=-l,a.lanes|=l,l=gc(l),yc(l,e,a,n),Dr(a,l),!1}a=a.return}while(a!==null);return!1}var vc=Error(f(461)),Oe=!1;function Ne(e,t,a,n){t.child=e===null?sc(t,null,a,n):on(t,e.child,a,n)}function bc(e,t,a,n,l){a=a.render;var i=t.ref;if("ref"in n){var r={};for(var o in n)o!=="ref"&&(r[o]=n[o])}else r=n;return Ma(t),n=zr(e,t,a,r,i,l),o=Ur(),e!==null&&!Oe?(Lr(e,t,l),Ut(e,t,l)):(ee&&o&&vr(t),t.flags|=1,Ne(e,t,n,l),t.child)}function Tc(e,t,a,n,l){if(e===null){var i=a.type;return typeof i=="function"&&!pr(i)&&i.defaultProps===void 0&&a.compare===null?(t.tag=15,t.type=i,Sc(e,t,i,n,l)):(e=ql(a.type,null,n,t,t.mode,l),e.ref=t.ref,e.return=t,t.child=e)}if(i=e.child,!ro(e,l)){var r=i.memoizedProps;if(a=a.compare,a=a!==null?a:xn,a(r,n)&&e.ref===t.ref)return Ut(e,t,l)}return t.flags|=1,e=kt(i,n),e.ref=t.ref,e.return=t,t.child=e}function Sc(e,t,a,n,l){if(e!==null){var i=e.memoizedProps;if(xn(i,n)&&e.ref===t.ref)if(Oe=!1,t.pendingProps=n=i,ro(e,l))(e.flags&131072)!==0&&(Oe=!0);else return t.lanes=e.lanes,Ut(e,t,l)}return Fr(e,t,a,n,l)}function wc(e,t,a){var n=t.pendingProps,l=n.children,i=e!==null?e.memoizedState:null;if(n.mode==="hidden"){if((t.flags&128)!==0){if(n=i!==null?i.baseLanes|a:a,e!==null){for(l=t.child=e.child,i=0;l!==null;)i=i|l.lanes|l.childLanes,l=l.sibling;t.childLanes=i&~n}else t.childLanes=0,t.child=null;return Ac(e,t,n,a)}if((a&536870912)!==0)t.memoizedState={baseLanes:0,cachePool:null},e!==null&&Ql(t,i!==null?i.cachePool:null),i!==null?Su(t,i):_r(),uc(t);else return t.lanes=t.childLanes=536870912,Ac(e,t,i!==null?i.baseLanes|a:a,a)}else i!==null?(Ql(t,i.cachePool),Su(t,i),Jt(),t.memoizedState=null):(e!==null&&Ql(t,null),_r(),Jt());return Ne(e,t,l,a),t.child}function Ac(e,t,a,n){var l=Rr();return l=l===null?null:{parent:Se._currentValue,pool:l},t.memoizedState={baseLanes:a,cachePool:l},e!==null&&Ql(t,null),_r(),uc(t),e!==null&&Hn(e,t,n,!0),null}function oi(e,t){var a=t.ref;if(a===null)e!==null&&e.ref!==null&&(t.flags|=4194816);else{if(typeof a!="function"&&typeof a!="object")throw Error(f(284));(e===null||e.ref!==a)&&(t.flags|=4194816)}}function Fr(e,t,a,n,l){return Ma(t),a=zr(e,t,a,n,void 0,l),n=Ur(),e!==null&&!Oe?(Lr(e,t,l),Ut(e,t,l)):(ee&&n&&vr(t),t.flags|=1,Ne(e,t,a,l),t.child)}function Ec(e,t,a,n,l,i){return Ma(t),t.updateQueue=null,a=Au(t,n,a,l),wu(e),n=Ur(),e!==null&&!Oe?(Lr(e,t,i),Ut(e,t,i)):(ee&&n&&vr(t),t.flags|=1,Ne(e,t,a,i),t.child)}function Oc(e,t,a,n,l){if(Ma(t),t.stateNode===null){var i=Wa,r=a.contextType;typeof r=="object"&&r!==null&&(i=ze(r)),i=new a(n,i),t.memoizedState=i.state!==null&&i.state!==void 0?i.state:null,i.updater=$r,t.stateNode=i,i._reactInternals=t,i=t.stateNode,i.props=n,i.state=t.memoizedState,i.refs={},Nr(t),r=a.contextType,i.context=typeof r=="object"&&r!==null?ze(r):Wa,i.state=t.memoizedState,r=a.getDerivedStateFromProps,typeof r=="function"&&(Wr(t,a,r,n),i.state=t.memoizedState),typeof a.getDerivedStateFromProps=="function"||typeof i.getSnapshotBeforeUpdate=="function"||typeof i.UNSAFE_componentWillMount!="function"&&typeof i.componentWillMount!="function"||(r=i.state,typeof i.componentWillMount=="function"&&i.componentWillMount(),typeof i.UNSAFE_componentWillMount=="function"&&i.UNSAFE_componentWillMount(),r!==i.state&&$r.enqueueReplaceState(i,i.state,null),Kn(t,n,i,l),Qn(),i.state=t.memoizedState),typeof i.componentDidMount=="function"&&(t.flags|=4194308),n=!0}else if(e===null){i=t.stateNode;var o=t.memoizedProps,s=Da(a,o);i.props=s;var m=i.context,y=a.contextType;r=Wa,typeof y=="object"&&y!==null&&(r=ze(y));var T=a.getDerivedStateFromProps;y=typeof T=="function"||typeof i.getSnapshotBeforeUpdate=="function",o=t.pendingProps!==o,y||typeof i.UNSAFE_componentWillReceiveProps!="function"&&typeof i.componentWillReceiveProps!="function"||(o||m!==r)&&dc(t,i,n,r),It=!1;var p=t.memoizedState;i.state=p,Kn(t,n,i,l),Qn(),m=t.memoizedState,o||p!==m||It?(typeof T=="function"&&(Wr(t,a,T,n),m=t.memoizedState),(s=It||cc(t,a,s,n,p,m,r))?(y||typeof i.UNSAFE_componentWillMount!="function"&&typeof i.componentWillMount!="function"||(typeof i.componentWillMount=="function"&&i.componentWillMount(),typeof i.UNSAFE_componentWillMount=="function"&&i.UNSAFE_componentWillMount()),typeof i.componentDidMount=="function"&&(t.flags|=4194308)):(typeof i.componentDidMount=="function"&&(t.flags|=4194308),t.memoizedProps=n,t.memoizedState=m),i.props=n,i.state=m,i.context=r,n=s):(typeof i.componentDidMount=="function"&&(t.flags|=4194308),n=!1)}else{i=t.stateNode,kr(e,t),r=t.memoizedProps,y=Da(a,r),i.props=y,T=t.pendingProps,p=i.context,m=a.contextType,s=Wa,typeof m=="object"&&m!==null&&(s=ze(m)),o=a.getDerivedStateFromProps,(m=typeof o=="function"||typeof i.getSnapshotBeforeUpdate=="function")||typeof i.UNSAFE_componentWillReceiveProps!="function"&&typeof i.componentWillReceiveProps!="function"||(r!==T||p!==s)&&dc(t,i,n,s),It=!1,p=t.memoizedState,i.state=p,Kn(t,n,i,l),Qn();var g=t.memoizedState;r!==T||p!==g||It||e!==null&&e.dependencies!==null&&Gl(e.dependencies)?(typeof o=="function"&&(Wr(t,a,o,n),g=t.memoizedState),(y=It||cc(t,a,y,n,p,g,s)||e!==null&&e.dependencies!==null&&Gl(e.dependencies))?(m||typeof i.UNSAFE_componentWillUpdate!="function"&&typeof i.componentWillUpdate!="function"||(typeof i.componentWillUpdate=="function"&&i.componentWillUpdate(n,g,s),typeof i.UNSAFE_componentWillUpdate=="function"&&i.UNSAFE_componentWillUpdate(n,g,s)),typeof i.componentDidUpdate=="function"&&(t.flags|=4),typeof i.getSnapshotBeforeUpdate=="function"&&(t.flags|=1024)):(typeof i.componentDidUpdate!="function"||r===e.memoizedProps&&p===e.memoizedState||(t.flags|=4),typeof i.getSnapshotBeforeUpdate!="function"||r===e.memoizedProps&&p===e.memoizedState||(t.flags|=1024),t.memoizedProps=n,t.memoizedState=g),i.props=n,i.state=g,i.context=s,n=y):(typeof i.componentDidUpdate!="function"||r===e.memoizedProps&&p===e.memoizedState||(t.flags|=4),typeof i.getSnapshotBeforeUpdate!="function"||r===e.memoizedProps&&p===e.memoizedState||(t.flags|=1024),n=!1)}return i=n,oi(e,t),n=(t.flags&128)!==0,i||n?(i=t.stateNode,a=n&&typeof a.getDerivedStateFromError!="function"?null:i.render(),t.flags|=1,e!==null&&n?(t.child=on(t,e.child,null,l),t.child=on(t,null,a,l)):Ne(e,t,a,l),t.memoizedState=i.state,e=t.child):e=Ut(e,t,l),e}function Rc(e,t,a,n){return Un(),t.flags|=256,Ne(e,t,a,n),t.child}var eo={dehydrated:null,treeContext:null,retryLane:0,hydrationErrors:null};function to(e){return{baseLanes:e,cachePool:hu()}}function ao(e,t,a){return e=e!==null?e.childLanes&~a:0,t&&(e|=dt),e}function Mc(e,t,a){var n=t.pendingProps,l=!1,i=(t.flags&128)!==0,r;if((r=i)||(r=e!==null&&e.memoizedState===null?!1:(we.current&2)!==0),r&&(l=!0,t.flags&=-129),r=(t.flags&32)!==0,t.flags&=-33,e===null){if(ee){if(l?$t(t):Jt(),ee){var o=he,s;if(s=o){e:{for(s=o,o=St;s.nodeType!==8;){if(!o){o=null;break e}if(s=gt(s.nextSibling),s===null){o=null;break e}}o=s}o!==null?(t.memoizedState={dehydrated:o,treeContext:wa!==null?{id:Dt,overflow:Bt}:null,retryLane:536870912,hydrationErrors:null},s=$e(18,null,null,0),s.stateNode=o,s.return=t,t.child=s,He=t,he=null,s=!0):s=!1}s||Oa(t)}if(o=t.memoizedState,o!==null&&(o=o.dehydrated,o!==null))return Yo(o)?t.lanes=32:t.lanes=536870912,null;zt(t)}return o=n.children,n=n.fallback,l?(Jt(),l=t.mode,o=si({mode:"hidden",children:o},l),n=Sa(n,l,a,null),o.return=t,n.return=t,o.sibling=n,t.child=o,l=t.child,l.memoizedState=to(a),l.childLanes=ao(e,r,a),t.memoizedState=eo,n):($t(t),no(t,o))}if(s=e.memoizedState,s!==null&&(o=s.dehydrated,o!==null)){if(i)t.flags&256?($t(t),t.flags&=-257,t=lo(e,t,a)):t.memoizedState!==null?(Jt(),t.child=e.child,t.flags|=128,t=null):(Jt(),l=n.fallback,o=t.mode,n=si({mode:"visible",children:n.children},o),l=Sa(l,o,a,null),l.flags|=2,n.return=t,l.return=t,n.sibling=l,t.child=n,on(t,e.child,null,a),n=t.child,n.memoizedState=to(a),n.childLanes=ao(e,r,a),t.memoizedState=eo,t=l);else if($t(t),Yo(o)){if(r=o.nextSibling&&o.nextSibling.dataset,r)var m=r.dgst;r=m,n=Error(f(419)),n.stack="",n.digest=r,Ln({value:n,source:null,stack:null}),t=lo(e,t,a)}else if(Oe||Hn(e,t,a,!1),r=(a&e.childLanes)!==0,Oe||r){if(r=se,r!==null&&(n=a&-a,n=(n&42)!==0?1:Yi(n),n=(n&(r.suspendedLanes|a))!==0?0:n,n!==0&&n!==s.retryLane))throw s.retryLane=n,Za(e,n),at(r,e,n),vc;o.data==="$?"||Ao(),t=lo(e,t,a)}else o.data==="$?"?(t.flags|=192,t.child=e.child,t=null):(e=s.treeContext,he=gt(o.nextSibling),He=t,ee=!0,Ea=null,St=!1,e!==null&&(st[ut++]=Dt,st[ut++]=Bt,st[ut++]=wa,Dt=e.id,Bt=e.overflow,wa=t),t=no(t,n.children),t.flags|=4096);return t}return l?(Jt(),l=n.fallback,o=t.mode,s=e.child,m=s.sibling,n=kt(s,{mode:"hidden",children:n.children}),n.subtreeFlags=s.subtreeFlags&65011712,m!==null?l=kt(m,l):(l=Sa(l,o,a,null),l.flags|=2),l.return=t,n.return=t,n.sibling=l,t.child=n,n=l,l=t.child,o=e.child.memoizedState,o===null?o=to(a):(s=o.cachePool,s!==null?(m=Se._currentValue,s=s.parent!==m?{parent:m,pool:m}:s):s=hu(),o={baseLanes:o.baseLanes|a,cachePool:s}),l.memoizedState=o,l.childLanes=ao(e,r,a),t.memoizedState=eo,n):($t(t),a=e.child,e=a.sibling,a=kt(a,{mode:"visible",children:n.children}),a.return=t,a.sibling=null,e!==null&&(r=t.deletions,r===null?(t.deletions=[e],t.flags|=16):r.push(e)),t.child=a,t.memoizedState=null,a)}function no(e,t){return t=si({mode:"visible",children:t},e.mode),t.return=e,e.child=t}function si(e,t){return e=$e(22,e,null,t),e.lanes=0,e.stateNode={_visibility:1,_pendingMarkers:null,_retryCache:null,_transitions:null},e}function lo(e,t,a){return on(t,e.child,null,a),e=no(t,t.pendingProps.children),e.flags|=2,t.memoizedState=null,e}function Nc(e,t,a){e.lanes|=t;var n=e.alternate;n!==null&&(n.lanes|=t),wr(e.return,t,a)}function io(e,t,a,n,l){var i=e.memoizedState;i===null?e.memoizedState={isBackwards:t,rendering:null,renderingStartTime:0,last:n,tail:a,tailMode:l}:(i.isBackwards=t,i.rendering=null,i.renderingStartTime=0,i.last=n,i.tail=a,i.tailMode=l)}function kc(e,t,a){var n=t.pendingProps,l=n.revealOrder,i=n.tail;if(Ne(e,t,n.children,a),n=we.current,(n&2)!==0)n=n&1|2,t.flags|=128;else{if(e!==null&&(e.flags&128)!==0)e:for(e=t.child;e!==null;){if(e.tag===13)e.memoizedState!==null&&Nc(e,a,t);else if(e.tag===19)Nc(e,a,t);else if(e.child!==null){e.child.return=e,e=e.child;continue}if(e===t)break e;for(;e.sibling===null;){if(e.return===null||e.return===t)break e;e=e.return}e.sibling.return=e.return,e=e.sibling}n&=1}switch(O(we,n),l){case"forwards":for(a=t.child,l=null;a!==null;)e=a.alternate,e!==null&&li(e)===null&&(l=a),a=a.sibling;a=l,a===null?(l=t.child,t.child=null):(l=a.sibling,a.sibling=null),io(t,!1,l,a,i);break;case"backwards":for(a=null,l=t.child,t.child=null;l!==null;){if(e=l.alternate,e!==null&&li(e)===null){t.child=l;break}e=l.sibling,l.sibling=a,a=l,l=e}io(t,!0,a,null,i);break;case"together":io(t,!1,null,null,void 0);break;default:t.memoizedState=null}return t.child}function Ut(e,t,a){if(e!==null&&(t.dependencies=e.dependencies),na|=t.lanes,(a&t.childLanes)===0)if(e!==null){if(Hn(e,t,a,!1),(a&t.childLanes)===0)return null}else return null;if(e!==null&&t.child!==e.child)throw Error(f(153));if(t.child!==null){for(e=t.child,a=kt(e,e.pendingProps),t.child=a,a.return=t;e.sibling!==null;)e=e.sibling,a=a.sibling=kt(e,e.pendingProps),a.return=t;a.sibling=null}return t.child}function ro(e,t){return(e.lanes&t)!==0?!0:(e=e.dependencies,!!(e!==null&&Gl(e)))}function Dh(e,t,a){switch(t.tag){case 3:ce(t,t.stateNode.containerInfo),Xt(t,Se,e.memoizedState.cache),Un();break;case 27:case 5:zi(t);break;case 4:ce(t,t.stateNode.containerInfo);break;case 10:Xt(t,t.type,t.memoizedProps.value);break;case 13:var n=t.memoizedState;if(n!==null)return n.dehydrated!==null?($t(t),t.flags|=128,null):(a&t.child.childLanes)!==0?Mc(e,t,a):($t(t),e=Ut(e,t,a),e!==null?e.sibling:null);$t(t);break;case 19:var l=(e.flags&128)!==0;if(n=(a&t.childLanes)!==0,n||(Hn(e,t,a,!1),n=(a&t.childLanes)!==0),l){if(n)return kc(e,t,a);t.flags|=128}if(l=t.memoizedState,l!==null&&(l.rendering=null,l.tail=null,l.lastEffect=null),O(we,we.current),n)break;return null;case 22:case 23:return t.lanes=0,wc(e,t,a);case 24:Xt(t,Se,e.memoizedState.cache)}return Ut(e,t,a)}function Dc(e,t,a){if(e!==null)if(e.memoizedProps!==t.pendingProps)Oe=!0;else{if(!ro(e,a)&&(t.flags&128)===0)return Oe=!1,Dh(e,t,a);Oe=(e.flags&131072)!==0}else Oe=!1,ee&&(t.flags&1048576)!==0&&ru(t,Vl,t.index);switch(t.lanes=0,t.tag){case 16:e:{e=t.pendingProps;var n=t.elementType,l=n._init;if(n=l(n._payload),t.type=n,typeof n=="function")pr(n)?(e=Da(n,e),t.tag=1,t=Oc(null,t,n,e,a)):(t.tag=0,t=Fr(null,t,n,e,a));else{if(n!=null){if(l=n.$$typeof,l===ht){t.tag=11,t=bc(null,t,n,e,a);break e}else if(l===Ke){t.tag=14,t=Tc(null,t,n,e,a);break e}}throw t=pa(n)||n,Error(f(306,t,""))}}return t;case 0:return Fr(e,t,t.type,t.pendingProps,a);case 1:return n=t.type,l=Da(n,t.pendingProps),Oc(e,t,n,l,a);case 3:e:{if(ce(t,t.stateNode.containerInfo),e===null)throw Error(f(387));n=t.pendingProps;var i=t.memoizedState;l=i.element,kr(e,t),Kn(t,n,null,a);var r=t.memoizedState;if(n=r.cache,Xt(t,Se,n),n!==i.cache&&Ar(t,[Se],a,!0),Qn(),n=r.element,i.isDehydrated)if(i={element:n,isDehydrated:!1,cache:r.cache},t.updateQueue.baseState=i,t.memoizedState=i,t.flags&256){t=Rc(e,t,n,a);break e}else if(n!==l){l=rt(Error(f(424)),t),Ln(l),t=Rc(e,t,n,a);break e}else for(e=t.stateNode.containerInfo,e.nodeType===9?e=e.body:e=e.nodeName==="HTML"?e.ownerDocument.body:e,he=gt(e.firstChild),He=t,ee=!0,Ea=null,St=!0,a=sc(t,null,n,a),t.child=a;a;)a.flags=a.flags&-3|4096,a=a.sibling;else{if(Un(),n===l){t=Ut(e,t,a);break e}Ne(e,t,n,a)}t=t.child}return t;case 26:return oi(e,t),e===null?(a=Cd(t.type,null,t.pendingProps,null))?t.memoizedState=a:ee||(a=t.type,e=t.pendingProps,n=wi(Y.current).createElement(a),n[Ce]=t,n[qe]=e,De(n,a,e),Ee(n),t.stateNode=n):t.memoizedState=Cd(t.type,e.memoizedProps,t.pendingProps,e.memoizedState),null;case 27:return zi(t),e===null&&ee&&(n=t.stateNode=Bd(t.type,t.pendingProps,Y.current),He=t,St=!0,l=he,oa(t.type)?(Vo=l,he=gt(n.firstChild)):he=l),Ne(e,t,t.pendingProps.children,a),oi(e,t),e===null&&(t.flags|=4194304),t.child;case 5:return e===null&&ee&&((l=n=he)&&(n=lm(n,t.type,t.pendingProps,St),n!==null?(t.stateNode=n,He=t,he=gt(n.firstChild),St=!1,l=!0):l=!1),l||Oa(t)),zi(t),l=t.type,i=t.pendingProps,r=e!==null?e.memoizedProps:null,n=i.children,Lo(l,i)?n=null:r!==null&&Lo(l,r)&&(t.flags|=32),t.memoizedState!==null&&(l=zr(e,t,wh,null,null,a),hl._currentValue=l),oi(e,t),Ne(e,t,n,a),t.child;case 6:return e===null&&ee&&((e=a=he)&&(a=im(a,t.pendingProps,St),a!==null?(t.stateNode=a,He=t,he=null,e=!0):e=!1),e||Oa(t)),null;case 13:return Mc(e,t,a);case 4:return ce(t,t.stateNode.containerInfo),n=t.pendingProps,e===null?t.child=on(t,null,n,a):Ne(e,t,n,a),t.child;case 11:return bc(e,t,t.type,t.pendingProps,a);case 7:return Ne(e,t,t.pendingProps,a),t.child;case 8:return Ne(e,t,t.pendingProps.children,a),t.child;case 12:return Ne(e,t,t.pendingProps.children,a),t.child;case 10:return n=t.pendingProps,Xt(t,t.type,n.value),Ne(e,t,n.children,a),t.child;case 9:return l=t.type._context,n=t.pendingProps.children,Ma(t),l=ze(l),n=n(l),t.flags|=1,Ne(e,t,n,a),t.child;case 14:return Tc(e,t,t.type,t.pendingProps,a);case 15:return Sc(e,t,t.type,t.pendingProps,a);case 19:return kc(e,t,a);case 31:return n=t.pendingProps,a=t.mode,n={mode:n.mode,children:n.children},e===null?(a=si(n,a),a.ref=t.ref,t.child=a,a.return=t,t=a):(a=kt(e.child,n),a.ref=t.ref,t.child=a,a.return=t,t=a),t;case 22:return wc(e,t,a);case 24:return Ma(t),n=ze(Se),e===null?(l=Rr(),l===null&&(l=se,i=Er(),l.pooledCache=i,i.refCount++,i!==null&&(l.pooledCacheLanes|=a),l=i),t.memoizedState={parent:n,cache:l},Nr(t),Xt(t,Se,l)):((e.lanes&a)!==0&&(kr(e,t),Kn(t,null,null,a),Qn()),l=e.memoizedState,i=t.memoizedState,l.parent!==n?(l={parent:n,cache:n},t.memoizedState=l,t.lanes===0&&(t.memoizedState=t.updateQueue.baseState=l),Xt(t,Se,n)):(n=i.cache,Xt(t,Se,n),n!==l.cache&&Ar(t,[Se],a,!0))),Ne(e,t,t.pendingProps.children,a),t.child;case 29:throw t.pendingProps}throw Error(f(156,t.tag))}function Lt(e){e.flags|=4}function Bc(e,t){if(t.type!=="stylesheet"||(t.state.loading&4)!==0)e.flags&=-16777217;else if(e.flags|=16777216,!qd(t)){if(t=ct.current,t!==null&&((P&4194048)===P?wt!==null:(P&62914560)!==P&&(P&536870912)===0||t!==wt))throw Gn=Mr,mu;e.flags|=8192}}function ui(e,t){t!==null&&(e.flags|=4),e.flags&16384&&(t=e.tag!==22?us():536870912,e.lanes|=t,dn|=t)}function Jn(e,t){if(!ee)switch(e.tailMode){case"hidden":t=e.tail;for(var a=null;t!==null;)t.alternate!==null&&(a=t),t=t.sibling;a===null?e.tail=null:a.sibling=null;break;case"collapsed":a=e.tail;for(var n=null;a!==null;)a.alternate!==null&&(n=a),a=a.sibling;n===null?t||e.tail===null?e.tail=null:e.tail.sibling=null:n.sibling=null}}function fe(e){var t=e.alternate!==null&&e.alternate.child===e.child,a=0,n=0;if(t)for(var l=e.child;l!==null;)a|=l.lanes|l.childLanes,n|=l.subtreeFlags&65011712,n|=l.flags&65011712,l.return=e,l=l.sibling;else for(l=e.child;l!==null;)a|=l.lanes|l.childLanes,n|=l.subtreeFlags,n|=l.flags,l.return=e,l=l.sibling;return e.subtreeFlags|=n,e.childLanes=a,t}function Bh(e,t,a){var n=t.pendingProps;switch(br(t),t.tag){case 31:case 16:case 15:case 0:case 11:case 7:case 8:case 12:case 9:case 14:return fe(t),null;case 1:return fe(t),null;case 3:return a=t.stateNode,n=null,e!==null&&(n=e.memoizedState.cache),t.memoizedState.cache!==n&&(t.flags|=2048),xt(Se),Gt(),a.pendingContext&&(a.context=a.pendingContext,a.pendingContext=null),(e===null||e.child===null)&&(zn(t)?Lt(t):e===null||e.memoizedState.isDehydrated&&(t.flags&256)===0||(t.flags|=1024,uu())),fe(t),null;case 26:return a=t.memoizedState,e===null?(Lt(t),a!==null?(fe(t),Bc(t,a)):(fe(t),t.flags&=-16777217)):a?a!==e.memoizedState?(Lt(t),fe(t),Bc(t,a)):(fe(t),t.flags&=-16777217):(e.memoizedProps!==n&&Lt(t),fe(t),t.flags&=-16777217),null;case 27:Tl(t),a=Y.current;var l=t.type;if(e!==null&&t.stateNode!=null)e.memoizedProps!==n&&Lt(t);else{if(!n){if(t.stateNode===null)throw Error(f(166));return fe(t),null}e=_.current,zn(t)?ou(t):(e=Bd(l,n,a),t.stateNode=e,Lt(t))}return fe(t),null;case 5:if(Tl(t),a=t.type,e!==null&&t.stateNode!=null)e.memoizedProps!==n&&Lt(t);else{if(!n){if(t.stateNode===null)throw Error(f(166));return fe(t),null}if(e=_.current,zn(t))ou(t);else{switch(l=wi(Y.current),e){case 1:e=l.createElementNS("http://www.w3.org/2000/svg",a);break;case 2:e=l.createElementNS("http://www.w3.org/1998/Math/MathML",a);break;default:switch(a){case"svg":e=l.createElementNS("http://www.w3.org/2000/svg",a);break;case"math":e=l.createElementNS("http://www.w3.org/1998/Math/MathML",a);break;case"script":e=l.createElement("div"),e.innerHTML="<script><\/script>",e=e.removeChild(e.firstChild);break;case"select":e=typeof n.is=="string"?l.createElement("select",{is:n.is}):l.createElement("select"),n.multiple?e.multiple=!0:n.size&&(e.size=n.size);break;default:e=typeof n.is=="string"?l.createElement(a,{is:n.is}):l.createElement(a)}}e[Ce]=t,e[qe]=n;e:for(l=t.child;l!==null;){if(l.tag===5||l.tag===6)e.appendChild(l.stateNode);else if(l.tag!==4&&l.tag!==27&&l.child!==null){l.child.return=l,l=l.child;continue}if(l===t)break e;for(;l.sibling===null;){if(l.return===null||l.return===t)break e;l=l.return}l.sibling.return=l.return,l=l.sibling}t.stateNode=e;e:switch(De(e,a,n),a){case"button":case"input":case"select":case"textarea":e=!!n.autoFocus;break e;case"img":e=!0;break e;default:e=!1}e&&Lt(t)}}return fe(t),t.flags&=-16777217,null;case 6:if(e&&t.stateNode!=null)e.memoizedProps!==n&&Lt(t);else{if(typeof n!="string"&&t.stateNode===null)throw Error(f(166));if(e=Y.current,zn(t)){if(e=t.stateNode,a=t.memoizedProps,n=null,l=He,l!==null)switch(l.tag){case 27:case 5:n=l.memoizedProps}e[Ce]=t,e=!!(e.nodeValue===a||n!==null&&n.suppressHydrationWarning===!0||Ed(e.nodeValue,a)),e||Oa(t)}else e=wi(e).createTextNode(n),e[Ce]=t,t.stateNode=e}return fe(t),null;case 13:if(n=t.memoizedState,e===null||e.memoizedState!==null&&e.memoizedState.dehydrated!==null){if(l=zn(t),n!==null&&n.dehydrated!==null){if(e===null){if(!l)throw Error(f(318));if(l=t.memoizedState,l=l!==null?l.dehydrated:null,!l)throw Error(f(317));l[Ce]=t}else Un(),(t.flags&128)===0&&(t.memoizedState=null),t.flags|=4;fe(t),l=!1}else l=uu(),e!==null&&e.memoizedState!==null&&(e.memoizedState.hydrationErrors=l),l=!0;if(!l)return t.flags&256?(zt(t),t):(zt(t),null)}if(zt(t),(t.flags&128)!==0)return t.lanes=a,t;if(a=n!==null,e=e!==null&&e.memoizedState!==null,a){n=t.child,l=null,n.alternate!==null&&n.alternate.memoizedState!==null&&n.alternate.memoizedState.cachePool!==null&&(l=n.alternate.memoizedState.cachePool.pool);var i=null;n.memoizedState!==null&&n.memoizedState.cachePool!==null&&(i=n.memoizedState.cachePool.pool),i!==l&&(n.flags|=2048)}return a!==e&&a&&(t.child.flags|=8192),ui(t,t.updateQueue),fe(t),null;case 4:return Gt(),e===null&&_o(t.stateNode.containerInfo),fe(t),null;case 10:return xt(t.type),fe(t),null;case 19:if(M(we),l=t.memoizedState,l===null)return fe(t),null;if(n=(t.flags&128)!==0,i=l.rendering,i===null)if(n)Jn(l,!1);else{if(me!==0||e!==null&&(e.flags&128)!==0)for(e=t.child;e!==null;){if(i=li(e),i!==null){for(t.flags|=128,Jn(l,!1),e=i.updateQueue,t.updateQueue=e,ui(t,e),t.subtreeFlags=0,e=a,a=t.child;a!==null;)iu(a,e),a=a.sibling;return O(we,we.current&1|2),t.child}e=e.sibling}l.tail!==null&&Tt()>fi&&(t.flags|=128,n=!0,Jn(l,!1),t.lanes=4194304)}else{if(!n)if(e=li(i),e!==null){if(t.flags|=128,n=!0,e=e.updateQueue,t.updateQueue=e,ui(t,e),Jn(l,!0),l.tail===null&&l.tailMode==="hidden"&&!i.alternate&&!ee)return fe(t),null}else 2*Tt()-l.renderingStartTime>fi&&a!==536870912&&(t.flags|=128,n=!0,Jn(l,!1),t.lanes=4194304);l.isBackwards?(i.sibling=t.child,t.child=i):(e=l.last,e!==null?e.sibling=i:t.child=i,l.last=i)}return l.tail!==null?(t=l.tail,l.rendering=t,l.tail=t.sibling,l.renderingStartTime=Tt(),t.sibling=null,e=we.current,O(we,n?e&1|2:e&1),t):(fe(t),null);case 22:case 23:return zt(t),xr(),n=t.memoizedState!==null,e!==null?e.memoizedState!==null!==n&&(t.flags|=8192):n&&(t.flags|=8192),n?(a&536870912)!==0&&(t.flags&128)===0&&(fe(t),t.subtreeFlags&6&&(t.flags|=8192)):fe(t),a=t.updateQueue,a!==null&&ui(t,a.retryQueue),a=null,e!==null&&e.memoizedState!==null&&e.memoizedState.cachePool!==null&&(a=e.memoizedState.cachePool.pool),n=null,t.memoizedState!==null&&t.memoizedState.cachePool!==null&&(n=t.memoizedState.cachePool.pool),n!==a&&(t.flags|=2048),e!==null&&M(Na),null;case 24:return a=null,e!==null&&(a=e.memoizedState.cache),t.memoizedState.cache!==a&&(t.flags|=2048),xt(Se),fe(t),null;case 25:return null;case 30:return null}throw Error(f(156,t.tag))}function _h(e,t){switch(br(t),t.tag){case 1:return e=t.flags,e&65536?(t.flags=e&-65537|128,t):null;case 3:return xt(Se),Gt(),e=t.flags,(e&65536)!==0&&(e&128)===0?(t.flags=e&-65537|128,t):null;case 26:case 27:case 5:return Tl(t),null;case 13:if(zt(t),e=t.memoizedState,e!==null&&e.dehydrated!==null){if(t.alternate===null)throw Error(f(340));Un()}return e=t.flags,e&65536?(t.flags=e&-65537|128,t):null;case 19:return M(we),null;case 4:return Gt(),null;case 10:return xt(t.type),null;case 22:case 23:return zt(t),xr(),e!==null&&M(Na),e=t.flags,e&65536?(t.flags=e&-65537|128,t):null;case 24:return xt(Se),null;case 25:return null;default:return null}}function _c(e,t){switch(br(t),t.tag){case 3:xt(Se),Gt();break;case 26:case 27:case 5:Tl(t);break;case 4:Gt();break;case 13:zt(t);break;case 19:M(we);break;case 10:xt(t.type);break;case 22:case 23:zt(t),xr(),e!==null&&M(Na);break;case 24:xt(Se)}}function Fn(e,t){try{var a=t.updateQueue,n=a!==null?a.lastEffect:null;if(n!==null){var l=n.next;a=l;do{if((a.tag&e)===e){n=void 0;var i=a.create,r=a.inst;n=i(),r.destroy=n}a=a.next}while(a!==l)}}catch(o){oe(t,t.return,o)}}function Ft(e,t,a){try{var n=t.updateQueue,l=n!==null?n.lastEffect:null;if(l!==null){var i=l.next;n=i;do{if((n.tag&e)===e){var r=n.inst,o=r.destroy;if(o!==void 0){r.destroy=void 0,l=t;var s=a,m=o;try{m()}catch(y){oe(l,s,y)}}}n=n.next}while(n!==i)}}catch(y){oe(t,t.return,y)}}function xc(e){var t=e.updateQueue;if(t!==null){var a=e.stateNode;try{Tu(t,a)}catch(n){oe(e,e.return,n)}}}function Cc(e,t,a){a.props=Da(e.type,e.memoizedProps),a.state=e.memoizedState;try{a.componentWillUnmount()}catch(n){oe(e,t,n)}}function el(e,t){try{var a=e.ref;if(a!==null){switch(e.tag){case 26:case 27:case 5:var n=e.stateNode;break;case 30:n=e.stateNode;break;default:n=e.stateNode}typeof a=="function"?e.refCleanup=a(n):a.current=n}}catch(l){oe(e,t,l)}}function At(e,t){var a=e.ref,n=e.refCleanup;if(a!==null)if(typeof n=="function")try{n()}catch(l){oe(e,t,l)}finally{e.refCleanup=null,e=e.alternate,e!=null&&(e.refCleanup=null)}else if(typeof a=="function")try{a(null)}catch(l){oe(e,t,l)}else a.current=null}function zc(e){var t=e.type,a=e.memoizedProps,n=e.stateNode;try{e:switch(t){case"button":case"input":case"select":case"textarea":a.autoFocus&&n.focus();break e;case"img":a.src?n.src=a.src:a.srcSet&&(n.srcset=a.srcSet)}}catch(l){oe(e,e.return,l)}}function oo(e,t,a){try{var n=e.stateNode;Fh(n,e.type,a,t),n[qe]=t}catch(l){oe(e,e.return,l)}}function Uc(e){return e.tag===5||e.tag===3||e.tag===26||e.tag===27&&oa(e.type)||e.tag===4}function so(e){e:for(;;){for(;e.sibling===null;){if(e.return===null||Uc(e.return))return null;e=e.return}for(e.sibling.return=e.return,e=e.sibling;e.tag!==5&&e.tag!==6&&e.tag!==18;){if(e.tag===27&&oa(e.type)||e.flags&2||e.child===null||e.tag===4)continue e;e.child.return=e,e=e.child}if(!(e.flags&2))return e.stateNode}}function uo(e,t,a){var n=e.tag;if(n===5||n===6)e=e.stateNode,t?(a.nodeType===9?a.body:a.nodeName==="HTML"?a.ownerDocument.body:a).insertBefore(e,t):(t=a.nodeType===9?a.body:a.nodeName==="HTML"?a.ownerDocument.body:a,t.appendChild(e),a=a._reactRootContainer,a!=null||t.onclick!==null||(t.onclick=Si));else if(n!==4&&(n===27&&oa(e.type)&&(a=e.stateNode,t=null),e=e.child,e!==null))for(uo(e,t,a),e=e.sibling;e!==null;)uo(e,t,a),e=e.sibling}function ci(e,t,a){var n=e.tag;if(n===5||n===6)e=e.stateNode,t?a.insertBefore(e,t):a.appendChild(e);else if(n!==4&&(n===27&&oa(e.type)&&(a=e.stateNode),e=e.child,e!==null))for(ci(e,t,a),e=e.sibling;e!==null;)ci(e,t,a),e=e.sibling}function Lc(e){var t=e.stateNode,a=e.memoizedProps;try{for(var n=e.type,l=t.attributes;l.length;)t.removeAttributeNode(l[0]);De(t,n,a),t[Ce]=e,t[qe]=a}catch(i){oe(e,e.return,i)}}var Ht=!1,ge=!1,co=!1,Hc=typeof WeakSet=="function"?WeakSet:Set,Re=null;function xh(e,t){if(e=e.containerInfo,zo=Ni,e=Zs(e),sr(e)){if("selectionStart"in e)var a={start:e.selectionStart,end:e.selectionEnd};else e:{a=(a=e.ownerDocument)&&a.defaultView||window;var n=a.getSelection&&a.getSelection();if(n&&n.rangeCount!==0){a=n.anchorNode;var l=n.anchorOffset,i=n.focusNode;n=n.focusOffset;try{a.nodeType,i.nodeType}catch{a=null;break e}var r=0,o=-1,s=-1,m=0,y=0,T=e,p=null;t:for(;;){for(var g;T!==a||l!==0&&T.nodeType!==3||(o=r+l),T!==i||n!==0&&T.nodeType!==3||(s=r+n),T.nodeType===3&&(r+=T.nodeValue.length),(g=T.firstChild)!==null;)p=T,T=g;for(;;){if(T===e)break t;if(p===a&&++m===l&&(o=r),p===i&&++y===n&&(s=r),(g=T.nextSibling)!==null)break;T=p,p=T.parentNode}T=g}a=o===-1||s===-1?null:{start:o,end:s}}else a=null}a=a||{start:0,end:0}}else a=null;for(Uo={focusedElem:e,selectionRange:a},Ni=!1,Re=t;Re!==null;)if(t=Re,e=t.child,(t.subtreeFlags&1024)!==0&&e!==null)e.return=t,Re=e;else for(;Re!==null;){switch(t=Re,i=t.alternate,e=t.flags,t.tag){case 0:break;case 11:case 15:break;case 1:if((e&1024)!==0&&i!==null){e=void 0,a=t,l=i.memoizedProps,i=i.memoizedState,n=a.stateNode;try{var L=Da(a.type,l,a.elementType===a.type);e=n.getSnapshotBeforeUpdate(L,i),n.__reactInternalSnapshotBeforeUpdate=e}catch(x){oe(a,a.return,x)}}break;case 3:if((e&1024)!==0){if(e=t.stateNode.containerInfo,a=e.nodeType,a===9)qo(e);else if(a===1)switch(e.nodeName){case"HEAD":case"HTML":case"BODY":qo(e);break;default:e.textContent=""}}break;case 5:case 26:case 27:case 6:case 4:case 17:break;default:if((e&1024)!==0)throw Error(f(163))}if(e=t.sibling,e!==null){e.return=t.return,Re=e;break}Re=t.return}}function qc(e,t,a){var n=a.flags;switch(a.tag){case 0:case 11:case 15:ea(e,a),n&4&&Fn(5,a);break;case 1:if(ea(e,a),n&4)if(e=a.stateNode,t===null)try{e.componentDidMount()}catch(r){oe(a,a.return,r)}else{var l=Da(a.type,t.memoizedProps);t=t.memoizedState;try{e.componentDidUpdate(l,t,e.__reactInternalSnapshotBeforeUpdate)}catch(r){oe(a,a.return,r)}}n&64&&xc(a),n&512&&el(a,a.return);break;case 3:if(ea(e,a),n&64&&(e=a.updateQueue,e!==null)){if(t=null,a.child!==null)switch(a.child.tag){case 27:case 5:t=a.child.stateNode;break;case 1:t=a.child.stateNode}try{Tu(e,t)}catch(r){oe(a,a.return,r)}}break;case 27:t===null&&n&4&&Lc(a);case 26:case 5:ea(e,a),t===null&&n&4&&zc(a),n&512&&el(a,a.return);break;case 12:ea(e,a);break;case 13:ea(e,a),n&4&&Gc(e,a),n&64&&(e=a.memoizedState,e!==null&&(e=e.dehydrated,e!==null&&(a=Gh.bind(null,a),rm(e,a))));break;case 22:if(n=a.memoizedState!==null||Ht,!n){t=t!==null&&t.memoizedState!==null||ge,l=Ht;var i=ge;Ht=n,(ge=t)&&!i?ta(e,a,(a.subtreeFlags&8772)!==0):ea(e,a),Ht=l,ge=i}break;case 30:break;default:ea(e,a)}}function Yc(e){var t=e.alternate;t!==null&&(e.alternate=null,Yc(t)),e.child=null,e.deletions=null,e.sibling=null,e.tag===5&&(t=e.stateNode,t!==null&&ji(t)),e.stateNode=null,e.return=null,e.dependencies=null,e.memoizedProps=null,e.memoizedState=null,e.pendingProps=null,e.stateNode=null,e.updateQueue=null}var de=null,Ge=!1;function qt(e,t,a){for(a=a.child;a!==null;)Vc(e,t,a),a=a.sibling}function Vc(e,t,a){if(Pe&&typeof Pe.onCommitFiberUnmount=="function")try{Pe.onCommitFiberUnmount(Sn,a)}catch{}switch(a.tag){case 26:ge||At(a,t),qt(e,t,a),a.memoizedState?a.memoizedState.count--:a.stateNode&&(a=a.stateNode,a.parentNode.removeChild(a));break;case 27:ge||At(a,t);var n=de,l=Ge;oa(a.type)&&(de=a.stateNode,Ge=!1),qt(e,t,a),ul(a.stateNode),de=n,Ge=l;break;case 5:ge||At(a,t);case 6:if(n=de,l=Ge,de=null,qt(e,t,a),de=n,Ge=l,de!==null)if(Ge)try{(de.nodeType===9?de.body:de.nodeName==="HTML"?de.ownerDocument.body:de).removeChild(a.stateNode)}catch(i){oe(a,t,i)}else try{de.removeChild(a.stateNode)}catch(i){oe(a,t,i)}break;case 18:de!==null&&(Ge?(e=de,kd(e.nodeType===9?e.body:e.nodeName==="HTML"?e.ownerDocument.body:e,a.stateNode),yl(e)):kd(de,a.stateNode));break;case 4:n=de,l=Ge,de=a.stateNode.containerInfo,Ge=!0,qt(e,t,a),de=n,Ge=l;break;case 0:case 11:case 14:case 15:ge||Ft(2,a,t),ge||Ft(4,a,t),qt(e,t,a);break;case 1:ge||(At(a,t),n=a.stateNode,typeof n.componentWillUnmount=="function"&&Cc(a,t,n)),qt(e,t,a);break;case 21:qt(e,t,a);break;case 22:ge=(n=ge)||a.memoizedState!==null,qt(e,t,a),ge=n;break;default:qt(e,t,a)}}function Gc(e,t){if(t.memoizedState===null&&(e=t.alternate,e!==null&&(e=e.memoizedState,e!==null&&(e=e.dehydrated,e!==null))))try{yl(e)}catch(a){oe(t,t.return,a)}}function Ch(e){switch(e.tag){case 13:case 19:var t=e.stateNode;return t===null&&(t=e.stateNode=new Hc),t;case 22:return e=e.stateNode,t=e._retryCache,t===null&&(t=e._retryCache=new Hc),t;default:throw Error(f(435,e.tag))}}function fo(e,t){var a=Ch(e);t.forEach(function(n){var l=jh.bind(null,e,n);a.has(n)||(a.add(n),n.then(l,l))})}function Je(e,t){var a=t.deletions;if(a!==null)for(var n=0;n<a.length;n++){var l=a[n],i=e,r=t,o=r;e:for(;o!==null;){switch(o.tag){case 27:if(oa(o.type)){de=o.stateNode,Ge=!1;break e}break;case 5:de=o.stateNode,Ge=!1;break e;case 3:case 4:de=o.stateNode.containerInfo,Ge=!0;break e}o=o.return}if(de===null)throw Error(f(160));Vc(i,r,l),de=null,Ge=!1,i=l.alternate,i!==null&&(i.return=null),l.return=null}if(t.subtreeFlags&13878)for(t=t.child;t!==null;)jc(t,e),t=t.sibling}var pt=null;function jc(e,t){var a=e.alternate,n=e.flags;switch(e.tag){case 0:case 11:case 14:case 15:Je(t,e),Fe(e),n&4&&(Ft(3,e,e.return),Fn(3,e),Ft(5,e,e.return));break;case 1:Je(t,e),Fe(e),n&512&&(ge||a===null||At(a,a.return)),n&64&&Ht&&(e=e.updateQueue,e!==null&&(n=e.callbacks,n!==null&&(a=e.shared.hiddenCallbacks,e.shared.hiddenCallbacks=a===null?n:a.concat(n))));break;case 26:var l=pt;if(Je(t,e),Fe(e),n&512&&(ge||a===null||At(a,a.return)),n&4){var i=a!==null?a.memoizedState:null;if(n=e.memoizedState,a===null)if(n===null)if(e.stateNode===null){e:{n=e.type,a=e.memoizedProps,l=l.ownerDocument||l;t:switch(n){case"title":i=l.getElementsByTagName("title")[0],(!i||i[En]||i[Ce]||i.namespaceURI==="http://www.w3.org/2000/svg"||i.hasAttribute("itemprop"))&&(i=l.createElement(n),l.head.insertBefore(i,l.querySelector("head > title"))),De(i,n,a),i[Ce]=e,Ee(i),n=i;break e;case"link":var r=Ld("link","href",l).get(n+(a.href||""));if(r){for(var o=0;o<r.length;o++)if(i=r[o],i.getAttribute("href")===(a.href==null||a.href===""?null:a.href)&&i.getAttribute("rel")===(a.rel==null?null:a.rel)&&i.getAttribute("title")===(a.title==null?null:a.title)&&i.getAttribute("crossorigin")===(a.crossOrigin==null?null:a.crossOrigin)){r.splice(o,1);break t}}i=l.createElement(n),De(i,n,a),l.head.appendChild(i);break;case"meta":if(r=Ld("meta","content",l).get(n+(a.content||""))){for(o=0;o<r.length;o++)if(i=r[o],i.getAttribute("content")===(a.content==null?null:""+a.content)&&i.getAttribute("name")===(a.name==null?null:a.name)&&i.getAttribute("property")===(a.property==null?null:a.property)&&i.getAttribute("http-equiv")===(a.httpEquiv==null?null:a.httpEquiv)&&i.getAttribute("charset")===(a.charSet==null?null:a.charSet)){r.splice(o,1);break t}}i=l.createElement(n),De(i,n,a),l.head.appendChild(i);break;default:throw Error(f(468,n))}i[Ce]=e,Ee(i),n=i}e.stateNode=n}else Hd(l,e.type,e.stateNode);else e.stateNode=Ud(l,n,e.memoizedProps);else i!==n?(i===null?a.stateNode!==null&&(a=a.stateNode,a.parentNode.removeChild(a)):i.count--,n===null?Hd(l,e.type,e.stateNode):Ud(l,n,e.memoizedProps)):n===null&&e.stateNode!==null&&oo(e,e.memoizedProps,a.memoizedProps)}break;case 27:Je(t,e),Fe(e),n&512&&(ge||a===null||At(a,a.return)),a!==null&&n&4&&oo(e,e.memoizedProps,a.memoizedProps);break;case 5:if(Je(t,e),Fe(e),n&512&&(ge||a===null||At(a,a.return)),e.flags&32){l=e.stateNode;try{Ga(l,"")}catch(g){oe(e,e.return,g)}}n&4&&e.stateNode!=null&&(l=e.memoizedProps,oo(e,l,a!==null?a.memoizedProps:l)),n&1024&&(co=!0);break;case 6:if(Je(t,e),Fe(e),n&4){if(e.stateNode===null)throw Error(f(162));n=e.memoizedProps,a=e.stateNode;try{a.nodeValue=n}catch(g){oe(e,e.return,g)}}break;case 3:if(Oi=null,l=pt,pt=Ai(t.containerInfo),Je(t,e),pt=l,Fe(e),n&4&&a!==null&&a.memoizedState.isDehydrated)try{yl(t.containerInfo)}catch(g){oe(e,e.return,g)}co&&(co=!1,Qc(e));break;case 4:n=pt,pt=Ai(e.stateNode.containerInfo),Je(t,e),Fe(e),pt=n;break;case 12:Je(t,e),Fe(e);break;case 13:Je(t,e),Fe(e),e.child.flags&8192&&e.memoizedState!==null!=(a!==null&&a.memoizedState!==null)&&(vo=Tt()),n&4&&(n=e.updateQueue,n!==null&&(e.updateQueue=null,fo(e,n)));break;case 22:l=e.memoizedState!==null;var s=a!==null&&a.memoizedState!==null,m=Ht,y=ge;if(Ht=m||l,ge=y||s,Je(t,e),ge=y,Ht=m,Fe(e),n&8192)e:for(t=e.stateNode,t._visibility=l?t._visibility&-2:t._visibility|1,l&&(a===null||s||Ht||ge||Ba(e)),a=null,t=e;;){if(t.tag===5||t.tag===26){if(a===null){s=a=t;try{if(i=s.stateNode,l)r=i.style,typeof r.setProperty=="function"?r.setProperty("display","none","important"):r.display="none";else{o=s.stateNode;var T=s.memoizedProps.style,p=T!=null&&T.hasOwnProperty("display")?T.display:null;o.style.display=p==null||typeof p=="boolean"?"":(""+p).trim()}}catch(g){oe(s,s.return,g)}}}else if(t.tag===6){if(a===null){s=t;try{s.stateNode.nodeValue=l?"":s.memoizedProps}catch(g){oe(s,s.return,g)}}}else if((t.tag!==22&&t.tag!==23||t.memoizedState===null||t===e)&&t.child!==null){t.child.return=t,t=t.child;continue}if(t===e)break e;for(;t.sibling===null;){if(t.return===null||t.return===e)break e;a===t&&(a=null),t=t.return}a===t&&(a=null),t.sibling.return=t.return,t=t.sibling}n&4&&(n=e.updateQueue,n!==null&&(a=n.retryQueue,a!==null&&(n.retryQueue=null,fo(e,a))));break;case 19:Je(t,e),Fe(e),n&4&&(n=e.updateQueue,n!==null&&(e.updateQueue=null,fo(e,n)));break;case 30:break;case 21:break;default:Je(t,e),Fe(e)}}function Fe(e){var t=e.flags;if(t&2){try{for(var a,n=e.return;n!==null;){if(Uc(n)){a=n;break}n=n.return}if(a==null)throw Error(f(160));switch(a.tag){case 27:var l=a.stateNode,i=so(e);ci(e,i,l);break;case 5:var r=a.stateNode;a.flags&32&&(Ga(r,""),a.flags&=-33);var o=so(e);ci(e,o,r);break;case 3:case 4:var s=a.stateNode.containerInfo,m=so(e);uo(e,m,s);break;default:throw Error(f(161))}}catch(y){oe(e,e.return,y)}e.flags&=-3}t&4096&&(e.flags&=-4097)}function Qc(e){if(e.subtreeFlags&1024)for(e=e.child;e!==null;){var t=e;Qc(t),t.tag===5&&t.flags&1024&&t.stateNode.reset(),e=e.sibling}}function ea(e,t){if(t.subtreeFlags&8772)for(t=t.child;t!==null;)qc(e,t.alternate,t),t=t.sibling}function Ba(e){for(e=e.child;e!==null;){var t=e;switch(t.tag){case 0:case 11:case 14:case 15:Ft(4,t,t.return),Ba(t);break;case 1:At(t,t.return);var a=t.stateNode;typeof a.componentWillUnmount=="function"&&Cc(t,t.return,a),Ba(t);break;case 27:ul(t.stateNode);case 26:case 5:At(t,t.return),Ba(t);break;case 22:t.memoizedState===null&&Ba(t);break;case 30:Ba(t);break;default:Ba(t)}e=e.sibling}}function ta(e,t,a){for(a=a&&(t.subtreeFlags&8772)!==0,t=t.child;t!==null;){var n=t.alternate,l=e,i=t,r=i.flags;switch(i.tag){case 0:case 11:case 15:ta(l,i,a),Fn(4,i);break;case 1:if(ta(l,i,a),n=i,l=n.stateNode,typeof l.componentDidMount=="function")try{l.componentDidMount()}catch(m){oe(n,n.return,m)}if(n=i,l=n.updateQueue,l!==null){var o=n.stateNode;try{var s=l.shared.hiddenCallbacks;if(s!==null)for(l.shared.hiddenCallbacks=null,l=0;l<s.length;l++)bu(s[l],o)}catch(m){oe(n,n.return,m)}}a&&r&64&&xc(i),el(i,i.return);break;case 27:Lc(i);case 26:case 5:ta(l,i,a),a&&n===null&&r&4&&zc(i),el(i,i.return);break;case 12:ta(l,i,a);break;case 13:ta(l,i,a),a&&r&4&&Gc(l,i);break;case 22:i.memoizedState===null&&ta(l,i,a),el(i,i.return);break;case 30:break;default:ta(l,i,a)}t=t.sibling}}function ho(e,t){var a=null;e!==null&&e.memoizedState!==null&&e.memoizedState.cachePool!==null&&(a=e.memoizedState.cachePool.pool),e=null,t.memoizedState!==null&&t.memoizedState.cachePool!==null&&(e=t.memoizedState.cachePool.pool),e!==a&&(e!=null&&e.refCount++,a!=null&&qn(a))}function mo(e,t){e=null,t.alternate!==null&&(e=t.alternate.memoizedState.cache),t=t.memoizedState.cache,t!==e&&(t.refCount++,e!=null&&qn(e))}function Et(e,t,a,n){if(t.subtreeFlags&10256)for(t=t.child;t!==null;)Kc(e,t,a,n),t=t.sibling}function Kc(e,t,a,n){var l=t.flags;switch(t.tag){case 0:case 11:case 15:Et(e,t,a,n),l&2048&&Fn(9,t);break;case 1:Et(e,t,a,n);break;case 3:Et(e,t,a,n),l&2048&&(e=null,t.alternate!==null&&(e=t.alternate.memoizedState.cache),t=t.memoizedState.cache,t!==e&&(t.refCount++,e!=null&&qn(e)));break;case 12:if(l&2048){Et(e,t,a,n),e=t.stateNode;try{var i=t.memoizedProps,r=i.id,o=i.onPostCommit;typeof o=="function"&&o(r,t.alternate===null?"mount":"update",e.passiveEffectDuration,-0)}catch(s){oe(t,t.return,s)}}else Et(e,t,a,n);break;case 13:Et(e,t,a,n);break;case 23:break;case 22:i=t.stateNode,r=t.alternate,t.memoizedState!==null?i._visibility&2?Et(e,t,a,n):tl(e,t):i._visibility&2?Et(e,t,a,n):(i._visibility|=2,sn(e,t,a,n,(t.subtreeFlags&10256)!==0)),l&2048&&ho(r,t);break;case 24:Et(e,t,a,n),l&2048&&mo(t.alternate,t);break;default:Et(e,t,a,n)}}function sn(e,t,a,n,l){for(l=l&&(t.subtreeFlags&10256)!==0,t=t.child;t!==null;){var i=e,r=t,o=a,s=n,m=r.flags;switch(r.tag){case 0:case 11:case 15:sn(i,r,o,s,l),Fn(8,r);break;case 23:break;case 22:var y=r.stateNode;r.memoizedState!==null?y._visibility&2?sn(i,r,o,s,l):tl(i,r):(y._visibility|=2,sn(i,r,o,s,l)),l&&m&2048&&ho(r.alternate,r);break;case 24:sn(i,r,o,s,l),l&&m&2048&&mo(r.alternate,r);break;default:sn(i,r,o,s,l)}t=t.sibling}}function tl(e,t){if(t.subtreeFlags&10256)for(t=t.child;t!==null;){var a=e,n=t,l=n.flags;switch(n.tag){case 22:tl(a,n),l&2048&&ho(n.alternate,n);break;case 24:tl(a,n),l&2048&&mo(n.alternate,n);break;default:tl(a,n)}t=t.sibling}}var al=8192;function un(e){if(e.subtreeFlags&al)for(e=e.child;e!==null;)Xc(e),e=e.sibling}function Xc(e){switch(e.tag){case 26:un(e),e.flags&al&&e.memoizedState!==null&&bm(pt,e.memoizedState,e.memoizedProps);break;case 5:un(e);break;case 3:case 4:var t=pt;pt=Ai(e.stateNode.containerInfo),un(e),pt=t;break;case 22:e.memoizedState===null&&(t=e.alternate,t!==null&&t.memoizedState!==null?(t=al,al=16777216,un(e),al=t):un(e));break;default:un(e)}}function Ic(e){var t=e.alternate;if(t!==null&&(e=t.child,e!==null)){t.child=null;do t=e.sibling,e.sibling=null,e=t;while(e!==null)}}function nl(e){var t=e.deletions;if((e.flags&16)!==0){if(t!==null)for(var a=0;a<t.length;a++){var n=t[a];Re=n,Zc(n,e)}Ic(e)}if(e.subtreeFlags&10256)for(e=e.child;e!==null;)Pc(e),e=e.sibling}function Pc(e){switch(e.tag){case 0:case 11:case 15:nl(e),e.flags&2048&&Ft(9,e,e.return);break;case 3:nl(e);break;case 12:nl(e);break;case 22:var t=e.stateNode;e.memoizedState!==null&&t._visibility&2&&(e.return===null||e.return.tag!==13)?(t._visibility&=-3,di(e)):nl(e);break;default:nl(e)}}function di(e){var t=e.deletions;if((e.flags&16)!==0){if(t!==null)for(var a=0;a<t.length;a++){var n=t[a];Re=n,Zc(n,e)}Ic(e)}for(e=e.child;e!==null;){switch(t=e,t.tag){case 0:case 11:case 15:Ft(8,t,t.return),di(t);break;case 22:a=t.stateNode,a._visibility&2&&(a._visibility&=-3,di(t));break;default:di(t)}e=e.sibling}}function Zc(e,t){for(;Re!==null;){var a=Re;switch(a.tag){case 0:case 11:case 15:Ft(8,a,t);break;case 23:case 22:if(a.memoizedState!==null&&a.memoizedState.cachePool!==null){var n=a.memoizedState.cachePool.pool;n!=null&&n.refCount++}break;case 24:qn(a.memoizedState.cache)}if(n=a.child,n!==null)n.return=a,Re=n;else e:for(a=e;Re!==null;){n=Re;var l=n.sibling,i=n.return;if(Yc(n),n===a){Re=null;break e}if(l!==null){l.return=i,Re=l;break e}Re=i}}}var zh={getCacheForType:function(e){var t=ze(Se),a=t.data.get(e);return a===void 0&&(a=e(),t.data.set(e,a)),a}},Uh=typeof WeakMap=="function"?WeakMap:Map,te=0,se=null,K=null,P=0,ae=0,et=null,aa=!1,cn=!1,po=!1,Yt=0,me=0,na=0,_a=0,go=0,dt=0,dn=0,ll=null,je=null,yo=!1,vo=0,fi=1/0,hi=null,la=null,ke=0,ia=null,fn=null,hn=0,bo=0,To=null,Wc=null,il=0,So=null;function tt(){if((te&2)!==0&&P!==0)return P&-P;if(v.T!==null){var e=Fa;return e!==0?e:No()}return fs()}function $c(){dt===0&&(dt=(P&536870912)===0||ee?ss():536870912);var e=ct.current;return e!==null&&(e.flags|=32),dt}function at(e,t,a){(e===se&&(ae===2||ae===9)||e.cancelPendingCommit!==null)&&(mn(e,0),ra(e,P,dt,!1)),An(e,a),((te&2)===0||e!==se)&&(e===se&&((te&2)===0&&(_a|=a),me===4&&ra(e,P,dt,!1)),Ot(e))}function Jc(e,t,a){if((te&6)!==0)throw Error(f(327));var n=!a&&(t&124)===0&&(t&e.expiredLanes)===0||wn(e,t),l=n?qh(e,t):Eo(e,t,!0),i=n;do{if(l===0){cn&&!n&&ra(e,t,0,!1);break}else{if(a=e.current.alternate,i&&!Lh(a)){l=Eo(e,t,!1),i=!1;continue}if(l===2){if(i=t,e.errorRecoveryDisabledLanes&i)var r=0;else r=e.pendingLanes&-536870913,r=r!==0?r:r&536870912?536870912:0;if(r!==0){t=r;e:{var o=e;l=ll;var s=o.current.memoizedState.isDehydrated;if(s&&(mn(o,r).flags|=256),r=Eo(o,r,!1),r!==2){if(po&&!s){o.errorRecoveryDisabledLanes|=i,_a|=i,l=4;break e}i=je,je=l,i!==null&&(je===null?je=i:je.push.apply(je,i))}l=r}if(i=!1,l!==2)continue}}if(l===1){mn(e,0),ra(e,t,0,!0);break}e:{switch(n=e,i=l,i){case 0:case 1:throw Error(f(345));case 4:if((t&4194048)!==t)break;case 6:ra(n,t,dt,!aa);break e;case 2:je=null;break;case 3:case 5:break;default:throw Error(f(329))}if((t&62914560)===t&&(l=vo+300-Tt(),10<l)){if(ra(n,t,dt,!aa),El(n,0,!0)!==0)break e;n.timeoutHandle=Md(Fc.bind(null,n,a,je,hi,yo,t,dt,_a,dn,aa,i,2,-0,0),l);break e}Fc(n,a,je,hi,yo,t,dt,_a,dn,aa,i,0,-0,0)}}break}while(!0);Ot(e)}function Fc(e,t,a,n,l,i,r,o,s,m,y,T,p,g){if(e.timeoutHandle=-1,T=t.subtreeFlags,(T&8192||(T&16785408)===16785408)&&(fl={stylesheets:null,count:0,unsuspend:vm},Xc(t),T=Tm(),T!==null)){e.cancelPendingCommit=T(rd.bind(null,e,t,i,a,n,l,r,o,s,y,1,p,g)),ra(e,i,r,!m);return}rd(e,t,i,a,n,l,r,o,s)}function Lh(e){for(var t=e;;){var a=t.tag;if((a===0||a===11||a===15)&&t.flags&16384&&(a=t.updateQueue,a!==null&&(a=a.stores,a!==null)))for(var n=0;n<a.length;n++){var l=a[n],i=l.getSnapshot;l=l.value;try{if(!We(i(),l))return!1}catch{return!1}}if(a=t.child,t.subtreeFlags&16384&&a!==null)a.return=t,t=a;else{if(t===e)break;for(;t.sibling===null;){if(t.return===null||t.return===e)return!0;t=t.return}t.sibling.return=t.return,t=t.sibling}}return!0}function ra(e,t,a,n){t&=~go,t&=~_a,e.suspendedLanes|=t,e.pingedLanes&=~t,n&&(e.warmLanes|=t),n=e.expirationTimes;for(var l=t;0<l;){var i=31-Ze(l),r=1<<i;n[i]=-1,l&=~r}a!==0&&cs(e,a,t)}function mi(){return(te&6)===0?(rl(0),!1):!0}function wo(){if(K!==null){if(ae===0)var e=K.return;else e=K,_t=Ra=null,Hr(e),rn=null,Wn=0,e=K;for(;e!==null;)_c(e.alternate,e),e=e.return;K=null}}function mn(e,t){var a=e.timeoutHandle;a!==-1&&(e.timeoutHandle=-1,tm(a)),a=e.cancelPendingCommit,a!==null&&(e.cancelPendingCommit=null,a()),wo(),se=e,K=a=kt(e.current,null),P=t,ae=0,et=null,aa=!1,cn=wn(e,t),po=!1,dn=dt=go=_a=na=me=0,je=ll=null,yo=!1,(t&8)!==0&&(t|=t&32);var n=e.entangledLanes;if(n!==0)for(e=e.entanglements,n&=t;0<n;){var l=31-Ze(n),i=1<<l;t|=e[l],n&=~i}return Yt=t,Ul(),a}function ed(e,t){j=null,v.H=ti,t===Vn||t===Kl?(t=yu(),ae=3):t===mu?(t=yu(),ae=4):ae=t===vc?8:t!==null&&typeof t=="object"&&typeof t.then=="function"?6:1,et=t,K===null&&(me=1,ri(e,rt(t,e.current)))}function td(){var e=v.H;return v.H=ti,e===null?ti:e}function ad(){var e=v.A;return v.A=zh,e}function Ao(){me=4,aa||(P&4194048)!==P&&ct.current!==null||(cn=!0),(na&134217727)===0&&(_a&134217727)===0||se===null||ra(se,P,dt,!1)}function Eo(e,t,a){var n=te;te|=2;var l=td(),i=ad();(se!==e||P!==t)&&(hi=null,mn(e,t)),t=!1;var r=me;e:do try{if(ae!==0&&K!==null){var o=K,s=et;switch(ae){case 8:wo(),r=6;break e;case 3:case 2:case 9:case 6:ct.current===null&&(t=!0);var m=ae;if(ae=0,et=null,pn(e,o,s,m),a&&cn){r=0;break e}break;default:m=ae,ae=0,et=null,pn(e,o,s,m)}}Hh(),r=me;break}catch(y){ed(e,y)}while(!0);return t&&e.shellSuspendCounter++,_t=Ra=null,te=n,v.H=l,v.A=i,K===null&&(se=null,P=0,Ul()),r}function Hh(){for(;K!==null;)nd(K)}function qh(e,t){var a=te;te|=2;var n=td(),l=ad();se!==e||P!==t?(hi=null,fi=Tt()+500,mn(e,t)):cn=wn(e,t);e:do try{if(ae!==0&&K!==null){t=K;var i=et;t:switch(ae){case 1:ae=0,et=null,pn(e,t,i,1);break;case 2:case 9:if(pu(i)){ae=0,et=null,ld(t);break}t=function(){ae!==2&&ae!==9||se!==e||(ae=7),Ot(e)},i.then(t,t);break e;case 3:ae=7;break e;case 4:ae=5;break e;case 7:pu(i)?(ae=0,et=null,ld(t)):(ae=0,et=null,pn(e,t,i,7));break;case 5:var r=null;switch(K.tag){case 26:r=K.memoizedState;case 5:case 27:var o=K;if(!r||qd(r)){ae=0,et=null;var s=o.sibling;if(s!==null)K=s;else{var m=o.return;m!==null?(K=m,pi(m)):K=null}break t}}ae=0,et=null,pn(e,t,i,5);break;case 6:ae=0,et=null,pn(e,t,i,6);break;case 8:wo(),me=6;break e;default:throw Error(f(462))}}Yh();break}catch(y){ed(e,y)}while(!0);return _t=Ra=null,v.H=n,v.A=l,te=a,K!==null?0:(se=null,P=0,Ul(),me)}function Yh(){for(;K!==null&&!sf();)nd(K)}function nd(e){var t=Dc(e.alternate,e,Yt);e.memoizedProps=e.pendingProps,t===null?pi(e):K=t}function ld(e){var t=e,a=t.alternate;switch(t.tag){case 15:case 0:t=Ec(a,t,t.pendingProps,t.type,void 0,P);break;case 11:t=Ec(a,t,t.pendingProps,t.type.render,t.ref,P);break;case 5:Hr(t);default:_c(a,t),t=K=iu(t,Yt),t=Dc(a,t,Yt)}e.memoizedProps=e.pendingProps,t===null?pi(e):K=t}function pn(e,t,a,n){_t=Ra=null,Hr(t),rn=null,Wn=0;var l=t.return;try{if(kh(e,l,t,a,P)){me=1,ri(e,rt(a,e.current)),K=null;return}}catch(i){if(l!==null)throw K=l,i;me=1,ri(e,rt(a,e.current)),K=null;return}t.flags&32768?(ee||n===1?e=!0:cn||(P&536870912)!==0?e=!1:(aa=e=!0,(n===2||n===9||n===3||n===6)&&(n=ct.current,n!==null&&n.tag===13&&(n.flags|=16384))),id(t,e)):pi(t)}function pi(e){var t=e;do{if((t.flags&32768)!==0){id(t,aa);return}e=t.return;var a=Bh(t.alternate,t,Yt);if(a!==null){K=a;return}if(t=t.sibling,t!==null){K=t;return}K=t=e}while(t!==null);me===0&&(me=5)}function id(e,t){do{var a=_h(e.alternate,e);if(a!==null){a.flags&=32767,K=a;return}if(a=e.return,a!==null&&(a.flags|=32768,a.subtreeFlags=0,a.deletions=null),!t&&(e=e.sibling,e!==null)){K=e;return}K=e=a}while(e!==null);me=6,K=null}function rd(e,t,a,n,l,i,r,o,s){e.cancelPendingCommit=null;do gi();while(ke!==0);if((te&6)!==0)throw Error(f(327));if(t!==null){if(t===e.current)throw Error(f(177));if(i=t.lanes|t.childLanes,i|=hr,vf(e,a,i,r,o,s),e===se&&(K=se=null,P=0),fn=t,ia=e,hn=a,bo=i,To=l,Wc=n,(t.subtreeFlags&10256)!==0||(t.flags&10256)!==0?(e.callbackNode=null,e.callbackPriority=0,Qh(Sl,function(){return dd(),null})):(e.callbackNode=null,e.callbackPriority=0),n=(t.flags&13878)!==0,(t.subtreeFlags&13878)!==0||n){n=v.T,v.T=null,l=R.p,R.p=2,r=te,te|=4;try{xh(e,t,a)}finally{te=r,R.p=l,v.T=n}}ke=1,od(),sd(),ud()}}function od(){if(ke===1){ke=0;var e=ia,t=fn,a=(t.flags&13878)!==0;if((t.subtreeFlags&13878)!==0||a){a=v.T,v.T=null;var n=R.p;R.p=2;var l=te;te|=4;try{jc(t,e);var i=Uo,r=Zs(e.containerInfo),o=i.focusedElem,s=i.selectionRange;if(r!==o&&o&&o.ownerDocument&&Ps(o.ownerDocument.documentElement,o)){if(s!==null&&sr(o)){var m=s.start,y=s.end;if(y===void 0&&(y=m),"selectionStart"in o)o.selectionStart=m,o.selectionEnd=Math.min(y,o.value.length);else{var T=o.ownerDocument||document,p=T&&T.defaultView||window;if(p.getSelection){var g=p.getSelection(),L=o.textContent.length,x=Math.min(s.start,L),ie=s.end===void 0?x:Math.min(s.end,L);!g.extend&&x>ie&&(r=ie,ie=x,x=r);var d=Is(o,x),c=Is(o,ie);if(d&&c&&(g.rangeCount!==1||g.anchorNode!==d.node||g.anchorOffset!==d.offset||g.focusNode!==c.node||g.focusOffset!==c.offset)){var h=T.createRange();h.setStart(d.node,d.offset),g.removeAllRanges(),x>ie?(g.addRange(h),g.extend(c.node,c.offset)):(h.setEnd(c.node,c.offset),g.addRange(h))}}}}for(T=[],g=o;g=g.parentNode;)g.nodeType===1&&T.push({element:g,left:g.scrollLeft,top:g.scrollTop});for(typeof o.focus=="function"&&o.focus(),o=0;o<T.length;o++){var b=T[o];b.element.scrollLeft=b.left,b.element.scrollTop=b.top}}Ni=!!zo,Uo=zo=null}finally{te=l,R.p=n,v.T=a}}e.current=t,ke=2}}function sd(){if(ke===2){ke=0;var e=ia,t=fn,a=(t.flags&8772)!==0;if((t.subtreeFlags&8772)!==0||a){a=v.T,v.T=null;var n=R.p;R.p=2;var l=te;te|=4;try{qc(e,t.alternate,t)}finally{te=l,R.p=n,v.T=a}}ke=3}}function ud(){if(ke===4||ke===3){ke=0,uf();var e=ia,t=fn,a=hn,n=Wc;(t.subtreeFlags&10256)!==0||(t.flags&10256)!==0?ke=5:(ke=0,fn=ia=null,cd(e,e.pendingLanes));var l=e.pendingLanes;if(l===0&&(la=null),Vi(a),t=t.stateNode,Pe&&typeof Pe.onCommitFiberRoot=="function")try{Pe.onCommitFiberRoot(Sn,t,void 0,(t.current.flags&128)===128)}catch{}if(n!==null){t=v.T,l=R.p,R.p=2,v.T=null;try{for(var i=e.onRecoverableError,r=0;r<n.length;r++){var o=n[r];i(o.value,{componentStack:o.stack})}}finally{v.T=t,R.p=l}}(hn&3)!==0&&gi(),Ot(e),l=e.pendingLanes,(a&4194090)!==0&&(l&42)!==0?e===So?il++:(il=0,So=e):il=0,rl(0)}}function cd(e,t){(e.pooledCacheLanes&=t)===0&&(t=e.pooledCache,t!=null&&(e.pooledCache=null,qn(t)))}function gi(e){return od(),sd(),ud(),dd()}function dd(){if(ke!==5)return!1;var e=ia,t=bo;bo=0;var a=Vi(hn),n=v.T,l=R.p;try{R.p=32>a?32:a,v.T=null,a=To,To=null;var i=ia,r=hn;if(ke=0,fn=ia=null,hn=0,(te&6)!==0)throw Error(f(331));var o=te;if(te|=4,Pc(i.current),Kc(i,i.current,r,a),te=o,rl(0,!1),Pe&&typeof Pe.onPostCommitFiberRoot=="function")try{Pe.onPostCommitFiberRoot(Sn,i)}catch{}return!0}finally{R.p=l,v.T=n,cd(e,t)}}function fd(e,t,a){t=rt(a,t),t=Jr(e.stateNode,t,2),e=Zt(e,t,2),e!==null&&(An(e,2),Ot(e))}function oe(e,t,a){if(e.tag===3)fd(e,e,a);else for(;t!==null;){if(t.tag===3){fd(t,e,a);break}else if(t.tag===1){var n=t.stateNode;if(typeof t.type.getDerivedStateFromError=="function"||typeof n.componentDidCatch=="function"&&(la===null||!la.has(n))){e=rt(a,e),a=gc(2),n=Zt(t,a,2),n!==null&&(yc(a,n,t,e),An(n,2),Ot(n));break}}t=t.return}}function Oo(e,t,a){var n=e.pingCache;if(n===null){n=e.pingCache=new Uh;var l=new Set;n.set(t,l)}else l=n.get(t),l===void 0&&(l=new Set,n.set(t,l));l.has(a)||(po=!0,l.add(a),e=Vh.bind(null,e,t,a),t.then(e,e))}function Vh(e,t,a){var n=e.pingCache;n!==null&&n.delete(t),e.pingedLanes|=e.suspendedLanes&a,e.warmLanes&=~a,se===e&&(P&a)===a&&(me===4||me===3&&(P&62914560)===P&&300>Tt()-vo?(te&2)===0&&mn(e,0):go|=a,dn===P&&(dn=0)),Ot(e)}function hd(e,t){t===0&&(t=us()),e=Za(e,t),e!==null&&(An(e,t),Ot(e))}function Gh(e){var t=e.memoizedState,a=0;t!==null&&(a=t.retryLane),hd(e,a)}function jh(e,t){var a=0;switch(e.tag){case 13:var n=e.stateNode,l=e.memoizedState;l!==null&&(a=l.retryLane);break;case 19:n=e.stateNode;break;case 22:n=e.stateNode._retryCache;break;default:throw Error(f(314))}n!==null&&n.delete(t),hd(e,a)}function Qh(e,t){return Li(e,t)}var yi=null,gn=null,Ro=!1,vi=!1,Mo=!1,xa=0;function Ot(e){e!==gn&&e.next===null&&(gn===null?yi=gn=e:gn=gn.next=e),vi=!0,Ro||(Ro=!0,Xh())}function rl(e,t){if(!Mo&&vi){Mo=!0;do for(var a=!1,n=yi;n!==null;){if(e!==0){var l=n.pendingLanes;if(l===0)var i=0;else{var r=n.suspendedLanes,o=n.pingedLanes;i=(1<<31-Ze(42|e)+1)-1,i&=l&~(r&~o),i=i&201326741?i&201326741|1:i?i|2:0}i!==0&&(a=!0,yd(n,i))}else i=P,i=El(n,n===se?i:0,n.cancelPendingCommit!==null||n.timeoutHandle!==-1),(i&3)===0||wn(n,i)||(a=!0,yd(n,i));n=n.next}while(a);Mo=!1}}function Kh(){md()}function md(){vi=Ro=!1;var e=0;xa!==0&&(em()&&(e=xa),xa=0);for(var t=Tt(),a=null,n=yi;n!==null;){var l=n.next,i=pd(n,t);i===0?(n.next=null,a===null?yi=l:a.next=l,l===null&&(gn=a)):(a=n,(e!==0||(i&3)!==0)&&(vi=!0)),n=l}rl(e)}function pd(e,t){for(var a=e.suspendedLanes,n=e.pingedLanes,l=e.expirationTimes,i=e.pendingLanes&-62914561;0<i;){var r=31-Ze(i),o=1<<r,s=l[r];s===-1?((o&a)===0||(o&n)!==0)&&(l[r]=yf(o,t)):s<=t&&(e.expiredLanes|=o),i&=~o}if(t=se,a=P,a=El(e,e===t?a:0,e.cancelPendingCommit!==null||e.timeoutHandle!==-1),n=e.callbackNode,a===0||e===t&&(ae===2||ae===9)||e.cancelPendingCommit!==null)return n!==null&&n!==null&&Hi(n),e.callbackNode=null,e.callbackPriority=0;if((a&3)===0||wn(e,a)){if(t=a&-a,t===e.callbackPriority)return t;switch(n!==null&&Hi(n),Vi(a)){case 2:case 8:a=rs;break;case 32:a=Sl;break;case 268435456:a=os;break;default:a=Sl}return n=gd.bind(null,e),a=Li(a,n),e.callbackPriority=t,e.callbackNode=a,t}return n!==null&&n!==null&&Hi(n),e.callbackPriority=2,e.callbackNode=null,2}function gd(e,t){if(ke!==0&&ke!==5)return e.callbackNode=null,e.callbackPriority=0,null;var a=e.callbackNode;if(gi()&&e.callbackNode!==a)return null;var n=P;return n=El(e,e===se?n:0,e.cancelPendingCommit!==null||e.timeoutHandle!==-1),n===0?null:(Jc(e,n,t),pd(e,Tt()),e.callbackNode!=null&&e.callbackNode===a?gd.bind(null,e):null)}function yd(e,t){if(gi())return null;Jc(e,t,!0)}function Xh(){am(function(){(te&6)!==0?Li(is,Kh):md()})}function No(){return xa===0&&(xa=ss()),xa}function vd(e){return e==null||typeof e=="symbol"||typeof e=="boolean"?null:typeof e=="function"?e:kl(""+e)}function bd(e,t){var a=t.ownerDocument.createElement("input");return a.name=t.name,a.value=t.value,e.id&&a.setAttribute("form",e.id),t.parentNode.insertBefore(a,t),e=new FormData(e),a.parentNode.removeChild(a),e}function Ih(e,t,a,n,l){if(t==="submit"&&a&&a.stateNode===l){var i=vd((l[qe]||null).action),r=n.submitter;r&&(t=(t=r[qe]||null)?vd(t.formAction):r.getAttribute("formAction"),t!==null&&(i=t,r=null));var o=new xl("action","action",null,n,l);e.push({event:o,listeners:[{instance:null,listener:function(){if(n.defaultPrevented){if(xa!==0){var s=r?bd(l,r):new FormData(l);Ir(a,{pending:!0,data:s,method:l.method,action:i},null,s)}}else typeof i=="function"&&(o.preventDefault(),s=r?bd(l,r):new FormData(l),Ir(a,{pending:!0,data:s,method:l.method,action:i},i,s))},currentTarget:l}]})}}for(var ko=0;ko<fr.length;ko++){var Do=fr[ko],Ph=Do.toLowerCase(),Zh=Do[0].toUpperCase()+Do.slice(1);mt(Ph,"on"+Zh)}mt(Js,"onAnimationEnd"),mt(Fs,"onAnimationIteration"),mt(eu,"onAnimationStart"),mt("dblclick","onDoubleClick"),mt("focusin","onFocus"),mt("focusout","onBlur"),mt(fh,"onTransitionRun"),mt(hh,"onTransitionStart"),mt(mh,"onTransitionCancel"),mt(tu,"onTransitionEnd"),qa("onMouseEnter",["mouseout","mouseover"]),qa("onMouseLeave",["mouseout","mouseover"]),qa("onPointerEnter",["pointerout","pointerover"]),qa("onPointerLeave",["pointerout","pointerover"]),ya("onChange","change click focusin focusout input keydown keyup selectionchange".split(" ")),ya("onSelect","focusout contextmenu dragend focusin keydown keyup mousedown mouseup selectionchange".split(" ")),ya("onBeforeInput",["compositionend","keypress","textInput","paste"]),ya("onCompositionEnd","compositionend focusout keydown keypress keyup mousedown".split(" ")),ya("onCompositionStart","compositionstart focusout keydown keypress keyup mousedown".split(" ")),ya("onCompositionUpdate","compositionupdate focusout keydown keypress keyup mousedown".split(" "));var ol="abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(" "),Wh=new Set("beforetoggle cancel close invalid load scroll scrollend toggle".split(" ").concat(ol));function Td(e,t){t=(t&4)!==0;for(var a=0;a<e.length;a++){var n=e[a],l=n.event;n=n.listeners;e:{var i=void 0;if(t)for(var r=n.length-1;0<=r;r--){var o=n[r],s=o.instance,m=o.currentTarget;if(o=o.listener,s!==i&&l.isPropagationStopped())break e;i=o,l.currentTarget=m;try{i(l)}catch(y){ii(y)}l.currentTarget=null,i=s}else for(r=0;r<n.length;r++){if(o=n[r],s=o.instance,m=o.currentTarget,o=o.listener,s!==i&&l.isPropagationStopped())break e;i=o,l.currentTarget=m;try{i(l)}catch(y){ii(y)}l.currentTarget=null,i=s}}}}function X(e,t){var a=t[Gi];a===void 0&&(a=t[Gi]=new Set);var n=e+"__bubble";a.has(n)||(Sd(t,e,2,!1),a.add(n))}function Bo(e,t,a){var n=0;t&&(n|=4),Sd(a,e,n,t)}var bi="_reactListening"+Math.random().toString(36).slice(2);function _o(e){if(!e[bi]){e[bi]=!0,ms.forEach(function(a){a!=="selectionchange"&&(Wh.has(a)||Bo(a,!1,e),Bo(a,!0,e))});var t=e.nodeType===9?e:e.ownerDocument;t===null||t[bi]||(t[bi]=!0,Bo("selectionchange",!1,t))}}function Sd(e,t,a,n){switch(Kd(t)){case 2:var l=Am;break;case 8:l=Em;break;default:l=Xo}a=l.bind(null,t,a,e),l=void 0,!Fi||t!=="touchstart"&&t!=="touchmove"&&t!=="wheel"||(l=!0),n?l!==void 0?e.addEventListener(t,a,{capture:!0,passive:l}):e.addEventListener(t,a,!0):l!==void 0?e.addEventListener(t,a,{passive:l}):e.addEventListener(t,a,!1)}function xo(e,t,a,n,l){var i=n;if((t&1)===0&&(t&2)===0&&n!==null)e:for(;;){if(n===null)return;var r=n.tag;if(r===3||r===4){var o=n.stateNode.containerInfo;if(o===l)break;if(r===4)for(r=n.return;r!==null;){var s=r.tag;if((s===3||s===4)&&r.stateNode.containerInfo===l)return;r=r.return}for(;o!==null;){if(r=Ua(o),r===null)return;if(s=r.tag,s===5||s===6||s===26||s===27){n=i=r;continue e}o=o.parentNode}}n=n.return}Ns(function(){var m=i,y=$i(a),T=[];e:{var p=au.get(e);if(p!==void 0){var g=xl,L=e;switch(e){case"keypress":if(Bl(a)===0)break e;case"keydown":case"keyup":g=Qf;break;case"focusin":L="focus",g=nr;break;case"focusout":L="blur",g=nr;break;case"beforeblur":case"afterblur":g=nr;break;case"click":if(a.button===2)break e;case"auxclick":case"dblclick":case"mousedown":case"mousemove":case"mouseup":case"mouseout":case"mouseover":case"contextmenu":g=Bs;break;case"drag":case"dragend":case"dragenter":case"dragexit":case"dragleave":case"dragover":case"dragstart":case"drop":g=_f;break;case"touchcancel":case"touchend":case"touchmove":case"touchstart":g=If;break;case Js:case Fs:case eu:g=zf;break;case tu:g=Zf;break;case"scroll":case"scrollend":g=Df;break;case"wheel":g=$f;break;case"copy":case"cut":case"paste":g=Lf;break;case"gotpointercapture":case"lostpointercapture":case"pointercancel":case"pointerdown":case"pointermove":case"pointerout":case"pointerover":case"pointerup":g=xs;break;case"toggle":case"beforetoggle":g=Ff}var x=(t&4)!==0,ie=!x&&(e==="scroll"||e==="scrollend"),d=x?p!==null?p+"Capture":null:p;x=[];for(var c=m,h;c!==null;){var b=c;if(h=b.stateNode,b=b.tag,b!==5&&b!==26&&b!==27||h===null||d===null||(b=Rn(c,d),b!=null&&x.push(sl(c,b,h))),ie)break;c=c.return}0<x.length&&(p=new g(p,L,null,a,y),T.push({event:p,listeners:x}))}}if((t&7)===0){e:{if(p=e==="mouseover"||e==="pointerover",g=e==="mouseout"||e==="pointerout",p&&a!==Wi&&(L=a.relatedTarget||a.fromElement)&&(Ua(L)||L[za]))break e;if((g||p)&&(p=y.window===y?y:(p=y.ownerDocument)?p.defaultView||p.parentWindow:window,g?(L=a.relatedTarget||a.toElement,g=m,L=L?Ua(L):null,L!==null&&(ie=H(L),x=L.tag,L!==ie||x!==5&&x!==27&&x!==6)&&(L=null)):(g=null,L=m),g!==L)){if(x=Bs,b="onMouseLeave",d="onMouseEnter",c="mouse",(e==="pointerout"||e==="pointerover")&&(x=xs,b="onPointerLeave",d="onPointerEnter",c="pointer"),ie=g==null?p:On(g),h=L==null?p:On(L),p=new x(b,c+"leave",g,a,y),p.target=ie,p.relatedTarget=h,b=null,Ua(y)===m&&(x=new x(d,c+"enter",L,a,y),x.target=h,x.relatedTarget=ie,b=x),ie=b,g&&L)t:{for(x=g,d=L,c=0,h=x;h;h=yn(h))c++;for(h=0,b=d;b;b=yn(b))h++;for(;0<c-h;)x=yn(x),c--;for(;0<h-c;)d=yn(d),h--;for(;c--;){if(x===d||d!==null&&x===d.alternate)break t;x=yn(x),d=yn(d)}x=null}else x=null;g!==null&&wd(T,p,g,x,!1),L!==null&&ie!==null&&wd(T,ie,L,x,!0)}}e:{if(p=m?On(m):window,g=p.nodeName&&p.nodeName.toLowerCase(),g==="select"||g==="input"&&p.type==="file")var N=Vs;else if(qs(p))if(Gs)N=uh;else{N=oh;var Q=rh}else g=p.nodeName,!g||g.toLowerCase()!=="input"||p.type!=="checkbox"&&p.type!=="radio"?m&&Zi(m.elementType)&&(N=Vs):N=sh;if(N&&(N=N(e,m))){Ys(T,N,a,y);break e}Q&&Q(e,p,m),e==="focusout"&&m&&p.type==="number"&&m.memoizedProps.value!=null&&Pi(p,"number",p.value)}switch(Q=m?On(m):window,e){case"focusin":(qs(Q)||Q.contentEditable==="true")&&(Xa=Q,ur=m,Cn=null);break;case"focusout":Cn=ur=Xa=null;break;case"mousedown":cr=!0;break;case"contextmenu":case"mouseup":case"dragend":cr=!1,Ws(T,a,y);break;case"selectionchange":if(dh)break;case"keydown":case"keyup":Ws(T,a,y)}var k;if(ir)e:{switch(e){case"compositionstart":var C="onCompositionStart";break e;case"compositionend":C="onCompositionEnd";break e;case"compositionupdate":C="onCompositionUpdate";break e}C=void 0}else Ka?Ls(e,a)&&(C="onCompositionEnd"):e==="keydown"&&a.keyCode===229&&(C="onCompositionStart");C&&(Cs&&a.locale!=="ko"&&(Ka||C!=="onCompositionStart"?C==="onCompositionEnd"&&Ka&&(k=ks()):(Kt=y,er="value"in Kt?Kt.value:Kt.textContent,Ka=!0)),Q=Ti(m,C),0<Q.length&&(C=new _s(C,e,null,a,y),T.push({event:C,listeners:Q}),k?C.data=k:(k=Hs(a),k!==null&&(C.data=k)))),(k=th?ah(e,a):nh(e,a))&&(C=Ti(m,"onBeforeInput"),0<C.length&&(Q=new _s("onBeforeInput","beforeinput",null,a,y),T.push({event:Q,listeners:C}),Q.data=k)),Ih(T,e,m,a,y)}Td(T,t)})}function sl(e,t,a){return{instance:e,listener:t,currentTarget:a}}function Ti(e,t){for(var a=t+"Capture",n=[];e!==null;){var l=e,i=l.stateNode;if(l=l.tag,l!==5&&l!==26&&l!==27||i===null||(l=Rn(e,a),l!=null&&n.unshift(sl(e,l,i)),l=Rn(e,t),l!=null&&n.push(sl(e,l,i))),e.tag===3)return n;e=e.return}return[]}function yn(e){if(e===null)return null;do e=e.return;while(e&&e.tag!==5&&e.tag!==27);return e||null}function wd(e,t,a,n,l){for(var i=t._reactName,r=[];a!==null&&a!==n;){var o=a,s=o.alternate,m=o.stateNode;if(o=o.tag,s!==null&&s===n)break;o!==5&&o!==26&&o!==27||m===null||(s=m,l?(m=Rn(a,i),m!=null&&r.unshift(sl(a,m,s))):l||(m=Rn(a,i),m!=null&&r.push(sl(a,m,s)))),a=a.return}r.length!==0&&e.push({event:t,listeners:r})}var $h=/\r\n?/g,Jh=/\u0000|\uFFFD/g;function Ad(e){return(typeof e=="string"?e:""+e).replace($h,`
`).replace(Jh,"")}function Ed(e,t){return t=Ad(t),Ad(e)===t}function Si(){}function le(e,t,a,n,l,i){switch(a){case"children":typeof n=="string"?t==="body"||t==="textarea"&&n===""||Ga(e,n):(typeof n=="number"||typeof n=="bigint")&&t!=="body"&&Ga(e,""+n);break;case"className":Rl(e,"class",n);break;case"tabIndex":Rl(e,"tabindex",n);break;case"dir":case"role":case"viewBox":case"width":case"height":Rl(e,a,n);break;case"style":Rs(e,n,i);break;case"data":if(t!=="object"){Rl(e,"data",n);break}case"src":case"href":if(n===""&&(t!=="a"||a!=="href")){e.removeAttribute(a);break}if(n==null||typeof n=="function"||typeof n=="symbol"||typeof n=="boolean"){e.removeAttribute(a);break}n=kl(""+n),e.setAttribute(a,n);break;case"action":case"formAction":if(typeof n=="function"){e.setAttribute(a,"javascript:throw new Error('A React form was unexpectedly submitted. If you called form.submit() manually, consider using form.requestSubmit() instead. If you\\'re trying to use event.stopPropagation() in a submit event handler, consider also calling event.preventDefault().')");break}else typeof i=="function"&&(a==="formAction"?(t!=="input"&&le(e,t,"name",l.name,l,null),le(e,t,"formEncType",l.formEncType,l,null),le(e,t,"formMethod",l.formMethod,l,null),le(e,t,"formTarget",l.formTarget,l,null)):(le(e,t,"encType",l.encType,l,null),le(e,t,"method",l.method,l,null),le(e,t,"target",l.target,l,null)));if(n==null||typeof n=="symbol"||typeof n=="boolean"){e.removeAttribute(a);break}n=kl(""+n),e.setAttribute(a,n);break;case"onClick":n!=null&&(e.onclick=Si);break;case"onScroll":n!=null&&X("scroll",e);break;case"onScrollEnd":n!=null&&X("scrollend",e);break;case"dangerouslySetInnerHTML":if(n!=null){if(typeof n!="object"||!("__html"in n))throw Error(f(61));if(a=n.__html,a!=null){if(l.children!=null)throw Error(f(60));e.innerHTML=a}}break;case"multiple":e.multiple=n&&typeof n!="function"&&typeof n!="symbol";break;case"muted":e.muted=n&&typeof n!="function"&&typeof n!="symbol";break;case"suppressContentEditableWarning":case"suppressHydrationWarning":case"defaultValue":case"defaultChecked":case"innerHTML":case"ref":break;case"autoFocus":break;case"xlinkHref":if(n==null||typeof n=="function"||typeof n=="boolean"||typeof n=="symbol"){e.removeAttribute("xlink:href");break}a=kl(""+n),e.setAttributeNS("http://www.w3.org/1999/xlink","xlink:href",a);break;case"contentEditable":case"spellCheck":case"draggable":case"value":case"autoReverse":case"externalResourcesRequired":case"focusable":case"preserveAlpha":n!=null&&typeof n!="function"&&typeof n!="symbol"?e.setAttribute(a,""+n):e.removeAttribute(a);break;case"inert":case"allowFullScreen":case"async":case"autoPlay":case"controls":case"default":case"defer":case"disabled":case"disablePictureInPicture":case"disableRemotePlayback":case"formNoValidate":case"hidden":case"loop":case"noModule":case"noValidate":case"open":case"playsInline":case"readOnly":case"required":case"reversed":case"scoped":case"seamless":case"itemScope":n&&typeof n!="function"&&typeof n!="symbol"?e.setAttribute(a,""):e.removeAttribute(a);break;case"capture":case"download":n===!0?e.setAttribute(a,""):n!==!1&&n!=null&&typeof n!="function"&&typeof n!="symbol"?e.setAttribute(a,n):e.removeAttribute(a);break;case"cols":case"rows":case"size":case"span":n!=null&&typeof n!="function"&&typeof n!="symbol"&&!isNaN(n)&&1<=n?e.setAttribute(a,n):e.removeAttribute(a);break;case"rowSpan":case"start":n==null||typeof n=="function"||typeof n=="symbol"||isNaN(n)?e.removeAttribute(a):e.setAttribute(a,n);break;case"popover":X("beforetoggle",e),X("toggle",e),Ol(e,"popover",n);break;case"xlinkActuate":Mt(e,"http://www.w3.org/1999/xlink","xlink:actuate",n);break;case"xlinkArcrole":Mt(e,"http://www.w3.org/1999/xlink","xlink:arcrole",n);break;case"xlinkRole":Mt(e,"http://www.w3.org/1999/xlink","xlink:role",n);break;case"xlinkShow":Mt(e,"http://www.w3.org/1999/xlink","xlink:show",n);break;case"xlinkTitle":Mt(e,"http://www.w3.org/1999/xlink","xlink:title",n);break;case"xlinkType":Mt(e,"http://www.w3.org/1999/xlink","xlink:type",n);break;case"xmlBase":Mt(e,"http://www.w3.org/XML/1998/namespace","xml:base",n);break;case"xmlLang":Mt(e,"http://www.w3.org/XML/1998/namespace","xml:lang",n);break;case"xmlSpace":Mt(e,"http://www.w3.org/XML/1998/namespace","xml:space",n);break;case"is":Ol(e,"is",n);break;case"innerText":case"textContent":break;default:(!(2<a.length)||a[0]!=="o"&&a[0]!=="O"||a[1]!=="n"&&a[1]!=="N")&&(a=Nf.get(a)||a,Ol(e,a,n))}}function Co(e,t,a,n,l,i){switch(a){case"style":Rs(e,n,i);break;case"dangerouslySetInnerHTML":if(n!=null){if(typeof n!="object"||!("__html"in n))throw Error(f(61));if(a=n.__html,a!=null){if(l.children!=null)throw Error(f(60));e.innerHTML=a}}break;case"children":typeof n=="string"?Ga(e,n):(typeof n=="number"||typeof n=="bigint")&&Ga(e,""+n);break;case"onScroll":n!=null&&X("scroll",e);break;case"onScrollEnd":n!=null&&X("scrollend",e);break;case"onClick":n!=null&&(e.onclick=Si);break;case"suppressContentEditableWarning":case"suppressHydrationWarning":case"innerHTML":case"ref":break;case"innerText":case"textContent":break;default:if(!ps.hasOwnProperty(a))e:{if(a[0]==="o"&&a[1]==="n"&&(l=a.endsWith("Capture"),t=a.slice(2,l?a.length-7:void 0),i=e[qe]||null,i=i!=null?i[a]:null,typeof i=="function"&&e.removeEventListener(t,i,l),typeof n=="function")){typeof i!="function"&&i!==null&&(a in e?e[a]=null:e.hasAttribute(a)&&e.removeAttribute(a)),e.addEventListener(t,n,l);break e}a in e?e[a]=n:n===!0?e.setAttribute(a,""):Ol(e,a,n)}}}function De(e,t,a){switch(t){case"div":case"span":case"svg":case"path":case"a":case"g":case"p":case"li":break;case"img":X("error",e),X("load",e);var n=!1,l=!1,i;for(i in a)if(a.hasOwnProperty(i)){var r=a[i];if(r!=null)switch(i){case"src":n=!0;break;case"srcSet":l=!0;break;case"children":case"dangerouslySetInnerHTML":throw Error(f(137,t));default:le(e,t,i,r,a,null)}}l&&le(e,t,"srcSet",a.srcSet,a,null),n&&le(e,t,"src",a.src,a,null);return;case"input":X("invalid",e);var o=i=r=l=null,s=null,m=null;for(n in a)if(a.hasOwnProperty(n)){var y=a[n];if(y!=null)switch(n){case"name":l=y;break;case"type":r=y;break;case"checked":s=y;break;case"defaultChecked":m=y;break;case"value":i=y;break;case"defaultValue":o=y;break;case"children":case"dangerouslySetInnerHTML":if(y!=null)throw Error(f(137,t));break;default:le(e,t,n,y,a,null)}}ws(e,i,o,s,m,r,l,!1),Ml(e);return;case"select":X("invalid",e),n=r=i=null;for(l in a)if(a.hasOwnProperty(l)&&(o=a[l],o!=null))switch(l){case"value":i=o;break;case"defaultValue":r=o;break;case"multiple":n=o;default:le(e,t,l,o,a,null)}t=i,a=r,e.multiple=!!n,t!=null?Va(e,!!n,t,!1):a!=null&&Va(e,!!n,a,!0);return;case"textarea":X("invalid",e),i=l=n=null;for(r in a)if(a.hasOwnProperty(r)&&(o=a[r],o!=null))switch(r){case"value":n=o;break;case"defaultValue":l=o;break;case"children":i=o;break;case"dangerouslySetInnerHTML":if(o!=null)throw Error(f(91));break;default:le(e,t,r,o,a,null)}Es(e,n,l,i),Ml(e);return;case"option":for(s in a)a.hasOwnProperty(s)&&(n=a[s],n!=null)&&(s==="selected"?e.selected=n&&typeof n!="function"&&typeof n!="symbol":le(e,t,s,n,a,null));return;case"dialog":X("beforetoggle",e),X("toggle",e),X("cancel",e),X("close",e);break;case"iframe":case"object":X("load",e);break;case"video":case"audio":for(n=0;n<ol.length;n++)X(ol[n],e);break;case"image":X("error",e),X("load",e);break;case"details":X("toggle",e);break;case"embed":case"source":case"link":X("error",e),X("load",e);case"area":case"base":case"br":case"col":case"hr":case"keygen":case"meta":case"param":case"track":case"wbr":case"menuitem":for(m in a)if(a.hasOwnProperty(m)&&(n=a[m],n!=null))switch(m){case"children":case"dangerouslySetInnerHTML":throw Error(f(137,t));default:le(e,t,m,n,a,null)}return;default:if(Zi(t)){for(y in a)a.hasOwnProperty(y)&&(n=a[y],n!==void 0&&Co(e,t,y,n,a,void 0));return}}for(o in a)a.hasOwnProperty(o)&&(n=a[o],n!=null&&le(e,t,o,n,a,null))}function Fh(e,t,a,n){switch(t){case"div":case"span":case"svg":case"path":case"a":case"g":case"p":case"li":break;case"input":var l=null,i=null,r=null,o=null,s=null,m=null,y=null;for(g in a){var T=a[g];if(a.hasOwnProperty(g)&&T!=null)switch(g){case"checked":break;case"value":break;case"defaultValue":s=T;default:n.hasOwnProperty(g)||le(e,t,g,null,n,T)}}for(var p in n){var g=n[p];if(T=a[p],n.hasOwnProperty(p)&&(g!=null||T!=null))switch(p){case"type":i=g;break;case"name":l=g;break;case"checked":m=g;break;case"defaultChecked":y=g;break;case"value":r=g;break;case"defaultValue":o=g;break;case"children":case"dangerouslySetInnerHTML":if(g!=null)throw Error(f(137,t));break;default:g!==T&&le(e,t,p,g,n,T)}}Ii(e,r,o,s,m,y,i,l);return;case"select":g=r=o=p=null;for(i in a)if(s=a[i],a.hasOwnProperty(i)&&s!=null)switch(i){case"value":break;case"multiple":g=s;default:n.hasOwnProperty(i)||le(e,t,i,null,n,s)}for(l in n)if(i=n[l],s=a[l],n.hasOwnProperty(l)&&(i!=null||s!=null))switch(l){case"value":p=i;break;case"defaultValue":o=i;break;case"multiple":r=i;default:i!==s&&le(e,t,l,i,n,s)}t=o,a=r,n=g,p!=null?Va(e,!!a,p,!1):!!n!=!!a&&(t!=null?Va(e,!!a,t,!0):Va(e,!!a,a?[]:"",!1));return;case"textarea":g=p=null;for(o in a)if(l=a[o],a.hasOwnProperty(o)&&l!=null&&!n.hasOwnProperty(o))switch(o){case"value":break;case"children":break;default:le(e,t,o,null,n,l)}for(r in n)if(l=n[r],i=a[r],n.hasOwnProperty(r)&&(l!=null||i!=null))switch(r){case"value":p=l;break;case"defaultValue":g=l;break;case"children":break;case"dangerouslySetInnerHTML":if(l!=null)throw Error(f(91));break;default:l!==i&&le(e,t,r,l,n,i)}As(e,p,g);return;case"option":for(var L in a)p=a[L],a.hasOwnProperty(L)&&p!=null&&!n.hasOwnProperty(L)&&(L==="selected"?e.selected=!1:le(e,t,L,null,n,p));for(s in n)p=n[s],g=a[s],n.hasOwnProperty(s)&&p!==g&&(p!=null||g!=null)&&(s==="selected"?e.selected=p&&typeof p!="function"&&typeof p!="symbol":le(e,t,s,p,n,g));return;case"img":case"link":case"area":case"base":case"br":case"col":case"embed":case"hr":case"keygen":case"meta":case"param":case"source":case"track":case"wbr":case"menuitem":for(var x in a)p=a[x],a.hasOwnProperty(x)&&p!=null&&!n.hasOwnProperty(x)&&le(e,t,x,null,n,p);for(m in n)if(p=n[m],g=a[m],n.hasOwnProperty(m)&&p!==g&&(p!=null||g!=null))switch(m){case"children":case"dangerouslySetInnerHTML":if(p!=null)throw Error(f(137,t));break;default:le(e,t,m,p,n,g)}return;default:if(Zi(t)){for(var ie in a)p=a[ie],a.hasOwnProperty(ie)&&p!==void 0&&!n.hasOwnProperty(ie)&&Co(e,t,ie,void 0,n,p);for(y in n)p=n[y],g=a[y],!n.hasOwnProperty(y)||p===g||p===void 0&&g===void 0||Co(e,t,y,p,n,g);return}}for(var d in a)p=a[d],a.hasOwnProperty(d)&&p!=null&&!n.hasOwnProperty(d)&&le(e,t,d,null,n,p);for(T in n)p=n[T],g=a[T],!n.hasOwnProperty(T)||p===g||p==null&&g==null||le(e,t,T,p,n,g)}var zo=null,Uo=null;function wi(e){return e.nodeType===9?e:e.ownerDocument}function Od(e){switch(e){case"http://www.w3.org/2000/svg":return 1;case"http://www.w3.org/1998/Math/MathML":return 2;default:return 0}}function Rd(e,t){if(e===0)switch(t){case"svg":return 1;case"math":return 2;default:return 0}return e===1&&t==="foreignObject"?0:e}function Lo(e,t){return e==="textarea"||e==="noscript"||typeof t.children=="string"||typeof t.children=="number"||typeof t.children=="bigint"||typeof t.dangerouslySetInnerHTML=="object"&&t.dangerouslySetInnerHTML!==null&&t.dangerouslySetInnerHTML.__html!=null}var Ho=null;function em(){var e=window.event;return e&&e.type==="popstate"?e===Ho?!1:(Ho=e,!0):(Ho=null,!1)}var Md=typeof setTimeout=="function"?setTimeout:void 0,tm=typeof clearTimeout=="function"?clearTimeout:void 0,Nd=typeof Promise=="function"?Promise:void 0,am=typeof queueMicrotask=="function"?queueMicrotask:typeof Nd<"u"?function(e){return Nd.resolve(null).then(e).catch(nm)}:Md;function nm(e){setTimeout(function(){throw e})}function oa(e){return e==="head"}function kd(e,t){var a=t,n=0,l=0;do{var i=a.nextSibling;if(e.removeChild(a),i&&i.nodeType===8)if(a=i.data,a==="/$"){if(0<n&&8>n){a=n;var r=e.ownerDocument;if(a&1&&ul(r.documentElement),a&2&&ul(r.body),a&4)for(a=r.head,ul(a),r=a.firstChild;r;){var o=r.nextSibling,s=r.nodeName;r[En]||s==="SCRIPT"||s==="STYLE"||s==="LINK"&&r.rel.toLowerCase()==="stylesheet"||a.removeChild(r),r=o}}if(l===0){e.removeChild(i),yl(t);return}l--}else a==="$"||a==="$?"||a==="$!"?l++:n=a.charCodeAt(0)-48;else n=0;a=i}while(a);yl(t)}function qo(e){var t=e.firstChild;for(t&&t.nodeType===10&&(t=t.nextSibling);t;){var a=t;switch(t=t.nextSibling,a.nodeName){case"HTML":case"HEAD":case"BODY":qo(a),ji(a);continue;case"SCRIPT":case"STYLE":continue;case"LINK":if(a.rel.toLowerCase()==="stylesheet")continue}e.removeChild(a)}}function lm(e,t,a,n){for(;e.nodeType===1;){var l=a;if(e.nodeName.toLowerCase()!==t.toLowerCase()){if(!n&&(e.nodeName!=="INPUT"||e.type!=="hidden"))break}else if(n){if(!e[En])switch(t){case"meta":if(!e.hasAttribute("itemprop"))break;return e;case"link":if(i=e.getAttribute("rel"),i==="stylesheet"&&e.hasAttribute("data-precedence"))break;if(i!==l.rel||e.getAttribute("href")!==(l.href==null||l.href===""?null:l.href)||e.getAttribute("crossorigin")!==(l.crossOrigin==null?null:l.crossOrigin)||e.getAttribute("title")!==(l.title==null?null:l.title))break;return e;case"style":if(e.hasAttribute("data-precedence"))break;return e;case"script":if(i=e.getAttribute("src"),(i!==(l.src==null?null:l.src)||e.getAttribute("type")!==(l.type==null?null:l.type)||e.getAttribute("crossorigin")!==(l.crossOrigin==null?null:l.crossOrigin))&&i&&e.hasAttribute("async")&&!e.hasAttribute("itemprop"))break;return e;default:return e}}else if(t==="input"&&e.type==="hidden"){var i=l.name==null?null:""+l.name;if(l.type==="hidden"&&e.getAttribute("name")===i)return e}else return e;if(e=gt(e.nextSibling),e===null)break}return null}function im(e,t,a){if(t==="")return null;for(;e.nodeType!==3;)if((e.nodeType!==1||e.nodeName!=="INPUT"||e.type!=="hidden")&&!a||(e=gt(e.nextSibling),e===null))return null;return e}function Yo(e){return e.data==="$!"||e.data==="$?"&&e.ownerDocument.readyState==="complete"}function rm(e,t){var a=e.ownerDocument;if(e.data!=="$?"||a.readyState==="complete")t();else{var n=function(){t(),a.removeEventListener("DOMContentLoaded",n)};a.addEventListener("DOMContentLoaded",n),e._reactRetry=n}}function gt(e){for(;e!=null;e=e.nextSibling){var t=e.nodeType;if(t===1||t===3)break;if(t===8){if(t=e.data,t==="$"||t==="$!"||t==="$?"||t==="F!"||t==="F")break;if(t==="/$")return null}}return e}var Vo=null;function Dd(e){e=e.previousSibling;for(var t=0;e;){if(e.nodeType===8){var a=e.data;if(a==="$"||a==="$!"||a==="$?"){if(t===0)return e;t--}else a==="/$"&&t++}e=e.previousSibling}return null}function Bd(e,t,a){switch(t=wi(a),e){case"html":if(e=t.documentElement,!e)throw Error(f(452));return e;case"head":if(e=t.head,!e)throw Error(f(453));return e;case"body":if(e=t.body,!e)throw Error(f(454));return e;default:throw Error(f(451))}}function ul(e){for(var t=e.attributes;t.length;)e.removeAttributeNode(t[0]);ji(e)}var ft=new Map,_d=new Set;function Ai(e){return typeof e.getRootNode=="function"?e.getRootNode():e.nodeType===9?e:e.ownerDocument}var Vt=R.d;R.d={f:om,r:sm,D:um,C:cm,L:dm,m:fm,X:mm,S:hm,M:pm};function om(){var e=Vt.f(),t=mi();return e||t}function sm(e){var t=La(e);t!==null&&t.tag===5&&t.type==="form"?Ju(t):Vt.r(e)}var vn=typeof document>"u"?null:document;function xd(e,t,a){var n=vn;if(n&&typeof t=="string"&&t){var l=it(t);l='link[rel="'+e+'"][href="'+l+'"]',typeof a=="string"&&(l+='[crossorigin="'+a+'"]'),_d.has(l)||(_d.add(l),e={rel:e,crossOrigin:a,href:t},n.querySelector(l)===null&&(t=n.createElement("link"),De(t,"link",e),Ee(t),n.head.appendChild(t)))}}function um(e){Vt.D(e),xd("dns-prefetch",e,null)}function cm(e,t){Vt.C(e,t),xd("preconnect",e,t)}function dm(e,t,a){Vt.L(e,t,a);var n=vn;if(n&&e&&t){var l='link[rel="preload"][as="'+it(t)+'"]';t==="image"&&a&&a.imageSrcSet?(l+='[imagesrcset="'+it(a.imageSrcSet)+'"]',typeof a.imageSizes=="string"&&(l+='[imagesizes="'+it(a.imageSizes)+'"]')):l+='[href="'+it(e)+'"]';var i=l;switch(t){case"style":i=bn(e);break;case"script":i=Tn(e)}ft.has(i)||(e=D({rel:"preload",href:t==="image"&&a&&a.imageSrcSet?void 0:e,as:t},a),ft.set(i,e),n.querySelector(l)!==null||t==="style"&&n.querySelector(cl(i))||t==="script"&&n.querySelector(dl(i))||(t=n.createElement("link"),De(t,"link",e),Ee(t),n.head.appendChild(t)))}}function fm(e,t){Vt.m(e,t);var a=vn;if(a&&e){var n=t&&typeof t.as=="string"?t.as:"script",l='link[rel="modulepreload"][as="'+it(n)+'"][href="'+it(e)+'"]',i=l;switch(n){case"audioworklet":case"paintworklet":case"serviceworker":case"sharedworker":case"worker":case"script":i=Tn(e)}if(!ft.has(i)&&(e=D({rel:"modulepreload",href:e},t),ft.set(i,e),a.querySelector(l)===null)){switch(n){case"audioworklet":case"paintworklet":case"serviceworker":case"sharedworker":case"worker":case"script":if(a.querySelector(dl(i)))return}n=a.createElement("link"),De(n,"link",e),Ee(n),a.head.appendChild(n)}}}function hm(e,t,a){Vt.S(e,t,a);var n=vn;if(n&&e){var l=Ha(n).hoistableStyles,i=bn(e);t=t||"default";var r=l.get(i);if(!r){var o={loading:0,preload:null};if(r=n.querySelector(cl(i)))o.loading=5;else{e=D({rel:"stylesheet",href:e,"data-precedence":t},a),(a=ft.get(i))&&Go(e,a);var s=r=n.createElement("link");Ee(s),De(s,"link",e),s._p=new Promise(function(m,y){s.onload=m,s.onerror=y}),s.addEventListener("load",function(){o.loading|=1}),s.addEventListener("error",function(){o.loading|=2}),o.loading|=4,Ei(r,t,n)}r={type:"stylesheet",instance:r,count:1,state:o},l.set(i,r)}}}function mm(e,t){Vt.X(e,t);var a=vn;if(a&&e){var n=Ha(a).hoistableScripts,l=Tn(e),i=n.get(l);i||(i=a.querySelector(dl(l)),i||(e=D({src:e,async:!0},t),(t=ft.get(l))&&jo(e,t),i=a.createElement("script"),Ee(i),De(i,"link",e),a.head.appendChild(i)),i={type:"script",instance:i,count:1,state:null},n.set(l,i))}}function pm(e,t){Vt.M(e,t);var a=vn;if(a&&e){var n=Ha(a).hoistableScripts,l=Tn(e),i=n.get(l);i||(i=a.querySelector(dl(l)),i||(e=D({src:e,async:!0,type:"module"},t),(t=ft.get(l))&&jo(e,t),i=a.createElement("script"),Ee(i),De(i,"link",e),a.head.appendChild(i)),i={type:"script",instance:i,count:1,state:null},n.set(l,i))}}function Cd(e,t,a,n){var l=(l=Y.current)?Ai(l):null;if(!l)throw Error(f(446));switch(e){case"meta":case"title":return null;case"style":return typeof a.precedence=="string"&&typeof a.href=="string"?(t=bn(a.href),a=Ha(l).hoistableStyles,n=a.get(t),n||(n={type:"style",instance:null,count:0,state:null},a.set(t,n)),n):{type:"void",instance:null,count:0,state:null};case"link":if(a.rel==="stylesheet"&&typeof a.href=="string"&&typeof a.precedence=="string"){e=bn(a.href);var i=Ha(l).hoistableStyles,r=i.get(e);if(r||(l=l.ownerDocument||l,r={type:"stylesheet",instance:null,count:0,state:{loading:0,preload:null}},i.set(e,r),(i=l.querySelector(cl(e)))&&!i._p&&(r.instance=i,r.state.loading=5),ft.has(e)||(a={rel:"preload",as:"style",href:a.href,crossOrigin:a.crossOrigin,integrity:a.integrity,media:a.media,hrefLang:a.hrefLang,referrerPolicy:a.referrerPolicy},ft.set(e,a),i||gm(l,e,a,r.state))),t&&n===null)throw Error(f(528,""));return r}if(t&&n!==null)throw Error(f(529,""));return null;case"script":return t=a.async,a=a.src,typeof a=="string"&&t&&typeof t!="function"&&typeof t!="symbol"?(t=Tn(a),a=Ha(l).hoistableScripts,n=a.get(t),n||(n={type:"script",instance:null,count:0,state:null},a.set(t,n)),n):{type:"void",instance:null,count:0,state:null};default:throw Error(f(444,e))}}function bn(e){return'href="'+it(e)+'"'}function cl(e){return'link[rel="stylesheet"]['+e+"]"}function zd(e){return D({},e,{"data-precedence":e.precedence,precedence:null})}function gm(e,t,a,n){e.querySelector('link[rel="preload"][as="style"]['+t+"]")?n.loading=1:(t=e.createElement("link"),n.preload=t,t.addEventListener("load",function(){return n.loading|=1}),t.addEventListener("error",function(){return n.loading|=2}),De(t,"link",a),Ee(t),e.head.appendChild(t))}function Tn(e){return'[src="'+it(e)+'"]'}function dl(e){return"script[async]"+e}function Ud(e,t,a){if(t.count++,t.instance===null)switch(t.type){case"style":var n=e.querySelector('style[data-href~="'+it(a.href)+'"]');if(n)return t.instance=n,Ee(n),n;var l=D({},a,{"data-href":a.href,"data-precedence":a.precedence,href:null,precedence:null});return n=(e.ownerDocument||e).createElement("style"),Ee(n),De(n,"style",l),Ei(n,a.precedence,e),t.instance=n;case"stylesheet":l=bn(a.href);var i=e.querySelector(cl(l));if(i)return t.state.loading|=4,t.instance=i,Ee(i),i;n=zd(a),(l=ft.get(l))&&Go(n,l),i=(e.ownerDocument||e).createElement("link"),Ee(i);var r=i;return r._p=new Promise(function(o,s){r.onload=o,r.onerror=s}),De(i,"link",n),t.state.loading|=4,Ei(i,a.precedence,e),t.instance=i;case"script":return i=Tn(a.src),(l=e.querySelector(dl(i)))?(t.instance=l,Ee(l),l):(n=a,(l=ft.get(i))&&(n=D({},a),jo(n,l)),e=e.ownerDocument||e,l=e.createElement("script"),Ee(l),De(l,"link",n),e.head.appendChild(l),t.instance=l);case"void":return null;default:throw Error(f(443,t.type))}else t.type==="stylesheet"&&(t.state.loading&4)===0&&(n=t.instance,t.state.loading|=4,Ei(n,a.precedence,e));return t.instance}function Ei(e,t,a){for(var n=a.querySelectorAll('link[rel="stylesheet"][data-precedence],style[data-precedence]'),l=n.length?n[n.length-1]:null,i=l,r=0;r<n.length;r++){var o=n[r];if(o.dataset.precedence===t)i=o;else if(i!==l)break}i?i.parentNode.insertBefore(e,i.nextSibling):(t=a.nodeType===9?a.head:a,t.insertBefore(e,t.firstChild))}function Go(e,t){e.crossOrigin==null&&(e.crossOrigin=t.crossOrigin),e.referrerPolicy==null&&(e.referrerPolicy=t.referrerPolicy),e.title==null&&(e.title=t.title)}function jo(e,t){e.crossOrigin==null&&(e.crossOrigin=t.crossOrigin),e.referrerPolicy==null&&(e.referrerPolicy=t.referrerPolicy),e.integrity==null&&(e.integrity=t.integrity)}var Oi=null;function Ld(e,t,a){if(Oi===null){var n=new Map,l=Oi=new Map;l.set(a,n)}else l=Oi,n=l.get(a),n||(n=new Map,l.set(a,n));if(n.has(e))return n;for(n.set(e,null),a=a.getElementsByTagName(e),l=0;l<a.length;l++){var i=a[l];if(!(i[En]||i[Ce]||e==="link"&&i.getAttribute("rel")==="stylesheet")&&i.namespaceURI!=="http://www.w3.org/2000/svg"){var r=i.getAttribute(t)||"";r=e+r;var o=n.get(r);o?o.push(i):n.set(r,[i])}}return n}function Hd(e,t,a){e=e.ownerDocument||e,e.head.insertBefore(a,t==="title"?e.querySelector("head > title"):null)}function ym(e,t,a){if(a===1||t.itemProp!=null)return!1;switch(e){case"meta":case"title":return!0;case"style":if(typeof t.precedence!="string"||typeof t.href!="string"||t.href==="")break;return!0;case"link":if(typeof t.rel!="string"||typeof t.href!="string"||t.href===""||t.onLoad||t.onError)break;return t.rel==="stylesheet"?(e=t.disabled,typeof t.precedence=="string"&&e==null):!0;case"script":if(t.async&&typeof t.async!="function"&&typeof t.async!="symbol"&&!t.onLoad&&!t.onError&&t.src&&typeof t.src=="string")return!0}return!1}function qd(e){return!(e.type==="stylesheet"&&(e.state.loading&3)===0)}var fl=null;function vm(){}function bm(e,t,a){if(fl===null)throw Error(f(475));var n=fl;if(t.type==="stylesheet"&&(typeof a.media!="string"||matchMedia(a.media).matches!==!1)&&(t.state.loading&4)===0){if(t.instance===null){var l=bn(a.href),i=e.querySelector(cl(l));if(i){e=i._p,e!==null&&typeof e=="object"&&typeof e.then=="function"&&(n.count++,n=Ri.bind(n),e.then(n,n)),t.state.loading|=4,t.instance=i,Ee(i);return}i=e.ownerDocument||e,a=zd(a),(l=ft.get(l))&&Go(a,l),i=i.createElement("link"),Ee(i);var r=i;r._p=new Promise(function(o,s){r.onload=o,r.onerror=s}),De(i,"link",a),t.instance=i}n.stylesheets===null&&(n.stylesheets=new Map),n.stylesheets.set(t,e),(e=t.state.preload)&&(t.state.loading&3)===0&&(n.count++,t=Ri.bind(n),e.addEventListener("load",t),e.addEventListener("error",t))}}function Tm(){if(fl===null)throw Error(f(475));var e=fl;return e.stylesheets&&e.count===0&&Qo(e,e.stylesheets),0<e.count?function(t){var a=setTimeout(function(){if(e.stylesheets&&Qo(e,e.stylesheets),e.unsuspend){var n=e.unsuspend;e.unsuspend=null,n()}},6e4);return e.unsuspend=t,function(){e.unsuspend=null,clearTimeout(a)}}:null}function Ri(){if(this.count--,this.count===0){if(this.stylesheets)Qo(this,this.stylesheets);else if(this.unsuspend){var e=this.unsuspend;this.unsuspend=null,e()}}}var Mi=null;function Qo(e,t){e.stylesheets=null,e.unsuspend!==null&&(e.count++,Mi=new Map,t.forEach(Sm,e),Mi=null,Ri.call(e))}function Sm(e,t){if(!(t.state.loading&4)){var a=Mi.get(e);if(a)var n=a.get(null);else{a=new Map,Mi.set(e,a);for(var l=e.querySelectorAll("link[data-precedence],style[data-precedence]"),i=0;i<l.length;i++){var r=l[i];(r.nodeName==="LINK"||r.getAttribute("media")!=="not all")&&(a.set(r.dataset.precedence,r),n=r)}n&&a.set(null,n)}l=t.instance,r=l.getAttribute("data-precedence"),i=a.get(r)||n,i===n&&a.set(null,l),a.set(r,l),this.count++,n=Ri.bind(this),l.addEventListener("load",n),l.addEventListener("error",n),i?i.parentNode.insertBefore(l,i.nextSibling):(e=e.nodeType===9?e.head:e,e.insertBefore(l,e.firstChild)),t.state.loading|=4}}var hl={$$typeof:Te,Provider:null,Consumer:null,_currentValue:z,_currentValue2:z,_threadCount:0};function wm(e,t,a,n,l,i,r,o){this.tag=1,this.containerInfo=e,this.pingCache=this.current=this.pendingChildren=null,this.timeoutHandle=-1,this.callbackNode=this.next=this.pendingContext=this.context=this.cancelPendingCommit=null,this.callbackPriority=0,this.expirationTimes=qi(-1),this.entangledLanes=this.shellSuspendCounter=this.errorRecoveryDisabledLanes=this.expiredLanes=this.warmLanes=this.pingedLanes=this.suspendedLanes=this.pendingLanes=0,this.entanglements=qi(0),this.hiddenUpdates=qi(null),this.identifierPrefix=n,this.onUncaughtError=l,this.onCaughtError=i,this.onRecoverableError=r,this.pooledCache=null,this.pooledCacheLanes=0,this.formState=o,this.incompleteTransitions=new Map}function Yd(e,t,a,n,l,i,r,o,s,m,y,T){return e=new wm(e,t,a,r,o,s,m,T),t=1,i===!0&&(t|=24),i=$e(3,null,null,t),e.current=i,i.stateNode=e,t=Er(),t.refCount++,e.pooledCache=t,t.refCount++,i.memoizedState={element:n,isDehydrated:a,cache:t},Nr(i),e}function Vd(e){return e?(e=Wa,e):Wa}function Gd(e,t,a,n,l,i){l=Vd(l),n.context===null?n.context=l:n.pendingContext=l,n=Pt(t),n.payload={element:a},i=i===void 0?null:i,i!==null&&(n.callback=i),a=Zt(e,n,t),a!==null&&(at(a,e,t),jn(a,e,t))}function jd(e,t){if(e=e.memoizedState,e!==null&&e.dehydrated!==null){var a=e.retryLane;e.retryLane=a!==0&&a<t?a:t}}function Ko(e,t){jd(e,t),(e=e.alternate)&&jd(e,t)}function Qd(e){if(e.tag===13){var t=Za(e,67108864);t!==null&&at(t,e,67108864),Ko(e,67108864)}}var Ni=!0;function Am(e,t,a,n){var l=v.T;v.T=null;var i=R.p;try{R.p=2,Xo(e,t,a,n)}finally{R.p=i,v.T=l}}function Em(e,t,a,n){var l=v.T;v.T=null;var i=R.p;try{R.p=8,Xo(e,t,a,n)}finally{R.p=i,v.T=l}}function Xo(e,t,a,n){if(Ni){var l=Io(n);if(l===null)xo(e,t,n,ki,a),Xd(e,n);else if(Rm(l,e,t,a,n))n.stopPropagation();else if(Xd(e,n),t&4&&-1<Om.indexOf(e)){for(;l!==null;){var i=La(l);if(i!==null)switch(i.tag){case 3:if(i=i.stateNode,i.current.memoizedState.isDehydrated){var r=ga(i.pendingLanes);if(r!==0){var o=i;for(o.pendingLanes|=2,o.entangledLanes|=2;r;){var s=1<<31-Ze(r);o.entanglements[1]|=s,r&=~s}Ot(i),(te&6)===0&&(fi=Tt()+500,rl(0))}}break;case 13:o=Za(i,2),o!==null&&at(o,i,2),mi(),Ko(i,2)}if(i=Io(n),i===null&&xo(e,t,n,ki,a),i===l)break;l=i}l!==null&&n.stopPropagation()}else xo(e,t,n,null,a)}}function Io(e){return e=$i(e),Po(e)}var ki=null;function Po(e){if(ki=null,e=Ua(e),e!==null){var t=H(e);if(t===null)e=null;else{var a=t.tag;if(a===13){if(e=q(t),e!==null)return e;e=null}else if(a===3){if(t.stateNode.current.memoizedState.isDehydrated)return t.tag===3?t.stateNode.containerInfo:null;e=null}else t!==e&&(e=null)}}return ki=e,null}function Kd(e){switch(e){case"beforetoggle":case"cancel":case"click":case"close":case"contextmenu":case"copy":case"cut":case"auxclick":case"dblclick":case"dragend":case"dragstart":case"drop":case"focusin":case"focusout":case"input":case"invalid":case"keydown":case"keypress":case"keyup":case"mousedown":case"mouseup":case"paste":case"pause":case"play":case"pointercancel":case"pointerdown":case"pointerup":case"ratechange":case"reset":case"resize":case"seeked":case"submit":case"toggle":case"touchcancel":case"touchend":case"touchstart":case"volumechange":case"change":case"selectionchange":case"textInput":case"compositionstart":case"compositionend":case"compositionupdate":case"beforeblur":case"afterblur":case"beforeinput":case"blur":case"fullscreenchange":case"focus":case"hashchange":case"popstate":case"select":case"selectstart":return 2;case"drag":case"dragenter":case"dragexit":case"dragleave":case"dragover":case"mousemove":case"mouseout":case"mouseover":case"pointermove":case"pointerout":case"pointerover":case"scroll":case"touchmove":case"wheel":case"mouseenter":case"mouseleave":case"pointerenter":case"pointerleave":return 8;case"message":switch(cf()){case is:return 2;case rs:return 8;case Sl:case df:return 32;case os:return 268435456;default:return 32}default:return 32}}var Zo=!1,sa=null,ua=null,ca=null,ml=new Map,pl=new Map,da=[],Om="mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset".split(" ");function Xd(e,t){switch(e){case"focusin":case"focusout":sa=null;break;case"dragenter":case"dragleave":ua=null;break;case"mouseover":case"mouseout":ca=null;break;case"pointerover":case"pointerout":ml.delete(t.pointerId);break;case"gotpointercapture":case"lostpointercapture":pl.delete(t.pointerId)}}function gl(e,t,a,n,l,i){return e===null||e.nativeEvent!==i?(e={blockedOn:t,domEventName:a,eventSystemFlags:n,nativeEvent:i,targetContainers:[l]},t!==null&&(t=La(t),t!==null&&Qd(t)),e):(e.eventSystemFlags|=n,t=e.targetContainers,l!==null&&t.indexOf(l)===-1&&t.push(l),e)}function Rm(e,t,a,n,l){switch(t){case"focusin":return sa=gl(sa,e,t,a,n,l),!0;case"dragenter":return ua=gl(ua,e,t,a,n,l),!0;case"mouseover":return ca=gl(ca,e,t,a,n,l),!0;case"pointerover":var i=l.pointerId;return ml.set(i,gl(ml.get(i)||null,e,t,a,n,l)),!0;case"gotpointercapture":return i=l.pointerId,pl.set(i,gl(pl.get(i)||null,e,t,a,n,l)),!0}return!1}function Id(e){var t=Ua(e.target);if(t!==null){var a=H(t);if(a!==null){if(t=a.tag,t===13){if(t=q(a),t!==null){e.blockedOn=t,bf(e.priority,function(){if(a.tag===13){var n=tt();n=Yi(n);var l=Za(a,n);l!==null&&at(l,a,n),Ko(a,n)}});return}}else if(t===3&&a.stateNode.current.memoizedState.isDehydrated){e.blockedOn=a.tag===3?a.stateNode.containerInfo:null;return}}}e.blockedOn=null}function Di(e){if(e.blockedOn!==null)return!1;for(var t=e.targetContainers;0<t.length;){var a=Io(e.nativeEvent);if(a===null){a=e.nativeEvent;var n=new a.constructor(a.type,a);Wi=n,a.target.dispatchEvent(n),Wi=null}else return t=La(a),t!==null&&Qd(t),e.blockedOn=a,!1;t.shift()}return!0}function Pd(e,t,a){Di(e)&&a.delete(t)}function Mm(){Zo=!1,sa!==null&&Di(sa)&&(sa=null),ua!==null&&Di(ua)&&(ua=null),ca!==null&&Di(ca)&&(ca=null),ml.forEach(Pd),pl.forEach(Pd)}function Bi(e,t){e.blockedOn===t&&(e.blockedOn=null,Zo||(Zo=!0,E.unstable_scheduleCallback(E.unstable_NormalPriority,Mm)))}var _i=null;function Zd(e){_i!==e&&(_i=e,E.unstable_scheduleCallback(E.unstable_NormalPriority,function(){_i===e&&(_i=null);for(var t=0;t<e.length;t+=3){var a=e[t],n=e[t+1],l=e[t+2];if(typeof n!="function"){if(Po(n||a)===null)continue;break}var i=La(a);i!==null&&(e.splice(t,3),t-=3,Ir(i,{pending:!0,data:l,method:a.method,action:n},n,l))}}))}function yl(e){function t(s){return Bi(s,e)}sa!==null&&Bi(sa,e),ua!==null&&Bi(ua,e),ca!==null&&Bi(ca,e),ml.forEach(t),pl.forEach(t);for(var a=0;a<da.length;a++){var n=da[a];n.blockedOn===e&&(n.blockedOn=null)}for(;0<da.length&&(a=da[0],a.blockedOn===null);)Id(a),a.blockedOn===null&&da.shift();if(a=(e.ownerDocument||e).$$reactFormReplay,a!=null)for(n=0;n<a.length;n+=3){var l=a[n],i=a[n+1],r=l[qe]||null;if(typeof i=="function")r||Zd(a);else if(r){var o=null;if(i&&i.hasAttribute("formAction")){if(l=i,r=i[qe]||null)o=r.formAction;else if(Po(l)!==null)continue}else o=r.action;typeof o=="function"?a[n+1]=o:(a.splice(n,3),n-=3),Zd(a)}}}function Wo(e){this._internalRoot=e}xi.prototype.render=Wo.prototype.render=function(e){var t=this._internalRoot;if(t===null)throw Error(f(409));var a=t.current,n=tt();Gd(a,n,e,t,null,null)},xi.prototype.unmount=Wo.prototype.unmount=function(){var e=this._internalRoot;if(e!==null){this._internalRoot=null;var t=e.containerInfo;Gd(e.current,2,null,e,null,null),mi(),t[za]=null}};function xi(e){this._internalRoot=e}xi.prototype.unstable_scheduleHydration=function(e){if(e){var t=fs();e={blockedOn:null,target:e,priority:t};for(var a=0;a<da.length&&t!==0&&t<da[a].priority;a++);da.splice(a,0,e),a===0&&Id(e)}};var Wd=G.version;if(Wd!=="19.1.0")throw Error(f(527,Wd,"19.1.0"));R.findDOMNode=function(e){var t=e._reactInternals;if(t===void 0)throw typeof e.render=="function"?Error(f(188)):(e=Object.keys(e).join(","),Error(f(268,e)));return e=A(t),e=e!==null?S(e):null,e=e===null?null:e.stateNode,e};var Nm={bundleType:0,version:"19.1.0",rendererPackageName:"react-dom",currentDispatcherRef:v,reconcilerVersion:"19.1.0"};if(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__<"u"){var Ci=__REACT_DEVTOOLS_GLOBAL_HOOK__;if(!Ci.isDisabled&&Ci.supportsFiber)try{Sn=Ci.inject(Nm),Pe=Ci}catch{}}return bl.createRoot=function(e,t){if(!B(e))throw Error(f(299));var a=!1,n="",l=fc,i=hc,r=mc,o=null;return t!=null&&(t.unstable_strictMode===!0&&(a=!0),t.identifierPrefix!==void 0&&(n=t.identifierPrefix),t.onUncaughtError!==void 0&&(l=t.onUncaughtError),t.onCaughtError!==void 0&&(i=t.onCaughtError),t.onRecoverableError!==void 0&&(r=t.onRecoverableError),t.unstable_transitionCallbacks!==void 0&&(o=t.unstable_transitionCallbacks)),t=Yd(e,1,!1,null,null,a,n,l,i,r,o,null),e[za]=t.current,_o(e),new Wo(t)},bl.hydrateRoot=function(e,t,a){if(!B(e))throw Error(f(299));var n=!1,l="",i=fc,r=hc,o=mc,s=null,m=null;return a!=null&&(a.unstable_strictMode===!0&&(n=!0),a.identifierPrefix!==void 0&&(l=a.identifierPrefix),a.onUncaughtError!==void 0&&(i=a.onUncaughtError),a.onCaughtError!==void 0&&(r=a.onCaughtError),a.onRecoverableError!==void 0&&(o=a.onRecoverableError),a.unstable_transitionCallbacks!==void 0&&(s=a.unstable_transitionCallbacks),a.formState!==void 0&&(m=a.formState)),t=Yd(e,1,!0,t,a??null,n,l,i,r,o,s,m),t.context=Vd(null),a=t.current,n=tt(),n=Yi(n),l=Pt(n),l.callback=null,Zt(a,l,n),a=n,t.current.lanes=a,An(t,a),Ot(t),e[za]=t.current,_o(e),new xi(t)},bl.version="19.1.0",bl}var of;function Hm(){if(of)return Jo.exports;of=1;function E(){if(!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__>"u"||typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE!="function"))try{__REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(E)}catch(G){console.error(G)}}return E(),Jo.exports=Lm(),Jo.exports}var qm=Hm(),yt=ls();const Ym=`# EdgeIQ — Master Build Plan & Product Roadmap
*Last updated: April 12, 2026*

---

## 🎯 PRODUCT VISION

**EdgeIQ** — "Find your edge, then automate it."
Personal win-rate calibration engine + autonomous trading system for small-cap volume profile trading.
- Stack: Python Streamlit (port 8080), Alpaca API, Supabase, Plotly — dark mode
- Differentiator: NOT a generic screener. Learns YOUR specific edge. Then automates it. Then builds a marketplace of proven edges. Then routes dynamically to whoever is best right now.
- Competitors: Trade Ideas, Tradervue, StocksToTrade, Warrior Trading (education only) — none do personal calibration, none have a data moat, none approach the meta-brain
- End goal: Fully autonomous, self-optimizing trading system with a marketplace of verified human edges and institutional data licensing

### SUBSCRIPTION TIERS (Updated April 10, 2026)
- **Tier 1 — $49/mo:** Personal brain (structure predictions, TCS calibration, win rate tracking by setup)
- **Tier 2 — $99/mo:** Personal brain + daily Telegram scanner alerts (morning setups + EOD outcomes)
- **Tier 3 — $199/mo:** License a top trader's verified brain (copy their calibrated edge; they earn revenue share)
- **Tier 4 — $999/mo:** Retail Meta-Brain (dynamic routing across top-verified profiles based on live conditions)
- **Tier 5 — $5,000–$15,000/mo (annual):** Professional/Institutional Meta-Brain (full signal output for external execution; prop traders, small funds)
- **Revenue share:** Top performers earn passive income from brain licensing — just for logging consistently

---

## 🗺️ PRODUCTION PHASES

### Phase 1 — Manual Signal Quality Validation (CURRENT — IN PROGRESS)
**Goal:** Prove the model's signals are accurate before trusting any automation.

Daily workflow:
1. Pre-market → Load watchlist → **Predict All** (saves predictions for next trading day)
2. During session → Trade, take notes, track levels manually
3. After 4pm → **Bot auto-verifies at 4:25 PM ET** → scores predictions vs actual structure autonomously (manual "Verify Date" button available as backup for historical audits)
4. Every verified trade feeds the win-rate calibration database AND per-structure win-rate calibration

Done when: 50+ verified trades with consistent win-rate data per structure type

Current capabilities built:
- Volume profile (POC, VAH, VAL, HVN/LVN)
- **7-structure classification** (already built — see framework below)
- IB detection (9:30–10:30 ET, industry standard inclusive; structure classification updates dynamically throughout the day as price interacts with fixed IB levels)
  - *Future enhancement: multi-timeframe IB detection (morning/midday/EOD) to capture evolving structure across the session — discussed, not yet built*
- TCS (Trade Confidence Score, 0–100) — see **TCS Deep Breakdown** section below
- RVOL (time-segmented, pace-adjusted, minute-by-minute 390-point intraday curve) — see **RVOL Deep Breakdown** section below
- Order flow signals — Tier 2 (pressure acceleration, bar quality, vol surge, tape streak)
- Predictive win rates per structure (Analytics tab)
- Trade journal with auto-grade A/B/C/F + grade discipline equity curve
- Watchlist predictions + EOD verification
- MarketBrain (live prediction vs actual tracker)
- Monte Carlo equity curves (1,000 simulations)
- Small Account Challenge tab
- Playbook screener tab
- Historical Backtest Engine tab
- Position management (entry/exit/P&L/MFE overlay on chart)
- Audio/visual alerts (Web Audio API)
- Pre-market gap scanner (processes all tickers provided in watchlist, gap% + PM RVOL; SIP data feed required for pre-market volume — IEX free tier shows blank PM volume)

### The Two-Layer Brain Architecture (April 8 insight — CRITICAL)

**Layer 1 — Personal brain (built)**
Each user's complete trading profile calibrates to their individual performance. This goes far beyond just structure weights:
- Brain weights per structure type (win rates, confidence per classification)
- Full trade journal history (win rates, A/B/C/F grades, P&L)
- TCS calibration per setup type
- RVOL bands + gap% bands per outcome
- Nightly confidence rankings (0–5 tiers across all tickers)
- Behavioral data (entry types, discipline tracking)
- Position sizing history and risk patterns
Your accuracy on Trend Day Up is yours alone. Nobody else's data touches it.

**Layer 2 — Collective brain (to build)**
Anonymized outcomes from ALL users, pooled across the platform.
"Across 847 verified trades from 312 users, Trend Day Up + TCS > 75 resolved correctly 84.7% of the time."
- What this means: out of 847 times ANY of those 312 users predicted that specific setup, the actual outcome matched 84.7% of the time. Different people, different days, different stocks — the common thread is the setup pattern. That's a collective win rate for a specific setup combination.
- Why it matters: this is a market truth, not a personal opinion. No one person could generate 847 data points on one setup — it takes a network.

How they combine:
- Collective brain → establishes baseline signal quality per structure (the "floor" — what works across everyone)
- Personal brain → adjusts that baseline up/down based on your specific accuracy (your "edge modifier")
- If you're a 90% Trend Day trader but the collective says 65%, your personal override keeps you at 90%. The collective doesn't drag you down — it lifts weaker users UP toward the baseline.
- If you're a 40% Trend Day trader and the collective says 65%, the system flags that you're underperforming on that setup.
- Final signal → market truth + personal edge modifier

Why this is the moat:
- Data network effect: user 1,000 gets a better product than user 10
- Not because code changed — because 999 people verified real outcomes before them
- A competitor who launches later starts with zero collective data. They might have one experienced trader with more personal data, but they don't have 1,000 people's pooled outcomes. The moat is the network, not any one individual.
- The longer EdgeIQ runs, the more verified trades accumulate. A competitor entering 12 months later is 12 months × 1,000 users × daily trades behind. That gap never closes.
- Analogous to: Tesla Autopilot (each car feeds the fleet — one good driver can't replicate the data from 4 million cars), Spotify (collective listening improves everyone's recommendations)

Why you NEVER mix personal weights across users:
- User A crushes Trend Days (90% win rate) but is terrible at Neutral Days (30%). User B is the opposite. If you average their weights, BOTH get a mediocre 60% signal on everything. Neither gets a signal optimized for their actual strengths.
- Personal weights exist to amplify individual strengths and flag individual weaknesses. Mixing them destroys both.

Build requirements:
- All personal data stays isolated per user (privacy, NEVER mix personal weights)
- Collective layer uses anonymized outcomes — minimum fields: (structure predicted, structure actual, TCS band, win/loss). Additional fields for deeper intelligence: RVOL band, gap% band, time of day, market regime, sector.
- Minimum n=50 per structure before collective brain influences base signal
  - This means: before the collective changes YOUR signal at all, there must be at least 50 verified trades for that specific structure type across ALL users combined. If only 12 users have ever traded "Trend Day Up + TCS > 75," the collective stays silent — your personal data alone drives the signal. Once 50+ people have verified that same setup, the collective baseline kicks in as a starting point.
- Personal layer always has override priority — your edge > collective average if they conflict
  - Note: more data (collective) ≠ more accuracy. The collective includes skilled AND unskilled users. A top performer's personal override prevents being pulled down to the collective average.

---

## 🔬 TCS DEEP BREAKDOWN (Trade Confidence Score)

### What it is
A 0–100 composite score measuring how much conviction the current price action deserves. It answers: "Is this move real, or is it noise?"

### Current formula (hardcoded — does NOT self-calibrate yet)

**Range Factor (max 40 pts)** — day range vs IB range
- Measures: How far has price extended beyond the Initial Balance?
- Formula: \`range_ratio = total_day_range / ib_range\`
- If range_ratio ≥ 2.5 → full 40 pts
- If 1.1 < range_ratio < 2.5 → linear scale from 0 to 40
- If range_ratio ≤ 1.1 → 0 pts
- What it catches: true directional days where price left the opening range
- What it misses: HOW the range extended — clean breakout vs choppy whipsaw score the same

**Velocity Factor (max 30 pts)** — recent volume pace vs session average
- Measures: Is current volume accelerating or decelerating?
- Formula: \`velocity_ratio = avg_vol_last_3_bars / avg_vol_entire_session\`
- If velocity_ratio ≥ 2.0 → full 30 pts
- If 1.0 < velocity_ratio < 2.0 → linear scale from 0 to 30
- What it misses: volume DIRECTION (selloff volume scores same as breakout volume)

**Structure Factor (max 30 pts)** — price distance from POC + trend direction
- Step 1: Is price > 1 ATR from POC?
- Step 2: Are the last 3 closes trending FURTHER from POC?
- If yes to both → full 30 pts
- What it misses: volume AT price levels (breakout quality)

**Sector Bonus (optional +10 pts)** — sector ETF tailwind
- Sector ETF up > 1% today → +10 pts
- Limitation: binary on/off, should scale with magnitude

### Honest assessment
Reasonable V1 framework. Captures the right categories (range, momentum, displacement) but missing: order flow integration, bid/ask spread quality, time-of-day factor, volume-at-price.

### Evolution path
1. Self-calibrating component weights (40/30/30 should auto-adjust per user)
2. New components: order flow, time-of-day multiplier, volume-at-price, liquidity quality
3. Component auto-discovery: brain tests new factors against trade outcomes, proposes additions

---

## 🔬 RVOL DEEP BREAKDOWN (Relative Volume)

### What it is
Measures today's volume vs "normal" at this exact time of day. RVOL 3.0 = 3× typical volume at this minute.

### Current lookback periods
- **Main chart:** 50-day baseline (390-element minute-by-minute curve)
- **Playbook screener:** 10-day baseline (more responsive)
- **Gap scanner PM:** 10-day pre-market average

### Small-cap concern (valid)
50 days for small caps that barely trade includes many dead-volume days. Baseline becomes artificially low, inflating RVOL on any activity. 10-20 day lookback would be more responsive.
Should auto-adjust: test which lookback period's RVOL bands correlate best with wins. Phase 2 brain calibration target.

### Scanner RVOL filtering
Currently: NO RVOL minimum filter in the live scanner. Shows everything.
Should: Start at baseline floor (e.g., RVOL ≥ 2.0), auto-adjust over time.

### RVOL bands
- \`3+\` — extreme volume | \`2-3\` — high | \`1-2\` — normal | \`<1\` — below average (caution)

---

## 🎯 TARGET ZONES & TAKE PROFIT (NOT always IB High)

### Current targets (already dynamic)
1. **Coast-to-Coast:** IB violated → price returns inside → target = opposite IB boundary
2. **Range Extensions (TCS > 70):** 1.5× and 2.0× IB range from breakout side
3. **Gap Fill:** Double distribution LVN detected → target = opposite HVN
4. **Volume profile levels**
5. **Fallback:** IB extensions at 1.0×, 1.5×, 2.0×

### What should learn over time
Which targets YOUR trades actually hit. If you consistently blow through 1.5× to hit 2.0×, hold longer. If trades reverse before 1.5×, take partial earlier. Baseline → learn → refine.

---

## 📊 LAYER 1 DEEP BREAKDOWN

### Brain Weights per Structure Type
- Stored in \`brain_weights.json\`, recalibrated nightly at 4:30 PM ET
- Each structure gets independent win rate tracking
- Cluster keys: structure + TCS band + RVOL band → ultra-specific condition win rates

### Trade Journal History (SEPARATE from behavioral — weighted independently)
- Tracks OUTCOMES: ticker, date, entry/exit, P&L, shares, thesis
- Auto-graded A/B/C/F based on RVOL, TCS, price vs IB
- Win rate by grade: "A-trades win 78%, C-trades win 34%"

### Behavioral Data (SEPARATE — weighted independently from journal)
- Tracks PROCESS: why you entered (Calculated/FOMO/Reactive/Revenge/Conviction Add/Average Down)
- Why separate: A FOMO trade can win (outcome good, process bad). Journal = results. Behavioral = discipline. Different signals.

### TCS Calibration per Setup Type
- TCS 60 means different things on Trend Day vs Non-Trend Day
- Over time: per-structure TCS thresholds auto-adjust

### RVOL Bands + Gap% Bands per Outcome
- Every prediction tagged with RVOL band and gap%. Win rates computed per band.

### Nightly Confidence Rankings (0-5)
- User rates tickers nightly. Next day: outcome auto-verified.
- After 90+ nights: feeds Kelly sizing as confidence multiplier.

### Position Sizing History
- Tracks capital allocation per trade. Reveals sizing patterns (oversize on FOMO?)
- Feeds fractional Kelly in Phase 4: account balance + win rate + TCS + regime → optimal size

---

## 🧠 COLLECTIVE BRAIN ADVANCEMENT (April 12 deep-dive)

### What "84.7%" measures — IMPORTANT
Collective brain measures **structure prediction accuracy**, NOT trade P&L.
- "Did the model correctly predict what type of day this would be?" ≠ "Did the trade make money?"
- Future V2: track full trade outcomes (entry quality, P&L, hold duration) alongside structure accuracy

### Auto-calibration design
- Minimum 4 fields are the starting point (floor, not ceiling)
- System should analyze which additional fields improve accuracy and promote/demote accordingly
- Phase 2+ capability — requires significant data volume

### Mixing personal weights — why not
Can't combine users' personal weights into one "best" weight set because personalization IS the product. User A's strengths ≠ User B's strengths. Averaging destroys both.
The collective gives a BASELINE. Personal weights give YOUR EDGE MODIFIER on top of it.

---

## 📊 THE 350-TRADE QUESTION

### The concern
7 structures × 350 per structure = 2,450 trades = 1-2 years at current pace. Some structures are rare.

### Why it's solvable
1. Collective brain fills the gap — you don't need 350 personal per structure if 200 other users contribute
2. Adaptive weights kick in at just 15 rows total, not 350 per structure
3. Rare structures (Non-Trend Day) = "avoid, no edge" — being rare is fine

### Structure classification accuracy
- Based on Market Profile theory (Dalton) — industry standard, not invented
- Hard 3-branch decision tree. Updates dynamically throughout the day.
- Different market regimes shift structure DISTRIBUTION but not DEFINITIONS
- Classification logic is HARD PRESERVATION — do not touch

---

## 🚨 DATA GAPS FOUND (April 12 audit)

### RVOL persistence — ✅ DONE
- \`rvol\` column exists on both \`paper_trades\` and \`watchlist_predictions\` tables
- \`gap_pct\` also persisted on both tables
- Brain can now learn which RVOL bands correlate with wins

### paper_trades MAE/MFE columns — ⚠️ PENDING MIGRATION
- \`mae\`, \`mfe\`, \`entry_time\`, \`exit_trigger\`, \`entry_ib_distance\` — code ready, migration SQL documented
- Run via sidebar "🔧 Database Migrations" button or paste SQL in Supabase SQL Editor
- Once columns exist, paper trades will auto-log execution depth data

### Structure Priority — Adaptive TCS Thresholds — ✅ BUILT (April 12, 2026)
- \`compute_structure_tcs_thresholds()\` in backend.py; Analytics Section 4 + Trade Journal page
- Per-structure hit rate → recommended TCS threshold (lower = more aggressive)
- Formula: base 65, adjusted by (hit_rate - 60) × 0.5, clamped [45, 85]
- Shows: structure name, hit rate, sample count (journal + bot), brain weight, recommended TCS, action label
- Color-coded: 🟢 ≥70% / 🟡 ≥55% / 🟠 ≥40% / 🔴 <40%
- Action labels: AGGRESSIVE / STANDARD / CAUTIOUS / AVOID
- Self-compounding: updates automatically as accuracy_tracker + paper_trades grow

### Trade Journal Logger Page — ✅ BUILT (April 12, 2026)
- Standalone page at \`/?journal=<USER_ID>\` — no sidebar, no login required (single-user mode)
- Stats strip: Journal Entries, Predictions, Prediction Accuracy, Paper Win Rate
- Quick CSV import (Webull order history) with dedup against existing journal
- Per-structure TCS thresholds (live from accuracy_tracker)
- Recent journal entries table
- Only owner's user ID works — beta testers cannot access

### Performance Tab — Bot vs Overall Pred Rate Clarification (April 12, 2026)
- Performance tab KPI strip now shows 6 cards (was 5)
- "Bot Pred Rate" — bot watchlist_pred calls only (40.7%, 33/81) with "Bot watchlist calls only" label
- "Overall Pred Rate" — all sources combined (67.2%, 193/287) with "All sources combined" label
- Data breakdown: watchlist_pred=81 rows, webull_import=61 rows, manual/playbook=145 rows
- Bot accuracy (40.7%) is the key metric for autonomous improvement; overall (67.2%) includes human calls

### Webull CSV Import — April 12, 2026
- Imported 8 new trades from April 6–9 CSV: IPST (×1), RENX (×2), CYCU, MGN, YMT (×2), ONCO
- Total journal entries: 70 (was 62)
- Time-frame data preserved: entry timestamps, exit timestamps, P&L, shares in notes field
- Entry time distribution peaks at 09:00–10:00 (32 of 70 trades) — IB window trades dominate

### Private Build Notes Page — ✅ BUILT (April 12, 2026)
- Accessible at \`/?private=<KEY>\` using the private key
- Renders .local/build_notes_private.md with styled header
- No sidebar, no login — key-gated access only

### Portfolio Risk Metrics — ✅ BUILT (April 12, 2026)
- Sharpe ratio (daily + annualized), Alpha vs SPY, Alpha vs IWM, Max drawdown, Rolling drawdown chart
- \`compute_portfolio_metrics()\` in backend.py, displayed in Analytics tab Section 3C
- Both SPY (broad market) and IWM (small-cap benchmark) alpha computed from Alpaca daily bars
- IWM is the critical benchmark — trading small caps means SPY alpha alone could just be small-cap beta
- Fallback: if no P&L % column, uses win/loss as +1/-1 synthetic returns
- Self-compounding: metrics auto-update as bot logs more trades. Once MAE/MFE columns exist, P&L precision improves. Phase 2 enables segmentation by structure/RVOL/TCS band.

### accuracy_tracker data quality — ✅ CORRECT (April 12 audit)
- \`correct\` column stores ✅/❌ emojis (not NULL as previously documented). 287 rows total (193 ✅, 94 ❌)
- \`_strip_emoji()\` handles actual column emoji prefixes ("🔄 Neutral") for matching
- Recalibration reads correctly via \`"✅" in correct\` check — no fix needed

### NOT tracking multiple RVOL lookbacks
- Only one lookback used at a time (50-day main, 10-day playbook/scanner)
- Should run 10/20/30/50 in parallel for future comparison. Phase 2 target.

### RVOL ≥ 2.0 Scanner Floor — BUILT (April 12, 2026)
- Used in: structure classification, trade grading, entry trigger text, model warnings
- Scanner RVOL floor filter added: \`run_gap_scanner()\` accepts \`min_rvol\` param (default slider 2.0x in sidebar)
- Only applied with SIP feed (IEX has no PM volume data to filter on)
- Keeps tickers with unknown RVOL (doesn't drop them)

### Inside Bar — BUILT (April 12, 2026)
- Patterns built: H&S, Double Bottom/Top, Bull/Bear Flag, Cup & Handle, **Inside Bar**
- Runs on both 5m and 1hr timeframes. Base score 0.60, boosted by compression%, POC/IB proximity.
- Direction based on close position vs mother bar midpoint.
- 5m inside bars = micro-consolidation within IB (scalp signal). 1hr inside bars = genuine coiling before IB extension (big signal).

### P-Shape / D-Shape — NOT built
- P = short covering rally (volume top-heavy). D = long liquidation (volume bottom-heavy).
- Different from 7 structures (those track IB behavior; shapes track volume distribution)
- Fix: classify volume profile distribution shape (Phase 2)

---

## ⏰ SELF-LEARNING TIMELINE

### Working NOW (Phase 1)
1. Brain weights per structure (nightly recalibration)
2. Edge Score component weights (auto-calibrates from backtest history)
3. Win rate per cluster (structure + TCS band + RVOL band)
4. Structure classification (updates dynamically through trading day)
5. Trade grade auto-assignment (A/B/C/F)
6. Nightly ranking accuracy tracking

### BUILT (Phase 1 — data foundation — completed April 12, 2026)
7. ✅ RVOL persistence to Supabase (paper_trades + watchlist_predictions)
8. ✅ Scanner RVOL floor (≥ 2.0 default, SIP only)
9. ✅ Inside bar detection (5m + 1hr, compression + POC/IB confluence)
10. ✅ Gap% persistence per prediction
11. ✅ MAE/MFE execution depth (mae, mfe, entry_time, exit_trigger, entry_ib_distance — paper trades + analytics)
12. ✅ MAE/MFE analytics dashboard (MFE:MAE ratio, money left on table, by-structure breakdown, exit trigger analysis)

### Phase 2 (~500 trades — brain becomes self-optimizing)
13. Pattern Discovery Engine (cross-tab all factors → surface winning combos)
14. RVOL lookback auto-optimization (test 10/20/30/50, use best)
15. Scanner RVOL floor auto-adjustment
16. TCS internal weight self-calibration (40/30/30 learns from data)
17. TCS component auto-discovery (proposes adding order flow, time-of-day, etc.)
18. P/D/B shape classification
19. Target zone learning (which targets YOUR trades actually hit)
20. Per-structure TCS thresholds
21. Brain weight historical snapshots (nightly timestamped log of all weights — investor audit trail + evolution analysis)
22. TCS persistence per trade (store TCS at scan time alongside every paper trade and watchlist prediction for cross-tab analysis)
23. Edge Score persistence per trade (same as TCS — store at decision time for later pattern discovery)

### Phase 3 (~2,000+ users — collective intelligence)
24. Collective brain activation
25. Collective field auto-calibration
26. Auto-entry on paper account
27. Behavioral data auto-weighting

### Phase 4 (live autonomous trading)
28. Fractional Kelly position sizing
29. Market regime multiplier
30. Full trade outcome learning (entry quality, P&L, hold duration)

### Phase 5 (meta-brain)
31. Dynamic routing across user profiles
32. Brain licensing marketplace
33. Cross-user pattern discovery

| Priority | What | When | Depends On |
|---|---|---|---|
| NOW | Brain weights, Edge Score weights, clusters | Phase 1 (working) | — |
| DONE | RVOL persistence, scanner filter, inside bar | Phase 1 (built Apr 12) | — |
| DONE | MAE/MFE execution depth + analytics dashboard | Phase 1 (built Apr 12) | — |
| SOON | Pattern discovery, RVOL optimization, TCS self-cal | Phase 2 (~500 trades) | RVOL persistence |
| LATER | Collective brain, auto-entry, behavioral weighting | Phase 3 (~2K users) | Pattern discovery |
| FUTURE | Kelly sizing, regime, full P&L learning | Phase 4 | Collective brain |
| ENDGAME | Dynamic routing, brain licensing, cross-user | Phase 5 | All above |

---

## 📡 PRE-MARKET DATA & SIP
- IEX free tier: NO pre-market volume. Gap % works but PM RVOL shows "N/A"
- SIP ($99/mo via Alpaca — corrected from old $9/mo documentation): full pre-market volume data
- Without SIP: cannot log or study pre-market volume patterns over time
- **When to buy SIP:** Phase 1 — ideally NOW. Every day without SIP is a day of pre-market volume data you're NOT collecting. At $99/mo it's a significant but essential data investment. The PM RVOL data needs to accumulate alongside your other prediction data so it's ready for Phase 2 pattern discovery. Waiting until Phase 2 means Phase 2's pattern engine won't have any PM data to learn from.

---

### Phase 2 — Autonomous Pattern Discovery Engine
**Goal:** Let the system find its own edges from accumulated paper trade + scanner data.

How it works:
- Cross-tab every combination of: TCS band × structure type × RVOL band × gap % band × inside bar present (yes/no)
- Calculate win rate + avg follow-thru + sample count per combination
- Surface only combinations with n ≥ 10 (statistically meaningful)
- Flag combinations where win rate > 75% as "discovered edges"
- Output: ranked table sorted by win rate, shown in Analytics tab — one button, runs instantly

Example output it would surface:
> "TCS 70-80 + Trend Day Up + RVOL > 2.5 + gap > 5% pre-market → 84% win rate (n=23)"
> "Inside Bar at POC + Normal Variation structure → 79% win rate (n=11)"

What needs to be stored at scan time (not yet logging):
- Gap % at pre-market detection
- Time of gap detection (within first 10 min of pre-market = stronger signal)
- Pre-market RVOL at scan moment
- Inside bar present on 5m chart at IB close (yes/no flag)

Data threshold: ~500 paper trade rows needed before patterns are statistically real.
At 45 tickers × 5 days/week = ~225 rows/week → meaningful analysis in ~3 weeks.

Done when: at least 3 discovered edges with n ≥ 20 and win rate > 70% reproduced over 2+ weeks.

### Phase 3 — Alpaca Paper Trading Integration (was Phase 2)
**Goal:** Automate entries on high-confidence signals, validate with paper money.

Auto-entry criteria (to be tuned):
- TCS > 80
- Structure = Trend Day Up/Down
- RVOL > 2×
- Order Flow score > 60
- Pattern confirmation from Tier 3 (when built)
- Confirmed discovered edge from Phase 2 pattern engine

Track: paper P&L vs manual P&L — prove automation matches or beats human execution.
Risk controls: max position size, max daily loss, automated stop-loss.
Done when: paper win-rate matches or exceeds BOTH manual journal win-rate AND Webull CSV import win-rate over 30 sessions.
(Webull CSV = real executed trades, journal = curated manual logs — both must be beaten for Phase 4 to be justified.)

### Phase 4 — Live Autonomous Trading
**Goal:** Real money automated execution. Fully systematic — no emotion, no human override needed.
- Same entry criteria as Phase 3 but live Alpaca account
- Human kill switch always available (sidebar toggle) but should rarely be needed
- Hard daily loss limits + drawdown stops
- Full audit trail via journal + Supabase
- **Fractional Kelly position sizing (KEY — April 10 insight):**
  The bot calculates optimal position size per trade automatically:
  - Inputs: account balance + verified win rate for THIS structure type + TCS confidence + market regime multiplier
  - Kelly formula: sizes up on high-edge setups, sizes down on lower-confidence structures
  - Removes the last human variable (sizing decisions) from the execution loop
  - No guardrails needed because the sizing is mathematically calibrated to edge
  - Result: fully autonomous from signal → size → entry → exit. No emotion possible.
- Done when: live P&L matches or exceeds paper P&L over 30 sessions

### Phase 5 — Meta-Brain + Marketplace (18–30 months from Phase 1 start)
**Goal:** Dynamic routing system + "copy a top trader" marketplace.

**What gets built:**
- **Leaderboard** (opt-in): surfaces top-performing users by win rate, structure accuracy, regime performance
- **Brain licensing marketplace:** top traders list their brain for $199/mo (they earn revenue share, you take a cut)
- **Collective brain activation:** Layer 2 goes live — anonymized outcomes from all users pool into baseline signal weights. Requires n ≥ 50 per structure across platform before activating.
- **Meta-brain (Layer 3):** Dynamic routing engine. Watches real-time market conditions, routes to whichever user profile has historically dominated that exact context:
  - Time of day (9:30 vs 11 AM vs afternoon performance profiles)
  - Market regime (hot tape, cold tape, transitional)
  - Day of week (Monday follow-through vs Friday fade tendencies)
  - Macro environment (VIX spike, earnings season, Fed week)
  - Asset-specific (which brain dominates THIS ticker category)
- **Market regime tagging:** Every prediction + outcome tagged with regime at time of trade. Regime multipliers applied to TCS score — hot tape bullish breakout = higher confidence, cold tape same setup = lower.
- **Revenue share system:** Top performers earn passive income automatically from licensing fees

**What needs to be true:**
- 2,000+ users with 50+ verified trades each before meta-brain routing is meaningful
- Opt-in required for leaderboard + brain sharing (personal brain always stays isolated)
- Market regime detection built as multiplier, NOT a hard mode switch — bot never sits on its hands

**Pricing at this phase:**
- Retail Meta-Brain: $999/mo
- Brain licensing: $199/mo (creator gets ~40% revenue share)

### Phase 6 — Asset Class Expansion (2.5–3.5 years from Phase 1)
**Goal:** Same architecture, different data feeds. One codebase, multiple markets.

**Asset classes to add (priority order):**
1. **Futures: ES/NQ** — institutional money, higher ACV users, same IB structure applies
2. **Crude oil / Gold futures** — commodity traders, different volatility profile, same framework
3. **Crypto: BTC/ETH** — 24hr session-based IB equivalent, massive addressable market
4. **Forex** — session-based IB structure (London/NY session open = IB equivalent)

**Why it works without rebuilding:**
- Volume profile + IB structure is universal — the same auction theory applies to any liquid market
- The brain calibrates per-instrument, per-user — same loop, new data feed
- New asset class = new user segment = new revenue with no new architecture

### Phase 7 — Institutional Data Licensing (3.5–5 years from Phase 1)
**Goal:** Monetize the dataset externally. B2B revenue layer on top of consumer SaaS.

**What the dataset is by this point:**
- 10,000+ traders, verified outcome logs, millions of structure predictions mapped to actual outcomes
- Tagged by: market regime, time of day, asset class, TCS band, structure type, account size, trader tenure
- Multi-year time series across multiple market cycles
- No competitor can replicate this — it requires years of real retail traders logging real verified outcomes

**Who buys it:**
- Quant funds needing retail behavioral data for signal research
- Prop trading desks wanting calibrated retail flow intelligence
- Fintech companies building trading products who want pre-validated signal infrastructure
- Retail brokerages wanting to offer "personalized edge" as a platform feature (acquisition target)

**Licensing model:**
- API access to anonymized dataset: $2,000–$10,000/mo per institutional seat
- Custom analytics / research packages: project-based pricing
- Full platform acquisition: $200–300M range at this scale (see exit math below)

**Exit math:**
- At $4.5M ARR (Phase 5): $36–67M exit at 8–15× multiple
- At $20M ARR (Phase 7, full stack + institutional): $200–300M acquisition
- The acquirer buys the dataset, the brain architecture, and the user base — not just the SaaS revenue

---

## 📊 SIGNAL TIER ARCHITECTURE

### Tier 1 — Volume Profile + Day Structure ✅ COMPLETE
Core foundation. Classifies the auction process.
- IB detection (9:30–10:30 ET)
- Volume profile (POC, VAH, VAL, HVN/LVN, double distribution detection)
- TCS (Trade Confidence Score)
- Target zones (Coast-to-Coast, Range Extension 1.5×/2.0×, Gap Fill)
- Distance-to-target widget

### Tier 2 — Order Flow Signals ✅ COMPLETE
Real-time intraday momentum layer. Composite score −100 to +100.
- Pressure acceleration (3-bar vs 10-bar buy/sell delta)
- Bar quality score (0–100)
- Volume surge ratio (vs 10-bar average)
- Tape streak (consecutive bullish/bearish bars)
- IB proximity + volume confirmation

### Tier 3 — Chart Pattern Detection 🔴 TO BUILD (PRIORITY)
Confirmatory signal layer. Detects classic patterns on 5m and 1hr bars.

Patterns to detect:
| Pattern | Direction | Confluence Notes |
|---|---|---|
| Reverse Head & Shoulders | Bullish | Neckline break + volume confirm |
| Head & Shoulders | Bearish | Neckline break |
| Double Bottom | Bullish | Often base of cup, or L-shoulder of reverse H&S |
| Double Top | Bearish | — |
| Cup & Handle | Bullish | Double bottom base + handle pullback |
| Bull Flag | Bullish | Tight consolidation after impulse |
| Bear Flag | Bearish | — |
| Inside Bar | Neutral → directional on break | Full bar range contained within prior bar — coiling energy, breakout watch above/below prior bar's high/low |

Confluence scoring logic:
- Single pattern = base score
- Two patterns aligning (e.g., double bottom as left shoulder of reverse H&S) = 1.5× score
- Pattern + volume profile level (HVN/POC/IB) = "confluence confirmed"
- Pattern + trendline hold + volume contraction = "Coiled — breakout watch"

Example model output when built:
> "Neutral day — but 1hr reverse H&S neckline unbroken at $2.49 + trendline hold → bullish resolution probable"
> "Fakeout potential — coiled at trendline + POC confluence → breakout watch, not exit signal"

Real-world gap this fills (from today, April 6):
RENX → model said "fakeout potential" (Neutral structure read, correct)
but user identified 5m AND 1hr reverse H&S manually.
Pattern detection would have flipped the read to: "Neutral + H&S bullish confirmation"

### Tier 4 — Trendline Detection 🔲 FUTURE
Auto-draw trendlines from swing highs/lows on 5m/1hr/daily/weekly.
Required for: compression score, trendline hold confirmation, pattern geometry (necklines, channels).

**Key design insight (April 8):** Trendlines on bigger timeframes carry MORE weight — not less.
Daily/weekly trendlines have thousands of participants watching and placing orders at them,
making them self-fulfilling. 5m trendlines are noise by comparison.

Build priority order:
1. Daily trendline first — most participants watching = strongest signal
2. 1hr trendline second — intraday structure, IB context
3. 5m trendline third — entry timing only, not structure signal

Scoring rule when built:
- Daily trendline hold/break = high confidence signal (weight: 1.5×)
- 1hr trendline hold/break = medium confidence (weight: 1.0×)
- 5m trendline = entry timing only, not a structural signal (weight: 0.5×)
- Multi-timeframe alignment (daily + 1hr trendline both holding same level) = "Compression confirmed" (weight: 2.0×)

---

## 🔧 7-STRUCTURE FRAMEWORK ✅ ALREADY BUILT

Already implemented with hard 3-branch decision tree (no fallthrough):

| Structure | IB Behavior | Signal |
|---|---|---|
| **Trend Day Up** | One side broken (high) only — early, closes at extreme, directional vol | Aggressive long targets |
| **Trend Day Down** | One side broken (low) only — early, closes at extreme, directional vol | Aggressive short targets |
| **Normal Day** | No IB break — wide IB, big players set range | Fade extremes → POC |
| **Normal Variation** | One side broken moderately, returns inside | Conservative fade |
| **Neutral Day** | Both IB sides violated, close in middle | C2C targets, tight stops |
| **Neutral Extreme** | Both sides violated, closes top or bottom 20% | High volatility, wait |
| **Non-Trend Day** | Narrow IB, low volume, no interest | Avoid — no edge |
| **Double Distribution** | Bimodal volume profile, two distinct POCs | Gap fill between nodes |

Branch logic (hard gates, no fallthrough):
- Branch A: no_break → Normal or Non-Trend
- Branch B: both_broken → ONLY Neutral family (Neutral or Neutral Extreme)
- Branch C: one_side → ONLY Trend Day, Normal Variation, or Double Distribution

NOTE: run batch backtest on 20+ tickers with current logic to rebuild training data (Phase 2 task).
RVOL floor now built into scanner (2.0x default). Consider tightening Trend Day threshold after 500+ data points.

### 🚀 BATCH BACKTEST — FAST PATH TO 700 TRADES (April 12, 2026)
- **Problem:** Need ~700 verified rows across 7 structures for statistical significance. Currently at 287. At ~75/week = 5–6 more weeks of live data.
- **Shortcut:** Run \`classify_day_structure\` against historical Alpaca bars for 50+ tickers × 30–60 days = 1,500–3,000 classifications from real price data in a single batch run.
- **Steps:** Pull 60 days intraday bars → compute IB range, full-day range, RVOL, buy/sell pressure → classify → compute TCS → if TCS meets threshold, simulate paper trade (entry at IB break, exit at target/stop, compute P&L) → store ALL results in \`backtest_results\` table
- **Two layers of validation in one batch:**
  1. **Classifier accuracy** (all 1,500+ rows) — "Did we label the structure correctly?"
  2. **Paper trade edge** (only rows where TCS met threshold) — "When the system WOULD have traded, did it win?"
- If layer 2 returns 65%+ win rate across hundreds of simulated trades = backtested proof of system edge. Goes in investor pitch + marketing.
- **Validates:** Classifier accuracy, structure distribution, edge-by-structure, paper trade win rate
- **Does NOT validate:** Personal prediction accuracy, behavioral patterns, brain weight calibration (those require live data from the user)
- **Key distinction:** Backtested data validates the CLASSIFIER + SYSTEM EDGE. Live data validates the TRADER. Both needed. Backtest accelerates the first two.
- **Ticker selection strategy:**
  1. **Start with historical watchlist** — pull unique tickers from \`accuracy_tracker\` in Supabase, backtest those across 60 days
  2. **Expand with historical scanner** — seed universe of 200–300 small caps, filter each day by RVOL > 2.0x + price $1–$20 + volume floor (the stocks that WOULD have been on the watchlist)
  3. **Tag each row** with source (\`watchlist_history\` vs \`scanner_backfill\`) to compare accuracy between hand-picked vs. system-found tickers
- ⏰ **PRIORITY REMINDER:** Build the batch backtest script as a Phase 1 task — single fastest way to stress-test the classifier AND prove system edge before Phase 2.

---

## 🕌 ISLAMIC COMPLIANCE FILTER 🔲 BACKLOG
Build after Tier 3 pattern detection.

- API: Musaffa (musaffa.com) — halal / questionable / not-halal per ticker
- Where: Scanner tab — optional toggle filter
- Signal value: not-halal = structurally reduced Islamic buyer pool
- Market value: inclusive to Islamic trading community — unique differentiator
- Build time: ~30 minutes once Tier 3 is done

---

## 📋 CURRENT 7-TAB LAYOUT

| Tab | Name | Status |
|---|---|---|
| 1 | 📈 Main Chart | ✅ Complete |
| 2 | 🔍 Scanner | ✅ Complete |
| 3 | 📋 Playbook | ✅ Complete |
| 4 | 🔬 Backtest Engine | ✅ Complete |
| 5 | 📖 Journal | ✅ Complete |
| 6 | 📊 Analytics & Edge | ✅ Complete |
| 7 | 💪 Small Account | ✅ Complete |

---

## 💡 USER-GENERATED INSIGHTS (Trading Observations)

**Insight 1: Trendline + Level Confluence = Compression → Expansion**
"As long as a stock follows its trendline and holds levels in confluence with it,
it's set up for consolidation then breakout."
→ Encode as compression score in Tier 3 pattern engine.

**Insight 2: Pattern Stacking / Confluence**
Patterns appear together and amplify each other:
- Double bottom = base of cup & handle
- Double bottom = left shoulder of reverse H&S
- Wedge compression = flag before breakout
Tier 3 must detect stacked patterns and score them higher than singles.

**Insight 3: Entry Quality Logging**
Labeling trades as "FOMO" vs "calculated" at entry time builds one of the most
valuable personal edge metrics over time.
Over 50+ trades, win-rate by entry-type reveals discipline edge vs luck.

---

## 🧠 BEHAVIORAL DATA TRACKER — Full Build Spec (Fleshed Out April 10, 2026)

### What it is
A discipline analytics layer built on top of the existing trade journal.
Every trade already gets logged — this adds a single required field at log time: **entry type**.
Over time it builds the most honest metric a trader can have: *do you actually trade better when you're disciplined?*

### Entry Type Labels (logged at trade entry)
| Label | Definition |
|---|---|
| **Calculated** | Entry based on pre-planned level, structure, and thesis. You had a reason before price got there. |
| **FOMO** | Chased a move already in progress. Entered because price was going up, not because of a level. |
| **Reactive** | Responded to a break or signal in real time — not pre-planned but not pure chase either. Valid entry type. |
| **Revenge** | Entered to recover a previous loss. Highest-risk behavioral category. |
| **Conviction Add** | Added to a winning position at a planned level. Distinct from averaging down. |
| **Average Down** | Added to a losing position. Needs to be tracked separately from Conviction Add. |

### What it produces over 50+ trades
- **Win rate by entry type** — the core output. "Calculated: 64% | FOMO: 31% | Revenge: 12%"
- **P&L by entry type** — win rate isn't enough; a FOMO trade might win but with worse follow-through
- **Average follow-through by entry type** — calculated entries go further on winners than FOMO entries
- **Revenge trade frequency** — tracks emotional state patterns, flags if increasing over time
- **Discipline score (0–100)** — % of entries that are Calculated or Reactive vs FOMO/Revenge over rolling 20 trades
- **Discipline equity curve** — same format as the grade equity curve, but tracks discipline score over time

### Where it lives in the UI
1. **Journal tab** — entry type dropdown added to the log form (required field, not optional)
2. **Analytics tab** — new "Behavioral Edge" section:
   - Win rate table by entry type (color coded green/yellow/red)
   - Discipline score card (rolling 20-trade window)
   - Discipline equity curve chart
   - P&L by entry type bar chart
   - "Your calculated entries outperform your FOMO entries by X%" — plain language callout
3. **Performance tab** — Discipline Score added as a 6th KPI card

### How it feeds the brain
- Entry type gets stored with each journal row (new column: \`entry_type\`)
- Brain weight recalibration checks: *if FOMO win rate is < 40% consistently, auto-suppress FOMO-correlated signal patterns*
- Long term: behavioral patterns become part of the collective brain — platforms can detect if a trader's discipline score is declining before their P&L shows it

### Why this is a product differentiator
No trading platform tracks this. Tradervue doesn't. Tradezella doesn't. Everyone tracks outcome — nobody tracks *why* you entered.
This data answers the question every trader asks but can't answer: "Am I losing because my strategy is bad, or because I don't follow it?"
At scale: if 500 users' FOMO trades consistently underperform their calculated entries by 20%+ across all structures, that's a publishable market insight — "FOMO costs retail traders X% per trade" — that drives press coverage and user acquisition.

---

## 🎯 NIGHTLY TICKER RANKINGS — Built April 11, 2026

Human signal layer built into Paper Trade tab (Section 6).
User rates each watchlist ticker 0–5 every night based on chart read.
Outcomes auto-verified next trading day (% change pulled from Alpaca).
Accuracy table by rank tier builds over time.

**Supabase table:** \`ticker_rankings\` (user_id, rating_date, ticker, rank, notes, actual_open, actual_close, actual_chg_pct, verified, tcs, rvol, edge_score, predicted_structure, confidence_label, created_at)
**Backend functions:** \`ensure_ticker_rankings_table\`, \`save_ticker_rankings\`, \`load_ticker_rankings\`, \`verify_ticker_rankings\`, \`load_ranking_accuracy\`
**UI:** Paper Trade tab → Section 6. Left col: nightly form (watchlist auto-loaded, rank 0-5 selectbox per ticker + notes). Right col: Verify button + accuracy by rank tier table (with Avg TCS/RVOL when data exists) + recent rankings log (shows TCS, RVOL, predicted structure).
**Context enrichment (April 12):** At save time, each ranking is enriched with the bot's watchlist prediction context for that ticker (TCS, RVOL, Edge Score, predicted_structure, confidence_label). This creates two independent evaluation tracks stored side-by-side: YOUR rank (human intuition) + the BOT's prediction context (algorithm). Neither overwrites the other. Cross-tab analysis enables questions like "do my rank-5 picks perform better when the bot also has high TCS?" and "am I better than the bot at certain structure types?"
**Auto-verification (April 12):** Bot auto-verifies yesterday's ticker rankings at 4:25 PM ET alongside watchlist prediction verification. Separate independent verification — rankings measure YOUR stock-picking skill, watchlist verification measures the ALGORITHM's structure prediction accuracy.

**First validation (April 10):** Rank 5s → 4/6 winners including SKYQ +62.97% and CUE +68.46%. Rank 4s → 4/4 winners. Rank 3s → 0/4 winners (clean sweep of losses). Pattern is real and needs 30+ nights to confirm statistically.

**Future use:** Once the ranking system shows a clear differentiated accuracy gradient across tiers (rank-5 meaningfully outperforming rank-0, with a consistent spread in between), ranking score feeds into paper trade Kelly sizing as a confidence multiplier alongside TCS. The system evolves by comparing ALL tiers — rank-5 could end up noisier than rank-4. The comparison is what matters, not any single tier's number.

**SQL to create table:**
\`\`\`sql
CREATE TABLE IF NOT EXISTS ticker_rankings (
  id           SERIAL PRIMARY KEY,
  user_id      TEXT NOT NULL,
  rating_date  DATE NOT NULL,
  ticker       TEXT NOT NULL,
  rank         INTEGER NOT NULL CHECK (rank >= 0 AND rank <= 5),
  notes        TEXT DEFAULT '',
  actual_open  FLOAT,
  actual_close FLOAT,
  actual_chg_pct FLOAT,
  verified     BOOLEAN DEFAULT FALSE,
  tcs          REAL,
  rvol         REAL,
  edge_score   REAL,
  predicted_structure TEXT,
  confidence_label    TEXT,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, rating_date, ticker)
);
\`\`\`

---

### Build requirements
- Add \`entry_type\` column to \`trade_journal\` table in Supabase (TEXT, nullable for backward compat)
- Add entry type dropdown to journal log form in app.py (Calculated / FOMO / Reactive / Revenge / Conviction Add / Average Down)
- Analytics tab: new Behavioral Edge section with the 5 charts/tables listed above
- Performance tab: Discipline Score KPI card
- Discipline score formula: \`(calculated_count + reactive_count) / total_trades_last_20 × 100\`
- Build time estimate: ~3-4 hours for full implementation
- Priority: build after Phase 1 data gate is hit (150+ predictions) — needs enough journal entries to be meaningful

### Data already being collected that maps to this
- Journal \`grade\` field (A/B/C/F) partially overlaps — A grades tend to be calculated entries
- But grade ≠ entry type. A FOMO entry can get an A grade if it worked out. This is different.
- \`entry_type\` must be logged at the moment of entry — cannot be backfilled accurately after the fact

**Insight 4: After-Hours Small-Cap Reality**
After-hours on thin small caps: a few buy orders can create a big-looking volume spike.
Patterns formed in after-hours are low-conviction until confirmed at regular open.
Key: does the level hold into and through next morning's open?

---

## 🔨 TECHNICAL FIXES COMPLETED (Recent Sessions)

| Fix | Details |
|---|---|
| Verify predictions date picker | Manual date picker replaces "Verify Yesterday" |
| Supabase date range query | pred_date is timestamp — use gte()/lt() not eq() |
| Predict All trading day | Uses get_next_trading_day() — no weekend/holiday saves |
| IB computation | pd.Timestamp(tz=tz) cutoff, includes full 10:30 bar |
| IB manual override | st.form prevents collapse; per-ticker; ACTIVE label |
| Log entry timestamp | Date + time picker (1-min step) for accurate backdating |
| load_watchlist import | Added to explicit import block |
| get_next_trading_day | New backend.py function using Alpaca calendar API |

## 🐛 KNOWN ISSUES

| Issue | Priority |
|---|---|
| artifacts/api-server workflow failed state | Low — clean up, doesn't affect main app |
| April 5 predictions in Supabase (Saturday) | Low — unverifiable junk data, can ignore |
| IB 1-3 cent gap vs Webull | Unfixable — data feed fragmentation; use IB Override |

---

## ✅ BUILD CHECKLIST (Tonight / Next Session)

- [x] **Tier 3 pattern detection** — H&S, reverse H&S, double bottom/top, cup & handle, bull/bear flag + confluence scoring — COMPLETE (April 6)
- [x] **Batch calibration run** — One-click "🧠 RUN CALIBRATION" button in Backtest tab. 28 small-cap tickers × configurable 1–22 trading days lookback. Auto-saves to Supabase, shows win rate + structure distribution summary. Original per-ticker simulation unchanged below it. COMPLETE (April 6)
- [ ] **Islamic compliance filter** — Musaffa API, Scanner tab toggle (after Tier 3)
- [ ] **Clean up artifacts/api-server** — failed workflow, not needed

## 🧠 META-BRAIN & DYNAMIC PROFILE SWITCHING (Saved April 10, 2026)

### The Vision
As owner you have visibility into ALL user data. That's not just a privacy consideration — it's a product superpower.

**Layer 3: The Meta-Brain** (beyond personal + collective brains)
A dynamic routing system that watches market conditions in real time and switches to whichever user profile has historically performed best in that exact context:
- Hot small-cap tape → routes to the user who crushes momentum setups
- Slow/cold market → routes to the user who thrives in consolidation
- First 30 minutes of day → routes to whoever has the best 9:30–10:00 AM track record
- Earnings season / macro volatility → routes to the user who performs best in high-VIX conditions
- Switches dynamically, automatically, with no human intervention

This is ensemble trading — the same concept behind hedge fund pod structures — built from real retail trader data collected passively through a product they're already paying for.

### The Business Model Expansion (Updated April 10, 2026)

**Tier 1 — $49/mo:** Personal brain (structure predictions, TCS calibration, win rate tracking)
**Tier 2 — $99/mo:** Personal brain + daily Telegram scanner alerts
**Tier 3 — $199/mo:** License a top trader's verified brain (they earn ~40% revenue share)
**Tier 4 — $999/mo:** Retail Meta-Brain (dynamic routing across verified profiles, live conditions)
**Tier 5 — $5,000–$15,000/mo (annual):** Professional/Institutional Meta-Brain (signal output for prop traders, small funds)
**Revenue share:** Top performers earn passive income automatically from brain licensing

### Revenue Projections by Phase

**Phase 1–2 (0–6 months): Beta → Early paid**
~100–200 paid users at avg $49 → **$5–10K/mo = $60–120K ARR**

**Phase 3–4 (6–18 months): Automation live, product proven**
500 users:
- 250 × $49 = $12,250 | 175 × $99 = $17,325 | 75 × $199 = $14,925
- **~$44,500/mo = $534K ARR**

**Phase 5 (18–30 months): Meta-brain live, marketing agency engaged**
2,500 users across all tiers:
- 1,200 × $49 = $58,800 | 800 × $99 = $79,200 | 400 × $199 = $79,600
- 90 × $999 = $89,910 | 10 × $5,000 = $50,000
- Plus marketplace cuts: +$150–300K/yr
- **~$357,500/mo = ~$4.5M ARR total**

**Phase 6–7 (3–5 years): Asset expansion + institutional**
10,000+ users + B2B licensing:
- Consumer SaaS: ~$1.4M/mo | Institutional licensing: $2–5M/yr additional
- **~$19–22M ARR**

**Exit multiples:**
- At $4.5M ARR (Phase 5): **$36–67M** at 8–15× SaaS multiple
- At $20M ARR + institutional data locked in: **$200–300M acquisition target**
- Acquirer buys: dataset, brain architecture, user base — not just SaaS revenue

**Why the data moat is the actual asset:**
- Competitor launches today → starts with zero verified trade data
- EdgeIQ at 500 users → already has 500 brains, thousands of verifications, real P&L outcomes
- Meta-brain requires years of consistent user data — no shortcut, no way to replicate
- Every month widens the gap no competitor can close by just writing better code

**Owner data access:**
- As platform operator you own all data in your Supabase instance — fully legitimate
- Aggregate/anonymized use for collective brain + meta-brain = standard SaaS practice (Spotify, Netflix, Google all do this)
- Terms of Service covers internal use for platform improvement
- Opt-in required only for leaderboard/brain-sharing — personal brain data always stays isolated
- External institutional licensing requires explicit opt-in + revenue share language in ToS

### What needs to be true for this to work
1. Users must log consistently (this is why the trade log form and CSV import exist — reduce friction)
2. Each brain needs enough data to be meaningful (target: 50+ verified trades per user before routing)
3. The switching logic needs market regime context (the hot/cold/neutral market phase we discussed)
4. Privacy: users must opt-in to the meta-brain / leaderboard tier — personal brain data always stays isolated unless explicitly shared

### Build order implication
Nothing changes about Phase 1–4 order. The foundation being laid right now (trade logging, brain weights, structure verification) IS the meta-brain's raw material. No wasted work.

---

## 🌡️ MARKET REGIME DETECTION (To Add — Discussed April 10)
Tag each prediction + verified outcome with the market regime at the time.
Regimes: Hot (small-cap tape ripping), Cold (low volume, no follow-through), Neutral/Transitioning.
Currently in: HOT ZONE (small caps, April 2026).
Use: not as hard filters, but as multipliers — hot tape → bullish breakout predictions carry higher confidence.
Build approach: multiplier on existing signals, NOT a mode switch. Bot never sits on its hands.
Current status: BACKLOG — collect regime-tagged data passively first, build weighting logic once enough tagged samples exist.

---

## 🏆 COMPETITIVE POSITIONING & VISION (Saved April 8, 2026)

### What corporate trading desks actually sell
Hedge funds and prop desks sell one thing: consistent, explainable edge with controlled drawdown.
Not prediction accuracy alone — *calibrated* accuracy. Knowing when your signal is right AND when
it isn't is worth more than being right 70% of the time without knowing which 70%.

EdgeIQ is built around that exact concept. The brain weight system doesn't just track win rate —
it knows win rate by structure type, which means it can say "don't take this trade, your historical
edge on this structure is 42%." That's risk-adjusted signal filtering. That's what quant desks do.

### The three things that make it genuinely competitive

**1. The discovery engine (Phase 2)** — if it surfaces real, reproducible edges that hold up over
time, you have something quantifiable and defensible. "Our system discovered that X condition
produces Y outcome with Z% confidence across N trades" is a pitch, not a claim.

**2. Asset class expansion** — right now it's small-cap equities. The same volume profile +
structure framework applies to futures (ES, NQ, crude), crypto, and forex. Same architecture,
different data feeds. A system that works across asset classes with per-instrument calibration
is institutional-grade infrastructure.

**3. The personalization layer at scale** — this is the actual moat. If the brain calibrates to
each user's edge rather than a generic edge, you have something no corporate desk has built for
retail. Renaissance doesn't care about your individual win rate — they run one model across
everything. EdgeIQ runs a different model per trader. At scale, that's a SaaS product that
corporate analytics firms would license, not just individual traders.

### Why it won't hit the "posted my algo" ceiling
The "posting my algo" ceiling: someone backtests something, it works on paper, they publish it,
it stops working. Because they never had a calibration loop — they optimized to historical data.

EdgeIQ has a live feedback loop. Systems with live feedback loops that actually adapt don't stop
working — they learn when market conditions shift. That's why Renaissance has worked for 30+ years
while every "I published my algo" strategy has a 6-month half-life.

---

## 📈 PATTERN NOTE — Ascending Base + Liquidity Break (logged April 8)

**Setup:** Stock in sideways-to-upward angled consolidation (higher lows visible on 5m or 15m). Previous swing high = liquidity zone (cluster of stop orders sitting above it).

**Entry trigger:** 5m or 15m candle closes ABOVE the previous high AND the following candle holds above it (does not immediately reverse back through).

**Why it works:** The close-above filters out stop hunts (wicks through = fake). Two consecutive candles above the level = real buyers absorbed the liquidity and are defending the breakout.

**Tier 4 detection candidate:** Requires trendline detection (ascending base angle auto-identified) + previous high level flagged as liquidity zone + breakout candle + confirmation candle. Full auto-detection needs Tier 4 trendlines first.

---

## 🔒 PRESERVATION RULES (NEVER MODIFY)
- compute_buy_sell_pressure()
- classify_day_structure()
- compute_structure_probabilities()
- brain_weights.json
- Architecture: math/logic → backend.py only; UI/rendering → app.py only


## 2026-04-08 — Simulation Log Upgrades
- **Deduplication**: Added dedup logic by \`(ticker, sim_date)\` key before rendering the trade-by-trade log; shows a count if dupes were removed
- **Per-Ticker Breakdown table**: New expander above the log showing each ticker's: Win %, W/L count, Avg TCS, Top Structure, Avg Follow-Thru %, False Break %, Dates Seen — sorted by win rate; color-coded 🟢/🟡/🔴
- **Date column**: Added "Date" as column 0 in the trade-by-trade log so each row shows which trading date it belongs to (useful for range runs)
- \`_BT_COLS\` expanded from 10 to 11 columns; all row cell indices shifted accordingly

## 2026-04-08 — Load Saved Simulation Results
- Added "📂 Load Saved Simulation Results" expander in Section 2 (Simulation) of the Backtest tab
- Flow: (1) "Fetch My Saved Dates" → pulls distinct sim_dates from Supabase, (2) multiselect picker for date(s), (3) "Load Selected" reconstructs _results list + _summary dict from saved DB rows, injects into bt_results_cache session state, then st.rerun() so full chart/stats pipeline renders automatically
- Handles field remapping: follow_thru_pct → aft_move_pct; reconstructs actual_icon from outcome text; close_price defaults to 0 if not stored
- Works for single-day and multi-date range loads; _sim_is_range=True when >1 date selected

## 2026-04-08 — Auto Paper Trading Tab (📄 Paper Trade)
- New 8th tab added: "📄 Paper Trade"
- **Section 1 — Scan & Log**: date picker, feed selector, TCS min slider (default 50), price range, tickers textarea (pre-filled with watchlist). Calls run_historical_backtest → filters TCS ≥ min → calls log_paper_trades → deduplicates by (ticker, trade_date) before saving. Shows preview table of qualifying setups.
- **Section 2 — 3-Week Tracker**: 4 KPI cards (win rate, total setups, avg TCS, avg follow-thru), daily win rate trend chart with 55% target line, per-ticker breakdown table, full log expander
- **Backend additions**: ensure_paper_trades_table(), log_paper_trades(), load_paper_trades() in backend.py. Schema shown as SQL fallback if table doesn't exist yet.
- **Table**: paper_trades — user_id, trade_date, ticker, tcs, predicted, ib_low, ib_high, open_price, actual_outcome, follow_thru_pct, win_loss, false_break_up/down, min_tcs_filter, created_at
- User must create paper_trades table in Supabase SQL editor (shown on first load if missing)

## 2026-04-08 (Evening) — Paper Trader Bot + Adaptive Learning Loop

### Paper Trader Bot (paper_trader_bot.py) — FULLY LIVE
- User created paper_trades table in Supabase ✅
- Bot confirmed connected (HTTP 200) and watching **45 tickers** from live Supabase watchlist
- Fixed: was hardcoded to 14 stale tickers. Now calls load_watchlist(USER_ID) on every startup → falls back to 14 only if Supabase load fails
- Schedule: **10:35 AM ET** morning scan → **4:05 PM ET** EOD outcome update → **4:10 PM ET** brain recalibration
- RENX already in watchlist (position 43 of 45) — user noted potential reverse H&S forming on 15m, not confirmed yet

### Paper Trade Tab — 3 New Sections Added
- **Live Auto-Scan toggle**: when browser open during market hours, auto-scans every 30 min
- **Section 3 — Manual EOD Update**: force-update outcomes for any date on demand (don't wait for bot's 4:05 run)
- **Section 4 — IB Window Comparison**: same tickers through 10:30 / 12:00 / 14:00 cutoffs in parallel; shows win rate, W/L, avg TCS, follow-thru, false break side by side; tells you which cutoff produces cleanest signals
- **Section 5 — Brain Health**: live weight table with status badges (🟢 Boosted / ⚪ Neutral / 🔴 Penalized), "Recalibrate Now" button, "Reset to Neutral" button

### Adaptive Learning Loop (recalibrate_from_supabase) — COMPLETE
- New function in backend.py reads from BOTH:
  1. accuracy_tracker (journal-verified trades) — predicted / correct ✅/❌
  2. paper_trades (bot automated signals) — predicted / win_loss
- **Volume-weighted source blending**: each source's accuracy computed independently per structure, then blended by sample count. More data = more influence. NOT a fixed 50/50 split — the source with more samples carries proportionally more weight.
- Blend rules per structure:
  - Both ≥ MIN_SAMPLES → blended = (j_n × j_acc + b_n × b_acc) / (j_n + b_n)
  - Only journal ≥ MIN_SAMPLES → use journal only
  - Only bot ≥ MIN_SAMPLES → use bot only
  - Neither ≥ MIN_SAMPLES → skip (no update)
- MIN_SAMPLES scales with total data: 3 (<50 trades), 5 (<200), 8 (<500), 12 (500+)
- EMA learning rate also scales: 0.10 (<10 per structure) → 0.40 (100+ per structure)
- Weights saved per-user in Supabase (user_preferences.prefs["brain_weights"]) — isolated from other users
- Brain Health table shows: Journal Acc (n), Bot Acc (n), Blended Acc, Last Δ, Status per structure
- Bot auto-runs recalibration at 4:10 PM ET after EOD outcomes settle

### Data Flow Map (complete chain)
\`\`\`
Journal Tab (pre-market predictions)
    ↓ verify button EOD
watchlist_predictions table
    ↓ verify_watchlist_predictions()
accuracy_tracker table ←── also fed by manual trade log_accuracy_entry()
    ↓
recalibrate_from_supabase() [4:10 PM daily]
    ↑
paper_trades table ←── bot logs at 10:35 AM, outcomes at 4:05 PM
    ↓
volume-weighted blend per structure (≥MIN_SAMPLES each)
    ↓
brain_weights → Supabase per-user prefs
    ↓
TCS scoring uses updated weights next morning
\`\`\`

### Gemini Code Review (April 8 evening) — Points Addressed
1. **Slippage 0.0%**: Valid for Phase 4 — paper calibration only measures signal quality not P&L. Add 0.75% default for live sim in Phase 4. ✅ Noted.
2. **Sample skew**: Fixed with volume-weighted source blending. Simple 3x multiplier was wrong. Now uses proportional sample-count weighting. ✅ Fixed.
3. **Edge case — 0 journal entries**: Already handled by MIN_SAMPLES=5 gate. Bot data only until journal hits 5. ✅ Already correct.

### Tomorrow's Reminders
0. ~~**Inside Bar pattern**~~ → ✅ BUILT April 12, 2026. Added to \`detect_chart_patterns()\` on both 5m + 1hr. Compression scoring + POC/IB confluence.
1. **Clean accuracy_tracker**: Remove entries for tickers outside user's trading universe (random predictions that polluted the table). Calibration reads from accuracy_tracker — out-of-universe tickers skew structure weights.
2. **Webull CSV import pipeline**: Build import flow that maps each Webull trade (entry date + ticker) to an IB structure, feeds accuracy_tracker automatically. Removes all manual work for calibration.
3. **Go through core functions with AI**: Review brain weight math, TCS scoring, IB structure logic, blend accuracy — function by function review (user requested, Gemini timed out on full paste).
4. **Slippage Phase 4**: When building live sim / entry signal layer, default slippage to 0.75% for small-cap $2–$20 range.
5. **Phase 4 planning**: After 3-week paper calibration proves signal quality — add entry trigger (IB breakout + volume confirm), stop loss (IB low - 1 ATR buffer), target (measured move from IB height), position sizing (account % risk).

### Key Decisions Made
- **EOD journal is NOT optional**: Each verified journal entry is a direct training signal for brain weights. Miss journals = weights don't reflect your actual edge.
- **Watchlist predictions (untaken trades) are FINE for calibration**: Measures structure accuracy not trade P&L. Only clean up tickers completely outside your universe.
- **3-week paper window is deliberate**: Structure prediction must be proven before adding entry/exit layer. Bot currently predicts structure + win/loss, NOT buy price / stop / target (Phase 4).
- **Window comparison logic**: More cutoff windows = more calibration data. 3 windows × 45 tickers × 3 weeks = ~405 data points vs 135 single-window. Also reveals whether waiting for midday confirmation improves win rate.

### Current Watchlist (45 tickers as of April 8)
HCAI, MGN, HUBC, TDIC, SILO, CETX, IPST, LNAI, ZSPC, CUE, SKYQ, SIDU, CUPR, LXEH, KPRX, MEHA, JEM, AXTI, ADVB, TPET, WGRX, AAOI, MAXN, IRIX, PROP, AGPU, BFRG, MIGI, PPCB, CAR, AMZE, UK, TBH, AIB, ITP, ARTL, NCL, PSIG, RBNE, CYCU, LPCN, FCHL, RENX, MOVE, TURB

---

## Product Strategy Brainstorm — 2026-04-09 (pre-sleep session)

### Product Identity (locked tonight)
EdgeIQ is a **systems tool for traders to find their personal edge, then automate it.**
"Find your edge, then automate it" — the name always pointed here. Product truth articulated April 9.

### Repriced Tier Structure
| Tier | Price | Description |
|---|---|---|
| Starter | $49/mo | Journal + calibration + edge analytics. No live scanner. Entry tier. |
| Pro | $99/mo | Full scanner + alerts + calibration + paper trading. Core product. |
| Autonomous | $199/mo | Live trading enabled after edge proven. Bot manages positions. Phase 4 unlock. |

ARR projections (Pro tier):
- 50 users = $59,400/yr
- 100 users = $118,800/yr
- 500 users = $594,000/yr

### Copy Algo Tier Concept (brainstormed tonight)
User asked: "What about an expensive tier to copy a top learned algo from user data?"

**Verdict: Does NOT defeat the selling point — it's a separate product layer.**

Concept: "Copy Algo" marketplace tier ($299+/month)
- Top-performing EdgeIQ users (verified 12+ months, 65%+ win rate) opt in to share their brain weights
- Subscribers copy their structure preferences, alert thresholds, and weights
- Revenue share back to the algo owner
- The copy subscriber eventually migrates to their own calibration over time ("training wheels")

Risks to address:
- Top algo hits a drawdown → users blame EdgeIQ → need clear disclaimer and expectation setting
- Brain weights alone aren't the full picture (execution still matters)
- Requires minimum data threshold before anyone's algo is "shareable"

**This is Phase 4-5 territory. Don't build it until 50+ active users have 6+ months of data each.**
It's a marketplace play — needs supply (proven algos) before it can have demand.

### Direct Broker Sync — What's Actually Possible
User asked: "Is Webull direct sync possible? How much would that cost?"

**Webull: NO public retail API.** Institutional API exists but not accessible to independent developers.
Workaround is CSV export (current approach). Could theoretically scrape but fragile and against ToS.

**What IS possible for direct sync:**
- Alpaca — already integrated (paper trading live)
- Tradier — has public API, ~$10-25/month for live data feed
- Interactive Brokers (IBKR) — has API, complex but powerful, used by sophisticated traders
- TD Ameritrade/Schwab — API exists post-merger, Schwab is opening it up
- Robinhood — no API

**Priority order for direct sync:**
1. Alpaca (done — Phase 4 live trading)
2. IBKR (most serious traders use it, highest value add)
3. Tradier (easy API, good for mid-tier users)
4. Webull CSV will likely remain the workaround indefinitely

**Cost to build:** IBKR integration ~2-3 days of dev work. No licensing fee for read-only position sync. Live trading via IBKR requires account + API key from user.

### Features That Would Make EdgeIQ Huge
1. **Direct broker sync** — no CSV needed. Trades auto-import, auto-enrich, auto-calibrate. Near-zero churn.
2. **"Prove Your Edge" report** — exportable PDF: win rate by structure, best/worst setup, profit factor. Traders share these. Free marketing flywheel.
3. **Community aggregate insights** — individual brains stay personal. But publish: "Normal Day setups on IWM uptrend days = 74% win rate across all EdgeIQ traders." Proprietary research. Drives acquisition.
4. **Autonomous phase with verified track record** — flip from paper to live with 6 months of documented proof. That's a news story.
5. **Zero-friction Telegram journaling** — log a trade in 10 seconds while watching the chart. (Building tomorrow.)

### Acquisition Potential (user raised tonight)
**Real. The autonomous phase is the trigger.**

Potential acquirers:
- **Brokers** (Webull, IBKR, Alpaca) — they want calibration data + user base. Alpaca especially aligned since they're already the infrastructure.
- **Fintech platforms** (Bloomberg, FactSet) — small-cap retail trader segment is underserved at institutional level
- **Trading education companies** — the "find your edge + grow as a trader" angle fits ed-tech
- **Prop firms** — a tool that produces traders with proven, documented edges is valuable for recruiting/allocating capital

**What makes it acquirable:**
- Proprietary dataset of trader behavior mapped to market structure outcomes (no one else has this)
- Autonomous trading with verified track record (de-risked product for acquirer)
- Sticky user base (high switching cost = predictable ARR)
- Clean Alpaca integration = easy for broker to white-label or absorb

**When to think about it:** After Phase 4 goes live with 3+ months of autonomous trading with documented returns. That's when the asset has a price.

### The Real Moat (clarified tonight)
NOT the math (replicable).
NOT the data volume (someone could upload more CSVs).

THE MOAT IS:
1. Personal calibration — the bot learns YOUR edge, not a market average. A competitor clone starts flat.
2. The feedback loop — traders improve AS traders using this. They see their own patterns. They don't leave mirrors.
3. Time lead + first mover with real users — by the time anyone notices the niche, you have 12 months of data and a community
4. Switching cost — leaving means losing your entire calibration history

The sell point in one sentence: "EdgeIQ shows you exactly what your edge is, then automates it for you."

### Acquisition Valuation — "If This All Goes According to Plan"
User asked: "How much would a prop or Alpaca pay?"

**"According to plan" definition:** 200+ active users, 12+ months of autonomous trading with documented returns, $20k+ MRR, clean Alpaca integration, verified track record.

**Realistic acquisition range at that milestone: $3M–$8M**

Breakdown by buyer type:

**Alpaca (most likely, most strategic):**
- Already the infrastructure layer. EdgeIQ users ARE Alpaca users.
- They'd value: sticky user base (high LTV, documented trading activity), calibration technology, AUM generated by autonomous accounts
- The story they'd buy: "We acquired the tool that proves retail traders can be profitable on our platform"
- Range: $3M–$8M depending on user count and autonomous track record
- Structure: likely acqui-hire (keep you running it) + earnout tied to user growth

**Prop Firms (TopStep/FTMO tier, NOT Jane Street):**
- They'd be buying: a pipeline of traders with documented, verified edges + the calibration technology
- Most valuable to them: EdgeIQ users arrive pre-qualified. They already know their win rate, their best setups, their structure performance. That's the prop firm's intake process automated.
- Range: $1M–$5M. More if the autonomous returns are strong and consistent.

**Trading Ed-Tech (Warrior Trading, SMB Capital tier):**
- They want: brand + user base + the "find your edge" curriculum angle
- Range: $1M–$3M, likely structured as earnout
- Less upside here — they'd probably just want to white-label it

**What drives the number higher:**
- Autonomous phase live with 3+ months documented returns = biggest single lever
- 500+ active users = institutional attention
- "Prove Your Edge" shareable reports going viral = brand value
- Multi-broker sync = broader addressable market

**What the number is WITHOUT autonomous phase:** ~1-3x ARR. A $20k MRR journaling tool = ~$720k ARR = $1.5M–$3M. Respectable. The autonomous phase is what pushes it to the $5M–$15M range.

**When to think about it:** After Phase 4 has 3+ months of live documented returns. Before that, you're selling potential. After that, you're selling proof.

---

## 🧠 BRAINSTORM SESSION — April 10, 2026 (Full Capture)

---

### ✅ BOT AUTO-VERIFY EOD — ALREADY BUILT (confirmed April 10)
\`nightly_verify()\` runs at 4:25 PM ET every trading day. Calls \`verify_watchlist_predictions()\`, posts results to Telegram. The brain gets fresh signal data every session without any manual button press. This was built in Phase 3 of the bot — no action needed.

---

### 🏗️ STRUCTURE DETECTION — ONLY 3 OF 7 SHOWING UP
Not a detection bug. It's a market regime composition effect.

In the current hot small-cap momentum tape (April 2026), nearly every day produces Neutral Extreme (both IB sides broken, closes at extreme) or Trending Day (one side dominant early). The other 5 structures — Non-Trend, Double Distribution, Normal Variation, pure Neutral, Normal — require cold/low-volume/range-bound conditions that simply aren't present right now.

The detection logic in \`classify_day_structure()\` is correct. The tape is not producing the full range of structures. This is exactly why market regime tagging matters — once every outcome is tagged with the regime at time of trade, you'll see exactly which structures cluster in which market conditions. No code fix needed. Just keep logging and let the data accumulate.

---

### 🕌 ISLAMIC COMPLIANCE FILTER
Not noise, not core. It's a sector + debt ratio filter toggle on top of the existing screener — a user preference layer for a specific segment. The edge model doesn't need it. Low priority. Table for Phase 2+ as a checkbox setting in user preferences. Do NOT build during Phase 1.

---

### 📊 PAPER TRADE SAMPLE SIZE — REVISED UP TO 700+
Original estimate of ~500 was optimistic. The correct number for statistical significance across all 7 structures is **700+ total rows**.

Math: 7 structures × 50 verified trades each = 350 theoretical minimum. But Double Distribution and Non-Trend are rare in momentum markets, so in practice you need 700+ total entries to ensure all 7 reach n ≥ 50. At ~15 tickers/scan × 5 days/week = ~75 predictions/week → 700 rows in ~9–10 weeks of consistent scanning. Start the clock now.

---

### 📡 PRE-MARKET GAP % + RVOL — NO SIP NEEDED
The pre-market gap scanner already runs at 9:15 AM and calculates gap % + PM RVOL using Alpaca's free feed. SIP cap (now - 16min) only applies to intraday real-time data, not pre-market historical bars.

**The actual gap:** The scanner sends these values to Telegram but does NOT save them to Supabase alongside each prediction. Phase 2 needs them as logged features per prediction row for cross-tab analysis.

**Fix (Phase 2 task):** Add \`pre_mkt_gap_pct\` and \`pre_mkt_rvol\` columns to \`accuracy_tracker\` table. Log them at the time the morning scan runs. Zero new data infrastructure required — just schema + logging additions.

---

### 📈 IWM AUTO-LOOKUP — AUTOMATE REGIME TAGGING
IWM day type (Trending Up / Trending Down / Range-Bound) is the primary small-cap tape quality proxy.

**Auto-tag every trade source:**
- **Bot paper trades:** fully automatic at execution — IWM day type known at scan time, log it
- **Manual journal entries:** trade date is recorded → retroactive IWM bar lookup via Alpaca historical data → derive day type → attach to record
- **Webull CSV imports:** same as journal — date → retroactive lookup → backfill regime tag on import

All three sources can be fully automated. No manual input required from user. The IWM lookup on import/journal-save is a one-time Alpaca historical bars call per date.

---

### 🛑 STOP LEVEL TYPES — FULL LIST (April 10 expansion)

Previous short-list (POC, HVN, IB Low, VAL, Whole Number, ATR) was incomplete. Full stop reference library:

**Volume profile family:**
- POC (point of control — volume-weighted center of gravity)
- VAH / VAL (value area edges — institutional balance zone boundaries)
- HVN (high volume node — where price has spent time = real support/resistance)
- LVN (low volume node — thin areas price moves through fast; stops here get hunted)

**IB structure:**
- IB High / IB Low (the original balance zone boundaries)

**Psychological levels:**
- Whole dollar and half-dollar levels (resting order clustering)

**Chart structure:**
- Prior day high / prior day low
- Prior week high / prior week low
- VWAP and anchored VWAP
- Trendlines (ascending support / descending resistance)

**Order flow / tape:**
- Significant liquidity zones — areas where tape evidence (large prints, absorption, Level 2 clustering) confirms resting institutional orders. Often invisible on chart, visible in order flow. Among the strongest stop references.

**Support/resistance:**
- Prior consolidation zones, swing highs/lows tested and held 2+ times

**Mirrored structure (for longs AND shorts):**
In auction theory, once price breaks a level cleanly and retests from the other side, it flips polarity. Former IB high broken → becomes new support on retest (stop for long goes below it). Former POC from prior session that acted as ceiling → now acts as floor. The same level types apply directionally flipped:
- Longs: stop below structural support
- Shorts: stop above structural resistance
Full symmetry — volume profile + IB + liquidity zone logic applies identically to both directions.

**Volatility backstop:**
- ATR (1.5×–2× of relevant timeframe) used when no clean structural level is within reasonable distance

---

### 🎯 LEVEL-AWARE STOP SELECTION — PHASED APPROACH

**Phase 3 (now):** Use ranked priority list as default stop selection. The Kelly-stop-distance math naturally self-corrects:
> Position size = (account × Kelly %) ÷ stop distance

Tighter stop at strong level → larger size for same dollar risk. Wider stop → smaller size. Weak level selection is error-damped automatically by the sizing math.

**Phase 4–5 (future):** The system learns which level type produces the best stop placement per structure type. Accumulated stop-out logs tell you: "For Neutral Extreme setups, IB Low outperforms HVN as a stop reference by X% in outcomes." That calibration loop — same architecture as brain weights, new dimension (level type performance by structure).

**Don't build the weighting logic now.** Collect the data (log which level type was used as stop per trade), let Phase 4–5 analyze it.

---

### 💧 WICK FILLS, FADES, STOP HUNTS — DATA COLLECTION DESIGN

One of the most underappreciated sources of P&L leakage in systematic trading. Price wicks through the stop level, you're out, then it reverses and hits target. Thesis correct. Trade correct. Stop too precise.

**Key insight (April 10):** LVN areas on the volume profile are essentially thin ice — price moves through them fast and snaps back because there's no resting order density. A wick through an LVN with no volume = the market probing for orders, finding none, returning. Stops placed just past an LVN get hit on pure mechanics, not because the thesis was wrong. Wicks through low-volume areas have a systematically higher probability of not sticking.

**Data to log per stop trigger:**
- Was stop hit on a wick (intrabar) or a bar close through the level?
- What was the volume at the candle that hit the stop? (low vol = possible hunt)
- What did price do in the next 5 bars after stop hit?

**Three stop execution approaches (decide later, based on data):**
1. Close-based stops — only exit on bar close through level (fewer fake-outs, occasionally worse loss)
2. Buffer stops — stop = level minus small ATR multiple (absorbs typical wick depth)
3. Volume-confirmed stops — wick through low volume = ignore; close through elevated volume = real exit

**Action now:** Just log wick vs. close-through and post-stop price action. Don't change stop execution logic yet. The data will tell you which approach wins for which structure type.

---

### ⏱️ MULTI-DIMENSIONAL MARKET REGIME (April 10 expansion)

Hot/cold/neutral is the baseline. The full regime picture is layered and all dimensions become compounding TCS multipliers — never hard mode switches. Bot never sits on hands.

**Tape regime:** Hot / Cold / Neutral (already conceptually defined)

**Time of day:**
- 9:30–10:00: IB formation — most volatile, highest false-signal rate
- 10:30–11:30: cleanest signal window — IB complete, structure clear
- 11:30–1:00: midday chop — lower conviction on most setups
- 1:00–2:30: secondary momentum window — institutional flow returns
- 2:30–4:00: afternoon fade risk — win rate systematically lower for most traders

**Day of week:**
- Monday: follow-through from Friday OR sharp gap against — binary, not clean
- Tuesday–Thursday: cleanest data days — most reliable structure behavior
- Friday: fade tendencies, position unwinding, lower follow-through on breakouts

**Week of month:**
- Week 1: fund flow / institutional positioning — often directionally clean
- Week 3 (OpEx): options expiration introduces pinning + vol distortions that warp structure
- Week 4: window dressing, choppier into month-end

**Month/season:**
- April: historically one of the strongest months (tax refunds, pre-earnings momentum)
- September: statistically the worst month across nearly all asset classes
- January: strong directional bias early; sentiment-driven, not structure-driven

**Action now:** Tag every trade with all of these at the time of logging. Do NOT build multiplier weighting yet — collect data first, discover which dimensions actually matter via Phase 2 cross-tab analysis.

---

### 🧘 BEHAVIORAL ANALYTICS LAYER — FULL VISION (April 10)

A second product category built on the same infrastructure. Tracks the *human* side of trading — the gap between what the signal said to do and what actually happened in execution.

**What it measures per trade:**
- Entry timing deviation (signal at 9:47, entry at 10:12 = hesitation quantified)
- Stop adherence (honored vs. moved — separate P&L curves)
- Target adherence (exited at level vs. cut early)
- Size deviation (Kelly said 3.2%, traded 6% — why?)
- Consecutive loss behavior (win rate collapse pattern after 2–3 losses)
- Time-of-day performance decay (morning vs. afternoon win rates)
- Thesis accuracy vs. execution accuracy (the gap = pure behavioral leakage)

**The core product insight:**
"Trade your plan" is advice. "Your P&L would be 34% higher if you honored your stops, here's the exact calculation" is a product. The addressable market: every trader who knows their setups work but can't consistently execute.

**The industry analogy:** Whoop / Oura Ring for athletes — passive behavioral data collection correlated to performance outcomes. Nobody has built this for traders with actual outcome data.

**What already exists on the market:**
- Edgewonk: psychology journal with self-assessment fields (manual, no pattern detection)
- TraderSync / Tradervue: optional mood tags (self-reported, no outcome correlation)
- Brett Steenbarger (top trading psychologist): extensive writing, no product
- None of them: automated pattern detection from trade data, P&L outcome correlation, feedback into signal confidence, scale

EdgeIQ would be the first platform with all four. Data-backed behavioral coaching vs. opinion-based coaching.

---

### 📱 TELEGRAM BEHAVIORAL CHECK-IN — DESIGN

Post-session bot prompts (3–4 questions max). Feel like a supportive journal. Behavioral data collected silently on backend. Never reveal the analytical motive — users answer authentically only when they're not being tested.

**The questions (what they ask vs. what they measure):**
- "Did you follow your plan on every trade today?" → plan adherence score
- "On a scale of 1–10, how sharp did you feel?" → mental state tag
- "Did anything catch you off guard today?" → preparation quality, expectation calibration
- "Did you size the way you intended?" → sizing adherence flag
- Optional: "Walk me through your best decision today" → self-assessment quality (free text)

**The hook that drives daily engagement:**
Immediately after answering, bot sends a personalized recap: "You rated 8/10 sharpness today. Your win rate on 8+ sharpness days is historically 74% vs 51% on low-sharpness days." That's data the user can't get anywhere else. That's why they answer every day.

**Critical rule:** Never use EdgeIQ internal language in behavioral prompts. No TCS, no brain weights, no structure names. The questions must feel like a thoughtful friend asking about their day, not a system logging variables.

---

### 🔄 BEHAVIORAL DATA → BRAIN FEEDBACK

Behavioral state tags eventually feed back into the signal engine. The brain learns: "when this user has had 3+ consecutive losses, suppress confidence threshold — their win rate in this state is 38% vs baseline 71%."

**Weight rules:**
- Maximum influence: 5–8 TCS threshold points (small, never enough to override strong structural signal)
- Only triggered on data-confirmed patterns (not one bad day)
- Transparent to user — surfaced as a coaching note, NOT using TCS language
- **Accept / Ignore option** — user sees the suggestion, chooses to follow or dismiss. Coaching, not gatekeeping.
- The users who consistently follow behavioral adjustments will have better outcomes — that data validates the feature automatically over time

**Framing to user:** Something like "You've had 3 tough sessions in a row. Based on your history, your best setups today are [X structure]. Consider sitting out [Y structure]." Feels helpful. No mention of thresholds or internal mechanics.

---

### 🏢 BEHAVIORAL ANALYTICS — MARKET SEGMENTS

**Retail traders:** Primary market. Everyone who knows their setups work but leaks P&L through execution. Massive, underserved, willing to pay for objectivity over opinion.

**Trading coaches / educators:** B2B. Coach dashboard showing anonymized behavioral profiles of students. "Student A has 78% thesis accuracy but only captures 52% of available P&L — execution problem, not signal problem." No coach can see this without the system. Pricing: $299–599/month per educator with 20–50 students. They're already charging $200–500/month per student.

**Prop firms:** Highest ACV B2B segment. Use for: screening funded applicants, ongoing performance coaching, capital allocation decisions. "EdgeIQ users arrive pre-qualified — they already have documented win rates and behavioral profiles." API access to trader behavioral profiles: $2,000–5,000/month per firm.

**Psychology researchers:** The accumulated dataset (thousands of traders, behavioral tags, real P&L outcomes) is academically valuable. Publishable research: "What behavioral patterns statistically separate consistently profitable traders from losing traders?" Nobody has this data at this specificity. Generates third-party credibility + licensing revenue.

**2-year compounding effect of behavioral data at scale:**
- "Traders who rate mental sharpness below 6 have 23% lower win rate on the same setups the following session"
- "Sizing discipline in the first week of EdgeIQ is the single strongest predictor of 90-day profitability"
- "Taking more than 3 trades after a stop-out leads to drawdown 61% of the time"
- These become: marketing ammunition, publishable findings, institutional licensing content, and product validation all at once

---

### 🏆 LEADERBOARD — TIER STRUCTURE (Corrected April 10)

Leaderboard is a **Tier 3+ benefit**, not a standalone feature or product.

- **Tier 1–2:** No leaderboard access. User only sees their own data.
- **Tier 2:** Anonymized platform-wide aggregate stats (top structure win rates, no names, no individual profiles)
- **Tier 3+:** Full opt-in leaderboard — named, verified track records, ranked by win rate + structure accuracy + regime performance
- Being on the leaderboard = status signal + gateway to earning brain licensing revenue from Tier 3 subscribers

---

### 🤖 FULLY AUTONOMOUS EXECUTION TIER — NEW TIER ABOVE META-BRAIN

Above Tier 4 (Retail Meta-Brain at $999/mo), a "Managed Autopilot" tier where the system trades independently throughout the day using full meta-brain signal output. No user interface interaction required during market hours.

**Framing:** "You authorize, we execute on your behalf. You are the trader of record." Keeps regulatory complexity manageable (not acting as investment advisor — user authorizes each account link).

**Tier 5 — Autopilot:** $10,000+/year or performance-based. Positioned not as a subscription but as a managed product. Meta-brain selects the best-performing profile for current conditions → sizes via fractional Kelly → executes → logs → recalibrates. Fully lights-out.

**Regulatory note:** This tier needs legal review before launch. User is trader of record, execution is on their authorized account — similar to how copy-trading platforms operate. Structure it identically to avoid investment advisor registration requirements.

---

### 💰 FRACTIONAL KELLY + STOP DISTANCE — COMBINED POSITION SIZING FORMULA

**Phase 4 implementation:**

> **Position size = (account × Kelly %) ÷ stop distance**

Where:
- **Kelly %** = edge-weighted risk per trade:
  - Base = verified win rate for THIS structure type (from brain)
  - × TCS confidence modifier (higher TCS = higher Kelly %)
  - × market regime multiplier (hot tape + favorable timing = higher)
- **Stop distance** = entry price − nearest significant structural level (from full level-priority list)

**Why this is better than pure Kelly alone:**
- Tight stop at strong level → larger position for same dollar risk (rewards high-conviction setups with nearby structure)
- Wide stop → smaller position (punishes setups where conviction requires large risk buffer)
- The math naturally sizes down on weak setups and up on ideal setups — without any additional rules
- Removes the last human variable from the execution loop

**Note:** Smart level weighting (choosing which level based on structure type + volume context) is a Phase 4–5 feature. Phase 3 uses ranked priority list as default. The sizing formula already partially corrects for imperfect level selection.

---
## REMINDER — Tomorrow (2026-04-10) — HIGH PRIORITY

### 1. Build Telegram → Journal incoming pipeline (~40 min)
- Polling thread inside paper_trader_bot.py
- Command: \`/log MIGI win 1.94 2.85\`
- Parses → logs to Supabase → enriches with IB/TCS/RVOL for that date → confirms back
- Text-only first. Photo attachment is Phase 2.

### 2. Help onboarding 2 beta tester candidates
User has 2 specific people in mind. Need to:
- Create their EdgeIQ logins in Supabase (user does this, we guide)
- Set up Telegram group: user creates group → invites bot + both testers → get group chat_id → update TELEGRAM_CHAT_ID secret
- Explain the daily workflow to them in plain language (user needs help with this pitch/explanation)
- Walk through the Webull CSV export process so they can do the first backfill
- Data isolation: each tester has their own user_id, RLS handles separation

### 3. From earlier tonight (carried forward):
- Multi-user beta setup: need to change TELEGRAM_CHAT_ID to a group chat so multiple testers receive alerts. Store per-user chat_id in user_preferences for proper multi-user eventually.
- Telegram GROUP setup: user creates group, invites bot + testers, get group chat_id, update TELEGRAM_CHAT_ID. Phase 2.
- Beta tester minimum viable setup: Telegram group (alerts) + EdgeIQ login (journal) + Webull CSV (weekly backfill)
- 2 users same trade = 2 rows in accuracy_tracker isolated by user_id = fine for Phase 1
- Telegram → journal incoming pipeline: DOES NOT EXIST yet. Needs to be built. High value. User asked about this.

---

## 🧭 DISTRIBUTION STRATEGY — First Users (Saved April 10, 2026)

### Reddit Targeting — Organic Approach Only (No Spam)

**r/Daytrading** — PRIMARY target. Active daily traders who already understand momentum, volume, and structure but do it manually without a calibration system. Look for anyone asking:
- "How do I know if my setups actually have edge or if I'm just getting lucky?"
- "What's the best way to journal and track which setups work?"
- "How do I size positions based on win rate?"
These people ARE the product's customer. Answer genuinely, mention you built something that solves exactly that, and you're looking for beta testers.

**r/algotrading** — SECONDARY, feedback-focused. Sophisticated quants who will stress-test and ask hard questions. Good for validation and credibility, NOT likely to be paying users (they build their own tools). Use for refining the pitch and finding edge cases.

**r/smallstreetbets, r/pennystocks, r/RobinHoodPennyStocks** — small-cap focused, active traders, high overlap with the watchlist universe. Lower technical bar, higher likelihood of becoming a paying user.

**The ONLY approach that works without getting banned:**
Do NOT post promotional content. Go answer questions authentically. Provide real value. When the context is right — someone asking about edge tracking, position sizing, win rate calibration — answer the question fully, THEN mention you built something for exactly this and are looking for a few beta testers. That's help, not promotion.

**Systems-thinking filter:** Look specifically for people who ask about win rate BY SETUP TYPE, calibrating signals over time, logging frameworks, or position sizing based on edge. These are people who already think the way EdgeIQ is built. You're not selling them a concept — you're giving them the infrastructure for something they're already trying to do manually.

### Twitter/X — High Upside
Small-cap momentum Twitter is extremely active and vocal. One post showing a real bot-called trade verified EOD — predicted pre-market, confirmed after close — gets shared fast in that community. The concept is visual and demonstrable. Don't announce. Demonstrate.

### The First 2-3 Paying Users
- $49/month each
- Show the system running live — bot calling setups, Telegram alerts, EOD verification
- The product sells itself when seen in action
- Can happen within 2 weeks of actively reaching out
- Proves monetization works and gives real data point for the dad conversation

---

## 📅 BUILD TIMELINE — Reality Check (April 10, 2026)

**First data point in system:** March 30-31, 2026 (accuracy_tracker.csv)
**Today:** April 10, 2026
**Time elapsed:** ~38 days

**What was built in 38 days:**
- Full trading terminal (Streamlit, dark mode, Plotly)
- 7-structure IB classification engine
- TCS scoring system (0-100)
- Time-segmented RVOL with 5-day baseline
- Tier 2 order flow signals
- Nightly brain recalibration loop (EMA learning)
- Fully autonomous paper trading bot (5-event daily schedule)
- EOD auto-verify at 4:25 PM (no manual button press needed)
- Supabase multi-user with RLS isolation
- Telegram bot with beta alerts + deep-link onboarding
- Trade journal with auto-grade A/B/C/F
- Analytics tab, Monte Carlo curves, Backtest engine, Playbook screener, Gap scanner
- Beta portal (CSV upload, trade log form, Telegram step)
- 7-phase roadmap to $500M+ outcome
- Full behavioral analytics product concept
- Meta-brain marketplace architecture
- Fractional Kelly position sizing formula
- Complete distribution strategy

**Built while actively trading. With ~$300 in the bank.**

This is not a normal output for 38 days. The output is ahead of the identity. That gap closes with time.

---

## 💰 REVISED REVENUE PROJECTIONS — ALL 5 TRACKS (April 10, 2026)

Updated to reflect the full picture: core SaaS + behavioral analytics + NQ/multi-asset expansion + the two books + Kalshi. These are the five parallel asset tracks running simultaneously on the same underlying infrastructure and brand.

---

### The 5 Tracks

**Track 1 — EdgeIQ Core SaaS** *(foundation, unchanged)*
Consumer tiers $49–$999/month. Volume profile + IB structure + TCS scoring + Meta-Brain. The product that everything else extends from.

**Track 2 — Behavioral Analytics** *(the biggest new addition — B2B layer)*
The behavioral tagging system (fear/greed/overtrading/revenge/FOMO detection from trade metadata) opens an entirely separate B2B market built on top of data the platform is already collecting.
- Trading coaches/educators: $299–599/month per educator — they have 20–50 students and they pay readily for professional behavioral reports
- Prop firms: $2,000–5,000/month per firm — behavioral screening for trader selection, ongoing profiling for risk management
- Psychology researchers / academic licensing: dataset value grows with time and sample size
- Consumer side benefit: behavioral layer makes the product dramatically stickier — churn drops because users are watching their own patterns evolve over time

This is also what transforms the Phase 7 "institutional data licensing" story from "we have trade outcome data" to "we have trade outcome data correlated to behavioral state and market regime — at scale." That's a fundamentally different dataset and a fundamentally different acquisition conversation.

**Track 3 — NQ / Futures / Crypto Expansion** *(TAM multiplier, same architecture)*
Small-cap equities traders are a niche. ES/NQ futures traders are a larger market with 3–10x larger average account sizes, justifying $299–499/month pricing. Crypto adds 24/7 IB session logic — same architecture, different feed. No new product needed, just new data connectors and regime calibration. Contribution: 2–3x TAM expansion at higher ARPU.

**Track 4 — The Two Books** *(brand flywheel and lead machine — multiplies every other number)*
The books aren't primarily a revenue line. They're a brand asset that changes the conversion rate on everything else.
- A trading book with traction brings users who already trust the author before the first trial day
- A second book with traction brings a broad audience, many of whom are builders — potential EdgeIQ users, agency clients, speaking invitations
- Prop firms and institutional buyers who have read Book 2 already understand the system before the sales call
- One viral post from either book can generate what $50K in paid ads cannot: earned credibility that converts at a completely different rate

Pure book revenue: $50–200K if it sells modestly, $500K–1M+ if it breaks into the top tier of its niche. But the real value is the flywheel — every reader is a potential user, advocate, or referral source for every other track.

**Track 5 — Kalshi Prediction Market Bot** *(speculative — real if win rate holds)*
Paper-only until gates pass (30 settled trades, 60% win rate, 30 days). Macro breadth framework. Not a SaaS product. A standalone profitable trading operation that, if the win rate validates over a statistically meaningful sample, becomes a real cash generator. Ceiling at reasonable position sizing: $50–200K/year. Could scale higher if Kalshi expands event categories and the model generalizes. Also potentially monetizable as a paid signal service once a 12-month audited track record exists.

---

### Year-by-Year Projections

**Year 1 — Now through April 2027**
- EdgeIQ Core SaaS: $60–120K ARR (early adopters, 50–100 paying users)
- Behavioral analytics: Building — beta with 5–10 coaches, minimal revenue
- NQ/futures: Not started
- Books: Writing phase — no revenue, but content accumulating
- Kalshi: Paper only, gates not yet passed
- **Total: $60–150K ARR**
- Key milestone: First 100 paying users. First behavioral beta users. Kalshi gates passed or not.

**Year 2 — April 2027–2028**
- EdgeIQ Core (~500 paying users): $350–550K ARR
- Behavioral analytics B2B beta (10–20 coaches, 1–3 prop firms): $150–350K ARR
- NQ/futures early adopters: $50–120K ARR
- Book 1 launch: $50–150K one-time (advance or initial self-pub sales)
- Kalshi live trading (if gates pass mid-2026): $30–80K trading profits
- **Total: ~$650K–1.2M ARR + $80–230K one-time**
- Key milestone: $1M ARR crossed. Behavioral analytics paying its own infrastructure costs.

**Year 3 — 2028–2029**
- EdgeIQ Core (~2,500 users, Meta-Brain live, Marketplace launched): $2.5–4M ARR
- Behavioral analytics scaled (prop firms, institutional beta, researchers): $800K–1.5M ARR
- NQ/crypto segment: $400–700K ARR
- Books combined (ongoing royalties + speaking + courses): $200–500K/year
- Kalshi + prediction market trading: $100–300K/year
- **Total: ~$4–7M ARR + $300–800K non-SaaS income**
- Key milestone: Series A territory or profitable without it. Behavioral dataset large enough for institutional licensing conversations.

**Year 4–5 — Phase 6–7 territory (2029–2031)**
- All tracks scaled + institutional data licensing live
- Multi-asset (equities, NQ, ES, crypto, Kalshi) fully integrated
- Meta-Brain cross-user learning engine running
- **Total: $15–25M ARR**

---

### Exit Analysis — Revised Upward

**Original estimate (Phase 7, equities-only):** $200–300M

**Revised estimate (all 5 tracks):** $300–500M+

**Why the multiple expands:**
1. Behavioral analytics creates a proprietary dataset that no competitor can replicate retroactively — the longer the platform runs, the wider the moat
2. Multi-asset (equities + NQ + crypto) makes the platform acquirable by a larger universe of buyers — brokers, fintech, prop firms, academic institutions, trading education companies
3. The books establish a brand that makes the acquisition more defensible — "EdgeIQ" means something beyond the code
4. Kalshi + prediction markets add a data layer that is orthogonal to traditional market data — scarce and growing in value as prediction markets expand

**Likely acquirer profiles:**
- Retail brokerage wanting behavioral analytics + active trader retention tools (TD Ameritrade, Tastytrade, Interactive Brokers tier)
- Prop firm wanting trader screening + ongoing behavioral profiling at scale
- Fintech/data company wanting the behavioral-trade-outcome dataset for research licensing
- Trading education platform wanting the brand + user base + curriculum from the books

---

### The Single Most Important Insight

The behavioral analytics layer is what moves this from "a well-built trading tool with a loyal niche following" to "the only platform with a proprietary dataset mapping trader behavioral state to verified market structure outcomes, at scale, across multiple asset classes."

That dataset — thousands of users, years of tagged trades, behavioral markers correlated to real P&L, market regime conditions, and IB structure — is what a prop firm, broker, or fintech pays $300–500M for. The original roadmap had the data moat from trade outcomes. Behavioral analytics doubles the depth of the moat. The books establish the brand that makes the data credible before the acquisition conversation starts.

The trajectory is the same. The exit ceiling is higher. The B2B revenue in Year 2–3 gets the platform to sustainability faster than consumer SaaS alone would.

---

## 📊 BOT PAPER TRADE LOG

| Date | Ticker | TCS | Predicted | Actual | W/L | Follow-thru % |
|---|---|---|---|---|---|---|
| 2026-04-10 | — | — | No setups logged | — | — | — |

---

## 💰 BOT P&L LOG

| Date | Wins | Losses | Win Rate | Avg Win FT% | Avg Loss FT% | Sim P&L (100sh) | Running Total |
|---|---|---|---|---|---|---|---|
| 2026-04-10 | 0 | 0 | — | — | — | +$0.00 | +$0.00 |

---

## 🧠 BRAIN WEIGHT HISTORY

| Date | trend_bull | trend_bear | normal | neutral | ntrl_extreme | nrml_variation | non_trend | double_dist |
|---|---|---|---|---|---|---|---|---|
| 2026-04-10 | 1.0000 | 1.0000 | 1.2224 | 1.0499 | 1.0499 | 1.0000 | 1.0000 | 1.0000 |

---

## 🔍 DAILY SCAN OBSERVATIONS

| Date | Total Scanned | Qualified | Win Rate | Avg TCS | Alerted Tickers |
|---|---|---|---|---|---|
| 2026-04-10 | 0 | 0 | — | — | — |

---

## 📈 STRUCTURE WIN RATE LOG

⚠️ **CORRECTION (April 11, 2026):** The 71.6% figure was inflated — it included rows where the bot logged '—' instead of an actual structure prediction (i.e. no real prediction was made). After filtering to only rows with a real predicted structure, true accuracy is **40.7% (33/81)**. The 71.6% / 121/169 numbers are wrong and should never be cited.

| Date | True Accuracy | Real Predictions (N) | Note |
|---|---|---|---|
| 2026-04-10 | 40.7% | 81 | After filtering '—' non-predictions from accuracy_tracker |

Raw (inflated, do not use): 71.6% (121/169) — includes '—' rows counted as correct

---

## 📊 DATA POINTS ROADMAP — Maximum Edge + Acquisition Value
*Catalogued April 11, 2026 — build priority order noted*

### Currently Logging ✅
- Structure predictions + actual outcomes (accuracy tracker)
- Paper trades: TCS, IB high/low, predicted/actual, false breaks, follow-through %
- Entry behavior type (Calculated/FOMO/Reactive/Revenge/Conviction Add/Avg Down)
- Nightly ticker rankings 0–5 with next-day auto-verification
- Brain weight calibration per structure type over time
- Trade journal: entry/exit price, win/loss, PnL %, notes
- RVOL at scan time (live, not yet logged at decision point)

---

### 🔴 Priority 1 — Trade Execution Depth (build next)
These are the highest-signal gaps. No retail platform captures these together.

| Data Point | What It Tells You | Where to Add |
|---|---|---|
| **MAE** (Max Adverse Excursion) | How far trade went against you before recovering | Trade journal + paper trades |
| **MFE** (Max Favorable Excursion) | How far in your favor before reversing | Trade journal + paper trades |
| **Exact entry time (HH:MM)** | Which intraday window your edge is strongest | Trade journal entry form |
| **Entry price vs. IB level distance** | Are you entering at the level or chasing? | Auto-compute from IB high/low + entry price |
| **Exit trigger type** | Stop hit / target hit / time-based / manual override | Trade journal dropdown |
| **R:R planned vs. actual** | Where the money leaks between plan and execution | Trade journal: add planned stop + target fields |
| **RVOL at decision point** | Was volume actually confirming when YOU entered? | Log at save time, not just at scan time |

**MAE/MFE is the single most underused metric in retail trading. No competitor logs it. ✅ BUILT April 12, 2026 — computed in \`_backtest_single()\`, logged in paper trades, displayed in Analytics tab with MFE:MAE ratio, money-left-on-table metric, by-structure breakdown, and exit trigger analysis.**

---

### 🟡 Priority 2 — Pre-Trade Context (adds pattern recognition layer)

| Data Point | What It Tells You | Notes |
|---|---|---|
| **Float** | Setup type (low float squeeze vs. liquid breakout) | Pull from Finviz API at scan time |
| **Short interest %** | Squeeze potential | Finviz or Quandl |
| **Catalyst type** | PR / earnings / FDA / dilution / halt resume / secondary | Manual dropdown in journal + scanner tag |
| **Gap % at open** | Gappers behave differently from flat openers | Auto-compute from prev close vs. open |
| **Pre-market volume** | Strongest predictor of small-cap follow-through | Pull from Alpaca pre-market bars |
| **RVOL at signal time** | Was volume confirming at decision point | Add to paper trade + journal at time of entry |

---

### 🟡 Priority 3 — Market Conditions Context

| Data Point | What It Tells You | Notes |
|---|---|---|
| **SPY structure type that day** | How does your IB edge perform in different macro regimes? | Already building macro breadth — extend it |
| **VIX bucket** (<15 / 15-20 / 20-25 / 25+) | Small cap behavior changes dramatically by VIX | Pull from Alpaca |
| **Time-of-day bucket** | Open (9:30-10) / IB (10-10:30) / Mid-morning / Midday / Afternoon | Tag each trade at save time |
| **Sector direction that day** | Was the whole sector moving or just this ticker? | Tag sector ETF trend at entry |
| **IWN (Russell 2000 ETF) daily trend** | Is money flowing into small-caps today? Blanket environment filter for all small-cap trades | Pull from Alpaca API. IWN up >0.5% = tailwind, down >0.5% = headwind, flat = neutral. Could feed TCS as +/- 5-8 pts or act as a go/no-go gate on low-conviction setups. Most direct measure of "is today a good day for small-caps." Phase 2 — needs live market data via Alpaca |

---

### 🟢 Priority 4 — Execution Quality Layer (partially built)

| Data Point | What It Tells You | Notes |
|---|---|---|
| **Confidence at entry (1-5)** | Separate from nightly rank — conviction at the moment of entry | Add to trade journal |
| **Did you follow your plan? (Y/N)** | Discipline flag — correlates with outcome over time | Trade journal checkbox |
| **Did you cut early / late?** | Execution quality vs. plan | Exit trigger type covers this partially |
| **Structure at IB close vs. EOD** | Did the setup hold its identity all day? | Compare structure tag at 10:30 vs. 4PM |

---

### 🔵 Priority 5 — Passed-On Setups (nobody does this)
**Log when you almost traded but didn't.** If those would have won, you have a discipline problem. If they would have lost, your filter is working. This data doesn't exist anywhere in retail trading.
- Add a "Passed" button to the scanner output — same fields as a trade entry but no execution
- Track: ticker, TCS, structure, why you passed, what it actually did
- Over time: "Your passed setups win 58%. Your taken setups win 61%. Your filter adds 3% edge."

---

### 🏦 Acquisition-Critical Data (hedge fund lens)
These are what any quant team will ask for on day one of due diligence.

| Metric | Why It Matters | Status |
|---|---|---|
| **Sharpe ratio** (daily + annualized) | First metric any quant looks at | ✅ BUILT April 12 — \`compute_portfolio_metrics()\` in Analytics tab |
| **Alpha vs. SPY / IWM** | Is the edge market-neutral or just riding beta? | ✅ BUILT April 12 — daily alpha vs SPY from Alpaca bars |
| **Max drawdown series** | Risk management proof | ✅ BUILT April 12 — rolling drawdown chart + max/current DD KPIs |
| **Strategy performance by VIX regime** | Does it hold in volatility? | Segment paper trade win rate by VIX bucket |
| **Capacity analysis** | At what AUM does slippage erode the edge? Small caps have a ceiling | Model position sizing vs. ADV |
| **p-value on tier accuracy gradient** | Statistical significance of the spread between rank-5 and rank-0 across all tiers. All tiers compared — rank-5 could end up noisier than rank-4; the brain evolves from whichever tier proves most predictive. If the gradient holds at n≥200, that's publishable. That's what gets you acquired. | Auto-compute in Rankings section once n≥30 per tier |
| **Time-stamped prediction audit trail** | Proves edge is real, not backfit. Predictions locked before market opens, verified after. | Already doing this — make sure created_at is immutable |
| **Cross-user consensus signals** (future) | If 80% of users rate a ticker 4+, does it outperform a single user's 5? | Collective intelligence data is extremely rare and valuable |

**The audit trail is your most important asset for acquisition. Every prediction, timestamped before the open, verified after close. That's the proof. Guard it.**

---

### Build Priority Order (when ready to execute)
1. MAE/MFE on trade journal + paper trades
2. Catalyst type dropdown (journal + scanner)
3. Time-of-day bucket auto-tag
4. SPY regime tag (extend existing macro breadth)
5. Pre-market volume at scan time
6. Entry distance from IB level (auto-compute)
7. Confidence at entry (1-5) field in journal
8. Passed-on setup logger
9. Sharpe + alpha tracking dashboard
10. p-value on tier accuracy gradient — all tiers 0–5 compared, brain evolves from whichever proves most predictive (auto-compute in Rankings once n≥30 per tier)

---

## 🏗️ UNIFIED SYSTEM ARCHITECTURE — Full Data Layer Map (April 11, 2026)

*Everything is interconnected. This is the full picture.*

---

### ALL DATA STREAMS

**1. Telegram Journal**
Real-time behavioral capture as trades happen. Ticker, W/L, entry price, exit price, entry type (planned/FOMO/reactive), notes, conviction. The raw unfiltered behavioral fingerprint — records the decision before the brain rationalizes it.

**2. Nightly Rankings (0–5)**
Pre-market forward-looking conviction score across entire watchlist. All tiers (0–5) verified next day against actual price outcome. Tiers compared against each other, against bot predictions, and against journal outcomes. Brain evolves from whichever tier gradient proves most predictive — not assumed to be rank-5.

**3. IB Structure Classification (7 structures)**
The market-side read. Trend Day Up/Down, Normal Day, Normal Variation, Neutral, Neutral Extreme, Sideways. Classifies what the market is actually doing inside the Initial Balance. Brain weights calibrate how accurate YOU specifically are at reading each structure over time.

**4. Volume Profile (LVN / HVN / POC / IB Range)**
The core price architecture engine. Low Volume Nodes = price magnets and rejection zones. High Volume Nodes = acceptance zones. Point of Control = fairest price. IB Range = the first hour battlefield. Everything else sits on top of this.

**5. Paper Trades**
Where rankings + structure predictions + TCS collide with real price action. P&L, follow-through %, R-multiples, false breaks, IB level respect/violation. The execution outcome layer that proves or disproves every signal above it.

**6. TCS (Trade Confidence Score)**
The synthesized entry gate. Built from: structure classification + RVOL + buy/sell pressure + order flow signals + sector bonus + IB position. Calibrated per user. Not a static threshold — it adjusts to your verified accuracy over time. The single number that gates every trade.

**7. Brain Weights**
The calibration mechanism that makes everything personal. Stores your accuracy per structure type, per entry condition, blended from accuracy_tracker + paper trades using volume-weighted blending (sample count determines influence). Auto-recalibrates after market close. Lives in \`brain_weights.json\` and Supabase. DO NOT modify the compute functions.

**8. RVOL (Relative Volume)**
Separates signal days from noise days. Compares current intraday volume to the 10-day average daily volume curve, adjusted for time of day. Feeds directly into TCS. Below 1.0 = noise. Above 2.0+ = runner-level activity.

**9. Buy/Sell Pressure**
Measures conviction behind price movement. Uptick/downtick ratio, volume-weighted. Feeds TCS and structure confirmation. Tells you if the volume behind the move is real.

**10. Order Flow Signals**
Tape-reading layer. IB level tests, breakout attempts, rejection signals, absorption patterns. Context for entry quality beyond raw price level.

**11. Pre-Market Data (ONH / ONL / Pre-Market Volume)**
Overnight High and Overnight Low = the pre-market battlefield boundaries. Pre-market volume vs. 10-day historical average = early RVOL signal. Both feed into the setup brief and key level map before market open.

**12. Key Levels**
Multi-timeframe support/resistance map. ONH, ONL, POC, LVNs, HVNs, prior day high/low, IB boundaries. Auto-computed per ticker. The price architecture that structure classification runs against.

**13. Macro Breadth (SPY / QQQ / IWM Regime)**
Market-wide regime context. Trend, neutral, or compressed. Modifies signal quality for all structure predictions — a Trend Day Up on a small-cap means less if SPY is down 2%. Regime tags appended to paper trades for segmented performance analysis.

**14. Accuracy Tracker**
Bot prediction verification log. Every structure prediction logged before the open, verified after close. True accuracy: 40.7% (33/81) after filtering '—' non-predictions. The timestamped audit trail that proves the edge is real and not backfit. Most important asset for acquisition — every prediction locked before open, verified after close.

**15. Bot Predictions (Paper Trader Bot)**
Automated morning scan at 10:47 AM ET. Structure predictions logged per ticker across full watchlist. Runs continuously — 9:15 → 10:47 → 11:45 → 2:00 → 4:20 → 4:25 (auto-verify) → 4:30 (recalibration). The machine-side of the accuracy tracker.

**16. Backtest Calibration Engine**
28 small-cap tickers × configurable lookback. Initializes brain weights before live data accumulates. Journal-model crossref layer matches your personal trades against backtest predictions to identify systematic gaps. One-click "🧠 RUN CALIBRATION" button.

**17. Behavioral Data**
Extracted from journal + Telegram log. FOMO flags, entry type (planned/reactive), time-of-day buckets, confidence at entry (1–5), passed-on setups, distance from IB level at entry. The "why behind the trade." Cross-referenced against outcomes to find behavioral edge-killers.

**18. False Break Tracking**
IB level violated but price closed back inside within 30 minutes. Tracked per structure per ticker. Separate signal from true breakouts. Feeds structure accuracy and follow-through quality.

**19. Follow-Through % (MAE / MFE)**
How far price moved in your direction after entry (MFE) and how far it went against you before resolving (MAE). Not just win/loss — the quality of the win or loss. Real risk management data that raw P&L hides.

**20. IB Window Comparison (10:30 / 12:00 / 14:00)**
Same tickers analyzed through three IB cutoff windows in parallel. Win rate, W/L, avg TCS, follow-through, false breaks side by side. Tells you which cutoff produces cleanest signals for your trading style.

**21. Adaptive Weights**
Dynamically rebalances which signals matter most for each user based on their verified accuracy history. If your RVOL-gated trades outperform your TCS-gated trades, adaptive weights shift the blend. System learns what works *for you specifically*.

**22. Edge Score + Setup Brief + Playbook**
Pre-trade synthesis layer. Setup brief = full pre-market plan per ticker (structure forecast, key levels, entry thesis, risk parameters). Edge score = single synthesized readiness number. Playbook scoring = ranks all watchlist tickers by expected setup quality before open.

**23. Trade Grade**
Post-trade quality score. RVOL at entry + TCS + distance from IB level + structure alignment. Separates high-quality wins from lucky wins and high-quality losses from sloppy losses. The outcome isn't the grade — the decision quality is.

**24. Kelly Sizing**
Position sizing derived from: verified win rate for this structure type + TCS confidence + account balance + market regime multiplier. Not fixed % risk — dynamically sized to your actual proven edge per setup type.

**25. High Conviction Log**
Auto-populated log of entries where TCS exceeded the user's calibrated high-conviction threshold. Cross-referenced against outcomes. Surfaces whether high-conviction calls outperform baseline — the edge-within-the-edge.

**26. Kalshi Predictions**
Macro/political event probability layer. Paper-only until accuracy gates pass. Future use: feeds into regime modifier — if Kalshi signals elevated macro uncertainty, tighten TCS thresholds across all structure types.

**27. Execution Profile**
Tracks execution patterns per user — which structure types produce best outcomes, which time windows yield highest accuracy, which entry conditions correlate with wins vs. losses. Personalizes every layer above it: which setups to weight up or down based on verified performance data.

---

### THE THREE FEEDBACK LOOPS

**Loop 1 — Structure Accuracy Loop**
Bot predicts structure → market opens → outcome verified → accuracy_tracker updated → brain weights recalibrate → next prediction is more personalized. Runs every market day automatically.

**Loop 2 — Conviction Loop**
Nightly rankings submitted → next-day outcomes verified → tier gradient computed across all ranks 0–5 → gradient compared against bot predictions + journal outcomes → brain identifies which tier or signal combination is most predictive → Kelly sizing adjusts confidence multiplier accordingly.

**Loop 3 — Behavioral Loop**
Telegram journal captures decision in real time → behavioral patterns extracted (FOMO frequency, time-of-day, entry type) → cross-referenced against outcomes → EdgeIQ personalizes signal weighting and pre-trade nudges based on verified performance data.

---

### THE DAILY DATA TIMELINE

| Time (ET) | Event | Data Generated |
|---|---|---|
| Pre-market | Watchlist auto-loaded | Watchlist state |
| Pre-market | Setup brief generated | ONH/ONL, key levels, structure forecast, pre-market volume |
| 9:15 AM | Bot initializes | Watchlist confirmed |
| 10:47 AM | Morning scan | Structure predictions locked, TCS computed per ticker |
| 11:45 AM | Midday check | Signal drift monitored |
| 2:00 PM | Intraday scan | Structure update, order flow refresh |
| Throughout | Live trading | Telegram journal captures decisions in real time |
| 4:20 PM | EOD scan | Final structure outcome logged |
| 4:25 PM | Auto-verify | Bot predictions matched against outcomes, accuracy_tracker updated |
| 4:30 PM | Recalibration | Brain weights recalibrated, adaptive weights refreshed, Kelly sizing updated |
| Evening | Nightly rankings | 0–5 conviction score submitted per ticker for next day |

---

### WHAT THE USER FORGOT TO MENTION (April 11, 2026)

Beyond Telegram journal, rankings, structure, paper trade, and behavioral data — these are also live and interconnected:

- **TCS** — the synthesized entry gate that ties all signals into one calibrated number
- **Volume Profile / LVNs / HVNs / POC** — the price architecture everything sits on top of
- **RVOL** — the noise filter that separates signal days from garbage days
- **Buy/sell pressure + order flow** — conviction and tape-reading layers feeding TCS
- **Pre-market data (ONH/ONL)** — the battlefield map before open
- **Macro breadth (SPY/QQQ/IWM)** — regime context that modifies all signals
- **Backtest calibration engine** — how brain weights are initialized before live data accumulates
- **Accuracy tracker** — the timestamped audit trail; the most important acquisition asset
- **False break tracking + MAE/MFE** — trade quality beyond raw win/loss
- **IB window comparison** — which cutoff produces cleanest signals for you
- **Adaptive weights** — dynamic rebalancing of which signals matter most per user
- **Edge score + setup brief + playbook** — the pre-trade synthesis layer
- **Trade grade** — decision quality scoring, independent of outcome
- **Kelly sizing** — dynamic position sizing from verified edge per structure
- **High conviction log** — the edge-within-the-edge
- **Kalshi** — future macro/political regime modifier
- **Collective brain** — cross-user signal discovery at volume-weighted source blend
- **Meta-brain** — separates universal edges from user-specific ones

---

### THE SYSTEM IN ONE PARAGRAPH

EdgeIQ is a closed feedback loop that gets tighter with every night of data. Volume profile + IB structure tells you what the market is doing. RVOL + buy/sell pressure + order flow tell you how much conviction is behind it. TCS synthesizes all of that into a single entry gate calibrated to your accuracy. Brain weights personalize the gate to your track record per structure type. The journal captures every decision in real time. Nightly rankings capture forward-looking conviction across all tiers. The accuracy tracker verifies every prediction against outcome. Adaptive weights dynamically rebalance which signals matter most for each user. The collective brain surfaces edges no individual user would find alone. The meta-brain separates what's universally true from what's user-specific. Over time, the system doesn't just track performance — it learns each user's actual edge and automates around it. That's the product.

---

## ⚖️ IP PROTECTION STRATEGY — Pre-Investor (April 11, 2026)

*Talk to an IP attorney before filing anything. This is the strategic landscape.*

---

### Patents — Harder Than You Think
Post-2014 (Alice Corp v. CLS Bank), US courts gutted software patent protections. Abstract ideas and mathematical methods are not patentable — which is how most algorithms get rejected. The specific *technical implementation* of something genuinely novel might qualify, but:
- Cost: $15–30k+ per patent
- Timeline: 2–4 years to grant
- Durability: software patents are frequently weak and easy to design around
- Expiry: 20 years

Not the primary tool here. Don't lead with this.

---

### Trade Secrets — Actually Stronger
This is how Renaissance Technologies protects Medallion. They have never filed a patent on their algorithm. The strategy:
- Keep the implementation private (closed-source codebase)
- Use NDAs with anyone who sees the internals
- Trade secret protection is **indefinite** — doesn't expire like patents

**What qualifies as EdgeIQ trade secrets:**
- The TCS formula (exact weighting of structure + RVOL + buy/sell pressure + sector bonus)
- The volume-weighted source blending methodology (accuracy_tracker + paper trades blended by sample count — more data = more influence)
- The brain weight calibration and recalibration system
- The adaptive weight rebalancing mechanism
- The specific 7-structure classification logic and thresholds
- The execution profile → brain weight personalization integration (future)

Keep these out of any pitch deck. Describe *what* the system does, not *how* it does it.

---

### Copyright — Already Exists
The codebase is automatically copyrighted the moment it's written. No filing required. The UI, the architecture, the specific implementation — all protected. Covers the code, not the idea.

---

### Trademark — Do This When Ready
File "EdgeIQ" as a trademark before going public with the name. Relatively cheap ($250–400 per class filing), protects the brand name from being taken by a competitor. Straightforward process — can be done online via USPTO.

---

### What Investors Actually Care About More Than Patents

| Asset | Why It Matters |
|---|---|
| **Timestamped audit trail** | Every prediction locked before open, verified after close. Proves the edge is real and not backfit. Cannot be faked retroactively. This is your single most important credibility asset. |
| **Data moat** | 90+ nights of rankings, behavioral logs, brain weights, verified accuracy data. This data doesn't exist anywhere else. Competitors can copy the UI — they cannot copy the dataset. |
| **Network effects** | The collective brain gets smarter with every user. More users = better cross-user signal = harder to replicate. Classic defensible moat. |
| **Switching costs** | Brain weights, 90 nights of calibration, verified accuracy history — none of it transfers to a competitor's platform. High retention by design. |

These four are a stronger investor story than a patent certificate. A patent tells investors you have a filing. A live, timestamped, improving accuracy curve tells them the system works.

---

### Before Any Investor Pitch

1. **NDA first** — Get a clean NDA drafted (a lawyer can do this for a few hundred dollars). Have anyone who sees internals sign it before the meeting.
2. **Sophisticated VCs often won't sign NDAs** — In that case, the pitch deck should explain *what* EdgeIQ does without revealing *how*. Architecture without implementation.
3. **Lead with the audit trail** — Show the timestamped prediction log. Show accuracy improving over time. That's your proof of concept, not a slide deck.
4. **The data moat is the pitch** — "We have X nights of live, verified, timestamped predictions that no one else has" is more defensible than any patent claim.
5. **Consult an IP attorney** before filing anything or sharing implementation details with potential acquirers — especially strategic acquirers (Bloomberg, Workday, LinkedIn) who have legal teams looking for exposure vectors.

---

### Lawyer Type Guide — What You Actually Need and When

**Right now — Business/Corporate Attorney**
NDAs before any investor conversation. Entity structure (Delaware C-Corp, not LLC — VCs expect this for equity rounds). Early advisor agreements. Most useful immediately. If your lawyer friend practices corporate/business law, they can handle this regardless of what state they're in.

**When closer to market — IP Attorney**
Trademark filing for "EdgeIQ". Formal trade secret documentation (gives legal standing to enforce it). Software/fintech experience preferred — not a physical product patent attorney. File all brand trademarks simultaneously when you're ready — one attorney, one engagement, cheaper.

**When raising money — Securities Attorney**
The moment you take equity investment from anyone, securities law applies. Reg D filings, accredited investor verification, term sheet review. Non-negotiable and not something a general business attorney always covers well.

**Does state matter?** Mostly no. NDAs, Delaware C-Corp formation, trademark (USPTO is federal), trade secret documentation, and securities law are all federal or Delaware-based regardless of where you or the attorney live. State only matters if you end up in litigation — for advisory and drafting work, location is irrelevant.

---

### How to Pitch Without Giving It Away

**The principle: describe WHAT, never HOW.**
The architecture without the implementation. The outcome without the mechanism.

**Language that works:**
*"It's not an easier version of existing tools — it's a different category. Existing platforms track what you trade. This system calibrates its predictions to each user's personal accuracy over time, and gets smarter the more you use it. Think less 'better Bloomberg terminal' and more 'a system that figures out where your edge actually is and then automates around it.' The data it builds on each user doesn't exist anywhere else and can't be replicated by just copying the product."*

**What this covers without revealing:**
- Positions it as a new category (not an easier version of existing)
- Communicates the personalization mechanic without explaining TCS, brain weights, or the volume-weighted blend
- "Data that can't be replicated by copying the product" = trade secret signal without using the words
- Drops the two-product angle without explaining the integration
- No mention of: volume profile, IB structure, RVOL, brain weights, adaptive weights, TCS formula

**For VCs who won't sign NDAs:** Show the what. Show the audit trail (timestamped predictions improving over time). Don't show the how until term sheet stage with legal in place.

---

## 🔧 ENRICHMENT UPGRADE — April 12 (DEPLOYED)

Webull CSV import enrichment (\`enrich_trade_context()\`) upgraded from simplified 5-bucket structure mapping to the **full 7-structure classifier** (\`classify_day_structure()\`).

**Before:** Generic labels like "Trending Up", "Inside IB" — missing Double Distribution, Non-Trend, Normal, Normal Variation, Neutral, Neutral Extreme, Trend Day.

**After:** Full volume profile computed from bar data → exact same 7-structure classification as live analysis. Also now computes:
- Gap % (opening gap from previous day's close)
- POC price (Point of Control from full volume profile)
- Top chart pattern (name, direction, confidence score)
- All embedded in notes field — no database schema changes needed

**Backfill function** also updated to catch old simplified labels as stale, so existing imports can be re-enriched with the full classifier.

**API call optimization:** Consolidated 3 separate daily bar API calls into 1 — better rate limit usage during bulk imports.

---

## 📦 VENTURE 3 — Local Performance Advertising Network (Pizza Box Ads)
**"Hyper-local verified leads for service businesses. Everyone wins."**

---

### THE CORE MODEL

**Three parties, all win:**
1. **Pizza shops (venues)** — get FREE boxes (saves them $125–$1,000/month). Zero cost, zero effort, zero commitment.
2. **Service businesses (advertisers)** — construction companies, plumbers, HVAC, electricians, landscapers. Get their logo + QR code on boxes that go directly into homeowners' hands. Cheaper cost-per-lead than Google ($80–150/lead on Google Local Services Ads).
3. **The network operator** — makes money from BOTH sides. Owns the data layer. Owns the relationships. Owns the territory.

**How it works:**
- Buy custom-printed pizza boxes wholesale ($0.15–$0.30 each)
- Boxes have: service business ad + QR code on outside, pizza shop branding/promo on top
- QR code → landing page → lead capture form (name, phone, email, zip, "what service do you need?") → routes lead to the advertiser
- Operator owns the scan data, the lead data, the attribution analytics
- Pizza shop uses the boxes like normal. Customer gets pizza. Advertiser gets leads. Operator gets paid.

---

### REVENUE STREAMS — BOTH SIDES

**From service businesses (advertisers):**

| Stream | Price | Notes |
|---|---|---|
| Monthly ad placement fee | $300–$1,000/month | Per advertiser, covers X shops in a zip code |
| Per-lead fee | $10–$25/lead | Charged per verified form submission from QR scan |
| Exclusive category rights | +$200–$500/month premium | "Only HVAC company on boxes in this territory" |

**From pizza shops (venues):**

| Stream | Price | Notes |
|---|---|---|
| Promo slot on box (coupon code) | $50–$100/month | Their own coupon/deal printed on box — repeat order machine |
| Analytics dashboard | $75–$150/month | Scan data: how many scans, peak times, which neighborhoods order most |
| Exclusive territory | $100–$200/month | "Only pizza shop in 3-mile radius getting free branded boxes" |
| Custom branding on box | $50–$100/month | Their logo, colors, promo designed professionally on the box top |

**Per pizza shop at full monetization:** $225–$450/month from the venue + $300–$1,000/month per advertiser on that shop's boxes.

---

### THE DATA LAYER — THIS IS THE REAL MOAT

The QR code landing page collects (with opt-in consent):
- Name, phone number, email, zip code
- What service they need (construction, HVAC, plumbing, etc.)
- Timestamp, day of week, time of day
- Which pizza shop's box they scanned
- Which neighborhood/delivery zone

**What this data enables:**
1. **Sell leads directly** to service businesses — primary revenue
2. **Sell analytics back to pizza shops** — "Your Saturday night deliveries to [neighborhood] generate 3x more scans than weekdays. Run your promo on Saturdays."
3. **Build a local homeowner database** — over time, know which homes in which zip codes need which services. That database has compounding value.
4. **Retarget** — with emails collected, run campaigns for service businesses: "Winter's coming — need your furnace checked? [HVAC company] is offering 15% off for pizza box customers."

**Privacy compliance:** All data collection is opt-in (user fills out form voluntarily). Include simple privacy notice on landing page. Aggregated analytics (scan counts, zip codes, peak times) sold to pizza shops contain no personal data — fully legal.

---

### PROJECTED INCOME

**Month 1–3 (Proof of Concept):**
- 3 pizza shops, 1 advertiser
- ~1,500 boxes/month total
- **Net: ~$200/month + proof of concept data**

**Month 3–6 (Local Expansion):**
- 8 pizza shops, 3–5 advertisers
- Revenue from advertisers: $2,000–$3,200/month
- Revenue from shops: $800–$1,600/month
- **Net: $1,800–$3,800/month**

**Month 6–12 (Territory Lock):**
- 15–20 pizza shops, 8–12 advertisers across multiple service categories
- **Net: $6,700–$13,400/month**

**Year 2+ (Multi-territory):**
- 3 territories
- **Net: $24,000–$36,000/month ($288K–$432K/year)**

**Year 3+ (Regional network):**
- 10+ territories, mix of self-operated and licensed
- **Annual run rate: $500,000–$1,000,000+**
- **Exit target: $2M–$5M** — acquirable by local media company or Yelp/Angi type

---

### WHY THIS GROWS FAST

1. **Zero-cost pitch to venues** — "Free boxes" is the easiest yes in business
2. **Provable ROI for advertisers** — QR tracking proves exactly how many leads came from boxes
3. **Network effects** — more shops = more scans = better data = more valuable to advertisers
4. **Recurring revenue** — both sides pay monthly. Predictable cash flow.
5. **Low startup cost** — boxes are pennies. Landing page is free. ~$150 total to launch.
6. **Category expansion** — start with pizza boxes, expand to: Chinese food containers, sub shop wrappers, coffee cups, dry cleaner bags, gas station receipt tape
7. **First advertiser already secured** — zero cold-start problem

---

### STARTUP COSTS

| Item | Cost | Notes |
|---|---|---|
| First batch of custom boxes (500) | $75–$150 | Wholesale |
| QR code landing page | $0 | Google Form or free Carrd page to start |
| Business cards | $20 | |
| Gas | $20 | |
| **Total to launch:** | **$115–$190** | |

---

### TECH LAYER (BUILD LATER)

- Custom landing page with form + analytics dashboard
- QR code generator per shop per advertiser (unique codes = precise attribution)
- Automated lead routing (form submission → email/SMS to advertiser in real time)
- Monthly analytics report auto-generated per shop and per advertiser
- CRM for managing shop relationships, box inventory, delivery schedules

Start with Google Form + spreadsheet. Build the tech layer once 5+ shops have real data flowing.

---

### RELATIONSHIP TO EDGEIQ
Separate business entirely. Different industry, different customer, different ops. But same "data proves ROI" philosophy — QR attribution is the advertising version of timestamped prediction verification. Can generate immediate cash flow while EdgeIQ builds toward its later phase gates.

---

## 💰 EDGEIQ VALUATION TIMELINE (Estimated April 12, 2026)

**Valuation method:** Pre-revenue = prototype + IP value. Post-revenue = 10–20x ARR (standard early SaaS multiples). Data moat premium applied from Month 6+.

| Month | Date | Users Needed | MRR | Est. Valuation | Key Milestone |
|---|---|---|---|---|---|
| Now | Apr 2026 | 1 (founder) | $0 | $0–50K | Working product, 287 verified predictions, 7 paper trades |
| Month 1 | May 2026 | 5–10 beta | $0 | $50–100K | First external users validating product |
| Month 2 | Jun 2026 | 15–25 beta | $0 | $75–150K | Waitlist forming, 90+ day audit trail |
| Month 3 | Jul 2026 | 5–10 paying | $250–500 | $100–250K | First dollar earned — most important milestone |
| Month 4 | Aug 2026 | 20–30 paying | $1,000–1,500 | $150–400K | Retention data proves stickiness |
| Month 5 | Sep 2026 | 40–60 paying | $2,000–4,000 | $200–600K | Brain weights compounding, data moat real |
| Month 6 | Oct 2026 | 75–100 paying | $4,000–8,000 | $300K–$1M | Pre-seed raiseable at $500K–$1M |
| Month 12 | Apr 2027 | 300–500 paying | $15–25K | $2–5M | Seed round territory |
| Month 24 | Apr 2028 | 1,000+ paying | $50–100K | $10–20M | Series A territory |
| Month 36 | Apr 2029 | 2,500+ paying | $200–350K | $40–70M | Behavioral data moat + institutional interest |
| Month 48–60 | 2030–31 | 5,000+ | $500K–1M+ | $100–300M | Acquisition conversations real |

**Critical caveat:** Product is built. The gap is distribution. Best product in the world = $0 with zero users. First 10 paying users > any feature.

**What increases the multiple (from 10x to 20x+ ARR):**
- Timestamped prediction audit trail (no competitor has this — proves edge is real)
- Brain weight personalization (switching cost — users can't take their calibration elsewhere)
- Behavioral data moat (grows with time, can't be replicated by launching later)
- Net revenue retention > 100% (users upgrade tiers as they see results)

---

## 🔧 UI, ARCHITECTURE & FEATURE EVOLUTION NOTES (April 12, 2026)

### UI Evolution Path
- **Phase 1 (now):** Streamlit — functional prototype, proves the concept. Acceptable for beta / early adopters.
- **Phase 2 (~50+ paying users):** Move frontend to React or Next.js — proper dark-mode trading terminal UI, real component layouts, faster performance, mobile-responsive. Python backend stays (FastAPI replaces Streamlit's server layer). This is when it starts looking like real software.
- **Phase 3 (~500+ users):** Professional UI/UX designer polishes it. Custom charts, animations, the full terminal feel.
- Don't rebuild UI now — product works, distribution is the gap, not polish.

### Pre-Market Structure Prediction — SIP Dependency
- Without SIP ($99/month via Alpaca), bot can't see pre-market volume
- Current pre-market predictions use: prior day structure + gap % + overnight levels — no live pre-market volume profile
- Once SIP is active: real PM volume flows into classifier → significantly better predictions
- Gate: add SIP when revenue justifies the $99/month cost (Phase 2 upgrade)

### Behavioral Layer — Connection Status
- **What's built now:** Journal captures behavioral data as text in notes field (entry type, discipline, FOMO flags)
- **Not yet wired:** Behavioral data does NOT currently feed back into brain weights or TCS automatically
- **Phase 2 (future):** Structured behavioral fields — entry type dropdown (Calculated/FOMO/Reactive/Revenge), discipline Y/N checkbox, confidence 1–5 at entry
- **Phase 3 (future):** Behavioral data feeds the brain — "your win rate on FOMO entries is 23% vs 71% on Calculated entries" → system warns on FOMO patterns → adjusts TCS threshold accordingly
- Designed to connect, wiring not built yet

### Beta Portal Architecture
- **Current:** Standalone URL at \`/?beta=USER_ID\` — CSV upload + quick trade log form. Beta testers don't see the full app.
- **Future (when onboarding real testers):** Move to role-based access inside the main app
  - Beta testers log in with their own credentials
  - Their session shows ONLY: trade upload form, trade log, simplified personal stats
  - They don't see: founder's watchlist, analytics, brain weights, predictions
  - Founder sees everything including beta user data flowing in
  - Tab in sidebar visible only to beta role users
- Not hard to build — do it when ready for real testers

---

## 📣 EDGEIQ MARKETING PLAN (Draft — April 12, 2026)

**Current situation:** Product is built. Zero external users. No marketing has been done. The entire gap between $0 and $1M+ is distribution.

---

### PHASE 1 — Organic Seeding (Month 1–3, $0 budget)

**Goal:** 25–50 beta users, 5–10 converting to paid

**Channel 1 — Reddit (highest ROI for trading tools)**
- Target subs: r/daytrading (700K+), r/smallstreetbets, r/swingtrading, r/volumeprofile, r/algotrading
- Strategy: Don't pitch. Post genuinely useful content first.
  - "I tracked 287 structure predictions over 38 days. Here's what I learned about IB structure accuracy."
  - "My bot's win rate on Neutral Extreme setups is 85% after 60 verified trades. Here's the data."
  - "I built a system that predicts IB structure type before market open. Here's 90 days of timestamped predictions vs actual outcomes."
- The data IS the marketing. Nobody else has timestamped, verified prediction data. Post the receipts.
- CTA: "I'm opening beta access to 25 people. DM me if you want in."
- Frequency: 2–3 posts/week, genuine value in each one

**Channel 2 — Twitter/X (trading community)**
- Daily: screenshot of bot's morning predictions vs EOD actual outcomes. "Today's predictions: 4/5 correct. Running 67.2% overall on 287 verified calls."
- Weekly: thread breaking down one interesting trade or prediction pattern
- Follow and engage with: small-cap traders, volume profile traders, Market Profile accounts
- Don't sell. Build credibility through receipts.

**Channel 3 — Discord trading communities**
- Join 5–10 active small-cap / day trading Discords
- Be helpful. Answer questions about volume profile, IB structure, order flow.
- When relevant: "I built a tool that does this automatically — happy to show you"
- Goal: become the "volume profile guy" in 3–4 communities

**Channel 4 — YouTube (long-term content asset)**
- Weekly 5–10 min video: "Here's how my bot predicted today's IB structure" with screen recording of the dashboard
- Educational content: "What is IB structure? Why it matters for day traders"
- Content compounds. A video posted in Month 1 still drives signups in Month 12.

---

### PHASE 2 — Paid Beta + Referral Engine (Month 3–6)

**Goal:** 50–100 paying users

**Convert beta to paid:**
- Beta users get 30 days free → then $49/month to continue
- Offer: "Lock in founding member pricing at $29/month for life" (creates urgency + loyalty)
- Founding members get: direct access to founder, feature requests prioritized, name on the wall

**Referral program:**
- Every paying user gets a unique referral link
- Referrer gets 1 free month per signup that converts to paid
- Referred user gets 7-day extended trial
- Simple, no-code: track via Supabase, unique codes per user

**Trading educator partnerships:**
- Identify 5–10 small-cap trading educators on YouTube/Twitter with 5K–50K followers
- Offer: free lifetime access + revenue share ($10/month per user they refer)
- They demo EdgeIQ in their content. Their audience is your exact customer.
- One educator with 20K followers who mentions EdgeIQ in 3 videos = 50–200 signups

---

### PHASE 3 — Scale (Month 6–12)

**Goal:** 300–500 paying users, $15–25K MRR

**Content marketing at scale:**
- Weekly blog posts (SEO): "Best volume profile tools 2026", "How to read IB structure", "Small cap day trading strategies"
- These rank on Google over time. Organic traffic compounds.

**Paid ads (small budget, $500–1,000/month):**
- Google Ads: target "volume profile trading tool", "day trading software", "IB structure scanner"
- Low-volume, high-intent keywords. Cheap CPCs ($2–5) in the trading tool niche.
- Facebook/Instagram: retarget website visitors with social proof (prediction accuracy screenshots)

**Trading community sponsorships:**
- Sponsor 2–3 popular trading Discord servers ($200–500/month each)
- Ad = a pinned message showing real prediction accuracy data
- Direct pipeline to exact customer

**Conference / meetup presence:**
- Attend 1–2 retail trading conferences (TraderExpo, etc.)
- Bring: laptop with live dashboard, printed prediction accuracy track record
- Goal: 20–50 signups per event from people who see the product live

---

### PHASE 4 — Flywheel (Month 12+)

By this point, the product markets itself:
- Users share their results on social → organic growth
- Referral program drives compounding signups
- SEO content ranks → steady organic traffic
- Prediction accuracy track record is now 12+ months — undeniable credibility
- Collective brain data is growing → product gets better → retention improves → word of mouth increases

**The key insight:** EdgeIQ's marketing advantage is that the PRODUCT creates content. Every day, the bot generates timestamped predictions that can be screenshotted and shared. No other trading tool has this. The audit trail IS the marketing.

---

### MARKETING BUDGET SUMMARY

| Phase | Months | Budget | Expected Users |
|---|---|---|---|
| Phase 1 (Organic) | 1–3 | $0 | 25–50 beta |
| Phase 2 (Paid Beta) | 3–6 | $0–500/month | 50–100 paying |
| Phase 3 (Scale) | 6–12 | $500–1,500/month | 300–500 paying |
| Phase 4 (Flywheel) | 12+ | $1,000–3,000/month | 1,000+ paying |

**Total marketing spend Year 1:** $3,000–$12,000
**Expected ARR at end of Year 1:** $60–120K
**Customer acquisition cost:** $6–$24 per paying user (excellent for SaaS)

---

### WHAT NOT TO DO

- Don't build more features before getting users. The product is ready.
- Don't pay for expensive ads before organic traction exists. Ads amplify what's already working.
- Don't pitch. Share data. The receipts do the selling.
- Don't target "everyone who trades." Target: small-cap day traders who understand volume profile. That's 50,000 people, not 5 million. Niche first, expand later.

---
*Full technical documentation available internally.*
`,ns={content:Ym},Vm="121672";function Gm(E){const U=E.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").split(`
`),f=[];let B=!1,H=[];const q=()=>{H.length>0&&(f.push(`<ul class="my-2 pl-5 space-y-1">${H.join("")}</ul>`),H=[])};for(let ue=0;ue<U.length;ue++){const A=U[ue];if(A.startsWith("```")){B?(B=!1,f.push("</code></pre>")):(q(),B=!0,f.push('<pre class="bg-gray-900 border border-gray-700 rounded p-3 my-3 overflow-x-auto text-xs font-mono text-green-400"><code>'));continue}if(B){f.push(A+`
`);continue}if(A.startsWith("# "))q(),f.push(`<h1 class="text-2xl font-bold text-yellow-400 mt-8 mb-3 border-b border-yellow-400/30 pb-2">${ha(A.slice(2))}</h1>`);else if(A.startsWith("## "))q(),f.push(`<h2 class="text-xl font-bold text-cyan-400 mt-6 mb-2">${ha(A.slice(3))}</h2>`);else if(A.startsWith("### "))q(),f.push(`<h3 class="text-lg font-semibold text-emerald-400 mt-4 mb-2">${ha(A.slice(4))}</h3>`);else if(A.startsWith("#### "))q(),f.push(`<h4 class="text-base font-semibold text-purple-400 mt-3 mb-1">${ha(A.slice(5))}</h4>`);else if(A.match(/^-{3,}$/)||A.match(/^\*{3,}$/))q(),f.push('<hr class="border-gray-700 my-4" />');else if(A.startsWith("- ")||A.startsWith("* "))H.push(`<li class="text-gray-300 text-sm leading-relaxed">${ha(A.slice(2))}</li>`);else if(A.match(/^\d+\. /)){const S=A.replace(/^\d+\. /,"");H.push(`<li class="text-gray-300 text-sm leading-relaxed">${ha(S)}</li>`)}else A.startsWith("> ")?(q(),f.push(`<blockquote class="border-l-4 border-yellow-500 pl-3 my-2 text-gray-400 italic text-sm">${ha(A.slice(2))}</blockquote>`)):A.trim()===""?(q(),f.push('<div class="h-2"></div>')):(q(),f.push(`<p class="text-gray-300 text-sm leading-relaxed my-1">${ha(A)}</p>`))}return q(),f.join("")}function ha(E){return E.replace(/\*\*\*(.+?)\*\*\*/g,"<strong><em>$1</em></strong>").replace(/\*\*(.+?)\*\*/g,'<strong class="text-white font-semibold">$1</strong>').replace(/\*(.+?)\*/g,'<em class="text-gray-200">$1</em>').replace(/`(.+?)`/g,'<code class="bg-gray-800 text-green-400 px-1 rounded text-xs font-mono">$1</code>').replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" target="_blank" class="text-cyan-400 hover:text-cyan-300 underline">$1</a>')}function jm({onUnlock:E}){const[G,U]=yt.useState(""),[f,B]=yt.useState(!1),[H,q]=yt.useState(!1),ue=A=>{A.preventDefault(),G===Vm?E():(B(!0),q(!0),setTimeout(()=>q(!1),500),setTimeout(()=>{B(!1),U("")},1500))};return I.jsx("div",{className:"min-h-screen bg-gray-950 flex items-center justify-center p-4",children:I.jsxs("div",{className:"w-full max-w-sm",children:[I.jsxs("div",{className:"text-center mb-8",children:[I.jsx("div",{className:"text-yellow-400 text-4xl font-black tracking-tight mb-1",children:"EdgeIQ"}),I.jsx("div",{className:"text-gray-500 text-sm",children:"Build Notes & Product Roadmap"})]}),I.jsx("form",{onSubmit:ue,className:`${H?"animate-pulse":""}`,children:I.jsxs("div",{className:"bg-gray-900 border border-gray-700 rounded-xl p-6 space-y-4",children:[I.jsx("label",{className:"block text-gray-400 text-xs font-medium uppercase tracking-widest mb-1",children:"Access Code"}),I.jsx("input",{type:"password",value:G,onChange:A=>U(A.target.value),placeholder:"Enter passcode",className:`w-full bg-gray-800 border ${f?"border-red-500 text-red-400":"border-gray-600"} rounded-lg px-4 py-3 text-white text-center text-xl tracking-widest focus:outline-none focus:border-yellow-400 transition-colors`,autoFocus:!0,maxLength:10}),f&&I.jsx("p",{className:"text-red-400 text-xs text-center",children:"Invalid passcode"}),I.jsx("button",{type:"submit",className:"w-full bg-yellow-400 hover:bg-yellow-300 text-gray-900 font-bold py-3 rounded-lg transition-colors text-sm uppercase tracking-wider",children:"Unlock"})]})}),I.jsx("p",{className:"text-gray-700 text-xs text-center mt-4",children:"Private build documentation"})]})})}function Qm({sections:E,activeId:G,onNav:U}){return I.jsx("div",{className:"w-64 flex-shrink-0 hidden lg:block",children:I.jsxs("div",{className:"fixed top-0 left-0 w-64 h-screen bg-gray-900 border-r border-gray-800 overflow-y-auto p-4",children:[I.jsx("div",{className:"text-yellow-400 font-black text-lg mb-1",children:"EdgeIQ"}),I.jsx("div",{className:"text-gray-500 text-xs mb-4",children:"Build Notes"}),I.jsx("nav",{className:"space-y-0.5",children:E.map(f=>I.jsx("button",{onClick:()=>U(f.id),className:`block w-full text-left text-xs py-1 px-2 rounded transition-colors truncate
                ${f.level===1?"font-bold text-gray-300 hover:text-yellow-400 mt-2":""}
                ${f.level===2?"pl-3 text-gray-400 hover:text-cyan-400":""}
                ${f.level===3?"pl-5 text-gray-500 hover:text-emerald-400 text-xs":""}
                ${G===f.id?"text-yellow-400 bg-yellow-400/10":""}
              `,children:f.text},f.id))})]})})}function Km(){const[E,G]=yt.useState(""),[U,f]=yt.useState([]),[B,H]=yt.useState(""),[q,ue]=yt.useState(""),A=yt.useRef(null);yt.useEffect(()=>{const Z=ns.content,F=Gm(Z),ye=[];Z.split(`
`).forEach((Le,Ae)=>{const vt=Le.match(/^# (.+)$/),nt=Le.match(/^## (.+)$/),Te=Le.match(/^### (.+)$/);vt?ye.push({id:`h-${Ae}`,text:vt[1],level:1}):nt?ye.push({id:`h-${Ae}`,text:nt[1],level:2}):Te&&ye.push({id:`h-${Ae}`,text:Te[1],level:3})}),f(ye),G(F)},[]);const S=Z=>{H(Z);const F=document.getElementById(Z);F&&F.scrollIntoView({behavior:"smooth",block:"start"})},D=ns.content.match(/\*Last updated: (.+?)\*/)?.[1]||"";return I.jsxs("div",{className:"min-h-screen bg-gray-950 flex",children:[I.jsx(Qm,{sections:U,activeId:B,onNav:S}),I.jsxs("div",{className:"flex-1 lg:ml-64",children:[I.jsxs("div",{className:"sticky top-0 z-10 bg-gray-950/95 backdrop-blur border-b border-gray-800 px-4 py-3 flex items-center gap-4",children:[I.jsx("div",{className:"text-yellow-400 font-black text-sm lg:hidden",children:"EdgeIQ"}),I.jsx("input",{type:"text",placeholder:"Search notes...",value:q,onChange:Z=>ue(Z.target.value),className:"flex-1 max-w-sm bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-yellow-400 transition-colors"}),D&&I.jsxs("span",{className:"hidden md:block text-gray-600 text-xs ml-auto",children:["Updated: ",D]})]}),I.jsx("div",{className:"max-w-4xl mx-auto px-4 md:px-8 pb-16 pt-4",children:q?I.jsx(Im,{content:ns.content,query:q}):I.jsx("div",{ref:A,dangerouslySetInnerHTML:{__html:Xm(E,U)}})})]})]})}function Xm(E,G){let U=E;return G.forEach(f=>{const B=f.text.replace(/[.*+?^${}()|[\]\\&]/g,"\\$&");U=U.replace(new RegExp(`(<h[1-3][^>]*>)(${B})(</h[1-3]>)`),`$1<span id="${f.id}" class="scroll-mt-20">$2</span>$3`)}),U}function Im({content:E,query:G}){const U=E.split(`
`),f=G.toLowerCase(),B=U.map((H,q)=>({line:H,i:q})).filter(({line:H})=>H.toLowerCase().includes(f));return B.length===0?I.jsxs("p",{className:"text-gray-500 text-sm mt-8 text-center",children:['No results for "',G,'"']}):I.jsxs("div",{className:"space-y-3 mt-4",children:[I.jsxs("p",{className:"text-gray-500 text-xs",children:[B.length,' matches for "',G,'"']}),B.map(({line:H,i:q})=>{const ue=Math.max(0,q-1);U.slice(ue,q+3).join(`
`);const A=H.replace(new RegExp(G.replace(/[.*+?^${}()|[\]\\]/g,"\\$&"),"gi"),S=>`<mark class="bg-yellow-400/30 text-yellow-300 rounded px-0.5">${S}</mark>`);return I.jsxs("div",{className:"bg-gray-900 border border-gray-700 rounded-lg p-3",children:[I.jsx("p",{className:"text-sm text-gray-300",dangerouslySetInnerHTML:{__html:A}}),I.jsxs("p",{className:"text-xs text-gray-600 mt-1",children:["Line ",q+1]})]},q)})]})}function Pm(){const[E,G]=yt.useState(!1);yt.useEffect(()=>{sessionStorage.getItem("edgeiq_notes_unlocked")==="1"&&G(!0)},[]);const U=()=>{sessionStorage.setItem("edgeiq_notes_unlocked","1"),G(!0)};return E?I.jsx(Km,{}):I.jsx(jm,{onUnlock:U})}qm.createRoot(document.getElementById("root")).render(I.jsx(Pm,{}));
