/* Browser UI for an editable, unapplied mapping draft. */
(function(root,factory){"use strict";const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.SedoMappingEditor=api})(typeof globalThis!=="undefined"?globalThis:this,function(){
 "use strict";
 const normalized=v=>String(v||"").normalize("NFKC").toLocaleLowerCase("ru").replace(/ё/g,"е");
 function mount({container,notice,status,options,report,draft,manualLabel,onApply,onCancel}){
  const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e};
  const button=(cls,text)=>{const e=el("button",cls,text);e.type="button";return e};
  const old=new Map(options.before.map(f=>[f.path,f])),next=new Map(options.after.map(f=>[f.path,f]));
  let editorOpen=false,busy=false,closed=false,redraw=()=>{},formPending=()=>false;
  container.replaceChildren();notice.replaceChildren();
  const noticeText=el("span","mapping-draft-message"),topApply=button("open mapping-apply-top","Применить и обновить сравнение");notice.append(noticeText,topApply);notice.setAttribute("role","status");
  const sourceErrors=report.errors||[],warnings=report.warnings||[];
  for(const [messages,cls] of [[sourceErrors,"mapping-errors"],[warnings,"mapping-warnings"]])if(messages.length){const list=el("ul",cls);messages.slice(0,20).forEach(text=>list.append(el("li","",text)));if(messages.length>20)list.append(el("li","","Остальные сообщения: "+(messages.length-20)));container.append(list)}
  const bulk=el("div","mapping-controls"),all=button("swap mapping-select-all","Выбрать все предложения"),none=button("swap mapping-select-none","Снять выбор предложений"),add=button("swap mapping-add-row","Добавить соответствие");bulk.append(all,none,add);container.append(bulk);
  const selection=el("p","meta mapping-selected"),validation=el("p","mapping-errors mapping-draft-errors");selection.setAttribute("role","status");validation.setAttribute("role","alert");container.append(selection,validation);
  const filters=el("div","mapping-preview-filters"),searchLabel=el("label","","Поиск по тегу и описанию"),search=el("input","mapping-preview-search"),onlyLabel=el("label","mapping-only-label"),only=el("input","mapping-only-proposed");
  search.type="search";search.value="";search.placeholder="Тег, путь, описание или комментарий";only.type="checkbox";only.checked=false;searchLabel.append(search);onlyLabel.append(only,el("span","","Только предложенные"));filters.append(searchLabel,onlyLabel);container.append(filters);
  const editor=el("div","mapping-row-editor");editor.hidden=true;container.append(editor);
  function paginated(host,getRows,captions,makeRow,predicate=()=>true){
   const table=el("table","diff-table"),thead=el("thead"),head=el("tr"),body=el("tbody"),scroll=el("div","table-scroll"),nav=el("div","mapping-pages"),previous=button("swap mapping-prev","Назад"),nextPage=button("swap mapping-next","Далее"),caption=el("span","meta mapping-visible-count");
   captions.forEach(text=>head.append(el("th","",text)));thead.append(head);table.append(thead,body);scroll.append(table);caption.setAttribute("role","status");nav.append(previous,caption,nextPage);host.append(scroll,nav);let page=0;
   function draw(reset=false){
    if(reset)page=0;const rows=getRows(),visible=rows.filter(predicate),pages=Math.max(1,Math.ceil(visible.length/50));page=Math.min(Math.max(0,page),pages-1);body.replaceChildren();visible.slice(page*50,(page+1)*50).forEach(row=>body.append(makeRow(row)));
    if(!visible.length){const row=el("tr"),cell=el("td","empty","По этому поиску и фильтру пар не найдено.");cell.colSpan=captions.length;row.append(cell);body.append(row)}
    previous.disabled=page===0;nextPage.disabled=page===pages-1;previous.hidden=nextPage.hidden=pages===1;caption.textContent=visible.length?"Строки "+(page*50+1)+"–"+Math.min((page+1)*50,visible.length)+" из "+visible.length+" · всего пар "+rows.length+" · страница "+(page+1)+" из "+pages:"Найдено пар: 0 из "+rows.length;
   }
   previous.onclick=()=>{page--;draw()};nextPage.onclick=()=>{page++;draw()};draw();return draw;
  }
  const matches=row=>{
   if(only.checked&&row.origin==="confirmed")return false;
   const haystack=normalized([row.beforePath,row.afterPath,old.get(row.beforePath)?.name,next.get(row.afterPath)?.name,old.get(row.beforePath)?.doc,next.get(row.afterPath)?.doc,row.relativePath,row.parentBeforePath,row.parentAfterPath,row.comment,row.decision].join(" "));
   return normalized(search.value).split(/\s+/).filter(Boolean).every(t=>haystack.includes(t));
  };
  const primary=el("div","mapping-table");container.append(primary);
  redraw=paginated(primary,()=>draft.rows,["Применить","Источник","Путь и описание «Было»","Путь и описание «Стало»","Комментарий / основание","Действия"],row=>{
   const tr=el("tr"),choice=el("td"),check=el("input",row.origin==="confirmed"?"mapping-explicit":"mapping-proposal");check.type="checkbox";check.checked=row.selected;check.disabled=editorOpen||busy;check.dataset.beforePath=row.beforePath;check.dataset.afterPath=row.afterPath;check.setAttribute("aria-label","Сопоставить "+row.beforePath+" → "+row.afterPath);
   check.onchange=()=>{draft.toggle(row.id,check.checked);refresh()};choice.append(check);
   const label=row.origin==="confirmed"?manualLabel:row.origin==="workbook"?"Из Excel — не подтверждено":row.origin==="relative"?"По относительному пути":"Изменено в браузере";
   const reason=row.origin==="relative"?"Относительный путь: "+row.relativePath+"\nРодитель: "+row.parentBeforePath+" → "+row.parentAfterPath:row.origin==="workbook"?"Строка Excel "+row.row+" · решение «"+row.decision+"». "+row.comment:row.comment;
   const actions=el("td","mapping-row-actions"),edit=button("swap mapping-edit-row","Изменить"),remove=button("swap mapping-remove-row","Убрать");edit.disabled=remove.disabled=editorOpen||busy;
   edit.dataset.rowId=remove.dataset.rowId=String(row.id);edit.onclick=()=>openEditor(row.id);remove.onclick=()=>{draft.remove(row.id);refresh()};actions.append(edit,remove);
   tr.append(choice,el("td","",label),el("td","",row.beforePath+"\n"+(old.get(row.beforePath)?.doc||"")),el("td","",row.afterPath+"\n"+(next.get(row.afterPath)?.doc||"")),el("td","",reason),actions);return tr;
  },matches);
  container.append(el("p","meta","Поиск и фильтр меняют только список. Применяются все отмеченные пары, включая скрытые фильтром."));
  const conflictsHost=el("div","mapping-conflicts-host");container.append(conflictsHost);
  const apply=button("open mapping-apply","Применить и обновить сравнение"),cancel=button("swap mapping-discard","Отменить предпросмотр"),footer=el("div","mapping-controls");footer.append(apply,cancel);container.append(footer,el("p","meta","Правки пока находятся в черновике. Основная кнопка сохранит выбранные пары в браузере и обновит сравнение. Схемы и исходный Excel не изменяются."));
  function update(){
   const state=draft.summary(),rows=draft.rows,proposed=rows.filter(r=>r.origin!=="confirmed"),fixed=rows.filter(r=>r.origin==="confirmed"&&r.selected).length;
   selection.textContent="К применению: подтверждённых пар "+fixed+", предложений "+proposed.filter(r=>r.selected).length+" из "+proposed.length;
   validation.textContent=state.errors.join("\n");
   status.textContent="Сохранено пар: "+state.savedCount+". В предпросмотре выбрано: "+state.selectedCount+". Предложено из Excel "+rows.filter(r=>r.origin==="workbook").length+", предложено вложенных "+rows.filter(r=>r.origin==="relative").length+", ошибок "+sourceErrors.length+"."+(report.reversed?" Направление пар развёрнуто.":"");
   const unfinished=editorOpen&&formPending(),disabled=busy||unfinished||!!sourceErrors.length||!!state.errors.length||(!rows.length&&!state.savedCount);
   apply.disabled=topApply.disabled=disabled;apply.textContent="Применить и обновить сравнение ("+state.selectedCount+")";topApply.textContent=busy?"Обновляем сравнение…":"Применить ("+state.selectedCount+")";
   all.disabled=none.disabled=add.disabled=busy||editorOpen;cancel.disabled=busy;
   notice.hidden=!state.dirty&&!unfinished;
   noticeText.textContent=unfinished?"Редактирование пары ещё не завершено. Сохраните пару в черновик или отмените её правку.":"Черновик ещё не применён. Сравнение ниже использует "+state.savedCount+" сохранённых пар; в черновике выбрано "+state.selectedCount+". Нажмите «Применить», чтобы обновить результат.";
  }
  function refresh(reset=false){
   redraw(reset);conflictsHost.replaceChildren();const conflicts=draft.conflicts;
   if(conflicts.length){const details=el("details","mapping-conflicts");details.append(el("summary","","Требуют ручного решения · "+conflicts.length),el("p","meta","Эти пары автоматически не выбраны. Можно добавить нужную связь кнопкой «Добавить соответствие»."));paginated(details,()=>conflicts,["Путь «Было»","Путь «Стало»","Причина"],p=>{const row=el("tr");row.append(el("td","",p.beforePath),el("td","",p.afterPath),el("td","",p.reason));return row});conflictsHost.append(details)}
   update();
  }
  function picker(caption,fields,current,side,searchValue=""){
   const box=el("div","mapping-picker"),label=el("label","",caption),query=el("input","mapping-tag-search-"+side),select=el("select","mapping-tag-choice-"+side),path=el("code","mapping-picked-path"),description=el("p","meta mapping-picked-description"),index=new Map(fields.map(f=>[f.path,f]));
   query.type="search";query.value=searchValue;query.placeholder="Поиск по пути или описанию";query.setAttribute("aria-label","Поиск тега «"+caption+"»");select.size=6;select.setAttribute("aria-label","Тег «"+caption+"»");let chosen=current||"",filtered=[];
   function describe(){path.textContent=chosen;description.textContent=index.get(chosen)?.doc||"Описание не указано"}
   function draw(){
    const terms=normalized(query.value).split(/\s+/).filter(Boolean),matches=fields.filter(f=>terms.every(t=>normalized(f.path+" "+(f.doc||"")).includes(t)));
    filtered=[...matches];select.replaceChildren();const placeholder=el("option","",fields.length?"Выберите XML-тег":"Свободных тегов нет");placeholder.value="";select.append(placeholder);
    if(chosen&&index.has(chosen)&&!matches.some(f=>f.path===chosen))matches.unshift(index.get(chosen));
    for(const f of matches){const option=el("option","",f.path+(f.doc?" — "+f.doc.replace(/\s+/g," ").slice(0,100):""));option.value=f.path;select.append(option)}select.value=chosen;describe();
   }
   query.oninput=draw;query.onkeydown=event=>{if(event.key==="Enter"){event.preventDefault();if(filtered.length===1){chosen=filtered[0].path;draw();update()}select.focus?.()}};select.onchange=()=>{chosen=select.value;describe();update()};label.append(query,select);box.append(label,path,description);draw();return {box,query,value:()=>chosen};
  }
  function closeEditor(){editorOpen=false;editor.hidden=true;editor.replaceChildren();refresh()}
  function openEditor(id=null,savedPair=null,searches={}){
   if(editorOpen||busy||closed||!options.isCurrent())return;
   const row=id===null?null:draft.rows.find(r=>r.id===id);if(id!==null&&!row)return;
   editorOpen=true;editor.hidden=false;editor.replaceChildren();
   const occupied=draft.rows.filter(r=>r.selected&&r.id!==id),available=(fields,key,current)=>{const used=new Set(occupied.map(r=>r[key]));return fields.filter(f=>f.path===current||!used.has(f.path))};
   const form=el("form","mapping-edit-form"),title=el("h3","",row?"Изменить соответствие":"Добавить соответствие"),columns=el("div","mapping-pickers"),before=picker("Было",available(options.before,"beforePath",row?.beforePath),row?.beforePath,"before",searches.before),after=picker("Стало",available(options.after,"afterPath",row?.afterPath),row?.afterPath,"after",searches.after);columns.append(before.box,after.box);
   const label=el("label","","Комментарий"),comment=el("textarea","mapping-edit-comment");comment.rows=3;comment.value=row?.comment||"";comment.oninput=update;label.append(comment);formPending=()=>!!(before.value()||after.value()||comment.value);
   const error=el("p","mapping-errors mapping-editor-error"),save=button("open mapping-editor-save","Сохранить пару в черновик"),back=button("swap mapping-editor-cancel",savedPair?"Закрыть форму":"Отмена"),actions=el("div","mapping-controls");error.setAttribute("role","alert");actions.append(save,back);
   function commit(event){event?.preventDefault?.();try{const pair={beforePath:before.value(),afterPath:after.value(),comment:comment.value};row?draft.edit(row.id,pair):draft.add(pair);editorOpen=false;openEditor(null,pair,{before:before.query.value,after:after.query.value})}catch(e){error.textContent=e.message}}
   save.type="submit";form.onsubmit=commit;save.onclick=event=>commit(event);back.onclick=closeEditor;if(savedPair){const feedback=el("p","mapping-editor-feedback","Пара сохранена в черновик: "+savedPair.beforePath+" → "+savedPair.afterPath+". Выберите следующую пару или примените черновик.");feedback.setAttribute("role","status");form.append(feedback)}form.append(title,el("p","meta","Уже сопоставленные теги скрыты. При изменении пары доступны её текущие теги."),columns,label,error,actions);editor.append(form);refresh();editor.scrollIntoView?.({block:"nearest"});(row?after.query:before.query).focus?.();
  }
  all.onclick=()=>{draft.selectProposals(true);refresh()};none.onclick=()=>{draft.selectProposals(false);refresh()};add.onclick=()=>openEditor();
  search.oninput=()=>redraw(true);only.onchange=()=>redraw(true);
  async function applyDraft(){
   if(busy||(editorOpen&&formPending())||closed)return;if(!options.isCurrent()){validation.textContent="Выбранная пара редакций изменилась. Откройте предпросмотр снова.";return}
   const state=draft.summary();if(sourceErrors.length||state.errors.length)return;busy=true;refresh();
   try{await onApply(state.pairs);notice.hidden=true}catch(error){busy=false;refresh();validation.textContent="Не удалось обновить сравнение. "+error.message}
  }
  apply.onclick=topApply.onclick=applyDraft;
  cancel.onclick=()=>{if(!busy){closed=true;notice.hidden=true;onCancel()}};
  refresh();return {openEditor,hasUnapplied:()=>editorOpen||draft.summary().dirty,destroy(){closed=true;notice.hidden=true}};
 }
 return {mount};
});
