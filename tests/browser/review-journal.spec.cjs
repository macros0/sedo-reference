const {test,expect}=require('@playwright/test');
const ExcelJS=require('../../web/vendor/exceljs.min.js');
const {catalog,journal,journalRows}=require('./catalog-fixtures.cjs');
const fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');

async function routeJournal(page){
 await page.route('**/review-journal.js*',route=>route.fulfill({
  contentType:'application/javascript',path:path.join(__dirname,'../../web/review-journal.js')
 }));
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
