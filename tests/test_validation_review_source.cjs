const assert=require('node:assert/strict');
const {mount}=require('../web/validation-source.js');
class Node{
 constructor(tag){Object.assign(this,{tag,children:[],_text:'',attrs:{},style:{},value:'',hidden:false})}
 set textContent(value){this._text=String(value);this.children=[]}get textContent(){return this._text+this.children.map(n=>n.textContent).join('')}
 append(...nodes){this.children.push(...nodes)}replaceChildren(...nodes){this._text='';this.children=nodes}
 setAttribute(key,value){this.attrs[key]=value}getContext(){return null}focus(){}scrollIntoView(){}setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b}
}
const doc={createElement:tag=>new Node(tag),createTextNode:text=>{const n=new Node('#text');n.textContent=text;return n}};
const walk=node=>[node,...node.children.flatMap(walk)],host=new Node('section');
let opened=null;
const view=mount(host,doc,{onReview:field=>{opened=field}});
const xml='<root attr="PRIVATE"><code>SECRET</code></root>';
view.setSource(xml);
const start=xml.indexOf('<code>')+1,close=xml.indexOf('</code>')+2;
const location={field:2,path:'/root/code',line:1,ranges:[{start,end:start+4,kind:'tag'},{start:close,end:close+4,kind:'tag'}]};
view.setReviews([location,{field:1,path:'/root/@attr',line:1,ranges:[{start:6,end:10,kind:'attribute'}]}]);
const buttons=()=>walk(host).filter(n=>n.className==='xml-review-tag');
assert.deepEqual(buttons().map(n=>n.textContent),['attr','code','code']);
buttons()[0].onclick();assert.equal(opened,1);
view.setErrors([{line:1,locations:[location]}]);
assert.deepEqual(walk(host).filter(n=>n.tag==='mark').map(n=>n.textContent),['code','code']);
assert.equal(buttons().length,3,'validation error highlight and review controls coexist');
assert.equal(view.showReview(location),true);assert.equal(walk(host).filter(n=>n.tag==='mark').length,0);
walk(host).find(n=>n.tag==='button'&&n.textContent==='Редактировать XML').onclick();
const editor=walk(host).find(n=>n.tag==='textarea');editor.value='<root/>';editor.oninput();
assert.equal(buttons().length,0,'draft changes discard old review ranges');
view.clear();assert.equal(host.hidden,true);assert.equal(buttons().length,0);
console.log('Private XML review buttons, attributes, overlapping error marks, edits and clear verified.');
