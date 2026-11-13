/* XLSX workbench for human review. Source data is always written as text. */
(function(root,factory){
 "use strict";
 const api=factory();
 if(typeof module==="object"&&module.exports)module.exports=api;
 else root.SedoComparisonExport=api;
})(typeof globalThis!=="undefined"?globalThis:this,function(){
 "use strict";
 const statuses={added:"Добавлено",removed:"Удалено",changed:"Изменено",mapped:"Сопоставлено без изменений",same:"Без изменений"};
 const json=value=>JSON.stringify(value??null);
 const text=value=>value===null||value===undefined?"":typeof value==="object"?json(value):String(value);
 function constraints(f){
  if(!f)return "";
  const parts=[];
  for(const key of ["type","base"])if(f[key])parts.push(key+": "+f[key]);
  if(f.min!==undefined||f.max!==undefined)parts.push("Вхождения: "+text(f.min)+"…"+text(f.max));
  if(f.group)parts.push("Группа: "+text(f.group));
  if(f.facets?.length)parts.push("Ограничения: "+f.facets.map(v=>v[0]+" = "+json(v[1])).join("; "));
  if(f.values?.length)parts.push("Значения: "+f.values.map(v=>json(v[0])+(v[1]?" — "+v[1]:"")).join("; "));
  if(f.flags&&Object.keys(f.flags).length)parts.push("Свойства: "+json(f.flags));
  if(f.alternatives?.length)parts.push("Варианты union (ИЛИ):\n"+f.alternatives.map((v,i)=>(i+1)+". "+constraints(v)).join("\nИЛИ\n"));
  return parts.join("\n");
 }
 function edition(r){return "№ "+r.number+" · "+({prod:"Продуктивный",test:"Тестовый"}[r.environment]||r.environment)+" · "+(r.document_version||"Версия не указана")+(r.label_date?" · "+r.label_date:"")}
 function filename(left,right){
  const side=r=>[r.number,r.environment,"v"+(r.document_version||"unknown"),r.revision_id.slice(0,8)].join("-");
  return ("sedo-"+side(left)+"__"+side(right)+".xlsx").replace(/[^\p{L}\p{N}._-]/gu,"_");
 }
 function createWorkbook(ExcelJS,{left,right,before,after,results,mappings=[],baseURL,createdAt=new Date()}){
  const wb=new ExcelJS.Workbook();
  wb.creator="Справочник СЭДО";wb.created=createdAt;wb.modified=createdAt;
  wb.calcProperties.fullCalcOnLoad=true;
  const navy="164B53",pale="E8F4F2",input="FFF4CD";
  function sheet(name,headers,widths,rows,start=1){
   const ws=wb.addWorksheet(name,{views:[{state:"frozen",ySplit:start,xSplit:name==="Сопоставление"?2:1}],properties:{defaultRowHeight:30}});
   widths.forEach((width,i)=>{ws.getColumn(i+1).width=width});
   ws.getRow(start).values=headers;
   rows.forEach(row=>{
    for(const value of row)if(typeof value==="string"&&value.length>32767)throw Error("Текст превышает лимит Excel (32767 символов). Выгрузка остановлена без обрезки данных.");
    ws.addRow(row.map(value=>value===""?null:value));
   });
   ws.eachRow((row,n)=>{
    row.eachCell({includeEmpty:true},cell=>{
     cell.font={name:"Arial",size:10,color:{argb:"172F35"}};
     cell.alignment={vertical:"top",wrapText:true};
     if(cell.type===ExcelJS.ValueType.String)cell.numFmt="@";
     if(n===start){cell.fill={type:"pattern",pattern:"solid",fgColor:{argb:navy}};cell.font={name:"Arial",size:10,bold:true,color:{argb:"FFFFFF"}}}
     else if(n>start&&n%2===0)cell.fill={type:"pattern",pattern:"solid",fgColor:{argb:"F2F7F6"}};
    });
    row.height=n===start?42:name==="О выгрузке"?55:66;
   });
   ws.autoFilter={from:{row:start,column:1},to:{row:Math.max(start,ws.rowCount),column:headers.length}};
   ws.pageSetup={orientation:"landscape",fitToPage:true,fitToWidth:1,fitToHeight:0,printTitlesRow:`${start}:${start}`};
   return ws;
  }
  const diffByPath=new Map(results.filter(d=>!d.manual).map(d=>[d.path,d])),diffByPair=new Map(results.filter(d=>d.manual).map(d=>[JSON.stringify([d.beforePath,d.afterPath]),d])),target=new Map(after.map(f=>[f.path,f])),mappedByBefore=new Map(),usedTargets=new Set();
  for(const pair of mappings){if(!mappedByBefore.has(pair.beforePath))mappedByBefore.set(pair.beforePath,[]);mappedByBefore.get(pair.beforePath).push(pair);usedTargets.add(pair.afterPath)}
  const entries=before.flatMap(field=>mappedByBefore.has(field.path)?mappedByBefore.get(field.path).map(pair=>({field,pair})):[{field,pair:null}]);
  const mapRows=entries.map(({field:f,pair})=>{
   const exact=target.has(f.path)&&!usedTargets.has(f.path),path=pair?pair.afterPath:exact?f.path:"";
   const diff=pair?diffByPair.get(JSON.stringify([f.path,path])):diffByPath.get(f.path);
   return [statuses[diff?.status||"same"],f.path,f.doc||"",constraints(f),path,"","",pair?"Подтверждено":"Не проверено",pair?.comment||""];
  });
  const mapping=sheet("Сопоставление",["Изменение по полному пути","Путь «Было»","Описание «Было»","Ограничения «Было»","Путь «Стало» — введите или выберите","Описание «Стало» — автоматически","Ограничения «Стало» — автоматически","Решение — вручную","Комментарий — вручную"],[21,42,43,44,42,43,44,24,42],mapRows,5);
  for(const [r,value] of [[1,"Ручное сопоставление XML-полей"],[2,"Было: "+edition(left)],[3,"Стало: "+edition(right)],[4,"Жёлтые ячейки редактируются. Совпавший путь ещё не подтверждён. Инструкция и источники — на листе «О выгрузке»."]]){
   mapping.mergeCells(r,1,r,9);const cell=mapping.getCell(r,1);cell.value=value;cell.font={name:"Arial",size:r===1?14:10,bold:r===1,color:{argb:navy}};cell.alignment={wrapText:true,vertical:"middle"};mapping.getRow(r).height=r===4?32:25;
  }
  const sourceHeaders=["Полный XML-путь","Описание","Ограничения для сопоставления","Имя","Вид узла","Тип","Базовый тип","minOccurs / use","maxOccurs","Группа","Namespace","Facets (JSON)","Значения и описания (JSON)","Свойства (JSON)","Варианты union (JSON)","Файл схемы","Файл типа","Индекс родителя","Индексы детей (JSON)","Исходная запись (JSON)"];
  const sourceRows=fields=>fields.map(f=>[f.path,f.doc||"",constraints(f),f.name,f.kind,f.type||"",f.base||"",f.min??"",f.max??"",text(f.group),f.ns||"",json(f.facets||[]),json(f.values||[]),json(f.flags||{}),json(f.alternatives||[]),f.file||"",f.typeFile||"",f.parent??"",json(f.children||[]),json(f)]);
  const widths=[45,55,60,25,15,32,25,18,18,30,45,45,55,30,60,45,45,18,30,80];
  sheet("Было",sourceHeaders,widths,sourceRows(before));
  sheet("Стало",sourceHeaders,widths,sourceRows(after));
  const last=Math.max(2,after.length+1);
  if(after.length)wb.definedNames.add(`'Стало'!$A$2:$A$${last}`,"TargetPaths");
  for(let i=0;i<entries.length;i++){
   const row=i+6,path=mapping.getCell(row,5),exact=target.get(mapRows[i][4]);
   if(after.length)path.dataValidation={type:"list",allowBlank:true,formulae:["TargetPaths"],showInputMessage:true,promptTitle:"Новый XML-путь",prompt:"Скопируйте путь с листа «Стало» или выберите из списка.",showErrorMessage:true,errorStyle:"warning",errorTitle:"Путь не найден",error:"Проверьте полный путь на листе «Стало»."};
   // MATCH(…,0) still interprets wildcards: escape them for literal XML paths.
   const key=`SUBSTITUTE(SUBSTITUTE(SUBSTITUTE(E${row},"~","~~"),"*","~*"),"?","~?")`;
   for(const [column,sourceColumn,value] of [[6,"B",exact?.doc||""],[7,"C",exact?constraints(exact):""]]){
    mapping.getCell(row,column).value={formula:`IF(E${row}="","",_xlfn.IFNA(INDEX('Стало'!$${sourceColumn}$2:$${sourceColumn}$${last},MATCH(${key},'Стало'!$A$2:$A$${last},0))&"","Путь не найден"))`,result:value};
   }
   mapping.getCell(row,8).dataValidation={type:"list",allowBlank:true,formulae:['"Не проверено,Подтверждено,Удалено,Переработано,Требует уточнения"']};
   for(const column of [5,8,9]){const cell=mapping.getCell(row,column);cell.fill={type:"pattern",pattern:"solid",fgColor:{argb:input}};cell.font={name:"Arial",size:10,color:{argb:"156582"}};cell.numFmt="@"}
  }
  const diffRows=results.flatMap(d=>{
   const details=[d.before?.path||"",d.after?.path||"",d.manual?"Да":"",d.comment||""];
   return d.status==="changed"?d.changes.map(c=>[statuses[d.status],d.path,c.label,text(c.before),text(c.after),d.before?.doc||"",d.after?.doc||"",...details]):[[statuses[d.status],d.path,d.manual?"Сопоставление":"Узел XML",d.before?constraints(d.before):"",d.after?constraints(d.after):"",d.before?.doc||"",d.after?.doc||"",...details]];
  });
  sheet("Изменения",["Изменение","Полный XML-путь","Свойство","Было","Стало","Описание «Было»","Описание «Стало»","Путь «Было»","Путь «Стало»","Ручное соответствие","Комментарий"],[20,48,25,65,65,55,55,48,48,22,45],diffRows);
  const absolute=url=>{if(!url)return "";try{const parsed=new URL(url,baseURL);return ["https:","http:","file:"].includes(parsed.protocol)?parsed.href:""}catch{return ""}};
  const metaRows=[
   ["Сообщение и редакция",edition(left),edition(right)],
   ["Количество узлов XML",before.length,after.length],
   ["Идентификатор редакции",left.revision_id,right.revision_id],
   ["SHA-256 DOCX",left.document_sha256||"",right.document_sha256||""],
   ["SHA-256 архива XSD",left.archive_sha256||"",right.archive_sha256||""],
   ["Документ источника",absolute(left.document_url||""),absolute(right.document_url||"")],
   ["Сохранённый DOCX",left.document_download?absolute(left.document_download):"",right.document_download?absolute(right.document_download):""],
   ["Дерево в справочнике",absolute("index.html#"+new URLSearchParams({message:left.number,env:left.environment,rev:left.revision_id})),absolute("index.html#"+new URLSearchParams({message:right.number,env:right.environment,rev:right.revision_id}))],
   ["Происхождение",left.manual_source?.label||left.source_type||"Публикация СФР",right.manual_source?.label||right.source_type||"Публикация СФР"],
   ["Создано (UTC)",createdAt.toISOString(),""],
   ["Объём выгрузки","Все поля и изменения выбранной пары. Поиск и фильтр страницы не ограничивают выгрузку.",""],
   ["Сравнение","Подтверждённые ручные пары сравниваются между собой. Остальные поля сравниваются по полному XML-пути; при смене корня или имени считаются удалёнными/добавленными.",""],
   ["Как сопоставлять","На первом листе выберите или вставьте полный путь из «Стало» в жёлтую ячейку. Описание и ограничения подтянутся формулами Excel. Для родительского блока можно подтвердить его строку: при загрузке браузер предложит вложенные теги с одинаковыми относительными путями. Предложения можно проверить и исключить в предпросмотре.",""],
   ["Проверка смысла","Совпадение пути не подтверждает одинаковое назначение. Сверяйте описания, вложенные поля, группы, условия и ограничения. Переименования автоматически не определяются.",""],
   ["Разделение и объединение","При разделении старого поля на несколько новых дублируйте его строку и укажите разные пути. При объединении допускается один новый путь в нескольких строках. Поясните решение в комментарии.",""],
   ["Новые и удалённые поля","Все новые поля доступны на листах «Стало» и «Изменения». Для поля без замены оставьте новый путь пустым и укажите решение «Удалено» после проверки.",""],
   ["Ограничения","union означает альтернативы (ИЛИ), а не одновременные требования. min/max относятся к узлу в его группе; обязательность зависит также от родительских узлов и choice. Полные исходные записи сохранены на листах «Было»/«Стало».",""],
   ["Чтение длинного текста","Расширьте столбец или высоту строки; полное значение можно читать в строке формул. Исходные листы рекомендуется сохранять без изменений.",""],
   ["Сохранение работы","Сохраните книгу и загрузите её кнопкой «Загрузить сопоставления из Excel» на сравнении этих редакций. Строки «Подтверждено» принимаются сразу. Заполненные пары «Не проверено» и «Переработано» предлагаются в предпросмотре и подтверждаются кнопкой применения. Вложенные пары по относительным путям предлагаются отдельно в предпросмотре и применяются после подтверждения. Пары сохраняются в вашем браузере. Повторная выгрузка сохраняет применённые соответствия; в общий справочник они не публикуются.",""]
  ];
  const meta=sheet("О выгрузке",["Параметр / инструкция","Было / пояснение","Стало"],[32,95,95],metaRows);
  for(const n of [7,8,9])for(const c of [2,3]){const cell=meta.getCell(n,c);if(cell.value)cell.value={text:cell.value,hyperlink:cell.value}}
  mapping.properties.tabColor={argb:navy};
  return wb;
 }
 let library;
 function loadLibrary(){
  if(globalThis.ExcelJS)return Promise.resolve(globalThis.ExcelJS);
  if(!library)library=new Promise((resolve,reject)=>{
   const script=document.createElement("script");script.src=document.querySelector('meta[name="excel-export-library"]').content;
   script.onload=()=>{script.remove();if(globalThis.ExcelJS)resolve(globalThis.ExcelJS);else{library=null;reject(Error("Библиотека Excel не загрузилась."))}};
   script.onerror=()=>{script.remove();library=null;reject(Error("Не удалось загрузить библиотеку Excel. Проверьте наличие папки vendor рядом с index.html."))};
   document.head.append(script);
  });
  return library;
 }
 async function download(options){
  const ExcelJS=await loadLibrary(),wb=createWorkbook(ExcelJS,options),bytes=await wb.xlsx.writeBuffer();
  const url=URL.createObjectURL(new Blob([bytes],{type:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}));
  const link=document.createElement("a");link.href=url;link.download=filename(options.left,options.right);document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
 }
 return {constraints,edition,filename,createWorkbook,download,loadLibrary};
});
