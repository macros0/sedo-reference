"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),cp=require('node:child_process');
const root=path.resolve(__dirname,'..'),cat=JSON.parse(fs.readFileSync(path.join(root,'data/catalog.json'),'utf8'));
const row=cat.revisions.find(r=>r.number==='10110'&&r.environment==='prod'),file='site/editions/'+row.revision_id+'.html';
const html=process.env.SEDO_TEST_BASE_VIEWER?cp.execFileSync('git',['show','cf38e8e:'+file],{cwd:root,encoding:'utf8'}):fs.readFileSync(path.join(root,file),'utf8');
const scripts=[...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)],data=JSON.parse(scripts.find(s=>s[0].includes('id="catalog"'))[1]),handlers={},nodes=new Map();
let scrolled=0;
class Element{constructor(tag){Object.assign(this,{tagName:tag,children:[],value:'',textContent:'',className:'',style:{setProperty(){}}})}append(...xs){this.children.push(...xs)}replaceChildren(...xs){this.children=[...xs]}setAttribute(){}add(x){this.append(x)}querySelector(){return {scrollIntoView(){scrolled++}}}}
const parent={postMessage(){}},location={_hash:'',get hash(){return this._hash},set hash(value){this._hash=value.startsWith('#')?value:'#'+value},origin:'https://example.test'};
const context=vm.createContext({document:{createElement:t=>new Element(t),createTextNode:t=>({textContent:t}),querySelector:()=>({textContent:JSON.stringify(data)}),getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id)}},parent,location,window:{addEventListener:(type,fn)=>{handlers[type]=fn}},Option:class extends Element{constructor(text,value){super('option');this.textContent=text;this.value=value}},Set,Map,JSON,URLSearchParams,console});
vm.runInContext(scripts.at(-1)[1],context);
const fields=data.fields,block=fields.find(f=>f.parent===0&&f.children.length),target=fields.find(f=>f.path.startsWith('benefitResponse/benefit5/')&&!f.children.length);
assert.ok(block&&target);nodes.get('scope').value=block.path;nodes.get('scope').onchange();nodes.get('query').value='DOES_NOT_MATCH';nodes.get('query').oninput();
assert.equal(typeof handlers.message,'function','generated tree needs a checked field-selection message');
const event={source:parent,origin:location.origin,data:{type:'sedo-select-field',revision:row.revision_id,path:target.path}};
handlers.message({...event,source:{}});assert.equal(nodes.get('query').value,'DOES_NOT_MATCH');handlers.message({...event,data:{...event.data,revision:'other'}});assert.equal(nodes.get('query').value,'DOES_NOT_MATCH');handlers.message({...event,origin:'https://foreign.test'});assert.equal(nodes.get('query').value,'DOES_NOT_MATCH');
handlers.message(event);assert.equal(vm.runInContext('fields[current].path',context),target.path);assert.equal(nodes.get('query').value,'');assert.equal(nodes.get('scope').value,'');assert.ok(scrolled>0,'selected field is scrolled into view');assert.equal(new URLSearchParams(location.hash.slice(1)).get('field'),target.path);
// Re-selecting the current field still removes a newly entered hiding filter.
nodes.get('query').value='DOES_NOT_MATCH';handlers.message(event);assert.equal(nodes.get('query').value,'');
nodes.get('query').value='DOES_NOT_MATCH';vm.runInContext('select(0)',context);handlers.hashchange();assert.equal(nodes.get('query').value,'DOES_NOT_MATCH','normal tree selection hash changes must retain search');handlers.message(event);assert.equal(nodes.get('query').value,'','explicit validation jump still clears a hiding search');
location.hash=new URLSearchParams({field:fields[0].path}).toString();handlers.hashchange();assert.equal(vm.runInContext('fields[current].path',context),fields[0].path,'external new-field hash must still restore selection');
console.log('Validation navigation: actual generated tree, source/revision guards, field selection, repeated jumps and filter reset passed.');
