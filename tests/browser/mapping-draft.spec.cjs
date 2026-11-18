const {test,expect}=require('@playwright/test');
const ExcelJS=require('../../web/vendor/exceljs.min.js');
const {catalog}=require('./catalog-fixtures.cjs');
const left=catalog.revisions.find(r=>r.number==='105'&&r.environment==='prod'&&r.document_version==='2.28'&&r.ready);
const right=catalog.revisions.find(r=>r.number==='10510'&&r.environment==='prod'&&r.document_version==='3.0'&&r.ready);
const rootPair={beforePath:'approveSocialAssistResult',afterPath:'benefitResponseResult',comment:'Из Excel'};
const statusPair={beforePath:rootPair.beforePath+'/status',afterPath:rootPair.afterPath+'/status',comment:'Применено'};
const key='sedo-field-mappings:v1:'+[left.revision_id,right.revision_id].sort().map(encodeURIComponent).join(':');
const url='/site/#'+new URLSearchParams({message:'10510',compare:'1',left:left.revision_id,right:right.revision_id});

test.beforeEach(async({context})=>{
 const path=require('node:path');
 for(const script of ['compare.js','compare-core.js','comparison-mapping.js','mapping-draft.js','mapping-editor.js'])await context.route('**/'+script+'*',route=>route.fulfill({path:path.join(__dirname,'../../web',script),contentType:'application/javascript'}));
});

test('unapplied browser draft survives field navigation with comment and selections, isolated by direction',async({page,context})=>{
 await page.goto(url);await page.locator('.mapping-add').click();
 await page.locator('.mapping-tag-choice-before').selectOption(rootPair.beforePath);await page.locator('.mapping-tag-choice-after').selectOption(rootPair.afterPath);
 await page.locator('.mapping-edit-comment').fill('Проверить после чтения поля');await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();
 const excluded=page.locator('.mapping-proposal').first(),excludedPath=await excluded.getAttribute('data-before-path');await excluded.uncheck();
 const count=await page.locator('.mapping-table input:checked').count();expect(count).toBeGreaterThan(1);
 const visitField=async()=>{await page.locator('.difference-links').getByRole('link',{name:'Поле в «Было»',exact:true}).first().click();await expect(page.locator('#frame')).toBeVisible();await page.goBack();await expect(page.locator('.mapping-add')).toBeVisible()};
 await visitField();
 await expect(page.locator('.mapping-preview')).toContainText('Проверить после чтения поля');
 await expect(page.locator('.mapping-proposal[data-before-path="'+excludedPath+'"]')).not.toBeChecked();
 await expect(page.locator('.mapping-table input:checked')).toHaveCount(count);
 expect(await page.evaluate(key=>localStorage.getItem(key),key)).toBeNull();
 await page.getByRole('button',{name:'Поменять местами',exact:true}).click();await expect(page.locator('.mapping-add')).toBeVisible();await expect(page.locator('.mapping-table')).toHaveCount(0);
 await page.getByRole('button',{name:'Поменять местами',exact:true}).click();await expect(page.locator('.mapping-preview')).toContainText('Проверить после чтения поля');
 await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toContainText(String(count));
 const applied=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)).pairs,key);expect(applied).toHaveLength(count);expect(applied.some(p=>p.beforePath===excludedPath)).toBe(false);expect(applied.find(p=>p.beforePath===rootPair.beforePath).comment).toBe('Проверить после чтения поля');
 await visitField();await expect(page.locator('.mapping-table')).toHaveCount(0);
 await page.locator('.mapping-suggest').click();await page.locator('.mapping-edit-row').first().click();await page.locator('.mapping-edit-comment').fill('Отменённый комментарий');await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();await page.locator('.mapping-discard').click();
 await visitField();await expect(page.locator('.mapping-table')).toHaveCount(0);await page.locator('.mapping-suggest').click();await expect(page.locator('.mapping-preview')).not.toContainText('Отменённый комментарий');
});

