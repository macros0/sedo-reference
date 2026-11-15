"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{spawnSync}=require('node:child_process'),{Worker}=require('node:worker_threads');
const root=path.resolve(__dirname,'..'),read=n=>fs.readFileSync(path.join(root,n),'utf8');
const python=process.env.SEDO_PYTHON||'python';
function makePack(kind='static'){const generated=spawnSync(python,['tests/test_attribute_ref_constraints.py','--pack',kind],{cwd:root,encoding:'utf8',env:{...process.env,PYTHONPATH:root}});assert.equal(generated.status,0,generated.stderr);return JSON.parse(generated.stdout)}
const pack=makePack(),engine=read('web/vendor/xmllint/xmllint-browser.mjs').split(';(function initWorker()')[0];
const source=`const {parentPort}=require('node:worker_threads');self=globalThis;self.postMessage=data=>parentPort.postMessage(data);parentPort.on('message',data=>self.onmessage({data}));self.SEDO_WASM=new Uint8Array(${JSON.stringify([...fs.readFileSync(path.join(root,'web/vendor/xmllint/xmllint.wasm'))])});\n${engine}\n${read('web/validation-core.js')}\n${read('web/validation-paths.js')}\n${read('web/validation-worker.js')}`;
async function validate(xml,custom=pack){const worker=new Worker(source,{eval:true});try{return await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('fixed-value validation timed out')),15000);worker.on('error',reject);worker.on('message',result=>{clearTimeout(timer);resolve(result)});worker.postMessage({bytes:new TextEncoder().encode(xml),pack:custom})})}finally{await worker.terminate()}}
(async()=>{
 for(const [value,expected] of [[null,true],['false',true],['0',true],['true',false],['1',false]]){
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
   const w=new Worker(source+`\nself.onmessage=async event=>{try{const p=event.data;const files=[{fileName:'input.xml',contents:new TextEncoder().encode('<p:checks xmlns:p="'+p.supplementary_namespace+'"/>')},...Object.entries(p.files).map(([fileName,value])=>({fileName,contents:self.SedoValidationCore.decode64(value)}))];self.postMessage(await runLint(['--nonet','--noout','--schema',p.supplementary_schema,'input.xml'],files))}catch(error){self.postMessage({code:-1,stderr:String(error)})}}`,{eval:true});
   try{const result=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('supplementary schema timed out')),15000);w.on('error',reject);w.on('message',result=>{clearTimeout(timer);resolve(result)});w.postMessage(selected)});assert.equal(result.code,0,row.number+' '+row.environment+': '+result.stderr);count++}finally{await w.terminate()}
  }
  assert.ok(count>0,'rebuilt published catalog must include real fixed-attribute supplementary schemas');console.log('All published supplementary schemas compiled in WASM:',count);
 }
 console.log('Attribute refs: real WASM and Python use the same fixed-value probes.');
})().catch(error=>{console.error(error);process.exitCode=1});
