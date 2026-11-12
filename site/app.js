"use strict";
const catalog=window.SEDO_CATALOG;
const $=id=>document.getElementById(id);
const envNames={prod:"Продуктивный",test:"Тестовый"};
const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e};
const normalize=s=>String(s||"").toLocaleLowerCase("ru").replaceAll("ё","е");
if(catalog.checked_at){
 const dateText=new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'}).format(new Date(catalog.checked_at));
 document.querySelector('header small').textContent='Проверено '+dateText+' МСК';
}
const messages=new Map();
for(const r of [...catalog.revisions,...catalog.registry]){
 if(!messages.has(r.number))messages.set(r.number,{number:r.number,titles:new Set(),modules:new Set(),rows:[],revisions:[]});
 const m=messages.get(r.number);m.titles.add(r.title);if(r.module)m.modules.add(r.module);
 if(r.revision_id)m.revisions.push(r);else m.rows.push(r);
}
const route=(number,env,rev,field)=>new URLSearchParams(Object.fromEntries(Object.entries({message:number,env,rev,field}).filter(([,v])=>v))).toString();
const go=(number,env,rev,field)=>{location.hash=route(number,env,rev,field)};
function anchor(text,hash,cls){const a=node("a",cls,text);a.href="#"+hash;return a}
function external(text,url){const a=node("a","source",text);a.href=url;a.target="_blank";a.rel="noopener";return a}
function documentDownload(r){const a=node("a","source","Скачать DOCX");a.href=r.document_download;a.download=r.document_filename;return a}
function revisionOrigin(r){return r.document_url?external("Спецификация СФР ↗",r.document_url):node("span","source","Ручная загрузка · "+(r.manual_source?.label||"Файл владельца"))}
function date(s){return s?s.replace(/\.(\d{2})$/,".20$1"):"Дата не указана"}
function title(m,env){return m.revisions.find(r=>!env||r.environment===env)?.title||m.rows.find(r=>!env||r.environment===env)?.title||[...m.titles][0]}
function label(r){return envNames[r.environment]+" · "+(r.document_version||"Версия не указана")+" · "+date(r.label_date)+(r.source_type==="manual"?" · Ручная загрузка":"")}
function replacementNotice(m,env,compact=false){
 const info=window.SedoTransitions?.replacement(m.number,catalog,env);if(!info)return null;
 const notice=node("aside","message-transition"+(compact?" compact":""));
 notice.append(node("strong","",compact?"Заменён в ГИС ЕЦП":"Устаревший тип сообщения · переход на "+info.platform));
 if(!compact){
  notice.append(node("p","","Сообщение "+info.previous+" заменено типом "+info.current+". При переходе изменилась структура XML: используйте спецификацию нового типа."));
  notice.append(node("p","meta","По уведомлению СФР в продуктивном контуре приём и отправка прежнего типа прекращены с "+observationDate(info.production_retired_at)+"."));
 }
 notice.append(anchor(compact?"Новый тип "+info.current+" →":"Перейти к сообщению "+info.current,route(info.current,env),compact?"replacement-link":"open replacement-link"));
 if(!compact){notice.append(document.createTextNode(" · "),external("Уведомление СФР ↗",info.source_url))}
 return notice;
}
function renderMessages(){
 const terms=normalize($("message-query").value.trim()).split(/\s+/).filter(Boolean),env=$("catalog-env").value;
 const matches=[...messages.values()].filter(m=>{
  if(env&&![...m.rows,...m.revisions].some(r=>r.environment===env))return false;
  const text=normalize([m.number,...m.titles,...m.modules,...m.rows.map(r=>r.description)].join(" "));
  return terms.every(t=>text.includes(t));
 }).sort((a,b)=>Number(b.number===$("message-query").value.trim())-Number(a.number===$("message-query").value.trim())||Number(a.number)-Number(b.number));
 $("messages").replaceChildren();$("count").textContent="Найдено сообщений · "+matches.length;
 for(const m of matches){
  const card=node("article","card message-card");card.dataset.number=m.number;
  const a=anchor("",route(m.number,env),"message-main");
  a.append(node("span","number",m.number),node("strong","",title(m,env)));
  if(m.modules.size)a.append(node("span","meta",[...m.modules].join(" · ")));
  const badges=node("div","badges");
  for(const e of ["prod","test"])if([...m.rows,...m.revisions].some(r=>r.environment===e))badges.append(node("span","badge "+e,envNames[e]));
  a.append(badges,node("span","meta",m.revisions.some(r=>r.ready)?"Дерево тегов · "+m.revisions.filter(r=>r.ready).length+" редакции":"Документация"));
  card.append(a);const replacement=replacementNotice(m,env,true);if(replacement)card.append(replacement);$("messages").append(card)
 }
 if(!matches.length)$("messages").append(node("p","empty","Совпадений нет. Попробуйте часть номера или названия."));
}
function renderVersions(m,env){
 const section=$("versions");section.replaceChildren(node("h1","","Сообщение "+m.number),node("p","",title(m,env)));
 const replacement=replacementNotice(m,env);if(replacement)section.append(replacement);
 const ready=m.revisions.filter(r=>r.ready);
 if(ready.length>=2){const pair=comparisonDefaults(m);section.append(anchor("Сравнить редакции",comparisonRoute(m.number,pair.left,pair.right),"open"))}
 section.append(anchor("Журнал сообщения",updatesRoute(m.number,env),"message-updates"));
 const grid=node("div","versions-grid");section.append(grid);
 for(const e of ["prod","test"]){
  const panel=node("div","version-panel");panel.append(node("h2","",envNames[e]+" контур"));grid.append(panel);
  const revisions=m.revisions.filter(r=>r.environment===e).sort((a,b)=>date(b.label_date).split(".").reverse().join("").localeCompare(date(a.label_date).split(".").reverse().join("")));
  if(revisions.length){
   for(const r of revisions){
    const card=node("div","card");card.append(node("strong","","Версия "+(r.document_version||"не указана")),node("p","",date(r.label_date)));
    if(r.ready)card.append(anchor("Открыть дерево",route(m.number,e,r.revision_id),"open"));
    else card.append(node("p","",r.status==="document_only"?"DOCX сохранён. Схема XSD не загружена.":"Схема ожидает проверки"));
    card.append(node("br"),documentDownload(r),document.createTextNode(" · "),revisionOrigin(r));
    card.append(node("p","meta","Редакция "+r.revision_id.slice(0,8)));
    if(!r.listed_now&&r.source_type!=="manual")card.append(node("p","meta","Редакция сохранена в истории"));
    panel.append(card)
   }
  }else{
   const rows=m.rows.filter(r=>r.environment===e);
   panel.append(node("p","",rows.length?"Доступно описание в перечне сообщений. Дерево схемы пока не сопоставлено.":"В собранных источниках этого контура сообщение не найдено."));
   for(const r of rows){panel.append(node("p","meta",r.module),node("p","",r.description||r.title),external("Открыть официальный перечень ↗",r.source))}
  }
 }
}
let loadedRevision=null;
function renderRoute(){
 const p=new URLSearchParams(location.hash.slice(1)),number=p.get("message"),env=p.get("env"),rev=p.get("rev"),field=p.get("field");
 for(const id of ["catalog","versions","viewer","comparison","updates","import-help","error"])$(id).hidden=true;
 $("crumbs").replaceChildren(anchor("Все сообщения",""));
 if(p.has("import")){$("import-help").hidden=false;document.title="Ручная загрузка · СЭДО";return}
 if(p.has("updates")){$("updates").hidden=false;$("crumbs").append(node("span","","/"),node("span","","Журнал обновлений"));document.title="Журнал обновлений · СЭДО";renderUpdates(p);return}
 if(!number){$("catalog").hidden=false;renderMessages();return}
 const m=messages.get(number);
 if(!m){$("error").hidden=false;$("error").replaceChildren(node("h1","","Сообщение не найдено"),node("p","","Выберите сообщение из каталога."));return}
 $("crumbs").append(node("span","","/"),anchor(number,route(number,env)));
 if(p.has("compare")){$("comparison").hidden=false;$("crumbs").append(node("span","","/"),node("span","","Сравнение"));document.title="Сравнение СЭДО "+number;renderComparison(m,p);return}
 if(!rev){$("versions").hidden=false;renderVersions(m,env);return}
 const r=m.revisions.find(r=>r.revision_id===rev&&r.environment===env);
 if(!r||!r.ready){$("error").hidden=false;$("error").replaceChildren(node("h1","","Редакция недоступна"),anchor("Посмотреть доступные редакции",route(number,env)));return}
 $("viewer").hidden=false;$("crumbs").append(node("span","","/"),node("span","",label(r)));
 const warning=$("viewer-replacement");if(warning){warning.replaceChildren();const replacement=replacementNotice(m,env);if(replacement)warning.append(replacement)}
 $("view-title").textContent="Сообщение "+number;$("view-desc").textContent=r.title;
 $("doc-source").hidden=!r.document_url;if(r.document_url)$("doc-source").href=r.document_url;
 $("manual-source").hidden=r.source_type!=="manual";$("manual-source").textContent="Ручная загрузка · "+(r.manual_source?.label||"Файл владельца");
 $("doc-download").href=r.document_download;$("doc-download").download=r.document_filename;
 document.getElementById("compare-from-viewer")?.remove();
 const pair=comparisonDefaults(m),other=m.revisions.find(x=>x.ready&&x.revision_id!==rev);
 if(other){const link=anchor("Сравнить с другой редакцией",comparisonRoute(number,rev,pair.right===rev?other.revision_id:pair.right),"source");link.id="compare-from-viewer";$("doc-source").after(link)}
 const select=$("revision-switch");select.replaceChildren();
 for(const x of m.revisions.filter(r=>r.ready)){
  const duplicates=m.revisions.filter(y=>y.ready&&label(y)===label(x)).length>1;
  select.add(new Option(label(x)+(duplicates?' · '+x.revision_id.slice(0,8):''),x.revision_id));
 }
 select.value=rev;select.onchange=()=>{const x=m.revisions.find(r=>r.revision_id===select.value);go(number,x.environment,x.revision_id)};
 const src="editions/"+rev+".html"+(field?"#"+new URLSearchParams({field}):"");
 if(loadedRevision!==rev||$("frame").getAttribute("src")!==src){$("frame").src=src;loadedRevision=rev}
 document.title="СЭДО "+number+" · "+label(r);
}
window.addEventListener("message",e=>{
 const p=new URLSearchParams(location.hash.slice(1));
 if(e.source!==$("frame").contentWindow||e.data?.type!=="sedo-field"||e.data.revision!==p.get("rev")||typeof e.data.path!=="string")return;
 p.set("field",e.data.path);history.replaceState(null,"","#"+p);
 // Keep parent routing in sync without reloading the embedded field viewer.
 $("frame").setAttribute("data-field",e.data.path);
});
$("message-query").oninput=renderMessages;$("catalog-env").onchange=renderMessages;
window.addEventListener("hashchange",renderRoute);
renderRoute();

