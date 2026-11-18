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
 function suggestDescendants(pairs,before,after,deleted={}){
  validatePairs(pairs,before,after);
  const deletedBefore=new Set(deleted.before),deletedAfter=new Set(deleted.after);
  // A confirmed child block can start its own mapping below a deleted ancestor.
  const excluded=(path,parent,paths)=>{
   for(let candidate=path;candidate.length>parent.length;candidate=candidate.slice(0,candidate.lastIndexOf("/"))){
    if(paths.has(candidate))return true;
   }
   return false;
  };
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
    if(!targets.has(afterPath)||excluded(field.path,parent.beforePath,deletedBefore)||excluded(afterPath,parent.afterPath,deletedAfter))continue;
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
  const report={pairs:[],candidates:[],deleted:{before:[],after:[]},warnings:[],errors:[],ignored:0,duplicates:0,reversed:false};
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
  }catch(error){report.errors.push(error.message);report.pairs=[];report.candidates=[]}
  return report;
 }
 function preparePreview(report,before,after){
  if(report.errors?.length)return {suggestions:[],conflicts:[],excelCount:0,descendantCount:0};
  const pairs=report.pairs||[],candidates=report.candidates||[];validatePairs(candidates,before,after);
  const confirmed=new Set(pairs.map(p=>JSON.stringify([p.beforePath,p.afterPath]))),pending=new Map();
  for(const candidate of candidates){
   const key=JSON.stringify([candidate.beforePath,candidate.afterPath]);if(confirmed.has(key))continue;
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
  const available=p=>!inside(p.beforePath,oldRoots,p.parentBeforePath.length)&&!inside(p.afterPath,newRoots,p.parentAfterPath.length),relative=suggestDescendants(pairs,before,after,report.deleted);
  const descendants=relative.suggestions.filter(available).map(p=>({...p,origin:"relative",defaultSelected:true}));
  if(pairs.length+pending.size+descendants.length>MAX_ROWS)throw Error("Слишком много предложений. Разбейте книгу на несколько наборов.");
  return {suggestions:[...pending.values(),...descendants],conflicts:relative.conflicts.filter(available),excelCount:pending.size,descendantCount:descendants.length};
 }
 const draftSessions=new Map();
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
  const key=p=>JSON.stringify([p.beforePath,p.afterPath]),original=new Map(basePairs.map(p=>[key(p),p.comment]));
  for(const pair of updatedPairs){
   if(original.has(key(pair))&&original.get(key(pair))===pair.comment)continue;
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
  const key=p=>JSON.stringify([p.beforePath,p.afterPath]),map=pairs=>new Map(pairs.map(p=>[key(p),p])),equal=(a,b)=>a?.comment===b?.comment&&!!a===!!b;
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
 function createStore(getStorage){
  const lastStored=new Map(),sessionOnly=new Map(),failedReads=new Set(),compatibilityNotices=new Map();
  function readRecord(raw,options,notice=""){
   if(!raw)return {pairs:[],notice};
   const record=JSON.parse(raw);
   if(record.version!==1||!Array.isArray(record.pairs))throw Error("Неизвестный формат сохранённых соответствий.");
   const current=orient(record,options.left,options.right);validatePairs(current.pairs,options.before,options.after);
   const deleted=record.deleted||{before:[],after:[]};
   return {pairs:current.pairs,deleted:checkedDeleted(current.reversed?{before:deleted.after,after:deleted.before}:deleted,options),notice};
  }
  function mergeChanges(base,draft,current){
   const pairs=mergeDraft(base.pairs,draft.pairs,current.pairs);
   checkDeletionConflicts(base.pairs,draft.pairs,base.deleted,current.deleted,draft.deleted);
   checkDeletionConflicts(base.pairs,current.pairs,base.deleted,draft.deleted,current.deleted);
   return {pairs,deleted:mergeDeleted(base.deleted,draft.deleted,current.deleted)};
  }
  function transaction(options,update){
   const key=storageKey(options.left,options.right),locks=globalThis.navigator?.locks;
   // Every normal write (apply, reset and card undo) shares this lock. Check
   // navigation after acquisition; keep the final read and write synchronous.
   const run=()=>{if(options.isCurrent&&!options.isCurrent())throw Error("Выбранная пара редакций изменилась. Откройте предпросмотр снова.");return update()};
   if(locks?.request){compatibilityNotices.delete(key);return locks.request(key,run)}
   compatibilityNotices.set(key,"Для одновременной работы в нескольких вкладках нужен современный браузер. Применяйте изменения по очереди.");
   return run();
  }
  return {
   load(options){
    const key=storageKey(options.left,options.right);
    const pending=sessionOnly.get(key);
    let raw=pending?pending.raw:lastStored.get(key),notice=pending?.notice||compatibilityNotices.get(key)||"";
    if(!pending){
     try{raw=getStorage().getItem(key);lastStored.set(key,raw);failedReads.delete(key)}
     catch{failedReads.add(key);notice="Хранилище браузера недоступно. Соответствия будут действовать в текущем сеансе."}
    }
    try{return readRecord(raw,options,notice)}
    catch{return {pairs:[],notice:"Сохранённые соответствия повреждены или не подходят к схемам и не применены. Сбросьте их или загрузите Excel снова."}}
   },
   save(options,pairs,deleted={before:[],after:[]}){
    validatePairs(pairs,options.before,options.after);checkedDeleted(deleted,options);
    const key=storageKey(options.left,options.right);
    const encode=()=>JSON.stringify({version:1,left:options.left.revision_id,right:options.right.revision_id,pairs,deleted});
    const retain=()=>{
     const notice="Не удалось сохранить соответствия в браузере. Они действуют только в текущем сеансе; сохраните Excel для повторной загрузки.";
     sessionOnly.set(key,{raw:encode(),notice});return notice;
    };
    let storage,stored;
    try{storage=getStorage();stored=storage.getItem(key)}catch{return retain()}
    // A session-only draft and another tab may both have advanced since the
    // last durable baseline. Reconcile in the caller's current orientation.
    if((sessionOnly.has(key)||failedReads.has(key))&&lastStored.get(key)!==stored){
     const base=readRecord(lastStored.get(key),options),current=readRecord(stored,options);
     ({pairs,deleted}=mergeChanges(base,{pairs,deleted},current));
     validatePairs(pairs,options.before,options.after);checkedDeleted(deleted,options);
    }
    lastStored.set(key,stored);failedReads.delete(key);
    const raw=encode();
    try{storage.setItem(key,raw)}catch{return retain()}
    lastStored.set(key,raw);sessionOnly.delete(key);return "";
   },
   async apply(options,base,pairs,deletions){
    validatePairs(base,options.before,options.after);validatePairs(pairs,options.before,options.after);
    return transaction(options,()=>{
     const current=this.load(options),merged=mergeChanges({pairs:base,deleted:deletions?.base},{pairs,deleted:deletions?.draft},current);
     return this.save(options,merged.pairs,merged.deleted);
    });
   },
   async reset(options){return transaction(options,()=>this.clear(options))},
   clear(options){
    const key=storageKey(options.left,options.right);
    draftSessions.delete(JSON.stringify([options.left.revision_id,options.right.revision_id]));
    draftSessions.delete(JSON.stringify([options.right.revision_id,options.left.revision_id]));
    try{getStorage().removeItem(key);lastStored.set(key,null);failedReads.delete(key);sessionOnly.delete(key);return ""}
    catch{const notice="Соответствия сброшены в текущем сеансе. Хранилище браузера недоступно; после перезапуска может потребоваться повторный сброс.";sessionOnly.set(key,{raw:null,notice});return notice}
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
 function mount(container,options,saved,onChange){
  const draftKey=JSON.stringify([options.left.revision_id,options.right.revision_id]),retained=draftSessions.get(draftKey);
  const el=(tag,cls,value)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(value!==undefined)e.textContent=value;return e},button=(cls,value)=>{const e=el("button",cls,value);e.type="button";return e};
  const panel=el("section","mapping-panel"),status=el("p","meta"),preview=el("div","mapping-preview"),controls=el("div","mapping-controls"),notice=el("div","mapping-draft-notice");notice.hidden=true;
  panel.append(el("h2","","Ручные соответствия"),el("p","meta","Редактируйте пары в браузере или загрузите Excel. Правки остаются в черновике до кнопки «Применить и обновить сравнение». Файлы обрабатываются в браузере."));status.setAttribute("role","status");
  const restoreStatus=()=>{status.textContent="Применено пар: "+saved.pairs.length+(saved.notice?". "+saved.notice:"")};restoreStatus();
  const input=el("input","mapping-file");input.type="file";input.accept=".xlsx";input.hidden=true;
  const hasSaved=saved.pairs.length||saved.deleted?.before.length||saved.deleted?.after.length;
  const upload=button("swap","Загрузить сопоставления из Excel"),reset=button("swap","Сбросить сопоставления"),add=button("swap mapping-add","Добавить соответствие");reset.disabled=!hasSaved&&!saved.notice;
  let active=null,activeReport=null,generation=0,resetting=false;
  const savedReport=()=>{saved=store.load(options);return {pairs:saved.pairs,deleted:saved.deleted,candidates:[],errors:[],warnings:[],ignored:0,duplicates:0,reversed:false}};
  function showPreview(report,label,resumedSession){
   const baselineSaved=resumedSession?.saved??store.load(options),baseline=baselineSaved.pairs.map(p=>({...p}));
   const prepared=preparePreview(report,options.before,options.after),entries=[...report.pairs.map(p=>({...p,origin:"confirmed",selected:true})),...prepared.suggestions.map(p=>({...p,selected:p.defaultSelected}))];
   const draft=resumedSession?.draft??Drafts.create({before:options.before,after:options.after,savedPairs:baseline,savedDeleted:baselineSaved.deleted,entries,deleted:report.deleted,suggest:report.errors.length?()=>({suggestions:[],conflicts:[]}):suggestDescendants});
   active?.destroy();activeReport=report;
   const session=resumedSession??{draft,report,label,saved:baselineSaved,form:null};
   draftSessions.set(draftKey,session);
   active=Editor.mount({container:preview,notice,status,options,report,draft,session,manualLabel:label,onCancel:()=>{draftSessions.delete(draftKey);active?.destroy();active=null;activeReport=null;preview.replaceChildren();saved=store.load(options);restoreStatus()},onApply:async pairs=>{
    if(!options.isCurrent())throw Error("Выбранная пара редакций изменилась. Откройте предпросмотр снова.");
    await store.apply(options,baseline,pairs,{base:baselineSaved.deleted,draft:report.deleted});draftSessions.delete(draftKey);const result=await onChange();if(result===false)throw Error("Пары сохранены, но обновить результат не удалось. Повторите открытие сравнения.");
   }});
  }
  upload.onclick=()=>input.click();
  reset.onclick=async()=>{
   if(resetting||!options.isCurrent())return;resetting=true;reset.disabled=upload.disabled=add.disabled=true;if(savedSuggest)savedSuggest.disabled=true;
   try{await store.reset(options);draftSessions.delete(draftKey);active?.destroy();const result=await onChange();if(result===false)throw Error("Соответствия сброшены, но обновить результат не удалось. Откройте сравнение снова.")}
   catch(e){status.textContent="Не удалось обновить сравнение. "+e.message}
   finally{resetting=false;reset.disabled=!hasSaved&&!saved.notice;upload.disabled=add.disabled=false;if(savedSuggest)savedSuggest.disabled=false}
  };
  add.onclick=()=>{if(options.isCurrent()){if(!active||activeReport.errors.length)showPreview(savedReport(),"Ранее применено");active.openEditor()}};
  controls.append(upload,reset,input,add);panel.append(controls,status,preview);container.append(notice,panel);
  let savedSuggest=null;
  if(hasSaved){
   try{const available=suggestDescendants(saved.pairs,options.before,options.after,saved.deleted),view=button("swap mapping-suggest",available.suggestions.length||available.conflicts.length?"Предпросмотр вложенных тегов ("+available.suggestions.length+")":"Просмотреть сопоставления");
   view.onclick=()=>{if(options.isCurrent()){try{if(!active)showPreview(savedReport(),"Ранее применено")}catch(e){status.textContent="Не удалось открыть черновик. "+e.message}}};savedSuggest=view;controls.append(view)}catch(e){status.textContent+=" Не удалось подготовить предложения. "+e.message}
  }
  input.onchange=async()=>{
   const file=input.files?.[0];if(!file)return;const token=++generation;
   upload.disabled=add.disabled=reset.disabled=true;preview.inert=notice.inert=true;if(savedSuggest)savedSuggest.disabled=true;status.textContent="Читаем Excel…";
   try{const report=await readFile(file,options);if(report.errors.length)throw Error(report.errors.join(" "));if(token===generation&&options.isCurrent())showPreview(report,"Из Excel")}
   catch(e){if(token===generation&&options.isCurrent())status.textContent="Не удалось загрузить соответствия. "+e.message}
   finally{if(token===generation){upload.disabled=add.disabled=false;reset.disabled=!hasSaved&&!saved.notice;preview.inert=notice.inert=false;if(savedSuggest)savedSuggest.disabled=false;input.value=""}}
  };
  if(retained)showPreview(retained.report,retained.label,retained);
  return {hasUnapplied:()=>active?.hasUnapplied()??false};
 }
 return {preflightArchive,suggestDescendants,parseWorkbook,preparePreview,storageKey,createStore,store,readFile,mount};
});
