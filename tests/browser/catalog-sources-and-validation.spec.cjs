const {test,expect}=require('@playwright/test');
const {createHash}=require('node:crypto');
const fs=require('node:fs'),path=require('node:path');
const {createServer}=require('node:http'),{once}=require('node:events');
const {gzipSync}=require('node:zlib');
const {catalog,pythonFixture}=require('./catalog-fixtures.cjs');
const {namespaceCandidates}=require('../fixtures/namespace-candidates.cjs');
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');

test('a ready module schema keeps the diagnostic for its unresolved declaration',async({page})=>{
 const built=pythonFixture([
  'import contextlib,io,json,shutil',
  'from pathlib import Path',
  'from unittest.mock import patch',
  'import build_site',
  'from catalog import export',
  'from fields import build',
  'from tests.test_module_messages import collect_declaration_fixture',
  'from tests.test_source_collection_regressions import SourceCollectionTests',
  'f = SourceCollectionTests()',
  'f.setUp()',
  'try:',
  '    with contextlib.redirect_stdout(io.StringIO()):',
  "        collect_declaration_fixture(f, 'ambiguous')",
  "        shutil.copytree(Path('web'), f.root / 'web')",
  "        shutil.copy2('viewer-template.html', f.root)",
  "        index = f.root / 'data/catalog.json'",
  '        export(f.state, index)',
  "        build(index, f.state, f.root / 'data/fields')",
  "        with patch.object(build_site, 'HERE', f.root):",
  '            build_site.main()',
  "    print((f.root / 'site/catalog-data.js').read_text('utf8').removeprefix('window.SEDO_CATALOG=').removesuffix(';'))",
  'finally:',
  '    f.doCleanups()'
 ].join('\n'));
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',
  body:'window.SEDO_CATALOG='+JSON.stringify(built)+';'}));
 await page.goto('/site/#message=42&env=test');
 const schema=page.locator('.version-panel .card').filter({hasText:'Неоднозначные схемы'});
 await expect(schema).toHaveCount(1);
 await expect(schema.getByRole('link',{name:'Открыть дерево'})).toBeVisible();
 await expect(schema.getByRole('link',{name:'Проверить XML локально'})).toBeVisible();
 await expect(schema.locator('.schema-diagnostic')).toHaveText('r (пространство имён не указано) — Неоднозначные схемы');
});

test('7810 production shows label date and version history from its DOCX',async({page})=>{
 await page.goto('/site/#message=7810&env=prod');
 const panel=page.locator('.version-panel').filter({has:page.getByRole('heading',{name:'Продуктивный контур'})});
 const source=panel.locator('.edition-source').filter({hasText:'версия 3.1'});
 await expect(source).toContainText('Дата в названии документа на сайте СФР · 08.06.2026');
 await expect(source).toContainText('Дата публикации спецификации · 08.06.2025');
 await expect(source).toContainText('Период действия XML-схемы «С» · 08.06.2026');
 await expect(source).toContainText('Номер версии XML-схемы · 3.0');
 await expect(source.locator('.version-date-warning')).toHaveText('Дата в названии документа на сайте СФР отличается от даты публикации спецификации в таблице DOCX.');
 await expect(source).toContainText('17.11.2025');
 const revision=catalog.revisions.find(r=>r.number==='7810'&&r.environment==='prod');
 await expect(source.getByRole('link',{name:'Источник таблицы версий — DOCX'})).toHaveAttribute('href',revision.document_url);
 const changes=source.locator('details.document-change-history');
 const summary=changes.locator('summary');
 const changeSource=changes.locator('a');
 const entries=changes.locator('.document-version-entry');
 await expect(summary).toHaveText('История изменений документа (2)');
 await expect(entries).toHaveCount(2);
 await expect(entries.first()).toBeHidden();
 await expect(changeSource).toBeHidden();
 await summary.click();
 await expect(entries.first()).toBeVisible();
 await expect(changeSource).toHaveAttribute('href',revision.document_url);
 await expect(changeSource).toBeVisible();
 await summary.press('Enter');
 await expect(entries.first()).toBeHidden();
 await expect(summary).toBeFocused();
 await expect(panel.getByRole('link',{name:'Открыть дерево'})).toBeVisible();
});

