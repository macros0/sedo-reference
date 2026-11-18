const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),viewer=fs.readFileSync(path.join(root,'viewer-template.html'),'utf8');
const catalog=JSON.parse(fs.readFileSync(path.join(root,'data/catalog.json'),'utf8'));
class Element{constructor(tag){this.tagName=tag;this.children=[];this.value='';this.textContent='';this.className='';this.style={setProperty(){}}}append(...nodes){this.children.push(...nodes)}replaceChildren(...nodes){this.children=[...nodes]}setAttribute(){}add(node){this.append(node)}querySelector(){return null}}
function app(number,environment='prod',transform=fields=>fields,review_sources=[]){
 const r=catalog.revisions.find(r=>r.number===number&&r.environment===environment),fields=transform(JSON.parse(fs.readFileSync(path.join(root,'data/fields',r.revision_id+'.json'),'utf8')).fields);
 const nodes=new Map(),data={fields,version:'test',date:'test',archive:'test',files:1,sha256:'test',review_sources};
 const document={createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text}),querySelector:()=>({textContent:JSON.stringify(data)}),getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id)}};
 const context=vm.createContext({document,Option:class extends Element{constructor(label,value){super('option');this.textContent=label;this.value=value}},Set,Map,JSON,console});
 const scripts=[...viewer.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]);vm.runInContext(scripts.at(-1),context);
 return {nodes,context,fields};
}
for(const minimum of ['00',' 0 ']){
 const {nodes}=app('205','prod',fields=>[{...fields[0],min:minimum,max:'01',parent:-1,children:[]}]);
 const facts=nodes.get('detail').children.find(e=>e.className==='facts');
 assert.equal(facts.children[1].children[1].textContent,'0 … 1');
 assert.equal(facts.children[2].children[1].textContent,'Необязательно');
}
{
 const {nodes,context}=app('205','prod',fields=>[{...fields[0],parent:-1,children:[1]},
  {...fields[0],name:'child',path:fields[0].path+'/child',min:'1',max:'1',parent:0,children:[],group:'sequence [01..1]'}]);
 vm.runInContext('select(1,true)',context);
 const facts=nodes.get('detail').children.find(e=>e.className==='facts');
 assert.equal(facts.children[2].children[1].textContent,'Обязательно');
}
{
 const group='sequence [01..1] / Выбор 1, вариант 1 ИЛИ sequence [01..1] / Выбор 1, вариант 2';
 const {context}=app('205','prod',fields=>[{...fields[0],parent:-1,children:[],group}]);
 assert.equal(vm.runInContext('fields[0].group',context),'Выбор 1, вариант 1 ИЛИ Выбор 1, вариант 2');
}
for(const [number,env,path] of [['32','test','noticeRequest/*'],['32','test','noticeRequest/@*'],['10010','prod','benefitRequest/benefit6/*']]){
 const {nodes,context,fields}=app(number,env);
 const index=fields.findIndex(f=>f.path===path);assert.ok(index>=0);
 vm.runInContext('select('+index+',true)',context);
 const details=nodes.get('detail').children.find(e=>e.tagName==='details');
 assert.ok(details.children.some(e=>e.textContent.includes('##any')),path+' accepts any namespace');
}
{
 const field='benefitRequest/benefit6/assignmentParams';
 const findings=[{kind:'Ошибка порядка',field_paths:[field],field_link_scope:'field'},
  {kind:'Нет объявления',field_paths:[field],field_link_scope:'parent',xlocation:field+'/insurancePaymentObligation'}];
 const {nodes,context,fields}=app('10010','prod',f=>f,[{name:'Спецификация',findings}]);
 vm.runInContext('select('+fields.findIndex(f=>f.path===field)+',true)',context);
 const sections=nodes.get('detail').children.filter(e=>e.className==='field-reviews');
 assert.deepEqual(sections.map(e=>e.children[0].textContent),['Замечания к полю · 1','Замечания к дочерним полям · 1']);
}
for(const [base,flags,contentModel,expected] of [
 ['xs:anyType',{},undefined,'xs:anyType'],
 ['',{},['empty'],'Пустой составной тип'],
 ['',{mixed:'true'},['empty'],'Смешанный составной тип']
]){
 const {nodes}=app('205','prod',fields=>[{...fields[0],type:'',base,flags,contentModel,children:[]}]);
 const facts=nodes.get('detail').children.find(e=>e.className==='facts');
 assert.equal(facts.children[0].children[1].textContent,expected);
}
{
 const {nodes}=app('205','prod',fields=>[{...fields[0],children:[],facets:[['pattern','A'],['pattern','B']],patternGroups:[['A','B']],
  alternatives:[{base:'xs:string',facets:[['pattern','C'],['pattern','D']],patternGroups:[['C'],['D']]}]}]);
 const content=node=>(node.textContent||'')+(node.children||[]).map(content).join(' ');
 const abstract=app('205','prod',fields=>[{...fields[0],children:[],flags:{abstract:'true',typeAbstract:'true'}}]);
 assert.match(content(abstract.nodes.get('detail')),/Абстрактный элемент/);
 assert.match(content(abstract.nodes.get('detail')),/Абстрактный тип/);
 assert.match(content(nodes.get('detail')),/A.* ИЛИ .*B/);
 assert.match(content(nodes.get('detail')),/C.* И .*D/);
}
{
 const {nodes}=app('205','prod',fields=>[{...fields[0],name:'periods',path:'periods',parent:-1,children:[1],
  type:'',base:'',listItem:{base:'xs:dateTime',facets:[['pattern','[0-9]+Z']]},facets:[['maxLength','2']],values:[]},
  {...fields[0],name:'options',path:'periods/options',parent:0,children:[],type:'',base:'',values:[],
  alternatives:[{base:'xs:string'},{listItem:{type:'CodeValue',base:'xs:int',facets:[['minInclusive','5']],values:[['7','Семь']]}}]}]);
 const content=node=>typeof node==='string'?node:(node.textContent||'')+(node.children||[]).map(content).join(' ');
 assert.match(content(nodes.get('detail')),/Список значений/);
 assert.match(content(nodes.get('detail')),/Тип элемента списка.*xs:dateTime.*\[0-9\]/);
 const query=nodes.get('query');query.value='codevalue';query.oninput();
 const result=nodes.get('tree').children.find(row=>row.className?.startsWith('result'));
 assert.equal(result?.children[1].textContent,'periods/options');
 result.onclick();
 assert.match(content(nodes.get('detail')),/Тип элемента списка.*CodeValue.*Минимум включительно.*5.*Семь/);
 query.value='[0-9]+z';query.oninput();
 assert.equal(nodes.get('tree').children.find(row=>row.className?.startsWith('result'))?.children[1].textContent,'periods');
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
{
 const content=node=>typeof node==='string'?node:(node.textContent||'')+(node.children||[]).map(content).join(' ');
 const {nodes,context,fields}=app('10010','test');
 for(const name of ['parentProcessNum','subProcessReasonCode','subProcessReasonDetails']){
  const index=fields.findIndex(f=>f.name===name);assert.ok(index>=0);vm.runInContext('select('+index+',true)',context);
  const detail=content(nodes.get('detail'));assert.ok(!detail.includes('Вариант выбора'),name+' belongs to optional sequence, not choice');assert.match(detail,/Обязательно внутри блока/);assert.match(detail,/sequence \[0\.\.1\]/);assert.ok(!detail.includes('номером варианта'));
 }
 const choice=fields.findIndex(f=>/Выбор \d+, вариант/.test(f.group));assert.ok(choice>=0);vm.runInContext('select('+choice+',true)',context);assert.match(content(nodes.get('detail')),/Вариант выбора/);assert.match(content(nodes.get('detail')),/номером варианта/);
 const nested=app('10010','test',rows=>{rows.find(f=>f.name==='parentProcessNum').group='sequence [0..1] / Выбор 9, вариант 2';return rows});vm.runInContext('select('+nested.fields.findIndex(f=>f.name==='parentProcessNum')+',true)',nested.context);assert.match(content(nested.nodes.get('detail')),/Вариант выбора/,'optional sequence must preserve nested choice meaning');
}
console.log('Viewer sections verified: actual blocks, nested paths, scoped search, navigation, optional sequences and nested choices.');
{
 const content=n=>(n.textContent||'')+(n.children||[]).map(content).join(' ');
 for(const [number,kind,caption] of [['10110','attribute','Атрибут'],['10110','any','Расширение для элементов'],['12110','anyAttribute','Расширение для атрибутов']]){
  const {nodes,context,fields}=app(number);
  const index=fields.findIndex(f=>f.kind===kind);assert.ok(index>=0);
  vm.runInContext('select('+index+')',context);
  const title=nodes.get('detail').children.find(n=>n.className==='title');
  assert.equal(title.children[1].textContent,caption);
 }
 for(const [number,rule] of [['111',/в любом порядке.*xs:all/],['10110',/порядок.*xs:sequence/i]]){
  assert.match(content(app(number).nodes.get('detail')),rule);
 }
}
