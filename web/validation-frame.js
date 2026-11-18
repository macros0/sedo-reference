"use strict";
// XML and diagnostics stay in this opaque frame. Only a public field index
// is posted to the page after the user explicitly clicks a navigation button.
const messages=self.SedoValidationMessages;
const core=self.SedoValidationCore,$=id=>document.getElementById(id),sourceView=self.SedoXmlSource.mount($('xml-preview'),document,{onChange:sourceChanged,onValidate:()=>check(),onReview:field=>showReview(field)});
let worker=null,timer=null,generation=0;

const reviewPaths=new Map((SEDO_PACK.fields||[]).map((path,index)=>[path,index])),reviewsByField=new Map(),reviewCards=new Map();
for(const source of SEDO_REVIEWS)for(const finding of source.findings||[])for(const path of new Set(finding.field_paths||[])){
 const field=reviewPaths.get(path);if(field===undefined)continue;
 if(!reviewsByField.has(field))reviewsByField.set(field,[]);
 reviewsByField.get(field).push({source,finding});
}
function reviewDetails(field){
 const matches=reviewsByField.get(field)||[];if(!matches.length)return null;
 const details=document.createElement('details'),summary=document.createElement('summary');
 details.className='spec-review-details';summary.textContent='Показать замечания ('+matches.length+')';details.append(summary);
 for(const {source,finding} of matches){
  const item=document.createElement('article'),heading=document.createElement('strong'),origin=document.createElement('p'),evidence=document.createElement('dl');
  heading.textContent=finding.kind;origin.textContent=source.name+(source.version?' · версия '+source.version:'')+(source.date?' · '+source.date:'');
  for(const [label,value] of [['В спецификации',finding.spec],['В XSD',finding.xsd],['Последствие',finding.impact],['Место в спецификации',finding.location],['Место в XSD',finding.xlocation]]){
   if(!value)continue;const term=document.createElement('dt'),description=document.createElement('dd');term.textContent=label;description.textContent=value;evidence.append(term,description);
  }
  item.append(heading,origin,evidence);details.append(item);
 }
 return details;
}
function clearReviews(){reviewCards.clear();$('spec-reviews').replaceChildren();$('spec-reviews').hidden=true;sourceView.setReviews([])}
function showReview(field){
 const card=reviewCards.get(field);if(!card)return;
 card.details.open=true;card.item.scrollIntoView?.({block:'nearest'});card.details.querySelector('summary')?.focus?.();
}
function renderReviews(locations){
 clearReviews();const found=(locations||[]).filter(location=>reviewsByField.has(location.field));
 sourceView.setReviews(found);if(!found.length)return;
 const heading=document.createElement('h3');heading.textContent='Поля с замечаниями в спецификации · '+found.length;$('spec-reviews').append(heading);$('spec-reviews').hidden=false;
 for(const location of found){
  const item=document.createElement('article'),path=document.createElement('code'),show=document.createElement('button'),open=document.createElement('button'),details=reviewDetails(location.field);
  item.className='spec-review-card';path.textContent=location.path;show.type=open.type='button';
  show.textContent='Показать поле в XML';show.onclick=()=>sourceView.showReview(location);
  open.textContent='Открыть поле в справочнике';open.onclick=()=>parent.postMessage({type:'sedo-validation-field',revision:SEDO_PACK.revision_id,field:location.field},SEDO_PARENT_ORIGIN);
  item.append(path,show,open,details);
  if(location.rangesTruncated){const note=document.createElement('small');note.textContent='Для повторяющегося поля показана часть мест в XML.';item.append(note)}
  $('spec-reviews').append(item);reviewCards.set(location.field,{item,details});
 }
}

