const {test,expect}=require('@playwright/test');
const fs=require('node:fs'),path=require('node:path');
const ExcelJS=require('../../web/vendor/exceljs.min.js');
const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../../site/catalog-data.js'),'utf8').replace(/^window.SEDO_CATALOG=/,'').replace(/;\s*$/,''));
const edition=(number,version)=>catalog.revisions.find(r=>r.number===number&&r.environment==='prod'&&r.document_version===version&&r.ready);
const left=edition('105','2.28'),right=edition('10510','3.0'),key='sedo-field-mappings:v1:'+[left.revision_id,right.revision_id].sort().map(encodeURIComponent).join(':'),url='/site/#'+new URLSearchParams({message:'10510',compare:'1',left:left.revision_id,right:right.revision_id});
async function add(page,before,after,comment){
 await page.locator('.mapping-add').click();await page.locator('.mapping-tag-choice-before').selectOption(before);await page.locator('.mapping-tag-choice-after').selectOption(after);await page.locator('.mapping-edit-comment').fill(comment);await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();
}
async function editFirst(page,comment,afterPath){
 await page.locator('.mapping-suggest').click();await page.locator('.mapping-edit-row').first().click();
 if(afterPath)await page.locator('.mapping-tag-choice-after').selectOption(afterPath);
 await page.locator('.mapping-edit-comment').fill(comment);await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();
}
async function holdLock(context){
 const locker=await context.newPage();await locker.goto(url);
 await locker.evaluate(key=>new Promise(acquired=>{
  navigator.locks.request(key,()=>new Promise(release=>{window.releaseMappingLock=release;acquired()}));
 }),key);
 return locker;
}
async function waitForQueuedOperation(page){
 await expect.poll(()=>page.evaluate(async key=>(await navigator.locks.query()).pending.filter(lock=>lock.name===key).length,key)).toBe(1);
}
test.describe('one saved mapping snapshot',()=>{
 const parent={beforePath:'approveSocialAssistResult',afterPath:'benefitResponseResult',comment:'Родитель'};
 const child={beforePath:parent.beforePath+'/status',afterPath:parent.afterPath+'/status',comment:''};
 const independent={beforePath:parent.beforePath+'/socialAssistNum',afterPath:parent.afterPath+'/processNum',comment:'Независимая пара'};
 const reverseUrl='/site/#'+new URLSearchParams({message:'10510',compare:'1',left:right.revision_id,right:left.revision_id});
 const read=page=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key);
 const open=page=>page.locator('.mapping-suggest').click();
 const apply=async page=>{
  await page.locator('.mapping-apply').click();
  await expect(page.locator('.mapping-table')).toHaveCount(0);
 };
 const checkbox=(page,pair)=>page.locator('input[data-before-path="'+pair.beforePath+'"][data-after-path="'+pair.afterPath+'"]');
 const row=(page,pair)=>checkbox(page,pair).locator('xpath=ancestor::tr');
 async function downloadWorkbook(page){
  const pending=page.waitForEvent('download');
  await page.getByRole('button',{name:'Выгрузить Excel для сопоставления',exact:true}).click();
  const workbook=new ExcelJS.Workbook();
  await workbook.xlsx.load(fs.readFileSync(await (await pending).path()));
  return workbook;
 }
 async function confirmedSheetPairs(page){
  const workbook=await downloadWorkbook(page);
  const sheet=workbook.getWorksheet('Сопоставление');
  return sheet.getRows(6,sheet.rowCount-5).filter(item=>item.getCell(8).value==='Подтверждено');
 }
 async function blockStorage(page,{reads,writes}){
  await page.evaluate(({key,reads,writes})=>{
   const get=Storage.prototype.getItem,set=Storage.prototype.setItem;
   window.mappingStorageFailures={reads,writes};
   Storage.prototype.getItem=function(name){
    if(name===key&&window.mappingStorageFailures.reads)throw new DOMException('Denied','SecurityError');
    return get.call(this,name);
   };
   Storage.prototype.setItem=function(name,value){
    if(name===key&&window.mappingStorageFailures.writes)throw new DOMException('Denied','SecurityError');
    return set.call(this,name,value);
   };
  },{key,reads,writes});
 }
 test.beforeEach(async({context,page})=>{
  for(const script of ['compare.js','comparison-mapping.js','mapping-draft.js','mapping-editor.js'])await context.route('**/'+script+'*',route=>route.fulfill({path:path.join(__dirname,'../../web',script),contentType:'application/javascript'}));
  await page.goto('/site/');
 });
 async function seed(page,pairs){
  await page.evaluate(({key,left,right,pairs})=>localStorage.setItem(key,JSON.stringify({version:1,left,right,pairs})),{key,left:left.revision_id,right:right.revision_id,pairs});
  await page.goto(url);
 }
 async function expectPairs(page,direction,pairs){
  await expect(page.locator('.mapping-panel')).toContainText('Применено пар: '+pairs.length);
  await expect(page.locator('.mapping-undo')).toHaveCount(pairs.length);
  const rows=await confirmedSheetPairs(page);
  expect(rows.map(row=>({beforePath:row.getCell(2).value,afterPath:row.getCell(5).value,comment:row.getCell(9).value})))
   .toEqual(expect.arrayContaining(pairs.map(pair=>orientPair(pair,direction))));
  expect(rows).toHaveLength(pairs.length);
  for(const pair of pairs)await expect(page.locator('.difference').filter({has:page.locator('code').getByText(
   orientPair(pair,direction).beforePath+' → '+orientPair(pair,direction).afterPath,{exact:true})}).locator('.mapping-comment')).toHaveText(pair.comment);
 }
 const orientPair=(pair,reversed)=>reversed?{beforePath:pair.afterPath,afterPath:pair.beforePath,comment:pair.comment}:pair;
 async function importWorkbook(page,pairs,{hold=false,deleted,dismissed}={}){
  const workbook=new ExcelJS.Workbook();
  const metadata=workbook.addWorksheet('О выгрузке');
  metadata.addRow(['Идентификатор редакции',left.revision_id,right.revision_id]);
  if(dismissed){
   metadata.addRow(['Исключённые пары XML',left.revision_id,right.revision_id]);
   metadata.addRow(['Версия исключений XML','1',1]);
   metadata.addRow(['Исключённая пара XML',dismissed.beforePath,dismissed.afterPath]);
  }
  const sheet=workbook.addWorksheet('Сопоставление');
  sheet.addRow(['Путь «Было»','Путь «Стало» — введите или выберите','Решение — вручную','Комментарий — вручную']);
  for(const pair of pairs)sheet.addRow([pair.beforePath,pair.afterPath,'Подтверждено',pair.comment]);
  if(deleted)sheet.addRow([deleted,'','Удалено']);
  const name=hold?'held.xlsx':'review.xlsx';
  if(hold)await page.evaluate(name=>{
   const read=File.prototype.arrayBuffer;
   File.prototype.arrayBuffer=function(){
    if(this.name!==name)return read.call(this);
    return new Promise(resolve=>{window.releaseMappingImport=()=>resolve(read.call(this))});
   };
  },name);
  await page.locator('.mapping-file').setInputFiles({name,mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from(await workbook.xlsx.writeBuffer())});
  if(hold)await page.waitForFunction(()=>typeof window.releaseMappingImport==='function');
  else await expect(page.locator('.mapping-apply')).toBeEnabled();
 }
 function expectWorkbookPair(workbook,pair){
  const {beforePath,afterPath,comment}=pair;
  const mapping=workbook.getWorksheet('Сопоставление');
  const confirmed=mapping.getRows(6,mapping.rowCount-5).filter(item=>item.getCell(8).value==='Подтверждено');
  expect(confirmed.map(item=>[item.getCell(2).value,item.getCell(5).value,item.getCell(9).value])).toEqual([[beforePath,afterPath,comment]]);
  const changes=workbook.getWorksheet('Изменения');
  expect(changes.getRows(2,changes.rowCount-1).map(item=>item.values.slice(8,12))).toContainEqual([beforePath,afterPath,'Да',comment]);
 }
 for(const reversed of [false,true]){
  test('Excel reads the applied snapshot during import in '+(reversed?'reverse':'forward')+' comparison',async({page,context})=>{
   const remote={...child,comment:'Сохранено во второй вкладке'};
   const deleted=parent.beforePath+'/errorList/error/code';
   const dismissed={beforePath:deleted,afterPath:parent.afterPath+'/errorList/error/code'};
   await page.goto(reversed?reverseUrl:url);
   await importWorkbook(page,[independent],{hold:true});
   const other=await context.newPage();
   await other.goto(url);
   await importWorkbook(other,[remote],{deleted,dismissed});
   await apply(other);
   const persisted=await read(other);
   await expect(page.locator('.mapping-undo')).toHaveCount(0);
   await expect(page.locator('.mapping-panel')).toContainText('Читаем Excel');
   await page.evaluate(key=>{
    window.mappingExportAccess={reads:0,writes:0};
    for(const [method,counter] of [['getItem','reads'],['setItem','writes'],['removeItem','writes']]){
     const original=Storage.prototype[method];
     Storage.prototype[method]=function(...args){
      if(args[0]===key)window.mappingExportAccess[counter]++;
      return original.apply(this,args);
     };
    }
   },key);
   const workbook=await downloadWorkbook(page);
   expect(await page.evaluate(()=>window.mappingExportAccess)).toEqual({reads:1,writes:0});
   expectWorkbookPair(workbook,orientPair(remote,reversed));
   const exclusion=orientPair(dismissed,reversed);
   const meta=workbook.getWorksheet('О выгрузке');
   const metaRows=meta.getRows(2,meta.rowCount-1).map(item=>item.values.slice(1));
   expect(metaRows).toContainEqual(['Исключённая пара XML',exclusion.beforePath,exclusion.afterPath]);
   if(reversed)expect(metaRows).toContainEqual(['Исключённый путь «Стало»','Не сопоставлять автоматически',deleted]);
   else{
    const mapping=workbook.getWorksheet('Сопоставление');
    const deletedRow=mapping.getRows(6,mapping.rowCount-5).find(item=>item.getCell(2).value===deleted);
    expect(deletedRow?.getCell(8).value).toBe('Удалено');
   }
   expect(await read(other)).toEqual(persisted);
   await page.evaluate(()=>window.releaseMappingImport());
   await expect(row(page,orientPair(independent,reversed))).toContainText(independent.comment);
   await apply(page);
   await expectPairs(page,reversed,[independent,remote]);
   const final=await read(page);
   expect(final.deleted[reversed?'after':'before']).toEqual([deleted]);
   expect(final.dismissed).toEqual([{beforePath:exclusion.beforePath,afterPath:exclusion.afterPath}]);
  });
 }
 test('Excel export keeps the import baseline and retains a conflicting draft',async({page,context})=>{
  const imported={...child,comment:'Комментарий в Excel'};
  const remote={...child,comment:'Комментарий во второй вкладке'};
  await seed(page,[child]);
  await importWorkbook(page,[imported],{hold:true});
  const other=await context.newPage();
  await other.goto(url);
  await editFirst(other,remote.comment);
  await apply(other);
  expectWorkbookPair(await downloadWorkbook(page),remote);
  await page.evaluate(()=>window.releaseMappingImport());
  await expect(row(page,imported)).toContainText(imported.comment);
  await page.locator('.mapping-apply').click();
  await expect(page.locator('.mapping-draft-errors')).toContainText('Конфликт');
  await expect(page.locator('.mapping-draft-errors')).toContainText(remote.comment);
  await expect(row(page,imported)).toContainText(imported.comment);
  expect((await read(other)).pairs).toEqual([remote]);
 });
 test('declining a delayed Excel replacement keeps the draft and exports the applied session',async({page})=>{
  const local={...child,comment:'Только в сеансе'};
  const draft={...independent,comment:'Текущий черновик'};
  await page.goto(url);
  await blockStorage(page,{reads:false,writes:true});
  await add(page,local.beforePath,local.afterPath,local.comment);
  await apply(page);
  await expect(page.locator('.mapping-panel')).toContainText('только в текущем сеансе');
  await add(page,draft.beforePath,draft.afterPath,draft.comment);
  await importWorkbook(page,[{...draft,comment:'Новый Excel'}],{hold:true});
  expectWorkbookPair(await downloadWorkbook(page),local);
  expect(await read(page)).toBeNull();
  const declined=page.waitForEvent('dialog').then(async dialog=>{
   expect(dialog.message()).toContain('Заменить текущий черновик');
   await dialog.dismiss();
  });
  await Promise.all([declined,page.evaluate(()=>window.releaseMappingImport())]);
  await expect(row(page,draft)).toContainText(draft.comment);
  await page.evaluate(()=>{window.mappingStorageFailures.writes=false});
  await apply(page);
  await expectPairs(page,false,[local,draft]);
 });
 for(const reversed of [false,true]){
  const direction=reversed?'reverse':'forward';
  test('dismissing a stale proposal preserves a confirmed pair in the '+direction+' comparison',async({page,context})=>{
   const savedChild={...child,comment:'Подтверждено в другой вкладке'};
   await seed(page,[parent]);
   if(reversed)await page.goto(reverseUrl);
   await open(page);
   await page.locator('.mapping-select-none').click();
   const proposal=orientPair(child,reversed);
   await page.locator('.mapping-preview-search').fill(proposal.beforePath);
   await expect(checkbox(page,proposal)).toHaveClass('mapping-proposal');

   const other=await context.newPage();
   await other.goto(reversed?url:reverseUrl);
   await open(other);
   await other.locator('.mapping-select-none').click();
   const confirmation=orientPair(savedChild,!reversed);
   await other.locator('.mapping-preview-search').fill(confirmation.beforePath);
   await row(other,confirmation).locator('.mapping-edit-row').click();
   await other.locator('.mapping-edit-comment').fill(confirmation.comment);
   await other.locator('.mapping-editor-save').click();
   await other.locator('.mapping-editor-cancel').click();
   await apply(other);

   await row(page,proposal).locator('.mapping-remove-row').click();
   await apply(page);
   expect((await read(page)).dismissed??[]).toEqual([]);
   await page.reload();
   await expectPairs(page,reversed,[parent,savedChild]);
   await other.reload();
   await expectPairs(other,!reversed,[parent,savedChild]);
  });
  test('applying over new storage damage preserves the ready draft in the '+direction+' comparison',async({page,context})=>{
   await seed(page,[child]);
   if(reversed)await page.goto(reverseUrl);
   const draftPair=orientPair(independent,reversed);
   await add(page,draftPair.beforePath,draftPair.afterPath,draftPair.comment);
   await page.locator('.mapping-select-none').click();
   await page.evaluate(key=>{
    window.mappingWrites=0;
    for(const name of ['setItem','removeItem']){
     const original=Storage.prototype[name];
     Storage.prototype[name]=function(...args){
      if(args[0]===key)window.mappingWrites++;
      return original.apply(this,args);
     };
    }
   },key);
   const other=await context.newPage();
   await other.goto(reversed?url:reverseUrl);
   await other.evaluate(key=>localStorage.setItem(key,'{'),key);
   await expect(page.locator('.mapping-panel > p[role=status]')).toContainText('повреждены');
   await page.locator('.mapping-apply').click();
   await expect(page.locator('.mapping-draft-errors')).toContainText('изменились и повреждены');
   expect(await page.evaluate(()=>window.mappingWrites)).toBe(0);
   expect(await other.evaluate(key=>localStorage.getItem(key),key)).toBe('{');
   await expect(row(page,draftPair)).toContainText(draftPair.comment);
   await other.getByRole('button',{name:'Сбросить сопоставления',exact:true}).click();
   await expect.poll(()=>other.evaluate(key=>localStorage.getItem(key),key)).toBeNull();
   await apply(page);
   expect(await page.evaluate(()=>window.mappingWrites)).toBe(1);
   await page.reload();
   await expectPairs(page,reversed,[independent]);
  });
  test('retry rebases a damaged durable baseline in the '+direction+' comparison',async({page,context})=>{
   await page.evaluate(key=>localStorage.setItem(key,'{'),key);
   await page.goto(url);
   await expect(page.locator('.mapping-panel')).toContainText('повреждены');
   await blockStorage(page,{reads:false,writes:true});
   const local={...child,comment:'Сеансовая пара'};
   await add(page,local.beforePath,local.afterPath,local.comment);
   await apply(page);
   await expect(page.locator('.mapping-panel')).toContainText('только в текущем сеансе');
   const other=await context.newPage();await other.goto(reversed?reverseUrl:url);
   await other.getByRole('button',{name:'Сбросить сопоставления',exact:true}).click();
   await expect.poll(()=>other.evaluate(key=>localStorage.getItem(key),key)).toBeNull();
   const remote=orientPair(independent,reversed);
   await add(other,remote.beforePath,remote.afterPath,remote.comment);
   await apply(other);
   const retry=page.locator('.mapping-retry');
   await retry.click();
   await expect(page.locator('.mapping-panel')).toContainText('Применено пар: 2');
   await expect(retry).toBeEnabled();
   expect((await read(other)).pairs).toEqual([remote]);
   const changed={...independent,comment:'Уточнённый комментарий второй вкладки'};
   await editFirst(other,changed.comment);
   await apply(other);
   await page.evaluate(()=>{window.mappingStorageFailures.writes=false});
   await retry.click();
   await expect(retry).toBeHidden();
   await page.reload();await other.reload();
   expect((await read(other)).pairs).toEqual([changed,local]);
   await expectPairs(page,false,[changed,local]);
   await expectPairs(other,reversed,[changed,local]);
  });
 }
 test('failed last-pair undo can save an empty comparison across reloads',async({page,context})=>{
  const local={...child,comment:'Будет отменено'};
  await seed(page,[local]);
  await blockStorage(page,{reads:false,writes:true});
  await page.locator('.mapping-undo').click();
  await expect(page.locator('.mapping-panel')).toContainText('только в текущем сеансе');
  await expectPairs(page,false,[]);
  await page.locator('.mapping-add').click();
  await page.locator('.mapping-editor-cancel').click();
  await expect(page.locator('.mapping-apply')).toBeEnabled();
  await apply(page);
  await expect(page.locator('.mapping-retry')).toBeEnabled();
  expect((await read(page)).pairs).toEqual([local]);
  await page.evaluate(()=>{window.mappingStorageFailures.writes=false});
  await page.locator('.mapping-retry').click();
  await expect(page.locator('.mapping-retry')).toBeHidden();
  await page.reload();
  const other=await context.newPage();await other.goto(reverseUrl);
  expect(await read(other)).toMatchObject({pairs:[],dismissed:[{beforePath:child.beforePath,afterPath:child.afterPath}]});
  await expectPairs(page,false,[]);
  await expectPairs(other,true,[]);
 });
 test('retry reports new damage and endpoint conflicts while retaining the pending edit',async({page,context})=>{
  const local={...child,afterPath:parent.afterPath+'/errorList/error/code',comment:'Сеансовый комментарий'};
  const remote={...child,afterPath:independent.afterPath,comment:'Комментарий второй вкладки'};
  await seed(page,[child]);
  await blockStorage(page,{reads:false,writes:true});
  await editFirst(page,local.comment,local.afterPath);
  await apply(page);
  await expect(page.locator('.mapping-panel')).toContainText('только в текущем сеансе');
  const other=await context.newPage();await other.goto(reverseUrl);
  await other.evaluate(key=>localStorage.setItem(key,'{"version":2}'),key);
  await page.evaluate(()=>{window.mappingStorageFailures.writes=false});
  const retry=page.locator('.mapping-retry');
  await retry.click();
  await expect(page.locator('.mapping-panel')).toContainText('изменились и повреждены');
  await expect(retry).toBeEnabled();
  await add(other,remote.afterPath,remote.beforePath,remote.comment);
  await apply(other);
  await retry.click();
  await expect(page.locator('.mapping-panel')).toContainText('Конфликт');
  await expect(retry).toBeEnabled();
  expect((await read(other)).pairs).toEqual([orientPair(remote,true)]);
  await expectPairs(page,false,[local]);
  await expectPairs(other,true,[remote]);
 });
 test('an empty pending undo rebases across a damaged record and successive failed saves',async({page,context})=>{
  await page.evaluate(key=>localStorage.setItem(key,'{'),key);
  await page.goto(url);
  await blockStorage(page,{reads:false,writes:true});
  const local={...child,comment:'Отменённая сеансовая пара'};
  await add(page,local.beforePath,local.afterPath,local.comment);
  await apply(page);
  const other=await context.newPage();await other.goto(reverseUrl);
  await other.getByRole('button',{name:'Сбросить сопоставления',exact:true}).click();
  await expect.poll(()=>other.evaluate(key=>localStorage.getItem(key),key)).toBeNull();
  await page.locator('.mapping-undo').click();
  await expect(page.locator('.mapping-panel')).toContainText('Применено пар: 0');
  const retry=page.locator('.mapping-retry');
  await expect(retry).toBeEnabled();
  await add(other,independent.afterPath,independent.beforePath,independent.comment);
  await apply(other);
  await retry.click();
  await expect(page.locator('.mapping-panel')).toContainText('Применено пар: 1');
  await expect(retry).toBeEnabled();
  const updated={...independent,comment:'Уточнение независимой пары'};
  await editFirst(other,updated.comment);
  await apply(other);
  await page.evaluate(()=>{window.mappingStorageFailures.writes=false});
  await retry.click();
  await expect(retry).toBeHidden();
  await page.reload();await other.reload();
  expect(await read(other)).toMatchObject({pairs:[updated],dismissed:[{beforePath:child.beforePath,afterPath:child.afterPath}]});
  await expectPairs(page,false,[updated]);
  await expectPairs(other,true,[updated]);
 });
 test('local mapping recovery drops automatic children when the first durable change removes their parent',async({page})=>{
  await seed(page,[parent]);
  await page.evaluate(key=>{
   const write=Storage.prototype.setItem;
   Storage.prototype.setItem=function(name,value){
    if(name===key){
     Storage.prototype.setItem=write;
     throw new DOMException('Quota','QuotaExceededError');
    }
    return write.call(this,name,value);
   };
  },key);
  await open(page);
  await expect(checkbox(page,child)).toBeChecked();
  await apply(page);
  expect((await read(page)).pairs).toEqual([parent]);
  await expect(page.locator('.mapping-panel')).toContainText('действуют только в текущем сеансе');
  const parentPath=page.locator('code').getByText(parent.beforePath+' → '+parent.afterPath,{exact:true});
  await page.locator('.difference').filter({has:parentPath}).locator('.mapping-undo').click();
  await expect(page.locator('.mapping-applied-feedback')).toContainText('Сопоставление отменено');
  expect((await read(page)).pairs).toEqual([]);
  await page.reload();
  await expect(page.locator('.mapping-panel')).toContainText('Применено пар: 0');
  await expect(page.locator('.mapping-undo')).toHaveCount(0);
  expect(await confirmedSheetPairs(page)).toHaveLength(0);
 });
 for(const first of ['reads','writes']){
  test('session undo and Excel prune children before recovering '+first,async({page,context})=>{
   await seed(page,[parent]);
   await open(page);
   await blockStorage(page,{reads:true,writes:true});
   await apply(page);
   await expect(page.locator('.mapping-panel')).toContainText('действуют только в текущем сеансе');
   const parentPath=page.locator('code').getByText(parent.beforePath+' → '+parent.afterPath,{exact:true});
   await page.locator('.difference').filter({has:parentPath}).locator('.mapping-undo').click();
   await expect(page.locator('.mapping-panel')).toContainText('Применено пар: 0');
   await expect(page.locator('.mapping-undo')).toHaveCount(0);
   expect(await confirmedSheetPairs(page)).toHaveLength(0);
   const concurrent={...child,comment:'Подтверждено во второй вкладке'};
   const remotePairs=[parent],finalPairs=[independent];
   if(first==='reads'){
    const other=await context.newPage();
    await other.goto(url);
    await open(other);
    await other.getByRole('button',{name:'Снять выбор предложений',exact:true}).click();
    await row(other,child).locator('.mapping-edit-row').click();
    await other.locator('.mapping-edit-comment').fill(concurrent.comment);
    await other.locator('.mapping-editor-save').click();
    await other.locator('.mapping-editor-cancel').click();
    await apply(other);
    remotePairs.push(concurrent);
    finalPairs.unshift(concurrent);
   }
   await page.evaluate(first=>{window.mappingStorageFailures[first]=false},first);
   await add(page,independent.beforePath,independent.afterPath,independent.comment);
   await apply(page);
   await expect(page.locator('.mapping-panel')).toContainText('действуют только в текущем сеансе');
   await page.evaluate(()=>{window.mappingStorageFailures={reads:false,writes:false}});
   expect((await read(page)).pairs).toEqual(remotePairs);
   await open(page);
   await apply(page);
   await page.reload();
   await expect(page.locator('.mapping-panel')).toContainText('Применено пар: '+finalPairs.length);
   expect((await read(page)).pairs).toEqual(finalPairs);
   const rows=await confirmedSheetPairs(page);
   expect(rows).toHaveLength(finalPairs.length);
   for(const pair of finalPairs)expect(rows.find(item=>item.getCell(2).value===pair.beforePath)?.values)
    .toEqual(expect.arrayContaining(Object.values(pair)));
  });
 }
 for(const method of ['checkbox','reverse Excel']){
  test('pending child survives removing its parent through '+method,async({page})=>{
   await seed(page,[parent]);
   await open(page);
   await blockStorage(page,{reads:false,writes:true});
   await apply(page);
   await expect(page.locator('.mapping-panel')).toContainText('действуют только в текущем сеансе');
   expect((await read(page)).pairs).toEqual([parent]);
   let accepted=child;
   if(method==='checkbox'){
    await open(page);
    await expect(checkbox(page,child)).toHaveClass('mapping-proposal');
    await checkbox(page,child).uncheck();
    await checkbox(page,child).check();
    await expect(page.locator('.mapping-draft-notice')).toBeVisible();
    await apply(page);
    await expect(page.locator('.mapping-panel')).toContainText('действуют только в текущем сеансе');
    await open(page);
    await expect(checkbox(page,child)).toHaveClass('mapping-explicit');
    await row(page,parent).locator('.mapping-remove-row').click();
   }else{
    const workbook=await downloadWorkbook(page),sheet=workbook.getWorksheet('Сопоставление');
    for(const item of sheet.getRows(6,sheet.rowCount-5)){
     if(item.getCell(2).value===child.beforePath)continue;
     item.getCell(5).value=null;item.getCell(8).value='Не проверено';
    }
    await page.goto(reverseUrl);
    await page.locator('.mapping-file').setInputFiles({
     name:'Подтверждённая пара.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
     buffer:Buffer.from(await workbook.xlsx.writeBuffer())
    });
    accepted={beforePath:child.afterPath,afterPath:child.beforePath};
   }
   await expect(checkbox(page,accepted)).toBeChecked();
   await expect(page.locator('.mapping-table input:checked')).toHaveCount(1);
   await page.evaluate(()=>{window.mappingStorageFailures.writes=false});
   await apply(page);
   const expected={...accepted,comment:expect.stringContaining('по одинаковому относительному пути')};
   expect((await read(page)).pairs).toEqual([expected]);
   await page.reload();
   await expect(page.locator('.mapping-panel')).toContainText('Применено пар: 1');
   await expect(page.locator('.difference .mapping-comment')).toContainText('по одинаковому относительному пути');
   const rows=await confirmedSheetPairs(page);
   expect(rows).toHaveLength(1);
   expect(rows[0].values).toEqual(expect.arrayContaining(Object.values(expected)));
   await page.goto(method==='checkbox'?reverseUrl:url);
   const reversed=page.locator('code').getByText(accepted.afterPath+' → '+accepted.beforePath,{exact:true});
   await expect(page.locator('.difference').filter({has:reversed}).locator('.mapping-comment')).toHaveText((await read(page)).pairs[0].comment);
   const reversedRows=await confirmedSheetPairs(page);
   expect(reversedRows).toHaveLength(1);
   expect(reversedRows[0].getCell(2).value).toBe(accepted.afterPath);
   expect(reversedRows[0].getCell(5).value).toBe(accepted.beforePath);
  });
 }
 for(const mode of ['both endpoints','one endpoint','comment']){
  test('local mapping recovery keeps the intended pair when editing '+mode,async({page})=>{
   const replacement={...independent,
    beforePath:mode==='comment'?child.beforePath:independent.beforePath,
    afterPath:mode==='both endpoints'?independent.afterPath:child.afterPath};
   const changesPaths=mode!=='comment';
   const rejected={beforePath:child.beforePath,afterPath:child.afterPath};
   await seed(page,[parent,child]);
   await open(page);
   await row(page,child).locator('.mapping-edit-row').click();
   await page.locator('.mapping-tag-choice-before').selectOption(replacement.beforePath);
   await page.locator('.mapping-tag-choice-after').selectOption(replacement.afterPath);
   await page.locator('.mapping-edit-comment').fill(replacement.comment);
   await page.locator('.mapping-editor-save').click();
   await page.locator('.mapping-editor-cancel').click();
   if(changesPaths)await expect(checkbox(page,child)).toHaveCount(0);
   await apply(page);
   const record=await read(page);
   expect(record.pairs).toContainEqual(replacement);
   if(changesPaths)expect(record.dismissed).toContainEqual(rejected);
   else expect(record.dismissed??[]).not.toContainEqual(rejected);
   if(mode==='both endpoints'){
    await page.reload();
    expect((await read(page)).pairs).not.toContainEqual(child);
    await page.goto(reverseUrl);
    await open(page);
    const oriented={beforePath:child.afterPath,afterPath:child.beforePath,comment:'Возвращено вручную'};
    await expect(checkbox(page,oriented)).toHaveCount(0);
    await add(page,oriented.beforePath,oriented.afterPath,oriented.comment);
    await apply(page);
    expect((await read(page)).pairs).toContainEqual(oriented);
    expect((await read(page)).dismissed??[]).not.toContainEqual({beforePath:oriented.beforePath,afterPath:oriented.afterPath});
   }
  });
 }
 test('local mapping recovery preserves an individually accepted child when removing the parent in the draft',async({page})=>{
  await seed(page,[parent]);
  await open(page);
  const accepted=checkbox(page,child);
  await accepted.uncheck();
  await accepted.check();
  await row(page,parent).locator('.mapping-remove-row').click();
  await expect(accepted).toBeChecked();
  await expect(page.locator('.mapping-table input:checked')).toHaveCount(1);
  await apply(page);
  const pairs=(await read(page)).pairs;
  expect(pairs).toEqual([{...child,comment:expect.stringContaining('по одинаковому относительному пути')}]);
  await expect(page.locator('.mapping-panel')).toContainText('Применено пар: 1');
  await expect(page.locator('.difference .mapping-comment')).toHaveText(pairs[0].comment);
  const rows=await confirmedSheetPairs(page);
  expect(rows).toHaveLength(1);
  expect(rows[0].values).toEqual(expect.arrayContaining([child.beforePath,child.afterPath,pairs[0].comment]));
 });
 for(const remove of [true,false]){
  test('preview '+(remove?'removal':'unchecking')+' keeps a child dismissed across comparisons',async({page})=>{
   await seed(page,[parent,child]);
   await open(page);
   const checkbox=page.locator('.mapping-explicit[data-before-path="'+child.beforePath+'"]');
   if(remove)await checkbox.locator('xpath=ancestor::tr').locator('.mapping-remove-row').click();
   else await checkbox.uncheck();
   await apply(page);
   const rejected={beforePath:child.beforePath,afterPath:child.afterPath};
   expect((await read(page)).dismissed).toContainEqual(rejected);
   await page.goto(reverseUrl);
   await open(page);
   await expect(page.locator('.mapping-proposal[data-before-path="'+child.afterPath+'"]')).toHaveCount(0);
   await page.goto(url);
   await add(page,independent.beforePath,independent.afterPath,independent.comment);
   await apply(page);
   expect((await read(page)).pairs).not.toContainEqual(child);
   await add(page,child.beforePath,child.afterPath,child.comment);
   await apply(page);
   expect((await read(page)).pairs).toContainEqual(child);
   expect((await read(page)).dismissed??[]).not.toContainEqual(rejected);
  });
 }
 for(const operation of ['undo','reset','recheck']){
  test('an old preview validates child proposals after parent '+operation,async({page,context})=>{
   await seed(page,[parent]);
   const stale=await context.newPage();
   const recheck=operation==='recheck',orient=pair=>recheck?{...pair,beforePath:pair.afterPath,afterPath:pair.beforePath}:pair;
   await stale.goto(recheck?reverseUrl:url);
   await open(stale);
   const selected=orient(child),extra=orient(independent);
   const choice=checkbox(stale,selected);
   await expect(choice).toBeChecked();
   if(operation==='reset')await page.getByRole('button',{name:'Сбросить сопоставления',exact:true}).click();
   else await page.locator('.mapping-undo').click();
   await expect(page.locator('.mapping-applied-feedback')).toContainText('пар: 0');
   if(recheck){await choice.uncheck();await choice.check();await expect(choice).toHaveClass('mapping-explicit')}
   await add(stale,extra.beforePath,extra.afterPath,extra.comment);
   await apply(stale);
   const pairs=(await read(stale)).pairs;
   expect(pairs).toHaveLength(recheck?2:1);
   expect(pairs).toContainEqual(extra);
   if(recheck)expect(pairs).toContainEqual(expect.objectContaining({beforePath:selected.beforePath,afterPath:selected.afterPath}));
  });
 }
 test('an external save updates the committed comparison without closing its unfinished form',async({page,context})=>{
  await page.goto(url);
  const stale=await context.newPage();
  await stale.goto(url);
  await stale.locator('.mapping-add').click();
  await stale.locator('.mapping-edit-comment').fill('Незавершённая правка');
  await add(page,child.beforePath,child.afterPath,child.comment);
  await apply(page);
  await expect(stale.locator('.mapping-undo')).toHaveCount(1);
  await expect(stale.locator('.mapping-edit-comment')).toHaveValue('Незавершённая правка');
  await stale.locator('.mapping-editor-cancel').click();
  await stale.locator('.mapping-discard').click();
  await expect(stale.locator('.mapping-panel')).toContainText('Применено пар: 1');
  await expect(stale.getByRole('button',{name:'Сбросить сопоставления',exact:true})).toBeEnabled();
  const pending=stale.waitForEvent('download');
  await stale.getByRole('button',{name:'Выгрузить Excel для сопоставления',exact:true}).click();
  const download=await pending,workbook=new ExcelJS.Workbook();
  await workbook.xlsx.load(fs.readFileSync(await download.path()));
  const sheet=workbook.getWorksheet('Сопоставление');
  expect(sheet.getRows(2,sheet.rowCount-1).some(row=>row.values.includes(child.beforePath)&&row.values.includes(child.afterPath))).toBe(true);
 });
});
test.describe('comparison panel while its Web Lock is pending',()=>{
 const oldPath='approveSocialAssistResult/status',newPath='benefitResponseResult/status';
 const savedComment='Сохранённая пара',draftComment='Ожидающий черновик',excelComment='Предпросмотр Excel';
 const upload=page=>page.getByRole('button',{name:'Загрузить сопоставления из Excel',exact:true});
 const reset=page=>page.getByRole('button',{name:'Сбросить сопоставления',exact:true});
 const record=page=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key);
 async function swap(page,before){
  await page.getByRole('button',{name:'Поменять местами',exact:true}).click();
  await expect(page.locator('.compare-controls select').first()).toHaveValue(before.revision_id);
  await expect(page.locator('.mapping-add')).toBeVisible();
 }
 async function importedFile(){
  const workbook=new ExcelJS.Workbook();
  workbook.addWorksheet('О выгрузке').addRow(['Идентификатор редакции',left.revision_id,right.revision_id]);
  const sheet=workbook.addWorksheet('Сопоставление');
  sheet.addRow(['Путь «Было»','Путь «Стало» — введите или выберите','Решение — вручную','Комментарий — вручную']);
  sheet.addRow([oldPath,newPath,'Подтверждено',excelComment]);
  return {name:'pending.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from(await workbook.xlsx.writeBuffer())};
 }
 test.beforeEach(async({context,page})=>{
  for(const script of ['compare.js','compare-core.js','comparison-export.js','comparison-mapping.js','mapping-draft.js','mapping-editor.js'])await context.route('**/'+script+'*',route=>route.fulfill({path:path.join(__dirname,'../../web',script),contentType:'application/javascript'}));
  await page.goto(url);
  await add(page,oldPath,newPath,savedComment);
  await page.locator('.mapping-apply').click();
  await expect(page.locator('.mapping-applied-feedback')).toBeVisible();
  await editFirst(page,draftComment);
 });
 for(const action of ['apply','reset']){
  for(const navigate of [false,true]){
   test(action+' serializes commands while waiting'+(navigate?' and leaves a newer preview alone after navigation':''),async({page,context})=>{
    const file=await importedFile(),locker=await holdLock(context);
    const command=action==='apply'?page.locator('.mapping-apply-top'):reset(page);
    if(action==='reset'){
     const accepted=page.waitForEvent('dialog').then(dialog=>dialog.accept());
     await Promise.all([command.click(),accepted]);
    }else await command.click();
    await waitForQueuedOperation(page);
    await expect(upload(page)).toBeDisabled();
    await expect(reset(page)).toBeDisabled();
    // A file chooser can have been opened before the operation queued.
    await page.locator('.mapping-file').setInputFiles(file);
    await expect(page.locator('.mapping-preview')).toContainText(draftComment);
    expect((await record(page)).pairs[0].comment).toBe(savedComment);
    if(navigate){
     await page.evaluate(()=>{location.hash=''});
     await expect(page.locator('#catalog')).toBeVisible();
     await page.goBack();
     await expect(page.locator('.mapping-preview')).toContainText(draftComment);
     const accepted=page.waitForEvent('dialog').then(dialog=>dialog.accept());
     await Promise.all([page.locator('.mapping-file').setInputFiles(file),accepted]);
     await expect(page.locator('.mapping-preview')).toContainText(excelComment);
    }
    await locker.evaluate(()=>window.releaseMappingLock());
    await expect.poll(()=>page.evaluate(async key=>(await navigator.locks.query()).pending.filter(lock=>lock.name===key).length,key)).toBe(0);
    if(navigate){
     expect((await record(page)).pairs[0].comment).toBe(savedComment);
     await expect(page.locator('.mapping-preview')).toContainText(excelComment);
     await page.evaluate(()=>{location.hash=''});
     await expect(page.locator('#catalog')).toBeVisible();
     await page.goBack();
     await expect(page.locator('.mapping-preview')).toContainText(excelComment);
    }else{
     await expect(page.locator('.mapping-applied-feedback')).toBeVisible();
     if(action==='apply')expect((await record(page)).pairs[0].comment).toBe(draftComment);
     else expect(await record(page)).toBeNull();
    }
   });
  }
 }
 for(const accept of [false,true]){
  test('reset names both directions and '+(accept?'clears drafts with permission':'keeps both drafts if declined'),async({page})=>{
   const reverseComment='Несохранённая обратная правка';
   const reverseSource='benefitResponseResult/processNum',reverseTarget='approveSocialAssistResult/socialAssistNum';
   await swap(page,right);
   await page.locator('.mapping-add').click();
   await page.locator('.mapping-tag-choice-before').selectOption(reverseSource);
   await page.locator('.mapping-edit-comment').fill(reverseComment);
   await swap(page,left);
   const decision=page.waitForEvent('dialog',{timeout:3000}).then(async dialog=>{
    const message=dialog.message();
    await (accept?dialog.accept():dialog.dismiss());
    expect(message).toMatch(/текущ[а-я]+ направлен/iu);
    expect(message).toMatch(/обратн[а-я]+ направлен/iu);
   });
   await Promise.all([reset(page).click(),decision]);
   if(accept){
    await expect(page.locator('.mapping-applied-feedback')).toContainText('Применено пар: 0');
    expect(await record(page)).toBeNull();
    await expect(page.locator('.mapping-table')).toHaveCount(0);
    await swap(page,right);
    await expect(page.locator('.mapping-edit-form')).toHaveCount(0);
    return;
   }
   expect((await record(page)).pairs[0].comment).toBe(savedComment);
   await expect(page.locator('.mapping-preview')).toContainText(draftComment);
   await swap(page,right);
   await expect(page.locator('.mapping-tag-choice-before')).toHaveValue(reverseSource);
   await expect(page.locator('.mapping-edit-comment')).toHaveValue(reverseComment);
   await page.locator('.mapping-tag-choice-after').selectOption(reverseTarget);
   await page.locator('.mapping-editor-save').click();
   await page.locator('.mapping-apply-top').click();
   await expect(page.locator('.mapping-applied-feedback')).toContainText('Применено пар: 2');
   expect((await record(page)).pairs).toContainEqual({beforePath:reverseSource,afterPath:reverseTarget,comment:reverseComment});
  });
 }
 for(const conflict of [false,true]){
  test('pending apply prevents edits in an open form and '+(conflict?'restores it on conflict':'applies the ready draft'),async({page,context})=>{
   const locker=await holdLock(context),preview=page.locator('.mapping-preview'),apply=page.locator(conflict?'.mapping-apply':'.mapping-apply-top');
   const form=page.locator('.mapping-edit-form'),query=page.locator('.mapping-tag-search-before');
   const beforePath='approveSocialAssistResult/socialAssistNum',afterPath='benefitResponseResult/processNum';
   await page.locator('.mapping-add-row').click();
   await query.fill('socialAssistNum');
   await apply.click();
   await waitForQueuedOperation(page);
   // Action handlers can still be reached by synthetic events on an inert form.
   await form.evaluate((element,{beforePath,afterPath})=>{
    for(const [side,value] of [['before',beforePath],['after',afterPath]]){
     const select=element.querySelector('.mapping-tag-choice-'+side);
     select.value=value;
     select.dispatchEvent(new Event('change',{bubbles:true}));
    }
    element.requestSubmit();
   },{beforePath,afterPath});
   await expect(preview.locator('.mapping-explicit')).toHaveCount(1);
   await expect(preview).toHaveJSProperty('inert',true);
   await expect(upload(page)).toBeDisabled();
   expect((await record(page)).pairs[0].comment).toBe(savedComment);
   const competingComment='Правка другой вкладки';
   await locker.evaluate(({key,conflict,competingComment})=>{
    if(conflict){
     const saved=JSON.parse(localStorage.getItem(key));
     saved.pairs[0].comment=competingComment;
     localStorage.setItem(key,JSON.stringify(saved));
    }
    window.releaseMappingLock();
   },{key,conflict,competingComment});
   if(!conflict){
    await expect(page.locator('.mapping-applied-feedback')).toContainText('Применено пар: 1');
    expect((await record(page)).pairs.map(pair=>pair.comment)).toEqual([draftComment]);
    return;
   }
   await expect(page.locator('.mapping-draft-errors')).toContainText('Конфликт');
   await expect(preview).toContainText(draftComment);
   await expect(preview).toHaveJSProperty('inert',false);
   await expect(apply).toBeFocused();
   await expect(upload(page)).toBeEnabled();
   await expect(reset(page)).toBeEnabled();
   await expect(query).toHaveValue('socialAssistNum');
   await expect(page.locator('.mapping-tag-choice-before')).toHaveValue('');
   await expect(page.locator('.mapping-tag-choice-after')).toHaveValue('');
   expect((await record(page)).pairs.map(pair=>pair.comment)).toEqual([competingComment]);
   await page.locator('.mapping-tag-choice-before').selectOption(beforePath);
   await page.locator('.mapping-tag-choice-after').selectOption(afterPath);
   await page.locator('.mapping-editor-save').click();
   await expect(preview.locator('.mapping-explicit')).toHaveCount(2);
  });
 }
});

