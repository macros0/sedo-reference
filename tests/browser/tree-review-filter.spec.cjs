const {test,expect}=require('@playwright/test');
const fs=require('node:fs'),path=require('node:path');
const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../../site/catalog-data.js'),'utf8').replace(/^window.SEDO_CATALOG=/,'').replace(/;\s*$/,''));
test('review badges and filter work with search, scope and reset in both contours',async({page})=>{
 for(const env of ['prod','test']){
  const revision=catalog.revisions.find(r=>r.number==='1010'&&r.environment===env);
  await page.goto('/site/#'+new URLSearchParams({message:'1010',env,rev:revision.revision_id,field:'notice/benefit/benefitInfo/rowNumber'}));
  const frame=page.frameLocator('#frame'),tree=frame.locator('#tree'),filter=frame.getByRole('checkbox',{name:'Только с замечаниями'});
  const reviews=frame.getByRole('region',{name:'Замечания к полю'});
  await expect(reviews).toBeVisible();
  const expected=await reviews.locator('.field-review').count();
  expect(expected).toBeGreaterThan(0);
  await filter.check();
  await expect(tree.getByRole('button',{name:'rowNumber',exact:true})).toBeVisible();
  await expect(tree.getByRole('button',{name:'noticeNumber',exact:true})).toHaveCount(0);
  await expect(tree.getByRole('button',{name:'Свернуть benefitInfo',exact:true})).toBeVisible();
  const query=frame.getByRole('searchbox',{name:'Поиск по тегу или описанию'});
  await query.fill('rowNumber');await expect(tree.locator('.result')).toHaveCount(1);
  await expect(tree.locator('.review-mark')).toHaveText('Замечания · '+expected);
  await tree.locator('.result').click();
  await expect(frame.getByRole('region',{name:'Замечания к полю'})).toBeVisible();
  await frame.getByRole('combobox',{name:'Раздел сообщения'}).selectOption('notice/senderInfo');
  await expect(tree.locator('.result')).toHaveCount(0);await expect(tree).toContainText('Совпадений нет');
  await query.fill('');
  await expect(tree.getByRole('button',{name:'fullName',exact:true})).toBeVisible();
  await expect(tree.getByRole('button',{name:'rowNumber',exact:true})).toHaveCount(0);
  await frame.getByRole('button',{name:'Сбросить',exact:true}).click();
  await expect(filter).not.toBeChecked();await expect(query).toHaveValue('');
  await expect(frame.getByRole('combobox',{name:'Раздел сообщения'})).toHaveValue('');
  await query.fill('noticeNumber');await expect(tree.locator('.result')).toHaveCount(1);
  await expect(tree.locator('.review-mark')).toHaveCount(0);
 }
});