test('project warning remains visible after searching and opening a ready test message',async({page})=>{
 await page.goto('/site/');await page.locator('#catalog-env').selectOption('test');
 await page.locator('#message-query').fill('Является проектом, возможны модификации');
 await page.locator('.message-card[data-number="600"] a').first().click();
 const panel=page.locator('.version-panel').filter({has:page.getByRole('heading',{name:'Тестовый контур'})});
 await expect(panel).toContainText('Является проектом, возможны модификации');
 const registry=catalog.registry.find(r=>r.number==='600'&&r.environment==='test');
 await expect(panel.getByRole('link',{name:'Открыть официальный перечень ↗'})).toHaveAttribute('href',registry.source);
 await expect(panel.getByRole('link',{name:'Открыть дерево'})).toBeVisible();
 const prod=page.locator('.version-panel').filter({has:page.getByRole('heading',{name:'Продуктивный контур'})});
 await expect(prod).not.toContainText('Является проектом, возможны модификации');
});

test('a future test edition does not put the first production event into the changes filter',async({page})=>{
 const {summarizeUpdates}=require('../../summarize_updates.cjs'),{fields}=require('./catalog-fixtures.cjs');
 const revisions=['prod','test'].map(env=>catalog.revisions.find(r=>r.number==='122'&&r.environment===env&&r.ready));
 const events=revisions.map((r,index)=>({revision_id:r.revision_id,previous_revision_id:null,snapshot:'snapshot-'+index,observed_at:'2026-11-13T00:'+ (index?'53':'07')+':00Z'}));
 const data={...catalog,revisions,updates:events,source_updates:[]};
 data.schema_updates=summarizeUpdates(data,id=>fields(revisions.find(r=>r.revision_id===id)));
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify(data)}));
 await page.goto('/site/#updates=1&message=122&env=prod');
 const card=page.locator('.update-card');
 await expect(card).toContainText('подходящей редакции для сравнения пока нет');
 await expect(card.locator('.journal-counts')).toHaveCount(0);
 await page.locator('#updates-only-changes').check();await expect(card).toHaveCount(0);
 await page.locator('#updates-only-changes').uncheck();await expect(card).toHaveCount(1);
});

function documentOnlyFixture(){
 const published=catalog.revisions.find(r=>r.number==='10110'&&r.environment==='prod'&&r.document_download);
 const document={...published,ready:false,status:'document_only',source_type:'manual',document_url:null,
  manual_source:{label:'Telegram',filename:'10110_v3.1_отдел.docx'},validation_download:null,schema_download:null};
 delete document.edition_sources;
 const event={event_type:'source_added',revision_id:document.revision_id,event_id:'additional-document',source:document,observed_at:'2026-11-17T12:00:00Z'};
 return {document,data:{...catalog,revisions:[document],registry:[],document_sources:[],schema_updates:[],source_updates:[event]}};
}

test('manual DOCX is searchable by its original filename',async({page})=>{
 const {document,data}=documentOnlyFixture();
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify(data)}));
 await page.goto('/site/');await page.locator('#message-query').fill('отдел');
 await page.locator('.message-card[data-number="10110"] a').first().click();
 await expect(page.locator('#versions .edition-source')).toContainText(document.manual_source.filename);
 await expect(page.locator('#versions .edition-source')).toContainText('Telegram');
 await page.goto('/site/#updates=1&q=отдел');
 await expect(page.locator('.update-card')).toContainText(document.manual_source.filename);
});

