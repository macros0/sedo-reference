const {test,expect}=require('@playwright/test');
const fs=require('node:fs');
const path=require('node:path');
const ExcelJS=require('../../web/vendor/exceljs.min.js');
const {catalog}=require('./catalog-fixtures.cjs');
const left=catalog.revisions.find(r=>r.number==='105'&&r.environment==='prod'&&r.document_version==='2.28'&&r.ready);
const right=catalog.revisions.find(r=>r.number==='10510'&&r.environment==='prod'&&r.document_version==='3.0'&&r.ready);
const rootPair={beforePath:'approveSocialAssistResult',afterPath:'benefitResponseResult',comment:'Из Excel'};
const statusPair={beforePath:rootPair.beforePath+'/status',afterPath:rootPair.afterPath+'/status',comment:'Применено'};
const key='sedo-field-mappings:v1:'+[left.revision_id,right.revision_id].sort().map(encodeURIComponent).join(':');
const url='/site/#'+new URLSearchParams({message:'10510',compare:'1',left:left.revision_id,right:right.revision_id});
const reverseUrl='/site/#'+new URLSearchParams({message:'10510',compare:'1',left:right.revision_id,right:left.revision_id});

async function serveCurrentMapping(context){
 for(const script of ['compare.js','compare-core.js','comparison-export.js','comparison-mapping.js','mapping-draft.js','mapping-editor.js'])await context.route('**/'+script+'*',route=>route.fulfill({path:path.join(__dirname,'../../web',script),contentType:'application/javascript'}));
}
test.beforeEach(async({context})=>{await serveCurrentMapping(context)});

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
const samePaths=(a,b)=>a.beforePath===b.beforePath&&a.afterPath===b.afterPath;
const reversed=({beforePath,afterPath,...props})=>({...props,beforePath:afterPath,afterPath:beforePath});
const {comment:statusComment,...rejectedStatus}=statusPair;
const mappingChoice=(page,pair)=>page.locator(`input[data-before-path="${pair.beforePath}"][data-after-path="${pair.afterPath}"]`);
const mappingRow=(page,pair)=>mappingChoice(page,pair).locator('xpath=ancestor::tr');
const applyMappings=async page=>{
 await page.locator('.mapping-apply').click();
 await expect(page.locator('.mapping-applied-feedback')).toBeVisible();
 await expect(page.locator('.mapping-apply')).toHaveCount(0);
};
const loadWorkbook=async download=>{
 const book=new ExcelJS.Workbook();await book.xlsx.load(fs.readFileSync(await download.path()));return book;
};
const enterExcelStatus=async (page,decision)=>{
 const book=await loadWorkbook(await compareDownload(page));
 const row=workbookRow(book.getWorksheet('Сопоставление'),statusPair.beforePath);
 row.getCell(5).value=statusPair.afterPath;row.getCell(8).value=decision;
 row.getCell(9).value='Решение из Excel';
 await uploadWorkbook(page,book);
 await expect(mappingRow(page,statusPair)).toContainText('Из Excel');
};

