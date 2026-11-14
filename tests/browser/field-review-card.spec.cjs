const {test,expect}=require('@playwright/test');
const fs=require('node:fs'),path=require('node:path');
const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../../site/catalog-data.js'),'utf8').replace(/^window.SEDO_CATALOG=/,'').replace(/;\s*$/,''));
test('field cards show only linked findings from their own schema and contour',async({page})=>{
 for(const env of ['prod','test']){
  const revision=catalog.revisions.find(r=>r.number==='1010'&&r.environment===env);
  const field='notice/benefit/benefitInfo/rowNumber';
  await page.goto('/site/#'+new URLSearchParams({message:'1010',env,rev:revision.revision_id,field}));
  const detail=page.frameLocator('#frame').getByRole('region',{name:'Карточка поля'});
  const reviews=detail.getByRole('region',{name:'Замечания к полю'});
  await expect(reviews).toBeVisible();
  await expect(reviews).toContainText('Объявление типа');
  await expect(reviews).toContainText('minLength=1');
  const sources=await reviews.locator('a').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('href')).filter(h=>h.startsWith('https://')));
  expect(sources.length).toBeGreaterThan(0);
  expect(sources.every(u=>new URL(u).hostname===(env==='prod'?'lk.sfr.gov.ru':'lk-test.fss.ru'))).toBe(true);
  await page.reload();await expect(reviews).toBeVisible();
  // A clean sibling must not retain the previous field's notice.
  const viewer=page.frameLocator('#frame');
  await viewer.getByRole('searchbox',{name:'Поиск по тегу или описанию'}).fill('noticeNumber');
  await viewer.locator('#tree .result').filter({hasText:'notice/noticeNumber'}).first().click();
  await expect(detail.getByRole('heading',{name:'noticeNumber',exact:true})).toBeVisible();
  await expect(detail.getByRole('region',{name:'Замечания к полю'})).toHaveCount(0);
 }
});