test('new source of a document-only edition opens the message and downloads the DOCX',async({page})=>{
 const {document,data}=documentOnlyFixture();
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify(data)}));
 await page.goto('/site/#updates=1');
 const card=page.locator('.update-card');
 await expect(card).not.toContainText('XML-схема не изменилась');
 const download=page.waitForEvent('download');await card.getByRole('link',{name:'Скачать DOCX'}).click();
 expect(sha(fs.readFileSync(await (await download).path()))).toBe(document.document_sha256);
 await card.locator('.journal-links a').click();await expect(page.locator('#versions')).toBeVisible();
});

test('local validator discloses incomplete diagnostics',async({page})=>{
 const row=catalog.revisions.find(r=>r.number==='10110'&&r.environment==='prod'&&r.ready);
 const longName='item'+'z'.repeat(3500);
 const schema='<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="'+row.namespace+'"><xs:element name="'+row.root+'"><xs:complexType><xs:choice maxOccurs="unbounded"><xs:element name="n" type="xs:int"/><xs:element name="'+longName+'" type="xs:int"/></xs:choice></xs:complexType></xs:element></xs:schema>';
 const pack={format:1,entrypoint:'message.xsd',root:row.root,number:row.number,environment:row.environment,revision_id:row.revision_id,namespace:row.namespace,files:{'message.xsd':Buffer.from(schema).toString('base64')}};
 const bytes=gzipSync(JSON.stringify(pack)),data={...catalog,revisions:[{...row,validation_sha256:sha(bytes)}]};
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify(data)}));
 await page.route('**/'+row.validation_download+'*',route=>route.fulfill({body:bytes}));
 await page.goto('/site/#'+new URLSearchParams({message:row.number,env:row.environment,rev:row.revision_id,validate:'1'}));
 const validator=page.frameLocator('.validator-frame');
 await validator.getByText('Или вставьте XML текстом',{exact:true}).click();
 for(const [name,count,truncated] of [['n',31,true],[longName,25,true],['n',30,false]]){
  await validator.locator('#xml-text').fill('<r:'+row.root+' xmlns:r="'+row.namespace+'">'+('<'+name+'>bad</'+name+'>\n').repeat(count)+'</r:'+row.root+'>');
  await validator.locator('#check').click();
  await expect(validator.locator('#status')).toContainText('XML не соответствует');
  if(name==='n')await expect(validator.locator('.error-card')).toHaveCount(30);
  else expect(await validator.locator('.error-card').count()).toBeLessThan(count);
  if(truncated)await expect(validator.locator('#status')).toContainText('часть диагностических сообщений');
  else await expect(validator.locator('#status')).not.toContainText('часть диагностических сообщений');
 }
});

test('catalog search finds each module of the shared schema in its contour',async({page})=>{
 const shared=['prod','test'].map(env=>catalog.revisions.find(r=>r.number==='14'&&r.environment===env&&r.revision_id===r.display_revision_id
  &&r.edition_sources?.length>1&&r.edition_sources.some(s=>s.module?.includes('Реестр страхователей'))));
 const additional=shared.map(r=>{
  const id=sha('another independent schema for '+r.environment);
  return {...r,revision_id:id,display_revision_id:id,edition_sources:[{...r.edition_sources[0],revision_id:id}]};
 });
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions:[...additional,...catalog.revisions]})+';'}));
 for(const env of ['prod','test']){
  await page.goto('/site/');
  await page.locator('#catalog-env').selectOption(env);
  const card=page.locator('.message-card[data-number="14"]');
  for(const query of ['Реестр страхователей','РУСТ']){
   await page.locator('#message-query').fill(query);
   await expect(card).toContainText('Реестр страхователей');
  }
  await card.getByRole('link').first().click();
  const panel=page.locator('.version-panel').filter({has:page.getByRole('heading',{name:env==='prod'?'Продуктивный контур':'Тестовый контур'})});
  const row=shared.find(r=>r.environment===env);
  const second=additional.find(r=>r.environment===env);
  await expect(panel.locator('.card').filter({has:page.locator('a[href="#'+new URLSearchParams({message:second.number,env,rev:second.revision_id})+'"]')})).toHaveCount(1);
  const edition=panel.locator('.card').filter({has:page.locator('a[href="#'+new URLSearchParams({message:row.number,env,rev:row.revision_id})+'"]')});
  await expect(edition.getByRole('link',{name:'Открыть дерево'})).toHaveCount(1);
  for(const source of row.edition_sources){
   const entry=edition.locator('.edition-source').filter({has:page.locator('a').filter({hasText:decodeURIComponent(new URL(source.document_url).pathname.split('/').pop())})});
   await expect(entry).toContainText(source.module);
   await expect(entry.getByRole('link',{name:'Скачать DOCX'})).toHaveAttribute('href',source.document_download);
  }
 }
});

