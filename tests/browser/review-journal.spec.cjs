const {test,expect}=require('@playwright/test');
const ExcelJS=require('../../web/vendor/exceljs.min.js');
const {catalog,journal,journalRows,pythonFixture}=require('./catalog-fixtures.cjs');
const fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');

async function routeJournal(page){
 await page.route('**/review-journal.js*',route=>route.fulfill({
  contentType:'application/javascript',path:path.join(__dirname,'../../web/review-journal.js')
 }));
}

test('resolved graphs keep separate message and field links in the journal and Excel',async({page})=>{
 const {catalog:built,viewers}=pythonFixture([
  'import contextlib, io, json',
  'from tests.journal_graph_fixture import journal_graph_fixture',
  'with contextlib.redirect_stdout(io.StringIO()):',
  '    fixture = journal_graph_fixture()',
  'print(json.dumps(fixture))'
 ].join('\n'));
 const [first,second]=built.revisions;
 const finding=first.schema_review.findings[0];
 expect(finding.field_paths).toEqual(['r']);
 const fileKey=journal.xsdKey(first,finding);
 built.revisions.push({...first,revision_id:'c'.repeat(64)});
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',
  body:'window.SEDO_CATALOG='+JSON.stringify(built)+';'}));
 await page.route('**/editions/*.html*',route=>{
  const file=new URL(route.request().url()).pathname.split('/').pop();
  return viewers[file]?route.fulfill({contentType:'text/html',body:viewers[file]}):route.fallback();
 });
 await routeJournal(page);
 const remarks='/site/#remarks=1&number=999&env=test';
 await page.goto(remarks);
 const rows=page.locator('.review-results tbody tr');
 await page.locator('#review-specification').selectOption(journal.specificationKey(first));
 await expect(rows).toHaveCount(2);
 await page.locator('#review-xsd').selectOption(fileKey);
 await expect(rows).toHaveCount(2);
 for(const [index,revision] of [first,second].entries()){
  const row=rows.nth(index),message='#message=999&env=test&rev='+revision.revision_id;
  await expect(row.locator('td:first-child a')).toHaveAttribute('href',message);
  await expect(row.getByRole('link',{name:'Открыть поле XSD · r'})).toHaveAttribute('href',message+'&field=r');
  await expect(row.locator('td:nth-child(2) a').first()).toHaveAttribute('href',revision.document_download);
  await expect(row.locator('.review-source-chain').getByRole('link',{name:finding.xsd_source.path,exact:true}))
   .toHaveAttribute('href',new URL(finding.xsd_source.download,page.url()).href);
 }
 const ready=page.waitForEvent('download');
 await page.locator('#review-export').click();
 const book=new ExcelJS.Workbook();
 await book.xlsx.load(fs.readFileSync(await (await ready).path()));
 const sheet=book.getWorksheet('Замечания');
 expect(sheet.rowCount).toBe(3);
 for(const [index,revision] of [first,second].entries()){
  const row=index+2;
  const message=sheet.getCell('O'+row).value.hyperlink,field=sheet.getCell('S'+row).value.hyperlink;
  expect(new URLSearchParams(new URL(message).hash.slice(1)).get('rev')).toBe(revision.revision_id);
  expect(new URLSearchParams(new URL(field).hash.slice(1)).get('rev')).toBe(revision.revision_id);
  expect(new URLSearchParams(new URL(field).hash.slice(1)).get('field')).toBe('r');
  expect(sheet.getCell('M'+row).value.hyperlink).toBe(new URL(finding.xsd_source.download,page.url()).href);
 }
 await page.locator('#review-env').selectOption('prod');
 await expect(rows).toHaveCount(0);
 for(const [index,revision] of [first,second].entries()){
  await page.goto(remarks);
  await rows.nth(index).getByRole('link',{name:'Открыть поле XSD · r'}).click();
  await expect(page.locator('#frame')).toHaveAttribute('src',new RegExp(revision.revision_id+'\\.html.*#field=r$'));
  await expect(page.frameLocator('#frame').getByRole('region',{name:'Карточка поля'}).getByRole('heading',{name:'r',exact:true})).toBeVisible();
 }
});

