"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),ids=new Map();
class Element{
 constructor(tag,cls='',text=''){Object.assign(this,{tagName:tag,className:cls,children:[],parent:null,_text:text,value:'',hidden:false,attrs:{},dataset:{},contentWindow:{}})}
 get id(){return this._id}set id(value){this._id=value;ids.set(value,this)}
 get textContent(){return this._text+this.children.map(x=>x.textContent).join(' ')}set textContent(value){this._text=String(value);this.children=[]}
 append(...items){for(const item of items){item.parent=this;this.children.push(item)}}
 replaceChildren(...items){this._text='';this.children=[];this.append(...items)}
 add(item){this.append(item)}setAttribute(k,v){this.attrs[k]=v}getAttribute(k){return this.attrs[k]??null}
 get src(){return this.attrs.src}set src(value){this.attrs.src=value}
 before(item){item.parent=this.parent;this.parent.children.splice(this.parent.children.indexOf(this),0,item)}
 after(item){item.parent=this.parent;this.parent.children.splice(this.parent.children.indexOf(this)+1,0,item)}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);if(this.id)ids.delete(this.id)}
 querySelector(selector){return walk(this).find(e=>selector.startsWith('.')?e.className.split(' ').includes(selector.slice(1)):e.tagName===selector)||null}
 get childNodes(){return this.children}
}
const walk=e=>[e,...e.children.flatMap(walk)],node=(tag,cls='',text='')=>new Element(tag,cls,text);
const shell=node('root');for(const id of ['catalog','versions','viewer','comparison','updates','import-help','error','crumbs','message-query','catalog-env','count','messages','viewer-replacement','view-title','view-desc','doc-source','doc-download','manual-source','revision-switch','frame']){const e=node(id==='frame'?'iframe':'div');e.id=id;shell.append(e)}
const revision=(id,environment)=>({revision_id:id,display_revision_id:id,number:'14',environment,title:'Найдены ошибки',ready:true,status:'matched',listed_now:true,document_version:'3.1',label_date:'12.12.2024',document_url:'https://example.test/service.docx',document_download:'documents/service.docx',document_filename:'service.docx'});
const p=revision('prod-first','prod'),t=revision('test-first','test');
for(const r of [p,t])r.edition_sources=['3.1','5.5','2.22'].map((v,i)=>({...r,document_version:v,document_url:'https://example.test/source-'+i+'.docx',document_download:'documents/source-'+i+'.docx'}));
const alias={...p,revision_id:'prod-old-link',document_version:'5.5'},testAlias={...t,revision_id:'test-old-link'},catalog={registry:[],revisions:[p,t,alias,testAlias],updates:[{revision_id:p.revision_id,previous_revision_id:null,initial_import:true},{revision_id:t.revision_id,previous_revision_id:null,initial_import:true}]};
catalog.schema_updates=catalog.updates;catalog.updates=[...catalog.updates,{revision_id:alias.revision_id,previous_revision_id:p.revision_id},{revision_id:testAlias.revision_id,previous_revision_id:t.revision_id}];
const location={hash:'#message=14',href:'https://example.test/index.html#message=14'};let mappingIdentity;
const ctx=vm.createContext({console,Map,Set,URL,URLSearchParams,Intl,Date,Promise,location,history:{replaceState:(_a,_b,value)=>{location.hash=value}},Option:class extends Element{constructor(text,value){super('option','',text);this.value=value}},window:{SEDO_CATALOG:catalog,addEventListener(){}},document:{createElement:tag=>node(tag),createTextNode:text=>node('text','',text),getElementById:id=>ids.get(id)||null,querySelector:()=>node('small'),head:node('head')},SedoCompare:{compare:()=>[]},SedoMapping:{store:{load:options=>{mappingIdentity=options.left.revision_id;return{pairs:[]}}},mount(){}},SedoComparisonExport:{}});
for(const name of ['compare.js','journal.js','app.js'])vm.runInContext(fs.readFileSync(path.join(root,'web',name),'utf8'),ctx);
const cards=walk(ids.get('versions')).filter(e=>e.className==='card');assert.equal(cards.length,2,'one schema card per contour');
for(const card of cards){assert.equal(walk(card).filter(e=>e.className==='edition-source').length,3);assert.ok(card.textContent.includes('5.5'));assert.equal(walk(card).filter(e=>e.download!==undefined).length,3,'all original DOCX downloads are available')}
const m=vm.runInContext('messages.get("14")',ctx);assert.equal(m.revisions.length,2);assert.equal(ctx.comparisonPool(m).length,2);
assert.equal(ctx.comparisonRevision(ctx.comparisonPool(m),alias.revision_id).revision_id,alias.revision_id,'legacy comparison retains workbook identity');
location.hash='#message=14&env=prod&rev='+alias.revision_id;ctx.renderRoute();assert.ok(ids.get('frame').src.includes(alias.revision_id),'legacy tree URL loads original archived tree');assert.equal(ids.get('revision-switch').value,p.revision_id);assert.equal(walk(ids.get('viewer-sources')).filter(e=>e.className==='edition-source').length,3);
location.hash='#message=14&env=test&rev='+testAlias.revision_id;ctx.renderRoute();assert.equal(new URLSearchParams(ids.get('compare-from-viewer').href.slice(1)).get('right'),p.revision_id,'legacy test edition must compare with a distinct schema group');
ctx.renderUpdates(new URLSearchParams({updates:'1',q:'5.5'}));assert.equal(walk(ids.get('updates')).filter(e=>e.className==='update-card').length,2,'journal search includes versions of every source');
ctx.loadComparisonData=async()=>[];
(async()=>{location.hash='#message=14&compare=1&left='+alias.revision_id+'&right='+t.revision_id;await ctx.renderComparison(m,new URLSearchParams(location.hash.slice(1)));assert.equal(mappingIdentity,alias.revision_id);assert.equal(walk(ids.get('comparison')).filter(e=>e.className==='edition-source').length,6);console.log('Grouped editions: cards, all DOCX sources, selectors, legacy tree/comparison IDs and journal source-version search passed.')})().catch(e=>{console.error(e);process.exitCode=1});
