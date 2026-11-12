const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {replacements}=require('../web/message-transitions.js');
const root=path.resolve(__dirname,'..');
const catalog=JSON.parse(fs.readFileSync(path.join(root,'data/catalog.json'),'utf8'));
catalog.message_transitions=JSON.parse(fs.readFileSync(path.join(root,'data/message-transitions.json'),'utf8'));
const find=(n,env)=>replacements(n,catalog,env);
assert.equal(find('109','prod')[0].current,'10910');
assert.equal(find('109','test')[0].current,'10910');
assert.equal(find('109')[0].production_retired_at,'2026-09-19T10:00:00+03:00');
assert.deepEqual(find('110').map(r=>r.current),['11000','11010'],'110 has two functional branches');
assert.match(find('110')[0].scope,/ВНиМ/);
assert.match(find('110')[1].scope,/Возмещение/);
for(const n of ['10','84','85','104','106','110','120','121','122']){
 assert.ok(find(n).length,'documented counterpart for '+n);
 assert.ok(find(n).every(r=>!r.production_retired_at),'do not extend the September cutoff to '+n);
 assert.ok(find(n).every(r=>r.source_url.endsWith('.docx')),'link module documentation');
}
for(const n of ['10910','125','32','33','34','86','87','88','111','115','999'])assert.deepEqual(find(n),[],'no guessed replacement for '+n);
const without=(number,env)=>({...catalog,revisions:catalog.revisions.filter(r=>r.number!==number||(env&&r.environment===env)),registry:catalog.registry.filter(r=>r.number!==number||(env&&r.environment===env))});
assert.deepEqual(replacements('109',without('10910')),[],'no broken link');
assert.deepEqual(replacements('109',without('10910','test'),'prod'),[],'keep contour scope');
assert.equal(replacements('109',without('10910','test'),'test')[0].current,'10910');
assert.deepEqual(replacements('110',without('11010')).map(r=>r.current),['11000'],'keep available branch');
assert.deepEqual(replacements('109',{...catalog,message_transitions:undefined}),[]);
for(const pair of catalog.message_transitions.pairs){
 assert.notEqual(pair.previous,pair.current);
 for(const env of ['prod','test'])assert.ok(find(pair.previous,env).some(r=>r.current===pair.current),pair.previous+' -> '+pair.current+' in '+env);
}
assert.equal(catalog.message_transitions.pairs.length,20);
console.log('ECP transitions verified: 20 mappings, split 110, scoped cutoff and sources, contour scope, missing targets, unchanged codes.');
