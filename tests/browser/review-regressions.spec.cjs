const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'site/catalog-data.js'), 'utf8').replace(/^window.SEDO_CATALOG=/, '').replace(/;\s*$/, ''));

test.beforeEach(async ({ context }) => {
  await context.route('**/*', route => new URL(route.request().url()).origin === 'http://127.0.0.1:4173' ? route.continue() : route.abort());
  for(const script of ['app.js','journal.js','review-journal.js','compare.js','compare-core.js','comparison-export.js'])await context.route('**/'+script+'*',route=>route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(root,'web',script),'utf8')}));
});

async function controlledTitles(page){
 const sources=['prod','test'].map(environment=>catalog.revisions.find(r=>r.number==='32210'&&r.environment===environment));
 const revisions=catalog.revisions.filter(r=>r.number!=='32210');
 for(const [index,source] of sources.entries())for(const current of [false,true]){
  const revision_id=String(index*2+Number(current)+1).repeat(64);
  const title=current?(source.environment==='prod'?'Сведения о заработной плате — контрольное новое название':'Сведения о первых трех днях — контрольный тестовый контур'):'Прежнее контрольное название сообщения';
  const module='Контрольный модуль';
  const edition_sources=source.edition_sources.map(s=>({...s,revision_id,listed_now:current,title,module}));
  revisions.push({...source,revision_id,display_revision_id:revision_id,listed_now:current,label_date:'16.11.2026',module,description:'',title,edition_sources});
 }
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,registry:catalog.registry.filter(r=>r.number!=='32210'),revisions})+';'}));
}

test('a renamed source of the same schema remains searchable in its own contour',async({page})=>{
 const group=catalog.revisions.filter(r=>r.number==='14'&&r.environment==='prod');
 const rep=group.find(r=>r.revision_id===r.display_revision_id),alias=group.find(r=>r.revision_id!==r.display_revision_id);
 const renamed='Новая спецификация того же формата';
 const revisions=catalog.revisions.map(r=>!group.includes(r)?r:{...r,edition_sources:r.edition_sources.map(s=>({...s,title:s.revision_id===alias.revision_id?renamed:s.title,listed_now:s.revision_id===alias.revision_id}))});
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions,registry:catalog.registry.filter(r=>r.number!=='14')})+';'}));
 await page.goto('/site/');await page.locator('#catalog-env').selectOption('prod');await page.locator('#message-query').fill(renamed);
 const card=page.locator('.message-card[data-number="14"]');await expect(card).toHaveCount(1);await expect(card.getByText(renamed,{exact:true})).toBeVisible();
 await page.locator('#catalog-env').selectOption('test');await expect(card).toHaveCount(0);
 for(const r of [rep,alias]){
  await page.goto('/site/#message=14&env=prod&rev='+r.revision_id);
  await expect(page.locator('#frame')).toHaveAttribute('src',new RegExp(r.revision_id));
 }
});

test('comparison selectors preserve historical workbook IDs and saved mappings on return',async({page})=>{
 const {Workbook}=require('../../web/vendor/exceljs.min.js');
 const editions=catalog.revisions.filter(r=>r.number==='14');
 const canonical=env=>editions.find(r=>r.environment===env&&r.revision_id===r.display_revision_id);
 const historical=env=>editions.find(r=>r.environment===env&&r.revision_id!==r.display_revision_id);
 await page.goto('/site/');
 for(const side of [0,1]){
  const left=side===0?canonical('prod'):historical('prod'),right=side===0?historical('test'):canonical('test');
  const key='sedo-field-mappings:v1:'+[left.revision_id,right.revision_id].sort().join(':');
  const record={version:1,left:left.revision_id,right:right.revision_id,pairs:[{beforePath:left.root,afterPath:right.root,comment:'Сохранено для прежней ссылки'}]};
  await page.evaluate(([key,record])=>localStorage.setItem(key,JSON.stringify(record)),[key,record]);
  await page.goto('/site/#'+new URLSearchParams({message:'14',compare:'1',left:left.revision_id,right:right.revision_id}));
  const choices=page.locator('.compare-controls select');
  await expect(page.getByText(record.pairs[0].comment,{exact:true})).toBeVisible();
  await choices.nth(side).selectOption(canonical(side===0?'test':'prod').revision_id);
  await expect(choices.nth(side)).toHaveValue(canonical(side===0?'test':'prod').revision_id);
  await choices.nth(side).selectOption((side===0?left:right).revision_id);
  await expect(page.getByText(record.pairs[0].comment,{exact:true})).toBeVisible();
  const pending=page.waitForEvent('download');await page.getByRole('button',{name:'Выгрузить Excel для сопоставления'}).click();
  const book=new Workbook();await book.xlsx.load(fs.readFileSync(await (await pending).path()));
  const meta=book.getWorksheet('О выгрузке');expect([meta.getCell('B4').value,meta.getCell('C4').value]).toEqual([left.revision_id,right.revision_id]);
  expect(book.getWorksheet('Сопоставление').getCell('I6').value).toBe(record.pairs[0].comment);
 }
});

