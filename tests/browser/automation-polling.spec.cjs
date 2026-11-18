const {test,expect}=require('@playwright/test');
const path=require('node:path');

test('status polls every 60 seconds and pauses for hidden documents',async({page})=>{
 const initial=new Date('2026-11-14T12:00:00Z');
 await page.clock.install({time:initial});await page.clock.pauseAt(new Date(initial.getTime()+1000));
 await page.addInitScript(()=>{
  let state='visible';
  Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>state});
  window.changeVisibilityForTest=value=>{state=value;document.dispatchEvent(new Event('visibilitychange'))};
 });
 let requests=0,offline=false;
 await page.route('https://raw.githubusercontent.com/macros0/sedo-reference/automation-status/status.json*',async route=>{
  requests++;
  if(offline)return route.fulfill({status:503,body:'unavailable',headers:{'access-control-allow-origin':'*'}});
  return route.fulfill({json:{format:1,generated_at:initial.toISOString(),sourcecraft:{state:'success',finished_at:initial.toISOString(),contours:{prod:{observed_at:initial.toISOString()},test:{observed_at:initial.toISOString()}}},github:{check:{state:'success',finished_at:initial.toISOString()},versions:{count:0},publication:{state:'success'}}},headers:{'access-control-allow-origin':'*'}});
 });
 await page.goto('/site/');
 await expect.poll(()=>requests).toBe(1);
 await page.locator('#automation-summary').click();
 const button=page.locator('#automation-refresh');
 await expect(button).toBeEnabled();
 await page.clock.runFor(59999);expect(requests).toBe(1);
 await page.clock.runFor(1);await expect.poll(()=>requests).toBe(2);await expect(button).toBeEnabled();
 await page.evaluate(()=>window.changeVisibilityForTest('hidden'));
 await page.clock.runFor(180000);expect(requests).toBe(2);
 await page.evaluate(()=>window.changeVisibilityForTest('visible'));
 await expect.poll(()=>requests).toBe(3);await expect(button).toBeEnabled();
 await button.click();await expect.poll(()=>requests).toBe(4);await expect(button).toBeEnabled();
 offline=true;await page.clock.runFor(60000);await expect.poll(()=>requests).toBe(5);
 await expect(page.locator('#automation-summary')).toContainText('временно недоступен');await expect(button).toBeEnabled();
 offline=false;await page.clock.runFor(60000);await expect.poll(()=>requests).toBe(6);
 await expect(page.locator('#automation-summary')).toContainText('успешно');
});

test('Pages card keeps the linked check result while showing the latest actual deployment',async({page})=>{
 const runURL=id=>'https://github.com/macros0/sedo-reference/actions/runs/'+id;
 const linked={state:'success',run_id:41,url:runURL(41),run_attempt:2,finished_at:'2026-11-17T15:31:00Z'};
 const latest={state:'success',run_id:42,url:runURL(42),run_attempt:1,finished_at:'2026-11-18T11:51:00Z'};
 const report={format:1,generated_at:new Date().toISOString(),sourcecraft:{},github:{check:{state:'success'},versions:{count:0},publication:linked,last_successful_publication:linked}};
 await page.route('**/automation-status.js*',route=>route.fulfill({path:path.join(__dirname,'../../web/automation-status.js'),contentType:'application/javascript'}));
 await page.route('https://raw.githubusercontent.com/macros0/sedo-reference/automation-status/status.json*',route=>route.fulfill({json:report,headers:{'access-control-allow-origin':'*'}}));
 await page.goto('/site/');
 await page.locator('#automation-summary').click();
 const card=page.locator('.automation-card').filter({hasText:'GitHub Pages'});
 const linkedControl=card.getByRole('link',{name:'Открыть публикацию',exact:true});
 const latestControl=card.getByRole('link',{name:'Открыть последнюю успешную выкладку',exact:true});
 await expect(linkedControl).toHaveAttribute('href',linked.url+'/attempts/2');
 await expect(card.getByRole('link')).toHaveCount(1);
 report.github.last_successful_publication=latest;
 await page.locator('#automation-refresh').click();
 await expect(linkedControl).toHaveAttribute('href',linked.url+'/attempts/2');
 await expect(card).toContainText('17.11.2026, 18:31 МСК');
 await expect(card).toContainText('Последняя успешная выкладка: 18.11.2026, 14:51 МСК',{timeout:2000});
 await expect(latestControl).toHaveAttribute('href',latest.url+'/attempts/1');
});
