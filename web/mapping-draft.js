/* Editable mappings remain a draft until the complete selection is applied. */
(function(root,factory){"use strict";const common=typeof module==="object"&&module.exports;const api=factory(common?require("./comparison-export.js"):root.SedoComparisonExport);if(common)module.exports=api;else root.SedoMappingDraft=api})(typeof globalThis!=="undefined"?globalThis:this,function(Excel){
 "use strict";
 const key=p=>JSON.stringify([p.beforePath,p.afterPath]);
 const paths=p=>({beforePath:p.beforePath,afterPath:p.afterPath});
 const clean=p=>({beforePath:p.beforePath,afterPath:p.afterPath,comment:p.comment??""});
 const browserPair=(pair,id,origin="browser")=>({...clean(pair),id,origin,selected:true});
 const fingerprint=pairs=>JSON.stringify(pairs.map(p=>JSON.stringify(clean(p))).sort());
 const isProposal=row=>row.origin==="workbook"||row.origin==="relative";
 const isAdopted=row=>row.selected&&(row.origin==="browser"||row.origin==="adopted"||row.adopted);
 function create({before,after,savedPairs=[],entries=[],deleted,savedDeleted,savedAutomatic=[],savedDismissed=[],importedDismissed=[],suggest=()=>({suggestions:[],conflicts:[]})}){
  const old=new Set(before.map(f=>f.path)),next=new Set(after.map(f=>f.path)),saved=fingerprint(savedPairs),savedKeys=new Set(savedPairs.map(key));
  const initialKeys=new Set(entries.map(key)),pending=new Map(savedAutomatic.map(pair=>[key(pair),pair]));
  const savedExclusions=new Set(savedDismissed.map(key));
  const deletionKeys=value=>JSON.stringify(["before","after"].map(side=>[...new Set(value?.[side])].sort()));
  const deletionsChanged=deletionKeys(deleted)!==deletionKeys(savedDeleted);
  let serial=0,revision=0,rows=entries.map(p=>({...p,...clean(p),id:++serial,origin:p.origin||"confirmed",selected:p.selected!==false})),auto=new Map(),conflicts=[];
  let dismissed=new Map(importedDismissed.filter(p=>!savedExclusions.has(key(p))).map(p=>[key(p),{...paths(p),hidden:true}]));
  function check(p){
   if(typeof p.beforePath!=="string"||typeof p.afterPath!=="string"||typeof(p.comment??"")!=="string"||!old.has(p.beforePath)||!next.has(p.afterPath))throw Error("Выберите существующий XML-путь в обеих схемах.");
  }
  function checkEdit(p){check(p);Excel.checkMappingComment(p.comment??"")}
  function refresh(force){
   for(const row of rows){check(row);if(row.origin==="relative")auto.set(key(row),{id:row.id,selected:row.selected})}
   // These rows reserve their branches. A checkbox adopts only the selected
   // pair, so it cannot replace the original parent as a source of proposals.
   const explicit=rows.filter(r=>r.origin!=="relative");
   const parents=new Set(explicit.filter(r=>r.selected&&r.origin!=="workbook"&&r.origin!=="adopted").map(key));
   const exclusions=()=>[...savedDismissed,...[...dismissed.values()].filter(p=>p.hidden&&!p.pending)];
   const usedIds=new Set(explicit.map(r=>r.id)),chosen=explicit.map(clean);
   let proposal=suggest(chosen,before,after,deleted,exclusions());
   const available=p=>parents.has(key({beforePath:p.parentBeforePath,afterPath:p.parentAfterPath}));
   let promoted=false;
   for(const p of [...proposal.suggestions,...proposal.conflicts]){
    if(available(p)&&dismissed.get(key(p))?.pending){
     dismissed.set(key(p),{...paths(p),hidden:true});
     promoted=true;
    }
   }
   if(promoted)proposal=suggest(chosen,before,after,deleted,exclusions());
   const relative=proposal.suggestions.filter(available).map(p=>{
    const idKey=key(p),state=auto.get(idKey);
    const inherited=pending.get(idKey);
    const sameParent=inherited&&inherited.parentBeforePath===p.parentBeforePath&&inherited.parentAfterPath===p.parentAfterPath;
    const comment=sameParent?inherited.comment:p.comment;
    const selected=!dismissed.has(idKey)&&(typeof force==="boolean"?force:state?.selected??true);
    const id=state&&!usedIds.has(state.id)?state.id:++serial;
    usedIds.add(id);auto.set(idKey,{id,selected});return {...p,id,origin:"relative",selected,comment};
   });
   if(explicit.length+relative.length>50000)throw Error("Слишком много пар в черновике.");
   conflicts=proposal.conflicts.filter(available);
   rows=[...explicit,...relative];
  }
  function transaction(change,force){
   const previous={rows:rows.map(r=>({...r})),auto:new Map([...auto].map(([k,v])=>[k,{...v}])),serial,conflicts,dismissed:new Map(dismissed)};
   try{const result=change();refresh(force);revision++;return result}catch(error){({rows,auto,serial,conflicts,dismissed}=previous);throw error}
  }
  const find=id=>{const row=rows.find(r=>r.id===id);if(!row)throw Error("Строка черновика больше недоступна.");return row};
  function duplicate(pair,except){
   if(rows.some(r=>r.id!==except&&r.origin!=="relative"&&r.selected&&key(r)===key(pair)))throw Error("Такая выбранная пара уже есть в черновике.");
  }
  function excludeFromSuggestions(row,hidden){
   const idKey=key(row);
   if(savedKeys.has(idKey)||auto.has(idKey)||(hidden?initialKeys.has(idKey):row.origin==="workbook"&&row.selected)){
    dismissed.set(idKey,{...paths(row),hidden});
   }else if(hidden){
    // Adding and removing a new pair is a no-op unless it can be suggested again.
    dismissed.set(idKey,{...paths(row),hidden:true,pending:true});
   }
  }
  refresh();
  const initialRows=[...rows];
  return {
   isProposal,
   get revision(){return revision},
   get rows(){return rows.map(r=>({...r}))},
   get conflicts(){return conflicts.map(r=>({...r}))},
   get automaticPairs(){return rows.filter(r=>r.selected&&r.origin==="relative").map(r=>({
    ...clean(r),parentBeforePath:r.parentBeforePath,parentAfterPath:r.parentAfterPath
   }))},
   get adoptedPairs(){return rows.filter(isAdopted).map(clean)},
   // This list only suppresses proposals. Removing a previously applied pair
   // is represented by its absence in the selected rows compared to savedPairs.
   get dismissedPairs(){return [...dismissed.values()].filter(p=>!p.pending).map(paths)},
   add(pair){checkEdit(pair);duplicate(pair);return transaction(()=>{const id=++serial;dismissed.delete(key(pair));rows.push(browserPair(pair,id));return id})},
   edit(id,pair){
    checkEdit(pair);const row=find(id);duplicate(pair,id);
    return transaction(()=>{
     const sameKey=key(row)===key(pair);
     if(!sameKey)dismissed.set(key(row),{...paths(row),hidden:true});
     dismissed.delete(key(pair));
     const origin=sameKey&&row.origin==="adopted"?"adopted":"browser";
     rows=rows.map(r=>r.id===id?browserPair(pair,id,origin):r);
    });
   },
   remove(id){const row=find(id);return transaction(()=>{
    excludeFromSuggestions(row,true);rows=rows.filter(r=>r.id!==id);
   })},
   toggle(id,selected){
    const row=find(id),next=!!selected;
    return transaction(()=>{
     if(row.selected===next)return;
     if(next)dismissed.delete(key(row));else excludeFromSuggestions(row,false);
     const choice=next&&row.origin==="relative"?browserPair(row,id,"adopted"):
      {...row,selected:next,adopted:row.adopted||next&&pending.has(key(row))};
     rows=rows.map(r=>r.id===id?choice:r);
    });
   },
   selectProposals(selected){return transaction(()=>{
    rows=rows.map(r=>{
     if(!isProposal(r))return r;
     if(selected)dismissed.delete(key(r));
     return {...r,selected:!!selected};
    });
   },!!selected)},
   hasEdits(){
    return deletionsChanged||dismissed.size>0||rows.length!==initialRows.length||rows.some((row,index)=>{
     const initial=initialRows[index];
     return row.beforePath!==initial.beforePath||row.afterPath!==initial.afterPath||row.comment!==initial.comment||
      row.selected!==initial.selected||isProposal(row)!==isProposal(initial);
    });
   },
   summary(){
    const pairs=rows.filter(r=>r.selected).map(clean),errors=[],seen=new Set();
    for(const p of pairs){try{checkEdit(p)}catch(error){errors.push(p.beforePath+" → "+p.afterPath+". "+error.message)}const k=key(p);if(seen.has(k))errors.push("Дубликат выбранной пары: "+p.beforePath+" → "+p.afterPath);seen.add(k)}
    const promotesPending=rows.some(row=>isAdopted(row)&&pending.has(key(row)));
    const hasDismissals=[...dismissed.values()].some(p=>!p.pending);
    return {pairs,errors,dirty:deletionsChanged||hasDismissals||promotesPending||fingerprint(pairs)!==saved,selectedCount:pairs.length,savedCount:savedPairs.length};
   }
  };
 }
 return {create};
});
