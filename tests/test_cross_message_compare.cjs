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