test('saved specification filters survive a move and export the selected source',async({page})=>{
 const {catalog:built,viewers,old_url,new_url,document}=pythonFixture([
  'import contextlib, io, json',
  'from tests.journal_graph_fixture import journal_graph_fixture',
  'with contextlib.redirect_stdout(io.StringIO()):',
  '    fixture = journal_graph_fixture(move=True)',
  'print(json.dumps(fixture))'
 ].join('\n'));
 const [revision]=built.revisions,[finding]=revision.schema_review.findings;
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',
  body:'window.SEDO_CATALOG='+JSON.stringify(built)+';'}));
 await page.route('**/'+revision.document_download,route=>route.fulfill({body:Buffer.from(document,'base64')}));
 await page.route('**/editions/*.html*',route=>{
  const file=new URL(route.request().url()).pathname.split('/').pop();
  return viewers[file]?route.fulfill({contentType:'text/html',body:viewers[file]}):route.fallback();
 });
 await routeJournal(page);
 const specKey=url=>journal.specificationKey({...revision,document_url:url});
 const rows=page.locator('.review-results tbody tr'),filter=page.locator('#review-specification');
 for(const [key,url] of [[specKey(new_url),new_url],[specKey(old_url),old_url],[old_url,old_url]]){
  await page.goto('/site/#'+new URLSearchParams({remarks:'1',number:'999',env:'test',specification:key}));
  await expect(filter).toHaveValue(key);
  await expect(rows).toHaveCount(1);
  const official=rows.getByRole('link',{name:'Публикация СФР'});
  await expect(official).toHaveJSProperty('href',new URL(url).href);
  await expect(rows.getByRole('link',{name:finding.xsd_source.path,exact:true}))
   .toHaveAttribute('href',new URL(finding.xsd_source.download,page.url()).href);
  const local=rows.locator('td:nth-child(2) a').first();
  await expect(local).toHaveAttribute('href',revision.document_download);
  const expectedUrl=new URL(revision.document_download,page.url()).href;
  const response=await page.evaluate(async address=>{
   const data=await(await fetch(address)).arrayBuffer();
   return [...new Uint8Array(await crypto.subtle.digest('SHA-256',data))].map(n=>n.toString(16).padStart(2,'0')).join('');
  },expectedUrl);
  expect(response).toBe(revision.document_sha256);
  const ready=page.waitForEvent('download');
  await page.locator('#review-export').click();
  const workbook=new ExcelJS.Workbook();
  await workbook.xlsx.load(fs.readFileSync(await (await ready).path()));
  const sheet=workbook.getWorksheet('Замечания');
  expect(sheet.rowCount).toBe(2);
  expect(sheet.getCell('D2').value.hyperlink).toBe(expectedUrl);
  expect(sheet.getCell('M2').value.hyperlink).toBe(new URL(finding.xsd_source.download,page.url()).href);
  expect(sheet.getRow(2).values.some(value=>value?.hyperlink===new URL(url).href)).toBe(true);
 }
 await rows.getByRole('link',{name:'Открыть поле XSD · r'}).click();
 await expect(page.frameLocator('#frame').getByRole('region',{name:'Карточка поля'}).getByRole('heading',{name:'r',exact:true})).toBeVisible();
});

