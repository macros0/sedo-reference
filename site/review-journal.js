(function(root){
 'use strict';
 const envNames={prod:'Продуктивный',test:'Тестовый'};
 function sourceFilename(url,fallback=''){
  if(!url)return fallback;
  let name;
  try{name=new URL(url).pathname.split('/').pop()||fallback}catch{return fallback}
  try{return decodeURIComponent(name)}catch{return name}
 }
 function documentName(r){
  return sourceFilename(r.document_url,r.manual_source?.filename||r.document_filename||r.manual_source?.label||'Загруженная спецификация');
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
 function legacySpecificationKey(r){return r.document_url||r.document_download||r.document_sha256||r.revision_id}
 function specificationKey(r){return r.document_sha256?JSON.stringify([legacySpecificationKey(r),r.document_sha256]):legacySpecificationKey(r)}
 function legacyXsdKey(r,sources){
  return JSON.stringify([sources.publication.url||r.archive_url||r.manual_source?.label||'',sources.file.text]);
 }
 function versionedXsdKey(r,sources){
  const legacy=legacyXsdKey(r,sources);
  return sources.file.sha256?JSON.stringify([JSON.parse(legacy),sources.file.sha256]):legacy;
 }
 function xsdKey(r,f){return versionedXsdKey(r,schemaSources(r,f))}
 function sourceOptions(rows){
  const specifications=new Map(),schemas=new Map();
  for(const {revision:r,finding:f} of rows){
   specifications.set(specificationKey(r),[documentName(r),envNames[r.environment],r.document_version&&'версия '+r.document_version,r.document_sha256?.slice(0,8)].filter(Boolean).join(' · '));
   for(const sources of schemaChains(r,f)){
    schemas.set(versionedXsdKey(r,sources),[sources.file.text,sources.publication.text,sources.file.sha256?.slice(0,8),envNames[r.environment]].filter(Boolean).join(' · '));
   }
  }
  const sorted=map=>[...map].map(([value,text])=>({value,text})).sort((a,b)=>a.text.localeCompare(b.text,'ru'));
  return {specifications:sorted(specifications),schemas:sorted(schemas)};
 }
 function filter(rows,number='',env='',specification='',xsd=''){
  const query=number.trim();
  return rows.filter(({revision:r,finding:f})=>(!query||r.number===query)&&(!env||r.environment===env)
   &&(!specification||specificationKey(r)===specification||legacySpecificationKey(r)===specification)
   &&(!xsd||schemaChains(r,f).some(s=>versionedXsdKey(r,s)===xsd||legacyXsdKey(r,s)===xsd)));
 }
 function link(text,url,baseURL){
  if(!url)return text;
  const resolved=new URL(url,baseURL);
  return ['https:','http:','file:'].includes(resolved.protocol)?{text,hyperlink:resolved.href}:text;
 }
 function schemaSources(r,finding){return schemaChains(r,finding)[0]}
 function remoteSchemaSource(r,path,digest){
  const matches=r.schema_sources?.filter(s=>digest&&s.kind==='xsd'&&s.sha256===digest)||[];
  const exact=matches.filter(s=>s.archive_path===path);
  if(exact.length===1)return exact[0];
  if(matches.length===1&&matches[0].archive_path==null)return matches[0];
 }
 function schemaChains(r,finding){
  const selections=[finding?.xsd_source,...(finding?.xsd_sources||[])].filter(source=>source?.path);
  const chains=selections.length?selections.map(source=>schemaChain(r,finding,source)):[schemaChain(r,finding)];
  const seen=new Set();
  return chains.filter(source=>{
   const key=versionedXsdKey(r,source);
   if(seen.has(key))return false;
   seen.add(key);return true;
  });
 }
 function schemaChain(r,finding,explicit){
  const origin=r.archive_url?.split('#')[0],service=r.schema_source_url;
  const location=finding?.xlocation||'';
  const files=r.schema_files||[];
  let compared=explicit?files.find(s=>s.path===explicit.path&&(!explicit.sha256||s.sha256===explicit.sha256)):files.find(s=>location.includes(s.path));
  if(!compared&&!explicit){
   const candidates=files.filter(s=>location.includes(s.path.split('/').pop()));
   if(candidates.length===1)compared=candidates[0];
  }
  const path=explicit?.path||compared?.path||r.schema_path;
  const file=path?.split('/').pop();
  const digest=explicit?.sha256||compared?.sha256||r.dependencies?.find(([name])=>name===file)?.[1];
  const remote=['wsdl','wsdl_bridge'].includes(r.schema_source_type)?remoteSchemaSource(r,path,digest):null;
  const embedded=r.schema_source_type==='wsdl_bridge'||r.archive_url?.includes('#embedded=');
  const manual=origin?.startsWith('manual:');
  const publication=r.schema_source_type==='wsdl'||remote
   ?{text:'WSDL шлюза СФР',url:service}
   :{text:origin?(manual?'Загруженный архив ':embedded?'Документ ':'Архив ')+sourceFilename(origin,origin):r.manual_source?.label||'Источник не указан',url:manual?undefined:origin};
  return {
   page:{text:r.schema_publication_page?'Страница СФР ('+new URL(r.schema_publication_page).host+new URL(r.schema_publication_page).pathname+')':'Страница публикации не сохранена',url:r.schema_publication_page},
   publication,
   container:embedded&&!remote?(r.archive_url?.includes('#schema-')?'Схема в тексте документа':r.schema_source_type==='wsdl_bridge'?'Вложенная XSD':r.archive_name||'Вложенная XSD'):'',
   file:{text:path||'Файл XSD не указан',sha256:digest,url:explicit?.download||compared?.download||(digest&&/^[0-9a-f]{64}$/.test(digest)?'sources/'+digest+'.xsd':path===r.schema_path?r.schema_file_download:undefined)},
   official:explicit?.url||remote?.url?{text:'Официальная публикация XSD',url:explicit?.url||remote?.url}:null,
   wsdl:r.schema_source_type==='wsdl_bridge'&&!remote?{text:service||'Источник зависимостей не указан',url:service}:null
  };
 }
 function mainSchema(r,chains){
  const entry=r.schema_files?.find(file=>file.path===r.schema_path);
  const included=chains.some(({file})=>file.text===r.schema_path&&(!entry?.sha256||!file.sha256||file.sha256===entry.sha256));
  return r.schema_path&&!included?{text:r.schema_path,url:r.schema_file_download||entry?.download}:null;
 }
 function createWorkbook(ExcelJS,rows,{number='',env='',specification='',xsd='',baseURL,createdAt=new Date()}={}){
  const workbook=new ExcelJS.Workbook();workbook.creator='Справочник СЭДО';workbook.created=createdAt;
  const sheet=workbook.addWorksheet('Замечания',{views:[{state:'frozen',ySplit:1}]});
  const columns=[
   ['Сообщение',14],['Контур',20],['Версия документа',18],['Спецификация',65],
   ['Замечание',28],['Место в спецификации',60],['В спецификации',65],
   ['Место в XSD',60],['В XSD',65],['Последствие',60],['Страница публикации СФР',65],
   ['Архив, документ или WSDL с XSD',65],['Файл XSD',65],['Зависимости WSDL',65],
   ['Открыть сообщение',32],['Дата спецификации',22],['Расположение схемы в документе',42],['Основная схема сообщения',65]
  ];
  const fieldCount=rows.reduce((count,{revision,finding})=>Math.max(count,fieldLinks(revision,finding).length),1);
  for(let i=0;i<fieldCount;i++)columns.push([fieldCount===1?'Открыть поле XSD':'Поле XSD '+(i+1),65]);
  columns.push(['Официальная публикация DOCX',65]);
  columns.push(['Официальная публикация XSD',65]);
  const chainsByRow=rows.map(({revision,finding})=>schemaChains(revision,finding));
  const schemaCount=chainsByRow.reduce((count,chains)=>Math.max(count,chains.length),1);
  for(let i=2;i<=schemaCount;i++){
   columns.push(['Файл XSD '+i,65],['Официальная публикация XSD '+i,65],
    ['Архив, документ или WSDL с XSD '+i,65],['Расположение схемы '+i+' в документе',42],['Зависимости WSDL '+i,65]);
  }
  const wsdlLocation=rows.some(({revision})=>revision.schema_docx_location);
  if(wsdlLocation)columns.push(['Ссылка на XSD в DOCX',65],['Файл объявления корня',65],['Точка входа XSD',65]);
  sheet.columns=columns.map(([header,width])=>({header,width}));
  for(const [index,{revision:r,finding:f}] of rows.entries()){
   const chains=chainsByRow[index],sources=chains[0],cell=s=>s?link(s.text,s.url,baseURL):'';
   const main=mainSchema(r,chains);
   const values=[r.number,envNames[r.environment],r.document_version||'',link(documentName(r),r.document_download||r.document_url,baseURL),
    f.kind,f.location,f.spec,f.xlocation,f.xsd,f.impact,
    cell(sources.page),cell(sources.publication),cell(sources.file),cell(sources.wsdl),
    link('Сообщение '+r.number,'index.html#'+new URLSearchParams({message:r.number,env:r.environment,rev:r.revision_id}),baseURL),
    r.label_date||'',sources.container,main?cell(main):r.schema_path||''];
   const targets=fieldLinks(r,f);
   for(let i=0;i<fieldCount;i++){
    const target=targets[i];
    values.push(target?link(target.text,'index.html'+target.href,baseURL):(i===0?'Поле не определено':''));
   }
   values.push(r.document_url?link('Публикация СФР',r.document_url,baseURL):'');
   values.push(cell(sources.official));
   const sameSource=(a,b)=>a?.text===b?.text&&a?.url===b?.url;
   for(const additional of chains.slice(1)){
    values.push(cell(additional.file),cell(additional.official),
     sameSource(additional.publication,sources.publication)?'':cell(additional.publication),
     additional.container===sources.container?'':additional.container,
     sameSource(additional.wsdl,sources.wsdl)?'':cell(additional.wsdl));
   }
   if(wsdlLocation){
    values.push(r.schema_docx_location?link(r.schema_docx_location,r.schema_docx_url||r.schema_docx_download,baseURL):'',
     r.schema_declaration_path?link(r.schema_declaration_path,r.schema_declaration_download||r.schema_declaration_url,baseURL):'',
     r.schema_docx_location?link(r.schema_path,r.schema_file_download,baseURL):'');
   }
   for(const value of values)if(typeof value==='string'&&value.length>32767)throw Error('Текст превышает лимит ячейки Excel. Выгрузка остановлена без обрезки.');
   const row=sheet.addRow(values);
   row.alignment={vertical:'top',wrapText:true};
  }
  sheet.autoFilter={from:{row:1,column:1},to:{row:Math.max(1,sheet.rowCount),column:columns.length}};
  sheet.getRow(1).font={bold:true,color:{argb:'FFFFFFFF'}};
  sheet.getRow(1).fill={type:'pattern',pattern:'solid',fgColor:{argb:'FF165A56'}};
  sheet.getRow(1).alignment={wrapText:true};sheet.getRow(1).height=32;
  const info=workbook.addWorksheet('О журнале');info.columns=[{width:28},{width:110}];
  const options=sourceOptions(rows);
  info.addRows([
   ['Номер сообщения',number||'Все сообщения'],['Контур',envNames[env]||'Все контуры'],['Записей',rows.length],
   ['Спецификация',options.specifications.find(s=>s.value===specification)?.text||specification||'Все спецификации'],
   ['Файл XSD',options.schemas.find(s=>s.value===xsd)?.text||xsd||'Все файлы XSD'],
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
  const all=collect(catalog),options=sourceOptions(all),requestedSpecification=params.get('specification');
  if(requestedSpecification&&!options.specifications.some(s=>s.value===requestedSpecification)){
   const legacy=all.find(({revision})=>legacySpecificationKey(revision)===requestedSpecification);
   if(legacy)options.specifications.unshift({value:requestedSpecification,text:documentName(legacy.revision)+' · все версии по сохранённой ссылке'});
  }
  const requestedXsd=params.get('xsd');
  if(requestedXsd&&!options.schemas.some(s=>s.value===requestedXsd)){
   for(const {revision,finding} of all){
    const legacy=schemaChains(revision,finding).find(s=>legacyXsdKey(revision,s)===requestedXsd);
    if(!legacy)continue;
    options.schemas.unshift({value:requestedXsd,text:legacy.file.text+' · все версии по сохранённой ссылке'});
    break;
   }
  }
  const sourceControls=el('div',undefined,'review-source-filters');
  function sourceSelect(title,id,items,initial,empty){
   const label=el('label',title),select=el('select');select.id=id;
   for(const item of [{value:'',text:empty},...items]){
    const option=el('option',item.text);option.value=item.value;select.append(option);
   }
   if(initial&&!items.some(s=>s.value===initial)){
    const option=el('option','Источник отсутствует в текущем журнале');option.value=initial;select.append(option);
   }
   select.value=initial;label.append(select);sourceControls.append(label);return select;
  }
  const specification=sourceSelect('Спецификация','review-specification',options.specifications,params.get('specification')||'','Все спецификации');
  const xsd=sourceSelect('Файл XSD','review-xsd',options.schemas,params.get('xsd')||'','Все файлы XSD');
  const button=el('button','Скачать Excel','open');button.type='button';button.id='review-export';
  const status=el('p','','meta');status.setAttribute('role','status');
  const result=el('div',undefined,'review-results');container.append(controls,sourceControls,button,status,result);
  function sourceLink(step){
   const resolved=link(step.text,step.url,location.href);
   const item=el(typeof resolved==='object'?'a':'span',step.text);
   if(typeof resolved==='object'){item.href=resolved.hyperlink;item.target='_blank';item.rel='noopener'}
   return item;
  }
  let visible=[];
  function render(){
   visible=filter(all,input.value,select.value,specification.value,xsd.value);result.replaceChildren();
   status.textContent='Замечаний: '+visible.length;button.disabled=!visible.length;
   const route=new URLSearchParams({remarks:'1'});
   if(input.value.trim())route.set('number',input.value.trim());
   if(select.value)route.set('env',select.value);
   if(specification.value)route.set('specification',specification.value);
   if(xsd.value)route.set('xsd',xsd.value);
   history.replaceState(null,'','#'+route);
   if(!visible.length){result.append(el('p','По выбранным фильтрам замечаний нет. Объём выполненной сверки указан в карточках сообщений.'));return}
   const table=el('table'),head=el('thead'),header=el('tr');
   for(const text of ['Сообщение','Спецификация','Замечание','В спецификации','В XSD','Последствие']){const th=el('th',text);th.scope='col';header.append(th)}
   head.append(header);table.append(head);const body=el('tbody');
   for(const {revision:r,finding:f} of visible){
    const row=el('tr'),identity=el('td'),a=el('a',r.number+' · '+envNames[r.environment]);
    a.href='#'+new URLSearchParams({message:r.number,env:r.environment,rev:r.revision_id});identity.append(a);
    const source=el('td'),doc=el(r.document_url||r.document_download?'a':'span',documentName(r));
    if(doc.tagName==='A'){doc.href=r.document_download||r.document_url;doc.target='_blank';doc.rel='noopener'}
    source.append(doc,el('p','Версия '+(r.document_version||'не указана')+(r.label_date?' · '+r.label_date:''),'meta'));
    if(r.document_download&&r.document_url){const official=el('a','Публикация СФР');official.href=r.document_url;official.target='_blank';official.rel='noopener';source.append(official)}
    const spec=el('td'),schema=el('td');spec.append(el('p',f.spec),el('p',f.location,'meta'));schema.append(el('p',f.xsd),el('p',f.xlocation,'meta'));
    const chains=schemaChains(r,f);
    for(const sources of chains){
     const chain=el('p','Первоисточник XSD · ','meta review-source-chain');
     const steps=[sources.page,sources.publication,sources.container&&{text:sources.container},sources.file,sources.official].filter(Boolean);
     for(const [index,step] of steps.entries()){
      if(index)chain.append(document.createTextNode(' → '));
      chain.append(sourceLink(step));
     }
     if(sources.wsdl)chain.append(' · Зависимости из ',sourceLink({text:'WSDL шлюза СФР',url:sources.wsdl.url}));
     schema.append(chain);
    }
    const main=mainSchema(r,chains);
    if(main){const label=el('p','Основная схема сообщения · ','meta review-main-schema');label.append(sourceLink(main));schema.append(label)}
    for(const target of fieldLinks(r,f)){const a=el('a',target.text,'source');a.href=target.href;schema.append(el('br'),a)}
    row.append(identity,source,el('td',f.kind),spec,schema,el('td',f.impact));body.append(row);
   }
   table.append(body);result.append(table);
  }
  input.oninput=render;select.onchange=render;specification.onchange=render;xsd.onchange=render;
  button.onclick=async()=>{
   const snapshot=visible.slice(),number=input.value.trim(),env=select.value;
   const selectedSpecification=specification.value,selectedXsd=xsd.value;button.disabled=true;
   try{
    const ExcelJS=await root.SedoComparisonExport.loadLibrary();
    const workbook=createWorkbook(ExcelJS,snapshot,{number,env,specification:selectedSpecification,xsd:selectedXsd,baseURL:location.href});
    const buffer=await workbook.xlsx.writeBuffer(),url=URL.createObjectURL(new Blob([buffer],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
    const a=el('a');a.href=url;a.download='sedo-remarks-'+(number||'all')+'-'+(env||'all')+'.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    status.textContent='В Excel выгружено замечаний: '+snapshot.length;
   }catch(error){status.textContent='Не удалось выгрузить Excel. '+error.message}
   finally{button.disabled=!visible.length}
  };
  render();
 }
 const api={collect,filter,createWorkbook,mount,schemaSources,fieldLinks,specificationKey,xsdKey,sourceOptions,sourceFilename,documentName,remoteSchemaSource};
 if(typeof module==='object'&&module.exports)module.exports=api;
 else root.SedoReviewJournal=api;
})(typeof window==='undefined'?globalThis:window);
