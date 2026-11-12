const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),viewer=fs.readFileSync(path.join(root,'viewer-template.html'),'utf8');
const catalog=JSON.parse(fs.readFileSync(path.join(root,'data/catalog.json'),'utf8'));
class Element{constructor(tag){this.tagName=tag;this.children=[];this.value='';this.textContent='';this.className='';this.style={setProperty(){}}}append(...nodes){this.children.push(...nodes)}replaceChildren(...nodes){this.children=[...nodes]}setAttribute(){}add(node){this.append(node)}querySelector(){return null}}
function app(number){
 const r=catalog.revisions.find(r=>r.number===number&&r.environment==='prod'),fields=JSON.parse(fs.readFileSync(path.join(root,'data/fields',r.revision_id+'.json'),'utf8')).fields;
 const nodes=new Map(),data={fields,version:'test',date:'test',archive:'test',files:1,sha256:'test'};
 const document={createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text}),querySelector:()=>({textContent:JSON.stringify(data)}),getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id)}};
 const context=vm.createContext({document,Option:class extends Element{constructor(label,value){super('option');this.textContent=label;this.value=value}},Set,Map,JSON,console});
 const scripts=[...viewer.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]);vm.runInContext(scripts.at(-1),context);
 return {nodes,context,fields};
}
for(const number of ['86','10110','611']){
 const {nodes,context,fields}=app(number),scope=nodes.get('scope'),tree=nodes.get('tree');
 const block=fields.find(f=>f.parent===0&&f.children.length),nested=fields.find(f=>f.parent>0&&f.children.length);
 assert.ok(scope.children.some(o=>o.value===block.path),'top-level block '+number);
 if(nested)assert.ok(scope.children.some(o=>o.value===nested.path),'nested block '+number);
 scope.value=block.path;scope.onchange();
 assert.equal(vm.runInContext('fields[current].path',context),block.path,'selection opens the chosen block');
 nodes.get('query').value=fields[block.children[0]].name;nodes.get('query').oninput();
 const hits=tree.children.filter(e=>e.className?.startsWith('result'));
 assert.ok(hits.length,'search inside selected block');
 assert.ok(hits.every(e=>e.children[1].textContent.startsWith(block.path+'/')||e.children[1].textContent===block.path),'search excludes other blocks');
 const childButton=nodes.get('detail').children.find(e=>e.className==='children').children[0];childButton.onclick();
 assert.equal(scope.value,block.path,'opening a child keeps selected scope');
 nodes.get('reset').onclick();assert.equal(scope.value,'');assert.equal(nodes.get('query').value,'');
}
{
 const {nodes}=app('104');nodes.get('query').value='holidType';nodes.get('query').oninput();
 const hit=nodes.get('tree').children.find(e=>e.className?.startsWith('result'));assert.ok(hit);hit.onclick();
 const content=node=>typeof node==='string'?node:(node.textContent||'')+(node.children||[]).map(content).join(' ');
 assert.ok(content(nodes.get('detail')).includes('пустая строка'),'empty union option must be visible');
 assert.ok(content(nodes.get('detail')).includes('Всего цифр, до: 1'),'integer alternative restriction');
}
console.log('Viewer sections verified on 86, 10110 and 611: actual blocks, nested paths, scoped search, navigation and reset.');
