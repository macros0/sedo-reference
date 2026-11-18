const assert=require('node:assert/strict');
const {summarizeUpdates}=require('../summarize_updates.cjs');
const field=(path,extra={})=>({path,min:0,max:1,...extra});
const revision=(revision_id,environment='prod',extra={})=>({revision_id,number:'10110',environment,document_version:'3.0',ready:true,listed_now:true,...extra});
const event=(revision_id,previous_revision_id=null)=>({revision_id,previous_revision_id,initial_import:!previous_revision_id});
const fields={p1:[field('root'),field('root/removed'),field('root/changed')],p2:[field('root'),field('root/added'),field('root/added2'),field('root/changed',{min:1,max:2})]};
fields.t1=fields.p2;
const catalog={revisions:[revision('p1'),revision('p2'),revision('t1','test')],updates:[event('p1'),event('p2','p1'),event('t1')]};
const result=summarizeUpdates(catalog,id=>fields[id]);
{
 const {facets,bounds,enumWhitespace,union,unionFlags}=require('./fixtures/comparison-xsd.cjs');
 for(const [trees,changed] of [
  [facets.token,0],[facets.dominatedLength,0],[facets.string,1],[facets.effectiveLength,1],
  [enumWhitespace.inherited,1],[enumWhitespace.sameRestriction,1],[enumWhitespace.baseAlreadyCollapse,0],
  [union.qname.slice(0,2),1],[[union.qname[0],union.qname[2]],0],
  [bounds.dominated,0],[bounds.changed,1],[unionFlags.globalFixed.slice(0,2),1]
 ]){
  const rows=summarizeUpdates({revisions:[revision('old'),revision('next')],updates:[event('next','old')]},id=>trees[id==='old'?0:1]);
  assert.deepEqual(rows[0].comparison.counts,{added:0,removed:0,changed});
 }
 for(const [trees,changed,limited] of [
  [[union.mixed[0],union.mixed[0]],0,true],
  [union.mixed,1,true],
  [[union.qname[0],union.qname[2]],0,false],
  [unionFlags.unresolved,0,true],[[unionFlags.directDefault[0],unionFlags.directDefault[2]],0,false]
 ]){
  const [row]=summarizeUpdates({revisions:[revision('old'),revision('next')],updates:[event('next','old')]},id=>trees[id==='old'?0:1]);
  assert.equal(row.comparison.counts.changed,changed);
  assert.equal(row.comparison.limitedUnion??false,limited);
 }
}
{
 const initial={revision_id:'p1',snapshot:'early',observed_at:'2026-11-13T00:00:00Z'};
 const future={revision_id:'t1',snapshot:'late',observed_at:'2026-11-13T00:45:00Z'};
 const early={revisions:[revision('p1')],updates:[initial]};
 const later={revisions:[revision('p1'),revision('t1','test')],updates:[initial,future]};
 const saved=summarizeUpdates(early,id=>fields[id])[0];
 assert.deepEqual(summarizeUpdates(later,id=>fields[id])[0],saved,'later contour discovery cannot rewrite an old journal entry');
 assert.equal(saved.comparison.status,'no_baseline');
 for(const revisions of [later.revisions,[...later.revisions].reverse()]){
  const same={revisions,updates:[initial,{...future,snapshot:'early',observed_at:initial.observed_at}]};
  const rows=summarizeUpdates(same,id=>fields[id]);
  assert.equal(rows[0].comparison.left,'t1');assert.equal(rows[1].comparison.left,'p1','both contours in the same snapshot are known');
 }
 for(const reverse of [false,true]){
  const same={revisions:later.revisions,updates:[{revision_id:'p1',snapshot:'early'},{revision_id:'t1',snapshot:'early'}]};
  if(reverse)same.updates.reverse();
  const rows=summarizeUpdates(same,id=>fields[id]);
  assert.equal(rows.find(r=>r.revision_id==='p1').comparison.left,'t1','a snapshot with no observed_at knows both contours, in either event order');
  assert.equal(rows.find(r=>r.revision_id==='t1').comparison.left,'p1');
 }
 const untimed={revisions:later.revisions,updates:[{revision_id:'p1',snapshot:'early'},{revision_id:'t1',snapshot:'later'}]};
 assert.equal(summarizeUpdates(untimed,id=>fields[id])[0].comparison.status,'no_baseline','snapshot order supplies the boundary when timestamps are absent');
 const history={revisions:[revision('p1'),revision('t1','test',{listed_now:false,label_date:'01.01.2020'}),revision('future','test',{label_date:'01.01.2030'})],updates:[future,initial,{revision_id:'future',snapshot:'new',observed_at:'2026-11-14T00:00:00Z'}]};
 history.updates[0]={...future,observed_at:'2026-11-12T00:00:00Z'};
 assert.equal(summarizeUpdates(history,id=>fields[id]||fields.t1)[1].comparison.left,'t1','discovery time takes precedence over current membership and array order');
}
{
 for(const [base,old,next,facets] of [['decimal','01.00','1',[]],['token',' foo   bar ','foo bar',[]],['decimal','1','1',[['minInclusive','01.00']]]]){
  const rows=summarizeUpdates({revisions:[revision('old'),revision('next')],updates:[event('next','old')]},id=>[field('v',{base:'xs:'+base,flags:{fixed:id==='old'?old:next},values:[[id==='old'?old:next,'']],facets:id==='old'?facets:facets.map(([k])=>[k,'1'])})]);
  assert.deepEqual(rows[0].comparison.counts,{added:0,removed:0,changed:0},'typed equivalents do not enter journal counts');
 }
}
{
 const old=['01','+01.00','9007199254740992','01','1','1.0E0'],next=['1','1','9007199254740993','1','1.0E0','1'];
 const types=['integer','decimal','long','string','float','double'];
 const events=summarizeUpdates({revisions:[revision('old'),revision('next')],updates:[event('next','old')]},
  id=>types.map((type,i)=>field(type,{base:'xs:'+type,values:[[(id==='old'?old:next)[i],'']]})));
 assert.equal(events[0].comparison.counts.changed,2,'only a real numeric change and the changed string enter the journal');
}
{
 const versions={old:'1',next:'true'};
 const events=summarizeUpdates({revisions:[revision('old'),revision('next')],updates:[event('next','old')]},
  id=>[field('flag',{base:'xs:boolean',flags:{default:versions[id]}})]);
 assert.equal(events[0].comparison.counts.changed,0,'equivalent boolean defaults have identical journal counts');
}
{
 const model=groups=>[{...field('root'),contentModel:['sequence','a','b','c','d'],contentModelBounds:{kind:'sequence',min:'1',max:'1',children:groups.map(children=>({kind:'sequence',min:'0',max:'1',children}))}}];
 const versions={old:model([['a','b'],['c','d']]),next:model([['a'],['b','c'],['d']])};
 const changed=summarizeUpdates({revisions:[revision('old'),revision('next')],updates:[event('next','old')]},id=>versions[id]);
 assert.deepEqual(changed[0].comparison.counts,{added:0,removed:0,changed:1},'journal counts include changed conditions of joint presence');
}
assert.deepEqual(result[1].comparison.counts,{added:2,removed:1,changed:1});
assert.equal(result[1].comparison.left,'p1');assert.equal(result[1].comparison.right,'p2');assert.equal(result[1].comparison.basis,'previous');
assert.equal(result[0].comparison.left,'t1');assert.equal(result[0].comparison.right,'p1');assert.equal(result[0].comparison.basis,'other_contour');
assert.deepEqual(result[0].comparison.counts,{added:1,removed:2,changed:1});
assert.deepEqual(result[2].comparison.counts,{added:0,removed:0,changed:0});
assert.ok(!('comparison' in catalog.updates[0]),'canonical discovery events stay unchanged');
const first=summarizeUpdates({revisions:[revision('p1')],updates:[event('p1')]},id=>fields[id])[0];
assert.equal(first.comparison.status,'no_baseline');assert.equal(first.comparison.counts,null);
const unavailable=summarizeUpdates({revisions:[revision('p1','prod',{ready:false}),revision('p2')],updates:[event('p2','p1')]},id=>fields[id])[0];
assert.equal(unavailable.comparison.status,'unavailable');assert.equal(unavailable.comparison.counts,null);
assert.throws(()=>summarizeUpdates({revisions:[revision('p1','test'),revision('p2')],updates:[event('p2','p1')]},id=>fields[id]),/contour|контур/i);
const preferred=summarizeUpdates({revisions:[revision('p1'),revision('old-test','test',{listed_now:false,label_date:'31.12.2027'}),revision('t1','test',{label_date:'12.11.2026'})],updates:[{...event('old-test'),snapshot:'old',observed_at:'2026-11-10T00:00:00Z'},{...event('t1'),snapshot:'new',observed_at:'2026-11-12T00:00:00Z'},{...event('p1'),snapshot:'prod',observed_at:'2026-11-13T00:00:00Z'}]},id=>fields[id]||fields.t1)[2];
assert.equal(preferred.comparison.left,'t1','prefer the latest known counterpart, independently of current membership');
console.log('Journal summaries verified: counts, direction, same-contour predecessor, cross-contour baseline, zero vs unavailable, stable events.');

