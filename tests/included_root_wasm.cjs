'use strict';
// Exercise generated included-root packs with the exact shipped WASM worker.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{Worker}=require('node:worker_threads');
const root=path.resolve(__dirname,'..'),read=name=>fs.readFileSync(path.join(root,name),'utf8');
const engine=read('web/vendor/xmllint/xmllint-browser.mjs').split(';(function initWorker()')[0];
const source=`const {parentPort}=require('node:worker_threads');self=globalThis;self.postMessage=data=>parentPort.postMessage(data);parentPort.on('message',data=>self.onmessage({data}));self.SEDO_WASM=new Uint8Array(${JSON.stringify([...fs.readFileSync(path.join(root,'web/vendor/xmllint/xmllint.wasm'))])});\n${engine}\n${read('web/validation-core.js')}\n${read('web/validation-paths.js')}\n${read('web/validation-worker.js')}`;
async function validate(pack,xml){
 const worker=new Worker(source,{eval:true});
 try{return await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('included-root WASM timed out')),15000);
  worker.once('message',value=>{clearTimeout(timer);resolve(value)});
  worker.once('error',error=>{clearTimeout(timer);reject(error)});
  worker.postMessage({pack,bytes:new TextEncoder().encode(xml)});
 })}finally{await worker.terminate()}
}
(async()=>{
 for(const pack of JSON.parse(fs.readFileSync(0,'utf8'))){
  for(const [xml,valid] of [[`<r xmlns="${pack.namespace}">1</r>`,true],[`<r xmlns="${pack.namespace}">bad</r>`,false],['<r xmlns="urn:wrong">1</r>',false]]){
   const result=await validate(pack,xml);assert.equal(result.valid,valid,xml+' '+JSON.stringify(result));
  }
 }
 console.log('Included roots compiled and validated in shipped WASM.');
})().catch(error=>{console.error(error);process.exitCode=1});
