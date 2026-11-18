"use strict";
// A blob worker inherits the opaque frame's connect-src 'none' CSP.
// This additional guard prevents even accidental attempts by the engine.
for(const name of ['fetch','XMLHttpRequest','WebSocket','EventSource','importScripts'])Object.defineProperty(self,name,{value:()=>{throw Error('Сетевые обращения валидатора запрещены.')},writable:false,configurable:false});
function runLint(args,inputFiles,filterBooleanAliases=false){return new Promise((resolve,reject)=>{
 let stdout='',stderr='',stderrTruncated=false,lintErrors=false,booleanAliases=0,otherOutput=false,pending=[],warning=false;
 const append=(old,text)=>(old+String(text)+'\n').slice(0,65536);
 const retain=s=>{
  for(const line of String(s).split('\n')){
   if(self.SedoValidationCore.isWarningDiagnostic(line)){warning=true;continue}
   if(/^[^\n]*:\d+:/.test(line))warning=false;
   if(warning)continue;
   const diagnosedError=/^[^\n]*:\d+:\s+(?:[^\n]*?Schemas validity|parser|namespace) error\s*:/i.test(line);
   lintErrors||=diagnosedError;
   // Excluding complete warning blocks keeps the byte budget for errors.
   otherOutput||=diagnosedError;
   stderrTruncated||=stderr.length+line.length+1>65536;
   stderr=append(stderr,line);
  }
 };
 function flush(){
  if(!pending.length)return;
  const message=pending.join('\n');pending=[];
  const alias=message.match(/^input\.xml:\d+: [^\n]*Schemas validity error : Element '[^']+': The actual value '[ \t\r\n]*(true|1|false|0)[ \t\r\n]*' does not match the fixed value constraint '[ \t\r\n]*(true|1|false|0)[ \t\r\n]*'\.$/);
  const typed=filterBooleanAliases==='typed'&&!alias&&/^input\.xml:\d+: [^\n]*Schemas validity error : Element '[^']+': The actual value '[\s\S]*' does not match the fixed value constraint '[\s\S]*'\.$/.test(message);
  if(typed||alias&&(['1','true'].includes(alias[1])===['1','true'].includes(alias[2])))booleanAliases++;
  else retain(message);
 }
 const printErr=s=>{
  if(!filterBooleanAliases){retain(s);return}
  if(/^input\.xml(?:[: ])/.test(s))flush();
  if(pending.length||/^input\.xml:\d+: [^\n]*Schemas validity error : Element '[^']+': The actual value '/.test(s))pending.push(s);
  else retain(s);
 };
 Module({wasmBinary:self.SEDO_WASM,inputFiles,arguments:[...args],wasmMemory:new WebAssembly.Memory({initial:256,maximum:2048}),print:s=>{stdout=append(stdout,s)},printErr,onExit:code=>{flush();resolve({code,stdout,stderr,stderrTruncated,lintErrors,booleanAliases,otherOutput})},onAbort:()=>reject(Error('Недостаточно памяти или ошибка WASM.'))}).catch(reject);
})}
// libxml2 can emit namespace/parser errors while xmllint still exits with zero.
// Keep success messages and warnings separate from diagnosed errors.
function lintDiagnostics(result){
 const {errors,truncated}=self.SedoValidationCore.readDiagnostics(result.stderr);
 return {errors,diagnosticsTruncated:result.stderrTruncated||truncated};
}
const MONTH_START=[0,31,59,90,120,151,181,212,243,273,304,334];
function calendarValue(kind,value){
 const match=/^(?:(-?[0-9]{4,})-([0-9]{2})-([0-9]{2})T)?([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})?$/.exec(value.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g,''));
 if(!match||Boolean(match[1])!==(kind==='dateTime'))return null;
 const [hour,minute,second]=match.slice(4,7).map(Number),digits=match[7]||'';
 let end=digits.length;while(digits[end-1]==='0')end--;
 const fraction=digits.slice(0,end);
 if(hour>24||minute>59||second>59||hour===24&&(minute||second||fraction))return null;
 const zone=match[8];let offset=0;
 if(zone&&zone!=='Z'){
  const hours=Number(zone.slice(1,3)),minutes=Number(zone.slice(4));
  if(hours>14||minutes>59||hours===14&&minutes)return null;
  offset=(zone[0]==='-'?-1:1)*(hours*60+minutes);
 }
 const seconds=(hour*60+minute-offset)*60+second,dayDelta=Math.floor(seconds/86400);
 const clock=seconds-dayDelta*86400;
 if(kind==='time')return [Boolean(zone),clock,fraction];
 const year=BigInt(match[1]),month=Number(match[2]),day=Number(match[3]);
 if(!year||month<1||month>12)return null;
 // The schema validates the date itself. A Gregorian day number preserves
 // the date when the offset crosses midnight, including at year boundaries.
 const leap=year%4n===0n&&(year%100n!==0n||year%400n===0n),prior=year-1n;
 const floor=(value,by)=>(value<0n?value-by+1n:value)/by;
 const ordinal=365n*prior+floor(prior,4n)-floor(prior,100n)+floor(prior,400n)+(year<0n?366n:0n)
  +BigInt(MONTH_START[month-1]+day+(leap&&month>2?1:0));
 return [Boolean(zone),ordinal+BigInt(dayDelta),clock,fraction];
}
// libxml2 skips fixed constraints placed on attribute refs. Validate all actual
// uses in one auxiliary document with direct local-attribute declarations. The
// scalar types and fixed values are compiled from the same exact XSD closure.
function attributeProbes(pack,source){
 if(!pack.attribute_checks?.length&&!pack.attribute_type_checks?.length&&!pack.attribute_element_checks?.length)return null;
 const text=source.replace(/\r\n?/g,'\n');
 const guardedLayouts=new Set();let maxChildren=0;
 function indexProfile(checks=[],paths=[],wildcards=[],anyTypes=[]){
  const rules=new Map(),modes=new Map();
  const add=(map,path,value)=>{const key=JSON.stringify(path);if(!map.has(key))map.set(key,[]);map.get(key).push(value)};
  for(const rule of checks){
   if(!rule.guards){add(rules,rule.path,rule);continue}
   const guards=rule.guards.map(guard=>({up:guard.up,layouts:guard.layouts.map(({children,index})=>{
    const layout=JSON.stringify(children);guardedLayouts.add(layout);maxChildren=Math.max(maxChildren,children.length);
    return {layout,index};
   })}));
   add(rules,rule.path,{...rule,guards});
  }
  for(const w of wildcards)add(modes,w.path,{...w,names:w.namespace.split(/\s+/),explicit:new Set(w.explicit.map(name=>JSON.stringify(name)))});
  return {rules,modes,wildcards:new Set(paths.map(path=>JSON.stringify(path))),anyTypes:new Set(anyTypes.map(path=>JSON.stringify(path)))};
 }
 const rootProfile=indexProfile(pack.attribute_checks,pack.attribute_wildcard_paths,pack.attribute_wildcards,pack.attribute_any_type_paths);
 const profiles=(items,key)=>new Map((items||[]).map(p=>[JSON.stringify(p[key]),indexProfile(p.checks,p.wildcard_paths,p.wildcards,p.any_type_paths)]));
 const byType=profiles(pack.attribute_type_checks,'type'),byElement=profiles(pack.attribute_element_checks,'element');
 const positions=new Map();
 if(guardedLayouts.size){
  // Finish each sibling list before matching it, including siblings after
  // the probe. Keep offsets only for layouts used by a supplementary rule.
  const pending=[];
  const finish=()=>{
   const {children,offsets}=pending.pop();
   if(!children.length||children.length>maxChildren)return;
   const layout=JSON.stringify(children);
   if(guardedLayouts.has(layout))offsets.forEach((offset,index)=>positions.set(offset,{layout,index}));
  };
  for(const node of self.SedoValidationPaths.scan(text)){
   while(pending.length&&pending.at(-1).node!==node.parent)finish();
   const parent=pending.at(-1);
   if(parent&&parent.children.length<=maxChildren){
    parent.children.push([node.ns,node.name]);parent.offsets.push(node.nameStart);
   }
   pending.push({node,children:[],offsets:[]});
  }
  while(pending.length)finish();
 }
 const matchesGuard=(node,guard)=>{
  for(let i=0;i<guard.up&&node;i++)node=node.parent;
  const actual=positions.get(node?.nameStart);
  return actual&&guard.layouts.some(({layout,index})=>actual.layout===layout&&actual.index===index);
 };
 const globals=new Set((pack.attribute_known_elements||[]).map(name=>JSON.stringify(name)));
 const typeWhitespace=new Map((pack.element_type_whitespace||[]).map(item=>[JSON.stringify(item.type),item.mode]));
 const matchesNamespace=(w,ns)=>w.names.some(value=>value==='##any'||(value==='##other'?!!ns&&ns!==w.target_namespace:value==='##targetNamespace'?ns===w.target_namespace:value==='##local'?!ns:value===ns));
 const escape=value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;').replace(/\t/g,'&#9;').replace(/\n/g,'&#10;').replace(/\r/g,'&#13;');
 const decodeText=value=>value.replace(/&(#x[\da-fA-F]+|#\d+|amp|lt|gt|quot|apos);/g,(_,key)=>key[0]==='#'?String.fromCodePoint(key[1]==='x'?parseInt(key.slice(2),16):Number(key.slice(1))):({amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"})[key]);
 const decode=value=>decodeText(value.replace(/[\t\n\r]/g,' '));
 const content=node=>{
  if(/\/\s*>$/.test(text.slice(node.nameEnd,node.openEnd)))return '';
  // Only scalar probes reach here, after native XSD validation. Preserve text
  // whitespace and CDATA while excluding comments and processing instructions.
  let value='';for(const match of text.slice(node.openEnd).matchAll(/<!\[CDATA\[([\s\S]*?)\]\]>|<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<\/[^>]+>|([^<]+)/g)){
   if(match[0].startsWith('</'))break;
   if(match[1]!==undefined)value+=match[1];else if(match[2]!==undefined)value+=decodeText(match[2]);
  }return value;
 };
 const lines=['<sedoprobe:checks xmlns:sedoprobe="'+escape(pack.supplementary_namespace)+'">'],subjects=new Map(),contexts=new WeakMap();let total=lines[0].length;
 for(const node of self.SedoValidationPaths.scan(text)){
  const parent=contexts.get(node.parent),qname=[node.ns,node.name],key=JSON.stringify(qname);
  if(parent?.skip){contexts.set(node,parent);continue}
  const parentPath=parent?.path||[],path=[...parentPath,qname];
  let scopes=parent?.scopes||[{depth:0,profile:rootProfile}],mode=parent?.lax?'lax':'';
  if(parent){
   for(const {depth,profile} of scopes){
    if(!profile.modes.size)continue;
    const candidates=profile.modes.get(JSON.stringify(parentPath.slice(depth)))||[];
    const match=candidates.find(w=>!w.explicit.has(key)&&matchesNamespace(w,node.ns));
    if(match)mode=match.process;
   }
  }
  if(mode==='skip'){contexts.set(node,{skip:true});continue}
  const attributes=[];
  for(const match of text.slice(node.nameEnd,node.openEnd-1).matchAll(/([^\s=]+)\s*=\s*(['"])([\s\S]*?)\2/g)){
   const parts=match[1].split(':'),name=parts.at(-1),ns=parts.length>1?node.namespaces[parts[0]]||'':'';
   if(match[1]==='xmlns'||parts[0]==='xmlns')continue;
   attributes.push({ns,name,value:decode(match[3])});
  }
  const rules=[];let wildcard=mode==='lax'||mode==='strict',anyType=false;
  for(const {depth,profile} of scopes){
   const relative=JSON.stringify(path.slice(depth));
   rules.push(...(profile.rules.get(relative)||[]));wildcard||=profile.wildcards.has(relative);anyType||=profile.anyTypes.has(relative);
  }
  const localProfiles=[];
  if(wildcard){const element=byElement.get(key);if(element){localProfiles.push(element);anyType||=element.anyTypes.has('[]')}}
  let laxChildren=anyType||(mode==='lax'&&!globals.has(key));
  const xtype=attributes.find(a=>a.ns==='http://www.w3.org/2001/XMLSchema-instance'&&a.name==='type');
  const fixedRules=rules.filter(rule=>rule.element_value);
  for(const profile of localProfiles)fixedRules.push(...(profile.rules.get('[]')||[]).filter(rule=>rule.element_value));
  let whitespace;
  if(xtype){
   const value=xtype.value.trim(),colon=value.indexOf(':'),name=colon<0?value:value.slice(colon+1),ns=node.namespaces[colon<0?'':value.slice(0,colon)]||'';
   whitespace=ns==='http://www.w3.org/2001/XMLSchema'?({string:'preserve',anySimpleType:'preserve',normalizedString:'replace'}[name]||'collapse'):typeWhitespace.get(JSON.stringify([ns,name]));
   laxChildren=ns==='http://www.w3.org/2001/XMLSchema'&&name==='anyType';
   if(pack.attribute_type_profiles_complete){
    // Named profiles describe the full effective type, including inherited uses.
    scopes=[];rules.length=0;localProfiles.length=0;
   }
   const profile=byType.get(JSON.stringify([ns,name]));if(profile)localProfiles.push(profile);
  }
  if(localProfiles.length){
   scopes=[...scopes,...localProfiles.map(profile=>({depth:path.length,profile}))];
   for(const profile of localProfiles)rules.push(...(profile.rules.get('[]')||[]));
  }
  rules.push(...fixedRules);
  contexts.set(node,{path,scopes,lax:laxChildren});
  const seen=new Set();
  for(const rule of rules){
   if(rule.guards&&!rule.guards.every(guard=>matchesGuard(node,guard)))continue;
   const identity=JSON.stringify([rule.attribute,rule.element]);if(seen.has(identity))continue;seen.add(identity);
   const attribute=rule.element_value?{value:content(node)}:attributes.find(a=>a.ns===rule.attribute[0]&&a.name===rule.attribute[1]);if(!attribute||rule.element_value&&attribute.value==='')continue;
   if(rule.element_value&&['replace','collapse'].includes(whitespace)){
    attribute.value=attribute.value.replace(/[\t\r\n]/g,' ');
    if(whitespace==='collapse')attribute.value=attribute.value.replace(/ +/g,' ').replace(/^ | $/g,'');
   }
   let namespaces='';for(const name in node.namespaces){if(name==='xml')continue;namespaces+=' xmlns'+(name?':'+name:'')+'="'+escape(node.namespaces[name])+'"'}
   let prefix='sedoprobe';while(prefix in node.namespaces)prefix+='x';
   const line='<'+prefix+':'+rule.element+' xmlns:'+prefix+'="'+escape(pack.supplementary_namespace)+'"'+namespaces+' value="'+escape(attribute.value)+'"/>';total+=line.length;
   if(total>32*1024*1024)throw Error('Дополнительная проверка XSD превышает 32 МиБ. Используйте Python-валидатор из ZIP.');
   lines.push(line);subjects.set(lines.length,{node:{line:node.line,ns:node.ns,name:node.name},rule,
    calendarValue:rule.calendar_fixed?attribute.value:undefined});
  }
 }
 lines.push('</sedoprobe:checks>');return {xml:lines.join('\n'),subjects};
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
  if(parsed.code!==0||parsed.lintErrors){self.postMessage({type:'result',valid:false,...lintDiagnostics(parsed)});return}
  const locations=self.SedoValidationPaths,index=locations.index(typeof event.data.source==='string'&&event.data.source.length<=core.MAX_BYTES?event.data.source:text);
  if(parsed.stdout.trim()!==pack.root+'|'+(pack.namespace||'')){self.postMessage({type:'result',valid:false,errors:locations.locate([{root:true,line:index?.[0]?.line||1,message:'Неверный корневой элемент или пространство имён. Ожидается {'+(pack.namespace||'')+'}'+pack.root+'.'}],index,pack.fields||[],pack.field_namespaces||[])});return}
  const schemaArgs=['--nonet','--noout','--schema','message.xsd','input.xml'];
  const typedFixed=[pack.attribute_checks||[],...(pack.attribute_type_checks||[]).map(p=>p.checks),...(pack.attribute_element_checks||[]).map(p=>p.checks)].some(rules=>rules.some(rule=>rule.element_value));
  let result=await runLint(schemaArgs,files,pack.boolean_fixed_replacements?(typedFixed?'typed':true):false);
  if(![0,3,4].includes(result.code))throw Error('Не удалось выполнить XSD-проверку. '+result.stderr.slice(0,2000));
  if([3,4].includes(result.code)&&result.booleanAliases){
   // Keep all original lexical restrictions, including the actual xsi:type.
   // Scalar element fixed constraints become defaults; typed probes below
   // enforce their equality. Legacy packs relax only boolean aliases.
   const alternate=files.map(file=>{
    const name=pack.boolean_fixed_replacements[file.fileName];
    return name?{...file,contents:core.decode64(pack.files[name])}:file;
   });
   const second=await runLint(schemaArgs,alternate);
   if(![0,3,4].includes(second.code))throw Error('Не удалось проверить логическое значение fixed. '+second.stderr.slice(0,2000));
   if(!result.otherOutput)result=second;
   else if(second.code!==0){
    // Keep wrong boolean values (reported by the primary) and any string
    // fixed diagnostics that looked like boolean aliases until the second pass.
    const seen=new Set(result.stderr.split('\n'));
    const extra=second.stderr.split('\n').filter(line=>!seen.has(line)).join('\n');
    if(extra){result.stderrTruncated||=result.stderr.length+extra.length>65536;result.stderr=(result.stderr+extra).slice(0,65536)}
    result.stderrTruncated||=second.stderrTruncated;
   }
  }
  let valid=result.code===0&&!result.lintErrors;
  let {errors,diagnosticsTruncated}=valid?{errors:[],diagnosticsTruncated:false}:lintDiagnostics(result);
  if(valid){
   const probes=attributeProbes(pack,text);
   if(probes&&probes.subjects.size){
    const probeFiles=files.map(file=>file.fileName==='input.xml'?{fileName:'input.xml',contents:new TextEncoder().encode(probes.xml)}:file);
    const checked=await runLint(['--nonet','--noout','--schema',pack.supplementary_schema,'input.xml'],probeFiles);
    if(![0,3,4].includes(checked.code))throw Error('Не удалось проверить fixed на ссылках атрибутов. '+checked.stderr.slice(0,2000));
    valid=checked.code===0&&!checked.lintErrors;
    if(!valid)({errors,diagnosticsTruncated}=lintDiagnostics(checked));
    const failed=new Set(errors.map(error=>error.line));
    errors=errors.map(error=>{
     const subject=probes.subjects.get(error.line);if(!subject)return error;
     const {node,rule}=subject,element=(node.ns?'{'+node.ns+'}':'')+node.name,attribute=(rule.attribute[0]?'{'+rule.attribute[0]+'}':'')+rule.attribute[1];
     const reason=error.message.replace(/^.*?Element '[^']+', attribute 'value':\s*/, '');
     return {line:node.line,message:"Element '"+element+"'"+(rule.element_value?'':", attribute '"+attribute+"'")+': '+reason};
    });
    for(const [line,{node,rule,calendarValue:actual}] of probes.subjects){
     if(!rule.calendar_fixed||failed.has(line))continue;
     const [kind,fixed]=rule.calendar_fixed,value=calendarValue(kind,actual),expected=calendarValue(kind,fixed);
     if(value&&expected&&value.every((part,i)=>part===expected[i]))continue;
     valid=false;
     if(errors.length===30){diagnosticsTruncated=true;break}
     const element=(node.ns?'{'+node.ns+'}':'')+node.name;
     const reason=value?`The actual value '${actual}' does not match the fixed value constraint '${fixed}'.`
      :`'${actual}' is not a valid value of the atomic type 'xs:${kind}'.`;
     const message=`Element '${element}': ${reason}`;
     diagnosticsTruncated||=message.length>2000;
     errors.push({line:node.line,message:message.slice(0,2000)});
    }
   }
  }
  const reviewLocations=locations.reviewLocations(index,pack.fields||[],pack.field_namespaces||[],event.data.reviewFields||[]);
  self.postMessage({type:'result',valid,reviewLocations,diagnosticsTruncated,errors:valid?[]:locations.locate(errors,index,pack.fields||[],pack.field_namespaces||[])});
 }catch(error){self.postMessage({type:'result',valid:false,runtime:true,errors:[{line:null,message:String(error.message||error).slice(0,2000)}]})}
};
