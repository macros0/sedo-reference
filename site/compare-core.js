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
function compare(left,right){
 const a=index(left),b=index(right),results=[];
 for(const path of [...new Set([...a.keys(),...b.keys()])].sort()){
  const before=a.get(path),after=b.get(path);
  if(!before||!after){results.push({path,status:before?"removed":"added",before,after,changes:[]});continue}
  const changes=properties.filter(([key,,get])=>{
   if(key==="valueDescriptions"){
    // Report annotation-only differences here; changes to allowed values are listed separately.
    const common=(f,other)=>sorted((f.values||[]).filter(([v])=>(other.values||[]).some(([w])=>v===w)));
    return stable(common(before,after))!==stable(common(after,before));
   }
   return stable(get(before))!==stable(get(after));
  }).map(([key,label,get])=>({key,label,before:get(before),after:get(after)}));
  if(changes.length)results.push({path,status:"changed",before,after,changes});
 }
 return results;
}
const api={compare};root.SedoCompare=api;
if(typeof module!=="undefined"&&module.exports)module.exports=api;
})(typeof window!=="undefined"?window:globalThis);

