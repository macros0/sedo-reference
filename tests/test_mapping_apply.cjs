"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),core=require('../web/compare-core.js');
const exporter=require('../web/comparison-export.js');
const root=path.resolve(__dirname,'..'),catalog=JSON.parse(fs.readFileSync(path.join(root,'data/catalog.json'),'utf8'));
const rev=n=>({...catalog.revisions.find(r=>r.number===n&&r.environment==='prod'&&r.status==='matched'),ready:true}),left=rev('105'),right=rev('10510'),fields=r=>JSON.parse(fs.readFileSync(path.join(root,'data/fields/'+r.revision_id+'.json'),'utf8')).fields,before=fields(left),after=fields(right);
const all=before.map(f=>{const suffix=f.path.replace(/^approveSocialAssistResult\/?/,'');return{beforePath:f.path,afterPath:'benefitResponseResult'+(suffix?'/'+(suffix==='socialAssistNum'?'processNum':suffix==='registryNum'?'batchNo':suffix):''),comment:''}});
const mapping=require('../web/comparison-mapping.js'),records=new Map();
const storage={getItem:k=>records.get(k)??null,setItem:(k,v)=>records.set(k,v),removeItem:k=>records.delete(k)};
const store=mapping.createStore(()=>storage),storeOpts={left,right,before,after};
let exported;let saved=all.filter(p=>!p.beforePath.endsWith('/socialAssistNum')),onChange,scrolls=0;
class Element{
 constructor(tag,cls='',text=''){Object.assign(this,{tagName:tag,className:cls,textContent:text,children:[],dataset:{},parent:null,value:'',hidden:false})}
 append(...items){for(const item of items){item.parent=this;this.children.push(item)}}
 replaceChildren(...items){this.children.forEach(c=>c.parent=null);this.children=[];this.append(...items)}
 remove(){if(this.parent){this.parent.children=this.parent.children.filter(c=>c!==this);this.parent=null}}
 add(item){this.append(item)}setAttribute(){}scrollIntoView(){scrolls++}
 querySelector(selector){return walk(this).find(e=>selector.startsWith('.')?e.className.split(' ').includes(selector.slice(1)):e.tagName===selector)||null}
 get isConnected(){return this===section||!!this.parent?.isConnected}
}
const walk=e=>[e,...e.children.flatMap(walk)],section=new Element('section'),node=(tag,cls='',text='')=>new Element(tag,cls,text),messages=new Map([['105',{number:'105',revisions:[left]}],['10510',{number:'10510',revisions:[right]}]]),hash='#'+new URLSearchParams({message:'10510',compare:'1',left:left.revision_id,right:right.revision_id});
const context=vm.createContext({window:{},localStorage:storage,document:{createElement:tag=>node(tag),createTextNode:text=>node('text','',text)},location:{hash,href:'https://example.test/'+hash},URL,URLSearchParams,Map,Set,Option:class extends Element{constructor(text,value){super('option','',text);this.value=value}},catalog:{message_transitions:{pairs:[{previous:'105',current:'10510'}]}},messages,$:()=>section,node,title:()=>'',label:r=>r.document_version,date:v=>v||'',normalize:v=>String(v||'').toLowerCase(),route:()=>'',anchor:text=>node('a','',text),documentDownload:()=>node('a'),revisionOrigin:()=>node('a'),appendEditionSources:()=>{},SedoCompare:core,SedoComparisonExport:{...exporter,download:async options=>{exported=options}}});
for(const name of ['mapping-draft.js','mapping-editor.js','comparison-mapping.js','compare.js'])vm.runInContext(fs.readFileSync(path.join(root,'web',name),'utf8'),context);
context.loadComparisonData=async id=>id===left.revision_id?before:after;
const mount=context.SedoMapping.mount;
context.SedoMapping.mount=(host,options,saved,callback,view)=>{onChange=callback;return mount(host,options,saved,callback,view)};
const params=new URLSearchParams(hash.slice(1)),m=messages.get('10510'),count=cls=>section.querySelector('.'+cls)?.textContent;
(async()=>{
 store.save(storeOpts,saved);await context.renderComparison(m,params);assert.equal(count('removed'),'Удалено · 1');
 saved=all;store.save(storeOpts,saved);const result=await onChange();assert.equal(count('removed'),'Удалено · 0');assert.equal(count('added'),'Добавлено · 3');assert.equal(result,true,'apply callback must await a confirmed refreshed comparison');
 await section.querySelector('.comparison-export').children[0].onclick();assert.equal(exported.mappings.length,16,'export must use refreshed saved pairs');
 assert.ok(walk(section).some(e=>e.className==='mapping-applied-feedback'&&e.textContent.includes('16')));assert.ok(scrolls>0,'new counts should become visible after apply');
 const undoButtons=()=>walk(section).filter(e=>e.className.split(' ').includes('mapping-undo'));
 assert.equal(undoButtons().length,16,'each manual result must offer undo');
 const socialCard=()=>walk(section).find(e=>e.tagName==='article'&&walk(e).some(n=>n.tagName==='code'&&n.textContent==='approveSocialAssistResult/socialAssistNum → benefitResponseResult/processNum'));
 const socialUndo=()=>walk(socialCard()).find(e=>e.className.split(' ').includes('mapping-undo'));
 section.querySelector('.mapping-add').onclick();await socialUndo().onclick();assert.equal(store.load(storeOpts).pairs.length,16,'undo must not discard an unapplied draft');assert.ok(walk(socialCard()).some(e=>e.textContent.includes('черновик')));section.querySelector('.mapping-discard').onclick();
 await socialUndo().onclick();assert.equal(store.load(storeOpts).pairs.length,15);assert.equal(count('removed'),'Удалено · 1');assert.equal(count('added'),'Добавлено · 4');assert.equal(undoButtons().length,15);assert.ok(walk(section).some(e=>e.className==='mapping-applied-feedback'&&e.textContent.includes('отменено')));
 assert.equal(store.load({...storeOpts,left:right,right:left,before:after,after:before}).pairs.length,15,'undo persists in reverse comparison');
 const stale=undoButtons()[0],snapshot=JSON.stringify(store.load(storeOpts).pairs);context.location.hash='#message=101';await stale.onclick();assert.equal(JSON.stringify(store.load(storeOpts).pairs),snapshot,'stale card must not change another comparison');context.location.hash=hash;
 // Keep the user's current search and change filter after cancelling a card.
 const filters=()=>walk(section.querySelector('.searchbar'));
 store.save(storeOpts,all);await context.renderComparison(m,params);const search=filters().find(e=>e.tagName==='input'),kind=filters().find(e=>e.tagName==='select');search.value='socialAssistNum';search.oninput();kind.value='changed';kind.onchange();assert.equal(undoButtons().length,2,'the parent order change and matching child are searchable');await socialUndo().onclick();assert.equal(filters().find(e=>e.tagName==='input').value,'socialAssistNum');assert.equal(filters().find(e=>e.tagName==='select').value,'changed');assert.equal(undoButtons().length,0);
 // Removing a parent cancels only that pair; its explicitly saved descendants survive.
 await context.renderComparison(m,params);const parent=walk(section).find(e=>e.tagName==='article'&&walk(e).some(n=>n.tagName==='code'&&n.textContent==='approveSocialAssistResult → benefitResponseResult'));await walk(parent).find(e=>e.className.split(' ').includes('mapping-undo')).onclick();assert.equal(store.load(storeOpts).pairs.length,14);assert.ok(store.load(storeOpts).pairs.some(p=>p.beforePath==='approveSocialAssistResult/errorList/error/code'));
 // Split mappings: undo one link without removing the other link from that source.
 store.save(storeOpts,[{beforePath:'approveSocialAssistResult/status',afterPath:'benefitResponseResult/status',comment:''},{beforePath:'approveSocialAssistResult/status',afterPath:'benefitResponseResult/processNum',comment:''}]);await context.renderComparison(m,params);const split=walk(section).find(e=>e.tagName==='article'&&walk(e).some(n=>n.tagName==='code'&&n.textContent==='approveSocialAssistResult/status → benefitResponseResult/processNum'));await walk(split).find(e=>e.className.split(' ').includes('mapping-undo')).onclick();assert.equal(store.load(storeOpts).pairs.length,1);assert.equal(store.load(storeOpts).pairs[0].afterPath,'benefitResponseResult/status');
 // An unchanged manually matched pair also offers undo; ordinary deltas do not.
 context.loadComparisonData=async()=>[{path:'same',name:'same',min:1,max:1,doc:''}];store.save({...storeOpts,before:[{path:'same'}],after:[{path:'same'}]},[{beforePath:'same',afterPath:'same',comment:''}]);await context.renderComparison(m,params);assert.equal(undoButtons().length,1);await undoButtons()[0].onclick();assert.equal(undoButtons().length,0);assert.equal(store.load({...storeOpts,before:[{path:'same'}],after:[{path:'same'}]}).pairs.length,0);context.loadComparisonData=async id=>id===left.revision_id?before:after;
 // Undo and a different tab's apply must share the same lock and merge from
 // the rendered baseline, including rejecting removal of a concurrently edited pair.
 const navigatorDescriptor=Object.getOwnPropertyDescriptor(globalThis,'navigator');let queue=Promise.resolve(),release,lockRequests=0;const key=mapping.storageKey(left,right),locks={request(name,callback){assert.equal(name,key);lockRequests++;const pending=queue.then(callback);queue=pending.catch(()=>{});return pending}};
 Object.defineProperty(globalThis,'navigator',{value:{locks},configurable:true});
 context.navigator={locks};
 try{
  const originalPair={...all.find(p=>p.beforePath.endsWith('/socialAssistNum')),comment:'Rendered'},extra=all.find(p=>p.beforePath.endsWith('/status'));store.save(storeOpts,[originalPair]);await context.renderComparison(m,params);
  const held=locks.request(key,()=>new Promise(resolve=>{release=resolve}));await Promise.resolve();const applying=store.apply(storeOpts,[originalPair],[originalPair,extra]),undoing=socialUndo().onclick();assert.equal(store.load(storeOpts).pairs.length,1,'undo must wait for the same shared lock as apply');assert.equal(lockRequests,3);release();await held;await Promise.all([applying,undoing]);assert.equal(store.load(storeOpts).pairs.length,1);assert.equal(store.load(storeOpts).pairs[0].beforePath,extra.beforePath,'undo must preserve a different tab\'s added pair');
  store.save(storeOpts,[originalPair]);await context.renderComparison(m,params);store.save(storeOpts,[{...originalPair,comment:'Changed in other tab'}]);await socialUndo().onclick();assert.equal(store.load(storeOpts).pairs[0].comment,'Changed in other tab','undo must not delete a concurrently edited pair');assert.ok(walk(socialCard()).some(n=>n.textContent.includes('Changed in other tab')&&n.textContent.includes('соответствие удалено')),'undo conflict shows saved and removal variants');
  await context.renderComparison(m,params);const heldDraft=locks.request(key,()=>new Promise(resolve=>{release=resolve}));await Promise.resolve();const draftUndo=socialUndo().onclick();section.querySelector('.mapping-add').onclick();assert.equal(section.querySelector('.mapping-edit-form'),null,'undo prevents opening a draft while waiting for the lock');release();await heldDraft;await draftUndo;assert.equal(store.load(storeOpts).pairs.length,0);
  store.save(storeOpts,[originalPair]);await context.renderComparison(m,params);const snapshot=JSON.stringify(store.load(storeOpts).pairs),heldAgain=locks.request(key,()=>new Promise(resolve=>{release=resolve}));await Promise.resolve();const staleUndo=socialUndo().onclick();context.location.hash='#message=101';release();await heldAgain;await staleUndo;assert.equal(JSON.stringify(store.load(storeOpts).pairs),snapshot,'undo queued before navigation cannot change the old comparison');context.location.hash=hash;
 }finally{delete context.navigator;if(navigatorDescriptor)Object.defineProperty(globalThis,'navigator',navigatorDescriptor);else delete globalThis.navigator}
 context.SedoMapping.mount=()=>{throw Error('fixture render failure')};const failed=await context.renderComparison(m,params,true);assert.equal(failed,false);assert.ok(walk(section).some(e=>e.textContent.includes('fixture render failure')),'failure after removing loading state must remain visible');
 console.log('Apply and undo: immediate refresh, one-pair persistence, parent/split/unchanged cards, draft/stale guards, preserved filters, export and renderer errors passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