test.describe('card undo while its Web Lock is pending',()=>{
 const pair={beforePath:'approveSocialAssistResult/status',afterPath:'benefitResponseResult/status',comment:'Отменяемая пара'};
 const remaining={beforePath:'approveSocialAssistResult/socialAssistNum',afterPath:'benefitResponseResult/processNum',comment:'Оставшаяся пара'};
 const record=page=>page.evaluate(key=>JSON.parse(localStorage.getItem(key)),key);
 test.beforeEach(async({context,page})=>{
  for(const script of ['compare.js','comparison-mapping.js','mapping-draft.js','mapping-editor.js'])await context.route('**/'+script+'*',route=>route.fulfill({path:path.join(__dirname,'../../web',script),contentType:'application/javascript'}));
  await page.goto(url);
 });
 for(const openBeforeUndo of [false,true]){
  test('reflects the saved result when '+(openBeforeUndo?'the preview was already open':'preview is requested during undo'),async({page,context})=>{
   await add(page,pair.beforePath,pair.afterPath,pair.comment);
   if(openBeforeUndo)await add(page,remaining.beforePath,remaining.afterPath,remaining.comment);
   await page.locator('.mapping-apply').click();
   await expect(page.locator('.mapping-applied-feedback')).toContainText('Применено пар: '+(openBeforeUndo?2:1));
   const suggest=page.locator('.mapping-suggest'),preview=page.locator('.mapping-preview');
   if(openBeforeUndo)await suggest.click();
   const locker=await holdLock(context);
   await page.locator('.difference').filter({hasText:pair.comment}).locator('.mapping-undo').click();
   await waitForQueuedOperation(page);
   const previewEnabled=await suggest.isEnabled();
   if(!openBeforeUndo)await suggest.dispatchEvent('click');
   const pairsWhilePending=(await record(locker)).pairs;
   await locker.evaluate(()=>window.releaseMappingLock());
   await expect(page.locator('.mapping-applied-feedback')).toContainText('Сохранено пар: '+(openBeforeUndo?1:0));
   expect((await record(locker)).pairs).toEqual(openBeforeUndo?[remaining]:[]);
   await expect(page.locator('.difference').filter({hasText:pair.comment})).toHaveCount(0);
   await expect(preview).not.toContainText(pair.comment);
   expect(previewEnabled).toBe(false);
   expect(pairsWhilePending).toContainEqual(pair);
   if(openBeforeUndo){
    await expect(preview).toContainText(remaining.comment);
    await expect(page.locator('.mapping-panel > [role=status]')).toContainText('Сохранено пар: 1');
   }else{
    await expect(page.locator('.mapping-panel > [role=status]')).toContainText('Применено пар: 0');
   }
  });
 }
 test('keeps the preview editable after a conflicting undo',async({page,context})=>{
  await add(page,pair.beforePath,pair.afterPath,pair.comment);
  await page.locator('.mapping-apply').click();
  await expect(page.locator('.mapping-applied-feedback')).toContainText('Применено пар: 1');
  await page.locator('.mapping-suggest').click();
  const locker=await holdLock(context);
  await page.locator('.mapping-undo').click();
  await waitForQueuedOperation(page);
  await locker.evaluate(({key,pair})=>{
   const saved=JSON.parse(localStorage.getItem(key));
   localStorage.setItem(key,JSON.stringify({...saved,pairs:[{...pair,comment:'Правка другой вкладки'}]}));
   window.releaseMappingLock();
  },{key,pair});
  await expect(page.locator('.mapping-undo-error')).toContainText('Конфликт');
  await expect(page.locator('.mapping-preview')).toContainText(pair.comment);
  await expect(page.locator('.mapping-preview')).toHaveJSProperty('inert',false);
  await expect(page.locator('.mapping-suggest')).toBeEnabled();
  await page.locator('.mapping-edit-row').click();
  await expect(page.locator('.mapping-edit-comment')).toHaveValue(pair.comment);
  expect((await record(locker)).pairs[0].comment).toBe('Правка другой вкладки');
 });
});

