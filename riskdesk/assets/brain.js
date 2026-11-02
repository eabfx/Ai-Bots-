/* Second Brain: an editable, Notion-style workspace inside RiskDesk (pages with blocks, and databases as tables).
   It edits RiskDesk's own copy (trader/brain/brain.json through /api/brain). It never talks to Notion. */
(function(){
"use strict";
var TXT={red:"#e5484d",green:"#30a46c",blue:"#3e8fd6",yellow:"#d6a800",orange:"#e5833a",purple:"#8e4ec6",pink:"#d6409f",brown:"#a2745a",gray:"#9c9c98"};
var COLN=Object.keys(TXT);
var HDRS={"X-Requested-With":"zones-dashboard","Content-Type":"application/json"};
var TEXTT={h1:1,h2:1,h3:1,paragraph:1,li:1,ol:1,todo:1,quote:1,callout:1,toggle:1};
var EMOJI=["\u{1F4A1}","\u{1F4CC}","⚠️","✅","❗","\u{1F525}","\u{1F4C8}","\u{1F4C9}","\u{1F3AF}","\u{1F4B0}","\u{1F9E0}","⭐","\u{1F4DD}","\u{1F6AB}","\u{1F512}","\u{1F4CA}","⏰","\u{1F4D2}"];
var TYPES={title:"Title",text:"Text",number:"Number",select:"Select",multi_select:"Multi-select",date:"Date",checkbox:"Checkbox",url:"URL",files:"Files & images"};
var TICON={title:"Aa",text:"≡",number:"#",select:"▾",multi_select:"☰",date:"\u{1F4C5}",checkbox:"☑",url:"\u{1F517}",files:"\u{1F4CE}"};
var S={space:"brain",api:"/api/brain",tok:"",home:null,doc:null,rev:0,loc:null,trail:[],dirty:false,saving:false,timer:null,mount:null,dbSt:{},open:new WeakSet(),conflict:false};
var ELS=new Map(),POP=null,SM=null,DRAG=null,STATUS=null,SELTB=null;

/* ---------- small helpers ---------- */
function h(tag,cls,text){var n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined&&text!==null)n.textContent=text;return n}
function uid(){return "x"+Math.random().toString(36).slice(2,9)+Date.now().toString(36).slice(-4)}
function clone(o){return JSON.parse(JSON.stringify(o))}
function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}
function rgb(hex){return[parseInt(hex.substr(1,2),16),parseInt(hex.substr(3,2),16),parseInt(hex.substr(5,2),16)]}
var REV={};COLN.forEach(function(n){REV[rgb(TXT[n]).join(",")]=n});
function nameOf(css){var m=/rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/.exec(css||"");return m?REV[m[1]+","+m[2]+","+m[3]]||null:null}
function tint(co){var m=/^([a-z]+?)(_background)?$/.exec(co||"");return m&&TXT[m[1]]?{c:TXT[m[1]],bg:!!m[2]}:null}
function bgOf(name){var c=rgb(TXT[name]);return"rgba("+c[0]+","+c[1]+","+c[2]+",.22)"}
function today(){var d=new Date();return d.getFullYear()+"-"+("0"+(d.getMonth()+1)).slice(-2)+"-"+("0"+d.getDate()).slice(-2)}
function say(msg,undo){
  var t=h("div","sb-toast");t.appendChild(h("span",null,msg));
  if(undo){var b=h("button",null,"Undo");b.type="button";b.onclick=function(){undo();t.remove()};t.appendChild(b)}
  document.body.appendChild(t);setTimeout(function(){t.remove()},undo?7000:3000)}

/* ---------- saving ---------- */
function setStatus(s,bad){if(STATUS){STATUS.textContent=s;STATUS.className="sb-status"+(bad?" bad":"")}}
function touch(){if(S.conflict)return;S.dirty=true;setStatus("Saving...");clearTimeout(S.timer);S.timer=setTimeout(flush,700)}
function flush(){
  clearTimeout(S.timer);
  if(!S.dirty)return Promise.resolve();
  if(S.saving){S.timer=setTimeout(flush,300);return Promise.resolve()}
  S.saving=true;S.dirty=false;S.doc.rev=S.rev;
  var hd={"X-Requested-With":HDRS["X-Requested-With"],"Content-Type":HDRS["Content-Type"]};if(S.tok)hd["X-Vault-Token"]=S.tok;
  return fetch(S.api,{method:"POST",headers:hd,body:JSON.stringify(S.doc)}).then(function(r){
    return r.json().then(function(j){
      S.saving=false;
      if(r.status===401&&S.space==="vault"){lockedOut();return}
      if(r.status===409){S.conflict=true;S.dirty=false;setStatus(j.error||"Changed somewhere else. Reload this page.",true);return}
      if(!r.ok)throw new Error(j.error||"save failed");
      S.rev=j.rev;if(S.dirty)flush();else setStatus("Saved")})
  }).catch(function(e){S.saving=false;S.dirty=true;setStatus("Could not save: "+e.message+" (retrying)",true);S.timer=setTimeout(flush,4000)})}
window.addEventListener("beforeunload",function(e){if(S.dirty||S.saving){flush();e.preventDefault();e.returnValue=""}});
document.addEventListener("visibilitychange",function(){if(document.hidden)flush()});

/* ---------- popovers ---------- */
function popClose(){if(POP){POP.el.remove();document.removeEventListener("mousedown",POP.away,true);POP=null}SM=null}
function popOpen(anchor,build,cls){
  popClose();var p=h("div","sb-pop "+(cls||""));build(p);document.body.appendChild(p);
  var r=anchor.getBoundingClientRect(),w=p.offsetWidth,ht=p.offsetHeight;
  var left=Math.max(8,Math.min(r.left,window.innerWidth-w-8)),top=r.bottom+4;
  if(top+ht>window.innerHeight-8)top=Math.max(8,r.top-ht-4);
  p.style.left=left+"px";p.style.top=top+"px";
  var away=function(e){if(!p.contains(e.target)&&!(anchor.contains&&anchor.contains(e.target)))popClose()};
  POP={el:p,away:away,t:Date.now()};setTimeout(function(){if(POP&&POP.el===p)document.addEventListener("mousedown",away,true)},0);return p}
document.addEventListener("keydown",function(e){if(e.key==="Escape"&&POP&&!SM)popClose()});
window.addEventListener("scroll",function(e){if(POP&&Date.now()-POP.t>400&&!POP.el.contains(e.target))popClose()},true);
function menuItem(p,label,fn,cls){
  var b=h("button","sb-mi"+(cls?" "+cls:""),label);b.type="button";
  b.onmousedown=function(e){e.preventDefault()};b.onclick=function(){popClose();fn()};p.appendChild(b);return b}

/* ---------- runs <-> html ---------- */
function runsToHtml(runs){
  return(runs||[]).map(function(r){
    var s=esc(r.x).replace(/\n/g,"<br>");
    if(r.c)s="<code>"+s+"</code>";if(r.b)s="<b>"+s+"</b>";if(r.i)s="<i>"+s+"</i>";if(r.u)s="<u>"+s+"</u>";if(r.s)s="<s>"+s+"</s>";
    var t=tint(r.co);if(t)s='<span style="'+(t.bg?"background-color:"+bgOf(r.co.replace("_background","")):"color:"+t.c)+'">'+s+"</span>";
    if(r.a&&/^(https?:|mailto:)/.test(r.a))s='<a href="'+esc(r.a)+'" target="_blank" rel="noopener noreferrer">'+s+"</a>";
    return s}).join("")}
function runsFromEl(root){
  var out=[];
  function push(x,st){
    if(!x)return;var r={x:x},k;for(k in st)if(st[k])r[k]=st[k];
    var l=out[out.length-1];
    if(l&&["b","i","u","s","c","co","a"].every(function(f){return l[f]===r[f]}))l.x+=x;else out.push(r)}
  (function walk(n,st){
    for(var c=n.firstChild;c;c=c.nextSibling){
      if(c.nodeType===3){push(c.nodeValue.replace(/\u00a0/g," "),st);continue}
      if(c.nodeType!==1)continue;
      var tag=c.tagName,s={};for(var k in st)s[k]=st[k];
      if(tag==="BR"){if(c===n.lastChild&&n===root)continue;push("\n",st);continue}
      if((tag==="DIV"||tag==="P")&&out.length)push("\n",st);
      if(tag==="B"||tag==="STRONG")s.b=true;if(tag==="I"||tag==="EM")s.i=true;if(tag==="U")s.u=true;
      if(tag==="S"||tag==="STRIKE"||tag==="DEL")s.s=true;if(tag==="CODE")s.c=true;
      if(tag==="A"&&/^(https?:|mailto:)/.test(c.getAttribute("href")||""))s.a=c.getAttribute("href");
      var cs=c.style;
      if(cs){
        if(cs.fontWeight==="bold"||+cs.fontWeight>=600)s.b=true;if(cs.fontStyle==="italic")s.i=true;
        var td=cs.textDecorationLine||cs.textDecoration||"";if(/underline/.test(td))s.u=true;if(/line-through/.test(td))s.s=true;
        if(cs.color){var nc=nameOf(cs.color);if(nc)s.co=nc}
        if(cs.backgroundColor){var nb=nameOf(cs.backgroundColor);if(nb)s.co=nb+"_background"}}
      if(tag==="FONT"&&c.getAttribute("color")){var nf=nameOf(c.getAttribute("color"));if(nf)s.co=nf}
      walk(c,s)}
  })(root,{});
  return out}
function plainOf(runs){return(runs||[]).map(function(r){return r.x}).join("")}

/* ---------- model helpers ---------- */
function newPara(){return{t:"paragraph",r:[]}}
function walkNodes(list,fn){(list||[]).forEach(function(n){fn(n);walkNodes(n.k,fn);(n.cols||[]).forEach(function(c){walkNodes(c.k,fn)})})}
function pageKids(id){var out=[];Object.keys(S.doc.pages).forEach(function(k){if(S.doc.pages[k].parent===id)out.push(k)});return out}
function dropPage(id){
  pageKids(id).forEach(dropPage);var pg=S.doc.pages[id];if(!pg)return;
  walkNodes(pg.nodes,function(n){if(n.t==="db"&&S.doc.dbs[n.id])delete S.doc.dbs[n.id]});delete S.doc.pages[id]}
function dropNode(n){
  if(n.t==="page"&&n.id&&S.doc.pages[n.id])dropPage(n.id);
  if(n.t==="db"&&S.doc.dbs[n.id])delete S.doc.dbs[n.id];
  walkNodes(n.k,function(x){if(x.t==="page"&&x.id&&S.doc.pages[x.id])dropPage(x.id)})}
function newDb(title){
  var id="d"+uid();
  S.doc.dbs[id]={title:title||"",props:[{id:"title",name:"Name",type:"title",options:[]},{id:"tags",name:"Tags",type:"multi_select",options:[]},{id:"date",name:"Date",type:"date",options:[]}],rows:[],view:{sort:[],filters:[],hidden:[]}};
  return id}
function newPage(parent,title){var id="p"+uid();S.doc.pages[id]={title:title||"Untitled",icon:"",parent:parent,nodes:[newPara()]};return id}

/* ---------- navigation and the page frame ---------- */
function crumbs(){
  var out=[],loc=S.loc;
  if(loc.row){out.push({label:(S.doc.dbs[loc.row.db].rows.filter(function(r){return r.id===loc.row.row})[0]||{v:{}}).v.title||"Untitled",loc:null});loc=S.loc.from?{page:S.loc.from}:{page:S.doc.root}}
  var id=loc.page,guard=0;
  while(id&&S.doc.pages[id]&&guard++<20){out.unshift({label:S.doc.pages[id].title||"Untitled",loc:{page:id}});id=S.doc.pages[id].parent}
  return out}
