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
const alternatives=rows=>sorted((rows||[]).map(r=>({type:r.type||"",base:r.base||"",flags:r.flags||{},facets:sorted(r.facets),values:sorted(r.values),alternatives:alternatives(r.alternatives)})));
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
function differences(before,after){
 return properties.filter(([key,,get])=>{
  if(key==="valueDescriptions"){
   const common=(f,other)=>sorted((f.values||[]).filter(([v])=>(other.values||[]).some(([w])=>v===w)));
   return stable(common(before,after))!==stable(common(after,before));
  }
  return stable(get(before))!==stable(get(after));
 }).map(([key,label,get])=>({key,label,before:get(before),after:get(after)}));
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

