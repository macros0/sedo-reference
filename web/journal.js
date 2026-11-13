"use strict";
function updatesRoute(number,env){return new URLSearchParams(Object.fromEntries(Object.entries({updates:"1",message:number,env}).filter(([,v])=>v))).toString()}
function observationDate(value,dayOnly=false){
 if(!value)return "Дата обнаружения не сохранена";
 const options=dayOnly?{dateStyle:"long"}:{dateStyle:"short",timeStyle:"short"};
 return new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",...options}).format(new Date(value))+(dayOnly?"":" МСК");
}
function journalMoscowDay(value){
 if(value===null||value===undefined||value==="")return null;
 const date=new Date(value);if(!Number.isFinite(date.getTime()))return null;
 const parts=Object.fromEntries(new Intl.DateTimeFormat("en",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(date).map(p=>[p.type,p.value]));
 return parts.year+"-"+parts.month+"-"+parts.day;
}
function journalValidDay(value){
 if(!/^\d{4}-\d{2}-\d{2}$/.test(value||""))return false;
 const date=new Date(value+"T00:00:00Z");return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===value;
}
function journalDateRange(period,from="",to="",now=new Date()){
 const range={active:period==="week"||period==="month"||period==="custom",from:null,to:null,error:null};
 if(period==="week"||period==="month"){
  range.to=journalMoscowDay(now);const start=new Date(range.to+"T00:00:00Z");start.setUTCDate(start.getUTCDate()-(period==="week"?6:29));range.from=start.toISOString().slice(0,10);
 }else if(period==="custom"){
  if((from&&!journalValidDay(from))||(to&&!journalValidDay(to)))range.error="Укажите корректные даты периода.";
  else if(from&&to&&from>to)range.error="Начало периода не может быть позже его окончания.";
  range.from=from||null;range.to=to||null;
 }
 return range;
}
function journalDateMatches(observedAt,range){
 if(range.error)return false;if(!range.active)return true;
 const day=journalMoscowDay(observedAt);return !!day&&(!range.from||day>=range.from)&&(!range.to||day<=range.to);
}
function renderUpdates(params){
 const section=$("updates");section.replaceChildren(node("h1","","Журнал обновлений"),node("p","","Когда редакции впервые появились в справочнике. Время указано по Москве; дата документа СФР показана отдельно."),node("p","meta","«Первичное наполнение» — редакции, уже существовавшие при первом сборе. Повторные проверки без новых редакций в журнал не добавляются."));
 const revisions=new Map(catalog.revisions.map(r=>[r.revision_id,r]));
 const entries=(catalog.schema_updates||catalog.updates||[]).map(event=>({event,r:revisions.get(event.revision_id)})).filter(x=>x.r).sort((a,b)=>(Date.parse(b.event.observed_at)||0)-(Date.parse(a.event.observed_at)||0)||Number(a.r.number)-Number(b.r.number)||a.r.environment.localeCompare(b.r.environment));
 const filters=node("div","searchbar journal-filters"),queryLabel=node("label","","Номер, название или версия"),query=node("input");query.type="search";query.id="updates-query";query.placeholder="Например, 10110 или 3.0.4";query.value=params.has("q")?params.get("q"):params.get("message")||"";queryLabel.append(query);
 const envLabel=node("label","","Контур"),env=node("select");env.id="updates-env";for(const [value,text] of [["","Все контуры"],["prod","Продуктивный"],["test","Тестовый"]])env.add(new Option(text,value));env.value=params.get("env")||"";envLabel.append(env);filters.append(queryLabel,envLabel);section.append(filters);
 const periodRow=node("div","journal-period"),periodLabel=node("label","","Период обнаружения"),period=node("select");period.id="updates-period";
 for(const [value,text] of [["all","За всё время"],["week","За неделю (7 дней)"],["month","За месяц (30 дней)"],["custom","Выбранный период"]])period.add(new Option(text,value));period.value=["week","month","custom"].includes(params.get("period"))?params.get("period"):"all";periodLabel.append(period);
 const bounds=node("div","journal-bounds"),fromLabel=node("label","","С"),from=node("input"),toLabel=node("label","","По");from.type="date";from.id="updates-from";from.value=params.get("from")||"";fromLabel.append(from);const to=node("input");to.type="date";to.id="updates-to";to.value=params.get("to")||"";toLabel.append(to);bounds.append(fromLabel,toLabel);periodRow.append(periodLabel,bounds);
 const rangeHint=node("p","meta"),rangeError=node("p","journal-range-error");rangeError.id="updates-date-error";rangeError.setAttribute("role","alert");section.append(periodRow,rangeHint,rangeError);
 const changesLabel=node("label","journal-toggle"),onlyChanges=node("input");onlyChanges.type="checkbox";onlyChanges.id="updates-only-changes";onlyChanges.checked=params.get("changed")==="1";changesLabel.append(onlyChanges,document.createTextNode("Только с изменениями"));
 const actions=node("div","journal-actions"),reset=node("button","swap","Сбросить фильтры");reset.type="button";reset.id="updates-reset";actions.append(changesLabel,reset);section.append(actions);
 const count=node("p","meta");count.id="updates-count";count.setAttribute("role","status");const list=node("div","journal-list");section.append(count,list);
 function draw(){
  bounds.hidden=period.value!=="custom";const range=journalDateRange(period.value,from.value,to.value);
  rangeError.hidden=!range.error;rangeError.textContent=range.error||"";rangeHint.hidden=period.value==="all";
  rangeHint.textContent=period.value==="custom"?"Границы включены, время московское. Можно указать только одну дату.":"С "+(range.from||"").split("-").reverse().join(".")+" по "+(range.to||"").split("-").reverse().join(".")+" включительно, московское время.";
  if(range.error){count.textContent="Проверьте выбранный период";list.replaceChildren();return}
  const terms=normalize(query.value).split(/\s+/).filter(Boolean);
  const exactNumber=/^\d+$/.test(query.value.trim())&&messages.has(query.value.trim())?query.value.trim():null;
  const visible=entries.filter(({event,r})=>journalDateMatches(event.observed_at,range)&&(!onlyChanges.checked||(event.comparison?.status==="ready"&&["added","removed","changed"].some(key=>event.comparison.counts[key]>0)))&&(!exactNumber||r.number===exactNumber)&&(!env.value||r.environment===env.value)&&terms.every(t=>normalize([r.number,r.title,...editionSources(r).flatMap(s=>[s.document_version,s.label_date,documentName(s)]),envNames[r.environment],observationDate(event.observed_at)].join(" ")).includes(t)));
  count.textContent="Редакций в журнале · "+visible.length;list.replaceChildren();let day=null;
  for(const {event,r} of visible){
   const nextDay=observationDate(event.observed_at,true);
   if(nextDay!==day){list.append(node("h2","journal-day",nextDay));day=nextDay}
   const card=node("article","update-card");card.dataset.revision=r.revision_id;
   const badges=node("div","badges");badges.append(node("span","badge "+r.environment,envNames[r.environment]),node("span","badge",event.initial_import?"Первичное наполнение":event.previous_revision_id?"Новая редакция":"Первое появление"));card.append(badges);
   const heading=node("h3");heading.append(anchor(r.number+" · "+(editionSources(r).length>1?"Редакция схемы · источников: "+editionSources(r).length:"Версия документа "+(r.document_version||"не указана")),route(r.number,r.environment,r.ready?r.revision_id:undefined)));card.append(heading,node("p","",r.title));
   appendEditionSources(card,r);
   const time=node("time","meta",event.observed_at?"Обнаружено: "+observationDate(event.observed_at):observationDate(null));if(event.observed_at)time.dateTime=event.observed_at;card.append(time,node("p","meta","Редакция "+r.revision_id.slice(0,8)));
   if(r.source_type==="manual")card.append(node("p","meta","Ручная загрузка · "+(r.manual_source?.label||"Файл владельца")));
   else if(r.manual_source)card.append(node("p","meta","Впервые загружено вручную · "+r.manual_source.label));
   else if(!r.listed_now)card.append(node("p","meta","Редакция сохранена в истории"));
   const summary=event.comparison;
   if(summary?.status==="ready"){
    const before=revisions.get(summary.left),after=revisions.get(summary.right);
    card.append(node("p","meta","Сравнение: "+label(before)+" → "+label(after)));
    const counts=node("div","compare-counts journal-counts");
    for(const [key,text] of [["added","Добавлено"],["removed","Удалено"],["changed","Изменено"]])counts.append(node("span",key,text+" · "+summary.counts[key]));
    card.append(counts);
   }else card.append(node("p","meta",r.status==="document_only"?"DOCX сохранён. Схема XSD не загружена.":summary?.status==="no_baseline"?"Первая сохранённая редакция: подходящей редакции для сравнения пока нет.":"Подсчёт изменений недоступен: нет готового дерева для сравнения."));
   const links=node("div","journal-links");if(r.ready)links.append(anchor("Открыть дерево",route(r.number,r.environment,r.revision_id)));
   if(summary?.status==="ready")links.append(anchor("Открыть сравнение",comparisonRoute(r.number,summary.left,summary.right)));
   card.append(links);list.append(card);
  }
  if(!visible.length)list.append(node("p","empty",entries.length?(onlyChanges.checked?"Нет записей с изменениями. Измените поиск, контур, период или отключите фильтр.":"Совпадений нет. Измените поиск, контур или период."):"Сохранённых событий пока нет."));
 }
 function change(){history.replaceState(null,"","#"+new URLSearchParams({updates:"1",q:query.value,env:env.value,...(onlyChanges.checked?{changed:"1"}:{}),...(period.value!=="all"?{period:period.value}:{}),...(period.value==="custom"&&from.value?{from:from.value}:{}),...(period.value==="custom"&&to.value?{to:to.value}:{})}));draw()}
 reset.onclick=()=>{query.value="";env.value="";period.value="all";from.value="";to.value="";onlyChanges.checked=false;history.replaceState(null,"","#"+updatesRoute());draw()};
 query.oninput=change;env.onchange=change;onlyChanges.onchange=change;period.onchange=change;from.onchange=change;to.onchange=change;draw();
}
if(typeof module!=="undefined"&&module.exports)module.exports={journalDateRange,journalDateMatches};
