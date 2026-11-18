"use strict";
const assert=require("node:assert/strict");
const {createWorkbook,filename}=require("../web/comparison-export.js");
const ExcelJS=require("../web/vendor/exceljs.min.js");
const core=require("../web/compare-core.js");
const field=(path,doc="Описание")=>({path,name:path.split("/").at(-1),kind:"element",doc,type:"xs:string",min:0,max:1,facets:[],values:[],flags:{}});
const left={number:"101",environment:"prod",revision_id:"left123456",document_version:"2.28",document_url:"https://example.com/old.docx"};
const right={number:"10110",environment:"test",revision_id:"right123456",document_version:"3.0",document_download:"documents/new.docx"};
(async()=>{
 const {facets,bounds,enumWhitespace,union,unionFlags,notation}=require('./fixtures/comparison-xsd.cjs');
 for(const [trees,expected] of [[unionFlags.unresolved,true],[union.mixedSameLexeme,true],[union.mixedNumeric.slice(0,2),false]]){
  const [before,after]=trees;
  const workbook=createWorkbook(ExcelJS,{left,right,before,after,results:core.compare(before,after)});
  const loaded=new ExcelJS.Workbook();await loaded.xlsx.load(await workbook.xlsx.writeBuffer());
  assert.equal(JSON.stringify(loaded.getWorksheet('О выгрузке').getSheetValues()).includes('исходному тексту'),expected);
 }
 for(const trees of Object.values(notation)){
  const [before,after]=trees;
  const legacy=before.map(({valueQNames,flagQNames,...field})=>field);
  for(const [old,unresolved] of [[before,false],[legacy,true]]){
   const workbook=createWorkbook(ExcelJS,{left,right,before:old,after,results:core.compare(old,after)});
   const loaded=new ExcelJS.Workbook();await loaded.xlsx.load(await workbook.xlsx.writeBuffer());
   const summary=JSON.stringify(loaded.getWorksheet('О выгрузке').getSheetValues());
   assert.equal(/NOTATION.*исходному тексту/.test(summary),unresolved);
   if(!unresolved)assert.match(JSON.stringify(loaded.getWorksheet('Изменения').getSheetValues()),/p:code.*urn:A.*p:code.*urn:B/);
  }
 }
 for(const [before,after] of [enumWhitespace.inherited,enumWhitespace.sameRestriction]){
  const workbook=createWorkbook(ExcelJS,{left,right,before,after,results:core.compare(before,after)});
  const sheet=workbook.getWorksheet('Изменения');
  assert.equal(sheet.rowCount,2);
  assert.equal(sheet.getCell('C2').value,'Допустимые значения');
  assert.deepEqual(JSON.parse(sheet.getCell('D2').value),[' A ']);
  assert.deepEqual(JSON.parse(sheet.getCell('E2').value),['A']);
 }
 for(const [trees,rows] of [[facets.token,1],[facets.dominatedLength,1],[facets.string,2],[facets.effectiveLength,2],[bounds.dominated,1],[bounds.changed,2]]){
  const [before,after]=trees;
  const workbook=createWorkbook(ExcelJS,{left,right,before,after,results:core.compare(before,after)});
  const loaded=new ExcelJS.Workbook();await loaded.xlsx.load(await workbook.xlsx.writeBuffer());
  assert.equal(loaded.getWorksheet('Изменения').rowCount,rows);
  assert.deepEqual(JSON.parse(loaded.getWorksheet('Было').getCell('L2').value),before[0].facets);
  assert.deepEqual(JSON.parse(loaded.getWorksheet('Стало').getCell('L2').value),after[0].facets);
 }
 const mappingCore=require('../web/comparison-mapping.js');
 const tree=['root','root/deleted','root/deleted/child','root/keep','root/deleted/explicit','root/deleted/explicit/id'].map(p=>field(p));
 const manual=[{beforePath:'root',afterPath:'root',comment:''},{beforePath:'root/deleted/explicit',afterPath:'root/deleted/explicit',comment:'explicit survives'}];
 for(const side of ['before','after']){
  const deleted={before:[],after:[],[side]:['root/deleted']};
  const original=createWorkbook(ExcelJS,{left,right,before:tree,after:tree,results:core.compare(tree,tree,manual),mappings:manual,deleted});
  const loaded=new ExcelJS.Workbook();await loaded.xlsx.load(await original.xlsx.writeBuffer());
  for(const reversed of [false,true]){
   const opts={left:reversed?right:left,right:reversed?left:right,before:tree,after:tree};
   const report=mappingCore.parseWorkbook(loaded,opts);
   assert.deepEqual(report.errors,[]);
   const expected=reversed?{before:deleted.after,after:deleted.before}:deleted;
   assert.deepEqual(report.deleted,expected,'XLSX preserves only explicit exclusion decisions');
   const reopened='root/deleted/explicit/id';
   const suggestions=exclusions=>mappingCore.suggestDescendants(report.pairs,tree,tree,exclusions).suggestions.map(p=>p.beforePath);
   assert.ok(suggestions(expected).includes(reopened));
   assert.deepEqual(suggestions(report.deleted),suggestions(expected),'reopened descendants remain eligible in either direction');
   assert.ok(![...report.pairs,...report.candidates].some(p=>p.beforePath==='root/deleted/child'),'deleted ancestors must survive XLSX roundtrip in either direction');
   assert.ok(report.pairs.some(p=>p.beforePath==='root/deleted/explicit'),'explicit child mapping survives');
   assert.ok(report.candidates.some(p=>p.beforePath==='root/keep'),'neighbor remains eligible');
   const preview=mappingCore.preparePreview(report,tree,tree);
   const selected=[...report.pairs,...preview.suggestions.filter(p=>p.defaultSelected)];
   const saved=new Map(),store=mappingCore.createStore(()=>({getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value)}));
   await store.apply(opts,[],selected,{base:{before:[],after:[]},draft:report.deleted});
   assert.ok(!store.load(opts).pairs.some(p=>p.beforePath==='root/deleted/child'),'apply does not restore excluded descendants');

  }
 }

 const wildcard={...field('root/*'),kind:'any',ns:'urn:main',flags:{processContents:'skip'}};
 const limited={...wildcard,wildcardNamespace:'##local urn:main',flags:{namespace:'##local ##targetNamespace'}};
 const namespaceBook=createWorkbook(ExcelJS,{left,right,before:[wildcard],after:[limited],results:core.compare([wildcard],[limited])});
 assert.equal(namespaceBook.getWorksheet('Было').getCell('K2').value,'##any');
 assert.equal(namespaceBook.getWorksheet('Стало').getCell('K2').value,'##local urn:main');
 const q=(uri,prefix)=>({...field('root'),type:'xs:QName',values:[[prefix+':read','']],valueQNames:[[uri,'read']],flags:{fixed:prefix+':read'},flagQNames:{fixed:[uri,'read']}});
 const qb=[q('urn:one','p')],qa=[q('urn:two','p')];
 const qbook=createWorkbook(ExcelJS,{left,right,before:qb,after:qa,results:core.compare(qb,qa),baseURL:'https://example.test/'});
 assert.match(qbook.getWorksheet('Было').getCell('C2').value,/p:read.*urn:one/s);
 assert.match(JSON.stringify(qbook.getWorksheet('Изменения').getSheetValues()),/urn:one/);
 assert.match(JSON.stringify(qbook.getWorksheet('Изменения').getSheetValues()),/urn:two/);
 const a=[field("old/root","=HYPERLINK(\"https://evil.test\")"),field("same/path"),field("old/union")];
 a[2].alternatives=[{base:"xs:string",facets:[["length","0"]],values:[]},{base:"xs:int",values:[["1","Да"]]}];
 const b=[field("new/root"),field("same/path","Изменено"),field("new/*","Звёздочка")];
 a[1].facets=[["pattern","A"],["pattern","B"]];a[1].patternGroups=[["A"],["B"]];
 b[1].facets=a[1].facets;b[1].patternGroups=[["A","B"]];
 const options={left,right,before:a,after:b,results:core.compare(a,b),baseURL:"https://example.com/reference/"};
 const caseBefore=[field('old/upper'),field('old/lower'),field('old/wildcard')],caseAfter=[field('benefitResponse/processNum','Верхний регистр'),field('benefitResponse/processnum','Нижний регистр'),field('new/*','Буквальная звёздочка')];
 const casePairs=caseBefore.map((f,i)=>({beforePath:f.path,afterPath:caseAfter[i].path}));
 const caseBook=createWorkbook(ExcelJS,{...options,before:caseBefore,after:caseAfter,mappings:casePairs,results:core.compare(caseBefore,caseAfter,casePairs)});
 const caseLoaded=new ExcelJS.Workbook();await caseLoaded.xlsx.load(await caseBook.xlsx.writeBuffer());
 for(const [row,value] of [[6,'Верхний регистр'],[7,'Нижний регистр'],[8,'Буквальная звёздочка']]){
  const cell=caseLoaded.getWorksheet('Сопоставление').getCell(row,6);
  assert.equal(cell.result,value);
  assert.ok(cell.formula.includes(`MATCH(TRUE,INDEX(EXACT(E${row},'Стало'!$A$2:$A$4),0),0)`),'serialized lookup uses case-sensitive literal equality without array-entry requirements');
 }
 const caseMapping=require('../web/comparison-mapping.js');
 assert.deepEqual(caseMapping.parseWorkbook(caseLoaded,{left,right,before:caseBefore,after:caseAfter}).pairs,casePairs.map(p=>({...p,comment:''})));
 caseLoaded.getWorksheet('Сопоставление').getCell('E6').value='benefitResponse/processNUM';
 assert.match(caseMapping.parseWorkbook(caseLoaded,{left,right,before:caseBefore,after:caseAfter}).errors.join(' '),/processNUM/,'a differently cased path is rejected by the importer');
 const bounded=groups=>[{...field('r'),contentModel:['sequence','a','b','c','d'],contentModelBounds:{kind:'sequence',min:'1',max:'1',children:groups.map(children=>({kind:'sequence',min:'0',max:'1',children}))}}];
 const beforeGroups=bounded([['a','b'],['c','d']]),afterGroups=bounded([['a'],['b','c'],['d']]);
 const groupResults=core.compare(beforeGroups,afterGroups);
 const groupBook=createWorkbook(ExcelJS,{...options,before:beforeGroups,after:afterGroups,results:groupResults});
 const decodedGroups=new ExcelJS.Workbook();await decodedGroups.xlsx.load(await groupBook.xlsx.writeBuffer());
 const groupDiff=decodedGroups.getWorksheet('Изменения');
 assert.equal(groupDiff.rowCount,2);assert.match(groupDiff.getCell('D2').value,/0\.\.1/);
 assert.notEqual(groupDiff.getCell('D2').value,groupDiff.getCell('E2').value,'Excel preserves changed optional-group membership');
 const mappedTree=(names)=>[{...field('root'),contentModel:['sequence',...names]},...names.map(name=>({...field('root/'+name),parent:0}))];
 const mappedBefore=mappedTree(['a','b']),mappedAfter=mappedTree(['y','x']),mappedPairs=[{beforePath:'root/a',afterPath:'root/x'},{beforePath:'root/b',afterPath:'root/y'}];
 const mappedBook=createWorkbook(ExcelJS,{...options,before:mappedBefore,after:mappedAfter,mappings:mappedPairs,results:core.compare(mappedBefore,mappedAfter,mappedPairs)});
 const mappedLoaded=new ExcelJS.Workbook();await mappedLoaded.xlsx.load(await mappedBook.xlsx.writeBuffer());
 const order=mappedLoaded.getWorksheet('Изменения').getRow(2);
 assert.equal(order.getCell(3).value,'Порядок и группировка дочерних элементов');
 assert.equal(order.getCell(4).value,'Последовательность (a, b)');assert.equal(order.getCell(5).value,'Последовательность (y, x)');
 const flagsText=require("../web/comparison-export.js").constraints({...a[0],flags:{abstract:"true",typeAbstract:"true"}});
 assert.match(flagsText,/Абстрактный элемент/);assert.match(flagsText,/Абстрактный тип/);
 const unionField=members=>({...field("r"),values:[["true",""]],alternatives:members.map(type=>({type}))});
 const beforeUnion=[unionField(["xs:boolean","xs:string"])],afterUnion=[unionField(["xs:string","xs:boolean"])];
 const unionBook=createWorkbook(ExcelJS,{...options,before:beforeUnion,after:afterUnion,results:core.compare(beforeUnion,afterUnion)});
 const decodedUnion=new ExcelJS.Workbook();await decodedUnion.xlsx.load(await unionBook.xlsx.writeBuffer());
 const unionDiff=decodedUnion.getWorksheet("Изменения");
 assert.equal(unionDiff.getCell("C2").value,"Альтернативы типа (union)");
 assert.deepEqual(JSON.parse(unionDiff.getCell("D2").value).map(member=>member.type),["xs:boolean","xs:string"]);
 assert.deepEqual(JSON.parse(unionDiff.getCell("E2").value).map(member=>member.type),["xs:string","xs:boolean"]);
 const clock=value=>({...field("clock"),base:"xs:time",flags:{fixed:value},facets:[["minInclusive",value]],values:[[value,""]]});
 const sameClock=[clock("09:00:00+00:00")],beforeClock=[clock("09:00:00.0Z")];
 const timeBook=createWorkbook(ExcelJS,{...options,before:beforeClock,after:sameClock,results:core.compare(beforeClock,sameClock)});
 const decodedClock=new ExcelJS.Workbook();await decodedClock.xlsx.load(await timeBook.xlsx.writeBuffer());
 assert.equal(decodedClock.getWorksheet("Изменения").getRow(2).getCell(1).value,null);
 const wb=createWorkbook(ExcelJS,options);
 assert.match(wb.getWorksheet("Было").getCell("C3").value,/A.* И .*B/);
 assert.match(wb.getWorksheet("Стало").getCell("C3").value,/A.* ИЛИ .*B/);
 assert.deepEqual(wb.worksheets.map(s=>s.name),["Сопоставление","Было","Стало","Изменения","О выгрузке"]);
 const map=wb.getWorksheet("Сопоставление");
 assert.equal(map.rowCount,a.length+5);
 assert.equal(map.getCell("E6").value,null);
 assert.equal(map.getCell("E7").value,"same/path");
 assert.equal(map.getCell("H7").value,"Не проверено");
 assert.match(map.getCell("F7").formula,/IFERROR/);
 assert.match(map.getCell("F7").formula,/MATCH/);
 assert.match(map.getCell("F7").formula,/EXACT/,'XML paths are case sensitive, including Excel lookups');
 assert.doesNotMatch(map.getCell("F7").formula,/_xlfn|SUBSTITUTE/,'lookup works without newer functions or wildcard matching');
 assert.equal(map.getCell("F7").result,"Изменено");
 assert.equal(wb.getWorksheet("Было").getCell("B2").value,a[0].doc);
 assert.equal(wb.getWorksheet("Было").getCell("B2").type,ExcelJS.ValueType.String);
 assert.match(wb.getWorksheet("Было").getCell("O4").value,/length/);
 assert.match(wb.getWorksheet("Было").getCell("C4").value,/ИЛИ/);
 assert.match(wb.getWorksheet("О выгрузке").getCell("C2").value,/10110/);
 assert.match(filename(left,right),/101-prod.*10110-test/);
 assert.equal(wb.getWorksheet("О выгрузке").getCell("B9").value.hyperlink,"https://example.com/reference/index.html#message=101&env=prod&rev=left123456");
 assert.equal(wb.getWorksheet("Было").getCell("H2").value,0);
 const groupedLeft={...left,edition_sources:[{...left,document_version:"3.1",document_url:"https://example.com/service.docx",document_download:"documents/service.docx"},{...left,document_version:"5.5",document_url:"https://example.com/rust.docx",document_download:"documents/rust.docx"}]};
 const grouped=createWorkbook(ExcelJS,{...options,left:groupedLeft}),meta=grouped.getWorksheet("О выгрузке");
 const sourceRows=[];meta.eachRow(row=>{if(String(row.getCell(1).value).startsWith("Источник схемы"))sourceRows.push(row)});
 assert.ok(sourceRows.some(row=>row.getCell(2).value?.hyperlink==="https://example.com/rust.docx"),"all source documents must be linked");
 assert.ok(sourceRows.some(row=>row.getCell(2).value?.hyperlink==="https://example.com/reference/documents/rust.docx"),"all archived originals must be linked");
 assert.ok(sourceRows.some(row=>String(row.getCell(2).value).includes("5.5")),"source document version is retained");
 assert.match(meta.getCell("B2").value,/схема.*источников: 2/);
 assert.equal(meta.getCell("B4").value,left.revision_id,"grouping preserves workbook edition IDs");
 const bytes=await wb.xlsx.writeBuffer();
 const loaded=new ExcelJS.Workbook();await loaded.xlsx.load(bytes);
 assert.equal(loaded.getWorksheet("Было").rowCount,4);
 assert.equal(loaded.getWorksheet("Сопоставление").getCell("E6").dataValidation.type,"list");
 const decision=loaded.getWorksheet("Сопоставление").getCell("H6").dataValidation;
 assert.equal(decision.allowBlank,true);
 assert.equal(decision.showErrorMessage,true);
 assert.deepEqual(decision.formulae,['"Не проверено,Подтверждено,Удалено,Переработано,Требует уточнения"']);
 assert.equal(loaded.getWorksheet("Сопоставление").getCell("F7").result,"Изменено");
 const reverse=createWorkbook(ExcelJS,{...options,left:right,right:left,before:b,after:a,results:core.compare(b,a)});
 assert.equal(reverse.getWorksheet("Было").getCell("A2").value,"new/root");
 assert.match(reverse.getWorksheet("О выгрузке").getCell("B2").value,/10110/);
 const offline=createWorkbook(ExcelJS,{...options,baseURL:"file:///C:/reference/"});
 assert.equal(offline.getWorksheet("О выгрузке").getCell("B9").value.hyperlink,"file:///C:/reference/index.html#message=101&env=prod&rev=left123456");
 assert.equal(offline.getWorksheet("О выгрузке").getCell("C8").value.hyperlink,"file:///C:/reference/documents/new.docx");
 const same=createWorkbook(ExcelJS,{...options,before:a,after:a,results:[]});
 assert.equal(same.getWorksheet("Сопоставление").getCell("E6").value,"old/root");
 assert.equal(same.getWorksheet("Изменения").rowCount,1);
 const fs=require("node:fs"),catalog=JSON.parse(fs.readFileSync(require("node:path").join(__dirname,"../data/catalog.json"),"utf8"));
 const revision=number=>catalog.revisions.find(r=>r.number===number&&r.environment==="prod"&&r.status==="matched");
 const old=revision("101"),next=revision("10110");
 const fields=r=>JSON.parse(fs.readFileSync(require("node:path").join(__dirname,"../data/fields/"+r.revision_id+".json"),"utf8")).fields;
 const oldFields=fields(old),newFields=fields(next);
 const actual=createWorkbook(ExcelJS,{left:old,right:next,before:oldFields,after:newFields,results:core.compare(oldFields,newFields),baseURL:"https://macros0.github.io/sedo-reference/"});
 assert.equal(actual.getWorksheet("Сопоставление").rowCount,oldFields.length+5);
 assert.equal(actual.getWorksheet("Было").rowCount,oldFields.length+1);
 assert.equal(actual.getWorksheet("Стало").rowCount,newFields.length+1);
 const example=oldFields.findIndex(f=>f.path==="approveSocialAssist/benefit6Approve");
 assert.notEqual(example,-1);assert.equal(actual.getWorksheet("Сопоставление").getCell(example+6,5).value,null);
 const actualBytes=await actual.xlsx.writeBuffer();
 assert.ok(actualBytes.length>1000);
 console.log("Real 101/10110: "+oldFields.length+" / "+newFields.length+" fields, renamed blocks left for human mapping.");
 console.log("Excel export: mapping, union, safe strings, round trip, reverse and identical editions passed");
})().catch(e=>{console.error(e);process.exitCode=1});
