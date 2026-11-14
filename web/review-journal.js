(function(root){
 'use strict';
 const envNames={prod:'Продуктивный',test:'Тестовый'};
 function documentName(r){
  if(!r.document_url)return r.manual_source?.filename||r.document_filename||'Загруженная спецификация';
  return decodeURIComponent(new URL(r.document_url).pathname.split('/').pop());
 }
 function collect(catalog){
  const rows=[],seen=new Set();
  for(const revision of catalog.revisions){
   for(const finding of revision.schema_review?.findings||[]){
    const identity=JSON.stringify([revision.number,revision.environment,revision.document_sha256||revision.document_url||revision.revision_id,revision.dependency_digest||revision.archive_sha256,finding]);
    if(seen.has(identity))continue;
    seen.add(identity);rows.push({revision,finding});
   }
  }
  return rows.sort((a,b)=>Number(a.revision.number)-Number(b.revision.number)||a.revision.environment.localeCompare(b.revision.environment)||documentName(a.revision).localeCompare(documentName(b.revision),'ru'));
 }
 function fieldLink(revision,path){
  return '#'+new URLSearchParams({message:revision.number,env:revision.environment,rev:revision.revision_id,field:path});
 }
 function fieldLinks(revision,finding){
  return (finding.field_paths||[]).map(path=>({path,href:fieldLink(revision,path),text:(finding.field_link_scope==='parent'?'Открыть родительский блок':'Открыть поле XSD')+' · '+path}));
 }
 function filter(rows,number='',env=''){
  const query=number.trim();
  return rows.filter(({revision:r})=>(!query||r.number===query)&&(!env||r.environment===env));
 }
 function link(text,url,baseURL){
  if(!url)return text;
  const resolved=new URL(url,baseURL);
  return ['https:','http:','file:'].includes(resolved.protocol)?{text,hyperlink:resolved.href}:text;
 }
 function schemaSources(r){
  const origin=r.archive_url?.split('#')[0],service=r.schema_source_url;
  const file=r.schema_path?.split('/').pop();
  const digest=r.dependencies?.find(([name])=>name===file)?.[1];
  const remote=r.schema_source_type==='wsdl'?r.schema_sources?.find(s=>digest&&s.kind==='xsd'&&s.sha256===digest):null;
  const name=url=>decodeURIComponent(new URL(url).pathname.split('/').pop());
  const embedded=r.schema_source_type==='wsdl_bridge'||r.archive_url?.includes('#embedded=');
  const manual=origin?.startsWith('manual:');
  const publication=r.schema_source_type==='wsdl'
   ?{text:'WSDL шлюза СФР',url:service}
   :{text:origin?(manual?'Загруженный архив ':embedded?'Документ ':'Архив ')+name(origin):r.manual_source?.label||'Источник не указан',url:manual?undefined:origin};
  return {
   page:{text:r.schema_publication_page?'Страница СФР ('+new URL(r.schema_publication_page).host+new URL(r.schema_publication_page).pathname+')':'Страница публикации не сохранена',url:r.schema_publication_page},
   publication,
   container:embedded?(r.archive_url.includes('#schema-')?'Схема в тексте документа':r.schema_source_type==='wsdl_bridge'?'Вложенная XSD':r.archive_name||'Вложенная XSD'):'',
   file:{text:r.schema_path||'Файл XSD не указан',url:remote?.url||r.schema_file_download},
   wsdl:r.schema_source_type==='wsdl_bridge'?{text:service||'Источник зависимостей не указан',url:service}:null
  };
 }
 function createWorkbook(ExcelJS,rows,{number='',env='',baseURL,createdAt=new Date()}={}){
  const workbook=new ExcelJS.Workbook();workbook.creator='Справочник СЭДО';workbook.created=createdAt;
  const sheet=workbook.addWorksheet('Замечания',{views:[{state:'frozen',ySplit:1}]});
  const columns=[
   ['Сообщение',14],['Контур',20],['Версия документа',18],['Спецификация',65],
   ['Замечание',28],['Место в спецификации',60],['В спецификации',65],
   ['Место в XSD',60],['В XSD',65],['Последствие',60],['Страница публикации СФР',65],
   ['Архив, документ или WSDL с XSD',65],['Файл XSD',65],['Зависимости WSDL',65],
   ['Открыть сообщение',32],['Дата спецификации',22],['Расположение схемы в документе',42]
  ];
  sheet.columns=columns.map(([header,width])=>({header,width}));
  for(const {revision:r,finding:f} of rows){
   const sources=schemaSources(r),cell=s=>s?link(s.text,s.url,baseURL):'';
   const values=[r.number,envNames[r.environment],r.document_version||'',link(documentName(r),r.document_url||r.document_download,baseURL),
    f.kind,f.location,f.spec,f.xlocation,f.xsd,f.impact,
    cell(sources.page),cell(sources.publication),cell(sources.file),cell(sources.wsdl),
    link('Сообщение '+r.number,'index.html#'+new URLSearchParams({message:r.number,env:r.environment,rev:r.revision_id}),baseURL),
    r.label_date||'',sources.container];
   for(const value of values)if(typeof value==='string'&&value.length>32767)throw Error('Текст превышает лимит ячейки Excel. Выгрузка остановлена без обрезки.');
   const row=sheet.addRow(values);
   row.alignment={vertical:'top',wrapText:true};
  }
  sheet.autoFilter={from:{row:1,column:1},to:{row:Math.max(1,sheet.rowCount),column:columns.length}};
  sheet.getRow(1).font={bold:true,color:{argb:'FFFFFFFF'}};
  sheet.getRow(1).fill={type:'pattern',pattern:'solid',fgColor:{argb:'FF165A56'}};
  sheet.getRow(1).alignment={wrapText:true};sheet.getRow(1).height=32;
  const info=workbook.addWorksheet('О журнале');info.columns=[{width:28},{width:110}];
  info.addRows([
   ['Номер сообщения',number||'Все сообщения'],['Контур',envNames[env]||'Все контуры'],['Записей',rows.length],
   ['Охват','Все сохранённые редакции. Одна строка — замечание к определённой спецификации и её комплекту XSD своего контура.'],
   ['Обновление','Замечания пересчитываются при каждой сборке справочника по исходным DOCX и XSD.'],
   ['Объём проверки','Автоматически сопоставимые таблицы и XML-примеры. Подробности охвата доступны в карточке сообщения.'],
   ['Сформировано UTC',createdAt.toISOString()]
  ]);info.eachRow(row=>{row.alignment={wrapText:true,vertical:'top'}});
  return workbook;
 }
 function mount(container,catalog,params){
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
  container.replaceChildren(el('h1','Журнал замечаний'));
  container.append(el('p','Замечания по всем сохранённым редакциям. Пересчитываются при каждой сборке по спецификациям и XSD соответствующего контура.'));
  const controls=el('div',undefined,'searchbar'),label=el('label','Номер сообщения'),input=el('input');
  input.type='search';input.inputMode='numeric';input.placeholder='Все сообщения';input.value=params.get('number')||'';input.id='review-number';label.append(input);
  const envLabel=el('label','Контур'),select=el('select');select.id='review-env';
  for(const [value,text] of [['','Все контуры'],['prod','Продуктивный'],['test','Тестовый']]){
   const option=el('option',text);option.value=value;select.append(option);
  }
  select.value=params.get('env')||'';envLabel.append(select);controls.append(label,envLabel);
  const button=el('button','Скачать Excel','open');button.type='button';button.id='review-export';
  const status=el('p','','meta');status.setAttribute('role','status');
  const result=el('div',undefined,'review-results');container.append(controls,button,status,result);
  const all=collect(catalog);let visible=[];
  function render(){
   visible=filter(all,input.value,select.value);result.replaceChildren();
   status.textContent='Замечаний: '+visible.length;button.disabled=!visible.length;
   const route=new URLSearchParams({remarks:'1'});
   if(input.value.trim())route.set('number',input.value.trim());
   if(select.value)route.set('env',select.value);
   history.replaceState(null,'','#'+route);
   if(!visible.length){result.append(el('p','По выбранным фильтрам замечаний нет. Объём выполненной сверки указан в карточках сообщений.'));return}
   const table=el('table'),head=el('thead'),header=el('tr');
   for(const text of ['Сообщение','Спецификация','Замечание','В спецификации','В XSD','Последствие']){const th=el('th',text);th.scope='col';header.append(th)}
   head.append(header);table.append(head);const body=el('tbody');
   for(const {revision:r,finding:f} of visible){
    const row=el('tr'),identity=el('td'),a=el('a',r.number+' · '+envNames[r.environment]);
    a.href='#'+new URLSearchParams({message:r.number,env:r.environment,rev:r.revision_id});identity.append(a);
    const source=el('td'),doc=el(r.document_url||r.document_download?'a':'span',documentName(r));
    if(doc.tagName==='A'){doc.href=r.document_url||r.document_download;doc.target='_blank';doc.rel='noopener'}
    source.append(doc,el('p','Версия '+(r.document_version||'не указана')+(r.label_date?' · '+r.label_date:''),'meta'));
    const spec=el('td'),schema=el('td');spec.append(el('p',f.spec),el('p',f.location,'meta'));schema.append(el('p',f.xsd),el('p',f.xlocation,'meta'));
    const chain=el('p','Первоисточник XSD · ','meta review-source-chain'),sources=schemaSources(r);
    const steps=[sources.page,sources.publication,...(sources.container?[{text:sources.container}]:[]),sources.file];
    for(const [index,step] of steps.entries()){
     if(index)chain.append(document.createTextNode(' → '));
     const resolved=link(step.text,step.url,location.href);
     const item=el(typeof resolved==='object'?'a':'span',step.text);
     if(typeof resolved==='object'){item.href=resolved.hyperlink;item.target='_blank';item.rel='noopener'}
     chain.append(item);
    }
    if(sources.wsdl){
     chain.append(document.createTextNode(' · Зависимости из '));
     const resolved=link('WSDL шлюза СФР',sources.wsdl.url,location.href);
     const item=el(typeof resolved==='object'?'a':'span','WSDL шлюза СФР');
     if(typeof resolved==='object'){item.href=resolved.hyperlink;item.target='_blank';item.rel='noopener'}
     chain.append(item);
    }
    schema.append(chain);
    for(const target of fieldLinks(r,f)){const a=el('a',target.text,'source');a.href=target.href;schema.append(el('br'),a)}
    row.append(identity,source,el('td',f.kind),spec,schema,el('td',f.impact));body.append(row);
   }
   table.append(body);result.append(table);
  }
  input.oninput=render;select.onchange=render;
  button.onclick=async()=>{
   const snapshot=visible.slice(),number=input.value.trim(),env=select.value;button.disabled=true;
   try{
    const ExcelJS=await root.SedoComparisonExport.loadLibrary();
    const workbook=createWorkbook(ExcelJS,snapshot,{number,env,baseURL:location.href});
    const buffer=await workbook.xlsx.writeBuffer(),url=URL.createObjectURL(new Blob([buffer],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
    const a=el('a');a.href=url;a.download='sedo-remarks-'+(number||'all')+'-'+(env||'all')+'.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    status.textContent='В Excel выгружено замечаний: '+snapshot.length;
   }catch(error){status.textContent='Не удалось выгрузить Excel. '+error.message}
   finally{button.disabled=!visible.length}
  };
  render();
 }
 const api={collect,filter,createWorkbook,mount,schemaSources,fieldLinks};
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.SedoReviewJournal=api;
})(typeof window==='undefined'?globalThis:window);