test('tab titles follow direct links and navigation between catalog, editions and errors', async ({ page }) => {
  await page.goto('/site/#remarks=1');
  await page.getByRole('link', { name:'Все сообщения', exact:true }).click();
  await expect(page).toHaveTitle('Справочник СЭДО');
  await page.locator('.message-card[data-number="10110"] .message-main').click();
  await expect(page).toHaveTitle('Сообщение 10110 · СЭДО');
  await page.getByRole('link',{name:'Сравнить редакции',exact:true}).click();
  await page.goBack();
  await expect(page).toHaveTitle('Сообщение 10110 · СЭДО');
  for(const [hash,title] of [['message=10110','Сообщение 10110 · СЭДО'],['message=unknown','Сообщение не найдено · СЭДО'],['message=10110&env=prod&rev=unknown','Редакция недоступна · СЭДО'],['','Справочник СЭДО']]){
    await page.goto('/site/#'+hash);
    await page.reload();
    await expect(page).toHaveTitle(title);
  }
});

test('catalog search uses titles of the selected contour', async ({ page }) => {
  await controlledTitles(page);
  await page.goto('/site/');
  for (const [environment, query, found] of [
    ['prod', 'заработной плате', true], ['test', 'заработной плате', false],
    ['test', 'первых трех', true], ['prod', 'первых трех', false],
    ['', 'заработной плате', true],
  ]) {
    await page.locator('#catalog-env').selectOption(environment);
    await page.locator('#message-query').fill(query);
    await expect(page.locator('.message-card[data-number="32210"]')).toHaveCount(found ? 1 : 0);
  }
});

test('renamed message keeps its current contour title while archived editions remain searchable',async({page})=>{
 await controlledTitles(page);
 await page.goto('/site/');
 const card=page.locator('.message-card[data-number="32210"]');
 for(const [environment,query,title] of [['prod','заработной плате','Сведения о заработной плате — контрольное новое название'],['test','первых трех','Сведения о первых трех днях — контрольный тестовый контур']]){
  await page.locator('#catalog-env').selectOption(environment);
  await page.locator('#message-query').fill(query);
  await expect(card.locator('strong')).toHaveText(title);
  await page.locator('#message-query').fill('32210');
  await expect(card.locator('strong')).toHaveText(title);
  await page.locator('#message-query').fill('Прежнее контрольное название');
  await expect(card.locator('strong')).toHaveText(title);
  await card.locator('.message-main').click();
  await expect(page.locator('#versions > p').first()).toHaveText(title);
  await expect(page.locator('.version-panel').filter({has:page.getByRole('heading',{name:environment==='prod'?'Продуктивный контур':'Тестовый контур'})}).getByRole('link',{name:'Открыть дерево'})).toHaveCount(2);
  await page.getByRole('link',{name:'Все сообщения',exact:true}).click();
 }
});

test('same-date and undated edition cards and cross-type links prefer the current publication',async({page})=>{
 const source=catalog.revisions.find(r=>r.number==='7810'&&r.environment==='test'&&r.ready);
 for(const label_date of ['16.11.2026',null])for(const ids of [['a','z'],['z','a']]){
  const archived={...source,revision_id:ids[0].repeat(64),display_revision_id:ids[0].repeat(64),label_date,listed_now:false};
  const current={...source,revision_id:ids[1].repeat(64),display_revision_id:ids[1].repeat(64),label_date,listed_now:true};
  const revisions=catalog.revisions.filter(r=>r.number!=='7810'||r.environment!=='test');
  await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions:[...revisions,archived,current]})+';'}));
  await page.goto('/site/#message=7810&env=test');await page.reload();
  const panel=page.locator('.version-panel').filter({has:page.getByRole('heading',{name:'Тестовый контур'})});
  await expect(panel.getByRole('link',{name:'Открыть дерево'}).first()).toHaveAttribute('href',new RegExp(current.revision_id));
  await page.goto('/site/#message=78&env=test');
  const hash=await page.getByRole('link',{name:'Сравнить 78 → 7810'}).getAttribute('href');
  expect(new URLSearchParams(hash.slice(1)).get('right')).toBe(current.revision_id);
 }
});

