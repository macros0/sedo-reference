const {test,expect}=require('@playwright/test');
const fs=require('node:fs');
const ExcelJS=require('../../web/vendor/exceljs.min.js');
const {journalRows,rowFieldLinks}=require('./catalog-fixtures.cjs');
test('downloaded findings open their exact XSD field',async({page})=>{
 for(const env of ['prod','test']){
  await page.goto('/site/#remarks=1&number=1010&env='+env);
  const pending=page.waitForEvent('download');await page.locator('#review-export').click();
  const download=await pending;
  const workbook=new ExcelJS.Workbook();await workbook.xlsx.load(fs.readFileSync(await download.path()));
  const sheet=workbook.getWorksheet('Замечания');
  expect(sheet.rowCount).toBe(journalRows('1010',env).length+1);
  const columns=sheet.getRow(1).values.flatMap((value,index)=>/^Открыть поле XSD$|^Поле XSD \d+$/.test(value)?[index]:[]);
  expect(columns.length).toBeGreaterThan(0);
  const field='notice/benefit/benefitInfo/rowNumber',expected=rowFieldLinks('1010',env,field);
  const links=[];sheet.eachRow((row,n)=>{if(n>1)for(const column of columns){const cell=row.getCell(column).value;if(cell?.hyperlink&&new URLSearchParams(new URL(cell.hyperlink).hash.slice(1)).get('field')===field)links.push(cell.hyperlink)}});
  expect(links.map(href=>new URL(href).hash)).toEqual(expected.map(link=>link.href));expect(links.length).toBeGreaterThan(0);
  for(const [index,href] of links.entries()){
   const params=new URLSearchParams(new URL(href).hash.slice(1));
   expect(params.get('env')).toBe(env);expect(params.get('rev')).toBe(expected[index].revision.revision_id);expect(params.get('field')).toBe(field);
   await page.goto(href);
   const detail=page.frameLocator('#frame').getByRole('region',{name:'Карточка поля'});
   await expect(detail.getByRole('heading',{name:'rowNumber',exact:true})).toBeVisible();await expect(detail.locator('code').first()).toHaveText(field);
  }
 }
});
