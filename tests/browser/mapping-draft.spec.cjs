const {test,expect}=require('@playwright/test');
const fs=require('node:fs');
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
 for(const script of ['compare.js','compare-core.js','comparison-export.js','comparison-mapping.js','mapping-draft.js','mapping-editor.js'])await context.route('**/'+script+'*',route=>route.fulfill({path:path.join(__dirname,'../../web',script),contentType:'application/javascript'}));
});

const visitField=async page=>{
 await page.locator('.difference-links').getByRole('link',{name:'Поле в «Было»',exact:true}).first().click();
 await expect(page.locator('#frame')).toBeVisible();
 await page.goBack();await expect(page.locator('.mapping-add')).toBeVisible();
};
const loadSaved=async (page,pair=statusPair)=>{
 await page.goto('/site/');
 await page.evaluate(({key,left,right,pair,comparison})=>{
  localStorage.setItem(key,JSON.stringify({version:1,left,right,pairs:[pair]}));
  location.hash=comparison;
 },{key,left:left.revision_id,right:right.revision_id,pair,comparison:url.split('#')[1]});
 await expect(page.locator('.mapping-suggest')).toBeVisible();
};
const readSaved=page=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key);
const choosePaths=async (page,beforePath,afterPath)=>{
 await page.locator('.mapping-tag-choice-before').selectOption(beforePath);
 await page.locator('.mapping-tag-choice-after').selectOption(afterPath);
};
const compareDownload=async page=>{
 const download=page.waitForEvent('download');
 await page.getByRole('button',{name:'Выгрузить Excel для сопоставления'}).click();
 return download;
};
const uploadWorkbook=async (page,book)=>{
 await page.locator('.mapping-file').setInputFiles({name:'review.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from(await book.xlsx.writeBuffer())});
};
const workbookRow=(sheet,path)=>{
 let found;
 sheet.eachRow(row=>{if(row.getCell(2).value===path)found=row});
 expect(found).toBeDefined();
 return found;
};

for(const mode of ['add','edit']){
 test('unfinished '+mode+' form survives returning from a field and stays local to its direction',async({page})=>{
  await loadSaved(page);
  if(mode==='edit'){
   await page.locator('.mapping-suggest').click();
   await page.locator('.mapping-edit-row').first().click();
  }else await page.locator('.mapping-add').click();
  const beforePath=rootPair.beforePath+'/socialAssistNum',afterPath=rootPair.afterPath+'/processNum';
  await choosePaths(page,beforePath,afterPath);
  await page.locator('.mapping-tag-search-before').fill('socialAssistNum');
  await page.locator('.mapping-tag-search-after').fill('processNum');
  await page.locator('.mapping-edit-comment').fill('Незавершённая форма');
  await visitField(page);
  const verifyForm=async()=>{
   await expect(page.locator('.mapping-edit-form h3')).toHaveText(mode==='edit'?'Изменить соответствие':'Добавить соответствие',{timeout:2000});
   await expect(page.locator('.mapping-tag-choice-before')).toHaveValue(beforePath);
   await expect(page.locator('.mapping-tag-choice-after')).toHaveValue(afterPath);
   await expect(page.locator('.mapping-tag-search-before')).toHaveValue('socialAssistNum');
   await expect(page.locator('.mapping-tag-search-after')).toHaveValue('processNum');
   await expect(page.locator('.mapping-edit-comment')).toHaveValue('Незавершённая форма');
   await expect(page.locator('.mapping-apply')).toBeDisabled();
  };
  await verifyForm();
  const swap=page.getByRole('button',{name:'Поменять местами',exact:true});
  await swap.click();await expect(page.locator('.mapping-add')).toBeVisible();await expect(page.locator('.mapping-edit-form')).toHaveCount(0);
  await swap.click();await verifyForm();
  expect((await readSaved(page)).pairs).toEqual([statusPair]);
  await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();
  const pending=page.locator('.mapping-explicit[data-before-path="'+beforePath+'"]');
  await expect(pending).toBeChecked();await pending.locator('xpath=ancestor::tr').locator('.mapping-edit-row').click();
  await page.locator('.mapping-edit-comment').fill('Отменённый ввод');await page.locator('.mapping-editor-cancel').click();
  await visitField(page);await expect(page.locator('.mapping-edit-form')).toHaveCount(0);
  await expect(pending.locator('xpath=ancestor::tr')).toContainText('Незавершённая форма');
  expect((await readSaved(page)).pairs).toEqual([statusPair]);
 });
}

test('the apply banner explains a half-selected next pair and lets me discard only that pair',async({page})=>{
 await loadSaved(page);
 await page.locator('.mapping-add').click();
 const beforePath=rootPair.beforePath+'/socialAssistNum',afterPath=rootPair.afterPath+'/processNum';
 await choosePaths(page,beforePath,afterPath);
 await page.locator('.mapping-editor-save').click();
 const apply=page.locator('.mapping-apply-top'),notice=page.locator('.mapping-draft-notice');
 await expect(apply).toBeEnabled();
 const readyLabel=await apply.textContent();
 await page.locator('.mapping-tag-search-before').fill('errorList');
 await expect(apply).toBeEnabled();
 await page.locator('.mapping-tag-choice-before').selectOption(rootPair.beforePath+'/errorList');
 await expect(apply).toBeDisabled();
 await expect(apply).toHaveCSS('opacity','0.55');
 await expect(notice).toContainText('Выберите тег «Стало»');
 const cancel=notice.getByRole('button',{name:'Отменить правку пары'});
 await cancel.click();
 await expect(page.locator('.mapping-edit-form')).toHaveCount(0);
 await expect(apply).toHaveText(readyLabel);
 await expect(apply).toBeEnabled();
 await apply.click();
 await expect(page.locator('.mapping-applied-feedback')).toBeVisible();
 expect((await readSaved(page)).pairs).toEqual(expect.arrayContaining([statusPair,expect.objectContaining({beforePath,afterPath})]));
});

test('downloaded workbook accepts lowercase decisions in both directions and rejects a typo without replacing the preview',async({page})=>{
 await loadSaved(page);
 const download=await compareDownload(page),book=new ExcelJS.Workbook();
 await book.xlsx.load(fs.readFileSync(await download.path()));
 const sheet=book.getWorksheet('Сопоставление'),status=workbookRow(sheet,statusPair.beforePath);
 const blank=workbookRow(sheet,rootPair.beforePath+'/socialAssistNum');
 const deleted=workbookRow(sheet,rootPair.beforePath+'/errorList');
 status.getCell(8).value='подтверждено';
 blank.getCell(8).value=null;blank.getCell(5).value=rootPair.afterPath+'/processNum';
 deleted.getCell(8).value='удалено';
 for(const reversed of [false,true]){
  if(reversed)await page.getByRole('button',{name:'Поменять местами',exact:true}).click();
  await uploadWorkbook(page,book);
  const beforePath=reversed?statusPair.afterPath:statusPair.beforePath;
  const confirmed=page.locator('.mapping-explicit[data-before-path="'+beforePath+'"]');
  await expect(confirmed.locator('xpath=ancestor::tr')).toContainText(statusPair.comment,{timeout:2000});
  const proposed=page.locator('.mapping-proposal[data-before-path="'+(reversed?blank.getCell(5).value:blank.getCell(2).value)+'"]');
  await expect(proposed).toBeChecked();
  await proposed.uncheck();
  await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toContainText('пар: 1');
  const record=await readSaved(page);
  expect(record.pairs).toEqual([reversed?{beforePath:statusPair.afterPath,afterPath:statusPair.beforePath,comment:statusPair.comment}:statusPair]);
  expect(record.deleted[reversed?'after':'before']).toEqual([deleted.getCell(2).value]);
 }
 await page.locator('.mapping-suggest').click();
 await page.locator('.mapping-edit-row').first().click();
 await page.locator('.mapping-edit-comment').fill('Черновик до неверной книги');
 const original=await readSaved(page);
 status.getCell(8).value='Подтверждно';
 await uploadWorkbook(page,book);
 await expect(page.locator('.mapping-panel > [role=status]')).toContainText(new RegExp('Строка '+status.number+':.*Подтверждно'));
 await expect(page.locator('.mapping-edit-comment')).toHaveValue('Черновик до неверной книги');
 await expect(page.locator('.mapping-apply')).toBeDisabled();
 expect(await readSaved(page)).toEqual(original);
});

test('overlong legacy comment remains editable and valid boundary exports without truncation',async({page})=>{
 const maximum='я'.repeat(32767),oversized=maximum+'ю';
 await loadSaved(page,{...statusPair,comment:oversized});
 const original=await readSaved(page);
 await page.locator('.mapping-suggest').click();
 await expect(page.locator('.mapping-apply')).toBeDisabled({timeout:2000});
 await page.locator('.mapping-edit-row').first().click();
 const comment=page.locator('.mapping-edit-comment'),save=page.locator('.mapping-editor-save');
 await expect(comment).toHaveValue(oversized);
 await save.click();await expect(page.locator('.mapping-editor-error')).toContainText('32767');
 await expect(comment).toHaveValue(oversized);
 expect(await readSaved(page)).toEqual(original);
 await comment.fill(maximum);await save.click();await page.locator('.mapping-editor-cancel').click();
 await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toBeVisible();
 const download=await compareDownload(page),book=new ExcelJS.Workbook();
 await book.xlsx.load(fs.readFileSync(await download.path()));
 expect(workbookRow(book.getWorksheet('Сопоставление'),statusPair.beforePath).getCell(9).value).toBe(maximum);
});

test('duplicate XLSX comments over the Excel limit preserve the existing preview',async({page})=>{
 await loadSaved(page);
 await page.locator('.mapping-suggest').click();
 const book=new ExcelJS.Workbook(),metadata=book.addWorksheet('О выгрузке'),sheet=book.addWorksheet('Сопоставление');
 metadata.addRow(['Идентификатор редакции',left.revision_id,right.revision_id]);
 sheet.addRow(['Путь «Было»','Путь «Стало» — введите или выберите','Решение — вручную','Комментарий — вручную']);
 sheet.addRow([statusPair.beforePath,statusPair.afterPath,'Подтверждено','я'.repeat(16384)]);
 const duplicate=sheet.addRow([statusPair.beforePath,statusPair.afterPath,'Подтверждено','ю'.repeat(16384)]);
 await uploadWorkbook(page,book);
 await expect(page.locator('.mapping-panel > [role=status]')).toContainText(new RegExp('Строка 3:.*32767'),{timeout:2000});
 await expect(page.locator('.mapping-table')).toContainText(statusPair.comment);
 expect((await readSaved(page)).pairs).toEqual([statusPair]);
 duplicate.getCell(4).value='ю'.repeat(16382);
 await uploadWorkbook(page,book);
 await expect(page.locator('.mapping-table')).toContainText('Из Excel');
 await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toBeVisible();
 expect((await readSaved(page)).pairs[0].comment).toBe('я'.repeat(16384)+'\n'+'ю'.repeat(16382));
});

for(const [reason,response,message] of [
 ['missing registration',{body:''},'нет дерева тегов'],
 ['non-array registration',{body:'window.SEDO_FIELDS['+JSON.stringify(left.revision_id)+'] = {}'},'нет дерева тегов'],
 ['HTTP error',{status:503,body:''},'Не удалось загрузить дерево']
]){
 test('comparison retries a field tree after '+reason,async({page})=>{
  let requests=0;
  await page.route('**/fields/'+left.revision_id+'.js*',route=>{
   requests++;
   return requests===1?route.fulfill({contentType:'application/javascript',...response}):route.continue();
  });
  await page.goto(url);
  await expect(page.locator('#comparison .empty').first()).toContainText(message,{timeout:2000});
  await page.evaluate(()=>{location.hash=''});
  await expect(page.locator('#catalog')).toBeVisible();
  await page.goBack();await expect(page.locator('.mapping-add')).toBeVisible({timeout:2000});
  expect(requests).toBe(2);
 });
}

test('unapplied browser draft survives field navigation with comment and selections, isolated by direction',async({page})=>{
 await page.goto(url);await page.locator('.mapping-add').click();
 await page.locator('.mapping-tag-choice-before').selectOption(rootPair.beforePath);await page.locator('.mapping-tag-choice-after').selectOption(rootPair.afterPath);
 await page.locator('.mapping-edit-comment').fill('Проверить после чтения поля');await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();
 const excluded=page.locator('.mapping-proposal').first(),excludedPath=await excluded.getAttribute('data-before-path');await excluded.uncheck();
 const count=await page.locator('.mapping-table input:checked').count();expect(count).toBeGreaterThan(1);
 await visitField(page);
 await expect(page.locator('.mapping-preview')).toContainText('Проверить после чтения поля');
 await expect(page.locator('.mapping-proposal[data-before-path="'+excludedPath+'"]')).not.toBeChecked();
 await expect(page.locator('.mapping-table input:checked')).toHaveCount(count);
 expect(await page.evaluate(key=>localStorage.getItem(key),key)).toBeNull();
 await page.getByRole('button',{name:'Поменять местами',exact:true}).click();await expect(page.locator('.mapping-add')).toBeVisible();await expect(page.locator('.mapping-table')).toHaveCount(0);
 await page.getByRole('button',{name:'Поменять местами',exact:true}).click();await expect(page.locator('.mapping-preview')).toContainText('Проверить после чтения поля');
 await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toContainText(String(count));
 const applied=await page.evaluate(key=>JSON.parse(localStorage.getItem(key)).pairs,key);expect(applied).toHaveLength(count);expect(applied.some(p=>p.beforePath===excludedPath)).toBe(false);expect(applied.find(p=>p.beforePath===rootPair.beforePath).comment).toBe('Проверить после чтения поля');
 await visitField(page);await expect(page.locator('.mapping-table')).toHaveCount(0);
 await page.locator('.mapping-suggest').click();await page.locator('.mapping-edit-row').first().click();await page.locator('.mapping-edit-comment').fill('Отменённый комментарий');await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();await page.locator('.mapping-discard').click();
 await visitField(page);await expect(page.locator('.mapping-table')).toHaveCount(0);await page.locator('.mapping-suggest').click();await expect(page.locator('.mapping-preview')).not.toContainText('Отменённый комментарий');
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
