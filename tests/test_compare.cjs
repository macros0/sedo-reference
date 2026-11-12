const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {compare}=require("../web/compare-core.js");
function field(name,changes={}){return {name,path:"root/"+name,parent:0,children:[],kind:"element",type:"xs:string",base:"xs:string",min:"0",max:"1",group:"",ns:"urn:test",flags:{},facets:[],values:[],doc:"Описание",...changes}}
const left=[field("old"),field("changed")],right=[field("added"),field("changed",{min:"1",facets:[["maxLength","12"]]})];
let diff=compare(left,right);
assert.deepEqual(diff.map(x=>[x.path,x.status]),[["root/added","added"],["root/changed","changed"],["root/old","removed"]]);
assert.deepEqual(diff[1].changes.map(c=>c.key),["min","facets"]);
assert.equal(compare(left,structuredClone(left)).length,0);
assert.equal(compare([field("x",{values:[["1","Один"],["2","Два"]],facets:[["minLength","1"],["maxLength","9"]]})],[field("x",{values:[["2","Два"],["1","Один"]],facets:[["maxLength","9"],["minLength","1"]]})]).length,0);
assert.deepEqual(compare([field("x")],[field("x",{doc:"Новое описание"})])[0].changes.map(c=>c.key),["doc"]);
assert.deepEqual(compare([field("x",{values:[["1","Прежнее"]]})],[field("x",{values:[["1","Новое"]]})])[0].changes.map(c=>c.key),["valueDescriptions"]);
assert.deepEqual(compare([field("x",{values:[["1","Один"]]})],[field("x",{values:[["1","Один"],["2","Два"]]})])[0].changes.map(c=>c.key),["values"]);
assert.deepEqual(compare([field("x")],[field("x",{flags:{fixed:"true"},max:"unbounded",group:"choice [0..1]"})])[0].changes.map(c=>c.key),["max","group","flags"]);
assert.equal(compare(right,left).find(x=>x.path==="root/added").status,"removed");
assert.throws(()=>compare([field("x"),field("x")],[]),/Неоднозначный/);
assert.equal(compare([field("u",{alternatives:[{base:"xs:string",facets:[["maxLength","5"]],values:[],flags:{}}]})],[field("u",{alternatives:[{base:"xs:string",facets:[["maxLength","6"]],values:[],flags:{}}]})])[0].changes[0].key,"alternatives");
const root=path.resolve(__dirname,".."),catalog=JSON.parse(fs.readFileSync(path.join(root,"data/catalog.json"),"utf8"));
const load=r=>JSON.parse(fs.readFileSync(path.join(root,"data/fields",r.revision_id+".json"),"utf8")).fields;
const pair=n=>catalog.revisions.filter(r=>r.number===n&&r.status==='matched');
const p10110=pair("10110");assert.equal(compare(load(p10110[0]),load(p10110[1])).length,0);
const p32=pair("32");assert.ok(compare(load(p32[0]),load(p32[1])).length>0);
for(const revision of catalog.revisions.filter(r=>r.status==='matched'))assert.equal(compare(load(revision),load(revision)).length,0);
console.log("Comparison verified: add/remove/change, restrictions, enum order and descriptions, duplicates, direction, real prod/test pairs and all editions.");

