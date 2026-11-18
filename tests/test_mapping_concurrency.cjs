"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{constructor(tag){Object.assign(this,{tagName:tag,children:[],dataset:{},disabled:false,checked:false,textContent:'',className:'',value:'',hidden:false})}append(...items){this.children.push(...items)}replaceChildren(...items){this.children=[...items]}setAttribute(){}}
const records=new Map(),storage={getItem:k=>records.get(k)??null,setItem:(k,v)=>records.set(k,v),removeItem:k=>records.delete(k)},walk=e=>[e,...e.children.flatMap(walk)];
let tail=Promise.resolve(),locksTaken=0;
const locks={request(name,callback){assert.equal(name,'sedo-field-mappings:v1:new:old');locksTaken++;const pending=tail.then(()=>callback());tail=pending.catch(()=>{});return pending}};
const opts={left:{revision_id:'old'},right:{revision_id:'new'},before:['old/a','old/b','old/c'].map(path=>({path})),after:['new/a','new/b','new/c'].map(path=>({path})),isCurrent:()=>true};
function tab(useLocks=true){
 const ctx=vm.createContext({document:{createElement:tag=>new Element(tag)},localStorage:storage,navigator:useLocks?{locks}:{}});
 for(const name of ['comparison-export.js','mapping-draft.js','mapping-editor.js','comparison-mapping.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',name),'utf8'),ctx);
 const api=ctx.SedoMapping,host=new Element('host');let changes=0,ui;
 const find=cls=>walk(host).filter(e=>e.className.split(' ').includes(cls)),mount=()=>{host.replaceChildren();ui=api.mount(host,opts,api.store.load(opts),async()=>{changes++;return true})};
 const choose=(side,value)=>{const select=find('mapping-tag-choice-'+side)[0];select.value=value;select.onchange()};
 const save=()=>find('mapping-edit-form')[0].onsubmit({preventDefault(){}});
 const add=(beforePath,afterPath,comment='')=>{find('mapping-add')[0].onclick();choose('before',beforePath);choose('after',afterPath);find('mapping-edit-comment')[0].value=comment;save();find('mapping-editor-cancel')[0].onclick()};
 const edit=comment=>{find('mapping-suggest')[0].onclick();find('mapping-edit-row')[0].onclick();find('mapping-edit-comment')[0].value=comment;save();find('mapping-editor-cancel')[0].onclick()};
 return {api,mount,find,add,edit,apply:()=>find('mapping-apply')[0].onclick(),get changes(){return changes},get dirty(){return ui.hasUnapplied()}};
}
const pairs=t=>JSON.parse(JSON.stringify(t.api.store.load(opts).pairs));
(async()=>{
 {
  const saved=new Map(),backing={getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value)};
  let readsBlocked=false,failOnce=false;
  const access={getItem(key){if(readsBlocked)throw Error('temporary read error');return backing.getItem(key)},setItem(key,value){if(failOnce){failOnce=false;throw Error('quota')}backing.setItem(key,value)}};
  const recovery=tab().api.createStore(()=>access),remote=tab().api.createStore(()=>backing);
  const options={...opts,before:[...opts.before,{path:'old/skip'}],after:[...opts.after,{path:'new/skip'}]};
  const pair=(name,comment='')=>({beforePath:'old/'+name,afterPath:'new/'+name,comment});
  const take=(s,view=options)=>JSON.parse(JSON.stringify(s.load(view).pairs));
  const first=pair('a'),second=pair('b'),third=pair('c');
  recovery.save(options,[first]);failOnce=true;
  await recovery.apply(options,[first],[first,second],{base:{},draft:{after:['new/skip']}});
  await remote.apply(options,[first],[first,third],{base:{},draft:{before:['old/skip']}});
  readsBlocked=true;
  await recovery.apply(options,[first,second],[first,pair('b','local')]);
  assert.deepEqual(take(remote),[first,third],'failed read cannot overwrite a newer record');
  readsBlocked=false;
  const reverse={...options,left:options.right,right:options.left,before:options.after,after:options.before};
  await recovery.apply(reverse,take(recovery,reverse),take(recovery,reverse));
  assert.deepEqual(take(remote).sort((x,y)=>x.beforePath.localeCompare(y.beforePath)),[first,pair('b','local'),third]);
  assert.deepEqual(JSON.parse(JSON.stringify(remote.load(options).deleted)),{before:['old/skip'],after:['new/skip']});
  recovery.save(options,[first]);failOnce=true;
  await recovery.apply(options,[first],[pair('a','local')]);await remote.apply(options,[first],[pair('a','other')]);
  await assert.rejects(recovery.apply(options,take(recovery),[pair('a','local'),second]),/Конфликт/);
  assert.deepEqual(take(remote),[pair('a','other')]);assert.deepEqual(take(recovery),[pair('a','local')]);
  locksTaken=0;
 }
 const a=tab(),b=tab();a.mount();b.mount();a.add('old/a','new/a','A');b.add('old/b','new/b','B');await Promise.all([a.apply(),b.apply()]);
 assert.deepEqual(pairs(a),[{beforePath:'old/a',afterPath:'new/a',comment:'A'},{beforePath:'old/b',afterPath:'new/b',comment:'B'}],'simultaneous mounted applies must preserve disjoint edits');assert.equal(locksTaken,2,'normal apply must use the shared lock');
 const existing=pairs(a);
 for(const reverse of [false,true]){
  const shared=opts;
  const other=reverse?{...shared,left:shared.right,right:shared.left,before:shared.after,after:shared.before}:shared;
  const pair=(beforePath,afterPath)=>({beforePath,afterPath,comment:''});
  const orient=p=>reverse?pair(p.afterPath,p.beforePath):p;
  const original=pair('old/a','new/a'),replacement=pair('old/a','new/b'),extra=pair('old/b','new/c');
  a.api.store.save(shared,[original]);
  const first=a.api.store.load(shared),second=b.api.store.load(other);
  await a.api.store.apply(shared,first.pairs,[replacement]);
  await b.api.store.apply(other,second.pairs,[orient(replacement),orient(extra)]);
  assert.deepEqual(JSON.parse(JSON.stringify(a.api.store.load(shared).pairs)),[replacement,extra]);
 }
 a.api.store.save(opts,existing);
 a.mount();b.mount();a.edit('First tab');b.edit('Second tab');await a.apply();const saved=pairs(a),beforeChanges=b.changes;await b.apply();
 assert.deepEqual(pairs(b),saved,'conflicting comment must not overwrite current storage');assert.equal(b.changes,beforeChanges,'conflict must not refresh away the draft');assert.equal(b.dirty,true);const conflict=b.find('mapping-draft-errors')[0].textContent;assert.match(conflict,/другой вкладк|конфликт/i);assert.match(conflict,/Сохранено в другой вкладке:.*First tab/);assert.match(conflict,/В этом черновике:.*Second tab/);assert.equal(b.find('mapping-draft-notice')[0].hidden,false);assert.ok(b.find('mapping-explicit').length,'conflicting draft stays editable');b.find('mapping-discard')[0].onclick();assert.deepEqual(pairs(b),saved,'cancelling a conflicting draft must preserve the competing saved variant');assert.equal(b.find('mapping-draft-notice')[0].hidden,true);
 a.mount();b.mount();a.edit('Changed before delete');b.find('mapping-suggest')[0].onclick();b.find('mapping-remove-row')[0].onclick();await a.apply();await b.apply();assert.equal(pairs(a)[0].comment,'Changed before delete','delete versus edit is a conflict');assert.equal(b.dirty,true);
 a.api.store.clear(opts);a.mount();b.mount();a.add('old/a','new/a','Saved endpoint');b.add('old/a','new/b','Draft endpoint');await a.apply();await b.apply();assert.equal(pairs(a).length,1,'different targets for the same concurrently edited source need an explicit decision');assert.equal(b.dirty,true);const endpointConflict=b.find('mapping-draft-errors')[0].textContent;assert.match(endpointConflict,/Сохранено в другой вкладке:.*old\/a → new\/a.*Saved endpoint/);assert.match(endpointConflict,/В этом черновике:.*old\/a → new\/b.*Draft endpoint/);
 a.api.store.clear(opts);const legacyA=tab(false),legacyB=tab(false);legacyA.mount();legacyB.mount();legacyA.add('old/a','new/a');legacyB.add('old/b','new/b');await legacyA.apply();await legacyB.apply();assert.equal(pairs(a).length,2,'compatibility path also merges stale drafts');
 // Reset uses the same lock as apply. Neither may mutate the shared record
 // while another holder owns it; a later reset must not be undone by an apply.
 a.api.store.save(opts,[{beforePath:'old/a',afterPath:'new/a',comment:'Existing'}]);a.mount();b.mount();a.add('old/b','new/b','Queued apply');
 let release;const key=a.api.storageKey(opts.left,opts.right),held=locks.request(key,()=>new Promise(resolve=>{release=resolve}));await Promise.resolve();const resetChanges=b.changes,applying=a.apply(),resetButton=b.find('mapping-controls')[0].children[1],resetting=resetButton.onclick();
 assert.equal(pairs(a).length,1,'queued reset must not clear storage while an apply lock is held');assert.equal(resetButton.disabled,true,'pending reset blocks duplicate commands');assert.equal(b.changes,resetChanges,'queued reset must not refresh the page before acquiring its lock');release();await held;await Promise.all([applying,resetting]);assert.deepEqual(pairs(a),[],'reset queued after apply must leave mappings cleared');
 // Navigating away while waiting must cancel both queued transaction types.
 a.api.store.save(opts,[{beforePath:'old/a',afterPath:'new/a',comment:'Keep'}]);a.mount();b.mount();a.add('old/b','new/b','Stale draft');const snapshot=pairs(a),changedBefore=b.changes;
 const heldAgain=locks.request(key,()=>new Promise(resolve=>{release=resolve}));await Promise.resolve();const staleApply=a.apply(),staleReset=b.find('mapping-controls')[0].children[1].onclick();opts.isCurrent=()=>false;release();await heldAgain;await Promise.all([staleApply,staleReset]);assert.deepEqual(pairs(a),snapshot,'queued stale apply and reset must not alter saved mappings');assert.equal(b.changes,changedBefore,'queued stale reset must not refresh another comparison');opts.isCurrent=()=>true;
 const normal={...opts,before:['old','old/note','old/sibling'].map(path=>({path})),after:['new','new/note','new/sibling'].map(path=>({path}))};
 const reverse={...normal,left:normal.right,right:normal.left,before:normal.after,after:normal.before};
 for(const deletionOpts of [normal,reverse]){
  const pairOpts=deletionOpts===normal?reverse:normal;
  const pair=({before,after},name)=>({beforePath:before[0].path+name,afterPath:after[0].path+name,comment:''});
  // Both views read the same saved parent before either edits its draft.
  for(const deletionFirst of [true,false]){
   a.api.store.save(normal,[pair(normal,'')]);
   const deleteBase=a.api.store.load(deletionOpts),pairBase=b.api.store.load(pairOpts);
   const deleted=deletionOpts===normal?{before:['old/note'],after:[]}:{before:[],after:['old/note']};
   const remove=()=>a.api.store.apply(deletionOpts,deleteBase.pairs,deleteBase.pairs,{base:deleteBase.deleted,draft:deleted});
   const include=()=>b.api.store.apply(pairOpts,pairBase.pairs,[...pairBase.pairs,pair(pairOpts,'/note')],{base:pairBase.deleted,draft:pairBase.deleted});
   await (deletionFirst?remove:include)();
   const untouched=JSON.stringify(a.api.store.load(normal));
   await assert.rejects(deletionFirst?include:remove,/другой вкладке.*old\/note/s,'a deletion cannot race with a new pair for that endpoint');
   assert.equal(JSON.stringify(a.api.store.load(normal)),untouched);
   if(deletionFirst){
    const fresh=b.api.store.load(pairOpts);
    await b.api.store.apply(pairOpts,fresh.pairs,[...fresh.pairs,pair(pairOpts,'/note')],{base:fresh.deleted,draft:fresh.deleted});
    assert.equal(b.api.store.load(pairOpts).pairs.length,2,'a user who saw the exclusion can add an explicit override');
   }
  }
 }
 {
  const resumed=tab(),other=tab(),original=[{beforePath:'old/a',afterPath:'new/a',comment:'Original'}];
  resumed.api.store.save(opts,original);resumed.mount();other.mount();
  resumed.add('old/b','new/b','Retained draft');other.add('old/c','new/c','Other tab');await other.apply();
  resumed.mount();assert.equal(resumed.dirty,true,'navigation remount retains the unapplied selection');await resumed.apply();
  assert.deepEqual(pairs(resumed).map(p=>p.comment).sort(),['Original','Other tab','Retained draft'],'resumed draft merges disjoint changes with its original baseline');
  resumed.api.store.save(opts,original);resumed.mount();other.mount();resumed.edit('Draft edit');other.edit('Concurrent edit');await other.apply();
  resumed.mount();await resumed.apply();
  assert.equal(pairs(resumed)[0].comment,'Concurrent edit','navigation must not replace the old baseline and hide a conflicting comment');
  assert.match(resumed.find('mapping-draft-errors')[0].textContent,/другой вкладке/);assert.equal(resumed.dirty,true);
  resumed.find('mapping-discard')[0].onclick();resumed.edit('Deliberate replacement');await resumed.apply();
  assert.equal(pairs(resumed)[0].comment,'Deliberate replacement','a fresh preview after explicit discard adopts the currently saved baseline');
 }
 console.log('Mounted mapping concurrency: shared locks, disjoint merge, comment/delete/endpoint conflicts, preserved and resumed drafts and compatibility merge passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