test('downloaded XLSX keeps exact exclusions and 13 saved pairs in clean forward and reverse comparisons',async({baseURL,browser,page})=>{
 await loadSaved(page,rootPair);
 await page.locator('.mapping-suggest').click();
 await mappingRow(page,statusPair).locator('.mapping-remove-row').click();
 await applyMappings(page);
 const saved=await readSaved(page);
 expect(saved.pairs).toHaveLength(13);
 expect(saved.dismissed).toEqual([rejectedStatus]);
 const file=await (await compareDownload(page)).path();
 const book=await loadWorkbook(await compareDownload(page));
 const candidate=workbookRow(book.getWorksheet('Сопоставление'),statusPair.beforePath);
 candidate.getCell(5).value=statusPair.afterPath;
 expect(candidate.getCell(8).value).toBe('Не проверено');
 for(const reverse of [false,true]){
  const context=await browser.newContext({baseURL});
  try{
   await serveCurrentMapping(context);
   const other=await context.newPage(),orient=reverse?reversed:pair=>pair;
   await other.goto(reverse?reverseUrl:url);
   expect(await readSaved(other)).toBeNull();
   if(reverse)await uploadWorkbook(other,book);
   else await other.locator('.mapping-file').setInputFiles(file);
   await expect(other.locator('.mapping-apply')).toHaveText('Применить и обновить сравнение (13)');
   await expect(other.locator('.mapping-explicit:checked')).toHaveCount(13);
   await expect(mappingChoice(other,orient(statusPair))).toHaveCount(0);
   expect(await readSaved(other)).toBeNull();
   await other.locator('.mapping-discard').click();
   expect(await readSaved(other)).toBeNull();
   await other.locator('.mapping-file').setInputFiles(file);
   await applyMappings(other);
   const restored=await readSaved(other);
   expect(restored.dismissed).toEqual([orient(rejectedStatus)]);
   expect(restored.pairs).toHaveLength(13);
   expect(restored.pairs).toEqual(expect.arrayContaining(saved.pairs.map(orient)));
   await other.locator('.mapping-suggest').click();
   await expect(mappingChoice(other,orient(statusPair))).toHaveCount(0);
   const reexport=await loadWorkbook(await compareDownload(other));
   const rows=reexport.getWorksheet('Сопоставление').getRows(6,reexport.getWorksheet('Сопоставление').rowCount-5);
   const confirmed=rows.filter(row=>row.getCell(8).value==='Подтверждено')
    .map(row=>({beforePath:row.getCell(2).value,afterPath:row.getCell(5).value,comment:row.getCell(9).value}));
   expect(confirmed).toHaveLength(13);
   expect(confirmed).toEqual(expect.arrayContaining(saved.pairs.map(orient)));
   await other.locator('.mapping-file').setInputFiles(await (await compareDownload(other)).path());
   await expect(other.locator('.mapping-explicit:checked')).toHaveCount(13);
   await expect(mappingChoice(other,orient(statusPair))).toHaveCount(0);
  }finally{await context.close()}
 }
});

for(const decision of ['Не проверено','Требует уточнения']){
 test('removing a '+decision+' Excel row survives apply, reopen and explicit acceptance',async({baseURL,browser,page})=>{
  await loadSaved(page,rootPair);
  await enterExcelStatus(page,decision);
  const choice=mappingChoice(page,statusPair);
  if(decision==='Не проверено')await expect(choice).toBeChecked();
  else await expect(choice).not.toBeChecked();
  await mappingRow(page,statusPair).locator('.mapping-remove-row').click();
  await applyMappings(page);
  const saved=await readSaved(page);
  expect(saved.dismissed).toContainEqual(rejectedStatus);
  expect(saved.pairs.some(pair=>samePaths(pair,statusPair))).toBe(false);
  await page.locator('.mapping-suggest').click();
  await expect(choice).toHaveCount(0);
  if(decision==='Требует уточнения'){
   const file=await (await compareDownload(page)).path(),context=await browser.newContext({baseURL});
   try{
    await serveCurrentMapping(context);
    const other=await context.newPage();await other.goto(url);
    expect(await readSaved(other)).toBeNull();
    await other.locator('.mapping-file').setInputFiles(file);
    await expect(other.locator('.mapping-apply')).toBeEnabled();
    await expect(mappingChoice(other,statusPair)).toHaveCount(0);
    await applyMappings(other);
    expect((await readSaved(other)).dismissed).toContainEqual(rejectedStatus);
   }finally{await context.close()}
  }
  await page.locator('.mapping-add-row').click();
  await choosePaths(page,statusPair.beforePath,statusPair.afterPath);
  await page.locator('.mapping-edit-comment').fill(statusComment);
  await page.locator('.mapping-editor-save').click();
  await page.locator('.mapping-editor-cancel').click();
  await applyMappings(page);
  const accepted=await readSaved(page);
  expect(accepted.dismissed??[]).not.toContainEqual(rejectedStatus);
  expect(accepted.pairs).toContainEqual(statusPair);
 });
}

for(const bulk of [false,true]){
 test((bulk?'Bulk-deselected':'Untouched')+' Excel proposals remain eligible when the preview reopens',async({page})=>{
  await loadSaved(page,rootPair);
  await enterExcelStatus(page,bulk?'Не проверено':'Требует уточнения');
  if(bulk)await page.locator('.mapping-select-none').click();
  await expect(mappingChoice(page,statusPair)).not.toBeChecked();
  await applyMappings(page);
  expect((await readSaved(page)).dismissed??[]).toEqual([]);
  await page.locator('.mapping-suggest').click();
  await expect(mappingChoice(page,statusPair)).toBeChecked();
 });
}