for(const recoverStorage of [false,true])for(const recheck of [false,true]){
 test('a relative pair undone in another tab '+(recheck?'returns when individually rechecked':'stays undone when the proposal is untouched')+(recoverStorage?' after storage recovery':''),async({page,context})=>{
  for(const script of ['compare.js','comparison-mapping.js','mapping-draft.js','mapping-editor.js'])await context.route('**/'+script+'*',route=>route.fulfill({path:path.join(__dirname,'../../web',script),contentType:'application/javascript'}));
  const parent={beforePath:'approveSocialAssistResult',afterPath:'benefitResponseResult',comment:'Родитель'};
  const beforePath=parent.beforePath+'/status',afterPath=parent.afterPath+'/status';
  await page.goto('/site/');
  await page.evaluate(({key,left,right,parent})=>localStorage.setItem(key,JSON.stringify({version:1,left,right,pairs:[parent]})),{key,left:left.revision_id,right:right.revision_id,parent});
  const stale=await context.newPage();
  await stale.goto(url);
  const independent={beforePath:parent.beforePath+'/socialAssistNum',afterPath:parent.afterPath+'/processNum',comment:'Независимая пара'};
  const readSaved=()=>stale.evaluate(key=>JSON.parse(localStorage.getItem(key)).pairs,key);
  if(recoverStorage){
   await stale.evaluate(key=>{
    const setItem=Storage.prototype.setItem;
    Storage.prototype.setItem=function(name,value){
     if(name===key){Storage.prototype.setItem=setItem;throw new DOMException('Quota exceeded','QuotaExceededError')}
     return setItem.call(this,name,value);
    };
   },key);
   await add(stale,independent.beforePath,independent.afterPath,independent.comment);
   await stale.locator('.mapping-select-none').click();
   await stale.locator('.mapping-apply').click();
   await expect(stale.locator('.mapping-panel')).toContainText('только в текущем сеансе');
   expect(await readSaved()).toEqual([parent]);
  }
  await stale.locator('.mapping-suggest').click();
  const choice=stale.locator(`input[data-before-path="${beforePath}"][data-after-path="${afterPath}"]`);
  await expect(choice).toBeChecked();
  const hasSavedChild=async()=> (await readSaved()).some(pair=>pair.beforePath===beforePath&&pair.afterPath===afterPath);
  await page.goto(url);await page.locator('.mapping-suggest').click();await page.locator('.mapping-apply').click();
  await expect(page.locator('.mapping-applied-feedback')).toContainText('Сравнение обновлено');
  const card=page.locator('.difference').filter({has:page.locator('code').getByText(beforePath+' → '+afterPath,{exact:true})});
  await card.locator('.mapping-undo').click();
  await expect(page.locator('.mapping-applied-feedback')).toContainText('Сопоставление отменено');
  expect(await hasSavedChild()).toBe(false);
  if(recheck){await choice.uncheck();await choice.check();await expect(choice).toHaveClass('mapping-explicit')}
  await stale.locator('.mapping-apply').click();
  await expect(stale.locator('.mapping-table')).toHaveCount(0);
  await expect(stale.locator('.mapping-applied-feedback')).toContainText('Сравнение обновлено');
  expect(await hasSavedChild()).toBe(recheck);
  if(recoverStorage)expect(await readSaved()).toContainEqual(independent);
 });
}