for(const [title,options] of [
 ['the DOCX and WSDL move',"move=True, publication='wsdl'"],
 ['the WSDL moves under the same DOCX URL',"publication='wsdl'"],
 ['the official XSD address changes under the same WSDL and DOCX URLs',"publication='member'"],
]){
 test('journal keeps complete observed source chains when '+title,async({page})=>{
  const fixture=pythonFixture([
   'import contextlib, io, json',
   'from tests.journal_graph_fixture import journal_graph_fixture',
   'with contextlib.redirect_stdout(io.StringIO()):',
   '    fixture = journal_graph_fixture('+options+')',
   'print(json.dumps(fixture))'
  ].join('\n'));
  const {catalog:built,old_url,new_url,old_links,new_links,files}=fixture;
  const [revision]=built.revisions,[finding]=revision.schema_review.findings;
  const variants=revision.schema_source_observations;
  const [oldSource,newSource]=[old_links,new_links].map(({xsd})=>
   variants.find(source=>source.schema_files.some(file=>file.official_url===xsd)));
  expect(oldSource.snapshot).not.toBe(newSource.snapshot);
  expect(oldSource.resolved_schema_graph).toBe(newSource.resolved_schema_graph);
  await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',
   body:'window.SEDO_CATALOG='+JSON.stringify(built)+';'}));
  await page.route('**/sources/*.xsd',route=>{
   const name=path.posix.basename(new URL(route.request().url()).pathname);
   return route.fulfill({body:Buffer.from(files[name],'base64')});
  });
  await routeJournal(page);
  const rows=page.locator('.review-results tbody tr'),download=page.locator('#review-export');
  const oldKey=journal.xsdKey(oldSource,finding),newKey=journal.xsdKey(newSource,finding);
  expect(oldKey).not.toBe(newKey);
  const spec=source=>journal.specificationKey(source);
  const visit=(specification,xsd)=>page.goto('/site/#'+new URLSearchParams({remarks:'1',number:'999',env:'test',specification,xsd}));
  for(const [source,urls,key] of [[oldSource,old_links,oldKey],[newSource,new_links,newKey]]){
   await visit(spec(source),key);
   await expect(page.locator('#review-xsd option').filter({hasText:urls.xsd})).toHaveCount(1);
   await expect(rows).toHaveCount(1);
   await expect(rows.getByRole('link',{name:'Публикация СФР'})).toHaveAttribute('href',source.document_url);
   await expect(rows.getByRole('link',{name:'WSDL шлюза СФР',exact:true})).toHaveAttribute('href',urls.wsdl);
   await expect(rows.getByRole('link',{name:'Официальная публикация XSD',exact:true})).toHaveAttribute('href',urls.xsd);
   await expect(rows.getByRole('link',{name:'Открыть поле XSD · r'})).toHaveAttribute('href',new RegExp(revision.revision_id+'.*field=r'));
   const local=new URL(finding.xsd_source.download,page.url()).href;
   const response=await page.evaluate(async address=>Array.from(new Uint8Array(await (await fetch(address)).arrayBuffer())),local);
   expect(createHash('sha256').update(Buffer.from(response)).digest('hex')).toBe(finding.xsd_source.sha256);
   const ready=page.waitForEvent('download');
   await download.click();
   const workbook=new ExcelJS.Workbook();
   await workbook.xlsx.load(fs.readFileSync(await (await ready).path()));
   const sheet=workbook.getWorksheet('Замечания');
   expect(sheet.rowCount).toBe(2);
   const hyperlinks=sheet.getRow(2).values.map(value=>value?.hyperlink);
   expect(hyperlinks).toEqual(expect.arrayContaining([local,new URL(source.document_url).href,urls.wsdl,urls.xsd]));
  }
  if(old_url!==new_url){
   for(const [source,key] of [[oldSource,newKey],[newSource,oldKey]]){
    await visit(spec(source),key);
    await expect(rows).toHaveCount(0);
    await expect(download).toBeDisabled();
   }
   const legacy=JSON.stringify([[old_links.wsdl,finding.xsd_source.path],finding.xsd_source.sha256]);
   await visit(old_url,legacy);
   await expect(page.locator('#review-xsd')).toHaveValue(legacy);
   await expect(rows.getByRole('link',{name:'Официальная публикация XSD',exact:true})).toHaveAttribute('href',old_links.xsd);
  }
 });
}

for(const fail of [false,true]){
 test('journal keeps a single export pending across filters '+(fail?'until the library fails':'and downloads the original selection'),async({page})=>{
  await routeJournal(page);
  let release,requested;
  const waiting=new Promise(resolve=>{release=resolve}),started=new Promise(resolve=>{requested=resolve});
  await page.route('**/exceljs.min.js*',async route=>{
   requested();await waiting;
   if(fail)await route.abort();else await route.continue();
  },{times:1});
  const downloads=[];page.on('download',file=>downloads.push(file));
  await page.goto('/site/#remarks=1&number=6&env=prod');
  const number=page.locator('#review-number'),button=page.locator('#review-export'),status=page.locator('#review-journal > [role=status]');
  await button.click();await started;
  try{
   await number.fill('111');
   await expect(button).toBeDisabled({timeout:1500});
   await button.dispatchEvent('click');
   await page.goto('/site/#remarks=1&number=999999&env=prod');
   await expect(number).toHaveValue('999999');
   await expect(button).toBeDisabled();
  }finally{release()}
  if(fail){
   await expect(status).toContainText('Не удалось выгрузить Excel');
   expect(downloads).toHaveLength(0);
   await expect(button).toBeDisabled();
   await number.fill('111');await expect(button).toBeEnabled();
   const ready=page.waitForEvent('download');await button.click();
   expect((await ready).suggestedFilename()).toBe('sedo-remarks-111-prod.xlsx');
  }else{
   await expect(status).toContainText('В Excel выгружено замечаний');
   await expect.poll(()=>downloads.length).toBe(1);
   const [file]=downloads,workbook=new ExcelJS.Workbook();
   expect(file.suggestedFilename()).toBe('sedo-remarks-6-prod.xlsx');
   await workbook.xlsx.load(fs.readFileSync(await file.path()));
   const sheet=workbook.getWorksheet('Замечания'),selected=journalRows('6','prod');
   expect(sheet.rowCount).toBe(selected.length+1);
   expect(sheet.getColumn('A').values.slice(2)).toEqual(selected.map(()=> '6'));
   expect(sheet.getColumn('G').values.slice(2)).toEqual(selected.map(({finding})=>finding.spec));
   await expect(button).toBeDisabled();
   await number.fill('111');await expect(button).toBeEnabled();
   expect(downloads).toHaveLength(1);
  }
 });
}