test('removing an unchecked Excel proposal preserves a confirmation in another tab',async({context,page})=>{
 await loadSaved(page,rootPair);
 await enterExcelStatus(page,'Требует уточнения');
 await expect(mappingChoice(page,statusPair)).not.toBeChecked();
 await page.locator('.mapping-select-none').click();
 await mappingRow(page,statusPair).locator('.mapping-remove-row').click();
 const other=await context.newPage();
 await other.goto(reverseUrl);
 await other.locator('.mapping-suggest').click();
 await other.locator('.mapping-select-none').click();
 const confirmed=reversed({...statusPair,comment:'Подтверждено во второй вкладке'});
 await mappingRow(other,confirmed).locator('.mapping-edit-row').click();
 await other.locator('.mapping-edit-comment').fill(confirmed.comment);
 await other.locator('.mapping-editor-save').click();
 await other.locator('.mapping-editor-cancel').click();
 await applyMappings(other);
 await applyMappings(page);
 const saved=await readSaved(page);
 expect(saved.pairs).toContainEqual(reversed(confirmed));
 expect(saved.dismissed??[]).not.toContainEqual(rejectedStatus);
 await page.locator('.mapping-suggest').click();
 await expect(mappingRow(page,statusPair)).toContainText(confirmed.comment);
});

test('exclusion-only XLSX applies with zero pairs after a browser-storage denial',async({page})=>{
 await loadSaved(page,statusPair);
 await page.evaluate(key=>{
  const save=Storage.prototype.setItem;
  Storage.prototype.setItem=function(name,value){if(name===key)throw new DOMException('Denied','SecurityError');return save.call(this,name,value)};
 },key);
 await page.locator('.mapping-undo').click();
 await expect(page.locator('.mapping-panel')).toContainText('текущем сеансе');
 expect((await readSaved(page)).pairs).toEqual([statusPair]);
 const file=await (await compareDownload(page)).path();
 const book=await loadWorkbook(await compareDownload(page));
 const meta=book.getWorksheet('О выгрузке');
 const version=meta.getRows(1,meta.rowCount).find(row=>row.getCell(1).value==='Версия исключений XML');
 expect(version).toBeDefined();
 version.getCell(2).value='99';
 await page.reload();
 await page.locator('.mapping-suggest').click();
 await page.locator('.mapping-edit-row').click();
 await page.locator('.mapping-edit-comment').fill('Сохранить открытый черновик');
 await page.locator('.mapping-editor-save').click();
 await page.locator('.mapping-editor-cancel').click();
 await uploadWorkbook(page,book);
 await expect(page.locator('.mapping-panel > [role=status]')).toContainText('Не удалось загрузить соответствия');
 await expect(mappingRow(page,statusPair)).toContainText('Сохранить открытый черновик');
 await page.locator('.mapping-discard').click();
 await page.evaluate(key=>localStorage.removeItem(key),key);
 await page.reload();
 await page.locator('.mapping-file').setInputFiles(file);
 const apply=page.locator('.mapping-apply');
 await expect(apply).toHaveText('Применить и обновить сравнение (0)');
 await expect(apply).toBeEnabled();
 expect(await readSaved(page)).toBeNull();
 await applyMappings(page);
 expect(await readSaved(page)).toMatchObject({pairs:[],dismissed:[rejectedStatus]});
});