function go(loc){flush();S.loc=loc;render();window.scrollTo(0,0)}
function render(){
  popClose();ELS.clear();var y=window.scrollY,m=S.mount;m.textContent="";
  var top=h("div","sb-top"),cr=h("div","sb-crumbs");
  crumbs().forEach(function(c,i,all){
    if(i)cr.appendChild(h("span","sb-sep","/"));
    if(c.loc&&i<all.length-1){var b=h("button","sb-crumb",c.label);b.type="button";b.onclick=function(){go(c.loc)};cr.appendChild(b)}else cr.appendChild(h("span","sb-crumb cur",c.label))});
  top.appendChild(cr);top.appendChild(viewControls());STATUS=h("span","sb-status",S.dirty?"Saving...":"Saved");top.appendChild(STATUS);m.appendChild(top);
  m.appendChild(sectionBar());
  var body=h("div","sb-body");m.appendChild(body);
  if(S.loc.row)rowPage(body);else pageView(body);
  applyView();window.scrollTo(0,y)}
/* how the page is shown: full width or a narrow column, and the text size. Kept in this browser only. */
var SIZES=[0.9,1,1.12,1.25,1.4],VIEW=(function(){try{var v=JSON.parse(localStorage.getItem("sb.view")||"{}");return{wide:v.wide!==false,size:Math.max(0,Math.min(SIZES.length-1,v.size===undefined?1:+v.size||0))}}catch(e){return{wide:true,size:1}}})();
function viewControls(){
  var box=h("div","sb-view");box.id="sb-viewbox";
  function btn(id,label,fn,title){var b=h("button","sb-mini",label);b.type="button";b.id=id;b.title=title;b.setAttribute("aria-label",title);b.onclick=fn;box.appendChild(b)}
  btn("sb-vw","",function(){VIEW.wide=!VIEW.wide;saveView();applyView()},"Switch between a full-width page and a narrow column");
  btn("sb-vm","A\u2212",function(){if(VIEW.size>0){VIEW.size--;saveView();applyView()}},"Smaller text");
  btn("sb-vp","A+",function(){if(VIEW.size<SIZES.length-1){VIEW.size++;saveView();applyView()}},"Bigger text");
  return box}
function saveView(){try{localStorage.setItem("sb.view",JSON.stringify(VIEW))}catch(e){}}
function applyView(){
  var m=S.mount;if(!m)return;
  m.classList.toggle("sbv-narrow",!VIEW.wide);m.style.setProperty("--sbs",String(SIZES[VIEW.size]));
  var w=document.getElementById("sb-vw"),a=document.getElementById("sb-vm"),b=document.getElementById("sb-vp");
  if(w)w.textContent=VIEW.wide?"Narrow page":"Wide page";if(a)a.disabled=VIEW.size===0;if(b)b.disabled=VIEW.size===SIZES.length-1}
function refreshFromNotion(btn){
  if(btn){btn.disabled=true;btn.textContent="Reading Notion..."}
  settle().then(function(){return fetch("/api/brain/fill",{method:"POST",headers:{"X-Requested-With":HDRS["X-Requested-With"],"Content-Type":HDRS["Content-Type"]},body:JSON.stringify({refresh:true})})})
   .then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||"failed");return j})})
   .then(function(j){return fetch("/api/brain").then(function(r){return r.json()}).then(function(d){
      S.doc=d.doc;S.rev=d.doc.rev||0;S.conflict=false;if(!S.doc.pages[S.loc.page])S.loc={page:S.doc.root};render();
      say(j.filled.length?"Filled from Notion: "+j.filled.join(", ")+".":"Nothing new to fill: every page already has content. Pages you edited are never overwritten.")})})
   .catch(function(e){say("Could not read Notion: "+e.message);if(btn){btn.disabled=false;btn.textContent="Refresh pages from Notion"}})}
function deletePageAsk(id){
  var pg=S.doc.pages[id];if(!pg||id===S.doc.root)return false;
  if(!confirm("Delete \""+(pg.title||"Untitled")+"\" and everything inside it? This only removes it here, never in Notion."))return false;
  var parent=pg.parent||S.doc.root;
  [S.doc.pages[parent]].forEach(function(pp){if(pp)pp.nodes=pp.nodes.filter(function(n){return!(n.t==="page"&&n.id===id)})});
  dropPage(id);touch();return parent}
/* the sections of your journal as buttons: Home, then every page that sits directly under it, in the order they appear */
function sectionIds(){
  var root=S.doc.root,seen={},out=[];
  walkNodes(S.doc.pages[root].nodes,function(n){if(n.t==="page"&&n.id&&S.doc.pages[n.id]&&S.doc.pages[n.id].parent===root&&!seen[n.id]){seen[n.id]=1;out.push(n.id)}});
  pageKids(root).forEach(function(k){if(!seen[k]){seen[k]=1;out.push(k)}});
  return out}
function sectionBar(){
  var bar=h("div","sb-tabs"),root=S.doc.root;
  var at=S.loc.row?(S.loc.from||root):S.loc.page,top=root,id=at,guard=0;
  while(id&&id!==root&&guard++<20){top=id;id=S.doc.pages[id]&&S.doc.pages[id].parent;if(!S.doc.pages[id]&&id!==root){top=root;break}}
  function tab(label,target,on,deletable){
    var b=h("button","sb-tab"+(on?" on":""),label);b.type="button";b.onclick=function(){go({page:target})};
    if(!deletable){bar.appendChild(b);return}
    var w=h("span","sb-tabw"),x=h("button","sb-tabx","\u2715");x.type="button";x.title="Delete this section";x.setAttribute("aria-label","Delete section "+label);
    x.onclick=function(e){e.stopPropagation();var parent=deletePageAsk(target);if(parent){go({page:on?root:at||root})}};
    w.appendChild(b);w.appendChild(x);bar.appendChild(w)}
  tab((S.space==="vault"?"\u{1F512} ":"\u{1F3E0} ")+(S.doc.pages[root].title||"Home"),root,top===root&&!S.loc.row&&at===root);
  sectionIds().forEach(function(k){var p=S.doc.pages[k];tab((p.icon?p.icon+" ":"")+(p.title||"Untitled"),k,top===k,true)});
  var add=h("button","sb-tab add","+ New section");add.type="button";
  add.onclick=function(){var id2=newPage(root,"Untitled");S.doc.pages[root].nodes.push({t:"page",id:id2,title:"Untitled"});touch();go({page:id2})};
  bar.appendChild(add);
  if(S.space==="brain"){
    var rf=h("button","sb-tab ctl","Refresh pages from Notion");rf.type="button";rf.title="Reads your Notion again (read only) and fills pages that are still empty. Pages you have written in are never touched.";
    rf.onclick=function(){refreshFromNotion(rf)};bar.appendChild(rf);
    var lk=h("button","sb-tab lock","\u{1F512} Thoughts + Emotions");lk.type="button";lk.onclick=openVault;bar.appendChild(lk)}
  else{
    function ctl(label,fn,cls){var b=h("button","sb-tab ctl"+(cls?" "+cls:""),label);b.type="button";b.onclick=fn;bar.appendChild(b)}
    ctl("Copy from Notion (read only)",importPrivate);ctl("Change password",changePassword);ctl("\u{1F512} Lock and leave",function(){leaveVault()},"lock")}
  return bar}
/* ---------- the password-locked section ---------- */
function settle(){return new Promise(function(res){var n=0;(function tick(){if((!S.dirty&&!S.saving)||n++>40)return res();flush();setTimeout(tick,150)})()})}
function vaultFetch(path,method,body,tok){
  var hd={"X-Requested-With":HDRS["X-Requested-With"],"Content-Type":HDRS["Content-Type"]};if(tok)hd["X-Vault-Token"]=tok;
  return fetch(path,{method:method,headers:hd,body:body?JSON.stringify(body):undefined}).then(function(r){return r.json().then(function(j){if(!r.ok){var e=new Error(j.error||"failed");e.status=r.status;throw e}return j})})}
function openVault(){
  flush();vaultFetch("/api/vault/status","GET").then(function(j){lockScreen(!j.set,"")}).catch(function(e){say("Could not reach the locked section: "+e.message)})}
function lockScreen(setMode,msg){
  var m=S.mount;m.textContent="";m.appendChild(sectionBar());
  var card=h("div","sb-lock");card.appendChild(h("div","sb-lockic","\u{1F512}"));card.appendChild(h("h2",null,"Thoughts + Emotions"));
  card.appendChild(h("p","sb-locknote",setMode?"Choose a PIN (4 digits is fine) or a longer password for this private section. It is kept nowhere, so if you forget it these pages cannot be recovered. Once it is set, your Thoughts + Emotions page is copied here from Notion (read only, Notion is never changed) and locked.":"This section is locked. Enter your password to open it. It locks again when you leave Second Brain, and after 20 minutes of no use."));
  var pw=h("input","sb-seli");pw.type="password";pw.placeholder=setMode?"New PIN or password (4 or more, 4 digits is fine)":"PIN or password";pw.setAttribute("inputmode","numeric");pw.setAttribute("autocomplete",setMode?"new-password":"current-password");card.appendChild(pw);
  var pw2=null;if(setMode){pw2=h("input","sb-seli");pw2.type="password";pw2.setAttribute("inputmode","numeric");pw2.placeholder="Type it again";pw2.setAttribute("autocomplete","new-password");card.appendChild(pw2)}
  var err=h("div","sb-lockerr",msg||"");var btn=h("button","sb-new",setMode?"Set PIN and open":"Unlock");btn.type="button";
  function go(){
    err.textContent="";
    if(setMode&&pw.value!==pw2.value){err.textContent="The two passwords are not the same.";return}
    btn.disabled=true;
    vaultFetch(setMode?"/api/vault/setup":"/api/vault/unlock","POST",{password:pw.value}).then(function(j){pw.value="";if(pw2)pw2.value="";return enterVault(j.token,setMode)})
      .catch(function(e){btn.disabled=false;err.textContent=e.message;pw.select()})}
  btn.onclick=go;[pw,pw2].forEach(function(x){if(x)x.onkeydown=function(e){if(e.key==="Enter")go()}});
  card.appendChild(btn);card.appendChild(err);m.appendChild(card);setTimeout(function(){pw.focus()},0)}
function enterVault(token,first){
  return vaultFetch("/api/vault/doc","GET",null,token).then(function(j){
    S.home={doc:S.doc,rev:S.rev,loc:S.loc};S.space="vault";S.api="/api/vault/doc";S.tok=token;S.doc=j.doc;S.rev=j.doc.rev||0;S.loc={page:S.doc.root};S.conflict=false;S.dirty=false;
    render();if(first)importPrivate(true)})}
function restoreHome(){
  if(!S.home)return;S.space="brain";S.api="/api/brain";S.tok="";S.doc=S.home.doc;S.rev=S.home.rev;S.loc=S.home.loc;S.home=null;S.dirty=false;S.conflict=false}
function leaveVault(quiet){
  if(S.space!=="vault")return Promise.resolve();
  var tok=S.tok;return settle().then(function(){return vaultFetch("/api/vault/lock","POST",{}, tok).catch(function(){})}).then(function(){restoreHome();if(!quiet&&S.mount&&S.mount.isConnected)render()})}