test('unusual percent-encoded filenames keep the journal, XLSX and archive card usable',async({page})=>{
 const rows=journalRows('6','prod'),base=rows[0],original=base.revision;
 const doc='https://lk.sfr.gov.ru/%CF%F0_100%.docx',archive='https://lk.sfr.gov.ru/100%.zip';
 const revision_id='f'.repeat(64);
 const affected={...original,number:'999',revision_id,display_revision_id:revision_id,document_url:doc,archive_url:archive,
  schema_source_type:'archive',schema_review:{...original.schema_review,findings:[base.finding,{...base.finding,location:'Другая таблица'}]}};
 delete affected.edition_sources;
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions:[affected,...catalog.revisions]})+';'}));
 await page.goto('/site/#remarks=1&env=prod&number=6');
 await expect(page.locator('.review-results tbody tr')).toHaveCount(rows.length);
 const ready=page.waitForEvent('download');await page.locator('#review-export').click();
 const workbook=new ExcelJS.Workbook();await workbook.xlsx.load(require('node:fs').readFileSync(await (await ready).path()));
 const sheet=workbook.getWorksheet('Замечания');expect(sheet.rowCount).toBe(rows.length+1);
 expect(sheet.getCell('A2').value).toBe('6');
 await page.goto('/site/#message=999&env=prod');
 await expect(page.getByRole('link',{name:'Архив 100%.zip',exact:true})).toHaveAttribute('href',archive);
 await expect(page.getByRole('link',{name:'%CF%F0_100%.docx',exact:true})).toHaveAttribute('href',doc);
});

test('XSD filters and Excel preserve different bytes at one official URL',async({page})=>{
 const source=journalRows('14','prod')[0];expect(source).toBeTruthy();
 const official='https://ecp.sfr.gov.ru/sedo/soap/SedoGateway?xsd=Fault.xsd';
 const variants=['a','b'].map(character=>{
  const sha=character.repeat(64),path='Fault.xsd',resource={path,sha256:sha,download:'sources/'+sha+'.xsd',url:official};
  return {...structuredClone(source.revision),revision_id:sha,dependency_digest:sha,schema_path:path,schema_source_type:'wsdl',schema_source_url:'https://ecp.sfr.gov.ru/sedo/soap/SedoGateway?wsdl',schema_files:[resource],schema_sources:[{kind:'xsd',sha256:sha,url:official}],schema_review:{...source.revision.schema_review,findings:[{...source.finding,xlocation:path+' · r/value',xsd_source:resource}]}};
 });
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions:variants})+';'}));
 await routeJournal(page);
 await page.goto('/site/#remarks=1&number=14&env=prod');
 const rows=page.locator('.review-results tbody tr');await expect(rows).toHaveCount(2);
 for(const revision of variants){
  const finding=revision.schema_review.findings[0],key=journal.xsdKey(revision,finding);
  await page.locator('#review-xsd').selectOption(key);await expect(rows).toHaveCount(1);
  await expect(rows.locator('.review-source-chain a').filter({hasText:'Fault.xsd'})).toHaveAttribute('href',new URL(finding.xsd_source.download,page.url()).href);
  await expect(rows.getByRole('link',{name:'Официальная публикация XSD'})).toHaveAttribute('href',official);
 }
 await page.locator('#review-xsd').selectOption('');
 const ready=page.waitForEvent('download');await page.locator('#review-export').click();
 const file=await ready,workbook=new ExcelJS.Workbook();await workbook.xlsx.load(require('node:fs').readFileSync(await file.path()));
 const sheet=workbook.getWorksheet('Замечания');expect(sheet.rowCount).toBe(3);
 expect(sheet.getCell('M2').value.hyperlink).not.toBe(sheet.getCell('M3').value.hyperlink);
 expect(sheet.getRow(2).values.some(value=>value?.hyperlink===official)).toBe(true);
 const legacy=JSON.stringify([variants[0].schema_source_url,'Fault.xsd']);
 await page.goto('/site/#'+new URLSearchParams({remarks:'1',number:'14',env:'prod',xsd:legacy}));
 await expect(rows).toHaveCount(2);await expect(page.locator('#review-xsd option:checked')).toContainText('все версии');
});

