"use strict";
// A blob worker inherits the opaque frame's connect-src 'none' CSP.
// This additional guard prevents even accidental attempts by the engine.
for(const name of ['fetch','XMLHttpRequest','WebSocket','EventSource','importScripts'])Object.defineProperty(self,name,{value:()=>{throw Error('Сетевые обращения валидатора запрещены.')},writable:false,configurable:false});
function runLint(args,inputFiles,{booleanAliases:filterBooleanAliases=false,typedFixed=false,calendarAttributes=false,compoundAttributes=false,probeLocations,assessedIds}={}){return new Promise((resolve,reject)=>{
 let stdout='',stderr='',stderrTruncated=false,lintErrors=false,booleanAliases=0,otherOutput=false,pending=[],warning=false;
 const invalidLines=new Map();let unlocatedError=false;
 const append=(old,text)=>(old+String(text)+'\n').slice(0,65536);
 const retain=s=>{
  for(let line of String(s).split('\n')){
   let probeID;
   if(probeLocations){
    if(/^(?:input|probe-\d+)\.xml (?:validates|fails to validate)$/.test(line))continue;
    const location=/^([^:\n]+):(\d+):/.exec(line);
    probeID=location?probeLocations.get(location[1])?.[Number(location[2])-2]:undefined;
    if(probeID!==undefined)line='input.xml:'+probeID+':'+line.slice(location[0].length);
   }
   if(self.SedoValidationCore.isWarningDiagnostic(line)){warning=true;continue}
   if(/^[^\n]*:\d+:/.test(line))warning=false;
   if(warning)continue;
   const diagnosedError=/^[^\n]*:\d+:\s+(?:[^\n]*?Schemas validity|parser|namespace) error\s*:/i.test(line);
   if(probeLocations&&diagnosedError){
    if(probeID===undefined||!/^input\.xml:\d+: [^\n]*Schemas validity error :/.test(line))unlocatedError=true;
    else{
     const facet=!/does not match the fixed value constraint|No match found for key-sequence/.test(line);
     invalidLines.set(probeID,facet||invalidLines.get(probeID)||false);
    }
   }
   if(assessedIds&&diagnosedError){
    const id=line.match(/attribute '\{http:\/\/www\.w3\.org\/2001\/XMLSchema-instance\}type': ':sedoId(\d+)' is not a valid value/);
    if(id)assessedIds.add(Number(id[1]));
   }
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
  const typed=typedFixed&&!alias&&/^input\.xml:\d+: [^\n]*Schemas validity error : Element '[^']+': The actual value '[\s\S]*' does not match the fixed value constraint '[\s\S]*'\.$/.test(message);
  const calendar=(calendarAttributes||compoundAttributes)&&message.match(/^input\.xml:\d+: [^\n]*Schemas validity error : Element '[^']+', attribute '[^']+': The value '([\s\S]*)' does not match the fixed value constraint '([\s\S]*)'\.$/);
  if(typed||calendar&&(compoundAttributes||sameCalendarValue(calendarKind(calendar[2]),calendar[1],calendar[2]))
    ||alias&&(['1','true'].includes(alias[1])===['1','true'].includes(alias[2])))booleanAliases++;
  else retain(message);
 }
 const printErr=s=>{
  if(!filterBooleanAliases){retain(s);return}
  if(/^input\.xml(?:[: ])/.test(s))flush();
  if(pending.length||/^input\.xml:\d+: [^\n]*Schemas validity error : Element '[^']+': The actual value '/.test(s)
    ||(calendarAttributes||compoundAttributes)&&/^input\.xml:\d+: [^\n]*Schemas validity error : Element '[^']+', attribute '[^']+': The value '/.test(s))pending.push(s);
  else retain(s);
 };
 Module({wasmBinary:self.SEDO_WASM,inputFiles,arguments:[...args],wasmMemory:new WebAssembly.Memory({initial:256,maximum:2048}),print:s=>{stdout=append(stdout,s)},printErr,onExit:code=>{flush();resolve({code,stdout,stderr,stderrTruncated,lintErrors,booleanAliases,otherOutput,invalidLines,unlocatedError})},onAbort:()=>reject(Error('Недостаточно памяти или ошибка WASM.'))}).catch(reject);
})}
// libxml2 can emit namespace/parser errors while xmllint still exits with zero.
// Keep success messages and warnings separate from diagnosed errors.
function lintDiagnostics(result){
 const {errors,truncated}=self.SedoValidationCore.readDiagnostics(result.stderr);
 return {errors,diagnosticsTruncated:result.stderrTruncated||truncated};
}
const MONTH_START=[0,31,59,90,120,151,181,212,243,273,304,334];
function calendarKind(value){
 if(/^[ \t\r\n]*-?P/.test(value))return 'duration';
 return value.includes('T')?'dateTime':'time';
}
function sameCalendarValue(kind,left,right){
 const a=calendarValue(kind,left),b=calendarValue(kind,right);
 return a&&b&&a.every((part,i)=>part===b[i]);
}
const order=(a,b)=>a>b?1:a<b?-1:0;
const floorBy=(value,by)=>(value<0n?value-by+1n:value)/by;
const durationDates=[[1696n,9n],[1697n,2n],[1903n,3n],[1903n,7n]];
function durationInstant(year,month,[negative,months,seconds,fraction]){
 const index=year*12n+month-1n+(negative?-months:months);
 year=floorBy(index,12n);
 month=Number(index-year*12n);
 const previous=year-1n,leap=year%4n===0n&&(year%100n!==0n||year%400n===0n);
 const days=365n*previous+floorBy(previous,4n)-floorBy(previous,100n)+floorBy(previous,400n)
  +BigInt(MONTH_START[month]+(leap&&month>=2?1:0));
 return [days*86400n+(negative?-seconds:seconds),fraction,fraction?(negative?-1:1):0];
}
function orderInstant([a,frac,sign],[b,other,otherSign]){
 return order(a,b)||order(sign,otherSign)||sign*order(frac,other);
}
function calendarOrder(kind,left,right){
 const a=calendarValue(kind,left),b=calendarValue(kind,right);
 if(!a||!b)return null;
 if(a.every((part,i)=>part===b[i]))return 0;
 if(kind==='duration'){
  const orders=new Set(durationDates.map(([year,month])=>
   orderInstant(durationInstant(year,month,a),durationInstant(year,month,b))));
  return orders.size===1&&!orders.has(0)?orders.values().next().value:null;
 }
 const clock=parts=>kind==='dateTime'?[parts[1]*86400n+BigInt(parts[2]),parts[3],1]:[BigInt(parts[1]),parts[2],1];
 if(a[0]===b[0])return orderInstant(clock(a),clock(b));
 const [known,unknown]=a[0]?[clock(a),clock(b)]:[clock(b),clock(a)];
 const outside=orderInstant(known,[unknown[0]+50400n,unknown[1],1])>0?1:
  orderInstant(known,[unknown[0]-50400n,unknown[1],1])<0?-1:null;
 return outside===null||a[0]?outside:-outside;
}
function calendarValue(kind,value){
 value=value.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g,'');
 if(kind==='duration'){
  const match=/^(-)?P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d*)?|\.\d+)S)?)?$/.exec(value);
  if(!match||!match.slice(2).some(Boolean)||value.includes('T')&&!match.slice(5).some(Boolean))return null;
  const [,negative,years,months,days,hours,minutes,seconds]=match;
  const totalMonths=BigInt(years||0)*12n+BigInt(months||0);
  const [whole,fraction='']=(seconds||'0').split('.'),tail=fraction.replace(/0+$/,'');
  const totalSeconds=BigInt(days||0)*86400n+BigInt(hours||0)*3600n+BigInt(minutes||0)*60n+BigInt(whole||0);
  // Match the independent month and second components used in edition comparisons.
  return [Boolean(negative&&(totalMonths||totalSeconds||tail)),totalMonths,totalSeconds,tail];
 }
 const match=/^(?:(-?[0-9]{4,})-([0-9]{2})-([0-9]{2})T)?([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})?$/.exec(value);
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
const escapeAttribute=value=>String(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;').replace(/\t/g,'&#9;').replace(/\n/g,'&#10;').replace(/\r/g,'&#13;');
const decodeText=value=>value.replace(/&(#x[\da-fA-F]+|#\d+|amp|lt|gt|quot|apos);/g,(_,key)=>key[0]==='#'?String.fromCodePoint(key[1]==='x'?parseInt(key.slice(2),16):Number(key.slice(1))):({amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"})[key]);
const XSD='http://www.w3.org/2001/XMLSchema';
const DOCUMENT_TYPES=new Set(['ID','IDREF','IDREFS','ENTITY','ENTITIES']);
function legacyRequiresIdProfiles(files){
 for(const {fileName,contents} of files){
  if(fileName==='input.xml'||/^schemas\/(attribute-|boolean-fixed-)/.test(fileName))continue;
  const text=new TextDecoder().decode(contents).replace(/\r\n?/g,'\n');
  if(!text.includes('ID'))continue;
  for(const node of self.SedoValidationPaths.scan(text)){
   if(node.ns!==XSD)continue;
   for(const match of text.slice(node.nameEnd,node.openEnd-1).matchAll(/([^\s=]+)\s*=\s*(['"])([\s\S]*?)\2/g)){
    if(!['type','base','memberTypes'].includes(match[1]))continue;
    if(decodeText(match[3]).trim().split(/\s+/).some(q=>{
     const i=q.indexOf(':');return q.slice(i+1)==='ID'&&node.namespaces[i<0?'':q.slice(0,i)]===XSD;
    }))return true;
   }
  }
 }
 return false;
}
function legacyDocumentValueProbes(pack,files){
 // Format 1 packs originally lacked the probe metadata. Resolve the types
 // from the supplementary schema, including imported and chameleon types.
 const data=new Map(files.map(file=>[file.fileName,file.contents])),documents=new Map(),types=new Map();
 const qname=(namespace,name)=>JSON.stringify([namespace,name]);
 function read(file){
  if(documents.has(file))return documents.get(file);
  const text=new TextDecoder('utf-8',{fatal:true}).decode(data.get(file)).replace(/\r\n?/g,'\n');
  let root;
  for(const node of self.SedoValidationPaths.scan(text)){
   if(node.ns!==XSD||node.name==='annotation'||node.parent&&!node.parent.xsdChildren)continue;
   node.xsdChildren=[];
   node.xsdAttrs=Object.create(null);
   for(const attr of text.slice(node.nameEnd,node.openEnd-1).matchAll(/([^\s=]+)\s*=\s*(['"])([\s\S]*?)\2/g))
    node.xsdAttrs[attr[1]]=decodeText(attr[3]).trim();
   if(node.parent)node.parent.xsdChildren.push(node);
   else root=node;
  }
  documents.set(file,root);
  return root;
 }
 const entry=read(pack.supplementary_schema),pending=[[pack.supplementary_schema,'']],seen=new Set();
 while(pending.length){
  const [file,inherited]=pending.pop(),root=read(file);
  if(!root)continue;
  const declared=root.xsdAttrs.targetNamespace,namespace=declared||inherited,key=qname(namespace,file);
  if(seen.has(key))continue;
  seen.add(key);
  for(const node of root.xsdChildren){
   if(node.name==='simpleType'||node.name==='complexType'){
    types.set(qname(namespace,node.xsdAttrs.name),[node,declared?'':namespace]);
   }else if(['import','include','redefine'].includes(node.name)&&node.xsdAttrs.schemaLocation){
    const url=new URL(node.xsdAttrs.schemaLocation,'https://schema.invalid/'+file);
    const target=url.pathname.slice(1);
    if(url.origin==='https://schema.invalid'&&data.has(target))pending.push([target,node.name==='import'?'':namespace]);
   }
  }
 }
 function hasDocumentType(element){
  const pending=[[element,'']],seen=new Map();
  while(pending.length){
   const [node,adopted]=pending.pop();
   if(seen.get(node)?.has(adopted))continue;
   if(!seen.has(node))seen.set(node,new Set());
   seen.get(node).add(adopted);
   for(const attr of ['type','base','itemType','memberTypes']){
    for(const token of (node.xsdAttrs[attr]||'').split(/\s+/).filter(Boolean)){
     const colon=token.indexOf(':'),name=token.slice(colon+1);
     const namespace=colon<0?node.namespaces['']||adopted:node.namespaces[token.slice(0,colon)];
     if(namespace===XSD){if(DOCUMENT_TYPES.has(name))return true}
     else {
      const target=types.get(qname(namespace,name));
      if(!target)return true; // Keep an unresolved legacy probe independent.
      pending.push(target);
     }
    }
   }
   pending.push(...node.xsdChildren.map(child=>[child,adopted]));
  }
  return false;
 }
 return new Set((entry?.xsdChildren||[]).filter(node=>node.name==='element'&&node.xsdAttrs.name!=='checks'
  &&hasDocumentType(node)).map(node=>node.xsdAttrs.name));
}
function probeLine(pack,name,value,namespaces){
 let bindings='',prefix='sedoprobe';
 for(const key in namespaces){if(key!=='xml')bindings+=' xmlns'+(key?':'+key:'')+'="'+escapeAttribute(namespaces[key])+'"'}
 while(prefix in namespaces)prefix+='x';
 return '<'+prefix+':'+name+' xmlns:'+prefix+'="'+escapeAttribute(pack.supplementary_namespace)+'"'+bindings+' value="'+escapeAttribute(value)+'"/>';
}
function probeBatch(pack,files,documentTypes,budget,deduplicate=true){
 const open='<sedoprobe:checks xmlns:sedoprobe="'+escapeAttribute(pack.supplementary_namespace)+'">\n';
 const close='</sedoprobe:checks>',encoder=new TextEncoder(),wrapper=encoder.encode(open+close).length;
 const ordinary=[],isolated=[],seen=new Map();let count=0;
 function addLine(name,line){
  if(deduplicate&&seen.has(line))return seen.get(line);
  const document=documentTypes.has(name)?isolated:ordinary;
  budget.size+=encoder.encode(line).length+1+(document===isolated||!ordinary.length?wrapper:0);
  if(budget.size>32*1024*1024)throw Error('Дополнительная проверка XSD превышает 32 МиБ. Используйте Python-валидатор из ZIP.');
  const id=++count;
  document.push({id,line});
  if(deduplicate)seen.set(line,id);
  return id;
 }
 return {
  add(name,value,namespaces){
   return addLine(name,probeLine(pack,name,value,namespaces));
  },
  addPair(name,actual,expected,namespaces,fixedNamespaces){
   return addLine(name,'<sedoprobe:'+name+'>'+probeLine(pack,'expected',expected,fixedNamespaces)
    +probeLine(pack,'actual',actual,namespaces)+'</sedoprobe:'+name+'>');
  },
  async check(diagnostics=false){
   const invalid=new Map(),schemaFiles=files.filter(file=>file.fileName!=='input.xml');
   let errors=[],diagnosticsTruncated=false;
   // xmllint can reuse the schema across several source documents. Each
   // contextual probe still gets its own XML document and ID table.
   for(let offset=0;offset<isolated.length||offset===0&&ordinary.length;offset+=128){
    const locations=new Map(),input=[...schemaFiles];
    function document(name,rows){
     const contents=encoder.encode(open+rows.map(row=>row.line+'\n').join('')+close);
     locations.set(name,rows.map(row=>row.id));
     input.push({fileName:name,contents});
    }
    if(offset===0&&ordinary.length)document('input.xml',ordinary);
    for(const probe of isolated.slice(offset,offset+128))document('probe-'+probe.id+'.xml',[probe]);
    const result=await runLint(['--nonet','--noout','--schema',pack.supplementary_schema,...locations.keys()],input,{probeLocations:locations});
    if(result.unlocatedError||!(result.code===0||result.code===3&&result.invalidLines.size))
     throw Error('Не удалось выполнить дополнительную проверку типов XSD. '+result.stderr.slice(0,2000));
    for(const pair of result.invalidLines)invalid.set(...pair);
    if(diagnostics&&result.invalidLines.size){
     const found=lintDiagnostics(result);
     errors.push(...found.errors);
     errors.sort((left,right)=>left.line-right.line);
     diagnosticsTruncated||=found.diagnosticsTruncated||errors.length>30;
     errors=errors.slice(0,30);
    }
   }
   return {invalid,errors,diagnosticsTruncated};
  }
 };
}
const listTokens=value=>value.match(/[^ \t\r\n]+/g)||[];
function normalizeWhitespace(value,mode){
 if(mode!=='replace'&&mode!=='collapse')return value;
 value=value.replace(/[\t\r\n]/g,' ');
 return mode==='collapse'?value.replace(/ +/g,' ').replace(/^ | $/g,''):value;
}
function prepareEnumComparison(shape,actual,expected,namespaces,scope,batch){
 const probes=new Map();
 const key=(name,left,right)=>JSON.stringify([name,left,right]);
 function register(type,left,right){
  if(typeof type==='string'){
   if(type.startsWith('item'))probes.set(key(type,left,right),batch.addPair(type,left,right,namespaces,scope));
   return;
  }
  const [kind,nested,mode]=type;
  if(kind==='list'){
   const lhs=listTokens(left),rhs=listTokens(right);
   if(lhs.length===rhs.length)lhs.forEach((token,index)=>register(nested,token,rhs[index]));
  }else if(kind==='union'){
   for(const [,member] of nested)register(member,left,right);
  }else if(kind==='whitespace'){
   register(nested,normalizeWhitespace(left,mode),normalizeWhitespace(right,mode));
  }else if(kind!=='limited')register(nested,left,right);
 }
 register(shape,actual,expected);
 return (invalid,name,left,right)=>{
  const line=probes.get(key(name,left,right));
  if(line===undefined)return 'unknown';
  return !invalid.has(line)?'equal':invalid.get(line)?'unknown':'different';
 };
}
function resolveFacets(content,facets,value,namespaces,batch){
 const resolve=resolveCompound(content,value,namespaces,batch),enums=[],bounds=[];
 for(const [facet,lexical,scope] of facets){
  if(facet==='enumeration')enums.push([resolveCompound(content,lexical,scope,batch),
   prepareEnumComparison(content,value,lexical,namespaces,scope,batch)]);
  else bounds.push([facet,lexical]);
 }
 return invalid=>{
  const actual=resolve(invalid),[kind,lexical]=actual;
  if(kind==='excluded'||kind==='unknown')return actual;
  for(const [facet,bound] of bounds){
   const cmp=['dateTime','time','duration'].includes(kind)?calendarOrder(kind,lexical,bound):null;
   if(cmp===null||facet==='minInclusive'&&cmp<0||facet==='minExclusive'&&cmp<=0
     ||facet==='maxInclusive'&&cmp>0||facet==='maxExclusive'&&cmp>=0)return ['excluded',null];
  }
  if(!enums.length)return actual;
  const statuses=enums.map(([expected,compare])=>compareCompound(actual,expected(invalid),
   (name,left,right)=>compare(invalid,name,left,right))[0]);
  if(statuses.includes('equal'))return actual;
  return [statuses.includes('unknown')||statuses.includes('native')?'unknown':'excluded',null];
 };
}
function resolveCompound(shape,value,namespaces,batch){
 if(typeof shape==='string')return ()=>[shape,value];
 const [kind,content,itemProbe]=shape;
 if(kind==='limited')return ()=>['unknown',null];
 if(kind==='whitespace')return resolveCompound(content,normalizeWhitespace(value,itemProbe),namespaces,batch);
 if(kind==='duration-enumeration'){
  if(!itemProbe.some(bound=>sameCalendarValue('duration',value,bound)))return ()=>['excluded',null];
  return resolveCompound(content,value,namespaces,batch);
 }
 if(kind==='calendar-facets')return resolveFacets(content,itemProbe,value,namespaces,batch);
 if(kind==='list'){
  const items=listTokens(value).map(item=>resolveCompound(content,item,namespaces,batch));
  return invalid=>{
   const resolved=items.map(get=>get(invalid));
   const failure=['excluded','unknown'].find(status=>resolved.some(item=>item[0]===status));
   return failure?[failure,null]:[kind,[itemProbe,resolved]];
  };
 }
 const members=content.map(([name,member])=>[batch.add(name,value,namespaces),resolveCompound(member,value,namespaces,batch)]);
 return invalid=>{
  let excluded=false;
  for(const [line,resolve] of members){
   if(invalid.has(line))continue;
   const result=resolve(invalid);
   if(result[0]!=='excluded')return result;
   excluded=true;
  }
  return [excluded?'excluded':'unknown',null];
 };
}
function compareCompound([kind,value],[other,fixed],compareItem){
 if(kind==='excluded'||other==='excluded')return ['different',''];
 if(kind==='unknown'||other==='unknown')return ['unknown',''];
 if(kind!==other)return ['different',''];
 if(kind==='native')return [kind,value];
 if(kind.startsWith('item'))return [compareItem(kind,value,fixed),''];
 if(kind!=='list')return sameCalendarValue(kind,value,fixed)?['equal',fixed]:['different',''];
 const [probe,items]=value,[fixedProbe,fixedItems]=fixed;
 if(items.length!==fixedItems.length)return ['different',''];
 const results=items.map((item,index)=>{
  const expected=fixedItems[index],result=compareCompound(item,expected,compareItem);
  if(result[0]!=='native'||!(probe||fixedProbe))return result;
  if(probe!==fixedProbe)return ['unknown',''];
  return [compareItem(probe,item[1],expected[1]),''];
 });
 for(const failure of ['different','unknown'])if(results.some(([status])=>status===failure))return [failure,''];
 // Legacy validation packs used a fixed probe for the whole list.
 return results.some(([status])=>status==='native')?['native',results.map(row=>row[1]).join(' ')]:['equal',''];
}
function sameScopedLiteral(actual,fixed,namespaces,fixedNamespaces){
 if(actual!==fixed)return false;
 return (fixed.match(/[^ \t\r\n]+/g)||[]).every(token=>{
  const prefix=token.slice(0,Math.max(0,token.lastIndexOf(':')));
  return (namespaces[prefix]||'')===(Object.hasOwn(fixedNamespaces,prefix)?fixedNamespaces[prefix]:'');
 });
}
async function checkCompoundValues(pack,files,documentTypes,budget,subjects,failed){
 const selectors=probeBatch(pack,files,documentTypes,budget),checks=probeBatch(pack,files,documentTypes,budget);
 const pending=[],outcomes=new Map();
 for(const [line,subject] of subjects){
  const compound=subject.rule.calendar_compound;
  if(!compound||failed.has(line))continue;
  const {shape,fixed,namespaces}=compound;
  if(compound.limited){
   outcomes.set(line,sameScopedLiteral(subject.calendarValue,fixed,subject.namespaces,namespaces)?'equal':'unknown');
   continue;
  }
  pending.push({line,subject,
   fixedProbe:fixed===undefined?null:selectors.add(subject.rule.element,fixed,namespaces),
   actual:resolveCompound(shape,subject.calendarValue,subject.namespaces,selectors),
   expected:fixed===undefined?null:resolveCompound(shape,fixed,namespaces,selectors)});
 }
 const {invalid}=await selectors.check(),native=[],pairs=[];
 for(const {line,subject,fixedProbe,actual,expected} of pending){
  if(fixedProbe===null){
   outcomes.set(line,['excluded','unknown'].includes(actual(invalid)[0])?'different':'equal');
   continue;
  }
  if(invalid.has(fixedProbe)){outcomes.set(line,'unknown');continue}
  const items=[],compound=subject.rule.calendar_compound;
  const [outcome,lexical]=compareCompound(actual(invalid),expected(invalid),(...item)=>{
   items.push(item);
   return 'equal';
  });
  outcomes.set(line,outcome);
  if(outcome==='native'&&compound.comparison)native.push({line,probe:checks.add(compound.comparison,lexical,subject.namespaces),changed:lexical!==subject.calendarValue});
  else if(outcome==='equal')for(const item of items)pairs.push({line,probe:checks.addPair(...item,subject.namespaces,compound.namespaces)});
 }
 const {invalid:rejected}=await checks.check();
 for(const {line,probe,changed} of native)outcomes.set(line,!rejected.has(probe)?'equal':rejected.get(probe)&&changed?'unknown':'different');
 for(const {line,probe} of pairs)if(rejected.has(probe)&&outcomes.get(line)!=='different')outcomes.set(line,rejected.get(probe)?'unknown':'different');
 for(const {line,subject} of pending){
  const {fixed,namespaces}=subject.rule.calendar_compound;
  if(outcomes.get(line)==='unknown'&&fixed!==undefined&&!subject.rule.instance_value
   &&sameScopedLiteral(subject.calendarValue,fixed,subject.namespaces,namespaces))outcomes.set(line,'equal');
 }
 return outcomes;
}
// libxml2 skips fixed constraints placed on attribute refs. Probe each actual
// use with the scalar type compiled from the exact XSD closure.
function attributeProbes(pack,source,batch,idBatch,idSubjects){
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
 const globalAttributes=new Map((pack.attribute_global_checks||[]).map(rule=>[JSON.stringify(rule.attribute),rule]));
 const typeWhitespace=new Map((pack.element_type_whitespace||[]).map(item=>[JSON.stringify(item.type),item.mode]));
 const typeValues=new Map((pack.element_type_values??pack.element_type_unions??[]).map(item=>[JSON.stringify(item.type),item]));
 const matchesNamespace=(w,ns)=>w.names.some(value=>value==='##any'||(value==='##other'?!!ns&&ns!==w.target_namespace:value==='##targetNamespace'?ns===w.target_namespace:value==='##local'?!ns:value===ns));
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
 const idTypes=new Map((pack.id_types||[]).map(p=>[JSON.stringify(p.type),p.shape]));
 function registerId(node,owner,shape,value,attribute,typeRange){
  if(!owner||!shape)return;
  function prepare(shape){
   return typeof shape==='boolean'?shape:shape.map(([name,nested])=>[idBatch.add(name,value,node.namespaces),prepare(nested)]);
  }
  idSubjects.push({node,owner,attribute,typeRange,value:normalizeWhitespace(value,'collapse'),shape:prepare(shape)});
 }
 const subjects=new Map(),contexts=new WeakMap();
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
   const end=node.nameEnd+match.index+match[0].length-1;
   attributes.push({ns,name,value:decode(match[3]),range:[end-match[3].length,end]});
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
  let whitespace,effectiveValue;
  if(xtype){
   const value=xtype.value.trim(),colon=value.indexOf(':'),name=colon<0?value:value.slice(colon+1),ns=node.namespaces[colon<0?'':value.slice(0,colon)]||'';
   whitespace=ns==='http://www.w3.org/2001/XMLSchema'?({string:'preserve',anySimpleType:'preserve',normalizedString:'replace'}[name]||'collapse'):typeWhitespace.get(JSON.stringify([ns,name]));
   const idShape=idTypes.get(JSON.stringify([ns,name]))||(ns===XSD&&name==='ID');
   if(idShape&&!attributes.some(a=>a.ns==='http://www.w3.org/2001/XMLSchema-instance'&&a.name==='nil'&&['true','1'].includes(a.value)))
    registerId(node,node.parent,idShape,content(node),null,xtype.range);
   effectiveValue=typeValues.get(JSON.stringify([ns,name]));
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
  if(laxChildren&&globalAttributes.size)for(const {ns,name} of attributes){
   const rule=globalAttributes.get(JSON.stringify([ns,name]));if(rule)rules.push(rule);
  }
  contexts.set(node,{path,scopes,lax:laxChildren});
  const seen=new Set();
  for(let rule of rules){
   if(rule.guards&&!rule.guards.every(guard=>matchesGuard(node,guard)))continue;
   const identity=JSON.stringify([rule.attribute,rule.element]);if(seen.has(identity))continue;seen.add(identity);
   const attribute=rule.element_value?{value:content(node)}:attributes.find(a=>a.ns===rule.attribute[0]&&a.name===rule.attribute[1]);if(!attribute||rule.element_value&&attribute.value==='')continue;
   if(rule.id_shape&&(!rule.element_value||!xtype))
    registerId(node,rule.element_value?node.parent:node,rule.id_shape,attribute.value,rule.element_value?null:rule.attribute);
   if(rule.identity_only)continue;
   if(rule.element_value){
    if(effectiveValue&&rule.fixed_value){
     const {lexical,namespaces}=rule.fixed_value;
     rule={...rule,element:effectiveValue.element,instance_value:true,calendar_fixed:undefined,
      calendar_compound:{shape:effectiveValue.shape,fixed:lexical,namespaces}};
    }else attribute.value=normalizeWhitespace(attribute.value,whitespace);
   }
   const probe=batch.add(rule.element,attribute.value,node.namespaces);
   subjects.set(probe,{node:{line:node.line,ns:node.ns,name:node.name},rule,
    ...(rule.calendar_compound?{calendarValue:attribute.value,namespaces:node.namespaces}
      :rule.calendar_fixed?{calendarValue:attribute.value}:{})});
  }
 }
 return subjects;
}
self.onmessage=async event=>{
 const core=self.SedoValidationCore;
 try{
  const {bytes,pack}=event.data;const text=core.inspectBytes(bytes);core.checkPack(pack,pack);
  // The minimal libxml2 build lacks legacy iconv encodings. Decode strictly in
  // the browser and transcode an in-memory copy; source bytes are never edited.
  const xml=new TextEncoder().encode(text.replace(/\r\n?/g,'\n').replace(/^(<\?xml\s[^?]*\bencoding\s*=\s*)(["'])[^"']*\2/, '$1"UTF-8"'));
  const files=[{fileName:'input.xml',contents:xml},...Object.entries(pack.files).map(([fileName,value])=>({fileName,contents:core.decode64(value)}))];
  if(!pack.document_ids_complete&&legacyRequiresIdProfiles(files))
   throw Error('В старом комплекте отсутствуют профили ID. Соберите комплект заново.');
  const parsed=await runLint(['--nonet','--xpath','concat(local-name(/*), "|", namespace-uri(/*))','input.xml'],files);
  if(parsed.code!==0||parsed.lintErrors){self.postMessage({type:'result',valid:false,...lintDiagnostics(parsed)});return}
  const locations=self.SedoValidationPaths,index=locations.index(typeof event.data.source==='string'&&event.data.source.length<=core.MAX_BYTES?event.data.source:text);
  if(parsed.stdout.trim()!==pack.root+'|'+(pack.namespace||'')){self.postMessage({type:'result',valid:false,errors:locations.locate([{root:true,line:index?.[0]?.line||1,message:'Неверный корневой элемент или пространство имён. Ожидается {'+(pack.namespace||'')+'}'+pack.root+'.'}],index,pack.fields||[],pack.field_namespaces||[])});return}
  const schemaArgs=['--nonet','--noout','--schema','message.xsd','input.xml'];
  const rules=[pack.attribute_checks||[],pack.attribute_global_checks||[],...(pack.attribute_type_checks||[]).map(p=>p.checks),...(pack.attribute_element_checks||[]).map(p=>p.checks)].flat();
  const typedFixed=rules.some(rule=>rule.element_value);
  const calendarAttributes=rules.some(rule=>rule.calendar_fixed&&!rule.element_value);
  const compoundValues=(pack.element_type_values??pack.element_type_unions)?.length||rules.some(rule=>rule.calendar_compound);
  const compoundAttributes=rules.some(rule=>rule.calendar_compound&&!rule.element_value);
  let result=await runLint(schemaArgs,files,pack.boolean_fixed_replacements?{booleanAliases:true,typedFixed,calendarAttributes,compoundAttributes}:{});
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
  if(valid&&(rules.length||pack.id_types?.length||text.includes('type'))){
   const documentTypes=pack.supplementary_schema&&pack.document_value_probes===undefined?legacyDocumentValueProbes(pack,files):new Set(pack.document_value_probes);
   const budget={size:0},batch=probeBatch(pack,files,documentTypes,budget,false);
   const idSubjects=[],idBatch=probeBatch(pack,files,documentTypes,budget);
   const subjects=attributeProbes(pack,text,batch,idBatch,idSubjects);
   if(subjects?.size){
    const checked=await batch.check(true);
    valid=!checked.invalid.size;
    ({errors,diagnosticsTruncated}=checked);
    const failed=checked.invalid;
    errors=errors.map(error=>{
     const subject=subjects.get(error.line);if(!subject)return error;
     const {node,rule}=subject,element=(node.ns?'{'+node.ns+'}':'')+node.name,attribute=(rule.attribute[0]?'{'+rule.attribute[0]+'}':'')+rule.attribute[1];
     const reason=error.message.replace(/^.*?Element '[^']+', attribute 'value':\s*/, '');
     return {line:node.line,message:"Element '"+element+"'"+(rule.element_value?'':", attribute '"+attribute+"'")+': '+reason};
    });
    const compounds=compoundValues?await checkCompoundValues(pack,files,documentTypes,budget,subjects,failed):new Map();
    for(const [line,{node,rule,calendarValue:actual}] of subjects){
     if(!(rule.calendar_fixed||rule.calendar_compound)||failed.has(line))continue;
     let reason;
     if(rule.calendar_compound){
      const outcome=compounds.get(line),{fixed}=rule.calendar_compound;
      if(outcome==='equal')continue;
      reason=fixed===undefined?`Значение '${actual}' нарушает точное календарное ограничение типа XSD.`
       :outcome==='unknown'?`Не удалось точно сравнить значение '${actual}' с fixed '${fixed}'.`
       :`The actual value '${actual}' does not match the fixed value constraint '${fixed}'.`;
     }else{
      const [kind,fixed]=rule.calendar_fixed,value=calendarValue(kind,actual);
      if(value&&sameCalendarValue(kind,actual,fixed))continue;
      reason=value?`The actual value '${actual}' does not match the fixed value constraint '${fixed}'.`
       :`'${actual}' is not a valid value of the atomic type 'xs:${kind}'.`;
     }
     valid=false;
     if(errors.length===30){diagnosticsTruncated=true;break}
     const element=(node.ns?'{'+node.ns+'}':'')+node.name;
     const attribute=(rule.attribute[0]?'{'+rule.attribute[0]+'}':'')+rule.attribute[1];
     const message=`Element '${element}'${rule.element_value?'':", attribute '"+attribute+"'"}: ${reason}`;
     diagnosticsTruncated||=message.length>2000;
     errors.push({line:node.line,message:message.slice(0,2000)});
    }
   }
   if(idSubjects.length){
    const {invalid}=await idBatch.check(),owners=new Map();
    let assessed=null;
    if(!pack.supplementary_schema&&idSubjects.length>1&&files.slice(1).some(file=>new TextDecoder().decode(file.contents).includes('skip'))){
     // Mark candidate xsi:type values with distinct invalid QNames. The native
     // schema diagnoses precisely the elements outside skipped subtrees.
     assessed=new Set();
     const source=text.replace(/\r\n?/g,'\n'),pieces=[];
     let offset=0;
     for(const [id,{typeRange:[start,end]}] of idSubjects.entries()){
      pieces.push(source.slice(offset,start),':sedoId'+id);offset=end;
     }
     pieces.push(source.slice(offset));
     const marked=pieces.join('').replace(/^(<\?xml\s[^?]*\bencoding\s*=\s*)(["'])[^"']*\2/,'$1"UTF-8"');
     const result=await runLint(schemaArgs,[{fileName:'input.xml',contents:new TextEncoder().encode(marked)},...files.slice(1)],{assessedIds:assessed});
     if(![0,3].includes(result.code))throw Error('Не удалось определить область проверки ID.');
    }
    function selected(shape){
     while(Array.isArray(shape))shape=shape.find(([probe])=>!invalid.has(probe))?.[1]??false;
     return shape;
    }
    for(const [id,{node,owner,attribute,value,shape}] of idSubjects.entries()){
     if(assessed&&!assessed.has(id)||!selected(shape))continue;
     if(!owners.has(value)){owners.set(value,owner.nameStart);continue}
     if(owners.get(value)===owner.nameStart)continue;
     valid=false;
     if(errors.length>=30){diagnosticsTruncated=true;break}
     const element=(node.ns?'{'+node.ns+'}':'')+node.name;
     const attr=attribute?(attribute[0]?'{'+attribute[0]+'}':'')+attribute[1]:null;
     const message="Element '"+element+"'"+(attr?", attribute '"+attr+"'":"")+": Значение ID '"+value+"' уже принадлежит другому элементу.";
     diagnosticsTruncated||=message.length>2000;
     errors.push({line:node.line,message:message.slice(0,2000)});
    }
   }
  }
  const reviewLocations=locations.reviewLocations(index,pack.fields||[],pack.field_namespaces||[],event.data.reviewFields||[]);
  self.postMessage({type:'result',valid,reviewLocations,diagnosticsTruncated,errors:valid?[]:locations.locate(errors,index,pack.fields||[],pack.field_namespaces||[])});
 }catch(error){self.postMessage({type:'result',valid:false,runtime:true,errors:[{line:null,message:String(error.message||error).slice(0,2000)}]})}
};
