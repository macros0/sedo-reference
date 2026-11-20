(function(root){
 'use strict';
 const STALE=36*60*60*1000;
 const DAY=24*60*60*1000;
 const UTC_TIMESTAMP=/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
 const labels={success:'Успешно',failure:'Ошибка',processing:'Выполняется',waiting:'Ожидается',not_published:'Не опубликовано',superseded:'Замещено',cancelled:'Отменено',unknown:'Нет данных',stale:'Данные устарели'};
 function isoWeekStart(year){
  const fourth=new Date(year+'-01-04T00:00:00Z');
  return fourth.getTime()-((fourth.getUTCDay()+6)%7)*DAY;
 }
 function calendarDay(value){
  const compact=/^(\d{4})(\d{2})(\d{2})$/.exec(value);
  if(compact)return compact.slice(1).join('-');
  const week=/^(\d{4})(-?)W(\d{2})(?:\2([1-7]))?$/.exec(value);
  if(!week)return value;
  const [,year,,number,weekday='1']=week;
  if(year==='0000'||number==='00'||+number>53)return '';
  const start=isoWeekStart(year),monday=start+(+number-1)*7*DAY;
  if(+number===53&&new Date(monday+3*DAY).getUTCFullYear()!==+year)return '';
  return new Date(monday+(+weekday-1)*DAY).toISOString().split('T')[0];
 }
 function parseTimestamp(value){
  if(typeof value!=='string')return NaN;
  const parts=/^([\dW-]+)[Tt ](\d{2})(?:(?<clockSeparator>:?)(\d{2})(?:\k<clockSeparator>(\d{2}))?)?(?:[.,](\d+))?(?:Z|([+-])(\d{2})(?:(?<offsetSeparator>:?)(\d{2})(?:\k<offsetSeparator>(\d{2}))?)?(?:[.,](\d+))?)$/.exec(value);
  if(!parts||parts[0]!==value)return NaN;
  const [,day,hour,,minute='00',second='00',fraction,sign,hours=0,,minutes=0,seconds=0,offsetFraction]=parts;
  const calendar=calendarDay(day);
  if(!/^\d{4}-\d{2}-\d{2}$/.test(calendar)||calendar.startsWith('0000-'))return NaN;
  const local=calendar+'T'+hour+':'+minute+':'+second,time=Date.parse(local+'Z');
  if(!Number.isFinite(time)||new Date(time).toISOString().slice(0,19)!==local||hours>23||minutes>59||seconds>59)return NaN;
  const micros=digits=>Number((digits||'').slice(0,6).padEnd(6,'0'));
  const offset=(Number(hours)*3600+Number(minutes)*60+Number(seconds))*1000;
  const direction=sign==='-'?-1:1;
  // The producer's ISO parser treats a zero whole-second offset as UTC.
  const offsetMicros=offset?micros(offsetFraction):0;
  const utc=time-direction*offset+Math.floor((micros(fraction)-direction*offsetMicros)/1000);
  const year=new Date(utc).getUTCFullYear();
  return year>=1&&year<=9999?utc:NaN;
 }
 function timestamp(record,field){
  const normalized=field+'_utc';
  if(!Object.hasOwn(record,normalized))return parseTimestamp(record[field]);
  const value=record[normalized];
  return typeof value==='string'&&UTC_TIMESTAMP.test(value)?parseTimestamp(value):NaN;
 }
 function date(value){const n=typeof value==='number'?value:parseTimestamp(value);return Number.isFinite(n)?new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'}).format(n)+' МСК':'время неизвестно'}
 // Keep these UTC times aligned with SourceCraft .sourcecraft/ci.yaml and
 // .github/workflows/check-sfr.yml. Moscow is UTC+3; visitor time zones do not apply.
 const dailyRuns={sourcecraft:{hour:4,minute:0},github:{hour:4,minute:17}};
 function nextRun(kind,now){const {hour,minute}=dailyRuns[kind],next=new Date(now);next.setUTCHours(hour,minute,0,0);if(next.getTime()<=now)next.setUTCDate(next.getUTCDate()+1);return date(next.getTime())}
 function sourcecraftCard(sc,now){
  const contours=['prod','test'].map(env=>({env,...(sc.contours?.[env]||{})}));
  let sourceState=sc.state||'unknown';
  if(sourceState==='success'){
   const times=[timestamp(sc,'finished_at'),...contours.map(c=>timestamp(c,'observed_at'))];
   sourceState=times.some(value=>!Number.isFinite(value))?'unknown':times.some(value=>now-value>STALE)?'stale':'success';
  }
  if(sourceState==='processing'&&now-timestamp(sc,'started_at')>30*60*1000)sourceState='stale';
  const changed=c=>c.changed===true?' · XSD изменились':c.changed===false?' · XSD без изменений':c.observed_at?' · первый снимок':'';
  const sourceLines=contours.map(c=>(c.env==='prod'?'Продуктивный':'Тестовый')+': '+(c.observed_at?date(timestamp(c,'observed_at'))+changed(c):'не проверен в последнем запуске'));
  if(sourceState==='stale'&&sc.state==='processing')sourceLines.push('Сбор не завершился за 30 минут. Проверьте журнал SourceCraft.');
  if(sc.state==='failure'&&sc.error)sourceLines.push(sc.error);
  if(sc.state!=='success'&&sc.last_success_at)sourceLines.push('Последний успешный сбор: '+date(timestamp(sc,'last_success_at')));
  sourceLines.push('Следующая проверка по расписанию: '+nextRun('sourcecraft',now));
  return {title:'SourceCraft · '+(labels[sourceState]||labels.unknown),state:sourceState,lines:sourceLines,url:sc.url,link:sc.run_id?'SourceCraft · запуск № '+sc.run_id:'Открыть SourceCraft'};
 }
 function homeCard(check,checkState,versions){
  const checkedAt=timestamp(versions,'checked_at');
  const state=checkState==='success'&&(!Number.isFinite(checkedAt)||!Number.isInteger(versions.count))?'unknown':checkState||'unknown';
  const text={processing:'GitHub проверяет снимки домашнего сборщика.',failure:'Проверка GitHub завершилась ошибкой.',
   cancelled:'Проверка GitHub отменена.',waiting:'Ожидается проверка снимков GitHub.',unknown:'Обработка снимков пока не подтверждена.'};
  const lines=Number.isFinite(checkedAt)?['Снимки обоих контуров обработаны GitHub '+date(checkedAt)]:[text[state]||text.unknown];
  return {title:'Домашний сборщик · '+(labels[state]||labels.unknown),state,lines,url:check.url,link:'Открыть обработку снимков в GitHub'};
 }
 function model(data,now=Date.now()){
  if(data?.format!==1||!data.github||!data.sourcecraft)return {tone:'unknown',summary:'Автообновление · статус недоступен',cards:[],updated:''};
  const sc=data.sourcecraft,gh=data.github,check=gh.check||{},pub=gh.publication||{},versions=gh.versions||{};
  const checkState=['success','waiting'].includes(check.state)&&now-timestamp(check,'finished_at')>STALE?'stale':check.state;
  const sourceCard=data.collector_source==='home'?homeCard(check,checkState,versions):sourcecraftCard(sc,now);
  const count=Number.isInteger(versions.count)&&versions.count>=0?versions.count:null;
  const versionsText=check.state==='success'&&count!==null?(count?'Добавлено редакций: '+count:'Новых редакций нет'):check.state==='processing'?'Проверка справочника выполняется':check.state==='waiting'?'Ожидается обработка новых снимков SourceCraft':'Число новых редакций пока неизвестно';
  const publicationURL=pagesRunURL(pub),lastRun=gh.last_successful_publication;
  // Older reports could put an independent Pages run in the check's slot.
  const ambiguousPublication=!Object.hasOwn(gh,'independent_publication')&&['failure','cancelled'].includes(check.state)
   &&(pub.run_id||publicationURL)&&!pub.candidate_sha;
  const publicationTexts={success:'Обновление опубликовано',failure:'Публикация завершилась ошибкой',processing:'Публикация выполняется',waiting:'Ожидает публикации',not_published:'Публикация по итогам проверки СФР не состоялась',superseded:'Pages опубликовал другую версию справочника',cancelled:'Публикация отменена',unknown:'Публикация не подтверждена'};
  const publicationLines=[ambiguousPublication?'Связь с проверкой СФР не подтверждена':publicationTexts[pub.state]||publicationTexts.unknown];
  if(pub.finished_at)publicationLines.push(date(timestamp(pub,'finished_at')));
  const independent=gh.independent_publication;
  let independentDeployment;
  if(independent?.run_id){
   const finished=independent.finished_at?' · '+date(timestamp(independent,'finished_at')):'';
   independentDeployment={text:'Независимая выкладка · '+(labels[independent.state]||labels.unknown)+finished,
    url:pagesRunURL(independent),link:'Открыть независимую выкладку'};
  }
  let latestDeployment;
  if(lastRun?.state==='success'){
   const finished=timestamp(lastRun,'finished_at');
   if(Number.isFinite(finished)){
    const url=pagesRunURL(lastRun);
    latestDeployment={text:'Последняя успешная выкладка: '+date(finished),
     url:url===publicationURL||url===independentDeployment?.url?null:url};
   }
  }
  const generated=timestamp(data,'generated_at');
  const states=[sourceCard.state,checkState||'unknown',pub.state||'unknown'];
  let tone=states.includes('failure')||states.includes('not_published')?'failure':
   states.includes('stale')||now-generated>STALE?'stale':
   states.some(s=>['processing','waiting'].includes(s))?'processing':
   states.every(s=>s==='success')&&count!==null?'success':'unknown';
  if(!Number.isFinite(generated))tone='unknown';
  const superseded=pub.state==='superseded'&&sourceCard.state==='success'&&checkState==='success';
  const summary={success:'Автообновление · успешно · '+(count?'новых редакций: '+count:'новых редакций нет'),failure:'Автообновление · есть ошибка',stale:'Автообновление · данные статуса устарели',processing:'Автообновление · проверка или публикация ожидается',unknown:superseded?'Автообновление · опубликована другая версия справочника':'Автообновление · не все этапы подтверждены'}[tone];
  return {tone,summary,updated:'Статус получен '+date(generated)+'. Фактический запуск может задержаться относительно расписания.',cards:[
   sourceCard,
   {title:'Новые редакции · '+(labels[checkState]||labels.unknown),state:checkState,lines:[versionsText,...(versions.checked_at?['Проверено '+date(timestamp(versions,'checked_at'))]:[]),'Следующий забор по расписанию: '+nextRun('github',now)],url:check.url,link:'Открыть проверку GitHub'},
   {title:'GitHub Pages · '+(labels[pub.state]||labels.unknown),state:pub.state,lines:publicationLines,url:publicationURL,
    link:ambiguousPublication?'Открыть запуск Pages':'Открыть публикацию',independentDeployment,latestDeployment}
  ]};
 }
 function safeLink(value){return typeof value==='string'&&(/^https:\/\/github\.com\/macros0\/sedo-reference\/actions\/runs\/\d+(?:\/attempts\/[1-9]\d*)?$/.test(value)||value==='https://sourcecraft.dev/alexey-sagitov/sedo-xsd-collector')?value:null}
 function pagesRunURL(run){
  const url=safeLink(run.url);
  if(!url?.startsWith('https://github.com/'))return null;
  const attempt=run.run_attempt;
  return Number.isInteger(attempt)&&attempt>0&&!url.includes('/attempts/')?url+'/attempts/'+attempt:url;
 }
 function mount(document,fetcher){
  const host=document.getElementById('automation-status');if(!host)return;
  const summary=document.getElementById('automation-summary'),cards=document.getElementById('automation-cards'),note=document.getElementById('automation-note'),button=document.getElementById('automation-refresh');
  function appendLink(box,value,text){
   const url=safeLink(value);if(!url)return;
   const a=document.createElement('a');a.textContent=text;a.href=url;a.target='_blank';a.rel='noopener';box.append(a);
  }
  function render(view){
   host.dataset.tone=view.tone;summary.textContent=view.summary;note.textContent=view.updated;cards.replaceChildren();
   for(const card of view.cards){const box=document.createElement('article');box.className='automation-card';box.dataset.state=card.state||'unknown';const title=document.createElement('h2');title.textContent=card.title;box.append(title);
    for(const text of card.lines){const p=document.createElement('p');p.textContent=text;box.append(p)}
    appendLink(box,card.url,card.link);
    for(const deployment of [card.independentDeployment,card.latestDeployment]){
     if(!deployment)continue;
     const p=document.createElement('p');p.textContent=deployment.text;box.append(p);
     appendLink(box,deployment.url,deployment.link||'Открыть последнюю успешную выкладку');
    }
    cards.append(box)
   }
  }
  let busy=false,pollTimer;
  async function refresh(){
   if(busy||document.visibilityState==='hidden')return;
   clearTimeout(pollTimer);busy=true;button.disabled=true;
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
   try{const response=await fetcher('https://raw.githubusercontent.com/macros0/sedo-reference/automation-status/status.json?t='+Date.now(),{cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer',signal:controller.signal});if(!response.ok)throw new Error('status unavailable');render(model(await response.json()))}
   catch(_){render({tone:'unknown',summary:'Автообновление · статус временно недоступен',cards:[],updated:'Не удалось получить свежий статус. Данные справочника доступны; попробуйте обновить статус позже.'})}
   finally{
    clearTimeout(timer);busy=false;button.disabled=false;
    if(document.visibilityState!=='hidden')pollTimer=setTimeout(refresh,60000);
   }
  }
  document.addEventListener('visibilitychange',()=>{
   clearTimeout(pollTimer);
   if(document.visibilityState!=='hidden')refresh();
  });
  button.onclick=refresh;refresh();return {refresh};
 }
 if(typeof module==='object'&&module.exports)module.exports={model,date,safeLink,mount};
 else{root.SedoAutomationStatus={model,mount};mount(root.document,root.fetch.bind(root))}
})(typeof window!=='undefined'?window:globalThis);