function lockedOut(){restoreHome();render();say("The locked section locked itself. Open it with your password again.")}
function importPrivate(auto){
  if(S.space!=="vault")return;
  if(!auto&&!confirm("Copy Thoughts + Emotions from Notion into this locked section? Notion is only read. Anything already here stays, and the copy arrives as a new sub-page."))return;
  say("Copying from Notion (read only). This can take a minute...");
  settle().then(function(){return vaultFetch("/api/vault/import","POST",{},S.tok)}).then(function(j){
    S.doc=j.doc;S.rev=j.doc.rev;S.loc={page:S.doc.root};render();say("Copied "+Object.keys(S.doc.pages).length+" page(s) from Notion into the locked section.")})
  .catch(function(e){if(e.status===401)return lockedOut();say("Could not copy from Notion: "+e.message)})}
function changePassword(){
  var a=prompt("New PIN or password (4 or more characters, 4 digits is fine). Do not forget it, it cannot be recovered:");if(!a)return;
  var b=prompt("Type the new password again:");if(a!==b){say("The two passwords were not the same. Nothing changed.");return}
  settle().then(function(){return vaultFetch("/api/vault/password","POST",{password:a},S.tok)}).then(function(j){S.tok=j.token;say("Password changed.")}).catch(function(e){say("Could not change it: "+e.message)})}
function pageView(body){
  var id=S.loc.page;if(!S.doc.pages[id]){S.loc={page:S.doc.root};id=S.doc.root}
  var pg=S.doc.pages[id];
  var head=h("div","sb-head");
  var ic=h("button","sb-icon",pg.icon||"\u{1F4C4}");ic.type="button";ic.title="Change icon";
  ic.onclick=function(){emojiPop(ic,function(e){pg.icon=e;touch();render()},true)};head.appendChild(ic);
  head.appendChild(titleEl(pg.title,"Untitled",function(v){pg.title=v;touch()},function(){render()}));
  body.appendChild(head);
  if(id!==S.doc.root){
    var del=h("button","sb-link sb-del",(pg.parent===S.doc.root?"Delete this section":"Delete this page"));del.type="button";
    del.onclick=function(){var parent=deletePageAsk(id);if(parent)go({page:parent})};
    body.appendChild(del)}
  var ed=h("div","sb-ed");body.appendChild(ed);
  editor(pg.nodes,ed,null,{page:id,fill:true});
  ed.addEventListener("click",function(e){if(e.target===ed){var last=pg.nodes[pg.nodes.length-1];if(!last||!TEXTT[last.t]||plainOf(last.r)){pg.nodes.push(newPara());touch();redraw(pg.nodes[pg.nodes.length-1])}else focusNode(last,"end")}})}
function titleEl(value,ph,onInput,onBlur){
  var t=h("div","sb-title");t.contentEditable=(("plaintextOnly" in t)?"plaintext-only":"true");t.setAttribute("data-ph",ph);t.textContent=value||"";
  t.addEventListener("keydown",function(e){if(e.key==="Enter"){e.preventDefault();t.blur()}});
  t.addEventListener("input",function(){onInput(t.textContent.replace(/\n/g," "))});
  t.addEventListener("paste",function(e){e.preventDefault();document.execCommand("insertText",false,(e.clipboardData.getData("text/plain")||"").replace(/\s+/g," "))});
  if(onBlur)t.addEventListener("blur",onBlur);return t}
function emojiPop(anchor,pick,canClear){
  popOpen(anchor,function(p){
    var g=h("div","sb-emoji");EMOJI.forEach(function(e){var b=h("button",null,e);b.type="button";b.onclick=function(){popClose();pick(e)};g.appendChild(b)});p.appendChild(g);
    if(canClear)menuItem(p,"Remove icon",function(){pick("")})})}
function redraw(focusNode_,pos){
  var y=window.scrollY;render();window.scrollTo(0,y);if(focusNode_)focusNode(focusNode_,pos===undefined?"start":pos)}

/* ---------- caret helpers ---------- */
function caretOffset(el){
  var s=window.getSelection();if(!s.rangeCount)return 0;var r=s.getRangeAt(0).cloneRange();
  var pre=r.cloneRange();pre.selectNodeContents(el);pre.setEnd(r.startContainer,r.startOffset);return pre.toString().length}
function placeCaret(el,off){
  el.focus();var s=window.getSelection(),r=document.createRange();
  if(off==="end"||off===undefined){r.selectNodeContents(el);r.collapse(false)}
  else if(off==="start"||off<=0){r.selectNodeContents(el);r.collapse(true)}
  else{
    var w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),n,left=off,done=false;
    while((n=w.nextNode())){if(n.nodeValue.length>=left){r.setStart(n,left);r.collapse(true);done=true;break}left-=n.nodeValue.length}
    if(!done){r.selectNodeContents(el);r.collapse(false)}}
  s.removeAllRanges();s.addRange(r)}
function focusNode(node,pos){var el=ELS.get(node);if(el){placeCaret(el,pos);el.scrollIntoView({block:"nearest"})}}
function atStart(el){var s=window.getSelection();if(!s.rangeCount||!s.isCollapsed)return false;return caretOffset(el)===0}
function atEnd(el){var s=window.getSelection();if(!s.rangeCount||!s.isCollapsed)return false;return caretOffset(el)>=el.textContent.length}
function splitAtCaret(el){
  var s=window.getSelection(),r=s.getRangeAt(0);
  var a=document.createRange();a.selectNodeContents(el);a.setEnd(r.startContainer,r.startOffset);
  var b=document.createRange();b.selectNodeContents(el);b.setStart(r.endContainer,r.endOffset);
  var da=h("div"),db=h("div");da.appendChild(a.cloneContents());db.appendChild(b.cloneContents());
  return[runsFromEl(da),runsFromEl(db)]}
function focusAdj(el,dir){
  var all=[].slice.call(S.mount.querySelectorAll(".bk-t[data-bk]")),i=all.indexOf(el),n=all[i+dir];
  if(n)placeCaret(n,dir<0?"end":"start")}

/* ---------- the block editor ---------- */
var ITEMS=[
  {k:"text paragraph",l:"Text",d:"Plain text",t:"paragraph"},
  {k:"heading h1 title big",l:"Heading 1",d:"Big heading",t:"h1"},
  {k:"heading h2 medium",l:"Heading 2",d:"Medium heading",t:"h2"},
  {k:"heading h3 small",l:"Heading 3",d:"Small heading",t:"h3"},
  {k:"bullet list ul",l:"Bulleted list",d:"A simple list",t:"li"},
  {k:"number list ol",l:"Numbered list",d:"A list with numbers",t:"ol"},
  {k:"todo task check",l:"To-do list",d:"Track tasks with checkboxes",t:"todo"},
  {k:"toggle collapse",l:"Toggle list",d:"Hide and show content",t:"toggle"},
  {k:"quote",l:"Quote",d:"Capture a quote",t:"quote"},
  {k:"callout note box",l:"Callout",d:"Make text stand out",t:"callout"},
  {k:"divider line hr",l:"Divider",d:"Visually divide blocks",t:"hr"},
  {k:"code",l:"Code",d:"Monospace block",t:"code"},
  {k:"table grid",l:"Table",d:"Rows and columns",t:"table"},
  {k:"columns layout 2",l:"2 columns",d:"Side by side",t:"cols",n:2},
  {k:"columns layout 3",l:"3 columns",d:"Three side by side",t:"cols",n:3},
  {k:"page subpage",l:"Page",d:"A page inside this page",t:"page"},
  {k:"database table db",l:"Database",d:"A table you can sort and filter",t:"db"},
  {k:"image picture screenshot photo",l:"Image",d:"Upload a picture",t:"img"}];
function editor(list,box,up,ctx){
  if(!list.length&&ctx.fill)list.push(newPara());
  var num=0;
  list.forEach(function(n,i){num=n.t==="ol"?num+1:0;box.appendChild(blockEl(n,list,i,up,ctx,num))})}
