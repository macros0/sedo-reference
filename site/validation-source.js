(function(scope){
"use strict";
// Source/draft stay private; read-only rows and editor line-number DOM are bounded.
function mount(host,document,{onChange=()=>{},onValidate=()=>{}}={}){
 const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e};
 const title=node('h3','','XML — локальный просмотр'),info=node('p','source-info'),tools=node('div','source-tools'),lines=node('div','source-lines');
 lines.setAttribute('role','region');lines.setAttribute('aria-label','XML с номерами строк');
 const input=node('input');input.type='number';input.min='1';input.setAttribute('aria-label','Номер строки XML');
 const action=(text,fn)=>{const b=node('button','',text);b.type='button';b.onclick=fn;tools.append(b);return b};
 const previous=action('Предыдущие строки',()=>go(current-25,1,true));tools.append(input);const jump=action('К строке',()=>go(Number(input.value),1,true));const next=action('Следующие строки',()=>go(current+25,1,true));
 const left=action('Ранее в строке',()=>go(current,column-1024,true)),right=action('Далее в строке',()=>go(current,column+1024,true));
 input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();jump.onclick()}};
 const editTools=node('div','source-tools'),editor=node('textarea','source-editor');editor.setAttribute('aria-label','XML для редактирования');editor.setAttribute('autocomplete','off');editor.setAttribute('spellcheck','false');editor.setAttribute('wrap','off');editor.rows=12;editor.hidden=true;
 const editWrap=node('div','source-edit-wrap'),gutter=node('div','editor-gutter'),gutterViewport=node('div','editor-gutter-viewport');gutter.setAttribute('aria-hidden','true');gutter.append(gutterViewport);editWrap.append(gutter,editor);editWrap.hidden=true;
 const edit=node('button','','Редактировать XML'),repeat=node('button','','Проверить исправленный XML');edit.type=repeat.type='button';repeat.hidden=true;repeat.disabled=true;editTools.append(edit,repeat);
 const editNote=node('small','','Правки остаются в этом окне до очистки или закрытия валидатора. Исходный файл не меняется.');
 host.replaceChildren(title,info,editTools,tools,editWrap,lines,editNote);host.hidden=true;
 let text='',count=0,current=1,column=1,ranges=[],hint='',editing=false,hasDraft=false,busy=false;
 function countLines(){count=1;for(let i=0;i<text.length;i++)if(text.charCodeAt(i)===10)count++}
 function syncEditorGutter(){
  if(!editing||!count)return;
  const style=document.defaultView?.getComputedStyle(editor),height=Number.parseFloat(style?.lineHeight)||20,padding=Number.parseFloat(style?.paddingTop),topPadding=Number.isFinite(padding)?padding:10,scroll=Math.max(0,Number(editor.scrollTop)||0),viewport=Number(editor.clientHeight)||260;
  const first=Math.max(1,Math.min(count,Math.floor(Math.max(0,scroll-topPadding)/height)+1)),last=Math.min(count,first+Math.min(64,Math.ceil(viewport/height)+2)-1);
  editWrap.style.gridTemplateColumns=Math.max(4,String(count).length+2)+'ch minmax(0,1fr)';gutterViewport.style.height=viewport+'px';gutterViewport.replaceChildren();
  for(let number=first;number<=last;number++){const row=node('span','editor-gutter-line'+(number===current?' editor-gutter-current':''),String(number));row.setAttribute('data-line',String(number));row.style.top=(topPadding+(number-1)*height-scroll)+'px';row.style.height=height+'px';row.style.lineHeight=height+'px';gutterViewport.append(row)}
 }
 function editorInfo(){input.value=String(current);input.max=String(count);info.textContent='Строка '+current+' из '+count+'. Редактирование XML. '+hint}
 function fragmentStart(length,col){return Math.max(0,Math.min(Math.max(0,length-2048),col-1-512))}
 function navigationState(length,from){previous.disabled=current===1;next.disabled=current===count;left.disabled=from===0;right.disabled=from+2048>=length}
 function editorPosition(){if(!editing||!count)return;const offset=clamp(editor.selectionStart||0,0,text.length),at=point(offset);current=at.line;column=at.column;const begin=offset>0?text.lastIndexOf('\n',offset-1)+1:0,end=text.indexOf('\n',begin),length=(end<0?text.length:end)-begin;navigationState(length,fragmentStart(length,column));editorInfo();syncEditorGutter()}
 function setBusy(value){busy=!!value;repeat.disabled=busy||!hasDraft||!editor.value.trim()}
 const measureContext=document.createElement('canvas').getContext?.('2d');
 function editorOffset(begin,end){
  const style=document.defaultView?.getComputedStyle(editor);if(measureContext)measureContext.font=style?.font||'14px monospace';
  const measure=value=>measureContext?measureContext.measureText(value).width:value.length*8.4,tabWidth=Math.max(1,measure(' ')*(Number.parseFloat(style?.tabSize)||8));
  let width=0,start=begin;while(start<end){const tab=text.indexOf('\t',start),stop=tab<0||tab>=end?end:tab;if(stop>start)width+=measure(text.slice(start,stop));if(stop===end)break;width=(Math.floor(width/tabWidth+1e-7)+1)*tabWidth;start=stop+1}return width;
 }

 edit.onclick=()=>{editing=!editing;if(editing&&!hasDraft){editor.value=text;hasDraft=true}editor.hidden=!editing;editWrap.hidden=!editing;lines.hidden=editing;edit.textContent=editing?'Показать подсветку':'Редактировать XML';repeat.hidden=!hasDraft;setBusy(busy);render(true)};
 repeat.onclick=()=>{if(!repeat.disabled)return onValidate()};
 editor.oninput=()=>{hasDraft=true;text=editor.value.replace(/\r\n?/g,'\n');countLines();ranges=[];hint='XML изменён — повторите проверку.';const at=point(editor.selectionStart||0);current=at.line;column=at.column;setBusy(false);render(false);onChange()};
 editor.onscroll=syncEditorGutter;editor.onclick=editor.onkeyup=editor.onselect=editorPosition;
 const Resize=document.defaultView?.ResizeObserver;if(typeof Resize==='function')new Resize(syncEditorGutter).observe(editor);document.defaultView?.addEventListener?.('resize',syncEditorGutter);
 const clamp=(x,a,b)=>Math.max(a,Math.min(b,Number.isFinite(x)?Math.trunc(x):a));
 function startOf(line){let at=0;for(let n=1;n<line;n++){const end=text.indexOf('\n',at);if(end<0)return text.length;at=end+1}return at}
 function point(offset){offset=clamp(offset,0,text.length);let line=1,start=0;for(let i=0;i<offset;i++)if(text.charCodeAt(i)===10){line++;start=i+1}return {line,column:offset-start+1}}
 function validRanges(error,location){return (location?[location]:error.locations||[]).flatMap(l=>l.ranges||[]).filter(r=>Number.isInteger(r.start)&&Number.isInteger(r.end)&&r.start>=0&&r.end>r.start&&r.end<=text.length).slice(0,192)}
 function canShow(error){return !!text&&(Number.isInteger(error?.line)&&error.line>0||validRanges(error||{}).length>0)}
 function render(scroll){
  if(!count)return;
  const first=Math.max(1,current-8),last=Math.min(count,first+24),focus=startOf(current)+column-1;let at=startOf(first),selected=null;
  const sorted=[...ranges].sort((a,b)=>a.start-b.start||a.end-b.end),merged=[];
  for(const r of sorted){const prev=merged.at(-1);if(prev&&r.start<prev.end)prev.end=Math.max(prev.end,r.end);else merged.push({start:r.start,end:r.end})}
  lines.replaceChildren();let currentLength=0,currentLeft=0,visibleMarks=0;
  for(let number=first;number<=last;number++){
   let end=text.indexOf('\n',at);if(end<0)end=text.length;
   const length=end-at,from=number===current?fragmentStart(length,column):0,to=Math.min(length,from+2048),begin=at+from,finish=at+to;
   const row=node('div','source-row'+(number===current?' source-current':'')),gutter=node('span','source-number',String(number)),code=node('code','source-code');gutter.setAttribute('aria-hidden','true');row.setAttribute('aria-label','Строка '+number);row.tabIndex=-1;
   if(number===current){selected=row;currentLength=length;currentLeft=from}
   if(from)code.append(document.createTextNode('…'));
   let cursor=begin;
   for(const r of merged){const a=Math.max(begin,r.start),b=Math.min(finish,r.end);if(a>=b)continue;if(a>cursor)code.append(document.createTextNode(text.slice(cursor,a)));visibleMarks++;const mark=node('mark','xml-error-tag',text.slice(a,b));mark.tabIndex=-1;code.append(mark);if(number===current&&r.start<=focus&&focus<r.end)selected=mark;cursor=b}
   if(cursor<finish)code.append(document.createTextNode(text.slice(cursor,finish)));if(to<length)code.append(document.createTextNode('…'));
   row.append(gutter,code);lines.append(row);at=end+1;
  }
  input.value=String(current);input.max=String(count);navigationState(currentLength,currentLeft);
  info.textContent='Строка '+current+' из '+count+'. Показаны строки '+first+'–'+last+'. '+(currentLength>2048?'Показан фрагмент длинной строки. ':'')+(ranges.length&&!visibleMarks?'Тег вне показанного фрагмента; нажмите «Показать тег в XML».':hint);
  if(scroll&&editing){const begin=startOf(current),end=text.indexOf('\n',begin),range=ranges.find(r=>r.start<=focus&&focus<r.end);editor.focus?.({preventScroll:true});editor.setSelectionRange(range?range.start:begin,range?range.end:(end<0?text.length:end));editor.scrollTop=Math.max(0,(current-3)*20);editor.scrollLeft=Math.max(0,editorOffset(begin,focus)-(editor.clientWidth||512)/2);editor.scrollIntoView?.({block:'center'});}
  else if(scroll&&selected){selected.scrollIntoView?.({block:'center',inline:'center'});selected.focus?.({preventScroll:true})}
  if(editing){editorInfo();syncEditorGutter()}
 }
 function go(line,col,scroll){if(!count)return;current=clamp(line,1,count);const start=startOf(current),end=text.indexOf('\n',start);column=clamp(col,1,(end<0?text.length:end)-start+1);render(scroll)}
 function showError(error,location,scroll=true){
  if(!canShow(error))return false;
  ranges=validRanges(error,location);const ambiguous=location?location.occurrences>1:!!error.sourceAmbiguous||!!error.ambiguous;
  const clipped=location?location.rangesTruncated:!!error.truncated||(error.locations||[]).some(l=>l.rangesTruncated);
  hint=ranges.length?(ambiguous?'Подсвечены возможные теги; точный экземпляр неоднозначен. ':'Подсвечен тег или атрибут ошибки. ')+(clipped?'Показана часть возможных мест. ':''):'Точный тег не определён; показана строка диагностики.';
  let line=clamp(error.line||1,1,count),col=1;
  if(ranges.length){
   if(location){const preferred=ranges.find(r=>r.kind==='attribute')||ranges[0],p=point(preferred.start);line=p.line;col=p.column}
   else {const begin=startOf(line),next=text.indexOf('\n',begin),end=next<0?text.length:next;const onLine=ranges.filter(r=>r.start>=begin&&r.start<=end),preferred=onLine.find(r=>r.kind==='attribute')||onLine[0];if(preferred)col=preferred.start-begin+1}
  }
  if(editing&&!scroll){const caret=point(editor.selectionStart||0);line=caret.line;col=caret.column}
  go(line,col,scroll);return true;
 }
 function clear(){text='';count=0;current=1;column=1;ranges=[];hint='';editing=hasDraft=busy=false;editor.value='';editor.hidden=true;editWrap.hidden=true;gutterViewport.replaceChildren();gutterViewport.style.height='0px';editWrap.style.gridTemplateColumns='';lines.hidden=false;edit.textContent='Редактировать XML';repeat.hidden=true;repeat.disabled=true;lines.replaceChildren();info.textContent='';input.value='';input.max='1';previous.disabled=next.disabled=left.disabled=right.disabled=true;host.hidden=true}
 return {getEditedSource(){return hasDraft?editor.value:null},setBusy,setSource(source,{preserveEditor=false}={}){if(!preserveEditor||!hasDraft)clear();text=String(source).replace(/\r\n?/g,'\n');if(!text)return;countLines();current=Math.min(current,count);ranges=[];hint='';host.hidden=false;render(false)},setErrors(errors){const first=(errors||[]).find(canShow);if(first)showError(first,null,false);else {ranges=[];hint='';render(false)}},showError,canShow,clear};
}
const api={mount};scope.SedoXmlSource=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof self==='object'?self:globalThis);