test('exported workbook reopens descendants of an explicitly mapped nested block',async({page})=>{
 const before=rootPair.beforePath+'/errorList',after=rootPair.afterPath+'/errorList';
 const block={beforePath:before+'/error',afterPath:after+'/error',comment:'Открытый блок'};
 const stored={version:1,left:left.revision_id,right:right.revision_id,pairs:[block],deleted:{before:[before],after:[]}};
 await page.goto(url);
 await page.evaluate(({key,stored})=>localStorage.setItem(key,JSON.stringify(stored)),{key,stored});
 await page.reload();await page.locator('.mapping-suggest').click();
 const nested=page.locator('.mapping-proposal').filter({visible:true});
 const field=page.locator('.mapping-proposal[data-before-path="'+block.beforePath+'/code"]');
 await expect(field).toBeChecked();const count=await nested.count();
 const downloadPromise=page.waitForEvent('download');await page.getByRole('button',{name:'Выгрузить Excel для сопоставления'}).click();
 const download=await downloadPromise;await page.locator('.mapping-file').setInputFiles(await download.path());
 await expect(page.locator('.mapping-explicit').first().locator('xpath=ancestor::tr')).toContainText('Из Excel');
 await expect(field).toBeChecked();expect(await nested.count()).toBe(count);
 await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toBeVisible();
 const saved=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key);
 expect(saved.deleted).toEqual(stored.deleted);expect(saved.pairs.some(p=>p.beforePath===block.beforePath+'/code')).toBe(true);
});

test('Excel draft survives reviewing mappings and applies only selected explicit pairs',async({page,context})=>{
 await context.route('**/*',route=>new URL(route.request().url()).origin==='http://127.0.0.1:4173'?route.continue():route.abort());
 await page.goto(url);
 await page.evaluate(({key,left,right,pairs})=>localStorage.setItem(key,JSON.stringify({version:1,left,right,pairs})),{key,left:left.revision_id,right:right.revision_id,pairs:[statusPair]});
 await page.reload();await expect(page.locator('.mapping-suggest')).toBeVisible();
 const workbook=new ExcelJS.Workbook(),metadata=workbook.addWorksheet('О выгрузке'),sheet=workbook.addWorksheet('Сопоставление');
 metadata.addRow(['Идентификатор редакции',left.revision_id,right.revision_id]);
 sheet.addRow(['Путь «Было»','Путь «Стало» — введите или выберите','Решение — вручную','Комментарий — вручную']);
 sheet.addRow([statusPair.beforePath,statusPair.afterPath,'Подтверждено',statusPair.comment]);
 sheet.addRow([rootPair.beforePath,rootPair.afterPath,'Не проверено',rootPair.comment]);
 await page.locator('.mapping-file').setInputFiles({name:'draft.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from(await workbook.xlsx.writeBuffer())});
 // The unconfirmed parent must not silently select any of its other children.
 await expect(page.locator('.mapping-proposal')).toHaveCount(1);
 const rootRow=page.locator('.mapping-table tbody tr').filter({has:page.getByRole('checkbox',{name:'Сопоставить '+rootPair.beforePath+' → '+rootPair.afterPath,exact:true})});
 await rootRow.locator('.mapping-edit-row').click();await page.locator('.mapping-edit-comment').fill('Сохранённая правка');await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();
 // Editing explicitly confirms this parent and enables the usual child proposals.
 expect(await page.locator('.mapping-proposal').count()).toBeGreaterThan(1);
 await page.locator('.mapping-select-none').click();
 await expect(rootRow.getByRole('checkbox')).toBeChecked();
 await page.locator('.mapping-add-row').click();
 await page.locator('.mapping-tag-choice-before').selectOption(rootPair.beforePath+'/socialAssistNum');await page.locator('.mapping-tag-choice-after').selectOption(rootPair.afterPath+'/processNum');
 await page.locator('.mapping-edit-comment').fill('Новая пара');await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();
 await page.locator('.mapping-select-none').click();
 await page.locator('.mapping-suggest').click();
 await expect(page.locator('.mapping-preview')).toContainText('Сохранённая правка');await expect(page.locator('.mapping-preview')).toContainText('Новая пара');
 await expect(page.locator('.mapping-draft-notice')).toBeVisible();
 expect(await page.locator('.mapping-proposal:not(:checked)').count()).toBeGreaterThan(0);
 await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toContainText('3');
 const pairs=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)).pairs,key);
 expect(pairs).toHaveLength(3);expect(pairs.find(p=>p.beforePath===rootPair.beforePath).comment).toBe('Сохранённая правка');expect(pairs.some(p=>p.comment==='Новая пара')).toBe(true);
 await page.getByRole('button',{name:'Поменять местами'}).click();
 await page.locator('.mapping-suggest').click();
 await expect(page.locator('.mapping-explicit:checked')).toHaveCount(3);
 await expect(page.locator('.mapping-preview')).toContainText('Сохранённая правка');
});