function textEl(node,ph,multi){
  var el=h("div","bk-t");el.contentEditable="true";el.setAttribute("data-bk","1");el.setAttribute("data-ph",ph||"");
  el.innerHTML=runsToHtml(node.r);ELS.set(node,el);
  var sync=function(){node.r=runsFromEl(el);if(!el.textContent)el.innerHTML="";touch()};
  el._sync=sync;
  el.addEventListener("blur",function(){if(SM&&SM.el===el)setTimeout(function(){if(SM&&SM.el===el)popClose()},150)});
  el.addEventListener("input",function(){sync();shortcuts(node,el);slashCheck(node,el)});
  el.addEventListener("paste",function(e){
    var items=e.clipboardData&&e.clipboardData.files;
    if(items&&items.length&&/^image\//.test(items[0].type)){e.preventDefault();pasteImage(items[0],node);return}
    e.preventDefault();var t=(e.clipboardData.getData("text/plain")||"").replace(/\r/g,"");
    document.execCommand("insertText",false,t)});
  return el}
function pasteImage(file,node){
  var holder=findHolder(node);if(!holder)return;
  uploadImage(file).then(function(name){var i=holder.list.indexOf(holder.node);holder.list.splice(i+1,0,{t:"img",img:name});touch();redraw()}).catch(function(e){say("Could not add the image: "+e.message)})}
function findHolder(node){var found=null;(function scan(list){list.forEach(function(n){if(n===node)found={list:list,node:n};scan(n.k||[]);(n.cols||[]).forEach(function(c){scan(c.k)})})})(curList());return found}
function curList(){
  if(S.loc.row){var r=rowOf(S.loc.row.db,S.loc.row.row);if(r){r.nodes=r.nodes||[];return r.nodes}return[]}
  return S.doc.pages[S.loc.page].nodes}
function blockEl(node,list,i,up,ctx,num){
  var bk=h("div","bk bkt-"+node.t);bk._bk={node:node,list:list,up:up};
  var hd=h("div","bk-hd"),plus=h("button","bk-h","+"),grip=h("button","bk-h bk-grip","⠇");
  plus.type=grip.type="button";plus.title="Add a block below";grip.title="Drag to move, click for options";
  plus.onclick=function(){var nb=newPara();list.splice(i+1,0,nb);touch();redraw(nb);setTimeout(function(){openSlash(nb,ELS.get(nb),"")},0)};
  grip.draggable=true;
  grip.addEventListener("click",function(){blockMenu(grip,node,list,i,up)});
  grip.addEventListener("dragstart",function(e){DRAG={node:node,list:list};e.dataTransfer.setData("text/plain","block");e.dataTransfer.effectAllowed="move";bk.classList.add("drag")});
  grip.addEventListener("dragend",function(){DRAG=null;bk.classList.remove("drag");clearDrop()});
  hd.appendChild(plus);hd.appendChild(grip);bk.appendChild(hd);
  bk.addEventListener("dragover",function(e){
    if(!DRAG||DRAG.node===node||contains(DRAG.node,node))return;e.preventDefault();e.stopPropagation();
    var r=bk.getBoundingClientRect(),after=e.clientY>r.top+r.height/2;clearDrop();bk.classList.add(after?"drop-after":"drop-before")});
  bk.addEventListener("drop",function(e){
    if(!DRAG)return;e.preventDefault();e.stopPropagation();
    var after=bk.classList.contains("drop-after"),d=DRAG;clearDrop();DRAG=null;
    if(d.node===node||contains(d.node,node))return;
    d.list.splice(d.list.indexOf(d.node),1);
    var at=list.indexOf(node)+(after?1:0);list.splice(at,0,d.node);touch();redraw()});
  var main=h("div","bk-main");bk.appendChild(main);
  var t=node.t,tn=tint(node.co);
  var kids=function(){if(node.k&&node.k.length&&(t!=="toggle"||S.open.has(node))){var k=h("div","bk-kids");editor(node.k,k,{list:list,node:node,up:up},ctx);main.appendChild(k)}};
  var ph={h1:"Heading 1",h2:"Heading 2",h3:"Heading 3",quote:"Quote",callout:"Type something",toggle:"Toggle",paragraph:"Type '/' for blocks"}[t]||"List";
  if(TEXTT[t]){
    var row=h("div","bk-row"),te=textEl(node,ph);te.classList.add("bk-"+t+"-t");
    if(tn){if(tn.bg){te.style.background=tn.c+"26";te.style.borderRadius="6px";te.style.padding="2px 8px"}else te.style.color=tn.c}
    te.addEventListener("keydown",function(e){textKey(e,node,te,list,up,ctx)});
    if(t==="li")row.appendChild(h("span","bk-b","•"));
    if(t==="ol")row.appendChild(h("span","bk-b",num+"."));
    if(t==="todo"){var cb=h("input","bk-cb");cb.type="checkbox";cb.checked=!!node.done;te.classList.toggle("done",!!node.done);
      cb.onchange=function(){node.done=cb.checked;te.classList.toggle("done",cb.checked);touch()};row.appendChild(cb)}
    if(t==="toggle"){var ar=h("button","bk-arrow",S.open.has(node)?"▾":"▸");ar.type="button";
      ar.onclick=function(){if(S.open.has(node))S.open.delete(node);else S.open.add(node);redraw(node,"end")};row.appendChild(ar)}
    if(t==="callout"){row.classList.add("callout");if(tn&&tn.bg){row.style.background=tn.c+"26";row.style.borderColor=tn.c;te.style.background="none";te.style.padding="0"}
      var ci=h("button","bk-cico",node.ic||"\u{1F4A1}");ci.type="button";ci.onclick=function(){emojiPop(ci,function(e){node.ic=e||"\u{1F4A1}";touch();ci.textContent=node.ic})};row.appendChild(ci)}
    row.appendChild(te);main.appendChild(row);
    kids()}
  else if(t==="hr"){main.appendChild(h("hr","bk-hr"))}
  else if(t==="code"){
    var ta=h("textarea","bk-code");ta.value=node.x||"";ta.spellcheck=false;ta.rows=Math.max(2,(node.x||"").split("\n").length);
    ta.oninput=function(){node.x=ta.value;ta.rows=Math.max(2,ta.value.split("\n").length);touch()};
    ta.onkeydown=function(e){if(e.key==="Tab"){e.preventDefault();document.execCommand("insertText",false,"  ")}};
    ta.setAttribute("data-bk","code");main.appendChild(ta)}
  else if(t==="table"){main.appendChild(tableEl(node))}
  else if(t==="cols"){
    var cw=h("div","bk-cols");
    node.cols.forEach(function(c,ci){var col=h("div","bk-col");editor(c.k,col,{list:list,node:node,up:up,col:c},{page:ctx.page,fill:true});cw.appendChild(col)});
    main.appendChild(cw)}
  else if(t==="page"){
    var pg=node.id&&S.doc.pages[node.id];
    if(node.locked&&S.space==="brain"){var lb=h("button","bk-pagelink","\u{1F512} "+(node.title||"Private page")+"  (password)");lb.type="button";lb.onclick=openVault;main.appendChild(lb)}
    else if(node.locked||!pg){main.appendChild(h("div","bk-pagelink locked","\u{1F512} "+(node.title||"Private page")))}
    else{var pl=h("button","bk-pagelink",(pg.icon||node.ic||"\u{1F4C4}")+"  "+(pg.title||"Untitled"));pl.type="button";pl.onclick=function(){go({page:node.id})};main.appendChild(pl)}}
  else if(t==="db"){main.appendChild(dbBox(node.id))}
  else if(t==="img"){
    var im=h("img","bk-img");im.src="/api/img/"+node.img;im.loading="lazy";im.alt="";im.onclick=function(){lightbox(node.img)};main.appendChild(im)}
  else if(t==="file"){main.appendChild(h("span","bk-chip","\u{1F4CE} "+(node.name||"File")))}
  else if(t==="link"){var a=h("a",null,node.url);if(/^https?:\/\//.test(node.url||"")){a.href=node.url;a.target="_blank";a.rel="noopener noreferrer"}main.appendChild(a)}
  return bk}
function contains(parent,child){var f=false;(function w(list){(list||[]).forEach(function(n){if(n===child)f=true;w(n.k);(n.cols||[]).forEach(function(c){w(c.k)})})})([parent]);return f&&parent!==child}
function clearDrop(){[].forEach.call(document.querySelectorAll(".drop-before,.drop-after"),function(n){n.classList.remove("drop-before","drop-after")})}

function textKey(e,node,el,list,up,ctx){
  if(SM&&slashKey(e))return;
  var i=list.indexOf(node);
  if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){
    e.preventDefault();
    var parts=splitAtCaret(el),t=node.t;
    var empty=!el.textContent;
    if(empty&&(t==="li"||t==="ol"||t==="todo")){
      if(up){outdent(node,list,up);return}
      node.t="paragraph";delete node.done;touch();redraw(node);return}
    if(t==="toggle"&&S.open.has(node)){
      node.k=node.k||[];var c0={t:"paragraph",r:parts[1]};node.k.unshift(c0);node.r=parts[0];touch();redraw(c0,"start");return}
    var same=(t==="li"||t==="ol"||t==="todo");
    var nb={t:same?t:"paragraph",r:parts[1]};if(t==="todo")nb.done=false;
    node.r=parts[0];
    if(node.k&&node.k.length&&same){nb.k=node.k;node.k=undefined}
    list.splice(i+1,0,nb);touch();redraw(nb,"start");return}
  if(e.key==="Backspace"&&atStart(el)){
    if(node.t!=="paragraph"){e.preventDefault();node.t="paragraph";delete node.done;delete node.ic;touch();redraw(node,"start");return}
    if(i>0){
      var prev=list[i-1];
      if(prev.t==="hr"||prev.t==="img"){e.preventDefault();list.splice(i-1,1);touch();redraw(node,"start");return}
      if(TEXTT[prev.t]&&prev.t!=="callout"){
        e.preventDefault();var at=plainOf(prev.r).length;
        prev.r=(prev.r||[]).concat(node.r||[]);
        if(node.k&&node.k.length){prev.k=(prev.k||[]).concat(node.k)}
        list.splice(i,1);touch();redraw(prev,at);return}}
    else if(up){e.preventDefault();outdent(node,list,up);return}
    else if(!el.textContent&&list.length>1){e.preventDefault();list.splice(i,1);touch();var nx=list[Math.max(0,i-1)];redraw(TEXTT[nx.t]?nx:null,"end");return}
    return}
  if(e.key==="Tab"){
    e.preventDefault();
    if(e.shiftKey){if(up)outdent(node,list,up);return}
    if(i>0&&TEXTT[list[i-1].t]){var p=list[i-1];p.k=p.k||[];list.splice(i,1);p.k.push(node);if(p.t==="toggle")S.open.add(p);touch();redraw(node,"end")}
    return}
  if(e.key==="ArrowUp"&&!e.shiftKey&&atStart(el)){e.preventDefault();focusAdj(el,-1);return}
  if(e.key==="ArrowDown"&&!e.shiftKey&&atEnd(el)){e.preventDefault();focusAdj(el,1);return}
  if((e.metaKey||e.ctrlKey)&&e.key==="Enter"&&node.t==="todo"){e.preventDefault();node.done=!node.done;touch();redraw(node,"end")}}
function outdent(node,list,up){
  list.splice(list.indexOf(node),1);if(!list.length&&up.node&&!up.col)up.node.k=undefined;
  var at=up.list.indexOf(up.node);up.list.splice(at+1,0,node);touch();redraw(node,"end")}
function shortcuts(node,el){
  if(node.t!=="paragraph")return;
  var tx=el.textContent.replace(/\u00a0/g," "),m;
  var set=function(t,extra){node.t=t;node.r=[];if(extra)for(var k in extra)node[k]=extra[k];touch();redraw(node,"start")};
  if((m=/^(#{1,3}) $/.exec(tx)))return set("h"+m[1].length);
  if(/^[-*] $/.test(tx))return set("li");
  if(/^1[.)] $/.test(tx))return set("ol");
  if(/^\[ ?\] $/.test(tx))return set("todo",{done:false});
  if(/^> $/.test(tx))return set("quote");
  if(tx==="```")return set("code",{x:""});
  if(tx==="---"){var list=(findHolder(node)||{}).list;if(list){var i=list.indexOf(node);list[i]={t:"hr"};var nb=newPara();list.splice(i+1,0,nb);touch();redraw(nb,"start")}}}

/* ---------- slash menu ---------- */
function slashCheck(node,el){
  var tx=el.textContent.replace(/\u00a0/g," "),m=/(^|\s)\/([\w ]{0,20})$/.exec(tx);
  if(SM&&SM.fromPlus&&SM.node===node){SM.q=tx;SM.idx=0;drawSlash();return}
  if(!m||caretOffset(el)<tx.length){if(SM)popClose();return}
  openSlash(node,el,m[2])}
function openSlash(node,el,q){
  if(!el)return;SM={node:node,el:el,q:q,idx:0,fromPlus:false};if(!/\//.test(el.textContent))SM.fromPlus=true;
  drawSlash()}
function slashItems(){var q=SM.q.toLowerCase().trim();return ITEMS.filter(function(it){return!q||it.l.toLowerCase().indexOf(q)>=0||it.k.indexOf(q)>=0})}
function drawSlash(){
  var items=slashItems();
  if(!items.length){var keep=SM;popClose();if(keep&&keep.q.length>8)return;SM=null;return}
  if(SM.idx>=items.length)SM.idx=0;var st=SM;
  var p=popOpen(SM.el,function(p){
    items.forEach(function(it,n){
      var b=h("button","sb-mi sb-slash"+(n===st.idx?" on":""));b.type="button";
      b.appendChild(h("b",null,it.l));b.appendChild(h("span",null,it.d));
      b.onmousedown=function(e){e.preventDefault()};b.onclick=function(){pickItem(st,it)};p.appendChild(b)})},"sb-slashpop");
  SM=st;POP.away=POP.away;var on=p.querySelector(".on");if(on)on.scrollIntoView({block:"nearest"})}
function slashKey(e){
  var items=slashItems();if(!items.length)return false;
  if(e.key==="ArrowDown"){e.preventDefault();SM.idx=(SM.idx+1)%items.length;var s=SM;drawSlash();return true}
  if(e.key==="ArrowUp"){e.preventDefault();SM.idx=(SM.idx-1+items.length)%items.length;drawSlash();return true}
  if(e.key==="Enter"||e.key==="Tab"){e.preventDefault();pickItem(SM,items[SM.idx]);return true}
  if(e.key==="Escape"){e.preventDefault();popClose();return true}
  return false}
function pickItem(st,it){
  var node=st.node,holder=findHolder(node);popClose();if(!holder)return;
  var list=holder.list,i=list.indexOf(node);
  var tx=(ELS.get(node)||{textContent:""}).textContent||"";
  var clean=st.fromPlus?"":tx.replace(/\u00a0/g," ").replace(/(^|\s)\/[\w ]{0,20}$/,"$1").replace(/\s+$/,"");
  var rest=[];if(clean)rest=[{x:clean}];
  var emptyBlock=!clean,t=it.t,nn;
  if(TEXTT[t]){
    if(emptyBlock&&TEXTT[node.t]){node.t=t;node.r=[];if(t==="todo")node.done=false;if(t==="callout")node.ic=node.ic||"\u{1F4A1}";touch();redraw(node,"start");return}
    nn={t:t,r:[]};if(t==="todo")nn.done=false;if(t==="callout")nn.ic="\u{1F4A1}"}
  else if(t==="hr")nn={t:"hr"};
  else if(t==="code")nn={t:"code",x:""};
  else if(t==="table")nn={t:"table",head:true,rowhead:false,rows:[[[],[],[]],[[],[],[]],[[],[],[]]]};
  else if(t==="cols"){nn={t:"cols",cols:[]};for(var c=0;c<it.n;c++)nn.cols.push({k:[newPara()]})}
  else if(t==="page"){var cur=S.loc.row?(S.loc.from||S.doc.root):S.loc.page;var pid=newPage(cur,"Untitled");nn={t:"page",id:pid,title:"Untitled"}}
  else if(t==="db"){nn={t:"db",id:newDb("")}}
  else if(t==="img"){
    node.r=rest;var f=h("input");f.type="file";f.accept="image/*";
    f.onchange=function(){if(!f.files[0])return;uploadImage(f.files[0]).then(function(name){list.splice(i+1,0,{t:"img",img:name});touch();redraw()}).catch(function(e){say("Could not add the image: "+e.message)})};
    touch();f.click();return}
  if(emptyBlock&&TEXTT[node.t]&&!(node.k&&node.k.length)){list[i]=nn}else{node.r=rest;list.splice(i+1,0,nn);i++}
  if(!TEXTT[t]&&(!list[i+1]||!TEXTT[list[i+1].t]))list.splice(i+1,0,newPara());
  touch();
  if(t==="page"){go({page:nn.id});return}
  redraw(TEXTT[t]?nn:list[i+1],"start")}

/* ---------- block menu ---------- */
function blockMenu(anchor,node,list,i,up){
  popOpen(anchor,function(p){
    menuItem(p,"Delete",function(){
      var heavy=false;walkNodes([node],function(x){if((x.t==="page"&&x.id&&S.doc.pages[x.id])||x.t==="db")heavy=true});
      if(heavy&&!confirm("This removes a page or table and everything inside it. Continue?"))return;
      var at=list.indexOf(node),snap=clone(node);list.splice(at,1);if(heavy)dropNode(node);touch();redraw();
      if(!heavy)say("Block deleted.",function(){list.splice(at,0,snap);touch();redraw()})});
    menuItem(p,"Duplicate",function(){
      if(node.t==="page"||node.t==="db"){say("Pages and databases cannot be duplicated here.");return}
      list.splice(i+1,0,clone(node));touch();redraw()});
    if(i>0)menuItem(p,"Move up",function(){list.splice(i,1);list.splice(i-1,0,node);touch();redraw()});
    if(i<list.length-1)menuItem(p,"Move down",function(){list.splice(i,1);list.splice(i+1,0,node);touch();redraw()});
    if(TEXTT[node.t]){
      p.appendChild(h("div","sb-mh","Turn into"));
      [["Text","paragraph"],["Heading 1","h1"],["Heading 2","h2"],["Heading 3","h3"],["Bulleted list","li"],["Numbered list","ol"],["To-do","todo"],["Toggle","toggle"],["Quote","quote"],["Callout","callout"]].forEach(function(x){
        menuItem(p,x[0],function(){node.t=x[1];if(x[1]==="todo")node.done=!!node.done;if(x[1]==="callout")node.ic=node.ic||"\u{1F4A1}";touch();redraw(node,"end")},node.t===x[1]?"on":"")});
      p.appendChild(h("div","sb-mh","Color"));
      var sw=h("div","sb-sw");
      var none=h("button","sb-swatch","A");none.type="button";none.title="Default";none.onclick=function(){popClose();delete node.co;touch();redraw()};sw.appendChild(none);
      COLN.forEach(function(c){var b=h("button","sb-swatch","A");b.type="button";b.style.color=TXT[c];b.title=c+" text";b.onclick=function(){popClose();node.co=c;touch();redraw()};sw.appendChild(b)});
      p.appendChild(sw);var sw2=h("div","sb-sw");
      COLN.forEach(function(c){var b=h("button","sb-swatch","");b.type="button";b.style.background=bgOf(c);b.title=c+" background";b.onclick=function(){popClose();node.co=c+"_background";touch();redraw()};sw2.appendChild(b)});
      p.appendChild(sw2)}
    if(node.t==="cols"){
      menuItem(p,"Add a column",function(){if(node.cols.length<5){node.cols.push({k:[newPara()]});touch();redraw()}});
      menuItem(p,"Remove last column",function(){if(node.cols.length>2){var l=node.cols.pop();node.cols[node.cols.length-1].k=node.cols[node.cols.length-1].k.concat(l.k);touch();redraw()}});
      menuItem(p,"Unwrap (keep the content)",function(){var all=[];node.cols.forEach(function(c){all=all.concat(c.k)});list.splice.apply(list,[i,1].concat(all));touch();redraw()})}})}

/* ---------- tables inside pages ---------- */
function tableEl(node){
  var wrap=h("div","bk-tw"),sc=h("div","bk-tscroll"),tb=h("table","bk-tbl"),focus={r:0,c:0};
  function build(){
    tb.textContent="";
    node.rows.forEach(function(row,ri){
      var tr=h("tr");
      row.forEach(function(cell,ci){
        var hd=(node.head&&ri===0)||(node.rowhead&&ci===0);var td=h(hd?"th":"td");
        var d=h("div","bk-cell");d.contentEditable="true";d.innerHTML=runsToHtml(cell);d.setAttribute("data-cell","1");
        d.addEventListener("focus",function(){focus={r:ri,c:ci};wrap.classList.add("on")});
        d.addEventListener("input",function(){node.rows[ri][ci]=runsFromEl(d);touch()});
        d.addEventListener("paste",function(e){e.preventDefault();document.execCommand("insertText",false,(e.clipboardData.getData("text/plain")||"").replace(/\r?\n/g," "))});
        d.addEventListener("keydown",function(e){
          var go2=function(r,c){var t=tb.rows[r]&&tb.rows[r].cells[c];if(t)placeCaret(t.firstChild,"end")};
          if(e.key==="Tab"){e.preventDefault();
            var r=ri,c=ci+(e.shiftKey?-1:1);if(c>=row.length){c=0;r++}if(c<0){r--;c=row.length-1}
            if(r>=node.rows.length){addRow();build()}if(r>=0)go2(r,c);return}
          if(e.key==="Enter"){e.preventDefault();if(ri+1>=node.rows.length){addRow();build()}go2(ri+1,ci)}});
        td.appendChild(d);tr.appendChild(td)});
      tb.appendChild(tr)})}
  function addRow(){node.rows.push(node.rows[0].map(function(){return[]}));touch()}
  build();sc.appendChild(tb);wrap.appendChild(sc);
  var bar=h("div","bk-tbar");
  function btn(label,fn){var b=h("button","sb-mini",label);b.type="button";b.onmousedown=function(e){e.preventDefault()};b.onclick=function(){fn();touch();build()};bar.appendChild(b)}
  btn("+ Row",addRow);
  btn("+ Column",function(){node.rows.forEach(function(r){r.push([])})});
  btn("− Row",function(){if(node.rows.length>1)node.rows.splice(Math.min(focus.r,node.rows.length-1),1)});
  btn("− Column",function(){if(node.rows[0].length>1)node.rows.forEach(function(r){r.splice(Math.min(focus.c,r.length-1),1)})});
  var hr=h("label","sb-mini"),c1=h("input");c1.type="checkbox";c1.checked=!!node.head;c1.onchange=function(){node.head=c1.checked;touch();build()};hr.appendChild(c1);hr.appendChild(document.createTextNode(" Header row"));bar.appendChild(hr);
  var hc=h("label","sb-mini"),c2=h("input");c2.type="checkbox";c2.checked=!!node.rowhead;c2.onchange=function(){node.rowhead=c2.checked;touch();build()};hc.appendChild(c2);hc.appendChild(document.createTextNode(" Header column"));bar.appendChild(hc);
  wrap.appendChild(bar);return wrap}

/* ---------- selection toolbar (bold, italic, underline, strike, code, link, colour) ---------- */
function ensureSelTb(){
  if(SELTB)return SELTB;
  var tb=h("div","sb-seltb");tb.onmousedown=function(e){if(e.target.tagName!=="INPUT")e.preventDefault()};
  function sync(){var a=document.activeElement;var t=a&&a.closest&&a.closest("[data-bk],[data-cell]");if(t){if(t._sync)t._sync();else t.dispatchEvent(new Event("input",{bubbles:true}))}}
  function b(label,title,fn,style){var x=h("button",null,label);x.type="button";x.title=title;if(style)x.style.cssText=style;x.onclick=function(){document.execCommand("styleWithCSS",false,true);fn();sync()};tb.appendChild(x);return x}
  document.execCommand("styleWithCSS",false,true);
  b("B","Bold",function(){document.execCommand("bold")},"font-weight:800");
  b("I","Italic",function(){document.execCommand("italic")},"font-style:italic");
  b("U","Underline",function(){document.execCommand("underline")},"text-decoration:underline");
  b("S","Strikethrough",function(){document.execCommand("strikeThrough")},"text-decoration:line-through");
  b("</>","Code",function(){var t=window.getSelection().toString();if(t)document.execCommand("insertHTML",false,"<code>"+esc(t)+"</code>")},"font-family:monospace");
  var link=b("Link","Add a link",function(){});
  link.onclick=function(){
    var saved=window.getSelection().getRangeAt(0).cloneRange();
    var inp=h("input","sb-linkin");inp.placeholder="Paste a link, press Enter";tb.appendChild(inp);inp.focus();
    inp.onkeydown=function(e){
      if(e.key==="Enter"){var u=inp.value.trim();if(u&&!/^(https?:|mailto:)/i.test(u))u="https://"+u;
        var s=window.getSelection();s.removeAllRanges();s.addRange(saved);if(u)document.execCommand("createLink",false,u);inp.remove();sync()}
      if(e.key==="Escape"){inp.remove()}}};
  var col=b("Color","Text and highlight colour",function(){});
  col.onclick=function(){
    var saved=window.getSelection().getRangeAt(0).cloneRange();
    popOpen(col,function(p){
      var restore=function(){var s=window.getSelection();s.removeAllRanges();s.addRange(saved)};
      p.appendChild(h("div","sb-mh","Text"));var sw=h("div","sb-sw");
      COLN.forEach(function(c){var x=h("button","sb-swatch","A");x.type="button";x.style.color=TXT[c];x.onmousedown=function(e){e.preventDefault()};x.onclick=function(){popClose();restore();document.execCommand("foreColor",false,TXT[c]);sync()};sw.appendChild(x)});p.appendChild(sw);
      p.appendChild(h("div","sb-mh","Highlight"));var sw2=h("div","sb-sw");
      COLN.forEach(function(c){var x=h("button","sb-swatch","");x.type="button";x.style.background=bgOf(c);x.onmousedown=function(e){e.preventDefault()};x.onclick=function(){popClose();restore();document.execCommand("hiliteColor",false,bgOf(c));sync()};sw2.appendChild(x)});p.appendChild(sw2);
      var cl=h("button","sb-mi","Clear formatting");cl.type="button";cl.onmousedown=function(e){e.preventDefault()};cl.onclick=function(){popClose();restore();document.execCommand("removeFormat");sync()};p.appendChild(cl)})};
  document.body.appendChild(tb);SELTB=tb;return tb}
var selTick=0;
document.addEventListener("selectionchange",function(){
  if(selTick)return;selTick=requestAnimationFrame(function(){
    selTick=0;
    if(!S.mount||!S.mount.isConnected)return;
    var s=window.getSelection(),tb=SELTB;
    var an=s.rangeCount&&s.anchorNode?(s.anchorNode.nodeType===1?s.anchorNode:s.anchorNode.parentElement):null;
    var ok=an&&!s.isCollapsed&&an.closest&&an.closest("[data-bk='1'],[data-cell]");
    if(!ok){if(tb&&!(document.activeElement&&tb.contains(document.activeElement)))tb.style.display="none";return}
    tb=ensureSelTb();var r=s.getRangeAt(0).getBoundingClientRect();
    tb.style.display="flex";tb.style.left=Math.max(8,Math.min(r.left+r.width/2-tb.offsetWidth/2,window.innerWidth-tb.offsetWidth-8))+"px";
    tb.style.top=Math.max(8,r.top-tb.offsetHeight-8)+"px"})});

/* ---------- images ---------- */
function shrink(file){
  return new Promise(function(res){
    if(!/^image\/(png|jpeg)$/.test(file.type)||file.size<1500000)return res(file);
    var img=new Image(),u=URL.createObjectURL(file);
    img.onload=function(){
      var k=Math.min(1,2200/Math.max(img.width,img.height)),c=document.createElement("canvas");c.width=Math.round(img.width*k);c.height=Math.round(img.height*k);
      c.getContext("2d").drawImage(img,0,0,c.width,c.height);URL.revokeObjectURL(u);
      c.toBlob(function(b){res(b||file)},"image/jpeg",.9)};
    img.onerror=function(){URL.revokeObjectURL(u);res(file)};img.src=u})}
function uploadImage(file){
  return shrink(file).then(function(blob){
    return fetch("/api/journal/image",{method:"POST",headers:{"X-Requested-With":"zones-dashboard","Content-Type":blob.type||"application/octet-stream"},body:blob})
      .then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||"upload failed");return j.name})})})}
