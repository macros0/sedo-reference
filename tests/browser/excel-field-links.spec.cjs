const {test,expect}=require('@playwright/test');
const fs=require('node:fs');
const ExcelJS=require('../../web/vendor/exceljs.min.js');
test('downloaded findings open their exact XSD field',async({page})=>{
 for(const env of ['prod','test']){
  await page.goto('/site/#remarks=1&number=1010&env='+env);
  const pending=page.waitForEvent('download');await page.locator('#review-export').click();
  const download=await pending;
  const workbook=new ExcelJS.Workbook();await workbook.xlsx.load(fs.readFileSync(await download.path()));
  const sheet=workbook.getWorksheet('Замечания');
  const column=sheet.getRow(1).values.indexOf('Открыть поле XSD');
  expect(column).toBeGreaterThan(0);
  const links=[];sheet.eachRow((row,n)=>{if(n>1&&row.getCell(column).value?.text?.includes('rowNumber'))links.push(row.getCell(column).value.hyperlink)});
  expect(links).toHaveLength(1);
  const params=new URLSearchParams(new URL(links[0]).hash.slice(1));
  expect(params.get('env')).toBe(env);expect(params.get('field')).toBe('notice/benefit/benefitInfo/rowNumber');
  await page.goto(links[0]);
  const detail=page.frameLocator('#frame').getByRole('region',{name:'Карточка поля'});
  await expect(detail.getByRole('heading',{name:'rowNumber',exact:true})).toBeVisible();
  await expect(detail).toContainText('3');
 }
});