const workerSource='self.SEDO_WASM=Uint8Array.from(atob('+JSON.stringify(SEDO_WASM64)+'),c=>c.charCodeAt(0));\n'+SEDO_WORKER;
const workerUrl=URL.createObjectURL(new Blob([workerSource],{type:'text/javascript'}));
function stop(){generation++;if(worker){worker.terminate();worker=null}clearTimeout(timer);timer=null}
function status(text,cls=''){$('status').textContent=text;$('status').className=cls}
function checking(value){$('check').disabled=value;sourceView.setBusy(value)}
function sourceChanged(){stop();clearReviews();$('xml').value='';$('xml-text').value='';$('errors').replaceChildren();$('validation-results').className='validation-results source-only';checking(!sourceView.getEditedSource().trim());status('XML изменён. Нажмите «Проверить исправленный XML», чтобы обновить результат.')}
function clear(){stop();clearReviews();sourceView.clear();$('xml').value='';$('xml-text').value='';$('check').disabled=true;$('errors').replaceChildren();status('Файл и результат очищены. Выберите следующий XML.')}
$('clear').onclick=clear;
$('xml').onchange=()=>{stop();clearReviews();sourceView.clear();$('xml-text').value='';$('errors').replaceChildren();$('check').disabled=!$('xml').files.length;status($('xml').files.length?'Файл выбран локально. Нажмите «Проверить XML».':'Выберите файл для проверки.')};
$('xml-text').oninput=()=>{stop();clearReviews();sourceView.clear();$('xml').value='';$('errors').replaceChildren();$('check').disabled=!$('xml-text').value.trim();status('Текст остаётся только в этом окне. Нажмите «Проверить XML».')};
async function check(){
 stop();const token=generation,edited=sourceView.getEditedSource(),file=edited===null?$('xml').files[0]:null,pasted=edited===null?$('xml-text').value:edited;if(!file&&!pasted.trim())return;
 clearReviews();sourceView.setErrors([]);$('validation-results').className='validation-results source-only';checking(true);$('errors').replaceChildren();status('Проверяем XML на вашем компьютере…');
 try{
  if((file?.size||pasted.length)>core.MAX_BYTES)throw Error('Размер XML превышает 10 МиБ. Используйте Python-валидатор из ZIP.');
  const bytes=file?new Uint8Array(await file.arrayBuffer()):new TextEncoder().encode(pasted.replace(/^(<\?xml\s[^?]*\bencoding\s*=\s*)(["'])[^"']*\2/, '$1"UTF-8"'));if(token!==generation)return;
  core.checkPack(SEDO_PACK,SEDO_PACK);const decoded=core.inspectBytes(bytes);sourceView.setSource(file?decoded:pasted,{preserveEditor:edited!==null});sourceView.setBusy(true);
  worker=new Worker(workerUrl);timer=setTimeout(()=>{if(token!==generation)return;stop();checking(false);status('Проверка остановлена через 30 секунд. Можно повторить или использовать Python-валидатор из ZIP.','invalid')},30000);
  worker.onmessage=event=>{if(token!==generation)return;const result=event.data;stop();checking(false);
   status(result.valid?'XML соответствует XSD выбранного сообщения и редакции.':result.runtime?'Проверку не удалось завершить.':'XML не соответствует выбранной схеме.',result.valid?'valid':'invalid');
   renderReviews(result.reviewLocations||[]);sourceView.setErrors(result.errors||[]);$('validation-results').className='validation-results'+(result.errors?.length?'':' source-only');
   for(const error of core.groupErrors(result.errors||[])){
    const item=document.createElement('li'),message=document.createElement('button');item.className='error-card';message.type='button';message.className='error-message';message.textContent=(error.line?'Строка '+error.line:'Диагностика XML')+' · Нарушений: '+error.diagnostics.length;message.title='Перейти к XML возле ошибки';message.disabled=!sourceView.canShow(error);message.onclick=()=>sourceView.showError(error);item.append(message);
    const reasons=document.createElement('ul');reasons.className='error-diagnostics';reasons.setAttribute('aria-label','Нарушенные ограничения и диагностики');
    for(const diagnostic of error.diagnostics){
     const reason=document.createElement('li'),explanation=document.createElement('span');explanation.className='error-explanation';explanation.textContent=(diagnostic.line&&diagnostic.line!==error.line?'Строка '+diagnostic.line+': ':'')+messages.explain(diagnostic.message);
     const details=document.createElement('details'),summary=document.createElement('summary'),original=document.createElement('pre');details.className='error-details';details.open=false;summary.textContent='Подробнее';original.className='error-original';original.textContent=diagnostic.message;details.append(summary,original);reason.append(explanation,details);reasons.append(reason)
    }item.append(reasons);
    if(error.ambiguous){const note=document.createElement('p');note.textContent='В этой строке несколько возможных путей'+(error.truncated?' (показаны первые 8)':'')+'. Выберите нужное поле.';item.append(note)}
    for(const location of error.locations||[]){
     const code=document.createElement('code');code.textContent=location.path;item.append(code);const notes=reviewDetails(location.field);if(notes){item.className='error-card has-spec-review';item.append(notes)}
     if(location.ranges?.length){const show=document.createElement('button');show.type='button';show.textContent='Показать тег в XML';show.onclick=()=>sourceView.showError(error,location);item.append(show)}
     const field=location.field;
     if(Number.isInteger(field)&&field>=0&&field<(SEDO_PACK.fields||[]).length){
      const button=document.createElement('button');button.type='button';button.textContent=location.parent?'Открыть родительский блок':'Посмотреть ограничения';
      button.onclick=()=>parent.postMessage({type:'sedo-validation-field',revision:SEDO_PACK.revision_id,field},SEDO_PARENT_ORIGIN);item.append(button);
     }
    }
    if(error.sourceAmbiguous&&!error.ambiguous){const note=document.createElement('small');note.textContent='Несколько экземпляров тега: подсветка показывает возможные места.';item.append(note)}
    if(!error.locations?.length){const note=document.createElement('small');note.textContent='Путь тега не удалось определить.';item.append(note)}
    $('errors').append(item);
   }
  };
  worker.onerror=()=>{if(token!==generation)return;stop();checking(false);status('Ошибка запуска валидатора. Попробуйте снова или используйте Python-валидатор из ZIP.','invalid')};
  worker.postMessage({bytes,pack:SEDO_PACK,reviewFields:[...reviewsByField.keys()],source:file?undefined:pasted},[bytes.buffer]);
 }catch(error){if(token!==generation)return;stop();checking(false);status(String(error.message||error),'invalid')}
}
$('check').onclick=check;
addEventListener('pagehide',()=>{stop();URL.revokeObjectURL(workerUrl)});