test('the second XSD filters one finding and both files remain linked in its workbook row',async({page})=>{
 const {revision,finding}=journalRows('15','prod').find(row=>row.finding.xsd_source?.path==='package-2/v01/type/Organization.xsd');
 const primary=revision.schema_files.find(file=>file.path===revision.schema_path),secondary=finding.xsd_source;
 const single={...finding,location:'Контрольное замечание',xsd_source:primary};
 const multiple={...finding,location:'Замечание по двум файлам',xsd_source:primary,xsd_sources:[primary,secondary]};
 const revisions=[{...revision,schema_review:{...revision.schema_review,findings:[single,multiple]}}];
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions})+';'}));
 await routeJournal(page);
 await page.goto('/site/#remarks=1&number=15&env=prod');
 const rows=page.locator('.review-results tbody tr');
 const filter=page.locator('#review-xsd');
 await expect(rows).toHaveCount(2);
 const option=filter.getByRole('option').filter({hasText:secondary.path});
 await expect(option).toHaveCount(1);
 const key=await option.getAttribute('value');
 await filter.selectOption(key);
 await page.reload();
 await expect(filter).toHaveValue(key);
 await expect(rows).toHaveCount(1);
 await expect(rows).toContainText(multiple.location);
 for(const file of [primary,secondary]){
  await expect(rows.getByRole('link',{name:file.path,exact:true})).toHaveAttribute('href',new URL(file.download,page.url()).href);
 }
 const ready=page.waitForEvent('download');await page.locator('#review-export').click();
 const workbook=new ExcelJS.Workbook();await workbook.xlsx.load(fs.readFileSync(await (await ready).path()));
 const sheet=workbook.getWorksheet('Замечания');
 expect(sheet.rowCount).toBe(2);
 expect(sheet.getCell('F2').value).toBe(multiple.location);
 for(const file of [primary,secondary]){
  expect(sheet.getRow(2).values.filter(value=>value?.hyperlink===new URL(file.download,page.url()).href)).toHaveLength(1);
 }
});