function lightbox(name,onRemove){
  var o=h("div","sb-lb"),im=h("img");im.src="/api/img/"+name;o.appendChild(im);
  var x=h("button","sb-lbx","✕");x.type="button";x.onclick=function(){o.remove()};o.appendChild(x);
  if(onRemove){var rm=h("button","sb-lbr","Remove from this row");rm.type="button";rm.onclick=function(){o.remove();onRemove()};o.appendChild(rm)}
  o.onclick=function(e){if(e.target===o)o.remove()};document.body.appendChild(o)}

/* ---------- databases ---------- */
function rowOf(dbId,rowId){var db=S.doc.dbs[dbId];if(!db)return null;for(var i=0;i<db.rows.length;i++)if(db.rows[i].id===rowId)return db.rows[i];return null}
function propOf(db,id){return db.props.filter(function(p){return p.id===id})[0]}
function optColor(p,name){var o=p.options.filter(function(x){return x.name===name})[0];return o?o.color:"default"}
function chip(p,name){
  var c=h("span","ch ch-"+optColor(p,name),name);return c}
function cmpVal(p,v){
  if(v===undefined||v===null||v===""||(Array.isArray(v)&&!v.length))return null;
  if(p.type==="number")return+v;if(p.type==="checkbox")return v?1:0;if(p.type==="multi_select"||p.type==="files")return v.join(", ").toLowerCase();
  return String(v).toLowerCase()}
