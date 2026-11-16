/* Structural comparison shared by the offline site and Node acceptance tests. */
(function(root){
"use strict";
const canonical=value=>{
 if(Array.isArray(value))return value.map(canonical);
 if(value&&typeof value==="object")return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));
 return value;
};
const stable=value=>JSON.stringify(canonical(value));
const sorted=rows=>[...(rows||[])].sort((a,b)=>stable(a).localeCompare(stable(b)));
const lexicalType=value=>(value||"").replace(/^(?:xs|xsd):/,"{http://www.w3.org/2001/XMLSchema}");
function sameType(a,b,key){
 const left=a[key]||"",right=b[key]||"",ns=key+"Ns";
 // Older trees have lexical QNames only. Use their original comparison until
 // both sides carry namespace metadata; never infer a custom prefix's URI.
 if(Object.hasOwn(a,ns)&&Object.hasOwn(b,ns))return a[ns]===b[ns]&&left.split(":").at(-1)===right.split(":").at(-1);
 return lexicalType(left)===lexicalType(right);
}
const typeLabel=(f,key)=>f[key]?(f[key]+(Object.hasOwn(f,key+"Ns")?" ("+f[key+"Ns"]+")":"")):"";
const alternatives=rows=>sorted((rows||[]).map(r=>({type:typeLabel(r,"type"),base:typeLabel(r,"base"),flags:r.flags||{},facets:sorted(r.facets),values:sorted(r.values),alternatives:alternatives(r.alternatives)})));
function sameAlternatives(left=[],right=[]){
 if(left.length!==right.length)return false;
 const remaining=[...right];
 for(const a of left){
  const index=remaining.findIndex(b=>sameType(a,b,"type")&&sameType(a,b,"base")&&stable(a.flags||{})===stable(b.flags||{})&&stable(sorted(a.facets))===stable(sorted(b.facets))&&stable(sorted(a.values))===stable(sorted(b.values))&&sameAlternatives(a.alternatives,b.alternatives));
  if(index<0)return false;
  remaining.splice(index,1);
 }
 return true;
}
const properties=[
 ["kind","Вид узла",f=>f.kind],
 ["type","Тип",f=>f.type||""],
 ["base","Базовый тип",f=>f.base||""],
 ["min","Минимальная кратность",f=>String(f.min)],
 ["max","Максимальная кратность",f=>String(f.max)],
 ["group","Группа и вариант выбора",f=>f.group||""],
 ["ns","Пространство имён",f=>f.ns||""],
 ["flags","Фиксированное значение, значение по умолчанию и другие свойства",f=>f.flags||{}],
 ["facets","Ограничения значения",f=>sorted(f.facets)],
 ["values","Допустимые значения",f=>sorted((f.values||[]).map(([v])=>v))],
 ["alternatives","Альтернативы типа (union)",f=>alternatives(f.alternatives)],
 ["doc","Описание и условия заполнения",f=>f.doc||""],
 ["valueDescriptions","Описания допустимых значений",f=>sorted(f.values)]
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
function differences(before,after){
 const changes=properties.filter(([key,,get])=>{
  if(key==="type"||key==="base")return !sameType(before,after,key);
  if(key==="alternatives")return !sameAlternatives(before.alternatives,after.alternatives);
  if(key==="valueDescriptions"){
   const common=(f,other)=>sorted((f.values||[]).filter(([v])=>(other.values||[]).some(([w])=>v===w)));
   return stable(common(before,after))!==stable(common(after,before));
  }
  return stable(get(before))!==stable(get(after));
 }).map(([key,label,get])=>{
  const clarify=(key==="type"||key==="base")&&get(before)===get(after);
  return {key,label,before:clarify?typeLabel(before,key):get(before),after:clarify?typeLabel(after,key):get(after)};
 });
 if(before.contentModel&&after.contentModel){
  const left=commonModel(before.contentModel,modelNames(after.contentModel));
  const right=commonModel(after.contentModel,modelNames(before.contentModel));
  if(stable(left)!==stable(right)){
   changes.push({key:"contentModel",label:"Порядок и группировка дочерних элементов",before:describeModel(left),after:describeModel(right)});
  }
 }
 return changes;
}
function compare(left,right,mappings=[]){
 const a=index(left),b=index(right),results=[],usedBefore=new Set(),usedAfter=new Set(),seen=new Set();
 if(!Array.isArray(mappings))throw Error("Соответствия должны быть списком пар XML-путей.");
 for(const pair of mappings){
  if(!pair||typeof pair.beforePath!=="string"||typeof pair.afterPath!=="string"||!a.has(pair.beforePath)||!b.has(pair.afterPath))throw Error("Не найден XML-путь ручного соответствия: "+String(pair?.beforePath)+" → "+String(pair?.afterPath));
  const key=JSON.stringify([pair.beforePath,pair.afterPath]);if(seen.has(key))continue;seen.add(key);
  const before=a.get(pair.beforePath),after=b.get(pair.afterPath),changes=differences(before,after);
  usedBefore.add(pair.beforePath);usedAfter.add(pair.afterPath);
  results.push({path:pair.beforePath,beforePath:pair.beforePath,afterPath:pair.afterPath,manual:true,comment:typeof pair.comment==="string"?pair.comment:"",status:changes.length?"changed":"mapped",before,after,changes});
 }
 for(const path of [...new Set([...a.keys(),...b.keys()])].sort()){
  const before=usedBefore.has(path)?undefined:a.get(path),after=usedAfter.has(path)?undefined:b.get(path);
  if(!before&&!after)continue;
  if(!before||!after){results.push({path,status:before?"removed":"added",before,after,changes:[]});continue}
  const changes=differences(before,after);
  if(changes.length)results.push({path,status:"changed",before,after,changes});
 }
 return results.sort((x,y)=>x.path.localeCompare(y.path)||(x.afterPath||"").localeCompare(y.afterPath||""));
}
const api={compare};root.SedoCompare=api;
if(typeof module!=="undefined"&&module.exports)module.exports=api;
})(typeof window!=="undefined"?window:globalThis);

