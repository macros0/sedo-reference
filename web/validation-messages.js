(function(scope){
"use strict";
// Local, conservative descriptions of known libxml2 messages. Never execute an
// XSD regex or translate by replacing substrings inside echoed XML values.
const neutral='Ошибка проверки XML. Точная причина — в «Подробнее».';
const word=(n,one,few,many)=>n%100>=11&&n%100<=14?many:n%10===1?one:n%10>=2&&n%10<=4?few:many;
const digits=n=>word(Number(n),'цифра','цифры','цифр');
function names(text){
 const result=[];let cursor=0;
 for(const m of text.matchAll(/(?:\{[^{}]*\})?[^\s,{}]+/g)){
  if(!/^\s*(?:,\s*)?$/.test(text.slice(cursor,m.index)))return null;
  const name=m[0].slice(m[0].lastIndexOf('}')+1);if(!/^[\p{L}_][\p{L}\p{N}\p{M}_.:\-]*$/u.test(name))return null;
  result.push('«'+name+'»');cursor=m.index+m[0].length;
 }
 return result.length&&/^\s*$/.test(text.slice(cursor))?result:null;
}
function digitPattern(pattern,value){
 const m=pattern.match(/^(\\d|\[0-9\])\{(\d+)(?:,(\d*))?\}$/);if(!m)return null;
 const low=Number(m[2]),high=m[3]===undefined?low:m[3]===''?Infinity:Number(m[3]);
 if(!Number.isSafeInteger(low)||low<0||high!==Infinity&&(!Number.isSafeInteger(high)||high<low))return null;
 const ascii=m[1]==='[0-9]',allDigits=ascii?/^[0-9]*$/.test(value):/^\p{Nd}*$/u.test(value),count=Array.from(value).length;
 if(low===high){
  if(allDigits&&count!==low)return 'Ожидается '+low+' '+digits(low)+', указано '+count+'.';
  return 'Ожидается ровно '+low+' '+digits(low)+(ascii?' от 0 до 9.':' без букв, пробелов и других символов.');
 }
 const expected=high===Infinity?'Количество цифр должно быть не меньше '+low:'Ожидается от '+low+' до '+high+' цифр';
 return expected+(allDigits&&(count<low||count>high)?', указано '+count:'')+'.';
}
function explain(message){
 const text=String(message||'');
 if(/^Неверный корневой элемент или пространство имён\./.test(text))return 'Корневой элемент или его пространство имён не соответствует выбранному сообщению.';
 if(/^DTD и объявления ENTITY запрещены:/.test(text))return 'XML содержит запрещённое объявление DTD или ENTITY.';
 const subject=text.match(/^(?:Schemas validity error\s*:\s*)?Element '[^'\n]+'(?:, attribute '[^'\n]+')?:\s*([^\n]+)$/);if(!subject)return neutral;
 const body=subject[1];let m;
 if((m=body.match(/^\[facet 'length'\] The value has a length of '(\d+)'; this differs from the allowed length of '(\d+)'\.$/)))return 'Точная длина по XSD — '+m[2]+'; фактическая — '+m[1]+'.';
 if((m=body.match(/^\[facet 'minLength'\] The value has a length of '(\d+)'; this underruns the allowed minimum length of '(\d+)'\.$/)))return 'Минимальная длина по XSD — '+m[2]+'; фактическая — '+m[1]+'.';
 if((m=body.match(/^\[facet 'maxLength'\] The value has a length of '(\d+)'; this exceeds the allowed maximum length of '(\d+)'\.$/)))return 'Максимальная длина по XSD — '+m[2]+'; фактическая — '+m[1]+'.';
 if((m=body.match(/^\[facet 'pattern'\] The value '(.*)' is not accepted by the pattern '(.*)'\.$/)))return digitPattern(m[2],m[1])||'Значение не соответствует формату, заданному схемой XSD.';
 if((m=body.match(/^\[facet 'enumeration'\] The value '.*' is not an element of the set \{(.*)\}\.$/))){
  const values=/^'[^']*'(?:,\s*'[^']*')*$/.test(m[1])?[...m[1].matchAll(/'([^']*)'/g)].map(x=>x[1]):[];
  if(values.length&&values.every(v=>v.length<=120))return 'Допустимые значения: '+values.slice(0,6).map(v=>'«'+v+'»').join(', ')+(values.length>6?', … (полный список — в «Подробнее»).':'.');
  return 'Значение не входит в список допустимых значений. См. «Подробнее».';
 }
 if((m=body.match(/^\[facet '(minInclusive|maxInclusive)'\] The value '.*' is (less|greater) than the (minimum|maximum) value allowed \('([^']+)'\)\.$/))){
  if(m[1]==='minInclusive'&&m[2]==='less'&&m[3]==='minimum')return 'Значение должно быть не меньше '+m[4]+'.';
  if(m[1]==='maxInclusive'&&m[2]==='greater'&&m[3]==='maximum')return 'Значение должно быть не больше '+m[4]+'.';return neutral;
 }
 if((m=body.match(/^\[facet '(minExclusive|maxExclusive)'\] The value '.*' must be (greater|less) than '([^']+)'\.$/))){
  if(m[1]==='minExclusive'&&m[2]==='greater')return 'Значение должно быть больше '+m[3]+'.';
  if(m[1]==='maxExclusive'&&m[2]==='less')return 'Значение должно быть меньше '+m[3]+'.';return neutral;
 }
 if((m=body.match(/^\[facet 'fractionDigits'\] The value '.*' has more fractional digits than are allowed \('(\d+)'\)\.$/)))return 'Допустимо не больше '+m[1]+' '+word(Number(m[1]),'знака','знаков','знаков')+' после десятичной точки.';
 if((m=body.match(/^\[facet 'totalDigits'\] The value '.*' has more digits than are allowed \('(\d+)'\)\.$/)))return 'Допустимо не больше '+m[1]+' '+word(Number(m[1]),'значащей цифры','значащих цифр','значащих цифр')+'.';
 if((m=body.match(/^'.*' is not a valid value of the atomic type 'xs:([A-Za-z]+)'\.$/))){
  const type=m[1],known={boolean:'Ожидается логическое значение: true, false, 1 или 0.',date:'Ожидается корректная дата XSD, например 2000-01-31.',dateTime:'Ожидаются корректные дата и время XSD, например 2000-01-31T12:30:00.',time:'Ожидается корректное время XSD, например 12:30:00.',decimal:'Ожидается десятичное число; разделитель дробной части — точка.',float:'Ожидается число в допустимом формате XSD.',double:'Ожидается число в допустимом формате XSD.',nonNegativeInteger:'Ожидается целое число не меньше 0.',positiveInteger:'Ожидается целое число больше 0.',nonPositiveInteger:'Ожидается целое число не больше 0.',negativeInteger:'Ожидается целое число меньше 0.'};
  if(Object.hasOwn(known,type))return known[type];if(['integer','long','int','short','byte'].includes(type))return 'Ожидается целое число допустимого для схемы размера.';
  if(['unsignedLong','unsignedInt','unsignedShort','unsignedByte'].includes(type))return 'Ожидается неотрицательное целое число допустимого для схемы размера.';return neutral;
 }
 if((m=body.match(/^The attribute '([^']+)' is required but missing\.$/))){const name=names(m[1]);return name?.length===1?'Не заполнен обязательный атрибут '+name[0]+'.':neutral}
 if((m=body.match(/^Missing child element\(s\)\. Expected is (one of )?\( (.+) \)\.$/))){const expected=names(m[2]);return expected?(m[1]?'Ожидается один из вложенных тегов: '+expected.join(', '):expected.length===1?'Не заполнен обязательный вложенный тег '+expected[0]:'Ожидаются вложенные теги: '+expected.join(', '))+'.':neutral}
 if((m=body.match(/^This element is not expected\. Expected is (one of )?\( (.+) \)\.$/))){const expected=names(m[2]);return expected?'Тег недопустим в этом месте. Ожидается '+(m[1]?'один из тегов: ':'')+expected.join(', ')+'.':neutral}
 if(body==='This element is not expected.')return 'Этот тег недопустим в данном месте XML. Проверьте его имя, пространство имён и порядок тегов.';
 return neutral;
}
const api={explain};scope.SedoValidationMessages=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof self==='object'?self:globalThis);