function matchFilter(db,f,row){
  var p=propOf(db,f.p);if(!p)return true;var v=row.v[p.id],e=cmpVal(p,v)===null,val=f.val||"";
  switch(f.op){
    case"empty":return e;case"not_empty":return!e;
    case"contains":return String(p.type==="multi_select"?(v||[]).join(", "):v||"").toLowerCase().indexOf(val.toLowerCase())>=0;
    case"not_contains":return String(p.type==="multi_select"?(v||[]).join(", "):v||"").toLowerCase().indexOf(val.toLowerCase())<0;
    case"is":
      if(p.type==="multi_select")return(v||[]).indexOf(val)>=0;if(p.type==="checkbox")return!!v===(val==="true");
      if(p.type==="number")return v!==undefined&&+v===+val;return String(v||"").toLowerCase()===val.toLowerCase();
    case"is_not":if(p.type==="multi_select")return(v||[]).indexOf(val)<0;return String(v||"").toLowerCase()!==val.toLowerCase();
    case"gt":return v!==undefined&&+v>+val;case"lt":return v!==undefined&&+v<+val;case"gte":return v!==undefined&&+v>=+val;case"lte":return v!==undefined&&+v<=+val;
    case"before":return!!v&&v<val;case"after":return!!v&&v>val;case"on_or_before":return!!v&&v<=val;case"on_or_after":return!!v&&v>=val}
  return true}
function opsFor(p){
  var base=[["empty","is empty"],["not_empty","is not empty"]];
  if(p.type==="number")return[["is","="],["gt",">"],["lt","<"],["gte","≥"],["lte","≤"]].concat(base);
  if(p.type==="date")return[["is","is"],["before","is before"],["after","is after"],["on_or_before","is on or before"],["on_or_after","is on or after"]].concat(base);
  if(p.type==="select"||p.type==="multi_select")return[["is","is"],["is_not","is not"]].concat(base);
  if(p.type==="checkbox")return[["is","is"]];
  if(p.type==="files")return base;
  return[["contains","contains"],["not_contains","does not contain"],["is","is"]].concat(base)}
function visibleRows(db,st){
  var rows=db.rows.slice(),q=(st.q||"").toLowerCase().trim();
  db.view.filters.forEach(function(f){rows=rows.filter(function(r){return matchFilter(db,f,r)})});
  if(q)rows=rows.filter(function(r){return db.props.some(function(p){var v=r.v[p.id];if(v===undefined||p.type==="files"||p.type==="checkbox")return false;return String(Array.isArray(v)?v.join(" "):v).toLowerCase().indexOf(q)>=0})});
  if(db.view.sort.length){
    rows=rows.map(function(r,i){return{r:r,i:i}});
    rows.sort(function(a,b){
      for(var k=0;k<db.view.sort.length;k++){
        var s=db.view.sort[k],p=propOf(db,s.p);if(!p)continue;
        var x=cmpVal(p,a.r.v[p.id]),y=cmpVal(p,b.r.v[p.id]);
        if(x===null&&y===null)continue;if(x===null)return 1;if(y===null)return-1;
        var c=typeof x==="number"?x-y:String(x).localeCompare(String(y),undefined,{numeric:true});
        if(c)return s.dir==="desc"?-c:c}
      return a.i-b.i});
    rows=rows.map(function(x){return x.r})}
  return rows}
function dbBox(id){
  var db=S.doc.dbs[id];if(!db)return h("div","sb-empty","This table was removed.");
  var st=S.dbSt[id]||(S.dbSt[id]={q:"",n:50});
  var box=h("div","sb-db");
  var top=h("div","sb-dbtop");
  var ti=h("input","sb-dbtitle");ti.placeholder="Untitled table";ti.value=db.title||"";ti.oninput=function(){db.title=ti.value;touch()};top.appendChild(ti);
  var bar=h("div","sb-dbbar");
  var bFilter=h("button","sb-tool"),bSort=h("button","sb-tool"),bProps=h("button","sb-tool","Properties");
  [bFilter,bSort,bProps].forEach(function(b){b.type="button"});
  var search=h("input","sb-search");search.placeholder="Search";search.value=st.q||"";
  var bNew=h("button","sb-new","New");bNew.type="button";
  bar.appendChild(search);bar.appendChild(bFilter);bar.appendChild(bSort);bar.appendChild(bProps);bar.appendChild(bNew);
  var wrap=h("div","sb-dbwrap"),foot=h("div","sb-dbfoot");
  box.appendChild(top);box.appendChild(bar);box.appendChild(wrap);box.appendChild(foot);
  function labels(){
    var nf=db.view.filters.length,ns=db.view.sort.length;
    bFilter.textContent=nf?"Filter · "+nf:"Filter";bSort.textContent=ns?"Sort · "+ns:"Sort";
    bFilter.classList.toggle("on",!!nf);bSort.classList.toggle("on",!!ns)}
  function full(){labels();draw()}
  function draw(){
    wrap.textContent="";var rows=visibleRows(db,st),props=db.props.filter(function(p){return db.view.hidden.indexOf(p.id)<0||p.type==="title"});
    var tbl=h("table","sb-dbt"),thead=h("thead"),hr=h("tr");
    props.forEach(function(p,n){
      var th=h("th",n===0?"first":""),b=h("button","sb-th");b.type="button";b.appendChild(h("span","sb-ti",TICON[p.type]));b.appendChild(h("span",null,p.name||"Untitled"));
      if(db.view.sort.some(function(s){return s.p===p.id})){var sd=db.view.sort.filter(function(s){return s.p===p.id})[0];b.appendChild(h("span","sb-sd",sd.dir==="desc"?"↓":"↑"))}
      b.onclick=function(){propMenu(b,db,p,full)};th.appendChild(b);th.style.minWidth=({title:260,text:220,select:150,multi_select:230,date:130,number:90,files:200,checkbox:70,url:200})[p.type]+"px";hr.appendChild(th)});
    var tha=h("th","add"),ba=h("button","sb-th","+");ba.type="button";ba.title="Add a property";ba.onclick=function(){addPropPop(ba,db,full)};tha.appendChild(ba);hr.appendChild(tha);
    thead.appendChild(hr);tbl.appendChild(thead);
    var tb=h("tbody");
    rows.slice(0,st.n).forEach(function(row){
      var tr=h("tr");
      props.forEach(function(p,n){
        var td=h("td",n===0?"first":"");td.appendChild(cellEditor(db,p,row,"table",function(){}));
        if(n===0){
          var mb=h("button","sb-rowmenu","⋯");mb.type="button";mb.title="Row options";mb.onclick=function(){rowMenu(mb,db,row,full)};td.insertBefore(mb,td.firstChild);
          var ob=h("button","sb-open","OPEN");ob.type="button";ob.onclick=function(){go({row:{db:id,row:row.id},from:S.loc.page||S.loc.from})};td.appendChild(ob)}
        tr.appendChild(td)});
      tr.appendChild(h("td","add"));tb.appendChild(tr)});
    var nr=h("tr","newrow"),nc=h("td");nc.colSpan=props.length+1;
    var nb=h("button","sb-newrow","+ New");nb.type="button";nb.onclick=function(){addRow()};nc.appendChild(nb);nr.appendChild(nc);tb.appendChild(nr);
    tbl.appendChild(tb);wrap.appendChild(tbl);
    foot.textContent="";
    var left=rows.length-st.n;
    foot.appendChild(h("span","sb-count",rows.length+(rows.length===1?" row":" rows")+(rows.length!==db.rows.length?" (of "+db.rows.length+")":"")));
    if(left>0){var more=h("button","sb-tool","Show "+Math.min(50,left)+" more ("+left+" left)");more.type="button";more.onclick=function(){st.n+=50;draw()};foot.appendChild(more);
      var all=h("button","sb-tool","Show all");all.type="button";all.onclick=function(){st.n=1e9;draw()};foot.appendChild(all)}}
  function addRow(){
    var row={id:"n"+uid(),v:{}};
    db.props.forEach(function(p){
      if(p.type==="date"&&/^date$/i.test(p.name))row.v[p.id]=today();
      if(p.type==="number"&&/^trade no\.?$/i.test(p.name)){var mx=0;db.rows.forEach(function(r){if(+r.v[p.id]>mx)mx=+r.v[p.id]});row.v[p.id]=mx+1}});
    db.view.filters.forEach(function(f){var p=propOf(db,f.p);if(p&&f.op==="is"&&f.val&&(p.type==="select"))row.v[p.id]=f.val});
    db.rows.unshift(row);touch();st.n=Math.max(st.n,50);full();
    var first=wrap.querySelector("tbody tr:first-child .sb-cell-t");if(first)placeCaret(first,"end")}
  search.oninput=function(){st.q=search.value;st.n=50;draw()};
  bNew.onclick=addRow;
  bFilter.onclick=function(){filterPop(bFilter,db,full)};bSort.onclick=function(){sortPop(bSort,db,full)};bProps.onclick=function(){propsPop(bProps,db,full)};
  labels();draw();return box}
