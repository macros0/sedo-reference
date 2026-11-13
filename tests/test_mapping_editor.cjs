"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
class Element{constructor(tag){Object.assign(this,{tagName:tag,children:[],dataset:{},disabled:false,checked:false,textContent:'',className:'',value:'',hidden:false})}append(...items){this.children.push(...items)}replaceChildren(...items){this.children=[...items]}setAttribute(){}}
const storage=new Map(),ctx=vm.createContext({document:{createElement:tag=>new Element(tag)},localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}});
for(const name of ['mapping-draft.js','mapping-editor.js','comparison-mapping.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../web',name),'utf8'),ctx);
const api=ctx.SedoMapping,host=new Element('host'),walk=e=>[e,...e.children.flatMap(walk)],find=cls=>walk(host).filter(e=>e.className.split(' ').includes(cls));
const opts={left:{revision_id:'left'},right:{revision_id:'right'},before:[{path:'old/a',doc:'Исходное поле'}],after:[{path:'new/a',doc:'Новое поле'},{path:'new/b',doc:'Другое поле'}],isCurrent:()=>true};let changed=0;
const mount=saved=>{host.replaceChildren();api.mount(host,opts,{pairs:saved,notice:''},async()=>{await Promise.resolve();changed++;return true})};
const choose=(side,value)=>{const select=find('mapping-tag-choice-'+side)[0];select.value=value;select.onchange()};
(async()=>{
 mount([]);find('mapping-add')[0].onclick();assert.equal(find('mapping-apply')[0].disabled,true,'unfinished editor blocks applying');
 choose('before','old/a');choose('after','new/a');find('mapping-edit-comment')[0].value='Первый комментарий';find('mapping-editor-save')[0].onclick();
 assert.equal(find('mapping-proposal').length,1);assert.equal(find('mapping-draft-notice')[0].hidden,false);assert.equal(api.store.load(opts).pairs.length,0,'draft never writes storage');
 find('mapping-edit-row')[0].onclick();choose('after','new/b');find('mapping-edit-comment')[0].value='Изменено в браузере';find('mapping-editor-save')[0].onclick();
 assert.equal(find('mapping-proposal')[0].dataset.afterPath,'new/b');
 find('mapping-add-row')[0].onclick();choose('before','old/a');choose('after','new/b');find('mapping-editor-save')[0].onclick();assert.match(find('mapping-editor-error')[0].textContent,/уже/);assert.equal(find('mapping-apply')[0].disabled,true);
 find('mapping-editor-cancel')[0].onclick();await find('mapping-apply')[0].onclick();assert.equal(changed,1);assert.equal(find('mapping-draft-notice')[0].hidden,true);
 const saved=api.store.load(opts).pairs;assert.equal(saved.length,1);assert.equal(saved[0].afterPath,'new/b');assert.equal(saved[0].comment,'Изменено в браузере');
 mount(saved);find('mapping-suggest')[0].onclick();assert.equal(find('mapping-explicit')[0].disabled,false,'confirmed pair can be excluded');find('mapping-remove-row')[0].onclick();assert.equal(find('mapping-apply')[0].disabled,false,'empty draft can replace a saved set');await find('mapping-apply')[0].onclick();assert.equal(api.store.load(opts).pairs.length,0);
 api.store.save(opts,saved);mount(saved);find('mapping-suggest')[0].onclick();find('mapping-remove-row')[0].onclick();find('mapping-discard')[0].onclick();assert.equal(api.store.load(opts).pairs.length,1,'discard must preserve saved mappings');assert.equal(find('mapping-draft-notice')[0].hidden,true);
 mount(saved);find('mapping-suggest')[0].onclick();opts.isCurrent=()=>false;changed=0;await find('mapping-apply')[0].onclick();assert.equal(changed,0);assert.match(find('mapping-draft-errors')[0].textContent,/редакций/);
 console.log('Mapping editor UI: add/edit/comment, duplicate validation, unfinished form, unapplied banner, awaited save, remove-all, discard and stale guards passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
