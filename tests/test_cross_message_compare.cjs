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
assert.equal(context.canCompareTypes('11000','11010'),false);assert.equal(context.canCompareTypes('105','10510'),true);assert.equal(context.canCompareTypes('10510','105'),true);
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