test('card undo keeps a child out of automatic mappings until it is added manually',async({page})=>{
 await page.goto('/site/');
 await page.evaluate(async ({key,left,right,parent,child,comparison})=>{
  const [before,after]=await Promise.all([loadComparisonData(left),loadComparisonData(right)]);
  const pairs=[parent,child,...SedoMapping.suggestDescendants([parent,child],before,after).suggestions.map(({beforePath,afterPath,comment})=>({beforePath,afterPath,comment}))];
  localStorage.setItem(key,JSON.stringify({version:1,left,right,pairs}));
  location.hash=comparison;
 },{key,left:left.revision_id,right:right.revision_id,parent:rootPair,child:statusPair,comparison:url.split('#')[1]});
 const suggest=page.locator('.mapping-suggest');
 await suggest.click();
 const childCard=page.locator('.difference').filter({has:page.locator('.mapping-comment').getByText(statusPair.comment,{exact:true})});
 await childCard.locator('.mapping-undo').click();
 await expect(page.locator('.mapping-applied-feedback')).toContainText('Сопоставление отменено');
 const selectedChild=(path)=>page.locator(`.mapping-preview input[data-before-path="${path}"]:checked`);
 await expect(selectedChild(statusPair.beforePath)).toHaveCount(0);
 await expect(childCard).toHaveCount(0);
 const extra={beforePath:rootPair.beforePath+'/socialAssistNum',afterPath:rootPair.afterPath+'/processNum',comment:'Независимая правка'};
 async function addAndApply(pair){
  await page.locator('.mapping-add').click();
  await choosePaths(page,pair.beforePath,pair.afterPath);
  await page.locator('.mapping-edit-comment').fill(pair.comment);
  await page.locator('.mapping-editor-save').click();
  await page.locator('.mapping-apply-top').click();
  await expect(page.locator('.mapping-applied-feedback')).toContainText('Сравнение обновлено');
 }
 await addAndApply(extra);
 await page.reload();
 expect((await readSaved(page)).pairs).toContainEqual(extra);
 expect((await readSaved(page)).pairs.some(pair=>pair.beforePath===statusPair.beforePath)).toBe(false);
 await suggest.click();
 await expect(childCard).toHaveCount(0);
 await expect(selectedChild(statusPair.beforePath)).toHaveCount(0);
 await page.getByRole('button',{name:'Поменять местами',exact:true}).click();
 await expect(page.locator('.compare-controls select').first()).toHaveValue(right.revision_id);
 await suggest.click();
 await expect(selectedChild(statusPair.afterPath)).toHaveCount(0);
 const reversed={beforePath:statusPair.afterPath,afterPath:statusPair.beforePath,comment:'Возвращено вручную'};
 await addAndApply(reversed);
 await expect(page.locator('.mapping-comment').getByText(reversed.comment,{exact:true})).toBeVisible();
 expect((await readSaved(page)).pairs).toContainEqual(reversed);
});

test('card undo protects deselected proposals even when all saved pairs are still selected',async({page})=>{
 await loadSaved(page,rootPair);
 await page.evaluate(({key,child,comparison})=>{
  const record=JSON.parse(localStorage.getItem(key));
  record.pairs.push(child);
  localStorage.setItem(key,JSON.stringify(record));
  location.hash=comparison;
 },{key,child:statusPair,comparison:url.split('#')[1]});
 await page.reload();
 await page.locator('.mapping-suggest').click();
 const selected=page.locator('.mapping-proposal:checked');
 expect(await selected.count()).toBeGreaterThan(0);
 await page.locator('.mapping-select-none').click();
 const card=page.locator('.difference').filter({has:page.locator('.mapping-comment').getByText(statusPair.comment,{exact:true})});
 await card.locator('.mapping-undo').click();
 await expect(card.locator('.mapping-undo-error')).toContainText('Сначала примените или отмените черновик');
 await expect(selected).toHaveCount(0);
 expect((await readSaved(page)).pairs).toContainEqual(statusPair);
});

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
 await expect(apply).toBeFocused();
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
  if(reversed){
   // Repeat the decision parsing with a fresh baseline. The first apply
   // deliberately excluded the blank-decision pair in both orientations.
   await loadSaved(page);
   await page.getByRole('button',{name:'Поменять местами',exact:true}).click();
  }
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
 ['empty tree',{body:'window.SEDO_FIELDS['+JSON.stringify(left.revision_id)+'] = []'},'дерево тегов пустое или повреждено'],
 ['malformed root',{body:'window.SEDO_FIELDS['+JSON.stringify(left.revision_id)+'] = [{name:"broken",path:"",parent:-1,children:[]}]'},'дерево тегов пустое или повреждено'],
 ['duplicate path',{body:'window.SEDO_FIELDS['+JSON.stringify(left.revision_id)+'] = [0,1].map(()=>({name:"request",path:"request",parent:-1,children:[]}))'},'дерево тегов пустое или повреждено'],
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
  await expect(page.locator('.compare-counts')).toHaveCount(0);
  await page.evaluate(()=>{location.hash=''});
  await expect(page.locator('#catalog')).toBeVisible();
  await page.goBack();await expect(page.locator('.mapping-add')).toBeVisible({timeout:2000});
  await expect(page.locator('.compare-counts')).toBeVisible();
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

