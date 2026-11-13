(function(root){
 'use strict';
 const STALE=36*60*60*1000;
 const labels={success:'Успешно',failure:'Ошибка',processing:'Выполняется',waiting:'Ожидается',not_published:'Не опубликовано',cancelled:'Отменено',unknown:'Нет данных',stale:'Данные устарели'};
 function date(value){const n=Date.parse(value);return Number.isFinite(n)?new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'short',timeStyle:'short'}).format(n)+' МСК':'время неизвестно'}
 function model(data,now=Date.now()){
  if(data?.format!==1||!data.github||!data.sourcecraft)return {tone:'unknown',summary:'Автообновление · статус недоступен',cards:[],updated:''};
  const sc=data.sourcecraft,gh=data.github,check=gh.check||{},pub=gh.publication||{},versions=gh.versions||{};
  const contours=['prod','test'].map(env=>({env,...(sc.contours?.[env]||{})}));
  let sourceState=sc.state||'unknown';
  if(sourceState==='success'&&!contours.every(c=>Number.isFinite(Date.parse(c.observed_at))))sourceState='unknown';
  const sourceAge=now-Date.parse(sc.finished_at);
  if(sourceState==='success'&&sourceAge>STALE)sourceState='stale';
  if(sourceState==='processing'&&now-Date.parse(sc.started_at)>30*60*1000)sourceState='stale';
  const checkState=['success','waiting'].includes(check.state)&&now-Date.parse(check.finished_at)>STALE?'stale':check.state;
  const changed=c=>c.changed===true?' · XSD изменились':c.changed===false?' · XSD без изменений':c.observed_at?' · первый снимок':'';
  const sourceLines=contours.map(c=>(c.env==='prod'?'Продуктивный':'Тестовый')+': '+(c.observed_at?date(c.observed_at)+changed(c):'не проверен в последнем запуске'));
  if(sourceState==='stale'&&sc.state==='processing')sourceLines.push('Сбор не завершился за 30 минут. Проверьте журнал SourceCraft.');
  if(sc.state==='failure'&&sc.error)sourceLines.push(sc.error);
  if(sc.state!=='success'&&sc.last_success_at)sourceLines.push('Последний успешный сбор: '+date(sc.last_success_at));
  const count=Number.isInteger(versions.count)&&versions.count>=0?versions.count:null;
  const versionsText=check.state==='success'&&count!==null?(count?'Добавлено редакций: '+count:'Новых редакций нет'):check.state==='processing'?'Проверка справочника выполняется':check.state==='waiting'?'Ожидается обработка новых снимков SourceCraft':'Число новых редакций пока неизвестно';
  const publicationText={success:'Обновление опубликовано',failure:'Публикация завершилась ошибкой',processing:'Публикация выполняется',waiting:'Ожидает публикации',not_published:'Обновление не опубликовано',cancelled:'Публикация отменена',unknown:'Публикация не подтверждена'}[pub.state]||'Публикация не подтверждена';
  const publicationLines=[publicationText];if(pub.finished_at)publicationLines.push(date(pub.finished_at));
  if(pub.state!=='success'&&gh.last_successful_publication?.finished_at)publicationLines.push('Предыдущая успешная публикация: '+date(gh.last_successful_publication.finished_at));
  const states=[sourceState,checkState||'unknown',pub.state||'unknown'];
  let tone=states.includes('failure')||states.includes('not_published')?'failure':
   states.includes('stale')||now-Date.parse(data.generated_at)>STALE?'stale':
   states.some(s=>['processing','waiting'].includes(s))?'processing':
   states.every(s=>s==='success')&&count!==null?'success':'unknown';
  if(!Number.isFinite(Date.parse(data.generated_at)))tone='unknown';
  const summary={success:'Автообновление · успешно · '+(count?'новых редакций: '+count:'новых редакций нет'),failure:'Автообновление · есть ошибка',stale:'Автообновление · данные статуса устарели',processing:'Автообновление · проверка или публикация ожидается',unknown:'Автообновление · не все этапы подтверждены'}[tone];
  return {tone,summary,updated:'Статус получен '+date(data.generated_at),cards:[
   {title:'SourceCraft · '+(labels[sourceState]||labels.unknown),state:sourceState,lines:sourceLines,url:sc.url,link:sc.run_id?'SourceCraft · запуск № '+sc.run_id:'Открыть SourceCraft'},
   {title:'Новые редакции · '+(labels[checkState]||labels.unknown),state:checkState,lines:[versionsText,...(versions.checked_at?['Проверено '+date(versions.checked_at)]:[])],url:check.url,link:'Открыть проверку GitHub'},
   {title:'GitHub Pages · '+(labels[pub.state]||labels.unknown),state:pub.state,lines:publicationLines,url:pub.url,link:'Открыть публикацию'}
  ]};
 }
 function safeLink(value){return typeof value==='string'&&(/^https:\/\/github\.com\/macros0\/sedo-reference\/actions\/runs\/\d+$/.test(value)||value==='https://sourcecraft.dev/alexey-sagitov/sedo-xsd-collector')?value:null}
 function mount(document,fetcher){
  const host=document.getElementById('automation-status');if(!host)return;
  const summary=document.getElementById('automation-summary'),cards=document.getElementById('automation-cards'),note=document.getElementById('automation-note'),button=document.getElementById('automation-refresh');
  function render(view){
   host.dataset.tone=view.tone;summary.textContent=view.summary;note.textContent=view.updated;cards.replaceChildren();
   for(const card of view.cards){const box=document.createElement('article');box.className='automation-card';box.dataset.state=card.state||'unknown';const title=document.createElement('h2');title.textContent=card.title;box.append(title);
    for(const text of card.lines){const p=document.createElement('p');p.textContent=text;box.append(p)}
    const url=safeLink(card.url);if(url){const a=document.createElement('a');a.textContent=card.link;a.href=url;a.target='_blank';a.rel='noopener';box.append(a)}cards.append(box)
   }
  }
  let busy=false;
  async function refresh(){
   if(busy)return;busy=true;button.disabled=true;
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
   try{const response=await fetcher('https://raw.githubusercontent.com/macros0/sedo-reference/automation-status/status.json?t='+Date.now(),{cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer',signal:controller.signal});if(!response.ok)throw new Error('status unavailable');render(model(await response.json()))}
   catch(_){render({tone:'unknown',summary:'Автообновление · статус временно недоступен',cards:[],updated:'Не удалось получить свежий статус. Данные справочника доступны; попробуйте обновить статус позже.'})}
   finally{clearTimeout(timer);busy=false;button.disabled=false}
  }
  button.onclick=refresh;refresh();return {refresh};
 }
 if(typeof module==='object'&&module.exports)module.exports={model,date,safeLink,mount};
 else{root.SedoAutomationStatus={model,mount};mount(root.document,root.fetch.bind(root))}
})(typeof window!=='undefined'?window:globalThis);
