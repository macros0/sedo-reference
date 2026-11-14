"use strict";
const catalog=window.SEDO_CATALOG;
const $=id=>document.getElementById(id);
const envNames={prod:"Продуктивный",test:"Тестовый"};
const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e};
const normalize=s=>String(s||"").toLocaleLowerCase("ru").replaceAll("ё","е");
if(catalog.checked_at){
 const dateText=new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'}).format(new Date(catalog.checked_at));
 document.querySelector('header small').textContent='Каталог проверен '+dateText+' МСК';
}
const messages=new Map();
for(const r of [...catalog.revisions,...catalog.registry]){
 if(r.revision_id&&r.display_revision_id&&r.display_revision_id!==r.revision_id)continue;
 if(!messages.has(r.number))messages.set(r.number,{number:r.number,titles:new Set(),modules:new Set(),rows:[],revisions:[]});
 const m=messages.get(r.number);m.titles.add(r.title);if(r.module)m.modules.add(r.module);
 if(r.revision_id)m.revisions.push(r);else m.rows.push(r);
}
const route=(number,env,rev,field)=>new URLSearchParams(Object.fromEntries(Object.entries({message:number,env,rev,field}).filter(([,v])=>v))).toString();
const go=(number,env,rev,field)=>{location.hash=route(number,env,rev,field)};
function anchor(text,hash,cls){const a=node("a",cls,text);a.href="#"+hash;return a}
function external(text,url){const a=node("a","source",text);a.href=url;a.target="_blank";a.rel="noopener";return a}
function documentDownload(r){const a=node("a","source","Скачать DOCX");a.href=r.document_download;a.download=r.document_filename;return a}
function appendSchemaOrigin(card,r){
 if(!r.schema_path)return;
 const file=r.schema_path.split("/").pop(), line=node("p","meta schema-origin","Первоисточник XSD · ");
 const origin=r.archive_url?.split("#")[0], service=r.schema_source_url;
 const filename=url=>decodeURIComponent(new URL(url).pathname.split("/").pop());
 const step=()=>line.append(document.createTextNode(" → "));
 if(r.schema_publication_page){
  line.append(external("Страница СФР ("+new URL(r.schema_publication_page).host+new URL(r.schema_publication_page).pathname+")",r.schema_publication_page));
  step();
 }
 if(r.schema_source_type==="wsdl"){
  if(service){line.append(external("WSDL шлюза СФР",service));step()}
  const digest=r.dependencies?.find(([name])=>name===file)?.[1];
  const original=r.schema_sources?.find(s=>digest&&s.kind==="xsd"&&s.sha256===digest);
  if(original)line.append(external(file,original.url));
  else line.append(document.createTextNode(file));
 }else{
  if(origin?.startsWith("manual:")){
   line.append(document.createTextNode("Загруженный архив "+filename(origin)));
  }else if(origin){
   const embedded=r.schema_source_type==="wsdl_bridge"||r.archive_url.includes("#embedded=");
   line.append(external((embedded?"Документ ":"Архив ")+filename(origin),origin));
   if(embedded){
    step();
    const inline=r.archive_url.includes("#schema-");
    const attachment=r.schema_source_type==="wsdl_bridge"?"":r.archive_name?.replace(/ \(из DOCX\)$/,"");
    line.append(document.createTextNode(inline?"Схема в тексте документа":attachment&&attachment!==file?"Вложение "+attachment:"Вложенная XSD"));
   }
  }
  step();
  if(r.schema_file_download){
   const link=node("a","source",r.schema_path);link.href=r.schema_file_download;link.download=r.schema_file_name||file;
   link.title="Скачать исходный файл XSD";line.append(link);
  }else line.append(document.createTextNode(r.schema_path));
  if(r.schema_source_type==="wsdl_bridge"&&service){
   line.append(document.createTextNode(" · Зависимости из "),external("WSDL шлюза СФР ("+new URL(service).hostname+")",service));
  }
 }
 if(!r.schema_publication_page)line.append(document.createTextNode(origin?.startsWith("manual:")?" · "+(r.manual_source?.label||"Ручная загрузка"):" · Страница публикации не сохранена"));
 card.append(line);
}
function appendSchemaReview(container,r){
 const review=r.schema_review;
 if(!review)return;
 const coverage="Проверено таблиц или строк: "+review.tables+" · XML-примеров: "+review.examples+".";
 const found=review.findings.length;
 const report=node("section","schema-review");
 container.append(report);
 if(!found){
  const status=review.tables||review.examples?"В проверенной части расхождений не найдено.":"Сопоставимых таблиц и XML-примеров для автоматической проверки не найдено.";
  report.append(node("p","meta",status+" "+coverage));
  for(const note of review.notes||[])report.append(node("p","meta",note));
  return;
 }
 const button=node("button","open","Показать замечания ("+found+")");
 button.type="button";button.setAttribute("aria-expanded","false");
 const details=node("div","schema-review-details");details.hidden=true;
 report.append(button,details);
 button.onclick=()=>{
  if(!details.children.length){
   details.append(node("p","meta",coverage));
   for(const note of review.notes||[])details.append(node("p","meta",note));
   for(const issue of review.findings){
    const item=node("article","schema-review-item");
    item.append(node("h3","",issue.kind));
    const evidence=node("dl","");
    for(const [label,value] of [["Место в спецификации",issue.location],["В спецификации",issue.spec],["Место в XSD",issue.xlocation],["В схеме",issue.xsd],["Последствие",issue.impact]]){
     if(value)evidence.append(node("dt","",label),node("dd","",value));
    }
    item.append(evidence);
    for(const target of window.SedoReviewJournal?.fieldLinks(r,issue)||[]){
     const a=node("a","source",target.text);a.href=target.href;item.append(node("br"),a);
    }
    details.append(item);
   }
  }
  details.hidden=!details.hidden;
  button.setAttribute("aria-expanded",String(!details.hidden));
  button.textContent=details.hidden?"Показать замечания ("+found+")":"Скрыть замечания ("+found+")";
 };
}
function schemaExportControl(r){
 if(r.schema_download){const a=node("a","source schema-download","Скачать XSD (ZIP)");a.href=r.schema_download+(r.schema_bundle_sha256?"?v="+r.schema_bundle_sha256.slice(0,16):"");a.download=r.schema_filename;a.title="XSD сообщения и зависимости для локальной валидации XML";return a}
 return r.schema_export_error?node("span","meta schema-export-unavailable",r.schema_export_error):null;
}
function documentName(r){return r.document_url?decodeURIComponent(new URL(r.document_url).pathname.split("/").pop()):(r.manual_source?.label||r.document_filename||"Файл владельца")}
function editionSources(r){return r.edition_sources||[r]}
function appendEditionSources(container,r){
 const list=node("div","edition-sources");
 for(const source of editionSources(r)){
  const item=node("div","edition-source");
  item.append(node("p","meta","Формат описан в спецификации · версия "+(source.document_version||"не указана")+" · "+date(source.label_date)));
  item.append(source.document_url?external(documentName(source),source.document_url):node("p","meta",documentName(source)));
  if(source.document_download)item.append(node("br"),documentDownload(source));
  if(source.listed_now===false)item.append(node("p","meta","Источник сохранён в истории"));
  const revision=catalog.revisions.find(row=>row.revision_id===source.revision_id)||r;
  appendSchemaOrigin(item,revision);
  appendSchemaReview(item,revision);
  list.append(item);
 }
 container.append(list);return list;
}
function revisionOrigin(r){return r.document_url?external("Спецификация СФР ↗",r.document_url):node("span","source","Ручная загрузка · "+(r.manual_source?.label||"Файл владельца"))}
function date(s){return s?s.replace(/\.(\d{2})$/,".20$1"):"Дата не указана"}
function title(m,env){return m.revisions.find(r=>!env||r.environment===env)?.title||m.rows.find(r=>!env||r.environment===env)?.title||[...m.titles][0]}
function label(r){return editionSources(r).length>1?envNames[r.environment]+" · схема · источников: "+editionSources(r).length:envNames[r.environment]+" · документ "+(r.document_version||"без версии")+" · "+date(r.label_date)+(r.source_type==="manual"?" · Ручная загрузка":"")}
function replacementNotice(m,env,compact=false){
 const options=window.SedoTransitions?.replacements(m.number,catalog,env)||[];if(!options.length)return null;
 const dated=options.every(info=>info.production_retired_at);
 const notice=node("aside","message-transition"+(compact?" compact":""));
 notice.append(node("strong","",dated?(compact?"Заменён в ГИС ЕЦП":"Устаревший тип сообщения · переход на ГИС ЕЦП"):"Новый формат ГИС ЕЦП"));
 if(!compact){
  notice.append(node("p","",options.length>1?"Для сообщения "+m.number+" в ГИС ЕЦП предусмотрены разные типы по назначению. Выберите нужный процесс. При переходе изменилась структура XML: используйте спецификацию выбранного типа.":"Сообщению "+m.number+" соответствует новый тип "+options[0].current+". При переходе изменилась структура XML: используйте спецификацию нового типа."));
 }
 for(const info of options){
  const entry=node("div","transition-option");
  if(!compact||options.length>1)entry.append(node("p","transition-scope",info.scope));
  entry.append(anchor(compact?"Новый тип "+info.current+" →":"Перейти к сообщению "+info.current,route(info.current,env),compact?"replacement-link":"open replacement-link"));
  if(!compact){
   if(info.production_retired_at)entry.append(node("p","meta","По уведомлению СФР в продуктивном контуре приём и отправка прежнего типа прекращены с "+observationDate(info.production_retired_at)+"."));
   entry.append(document.createTextNode(" · "),external("Источник СФР ↗",info.source_url));
  }
  notice.append(entry);
 }
 return notice;
}
function renderMessages(){
 const terms=normalize($("message-query").value.trim()).split(/\s+/).filter(Boolean),env=$("catalog-env").value;
 const candidates=[...messages.values()].filter(m=>{
  if(env&&![...m.rows,...m.revisions].some(r=>r.environment===env))return false;
  const text=normalize([m.number,...m.titles,...m.modules,...m.rows.map(r=>r.description)].join(" "));
  return terms.every(t=>text.includes(t));
 });
 const onlyCurrent=$('catalog-current-only').checked;
 const matches=candidates.filter(m=>!onlyCurrent||!window.SedoTransitions?.isSuperseded(m.number,catalog,env)).sort((a,b)=>Number(b.number===$("message-query").value.trim())-Number(a.number===$("message-query").value.trim())||Number(a.number)-Number(b.number));
 $("messages").replaceChildren();$("count").textContent="Найдено сообщений · "+matches.length+(onlyCurrent&&candidates.length>matches.length?" · Скрыто заменённых · "+(candidates.length-matches.length):"");
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
 if(!matches.length)$("messages").append(node("p","empty",onlyCurrent&&candidates.length?"Совпадений нет. Снимите флажок «Только актуальные», чтобы показать прежние типы сообщений.":"Совпадений нет. Попробуйте часть номера или названия."));
}
function renderVersions(m,env){
 const section=$("versions");section.replaceChildren(node("h1","","Сообщение "+m.number),node("p","",title(m,env)));
 const replacement=replacementNotice(m,env);if(replacement)section.append(replacement);
 appendRelatedComparisons(section,m,env);
 const ready=m.revisions.filter(r=>r.ready);
 if(ready.length>=2){const pair=comparisonDefaults(m);section.append(anchor("Сравнить редакции",comparisonRoute(m.number,pair.left,pair.right),"open"))}
 section.append(anchor("Журнал сообщения",updatesRoute(m.number,env),"message-updates"));
 const grid=node("div","versions-grid");section.append(grid);
 for(const e of ["prod","test"]){
  const panel=node("div","version-panel");panel.append(node("h2","",envNames[e]+" контур"));grid.append(panel);
  const revisions=m.revisions.filter(r=>r.environment===e).sort((a,b)=>date(b.label_date).split(".").reverse().join("").localeCompare(date(a.label_date).split(".").reverse().join("")));
  if(revisions.length){
   for(const r of revisions){
    const card=node("div","card");card.append(node("strong","",editionSources(r).length>1?"Редакция схемы · источников: "+editionSources(r).length:"Версия документа "+(r.document_version||"не указана")));
    if(r.ready)card.append(anchor("Открыть дерево",route(m.number,e,r.revision_id),"open"));
    else card.append(node("p","",r.status==="document_only"?"DOCX сохранён. Схема XSD не загружена.":"Дерево тегов ожидает проверки"));
    const schemaControl=schemaExportControl(r);if(schemaControl)card.append(schemaControl);
    if(r.validation_download)card.append(anchor('Проверить XML локально',route(m.number,e,r.revision_id)+'&validate=1','open validation-link'));
    appendEditionSources(card,r);
    if(!r.listed_now&&r.source_type!=="manual")card.append(node("p","meta","Редакция сохранена в истории"));
    panel.append(card)
   }
  }else{
   const rows=m.rows.filter(r=>r.environment===e);
   panel.append(node("p","",rows.length?"Доступно описание в перечне сообщений. Дерево схемы пока не сопоставлено.":"В собранных источниках этого контура сообщение не найдено."));
   for(const r of rows){
    panel.append(node("p","meta",r.module),node("p","",r.description||r.title));
    if(r.source_type==="module"){
     panel.append(node("p","meta","Отдельная спецификация модуля · версия "+(r.document_version||"не указана")+" · "+date(r.label_date)));
     if(!r.listed_now)panel.append(node("p","meta","Спецификация сохранена в истории"));
     if(r.document_download)panel.append(documentDownload(r),document.createTextNode(" · "));
    }
    panel.append(external(r.source_type==="module"?"Спецификация модуля СФР ↗":"Открыть официальный перечень ↗",r.source));
   }
  }
 }
}
function editionUrl(revision,field){return 'editions/'+revision.revision_id+'.html'+(revision.viewer_sha256?'?v='+revision.viewer_sha256.slice(0,16):'')+(field?'#'+new URLSearchParams({field}):'')}
function openValidationField(revision,path){
 const p=new URLSearchParams(location.hash.slice(1));
 if(p.get('message')!==revision.number||p.get('env')!==revision.environment||p.get('rev')!==revision.revision_id)return;
 p.set('field',path);history.replaceState(null,'','#'+p);
 const frame=$('frame');frame.src=editionUrl(revision,path);
 frame.contentWindow.postMessage?.({type:'sedo-select-field',revision:revision.revision_id,path},new URL(location.href).origin);
 frame.setAttribute('data-field',path);frame.scrollIntoView?.({block:'start',behavior:'smooth'});
}
let loadedRevision=null;
function renderRoute(){
 window.SedoValidator?.dispose();
 const journal=document.getElementById("review-journal");if(journal)journal.hidden=true;
 const p=new URLSearchParams(location.hash.slice(1)),number=p.get("message"),env=p.get("env"),rev=p.get("rev"),field=p.get("field");
 for(const id of ["catalog","versions","viewer","comparison","updates","import-help","error"])$(id).hidden=true;
 $("crumbs").replaceChildren(anchor("Все сообщения",""));
 if(p.has("remarks")){
  let section=document.getElementById("review-journal");
  if(!section){section=node("section");section.id="review-journal";$("versions").after(section)}
  section.hidden=false;$("crumbs").append(node("span","","/"),node("span","","Журнал замечаний"));
  document.title="Журнал замечаний · СЭДО";window.SedoReviewJournal.mount(section,catalog,p);return;
 }
 if(p.has("import")){$("import-help").hidden=false;document.title="Ручная загрузка · СЭДО";return}
 if(p.has("updates")){$("updates").hidden=false;$("crumbs").append(node("span","","/"),node("span","","Журнал обновлений"));document.title="Журнал обновлений · СЭДО";renderUpdates(p);return}
 if(!number){$("catalog").hidden=false;renderMessages();return}
 const m=messages.get(number);
 if(!m){$("error").hidden=false;$("error").replaceChildren(node("h1","","Сообщение не найдено"),node("p","","Выберите сообщение из каталога."));return}
 $("crumbs").append(node("span","","/"),anchor(number,route(number,env)));
 if(p.has("compare")){$("comparison").hidden=false;$("crumbs").append(node("span","","/"),node("span","","Сравнение"));document.title="Сравнение СЭДО "+number;renderComparison(m,p);return}
 if(!rev){$("versions").hidden=false;renderVersions(m,env);return}
 const r=catalog.revisions.find(r=>r.number===number&&r.revision_id===rev&&r.environment===env);
 if(!r||!r.ready){$("error").hidden=false;$("error").replaceChildren(node("h1","","Редакция недоступна"),anchor("Посмотреть доступные редакции",route(number,env)));return}
 $("viewer").hidden=false;$("crumbs").append(node("span","","/"),node("span","",label(r)));
 const warning=$("viewer-replacement");if(warning){warning.replaceChildren();const replacement=replacementNotice(m,env);if(replacement)warning.append(replacement);appendRelatedComparisons(warning,m,env)}
 $("view-title").textContent="Сообщение "+number;$("view-desc").textContent=r.title;
 const grouped=editionSources(r).length>1;
 $("doc-source").hidden=!r.document_url||grouped;if(r.document_url){$("doc-source").href=r.document_url;$("doc-source").textContent="Источник версии: "+documentName(r);}
 $("manual-source").hidden=r.source_type!=="manual";$("manual-source").textContent="Ручная загрузка · "+(r.manual_source?.label||"Файл владельца");
 $("doc-download").href=r.document_download;$("doc-download").download=r.document_filename;
 $("doc-download").hidden=grouped;
 document.getElementById("xsd-from-viewer")?.remove();const schemaControl=schemaExportControl(r);if(schemaControl){schemaControl.id="xsd-from-viewer";$("doc-download").after(schemaControl)}
 document.getElementById('validate-from-viewer')?.remove();
 let validationHost=$('validation-host');if(!validationHost){validationHost=node('div','validation-host');validationHost.id='validation-host';$('frame').before(validationHost)}
 if(r.validation_download){const button=node('button','open validation-open','Проверить XML локально');button.id='validate-from-viewer';button.type='button';button.onclick=()=>window.SedoValidator?.mount(r,validationHost,path=>openValidationField(r,path));$('doc-download').after(button);if(p.has('validate'))button.onclick()}
 let sourceBox=$("viewer-sources");if(!sourceBox){sourceBox=node("div","viewer-sources");sourceBox.id="viewer-sources";$("frame").before(sourceBox)}
 sourceBox.replaceChildren();sourceBox.hidden=false;
  if(grouped){sourceBox.append(node("h2","","Источники этой схемы"));appendEditionSources(sourceBox,r)}
  else{appendSchemaOrigin(sourceBox,r);appendSchemaReview(sourceBox,r)}
 document.getElementById("compare-from-viewer")?.remove();
 const pair=comparisonDefaults(m),other=m.revisions.find(x=>x.ready&&x.revision_id!==(r.display_revision_id||rev));
 if(other){const link=anchor("Сравнить с другой редакцией",comparisonRoute(number,rev,pair.right===(r.display_revision_id||rev)?other.revision_id:pair.right),"source");link.id="compare-from-viewer";$("doc-source").after(link)}
 const select=$("revision-switch");select.replaceChildren();
 for(const x of m.revisions.filter(r=>r.ready)){
  const duplicates=m.revisions.filter(y=>y.ready&&label(y)===label(x)).length>1;
  select.add(new Option(label(x)+(duplicates?' · '+x.revision_id.slice(0,8):''),x.revision_id));
 }
 select.value=r.display_revision_id||rev;select.onchange=()=>{const x=m.revisions.find(r=>r.revision_id===select.value);go(number,x.environment,x.revision_id)};
 const src=editionUrl(r,field);
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
$("message-query").oninput=renderMessages;$("catalog-env").onchange=renderMessages;$("catalog-current-only").checked=true;$("catalog-current-only").onchange=renderMessages;
window.addEventListener("hashchange",renderRoute);
renderRoute();

