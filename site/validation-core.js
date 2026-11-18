(function(scope){
"use strict";
const MAX_BYTES=10*1024*1024;
// Validate the original declaration before any in-memory transcoding can alter
// it. XMLDecl requires version first, then optional encoding and standalone.
function inspectDeclaration(text){
 if(!/^\uFEFF?<\?xml(?=[\s?]|$)/.test(text))return;
 const declaration=text.match(/^\uFEFF?<\?xml[ \t\r\n]+version[ \t\r\n]*=[ \t\r\n]*(["'])(1\.[0-9]+)\1(?:[ \t\r\n]+encoding[ \t\r\n]*=[ \t\r\n]*(["'])([A-Za-z][A-Za-z0-9._-]*)\3)?(?:[ \t\r\n]+standalone[ \t\r\n]*=[ \t\r\n]*(["'])(yes|no)\5)?[ \t\r\n]*\?>/);
 if(!declaration)throw Error('Некорректное объявление XML. Укажите version, затем непустую encoding и standalone при необходимости.');
 return declaration;
}
function utf8Bytes(text){
 inspectDeclaration(text);
 return new TextEncoder().encode(text.replace(/^(\uFEFF?<\?xml\s[^?]*\bencoding\s*=\s*)(["'])[^"']*\2/, '$1"UTF-8"'));
}
function inspectBytes(bytes){
 if(!(bytes instanceof Uint8Array)||!bytes.length)throw Error('Выберите непустой XML-файл.');
 if(bytes.length>MAX_BYTES)throw Error('Размер XML превышает 10 МиБ. Используйте локальный Python-валидатор из ZIP.');
 let encoding='utf-8';
 if(bytes[0]===255&&bytes[1]===254||bytes[0]===60&&bytes[1]===0)encoding='utf-16le';
 else if(bytes[0]===254&&bytes[1]===255||bytes[0]===0&&bytes[1]===60)encoding='utf-16be';
 else {
  const start=bytes[0]===0xef&&bytes[1]===0xbb&&bytes[2]===0xbf?3:0;
  const decoder=new TextDecoder('latin1');
  if(/^<\?xml[ \t\r\n]/.test(decoder.decode(bytes.subarray(start,start+6)))){
   const end=bytes.indexOf(0x3f,start+5);
   if(end<0||bytes[end+1]!==0x3e)throw Error('Некорректное объявление XML.');
   const declaration=inspectDeclaration(decoder.decode(bytes.subarray(start,end+2)));
   encoding=declaration[4]||encoding;
  }
 }
 let text;
 try{
  const label=encoding.toLowerCase();
  if(['ascii','us-ascii'].includes(label)){
   if(bytes.some(b=>b>127))throw Error('Недопустимый байт ASCII');text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
  }else if(['iso-8859-1','iso8859-1','latin1'].includes(label)){
   text='';for(let i=0;i<bytes.length;i+=16384)text+=String.fromCharCode(...bytes.subarray(i,i+16384));
  }else if(['utf-8','utf8','utf-16','utf-16le','utf-16be','windows-1251','cp1251'].includes(label))text=new TextDecoder(encoding,{fatal:true}).decode(bytes);
  else throw Error('Кодировка не поддерживается');
 }catch{throw Error('Некорректные байты или неподдерживаемая кодировка XML. Поддерживаются UTF-8, UTF-16, Windows-1251, ASCII и ISO-8859-1. Можно использовать валидатор из ZIP.')}
 // The Unicode decoder consumed the signature. A remaining U+FEFF is an
 // illegal character before the document, not a new signature for libxml2.
 if(text.startsWith('\uFEFF'))throw Error('XML содержит повторную сигнатуру BOM перед корнем.');
 inspectDeclaration(text);
 const declaration=text.match(/^<\?xml\s[^?]*encoding\s*=\s*["']([^"']+)["']/);
 if(encoding.startsWith('utf-16')&&declaration&&!['utf-16',encoding].includes(declaration[1].toLowerCase()))throw Error('Объявленная кодировка XML не соответствует байтам UTF-16.');
 // Skip lexical containers where declaration-looking text has no DTD meaning.
 let position=0;
 while((position=text.indexOf('<',position))!==-1){
  let close=null,skip=0;
  if(text.startsWith('<!--',position)){close='-->';skip=4}
  else if(text.startsWith('<![CDATA[',position)){close=']]>';skip=9}
  else if(text.startsWith('<?',position)){close='?>';skip=2}
  if(close){const end=text.indexOf(close,position+skip);if(end===-1)break;position=end+close.length;continue}
  if(/^<!\s*(?:DOCTYPE|ENTITY)\b/i.test(text.slice(position,position+16)))throw Error('DTD и объявления ENTITY запрещены: внешние ресурсы не загружаются.');
  position++;
 }
 return text;
}
function checkPack(pack,expected){
 if(pack?.format!==1||pack.entrypoint!=='message.xsd'||typeof pack.root!=='string'||!pack.root||typeof pack.files!=='object'||!pack.files)throw Error('Некорректный комплект XSD.');
 for(const key of ['number','environment','revision_id','root','namespace'])if((pack[key]||'')!==(expected[key]||''))throw Error('Комплект XSD не соответствует выбранной редакции.');
 const entries=Object.entries(pack.files);
 if(!entries.length||entries.length>256||!pack.files['message.xsd'])throw Error('Неполный комплект XSD.');
 let size=0;
 for(const [name,value] of entries){if(!/^(?:message\.xsd|schemas\/[A-Za-z0-9_.-]+\.xsd)$/.test(name)||name.includes('..'))throw Error('Небезопасный путь XSD.');if(typeof value!=='string'||!/^[A-Za-z0-9+/]*={0,2}$/.test(value))throw Error('Некорректные байты XSD.');size+=value.length}
 if(size>32*1024*1024)throw Error('Слишком большой комплект XSD.');
 if(pack.boolean_fixed_replacements!==undefined){
  const replacements=pack.boolean_fixed_replacements;
  if(!replacements||typeof replacements!=='object'||Array.isArray(replacements)||Object.entries(replacements).some(([name,alternate])=>
   !Object.hasOwn(pack.files,name)||typeof alternate!=='string'||!/^schemas\/boolean-fixed-\d+\.xsd$/.test(alternate)||!Object.hasOwn(pack.files,alternate)))throw Error('Некорректные схемы для проверки логического fixed.');
 }
 if(pack.attribute_type_profiles_complete!==undefined&&typeof pack.attribute_type_profiles_complete!=='boolean')throw Error('Некорректная версия профилей типов XSD.');
 if([pack.attribute_checks,pack.attribute_type_checks,pack.attribute_wildcard_paths,pack.attribute_element_checks,pack.attribute_wildcards,pack.attribute_known_elements,pack.attribute_any_type_paths].some(value=>value!==undefined)){
  const pair=value=>Array.isArray(value)&&value.length===2&&value.every(x=>typeof x==='string'&&x.length<=8192)&&value[1].length>0;
  const path=(value,relative)=>Array.isArray(value)&&(relative||value.length>0)&&value.length<=256&&value.every(pair);
  const guards=(value,relative)=>{
   if(value.guards===undefined)return true;
   return Array.isArray(value.guards)&&value.guards.length<=value.path.length&&value.guards.every(guard=>{
    if(!guard||!Number.isInteger(guard.up)||guard.up<0||guard.up>=value.path.length-(relative?0:1)
      ||!Array.isArray(guard.layouts)||guard.layouts.length<1||guard.layouts.length>256||(total+=guard.layouts.length)>50000)return false;
    const subject=value.path.at(-guard.up-1);
    return guard.layouts.every(layout=>layout&&path(layout.children,false)&&Number.isInteger(layout.index)
      &&layout.index>=0&&layout.index<layout.children.length
      &&subject.every((part,i)=>part===layout.children[layout.index][i]));
   });
  };
  const calendar=value=>value.calendar_fixed===undefined||value.element_value===true&&Array.isArray(value.calendar_fixed)
   &&value.calendar_fixed.length===2&&['dateTime','time'].includes(value.calendar_fixed[0])&&typeof value.calendar_fixed[1]==='string';
  const rule=(value,relative)=>value&&path(value.path,relative)&&pair(value.attribute)&&typeof value.element==='string'&&/^value[0-9]+$/.test(value.element)&&typeof value.field==='string'&&value.field.length<=8192&&guards(value,relative)&&calendar(value);
  if(!Array.isArray(pack.attribute_checks)||pack.attribute_checks.length>50000||typeof pack.supplementary_namespace!=='string'||!pack.supplementary_namespace||pack.supplementary_namespace.length>8192||typeof pack.supplementary_schema!=='string'||!Object.hasOwn(pack.files,pack.supplementary_schema))throw Error('Некорректные дополнительные проверки XSD.');
  let total=pack.attribute_checks.length;
  if(!pack.attribute_checks.every(value=>rule(value,false)))throw Error('Некорректный путь дополнительной проверки XSD.');
  const wildcardPaths=(paths,relative=false)=>{
   if(paths===undefined)return true;
   return Array.isArray(paths)&&(total+=paths.length)<=50000&&paths.every(value=>path(value,relative));
  };
  const wildcards=(items,relative)=>items===undefined||Array.isArray(items)&&(total+=items.length)<=50000&&items.every(w=>w&&path(w.path,relative)&&typeof w.namespace==='string'&&typeof w.target_namespace==='string'&&['skip','lax','strict'].includes(w.process)&&Array.isArray(w.explicit)&&w.explicit.length<=50000&&w.explicit.every(pair));
  const known=pack.attribute_known_elements;
  if(!wildcardPaths(pack.attribute_wildcard_paths)||!wildcardPaths(pack.attribute_any_type_paths)||!wildcards(pack.attribute_wildcards,false)||known!==undefined&&(!Array.isArray(known)||known.length>50000||!known.every(pair)))throw Error('Некорректные настройки wildcard XSD.');
  for(const [profiles,key] of [[pack.attribute_type_checks,'type'],[pack.attribute_element_checks,'element']]){
   if(profiles===undefined)continue;
   if(!Array.isArray(profiles)||profiles.length>50000)throw Error('Некорректные профили проверок XSD.');
   for(const profile of profiles){
    if(!profile||!pair(profile[key])||!Array.isArray(profile.checks)||(total+=profile.checks.length)>50000||!profile.checks.every(value=>rule(value,true))||!wildcardPaths(profile.wildcard_paths)||!wildcardPaths(profile.any_type_paths,true)||!wildcards(profile.wildcards,true))throw Error('Некорректный путь проверки XSD.');
   }
  }
 }
 return pack;
}
function decode64(text){return Uint8Array.from(atob(text),c=>c.charCodeAt(0))}
function isWarningDiagnostic(line){
 return /^(?:[^\n]*:\d+:\s*)?(?:namespace|parser|Schemas(?: validity| parser)?) warning\s*:/i.test(line);
}
function readDiagnostics(text){
 const diagnostics=[];let current=null,truncated=false,warning=false;
 for(const line of String(text||'Ошибка проверки XML.').split('\n')){
  if(!line.trim()||/^input\.xml (?:validates|fails to validate)$/.test(line.trim()))continue;
  const header=line.match(/^input\.xml:(\d+):\s*(.*)/);
  if(isWarningDiagnostic(line)){warning=true;continue}
  if(header)warning=false;
  if(warning)continue;
  if(header||!current){
   if(diagnostics.length===30){truncated=true;break}
   const message=header?header[2]:line;
   if(message.length>2000)truncated=true;
   current={line:header?Number(header[1]):null,message:message.slice(0,2000)};
   diagnostics.push(current);
  }else{
   const message=current.message+'\n'+line;
   if(message.length>2000)truncated=true;
   current.message=message.slice(0,2000);
  }
 }
 return {errors:diagnostics,truncated};
}
const errors=text=>readDiagnostics(text).errors;
// Group only proven source occurrences, never a path/line guess. All metadata
// and diagnostic text remain inside the opaque frame and its private worker.
function groupErrors(errors){
 const groups=[],bySource=new Map();
 for(const error of errors){
  const location=error.locations?.length===1?error.locations[0]:null,ranges=location?.ranges;
  const exact=location&&typeof location.path==='string'&&!error.ambiguous&&!error.sourceAmbiguous&&!error.truncated&&location.occurrences===1&&!location.rangesTruncated&&Array.isArray(ranges)&&ranges.length>0&&ranges.some(r=>r.kind==='tag')&&(!location.path.includes('/@')||ranges.some(r=>r.kind==='attribute'))&&ranges.every(r=>Number.isInteger(r.start)&&Number.isInteger(r.end)&&r.start>=0&&r.end>r.start);
  const key=exact?JSON.stringify([location.path,location.field??null,!!location.parent,ranges.map(r=>[r.kind,r.start,r.end]).sort((a,b)=>a[1]-b[1]||a[2]-b[2]||String(a[0]).localeCompare(String(b[0])))]):null;
  const existing=key===null?null:bySource.get(key);
  if(existing){existing.diagnostics.push(error);continue}
  const group={...error,diagnostics:[error]};groups.push(group);if(key!==null)bySource.set(key,group);
 }
 return groups;
}
const api={MAX_BYTES,inspectDeclaration,utf8Bytes,inspectBytes,checkPack,decode64,isWarningDiagnostic,readDiagnostics,errors,groupErrors};scope.SedoValidationCore=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof self==='object'?self:globalThis);
