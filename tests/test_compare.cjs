const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {compare,hasUnresolvedUnionValues}=require("../web/compare-core.js");
const {facets:facetTrees,bounds,enumWhitespace,union:unionTrees,unionFlags}=require('./fixtures/comparison-xsd.cjs');
for(const [name,[before,after,alias]] of Object.entries(unionFlags)){
 const changes=compare(before,after).flatMap(row=>row.changes);
 if(name==='unresolved'){
  assert.ok(before.some(hasUnresolvedUnionValues),'a fixed union value with unproved member limits coverage');
  assert.deepEqual(changes,[],'unproved values fall back to their lexical spelling');
  continue;
 }
 assert.deepEqual(changes.map(change=>change.key),['flags'],name+' expanded QName changes');
 assert.match(JSON.stringify(changes[0].before),/p:X.*urn:A/);
 assert.match(JSON.stringify(changes[0].after),/p:X.*urn:B/);
 assert.deepEqual(compare(before,alias),[],name+' equivalent prefix');
 assert.equal(before.some(hasUnresolvedUnionValues),false);
}
for(const [name,trees] of Object.entries(bounds)){
 const expected=['dominated','integer'].includes(name)?[]:['facets'];
 assert.deepEqual(compare(...trees).flatMap(row=>row.changes.map(change=>change.key)),expected,name);
 assert.deepEqual(compare(...trees.toReversed()).flatMap(row=>row.changes.map(change=>change.key)),expected,name);
}
for(const [name,trees] of Object.entries(facetTrees)){
 const expected=['string','effectiveLength','effectiveDigits'].includes(name)?['facets']:[];
 assert.deepEqual(compare(...trees).flatMap(row=>row.changes.map(change=>change.key)),expected,name);
}
for(const [name,trees] of Object.entries(enumWhitespace)){
 const expected=name==='baseAlreadyCollapse'?[]:['values'];
 assert.deepEqual(compare(...trees).flatMap(row=>row.changes.map(change=>change.key)),expected,name);
 assert.deepEqual(compare(...trees.toReversed()).flatMap(row=>row.changes.map(change=>change.key)),expected,name);
}
{
 const [a,b,alias]=unionTrees.qname;
 const change=compare(a,b).flatMap(row=>row.changes);
 assert.deepEqual(change.map(c=>c.key),['values']);
 assert.match(JSON.stringify(change[0].before),/p:read.*urn:A/);
 assert.match(JSON.stringify(change[0].after),/p:read.*urn:B/);
 assert.deepEqual(compare(a,alias),[]);
 assert.deepEqual(compare(...unionTrees.stringFirst),[]);
 assert.deepEqual(compare(...unionTrees.mixed).flatMap(row=>row.changes.map(c=>c.key)),['values']);
 assert.deepEqual(compare(...unionTrees.mixedSameLexeme).flatMap(row=>row.changes.map(c=>c.key)),['values'],
  'an unresolved duplicate does not mask a known QName on both sides');
 const [integer,integerAlias,otherInteger]=unionTrees.integer;
 assert.deepEqual(compare(integer,integerAlias),[]);
 assert.deepEqual(compare(integer,otherInteger).flatMap(row=>row.changes.map(c=>c.key)),['values']);
 const [whole,decimal,fraction]=unionTrees.mixedNumeric;
 assert.deepEqual(compare(whole,decimal),[],'integer and decimal union members share the decimal value space');
 assert.deepEqual(compare(whole,fraction).flatMap(row=>row.changes.map(c=>c.key)),['values']);
 const [literal,token,differentToken]=unionTrees.mixedString;
 assert.deepEqual(compare(literal,token),[],'string-derived members share a value space after applying their whitespace rules');
 assert.deepEqual(compare(literal,differentToken).flatMap(row=>row.changes.map(c=>c.key)),['values']);
 const legacy=structuredClone(a);
 delete legacy[0].valueQNames;
 delete legacy[0].unionValueTypes;
 assert.deepEqual(compare(legacy,a),[]);
 assert.deepEqual(compare(a,legacy),[]);
}
function field(name,changes={}){return {name,path:"root/"+name,parent:0,children:[],kind:"element",type:"xs:string",base:"xs:string",min:"0",max:"1",group:"",ns:"urn:test",flags:{},facets:[],values:[],doc:"Описание",...changes}}
for(const minimum of ['00',' 0 ']){
 const parent=field('root',{path:'root',parent:-1,min:'1',children:[1],contentModel:['sequence','a'],contentModelBounds:{kind:'sequence',min:'0',max:'1',children:['a']}});
 const legacy={...parent,contentModelBounds:{kind:'sequence',min:minimum,max:'01',children:['a']}};
 assert.deepEqual(compare([parent,field('a',{group:'sequence [0..1]'})],[legacy,field('a',{min:minimum,max:'01',group:'sequence ['+minimum+'..01]'})]),[],'legacy occurrence aliases compare by integer value');
}
const left=[field("old"),field("changed")],right=[field("added"),field("changed",{min:"1",facets:[["maxLength","12"]]})];
for(const kind of ['sequence','all','choice','group']){
 assert.deepEqual(compare([field('a',{group:kind+' [01..1]'})],[field('a')]),[],'neutral legacy '+kind+' bounds are equivalent to omitted bounds');
 assert.deepEqual(compare([field('a',{group:'Выбор 1, вариант 1 / '+kind+' [01..1]'})],[field('a',{group:'Выбор 1, вариант 1'})]),[],'choice branch remains after neutral group removal');
}
{
 const before='sequence [01..1] / Выбор 1, вариант 1 ИЛИ sequence [01..1] / Выбор 1, вариант 2';
 const after='Выбор 1, вариант 1 ИЛИ Выбор 1, вариант 2';
 assert.deepEqual(compare([field('a',{group:before})],[field('a',{group:after})]),[],'neutral groups are removed independently from each coalesced branch');
}
{
 const typed=(base,value,facets=[])=>field('typed',{base:'xs:'+base,flags:{fixed:value,default:value},facets,values:[[value,'Описание']]});
 const equivalent=[['decimal','01.00','1'],['token',' foo   bar ','foo bar'],['normalizedString','a\tb','a b'],['string',' a\t b ','a b',[['whiteSpace','collapse']]]];
 for(const [base,a,b,facets] of equivalent){
  const before=typed(base,a,facets),after=typed(base,b,facets);
  assert.deepEqual(compare([before],[after]),[],base+' uses typed fixed/default/enumeration values');
  assert.deepEqual(compare([field('union',{alternatives:[before]})],[field('union',{alternatives:[after]})]),[],base+' union constraints');
 }
 for(const [base,a,b] of [['decimal','1','2'],['integer','9007199254740992','9007199254740993'],['string',' foo ','foo'],['normalizedString',' a ','a']]){
  const changes=compare([typed(base,a)],[typed(base,b)])[0].changes;
  assert.deepEqual(changes.map(c=>c.key),['flags','values']);
  assert.equal(changes[0].before.fixed,a,'display retains original lexical spelling');
 }
 for(const base of ['decimal','integer']){
  const a=typed(base,'1',[['minInclusive','+01'],['maxExclusive','002']]),b=typed(base,'1',[['minInclusive','1'],['maxExclusive','2']]);
  assert.deepEqual(compare([a],[b]),[],'numeric bounds compare in value space');
  assert.deepEqual(compare([field('union',{alternatives:[a]})],[field('union',{alternatives:[b]})]),[]);
  assert.equal(compare([a],[{...b,facets:[['minInclusive','2'],['maxExclusive','3']]}])[0].changes[0].key,'facets');
 }
 const token=typed('token','foo',[['minLength','01']]);
 assert.deepEqual(compare([token],[{...token,facets:[['minLength','1']]}]),[],'integer-valued facets do not inherit the string type');
 assert.equal(compare([typed('string','a',[['pattern','01']])],[typed('string','a',[['pattern','1']])])[0].changes[0].key,'facets');
}
for(const [before,after] of [['1','true'],['0','false']]){
 const typed=(v,base='b:boolean',baseNs='http://www.w3.org/2001/XMLSchema')=>field('flag',{base,baseNs,flags:{fixed:v},values:[[v,'Значение']]});
 assert.deepEqual(compare([typed(before)],[typed(after)]),[]);
 for(const baseNs of ['urn:other','http://www.w3.org/2001/XMLSchema']){
  const base=baseNs==='urn:other'?'b:boolean':'xs:string';
  assert.deepEqual(compare([typed(before,base,baseNs)],[typed(after,base,baseNs)])[0].changes.map(c=>c.key),['flags','values']);
 }
 const real=compare([typed('1')],[typed('false')])[0].changes;
 assert.equal(real[0].before.fixed,'1');assert.equal(real[0].after.fixed,'false');
 const both={...typed('true'),values:[['1','Да'],['true','Да']]};
 assert.deepEqual(compare([both],[{...typed('1'),values:[['true','Да']]}]),[]);
 const descriptions={...both,values:[['true','Да'],['1','Иное описание']]};
 assert.deepEqual(compare([descriptions],[{...typed('1'),values:[['true','Да']]}])[0].changes.map(c=>c.key),['valueDescriptions']);
}
{
 const numeric=(name,values,ns='http://www.w3.org/2001/XMLSchema')=>field('amount',{base:'n:'+name,baseNs:ns,values});
 for(const [type,oldValue,newValue] of [
  ['integer','01','1'],['int','+1','1'],['nonNegativeInteger','-0','0'],
  ['decimal',' -.000 ','0'],['decimal','+01.00','1'],['decimal','.50','0.500'],
  ['unsignedLong','00018446744073709551615','18446744073709551615'],
  ['decimal','10000000000000000.00000000000001','010000000000000000.000000000000010']
 ]){
  const a=numeric(type,[[oldValue,'Сумма'],[newValue,'Сумма']]),b=numeric(type,[[newValue,'Сумма']]);
  assert.deepEqual(compare([a],[b]),[],type+' equivalents form one enum value');
  assert.deepEqual(compare([b],[numeric(type,[[oldValue,'Иное описание']])])[0].changes.map(x=>x.key),['valueDescriptions']);
 }
 const big='10000000000000000000000000000000';
 const changed=compare([numeric('integer',[[big+'1','']])],[numeric('integer',[[big+'2','']])])[0].changes;
 assert.deepEqual(changed.map(x=>[x.key,x.before,x.after]),[['values',[big+'1'],[big+'2']]]);
 const floats=(type,a,b)=>compare([numeric(type,[[a,'']])],[numeric(type,[[b,'']])]);
 for(const type of ['float','double']){
  for(const [a,b] of [['1','1.0E0'],['-0E12','0'],['INF','1e999'],['-INF','-1E999'],['NaN',' NaN ']]){
   assert.deepEqual(floats(type,a,b),[],type+' '+a+' = '+b);
  }
  for(const [a,b] of [['0x10','16'],['Infinity','INF'],['nan','NaN'],['+INF','INF'],['INF','NaN']]){
   assert.deepEqual(floats(type,a,b)[0].changes.map(x=>x.key),['values']);
  }
 }
 assert.deepEqual(floats('float','16777216','16777217'),[],'binary32 precision');
 assert.deepEqual(floats('double','16777216','16777217')[0].changes.map(x=>x.key),['values'],'binary64 precision');
 for(const fieldType of [v=>numeric('string',[[v,'']]),v=>numeric('decimal',[[v,'']],'urn:other')]){
  assert.deepEqual(compare([fieldType('01')],[fieldType('1')])[0].changes.map(x=>x.key),['values']);
 }
}
{
 const any=(extra={})=>field('*',{kind:'any',...extra});
 assert.deepEqual(compare([any({ns:'urn:old'})],[any({ns:'',wildcardNamespace:'##any'})]),[]);
 const delta=compare([any({wildcardNamespace:'##local',flags:{namespace:'##local'}})],[any({wildcardNamespace:'urn:new',flags:{namespace:'##targetNamespace'}})])[0];
 assert.deepEqual(delta.changes.map(c=>[c.key,c.before,c.after]),[['ns','##local','urn:new']]);
}
assert.equal(compare([field("q",{type:"old:Code",typeNs:"urn:types",base:"xs:string",baseNs:"http://www.w3.org/2001/XMLSchema"})],[field("q",{type:"new:Code",typeNs:"urn:types",base:"xsd:string",baseNs:"http://www.w3.org/2001/XMLSchema"})]).length,0);
const namespaceChange=compare([field("q",{type:"types:Code",typeNs:"urn:old"})],[field("q",{type:"types:Code",typeNs:"urn:new"})]);
assert.deepEqual(namespaceChange[0].changes.map(c=>c.key),["type"]);
assert.notEqual(namespaceChange[0].changes[0].before,namespaceChange[0].changes[0].after,"URI change stays visible to journal and Excel consumers");
assert.equal(compare([field("q",{type:"types:Code"})],[field("q",{type:"types:Code",typeNs:"urn:new"})]).length,0,"legacy fields without QName metadata remain compatible");
assert.equal(compare([field("q",{alternatives:[{type:"old:Code",typeNs:"urn:t",base:"xs:string"}]})],[field("q",{alternatives:[{type:"new:Code",typeNs:"urn:t",base:"xs:string"}]})]).length,0);
let diff=compare(left,right);
{
 const q=(prefix,uri,doc='описание')=>field('q',{values:[[prefix+':read',doc]],valueQNames:[[uri,'read']],flags:{fixed:prefix+':read'},flagQNames:{fixed:[uri,'read']}});
 const a=q('p','urn:one'),b=q('p','urn:two'),alias=q('alias','urn:one');
 assert.deepEqual(compare([a],[alias]),[],'QName enumeration and fixed depend on URI, not prefix');
 assert.deepEqual(compare([a],[b])[0]?.changes.map(c=>c.key),['flags','values']);
 assert.match(JSON.stringify(compare([a],[b])[0].changes),/urn:two/);
 assert.deepEqual(compare([a],[q('other','urn:one','новое')])[0].changes.map(c=>c.key),['valueDescriptions']);
 assert.deepEqual(compare([field('u',{alternatives:[a]})],[field('u',{alternatives:[alias]})]),[]);
 assert.match(JSON.stringify(compare([field('u',{alternatives:[a]})],[field('u',{alternatives:[b]})])),/urn:two/);
 const old={...a};delete old.valueQNames;delete old.flagQNames;assert.deepEqual(compare([old],[a]),[]);
}
{
 const boolean={type:"xs:boolean"},string={type:"xs:string"};
 for(const nested of [false,true]){
  const member=alternatives=>field("u",{values:[["true",""]],alternatives:nested?[{alternatives}]:alternatives});
  const change=compare([member([boolean,string])],[member([string,boolean])])[0]?.changes[0];
  assert.equal(change?.key,"alternatives","overlapping union members are evaluated in declaration order");
  assert.notDeepEqual(change.before,change.after,"displayed and exported differences preserve union order");
 }
}
assert.deepEqual(diff.map(x=>[x.path,x.status]),[["root/added","added"],["root/changed","changed"],["root/old","removed"]]);
assert.deepEqual(diff[1].changes.map(c=>c.key),["min","facets"]);
assert.equal(compare(left,structuredClone(left)).length,0);
assert.equal(compare([field("x",{values:[["1","Один"],["2","Два"]],facets:[["minLength","1"],["maxLength","9"]]})],[field("x",{values:[["2","Два"],["1","Один"]],facets:[["maxLength","9"],["minLength","1"]]})]).length,0);
assert.deepEqual(compare([field("x")],[field("x",{doc:"Новое описание"})])[0].changes.map(c=>c.key),["doc"]);
assert.deepEqual(compare([field("x",{values:[["1","Прежнее"]]})],[field("x",{values:[["1","Новое"]]})])[0].changes.map(c=>c.key),["valueDescriptions"]);
assert.deepEqual(compare([field("x",{values:[["1","Один"]]})],[field("x",{values:[["1","Один"],["2","Два"]]})])[0].changes.map(c=>c.key),["values"]);
assert.deepEqual(compare([field("x")],[field("x",{flags:{fixed:"true"},max:"unbounded",group:"choice [0..1]"})])[0].changes.map(c=>c.key),["max","group","flags"]);
assert.equal(compare(right,left).find(x=>x.path==="root/added").status,"removed");
assert.throws(()=>compare([field("x"),field("x")],[]),/Неоднозначный/);
assert.equal(compare([field("u",{alternatives:[{base:"xs:string",facets:[["maxLength","5"]],values:[],flags:{}}]})],[field("u",{alternatives:[{base:"xs:string",facets:[["maxLength","6"]],values:[],flags:{}}]})])[0].changes[0].key,"alternatives");
const model=(kind,...children)=>field("parent",{contentModel:[kind,...children]});
{
 const group=(children,min='1',max='1',kind='sequence')=>({kind,min,max,children});
 const bounded=children=>({...model('sequence','a','b','c','d'),contentModelBounds:group(children)});
 const first=bounded([group(['a','b'],'0'),group(['c','d'],'0')]);
 const second=bounded([group(['a'],'0'),group(['b','c'],'0'),group(['d'],'0')]);
 const changes=compare([first],[second]);
 assert.equal(changes[0]?.changes[0].key,'contentModel','optional sequence membership changes the XML contract');
 assert.match(changes[0].changes[0].before,/0.*1/);
 assert.notEqual(changes[0].changes[0].before,changes[0].changes[0].after);
 assert.equal(compare([bounded([group(['a','b']),'c','d'])],[bounded(['a','b','c','d'])]).length,0);
 assert.equal(compare([bounded([group([group(['a','b'])],'0','2','group')])],[bounded([group(['a','b'],'0','2')])]).length,0,'group references have the same bounds as inline compositors');
 assert.equal(compare([first],[model('sequence','a','b','c','d')]).length,0,'legacy trees without bounds remain readable');
 assert.equal(compare([bounded([group(['a'],'0','2')])],[bounded([group(['a'],'0','3')])])[0]?.changes[0].key,'contentModel');
}
const patternField=groups=>field("code",{facets:[["pattern","A"],["pattern","B"]],patternGroups:groups});
const joined=patternField([["A","B"]]),inherited=patternField([["A"],["B"]]);
assert.deepEqual(compare([joined],[inherited])[0]?.changes.map(c=>c.key),["patternGroups"]);
assert.equal(compare([joined],[patternField([["B","A"]])]).length,0);
assert.equal(compare([inherited],[patternField([["B"],["A"]])]).length,0);
const legacyPatterns=field("code",{facets:joined.facets});
assert.equal(compare([legacyPatterns],[joined]).length,0,"a legacy field has no evidence of how its patterns were grouped");
assert.equal(compare([field("q",{alternatives:[joined]})],[field("q",{alternatives:[inherited]})])[0]?.changes[0].key,"alternatives");
const choiceTree=(model,labels)=>[field("root",{path:"root",parent:-1,contentModel:model}),...Object.entries(labels).map(([name,group])=>field(name,{group}))];
const branch=n=>"Выбор 1, вариант "+n;
const originalChoice=choiceTree(["choice",["sequence","a","b"],"c"],{a:branch(1),b:branch(1),c:branch(2)});
const reorderedChoice=choiceTree(["choice","c",["sequence","a","b"]],{a:branch(2),b:branch(2),c:branch(1)});
assert.deepEqual(compare(originalChoice,reorderedChoice),[]);
const boundedChoice=choiceTree(["choice","c",["sequence","a","b"]],{a:"choice [0..1] / "+branch(2),b:"choice [0..1] / "+branch(2),c:"choice [0..1] / "+branch(1)});
assert.ok(compare(originalChoice,boundedChoice).some(r=>r.changes.some(c=>c.key==="group")));
const regroupedChoice=choiceTree(["choice","a",["sequence","b","c"]],{a:branch(1),b:branch(2),c:branch(2)});
assert.ok(compare(originalChoice,regroupedChoice).some(r=>r.changes.some(c=>c.key==="contentModel")));
assert.deepEqual(compare([model("sequence","a","b")],[model("sequence","b","a")])[0].changes.map(c=>c.key),["contentModel"]);
assert.equal(compare([model("all","a","b")],[model("all","b","a")]).length,0);
assert.equal(compare([model("sequence","a","b")],[model("all","a","b")])[0].changes[0].key,"contentModel");
assert.equal(compare([field("parent")],[model("sequence","a","b")]).length,0,"historical trees lack compositor information");
assert.equal(compare([model("sequence",["sequence","a","b"],"c")],[model("sequence","a","b","c")]).length,0,"nested sequences preserve the same sibling order");
assert.equal(compare([model("sequence","a","b")],[model("sequence","a","extra","b")]).length,0,"an added child already has its own result");
assert.equal(compare([model("sequence",["choice","a","b"],"c")],[model("sequence","c",["choice","a","b"])])[0].changes[0].key,"contentModel");
{
 const tree=(root,names,bounded)=>[field(root,{path:root,parent:-1,contentModel:['sequence',...names],...(bounded?{contentModelBounds:{kind:'sequence',min:'1',max:'1',children:names}}:{})}),...names.map(name=>field(name,{path:root+'/'+name,min:'1'}))];
 for(const bounded of [false,true])for(const renamedRoot of [false,true]){
  const before=tree('root',['a','b'],bounded),target=renamedRoot?'newRoot':'root';
  const mappings=[...(renamedRoot?[{beforePath:'root',afterPath:target}]:[]),{beforePath:'root/a',afterPath:target+'/x'},{beforePath:'root/b',afterPath:target+'/y'}];
  const renamed=compare(before,tree(target,['x','y'],bounded),mappings);
  assert.equal(renamed.filter(r=>r.status==='changed').length,0,'renaming siblings alone preserves their sequence');
  const reordered=compare(before,tree(target,['y','x'],bounded),mappings);
  assert.equal(reordered.filter(r=>r.status==='mapped').length,2);
  const parent=reordered.find(r=>r.path==='root');
  assert.equal(parent?.status,'changed','confirmed child mappings reveal a reordered parent');
  assert.deepEqual(parent.changes.map(c=>c.key),['contentModel']);
  assert.notEqual(parent.changes[0].before,parent.changes[0].after);
  for(const kind of ['all','choice']){
   const a=tree('root',['a','b'],bounded),b=tree(target,['y','x'],bounded);
   a[0].contentModel[0]=kind;b[0].contentModel[0]=kind;
   if(bounded){a[0].contentModelBounds.kind=kind;b[0].contentModelBounds.kind=kind}
   assert.equal(compare(a,b,mappings).filter(r=>r.status==='changed').length,0,'renamed unordered children remain equivalent');
  }
 }
 const split=compare(tree('root',['a','b']),tree('root',['x','y']),[{beforePath:'root/a',afterPath:'root/x'},{beforePath:'root/a',afterPath:'root/y'}]);
 assert.match(JSON.stringify(split.find(r=>r.path==='root')?.changes),/Неоднозначное сопоставление/,'split mappings cannot prove sibling order');
}
const root=path.resolve(__dirname,".."),catalog=JSON.parse(fs.readFileSync(path.join(root,"data/catalog.json"),"utf8"));
const load=r=>JSON.parse(fs.readFileSync(path.join(root,"data/fields",r.revision_id+".json"),"utf8")).fields;
const pair=n=>catalog.revisions.filter(r=>r.number===n&&r.status==='matched');
const p10110=pair("10110");assert.equal(compare(load(p10110[0]),load(p10110[1])).length,0);
const p32=pair("32");assert.ok(compare(load(p32[0]),load(p32[1])).length>0);
for(const revision of catalog.revisions.filter(r=>r.status==='matched'))assert.equal(compare(load(revision),load(revision)).length,0);
console.log("Comparison verified: add/remove/change, restrictions, enum order and descriptions, duplicates, direction, real prod/test pairs and all editions.");


