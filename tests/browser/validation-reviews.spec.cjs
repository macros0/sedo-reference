const {test,expect}=require('@playwright/test');
const fs=require('node:fs'),path=require('node:path');
const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../../site/catalog-data.js'),'utf8').replace(/^window.SEDO_CATALOG=/,'').replace(/;\s*$/,''));
test('valid and invalid XML expose specification reviews locally in both contours',async({page,context})=>{
 for(const env of ['prod','test']){
  const row=catalog.revisions.find(r=>r.number==='10510'&&r.environment===env);
  await page.goto('/site/#'+new URLSearchParams({message:row.number,env,rev:row.revision_id,validate:'1'}));
  await expect(page.getByRole('region',{name:'Локальная проверка XML',exact:true}).getByRole('status')).toContainText('Готово.');
  const validator=page.frameLocator('iframe[title="Приватный валидатор XML: файл и результаты остаются в браузере"]');
  await validator.getByText('Или вставьте XML текстом',{exact:true}).click();
  const dictionary=page.frameLocator('#frame'),onlyReviews=dictionary.getByRole('checkbox',{name:'Только с замечаниями'});
  await onlyReviews.check();
  const requests=[],record=request=>{
   if(!/^https?:/.test(request.url()))return;
   let frame;try{frame=request.frame()}catch{}
   if(!frame||frame.url()==='about:srcdoc'||(request.url()+(request.postData()||'')).includes('PRIVATE_MARKER'))requests.push(request.url());
  };context.on('request',record);
  const xml='<'+row.root+' xmlns="'+row.namespace+'" specVersion="'+row.document_version+'" responseOn="00000000-0000-0000-0000-000000000000" messageDateTime="2026-11-13T12:00:00Z"><processNum>PRIVATE_MARKER</processNum><status>RECEIVED</status></'+row.root+'>';
  const input=validator.getByRole('textbox',{name:'XML для локальной проверки',exact:true});
  await input.fill(xml);await validator.getByRole('button',{name:'Проверить XML',exact:true}).click();
  await expect(validator.getByRole('status')).toHaveText('XML соответствует XSD выбранного сообщения и редакции.');
  const reviews=validator.getByRole('region',{name:'Замечания к спецификации в XML'});
  await expect(reviews).toBeVisible();await expect(validator.locator('.xml-review-tag')).toHaveCount(2);
  await validator.locator('.xml-review-tag').first().click();
  await expect(reviews.locator('details')).toHaveJSProperty('open',true);
  await expect(reviews).toContainText('Путь к XSD');
  const expected=row.review_sources[0].findings[0].spec;
  await expect(reviews).toContainText(expected);
  await input.fill(xml.replace('RECEIVED','INVALID'));
  await expect(reviews).toBeHidden();
  await validator.getByRole('button',{name:'Проверить XML',exact:true}).click();
  await expect(validator.getByRole('status')).toHaveText('XML не соответствует выбранной схеме.');
  await expect(reviews).toBeVisible();
  await validator.locator('#errors').getByRole('button',{name:'Посмотреть ограничения',exact:true}).click();
  await expect(dictionary.getByRole('heading',{name:'status',exact:true})).toBeVisible();
  await expect(onlyReviews).not.toBeChecked();
  await input.fill(xml.replace('<status>RECEIVED</status>',''));
  await validator.getByRole('button',{name:'Проверить XML',exact:true}).click();
  await expect(validator.getByRole('status')).toHaveText('XML не соответствует выбранной схеме.');
  const errorNotes=validator.locator('#errors .spec-review-details');
  await expect(errorNotes).toHaveCount(1);
  await expect(validator.locator('#errors > .has-spec-review')).toHaveCount(1);
  await errorNotes.locator('summary').click();
  await expect(errorNotes).toContainText('Путь к XSD');
  await validator.getByRole('button',{name:'Очистить файл и результат',exact:true}).click();
  await expect(reviews).toBeHidden();await expect(validator.locator('.xml-review-tag')).toHaveCount(0);
  context.off('request',record);expect(requests).toEqual([]);
 }
});
