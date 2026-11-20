const assert=require('node:assert/strict');
const journal=require('../web/review-journal.js');
const ExcelJS=require('../web/vendor/exceljs.min.js');
const issue={kind:'Кратность',location:'Таблица 2',spec:'=0..1',xlocation:'main.xsd / item',xsd:'1..1',impact:'Обязательное поле'};
const revision={number:'6',environment:'prod',document_version:'3.1',label_date:'12.12.2024',document_sha256:'doc',archive_sha256:'archive',dependency_digest:'deps',document_url:'https://lk.sfr.gov.ru/spec.docx',archive_url:'https://lk.sfr.gov.ru/xsd.zip',schema_publication_page:'https://lk.sfr.gov.ru/sedo.html',schema_file_download:'sources/main.xsd',schema_path:'main.xsd',revision_id:'one',schema_review:{findings:[issue]}};
(async()=>{
 const catalog={revisions:[revision,{...revision,revision_id:'alias'},{...revision,revision_id:'test',environment:'test'},{...revision,revision_id:'changed',dependency_digest:'new'},{...revision,revision_id:'empty',number:'60',schema_review:{findings:[]}}]};
 const rows=journal.collect(catalog);
 const graphA={...revision,resolved_schema_graph:'a'.repeat(64)};
 const graphB={...graphA,revision_id:'different-graph',resolved_schema_graph:'b'.repeat(64)};
 const graphRows=journal.collect({revisions:[graphA,graphB,{...graphA,revision_id:'repeat'}]});
 assert.deepEqual(graphRows.map(row=>row.revision.revision_id),[graphA.revision_id,graphB.revision_id]);
 const oldUrl='https://lk.sfr.gov.ru/archive/spec.docx',download='documents/doc.docx';
 const oldArchive='https://lk.sfr.gov.ru/archive/xsd-original.zip';
 const oldSource={document_url:oldUrl,document_sha256:'doc',revision_id:'repeat',
  schema_path:revision.schema_path,archive_sha256:'original-zip',archive_url:oldArchive};
 const foreignSource={...oldSource,document_url:'https://lk.sfr.gov.ru/foreign.docx',revision_id:graphB.revision_id};
 const newBytes={...graphA,revision_id:'replacement',document_url:oldUrl,document_sha256:'replacement'};
 const moved=journal.collect({revisions:[
  {...graphA,document_download:download},
  {...graphA,revision_id:'repeat',document_download:download,edition_sources:[oldSource,foreignSource]},
  {...graphB,document_download:download},
  newBytes
 ]});
 const oldKey=journal.specificationKey(oldSource);
 assert.ok(journal.sourceOptions(moved).specifications.some(source=>source.value===oldKey));
 const [selectedOld,...duplicates]=journal.filter(moved,'','',oldKey);
 assert.deepEqual(duplicates,[]);
 assert.equal(selectedOld.revision.document_url,oldUrl);
 assert.equal(selectedOld.revision.document_download,download);
 assert.equal(journal.filter(moved,'','',foreignSource.document_url).length,0);
 assert.equal(journal.filter(moved,'','',oldUrl).length,2);
 assert.equal(journal.filter(moved,'','',journal.specificationKey(graphA)).length,2);
 const historic=journal.createWorkbook(ExcelJS,[selectedOld],{specification:oldKey,baseURL:'https://example.com/site/'});
 const historicSheet=historic.getWorksheet('Замечания');
 assert.equal(historicSheet.getCell('D2').value.hyperlink,'https://example.com/site/'+download);
 assert.ok(historicSheet.getRow(2).values.some(value=>value?.hyperlink===oldUrl));
 assert.ok(historicSheet.getRow(2).values.some(value=>value?.hyperlink===oldArchive));
 const oldXsd=JSON.stringify([oldArchive,revision.schema_path]);
 assert.equal(journal.filter(moved,'','',oldKey,oldXsd).length,1);
 const sameGraphSources=['old','new'].map(tag=>({
  ...graphA,snapshot:tag,document_url:tag==='old'?oldUrl:graphA.document_url,
  schema_source_type:'wsdl',schema_source_url:'https://ecp.sfr.gov.ru/'+tag+'?wsdl',
  schema_files:[{path:'main.xsd',download:'sources/main.xsd',sha256:'m'.repeat(64),
   official_url:'https://ecp.sfr.gov.ru/'+tag+'?xsd=main.xsd'}]
 }));
 const [oldPublication,newPublication]=sameGraphSources;
 const siteFinding={...issue,xsd_source:{...newPublication.schema_files[0],url:newPublication.schema_files[0].official_url}};
 const observed=journal.collect({revisions:[{...newPublication,schema_review:{findings:[siteFinding]},
  schema_source_observations:sameGraphSources}]});
 const oldObservedKey=journal.specificationKey(oldPublication),newObservedKey=journal.specificationKey(newPublication);
 const oldV1Key=JSON.stringify([[oldPublication.schema_source_url,'main.xsd'],'m'.repeat(64)]);
 const newV1Key=JSON.stringify([[newPublication.schema_source_url,'main.xsd'],'m'.repeat(64)]);
 assert.equal(journal.filter(observed,'','',oldObservedKey,newV1Key).length,0);
 assert.equal(journal.filter(observed,'','',newObservedKey,oldV1Key).length,0);
 const [oldPublicationRow]=journal.filter(observed,'','',oldObservedKey,oldV1Key);
 assert.ok(oldPublicationRow);
 assert.equal(journal.schemaSources(oldPublicationRow.revision,siteFinding).official.url,oldPublication.schema_files[0].official_url);
 assert.equal(journal.filter(observed,'','',newObservedKey,newV1Key).length,1);
 const incomplete={...oldPublication,schema_files:oldPublication.schema_files.map(({official_url,...file})=>file)};
 const incompleteRows=journal.collect({revisions:[{...newPublication,schema_review:{findings:[siteFinding]},
  schema_source_observations:[newPublication,incomplete]}]});
 const [incompleteRow]=journal.filter(incompleteRows,'','',oldObservedKey,oldV1Key);
 const incompleteChain=journal.schemaSources(incompleteRow.revision,siteFinding);
 assert.equal(incompleteChain.file.url,'sources/main.xsd');
 assert.equal(incompleteChain.official,null);
 assert.equal(journal.filter(incompleteRows,'','',oldObservedKey,journal.xsdKey(oldPublication,siteFinding)).length,0);
 const malformed={...revision,number:'99',document_url:'https://lk.sfr.gov.ru/%CF%F0_100%.docx',archive_url:'https://lk.sfr.gov.ru/100%.zip',schema_review:{findings:[issue,{...issue,location:'Таблица 3'}]}};
 const mixed=journal.collect({revisions:[revision,malformed]});
 assert.equal(journal.filter(mixed,'6').length,1);
 const affected=journal.filter(mixed,'99'),options=journal.sourceOptions(affected);
 assert.ok(options.specifications[0].text.includes('%CF%F0_100%.docx'));
 const source=journal.schemaSources(malformed,issue);
 assert.equal(source.publication.text,'Архив 100%.zip');assert.equal(source.publication.url,malformed.archive_url);
 const unusual=journal.createWorkbook(ExcelJS,affected,{baseURL:'https://example.com/site/'}).getWorksheet('Замечания');
 assert.deepEqual(unusual.getCell('D2').value,{text:'%CF%F0_100%.docx',hyperlink:malformed.document_url});
 const versions=['a','b'].map(character=>({revision:{...revision,schema_source_type:'wsdl',schema_source_url:'https://ecp.sfr.gov.ru/gateway?wsdl',schema_files:[{path:'main.xsd',sha256:character.repeat(64),download:'sources/'+character.repeat(64)+'.xsd'}],schema_sources:[{kind:'xsd',sha256:character.repeat(64),url:'https://ecp.sfr.gov.ru/gateway?xsd=main.xsd'}]},finding:issue}));
 assert.equal(journal.sourceOptions(versions).schemas.length,2,'same official URL and path with distinct bytes retain distinct XSD options');
 assert.notEqual(...journal.sourceOptions(versions).schemas.map(option=>option.text));
 const versionSources=versions.map(({revision,finding})=>journal.schemaSources(revision,finding));
 assert.notEqual(versionSources[0].file.url,versionSources[1].file.url);
 assert.equal(versionSources[0].official.url,versions[0].revision.schema_sources[0].url);
 for(const row of versions)assert.deepEqual(journal.filter(versions,'','','',journal.xsdKey(row.revision,row.finding)),[row]);
 const legacyXsd=JSON.stringify([versions[0].revision.schema_source_url,'main.xsd']);
 assert.deepEqual(journal.filter(versions,'','','',legacyXsd),versions,'legacy XSD bookmarks still show all bytes at their old source');
 const bytesSheet=journal.createWorkbook(ExcelJS,versions,{baseURL:'https://example.com/site/'}).getWorksheet('Замечания');
 assert.notEqual(bytesSheet.getCell('M2').value.hyperlink,bytesSheet.getCell('M3').value.hyperlink);
 assert.ok(bytesSheet.getRow(2).values.some(value=>value?.hyperlink===versionSources[0].official.url));
 assert.equal(rows.length,3,'identical sources do not create duplicate notices');
 const selected=journal.filter(rows,' 6 ','prod');
 assert.equal(selected.length,2);assert.equal(journal.filter(rows,'60','').length,0);
 const workbook=journal.createWorkbook(ExcelJS,selected,{number:'6',env:'prod',baseURL:'https://macros0.github.io/sedo-reference/'});
 const loaded=new ExcelJS.Workbook();await loaded.xlsx.load(await workbook.xlsx.writeBuffer());
 const sheet=loaded.getWorksheet('Замечания');
 assert.equal(sheet.rowCount,3);assert.equal(sheet.getCell('B2').value,'Продуктивный');
 assert.equal(sheet.getCell('G2').value,'=0..1','source text must stay literal, including formula-like values');
 assert.equal(sheet.getCell('D2').value.hyperlink,revision.document_url);
 assert.equal(sheet.getCell('K2').value.hyperlink,revision.schema_publication_page);
 assert.equal(sheet.getCell('L2').value.hyperlink,revision.archive_url);
 assert.equal(sheet.getCell('M2').value.hyperlink,'https://macros0.github.io/sedo-reference/sources/main.xsd');
 assert.equal(sheet.getCell('P2').value,revision.label_date);
 const bridged={...revision,schema_source_type:'wsdl_bridge',archive_url:revision.document_url+'#wsdl=generated',schema_source_url:'https://ecp.sfr.gov.ru/sedo/soap/SedoGateway?wsdl'};
 const bridge=journal.createWorkbook(ExcelJS,[{revision:bridged,finding:issue}],{baseURL:'https://macros0.github.io/sedo-reference/'}).getWorksheet('Замечания');
 assert.equal(bridge.getCell('L2').value.hyperlink,revision.document_url);
 assert.equal(bridge.getCell('N2').value.hyperlink,bridged.schema_source_url);
 assert.equal(bridge.getCell('Q2').value,'Вложенная XSD');
 const direct={...revision,schema_source_type:'wsdl',schema_source_url:bridged.schema_source_url,dependencies:[['main.xsd','hash']],schema_sources:[{kind:'xsd',sha256:'hash',url:'https://ecp.sfr.gov.ru/sedo/soap/SedoGateway?xsd=main.xsd'}]};
 assert.equal(journal.schemaSources(direct).file.url,revision.schema_file_download);
 assert.equal(journal.schemaSources(direct).official.url,direct.schema_sources[0].url);
 assert.equal(journal.schemaSources({...revision,archive_url:'manual:///uploaded.zip'}).publication.url,undefined);
 const imported={...revision,schema_files:[{path:'main.xsd',download:'sources/main.xsd',sha256:'hash'},{path:'types/Common.xsd',download:'sources/common.xsd',sha256:'common'}]};
 const importedSource=journal.schemaSources(imported,{xlocation:'types/Common.xsd · root/value'});
 assert.equal(importedSource.file.text,'types/Common.xsd');assert.equal(importedSource.file.url,'sources/common.xsd');
 const baseURL='https://example.com/site/';
 const [entry,dependency]=imported.schema_files;
 const importedFinding={...issue,xsd_source:dependency};
 const importedSheet=journal.createWorkbook(ExcelJS,[{revision:imported,finding:importedFinding}],{baseURL}).getWorksheet('Замечания');
 assert.deepEqual(importedSheet.getCell('R2').value,{text:entry.path,hyperlink:baseURL+entry.download});
 const entrySheet=journal.createWorkbook(ExcelJS,[{revision:imported,finding:{...issue,xsd_source:entry}}],{baseURL}).getWorksheet('Замечания');
 assert.equal(entrySheet.getRow(2).values.filter(value=>value?.hyperlink===baseURL+entry.download).length,1);
 const multi={revision:imported,finding:{...issue,xsd_source:entry,xsd_sources:[entry,dependency]}};
 const companion={revision:imported,finding:{...issue,xsd_source:entry}};
 const secondKey=JSON.stringify([[revision.archive_url,dependency.path],dependency.sha256]);
 assert.ok(journal.sourceOptions([multi]).schemas.some(option=>option.value===secondKey));
 assert.deepEqual(journal.filter([multi,companion],'','','',secondKey),[multi]);
 assert.deepEqual(journal.filter([multi,companion],'','','',JSON.stringify([revision.archive_url,dependency.path])),[multi]);
 const multiSheet=journal.createWorkbook(ExcelJS,[multi],{xsd:secondKey,baseURL}).getWorksheet('Замечания');
 assert.equal(multiSheet.rowCount,2);
 const xsdLinks=multiSheet.getRow(2).values.filter(value=>value?.hyperlink?.endsWith('.xsd'));
 assert.deepEqual(xsdLinks.map(value=>value.hyperlink),[baseURL+entry.download,baseURL+dependency.download]);
 const wsdlImport={...imported,schema_source_type:'wsdl_bridge',schema_source_url:bridged.schema_source_url,schema_sources:[{kind:'xsd',sha256:'common',url:'https://ecp.sfr.gov.ru/service?xsd=Common.xsd'}]};
 const importedRemote=journal.schemaSources(wsdlImport,{xlocation:'types/Common.xsd · value'});
 assert.equal(importedRemote.publication.url,bridged.schema_source_url);assert.equal(importedRemote.file.url,'sources/common.xsd');assert.equal(importedRemote.official.url,wsdlImport.schema_sources[0].url);
 const wsdlRevision={
  ...wsdlImport,archive_url:bridged.archive_url,
  schema_docx_location:'Таблица 2',schema_docx_url:'https://ecp.sfr.gov.ru/service?xsd=main.xsd',
  schema_declaration_path:'declaration.xsd',schema_declaration_download:'sources/declaration.xsd',
  schema_sources:[{kind:'xsd',sha256:entry.sha256,url:'https://ecp.sfr.gov.ru/service?xsd=main.xsd'}],
 };
 const wsdlRows=[
  {revision:wsdlRevision,finding:{...companion.finding,location:'Одно XSD'}},
  {revision:wsdlRevision,finding:{...multi.finding,location:'Два XSD',xsd_sources:[{...dependency,url:'https://ecp.sfr.gov.ru/service?xsd=Common.xsd'}]}},
 ];
 for(const ordered of [wsdlRows,[...wsdlRows].reverse()]){
  const selected=journal.filter([...ordered,{...ordered[0],revision:{...wsdlRevision,environment:'test'}}],'6','prod');
  assert.equal(selected.length,2);
  const workbook=journal.createWorkbook(ExcelJS,selected,{number:'6',env:'prod',baseURL});
  const loaded=new ExcelJS.Workbook();await loaded.xlsx.load(await workbook.xlsx.writeBuffer());
  const sheet=loaded.getWorksheet('Замечания'),headers=sheet.getRow(1).values;
  const start=headers.indexOf('Файл XSD 2');
  assert.ok(start>0);
  assert.deepEqual(headers.slice(start),[
   'Файл XSD 2','Официальная публикация XSD 2','Архив, документ или WSDL с XSD 2',
   'Расположение схемы 2 в документе','Зависимости WSDL 2',
   'Ссылка на XSD в DOCX','Файл объявления корня','Точка входа XSD',
  ]);
  assert.equal(sheet.rowCount,3);
  assert.equal(loaded.getWorksheet('О журнале').getCell('B3').value,2);
  for(const [index,row] of ordered.entries()){
   const rowNumber=index+2;
   assert.equal(sheet.getCell(rowNumber,headers.indexOf('Место в спецификации')).value,row.finding.location);
   const cells=Array.from({length:8},(_,offset)=>sheet.getCell(rowNumber,start+offset).value||null);
   const additional=row.finding.xsd_sources?[
    {text:'types/Common.xsd',hyperlink:baseURL+'sources/common.xsd'},
    {text:'Официальная публикация XSD',hyperlink:'https://ecp.sfr.gov.ru/service?xsd=Common.xsd'},
    {text:'Документ spec.docx',hyperlink:'https://lk.sfr.gov.ru/spec.docx'},
    'Вложенная XSD',
    {text:bridged.schema_source_url,hyperlink:bridged.schema_source_url},
   ]:Array(5).fill(null);
   assert.deepEqual(cells,[...additional,
    {text:'Таблица 2',hyperlink:'https://ecp.sfr.gov.ru/service?xsd=main.xsd'},
    {text:'declaration.xsd',hyperlink:baseURL+'sources/declaration.xsd'},
    {text:'main.xsd',hyperlink:baseURL+'sources/main.xsd'},
   ]);
  }
 }
 const copies=['v1','v2'].map(version=>({kind:'xsd',archive_path:`schemas/${version}/Common.xsd`,sha256:'c'.repeat(64),url:`https://ecp.sfr.gov.ru/sedo/${version}/Common.xsd`}));
 const files=copies.map(copy=>({path:copy.archive_path,download:`sources/${copy.sha256}.xsd`,sha256:copy.sha256}));
 const duplicate={...direct,schema_files:[entry,...files],schema_sources:copies};
 const copiedFinding={...issue,xsd_source:files[1]};
 const chain=journal.schemaSources(duplicate,copiedFinding);
 assert.equal(chain.official.url,copies[1].url);
 assert.equal(chain.file.text,files[1].path);
 const copySheet=journal.createWorkbook(ExcelJS,[{revision:duplicate,finding:copiedFinding}],{baseURL}).getWorksheet('Замечания');
 assert.deepEqual(copySheet.getCell('M2').value,{text:files[1].path,hyperlink:baseURL+files[1].download});
 assert.ok(copySheet.getRow(2).values.some(value=>value?.hyperlink===copies[1].url));
 assert.ok(!copySheet.getRow(2).values.some(value=>value?.hyperlink===copies[0].url));
 const shared={revision:{...imported,number:'7'},finding:issue};
 const differentDoc={revision:{...imported,number:'8',document_url:'https://lk.sfr.gov.ru/other.docx'},finding:issue};
 const differentFile={revision,finding:{...issue,xlocation:'types/Common.xsd / root/value'}};
 const sourceRows=[{revision:imported,finding:issue},shared,differentDoc,{...differentFile,revision:imported}];
 const specKey=journal.specificationKey(revision),fileKey=journal.xsdKey(imported,issue);
 const replacements=['11','22'].map((sha,i)=>({revision:{...revision,document_version:'3.'+i,document_sha256:sha,document_download:'documents/'+sha+'.docx'},finding:issue}));
 const choices=journal.sourceOptions(replacements).specifications;
 assert.equal(choices.length,2,'two documents at the same official URL retain distinct filter options');
 assert.notEqual(choices[0].text,choices[1].text);
 for(const row of replacements){
  assert.deepEqual(journal.filter(replacements,'','',journal.specificationKey(row.revision)),[row]);
 }
 assert.deepEqual(journal.filter(replacements,'','',revision.document_url),replacements,'old URL bookmarks include all its versions');
 const versionSheet=journal.createWorkbook(ExcelJS,replacements,{baseURL:'https://example.com/site/'}).getWorksheet('Замечания');
 for(const [index,row] of replacements.entries()){
  assert.equal(versionSheet.getCell(index+2,4).value.hyperlink,'https://example.com/site/'+row.revision.document_download);
  assert.ok(versionSheet.getRow(index+2).values.some(value=>value?.hyperlink===revision.document_url));
 }
 assert.equal(journal.filter(sourceRows,'','',specKey).length,3,'one specification across messages');
 assert.equal(journal.filter(sourceRows,'','',specKey,fileKey).length,2,'intersection excludes another imported file');
 assert.equal(journal.filter(sourceRows,'7','prod',specKey,fileKey).length,1);
 assert.equal(journal.filter(sourceRows,'','test',specKey,fileKey).length,0);
 assert.equal(journal.filter(sourceRows,'','','missing-source').length,0,'unknown filter must never show all rows');
 const sourceWorkbook=journal.createWorkbook(ExcelJS,journal.filter(sourceRows,'','',specKey,fileKey),{specification:specKey,xsd:fileKey,baseURL:'https://macros0.github.io/sedo-reference/'});
 assert.equal(sourceWorkbook.getWorksheet('Замечания').rowCount,3);
 const info=sourceWorkbook.getWorksheet('О журнале');
 assert.match(info.getCell('B4').value,/spec.docx/);assert.match(info.getCell('B5').value,/main.xsd/);
 const otherArchive={...imported,archive_url:'https://lk.sfr.gov.ru/another.zip'};
 assert.notEqual(journal.xsdKey(imported,issue),journal.xsdKey(otherArchive,issue),'same XSD name in different archives remains distinct');
 assert.ok(sheet.autoFilter);assert.equal(sheet.views[0].ySplit,1);
 assert.equal(journal.collect({revisions:[{...revision,schema_review:{findings:[]}}]}).length,0,'updated source removes resolved notices');
 console.log('Journal filters, changed source results, deduplication and Excel roundtrip verified.');
})().catch(error=>{console.error(error);process.exitCode=1});