function rowMenu(anchor,db,row,full){
  popOpen(anchor,function(p){
    menuItem(p,"Open as page",function(){go({row:{db:dbIdOf(db),row:row.id},from:S.loc.page||S.loc.from})});
    menuItem(p,"Duplicate",function(){var c=clone(row);c.id="n"+uid();db.rows.splice(db.rows.indexOf(row)+1,0,c);touch();full()});
    menuItem(p,"Delete",function(){var at=db.rows.indexOf(row);db.rows.splice(at,1);touch();full();say("Row deleted.",function(){db.rows.splice(at,0,row);touch();full()})},"danger")})}
function dbIdOf(db){return Object.keys(S.doc.dbs).filter(function(k){return S.doc.dbs[k]===db})[0]}

/* cell editors: used in the table and in the row page */
function cellEditor(db,p,row,mode,after){
  var v=row.v[p.id],t=p.type;
  var set=function(val){if(val===undefined||val===null||val===""||val===false||(Array.isArray(val)&&!val.length))delete row.v[p.id];else row.v[p.id]=val;touch();after()};
  if(t==="title"||t==="text"||t==="url"){
    var d=h("div","sb-cell-t"+(t==="title"?" ttl":""));d.contentEditable=("plaintextOnly" in d)?"plaintext-only":"true";d.textContent=v||"";
    var multi=mode==="page"&&t==="text";if(t==="title")d.setAttribute("data-ph","Untitled");
    d.addEventListener("input",function(){var x=d.textContent;if(!multi)x=x.replace(/\n/g," ");set(x)});
    d.addEventListener("keydown",function(e){if(e.key==="Enter"&&!(multi)){e.preventDefault();d.blur()}});
    d.addEventListener("paste",function(e){e.preventDefault();var tx=(e.clipboardData.getData("text/plain")||"");document.execCommand("insertText",false,multi?tx:tx.replace(/\s*\n\s*/g," "))});
    return d}
  if(t==="number"){
    var inp=h("input","sb-num");inp.value=v===undefined?"":v;inp.inputMode="decimal";
    inp.oninput=function(){var x=parseFloat(inp.value.replace(",","."));set(isNaN(x)?undefined:x)};return inp}
  if(t==="date"){var di=h("input","sb-date");di.type="date";di.value=v||"";di.onchange=function(){set(di.value)};return di}
  if(t==="checkbox"){var cb=h("input","sb-cbx");cb.type="checkbox";cb.checked=!!v;cb.onchange=function(){set(cb.checked)};return cb}
  if(t==="select"||t==="multi_select"){
    var ch=h("div","sb-chips"+(mode==="page"?" pg":""));ch.tabIndex=0;
    var paint=function(){
      ch.textContent="";var vals=t==="select"?(row.v[p.id]?[row.v[p.id]]:[]):(row.v[p.id]||[]);
      vals.forEach(function(x){ch.appendChild(chip(p,x))});if(!vals.length&&mode==="page")ch.appendChild(h("span","sb-ph","Empty"))};
    paint();
    var open=function(){selectPop(ch,db,p,row,function(){paint();after()})};
    ch.onclick=open;ch.onkeydown=function(e){if(e.key==="Enter"||e.key===" "){e.preventDefault();open()}};return ch}
  if(t==="files"){
    var fw=h("div","sb-files"+(mode==="page"?" pg":""));
    var draw=function(){
      fw.textContent="";(row.v[p.id]||[]).forEach(function(n){
        var im=h("img","sb-thumb");im.src="/api/img/"+n;im.loading="lazy";im.alt="";
        im.onclick=function(){lightbox(n,function(){row.v[p.id]=(row.v[p.id]||[]).filter(function(x){return x!==n});if(!row.v[p.id].length)delete row.v[p.id];touch();draw()})};fw.appendChild(im)});
      var ad=h("button","sb-addimg","+");ad.type="button";ad.title="Add a screenshot";
      var f=h("input");f.type="file";f.accept="image/*";f.multiple=true;f.style.display="none";
      ad.onclick=function(){f.click()};
      f.onchange=function(){var files=[].slice.call(f.files);f.value="";ad.textContent="...";
        Promise.all(files.map(uploadImage)).then(function(names){row.v[p.id]=(row.v[p.id]||[]).concat(names);touch();draw()}).catch(function(e){say("Could not add the image: "+e.message);draw()})};
      fw.appendChild(ad);fw.appendChild(f)};
    draw();return fw}
  return h("span")}

function selectPop(anchor,db,p,row,done){
  var multi=p.type==="multi_select";
  popOpen(anchor,function(pop){
    var input=h("input","sb-seli");input.placeholder=multi?"Search or create an option":"Search or create an option";pop.appendChild(input);
    var list=h("div","sb-sellist");pop.appendChild(list);
    var cur=function(){return multi?(row.v[p.id]||[]):(row.v[p.id]?[row.v[p.id]]:[])};
    var save=function(vals){if(!vals.length)delete row.v[p.id];else row.v[p.id]=multi?vals:vals[0];touch();done()};
    var pick=function(name){
      var vals=cur().slice(),at=vals.indexOf(name);
      if(multi){if(at>=0)vals.splice(at,1);else vals.push(name);save(vals);paint()}
      else{save(at>=0?[]:[name]);popClose()}};
    var paint=function(){
      list.textContent="";var q=input.value.trim().toLowerCase(),has=false;
      p.options.forEach(function(o){
        if(q&&o.name.toLowerCase().indexOf(q)<0)return;
        if(o.name.toLowerCase()===q)has=true;
        var r=h("div","sb-opt"+(cur().indexOf(o.name)>=0?" on":""));
        var c=h("span","ch ch-"+o.color,o.name);r.appendChild(c);
        var ed=h("button","sb-optx","⋯");ed.type="button";ed.title="Edit option";
        ed.onclick=function(e){e.stopPropagation();editOpt(o)};
        r.onclick=function(){pick(o.name)};r.appendChild(ed);list.appendChild(r)});
      if(q&&!has){var cr=h("div","sb-opt create");cr.appendChild(document.createTextNode("Create "));cr.appendChild(h("span","ch ch-default",input.value.trim()));
        cr.onclick=function(){create(input.value.trim())};list.appendChild(cr)}};
    var create=function(name){
      if(!name)return;var ex=p.options.filter(function(o){return o.name.toLowerCase()===name.toLowerCase()})[0];
      if(!ex){ex={name:name,color:["gray","brown","orange","yellow","green","blue","purple","pink","red"][p.options.length%9]};p.options.push(ex);touch()}
      input.value="";pick(ex.name)};
    var editOpt=function(o){
      list.textContent="";var nm=h("input","sb-seli");nm.value=o.name;list.appendChild(nm);
      var sw=h("div","sb-sw");["default"].concat(COLN).forEach(function(c){
        var b=h("button","sb-swatch ch-"+c,"");b.type="button";b.title=c;if(o.color===c)b.classList.add("sel");
        b.onclick=function(){o.color=c;touch();done();editOpt(o)};sw.appendChild(b)});list.appendChild(sw);
      var ok=h("button","sb-mi","Save name");ok.type="button";ok.onclick=function(){
        var nn=nm.value.trim();if(nn&&nn!==o.name&&!p.options.some(function(x){return x.name.toLowerCase()===nn.toLowerCase()})){
          db.rows.forEach(function(r){var v=r.v[p.id];if(multi&&v)r.v[p.id]=v.map(function(x){return x===o.name?nn:x});else if(v===o.name)r.v[p.id]=nn});o.name=nn;touch();done()}
        paint()};list.appendChild(ok);
      var dl=h("button","sb-mi danger","Delete option");dl.type="button";dl.onclick=function(){
        p.options=p.options.filter(function(x){return x!==o});
        db.rows.forEach(function(r){var v=r.v[p.id];if(multi&&v){r.v[p.id]=v.filter(function(x){return x!==o.name});if(!r.v[p.id].length)delete r.v[p.id]}else if(v===o.name)delete r.v[p.id]});
        touch();done();paint()};list.appendChild(dl);
      var bk=h("button","sb-mi","Back");bk.type="button";bk.onclick=paint;list.appendChild(bk)};
    input.oninput=paint;
    input.onkeydown=function(e){
      if(e.key==="Enter"){e.preventDefault();var q=input.value.trim().toLowerCase();
        var first=p.options.filter(function(o){return!q||o.name.toLowerCase().indexOf(q)>=0})[0];
        var exact=p.options.filter(function(o){return o.name.toLowerCase()===q})[0];
        if(exact)pick(exact.name);else if(q)create(input.value.trim());else if(first)pick(first.name)}
      if(e.key==="Escape"){popClose()}};
    paint();setTimeout(function(){input.focus()},0)},"sb-selpop")}

function propMenu(anchor,db,p,full){
  popOpen(anchor,function(pop){
    var isTitle=p.type==="title";
    var nm=h("input","sb-seli");nm.value=p.name;nm.oninput=function(){p.name=nm.value;touch()};
    nm.onkeydown=function(e){if(e.key==="Enter"){popClose();full()}};pop.appendChild(nm);
    if(!isTitle){
      var sel=h("select","sb-sel");Object.keys(TYPES).filter(function(k){return k!=="title"}).forEach(function(k){var o=h("option",null,TYPES[k]);o.value=k;if(k===p.type)o.selected=true;sel.appendChild(o)});
      sel.onchange=function(){convertProp(db,p,sel.value);touch();popClose();full()};pop.appendChild(sel)}
    menuItem(pop,"Sort ascending",function(){db.view.sort=[{p:p.id,dir:"asc"}];touch();full()});
    menuItem(pop,"Sort descending",function(){db.view.sort=[{p:p.id,dir:"desc"}];touch();full()});
    menuItem(pop,"Filter by this",function(){db.view.filters.push({p:p.id,op:opsFor(p)[0][0],val:""});touch();full();})
    if(!isTitle)menuItem(pop,"Hide",function(){db.view.hidden.push(p.id);touch();full()});
    if(!isTitle)menuItem(pop,"Delete property",function(){
      if(!confirm("Delete the property \""+p.name+"\" and its values in every row?"))return;
      db.props=db.props.filter(function(x){return x!==p});db.rows.forEach(function(r){delete r.v[p.id]});
      db.view.sort=db.view.sort.filter(function(s){return s.p!==p.id});db.view.filters=db.view.filters.filter(function(f){return f.p!==p.id});
      db.view.hidden=db.view.hidden.filter(function(x){return x!==p.id});touch();full()},"danger");
    setTimeout(function(){nm.select()},0)},"sb-propmenu")}
