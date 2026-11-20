/* Local-only XLSX mappings: no uploads, no evaluation of workbook formulas. */
(function(root,factory){"use strict";const common=typeof module==="object"&&module.exports;const api=factory(common?require("./mapping-draft.js"):root.SedoMappingDraft,common?require("./mapping-editor.js"):root.SedoMappingEditor,common?require("./comparison-export.js"):root.SedoComparisonExport);if(common)module.exports=api;else root.SedoMapping=api})(typeof globalThis!=="undefined"?globalThis:this,function(Drafts,Editor,Excel){
 "use strict";
 const MAX_FILE=10*1024*1024,MAX_EXPANDED=64*1024*1024,MAX_ROWS=50000;
 const decisions=new Map(Excel.mappingDecisions.map(decision=>[decision.toLowerCase(),decision]));
 function mergeComments(previous,added){
  const combined=added&&added!==previous?[previous,added].filter(Boolean).join("\n"):previous;
  Excel.checkMappingComment(combined);
  return combined;
 }
 function preflightArchive(bytes){
  if(bytes.byteLength>MAX_FILE)throw Error("Размер XLSX превышает 10 МиБ.");
  if(bytes.byteLength<22)throw Error("Файл не является XLSX (ZIP).");
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);let end=-1;
  for(let i=bytes.byteLength-22;i>=Math.max(0,bytes.byteLength-65557);i--)if(view.getUint32(i,true)===0x06054b50&&i+22+view.getUint16(i+20,true)===bytes.byteLength){end=i;break}
  if(end<0)throw Error("Не найден каталог ZIP внутри XLSX.");
  const count=view.getUint16(end+10,true),size=view.getUint32(end+12,true),start=view.getUint32(end+16,true);
  if(view.getUint16(end+4,true)||view.getUint16(end+6,true)||count===65535||count>2000||start+size!==end)throw Error("Этот формат ZIP/XLSX не поддерживается.");
  let offset=start,total=0;const names=new Set(),entries=[];
  for(let i=0;i<count;i++){
   if(offset+46>end||view.getUint32(offset,true)!==0x02014b50)throw Error("Повреждён каталог ZIP/XLSX.");
   const expanded=view.getUint32(offset+24,true),compressed=view.getUint32(offset+20,true);
   total+=expanded;
   if(total>MAX_EXPANDED)throw Error("Заявленный распакованный объём XLSX превышает 64 МиБ.");
   if(view.getUint16(offset+8,true)&1)throw Error("XLSX с паролем не поддерживается.");
   const nameLength=view.getUint16(offset+28,true),extra=view.getUint16(offset+30,true),comment=view.getUint16(offset+32,true);
   const next=offset+46+nameLength+extra+comment;if(next>end)throw Error("Повреждён каталог ZIP/XLSX.");
   const method=view.getUint16(offset+10,true),local=view.getUint32(offset+42,true);
   if(![0,8].includes(method)||view.getUint16(offset+34,true)!==0||local+30>start||view.getUint32(local,true)!==0x04034b50||view.getUint16(local+8,true)!==method)throw Error("Неподдерживаемая или повреждённая запись ZIP/XLSX.");
   const dataStart=local+30+view.getUint16(local+26,true)+view.getUint16(local+28,true);
   if(dataStart+compressed>start)throw Error("Данные записи ZIP/XLSX выходят за границы файла.");
   entries.push({method,start:dataStart,end:dataStart+compressed,expanded});
   names.add(new TextDecoder().decode(bytes.subarray(offset+46,offset+46+nameLength)));offset=next;
  }
  if(offset!==end||!names.has("[Content_Types].xml")||!names.has("xl/workbook.xml"))throw Error("В ZIP нет структуры книги XLSX.");
  return entries;
 }
 async function checkExpandedBytes(bytes,entries,{entry=MAX_EXPANDED,total=MAX_EXPANDED}={}){
  if(typeof DecompressionStream!=="function"&&entries.some(member=>member.method===8))throw Error("Для безопасного чтения сжатого XLSX нужен браузер с потоковой распаковкой DEFLATE.");
  let archiveBytes=0;
  for(const member of entries){
   let expanded=0;
   const count=size=>{
    expanded+=size;archiveBytes+=size;
    if(expanded>entry||archiveBytes>total)throw Error("Фактический распакованный объём записи или книги XLSX превышает лимит.");
   };
   if(member.method===0)count(member.end-member.start);
   else{
    // Small compressed chunks also bound inflation queued by a single write.
    let offset=member.start;
    const compressed=new ReadableStream({pull(controller){
     if(offset===member.end){controller.close();return}
     const end=Math.min(offset+1024,member.end);controller.enqueue(bytes.subarray(offset,end));offset=end;
    }});
    const reader=compressed.pipeThrough(new DecompressionStream('deflate-raw')).getReader();
    try{
     while(true){const {value,done}=await reader.read();if(done)break;count(value.byteLength)}
    }catch(error){await reader.cancel().catch(()=>{});throw error}
    finally{reader.releaseLock()}
   }
   if(expanded!==member.expanded)throw Error("Фактический распакованный размер записи XLSX не совпадает с каталогом ZIP.");
  }
 }
 function cellText(cell,trim=true){
  const value=cell?.value;if(value===null||value===undefined)return "";
  if(typeof value==="string")return trim?value.trim():value;
  if(value&&Array.isArray(value.richText)&&value.richText.every(v=>typeof v.text==="string")){const text=value.richText.map(v=>v.text).join("");return trim?text.trim():text}
  throw Error("Нужен текст без формул, чисел и ошибок Excel.");
 }
 const pairKey=p=>JSON.stringify([p.beforePath,p.afterPath]);
 const pairPaths=p=>({beforePath:p.beforePath,afterPath:p.afterPath});
 const reversePaths=p=>({beforePath:p.afterPath,afterPath:p.beforePath});
 const reverseProposal=p=>({...p,...reversePaths(p),parentBeforePath:p.parentAfterPath,parentAfterPath:p.parentBeforePath});
 const parentKey=p=>JSON.stringify([p.parentBeforePath,p.parentAfterPath]);
 function orient(record,left,right){
  if(record.left===left.revision_id&&record.right===right.revision_id)return {pairs:record.pairs,reversed:false};
  if(record.left===right.revision_id&&record.right===left.revision_id)return {pairs:record.pairs.map(p=>({...reversePaths(p),comment:p.comment||""})),reversed:true};
  throw Error("Книга относится к другой паре редакций. Откройте указанные в ней редакции или выгрузите новую книгу.");
 }
 function validatePair(p,old,next,requireComment=true){
  if(!p||typeof p.beforePath!=="string"||typeof p.afterPath!=="string"||(requireComment&&typeof p.comment!=="string")||!old.has(p.beforePath)||!next.has(p.afterPath))throw Error("Не найден XML-путь соответствия: "+String(p?.beforePath)+" → "+String(p?.afterPath));
 }
 function validatePairs(pairs,before,after,requireComment=true){
  if(!Array.isArray(pairs)||pairs.length>MAX_ROWS)throw Error("Некорректный список соответствий.");
  const old=new Set(before.map(f=>f.path)),next=new Set(after.map(f=>f.path));
  for(const p of pairs)validatePair(p,old,next,requireComment);
 }
 function suggestDescendants(pairs,before,after,deleted={},dismissed=[]){
  validatePairs(pairs,before,after);
  const deletedBefore=new Set(deleted.before),deletedAfter=new Set(deleted.after);
  const dismissedKeys=new Set(dismissed.map(pairKey));
  // A confirmed child block can start its own mapping below a deleted ancestor.
  const excluded=(path,parent,paths)=>{
   for(let candidate=path;candidate.length>parent.length;candidate=candidate.slice(0,candidate.lastIndexOf("/"))){
    if(paths.has(candidate))return true;
   }
   return false;
  };
  const oldParents=new Map(),newParents=new Map(),unique=new Map(),targets=new Set(after.map(f=>f.path));
  for(const pair of pairs){const key=pairKey(pair);if(!unique.has(key))unique.set(key,{...pair,key})}
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
    if(!targets.has(afterPath)||excluded(field.path,parent.beforePath,deletedBefore)||excluded(afterPath,parent.afterPath,deletedAfter))continue;
    const key=JSON.stringify([field.path,afterPath]);if(seen.has(key)||dismissedKeys.has(key))continue;seen.add(key);
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
 function parseDismissals(metadata,ids,oldPaths,newPaths,reversed){
  const section=Excel.dismissalSection,dismissed=[],seen=new Set();
  let scope,count;
  metadata.eachRow((row,number)=>{
   const label=row.getCell(1).value;
   if(label===section.scope){
    if(scope)throw Error("Редакции в списке исключённых пар указаны несколько раз.");
    scope={left:cellText(row.getCell(2)),right:cellText(row.getCell(3))};
   }else if(label===section.format){
    if(count!==undefined||cellText(row.getCell(2))!==section.version)throw Error("Некорректная версия списка исключённых пар.");
    count=row.getCell(3).value;
    if(!Number.isInteger(count)||count<0||count>section.maxRows)throw Error("Некорректное число строк в списке исключённых пар.");
   }else if(label===section.pair){
    if(dismissed.length>=section.maxRows)throw Error("В списке исключённых пар больше 50000 строк.");
    try{
     const paths={beforePath:cellText(row.getCell(2),false),afterPath:cellText(row.getCell(3),false)};
     const pair=reversed?reversePaths(paths):paths;
     validatePair(pair,oldPaths,newPaths,false);
     const key=pairKey(pair);
     if(seen.has(key))throw Error("Повтор исключённой пары.");
     seen.add(key);
     dismissed.push(pair);
    }catch(error){throw Error("Список исключённых пар, строка "+number+": "+error.message)}
   }
  });
  if(scope||count!==undefined||dismissed.length){
   if(scope?.left!==ids.left||scope?.right!==ids.right)throw Error("Редакции списка исключённых пар не совпадают с редакциями книги.");
   if(dismissed.length!==count)throw Error("Число строк в списке исключённых пар не совпадает с указанным в книге.");
  }
  return dismissed;
 }
 function parseWorkbook(workbook,options){
  const report={pairs:[],candidates:[],deleted:{before:[],after:[]},dismissed:[],warnings:[],errors:[],ignored:0,duplicates:0,reversed:false};
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
   report.dismissed=parseDismissals(metadata,ids,oldPaths,newPaths,report.reversed);
   metadata.eachRow((row,number)=>{
    if(row.getCell(1).value!=="Исключённый путь «Стало»")return;
    const path=cellText(row.getCell(3)),paths=report.reversed?oldPaths:newPaths;
    if(paths.has(path))report.deleted[report.reversed?"before":"after"].push(path);
    else report.warnings.push("Лист «О выгрузке», строка "+number+": не найден исключённый путь "+path);
   });
   for(let row=headerRow+1;row<=sheet.rowCount;row++){
    try{
     const value=cellText(sheet.getCell(row,columns.decision));
     const decision=value?decisions.get(value.toLowerCase()):"Не проверено";
     if(!decision)throw Error("Неизвестное решение «"+value+"». Выберите решение из списка Excel.");
     if(decision!=="Подтверждено"){
      report.ignored++;
      if(decision==="Удалено"){
       const path=cellText(sheet.getCell(row,columns.beforePath)),paths=report.reversed?newPaths:oldPaths;
       if(paths.has(path))report.deleted[report.reversed?"after":"before"].push(path);
       else report.warnings.push("Строка "+row+": не найден удалённый путь из столбца «Было»: "+path);
      }else{
       let candidate;
       try{
        const afterPath=cellText(sheet.getCell(row,columns.afterPath));
        if(afterPath){
         const pair={beforePath:cellText(sheet.getCell(row,columns.beforePath)),afterPath,comment:cellText(sheet.getCell(row,columns.comment),false)};
         if(report.reversed)[pair.beforePath,pair.afterPath]=[pair.afterPath,pair.beforePath];
         validatePair(pair,oldPaths,newPaths);candidate={...pair,decision,row};
        }
       }catch(error){report.warnings.push("Строка "+row+": неподтверждённая пара не предложена. "+error.message)}
       if(candidate){Excel.checkMappingComment(candidate.comment);report.candidates.push(candidate)}
      }
      continue;
     }
     const pair={beforePath:cellText(sheet.getCell(row,columns.beforePath)),afterPath:cellText(sheet.getCell(row,columns.afterPath)),comment:cellText(sheet.getCell(row,columns.comment),false)};
     if(report.reversed)[pair.beforePath,pair.afterPath]=[pair.afterPath,pair.beforePath];
     validatePair(pair,oldPaths,newPaths);
     Excel.checkMappingComment(pair.comment);
     const key=JSON.stringify([pair.beforePath,pair.afterPath]);
     if(seen.has(key)){
      report.duplicates++;const existing=seen.get(key);existing.comment=mergeComments(existing.comment,pair.comment);continue;
     }
     seen.set(key,pair);report.pairs.push(pair);
    }catch(error){report.errors.push("Строка "+row+": "+error.message)}
   }
  }catch(error){report.errors.push(error.message);report.pairs=[];report.candidates=[];report.dismissed=[]}
  return report;
 }
 function preparePreview(report,before,after,dismissed=[]){
  if(report.errors?.length)return {suggestions:[],conflicts:[],excelCount:0,descendantCount:0};
  const pairs=report.pairs||[],candidates=report.candidates||[];validatePairs(candidates,before,after);
  const exclusions=[...dismissed,...(report.dismissed??[])];
  const unavailable=new Set([...pairs,...exclusions].map(pairKey)),pending=new Map();
  for(const candidate of candidates){
   const key=pairKey(candidate);if(unavailable.has(key))continue;
   const existing=pending.get(key);
   if(existing){
    try{existing.comment=mergeComments(existing.comment,candidate.comment)}
    catch(error){throw Error("Строка "+candidate.row+": "+error.message)}
    if(candidate.decision==="Требует уточнения")existing.defaultSelected=false;
   }else pending.set(key,{...candidate,origin:"workbook",defaultSelected:candidate.decision!=="Требует уточнения"});
  }
  // A filled manual branch should not acquire a different automatically guessed target.
  const oldRoots=new Set([...pending.values()].map(p=>p.beforePath)),newRoots=new Set([...pending.values()].map(p=>p.afterPath));
  const inside=(path,roots,minLength)=>{
   if(path.length>=minLength&&roots.has(path))return true;
   for(let slash=path.lastIndexOf("/");slash>=minLength;slash=path.lastIndexOf("/",slash-1)){if(roots.has(path.slice(0,slash)))return true;if(slash===0)break}
   return false;
  };
  const available=p=>!inside(p.beforePath,oldRoots,p.parentBeforePath.length)&&!inside(p.afterPath,newRoots,p.parentAfterPath.length),relative=suggestDescendants(pairs,before,after,report.deleted,exclusions);
  const descendants=relative.suggestions.filter(available).map(p=>({...p,origin:"relative",defaultSelected:true}));
  if(pairs.length+pending.size+descendants.length>MAX_ROWS)throw Error("Слишком много предложений. Разбейте книгу на несколько наборов.");
  return {suggestions:[...pending.values(),...descendants],conflicts:relative.conflicts.filter(available),excelCount:pending.size,descendantCount:descendants.length};
 }
 const draftSessions=new Map();
 function sessionHasEdits(session){
  if(!session)return false;
  const {draft,form,fromWorkbook}=session;
  if(draft.hasEdits()||(fromWorkbook&&draft.summary().dirty))return true;
  if(!form)return false;
  if(form.id===null)return !!(form.beforePath||form.afterPath||form.comment);
  const row=draft.rows.find(row=>row.id===form.id);
  return row?.beforePath!==form.beforePath||row?.afterPath!==form.afterPath||row?.comment!==form.comment;
 }
 const storageKey=(left,right)=>"sedo-field-mappings:v1:"+[left.revision_id,right.revision_id].sort().map(encodeURIComponent).join(":");
 function mergeDeleted(base,draft,current){
  const result={};
  for(const side of ["before","after"]){
   const previous=new Set(base?.[side]),proposed=new Set(draft?.[side]),latest=new Set(current?.[side]);
   for(const path of previous)if(!proposed.has(path))latest.delete(path);
   for(const path of proposed)if(!previous.has(path))latest.add(path);
   result[side]=[...latest];
  }
  return result;
 }
 function mergeDismissed(base=[],draft=[],current=[],pairs){
  const previous=new Set(base.map(pairKey)),proposed=new Map(draft.map(p=>[pairKey(p),p])),latest=new Map(current.map(p=>[pairKey(p),p]));
  for(const key of previous)if(!proposed.has(key))latest.delete(key);
  for(const [key,pair] of proposed)if(!previous.has(key))latest.set(key,pair);
  for(const pair of pairs)latest.delete(pairKey(pair));
  return [...latest.values()];
 }
 function checkedDeleted(deleted,options){
  for(const side of ["before","after"]){
   const paths=deleted[side],known=new Set(options[side].map(f=>f.path));
   if(!Array.isArray(paths)||paths.length>MAX_ROWS||paths.some(path=>!known.has(path)))throw Error("Неизвестный путь с решением «Удалено».");
  }
  return deleted;
 }
 function checkDeletionConflicts(basePairs,updatedPairs,baseDeleted,addedByOther,alreadySelected){
  const excluded={};
  for(const side of ["before","after"]){
   const known=new Set([...(baseDeleted?.[side]||[]),...(alreadySelected?.[side]||[])]);
   excluded[side]=new Set((addedByOther?.[side]||[]).filter(path=>!known.has(path)));
  }
  if(!excluded.before.size&&!excluded.after.size)return;
  const original=new Map(basePairs.map(p=>[pairKey(p),p.comment]));
  for(const pair of updatedPairs){
   if(original.has(pairKey(pair))&&original.get(pairKey(pair))===pair.comment)continue;
   for(const side of ["before","after"]){
    let path=pair[side+"Path"];
    while(path){
     if(excluded[side].has(path))throw Error("Соответствия изменены в другой вкладке. Конфликт: для пути "+path+" одновременно выбраны решение «Удалено» и пара "+pair.beforePath+" → "+pair.afterPath+". Черновик сохранён в этом окне. Сверьте изменения перед повторным применением.");
     const parent=path.lastIndexOf("/");if(parent<0)break;path=path.slice(0,parent);
    }
   }
  }
 }
 function mergeDraft(base,draft,current){
  const map=pairs=>new Map(pairs.map(p=>[pairKey(p),p])),equal=(a,b)=>a?.comment===b?.comment&&!!a===!!b;
  const original=map(base),local=map(draft),latest=map(current),changes=next=>[...new Set([...original.keys(),...next.keys()])].filter(k=>!equal(original.get(k),next.get(k)));
  const ours=changes(local),theirs=changes(latest),conflicts=new Map(),sources=new Map(),targets=new Map();
  const add=(index,path,key)=>{if(!index.has(path))index.set(path,[]);index.get(path).push(key)};
  for(const k of theirs){const p=latest.get(k)||original.get(k);add(sources,p.beforePath,k);add(targets,p.afterPath,k)}
  for(const a of ours){
   // A remap changes two keys (remove the old pair and add the new one).
   // Shared endpoints only conflict when a final pair or its comment differs.
   const x=local.get(a)||original.get(a),conflict=b=>!equal(local.get(a),latest.get(a))||!equal(local.get(b),latest.get(b));
   if((sources.get(x.beforePath)||[]).some(conflict)||(targets.get(x.afterPath)||[]).some(conflict))conflicts.set(a,x);
  }
  if(conflicts.size){
   const describe=(pairs,field)=>{const variants=pairs.filter(p=>p.beforePath===field.beforePath||p.afterPath===field.afterPath);return variants.length?variants.map(p=>p.beforePath+' → '+p.afterPath+' · комментарий: '+JSON.stringify(p.comment)).join('; '):'соответствие удалено'};
   const details=[...conflicts.values()].slice(0,5).map(field=>'Конфликт: '+field.beforePath+' → '+field.afterPath+'. Сохранено в другой вкладке: '+describe(current,field)+'. В этом черновике: '+describe(draft,field)+'.');
   if(conflicts.size>5)details.push('Остальные конфликтующие правки: '+(conflicts.size-5)+'.');
   throw Error('Соответствия изменены в другой вкладке. '+details.join('\n')+' Черновик сохранён в этом окне. Сверьте изменения перед повторным применением.');
  }
  for(const k of ours){if(local.has(k))latest.set(k,local.get(k));else latest.delete(k)}
  return [...latest.values()];
 }
 function withoutAutomatic(snapshot,automatic=snapshot.automatic??[]){
  if(!automatic.length)return snapshot;
  const comments=new Map(automatic.map(p=>[pairKey(p),p.comment]));
  return {...snapshot,pairs:snapshot.pairs.filter(p=>comments.get(pairKey(p))!==p.comment)};
 }
 function createStore(getStorage){
  const lastStored=new Map(),sessionOnly=new Map(),compatibilityNotices=new Map();
  const damaged="Сохранённые соответствия повреждены или не подходят к схемам и не применены. Сбросьте их или загрузите Excel снова.";
  const unavailable="Хранилище браузера недоступно. Соответствия будут действовать в текущем сеансе.";
  function readRecord(raw,options){
   const record=JSON.parse(raw);
   if(record.version!==1||!Array.isArray(record.pairs))throw Error("Неизвестный формат сохранённых соответствий.");
   const current=orient(record,options.left,options.right);validatePairs(current.pairs,options.before,options.after);
   const deleted=record.deleted||{before:[],after:[]};
   const storedDismissed=record.dismissed??[],dismissed=current.reversed?storedDismissed.map(reversePaths):storedDismissed;
   validatePairs(dismissed,options.before,options.after,false);
   return {pairs:current.pairs,deleted:checkedDeleted(current.reversed?{before:deleted.after,after:deleted.before}:deleted,options),dismissed};
  }
  function checkedRecord(raw,options){
   const left=options.left.revision_id;
   if(raw===null)return {raw,left,state:"missing",snapshot:{pairs:[]}};
   try{return {raw,left,state:"valid",snapshot:readRecord(raw,options)}}
   catch{return {raw,left,state:"invalid",snapshot:{pairs:[]}}}
  }
  function readStored(key,options){
   const cached=lastStored.get(key);
   let storage,raw;
   try{storage=getStorage();raw=storage.getItem(key)??null}
   catch{return {record:cached,notice:unavailable}}
   const record=cached?.raw===raw?cached:checkedRecord(raw,options);
   lastStored.set(key,record);
   return {storage,record,notice:record.state==="invalid"?damaged:compatibilityNotices.get(key)||""};
  }
  function orientSnapshot(record,options){
   if(!record)return {pairs:[]};
   const {pairs,deleted,dismissed}=record.snapshot;
   const reversed=record.left!==options.left.revision_id;
   const orientPair=reversed?reversePaths:pairPaths;
   return {pairs:pairs.map(p=>({...orientPair(p),comment:p.comment})),
    ...(deleted?{deleted:{before:[...(reversed?deleted.after:deleted.before)],after:[...(reversed?deleted.before:deleted.after)]}}:{}),
    ...(dismissed?{dismissed:dismissed.map(orientPair)}:{})};
  }
  function readSession(pending,options){
   const orientProposal=pending.left===options.left.revision_id?p=>({...p}):reverseProposal;
   const automatic=pending.automatic.map(orientProposal);
   return {...orientSnapshot(pending,options),notice:pending.notice,automatic,pending:true,
    storageBaseline:storageBaseline(pending.baseline)};
  }
  const storageBaseline=record=>record?{state:record.state,raw:record.raw}:null;
  function mergeChanges(base,draft,current){
   const selected=draft.pairs;
   // Only removing a pair from the original selection is an explicit deletion.
   // Dismissing a proposal changes exclusions and preserves a concurrent pair.
   const pairs=mergeDraft(base.pairs,selected,current.pairs);
   checkDeletionConflicts(base.pairs,selected,base.deleted,current.deleted,draft.deleted);
   checkDeletionConflicts(base.pairs,current.pairs,base.deleted,draft.deleted,current.deleted);
   const deleted=mergeDeleted(base.deleted,draft.deleted,current.deleted);
   const dismissed=mergeDismissed(base.dismissed,draft.dismissed,current.dismissed,pairs);
   return {pairs,deleted,dismissed};
  }
  function publish(options,key,stored,baseline,{pairs,deleted,dismissed},proposals=[],reset=false){
   // Check both the session and durable result against the same final parents
   // and exclusions. Pending descendants cannot establish parents themselves.
   let automatic=[];
   if(proposals.length){
    const eligible=new Map(suggestDescendants(pairs,options.before,options.after,deleted,dismissed).suggestions.map(p=>[pairKey(p),parentKey(p)]));
    automatic=proposals.filter(p=>eligible.get(pairKey(p))===parentKey(p));
    pairs=[...pairs,...automatic.map(p=>({...pairPaths(p),comment:p.comment}))];
   }
   validatePairs(pairs,options.before,options.after);checkedDeleted(deleted,options);validatePairs(dismissed,options.before,options.after,false);
   const left=options.left.revision_id,right=options.right.revision_id;
   const snapshot=orientSnapshot({left,snapshot:{pairs,deleted,dismissed}},options);
   const clear=reset&&!pairs.length&&!deleted.before.length&&!deleted.after.length&&!dismissed.length;
   if(stored.storage){
    try{
     if(clear){stored.storage.removeItem(key);lastStored.set(key,checkedRecord(null,options))}
     else{
      const raw=JSON.stringify({version:1,left,right,pairs,deleted,...(dismissed.length?{dismissed}:{})});
      stored.storage.setItem(key,raw);
      lastStored.set(key,{raw,left,state:"valid",snapshot});
     }
     sessionOnly.delete(key);return "";
    }catch{/* Keep the validated snapshot available for the next transaction. */}
   }
   const notice=clear?"Соответствия сброшены только в текущем сеансе. Повторите сохранение, когда хранилище станет доступно.":
    "Не удалось сохранить соответствия в браузере. Они действуют только в текущем сеансе; сохраните Excel для повторной загрузки.";
   sessionOnly.set(key,{snapshot,notice,left,automatic,baseline,reset});return notice;
  }
  function commit(options,opened,prepare){
   return transaction(options,(key,stored,pending)=>{
    if(!prepare&&!pending)return "";
    const expected=pending?[opened,pending.baseline??null]:[opened];
    const record=stored.record;
    if(record?.state==="invalid"&&expected.some(base=>base!==undefined&&
      (base?.state!=="invalid"||base.raw!==record.raw))){
     throw Error("Сохранённые соответствия изменились и повреждены. Исправьте или сбросьте запись в другой вкладке и повторите сохранение.");
    }
    const current=pending?readSession(pending,options):orientSnapshot(record,options);
    let {snapshot,proposals}=prepare?prepare(current):{snapshot:withoutAutomatic(current),proposals:current.automatic};
    // A failed write saves the rebased operation together with the durable
    // record it incorporated, so that retry can merge just the new changes.
    const baseline=pending&&!stored.storage?pending.baseline:record;
    if(pending&&stored.storage&&pending.baseline?.raw!==baseline.raw){
     snapshot=mergeChanges(orientSnapshot(pending.baseline,options),snapshot,orientSnapshot(baseline,options));
    }
    return publish(options,key,stored,baseline,snapshot,proposals,pending?.reset);
   });
  }
  function transaction(options,update){
   const key=storageKey(options.left,options.right),locks=globalThis.navigator?.locks;
   // Every normal write (apply, reset and card undo) shares this lock. Check
   // navigation after acquisition; keep the final read and write synchronous.
   const run=()=>{
    if(options.isCurrent&&!options.isCurrent())throw Error("Выбранная пара редакций изменилась. Откройте предпросмотр снова.");
    return update(key,readStored(key,options),sessionOnly.get(key));
   };
   if(locks?.request){compatibilityNotices.delete(key);return locks.request(key,run)}
   compatibilityNotices.set(key,"Для одновременной работы в нескольких вкладках нужен современный браузер. Применяйте изменения по очереди.");
   return run();
  }
  return {
   load(options){
    const key=storageKey(options.left,options.right);
    const pending=sessionOnly.get(key);
    if(pending)return readSession(pending,options);
    const stored=readStored(key,options);
    return {...orientSnapshot(stored.record,options),notice:stored.notice,storageBaseline:storageBaseline(stored.record)};
   },
   save(options,pairs,deleted={before:[],after:[]},dismissed=[]){
    const key=storageKey(options.left,options.right);
    const stored=readStored(key,options);
    return publish(options,key,stored,stored.record,{pairs,deleted,dismissed});
   },
   async apply(options,base,pairs,deletions,{removed=[],dismissed=removed,automatic=[],adopted=[],baseAutomatic,baseDismissed,opened}={}){
    validatePairs(base,options.before,options.after);validatePairs(pairs,options.before,options.after);
    validatePairs(dismissed,options.before,options.after,false);
    const key=storageKey(options.left,options.right),pending=sessionOnly.get(key);
    const basis=opened===undefined?storageBaseline(pending?pending.baseline:lastStored.get(key)):opened;
    return commit(options,basis,current=>{
     const latestDismissed=baseDismissed??current.dismissed??[];
     const nextDismissed=[...new Map([...latestDismissed,...dismissed].map(p=>[pairKey(p),pairPaths(p)])).values()];
     // Merge explicit edits before testing any inference against its parent.
     // A preview can predate a session edit made in the reverse comparison.
     const inherited=baseAutomatic??current.automatic??[];
     const selected=new Map([...inherited,...automatic].map(p=>[pairKey(p),p]));
     for(const pair of adopted)selected.delete(pairKey(pair));
     const draftAutomatic=pairs.flatMap(pair=>{
      const proposal=selected.get(pairKey(pair));
      return proposal?.comment===pair.comment?[proposal]:[];
     });
     const proposals=new Map((current.automatic??[]).map(p=>[pairKey(p),p]));
     for(const pair of inherited)proposals.delete(pairKey(pair));
     for(const pair of draftAutomatic)proposals.set(pairKey(pair),pair);
     const baseline=withoutAutomatic({pairs:base,deleted:deletions?.base,dismissed:latestDismissed},inherited);
     const draft=withoutAutomatic({pairs,deleted:deletions?.draft,dismissed:nextDismissed},draftAutomatic);
     const merged=mergeChanges(baseline,draft,withoutAutomatic(current));
     return {snapshot:merged,proposals:[...proposals.values()]};
    });
   },
   async retry(options){
    return commit(options);
   },
   async reset(options,discardDrafts){
    return transaction(options,(key,stored)=>{
     const notice=publish(options,key,stored,stored.record,{pairs:[],deleted:{before:[],after:[]},dismissed:[]},[],true);
     discardDrafts?.();
     return notice;
    });
   },
   clear(options){
    const key=storageKey(options.left,options.right);
    const stored=readStored(key,options);
    return publish(options,key,stored,stored.record,{pairs:[],deleted:{before:[],after:[]},dismissed:[]},[],true);
   }
  };
 }
 const store=createStore(()=>globalThis.localStorage);
 async function readFile(file,options,limits){
  if(file.size>MAX_FILE)throw Error("Размер XLSX превышает 10 МиБ.");
  const bytes=new Uint8Array(await file.arrayBuffer());
  await checkExpandedBytes(bytes,preflightArchive(bytes),limits);
  const ExcelJS=await globalThis.SedoComparisonExport.loadLibrary(),workbook=new ExcelJS.Workbook();
  await workbook.xlsx.load(bytes);return parseWorkbook(workbook,options);
 }
 // Routing to a field remounts the comparison. Keep its unapplied draft in
 // this page session, independently for each ordered pair of original IDs.
 function mount(container,options,comparisonSaved,onChange,{openSaved=false,onSavedChange=()=>{}}={}){
  const draftKey=JSON.stringify([options.left.revision_id,options.right.revision_id]),retained=draftSessions.get(draftKey);
  let saved=comparisonSaved;
  const el=(tag,cls,value)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(value!==undefined)e.textContent=value;return e},button=(cls,value)=>{const e=el("button",cls,value);e.type="button";return e};
  const panel=el("section","mapping-panel"),status=el("p","meta"),preview=el("div","mapping-preview"),controls=el("div","mapping-controls"),notice=el("div","mapping-draft-notice");notice.hidden=true;
  panel.append(el("h2","","Ручные соответствия"),el("p","meta","Редактируйте пары в браузере или загрузите Excel. Правки остаются в черновике до кнопки «Применить и обновить сравнение». Файлы обрабатываются в браузере."));status.setAttribute("role","status");
  const restoreStatus=()=>{status.textContent="Применено пар: "+saved.pairs.length+(saved.notice?". "+saved.notice:"")};restoreStatus();
  const input=el("input","mapping-file");input.type="file";input.accept=".xlsx";input.hidden=true;
  const hasSaved=()=>saved.pairs.length||saved.deleted?.before.length||saved.deleted?.after.length;
  const canReset=()=>hasSaved()||saved.dismissed?.length||saved.notice;
  const upload=button("swap","Загрузить сопоставления из Excel"),reset=button("swap","Сбросить сопоставления"),add=button("swap mapping-add","Добавить соответствие");reset.disabled=!canReset();
  const retry=button("swap mapping-retry","Повторить сохранение в браузере");
  let active=null,savedSuggest=null,operation=null,externalChange=false;
  const canStart=()=>!operation&&options.isCurrent();
  const owns=session=>options.isCurrent()&&draftSessions.get(draftKey)===session;
  const transactionOptions=session=>({...options,isCurrent:()=>owns(session)});
  function updateRetry(){retry.hidden=!saved.pending||!!active;retry.disabled=!!operation}
  function setOperation(next){
   operation=next;
   upload.disabled=add.disabled=!!next;
   reset.disabled=!!next||!canReset();
   if(savedSuggest)savedSuggest.disabled=!!next;
   preview.inert=!!next;
   notice.inert=!!next&&next!=="apply";
   active?.setApplying(next==="apply"||next==="undo");
   updateRetry();
   if(!next&&externalChange&&options.isCurrent())refreshSaved();
  }
  function refreshSaved(){
   if(operation){externalChange=true;return}
   externalChange=false;
   const latest=store.load(options);
   if(JSON.stringify(saved)===JSON.stringify(latest))return;
   saved=latest;
   reset.disabled=!canReset();
   updateSavedSuggest();
   updateRetry();
   if(active)active.refreshSaved();else restoreStatus();
   onSavedChange(saved);
  }
  function createSession(report,fromWorkbook,baselineSaved){
   const baseline=baselineSaved.pairs.map(p=>({...p})),dismissed=baselineSaved.dismissed;
   const savedAutomatic=baselineSaved.automatic??[];
   const explicit=fromWorkbook?report:withoutAutomatic(report,savedAutomatic);
   const prepared=preparePreview(explicit,options.before,options.after,dismissed);
   const entries=[
    ...explicit.pairs.map(p=>({...p,origin:"confirmed",selected:true,adopted:fromWorkbook})),
    ...prepared.suggestions.map(p=>({...p,selected:p.defaultSelected}))
   ];
   const draft=Drafts.create({before:options.before,after:options.after,savedPairs:baseline,savedDeleted:baselineSaved.deleted,savedAutomatic,
    savedDismissed:dismissed,importedDismissed:report.dismissed,entries,deleted:report.deleted,suggest:suggestDescendants});
   return {draft,report,fromWorkbook,saved:baselineSaved,form:null};
  }
  function showPreview(session){
   const {report,fromWorkbook,draft,saved:baseline}=session;
   active?.destroy();
   draftSessions.set(draftKey,session);
   active=Editor.mount({container:preview,notice,status,options,report,draft,session,manualLabel:fromWorkbook?"Из Excel":"Ранее применено",panelAdd:add,getSavedCount:()=>saved.pairs.length,getSavedNotice:()=>saved.notice,getPending:()=>saved.pending,onCancel:()=>{
    if(operation||!owns(session))return;
    draftSessions.delete(draftKey);active.destroy();active=null;preview.replaceChildren();refreshSaved();restoreStatus();updateRetry();
   },onApply:async pairs=>{
    if(operation)return;
    if(!owns(session))throw Error("Выбранная пара редакций изменилась. Откройте предпросмотр снова.");
    setOperation("apply");
    try{
     await store.apply(transactionOptions(session),baseline.pairs,pairs,{base:baseline.deleted,draft:report.deleted},{
      dismissed:draft.dismissedPairs,automatic:draft.automaticPairs,adopted:draft.adoptedPairs,
      baseAutomatic:baseline.automatic??[],baseDismissed:baseline.dismissed??[],opened:baseline.storageBaseline
     });
     if(!owns(session))return;
     draftSessions.delete(draftKey);
     const result=await onChange();
     if(result===false)throw Error("Пары сохранены, но обновить результат не удалось. Повторите открытие сравнения.");
     active.destroy();
    }finally{setOperation(null)}
   }});
   updateRetry();
  }
  function showSavedPreview(){
   refreshSaved();
   const report={pairs:saved.pairs,deleted:saved.deleted,candidates:[],errors:[],warnings:[],ignored:0,duplicates:0,reversed:false};
   showPreview(createSession(report,false,saved));
  }
  const hasUnapplied=()=>!!operation||(active?.hasUnapplied()??false)||sessionHasEdits(draftSessions.get(draftKey));
  async function undoPair(pair,refresh){
   if(!options.isCurrent())throw Error("Выбранная пара редакций изменилась. Откройте сравнение снова.");
   if(hasUnapplied())throw Error("Сначала примените или отмените черновик соответствий выше, чтобы не потерять правки.");
   const session=draftSessions.get(draftKey),baseline=saved.pairs;
   const remaining=baseline.filter(p=>p.beforePath!==pair.beforePath||p.afterPath!==pair.afterPath);
   setOperation("undo");
   try{
    await store.apply(transactionOptions(session),baseline,remaining,undefined,{
     dismissed:[pair],baseAutomatic:saved.automatic??[],baseDismissed:saved.dismissed??[],opened:saved.storageBaseline
    });
    if(!owns(session))return;
    draftSessions.delete(draftKey);
    if(await refresh(!!active)===false)throw Error("Пара отменена, но обновить сравнение не удалось. Откройте его снова.");
    active?.destroy();
   }finally{setOperation(null)}
  }
  upload.onclick=()=>{if(canStart())input.click()};
  reset.onclick=async()=>{
   if(!canStart())return;
   const reverseKey=JSON.stringify([options.right.revision_id,options.left.revision_id]);
   const targets=(reverseKey===draftKey?[draftKey]:[draftKey,reverseKey]).map(key=>{
    const session=draftSessions.get(key);
    return {key,session,form:session?.form,revision:session?.draft.revision};
   });
   const changed=targets.filter(({session})=>sessionHasEdits(session));
   if(changed.length){
    const directions=changed.map(({key})=>key===draftKey?"текущее направление":"обратное направление");
    if(!globalThis.confirm("Сбросить сохранённые сопоставления и удалить черновики с неприменёнными правками? Будут затронуты: "+directions.join(", ")+"."))
     return;
   }
   const resetOptions={...options,isCurrent:()=>options.isCurrent()&&targets.every(({key,session,form,revision})=>
    draftSessions.get(key)===session&&session?.form===form&&session?.draft.revision===revision)};
   setOperation("reset");
   try{
    await store.reset(resetOptions,()=>{for(const {key} of targets)draftSessions.delete(key)});
    if(!owns(undefined))return;
    active?.destroy();
    const result=await onChange();
    if(result===false)throw Error("Соответствия сброшены, но обновить результат не удалось. Откройте сравнение снова.");
   }catch(e){if(options.isCurrent())status.textContent="Не удалось обновить сравнение. "+e.message}
   finally{setOperation(null)}
  };
  retry.onclick=async()=>{
   if(!canStart()||!saved.pending||active)return;
   setOperation("retry");
   try{
    await store.retry(transactionOptions(undefined));
    if(owns(undefined)&&await onChange()===false)throw Error("Не удалось обновить результат. Повторите открытие сравнения.");
   }catch(e){if(owns(undefined)){restoreStatus();status.textContent+=" "+e.message}}
   finally{setOperation(null);if(owns(undefined))refreshSaved()}
  };
  add.onclick=()=>{if(canStart()){if(!active)showSavedPreview();active.openEditor()}};
  controls.append(upload,reset,input,add,retry);panel.append(controls,status,preview);container.append(notice,panel);updateRetry();
  function updateSavedSuggest(){
   if(!hasSaved()){savedSuggest?.remove();savedSuggest=null;return}
   try{
    const available=suggestDescendants(withoutAutomatic(saved).pairs,options.before,options.after,saved.deleted,saved.dismissed);
    const label=available.suggestions.length||available.conflicts.length?"Предпросмотр вложенных тегов ("+available.suggestions.length+")":"Просмотреть сопоставления";
    if(savedSuggest){savedSuggest.textContent=label;return}
    savedSuggest=button("swap mapping-suggest",label);
    savedSuggest.onclick=()=>{if(canStart()){try{if(!active)showSavedPreview()}catch(e){status.textContent="Не удалось открыть черновик. "+e.message}}};
    controls.append(savedSuggest);
   }catch(e){status.textContent+=" Не удалось подготовить предложения. "+e.message}
  }
  updateSavedSuggest();
  input.onchange=async()=>{
   const file=input.files?.[0];input.value="";
   if(!file||!canStart())return;
   refreshSaved();
   const baseline=saved,session=draftSessions.get(draftKey),previousStatus=status.textContent;
   setOperation("import");status.textContent="Читаем Excel…";
   try{
    const report=await readFile(file,options);
    if(!owns(session))return;
    if(report.errors.length)throw Error(report.errors.join(" "));
    const replacement=createSession(report,true,baseline);
    if(sessionHasEdits(session)&&!globalThis.confirm("Заменить текущий черновик сопоставлений данными из Excel? Неприменённые правки будут потеряны.")){
     status.textContent=previousStatus;return;
    }
    if(owns(session))showPreview(replacement);
   }catch(e){if(owns(session))status.textContent="Не удалось загрузить соответствия. "+e.message}
   finally{setOperation(null)}
  };
  if(retained)showPreview(retained);
  else if(openSaved)showSavedPreview();
  return {hasUnapplied,undoPair,refreshSaved};
 }
 return {preflightArchive,suggestDescendants,parseWorkbook,preparePreview,storageKey,createStore,store,readFile,mount};
});
