const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const catalog = require('../data/catalog.json');
const walk = el => [el, ...el.children.flatMap(walk)];
function cardFor(r, environment, rows=[r], registry=[]) {
  const ids = new Map();
  const element = () => ({
    textContent: '', children: [], dataset:{}, value:'', get childNodes() { return this.children; }, append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute(name,value) { this[name]=String(value); }
  });
  const document = {
    createElement: element, createTextNode: text => ({textContent: text, children: []}),
    getElementById(id) { if (!ids.has(id)) ids.set(id, element()); return ids.get(id); }
  };
  if(environment!==undefined)document.getElementById('catalog-env').value=environment;
  const window = {SEDO_CATALOG: {revisions: rows, registry}, addEventListener() {}};
  const ctx = vm.createContext({window, document, URL, URLSearchParams, location: {hash: environment===undefined?'#message='+r.number:''}, updatesRoute: () => ''});
  for(const script of ['compare.js','review-journal.js','app.js'])vm.runInContext(fs.readFileSync(path.join(__dirname, '../web',script), 'utf8'),ctx);
  return environment===undefined?(rows.length?walk(ids.get('versions')).find(x => x.className === 'card'):ids.get('versions')):ids.get('messages');
}
// The Python replay test supplies the actual published individual edition.
if (process.argv[2]) {
  const published = {};
  vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {window:published});
  const row = published.SEDO_CATALOG.revisions.find(r => r.revision_id === process.argv[3]);
  const origin = walk(cardFor(row)).find(n => n.className === 'meta schema-origin');
  const links = origin.children.filter(n => n.href);
  const common = ['https://lk-test.fss.ru/sedo.html',
    'https://ecp-test.sfr.gov.ru/sedo/fss/api/soap/SedoGatewaySimple?wsdl'];
  const base = 'https://ecp-test.sfr.gov.ru/sedo/fss/api/soap/';
  if (process.argv[4] === 'composition') {
    assert.deepEqual(links.map(n => n.href), [...common, `${base}root.xsd`,
      row.schema_declaration_download, row.schema_file_download]);
    assert.deepEqual(links.slice(-2).map(n => [n.textContent, n.download]), [
      ['schemas/002/root.xsd', 'root.xsd'], ['schemas/001/inline-0.xsd', 'inline-0.xsd']]);
  } else if (process.argv[4] === 'transitive') {
    assert.deepEqual(links.map(n => n.href), [...common, row.schema_docx_url, row.schema_docx_download,
      row.schema_declaration_url, row.schema_declaration_download, row.schema_file_download]);
    for (const label of ['Файл из DOCX', 'Файл объявления корня', 'Точка входа XSD']) {
      assert.ok(origin.children.some(n => n.textContent.includes(label)));
    }
    const journal = require('../web/review-journal.js'), ExcelJS = require('../web/vendor/exceljs.min.js');
    const actual = row.schema_files.find(file => file.path === row.schema_declaration_path);
    const baseURL = 'https://example.test/reference/';
    const workbook = journal.createWorkbook(ExcelJS, [{revision: row,
      finding: {kind: 'Кратность', location: 'Таблица', xsd_source: actual}}], {baseURL});
    const sheet = workbook.getWorksheet('Замечания');
    const cell = name => sheet.getCell(2, sheet.getRow(1).values.indexOf(name)).value;
    assert.deepEqual(cell('Ссылка на XSD в DOCX'), {text: 'root.xsd', hyperlink: row.schema_docx_url});
    assert.deepEqual(cell('Файл объявления корня'), {text: row.schema_declaration_path, hyperlink: baseURL + actual.download});
    assert.deepEqual(cell('Точка входа XSD'), {text: row.schema_path, hyperlink: baseURL + row.schema_file_download});
  } else {
    assert.deepEqual(links.map(n => n.href), [...common, `${base}SedoGatewaySimple?xsd=main.xsd`]);
  }
}
{
 const rows=[...catalog.revisions,...catalog.registry].filter(row=>row.number==='600');
 const content=node=>(node.textContent||'')+(node.children||[]).map(content).join(' ');
 for(const env of ['prod','test','']){
  const text=content(cardFor(rows[0],env,rows));
  const expected=[...new Set(rows.filter(row=>!env||row.environment===env).map(row=>row.module))];
  for(const module of expected)assert.ok(text.includes(module),'selected contour module is visible: '+module);
  for(const module of rows.map(row=>row.module).filter(module=>!expected.includes(module)))assert.ok(!text.includes(module),'other contour module must stay hidden: '+module);
 }
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
const linked={...archived,schema_publication_page:'https://lk.sfr.gov.ru/prab.html',
  schema_file_download:'sources/original.xsd',schema_file_name:'Notice.xsd'};
const chain=walk(cardFor(linked)).find(x=>x.className==='meta schema-origin');
const links=chain.children.filter(x=>x.tagName==='a'||x.href);
assert.deepEqual(links.map(x=>x.href),[linked.schema_publication_page,linked.archive_url,linked.schema_file_download]);
assert.equal(links[2].download,'Notice.xsd');
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

{
 const registry={number:'99999',environment:'prod',title:'Перечень',module:'Реестр',source:'https://example.test/types.docx',document_download:'documents/registry.docx',document_filename:'registry.docx'};
 const card=cardFor(registry,undefined,[],[registry]);
 assert.ok(walk(card).some(n=>n.href===registry.document_download&&n.download===registry.document_filename),'registry DOCX is downloadable independent of source_type');
}

{
 const localOnly={...archived,schema_download:'schemas/local.zip',schema_filename:'local.zip',validation_download:undefined,validation_error:'Скачайте XSD (ZIP) для локальной проверки.'};
 assert.ok(walk(cardFor(localOnly)).some(n=>n.textContent===localOnly.validation_error),'local-only ZIP explains missing browser validation');
}