test('edition counts follow the active contour on message 10110',async({page})=>{
 const pair=['prod','test'].map(env=>catalog.revisions.find(r=>r.number==='10110'&&r.environment===env&&r.ready&&r.revision_id===r.display_revision_id));
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions:pair})+';'}));
 await page.goto('/site/');await page.locator('#message-query').fill('10110');
 const card=page.locator('.message-card[data-number="10110"]');
 for(const [env,count] of [['prod',1],['test',1],['',2]]){
  await page.locator('#catalog-env').selectOption(env);
  await expect(card.getByText('Редакций с деревом · '+count,{exact:true})).toBeVisible();
 }
});

test('the generated viewer shows the wildcard kind and compositor',async({page})=>{
 const {fields}=require('./catalog-fixtures.cjs');
 for(const [number,env,path] of [['32','test','noticeRequest/*'],['32','test','noticeRequest/@*'],['10010','prod','benefitRequest/benefit6/*']]){
  const row=catalog.revisions.find(r=>r.number===number&&r.environment===env&&r.ready&&fields(r).some(field=>field.path===path));
  await page.goto('/site/editions/'+row.revision_id+'.html#'+new URLSearchParams({field:path}));
  const details=page.locator('#detail details').filter({hasText:'Источник в XSD'});
  await details.locator('summary').click();
  await expect(details).toContainText('Допустимые пространства имён: любые (##any)');
 }
 for(const [number,kind,expected] of [['10110','attribute','Атрибут'],['10110','any','Расширение для элементов'],['12110','anyAttribute','Расширение для атрибутов']]){
  const row=catalog.revisions.find(r=>r.number===number&&r.environment==='prod'),field=fields(row).find(f=>f.kind===kind);
  await page.goto('/site/editions/'+row.revision_id+'.html#'+new URLSearchParams({field:field.path}));
  await expect(page.locator('#detail .pill')).toHaveText(expected);
 }
 for(const [number,rule] of [['111',/в любом порядке.*xs:all/],['10110',/порядок.*xs:sequence/i]]){
  const row=catalog.revisions.find(r=>r.number===number&&r.environment==='prod');
  await page.goto('/site/editions/'+row.revision_id+'.html');
  await expect(page.locator('#detail')).toContainText(rule);
 }
});

test('document-only module sources retain the DOCX beside a ready edition',async({page})=>{
 const digest='987d87743ee6bb7c13aede75cc2993d72e5fb22347c24b36d21a96f4c8d038d2';
 for(const number of ['317','318','319']){
  await page.goto('/site/#message='+number+'&env=prod');
  const panel=page.locator('.version-panel').filter({has:page.getByRole('heading',{name:'Продуктивный контур'})});
  const source=panel.locator('.document-source').filter({hasText:'23.06.2023'});
  await expect(source).toHaveCount(1);
  await expect(source).toContainText('версия 2.0');
  const document=catalog.document_sources.find(r=>r.number===number&&r.document_sha256===digest);
  await expect(source).toContainText(document.schema_reason);
  await expect(source.locator('.schema-diagnostic')).toHaveCount(2);
  await expect(source.locator('.schema-diagnostic').filter({hasText:'Архивное наблюдение'})).toHaveCount(1);
  const officialName=decodeURIComponent(new URL(document.document_url).pathname.split('/').pop());
  await expect(source.getByRole('link',{name:officialName})).toHaveAttribute('href',document.document_url);
  await expect(source.getByRole('link',{name:'Скачать DOCX'})).toHaveAttribute('href','documents/'+digest+'.docx');
  const pending=page.waitForEvent('download');await source.getByRole('link',{name:'Скачать DOCX'}).click();
  const bytes=await require('node:fs/promises').readFile(await (await pending).path());
  expect(sha(bytes)).toBe(digest);
  await expect(panel.locator('.card').filter({hasText:'Версия документа 2.7'}).getByRole('link',{name:'Открыть дерево'})).toBeVisible();
 }
});

