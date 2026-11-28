const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..');
const catalog=JSON.parse(fs.readFileSync(path.join(root,'site/catalog-data.js'),'utf8').replace(/^window.SEDO_CATALOG=/,'').replace(/;\s*$/,''));
class Element{
 constructor(tag){this.tagName=tag;this.children=[];this.textContent='';this.dataset={};this.value='';this.listeners={};this.open=false}
 append(...children){this.children.push(...children)}
 add(child){this.children.push(child)}
 replaceChildren(...children){this.children=children}
 setAttribute(name,value){this[name]=value}
 addEventListener(name,listener){(this.listeners[name]??=[]).push(listener)}
 removeEventListener(name,listener){this.listeners[name]=(this.listeners[name]||[]).filter(item=>item!==listener)}
 dispatchEvent(event){for(const listener of this.listeners[event.type]||[])listener(event)}
}
const nodes=new Map(),document={createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text}),querySelector:()=>new Element('small'),getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id)}};
const context=vm.createContext({document,window:{SEDO_CATALOG:catalog,SedoReviewJournal:require('../web/review-journal.js')},URL,URLSearchParams,Intl,
 newestEditionFirst:()=>0,appendRelatedComparisons(){},comparisonDefaults:()=>({}),comparisonRoute:()=>'',updatesRoute:()=>''});
