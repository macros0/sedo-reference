"use strict";
window.SedoValidator=(()=>{
const ASSETS=[{"key":"engine","path":"vendor/xmllint/xmllint-browser.mjs","sha":"5f5a2c17be12d398c640f3b494e7ded8cd8c9b0175d2c65e6f197a28b77fd01e"},{"key":"wasm","path":"vendor/xmllint/xmllint.wasm","sha":"4e3cc21a67e8dd40ccb59822e4193fd1923b4cda08b40fe8ff8973de5eed9515"},{"key":"source","path":"validation-source.js","sha":"bddb85198bbe30a023b84ff9f70c1e08c503b87ff2910c130e895ced2f8e6122"},{"key":"core","path":"validation-core.js","sha":"6843ec523fef1f6fac3c0881f1e19723451c37f3ba73e788c0b05a62bd63a680"},{"key":"paths","path":"validation-paths.js","sha":"73fd800be0a3ecb802faef44f6b3e3666fd39763d4a6e3f4c340444bfedd1755"},{"key":"worker","path":"validation-worker.js","sha":"cb5f05e0d7e2e16837912dbea1e9a4dda9c1e5007d0d098165fa875a6118b02a"},{"key":"frame","path":"validation-frame.js","sha":"785ca5046685024eec677c648e3444651299321c1b0d2b07d5235383b8cc0f2e"}];
let serial=0,active=null;
const encoder=new TextEncoder(),hex=bytes=>Array.from(new Uint8Array(bytes),x=>x.toString(16).padStart(2,'0')).join('');
const digest=async bytes=>hex(await crypto.subtle.digest('SHA-256',bytes));
const b64=bytes=>{let text='';for(let i=0;i<bytes.length;i+=16384)text+=String.fromCharCode(...bytes.subarray(i,i+16384));return btoa(text)};
const json=value=>JSON.stringify(value).replaceAll('<','\\u003c').replaceAll('\u2028','\\u2028').replaceAll('\u2029','\\u2029');
function checkedFields(pack){
 const fields=pack.fields===undefined?[]:pack.fields;
 if(!Array.isArray(fields)||fields.length>50000||fields.some(p=>typeof p!=='string'||!p||p.length>8192||!(p===pack.root||p.startsWith(pack.root+'/')))||new Set(fields).size!==fields.length)throw Error('Некорректный публичный индекс полей.');
 if(pack.field_namespaces!==undefined&&(!Array.isArray(pack.field_namespaces)||pack.field_namespaces.length!==fields.length||pack.field_namespaces.some(ns=>!Array.isArray(ns)||ns.some(n=>typeof n!=='string'))))throw Error('Некорректные пространства имён полей.');
 return fields.slice();
}
async function createDocument({pack,expected=pack,engine,wasm,core,paths='',source='',worker,frame,parentOrigin=window.location?.origin||'http://localhost'}){
 checkedFields(pack);
 for(const key of ['number','environment','revision_id','root','namespace'])if((pack[key]||'')!==(expected[key]||''))throw Error('Комплект XSD не соответствует выбранной редакции.');
 const boundary=engine.indexOf(';(function initWorker()');if(boundary>=0)engine=engine.slice(0,boundary);
 const workerCode=engine+'\n'+core+'\n'+paths+'\n'+worker;
 const script=('"use strict";\nconst SEDO_PACK='+json(pack)+';\nconst SEDO_WORKER='+json(workerCode)+';\nconst SEDO_PARENT_ORIGIN='+json(parentOrigin)+';\nconst SEDO_WASM64='+json(wasm)+';\n'+core+'\n'+source+'\n'+frame).replace(/\r\n?/g,'\n');
 const hash=b64(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(script))));
 const policy="default-src 'none'; script-src 'sha256-"+hash+"' 'wasm-unsafe-eval'; worker-src blob:; connect-src 'none'; style-src 'unsafe-inline'; img-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
 return '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="'+policy+'"><meta name="referrer" content="no-referrer"><style>body{font:16px system-ui;color:#183b3c;margin:20px;line-height:1.5}button,input{font:inherit}button{padding:9px 16px;margin:8px 8px 8px 0;cursor:pointer}input{display:block;margin:16px 0;max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}code{display:block;margin:8px 0;overflow-wrap:anywhere}li{margin:12px 0;overflow-wrap:anywhere}.valid{color:#096f46}.invalid{color:#a32929}small{display:block;color:#52666a}#status{font-weight:600}.validation-results{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,3fr);gap:18px;align-items:start}.validation-results.source-only{grid-template-columns:minmax(0,1fr)}#errors{margin-top:0;padding-left:20px}#errors:empty{display:none}.error-message{display:block;width:100%;border:0;padding:0;margin:0;background:none;color:inherit;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}.error-message:disabled{cursor:default}.source-editor{display:block;box-sizing:border-box;width:100%;max-width:100%;min-height:260px;padding:10px;border:1px solid #9aafad;border-radius:6px;background:#fff;color:#183b3c;font:14px/20px monospace;white-space:pre;resize:vertical}.source-editor[hidden]{display:none}.source-lines[hidden]{display:none}.source-lines{max-height:320px;overflow:auto;border:1px solid #c5d4d3;border-radius:6px;background:#f8faf9}.source-row{display:grid;grid-template-columns:4em minmax(max-content,1fr);min-height:20px;font:13px/20px Consolas,monospace;white-space:pre}.source-number{position:sticky;left:0;z-index:1;text-align:right;padding-right:10px;color:#647779;background:#eef2f0;user-select:none}.source-code{margin:0;white-space:pre;overflow-wrap:normal}.source-current{background:#e8f3ff}.xml-error-tag{background:#ffce7a;color:#4d2900;outline:1px solid #b77419;border-radius:2px}.source-tools{display:flex;flex-wrap:wrap;gap:5px;margin:10px 0}.source-tools button{font-size:12px;padding:5px 8px;margin:0}.source-tools input{width:72px;margin:0}.source-info{font-size:12px;color:#52666a}#xml-preview h3{margin:0 0 8px}@media(max-width:760px){.validation-results{display:block}#xml-preview{margin-top:18px}}</style></head><body><p>Файл, его имя и результаты остаются только в этом окне браузера. Сетевые обращения здесь запрещены. XML не сохраняется.</p><label for="xml">Выберите XML до 10 МиБ</label><input id="xml" type="file" accept=".xml,application/xml,text/xml"><details><summary>Или вставьте XML текстом</summary><label for="xml-text">XML для локальной проверки</label><textarea id="xml-text" rows="8" style="width:100%;box-sizing:border-box;font:14px monospace" autocomplete="off" spellcheck="false"></textarea></details><button id="check" disabled>Проверить XML</button><button id="clear">Очистить файл и результат</button><p id="status" role="status">Выберите файл для проверки.</p><div id="validation-results" class="validation-results source-only"><ol id="errors"></ol><section id="xml-preview" aria-label="Локальный просмотр XML" hidden></section></div><small>Проверяются корень, структура, типы и ограничения XSD. Бизнес-проверки СФР и подлинность подписи не проверяются. DTD и ENTITY запрещены. Максимальное время — 30 секунд.</small><script>'+script+'</script></body></html>';
}
async function limited(stream,max){const reader=stream.getReader();let size=0,parts=[];try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>max)throw Error('Слишком большой файл программы или схем.');parts.push(value)}}catch(e){await reader.cancel().catch(()=>{});throw e}const result=new Uint8Array(size);let offset=0;for(const p of parts){result.set(p,offset);offset+=p.length}return result}
async function download(path,sha,signal,max=2*1024*1024){
 const response=await fetch(path+'?v='+sha.slice(0,16),{signal,credentials:'omit',referrerPolicy:'no-referrer'});if(!response.ok)throw Error('Не удалось загрузить публичный комплект: HTTP '+response.status);
 const bytes=await limited(response.body,max);if(await digest(bytes)!==sha)throw Error('Контрольная сумма программы или схем не совпала. Обновите страницу.');return bytes;
}
function dispose(){serial++;if(active){active.abort.abort();active.panel.remove();active=null}}
async function mount(row,container,onField){
 dispose();const token=serial,abort=new AbortController(),panel=document.createElement('section');panel.className='local-validator';panel.setAttribute('aria-label','Локальная проверка XML');
 const heading=document.createElement('h2');heading.textContent='Локальная проверка XML · '+row.number;
 const info=document.createElement('p');info.textContent=(row.environment==='prod'?'Продуктивный':'Тестовый')+' · документ '+(row.document_version||'без версии')+' · '+(row.label_date||'без даты')+' · редакция '+row.revision_id.slice(0,8);
 const close=document.createElement('button');close.type='button';close.textContent='Закрыть валидатор';close.onclick=dispose;
 const status=document.createElement('p');status.setAttribute('role','status');status.textContent='Подготавливаем программу и XSD. Файл пока не выбирается и никуда не передаётся.';
 panel.append(heading,info,close,status);container.append(panel);active={abort,panel,row,onField};
 try{
  if(!window.isSecureContext||!crypto?.subtle||typeof DecompressionStream!=='function'||typeof WebAssembly!=='object')throw Error('Нужен современный браузер с WebAssembly и DecompressionStream, открытый по HTTPS или localhost. Можно использовать Python-валидатор из ZIP.');
  if(!/^[a-f0-9]{64}$/.test(row.revision_id)||row.validation_download!=='schemas/'+row.revision_id+'.validation.json.gz'||!/^[a-f0-9]{64}$/.test(row.validation_sha256||''))throw Error('Для этой редакции нет комплекта браузерной проверки.');
  const [compressed,...assets]=await Promise.all([download(row.validation_download,row.validation_sha256,abort.signal,8*1024*1024),...ASSETS.map(a=>download(a.path,a.sha,abort.signal))]);
  const raw=await limited(new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip')),32*1024*1024),pack=JSON.parse(new TextDecoder().decode(raw));
  // No private input exists yet: only verified public bytes enter the frame.
  const payload={pack,expected:row};ASSETS.forEach((a,i)=>{payload[a.key]=a.key==='wasm'?b64(assets[i]):new TextDecoder().decode(assets[i])});
  const srcdoc=await createDocument(payload);if(token!==serial)return;
  const frame=document.createElement('iframe');frame.title='Приватный валидатор XML: файл и результаты остаются в браузере';frame.setAttribute('sandbox','allow-scripts');frame.setAttribute('referrerpolicy','no-referrer');frame.srcdoc=srcdoc;frame.className='validator-frame';
  active.frame=frame;active.fields=checkedFields(pack);panel.append(frame);status.textContent='Готово. Схема и программа загружены; теперь проверка работает и без интернета. Сетевые обращения внутри валидатора запрещены.';
 }catch(error){if(token!==serial)return;status.textContent='Валидатор не открыт: '+String(error.message||error)+ ' Закройте окно и попробуйте снова.'}
}
window.addEventListener?.('message',event=>{
 const data=event.data,current=active;
 if(!current?.frame?.contentWindow||event.source!==current.frame.contentWindow||event.origin!=='null'||!data||Object.keys(data).sort().join('|')!=='field|revision|type'||data.type!=='sedo-validation-field'||data.revision!==current.row.revision_id||!Number.isInteger(data.field)||data.field<0||data.field>=current.fields.length)return;
 if(typeof current.onField==='function')current.onField(current.fields[data.field]);
});
window.addEventListener?.('pagehide',dispose);
return {mount,dispose,createDocument};
})();