test('catalog card modules follow the selected contour',async({page})=>{
 await page.route('**/app.js*',route=>route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(root,'web/app.js'),'utf8')}));
 await page.goto('/site/');
 await page.locator('#message-query').fill('600');
 const card=page.locator('.message-card[data-number="600"]');
 const prod='РУСТ',testModule='Сообщения ФК «Реестр страхователей»';
 for(const [environment,present,absent] of [['prod',prod,testModule],['test',testModule,prod]]){
  await page.locator('#catalog-env').selectOption(environment);
  await expect(card).toContainText(present);await expect(card).not.toContainText(absent);
 }
 await page.locator('#catalog-env').selectOption('');
 await expect(card).toContainText(prod);await expect(card).toContainText(testModule);
});

test('dated revisions lead the edition list and cross-type comparison',async({page})=>{
 const source=catalog.revisions.find(r=>r.number==='7810'&&r.environment==='test'&&r.ready);
 const undated={...source,label_date:null,display_revision_id:source.revision_id};
 const latest={...source,revision_id:'c'.repeat(64),display_revision_id:'c'.repeat(64),document_version:'3.2.0',label_date:'16.11.2026'};
 const revisions=catalog.revisions.filter(r=>r.number!=='7810'||r.environment!=='test');
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions:[...revisions,undated,latest]})+';'}));
 await page.goto('/site/#message=7810&env=test');
 const testEditions=page.locator('.version-panel').filter({has:page.getByRole('heading',{name:'Тестовый контур'})});
 await expect(testEditions.getByRole('link',{name:'Открыть дерево'}).first()).toHaveAttribute('href',new RegExp(latest.revision_id));
 await page.goto('/site/#message=78&env=test');
 const hash=await page.getByRole('link',{name:'Сравнить 78 → 7810'}).getAttribute('href');
 expect(new URLSearchParams(hash.slice(1)).get('right')).toBe(latest.revision_id);
});

test('added and removed fields are searchable by their constraints', async ({ page }) => {
  const edition = number => catalog.revisions.find(r => r.number === number && r.environment === 'prod' && r.ready);
  const a = edition('10'), b = edition('1010');
  for (const [left, right] of [[a,b],[b,a]]) {
    const query = new URLSearchParams({ message: '1010', compare: '1', left: left.revision_id, right: right.revision_id });
    await page.goto('/site/#'+query);
    const search = page.getByPlaceholder('Тег, путь, описание или ограничение');
    await expect(search).toBeVisible();
    await search.fill('[0-9a-fA-F]{8}');
    await expect(page.locator('.differences')).toContainText('notice/@messageUuid101');
  }
});

test('comparison exposes unresolved union enumeration coverage', async ({ page }) => {
  const [left, right] = ['prod', 'test'].map(environment =>
    catalog.revisions.find(r => r.number === '104' && r.environment === environment && r.ready));
  for (const edition of [left, right]) {
    const id = edition.revision_id;
    const data = JSON.parse(fs.readFileSync(path.join(root, 'data', 'fields', id+'.json'), 'utf8'));
    const union = data.fields.find(f => f.alternatives?.length && f.values?.length);
    expect(union).toBeTruthy();
    union.unionValueTypes = union.values.map(() => null);
    await page.route('**/fields/'+id+'.js*', route => route.fulfill({
      contentType: 'application/javascript',
      body: 'window.SEDO_FIELDS=window.SEDO_FIELDS||{};window.SEDO_FIELDS['+JSON.stringify(id)+']='+JSON.stringify(data.fields)+';'
    }));
  }
  await page.goto('/site/#'+new URLSearchParams({ message:'104', compare:'1', left:left.revision_id, right:right.revision_id }));
  await expect(page.getByText('Для некоторых значений union тип не определён.', { exact:false })).toBeVisible();
});

