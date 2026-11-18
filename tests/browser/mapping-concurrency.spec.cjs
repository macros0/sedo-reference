const {test,expect}=require('@playwright/test');
const fs=require('node:fs'),path=require('node:path');
const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../../site/catalog-data.js'),'utf8').replace(/^window.SEDO_CATALOG=/,'').replace(/;\s*$/,''));
const edition=(number,version)=>catalog.revisions.find(r=>r.number===number&&r.environment==='prod'&&r.document_version===version&&r.ready);
const left=edition('105','2.28'),right=edition('10510','3.0'),key='sedo-field-mappings:v1:'+[left.revision_id,right.revision_id].sort().map(encodeURIComponent).join(':'),url='/site/#'+new URLSearchParams({message:'10510',compare:'1',left:left.revision_id,right:right.revision_id});
async function add(page,before,after,comment){
 await page.locator('.mapping-add').click();await page.locator('.mapping-tag-choice-before').selectOption(before);await page.locator('.mapping-tag-choice-after').selectOption(after);await page.locator('.mapping-edit-comment').fill(comment);await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();
}
async function editFirst(page,comment){
 await page.locator('.mapping-suggest').click();await page.locator('.mapping-edit-row').first().click();await page.locator('.mapping-edit-comment').fill(comment);await page.locator('.mapping-editor-save').click();await page.locator('.mapping-editor-cancel').click();
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
