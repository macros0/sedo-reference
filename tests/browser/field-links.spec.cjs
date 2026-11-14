const {test,expect}=require('@playwright/test');
test('notices open exact elements and attributes of their own contour',async({page})=>{
 for(const env of ['prod','test']){
  await page.goto('/site/#remarks=1&number=1010&env='+env);
  const row=page.locator('.review-results tbody tr').filter({hasText:'rowNumber'});
  const link=row.getByRole('link',{name:/Открыть поле XSD.*rowNumber/});
  await expect(link).toHaveCount(1);const href=await link.getAttribute('href');
  expect(new URLSearchParams(href.slice(1)).get('env')).toBe(env);
  await link.click();
  const card=page.frameLocator('#frame').getByRole('region',{name:'Карточка поля'});
  await expect(card.getByRole('heading',{name:'rowNumber',exact:true})).toBeVisible();
  await expect(card).toContainText('3');
  await page.reload();await expect(card.getByRole('heading',{name:'rowNumber',exact:true})).toBeVisible();
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