for(const [name,category,fixture,changed,partial] of [
  ['comparison reveals a QName URI change in union fixed', 'unionFlags', 'globalFixed', 1, false],
  ['comparison marks a union fixed value with an unproved type', 'unionFlags', 'unresolved', 0, true],
  ['comparison omits overridden numeric limits', 'bounds', 'dominated', 0, false],
  ['comparison shows a changed effective numeric limit with its original spelling', 'bounds', 'changed', 1, false],
]){
  test(name,async({page})=>{
    const editions=['prod','test'].map(environment=>catalog.revisions.find(r=>r.number==='104'&&r.environment===environment&&r.ready));
    const [before,after]=require('../fixtures/comparison-xsd.cjs')[category][fixture];
    for(const [index,edition] of editions.entries()){
      const id=edition.revision_id;
      await page.route('**/fields/'+id+'.js*',route=>route.fulfill({contentType:'application/javascript',
        body:'window.SEDO_FIELDS=window.SEDO_FIELDS||{};window.SEDO_FIELDS['+JSON.stringify(id)+']='+JSON.stringify(index===0?before:after)+';'}));
    }
    await page.goto('/site/#'+new URLSearchParams({ message:'104', compare:'1', left:editions[0].revision_id, right:editions[1].revision_id }));
    await expect(page.locator('.compare-counts .changed')).toHaveText('Изменено · '+changed);
    await expect(page.getByText('Для некоторых значений union тип не определён.',{exact:false})).toHaveCount(partial?1:0);
    if(fixture==='globalFixed'){
      const row=page.getByRole('row').filter({hasText:'Фиксированное значение'});
      await expect(row.locator('.before')).toContainText('p:X ({urn:A}X)');
      await expect(row.locator('.after')).toContainText('p:X ({urn:B}X)');
    }
    if(fixture==='changed'){
      const row=page.getByRole('row').filter({hasText:'Ограничения значения'});
      await expect(row.locator('.before')).toContainText('10');
      await expect(row.locator('.after')).toContainText('11');
    }
  });
}

test('comparison keeps the declared whitespace of inherited enumeration values', async ({ page }) => {
  const editions = ['prod', 'test'].map(environment =>
    catalog.revisions.find(r => r.number === '104' && r.environment === environment && r.ready));
  const { enumWhitespace } = require('../fixtures/comparison-xsd.cjs');
  for (const [index, edition] of editions.entries()) {
    const id = edition.revision_id;
    await page.route('**/fields/'+id+'.js*', route => route.fulfill({
      contentType:'application/javascript',
      body:'window.SEDO_FIELDS=window.SEDO_FIELDS||{};window.SEDO_FIELDS['+JSON.stringify(id)+']='+JSON.stringify(enumWhitespace.inherited[index])+';'
    }));
  }
  await page.goto('/site/#'+new URLSearchParams({ message:'104', compare:'1',
    left:editions[0].revision_id, right:editions[1].revision_id }));
  const row = page.getByRole('row').filter({hasText:'Допустимые значения'});
  await expect(row).toBeVisible();
  expect(await row.locator('.before').textContent()).toBe(' A ');
  expect(await row.locator('.after').textContent()).toBe('A');
});

test('the update journal marks partial union and NOTATION coverage and filters by proved changes', async ({ page }) => {
  const [left, right] = ['prod', 'test'].map(environment =>
    catalog.revisions.find(r => r.number === '104' && r.environment === environment && r.ready));
  const events = [{id:'partial-zero', changed:0, limitedUnion:true}, {id:'partial-change', changed:1, limitedUnion:true},
    {id:'notation-zero', changed:0, limitedNotation:true}, {id:'complete-zero', changed:0}].map(({id,changed,limitedUnion,limitedNotation}) => ({
    event_id:id, revision_id:right.revision_id, previous_revision_id:left.revision_id, observed_at:'2026-11-16T09:00:00Z',
    comparison:{status:'ready',left:left.revision_id,right:right.revision_id,counts:{added:0,removed:0,changed},limitedUnion,limitedNotation},
  }));
  await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',
    body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,schema_updates:events,source_updates:[]})+';'}));
  await page.goto('/site/#updates=1');
  const card = id => page.locator('.update-card[data-event="'+id+'"]');
  for(const id of ['partial-zero','partial-change']){
    await expect(card(id)).toContainText('Для некоторых значений union тип не определён.');
  }
  await expect(card('notation-zero')).toContainText('Для некоторых значений NOTATION пространство имён не установлено.');
  await expect(card('complete-zero')).not.toContainText('тип не определён');
  await page.locator('#updates-only-changes').check();
  await expect(card('partial-zero')).toHaveCount(0);
  await expect(card('notation-zero')).toHaveCount(0);
  await expect(card('complete-zero')).toHaveCount(0);
  await expect(card('partial-change')).toBeVisible();
});

test('a pattern containing a backslash can be pasted into comparison search', async ({ page }) => {
  const edition = number => catalog.revisions.find(r => r.number === number && r.environment === 'test' && r.ready);
  const a = edition('84'), b = edition('8410');
  for (const [left,right] of [[a,b],[b,a]]) {
    await page.goto('/site/#'+new URLSearchParams({ message:'8410', compare:'1', left:left.revision_id, right:right.revision_id }));
    await page.getByPlaceholder('Тег, путь, описание или ограничение').fill('[C|E|P]_\\d{10}_');
    await expect(page.locator('.differences')).toContainText('paymentStatusRequest/batchNo');
  }
});
