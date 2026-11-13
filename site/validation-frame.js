"use strict";
// XML and diagnostics stay in this opaque frame. Only a public field index
// is posted to the page after the user explicitly clicks a navigation button.
const core=self.SedoValidationCore,$=id=>document.getElementById(id);
let worker=null,timer=null,generation=0;
const workerSource='self.SEDO_WASM=Uint8Array.from(atob('+JSON.stringify(SEDO_WASM64)+'),c=>c.charCodeAt(0));\n'+SEDO_WORKER;
const workerUrl=URL.createObjectURL(new Blob([workerSource],{type:'text/javascript'}));
function stop(){generation++;if(worker){worker.terminate();worker=null}clearTimeout(timer);timer=null}
function status(text,cls=''){$('status').textContent=text;$('status').className=cls}
function clear(){stop();$('xml').value='';$('xml-text').value='';$('check').disabled=true;$('errors').replaceChildren();status('Файл и результат очищены. Выберите следующий XML.')}
$('clear').onclick=clear;
$('xml').onchange=()=>{stop();$('xml-text').value='';$('errors').replaceChildren();$('check').disabled=!$('xml').files.length;status($('xml').files.length?'Файл выбран локально. Нажмите «Проверить XML».':'Выберите файл для проверки.')};
$('xml-text').oninput=()=>{stop();$('xml').value='';$('errors').replaceChildren();$('check').disabled=!$('xml-text').value.trim();status('Текст остаётся только в этом окне. Нажмите «Проверить XML».')};
$('check').onclick=async()=>{
 stop();const token=generation,file=$('xml').files[0],pasted=$('xml-text').value;if(!file&&!pasted.trim())return;
 $('check').disabled=true;$('errors').replaceChildren();status('Проверяем XML на вашем компьютере…');
 try{
  if((file?.size||pasted.length)>core.MAX_BYTES)throw Error('Размер XML превышает 10 МиБ. Используйте Python-валидатор из ZIP.');
  const bytes=file?new Uint8Array(await file.arrayBuffer()):new TextEncoder().encode(pasted.replace(/^(<\?xml\s[^?]*\bencoding\s*=\s*)(["'])[^"']*\2/, '$1"UTF-8"'));if(token!==generation)return;
  core.checkPack(SEDO_PACK,SEDO_PACK);core.inspectBytes(bytes);
  worker=new Worker(workerUrl);timer=setTimeout(()=>{if(token!==generation)return;stop();$('check').disabled=false;status('Проверка остановлена через 30 секунд. Можно повторить или использовать Python-валидатор из ZIP.','invalid')},30000);
  worker.onmessage=event=>{if(token!==generation)return;const result=event.data;stop();$('check').disabled=false;
   status(result.valid?'XML соответствует XSD выбранного сообщения и редакции.':result.runtime?'Проверку не удалось завершить.':'XML не соответствует выбранной схеме.',result.valid?'valid':'invalid');
   for(const error of result.errors||[]){
    const item=document.createElement('li');item.textContent=(error.line?'Строка '+error.line+': ':'')+error.message;
    if(error.ambiguous){const note=document.createElement('p');note.textContent='В этой строке несколько возможных путей'+(error.truncated?' (показаны первые 8)':'')+'. Выберите нужное поле.';item.append(note)}
    for(const location of error.locations||[]){
     const code=document.createElement('code');code.textContent=location.path;item.append(code);
     const field=location.field;
     if(Number.isInteger(field)&&field>=0&&field<(SEDO_PACK.fields||[]).length){
      const button=document.createElement('button');button.type='button';button.textContent=location.parent?'Открыть родительский блок':'Посмотреть ограничения';
      button.onclick=()=>parent.postMessage({type:'sedo-validation-field',revision:SEDO_PACK.revision_id,field},SEDO_PARENT_ORIGIN);item.append(button);
     }
    }
    if(!error.locations?.length){const note=document.createElement('small');note.textContent='Путь тега не удалось определить.';item.append(note)}
    $('errors').append(item);
   }
  };
  worker.onerror=()=>{if(token!==generation)return;stop();$('check').disabled=false;status('Ошибка запуска валидатора. Попробуйте снова или используйте Python-валидатор из ZIP.','invalid')};
  worker.postMessage({bytes,pack:SEDO_PACK},[bytes.buffer]);
 }catch(error){if(token!==generation)return;stop();$('check').disabled=false;status(String(error.message||error),'invalid')}
};
addEventListener('pagehide',()=>{stop();URL.revokeObjectURL(workerUrl)});
