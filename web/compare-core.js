/* Structural comparison shared by the offline site and Node acceptance tests. */
(function(root){
"use strict";
const canonical=value=>{
 if(Array.isArray(value))return value.map(canonical);
 if(value&&typeof value==="object")return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));
 return value;
};
const stable=value=>JSON.stringify(canonical(value));
const sorted=rows=>[...(rows||[])].sort((a,b)=>stable(a)<stable(b)?-1:stable(a)>stable(b)?1:0);
const occurrence=value=>{
 const lexical=String(value).trim();
 return /^\+?\d+$/.test(lexical)?lexical.replace(/^\+?0*(?=\d)/,''):lexical;
};
const occurrenceGroups=value=>(value||'').replace(/\[\s*(\+?\d+)\s*\.\.\s*(\+?\d+|unbounded)\s*\]/g,(_,low,high)=>'['+occurrence(low)+'..'+occurrence(high)+']')
 .split(/\s+ИЛИ\s+/).map(branch=>branch.split(/\s*\/\s*/).filter(part=>! /^(?:sequence|all|choice|group) \[1\.\.1\]$/.test(part)).join(' / ')).join(' ИЛИ ');
const lexicalType=value=>(value||"").replace(/^(?:xs|xsd):/,"{http://www.w3.org/2001/XMLSchema}");
function sameType(a,b,key){
 const left=a[key]||"",right=b[key]||"",ns=key+"Ns";
 // Older trees have lexical QNames only. Use their original comparison until
 // both sides carry namespace metadata; never infer a custom prefix's URI.
 if(Object.hasOwn(a,ns)&&Object.hasOwn(b,ns))return a[ns]===b[ns]&&left.split(":").at(-1)===right.split(":").at(-1);
 return lexicalType(left)===lexicalType(right);
}
const typeLabel=(f,key)=>f[key]?(f[key]+(Object.hasOwn(f,key+"Ns")?" ("+f[key+"Ns"]+")":"")):"";
const patternGroups=f=>sorted((f.patternGroups||[]).map(sorted));
// Legacy trees flattened the pattern facets. The grouping cannot be inferred
// from that list. Flat facets remain comparable across every tree version.
const samePatternGroups=(a,b)=>!a.patternGroups||!b.patternGroups||stable(patternGroups(a))===stable(patternGroups(b));
const showQName=(value,name)=>name?value+" ({"+name[0]+"}"+name[1]+")":value;
const wildcard=f=>f.kind==="any"||f.kind==="anyAttribute";
const fieldNamespace=f=>wildcard(f)?f.wildcardNamespace||f.flags?.namespace||"##any":f.ns||"";
function builtinType(field){
 const base=field.base||field.type||"",ns=field.base?field.baseNs:field.typeNs;
 if(ns===undefined)return /^(?:xs|xsd):/.test(base)?base.split(":").at(-1):"";
 return ns==="http://www.w3.org/2001/XMLSchema"?base.split(":").at(-1):"";
}
function booleanValue(value){
 const lexical=value.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g,"");
 if(lexical==="1"||lexical==="true")return "true";
 if(lexical==="0"||lexical==="false")return "false";
 return value;
}
const integers=new Set(["integer","long","int","short","byte","nonPositiveInteger","negativeInteger","nonNegativeInteger","positiveInteger","unsignedLong","unsignedInt","unsignedShort","unsignedByte"]);
const strings=new Set(['string','normalizedString','token','language','NMTOKEN','Name','NCName','ID','IDREF','ENTITY']);
function builtinWhitespace(type){
 if(!type||type==='anyType')return undefined;
 return type==='string'||type==='anySimpleType'?'preserve':type==='normalizedString'?'replace':'collapse';
}
function whitespaceRule(field){
 return (field.facets||[]).findLast(([key])=>key==='whiteSpace')?.[1]??builtinWhitespace(builtinType(field));
}
function normalizeWhitespace(whitespace,value){
 if(whitespace!=='preserve')value=value.replace(/[\t\r\n]/g,' ');
 return whitespace==='collapse'?value.replace(/ +/g,' ').replace(/^ | $/g,''):value;
}
function whitespaceValue(field,value){
 // Unknown/custom types have no established value space in legacy trees.
 return normalizeWhitespace(whitespaceRule(field)??'preserve',value);
}
function decimalValue(type,value){
 // XSD decimal and its integer derivatives have arbitrary precision.
 const lexical=value.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g,"");
 if(!(type==="decimal"?/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/:/^[+-]?\d+$/).test(lexical))return value;
 const [integral,fraction=""]=lexical.replace(/^[+-]/,"").split(".");
 const whole=integral.replace(/^0+/,"")||"0",tail=fraction.replace(/0+$/,"");
 return (lexical[0]==="-"&&(whole!=="0"||tail)?"-":"")+whole+(tail?"."+tail:"");
}
function timezoneSeconds(zone){
 if(!zone||zone==='Z')return 0;
 const hours=Number(zone.slice(1,3)),minutes=Number(zone.slice(4));
 if(hours>14||minutes>59||(hours===14&&minutes))return null;
 return (zone[0]==='-'?-1:1)*(hours*60+minutes)*60;
}
function clockSeconds(hourText,minuteText,secondText,fraction){
 const hour=Number(hourText),minute=Number(minuteText),second=Number(secondText);
 if(hour>24||minute>59||second>59||(hour===24&&(minute||second||fraction)))return null;
 return hour*3600+minute*60+second;
}
function dateTimeValue(value){
 const match=/^(-?\d{4,})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$/.exec(value);
 if(!match)return value;
 const [,yearText,monthText,dayText,hourText,minuteText,secondText,fraction='',zone]=match;
 const year=BigInt(yearText);if(!year)return value; // XSD 1.0 has no year zero.
 const month=Number(monthText),day=Number(dayText),tail=fraction.replace(/0+$/,'');
 const leap=year%4n===0n&&(year%100n!==0n||year%400n===0n),months=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
 const clock=clockSeconds(hourText,minuteText,secondText,tail),offset=timezoneSeconds(zone);
 if(month<1||month>12||day<1||day>months[month-1]||clock===null||offset===null)return value;
 // Keep arbitrary year and fractional precision; Date would discard submilliseconds.
 // For BCE dates, count complete years back from -0001 without a year zero.
 // BigInt division truncates toward zero, matching those negative year counts.
 const previous=year>0n?year-1n:year;
 const days=365n*previous+previous/4n-previous/100n+previous/400n+BigInt(months.slice(0,month-1).reduce((sum,n)=>sum+n,day-1));
 return (zone?'zoned:':'local:')+String(days*86400n+BigInt(clock-offset))+'.'+tail;
}
function dateValue(value){
 const match=/^(-?\d{4,}-\d{2}-\d{2})(Z|[+-]\d{2}:\d{2})?$/.exec(value);
 if(!match)return value;
 // Dates denote midnight in their timezone. Keep timezone absence distinct,
 // and reuse the arbitrary-year calendar arithmetic used for dateTime.
 const midnight=match[1]+'T00:00:00'+(match[2]||''),normalized=dateTimeValue(midnight);
 return normalized===midnight?value:normalized;
}
function timeValue(value){
 const match=/^(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$/.exec(value);
 if(!match)return value;
 const [,hour,minute,second,fraction='',zone]=match,tail=fraction.replace(/0+$/,'');
 const clock=clockSeconds(hour,minute,second,tail),offset=timezoneSeconds(zone);
 if(clock===null||offset===null)return value;
 // xs:time represents a recurring instant within one day.
 return (zone?'zoned:':'local:')+String((clock-offset+86400)%86400)+'.'+tail;
}
const partialCalendar={
 gYearMonth:[/^(-?(?:\d{4}|[1-9]\d{4,})-\d{2})$/,'$1-01'],
 gYear:[/^(-?(?:\d{4}|[1-9]\d{4,}))$/,'$1-01-01'],
 gMonthDay:[/^--(\d{2}-\d{2})$/,'2000-$1'],
 gDay:[/^---(\d{2})$/,'2000-01-$1'],
 gMonth:[/^--(\d{2})$/,'2000-$1-01']
};
function partialCalendarValue(type,value){
 const match=/^(.*?)(Z|[+-]\d{2}:\d{2})?$/.exec(value);
 if(!match)return value;
 const [,date,zone='']=match,[pattern,fullDate]=partialCalendar[type];
 if(!pattern.test(date))return value;
 // XSD compares the starting instants of these calendar periods. Use an
 // arbitrary leap year and a 31-day month for the recurring day types.
 const anchored=date.replace(pattern,fullDate)+zone;
 const normalized=dateValue(anchored);
 return normalized===anchored?value:normalized;
}
function durationValue(value){
 const match=/^(-)?P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d*)?|\.\d+)S)?)?$/.exec(value);
 if(!match||!match.slice(2).some(v=>v!==undefined)||(value.includes('T')&&!match.slice(5).some(v=>v!==undefined)))return value;
 const [,negative,years,months,days,hours,minutes,seconds]=match;
 const totalMonths=BigInt(years||0)*12n+BigInt(months||0);
 const [whole,fraction='']=(seconds||'0').split('.'),tail=fraction.replace(/0+$/,'');
 const totalSeconds=BigInt(days||0)*86400n+BigInt(hours||0)*3600n+BigInt(minutes||0)*60n+BigInt(whole||0);
 // XSD durations have independent month and second components: a month
 // cannot be converted to a fixed day count. BigInt preserves all digits.
 return (negative&&(totalMonths||totalSeconds||tail)?'-':'')+String(totalMonths)+'M:'+String(totalSeconds)+'.'+tail+'S';
}
function enumerationValue(type,value){
 if(type==="boolean")return booleanValue(value);
 if(type==="decimal"||integers.has(type))return decimalValue(type,value);
 if(type==='dateTime')return dateTimeValue(value);
 if(type==='date')return dateValue(value);
 if(type==='time')return timeValue(value);
 if(Object.hasOwn(partialCalendar,type))return partialCalendarValue(type,value);
 if(type==='duration')return durationValue(value);
 if(type==='hexBinary'&&/^(?:[\da-fA-F]{2})*$/.test(value))return value.toUpperCase();
 if(type==='base64Binary')return value.replace(/[ \t\r\n]/g,'');
 if(type!=="float"&&type!=="double")return value;
 const lexical=value.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g,"");
 if(["INF","-INF","NaN"].includes(lexical))return lexical;
 if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(lexical))return value;
 const number=type==="float"?Math.fround(Number(lexical)):Number(lexical);
 if(number===Infinity)return "INF";
 if(number===-Infinity)return "-INF";
 return String(number);
}
function typedValue(field,value){return enumerationValue(builtinType(field),whitespaceValue(field,value))}
const numericBounds=new Set(['minInclusive','minExclusive','maxInclusive','maxExclusive']);
const integerFacets=new Set(['length','minLength','maxLength','totalDigits','fractionDigits']);
const order=(a,b)=>a<b?-1:a>b?1:0;
function compareDecimal(a,b){
 const negative=a.startsWith('-');
 if(negative!==b.startsWith('-'))return negative?-1:1;
 const [wholeA,fractionA='']=a.replace(/^-/,'').split('.'),[wholeB,fractionB='']=b.replace(/^-/,'').split('.');
 return (negative?-1:1)*(order(wholeA.length,wholeB.length)||order(wholeA,wholeB)||order(fractionA,fractionB));
}
function numericBound(type,key,value){
 const lower=key.startsWith('min'),exclusive=key.endsWith('Exclusive');
 if(integers.has(type)&&/^-?\d+$/.test(value)){
  // Exclusive integer endpoints are equivalent to the next inclusive integer.
  return {key:lower?'minInclusive':'maxInclusive',value:String(BigInt(value)+(exclusive?(lower?1n:-1n):0n)),exclusive:false,lower};
 }
 if(type==='decimal'&&/^-?\d+(?:\.\d+)?$/.test(value))return {key,value,exclusive,lower};
 if((type==='float'||type==='double')&&(/^-?INF$/.test(value)||Number.isFinite(Number(value))&&value.trim()!=='')){
  return {key,value,exclusive,lower};
 }
 return null;
}
function stricterBound(type,bound,prior){
 if(!prior)return true;
 const real=value=>value==='INF'?Infinity:value==='-INF'?-Infinity:Number(value);
 const cmp=type==='float'||type==='double'
  ?order(real(bound.value),real(prior.value)):compareDecimal(bound.value,prior.value);
 return cmp===0?bound.exclusive&&!prior.exclusive:bound.lower?cmp>0:cmp<0;
}
function facetRows(field,other){
 const facets=field.facets||[];
 if(!other)return sorted(facets);
 const rows=[],limits=new Map(),bounds=new Map(),lengths=new Set(),type=builtinType(field);
 for(const [key,lexical] of facets){
  if(key==='whiteSpace'&&['preserve','replace','collapse'].includes(lexical))continue;
  const value=integerFacets.has(key)?decimalValue('integer',lexical):numericBounds.has(key)?typedValue(field,lexical):lexical;
  const bound=numericBounds.has(key)&&numericBound(type,key,value);
  if(bound){
   if(stricterBound(type,bound,bounds.get(bound.lower)))bounds.set(bound.lower,bound);
   continue;
  }
  if(!integerFacets.has(key)||!/^\d+$/.test(value)){
   rows.push([key,value]);
   continue;
  }
  if(key==='length'){
   lengths.add(value);
   continue;
  }
  if(key==='fractionDigits'&&value==='0'&&integers.has(type))continue;
  const prior=limits.get(key);
  if(prior===undefined||(key==='minLength'?BigInt(value)>BigInt(prior):BigInt(value)<BigInt(prior)))limits.set(key,value);
 }
 const minimum=limits.get('minLength'),maximum=limits.get('maxLength');
 if(!lengths.size&&minimum!==undefined&&minimum===maximum)lengths.add(minimum);
 if(lengths.size===1){
  const length=BigInt([...lengths][0]);
  if((minimum===undefined||BigInt(minimum)<=length)&&(maximum===undefined||BigInt(maximum)>=length)){
   limits.delete('minLength');
   limits.delete('maxLength');
  }
 }
 for(const length of lengths)rows.push(['length',length]);
 for(const {key,value} of bounds.values())rows.push([key,value]);
 const whitespace=whitespaceRule(field);
 if(whitespace!==builtinWhitespace(type)&&['preserve','replace','collapse'].includes(whitespace))rows.push(['whiteSpace',whitespace]);
 return sorted([...rows,...limits]);
}
function flagValues(field,other){
 return Object.fromEntries(Object.entries(field.flags||{}).filter(([key,value])=>{
  if(!other)return true;
  if(wildcard(field)&&key==="namespace")return false;
  if(["nillable","abstract","typeAbstract","mixed"].includes(key)&&["false","0"].includes(value))return false;
  return !(key==="processContents"&&value==="strict");
 }).map(([key,value])=>{
  const name=field.flagQNames?.[key];
  if(!other)return [key,showQName(value,name)];
  if(["fixed","default"].includes(key)){
   if(field.alternatives?.length||other.alternatives?.length){
    const selected=field.unionFlagTypes?.[key],otherSelected=other.unionFlagTypes?.[key];
     return [key,provedSelection(selected,name)&&provedSelection(otherSelected,other.flagQNames?.[key])
      ?selectedUnionValue(selected,value,name):value];
   }
   return [key,name&&other.flagQNames?.[key]?name:typedValue(field,value)];
  }
  if(["nillable","abstract","typeAbstract","mixed"].includes(key))value=value==="1"?"true":value==="0"?"false":value;
  return [key,value];
 }));
}
function selectedUnionValue(selection,value,name){
 const [type,whitespace]=selection;
 const family=integers.has(type)?'decimal':strings.has(type)?'string':type;
 return [family,name??enumerationValue(type,normalizeWhitespace(whitespace,value))];
}
const expandedNameType=type=>type==='QName'||type==='NOTATION';
const provedSelection=(selection,name)=>selection&&(!expandedNameType(selection[0])||name);
function unionValue(field,value,index){
 const selection=field.unionValueTypes?.[index],name=field.valueQNames?.[index];
 return provedSelection(selection,name)?selectedUnionValue(selection,value,name):null;
}
function hasUnresolvedUnionValues(field){
 const members=field.alternatives||[];
 return members.length>0&&((field.values||[]).some((_,index)=>!field.unionValueTypes?.[index])
  ||['fixed','default'].some(key=>Object.hasOwn(field.flags||{},key)&&!field.unionFlagTypes?.[key])
  ||members.some(hasUnresolvedUnionValues));
}
const unionCoverageNote="Для некоторых значений union тип не определён. Перечисления, фиксированные значения и значения по умолчанию с неопределённым типом сопоставлены по исходному тексту.";
function hasNotationMember(field){
 return builtinType(field)==='NOTATION'||(field.alternatives||[]).some(hasNotationMember);
}
function hasUnresolvedNotationValues(field){
 const members=field.alternatives||[],direct=builtinType(field)==='NOTATION',possible=members.some(hasNotationMember);
 const missing=(selection,name)=>!name&&(direct||selection?.[0]==='NOTATION'||possible&&!selection);
 return (field.values||[]).some((_,index)=>missing(field.unionValueTypes?.[index],field.valueQNames?.[index]))
  ||['fixed','default'].some(key=>Object.hasOwn(field.flags||{},key)&&missing(field.unionFlagTypes?.[key],field.flagQNames?.[key]))
  ||members.some(hasUnresolvedNotationValues);
}
const notationCoverageNote="Для некоторых значений NOTATION пространство имён не установлено. Такие значения сопоставлены по исходному тексту.";
function enumRows(field,other){
 const union=field.unionValueTypes||other?.unionValueTypes,typedUnion=field.unionValueTypes&&other?.unionValueTypes;
 const knownLexemes=new Set(),knownValues=new Set(),unknownLexemes=new Set();
 if(typedUnion)(other.values||[]).forEach(([value],index)=>{
  const key=unionValue(other,value,index);
  if(key){
   knownLexemes.add(value);
   knownValues.add(stable(key));
  }else unknownLexemes.add(value);
 });
 const rows=(field.values||[]).map(([value,doc],index)=>{
  const name=field.valueQNames?.[index];
  if(!other)return [showQName(value,name),doc];
  if(typedUnion){
   const key=unionValue(field,value,index);
   // An unknown entry on the other side should not mask a comparison
   // between proved values, even when one has the same spelling.
   if(key&&(!unknownLexemes.has(value)||knownLexemes.has(value)||knownValues.has(stable(key))))return [key,doc];
  }
  if(union)return [value,doc];
  const rule=field.enumWhiteSpace??whitespaceRule(field)??'preserve';
  return [name&&other.valueQNames?name:enumerationValue(builtinType(field),normalizeWhitespace(rule,value)),doc];
 });
 if(!other)return rows;
 return [...new Map(rows.map(row=>[stable(row),row])).values()];
}
function enumValues(field,other){
 const values=enumRows(field,other).map(([value])=>value);
 return sorted(other?[...new Map(values.map(value=>[stable(value),value])).values()]:values);
}
const alternatives=rows=>(rows||[]).map(r=>({type:typeLabel(r,"type"),base:typeLabel(r,"base"),flags:flagValues(r),facets:sorted(r.facets),...(r.patternGroups?{patternGroups:patternGroups(r)}:{}),values:sorted(enumRows(r)),alternatives:alternatives(r.alternatives)}));
function sameAlternatives(left=[],right=[]){
 if(left.length!==right.length)return false;
 for(const [index,a] of left.entries()){
  const b=right[index];
  const equal=sameType(a,b,"type")&&sameType(a,b,"base")&&stable(flagValues(a,b))===stable(flagValues(b,a))&&stable(facetRows(a,b))===stable(facetRows(b,a))&&samePatternGroups(a,b)&&stable(sorted(enumRows(a,b)))===stable(sorted(enumRows(b,a)))&&sameAlternatives(a.alternatives,b.alternatives);
  if(!equal)return false;
 }
 return true;
}
const properties=[
 ["kind","Вид узла",f=>f.kind],
 ["type","Тип",f=>f.type||""],
 ["base","Базовый тип",f=>f.base||""],
 ["min","Минимальная кратность",f=>occurrence(f.min)],
 ["max","Максимальная кратность",f=>occurrence(f.max)],
 ["group","Группа и вариант выбора",f=>occurrenceGroups(f.group)],
 ["ns","Пространство имён",fieldNamespace],
 ["flags","Фиксированное значение, значение по умолчанию и другие свойства",flagValues],
 ["facets","Ограничения значения",facetRows],
 ["patternGroups","Шаблоны XSD (ИЛИ внутри группы, И между группами)",patternGroups],
 ["values","Допустимые значения",enumValues],
 ["alternatives","Альтернативы типа (union)",f=>alternatives(f.alternatives)],
 ["doc","Описание и условия заполнения",f=>f.doc||""],
 ["valueDescriptions","Описания допустимых значений",f=>sorted(enumRows(f))]
];
function index(fields){
 const map=new Map();
 for(const f of fields){
  if(map.has(f.path))throw Error("Неоднозначный XML-путь в схеме: "+f.path);
  map.set(f.path,f);
 }
 return map;
}
// Compare the compositor for shared children. Added and removed children
// already have separate rows. Attributes are outside the XSD content model.
function modelNames(model,names=new Set()){
 if(typeof model==="string")names.add(model);
 else for(const item of model.slice(1))modelNames(item,names);
 return names;
}
function commonModel(model,names){
 if(typeof model==="string")return names.has(model)?model:null;
 const [kind,...entries]=model;
 const children=entries.map(entry=>commonModel(entry,names)).filter(entry=>entry!==null);
 if(children.length<2)return children[0]??null;
 if(kind==="sequence"){
  const ordered=[];
  for(const child of children){
   if(Array.isArray(child)&&child[0]==="sequence")ordered.push(...child.slice(1));
   else ordered.push(child);
  }
  return [kind,...ordered];
 }
 return [kind,...sorted(children)];
}
function describeModel(model){
 if(typeof model==="string")return model;
 const [kind,...children]=model;
 const caption={sequence:"Последовательность",all:"В любом порядке",choice:"Выбор"}[kind];
 return caption+" ("+children.map(describeModel).join(", ")+")";
}
function commonBoundedModel(model,names){
 if(typeof model==='string')return names.has(model)?model:null;
 model={...model,min:occurrence(model.min),max:occurrence(model.max)};
 if(model.kind==='group')model={...model,kind:'sequence'};
 const children=model.children.map(child=>commonBoundedModel(child,names)).filter(child=>child!==null);
 if(!children.length)return null;
 const unit=model.min==='1'&&model.max==='1';
 if(unit&&children.length===1)return children[0];
 const ordered=[];
 for(const child of children){
  if(model.kind==='sequence'&&typeof child!=='string'&&child.kind==='sequence'&&child.min==='1'&&child.max==='1')ordered.push(...child.children);
  else ordered.push(child);
 }
 return {...model,children:model.kind==='choice'||model.kind==='all'?sorted(ordered):ordered};
}
function describeBoundedModel(model){
 if(model===null)return '';
 if(typeof model==='string')return model;
 return ({sequence:'Последовательность',all:'В любом порядке',choice:'Выбор',group:'Группа'}[model.kind]||model.kind)+' ['+model.min+'..'+model.max+'] ('+model.children.map(describeBoundedModel).join(', ')+')';
}
// Choice and branch numbers are display ordinals. Membership and sequence
// order live in the parent's contentModel. Keep nested groups and their bounds.
const groupStructure=field=>occurrenceGroups(field.group).split(" ИЛИ ").map(group=>group.replace(/Выбор \d+, вариант \d+/g,"Выбор")).sort();
function mappedChildren(before,after,pairing){
 const beforeNames=modelNames(before.contentModel),afterNames=modelNames(after.contentModel),left=new Set(),right=new Set(),rename=new Map(),ambiguous=new Set();
 for(const name of beforeNames){
  const path=before.path+'/'+name,targets=pairing.targets.get(path);
  if(targets){
   if(targets.size!==1){ambiguous.add(path+' → '+[...targets].join(', '));continue}
   const target=[...targets][0],next=target.slice(after.path.length+1);
   if(!target.startsWith(after.path+'/')||!afterNames.has(next))continue;
   if(pairing.sources.get(target).size!==1){ambiguous.add([...pairing.sources.get(target)].join(', ')+' → '+target);continue}
   left.add(name);right.add(next);rename.set(next,name);
  }else if(afterNames.has(name)&&!pairing.sources.has(after.path+'/'+name)){
   left.add(name);right.add(name);rename.set(name,name);
  }
 }
 return {left,right,rename,ambiguous};
}
function renameModel(model,names){
 if(model===null)return null;
 if(typeof model==='string')return names.get(model)||model;
 if(Array.isArray(model))return [model[0],...model.slice(1).map(child=>renameModel(child,names))];
 return {...model,children:model.children.map(child=>renameModel(child,names))};
}
function differences(before,after,beforeParent,afterParent,pairing){
 const changes=properties.filter(([key,,get])=>{
  if(key==="type"||key==="base")return !sameType(before,after,key);
  if(key==="alternatives")return !sameAlternatives(before.alternatives,after.alternatives);
  if(key==="patternGroups")return !samePatternGroups(before,after);
  if(key==="group"&&beforeParent?.contentModel&&afterParent?.contentModel)return stable(groupStructure(before))!==stable(groupStructure(after));
  if(key==="valueDescriptions"){
   const common=(f,other)=>{
    const keys=new Set(enumRows(other,f).map(([v])=>stable(v)));
    return sorted(enumRows(f,other).filter(([v])=>keys.has(stable(v))));
   };
   return stable(common(before,after))!==stable(common(after,before));
  }
  return stable(get(before,after))!==stable(get(after,before));
 }).map(([key,label,get])=>{
  const clarify=(key==="type"||key==="base")&&get(before)===get(after);
  return {key,label,before:clarify?typeLabel(before,key):get(before),after:clarify?typeLabel(after,key):get(after)};
 });
 if(before.contentModel&&after.contentModel){
  const common=mappedChildren(before,after,pairing);
  const bounded=before.contentModelBounds&&after.contentModelBounds;
  const left=bounded?commonBoundedModel(before.contentModelBounds,common.left):commonModel(before.contentModel,common.left);
  const right=bounded?commonBoundedModel(after.contentModelBounds,common.right):commonModel(after.contentModel,common.right);
  // Rename before normalizing unordered groups, so their sort order does not
  // depend on the new spellings of confirmed child names.
  const renamed=renameModel(right,common.rename),normalized=bounded?(renamed&&commonBoundedModel(renamed,common.left)):(renamed&&commonModel(renamed,common.left));
  if(stable(left)!==stable(normalized)){
   const describe=bounded?describeBoundedModel:describeModel;
   changes.push({key:"contentModel",label:"Порядок и группировка дочерних элементов",before:describe(left),after:describe(right)});
  }
  if(common.ambiguous.size)changes.push({key:"contentModelMapping",label:"Порядок дочерних элементов требует проверки",before:"Неоднозначное сопоставление: "+[...common.ambiguous].join('; '),after:"Проверьте порядок и группировку по исходным XSD."});
 }
 return changes;
}
function compare(left,right,mappings=[]){
 const a=index(left),b=index(right),results=[],usedBefore=new Set(),usedAfter=new Set(),seen=new Set();
 const pairing={targets:new Map(),sources:new Map()},pairs=[];
 if(!Array.isArray(mappings))throw Error("Соответствия должны быть списком пар XML-путей.");
 for(const pair of mappings){
  if(!pair||typeof pair.beforePath!=="string"||typeof pair.afterPath!=="string"||!a.has(pair.beforePath)||!b.has(pair.afterPath))throw Error("Не найден XML-путь ручного соответствия: "+String(pair?.beforePath)+" → "+String(pair?.afterPath));
  const key=JSON.stringify([pair.beforePath,pair.afterPath]);if(seen.has(key))continue;seen.add(key);
  pairs.push(pair);
  for(const [map,from,to] of [[pairing.targets,pair.beforePath,pair.afterPath],[pairing.sources,pair.afterPath,pair.beforePath]]){if(!map.has(from))map.set(from,new Set());map.get(from).add(to)}
  usedBefore.add(pair.beforePath);usedAfter.add(pair.afterPath);
 }
 for(const pair of pairs){
  const before=a.get(pair.beforePath),after=b.get(pair.afterPath),changes=differences(before,after,left[before.parent],right[after.parent],pairing);
  results.push({path:pair.beforePath,beforePath:pair.beforePath,afterPath:pair.afterPath,manual:true,comment:typeof pair.comment==="string"?pair.comment:"",status:changes.length?"changed":"mapped",before,after,changes});
 }
 for(const path of [...new Set([...a.keys(),...b.keys()])].sort()){
  const before=usedBefore.has(path)?undefined:a.get(path),after=usedAfter.has(path)?undefined:b.get(path);
  if(!before&&!after)continue;
  if(!before||!after){results.push({path,status:before?"removed":"added",before,after,changes:[]});continue}
  const changes=differences(before,after,left[before.parent],right[after.parent],pairing);
  if(changes.length)results.push({path,status:"changed",before,after,changes});
 }
 return results.sort((x,y)=>x.path.localeCompare(y.path)||(x.afterPath||"").localeCompare(y.afterPath||""));
}
const api={compare,fieldNamespace,hasUnresolvedUnionValues,unionCoverageNote,hasUnresolvedNotationValues,notationCoverageNote};root.SedoCompare=api;
if(typeof module!=="undefined"&&module.exports)module.exports=api;
})(typeof window!=="undefined"?window:globalThis);