test('the real production message 15 links the exact imported and main XSD bytes',async({page})=>{
 const {revision,finding}=journalRows('15','prod').find(row=>row.finding.xsd_source?.path==='package-2/v01/type/Organization.xsd');
 const primary=revision.schema_files.find(file=>file.path==='package-1/Poved.xsd');
 const compared=finding.xsd_source;
 await routeJournal(page);
 const filter={remarks:'1',number:'15',env:'prod',xsd:journal.xsdKey(revision,finding)};
 await page.goto('/site/#'+new URLSearchParams(filter));
 const rows=page.locator('.review-results tbody tr');
 const row=rows.filter({hasText:finding.spec}).first();
 for(const file of [primary,compared]){
  const selected=row.getByRole('link',{name:file.path,exact:true});
  await expect(selected).toHaveAttribute('href',new URL(file.download,page.url()).href);
  const response=await page.request.get(await selected.getAttribute('href'));
  expect(response.ok()).toBe(true);
  expect(createHash('sha256').update(await response.body()).digest('hex')).toBe(file.sha256);
 }
 const ready=page.waitForEvent('download');await page.locator('#review-export').click();
 const workbook=new ExcelJS.Workbook();await workbook.xlsx.load(fs.readFileSync(await (await ready).path()));
 const sheet=workbook.getWorksheet('Замечания');
 const index=sheet.getColumn('G').values.findIndex(value=>value===finding.spec);
 expect(index).toBeGreaterThan(1);
 for(const [column,file] of [['M',compared],['R',primary]]){
  expect(sheet.getCell(column+index).value).toEqual({text:file.path,hyperlink:new URL(file.download,page.url()).href});
 }
 await page.goto('/site/#remarks=1&number=6&env=prod');
 const [control]=journalRows('6','prod');
 expect(journal.schemaSources(control.revision,control.finding).file.text).toBe(control.revision.schema_path);
 await expect(rows.first().getByRole('link',{name:control.revision.schema_path,exact:true})).toHaveCount(1);
});
test('journal filters match the exported Excel and survive navigation',async({page})=>{
 await page.goto('/site/#remarks=1');
 await expect(page.getByRole('heading',{name:'Журнал замечаний',exact:true})).toBeVisible();
 await page.locator('#review-number').fill('6');
 await page.locator('#review-env').selectOption('prod');
 const rows=page.locator('.review-results tbody tr');
 const selected=journalRows('6','prod');expect(selected.length).toBeGreaterThan(0);
 await expect(rows).toHaveCount(selected.length);
 expect(await rows.locator('td:first-child a').evaluateAll(nodes=>nodes.map(node=>new URLSearchParams(node.hash.slice(1)).get('rev')))).toEqual(selected.map(({revision})=>revision.revision_id));
 const baseline=selected.find(({revision})=>revision.schema_path==='document/SedoArm_ЕЦП.xsd');expect(baseline).toBeTruthy();
 const baselineRow=rows.filter({has:page.locator('td:first-child a[href*="rev='+baseline.revision.revision_id+'"]')}).first();
 await expect(baselineRow).toContainText('confirmMessageReadRequest');
 await expect(baselineRow).toContainText('Продуктивный');
 const chain=baselineRow.locator('.review-source-chain');
 await expect(chain).toContainText('Страница СФР');
 await expect(chain).toContainText('SedoArm_ЕЦП.xsd');
 await expect(chain.getByRole('link',{name:'WSDL шлюза СФР',exact:true})).toHaveAttribute('href','https://ecp.sfr.gov.ru/sedo/soap/SedoGateway?wsdl');
 const download=page.waitForEvent('download');
 await page.locator('#review-export').click();
 const result=await download;
 expect(result.suggestedFilename()).toBe('sedo-remarks-6-prod.xlsx');
 const workbook=new ExcelJS.Workbook();await workbook.xlsx.load(require('node:fs').readFileSync(await result.path()));
 const sheet=workbook.getWorksheet('Замечания');
 expect(sheet.rowCount).toBe(selected.length+1);
 for(const [index,{revision,finding}] of selected.entries()){
  const row=index+2;
  expect(sheet.getCell('A'+row).value).toBe('6');expect(sheet.getCell('B'+row).value).toBe('Продуктивный');
  expect(sheet.getCell('D'+row).value.hyperlink).toBe(new URL(revision.document_download||revision.document_url,page.url()).href);
  expect(sheet.getCell('G'+row).value).toBe(finding.spec);
  const params=new URLSearchParams(new URL(sheet.getCell('O'+row).value.hyperlink).hash.slice(1));
  expect(params.get('rev')).toBe(revision.revision_id);expect(params.get('env')).toBe('prod');
 }
 const baselineExcel=selected.indexOf(baseline)+2;
 expect(sheet.getCell('K'+baselineExcel).value.hyperlink).toBe('https://lk.sfr.gov.ru/sedo.html');
 expect(sheet.getCell('L'+baselineExcel).value.hyperlink).toMatch(/^https:\/\/lk.sfr.gov.ru\/.*\.docx$/);
 expect(sheet.getCell('M'+baselineExcel).value.text).toBe('document/SedoArm_ЕЦП.xsd');
 expect(sheet.getCell('M'+baselineExcel).value.hyperlink).toMatch(/\/sources\/[0-9a-f]{64}\.xsd$/);
 expect(sheet.getCell('N'+baselineExcel).value.hyperlink).toBe('https://ecp.sfr.gov.ru/sedo/soap/SedoGateway?wsdl');
 await page.reload();
 await expect(page.locator('#review-env')).toHaveValue('prod');await expect(page.locator('#review-number')).toHaveValue('6');
 await page.locator('#review-env').selectOption('test');
 await expect(rows).toHaveCount(journalRows('6','test').length);await expect(rows.first()).toContainText('Тестовый');
 await page.locator('#review-number').fill('999999');
 await expect(rows).toHaveCount(0);await expect(page.locator('#review-export')).toBeDisabled();
 await page.locator('.brand').click();await expect(page.locator('#review-journal')).toBeHidden();
});

