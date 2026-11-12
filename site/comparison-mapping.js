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
  const panel=el("section","mapping-panel"),status=el("p","meta"),preview=el("div","mapping-preview"),controls=el("div","mapping-controls");
  panel.append(el("h2","","Ручные соответствия"),el("p","meta","Загрузите заполненный Excel. Применяются только строки «Подтверждено» для этой пары редакций. Файл обрабатывается в браузере."));
  status.setAttribute("role","status");status.textContent="Применено пар: "+saved.pairs.length+(saved.notice?". "+saved.notice:"");
  const fileInput=el("input","mapping-file");fileInput.type="file";fileInput.accept=".xlsx";fileInput.hidden=true;
  const upload=el("button","swap","Загрузить сопоставления из Excel"),reset=el("button","swap","Сбросить сопоставления");upload.type=reset.type="button";reset.disabled=!saved.pairs.length&&!saved.notice;
  upload.onclick=()=>fileInput.click();reset.onclick=()=>{if(options.isCurrent()){store.clear(options);onChange()}};
  let generation=0;
  fileInput.onchange=async()=>{
   const file=fileInput.files?.[0];if(!file)return;const token=++generation;
   upload.disabled=true;status.textContent="Читаем Excel…";preview.replaceChildren();
   try{
    const report=await readFile(file,options);if(token!==generation||!options.isCurrent())return;
    status.textContent="Предпросмотр: пар "+report.pairs.length+", пропущено неподтверждённых строк "+report.ignored+", повторов "+report.duplicates+", ошибок "+report.errors.length+(report.reversed?". Направление пар развёрнуто.":".");
    const errors=el("ul","mapping-errors");for(const error of report.errors.slice(0,20))errors.append(el("li","",error));if(report.errors.length>20)errors.append(el("li","","Остальные ошибки: "+(report.errors.length-20)));if(report.errors.length)preview.append(errors);
    const old=new Map(options.before.map(f=>[f.path,f])),next=new Map(options.after.map(f=>[f.path,f]));
    if(report.pairs.length){
     const table=el("table","diff-table"),head=el("tr");for(const caption of ["Путь и описание «Было»","Путь и описание «Стало»","Комментарий"])head.append(el("th","",caption));const thead=el("thead");thead.append(head);table.append(thead);const body=el("tbody");
     for(const pair of report.pairs.slice(0,10)){const row=el("tr");row.append(el("td","",pair.beforePath+"\n"+(old.get(pair.beforePath)?.doc||"")),el("td","",pair.afterPath+"\n"+(next.get(pair.afterPath)?.doc||"")),el("td","",pair.comment));body.append(row)}table.append(body);const scroll=el("div","table-scroll");scroll.append(table);preview.append(scroll);
     if(report.pairs.length>10)preview.append(el("p","meta","Показаны первые 10 пар из "+report.pairs.length+". После применения сравнение учитывает весь набор."));
    }
    const apply=el("button","open mapping-apply","Применить сопоставления");apply.type="button";apply.disabled=!!report.errors.length||!report.pairs.length;
    apply.onclick=()=>{if(options.isCurrent()){store.save(options,report.pairs);onChange()}};preview.append(apply,el("p","meta","Применение заменит сохранённый набор для этой пары редакций. Ограничения берутся из схем справочника. Сопоставление блока не распространяется на вложенные поля."));
   }catch(error){if(token===generation&&options.isCurrent())status.textContent="Не удалось загрузить соответствия. "+error.message}
   finally{if(token===generation){upload.disabled=false;fileInput.value=""}}
  };
  controls.append(upload,reset,fileInput);panel.append(controls,status,preview);container.append(panel);
 }
 return {preflightArchive,parseWorkbook,storageKey,createStore,store,readFile,mount};
});
