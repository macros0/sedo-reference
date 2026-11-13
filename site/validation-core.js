(function(scope){
"use strict";
const MAX_BYTES=10*1024*1024;
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
 return pack;
}
function decode64(text){return Uint8Array.from(atob(text),c=>c.charCodeAt(0))}
function errors(text){return String(text||'Ошибка проверки XML.').split('\n').filter(s=>s.trim()&&!/^input\.xml (?:validates|fails to validate)$/.test(s.trim())).slice(0,30).map(line=>{const m=line.match(/^input\.xml:(\d+):\s*(.*)/);return {line:m?Number(m[1]):null,message:(m?m[2]:line).slice(0,2000)}})}
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
const api={MAX_BYTES,inspectBytes,checkPack,decode64,errors,groupErrors};scope.SedoValidationCore=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof self==='object'?self:globalThis);
