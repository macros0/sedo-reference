const {test,expect}=require('@playwright/test');
const {catalog,rowFieldLinks}=require('./catalog-fixtures.cjs');
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
 const historicalId='320eee70cfc63f4310acb14bc816cf54f16ac03edaa405592200cdcc291dd7d5';
 const historical=catalog.revisions.find(r=>r.revision_id===historicalId);
 const clean={...historical,revision_id:'a'.repeat(64),document_version:'4.0',label_date:'31.12.9999',schema_review:null,review_sources:[]};
 clean.display_revision_id=clean.revision_id;
 clean.edition_sources=[{...historical.edition_sources[0],revision_id:clean.revision_id,document_version:clean.document_version,label_date:clean.label_date}];
 await page.route('**/catalog-data.js*',route=>route.fulfill({contentType:'application/javascript',body:'window.SEDO_CATALOG='+JSON.stringify({...catalog,revisions:[...catalog.revisions,clean]})+';'}));
 await page.goto('/site/#message=1010');
 await page.reload();
 await expect(page.locator('.version-panel').first().locator('.card').first().getByRole('button',{name:/Показать замечания/})).toHaveCount(0);
 const prod=page.locator('.version-panel .card').filter({has:page.locator('a.open[href="#message=1010&env=prod&rev='+historicalId+'"]')});
 await prod.getByRole('button',{name:/Показать замечания/}).click();
 await prod.getByRole('link',{name:/Открыть поле XSD.*rowNumber/}).click();
 await expect(card.getByRole('heading',{name:'rowNumber',exact:true})).toBeVisible();
});

test('Back and Forward restore the root and its first selected child',async({page})=>{
 const id='3065ff2469613f57e21eaea824cfd5cb7ee170d1984b8a2f898128da4bdb8109';
 for(const embedded of [false,true]){
  await page.goto(embedded?'/site/#message=10110&env=prod&rev='+id:'/site/editions/'+id+'.html');
  const viewer=embedded?page.frameLocator('#frame'):page;
  const card=viewer.getByRole('region',{name:'Карточка поля'});
  await expect(card.getByRole('heading',{name:'benefitResponse',exact:true})).toBeVisible();
  await viewer.locator('#tree').getByRole('button',{name:'processNum',exact:true}).click();
  await expect(card.getByRole('heading',{name:'processNum',exact:true})).toBeVisible();
  await expect(page).toHaveURL(/field=/);
  await page.goBack();
  await expect(card.getByRole('heading',{name:'benefitResponse',exact:true})).toBeVisible();
  await expect(page).not.toHaveURL(/field=/);
  await page.goForward();
  await expect(card.getByRole('heading',{name:'processNum',exact:true})).toBeVisible();
  await expect(page).toHaveURL(/field=/);
 }
});
