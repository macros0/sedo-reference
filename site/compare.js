"use strict";
const comparisonData=new Map();
let comparisonRequest=0;
function loadComparisonData(revision){
 if(!comparisonData.has(revision)){
  const promise=new Promise((resolve,reject)=>{
   window.SEDO_FIELDS=window.SEDO_FIELDS||{};
   const script=document.createElement("script");
   script.src="fields/"+revision+".js";
   script.onload=()=>{script.remove();const fields=window.SEDO_FIELDS[revision];fields?resolve(fields):reject(Error("В файле редакции нет дерева тегов."))};
   script.onerror=()=>{script.remove();comparisonData.delete(revision);reject(Error("Не удалось загрузить дерево. Проверьте, что папка fields находится рядом с index.html."))};
   document.head.append(script);
  });
  comparisonData.set(revision,promise);
 }
 return comparisonData.get(revision);
}
function comparisonRoute(number,left,right){return new URLSearchParams({message:number,compare:"1",left,right}).toString()}
function comparisonLabel(r){return "№ "+r.number+" · "+label(r)+" · "+r.revision_id.slice(0,8)}
function canCompareTypes(a,b){return a===b||(catalog.message_transitions?.pairs||[]).some(p=>p.previous===a&&p.current===b||p.previous===b&&p.current===a)}
function comparisonPool(m){
 const numbers=new Set([m.number]);
 for(const pair of catalog.message_transitions?.pairs||[]){if(pair.previous===m.number)numbers.add(pair.current);if(pair.current===m.number)numbers.add(pair.previous)}
 return [...numbers].flatMap(number=>messages.get(number)?.revisions||[]).filter(r=>r.ready&&(!r.display_revision_id||r.display_revision_id===r.revision_id));
}
function comparisonRevision(pool,id){return pool.find(r=>r.revision_id===id)||catalog.revisions.find(r=>r.revision_id===id&&r.ready&&pool.some(x=>x.number===r.number))}
function crossTypePair(m,target,environment){
 const pool=comparisonPool(m),latest=rows=>[...rows].sort((a,b)=>date(b.label_date).split(".").reverse().join("").localeCompare(date(a.label_date).split(".").reverse().join("")))[0];
 for(const env of environment?[environment]:["prod","test"]){
  const left=latest(pool.filter(r=>r.number===m.number&&r.environment===env)),right=latest(pool.filter(r=>r.number===target&&r.environment===env));
  if(left&&right)return {left:left.revision_id,right:right.revision_id};
 }
 return null;
}
function appendRelatedComparisons(container,m,env){
 const targets=[...new Set(comparisonPool(m).map(r=>r.number))].filter(n=>n!==m.number),links=node("div","related-comparisons");
 for(const target of targets){const pair=crossTypePair(m,target,env);if(pair)links.append(anchor("Сравнить "+m.number+" → "+target,comparisonRoute(m.number,pair.left,pair.right),"source"))}
 if(links.childNodes.length)container.append(links);
}
function comparisonDefaults(m){
 const key=r=>r.label_date?date(r.label_date).split('.').reverse().join(''):'';
 const ready=m.revisions.filter(r=>r.ready).sort((a,b)=>key(b).localeCompare(key(a))||a.revision_id.localeCompare(b.revision_id));
 const prod=ready.find(r=>r.environment==='prod'),test=ready.find(r=>r.environment==='test');
 const right=test||prod,left=prod&&test?prod:ready.find(r=>r.revision_id!==right?.revision_id)||right;
 return {left:left?.revision_id,right:right?.revision_id};
}
function formatDifference(value){
 if(value===undefined||value===null||value==="")return "Не указано";
 if(Array.isArray(value))return value.length?value.map(v=>Array.isArray(v)?v.join(" — "):v&&typeof v==="object"?JSON.stringify(v,null,2):String(v)).join("\n"):"Без ограничений в этом свойстве";
 if(typeof value==="object")return Object.keys(value).length?Object.entries(value).map(([k,v])=>k+" = "+v).join("\n"):"Без дополнительных свойств";
 return String(value);
}
async function renderComparison(m,params,applied=false,view=null){
 const comparisonHash=location.hash,request=++comparisonRequest,section=$("comparison");section.replaceChildren(node("h1","","Сравнение сообщения "+m.number),node("p","",title(m)));
 const defaults=comparisonDefaults(m),leftId=params.get("left")||defaults.left,rightId=params.get("right")||defaults.right;
 const ready=comparisonPool(m),left=comparisonRevision(ready,leftId),right=comparisonRevision(ready,rightId);
 if(!left||!right){section.append(node("p","empty","Выбранная редакция недоступна для сравнения."));return false}
 if(left.number!==right.number){section.querySelector("h1").textContent="Сравнение сообщений "+left.number+" → "+right.number;section.append(node("p","meta","Сравниваются разные XML-форматы. Подтверждённые ручные пары учитываются отдельно; остальные теги сопоставляются по полным путям, включая имя корня."))}
 const controls=node("div","compare-controls"),selectors=[];
 for(const [caption,selected] of [["Было",left],["Стало",right]]){
  const wrap=node("label","",caption),select=node("select");
  for(const r of ready)select.add(new Option(comparisonLabel(r),r.revision_id));
  select.value=selected.display_revision_id||selected.revision_id;wrap.append(select);controls.append(wrap);selectors.push(select);
 }
 const switchSides=node("button","swap","Поменять местами");switchSides.type="button";
 switchSides.onclick=()=>{location.hash=comparisonRoute(m.number,rightId,leftId)};controls.append(switchSides);section.append(controls);
 selectors.forEach(select=>select.onchange=()=>{location.hash=comparisonRoute(m.number,selectors[0].value,selectors[1].value)});
 if(!canCompareTypes(left.number,right.number)){section.append(node("p","empty","Для этих типов нет подтверждённого соответствия. Выберите прежний тип и его новый формат либо две редакции одного типа."));return false}
 const sources=node("div","compare-sources");
 for(const [caption,r] of [["Было",left],["Стало",right]]){
  const box=node("div");box.append(node("strong","",caption+" · "+comparisonLabel(r)),node("br"),anchor("Открыть дерево",route(r.number,r.environment,r.revision_id),"source"));if(r.schema_download||r.schema_export_error){const schemaControl=schemaExportControl(r);if(schemaControl)box.append(document.createTextNode(" · "),schemaControl)}appendEditionSources(box,r);sources.append(box)
 }
 section.append(sources);
 const state=node("p","empty","Сравниваем деревья…");section.append(state);
 try{
  const [a,b]=await Promise.all([loadComparisonData(leftId),loadComparisonData(rightId)]);
  if(request!==comparisonRequest||!new URLSearchParams(location.hash.slice(1)).has("compare"))return false;
  const mappingOptions={left,right,before:a,after:b,isCurrent:()=>request===comparisonRequest&&location.hash===comparisonHash};
  const saved=SedoMapping.store.load(mappingOptions),mappings=saved.pairs;
  const results=SedoCompare.compare(a,b,mappings);state.remove();
  const counts={added:0,removed:0,changed:0,mapped:0};results.forEach(r=>counts[r.status]++);
  const summaries=node("div","compare-counts");for(const [key,text] of [["added","Добавлено"],["removed","Удалено"],["changed","Изменено"]])summaries.append(node("span",key,text+" · "+counts[key]));if(mappings.length)summaries.append(node("span","mapped","Ручных соответствий · "+mappings.length));section.append(summaries);
  if(applied){const feedback=node("p","mapping-applied-feedback",(applied==="cancelled"?"Сопоставление отменено. Сохранено пар: ":"Сравнение обновлено. Применено пар: ")+mappings.length);feedback.setAttribute("role","status");section.append(feedback)}
  const mappingPanel=SedoMapping.mount(section,mappingOptions,saved,()=>renderComparison(m,params,true));let cancelling=false;
  const exports=node("div","comparison-export"),exportButton=node("button","open","Выгрузить Excel для сопоставления"),exportState=node("span","meta");exportButton.type="button";exportState.setAttribute("role","status");
  exportButton.onclick=async()=>{
   exportButton.disabled=true;exportState.textContent="Готовим книгу…";
   try{await SedoComparisonExport.download({left,right,before:a,after:b,results,mappings,baseURL:new URL(".",location.href).href});exportState.textContent="Книга передана браузеру для скачивания."}
   catch(error){exportState.textContent="Не удалось выгрузить Excel. "+error.message}
   finally{exportButton.disabled=false}
  };
  exports.append(exportButton,exportState,node("p","meta","Вся выбранная пара редакций: исходные поля, изменения и таблица ручного сопоставления. Поиск и фильтры не ограничивают выгрузку."));section.append(exports);
  if(!results.length){section.append(node("p","empty","Деревья совпадают. Изменений в тегах, ограничениях и описаниях не найдено."));if(applied)summaries.scrollIntoView?.({block:"start",behavior:"smooth"});return true}
  const filters=node("div","searchbar"),searchLabel=node("label","","Поиск по изменениям"),search=node("input");search.type="search";search.placeholder="Тег, путь, описание или ограничение";searchLabel.append(search);
  const kindLabel=node("label","","Вид изменения"),kind=node("select");for(const [value,text] of [["","Все изменения"],["added","Добавлено"],["removed","Удалено"],["changed","Изменено"],["mapped","Сопоставлено без изменений"]])kind.add(new Option(text,value));kindLabel.append(kind);filters.append(searchLabel,kindLabel);section.append(filters);
  if(view){search.value=view.search;kind.value=view.kind}
  const count=node("p","meta");count.setAttribute("role","status");const list=node("div","differences");section.append(count,list);
  function draw(){
   const terms=normalize(search.value).split(/\s+/).filter(Boolean);
   const visible=results.filter(r=>(!kind.value||r.status===kind.value)&&terms.every(t=>normalize([r.path,r.beforePath,r.afterPath,r.comment,r.before?.doc,r.after?.doc,...r.changes.map(c=>c.label+" "+formatDifference(c.before)+" "+formatDifference(c.after))].join(" ")).includes(t)));
   count.textContent="Показано изменений · "+visible.length;list.replaceChildren();
   for(const r of visible){
    const card=node("article","difference "+r.status),head=node("div","difference-head");head.append(node("span","badge",({added:"Добавлено",removed:"Удалено",changed:"Изменено",mapped:"Сопоставлено без изменений"})[r.status]),node("code","",r.manual&&r.beforePath!==r.afterPath?r.beforePath+" → "+r.afterPath:r.path));if(r.manual)head.append(node("span","badge mapped","Ручное соответствие"));card.append(head);if(r.comment)card.append(node("p","mapping-comment",r.comment));
    if(r.status==="mapped")card.append(node("p","","Ограничения и описания сопоставленных полей совпадают."));
    if(r.status!=="changed")card.append(node("p","",r.after?.doc||r.before?.doc||""));
    else{
     const table=node("table","diff-table"),thead=node("thead"),tr=node("tr");["Свойство","Было","Стало"].forEach(s=>tr.append(node("th","",s)));thead.append(tr);table.append(thead);const body=node("tbody");
     for(const c of r.changes){const row=node("tr");row.append(node("th","",c.label),node("td","before",formatDifference(c.before)),node("td","after",formatDifference(c.after)));body.append(row)}
     table.append(body);const scroll=node("div","table-scroll");scroll.append(table);card.append(scroll);
    }
    const links=node("div","difference-links");
    if(r.before)links.append(anchor("Поле в «Было»",route(left.number,left.environment,leftId,r.before?.path||r.path)));
    if(r.after)links.append(anchor("Поле в «Стало»",route(right.number,right.environment,rightId,r.after?.path||r.path)));
    if(r.manual){
     const undo=node("button","swap mapping-undo","Отменить сопоставление"),error=node("p","mapping-undo-error");undo.type="button";error.setAttribute("role","alert");error.hidden=true;
     undo.onclick=async()=>{
      if(cancelling)return;error.hidden=false;error.textContent="";
      if(!mappingOptions.isCurrent()){error.textContent="Выбранная пара редакций изменилась. Откройте сравнение снова.";return}
      if(mappingPanel?.hasUnapplied()){error.textContent="Сначала примените или отмените черновик соответствий выше, чтобы не потерять правки.";return}
      cancelling=true;undo.disabled=true;
      try{
       const current=SedoMapping.store.load(mappingOptions).pairs,remaining=current.filter(p=>p.beforePath!==r.beforePath||p.afterPath!==r.afterPath);
       if(current.length===remaining.length)throw Error("Эта пара уже изменилась. Откройте сравнение снова.");
       SedoMapping.store.save(mappingOptions,remaining);
       const updated=await renderComparison(m,params,"cancelled",{search:search.value,kind:kind.value});
       if(updated===false)throw Error("Пара отменена, но обновить сравнение не удалось. Откройте его снова.");
      }catch(e){cancelling=false;undo.disabled=false;error.textContent="Не удалось отменить сопоставление. "+e.message}
     };
     links.append(undo);card.append(error);
    }
    card.append(links);list.append(card);
   }
  }
  search.oninput=draw;kind.onchange=draw;draw();if(applied)summaries.scrollIntoView?.({block:"start",behavior:"smooth"});return true;
 }catch(error){if(request===comparisonRequest){const message="Сравнение недоступно. "+error.message;if(state.isConnected)state.textContent=message;else{const failure=node("p","empty",message);failure.setAttribute("role","alert");section.append(failure)}}return false}
}

