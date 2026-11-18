const {test,expect}=require('@playwright/test');
const path=require('node:path');
const ExcelJS=require('../../web/vendor/exceljs.min.js');
const {catalog}=require('./catalog-fixtures.cjs');

const revision=environment=>catalog.revisions.find(r=>r.number==='10510'&&r.environment===environment&&r.ready);
const left=revision('prod'),right=revision('test');
const url='/site/#'+new URLSearchParams({message:'10510',compare:'1',left:left.revision_id,right:right.revision_id});
const schemaNamespace='http://www.w3.org/2001/XMLSchema';
const atomic=base=>({base:'xs:'+base,baseNs:schemaNamespace});
const list=(name,pattern)=>({
 name,path:'message/'+name,kind:'element',parent:0,children:[],min:'1',max:'1',ns:'',
 listItem:{alternatives:[{...atomic('int'),values:[['7','Допустимый код']]},{...atomic('string'),facets:[['pattern',pattern]]}]}
});
const message={name:'message',path:'message',kind:'element',parent:-1,min:'1',max:'1',ns:''};
const before=[{...message,children:[1]},list('codes','UNIQUEALPHA')];
const after=[{...message,children:[1,2]},list('codes','UNIQUEBETA'),list('added','UNIQUEADDED')];

async function serveFields(context,revision,fields){
 const id=revision.revision_id,body='window.SEDO_FIELDS['+JSON.stringify(id)+']='+JSON.stringify(fields)+';';
 await context.route('**/fields/'+id+'.js*',route=>route.fulfill({contentType:'application/javascript',body}));
}

test.beforeEach(async({context})=>{
 await context.route('**/*',route=>new URL(route.request().url()).origin==='http://127.0.0.1:4173'?route.continue():route.abort());
 for(const script of ['compare.js','compare-core.js','comparison-export.js']){
  await context.route('**/'+script+'*',route=>route.fulfill({path:path.join(__dirname,'../../web',script),contentType:'application/javascript'}));
 }
 await serveFields(context,left,before);
 await serveFields(context,right,after);
});

test('legacy lists without resolved item metadata show the limits of comparison',async({page,context})=>{
 const legacy=[before[0],{...before[1],listItem:atomic('QName'),values:[['p:first p:second','']]}];
 await serveFields(context,left,legacy);
 await serveFields(context,right,legacy);
 await page.goto(url);
 await expect(page.locator('#comparison')).toContainText('Такие значения сопоставлены по исходному тексту.');
 await expect(page.locator('#comparison')).not.toContainText('Деревья совпадают.');
});

test('comparison displays and finds constraints within changed and added lists',async({page})=>{
 await page.goto(url);
 const changed=page.locator('.difference.changed');
 for(const [side,pattern] of [['before','UNIQUEALPHA'],['after','UNIQUEBETA']]){
  const cell=changed.locator('td.'+side);
  await expect(cell).toContainText(pattern);
  await expect(cell).toContainText('Допустимый код');
  await expect(cell).not.toContainText('[object Object]');
 }
 const search=page.getByRole('searchbox',{name:'Поиск по изменениям'});
 for(const [pattern,field] of [['UNIQUEALPHA','message/codes'],['UNIQUEBETA','message/codes'],['UNIQUEADDED','message/added']]){
  await search.fill(pattern);
  const cards=page.locator('.difference');
  await expect(cards).toHaveCount(1);
  await expect(cards.locator('.difference-head code')).toHaveText(field);
 }
 await expect(page.locator('.difference.added')).toContainText('UNIQUEADDED');
 await search.fill('Допустимый код');
 await expect(page.locator('.difference')).toHaveCount(2);
});

test('the Excel changes sheet preserves nested list constraints for both editions',async({page})=>{
 await page.goto(url);
 const [download]=await Promise.all([
  page.waitForEvent('download'),
  page.getByRole('button',{name:'Выгрузить Excel для сопоставления',exact:true}).click()
 ]);
 const workbook=new ExcelJS.Workbook(),stream=await download.createReadStream(),chunks=[];
 for await(const chunk of stream)chunks.push(chunk);
 await workbook.xlsx.load(Buffer.concat(chunks));
 const rows=workbook.getWorksheet('Изменения').getSheetValues();
 const change=rows.find(row=>row?.[2]==='message/codes'&&row[3]==='Тип значения в списке (list)');
 expect(change[4]).toContain('UNIQUEALPHA');
 expect(change[5]).toContain('UNIQUEBETA');
 const added=rows.find(row=>row?.[2]==='message/added');
 expect(added[5]).toContain('UNIQUEADDED');
});
