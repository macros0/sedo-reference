const {test,expect}=require('@playwright/test');
const {rowFieldLinks}=require('./catalog-fixtures.cjs');
test('notices open exact elements and attributes of their own contour',async({page})=>{
 for(const env of ['prod','test']){
  await page.goto('/site/#remarks=1&number=1010&env='+env);
  const field='notice/benefit/benefitInfo/rowNumber',expected=rowFieldLinks('1010',env,field);
  const links=page.locator('.review-results').getByRole('link',{name:/Открыть поле XSD.*rowNumber/});
  await expect(links).toHaveCount(expected.length);expect(expected.length).toBeGreaterThan(0);
  const hrefs=await links.evaluateAll(nodes=>nodes.map(node=>node.getAttribute('href')));expect(hrefs).toEqual(expected.map(link=>link.href));
  for(const href of hrefs){
  expect(new URLSearchParams(href.slice(1)).get('env')).toBe(env);
  await page.goto('/site/'+href);
  const card=page.frameLocator('#frame').getByRole('region',{name:'Карточка поля'});
  await expect(card.getByRole('heading',{name:'rowNumber',exact:true})).toBeVisible();
  await expect(card.locator('code').first()).toHaveText(field);
  await page.reload();await expect(card.getByRole('heading',{name:'rowNumber',exact:true})).toBeVisible();
  }
 }
 await page.goto('/site/#remarks=1&number=12210&env=prod');
 await page.locator('.review-results').getByRole('link',{name:/Открыть поле XSD.*@responseOn/}).first().click();
 const card=page.frameLocator('#frame').getByRole('region',{name:'Карточка поля'});
 await expect(card.getByRole('heading',{name:'@responseOn',exact:true})).toBeVisible();
 await expect(card).toContainText('xs:string');
 // The same links also work from the per-message review panel.
 await page.goto('/site/#message=1010');
 const prod=page.locator('.version-panel').first().locator('.card').first();
 await prod.getByRole('button',{name:/Показать замечания/}).click();
 await prod.getByRole('link',{name:/Открыть поле XSD.*rowNumber/}).click();
 await expect(card.getByRole('heading',{name:'rowNumber',exact:true})).toBeVisible();
});
