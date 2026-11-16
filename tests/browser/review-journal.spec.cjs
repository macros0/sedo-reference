const {test,expect}=require('@playwright/test');
const ExcelJS=require('../../web/vendor/exceljs.min.js');
const {catalog,journal,journalRows}=require('./catalog-fixtures.cjs');
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
  expect(sheet.getCell('D'+row).value.hyperlink).toBe(new URL(revision.document_url||revision.document_download,page.url()).href);
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
 candidate.revisions.push({...structuredClone(original),revision_id:'e'.repeat(64),document_sha256:'f'.repeat(64)});
 await context.route('**/catalog-data.js*',route=>route.fulfill({body:'window.SEDO_CATALOG='+JSON.stringify(candidate)+';',contentType:'application/javascript'}));
 await page.goto('/site/#remarks=1&number=6&env=prod');
 const selected=journal.filter(journal.collect(candidate),'6','prod');expect(selected.length).toBeGreaterThan(journalRows('6','prod').length);
 await expect(page.locator('.review-results tbody tr')).toHaveCount(selected.length);
 const ready=page.waitForEvent('download');await page.locator('#review-export').click();
 const download=await ready,workbook=new ExcelJS.Workbook();await workbook.xlsx.load(require('node:fs').readFileSync(await download.path()));
 const sheet=workbook.getWorksheet('Замечания');expect(sheet.rowCount).toBe(selected.length+1);
 expect(Array.from({length:selected.length},(_,index)=>new URLSearchParams(new URL(sheet.getCell('O'+(index+2)).value.hyperlink).hash.slice(1)).get('rev'))).toEqual(selected.map(({revision})=>revision.revision_id));
});

test('specification and compared XSD filters persist and bound Excel rows',async({page})=>{
 await page.goto('/site/#remarks=1&number=111&env=prod');
 const rows=page.locator('.review-results tbody tr');
 const specification=await rows.first().locator('td').nth(1).locator('a').getAttribute('href');
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
  expect(sheet.getCell('D'+row).value.hyperlink).toBe(new URL(specification).href);
  expect(sheet.getCell('M'+row).value.text).toBe(xsdPath);
 }
 await page.locator('#review-env').selectOption('test');await expect(rows).toHaveCount(0);
 await expect(page.locator('#review-export')).toBeDisabled();
});
