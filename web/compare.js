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
function comparisonLabel(r){return label(r)+" · "+r.revision_id.slice(0,8)}
function comparisonDefaults(m){
 const ready=m.revisions.filter(r=>r.ready),prod=ready.filter(r=>r.environment==="prod"),test=ready.filter(r=>r.environment==="test");
 const latest=list=>[...list].sort((a,b)=>date(b.label_date).split(".").reverse().join("").localeCompare(date(a.label_date).split(".").reverse().join("")))[0];
 const left=latest(prod)||ready[0],right=latest(test)||ready.find(r=>r.revision_id!==left?.revision_id)||left;
 return {left:left?.revision_id,right:right?.revision_id};
}
function formatDifference(value){
 if(value===undefined||value===null||value==="")return "Не указано";
 if(Array.isArray(value))return value.length?value.map(v=>Array.isArray(v)?v.join(" — "):String(v)).join("\n"):"Без ограничений в этом свойстве";
 if(typeof value==="object")return Object.keys(value).length?Object.entries(value).map(([k,v])=>k+" = "+v).join("\n"):"Без дополнительных свойств";
 return String(value);
}
async function renderComparison(m,params){
 const request=++comparisonRequest,section=$("comparison");section.replaceChildren(node("h1","","Сравнение сообщения "+m.number),node("p","",title(m)));
 const defaults=comparisonDefaults(m),leftId=params.get("left")||defaults.left,rightId=params.get("right")||defaults.right;
 const ready=m.revisions.filter(r=>r.ready),left=ready.find(r=>r.revision_id===leftId),right=ready.find(r=>r.revision_id===rightId);
 if(!left||!right){section.append(node("p","empty","Выбранная редакция недоступна для сравнения."));return}
 const controls=node("div","compare-controls"),selectors=[];
 for(const [caption,selected] of [["Было",left],["Стало",right]]){
  const wrap=node("label","",caption),select=node("select");
  for(const r of ready)select.add(new Option(comparisonLabel(r),r.revision_id));
  select.value=selected.revision_id;wrap.append(select);controls.append(wrap);selectors.push(select);
 }
 const switchSides=node("button","swap","Поменять местами");switchSides.type="button";
 switchSides.onclick=()=>{location.hash=comparisonRoute(m.number,rightId,leftId)};controls.append(switchSides);section.append(controls);
 selectors.forEach(select=>select.onchange=()=>{location.hash=comparisonRoute(m.number,selectors[0].value,selectors[1].value)});
 const sources=node("div","compare-sources");
 for(const [caption,r] of [["Было",left],["Стало",right]]){
  const box=node("div");box.append(node("strong","",caption+" · "+comparisonLabel(r)),node("br"),documentDownload(r),document.createTextNode(" · "),revisionOrigin(r),document.createTextNode(" · "),anchor("Открыть дерево",route(m.number,r.environment,r.revision_id),"source"));sources.append(box)
 }
 section.append(sources);
 const state=node("p","empty","Сравниваем деревья…");section.append(state);
 try{
  const [a,b]=await Promise.all([loadComparisonData(leftId),loadComparisonData(rightId)]);
  if(request!==comparisonRequest||!new URLSearchParams(location.hash.slice(1)).has("compare"))return;
  const results=SedoCompare.compare(a,b);state.remove();
  const counts={added:0,removed:0,changed:0};results.forEach(r=>counts[r.status]++);
  const summaries=node("div","compare-counts");for(const [key,text] of [["added","Добавлено"],["removed","Удалено"],["changed","Изменено"]])summaries.append(node("span",key,text+" · "+counts[key]));section.append(summaries);
  if(!results.length){section.append(node("p","empty","Деревья совпадают. Изменений в тегах, ограничениях и описаниях не найдено."));return}
  const filters=node("div","searchbar"),searchLabel=node("label","","Поиск по изменениям"),search=node("input");search.type="search";search.placeholder="Тег, путь, описание или ограничение";searchLabel.append(search);
  const kindLabel=node("label","","Вид изменения"),kind=node("select");for(const [value,text] of [["","Все изменения"],["added","Добавлено"],["removed","Удалено"],["changed","Изменено"]])kind.add(new Option(text,value));kindLabel.append(kind);filters.append(searchLabel,kindLabel);section.append(filters);
  const count=node("p","meta");count.setAttribute("role","status");const list=node("div","differences");section.append(count,list);
  function draw(){
   const terms=normalize(search.value).split(/\s+/).filter(Boolean);
   const visible=results.filter(r=>(!kind.value||r.status===kind.value)&&terms.every(t=>normalize([r.path,r.before?.doc,r.after?.doc,...r.changes.map(c=>c.label+" "+formatDifference(c.before)+" "+formatDifference(c.after))].join(" ")).includes(t)));
   count.textContent="Показано изменений · "+visible.length;list.replaceChildren();
   for(const r of visible){
    const card=node("article","difference "+r.status),head=node("div","difference-head");head.append(node("span","badge",({added:"Добавлено",removed:"Удалено",changed:"Изменено"})[r.status]),node("code","",r.path));card.append(head);
    if(r.status!=="changed")card.append(node("p","",r.after?.doc||r.before?.doc||""));
    else{
     const table=node("table","diff-table"),thead=node("thead"),tr=node("tr");["Свойство","Было","Стало"].forEach(s=>tr.append(node("th","",s)));thead.append(tr);table.append(thead);const body=node("tbody");
     for(const c of r.changes){const row=node("tr");row.append(node("th","",c.label),node("td","before",formatDifference(c.before)),node("td","after",formatDifference(c.after)));body.append(row)}
     table.append(body);const scroll=node("div","table-scroll");scroll.append(table);card.append(scroll);
    }
    const links=node("div","difference-links");
    if(r.before)links.append(anchor("Поле в «Было»",route(m.number,left.environment,leftId,r.path)));
    if(r.after)links.append(anchor("Поле в «Стало»",route(m.number,right.environment,rightId,r.path)));
    card.append(links);list.append(card);
   }
  }
  search.oninput=draw;kind.onchange=draw;draw();
 }catch(error){if(request===comparisonRequest)state.textContent="Сравнение недоступно. "+error.message}
}