for(const [type,a,b] of [['dateTime','2026-11-17T12:00:00.123456789+03:00','2026-11-17T09:00:00.1234567890Z'],['hexBinary','aB','AB'],['base64Binary','QUJD','Q U J D']]){
 const rows=summarizeUpdates({revisions:[revision('old'),revision('next')],updates:[event('next','old')]},id=>[field('typed',{base:'xs:'+type,flags:{fixed:id==='old'?a:b},values:[[id==='old'?a:b,'']]})]);
 assert.equal(rows[0].comparison.counts.changed,0,type+' equivalent restrictions do not enter journal counts');
}

for(const [type,a,b,equivalent] of [
 ['date','2026-11-17Z','2026-11-17+00:00',true],
 ['date','2026-11-17','2026-11-17Z',false],
 ['time','09:00:00.0Z','09:00:00+00:00',true],
 ['time','09:00:00','09:00:00Z',false],
 ['gYear','2025+00:00','2025Z',true],
 ['gMonthDay','--09-20+00:00','--09-20Z',true],
 ['duration','P1Y','P12M',true],['duration','P1D','PT24H',true],
 ['duration','P1M','P30D',false],['duration','-PT0.000S','P0Y',true]
]){
 const rows=summarizeUpdates({revisions:[revision('old'),revision('next')],updates:[event('next','old')]},id=>[field('typed',{base:'xs:'+type,flags:{fixed:id==='old'?a:b,default:id==='old'?a:b},values:[[id==='old'?a:b,'']]})]);
 assert.deepEqual(rows[0].comparison.counts,{added:0,removed:0,changed:equivalent?0:1},type+' journal counts use the XSD value space');
}

{
 const versions={old:{nillable:'1'},next:{nillable:'true',processContents:'strict'}};
 const rows=summarizeUpdates({revisions:[revision('old'),revision('next')],updates:[event('next','old')]},id=>[field('flag',{flags:versions[id],values:id==='old'?[['й',''],['и\u0306','']]:[['и\u0306',''],['й',''] ]})]);
 assert.equal(rows[0].comparison.counts.changed,0,'journal suppresses semantic flags and enum reordering');
}
