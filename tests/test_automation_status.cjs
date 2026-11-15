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

// Home mode records GitHub's processing time, independent of SourceCraft's heartbeat.
const home=structuredClone(data);
home.collector_source='home';
home.github.check.url='https://github.com/macros0/sedo-reference/actions/runs/42';
home.sourcecraft.state='failure';
home.sourcecraft.error='inactive heartbeat unavailable';
let homeView=model(home,now);
assert.equal(homeView.tone,'success');
assert.match(homeView.cards[0].title,/Домашний сборщик/);
assert.equal(homeView.cards[0].url,home.github.check.url);
assert.match(homeView.cards[0].lines.join(' '),/GitHub.*11:00 МСК/);
assert.doesNotMatch(homeView.cards[0].lines.join(' '),/SourceCraft|inactive|07:00/);
home.github.check.state='processing';
home.github.versions={count:null,checked_at:null};
homeView=model(home,now);
assert.equal(homeView.cards[0].state,'processing');
assert.equal(homeView.tone,'processing');

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
 const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
 let time=0,timerId=0;const timers=new Map(),events=new Map();
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 async function advance(milliseconds){
  const target=time+milliseconds;
  for(;;){
   const next=[...timers].filter(([,t])=>t.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];
   if(!next)break;
   time=next[1].at;timers.delete(next[0]);next[1].callback();await flush();
  }
  time=target;await flush();
 }
 const context={module:{exports:{}},AbortController,
  setTimeout:(callback,delay)=>{timers.set(++timerId,{callback,at:time+delay});return timerId},
  clearTimeout:id=>timers.delete(id)};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/automation-status.js'),'utf8'),context);
 const {mount}=context.module.exports;
 class Element{constructor(){this.dataset={};this.children=[];this.textContent=''}append(...items){this.children.push(...items)}replaceChildren(...items){this.children=[...items]}}
 const nodes=Object.fromEntries(['automation-status','automation-summary','automation-cards','automation-note','automation-refresh'].map(id=>[id,new Element()]));
 const doc={visibilityState:'visible',getElementById:id=>nodes[id],createElement:()=>new Element(),addEventListener:(type,handler)=>events.set(type,handler)};
 const visibility=state=>{doc.visibilityState=state;events.get('visibilitychange')()};
 let fail=false,calls=0,pending;
 const current=structuredClone(data);current.generated_at=new Date().toISOString();current.sourcecraft.finished_at=current.generated_at;
 for(const value of Object.values(current.sourcecraft.contours))value.observed_at=current.generated_at;
 const app=mount(doc,async(url,options)=>{calls++;assert.equal(options.credentials,'omit');assert.match(url,/raw.githubuser/);if(fail)throw new Error('offline');if(pending)await pending;return {ok:true,json:async()=>current}});
 await flush();
 assert.equal(nodes['automation-status'].dataset.tone,'success');assert.equal(nodes['automation-cards'].children.length,3);
 assert.equal(calls,1);
 await advance(59999);assert.equal(calls,1);
 await advance(1);assert.equal(calls,2,'visible tab refreshes at 60 seconds');
 visibility('hidden');await advance(180000);assert.equal(calls,2,'hidden tab stops polling');
 await app.refresh();assert.equal(calls,2,'hidden tab does not start requests');
 visibility('visible');await flush();assert.equal(calls,3,'returning tab refreshes immediately');
 await nodes['automation-refresh'].onclick();assert.equal(calls,4,'manual refresh remains available');
 await advance(60000);assert.equal(calls,5,'manual refresh leaves one polling timer');
 fail=true;await app.refresh();assert.equal(nodes['automation-status'].dataset.tone,'unknown');assert.match(nodes['automation-summary'].textContent,/недоступен/);assert.equal(nodes['automation-refresh'].disabled,false);
 fail=false;await advance(60000);assert.equal(nodes['automation-status'].dataset.tone,'success','polling recovers after network failure');
 let release;pending=new Promise(resolve=>{release=resolve});
 const before=calls,inFlight=app.refresh();
 visibility('hidden');visibility('visible');await app.refresh();assert.equal(calls,before+1,'visibility changes and manual clicks do not overlap requests');
 pending=undefined;release();await inFlight;
 await advance(60000);assert.equal(calls,before+2,'in-flight completion leaves one timer');
 visibility('hidden');await advance(180000);assert.equal(calls,before+2);
 const hiddenEvents=new Map(),hiddenDoc={...doc,visibilityState:'hidden',addEventListener:(type,handler)=>hiddenEvents.set(type,handler)};calls=0;
 mount(hiddenDoc,async()=>{calls++;return {ok:true,json:async()=>current}});
 await flush();await advance(120000);assert.equal(calls,0,'initially hidden tab waits');
 hiddenDoc.visibilityState='visible';hiddenEvents.get('visibilitychange')();await flush();assert.equal(calls,1);
 hiddenDoc.visibilityState='hidden';hiddenEvents.get('visibilitychange')();
 console.log('Automation status rendering, 60-second polling, visibility pause, recovery and request concurrency passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