// Load the real rendering functions without starting the route/event loop.
vm.runInContext(fs.readFileSync(path.join(root,'web/journal.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(root,'web/app.js'),'utf8').split('window.addEventListener("message",')[0],context);
const text=node=>[node.textContent,...(node.children||[]).map(text)].join(' ');
const descendants=node=>[node,...(node.children||[]).flatMap(descendants)];
function render(number){vm.runInContext('renderVersions(messages.get('+JSON.stringify(number)+'),"prod")',context);return nodes.get('versions')}
{
 const published='https://lk-test.fss.ru/specs_sedo/tom-v3.4.0';
 const row={document_url:published,document_official_url:published+'.docx'};
 const link=vm.runInContext('revisionOrigin('+JSON.stringify(row)+')',context);
 assert.equal(link.href,published+'.docx','the official source opens the verified DOCX URL');
}
{
 const view=render('7810'),source=descendants(view).find(n=>n.className==='edition-source'&&text(n).includes('3.1'));
 assert.match(text(source),/Дата в названии документа на сайте СФР.*08\.06\.2026/);
 assert.match(text(source),/Дата публикации спецификации.*08\.06\.2025/);
 assert.match(text(source),/Период действия XML-схемы «С».*08\.06\.2026/);
 assert.match(text(source),/Номер версии XML-схемы.*3\.0/);
 assert.match(text(source),/Дата в названии документа на сайте СФР отличается от даты публикации спецификации в таблице DOCX/);
 assert.match(text(source),/17\.11\.2025/,'past versions stay visible with their own date');
 const revision=catalog.revisions.find(r=>r.number==='7810'&&r.environment==='prod');
 assert.ok(descendants(source).some(n=>n.tagName==='a'&&n.href===revision.document_url),'version table source opens its actual DOCX');
}
{
 const view=render('600'),panels=descendants(view).filter(n=>n.className==='version-panel');
 assert.match(text(panels[1]),/Является проектом, возможны модификации/);
 assert.doesNotMatch(text(panels[0]),/Является проектом/,'project warning belongs to the test contour');
 const registry=catalog.registry.find(r=>r.number==='600'&&r.environment==='test');
 assert.ok(descendants(panels[1]).some(n=>n.tagName==='a'&&n.href===registry.source));
 assert.ok(descendants(panels[1]).some(n=>n.tagName==='a'&&n.textContent==='Открыть дерево'));
 vm.runInContext('messages.get("600").rows.push({...messages.get("600").rows.find(r=>r.environment==="test"),label_date:"01.01.2000"})',context);
 const duplicateView=render('600'),testPanel=descendants(duplicateView).filter(n=>n.className==='version-panel')[1];
 assert.equal(descendants(testPanel).filter(n=>n.textContent==='Является проектом, возможны модификации').length,1,'repeated registry descriptions stay unique');
}
{
 const rows=()=>descendants(render('10110')).filter(n=>n.className?.split(' ').includes('registry-source'));
 const source=rows();
 assert.equal(source.length,1,'the same DOCX listed now and in history gives one record');
 assert.match(text(source[0]),/Актуальная запись.*Версия 2\.21.*22\.06\.2026/);
 assert.doesNotMatch(text(source[0]),/Описание сохранено в истории/);
 vm.runInContext(`messages.get("10110").rows.push({
  ...messages.get("10110").rows.find(r=>r.environment==="test"),
  document_version:"2.20",label_date:"01.06.2026",listed_now:false,
  source:"https://lk-test.fss.ru/specs_sedo/earlier.docx",
  document_sha256:"a".repeat(64),document_download:"documents/"+"a".repeat(64)+".docx"
 })`,context);
 const distinct=rows();
 assert.equal(distinct.length,2,'different documents with the same description retain both cards');
 assert.ok(distinct.every(n=>n.className.split(' ').includes('card')));
 assert.match(text(distinct[0]),/Актуальная запись.*Версия 2\.21/);
 assert.match(text(distinct[1]),/Описание сохранено в истории.*Версия 2\.20.*01\.06\.2026/);
 assert.ok(descendants(distinct[1]).some(n=>n.href==='https://lk-test.fss.ru/specs_sedo/earlier.docx'));
}
console.log('Catalog provenance verified: DOCX version history, distinct dates, project warning and ready tree.');

{
 const source={number:'122',environment:'test',source_type:'manual',label_date:'18.12.2025',document_history:[{version:'2.49',date:'18.12.2025',description:'Изменена структура ответа'}]};
 const host=new Element('section'); context.historyTestSource=source;context.historyTestHost=host;
 vm.runInContext('appendDocumentHistory(historyTestHost,historyTestSource)',context);
 const history=descendants(host).find(n=>n.tagName==='details');
 assert.ok(history,'document history is available as a disclosure');
 assert.doesNotMatch(text(history),/Изменена структура ответа/,'history entries are not rendered before opening');
 history.open=true;history.dispatchEvent({type:'toggle'});
 assert.match(text(host),/Дата спецификации при импорте.*18\.12\.2025/);
 assert.match(text(host),/Версия документа.*2\.49/);
 assert.match(text(host),/Дата изменения.*18\.12\.2025/);
 assert.match(text(host),/Изменена структура ответа/);
}

{
 const digest='a'.repeat(64), changes=[{version:'7.0',date:'17.11.2026',description:'Общая история исходного документа'}];
 catalog.document_histories={...(catalog.document_histories||{}),[digest]:changes};
 const source={number:'122',environment:'test',document_sha256:digest,document_download:'documents/'+digest+'.docx'};
 const host=new Element('section');context.historyTestSource=source;context.historyTestHost=host;
 vm.runInContext('appendDocumentHistory(historyTestHost,historyTestSource)',context);
 const history=descendants(host).find(n=>n.tagName==='details');
 assert.ok(history);
 assert.doesNotMatch(text(history),/Общая история исходного документа/,'shared document history is rendered on demand');
 history.open=true;history.dispatchEvent({type:'toggle'});
 assert.match(text(host),/Общая история исходного документа/);
 assert.ok(descendants(host).some(n=>n.tagName==='a'&&n.href===source.document_download));
}

{
 context.Option=function(text,value){const option=new Element('option');option.textContent=text;option.value=value;return option};
 const revision=catalog.revisions.find(r=>r.number==='14'&&r.environment==='prod');
 catalog.source_updates=[{event_type:'source_added',event_id:'history-title',revision_id:revision.revision_id,
  observed_at:'2026-11-17T08:00:00Z',source:{...revision,title:'Контрольное название обмена',document_version:'6.0',document_url:'https://lk.sfr.gov.ru/specs_sedo/v6.docx'}}];
 vm.runInContext(fs.readFileSync(path.join(root,'web/journal.js'),'utf8'),context);
 context.titleSearch=new URLSearchParams({q:'Контрольное название обмена'});
 vm.runInContext('renderUpdates(titleSearch)',context);
 const view=nodes.get('updates'),card=descendants(view).find(n=>n.dataset?.event==='history-title');
 assert.ok(card,'event is found by its original source title');
 assert.match(text(card),/Контрольное название обмена/);
 assert.ok(descendants(card).some(n=>n.tagName==='a'&&n.href.includes(revision.revision_id)),'source event retains its edition link');
}
