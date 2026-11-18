const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const viewer=fs.readFileSync(path.resolve(__dirname,'../viewer-template.html'),'utf8');
class Element{
 constructor(tag){Object.assign(this,{tagName:tag,children:[],value:'',textContent:'',className:'',checked:false,style:{setProperty(){}}})}
 append(...nodes){this.children.push(...nodes)}replaceChildren(...nodes){this.children=nodes}setAttribute(){}add(node){this.append(node)}querySelector(){return null}
}
const fields=[
 ['root',-1,[1,4,6]],['root/branch',0,[2,3]],['root/branch/problem',1,[]],['root/branch/clean',1,[]],
 ['root/other',0,[5]],['root/other/@flag',4,[]],['root/empty',0,[7]],['root/empty/plain',6,[]]
].map(([path,parent,children])=>({path,name:path.split('/').pop(),parent,children,kind:'element',type:'xs:string',base:'xs:string',doc:'Описание '+path,min:'0',max:'1',facets:[],flags:{},values:[],group:''}));
const data={fields,version:'test',archive:'test',review_sources:[
 {name:'one',findings:[{kind:'Тип',field_paths:['root/branch/problem'],spec:'A',xsd:'B'}]},
 {name:'two',findings:[{kind:'Кратность',field_paths:['root/branch/problem','root/other/@flag'],spec:'A',xsd:'B'}]}
]};
const nodes=new Map(),document={createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text,children:[]}),querySelector:()=>({textContent:JSON.stringify(data)}),getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id)}};
const context=vm.createContext({document,Option:class extends Element{constructor(label,value){super('option');this.textContent=label;this.value=value}},console});
vm.runInContext([...viewer.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)].at(-1)[1],context);
const walk=e=>[e,...(e.children||[]).flatMap(walk)];
const names=()=>walk(nodes.get('tree')).filter(n=>n.className==='name').map(n=>n.textContent);
const filter=nodes.get('review-only');
filter.checked=true;filter.onchange();
assert.deepEqual(names(),['root','branch','problem','other','@flag']);
assert.deepEqual(walk(nodes.get('tree')).filter(n=>n.className==='review-mark').map(n=>n.textContent),['Замечания · 2','Замечания · 1']);
assert.equal(vm.runInContext('fields[current].path',context),'root/branch/problem');
filter.checked=false;filter.onchange();
assert.ok(names().includes('problem'));assert.ok(names().includes('clean'));
filter.checked=true;filter.onchange();
nodes.get('query').value='flag';nodes.get('query').oninput();
assert.equal(nodes.get('tree').children.length,1);
assert.equal(nodes.get('tree').children[0].children[0].textContent,'@flag');
nodes.get('query').value='';nodes.get('query').oninput();
nodes.get('scope').value='root/empty';nodes.get('scope').onchange();
assert.equal(nodes.get('tree').children[0].className,'empty');
nodes.get('reset').onclick();
assert.equal(filter.checked,false);assert.equal(nodes.get('scope').value,'');assert.equal(nodes.get('query').value,'');
assert.deepEqual(names(),['root','branch','other','empty']);
console.log('Tree review badges, counts, ancestor navigation, search intersection, empty scopes and reset verified.');
