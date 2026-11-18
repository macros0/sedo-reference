const assert=require('node:assert/strict');
const journal=require('../web/review-journal.js');
const ExcelJS=require('../web/vendor/exceljs.min.js');
const rootURL='https://macros0.github.io/sedo-reference/index.html#remarks=1';
const revision={number:'1010',environment:'test',revision_id:'exact-edition',document_version:'3.0',document_url:'https://lk-test.fss.ru/spec.docx',schema_path:'notice.xsd'};
const finding={kind:'Объявление типа',location:'Таблица 31',xlocation:'notice.xsd',spec:'Тип из DOCX',xsd:'Тип из XSD',impact:'Разная длина',field_paths:['notice/one/rowNumber','notice/two/@responseOn'],field_link_scope:'field'};
(async()=>{
 const rows=[{revision,finding},{revision:{...revision,environment:'prod',revision_id:'prod-edition'},finding:{...finding,field_paths:['notice'],field_link_scope:'parent'}},{revision,finding:{...finding,field_paths:[]}}];
 const workbook=journal.createWorkbook(ExcelJS,rows,{baseURL:rootURL});
 const loaded=new ExcelJS.Workbook();await loaded.xlsx.load(await workbook.xlsx.writeBuffer());
 const sheet=loaded.getWorksheet('Замечания');
 const headers=sheet.getRow(1).values;
 const first=headers.indexOf('Поле XSD 1'),second=headers.indexOf('Поле XSD 2');
 assert.ok(first>0&&second===first+1);
 for(const [column,field] of [[first,finding.field_paths[0]],[second,finding.field_paths[1]]]){
  const cell=sheet.getCell(2,column).value;
  const url=new URL(cell.hyperlink),params=new URLSearchParams(url.hash.slice(1));
  assert.equal(url.origin,'https://macros0.github.io');
  assert.equal(url.pathname,'/sedo-reference/index.html');
  assert.equal(params.get('message'),'1010');assert.equal(params.get('env'),'test');
  assert.equal(params.get('rev'),'exact-edition');assert.equal(params.get('field'),field);
  assert.ok(cell.text.includes(field));
 }
 const parent=sheet.getCell(3,first).value;
 assert.match(parent.text,/родительский блок/);assert.match(parent.hyperlink,/env=prod/);
 assert.match(parent.hyperlink,/rev=prod-edition/);
 assert.equal(sheet.getCell(4,first).value,'Поле не определено');
 assert.ok(!sheet.getCell(4,second).value);
 assert.equal(sheet.autoFilter,'A1:'+sheet.getColumn(sheet.columnCount).letter+sheet.rowCount);
 const single=journal.createWorkbook(ExcelJS,[rows[1]],{baseURL:rootURL});
 assert.equal(single.getWorksheet('Замечания').rowCount,2);
 assert.ok(single.getWorksheet('Замечания').getRow(1).values.includes('Открыть поле XSD'));
 console.log('Excel field hyperlinks preserve all paths, exact edition, contour and parent scope after XLSX roundtrip.');
})().catch(error=>{console.error(error);process.exitCode=1});