test('a second reviewed document for message 6 remains visible and exportable',async({page,context})=>{
 const candidate=structuredClone(catalog),original=journalRows('6','prod')[0].revision;
 const updated={...structuredClone(original),revision_id:'e'.repeat(64),document_sha256:'f'.repeat(64),document_download:'documents/'+('f'.repeat(64))+'.docx'};
 candidate.revisions.push(updated);
 await context.route('**/catalog-data.js*',route=>route.fulfill({body:'window.SEDO_CATALOG='+JSON.stringify(candidate)+';',contentType:'application/javascript'}));
 await page.goto('/site/#remarks=1&number=6&env=prod');
 const selected=journal.filter(journal.collect(candidate),'6','prod');expect(selected.length).toBeGreaterThan(journalRows('6','prod').length);
 await expect(page.locator('.review-results tbody tr')).toHaveCount(selected.length);
 const ready=page.waitForEvent('download');await page.locator('#review-export').click();
 const download=await ready,workbook=new ExcelJS.Workbook();await workbook.xlsx.load(require('node:fs').readFileSync(await download.path()));
 const sheet=workbook.getWorksheet('Замечания');expect(sheet.rowCount).toBe(selected.length+1);
 expect(Array.from({length:selected.length},(_,index)=>new URLSearchParams(new URL(sheet.getCell('O'+(index+2)).value.hyperlink).hash.slice(1)).get('rev'))).toEqual(selected.map(({revision})=>revision.revision_id));
 for(const revision of [updated,original]){
  await page.locator('#review-specification').selectOption(journal.specificationKey(revision));
  const documentLinks=page.locator('.review-results tbody tr td:nth-child(2)');
  await expect(documentLinks.first().locator('a').first()).toHaveAttribute('href',revision.document_download);
  await expect(documentLinks.first().getByRole('link',{name:'Публикация СФР'})).toHaveAttribute('href',original.document_url);
 }
 await page.goto('/site/#'+new URLSearchParams({remarks:'1',number:'6',env:'prod',specification:original.document_url}));
 await expect(page.locator('.review-results tbody tr')).toHaveCount(journal.filter(selected,'6','prod',original.document_url).length);
 await expect(page.locator('#review-specification option:checked')).toContainText('все версии');
});

test('specification and compared XSD filters persist and bound Excel rows',async({page})=>{
 await page.goto('/site/#remarks=1&number=111&env=prod');
 const rows=page.locator('.review-results tbody tr');
 const source=journalRows('111','prod')[0].revision;
 const specification=journal.specificationKey(source);
 const xsdPath=await rows.first().locator('.review-source-chain a').nth(2).innerText();
 await page.locator('#review-specification').selectOption(specification);
 const fileValue=await page.locator('#review-xsd option').evaluateAll((options,path)=>options.find(o=>o.textContent.startsWith(path+' · ')&&o.textContent.endsWith(' · Продуктивный')).value,xsdPath);
 await page.locator('#review-xsd').selectOption(fileValue);
 await page.locator('#review-number').fill('');
 const count=await rows.count();expect(count).toBeGreaterThan(0);
 await page.reload();
 await expect(page.locator('#review-specification')).toHaveValue(specification);
 await expect(page.locator('#review-xsd')).toHaveValue(fileValue);
 await expect(rows).toHaveCount(count);
 const ready=page.waitForEvent('download');await page.locator('#review-export').click();
 const file=await ready,workbook=new ExcelJS.Workbook();
 await workbook.xlsx.load(require('node:fs').readFileSync(await file.path()));
 const sheet=workbook.getWorksheet('Замечания');expect(sheet.rowCount).toBe(count+1);
 for(let row=2;row<=sheet.rowCount;row++){
  expect(sheet.getCell('D'+row).value.hyperlink).toBe(new URL(source.document_download||source.document_url,page.url()).href);
  expect(sheet.getCell('M'+row).value.text).toBe(xsdPath);
 }
 await page.locator('#review-env').selectOption('test');await expect(rows).toHaveCount(0);
 await expect(page.locator('#review-export')).toBeDisabled();
});