function convertProp(db,p,nt){
  var old=p.type;if(old===nt)return;
  var txt=function(v){return v===undefined?"":Array.isArray(v)?v.join(", "):String(v)};
  db.rows.forEach(function(r){
    var v=r.v[p.id];if(v===undefined)return;var out;
    if(nt==="text"||nt==="url")out=txt(v);
    else if(nt==="number"){var x=parseFloat(txt(v).replace(",","."));out=isNaN(x)?undefined:x}
    else if(nt==="select"){out=txt(v).split(",")[0].trim()}
    else if(nt==="multi_select"){out=old==="multi_select"?v:txt(v).split(",").map(function(s){return s.trim()}).filter(Boolean)}
    else if(nt==="date"){out=/^\d{4}-\d{2}-\d{2}$/.test(txt(v))?txt(v):undefined}
    else if(nt==="checkbox"){out=/^(true|yes|x|1|✓)$/i.test(txt(v))?true:undefined}
    else out=undefined;
    if(out===undefined||out===""||(Array.isArray(out)&&!out.length))delete r.v[p.id];else r.v[p.id]=out});
  p.type=nt;
  if(nt==="select"||nt==="multi_select"){
    var seen={};p.options.forEach(function(o){seen[o.name]=1});
    db.rows.forEach(function(r){var v=r.v[p.id];(Array.isArray(v)?v:v?[v]:[]).forEach(function(x){if(!seen[x]){seen[x]=1;p.options.push({name:x,color:["gray","brown","orange","yellow","green","blue","purple","pink","red"][p.options.length%9]})}})})}}
function addPropPop(anchor,db,full){
  popOpen(anchor,function(pop){
    var nm=h("input","sb-seli");nm.placeholder="Property name";pop.appendChild(nm);
    var sel=h("select","sb-sel");Object.keys(TYPES).filter(function(k){return k!=="title"}).forEach(function(k){var o=h("option",null,TYPES[k]);o.value=k;sel.appendChild(o)});pop.appendChild(sel);
    var ok=h("button","sb-mi on","Add property");ok.type="button";
    ok.onclick=function(){
      var name=nm.value.trim()||"Property",id=name.toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^_|_$/g,"")||"p";
      var base=id,n=2;while(propOf(db,id))id=base+"_"+n++;
      db.props.push({id:id,name:name,type:sel.value,options:[]});touch();popClose();full()};
    pop.appendChild(ok);nm.onkeydown=function(e){if(e.key==="Enter")ok.click()};setTimeout(function(){nm.focus()},0)},"sb-propmenu")}
function filterPop(anchor,db,full){
  popOpen(anchor,function(pop){
    var draw=function(){
      pop.textContent="";
      db.view.filters.forEach(function(f,n){
        var p=propOf(db,f.p)||db.props[0],row=h("div","sb-frow");
        var sp=h("select","sb-sel");db.props.forEach(function(x){var o=h("option",null,x.name);o.value=x.id;if(x.id===f.p)o.selected=true;sp.appendChild(o)});
        sp.onchange=function(){f.p=sp.value;f.op=opsFor(propOf(db,f.p))[0][0];f.val="";touch();full();draw()};row.appendChild(sp);
        var so=h("select","sb-sel");opsFor(p).forEach(function(o){var x=h("option",null,o[1]);x.value=o[0];if(o[0]===f.op)x.selected=true;so.appendChild(x)});
        so.onchange=function(){f.op=so.value;touch();full();draw()};row.appendChild(so);
        if(f.op!=="empty"&&f.op!=="not_empty"){
          var vi;
          if(p.type==="select"||p.type==="multi_select"){vi=h("select","sb-sel");var e0=h("option",null,"Choose...");e0.value="";vi.appendChild(e0);p.options.forEach(function(o){var x=h("option",null,o.name);x.value=o.name;if(o.name===f.val)x.selected=true;vi.appendChild(x)});vi.onchange=function(){f.val=vi.value;touch();full()}}
          else if(p.type==="checkbox"){vi=h("select","sb-sel");[["true","checked"],["false","not checked"]].forEach(function(o){var x=h("option",null,o[1]);x.value=o[0];if(o[0]===f.val)x.selected=true;vi.appendChild(x)});if(!f.val){f.val="true"}vi.onchange=function(){f.val=vi.value;touch();full()}}
          else{vi=h("input","sb-seli");vi.type=p.type==="date"?"date":"text";vi.value=f.val||"";vi.oninput=function(){f.val=vi.value;touch();full()}}
          row.appendChild(vi)}
        var rm=h("button","sb-x","✕");rm.type="button";rm.onclick=function(){db.view.filters.splice(n,1);touch();full();draw()};row.appendChild(rm);pop.appendChild(row)});
      var add=h("button","sb-mi","+ Add a filter");add.type="button";
      add.onclick=function(){var p=db.props[1]||db.props[0];db.view.filters.push({p:p.id,op:opsFor(p)[0][0],val:""});touch();full();draw()};pop.appendChild(add);
      if(db.view.filters.length){var cl=h("button","sb-mi danger","Clear all filters");cl.type="button";cl.onclick=function(){db.view.filters=[];touch();full();draw()};pop.appendChild(cl)}};
    draw()},"sb-filterpop")}
function sortPop(anchor,db,full){
  popOpen(anchor,function(pop){
    var draw=function(){
      pop.textContent="";
      db.view.sort.forEach(function(s,n){
        var row=h("div","sb-frow"),sp=h("select","sb-sel");
        db.props.forEach(function(x){var o=h("option",null,x.name);o.value=x.id;if(x.id===s.p)o.selected=true;sp.appendChild(o)});
        sp.onchange=function(){s.p=sp.value;touch();full()};row.appendChild(sp);
        var sd=h("select","sb-sel");[["asc","Ascending"],["desc","Descending"]].forEach(function(o){var x=h("option",null,o[1]);x.value=o[0];if(s.dir===o[0])x.selected=true;sd.appendChild(x)});
        sd.onchange=function(){s.dir=sd.value;touch();full()};row.appendChild(sd);
        var rm=h("button","sb-x","✕");rm.type="button";rm.onclick=function(){db.view.sort.splice(n,1);touch();full();draw()};row.appendChild(rm);pop.appendChild(row)});
      var add=h("button","sb-mi","+ Add a sort");add.type="button";
      add.onclick=function(){var used=db.view.sort.map(function(s){return s.p});var p=db.props.filter(function(x){return used.indexOf(x.id)<0})[0]||db.props[0];db.view.sort.push({p:p.id,dir:"asc"});touch();full();draw()};pop.appendChild(add)};
    draw()},"sb-filterpop")}
function propsPop(anchor,db,full){
  popOpen(anchor,function(pop){
    var draw=function(){
      pop.textContent="";
      db.props.forEach(function(p,n){
        var row=h("div","sb-prow"),lab=h("label",null);var cb=h("input");cb.type="checkbox";cb.checked=db.view.hidden.indexOf(p.id)<0;cb.disabled=p.type==="title";
        cb.onchange=function(){if(cb.checked)db.view.hidden=db.view.hidden.filter(function(x){return x!==p.id});else db.view.hidden.push(p.id);touch();full()};
        lab.appendChild(cb);lab.appendChild(document.createTextNode(" "+TICON[p.type]+"  "+(p.name||"Untitled")));row.appendChild(lab);
        var up=h("button","sb-x","↑"),dn=h("button","sb-x","↓");up.type=dn.type="button";
        up.onclick=function(){if(n>1){db.props.splice(n,1);db.props.splice(n-1,0,p);touch();full();draw()}};
        dn.onclick=function(){if(n<db.props.length-1&&p.type!=="title"){db.props.splice(n,1);db.props.splice(n+1,0,p);touch();full();draw()}};
        if(p.type!=="title"){row.appendChild(up);row.appendChild(dn)}pop.appendChild(row)})};
    draw()},"sb-filterpop")}

/* the row page */
function rowPage(body){
  var db=S.doc.dbs[S.loc.row.db],row=rowOf(S.loc.row.db,S.loc.row.row);
  if(!db||!row){S.loc={page:S.doc.root};return pageView(body)}
  var head=h("div","sb-head");
  var tp=db.props.filter(function(p){return p.type==="title"})[0];
  head.appendChild(titleEl(row.v[tp.id]||"","Untitled",function(v){if(v)row.v[tp.id]=v;else delete row.v[tp.id];touch()},function(){render()}));
  body.appendChild(head);
  var props=h("div","sb-props");
  db.props.forEach(function(p){
    if(p.type==="title")return;
    var r=h("div","sb-prop"),l=h("div","sb-plabel");l.appendChild(h("span","sb-ti",TICON[p.type]));l.appendChild(h("span",null,p.name));r.appendChild(l);
    var v=h("div","sb-pval");v.appendChild(cellEditor(db,p,row,"page",function(){}));r.appendChild(v);props.appendChild(r)});
  body.appendChild(props);
  var bar=h("div","sb-rowbar"),del=h("button","sb-link","Delete this row");del.type="button";
  del.onclick=function(){if(!confirm("Delete this row?"))return;var at=db.rows.indexOf(row);db.rows.splice(at,1);touch();go(S.loc.from?{page:S.loc.from}:{page:S.doc.root})};
  bar.appendChild(del);body.appendChild(bar);body.appendChild(h("hr","sb-rule"));
  row.nodes=row.nodes||[];
  var ed=h("div","sb-ed");body.appendChild(ed);editor(row.nodes,ed,null,{page:S.loc.from||S.doc.root,fill:true});
  ed.addEventListener("click",function(e){if(e.target===ed){var last=row.nodes[row.nodes.length-1];if(!last||!TEXTT[last.t]||plainOf(last.r)){row.nodes.push(newPara());touch();redraw(row.nodes[row.nodes.length-1])}else focusNode(last,"end")}})}

/* ---------- loading ---------- */
function show(mount){
  S.mount=mount;
  if(S.doc){render();return}
  mount.textContent="";mount.appendChild(h("div","sb-empty","Opening Second Brain. The first time it copies your Notion journal, which can take a few seconds..."));
  fetch("/api/brain").then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||"could not load");return j})}).then(function(j){
    S.doc=j.doc;S.rev=j.doc.rev||0;S.loc={page:S.doc.root};S.conflict=false;render();
    if(j.seeded){var rows=0;Object.keys(S.doc.dbs).forEach(function(k){rows+=S.doc.dbs[k].rows.length});say("Copied from your Notion: "+Object.keys(S.doc.pages).length+" pages and "+rows+" table rows. Edits here never change Notion.")}
  }).catch(function(e){mount.textContent="";var d=h("div","sb-empty","Second Brain could not open: "+e.message);var b=h("button","sb-tool","Try again");b.type="button";b.onclick=function(){show(mount)};d.appendChild(h("br"));d.appendChild(b);mount.appendChild(d)})}
function css(){
  if(document.getElementById("sb-style"))return;
  var l=h("link");l.id="sb-style";l.rel="stylesheet";l.href="/brain.css";document.head.appendChild(l)}
window.SecondBrain={show:function(m){css();show(m)},flush:flush,leave:function(){return leaveVault(true)},state:S};
})();