// Review 13/17: XSD boolean lexical forms/defaults and strict Unicode set order.
for(const [a,b] of [[{nillable:'true'},{nillable:'1'}],[{nillable:'false'},{}],[{nillable:'0'},{}],[{processContents:'strict'},{}]]){
 assert.deepEqual(compare([field('flag',{flags:a})],[field('flag',{flags:b})]),[]);
}
for(const [a,b] of [[{nillable:'true'},{nillable:'false'}],[{processContents:'strict'},{processContents:'skip'}]]){
 assert.deepEqual(compare([field('flag',{flags:a})],[field('flag',{flags:b})])[0].changes.map(c=>c.key),['flags']);
}
const unicodeValues=[['й',''],['и\u0306','']];
assert.deepEqual(compare([field('enum',{values:unicodeValues})],[field('enum',{values:[...unicodeValues].reverse()})]),[]);

// Equivalent XSD values retain their original spelling for display only.
for(const [type,a,b] of [
 ['date','2026-11-17Z','2026-11-17+00:00'],
 ['date','2026-11-17-00:00','2026-11-17Z'],
 ['date','2026-11-17+14:00','2026-11-16-10:00'],
 ['date','12345-11-17Z','12345-11-17+00:00'],
 ['date','-0004-02-29Z','-0004-02-29+00:00'],
 ['date','-0004-03-01+14:00','-0004-02-29-10:00'],
 ['date','0001-01-01+14:00','-0001-12-31-10:00'],
 ['date','-0003-01-01+14:00','-0004-12-31-10:00'],
 ['duration','P1Y','P12M'],['duration','P1D','PT24H'],
 ['duration','-P1Y2M3DT4H5M6.700S','-P14MT273906.7S'],
 ['duration','-P0Y0M0DT0H0M0.000S','PT0S'],
 ['duration','PT.500S','PT0.5S'],['duration','PT1.S','PT1S'],
 ['duration','P9007199254740993Y','P108086391056891916M'],
 ['duration','P9007199254740993D','PT778222015609621795200S'],
 ['duration','PT0.123456789012345678900S','PT0.1234567890123456789S'],
 ['dateTime','2026-11-17T12:00:00+03:00','2026-11-17T09:00:00Z'],
 ['dateTime','2026-11-17T00:15:00.123456789+03:00','2026-11-16T21:15:00.1234567890Z'],
 ['dateTime','2026-11-17T09:00:00.000','2026-11-17T09:00:00'],
 ['dateTime','2026-11-17T24:00:00Z','2026-11-18T00:00:00+00:00'],
 ['dateTime','-0004-02-29T00:00:00Z','-0004-02-29T00:00:00+00:00'],
 ['dateTime','-0003-01-01T00:00:00+01:00','-0004-12-31T23:00:00Z'],
 ['time','09:00:00.0Z','09:00:00+00:00'],
 ['time','00:15:00.1234567890123456789+03:00','21:15:00.12345678901234567890Z'],
 ['time','24:00:00.0','00:00:00'],
 ['time','23:45:00-02:00','01:45:00Z'],
 ['gYearMonth','12345-11+00:00','12345-11Z'],
 ['gYear','2025+00:00','2025Z'],
 ['gMonthDay','--09-20+00:00','--09-20Z'],
 ['gMonthDay','--03-01+14:00','--02-29-10:00'],
 ['gDay','---20-00:00','---20Z'],
 ['gDay','---20+14:00','---19-10:00'],
 ['gMonth','--11+00:00','--11Z'],
 ['hexBinary','aB01','AB01'],['base64Binary','QUJD','Q U\tJ\nD']
]){
 const f=value=>field('typed',{base:'xs:'+type,flags:{fixed:value,default:value},values:[[value,'одинаково']],facets:['hexBinary','base64Binary'].includes(type)?[]:[['minInclusive',value]]});
 assert.deepEqual(compare([f(a)],[f(b)]),[],type+' lexical forms represent the same value');
 assert.deepEqual(compare([field('union',{alternatives:[f(a)]})],[field('union',{alternatives:[f(b)]})]),[],type+' union values compare in value space');
}
for(const [type,a,b] of [
 ['date','2026-11-17','2026-11-17Z'],
 ['date','2026-11-17+01:00','2026-11-17Z'],
 ['date','2026-11-17','2026-11-18'],
 ['date','-0004-12-31Z','-0003-01-01Z'],
 ['date','-0001-02-29Z','-0001-03-01Z'],
 ['date','0000-01-01Z','0001-01-01Z'],
 ['duration','P1M','P30D'],['duration','-P1D','P1D'],
 ['duration','P9007199254740993M','P9007199254740992M'],
 ['duration','PT0.1234567890123456789S','PT0.1234567890123456788S'],
 ['duration','P','PT0S'],['duration','P1DT','P1D'],['duration','+P1D','P1D'],
 ['dateTime','2026-11-17T09:00:00','2026-11-17T09:00:00Z'],
 ['dateTime','2026-11-17T09:00:00.123456789Z','2026-11-17T09:00:00.123456788Z'],
 ['dateTime','2026-11-17T09:00:00','2026-11-18T09:00:00'],
 ['time','09:00:00','09:00:00Z'],
 ['time','09:00:00.1234567890123456789Z','09:00:00.1234567890123456788Z'],
 ['time','24:00:00.1Z','00:00:00.1Z'],
 ['time','09:00:00+14:01','18:59:00Z'],
 ['gYearMonth','02025-11Z','2025-11Z'],
 ['gYear','0000Z','0001Z'],
 ['gMonthDay','--02-30Z','--03-01Z'],
 ['gDay','---00Z','---01Z'],
 ['gMonth','--11','--11Z'],
 ['hexBinary','AB','AC'],['base64Binary','QUJD','QUJE'],['string','aB','AB']
])assert.equal(compare([field('typed',{base:'xs:'+type,flags:{fixed:a}})],[field('typed',{base:'xs:'+type,flags:{fixed:b}})])[0]?.changes[0].key,'flags',type+' distinct values remain visible');
{
 const a=field('q',{type:'xs:QName',values:[['p:read','одно'],['alias:read','другое']],valueQNames:[['urn:one','read'],['urn:one','read']]});
 const b={...a,values:[['new:read','одно']],valueQNames:[['urn:one','read']]};
 assert.deepEqual(compare([a],[b])[0]?.changes.map(c=>c.key),['valueDescriptions'],'removing a QName description leaves the same allowed value set');
}
