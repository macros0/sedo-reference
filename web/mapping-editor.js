/* Browser UI for an editable, unapplied mapping draft. */
(function(root,factory){"use strict";const api=factory();if(typeof module==="object"&&module.exports)module.exports=api;else root.SedoMappingEditor=api})(typeof globalThis!=="undefined"?globalThis:this,function(){
 "use strict";
 const normalized=v=>String(v||"").normalize("NFKC").toLocaleLowerCase("ru").replace(/ё/g,"е");
 function mount({container,notice,status,options,report,draft,session,manualLabel,panelAdd,onApply,onCancel}){
  const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e};
  const button=(cls,text)=>{const e=el("button",cls,text);e.type="button";return e};
  const old=new Map(options.before.map(f=>[f.path,f])),next=new Map(options.after.map(f=>[f.path,f]));
  let editorOpen=false,busy=false,closed=false,redraw=()=>{};
  const canEdit=()=>!busy&&!closed&&options.isCurrent();
  function formPending(){
   const form=session.form;
   return !!form&&(form.id!==null||!!(form.beforePath||form.afterPath||form.comment));
  }
  function unfinishedMessage(form){
   if(!form.beforePath&&!form.afterPath)return "Выберите оба тега и сохраните пару в черновик.";
   if(!form.beforePath)return "Выберите тег «Было» и сохраните пару в черновик.";
   if(!form.afterPath)return "Выберите тег «Стало» и сохраните пару в черновик.";
   return "Сохраните текущую пару в черновик.";
  }
  container.replaceChildren();notice.replaceChildren();
  const noticeText=el("span","mapping-draft-message"),cancelEdit=button("swap mapping-cancel-edit-top","Отменить правку пары"),topApply=button("open mapping-apply-top","Применить и обновить сравнение");
  notice.append(noticeText,cancelEdit,topApply);notice.setAttribute("role","status");
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
    const focused=document.activeElement;
    if(reset)page=0;const rows=getRows(),visible=rows.filter(predicate),pages=Math.max(1,Math.ceil(visible.length/50));page=Math.min(Math.max(0,page),pages-1);body.replaceChildren();visible.slice(page*50,(page+1)*50).forEach(row=>body.append(makeRow(row)));
    if(!visible.length){const row=el("tr"),cell=el("td","empty","По этому поиску и фильтру пар не найдено.");cell.colSpan=captions.length;row.append(cell);body.append(row)}
    previous.disabled=page===0;nextPage.disabled=page===pages-1;previous.hidden=nextPage.hidden=pages===1;caption.textContent=visible.length?"Строки "+(page*50+1)+"–"+Math.min((page+1)*50,visible.length)+" из "+visible.length+" · всего пар "+rows.length+" · страница "+(page+1)+" из "+pages:"Найдено пар: 0 из "+rows.length;
    if((focused===previous||focused===nextPage)&&(focused.disabled||focused.hidden)){
     const other=focused===previous?nextPage:previous;
     const target=!other.disabled&&!other.hidden?other:body.querySelector("input:not(:disabled),button:not(:disabled)")||search;
     target.focus();
    }
   }
   previous.onclick=()=>{if(canEdit()){page--;draw()}};nextPage.onclick=()=>{if(canEdit()){page++;draw()}};draw();return draw;
  }
  const matches=row=>{
   if(only.checked&&!draft.isProposal(row))return false;
   const haystack=normalized([row.beforePath,row.afterPath,old.get(row.beforePath)?.name,next.get(row.afterPath)?.name,old.get(row.beforePath)?.doc,next.get(row.afterPath)?.doc,row.relativePath,row.parentBeforePath,row.parentAfterPath,row.comment,row.decision].join(" "));
   return normalized(search.value).split(/\s+/).filter(Boolean).every(t=>haystack.includes(t));
  };
  const primary=el("div","mapping-table");container.append(primary);
  function changeRow(action,selector,change){
   if(!canEdit())return;
   const focused=document.activeElement===action;
   const index=focused?[...primary.querySelectorAll(selector)].indexOf(action):-1;
   change();refresh();
   if(focused){
    const actions=[...primary.querySelectorAll(selector)];
    const target=actions.find(button=>button.dataset.rowId===action.dataset.rowId)||actions[Math.min(index,actions.length-1)]||search;
    target.focus({preventScroll:true});
   }
  }
  redraw=paginated(primary,()=>draft.rows,["Применить","Источник","Путь и описание «Было»","Путь и описание «Стало»","Комментарий / основание","Действия"],row=>{
   const tr=el("tr"),choice=el("td"),check=el("input",draft.isProposal(row)?"mapping-proposal":"mapping-explicit");check.type="checkbox";check.checked=row.selected;check.disabled=editorOpen||busy;check.dataset.beforePath=row.beforePath;check.dataset.afterPath=row.afterPath;check.setAttribute("aria-label","Сопоставить "+row.beforePath+" → "+row.afterPath);
   check.onchange=()=>changeRow(check,'input[type="checkbox"]',()=>draft.toggle(row.id,check.checked));choice.append(check);
   const label=row.origin==="confirmed"?manualLabel:row.origin==="workbook"?"Из Excel — не подтверждено":row.origin==="relative"?"По относительному пути":"Изменено в браузере";
   const reason=row.origin==="relative"?"Относительный путь: "+row.relativePath+"\nРодитель: "+row.parentBeforePath+" → "+row.parentAfterPath:row.origin==="workbook"?"Строка Excel "+row.row+" · решение «"+row.decision+"». "+row.comment:row.comment;
   const actions=el("td","mapping-row-actions"),edit=button("swap mapping-edit-row","Изменить"),remove=button("swap mapping-remove-row","Убрать");edit.disabled=remove.disabled=editorOpen||busy;
   check.dataset.rowId=edit.dataset.rowId=remove.dataset.rowId=String(row.id);edit.onclick=()=>openEditor(row.id);remove.onclick=()=>changeRow(remove,".mapping-remove-row",()=>draft.remove(row.id));actions.append(edit,remove);
   tr.append(choice,el("td","",label),el("td","",row.beforePath+"\n"+(old.get(row.beforePath)?.doc||"")),el("td","",row.afterPath+"\n"+(next.get(row.afterPath)?.doc||"")),el("td","",reason),actions);return tr;
  },matches);
  container.append(el("p","meta","Поиск и фильтр меняют только список. Применяются все отмеченные пары, включая скрытые фильтром."));
  const conflictsHost=el("div","mapping-conflicts-host");container.append(conflictsHost);
  const apply=button("open mapping-apply","Применить и обновить сравнение"),cancel=button("swap mapping-discard","Отменить предпросмотр"),footer=el("div","mapping-controls");footer.append(apply,cancel);container.append(footer,el("p","meta","Правки пока находятся в черновике. Основная кнопка сохранит выбранные пары в браузере и обновит сравнение. Схемы и исходный Excel не изменяются."));
  function update(){
   const state=draft.summary(),rows=draft.rows,proposed=rows.filter(draft.isProposal),fixed=rows.filter(r=>!draft.isProposal(r)&&r.selected).length;
   selection.textContent="К применению: подтверждённых пар "+fixed+", предложений "+proposed.filter(r=>r.selected).length+" из "+proposed.length;
   validation.textContent=state.errors.join("\n");
   status.textContent="Сохранено пар: "+state.savedCount+". В предпросмотре выбрано: "+state.selectedCount+". Предложено из Excel "+rows.filter(r=>r.origin==="workbook").length+", предложено вложенных "+rows.filter(r=>r.origin==="relative").length+", ошибок "+sourceErrors.length+"."+(report.reversed?" Направление пар развёрнуто.":"");
   const excluded=(report.deleted?.before.length||0)+(report.deleted?.after.length||0);
   if(excluded)status.textContent+=" Исключено из автосопоставления: "+excluded+".";
   const unfinished=editorOpen&&formPending(),disabled=busy||unfinished||!!sourceErrors.length||!!state.errors.length||(!rows.length&&!state.savedCount&&!state.dirty);
   apply.disabled=topApply.disabled=disabled;apply.textContent="Применить и обновить сравнение ("+state.selectedCount+")";topApply.textContent=busy?"Обновляем сравнение…":"Применить ("+state.selectedCount+")";
   all.disabled=none.disabled=add.disabled=busy||editorOpen;cancel.disabled=busy;
   cancelEdit.hidden=!unfinished;cancelEdit.disabled=busy;
   notice.hidden=!state.dirty&&!unfinished;
   noticeText.textContent=unfinished?unfinishedMessage(session.form):"Черновик ещё не применён. Сравнение ниже использует "+state.savedCount+" сохранённых пар; в черновике выбрано "+state.selectedCount+". Нажмите «Применить», чтобы обновить результат.";
  }
  function refresh(reset=false){
   redraw(reset);conflictsHost.replaceChildren();const conflicts=draft.conflicts;
   if(conflicts.length){const details=el("details","mapping-conflicts");details.append(el("summary","","Требуют ручного решения · "+conflicts.length),el("p","meta","Эти пары автоматически не выбраны. Можно добавить нужную связь кнопкой «Добавить соответствие»."));paginated(details,()=>conflicts,["Путь «Было»","Путь «Стало»","Причина"],p=>{const row=el("tr");row.append(el("td","",p.beforePath),el("td","",p.afterPath),el("td","",p.reason));return row});conflictsHost.append(details)}
   update();
  }
  function picker(caption,fields,current,side,searchValue,onChange){
   const box=el("div","mapping-picker"),label=el("label","",caption),query=el("input","mapping-tag-search-"+side),select=el("select","mapping-tag-choice-"+side),path=el("code","mapping-picked-path"),description=el("p","meta mapping-picked-description"),index=new Map(fields.map(f=>[f.path,f]));
   query.type="search";query.value=searchValue;query.placeholder="Поиск по пути или описанию";query.setAttribute("aria-label","Поиск тега «"+caption+"»");select.size=6;select.setAttribute("aria-label","Тег «"+caption+"»");let chosen=current||"",queryText=searchValue,filtered=[];
   function describe(){path.textContent=chosen;description.textContent=index.get(chosen)?.doc||"Описание не указано"}
   function draw(){
    const terms=normalized(query.value).split(/\s+/).filter(Boolean),matches=fields.filter(f=>terms.every(t=>normalized(f.path+" "+(f.doc||"")).includes(t)));
    filtered=[...matches];select.replaceChildren();const placeholder=el("option","",fields.length?"Выберите XML-тег":"Свободных тегов нет");placeholder.value="";select.append(placeholder);
    if(chosen&&index.has(chosen)&&!matches.some(f=>f.path===chosen))matches.unshift(index.get(chosen));
    for(const f of matches){const option=el("option","",f.path+(f.doc?" — "+f.doc.replace(/\s+/g," ").slice(0,100):""));option.value=f.path;select.append(option)}select.value=chosen;describe();
   }
   query.oninput=()=>{
    if(!canEdit()){query.value=queryText;return}
    queryText=query.value;draw();onChange();
   };
   query.onkeydown=event=>{
    if(event.key!=="Enter"||!canEdit())return;
    event.preventDefault();
    if(filtered.length===1){chosen=filtered[0].path;draw();onChange()}
    select.focus?.();
   };
   select.onchange=()=>{
    if(!canEdit()){select.value=chosen;return}
    chosen=select.value;describe();onChange();
   };
   label.append(query,select);box.append(label,path,description);draw();return {box,query,value:()=>chosen};
  }
  function closeEditor(){
   if(!canEdit())return;
   session.form=null;editorOpen=false;editor.hidden=true;editor.replaceChildren();refresh();
   const target=!notice.hidden&&!topApply.disabled?topApply:panelAdd;
   target.focus?.();
  }
  function openEditor(id=null,initial={},focus=true){
   if(editorOpen||!canEdit())return;
   const row=id===null?null:draft.rows.find(r=>r.id===id);if(id!==null&&!row)return;
   editorOpen=true;editor.hidden=false;editor.replaceChildren();
   const {savedPair,beforeSearch="",afterSearch=""}=initial,beforePath=initial.beforePath??row?.beforePath,afterPath=initial.afterPath??row?.afterPath;
   const occupied=draft.rows.filter(r=>r.selected&&r.id!==id),available=(fields,key,current)=>{const used=new Set(occupied.map(r=>r[key]));return fields.filter(f=>f.path===current||!used.has(f.path))};
   const form=el("form","mapping-edit-form"),title=el("h3","",row?"Изменить соответствие":"Добавить соответствие"),columns=el("div","mapping-pickers"),before=picker("Было",available(options.before,"beforePath",beforePath),beforePath,"before",beforeSearch,onInput),after=picker("Стало",available(options.after,"afterPath",afterPath),afterPath,"after",afterSearch,onInput);columns.append(before.box,after.box);
   const label=el("label","","Комментарий"),comment=el("textarea","mapping-edit-comment");comment.rows=3;comment.value=initial.comment??row?.comment??"";label.append(comment);
   comment.oninput=()=>{if(canEdit())onInput();else comment.value=session.form?.comment||""};
   function remember(){session.form={id,beforePath:before.value(),afterPath:after.value(),beforeSearch:before.query.value,afterSearch:after.query.value,comment:comment.value,savedPair}}
   function onInput(){remember();update()}
   const error=el("p","mapping-errors mapping-editor-error"),save=button("open mapping-editor-save","Сохранить пару в черновик"),back=button("swap mapping-editor-cancel",savedPair?"Закрыть форму":"Отмена"),actions=el("div","mapping-controls");error.setAttribute("role","alert");actions.append(save,back);
   function commit(event){
    event.preventDefault();
    if(!canEdit())return;
    try{
     const pair={beforePath:before.value(),afterPath:after.value(),comment:comment.value};
     row?draft.edit(row.id,pair):draft.add(pair);
     editorOpen=false;
     openEditor(null,{savedPair:{beforePath:pair.beforePath,afterPath:pair.afterPath},beforeSearch:before.query.value,afterSearch:after.query.value});
    }catch(e){error.textContent=e.message}
   }
   save.type="submit";form.onsubmit=commit;back.onclick=closeEditor;if(savedPair){const feedback=el("p","mapping-editor-feedback","Пара сохранена в черновик: "+savedPair.beforePath+" → "+savedPair.afterPath+". Выберите следующую пару или примените черновик.");feedback.setAttribute("role","status");form.append(feedback)}form.append(title,el("p","meta","Уже сопоставленные теги скрыты. При изменении пары доступны её текущие теги."),columns,label,error,actions);editor.append(form);remember();refresh();
   if(focus){editor.scrollIntoView?.({block:"nearest"});(row?after.query:before.query).focus?.()}
  }
  all.onclick=()=>{if(canEdit()){draft.selectProposals(true);refresh()}};
  none.onclick=()=>{if(canEdit()){draft.selectProposals(false);refresh()}};
  add.onclick=()=>openEditor();
  search.oninput=only.onchange=()=>{if(canEdit())redraw(true)};
  async function applyDraft(){
   if(busy||(editorOpen&&formPending())||closed)return;if(!options.isCurrent()){validation.textContent="Выбранная пара редакций изменилась. Откройте предпросмотр снова.";return}
   const state=draft.summary();if(sourceErrors.length||state.errors.length)return;
   const focused=document.activeElement;
   try{await onApply(state.pairs)}catch(error){
    if(closed||!options.isCurrent())return;
    validation.textContent="Не удалось обновить сравнение. "+error.message;
    if(document.activeElement===document.body)focused?.focus?.({preventScroll:true});
   }
  }
  apply.onclick=topApply.onclick=applyDraft;
  cancelEdit.onclick=closeEditor;
  cancel.onclick=()=>{if(!busy&&!closed)onCancel()};
  if(session.form)openEditor(session.form.id,session.form,false);else refresh();
  return {
   openEditor,
   hasUnapplied:()=>editorOpen||draft.summary().dirty,
   setApplying(applying){if(!closed&&busy!==applying){busy=applying;refresh()}},
   destroy(){closed=true;notice.hidden=true}
  };
 }
 return {mount};
});