for(const mode of ['ready draft','unfinished pair']){
 test('valid Excel requires confirmation before replacing a '+mode,async({page})=>{
  await loadSaved(page);
  await page.locator('.mapping-add').click();
  const beforePath=rootPair.beforePath+'/socialAssistNum',afterPath=rootPair.afterPath+'/processNum';
  await choosePaths(page,beforePath,afterPath);
  await page.locator('.mapping-edit-comment').fill('Пара из браузера');
  await page.locator('.mapping-editor-save').click();
  await page.locator('.mapping-editor-cancel').click();
  const savedRow=page.locator('.mapping-explicit[data-before-path="'+statusPair.beforePath+'"]');
  await savedRow.uncheck();
  if(mode==='unfinished pair'){
   await page.locator('.mapping-add-row').click();
   await page.locator('.mapping-tag-choice-before').selectOption(rootPair.beforePath+'/errorList');
   await page.locator('.mapping-edit-comment').fill('Незавершённая пара');
  }
  const book=new ExcelJS.Workbook();
  book.addWorksheet('О выгрузке').addRow(['Идентификатор редакции',left.revision_id,right.revision_id]);
  const sheet=book.addWorksheet('Сопоставление');
  sheet.addRow(['Путь «Было»','Путь «Стало» — введите или выберите','Решение — вручную','Комментарий — вручную']);
  sheet.addRow([statusPair.beforePath,statusPair.afterPath,'Подтверждено','Замена из Excel']);
  const cancelled=page.waitForEvent('dialog').then(dialog=>dialog.dismiss());
  await Promise.all([uploadWorkbook(page,book),cancelled]);
  await expect(page.locator('.mapping-preview')).toContainText('Пара из браузера');
  await expect(savedRow).not.toBeChecked();
  await expect(page.locator('.mapping-comment')).toHaveText(statusPair.comment);
  if(mode==='unfinished pair'){
   await expect(page.locator('.mapping-tag-choice-before')).toHaveValue(rootPair.beforePath+'/errorList');
   await expect(page.locator('.mapping-edit-comment')).toHaveValue('Незавершённая пара');
  }
  expect((await readSaved(page)).pairs).toEqual([statusPair]);
  const accepted=page.waitForEvent('dialog').then(dialog=>dialog.accept());
  await Promise.all([uploadWorkbook(page,book),accepted]);
  await expect(page.locator('.mapping-preview')).toContainText('Замена из Excel');
  await expect(page.locator('.mapping-preview')).not.toContainText('Пара из браузера');
  await expect(page.locator('.mapping-edit-form')).toHaveCount(0);
  expect((await readSaved(page)).pairs).toEqual([statusPair]);
 });
}

test('keyboard selection keeps its row across redraw and Tab reaches its edit button',async({page})=>{
 await loadSaved(page,{...rootPair,comment:'Блок для предложений'});
 await page.locator('.mapping-suggest').click();
 const rows=page.locator('.mapping-table input[type=checkbox]');
 for(const choice of [rows.last(),rows.first()]){
  const before=await choice.getAttribute('data-before-path');
  const unchangedRow=page.locator('.mapping-table [data-before-path="'+before+'"]');
  await choice.focus();
  await choice.press('Space');
  await expect(unchangedRow).not.toBeChecked();
  await expect(unchangedRow).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(unchangedRow.locator('xpath=ancestor::tr').locator('.mapping-edit-row')).toBeFocused();
 }
});

