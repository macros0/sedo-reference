const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const catalog = require('../data/catalog.json');
const walk = el => [el, ...el.children.flatMap(walk)];
function cardFor(r) {
  const ids = new Map();
  const element = () => ({
    textContent: '', children: [], append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute(name,value) { this[name]=String(value); }
  });
  const document = {
    createElement: element, createTextNode: text => ({textContent: text, children: []}),
    getElementById(id) { if (!ids.has(id)) ids.set(id, element()); return ids.get(id); }
  };
  const window = {SEDO_CATALOG: {revisions: [r], registry: []}, addEventListener() {}};
  const ctx = {window, document, URL, URLSearchParams, location: {hash: '#message='+r.number},
    updatesRoute: () => '', appendRelatedComparisons() {}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8'), ctx);
  return walk(ids.get('versions')).find(x => x.className === 'card');
}
for (const r of catalog.revisions.filter(r => r.number === '6' && r.schema_source_type === 'wsdl_bridge')) {
  const nodes = walk(cardFor(r));
  assert.ok(nodes.some(x => x.textContent.includes('Первоисточник XSD')), 'missing primary source');
  assert.ok(nodes.some(x => x.textContent.includes('SedoArm_ЕЦП.xsd')));
  assert.ok(nodes.some(x => x.href === r.schema_source_url));
  assert.ok(nodes.some(x => x.href === r.document_url && x.textContent.endsWith('.docx')));
  assert.ok(!nodes.some(x => x.textContent.includes(r.revision_id.slice(0,8))));
}
const wsdl = catalog.revisions.find(r => r.schema_source_type === 'wsdl');
const rootFile = wsdl.schema_path.split('/').pop();
const digest = wsdl.dependencies.find(([file]) => file === rootFile)[1];
const primary = wsdl.schema_sources.find(s => s.kind === 'xsd' && s.sha256 === digest);
assert.ok(walk(cardFor(wsdl)).some(x => x.href === primary.url && x.textContent.includes(rootFile)));
const archived = catalog.revisions.find(r => r.archive_url?.endsWith('.zip'));
assert.ok(walk(cardFor(archived)).some(x => x.href === archived.archive_url));
const manual = {...archived, source_type:'manual', archive_url:'manual:///my%20schemas.zip', document_url:null};
assert.ok(walk(cardFor(manual)).some(x => x.textContent.includes('my schemas.zip')));
assert.ok(!walk(cardFor(manual)).some(x => x.href?.startsWith('manual:')));
const finding={kind:'Кратность',location:'Таблица 3',spec:'0..1',xlocation:'schema.xsd · root/value',xsd:'1..1',impact:'Поле обязательно'};
const reviewed=cardFor({...archived,schema_review:{tables:2,examples:1,findings:[finding]}});
const button=walk(reviewed).find(x=>x.textContent.includes('Показать замечания'));
assert.ok(button,'open findings for the selected document');
button.onclick();
assert.ok(walk(reviewed).some(x=>x.textContent==='Таблица 3'));
assert.ok(walk(reviewed).some(x=>x.textContent==='1..1'));
assert.equal(button['aria-expanded'],'true');
button.onclick();
assert.equal(button['aria-expanded'],'false');
const clean=cardFor({...archived,schema_review:{tables:1,examples:0,findings:[]}});
assert.ok(walk(clean).some(x=>x.textContent.includes('расхождений не найдено')));
const uncovered=cardFor({...archived,schema_review:{tables:0,examples:0,findings:[]}});
assert.ok(!walk(uncovered).some(x=>x.textContent.includes('расхождений не найдено')));
console.log('Primary XSD sources verified');
