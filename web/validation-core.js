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
 else {const head=new TextDecoder('latin1').decode(bytes.subarray(0,512));const m=head.match(/^\s*(?:ï»¿)?<\?xml\s[^?]*encoding\s*=\s*["']([^"']+)["']/i);if(m)encoding=m[1]}
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
 if(pack.attribute_checks!==undefined||pack.attribute_type_checks!==undefined){
  const pair=value=>Array.isArray(value)&&value.length===2&&value.every(x=>typeof x==='string'&&x.length<=8192)&&value[1].length>0;
  const rule=(value,relative)=>value&&Array.isArray(value.path)&&(relative||value.path.length>0)&&value.path.length<=256&&value.path.every(pair)&&pair(value.attribute)&&typeof value.element==='string'&&/^value[0-9]+$/.test(value.element)&&typeof value.field==='string'&&value.field.length<=8192;
  if(!Array.isArray(pack.attribute_checks)||pack.attribute_checks.length>50000||typeof pack.supplementary_namespace!=='string'||!pack.supplementary_namespace||pack.supplementary_namespace.length>8192||typeof pack.supplementary_schema!=='string'||!Object.hasOwn(pack.files,pack.supplementary_schema))throw Error('Некорректные дополнительные проверки XSD.');
  if(!pack.attribute_checks.every(value=>rule(value,false)))throw Error('Некорректный путь дополнительной проверки XSD.');
  if(pack.attribute_type_checks!==undefined){
   if(!Array.isArray(pack.attribute_type_checks)||pack.attribute_type_checks.length>50000)throw Error('Некорректные проверки типов XSD.');
   let total=pack.attribute_checks.length;
   for(const profile of pack.attribute_type_checks){if(!profile||!pair(profile.type)||!Array.isArray(profile.checks)||(total+=profile.checks.length)>50000||!profile.checks.every(value=>rule(value,true)))throw Error('Некорректный путь проверки типа XSD.')}
  }
 }
 return pack;
}
function decode64(text){return Uint8Array.from(atob(text),c=>c.charCodeAt(0))}
function errors(text){
 const diagnostics=[];let current=null;
 for(const line of String(text||'Ошибка проверки XML.').split('\n')){
  if(!line.trim()||/^input\.xml (?:validates|fails to validate)$/.test(line.trim()))continue;
  const header=line.match(/^input\.xml:(\d+):\s*(.*)/);
  if(header||!current){
   if(diagnostics.length===30)break;
   current={line:header?Number(header[1]):null,message:(header?header[2]:line).slice(0,2000)};
   diagnostics.push(current);
  }else current.message=(current.message+'\n'+line).slice(0,2000);
 }
 return diagnostics;
}
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
const api={MAX_BYTES,inspectDeclaration,utf8Bytes,inspectBytes,checkPack,decode64,errors,groupErrors};scope.SedoValidationCore=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof self==='object'?self:globalThis);