test('partial module revocations show each QName beside the ready tree',async({page})=>{
 const row=catalog.revisions.find(r=>r.number==='10110'&&r.environment==='prod'&&r.ready&&r.listed_now);
 const source=(row.edition_sources||[row]).find(r=>r.listed_now!==false);
 const qnames=['urn:external:a','urn:external:b'];
 const document_sources=qnames.map((namespace,index)=>({
  title:source.title,document_sha256:source.document_sha256,document_url:source.document_url,
  document_download:source.document_download,document_filename:source.document_filename,listed_now:true,
  number:row.number,environment:row.environment,root:'r',namespace,
  schema_reason:'Неоднозначные схемы '+(index+1),
  schema_diagnostic:{code:'module_binding_revoked',root:'r',namespace,
   snapshot:sha('active snapshot'),resolved_schema_graph:sha(namespace)}
 }));
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',
  body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,document_sources})+';'}));
 await page.goto('/site/#'+new URLSearchParams({message:row.number,env:row.environment}));
 const panel=page.locator('.version-panel').filter({has:page.getByRole('heading',{name:'Продуктивный контур'})});
 const ready=panel.locator('.card').filter({has:page.getByRole('link',{name:'Открыть дерево',exact:true})})
  .filter({has:page.locator('.schema-diagnostic')});
 await expect(ready.locator('.schema-diagnostic')).toHaveCount(2);
 for(const [index,namespace] of qnames.entries()){
  const issue=ready.locator('.schema-diagnostic').filter({hasText:'{'+namespace+'}r'});
  await expect(issue).toContainText('Неоднозначные схемы '+(index+1));
 }
 await expect(ready.getByRole('link',{name:'Скачать XSD (ZIP)',exact:true})).toBeVisible();
 await expect(ready.getByRole('link',{name:'Проверить XML локально',exact:true})).toBeVisible();
});

