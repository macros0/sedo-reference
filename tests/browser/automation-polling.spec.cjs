const {test,expect}=require('@playwright/test');

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
