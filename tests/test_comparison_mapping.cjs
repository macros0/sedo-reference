"use strict";
const assert=require("node:assert/strict"),core=require("../web/compare-core.js"),ExcelJS=require("../web/vendor/exceljs.min.js"),exporter=require("../web/comparison-export.js");
const f=(path,facets=[])=>({path,name:path.split("/").at(-1),doc:"Описание",type:"xs:string",base:"xs:string",kind:"element",min:0,max:1,facets,values:[],flags:{}});
const before=[f("old/code",[["maxLength","10"]]),f("old/child"),f("same")],after=[f("new/code",[["maxLength","20"]]),f("new/child"),f("same")];
const pair={beforePath:"old/code",afterPath:"new/code",comment:"Проверено вручную"};
const result=core.compare(before,after,[pair]);
assert.equal(result.length,3,"manual pair replaces separate removal/addition");
const change=result.find(r=>r.manual);assert.ok(change);assert.equal(change.beforePath,pair.beforePath);assert.equal(change.afterPath,pair.afterPath);assert.equal(change.status,"changed");assert.deepEqual(change.changes.map(c=>c.key),["facets"]);assert.equal(change.comment,pair.comment);
assert.equal(core.compare([f("a")],[f("b")],[{beforePath:"a",afterPath:"b"}])[0].status,"mapped");
const collision=core.compare([f("a"),f("b")],[f("b"),f("c")],[{beforePath:"a",afterPath:"b"}]);
assert.equal(collision.filter(r=>r.manual).length,1);assert.ok(collision.some(r=>r.status==="removed"&&r.path==="b"));assert.ok(collision.some(r=>r.status==="added"&&r.path==="c"));
const split=[{beforePath:"a",afterPath:"b"},{beforePath:"a",afterPath:"c"}];
assert.equal(core.compare([f("a")],[f("b"),f("c")],split).length,2);
assert.equal(core.compare([f("b"),f("c")],[f("a")],split.map(p=>({beforePath:p.afterPath,afterPath:p.beforePath}))).length,2);
assert.throws(()=>core.compare(before,after,[{beforePath:"missing",afterPath:"new/code"}]),/путь/i);
const mapping=require("../web/comparison-mapping.js");
const left={number:"101",environment:"prod",revision_id:"old-edition",document_version:"2.28"},right={number:"10110",environment:"test",revision_id:"new-edition",document_version:"3.0"};
const options={left,right,before,after};
const workbook=()=>exporter.createWorkbook(ExcelJS,{...options,results:core.compare(before,after),baseURL:"https://example.test/"});
(async()=>{
 for(const rich of [false,true])for(const reverse of [false,true]){
  const comment='  <number>1</number>\n    keep indentation\n',manual={...pair,comment};
  const wb=exporter.createWorkbook(ExcelJS,{...options,results:core.compare(before,after,[manual]),mappings:[manual],baseURL:'https://example.test/'});
  if(rich)wb.getWorksheet('Сопоставление').getCell('I6').value={richText:[{text:'  <number>1</number>\n',font:{bold:true}},{text:'    keep indentation\n'}]};
  // Service columns still accept surrounding whitespace.
  wb.getWorksheet('Сопоставление').getCell('H6').value='  Подтверждено ';
  wb.getWorksheet('Сопоставление').getCell('E6').value=' new/code ';
  const reloaded=new ExcelJS.Workbook();await reloaded.xlsx.load(await wb.xlsx.writeBuffer());
  const view=reverse?{left:right,right:left,before:after,after:before}:options;
  const parsed=mapping.parseWorkbook(reloaded,view);
  assert.deepEqual(parsed.errors,[]);
  assert.equal(parsed.pairs[0].comment,comment,'unchanged workbook preserves exact '+(rich?'rich':'plain')+' comment in either direction');
  const applied=core.compare(view.before,view.after,parsed.pairs);
  assert.equal(applied.find(row=>row.manual).comment,comment);
 }
 const wb=workbook(),sheet=wb.getWorksheet("Сопоставление");
 // Earlier workbooks only recorded the literal row decision. Keep reading
 // explicit ancestor and leaf decisions exactly as the author supplied them.
 const legacy=workbook(),legacySheet=legacy.getWorksheet("Сопоставление");
 legacySheet.getCell("H6").value="Удалено";legacySheet.getCell("H7").value="Удалено";
 for(const reversed of [false,true]){
  const view=reversed?{left:right,right:left,before:after,after:before}:options;
  const report=mapping.parseWorkbook(legacy,view),side=reversed?'after':'before';
  assert.deepEqual(report.deleted[side],['old/code','old/child']);
  assert.equal(mapping.preparePreview(report,view.before,view.after).suggestions.some(p=>report.deleted[side].includes(p[side+'Path'])),false);
 }
 sheet.getCell("E6").value=pair.afterPath;sheet.getCell("H6").value="Подтверждено";sheet.getCell("I6").value=pair.comment;
 sheet.getCell("E7").value="new/child";sheet.getCell("H7").value="Не проверено";
 sheet.getCell("F6").value="Изменённое описание не должно импортироваться";
 const pendingBook=workbook(),pendingSheet=pendingBook.getWorksheet("Сопоставление");
 pendingSheet.getCell("E6").value="new/code";pendingSheet.getCell("H6").value="Переработано";
 pendingSheet.getCell("E7").value="new/child";pendingSheet.getCell("H7").value="Не проверено";
 pendingSheet.getCell("H8").value="Требует уточнения";
 const pendingParsed=mapping.parseWorkbook(pendingBook,options);
 assert.equal(pendingParsed.pairs.length,0);assert.equal(pendingParsed.candidates.length,3);
 pendingSheet.getCell("E7").value="not-found";
 const warning=mapping.parseWorkbook(pendingBook,options);assert.equal(warning.errors.length,0);assert.match(warning.warnings.join(" "),/not-found/);assert.equal(warning.candidates.length,2);
 const bytes=await wb.xlsx.writeBuffer();mapping.preflightArchive(new Uint8Array(bytes));
 const file=new Blob([bytes]);
 let loads=0;
 globalThis.SedoComparisonExport={loadLibrary:async()=>{loads++;return ExcelJS}};
 await assert.rejects(mapping.readFile(file,options,{entry:40,total:1024*1024}),/распакован/i);
 await assert.rejects(mapping.readFile(file,options,{entry:1024*1024,total:2500}),/распакован/i);
 assert.equal(loads,0,'neither budget may let ExcelJS start');
 assert.deepEqual((await mapping.readFile(file,options)).pairs,[pair],'stream-checked workbook round trip');
 delete globalThis.SedoComparisonExport;
 const loaded=new ExcelJS.Workbook();await loaded.xlsx.load(bytes);
 const parsed=mapping.parseWorkbook(loaded,options);assert.deepEqual(parsed.pairs,[pair]);assert.equal(parsed.errors.length,0);assert.equal(parsed.ignored,2);
 const reversed=mapping.parseWorkbook(loaded,{left:right,right:left,before:after,after:before});assert.equal(reversed.reversed,true);assert.deepEqual(reversed.pairs,[{beforePath:pair.afterPath,afterPath:pair.beforePath,comment:pair.comment}]);
 assert.ok(mapping.parseWorkbook(loaded,{...options,right:{...right,revision_id:"foreign"}}).errors.length);
 sheet.getCell("E6").value="not-found";assert.match(mapping.parseWorkbook(wb,options).errors.join(" "),/not-found/);
 sheet.getCell("E6").value={formula:'"new/code"',result:"new/code"};assert.match(mapping.parseWorkbook(wb,options).errors.join(" "),/формул/i);
 sheet.getCell("E6").value="new/code";
 sheet.addRow(sheet.getRow(6).values);const duplicate=mapping.parseWorkbook(wb,options);assert.equal(duplicate.pairs.length,1);assert.equal(duplicate.duplicates,1);
 const exported=exporter.createWorkbook(ExcelJS,{...options,results:result,mappings:[pair],baseURL:"https://example.test/"});
 assert.equal(exported.getWorksheet("Сопоставление").getCell("E6").value,"new/code");assert.equal(exported.getWorksheet("Сопоставление").getCell("H6").value,"Подтверждено");assert.equal(exported.getWorksheet("Сопоставление").getCell("I6").value,pair.comment);
 assert.deepEqual(mapping.parseWorkbook(exported,options).pairs,[pair]);
 const splitOptions={left,right,before:[f("a")],after:[f("b"),f("c")]};
 const splitBook=exporter.createWorkbook(ExcelJS,{...splitOptions,mappings:split,results:core.compare(splitOptions.before,splitOptions.after,split),baseURL:"https://example.test/"});
 const splitLoaded=new ExcelJS.Workbook();await splitLoaded.xlsx.load(await splitBook.xlsx.writeBuffer());
 assert.equal(splitLoaded.getWorksheet("Сопоставление").rowCount,7);
 assert.deepEqual(mapping.parseWorkbook(splitLoaded,splitOptions).pairs,split.map(p=>({...p,comment:""})));
 assert.equal(mapping.parseWorkbook(splitLoaded,{left:right,right:left,before:splitOptions.after,after:splitOptions.before}).pairs.length,2);

 const memory=new Map(),storage={getItem:k=>memory.get(k)??null,setItem:(k,v)=>memory.set(k,v),removeItem:k=>memory.delete(k)};
 const store=mapping.createStore(()=>storage);assert.equal(store.load(options).pairs.length,0);store.save(options,[pair]);
 assert.deepEqual(store.load({...options,left:right,right:left,before:after,after:before}).pairs,reversed.pairs);
 assert.equal(store.load({...options,right:{...right,revision_id:"another-edition"}}).pairs.length,0);
 const reload=mapping.createStore(()=>storage);assert.deepEqual(reload.load(options).pairs,[pair]);reload.clear(options);assert.equal(reload.load(options).pairs.length,0);
 assert.equal(store.load(options).pairs.length,0,"reset in another tab must be visible");
 reload.save(options,[{...pair,comment:"updated in another tab"}]);
 assert.equal(store.load(options).pairs[0].comment,"updated in another tab");
 let rejectRead=false,rejectWrite=false;
 const changingStorage={
  getItem(key){if(rejectRead)throw Error("blocked read");return storage.getItem(key)},
  setItem(key,value){if(rejectWrite)throw Error("blocked write");storage.setItem(key,value)},
  removeItem(key){storage.removeItem(key)}
 };
 const session=mapping.createStore(()=>changingStorage);
 session.save(options,[pair]);rejectRead=true;
 assert.deepEqual(session.load(options).pairs,[pair],"use last successful save when reads become blocked");
 assert.ok(session.load(options).notice);
 rejectRead=false;reload.clear(options);assert.deepEqual(session.load(options).pairs,[]);
 rejectRead=true;assert.deepEqual(session.load(options).pairs,[],"another tab's confirmed reset updates the fallback");
 rejectRead=false;rejectWrite=true;assert.ok(session.save(options,[pair]));
 assert.deepEqual(session.load(options).pairs,[pair],"a session-only save has priority over readable older storage");
 const blocked=mapping.createStore(()=>{throw Error("blocked")});assert.ok(blocked.save(options,[pair]));assert.deepEqual(blocked.load(options).pairs,[pair]);assert.ok(blocked.clear(options));assert.equal(blocked.load(options).pairs.length,0);
 storage.setItem(mapping.storageKey(left,right),'not json');const corrupt=mapping.createStore(()=>storage).load(options);assert.ok(corrupt.notice);assert.equal(corrupt.pairs.length,0);
 assert.throws(()=>mapping.preflightArchive(new Uint8Array([1,2,3])),/XLSX|ZIP/i);
 assert.throws(()=>mapping.preflightArchive(new Uint8Array(10*1024*1024+1)),/10/);
 const expanded=new Uint8Array(bytes),view=new DataView(expanded.buffer);let central=-1;
 for(let i=0;i<expanded.length-46;i++)if(view.getUint32(i,true)===0x02014b50){central=i;break}
 assert.ok(central>=0);view.setUint32(central+24,0xffffffff,true);assert.throws(()=>mapping.preflightArchive(expanded),/64/);
 const fs=require("node:fs"),path=require("node:path"),root=path.resolve(__dirname,".."),catalog=JSON.parse(fs.readFileSync(path.join(root,"data/catalog.json"),"utf8"));
 const rev=n=>catalog.revisions.find(r=>r.number===n&&r.environment==="prod"&&r.status==="matched");
 const fields=r=>JSON.parse(fs.readFileSync(path.join(root,"data/fields/"+r.revision_id+".json"),"utf8")).fields;
 const historical=id=>catalog.revisions.find(r=>r.revision_id===id);
 const old=historical("a7fc25a64380f00d69fdc9a0d7d1c7c98c6ca44aaca9d87286da7226aab4d3c0"),next=historical("3065ff2469613f57e21eaea824cfd5cb7ee170d1984b8a2f898128da4bdb8109"),oldFields=fields(old),newFields=fields(next);
 const actualOptions={left:old,right:next,before:oldFields,after:newFields};
 const actualPair={beforePath:"approveSocialAssist/benefit1Approve/calculationData/downPeriodList/downPeriod/idleAverage",afterPath:"benefitResponse/benefit1/calculationData/downPeriodList/downPeriod/idleAverage",comment:"Средний заработок за период простоя"};
 const actualBook=exporter.createWorkbook(ExcelJS,{...actualOptions,results:core.compare(oldFields,newFields),baseURL:"https://example.test/"});
 const row=oldFields.findIndex(f=>f.path===actualPair.beforePath)+6;assert.ok(row>=6);
 actualBook.getWorksheet("Сопоставление").getCell(row,5).value=actualPair.afterPath;
 actualBook.getWorksheet("Сопоставление").getCell(row,8).value="Подтверждено";
 actualBook.getWorksheet("Сопоставление").getCell(row,9).value=actualPair.comment;
 actualBook.getWorksheet("Было").getCell("L2").value='[["totalDigits","999"]]';
 const actualLoaded=new ExcelJS.Workbook();await actualLoaded.xlsx.load(await actualBook.xlsx.writeBuffer());
 const actualReport=mapping.parseWorkbook(actualLoaded,actualOptions);assert.equal(actualReport.errors.length,0);assert.deepEqual(actualReport.pairs,[actualPair]);
 const actualDiff=core.compare(oldFields,newFields,actualReport.pairs).find(r=>r.manual),facets=actualDiff.changes.find(c=>c.key==="facets");
 assert.ok(facets.before.some(([k,v])=>k==="totalDigits"&&v==="22"));assert.ok(facets.after.some(([k,v])=>k==="totalDigits"&&v==="11"));
 assert.ok(!facets.before.some(([k,v])=>v==="999"),"constraints must come from catalog, not workbook");
 const sheet101=actualLoaded.getWorksheet("Сопоставление");
 sheet101.getCell(6,5).value="benefitResponse";sheet101.getCell(6,8).value="Подтверждено";
 const deletedPath="approveSocialAssist/note",deletedRow=oldFields.findIndex(f=>f.path===deletedPath)+6;
 sheet101.getCell(deletedRow,5).value=null;sheet101.getCell(deletedRow,8).value="Удалено";
 for(const opts of [actualOptions,{left:next,right:old,before:newFields,after:oldFields}]){
  const report=mapping.parseWorkbook(actualLoaded,opts),preview=mapping.preparePreview(report,opts.before,opts.after);
  assert.deepEqual(report.errors,[]);
  const fromWorkbook=p=>report.reversed?p.afterPath:p.beforePath;
  assert.ok(!preview.suggestions.some(p=>fromWorkbook(p)===deletedPath),"Excel deletion must suppress the relative proposal in both orientations");
  const suggest=mapping.suggestDescendants,drafts=require("../web/mapping-draft.js");
  const createDraft=(pairs,deleted)=>drafts.create({...opts,entries:pairs,suggest,deleted});
  const draft=createDraft(report.pairs,report.deleted),root=draft.rows.find(p=>fromWorkbook(p)==="approveSocialAssist");
  draft.toggle(root.id,false);draft.toggle(root.id,true);draft.selectProposals(true);
  const pairs=draft.summary().pairs;
  assert.ok(!pairs.some(p=>fromWorkbook(p)===deletedPath));
  assert.ok(pairs.some(p=>fromWorkbook(p)==="approveSocialAssist/changeReason"),"neighboring fields still match");
  const reopened=mapping.createStore(()=>storage);reopened.clear(opts);
  await reopened.apply(opts,[],pairs,{base:{},draft:report.deleted});
  const saved=reopened.load(opts);
  assert.ok(!createDraft(saved.pairs,saved.deleted).summary().pairs.some(p=>fromWorkbook(p)===deletedPath),"reopening saved mappings must preserve the Excel deletion");
  const reexport=exporter.createWorkbook(ExcelJS,{...opts,results:core.compare(opts.before,opts.after,saved.pairs),mappings:saved.pairs,deleted:saved.deleted,baseURL:"https://example.test/"});
  if(!report.reversed){
   const rows=reexport.getWorksheet("Сопоставление");
   assert.equal(rows.getCell(deletedRow,8).value,"Удалено");
   assert.equal(rows.getCell(deletedRow,5).value,null);
  }
  assert.deepEqual(mapping.parseWorkbook(reexport,opts).deleted,saved.deleted,"export/import preserves decisions on either side");
  const opposite={left:opts.right,right:opts.left,before:opts.after,after:opts.before};
  const back=reopened.load(opposite);
  assert.ok(!drafts.create({...opposite,entries:back.pairs,suggest,deleted:back.deleted}).summary().pairs.some(p=>(report.reversed?p.beforePath:p.afterPath)===deletedPath),"saved exclusions must reverse along with mappings");
 }
 // Public-source reproduction of the reported workbook; no user file is committed.
 const old105=rev("105"),next105=rev("10510"),a105=fields(old105),b105=fields(next105),o105={left:old105,right:next105,before:a105,after:b105};
 const book105=exporter.createWorkbook(ExcelJS,{...o105,results:core.compare(a105,b105),baseURL:"https://example.test/"}),sheet105=book105.getWorksheet("Сопоставление");
 const checked=new Set(["","status","responseInfo","registryNum","errorList","errorList/error","errorList/error/code","errorList/error/message"]);
 a105.forEach((f,i)=>{
  const relative=f.path.replace(/^approveSocialAssistResult\/?/,""),nextRelative=relative==="socialAssistNum"?"processNum":relative==="registryNum"?"batchNo":relative;
  sheet105.getCell(i+6,5).value="benefitResponseResult"+(nextRelative?"/"+nextRelative:"");
  sheet105.getCell(i+6,8).value=checked.has(relative)?"Подтверждено":relative==="socialAssistNum"?"Переработано":"Не проверено";
 });
 const loaded105=new ExcelJS.Workbook();await loaded105.xlsx.load(await book105.xlsx.writeBuffer());
 const parsed105=mapping.parseWorkbook(loaded105,o105),preview105=mapping.preparePreview(parsed105,a105,b105);
 assert.equal(parsed105.pairs.length,8);assert.equal(parsed105.candidates.length,8);assert.equal(preview105.excelCount,8);assert.equal(preview105.descendantCount,0);
 const beforeApproval=core.compare(a105,b105,parsed105.pairs);assert.equal(beforeApproval.filter(r=>r.status==="removed").length,8);
 const all105=[...parsed105.pairs,...preview105.suggestions.filter(p=>p.defaultSelected)],fixed105=core.compare(a105,b105,all105);
 assert.equal(all105.length,16);assert.equal(fixed105.filter(r=>r.status==="removed").length,0);
 assert.deepEqual(new Set(fixed105.filter(r=>r.status==="added").map(r=>r.path)),new Set(["benefitResponseResult/@specVersion","benefitResponseResult/@messageDateTime","benefitResponseResult/@*"]));
 console.log("Reported 105/10510 case: 8 confirmed + 8 filled proposals; approval yields 0 removed and 3 new attributes.");
 console.log("Real 101/10110 import: idleAverage totalDigits 22 → 11, workbook source edits ignored.");
 console.log("Manual mappings: confirmed XLSX import, pair IDs, reverse, constraints, split/merge, collisions, local persistence, reset and re-export passed.");
})().catch(e=>{console.error(e);process.exitCode=1});
