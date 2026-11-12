/* Local-only XLSX mappings: no uploads, no evaluation of workbook formulas. */
(function(root,factory){"use strict";const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.SedoMapping=api})(typeof globalThis!=="undefined"?globalThis:this,function(){
 "use strict";
 const MAX_FILE=10*1024*1024,MAX_EXPANDED=64*1024*1024,MAX_ROWS=50000;
 function preflightArchive(bytes){
  if(bytes.byteLength>MAX_FILE)throw Error("Размер XLSX превышает 10 МиБ.");
  if(bytes.byteLength<22)throw Error("Файл не является XLSX (ZIP).");
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);let end=-1;
  for(let i=bytes.byteLength-22;i>=Math.max(0,bytes.byteLength-65557);i--)if(view.getUint32(i,true)===0x06054b50&&i+22+view.getUint16(i+20,true)===bytes.byteLength){end=i;break}
  if(end<0)throw Error("Не найден каталог ZIP внутри XLSX.");
  const count=view.getUint16(end+10,true),size=view.getUint32(end+12,true),start=view.getUint32(end+16,true);
  if(view.getUint16(end+4,true)||view.getUint16(end+6,true)||count===65535||count>2000||start+size!==end)throw Error("Этот формат ZIP/XLSX не поддерживается.");
  let offset=start,total=0;const names=new Set();
  for(let i=0;i<count;i++){
   if(offset+46>end||view.getUint32(offset,true)!==0x02014b50)throw Error("Повреждён каталог ZIP/XLSX.");
   total+=view.getUint32(offset+24,true);
   if(total>MAX_EXPANDED)throw Error("Заявленный распакованный объём XLSX превышает 64 МиБ.");
   if(view.getUint16(offset+8,true)&1)throw Error("XLSX с паролем не поддерживается.");
   const nameLength=view.getUint16(offset+28,true),extra=view.getUint16(offset+30,true),comment=view.getUint16(offset+32,true);
   const next=offset+46+nameLength+extra+comment;if(next>end)throw Error("Повреждён каталог ZIP/XLSX.");
   names.add(new TextDecoder().decode(bytes.subarray(offset+46,offset+46+nameLength)));offset=next;
  }
  if(offset!==end||!names.has("[Content_Types].xml")||!names.has("xl/workbook.xml"))throw Error("В ZIP нет структуры книги XLSX.");
 }
 function cellText(cell){
  const value=cell?.value;if(value===null||value===undefined)return "";
  if(typeof value==="string")return value.trim();
  if(value&&Array.isArray(value.richText)&&value.richText.every(v=>typeof v.text==="string"))return value.richText.map(v=>v.text).join("").trim();
  throw Error("Нужен текст без формул, чисел и ошибок Excel.");
 }
 function orient(record,left,right){
  if(record.left===left.revision_id&&record.right===right.revision_id)return {pairs:record.pairs,reversed:false};
  if(record.left===right.revision_id&&record.right===left.revision_id)return {pairs:record.pairs.map(p=>({beforePath:p.afterPath,afterPath:p.beforePath,comment:p.comment||""})),reversed:true};
  throw Error("Книга относится к другой паре редакций. Откройте указанные в ней редакции или выгрузите новую книгу.");
 }
 function validatePair(p,old,next){
  if(!p||typeof p.beforePath!=="string"||typeof p.afterPath!=="string"||typeof p.comment!=="string"||!old.has(p.beforePath)||!next.has(p.afterPath))throw Error("Не найден XML-путь соответствия: "+String(p?.beforePath)+" → "+String(p?.afterPath));
 }
 function validatePairs(pairs,before,after){
  if(!Array.isArray(pairs)||pairs.length>MAX_ROWS)throw Error("Некорректный список соответствий.");
  const old=new Set(before.map(f=>f.path)),next=new Set(after.map(f=>f.path));
  for(const p of pairs)validatePair(p,old,next);
 }
 function suggestDescendants(pairs,before,after){
  validatePairs(pairs,before,after);
  const oldParents=new Map(),newParents=new Map(),unique=new Map(),targets=new Set(after.map(f=>f.path));
  for(const pair of pairs){const key=JSON.stringify([pair.beforePath,pair.afterPath]);if(!unique.has(key))unique.set(key,{...pair,key})}
  const add=(map,path,pair)=>{if(!map.has(path))map.set(path,[]);map.get(path).push(pair)};
  for(const pair of unique.values()){add(oldParents,pair.beforePath,pair);add(newParents,pair.afterPath,pair)}
  const nearest=(path,parents)=>{
   for(let slash=path.lastIndexOf("/");slash>=0;slash=path.lastIndexOf("/",slash-1)){
    const found=parents.get(path.slice(0,slash));if(found)return found;if(slash===0)break;
   }
   return [];
  };
  const candidates=[],conflicts=[],seen=new Set();
  for(const field of before){
   if(oldParents.has(field.path))continue; // Explicit rows always take precedence.
   for(const parent of nearest(field.path,oldParents)){
    const suffix=field.path.slice(parent.beforePath.length),afterPath=parent.afterPath+suffix;
    if(!targets.has(afterPath))continue;
    const key=JSON.stringify([field.path,afterPath]);if(seen.has(key))continue;seen.add(key);
    if(seen.size+unique.size>MAX_ROWS)throw Error("Слишком много предложений. Уточните родительские соответствия или разбейте книгу.");
    const proposal={beforePath:field.path,afterPath,relativePath:suffix.slice(1),parentBeforePath:parent.beforePath,parentAfterPath:parent.afterPath,comment:"Сопоставлено по одинаковому относительному пути внутри родительского блока."};
    if(newParents.has(afterPath)){conflicts.push({...proposal,reason:"Путь «Стало» уже сопоставлен вручную."});continue}
    // Check both directions so a manually remapped target subtree remains intact.
    if(!nearest(afterPath,newParents).some(p=>p.key===parent.key)){conflicts.push({...proposal,reason:"Ветка «Стало» вручную сопоставлена с другим блоком."});continue}
    candidates.push(proposal);
   }
  }
  const oldCounts=new Map(),newCounts=new Map();
  for(const p of candidates){oldCounts.set(p.beforePath,(oldCounts.get(p.beforePath)||0)+1);newCounts.set(p.afterPath,(newCounts.get(p.afterPath)||0)+1)}
  const suggestions=[];
  for(const proposal of candidates){
   if(oldCounts.get(proposal.beforePath)>1||newCounts.get(proposal.afterPath)>1)conflicts.push({...proposal,reason:"Несколько возможных соответствий при разделении или объединении блоков. Укажите эту пару вручную."});
   else suggestions.push(proposal);
  }
  return {suggestions,conflicts};
 }
 function parseWorkbook(workbook,options){
  const report={pairs:[],errors:[],ignored:0,duplicates:0,reversed:false};
  try{
   const metadata=workbook.getWorksheet("О выгрузке"),sheet=workbook.getWorksheet("Сопоставление");
   if(!metadata||!sheet)throw Error("Нужна книга справочника с листами «Сопоставление» и «О выгрузке».");
   if(sheet.rowCount>MAX_ROWS+30)throw Error("В таблице больше 50000 строк.");
   let ids;
   for(let row=1;row<=Math.min(metadata.rowCount,30);row++)if(cellText(metadata.getCell(row,1))==="Идентификатор редакции"){
    if(ids)throw Error("Идентификаторы редакций указаны несколько раз.");
    ids={left:cellText(metadata.getCell(row,2)),right:cellText(metadata.getCell(row,3))};
   }
   if(!ids?.left||!ids?.right)throw Error("В книге отсутствуют идентификаторы редакций.");
   report.reversed=orient({...ids,pairs:[]},options.left,options.right).reversed;
   const columns={},headers={beforePath:"Путь «Было»",afterPath:"Путь «Стало» — введите или выберите",decision:"Решение — вручную",comment:"Комментарий — вручную"};let headerRow;
   for(let row=1;row<=Math.min(sheet.rowCount,30);row++){
    for(let col=1;col<=Math.min(sheet.columnCount,40);col++){
     let value;try{value=cellText(sheet.getCell(row,col))}catch{continue}
     for(const [key,label] of Object.entries(headers))if(value===label){if(columns[key])throw Error("Заголовки таблицы неоднозначны.");columns[key]=col;headerRow=row}
    }
    if(Object.keys(columns).length){if(Object.keys(columns).length!==4)throw Error("В таблице отсутствуют нужные заголовки столбцов.");break}
   }
   if(!headerRow)throw Error("Не найдены заголовки таблицы сопоставления.");
   const seen=new Map(),oldPaths=new Set(options.before.map(f=>f.path)),newPaths=new Set(options.after.map(f=>f.path));
   for(let row=headerRow+1;row<=sheet.rowCount;row++){
    try{
     const decision=cellText(sheet.getCell(row,columns.decision));
     if(decision!=="Подтверждено"){report.ignored++;continue}
     const pair={beforePath:cellText(sheet.getCell(row,columns.beforePath)),afterPath:cellText(sheet.getCell(row,columns.afterPath)),comment:cellText(sheet.getCell(row,columns.comment))};
     if(report.reversed)[pair.beforePath,pair.afterPath]=[pair.afterPath,pair.beforePath];
     validatePair(pair,oldPaths,newPaths);
     const key=JSON.stringify([pair.beforePath,pair.afterPath]);
     if(seen.has(key)){
      report.duplicates++;const existing=seen.get(key);if(pair.comment&&pair.comment!==existing.comment)existing.comment=[existing.comment,pair.comment].filter(Boolean).join("\n");continue;
     }
     seen.set(key,pair);report.pairs.push(pair);
    }catch(error){report.errors.push("Строка "+row+": "+error.message)}
   }
  }catch(error){report.errors.push(error.message);report.pairs=[]}
  return report;
 }
 const storageKey=(left,right)=>"sedo-field-mappings:v1:"+[left.revision_id,right.revision_id].sort().map(encodeURIComponent).join(":");
 function createStore(getStorage){
  const memory=new Map(),notices=new Map();
  return {
   load(options){
    const key=storageKey(options.left,options.right);let raw;
    try{raw=memory.has(key)?memory.get(key):getStorage().getItem(key)}catch{return {pairs:[],notice:"Хранилище браузера недоступно. Соответствия будут действовать в текущем сеансе."}}
    if(!raw)return {pairs:[],notice:notices.get(key)||""};
    try{
     const record=JSON.parse(raw);if(record.version!==1)throw Error("Неизвестный формат");
     if(!Array.isArray(record.pairs))throw Error("Не указан список пар");
     const current=orient(record,options.left,options.right);validatePairs(current.pairs,options.before,options.after);
     return {pairs:current.pairs,notice:notices.get(key)||""};
    }catch{return {pairs:[],notice:"Сохранённые соответствия повреждены или не подходят к схемам и не применены. Сбросьте их или загрузите Excel снова."}}
   },
   save(options,pairs){
    validatePairs(pairs,options.before,options.after);const key=storageKey(options.left,options.right),raw=JSON.stringify({version:1,left:options.left.revision_id,right:options.right.revision_id,pairs});memory.set(key,raw);
    let notice="";try{getStorage().setItem(key,raw)}catch{notice="Не удалось сохранить соответствия в браузере. Они действуют только в текущем сеансе; сохраните Excel для повторной загрузки."}notices.set(key,notice);return notice;
   },
   clear(options){
    const key=storageKey(options.left,options.right);memory.set(key,null);let notice="";
    try{getStorage().removeItem(key)}catch{notice="Соответствия сброшены в текущем сеансе. Хранилище браузера недоступно; после перезапуска может потребоваться повторный сброс."}notices.set(key,notice);return notice;
   }
  };
 }
 const store=createStore(()=>globalThis.localStorage);
 async function readFile(file,options){
  if(file.size>MAX_FILE)throw Error("Размер XLSX превышает 10 МиБ.");
  const bytes=new Uint8Array(await file.arrayBuffer());preflightArchive(bytes);
  const ExcelJS=await globalThis.SedoComparisonExport.loadLibrary(),workbook=new ExcelJS.Workbook();
  await workbook.xlsx.load(bytes);return parseWorkbook(workbook,options);
 }
 function mount(container,options,saved,onChange){
  const el=(tag,cls,value)=>{const element=document.createElement(tag);if(cls)element.className=cls;if(value!==undefined)element.textContent=value;return element};
  const button=(cls,value)=>{const control=el("button",cls,value);control.type="button";return control};
  const panel=el("section","mapping-panel"),status=el("p","meta"),preview=el("div","mapping-preview"),controls=el("div","mapping-controls");
  panel.append(el("h2","","Ручные соответствия"),el("p","meta","Загрузите заполненный Excel. По подтверждённым родительским блокам будут предложены вложенные теги с одинаковыми относительными путями. Файл обрабатывается в браузере."));
  status.setAttribute("role","status");status.textContent="Применено пар: "+saved.pairs.length+(saved.notice?". "+saved.notice:"");
  const fileInput=el("input","mapping-file");fileInput.type="file";fileInput.accept=".xlsx";fileInput.hidden=true;
  const upload=button("swap","Загрузить сопоставления из Excel"),reset=button("swap","Сбросить сопоставления");reset.disabled=!saved.pairs.length&&!saved.notice;
  upload.onclick=()=>fileInput.click();reset.onclick=()=>{if(options.isCurrent()){store.clear(options);onChange()}};
  function paginatedTable(host,rows,captions,makeRow){
   const table=el("table","diff-table"),head=el("tr"),thead=el("thead"),body=el("tbody"),scroll=el("div","table-scroll"),nav=el("div","mapping-pages");
   captions.forEach(text=>head.append(el("th","",text)));thead.append(head);table.append(thead,body);scroll.append(table);host.append(scroll);
   const previous=button("swap mapping-prev","Назад"),next=button("swap mapping-next","Далее"),caption=el("span","meta");caption.setAttribute("role","status");nav.append(previous,caption,next);
   let page=0;const size=50,pages=Math.ceil(rows.length/size);
   function draw(){body.replaceChildren();rows.slice(page*size,(page+1)*size).forEach((row,i)=>body.append(makeRow(row,page*size+i)));previous.disabled=page===0;next.disabled=page===pages-1;caption.textContent="Строки "+(page*size+1)+"–"+Math.min((page+1)*size,rows.length)+" из "+rows.length+" · страница "+(page+1)+" из "+pages}
   previous.onclick=()=>{page--;draw()};next.onclick=()=>{page++;draw()};if(pages>1)host.append(nav);draw();return draw;
  }
  function showPreview(report,manualLabel){
   preview.replaceChildren();
   const proposal=report.errors.length?{suggestions:[],conflicts:[]}:suggestDescendants(report.pairs,options.before,options.after),suggestions=proposal.suggestions;
   status.textContent="Предпросмотр: ручных пар "+report.pairs.length+", предложено вложенных "+suggestions.length+", требуют решения "+proposal.conflicts.length+", пропущено неподтверждённых строк "+report.ignored+", повторов "+report.duplicates+", ошибок "+report.errors.length+(report.reversed?". Направление пар развёрнуто.":".");
   const errors=el("ul","mapping-errors");for(const error of report.errors.slice(0,20))errors.append(el("li","",error));if(report.errors.length>20)errors.append(el("li","","Остальные ошибки: "+(report.errors.length-20)));if(report.errors.length)preview.append(errors);
   const old=new Map(options.before.map(f=>[f.path,f])),next=new Map(options.after.map(f=>[f.path,f])),selected=new Set(suggestions.map((_,i)=>i));
   const selection=el("p","meta mapping-selected"),apply=button("open mapping-apply","Применить сопоставления");selection.setAttribute("role","status");
   function update(){selection.textContent="К применению: ручных пар "+report.pairs.length+", вложенных "+selected.size+" из "+suggestions.length;apply.textContent="Применить сопоставления ("+(report.pairs.length+selected.size)+")";apply.disabled=!!report.errors.length||!report.pairs.length&&!selected.size}
   let redraw=()=>{};
   if(suggestions.length){
    const bulk=el("div","mapping-controls"),all=button("swap mapping-select-all","Выбрать все предложения"),none=button("swap mapping-select-none","Снять выбор предложений");
    all.onclick=()=>{suggestions.forEach((_,i)=>selected.add(i));redraw();update()};none.onclick=()=>{selected.clear();redraw();update()};bulk.append(all,none);preview.append(bulk);
   }
   preview.append(selection);
   const rows=[...report.pairs.map(pair=>({pair,suggestion:-1})),...suggestions.map((pair,i)=>({pair,suggestion:i}))];
   if(rows.length)redraw=paginatedTable(preview,rows,["Применить","Источник","Путь и описание «Было»","Путь и описание «Стало»","Основание / комментарий"],({pair,suggestion})=>{
    const row=el("tr"),input=el("input",suggestion>=0?"mapping-proposal":"mapping-explicit"),choice=el("td");input.type="checkbox";input.disabled=suggestion<0;input.checked=suggestion<0||selected.has(suggestion);input.setAttribute("aria-label","Сопоставить "+pair.beforePath+" → "+pair.afterPath);input.dataset.beforePath=pair.beforePath;input.dataset.afterPath=pair.afterPath;
    input.onchange=()=>{input.checked?selected.add(suggestion):selected.delete(suggestion);update()};choice.append(input);
    const reason=suggestion>=0?"Относительный путь: "+pair.relativePath+"\nРодитель: "+pair.parentBeforePath+" → "+pair.parentAfterPath:pair.comment;
    row.append(choice,el("td","",suggestion>=0?"По относительному пути":manualLabel),el("td","",pair.beforePath+"\n"+(old.get(pair.beforePath)?.doc||"")),el("td","",pair.afterPath+"\n"+(next.get(pair.afterPath)?.doc||"")),el("td","",reason));return row;
   });
   if(proposal.conflicts.length){
    const details=el("details","mapping-conflicts");details.append(el("summary","","Требуют ручного решения · "+proposal.conflicts.length),el("p","meta","Эти пары не выбраны автоматически. Уточните сопоставления в Excel и загрузите книгу снова."));
    paginatedTable(details,proposal.conflicts,["Путь «Было»","Путь «Стало»","Причина"],pair=>{const row=el("tr");row.append(el("td","",pair.beforePath),el("td","",pair.afterPath),el("td","",pair.reason));return row});preview.append(details);
   }
   apply.onclick=()=>{if(options.isCurrent()){
    const pairs=[...report.pairs,...suggestions.filter((_,i)=>selected.has(i))].map(({beforePath,afterPath,comment})=>({beforePath,afterPath,comment}));store.save(options,pairs);onChange();
   }};
   preview.append(apply,el("p","meta","Применение заменит сохранённый набор для этой пары редакций. Выбранные предложения будут подтверждены и сохранены в браузере. Сверьте описания: совпадение относительного пути не подтверждает одинаковый смысл. Ограничения берутся из схем справочника."));update();
  }
  controls.append(upload,reset,fileInput);panel.append(controls,status,preview);container.append(panel);
  let savedSuggest=null;
  if(saved.pairs.length){
   try{
    const available=suggestDescendants(saved.pairs,options.before,options.after);
    if(available.suggestions.length||available.conflicts.length){
     const suggest=button("swap mapping-suggest","Предпросмотр вложенных тегов ("+available.suggestions.length+")");suggest.onclick=()=>{if(options.isCurrent()){
      try{showPreview({pairs:saved.pairs,errors:[],ignored:0,duplicates:0,reversed:false},"Ранее применено")}catch(error){status.textContent="Не удалось подготовить предложения. "+error.message}
     }};savedSuggest=suggest;controls.append(suggest);
    }
   }catch(error){status.textContent+=" Не удалось подготовить предложения. "+error.message}
  }
  let generation=0;
  fileInput.onchange=async()=>{
   const file=fileInput.files?.[0];if(!file)return;const token=++generation;
   upload.disabled=true;if(savedSuggest)savedSuggest.disabled=true;status.textContent="Читаем Excel…";preview.replaceChildren();
   try{
    const report=await readFile(file,options);if(token!==generation||!options.isCurrent())return;showPreview(report,"Из Excel");
   }catch(error){if(token===generation&&options.isCurrent())status.textContent="Не удалось загрузить соответствия. "+error.message}
   finally{if(token===generation){upload.disabled=false;if(savedSuggest)savedSuggest.disabled=false;fileInput.value=""}}
  };
 }
 return {preflightArchive,suggestDescendants,parseWorkbook,storageKey,createStore,store,readFile,mount};
});
