"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process'),{Worker}=require('node:worker_threads');
const root=path.resolve(__dirname,'..'),read=n=>fs.readFileSync(path.join(root,n),'utf8');
const python=process.env.SEDO_PYTHON||'python';
function makePack(kind='static'){const generated=spawnSync(python,['tests/test_attribute_ref_constraints.py','--pack',kind],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONPATH:root}});assert.equal(generated.status,0,generated.stderr);return JSON.parse(generated.stdout)}
const pack=makePack(),engine=read('web/vendor/xmllint/xmllint-browser.mjs').split(';(function initWorker()')[0];
const source=`const {parentPort}=require('node:worker_threads');self=globalThis;self.postMessage=data=>parentPort.postMessage(data);parentPort.on('message',data=>self.onmessage({data}));self.SEDO_WASM=new Uint8Array(${JSON.stringify([...fs.readFileSync(path.join(root,'web/vendor/xmllint/xmllint.wasm'))])});\n${engine}\n${read('web/validation-core.js')}\n${read('web/validation-paths.js')}\n${read('web/validation-worker.js')}`;
async function validate(xml,custom=pack){const worker=new Worker(source,{eval:true});try{return await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('fixed-value validation timed out')),15000);worker.on('error',reject);worker.on('message',result=>{clearTimeout(timer);resolve(result)});worker.postMessage({bytes:new TextEncoder().encode(xml),pack:custom})})}finally{await worker.terminate()}}
async function compileSupplementary(selected){
 const worker=new Worker(source+`\nself.onmessage=async event=>{try{const p=event.data;const files=[{fileName:'input.xml',contents:new TextEncoder().encode('<p:checks xmlns:p="'+p.supplementary_namespace+'"/>')},...Object.entries(p.files).map(([fileName,value])=>({fileName,contents:self.SedoValidationCore.decode64(value)}))];self.postMessage(await runLint(['--nonet','--noout','--schema',p.supplementary_schema,'input.xml'],files))}catch(error){self.postMessage({code:-1,stderr:String(error)})}}`,{eval:true});
 try{return await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('supplementary schema timed out')),15000);worker.on('error',reject);worker.on('message',result=>{clearTimeout(timer);resolve(result)});worker.postMessage(selected)})}finally{await worker.terminate()}
}
(async()=>{
 const checkProfile=async(kind,profile,examples)=>{
  const custom=makePack(kind);
  assert.ok(custom.attribute_type_checks?.some(item=>item.type[0]==='urn:main'&&item.type[1]===profile),kind+' missing '+profile);
  for(const [body,expected] of examples){
   const xml='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:'+profile+'"'+body+'</m:request>';
   const result=await validate(xml,custom);assert.equal(result.runtime,undefined,JSON.stringify(result));assert.equal(result.valid,expected,kind+' '+xml+' '+JSON.stringify(result));
   if(!expected)assert.match(result.errors[0].message,/fixed value constraint/);
  }
 };
 for(const kind of ['named-base','anonymous-base']){
  await checkProfile(kind,'Checked',[[' a:editable="false">text',true],[' a:editable="true">text',false]]);
 }
 for(const kind of ['inactive-element','inactive-group']){
  await checkProfile(kind,'Dynamic',[[' a:editable="false">',true],[' a:editable="true">',false]]);
 }
 for(const [kind,examples] of [
  ['fixed-no-namespace-decimal', [['<request xmlns="urn:main">1.00</request>',true],['<request xmlns="urn:main">2</request>',false]]],
  ['fixed-no-namespace-decimal-restricted', [['<request xmlns="urn:main">1.00</request>',true],
   ['<request xmlns="urn:main">2</request>',false],['<request xmlns="urn:main">0.50</request>',false]]],
  ['fixed-no-namespace-local-default-restricted', [['<request xmlns="urn:main">1.00</request>',true],
   ['<request xmlns="urn:main">2</request>',false]]],
  ['fixed-no-namespace-qname', [['<m:request xmlns:m="urn:main">m:known</m:request>',true],
   ['<m:request xmlns:m="urn:main" xmlns="urn:main">known</m:request>',true],
   ['<m:request xmlns:m="urn:main" xmlns="urn:other">known</m:request>',false]]],
 ]){
  const custom=makePack(kind);
  for(const [xml,expected] of examples){
   const result=await validate(xml,custom);assert.equal(result.runtime,undefined,JSON.stringify(result));assert.equal(result.valid,expected,kind+' '+xml+' '+JSON.stringify(result));
  }
 }
 for(const [kind,second] of [['choice-unfixed','2'],['choice-same','1.00'],['choice-different','2.00']]){
  await checkProfile(kind,'ChoiceType',[
   ['><a/><value>1.00</value>',true],['><a/><value>2</value>',false],
   ['><b/><value>'+second+'</value>',true],['><b/><value>3</value>',kind==='choice-unfixed'],
  ]);
 }
 await checkProfile('choice-qualified','ChoiceType',[
  ['><m:marker/><!-- ignored --><value>1.00</value>',true],
  ['><m:marker/><value>2</value>',false],
  ['><marker/><value>2</value>',true],
 ]);
 const invalidGuard=makePack('choice-qualified');
 invalidGuard.attribute_type_checks.find(p=>p.type[1]==='ChoiceType').checks[0].guards[0].layouts[0].index=0;
 const rejectedGuard=await validate('<m:request xmlns:m="urn:main"/>',invalidGuard);
 assert.equal(rejectedGuard.runtime,true);assert.match(rejectedGuard.errors[0].message,/Некорректный путь проверки XSD/);
 const unusedRecursive=makePack('unused-recursive');
 const scalar=await validate('<request xmlns="urn:main">text</request>',unusedRecursive);
 assert.equal(scalar.runtime,undefined,JSON.stringify(scalar));assert.equal(scalar.valid,true,JSON.stringify(scalar));
 const extended=makePack('extended-any-type');
 for(const requestType of ['', ' xsi:type="m:Extended"'])for(const child of ['unknown xsi:type="m:Leaf"','m:known']){
  for(const [fixed,expected] of [['false',true],['true',false]]){
   const xml='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'+requestType+'><'+child+' a:editable="'+fixed+'"/></m:request>';
   const result=await validate(xml,extended);assert.equal(result.runtime,undefined,JSON.stringify(result));assert.equal(result.valid,expected,xml);
  }
 }
 for (const [kind,template] of [
  ['derived-scalar','<m:request xsi:type="m:TextWithFixed" a:editable="VALUE">text</m:request>'],
  ['strict-derived','<m:request><m:wrapper><child xsi:type="m:FixedLeaf" a:editable="VALUE"/></m:wrapper></m:request>'],
 ]){
  const custom=makePack(kind);
  for(const [value,expected] of [['false',true],['true',false]]){
   const xml=template.replace('>',' xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">').replace('VALUE',value);
   const result=await validate(xml,custom);
   assert.equal(result.runtime,undefined,JSON.stringify(result));assert.equal(result.valid,expected,kind+': '+JSON.stringify(result));
  }
 }
 for(const [kind,examples] of [
  ['implicit-xml', [['<m:request xmlns:m="urn:main" xml:lang="ru"/>',true],['<m:request xmlns:m="urn:main" xml:lang="123"/>',false],['<m:request xmlns:m="urn:main"/>',false]]],
  ['annotated', [['<m:request xmlns:m="urn:main"><amount>1.0</amount></m:request>',true],['<m:request xmlns:m="urn:main"><amount>2</amount></m:request>',false]]]
 ]){
  const custom=makePack(kind);
  for(const [xml,valid] of examples){const result=await validate(xml,custom);assert.equal(result.runtime,undefined,JSON.stringify(result));assert.equal(result.valid,valid,kind+': '+JSON.stringify(result))}
 }
 const boolean=makePack('boolean');
 for(const [body,valid] of [['<flag> false </flag>',true],['<flag>\nfalse\n</flag>',true],['<flag/>',true],
   ['<flag xsi:nil="true"/>',false],['<flag>false</flag><word>true</word>',true],['<flag>false</flag><word>1</word>',false],
   ['<flag>false</flag><literal>1</literal>',false],['<flag>false</flag>'.repeat(40),true],
   ['<flag>false</flag>'.repeat(40)+'<flag>true</flag>',false]]){
  const xml='<m:request xmlns:m="urn:main" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'+body+'</m:request>';
  const result=await validate(xml,boolean);assert.equal(result.valid,valid,JSON.stringify(result));
 }
 const narrowed=makePack('narrowed');
 for(const [type,valid] of [['Derived',true],['Base',false]]){
  const xml='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:'+type+'"><wrapper><m:item a:editable="true"/></wrapper></m:request>';
  assert.equal((await validate(xml,narrowed)).valid,valid,type);
 }
 const included=makePack('chameleon');
 for(const [value, expected] of [['OK',true],['NO',false],['lowercase',false]]){
  const result=await validate('<m:request xmlns:m="urn:main" m:code="'+value+'"/>',included);
  assert.equal(result.runtime,undefined);assert.equal(result.valid,expected,JSON.stringify(result));
 }
 const wildcard=makePack('wildcard'),envelope=body=>'<m:request xmlns:m="urn:main" xmlns:a="urn:attrs">'+body+'</m:request>';
 for(const [body,valid] of [['<strict><m:item a:editable="true"/></strict>',false],['<strict><m:item a:editable="0"/></strict>',true],
    ['<lax><m:group><m:item a:editable="true"/></m:group></lax>',false],['<skip><m:item a:editable="true"/></skip>',true],
    ['<lax><m:unknown a:editable="true"/></lax>',true],
    ['<lax><m:unknown><m:item a:editable="true"/></m:unknown></lax>',false],
    ['<strict><m:implicit><m:item a:editable="true"/></m:implicit></strict>',false],
    ['<lax><m:explicit><m:item a:editable="true"/></m:explicit></lax>',false],
    ['<localAny><m:item a:editable="true"/></localAny>',false],['<localAny><m:item a:editable="0"/></localAny>',true],
    ['<lax><m:unknown xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:s="http://www.w3.org/2001/XMLSchema" xsi:type="s:anyType"><m:item a:editable="true"/></m:unknown></lax>',false],
    ['<lax><m:implicit xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:LocalContent"><m:item a:editable="true"/></m:implicit></lax>',true],
    ['<skip><m:unknown xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:Fixed" a:editable="true"/></skip>',true],
    ['<skip><m:group><m:item a:editable="true"/></m:group></skip>',true]]){
  const result=await validate(envelope(body),wildcard);assert.equal(result.runtime,undefined,JSON.stringify(result));assert.equal(result.valid,valid,body);
 }
 const largePrefix='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs">'+'<item/>'.repeat(100000);
 for(const [tail,expected] of [['',true],['\n<item a:editable="false"/>',true],['\n<item a:editable="true"/>',false]]){
  const large=await validate(largePrefix+tail+'</m:request>');
  assert.equal(large.runtime,undefined,'navigation budget must not stop fixed-attribute validation: '+JSON.stringify(large));
  assert.equal(large.valid,expected,'fixed constraints apply even beyond the navigation index');
  if(!expected){assert.match(large.errors[0].message,/editable/);assert.equal(large.errors[0].line,2)}
 }
 const corrupted=structuredClone(pack);
 corrupted.files[pack.supplementary_schema]=Buffer.from('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="checks" type="MissingProbeType"/></xs:schema>').toString('base64');
 const rejected=await compileSupplementary(corrupted);assert.notEqual(rejected.code,0,'candidate compilation must reject invalid supplementary XSD');assert.match(rejected.stderr,/MissingProbeType/);
 for(const [value,expected] of [[null,true],['false',true],['fa&#x6c;se',true],['fa&#x6C;se',true],['fa&#108;se',true],['0',true],['true',false],['1',false]]){
  const attr=value===null?'':' a:editable="'+value+'"';const xml='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item'+attr+'/></m:request>';
  const result=await validate(xml);assert.equal(result.valid,expected,'same imported attribute schema, editable='+value+': '+JSON.stringify(result));assert.equal(result.runtime,undefined);
 }
 const result=await validate('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item a:editable="false"/><item a:editable="true"/></m:request>');assert.equal(result.valid,false,'each repeated attribute use must satisfy fixed');
 const badFirst=await validate('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs">\n<item a:editable="true"/>\n<item a:editable="false"/>\n</m:request>');assert.equal(badFirst.valid,false);assert.equal(badFirst.errors[0].line,2);assert.match(badFirst.errors[0].message,/attribute '\{urn:attrs\}editable'/);
 for(const [attributes,expected] of [['xmlns:v="urn:value" a:kind="v:known"',true],['xmlns:v="urn:other" a:kind="v:known"',false],['a:token="a   b"',true],['a:token="wrong"',false]]){const result=await validate('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item '+attributes+'/></m:request>');assert.equal(result.valid,expected,JSON.stringify(result));assert.equal(result.runtime,undefined)}
 for(const kind of ['dynamic','scalar','equivalent']){
  const custom=makePack(kind),xtype=kind==='scalar'?'m:BooleanWithFixed':'m:Derived';
  for(const [value,expected] of [['false',true],['0',true],['true',false],['1',false]]){
   const xml='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="'+xtype+'" a:editable="'+value+'">'+(kind==='scalar'?'true':'')+'</m:request>';
   const result=await validate(xml,custom);assert.equal(result.valid,expected,kind+' xsi:type '+value+': '+JSON.stringify(result));assert.equal(result.runtime,undefined);
  }
  if(kind==='dynamic')for(const [xml,expected] of [
   ['<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" a:editable="true"/>',true],
   ['<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:Base" a:editable="true"/>',true],
   ['<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:Derived"><child a:editable="true"/></m:request>',false],
   ['<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><child xsi:type="m:FixedLeaf" a:editable="true"/></m:request>',false]]){const result=await validate(xml,custom);assert.equal(result.valid,expected,JSON.stringify(result));assert.equal(result.runtime,undefined)}
 }
 if(process.argv.includes('--all-editions')){
  const {gunzipSync}=require('node:zlib'),{createHash}=require('node:crypto');const catalog=JSON.parse(read('site/catalog-data.js').replace(/^window.SEDO_CATALOG=/,'').replace(/;$/,''));let count=0;const seen=new Set();
  for(const row of catalog.revisions.filter(row=>row.validation_download)){
   const selected=JSON.parse(gunzipSync(fs.readFileSync(path.join(root,'site',row.validation_download))));if(!selected.attribute_checks?.length&&!selected.attribute_type_checks?.length)continue;
   const key=createHash('sha256').update(JSON.stringify(selected.files)).digest('hex');if(seen.has(key))continue;seen.add(key);
   const result=await compileSupplementary(selected);assert.equal(result.code,0,row.number+' '+row.environment+': '+result.stderr);assert.doesNotMatch(result.stderr,/(?:parser|namespace|validity) error\s*:/i);count++;
  }
  assert.ok(count>0,'rebuilt published catalog must include real fixed-attribute supplementary schemas');console.log('All published supplementary schemas compiled in WASM:',count);
 }
 console.log('Attribute refs: real WASM and Python use the same fixed-value probes.');
})().catch(error=>{console.error(error);process.exitCode=1});
