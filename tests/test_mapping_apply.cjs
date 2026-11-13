"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),core=require('../web/compare-core.js');
const root=path.resolve(__dirname,'..'),catalog=JSON.parse(fs.readFileSync(path.join(root,'data/catalog.json'),'utf8'));
const rev=n=>({...catalog.revisions.find(r=>r.number===n&&r.environment==='prod'&&r.status==='matched'),ready:true}),left=rev('105'),right=rev('10510'),fields=r=>JSON.parse(fs.readFileSync(path.join(root,'data/fields/'+r.revision_id+'.json'),'utf8')).fields,before=fields(left),after=fields(right);
const all=before.map(f=>{const suffix=f.path.replace(/^approveSocialAssistResult\/?/,'');return{beforePath:f.path,afterPath:'benefitResponseResult'+(suffix?'/'+(suffix==='socialAssistNum'?'processNum':suffix==='registryNum'?'batchNo':suffix):''),comment:''}});
let exported;let saved=all.filter(p=>!p.beforePath.endsWith('/socialAssistNum')),onChange,scrolls=0;
class Element{
 constructor(tag,cls='',text=''){Object.assign(this,{tagName:tag,className:cls,textContent:text,children:[],parent:null,value:'',hidden:false})}
 append(...items){for(const item of items){item.parent=this;this.children.push(item)}}
 replaceChildren(...items){this.children.forEach(c=>c.parent=null);this.children=[];this.append(...items)}
 remove(){if(this.parent){this.parent.children=this.parent.children.filter(c=>c!==this);this.parent=null}}
 add(item){this.append(item)}setAttribute(){}scrollIntoView(){scrolls++}
 querySelector(selector){return walk(this).find(e=>selector.startsWith('.')?e.className.split(' ').includes(selector.slice(1)):e.tagName===selector)||null}
 get isConnected(){return this===section||!!this.parent?.isConnected}
}
const walk=e=>[e,...e.children.flatMap(walk)],section=new Element('section'),node=(tag,cls='',text='')=>new Element(tag,cls,text),messages=new Map([['105',{number:'105',revisions:[left]}],['10510',{number:'10510',revisions:[right]}]]),hash='#'+new URLSearchParams({message:'10510',compare:'1',left:left.revision_id,right:right.revision_id});
const context=vm.createContext({window:{},document:{createElement:tag=>node(tag),createTextNode:text=>node('text','',text)},location:{hash,href:'https://example.test/'+hash},URL,URLSearchParams,Map,Set,Option:class extends Element{constructor(text,value){super('option','',text);this.value=value}},catalog:{message_transitions:{pairs:[{previous:'105',current:'10510'}]}},messages,$:()=>section,node,title:()=>'',label:r=>r.document_version,date:v=>v||'',normalize:v=>String(v||'').toLowerCase(),route:()=>'',anchor:text=>node('a','',text),documentDownload:()=>node('a'),revisionOrigin:()=>node('a'),appendEditionSources:()=>{},SedoCompare:core,SedoMapping:{store:{load:()=>({pairs:saved,notice:''})},mount:(_section,_opts,_saved,callback)=>{onChange=callback}},SedoComparisonExport:{download:async options=>{exported=options}}});
vm.runInContext(fs.readFileSync(path.join(root,'web/compare.js'),'utf8'),context);context.loadComparisonData=async id=>id===left.revision_id?before:after;
const params=new URLSearchParams(hash.slice(1)),m=messages.get('10510'),count=cls=>section.querySelector('.'+cls)?.textContent;
(async()=>{
 await context.renderComparison(m,params);assert.equal(count('removed'),'Удалено · 1');
 saved=all;const result=await onChange();assert.equal(count('removed'),'Удалено · 0');assert.equal(count('added'),'Добавлено · 3');assert.equal(result,true,'apply callback must await a confirmed refreshed comparison');
 await section.querySelector('.comparison-export').children[0].onclick();assert.equal(exported.mappings.length,16,'export must use refreshed saved pairs');
 assert.ok(walk(section).some(e=>e.className==='mapping-applied-feedback'&&e.textContent.includes('16')));assert.ok(scrolls>0,'new counts should become visible after apply');
 context.SedoMapping.mount=()=>{throw Error('fixture render failure')};const failed=await context.renderComparison(m,params,true);assert.equal(failed,false);assert.ok(walk(section).some(e=>e.textContent.includes('fixture render failure')),'failure after removing loading state must remain visible');
 console.log('Apply refresh: message=10510, 15→16 mappings, 1→0 removals, awaited update, visible feedback and renderer errors passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
