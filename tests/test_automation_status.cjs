const assert=require('node:assert/strict');
const {model,date,safeLink}=require('../web/automation-status.js');
const now=Date.parse('2026-11-13T12:00:00Z');
const data={format:1,generated_at:'2026-11-13T11:00:00Z',sourcecraft:{state:'success',finished_at:'2026-11-13T07:02:00Z',contours:{prod:{observed_at:'2026-11-13T07:01:00Z',changed:false},test:{observed_at:'2026-11-13T07:02:00Z',changed:false}}},github:{check:{state:'success'},versions:{count:0,checked_at:'2026-11-13T08:00:00Z'},publication:{state:'success',finished_at:'2026-11-13T09:00:00Z'}}};
assert.equal(model(data,now).tone,'success');
assert.match(model(data,now).cards[0].lines[0],/10:01.*МСК/);
assert.match(model(data,now).summary,/новых редакций нет/);
let d=structuredClone(data);d.github.publication.state='failure';assert.equal(model(d,now).tone,'failure');assert.doesNotMatch(model(d,now).summary,/успешно/);
d=structuredClone(data);d.github.publication.state='waiting';d.github.versions.count=4;assert.equal(model(d,now).tone,'processing');assert.match(model(d,now).cards[1].lines[0],/4/);
d=structuredClone(data);d.github.check.state='processing';d.github.versions.count=null;assert.match(model(d,now).cards[1].lines[0],/выполняется/);
d=structuredClone(data);delete d.sourcecraft.contours.test.observed_at;assert.notEqual(model(d,now).tone,'success');
d=structuredClone(data);d.sourcecraft.state='failure';assert.equal(model(d,now).tone,'failure');
assert.equal(model(data,now+48*3600*1000).tone,'stale');
d=structuredClone(data);d.generated_at='bad';assert.equal(model(d,now).tone,'unknown');assert.doesNotThrow(()=>date('bad'));
assert.equal(safeLink('javascript:alert(1)'),null);assert.equal(safeLink('https://evil.test'),null);
assert.equal(model(null,now).tone,'unknown');

// The next scheduled run depends on the current time, never on a stale report
// or the visitor's time zone. 04:00/04:17 UTC are 07:00/07:17 Moscow.
let next=model(data,Date.parse('2026-11-13T03:59:59Z'));
assert.match(next.cards[0].lines.at(-1),/Следующая проверка по расписанию: 13\.11\.2026, 07:00 МСК/);
assert.match(next.cards[1].lines.at(-1),/Следующий забор по расписанию: 13\.11\.2026, 07:17 МСК/);
next=model(data,Date.parse('2026-11-13T04:00:00Z'));
assert.match(next.cards[0].lines.at(-1),/14\.11\.2026, 07:00 МСК/);
assert.match(next.cards[1].lines.at(-1),/13\.11\.2026, 07:17 МСК/);
next=model(data,Date.parse('2026-12-31T21:30:00Z'));
assert.match(next.cards[0].lines.at(-1),/01\.01\.2027, 07:00 МСК/);
assert.match(next.cards[1].lines.at(-1),/01\.01\.2027, 07:17 МСК/);
assert.match(next.updated,/Фактический запуск может задержаться/);
console.log('Automation status states and safe links passed.');

(async()=>{
 const {mount}=require('../web/automation-status.js');
 class Element{constructor(){this.dataset={};this.children=[];this.textContent=''}append(...items){this.children.push(...items)}replaceChildren(...items){this.children=[...items]}}
 const nodes=Object.fromEntries(['automation-status','automation-summary','automation-cards','automation-note','automation-refresh'].map(id=>[id,new Element()]));
 const doc={getElementById:id=>nodes[id],createElement:()=>new Element()};
 let fail=false;
 const current=structuredClone(data);current.generated_at=new Date().toISOString();current.sourcecraft.finished_at=current.generated_at;
 for(const value of Object.values(current.sourcecraft.contours))value.observed_at=current.generated_at;
 const app=mount(doc,async(url,options)=>{assert.equal(options.credentials,'omit');assert.match(url,/raw.githubuser/);if(fail)throw new Error('offline');return {ok:true,json:async()=>current}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(nodes['automation-status'].dataset.tone,'success');assert.equal(nodes['automation-cards'].children.length,3);
 fail=true;await app.refresh();assert.equal(nodes['automation-status'].dataset.tone,'unknown');assert.match(nodes['automation-summary'].textContent,/недоступен/);assert.equal(nodes['automation-refresh'].disabled,false);
 console.log('Automation status rendering and offline refresh passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
