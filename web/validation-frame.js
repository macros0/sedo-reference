"use strict";
// XML and diagnostics stay in this opaque frame. Only a public field index
// is posted to the page after the user explicitly clicks a navigation button.
const core=self.SedoValidationCore,$=id=>document.getElementById(id),sourceView=self.SedoXmlSource.mount($('xml-preview'),document,{onChange:sourceChanged,onValidate:()=>check()});
let worker=null,timer=null,generation=0;
const workerSource='self.SEDO_WASM=Uint8Array.from(atob('+JSON.stringify(SEDO_WASM64)+'),c=>c.charCodeAt(0));\n'+SEDO_WORKER;
const workerUrl=URL.createObjectURL(new Blob([workerSource],{type:'text/javascript'}));
function stop(){generation++;if(worker){worker.terminate();worker=null}clearTimeout(timer);timer=null}
function status(text,cls=''){$('status').textContent=text;$('status').className=cls}
function checking(value){$('check').disabled=value;sourceView.setBusy(value)}
function sourceChanged(){stop();$('xml').value='';$('xml-text').value='';$('errors').replaceChildren();$('validation-results').className='validation-results source-only';checking(!sourceView.getEditedSource().trim());status('XML изменён. Нажмите «Проверить исправленный XML», чтобы обновить результат.')}
function clear(){stop();sourceView.clear();$('xml').value='';$('xml-text').value='';$('check').disabled=true;$('errors').replaceChildren();status('Файл и результат очищены. Выберите следующий XML.')}
$('clear').onclick=clear;
$('xml').onchange=()=>{stop();sourceView.clear();$('xml-text').value='';$('errors').replaceChildren();$('check').disabled=!$('xml').files.length;status($('xml').files.length?'Файл выбран локально. Нажмите «Проверить XML».':'Выберите файл для проверки.')};
$('xml-text').oninput=()=>{stop();sourceView.clear();$('xml').value='';$('errors').replaceChildren();$('check').disabled=!$('xml-text').value.trim();status('Текст остаётся только в этом окне. Нажмите «Проверить XML».')};
async function check(){
 stop();const token=generation,edited=sourceView.getEditedSource(),file=edited===null?$('xml').files[0]:null,pasted=edited===null?$('xml-text').value:edited;if(!file&&!pasted.trim())return;
 sourceView.setErrors([]);$('validation-results').className='validation-results source-only';checking(true);$('errors').replaceChildren();status('Проверяем XML на вашем компьютере…');
 try{
  if((file?.size||pasted.length)>core.MAX_BYTES)throw Error('Размер XML превышает 10 МиБ. Используйте Python-валидатор из ZIP.');
  const bytes=file?new Uint8Array(await file.arrayBuffer()):new TextEncoder().encode(pasted.replace(/^(<\?xml\s[^?]*\bencoding\s*=\s*)(["'])[^"']*\2/, '$1"UTF-8"'));if(token!==generation)return;
  core.checkPack(SEDO_PACK,SEDO_PACK);const decoded=core.inspectBytes(bytes);sourceView.setSource(file?decoded:pasted,{preserveEditor:edited!==null});sourceView.setBusy(true);
  worker=new Worker(workerUrl);timer=setTimeout(()=>{if(token!==generation)return;stop();checking(false);status('Проверка остановлена через 30 секунд. Можно повторить или использовать Python-валидатор из ZIP.','invalid')},30000);
  worker.onmessage=event=>{if(token!==generation)return;const result=event.data;stop();checking(false);
   status(result.valid?'XML соответствует XSD выбранного сообщения и редакции.':result.runtime?'Проверку не удалось завершить.':'XML не соответствует выбранной схеме.',result.valid?'valid':'invalid');
   sourceView.setErrors(result.errors||[]);$('validation-results').className='validation-results'+(result.errors?.length?'':' source-only');
   for(const error of result.errors||[]){
    const item=document.createElement('li'),message=document.createElement('button');message.type='button';message.className='error-message';message.textContent=(error.line?'Строка '+error.line+': ':'')+error.message;message.title='Перейти к XML возле ошибки';message.disabled=!sourceView.canShow(error);message.onclick=()=>sourceView.showError(error);item.append(message);
    if(error.ambiguous){const note=document.createElement('p');note.textContent='В этой строке несколько возможных путей'+(error.truncated?' (показаны первые 8)':'')+'. Выберите нужное поле.';item.append(note)}
    for(const location of error.locations||[]){
     const code=document.createElement('code');code.textContent=location.path;item.append(code);
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
  worker.postMessage({bytes,pack:SEDO_PACK,source:file?undefined:pasted},[bytes.buffer]);
 }catch(error){if(token!==generation)return;stop();checking(false);status(String(error.message||error),'invalid')}
}
$('check').onclick=check;
addEventListener('pagehide',()=>{stop();URL.revokeObjectURL(workerUrl)});