for(const mode of ['unchecked edit','untouched preview']){
 test('Excel replacement distinguishes an '+mode,async({page})=>{
  await loadSaved(page,mode==='unchecked edit'?statusPair:rootPair);
  const manualPath=rootPair.beforePath+'/socialAssistNum';
  if(mode==='unchecked edit'){
   await page.locator('.mapping-add').click();
   await choosePaths(page,manualPath,rootPair.afterPath+'/processNum');
   await page.locator('.mapping-edit-comment').fill('Пара без отметки');
   await page.locator('.mapping-editor-save').click();
   await page.locator('.mapping-editor-cancel').click();
   await page.locator('.mapping-explicit[data-before-path="'+manualPath+'"]').uncheck();
  }else{
   await page.locator('.mapping-suggest').click();
   await expect(page.locator('.mapping-proposal:checked').first()).toBeVisible();
  }
  const book=new ExcelJS.Workbook();
  book.addWorksheet('О выгрузке').addRow(['Идентификатор редакции',left.revision_id,right.revision_id]);
  const sheet=book.addWorksheet('Сопоставление');
  sheet.addRow(['Путь «Было»','Путь «Стало» — введите или выберите','Решение — вручную','Комментарий — вручную']);
  sheet.addRow([statusPair.beforePath,statusPair.afterPath,'Подтверждено','Заменённый предпросмотр']);
  if(mode==='unchecked edit'){
   const refused=page.waitForEvent('dialog',{timeout:3000}).then(dialog=>dialog.dismiss());
   await Promise.all([uploadWorkbook(page,book),refused]);
   const pair=page.locator('.mapping-explicit[data-before-path="'+manualPath+'"]');
   await expect(pair).not.toBeChecked();
   await expect(pair.locator('xpath=ancestor::tr')).toContainText('Пара без отметки');
   expect((await readSaved(page)).pairs).toEqual([statusPair]);
  }else{
   await uploadWorkbook(page,book);
   await expect(page.locator('.mapping-preview')).toContainText('Заменённый предпросмотр',{timeout:3000});
   expect((await readSaved(page)).pairs).toEqual([rootPair]);
  }
 });
}

test('keyboard pagination keeps focus at both boundaries and while filtering',async({page})=>{
 const legacy=catalog.revisions.find(r=>r.number==='101'&&r.environment==='prod'&&r.document_version==='2.28'&&r.ready);
 const current=catalog.revisions.find(r=>r.number==='10110'&&r.environment==='prod'&&r.document_version==='3.0'&&r.ready);
 await page.goto('/site/#'+new URLSearchParams({message:'10110',compare:'1',left:legacy.revision_id,right:current.revision_id}));
 await page.locator('.mapping-add').click();
 await choosePaths(page,'approveSocialAssist/benefit1Approve','benefitResponse/benefit1');
 await page.locator('.mapping-editor-save').click();
 await page.locator('.mapping-editor-cancel').click();
 const table=page.locator('.mapping-table'),caption=table.locator('.mapping-visible-count');
 const previous=table.locator('.mapping-prev'),next=table.locator('.mapping-next'),search=page.locator('.mapping-preview-search');
 await expect(caption).toContainText('страница 1 из 2');
 for(const key of ['Enter','Space']){
  await next.press(key);
  await expect(caption).toContainText('страница 2 из 2');
  await expect(previous).toBeFocused({timeout:2000});
  await previous.press(key);
  await expect(caption).toContainText('страница 1 из 2');
  await expect(next).toBeFocused({timeout:2000});
 }
 await next.press('Space');
 await search.fill('elnInfo');
 await expect(caption).toContainText('страница 1 из 1');
 await expect(next).toBeHidden();
 await expect(search).toBeFocused();
});

test('keyboard removal focuses the nearest filtered row or the search when none remain',async({page})=>{
 await loadSaved(page,{...rootPair,comment:'Блок для предложений'});
 await page.locator('.mapping-suggest').click();
 const search=page.locator('.mapping-preview-search'),remove=page.locator('.mapping-table .mapping-remove-row');
 await search.fill('errorList');
 const total=await remove.count();
 expect(total).toBeGreaterThan(1);
 const first=await remove.first().getAttribute('data-row-id');
 await remove.last().press('Enter');
 await expect(remove.last()).toBeFocused();
 for(let count=total-1;count>1;count--){
  await remove.last().press('Enter');
  await expect(remove.last()).toBeFocused();
 }
 await expect(remove.last()).toHaveAttribute('data-row-id',first);
 await remove.last().press('Enter');
 await expect(search).toBeFocused();
});

test('cancelling a partial pair on a clean draft focuses the panel add button',async({page})=>{
 await page.goto(url);
 const add=page.locator('.mapping-add');
 await add.click();
 await page.locator('.mapping-tag-choice-before').selectOption(statusPair.beforePath);
 await page.locator('.mapping-cancel-edit-top').press('Enter');
 await expect(page.locator('.mapping-draft-notice')).toBeHidden();
 await expect(add).toBeFocused();
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
 const download=await downloadPromise;
 await page.locator('.mapping-file').setInputFiles(await download.path());
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
