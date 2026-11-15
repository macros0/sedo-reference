"use strict";
// A blob worker inherits the opaque frame's connect-src 'none' CSP.
// This additional guard prevents even accidental attempts by the engine.
for(const name of ['fetch','XMLHttpRequest','WebSocket','EventSource','importScripts'])Object.defineProperty(self,name,{value:()=>{throw Error('Сетевые обращения валидатора запрещены.')},writable:false,configurable:false});
function runLint(args,inputFiles){return new Promise((resolve,reject)=>{
 let stdout='',stderr='';
 const append=(old,text)=>(old+String(text)+'\n').slice(0,65536);
 Module({wasmBinary:self.SEDO_WASM,inputFiles,arguments:args,wasmMemory:new WebAssembly.Memory({initial:256,maximum:2048}),print:s=>{stdout=append(stdout,s)},printErr:s=>{stderr=append(stderr,s)},onExit:code=>resolve({code,stdout,stderr}),onAbort:()=>reject(Error('Недостаточно памяти или ошибка WASM.'))}).catch(reject);
})}
// libxml2 skips fixed constraints placed on attribute refs. Validate all actual
// uses in one auxiliary document with direct local-attribute declarations. The
// scalar types and fixed values are compiled from the same exact XSD closure.
function attributeProbes(pack,nodes){
 if(!pack.attribute_checks?.length&&!pack.attribute_type_checks?.length)return null;
 if(!nodes)throw Error('Не удалось разобрать атрибуты для дополнительной XSD-проверки.');
 const byPath=new Map();for(const rule of pack.attribute_checks||[]){const key=JSON.stringify(rule.path);if(!byPath.has(key))byPath.set(key,[]);byPath.get(key).push(rule)}
 const byType=new Map();for(const profile of pack.attribute_type_checks||[]){const map=new Map();for(const rule of profile.checks){const key=JSON.stringify(rule.path);if(!map.has(key))map.set(key,[]);map.get(key).push(rule)}byType.set(JSON.stringify(profile.type),map)}
 const escape=value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;').replace(/\t/g,'&#9;').replace(/\n/g,'&#10;').replace(/\r/g,'&#13;');
 const decode=value=>value.replace(/[\t\n\r]/g,' ').replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos);/g,(_,key)=>key[0]==='#'?String.fromCodePoint(key[1]==='x'?parseInt(key.slice(2),16):Number(key.slice(1))):({amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"})[key]);
 let prefix='sedoprobe';while(nodes.some(node=>prefix in node.namespaces))prefix+='x';
 const lines=['<'+prefix+':checks xmlns:'+prefix+'="'+escape(pack.supplementary_namespace)+'">'],subjects=new Map(),paths=new WeakMap(),typeScopes=new WeakMap();let total=lines[0].length;
 for(const node of nodes){
  const path=[...(node.parent?paths.get(node.parent):[]),[node.ns,node.name]];paths.set(node,path);
  const attributes=[];
  for(const match of nodes.source.slice(node.nameEnd,node.openEnd-1).matchAll(/([^\s=]+)\s*=\s*(['"])([\s\S]*?)\2/g)){
   const parts=match[1].split(':'),name=parts.at(-1),ns=parts.length>1?node.namespaces[parts[0]]||'':'';
   if(match[1]==='xmlns'||parts[0]==='xmlns')continue;
   attributes.push({ns,name,value:decode(match[3])});
  }
  let scopes=node.parent?typeScopes.get(node.parent):[];
  const xtype=attributes.find(a=>a.ns==='http://www.w3.org/2001/XMLSchema-instance'&&a.name==='type');
  if(xtype){const value=xtype.value.trim(),colon=value.indexOf(':'),name=colon<0?value:value.slice(colon+1),ns=node.namespaces[colon<0?'':value.slice(0,colon)]||'',profile=byType.get(JSON.stringify([ns,name]));if(profile)scopes=[...scopes,{depth:path.length,profile}]}
  typeScopes.set(node,scopes);
  const rules=[...(byPath.get(JSON.stringify(path))||[])];for(const scope of scopes)rules.push(...(scope.profile.get(JSON.stringify(path.slice(scope.depth)))||[]));
  const seen=new Set();
  for(const rule of rules){
   const identity=JSON.stringify([rule.attribute,rule.element]);if(seen.has(identity))continue;seen.add(identity);
   const attribute=attributes.find(a=>a.ns===rule.attribute[0]&&a.name===rule.attribute[1]);if(!attribute)continue;
   let namespaces='';for(const name in node.namespaces){if(name==='xml')continue;namespaces+=' xmlns'+(name?':'+name:'')+'="'+escape(node.namespaces[name])+'"'}
   const line='<'+prefix+':'+rule.element+namespaces+' value="'+escape(attribute.value)+'"/>';total+=line.length;
   if(total>32*1024*1024)throw Error('Дополнительная проверка XSD превышает 32 МиБ. Используйте Python-валидатор из ZIP.');
   lines.push(line);subjects.set(lines.length,{node,rule});
  }
 }
 lines.push('</'+prefix+':checks>');return {xml:lines.join('\n'),subjects};
}
self.onmessage=async event=>{
 const core=self.SedoValidationCore;
 try{
  const {bytes,pack}=event.data;const text=core.inspectBytes(bytes);core.checkPack(pack,pack);
  // The minimal libxml2 build lacks legacy iconv encodings. Decode strictly in
  // the browser and transcode an in-memory copy; source bytes are never edited.
  const xml=new TextEncoder().encode(text.replace(/\r\n?/g,'\n').replace(/^(<\?xml\s[^?]*\bencoding\s*=\s*)(["'])[^"']*\2/, '$1"UTF-8"'));
  const files=[{fileName:'input.xml',contents:xml},...Object.entries(pack.files).map(([fileName,value])=>({fileName,contents:core.decode64(value)}))];
  const parsed=await runLint(['--nonet','--xpath','concat(local-name(/*), "|", namespace-uri(/*))','input.xml'],files);
  if(parsed.code!==0){self.postMessage({type:'result',valid:false,errors:core.errors(parsed.stderr)});return}
  const locations=self.SedoValidationPaths,index=locations.index(typeof event.data.source==='string'&&event.data.source.length<=core.MAX_BYTES?event.data.source:text);
  if(parsed.stdout.trim()!==pack.root+'|'+(pack.namespace||'')){self.postMessage({type:'result',valid:false,errors:locations.locate([{root:true,line:index?.[0]?.line||1,message:'Неверный корневой элемент или пространство имён. Ожидается {'+(pack.namespace||'')+'}'+pack.root+'.'}],index,pack.fields||[],pack.field_namespaces||[])});return}
  const result=await runLint(['--nonet','--noout','--schema','message.xsd','input.xml'],files);
  if(![0,3,4].includes(result.code))throw Error('Не удалось выполнить XSD-проверку. '+result.stderr.slice(0,2000));
  let valid=result.code===0,errors=valid?[]:core.errors(result.stderr);
  if(valid){
   const probes=attributeProbes(pack,locations.index(text));
   if(probes&&probes.subjects.size){
    const probeFiles=files.map(file=>file.fileName==='input.xml'?{fileName:'input.xml',contents:new TextEncoder().encode(probes.xml)}:file);
    const checked=await runLint(['--nonet','--noout','--schema',pack.supplementary_schema,'input.xml'],probeFiles);
    if(![0,3,4].includes(checked.code))throw Error('Не удалось проверить fixed на ссылках атрибутов. '+checked.stderr.slice(0,2000));
    valid=checked.code===0;
    errors=valid?[]:core.errors(checked.stderr).map(error=>{
     const subject=probes.subjects.get(error.line);if(!subject)return error;
     const {node,rule}=subject,element=(node.ns?'{'+node.ns+'}':'')+node.name,attribute=(rule.attribute[0]?'{'+rule.attribute[0]+'}':'')+rule.attribute[1];
     const reason=error.message.replace(/^.*?Element '[^']+', attribute 'value':\s*/, '');
     return {line:node.line,message:"Element '"+element+"', attribute '"+attribute+"': "+reason};
    });
   }
  }
  const reviewLocations=locations.reviewLocations(index,pack.fields||[],pack.field_namespaces||[],event.data.reviewFields||[]);
  self.postMessage({type:'result',valid,reviewLocations,errors:valid?[]:locations.locate(errors,index,pack.fields||[],pack.field_namespaces||[])});
 }catch(error){self.postMessage({type:'result',valid:false,runtime:true,errors:[{line:null,message:String(error.message||error).slice(0,2000)}]})}
};