test('storage recovery merges isolated tab edits into the stored comparison',async({page,context})=>{
 await page.goto(url);
 await page.evaluate(key=>{
  const original=Storage.prototype.setItem;
  Storage.prototype.setItem=function(name,value){
   if(name===key){Storage.prototype.setItem=original;throw new DOMException('Quota exceeded','QuotaExceededError')}
   return original.call(this,name,value);
  };
 },key);
 await add(page,'approveSocialAssistResult/status','benefitResponseResult/status','Локальная пара');
 await page.locator('.mapping-apply').click();
 await expect(page.locator('.mapping-panel')).toContainText('только в текущем сеансе');
 const other=await context.newPage();await other.goto(url);
 await add(other,'approveSocialAssistResult/socialAssistNum','benefitResponseResult/processNum','Пара другой вкладки');
 await other.locator('.mapping-apply').click();await expect(other.locator('.mapping-applied-feedback')).toContainText('1');
 await add(page,'approveSocialAssistResult/errorList/error/code','benefitResponseResult/errorList/error/code','Новая пара');
 await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toContainText('3');
 const record=await other.evaluate(key=>JSON.parse(localStorage.getItem(key)),key);
 expect(record.pairs.map(pair=>pair.comment).sort()).toEqual(['Локальная пара','Новая пара','Пара другой вкладки']);
});
for(const reverse of [false,true]){
 test('identical remaps preserve an independent pair in '+(reverse?'reversed':'forward')+' tabs',async({page,context})=>{
  const a='approveSocialAssistResult/status',b='approveSocialAssistResult/errorList/error/code';
  const x='benefitResponseResult/status',y='benefitResponseResult/processNum',z='benefitResponseResult/errorList/error/code';
  await page.goto(url);await add(page,a,x,'');
  await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toContainText('1');
  const second=await context.newPage();
  const otherUrl=reverse?'/site/#'+new URLSearchParams({message:'10510',compare:'1',left:right.revision_id,right:left.revision_id}):url;
  await second.goto(otherUrl);
  async function replace(tab,side,path){
   await tab.locator('.mapping-suggest').click();await tab.locator('.mapping-edit-row').first().click();
   await tab.locator('.mapping-tag-choice-'+side).selectOption(path);
   await tab.locator('.mapping-editor-save').click();await tab.locator('.mapping-editor-cancel').click();
  }
  await replace(page,'after',y);await replace(second,reverse?'before':'after',y);
  await add(second,reverse?z:b,reverse?b:z,'Независимая пара');
  await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toContainText('1');
  await second.locator('.mapping-apply').click();await expect(second.locator('.mapping-applied-feedback')).toContainText('2');
  await page.reload();
  const expected=[{beforePath:a,afterPath:y,comment:''},{beforePath:b,afterPath:z,comment:'Независимая пара'}];
  const pairs=await page.evaluate(key=>{
   const stored=JSON.parse(localStorage.getItem(key));
   const current=new URLSearchParams(location.hash.slice(1)).get('left');
   return stored.pairs.map(p=>stored.left===current?p:{beforePath:p.afterPath,afterPath:p.beforePath,comment:p.comment});
  },key);
  expect(pairs).toEqual(expected);
 });
}
test('different remap targets in two tabs keep the conflicting draft',async({page,context})=>{
 await page.goto(url);
 const a='approveSocialAssistResult/status',before='benefitResponseResult/status';
 await add(page,a,before,'');await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toContainText('1');
 const second=await context.newPage();await second.goto(url);
 for(const [tab,path] of [[page,'benefitResponseResult/processNum'],[second,'benefitResponseResult/errorList/error/code']]){
  await tab.locator('.mapping-suggest').click();await tab.locator('.mapping-edit-row').first().click();
  await tab.locator('.mapping-tag-choice-after').selectOption(path);
  await tab.locator('.mapping-editor-save').click();await tab.locator('.mapping-editor-cancel').click();
 }
 await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toBeVisible();
 await second.locator('.mapping-apply').click();
 await expect(second.locator('.mapping-draft-errors')).toContainText('Конфликт');
 await expect(second.locator('.mapping-draft-errors')).toContainText('benefitResponseResult/processNum');
 await expect(second.locator('.mapping-draft-errors')).toContainText('benefitResponseResult/errorList/error/code');
});
test('two real tabs merge disjoint draft applies and retain conflicting drafts',async({page,context})=>{
 // Exercise the current source through the ordinary comparison page even before
 // a site rebuild; no fixture bypasses the mounted editor or apply callback.
 await context.route('**/*',route=>new URL(route.request().url()).origin==='http://127.0.0.1:4173'?route.continue():route.abort());
 for(const script of ['compare.js','compare-core.js','comparison-export.js','comparison-mapping.js','mapping-draft.js','mapping-editor.js'])await context.route('**/'+script+'*',route=>route.fulfill({path:path.join(__dirname,'../../web',script),contentType:'application/javascript'}));
 const second=await context.newPage();await page.goto(url);await second.goto(url);await expect(page.locator('.mapping-add')).toBeVisible();await expect(second.locator('.mapping-add')).toBeVisible();
 await add(page,'approveSocialAssistResult/status','benefitResponseResult/status','Первое поле');await add(second,'approveSocialAssistResult/socialAssistNum','benefitResponseResult/processNum','Второе поле');
 // Hold the same real Web Lock so both ordinary apply handlers queue before
 // either can read storage. Releasing it exercises actual cross-tab contention.
 await page.evaluate(key=>{window.mappingLockHeld=false;navigator.locks.request(key,async()=>{window.mappingLockHeld=true;await new Promise(resolve=>{window.releaseMappingLock=resolve})})},key);await expect.poll(()=>page.evaluate(()=>window.mappingLockHeld)).toBe(true);
 await page.locator('.mapping-apply').click();
 await expect.poll(()=>page.evaluate(async key=>(await navigator.locks.query()).pending.filter(lock=>lock.name===key).length,key)).toBe(1);
 await second.locator('.mapping-apply').click();
 await expect.poll(()=>page.evaluate(async key=>(await navigator.locks.query()).pending.filter(lock=>lock.name===key).length,key)).toBe(2);
 await expect(page.locator('.mapping-apply')).toBeDisabled();await expect(second.locator('.mapping-apply')).toBeDisabled();expect(await page.evaluate(key=>localStorage.getItem(key),key)).toBeNull();await page.evaluate(()=>window.releaseMappingLock());
 await expect(second.locator('.mapping-applied-feedback')).toContainText('2');const stored=await second.evaluate(key=>JSON.parse(localStorage.getItem(key)).pairs,key);expect(stored.map(p=>p.comment).sort()).toEqual(['Второе поле','Первое поле']);
 await page.reload();await second.reload();await expect(page.locator('.mapping-suggest')).toBeVisible();await expect(second.locator('.mapping-suggest')).toBeVisible();await editFirst(page,'Первая правка');await editFirst(second,'Конфликтующая правка');await page.locator('.mapping-apply').click();await expect(page.locator('.mapping-applied-feedback')).toContainText('2');await second.locator('.mapping-apply').click();
 await expect(second.locator('.mapping-draft-errors')).toContainText('другой вкладке');await expect(second.locator('.mapping-draft-errors')).toContainText('Сохранено в другой вкладке: approveSocialAssistResult/status → benefitResponseResult/status · комментарий: "Первая правка"');await expect(second.locator('.mapping-draft-errors')).toContainText('В этом черновике: approveSocialAssistResult/status → benefitResponseResult/status · комментарий: "Конфликтующая правка"');await expect(second.locator('.mapping-draft-notice')).toBeVisible();await expect(second.locator('.mapping-preview')).toContainText('Конфликтующая правка');const saved=await second.evaluate(key=>JSON.parse(localStorage.getItem(key)).pairs,key);expect(saved[0].comment).toBe('Первая правка');await expect(second.locator('.mapping-apply')).toBeEnabled();await second.locator('.mapping-discard').click();expect(await second.evaluate(key=>JSON.parse(localStorage.getItem(key)).pairs,key)).toEqual(saved);await expect(second.locator('.mapping-draft-notice')).toBeHidden();
 await page.reload();await second.reload();await add(page,'approveSocialAssistResult/errorList/error/code','benefitResponseResult/errorList/error/code','Третье поле');
 await page.evaluate(key=>{window.mappingLockHeld=false;navigator.locks.request(key,async()=>{window.mappingLockHeld=true;await new Promise(resolve=>{window.releaseMappingLock=resolve})})},key);await expect.poll(()=>page.evaluate(()=>window.mappingLockHeld)).toBe(true);
 await page.locator('.mapping-apply').click();const reset=second.getByRole('button',{name:'Сбросить сопоставления',exact:true});await reset.click();await expect(reset).toBeDisabled();expect(await second.evaluate(key=>JSON.parse(localStorage.getItem(key)).pairs,key)).toEqual(saved);await page.evaluate(()=>window.releaseMappingLock());
 await expect.poll(()=>second.evaluate(key=>localStorage.getItem(key),key)).toBeNull();await expect(second.locator('.mapping-panel')).toContainText('Применено пар: 0');
});
