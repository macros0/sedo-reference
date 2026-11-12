"use strict";
function updatesRoute(number,env){return new URLSearchParams(Object.fromEntries(Object.entries({updates:"1",message:number,env}).filter(([,v])=>v))).toString()}
function observationDate(value,dayOnly=false){
 if(!value)return "Дата обнаружения не сохранена";
 const options=dayOnly?{dateStyle:"long"}:{dateStyle:"short",timeStyle:"short"};
 return new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",...options}).format(new Date(value))+(dayOnly?"":" МСК");
}
function renderUpdates(params){
 const section=$("updates");section.replaceChildren(node("h1","","Журнал обновлений"),node("p","","Когда редакции впервые появились в справочнике. Время указано по Москве; дата документа СФР показана отдельно."),node("p","meta","«Первичное наполнение» — редакции, уже существовавшие при первом сборе. Повторные проверки без новых редакций в журнал не добавляются."));
 const revisions=new Map(catalog.revisions.map(r=>[r.revision_id,r]));
 const entries=(catalog.updates||[]).map(event=>({event,r:revisions.get(event.revision_id)})).filter(x=>x.r).sort((a,b)=>(Date.parse(b.event.observed_at)||0)-(Date.parse(a.event.observed_at)||0)||Number(a.r.number)-Number(b.r.number)||a.r.environment.localeCompare(b.r.environment));
 const filters=node("div","searchbar journal-filters"),queryLabel=node("label","","Номер, название или версия"),query=node("input");query.type="search";query.id="updates-query";query.placeholder="Например, 10110 или 3.0.4";query.value=params.has("q")?params.get("q"):params.get("message")||"";queryLabel.append(query);
 const envLabel=node("label","","Контур"),env=node("select");env.id="updates-env";for(const [value,text] of [["","Все контуры"],["prod","Продуктивный"],["test","Тестовый"]])env.add(new Option(text,value));env.value=params.get("env")||"";envLabel.append(env);filters.append(queryLabel,envLabel);section.append(filters);
 const count=node("p","meta");count.id="updates-count";count.setAttribute("role","status");const list=node("div","journal-list");section.append(count,list);
 function draw(){
  const terms=normalize(query.value).split(/\s+/).filter(Boolean);
  const exactNumber=/^\d+$/.test(query.value.trim())&&messages.has(query.value.trim())?query.value.trim():null;
  const visible=entries.filter(({event,r})=>(!exactNumber||r.number===exactNumber)&&(!env.value||r.environment===env.value)&&terms.every(t=>normalize([r.number,r.title,r.document_version,r.label_date,envNames[r.environment],observationDate(event.observed_at)].join(" ")).includes(t)));
  count.textContent="Редакций в журнале · "+visible.length;list.replaceChildren();let day=null;
  for(const {event,r} of visible){
   const nextDay=observationDate(event.observed_at,true);
   if(nextDay!==day){list.append(node("h2","journal-day",nextDay));day=nextDay}
   const card=node("article","update-card");card.dataset.revision=r.revision_id;
   const badges=node("div","badges");badges.append(node("span","badge "+r.environment,envNames[r.environment]),node("span","badge",event.initial_import?"Первичное наполнение":event.previous_revision_id?"Новая редакция":"Первое появление"));card.append(badges);
   const heading=node("h3");heading.append(anchor(r.number+" · Версия "+(r.document_version||"не указана"),route(r.number,r.environment,r.revision_id)));card.append(heading,node("p","",r.title));
   const time=node("time","meta",event.observed_at?"Обнаружено: "+observationDate(event.observed_at):observationDate(null));if(event.observed_at)time.dateTime=event.observed_at;card.append(time,node("p","meta","Документ СФР: "+date(r.label_date)+" · Редакция "+r.revision_id.slice(0,8)));
   if(!r.listed_now)card.append(node("p","meta","Редакция сохранена в истории"));
   const links=node("div","journal-links");if(r.ready)links.append(anchor("Открыть дерево",route(r.number,r.environment,r.revision_id)));links.append(documentDownload(r));
   const previous=revisions.get(event.previous_revision_id);
   if(previous?.ready&&r.ready)links.append(anchor("Сравнить с версией "+(previous.document_version||"без номера"),comparisonRoute(r.number,previous.revision_id,r.revision_id)));
   else if(r.ready){
    const m=messages.get(r.number),defaults=comparisonDefaults(m),other=revisions.get(r.environment==="prod"?defaults.right:defaults.left);
    if(other?.ready&&other.environment!==r.environment)links.append(anchor("Сравнить с "+(other.environment==="test"?"тестом":"продуктивом")+" · "+(other.document_version||"без номера"),comparisonRoute(r.number,r.environment==="prod"?r.revision_id:other.revision_id,r.environment==="test"?r.revision_id:other.revision_id)));
   }
   card.append(links);list.append(card);
  }
  if(!visible.length)list.append(node("p","empty",entries.length?"Совпадений нет. Измените поиск или контур.":"Сохранённых событий пока нет."));
 }
 function change(){history.replaceState(null,"","#"+new URLSearchParams({updates:"1",q:query.value,env:env.value}));draw()}
 query.oninput=change;env.onchange=change;draw();
}
