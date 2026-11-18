/* Editable mappings remain a draft until the complete selection is applied. */
(function(root,factory){"use strict";const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.SedoMappingDraft=api})(typeof globalThis!=="undefined"?globalThis:this,function(){
 "use strict";
 const key=p=>JSON.stringify([p.beforePath,p.afterPath]);
 const clean=p=>({beforePath:p.beforePath,afterPath:p.afterPath,comment:p.comment??""});
 const fingerprint=pairs=>JSON.stringify(pairs.map(p=>JSON.stringify(clean(p))).sort());
 const isProposal=row=>row.origin==="workbook"||row.origin==="relative";
 function create({before,after,savedPairs=[],entries=[],deleted,savedDeleted,suggest=()=>({suggestions:[],conflicts:[]})}){
  const old=new Set(before.map(f=>f.path)),next=new Set(after.map(f=>f.path)),saved=fingerprint(savedPairs);
  const deletionKeys=value=>JSON.stringify(["before","after"].map(side=>[...new Set(value?.[side])].sort()));
  const deletionsChanged=deletionKeys(deleted)!==deletionKeys(savedDeleted);
  let serial=0,rows=entries.map(p=>({...p,...clean(p),id:++serial,origin:p.origin||"confirmed",selected:p.selected!==false})),blocked=new Set(),auto=new Map(),conflicts=[];
  function check(p){
   if(typeof p.beforePath!=="string"||typeof p.afterPath!=="string"||typeof(p.comment??"")!=="string"||!old.has(p.beforePath)||!next.has(p.afterPath))throw Error("Выберите существующий XML-путь в обеих схемах.");
  }
  function refresh(force){
   for(const row of rows){check(row);if(row.origin==="relative")auto.set(key(row),{id:row.id,selected:row.selected})}
   // Unconfirmed Excel rows reserve their branches but cannot infer child pairs.
   const explicit=rows.filter(r=>r.origin!=="relative"),parents=new Set(explicit.filter(r=>r.selected&&r.origin!=="workbook").map(key)),usedIds=new Set(explicit.map(r=>r.id)),proposal=suggest(explicit.map(clean),before,after,deleted);
   const relative=proposal.suggestions.filter(p=>!blocked.has(p.beforePath)&&parents.has(key({beforePath:p.parentBeforePath,afterPath:p.parentAfterPath}))).map(p=>{
    const state=auto.get(key(p)),selected=typeof force==="boolean"?force:state?.selected??true,id=state&&!usedIds.has(state.id)?state.id:++serial;usedIds.add(id);auto.set(key(p),{id,selected});return {...p,id,origin:"relative",selected};
   });
   if(explicit.length+relative.length>50000)throw Error("Слишком много пар в черновике.");
   conflicts=proposal.conflicts.filter(p=>!blocked.has(p.beforePath)&&parents.has(key({beforePath:p.parentBeforePath,afterPath:p.parentAfterPath})));
   rows=[...explicit,...relative];
  }
  function transaction(change,force){
   const previous={rows:rows.map(r=>({...r})),blocked:new Set(blocked),auto:new Map([...auto].map(([k,v])=>[k,{...v}])),serial,conflicts};
   try{const result=change();refresh(force);return result}catch(error){({rows,blocked,auto,serial,conflicts}=previous);throw error}
  }
  const find=id=>{const row=rows.find(r=>r.id===id);if(!row)throw Error("Строка черновика больше недоступна.");return row};
  function duplicate(pair,except){
   if(rows.some(r=>r.id!==except&&r.origin!=="relative"&&r.selected&&key(r)===key(pair)))throw Error("Такая выбранная пара уже есть в черновике.");
  }
  refresh();
  return {
   isProposal,
   get rows(){return rows.map(r=>({...r}))},
   get conflicts(){return conflicts.map(r=>({...r}))},
   add(pair){check(pair);duplicate(pair);return transaction(()=>{const id=++serial;rows.push({...clean(pair),id,origin:"browser",selected:true});return id})},
   edit(id,pair){check(pair);find(id);duplicate(pair,id);return transaction(()=>{rows=rows.map(r=>r.id===id?{...clean(pair),id,origin:"browser",selected:true}:r)})},
   remove(id){const row=find(id);return transaction(()=>{blocked.add(row.beforePath);rows=rows.filter(r=>r.id!==id)})},
   toggle(id,selected){find(id);return transaction(()=>{rows=rows.map(r=>r.id===id?{...r,selected:!!selected}:r)})},
   selectProposals(selected){return transaction(()=>{rows=rows.map(r=>isProposal(r)?{...r,selected:!!selected}:r)},!!selected)},
   summary(){
    const pairs=rows.filter(r=>r.selected).map(clean),errors=[],seen=new Set();
    for(const p of pairs){try{check(p)}catch(error){errors.push(error.message)}const k=key(p);if(seen.has(k))errors.push("Дубликат выбранной пары: "+p.beforePath+" → "+p.afterPath);seen.add(k)}
    return {pairs,errors,dirty:deletionsChanged||fingerprint(pairs)!==saved,selectedCount:pairs.length,savedCount:savedPairs.length};
   }
  };
 }
 return {create};
});
