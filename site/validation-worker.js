"use strict";
// A blob worker inherits the opaque frame's connect-src 'none' CSP.
// This additional guard prevents even accidental attempts by the engine.
for(const name of ['fetch','XMLHttpRequest','WebSocket','EventSource','importScripts'])Object.defineProperty(self,name,{value:()=>{throw Error('Сетевые обращения валидатора запрещены.')},writable:false,configurable:false});
function runLint(args,inputFiles){return new Promise((resolve,reject)=>{
 let stdout='',stderr='';
 const append=(old,text)=>(old+String(text)+'\n').slice(0,65536);
 Module({wasmBinary:self.SEDO_WASM,inputFiles,arguments:args,wasmMemory:new WebAssembly.Memory({initial:256,maximum:2048}),print:s=>{stdout=append(stdout,s)},printErr:s=>{stderr=append(stderr,s)},onExit:code=>resolve({code,stdout,stderr}),onAbort:()=>reject(Error('Недостаточно памяти или ошибка WASM.'))}).catch(reject);
})}
self.onmessage=async event=>{
 const core=self.SedoValidationCore;
 try{
  const {bytes,pack}=event.data;const text=core.inspectBytes(bytes);core.checkPack(pack,pack);
  // The minimal libxml2 build lacks legacy iconv encodings. Decode strictly in
  // the browser and transcode an in-memory copy; source bytes are never edited.
  const xml=new TextEncoder().encode(text.replace(/^(<\?xml\s[^?]*\bencoding\s*=\s*)(["'])[^"']*\2/, '$1"UTF-8"'));
  const files=[{fileName:'input.xml',contents:xml},...Object.entries(pack.files).map(([fileName,value])=>({fileName,contents:core.decode64(value)}))];
  const parsed=await runLint(['--nonet','--xpath','concat(local-name(/*), "|", namespace-uri(/*))','input.xml'],files);
  if(parsed.code!==0){self.postMessage({type:'result',valid:false,errors:core.errors(parsed.stderr)});return}
  if(parsed.stdout.trim()!==pack.root+'|'+(pack.namespace||'')){self.postMessage({type:'result',valid:false,errors:[{line:1,message:'Неверный корневой элемент или пространство имён. Ожидается {'+(pack.namespace||'')+'}'+pack.root+'.'}]});return}
  const result=await runLint(['--nonet','--noout','--schema','message.xsd','input.xml'],files);
  if(![0,3,4].includes(result.code))throw Error('Не удалось выполнить XSD-проверку. '+result.stderr.slice(0,2000));
  self.postMessage({type:'result',valid:result.code===0,errors:result.code===0?[]:core.errors(result.stderr)});
 }catch(error){self.postMessage({type:'result',valid:false,runtime:true,errors:[{line:null,message:String(error.message||error).slice(0,2000)}]})}
};
