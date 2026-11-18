const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),catalog=JSON.parse(fs.readFileSync(path.join(root,'data/catalog.json'),'utf8'));
catalog.message_transitions=JSON.parse(fs.readFileSync(path.join(root,'data/message-transitions.json'),'utf8'));
const messages=new Map();for(const r of catalog.revisions){if(!messages.has(r.number))messages.set(r.number,{number:r.number,revisions:[]});messages.get(r.number).revisions.push({...r,ready:r.status==='matched'})}
const context=vm.createContext({catalog,messages,Map,Set,URLSearchParams,date:s=>s||'',window:{}});vm.runInContext(fs.readFileSync(path.join(root,'web/compare.js'),'utf8'),context);
for(const [from,to] of [['105','10510'],['10510','105'],['101','10110'],['10110','101']]){
 const m=messages.get(from),pool=context.comparisonPool(m);assert.ok(pool.some(r=>r.number===to),'counterpart '+from+' -> '+to);
 const pair=context.crossTypePair(m,to,'prod');assert.ok(pair,'same-contour pair');
 assert.equal(pool.find(r=>r.revision_id===pair.left).number,from);assert.equal(pool.find(r=>r.revision_id===pair.right).number,to);
 assert.equal(pool.find(r=>r.revision_id===pair.left).environment,'prod');assert.equal(pool.find(r=>r.revision_id===pair.right).environment,'prod');
}
assert.equal(context.crossTypePair(messages.get('105'),'10010','prod'),null,'unrelated type excluded');
{
 const m=messages.get('7810'),original=m.revisions,source=original.find(r=>r.environment==='test'&&r.ready);
 for(const label_date of ['16.11.2026',null])for(const ids of [['aaa','zzz'],['zzz','aaa']])for(const sourceCount of [0,1,2]){
  const edition_sources=Array.from({length:sourceCount},()=>({document_version:source.document_version,label_date,listed_now:true}));
  const archived={...source,revision_id:ids[0],display_revision_id:undefined,label_date,listed_now:false,edition_sources};
  const current={...source,revision_id:ids[1],display_revision_id:undefined,label_date,listed_now:true,edition_sources};
  for(const revisions of [[archived,current],[current,archived]]){
   m.revisions=revisions;
   assert.equal(context.crossTypePair(messages.get('78'),'7810','test').right,current.revision_id,'same-date cross-type comparison chooses the current edition');
   assert.equal(context.comparisonDefaults(m).right,current.revision_id,'initial comparison chooses the current edition');
   assert.equal([...revisions].sort(context.newestEditionFirst)[0],current,'edition cards use the same ordering');
  }
 }
 m.revisions=original;
}
{
 const m=messages.get('7810'),original=m.revisions,source=original.find(r=>r.environment==='test'&&r.ready);
 const row=(revision_id,document_version)=>({...source,revision_id,document_version,display_revision_id:undefined,label_date:'17.11.2026',listed_now:true});
 const old=row('a','2.9'),unknown=row('b',null),next=row('c','2.10');
 for(const revisions of [[old,unknown,next],[old,next,unknown],[unknown,old,next],[unknown,next,old],[next,old,unknown],[next,unknown,old]]){
  m.revisions=revisions;
  assert.equal(context.comparisonDefaults(m).right,'c','known versions rank consistently ahead of missing versions in every permutation');
  assert.equal(context.comparisonDefaults(m).left,'a');
  assert.equal(context.crossTypePair(messages.get('78'),'7810','test').right,'c');
 }
 m.revisions=original;
}
{
 const originals=messages.get('7810').revisions,old=originals.find(r=>r.environment==='test'&&r.ready);
 const newer={...old,revision_id:'newer-dated',label_date:'16.11.2026',display_revision_id:undefined};
 const undated={...old,revision_id:'undated',label_date:null,display_revision_id:undefined};
 context.date=s=>s||'Дата не указана';
 for(const ordered of [[undated,newer],[newer,undated]]){
  messages.get('7810').revisions=ordered;
  assert.equal(context.crossTypePair(messages.get('78'),'7810','test').right,newer.revision_id);
 }
 messages.get('7810').revisions=originals;
}
assert.equal(context.canCompareTypes('11000','11010'),false);assert.equal(context.canCompareTypes('105','10510'),true);assert.equal(context.canCompareTypes('10510','105'),true);
{
 const m=messages.get('7810'),original=m.revisions,source=original.find(r=>r.environment==='test'&&r.ready);
 for(const ids of [['aaa','zzz'],['zzz','aaa']])for(const grouped of [false,true]){
  const row=(id,version)=>({...source,revision_id:id,display_revision_id:undefined,label_date:'17.11.2026',listed_now:true,document_version:grouped?'1.0':version,...(grouped?{edition_sources:[{document_version:'99.0',label_date:'17.11.2026',listed_now:false},{document_version:version,label_date:'17.11.2026',listed_now:true}]}:{})});
  const old=row(ids[0],'2.9'),next=row(ids[1],'2.10');
  for(const revisions of [[old,next],[next,old]]){
   m.revisions=revisions;
   assert.equal(context.comparisonDefaults(m).right,next.revision_id,'natural document version determines same-date comparison direction');
   assert.equal(context.comparisonDefaults(m).left,old.revision_id);
   assert.equal(context.crossTypePair(messages.get('78'),'7810','test').right,next.revision_id,'cross-type links choose the latest current source version');
  }
 }
 m.revisions=original;
}
assert.ok(!context.comparisonPool(messages.get('11000')).some(r=>r.number==='11010'),'avoid unrelated sibling process');
console.log('Cross-type comparison verified: confirmed transitions, both directions, explicit contours and unrelated types excluded.');
{
 const vm=require('node:vm'),context={};vm.createContext(context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8').match(/^function date\(s\).*$/m)[0]+'\n'+fs.readFileSync(path.join(__dirname,'../web/compare.js'),'utf8'),context);
 const row=(id,environment,label_date)=>({revision_id:id,environment,label_date,ready:true});
 for(const env of ['prod','test']){
  const rows=[row('old',env,'01.01.2026'),row('new',env,'01.03.2026'),row('middle',env,'01.02.2026')];
  for(const revisions of [rows,[...rows].reverse()]){
   const pair=context.comparisonDefaults({revisions});
   assert.equal(pair.left,'middle');assert.equal(pair.right,'new');
  }
  const pair=context.comparisonDefaults({revisions:[row('missing',env,null),row('dated',env,'01.03.2026')]});
  assert.equal(pair.right,'dated');assert.notEqual(pair.left,pair.right);
 }
 const pair=context.comparisonDefaults({revisions:[row('prod','prod','01.03.2026'),row('test','test','01.04.2026')]});
 assert.equal(pair.left,'prod');assert.equal(pair.right,'test');
}

{
 const scripts=[],catalog={revisions:[{revision_id:'same',fields_sha256:'aaa'}]};
 const context=vm.createContext({catalog,window:{},document:{createElement:()=>({remove(){}}),head:{append(script){scripts.push(script);context.window.SEDO_FIELDS.same=[];script.onload()}}},Map,Promise});
 vm.runInContext(fs.readFileSync(path.join(root,'web/compare.js'),'utf8'),context);
 context.loadComparisonData('same');
 assert.equal(scripts[0].src,'fields/same.js?v=aaa');
 catalog.revisions[0].fields_sha256='bbb';
 context.loadComparisonData('same');
 assert.equal(scripts[1].src,'fields/same.js?v=bbb','rebuilt fields invalidate the in-memory cache too');
}

(async()=>{
 const catalog={revisions:[{revision_id:'edition',fields_sha256:'aaa'}]};
 const scripts=[],window={};
 const context=vm.createContext({catalog,window,document:{createElement:()=>({remove(){}}),head:{append:script=>scripts.push(script)}}});
 vm.runInContext(fs.readFileSync(path.join(root,'web/compare.js'),'utf8'),context);
 for(const [complete,error] of [
  [script=>script.onload(),/нет дерева/],
  [script=>{window.SEDO_FIELDS.edition={};script.onload()},/нет дерева/],
  [script=>script.onerror(),/Не удалось загрузить дерево/]
 ]){
  const promise=context.loadComparisonData('edition');
  assert.equal(context.loadComparisonData('edition'),promise,'pending reads share one tree request');
  complete(scripts.at(-1));
  await assert.rejects(promise,error);
 }
 const pending=context.loadComparisonData('edition'),tree=[{path:'field'}];
 window.SEDO_FIELDS.edition=tree;scripts.at(-1).onload();
 assert.equal(await pending,tree);
 assert.equal(context.loadComparisonData('edition'),pending);
 assert.equal(scripts.length,4,'every failure permits a fresh request on the same page');
 catalog.revisions[0].fields_sha256='bbb';
 const updated=context.loadComparisonData('edition');
 scripts.at(-1).onload();
 await assert.rejects(updated,/нет дерева/,'the previous version cannot replace missing registration for a new digest');
})().catch(error=>{console.error(error);process.exitCode=1});