test('grouped validator identifies all source documents and preserves a specific revision',async({page})=>{
 const original=catalog.revisions.find(r=>r.number==='14'&&r.environment==='prod'&&r.revision_id===r.display_revision_id&&r.edition_sources?.length>1);
 const source=original.edition_sources[0],docx=fs.readFileSync(path.join(__dirname,'../../site',source.document_download));
 // Reissue the same document with a ZIP comment. Its XML and embedded XSD stay byte-identical.
 const end=docx.lastIndexOf(Buffer.from('504b0506','hex'),docx.length-22),comment=Buffer.from('Additional source publication');
 expect(end).toBeGreaterThanOrEqual(0);
 expect(end+22+docx.readUInt16LE(end+20)).toBe(docx.length);
 const reissued=Buffer.concat([docx.subarray(0,end+22),comment]);reissued.writeUInt16LE(comment.length,end+20);
 const extraSha=sha(reissued);expect(extraSha).not.toBe(source.document_sha256);
 const downloadPath='/site/documents/'+extraSha+'.docx';
 const server=createServer((request,response)=>{
  if(request.url!==downloadPath){response.writeHead(404).end();return}
  response.writeHead(200,{'Content-Type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
   'Content-Disposition':'attachment; filename="additional-14.docx"','Content-Length':reissued.length});
  response.end(reissued);
 });
 const listening=once(server,'listening');server.listen(0,'127.0.0.1');await listening;
 try{
 const additional={...source,revision_id:sha('additional source revision'),document_version:'99.0',label_date:'17.11.2026',
  document_sha256:extraSha,document_url:'https://lk.sfr.gov.ru/specs_sedo/additional-14.docx',
  document_download:'http://127.0.0.1:'+server.address().port+downloadPath,document_filename:'additional-14.docx'};
 const revisions=catalog.revisions.map(r=>r.display_revision_id===original.revision_id
  ?{...r,edition_sources:[...r.edition_sources,additional]}:r);
 const single=revisions.find(r=>r.number==='10110'&&r.environment==='prod'&&r.ready);
 const fixture=revisions.map(r=>r===single?{...r,edition_sources:[single]}:r);
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',
  body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions:fixture})+';'}));
 const grouped=revisions.filter(r=>r.display_revision_id===original.revision_id);
 const main=grouped.find(r=>r.revision_id===r.display_revision_id),alias=grouped.find(r=>r.revision_id!==r.display_revision_id);
 for(const row of [main,alias,single]){
  await page.goto('/site/#'+new URLSearchParams({message:row.number,env:row.environment,rev:row.revision_id,validate:'1'}));
  const panel=page.getByRole('region',{name:'Локальная проверка XML',exact:true});
  await expect(panel).toContainText(row.revision_id.slice(0,8));
  if(row===single){await expect(panel).toContainText('документ '+row.document_version);continue}
  await panel.getByText('Источники схемы · '+row.edition_sources.length,{exact:true}).click();
  await expect(panel.getByText(row.revision_id,{exact:true})).toBeVisible();
  const links=await panel.locator('details li a').evaluateAll(items=>items.map(a=>[a.textContent,a.getAttribute('href')]));
  expect(links).toHaveLength(row.edition_sources.length);
  expect(links).toEqual(expect.arrayContaining(row.edition_sources.map(s=>['Версия '+(s.document_version||'не указана')+' · '+(s.label_date||'без даты'),s.document_download||s.document_url])));
  if(row===main){
   const download=page.waitForEvent('download');
   await panel.locator('a[href="'+additional.document_download+'"]').click();
   expect(sha(fs.readFileSync(await (await download).path()))).toBe(additional.document_sha256);
  }
  if(row===alias)await expect(panel).toContainText('Выбранный документ '+row.document_version);
 }
 }finally{await new Promise(resolve=>server.close(resolve))}
});

test('both namespace candidates link to the correct field or parent in either order',async({page})=>{
 const row=catalog.revisions.find(r=>r.number==='10110'&&r.environment==='prod'),{pack,examples}=namespaceCandidates(row);
 const bytes=gzipSync(JSON.stringify(pack)),revision={...row,validation_sha256:sha(bytes)};
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',
  body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions:catalog.revisions.map(r=>r===row?revision:r)})+';'}));
 await page.route('**/'+row.validation_download+'*',route=>route.fulfill({body:bytes}));
 await page.goto('/site/#'+new URLSearchParams({message:row.number,env:row.environment,rev:row.revision_id,validate:'1'}));
 const validator=page.frameLocator('.validator-frame');
 await validator.getByText('Или вставьте XML текстом',{exact:true}).click();
 for(const xml of examples){
  await validator.getByRole('textbox',{name:'XML для локальной проверки',exact:true}).fill(xml);
  await validator.getByRole('button',{name:'Проверить XML',exact:true}).click();
  const error=validator.locator('#errors li.error-card');
  await expect(error).toContainText('несколько возможных путей');
  const candidates=error.locator('code');
  await expect(candidates.filter({hasText:'{urn:other}benefit1'})).toHaveCount(1);
  await expect(candidates.filter({hasText:'{'+row.namespace+'}benefit1'})).toHaveCount(1);
  for(const [caption,path] of [['Посмотреть ограничения',pack.fields[2]],['Открыть родительский блок',pack.root]]){
   await error.getByRole('button',{name:caption,exact:true}).click();
   await expect(page.frameLocator('#frame').locator('.pathbox code')).toHaveText(path);
  }
 }
});
