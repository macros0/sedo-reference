"""Publish exact-edition XSD closures with byte-identical originals and offline copies."""
import io
import copy
import base64
import gzip
import json
import posixpath
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from lxml import etree
from xml.etree import ElementTree as ET
from schema import import_basename, Schema
from sync import sha, write
from xsd_validate import compile_schema

XS='http://www.w3.org/2001/XMLSchema'
REFERENCES={'{'+XS+'}'+name for name in ('import','include','redefine','override')}
LXML_VERSION='6.1.3'


class IntegrityError(ValueError):pass
class Unavailable(ValueError):pass


def _digest(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):
        raise IntegrityError('Некорректная контрольная сумма XSD или архива')
    return value


def _source_path(value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value:
        raise IntegrityError('Некорректный путь XSD в архиве')
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or value!=str(path) or not value.lower().endswith('.xsd'):
        raise IntegrityError('Небезопасный путь XSD в архиве: '+value)
    return value


def _selected(row, archive):
    entry=_source_path(row['schema_path']);base=posixpath.dirname(entry)
    result={}
    for relative,digest in row['dependencies']:
        name=_source_path(posixpath.normpath(posixpath.join(base,relative)))
        if name in result:raise IntegrityError('Повторный путь зависимости XSD: '+name)
        try:content=archive.read(name)
        except KeyError as exc:raise IntegrityError('Отсутствует XSD: '+name) from exc
        if sha(content)!=_digest(digest):raise IntegrityError('Не совпадает контрольная сумма XSD: '+name)
        result[name]=content
    if entry not in result:raise IntegrityError('Отсутствует корневая XSD в зависимостях')
    return result


def _resolve(files, source, reference, namespace):
    location=reference.get('schemaLocation')
    if not location:raise Unavailable('Не задан schemaLocation в '+source)
    is_import=reference.tag=='{'+XS+'}import'
    allowed={reference.get('namespace')} if is_import else {namespace,None}
    expected=posixpath.normpath(posixpath.join(posixpath.dirname(source),location.replace('\\','/')))
    if expected in files:
        if files[expected][1].get('targetNamespace') not in allowed:raise Unavailable('Не совпадает namespace зависимости '+location)
        return expected
    candidates=[n for n,(content,root) in files.items() if posixpath.basename(n)==import_basename(location) and root.get('targetNamespace') in allowed]
    if candidates and len({sha(files[n][0]) for n in candidates})==1:return sorted(candidates)[0]
    raise Unavailable(('Неоднозначная' if candidates else 'Не найдена')+' зависимость XSD: '+location)


def _attribute_checks(row, selected, files, local_paths):
    """Compile attribute-use fixed constraints as equivalent local-attribute probes.

    libxml2 ignores fixed on an attribute ref. Local attribute fixed constraints use the
    same XSD type/value-space comparison and are enforced in both shipped engines.
    Originals and their working schema components remain otherwise unchanged.
    """
    fixed_globals={(root.get('targetNamespace',''),node.get('name')) for _,root in files.values()
                   for node in root.findall('{'+XS+'}attribute') if node.get('fixed') is not None}
    def affected(node):
        reference=node.get('ref')
        if node.tag!='{'+XS+'}attribute' or not reference:return False
        prefix,_,name=reference.rpartition(':')
        # A global attribute from a no-targetNamespace include adopts the
        # including namespace. Its physical document alone cannot identify all
        # effective QNames, so include that name in the inexpensive candidate
        # check; the expanded Schema below selects the actual fixed uses.
        return (node.get('fixed') is not None or (node.nsmap.get(prefix or None,''),name) in fixed_globals
                or ('',name) in fixed_globals)
    if not any(affected(node) for _,root in files.values() for node in root.iter()):return {},{}
    archive_bytes=io.BytesIO()
    with zipfile.ZipFile(archive_bytes,'w') as archive:
        for name,content in selected.items():archive.writestr(name,content)
    with zipfile.ZipFile(archive_bytes) as archive:
        tree=Schema(archive,row['schema_path'],row['root']);tree.field(tree.root)
    nodes={}
    for (name,_namespace),(root,_,_) in tree.documents.items():
        native=[e for e in files[name][1].iter() if isinstance(e.tag,str)]
        nodes.update(zip(root.iter(),native))
    namespace='urn:sedo:attribute-value-checks'
    while any(root.get('targetNamespace')==namespace for _,root in files.values()):namespace+=':checks'
    checks=etree.Element('{'+XS+'}schema',nsmap={'xs':XS,'probe':namespace},targetNamespace=namespace)
    groups={}
    for name,(_,root) in files.items():groups.setdefault(root.get('targetNamespace',''),[]).append(name)
    generated={};entry='schemas/attribute-checks.xsd'
    # Import complete namespace groups once. Includes retain chameleon semantics;
    # grouping also supports namespaces split across several source XSD files.
    for ordinal,(ns,names) in enumerate(sorted(groups.items())):
        if len(names)==1:target=local_paths[names[0]]
        else:
            target=f'schemas/attribute-types-{ordinal}.xsd'
            attributes={'targetNamespace':ns} if ns else {}
            group=etree.Element('{'+XS+'}schema',nsmap={'xs':XS},**attributes)
            for name in names:etree.SubElement(group,'{'+XS+'}include',schemaLocation=posixpath.relpath(local_paths[name],'schemas'))
            generated[target]=etree.tostring(group,encoding='UTF-8',xml_declaration=True)
        attributes={'namespace':ns} if ns else {}
        etree.SubElement(checks,'{'+XS+'}import',schemaLocation=posixpath.relpath(target,'schemas'),**attributes)
    probe_names={}
    def collect(fields, relative=False):
        rules=[];definitions={}
        for field in fields:
            if field['kind']!='attribute' or 'fixed' not in field['flags']:continue
            decl,source=tree.field_declarations[id(field)]
            if not decl.get('ref'):continue
            path=[];contexts=[field['group']] if field['group'] else [];current=field['parent']
            while current>=0:
                ancestor=fields[current];path.append([ancestor['ns'],ancestor['name']]);current=ancestor['parent']
                if ancestor['group']:contexts.append(ancestor['group'])
            path.reverse();contexts=tuple(reversed(contexts))
            if relative:path=path[1:]
            key=tuple(map(tuple,path))+((field['ns'],field['name']),)
            fixed=field['flags']['fixed']
            signature=(fixed,source.get('type'),tree.owner[source],etree.tostring(nodes[source]))
            # Restrictions retain inherited fixed values, including equivalent
            # lexical forms (boolean false/0). Keep both typed probes rather
            # than comparing their strings; distinct choice contexts stay ambiguous.
            prior=definitions.setdefault(key,[])
            if any(previous==signature for previous,_ in prior):continue
            if any(context!=contexts and any('Выбор' in label for label in (*context,*contexts)) for _,context in prior):raise Unavailable('Неоднозначное fixed для пути '+field['path'])
            prior.append((signature,contexts))
            constraint=decl if 'fixed' in decl.attrib else source
            probe_key=(constraint,source)
            if probe_key not in probe_names:
                fixed_source=nodes[constraint];actual=nodes[source];bindings=dict(fixed_source.nsmap)
                element_name='value'+str(len(probe_names));probe_names[probe_key]=element_name
                attributes={'name':'value','fixed':fixed,'use':'required'}
                if actual.get('type'):
                    qname=actual.get('type');prefix,_,name=qname.rpartition(':');ns=actual.nsmap.get(prefix or None,'')
                    if ns:
                        alias='sedotype'
                        while alias in bindings:alias+='x'
                        bindings[alias]=ns;attributes['type']=alias+':'+name
                    else:
                        if bindings.get(None):raise Unavailable('Тип без namespace и QName fixed требуют отдельного пространства имён: '+field['path'])
                        attributes['type']=name
                else:
                    anonymous=actual.find('{'+XS+'}simpleType')
                    if anonymous is None:
                        alias='sedotype'
                        while alias in bindings:alias+='x'
                        bindings[alias]=XS;attributes['type']=alias+':anySimpleType'
                probe_element=etree.SubElement(checks,'{'+XS+'}element',name=element_name)
                probe=etree.SubElement(etree.SubElement(probe_element,'{'+XS+'}complexType'),'{'+XS+'}attribute',nsmap=bindings,**attributes)
                if not actual.get('type') and actual.find('{'+XS+'}simpleType') is not None:probe.append(copy.deepcopy(actual.find('{'+XS+'}simpleType')))
            rules.append(dict(path=path,attribute=[field['ns'],field['name'][1:]],element=probe_names[probe_key],field=field['path']))
        return rules
    rules=collect(tree.fields);type_rules=[]
    def needs_checks(declaration):
        pending=[declaration];visited=set()
        while pending:
            node=pending.pop()
            if node in visited:continue
            visited.add(node)
            for child in node.iter():
                if child.tag=='{'+XS+'}attribute' and child.get('ref'):
                    attribute=tree.resolve(child.get('ref'),child,['attribute'])
                    if child.get('fixed') is not None or attribute.get('fixed') is not None:return True
                qname=child.get('type') or child.get('base') or child.get('ref')
                if not qname:continue
                kinds=['simpleType','complexType'] if child.get('type') or child.get('base') else [child.tag.rsplit('}',1)[-1]]
                target=tree.resolve(qname,child,kinds)
                if target is not None:pending.append(target)
        return False
    # xsi:type can select a compatible named type not present in the static root
    # tree. Compile each type's additional constraints relative to its XML node.
    # These expansions consume the same 50,000-node budget as the root tree.
    for (namespace_uri,kind,name),declaration in sorted(tree.symbol.items()):
        if kind!='complexType' or not needs_checks(declaration):continue
        bindings=dict(tree.prefixes[declaration]);alias='sedotyperef'
        while alias in bindings:alias+='x'
        if namespace_uri:bindings[alias]=namespace_uri;qname=alias+':'+name
        else:bindings['']='';qname=name
        temporary=ET.Element('{'+XS+'}element',name='typeRoot',type=qname)
        tree.owner[temporary]=tree.owner[declaration];tree.prefixes[temporary]=bindings
        tree.fields=[];tree.field(temporary)
        applicable=collect(tree.fields,relative=True)
        if applicable:type_rules.append(dict(type=[namespace_uri,name],checks=applicable))
    if not probe_names:return {},{}
    root=etree.SubElement(checks,'{'+XS+'}element',name='checks')
    choice=etree.SubElement(etree.SubElement(root,'{'+XS+'}complexType'),'{'+XS+'}choice',minOccurs='0',maxOccurs='unbounded')
    for element_name in probe_names.values():etree.SubElement(choice,'{'+XS+'}element',ref='probe:'+element_name)
    generated[entry]=etree.tostring(checks,encoding='UTF-8',xml_declaration=True)
    return generated,dict(attribute_checks=rules,attribute_type_checks=type_rules,supplementary_schema=entry,supplementary_namespace=namespace,generated_files=[dict(local_path=name,local_sha256=sha(content)) for name,content in sorted(generated.items())])


def _prepare(row, selected):
    files={}
    for name,content in sorted(selected.items()):
        root=etree.fromstring(content,etree.XMLParser(no_network=True,resolve_entities=False,load_dtd=False))
        if root.tag!='{'+XS+'}schema' or root.getroottree().docinfo.doctype:raise Unavailable('Файл не является автономной XSD: '+name)
        files[name]=(content,root)
    entry=row['schema_path'];root=files[entry][1]
    if root.get('targetNamespace')!=row.get('namespace') or not any(e.get('name')==row['root'] for e in root.findall('{'+XS+'}element')):
        raise Unavailable('Корень или namespace не соответствует выбранной редакции')
    reference_targets={};contexts=set()
    def visit(name,inherited_namespace=None):
        document=files[name][1]
        namespace=document.get('targetNamespace') or inherited_namespace
        if (name,namespace) in contexts:return
        contexts.add((name,namespace))
        for reference in document:
            if reference.tag not in REFERENCES:continue
            target=_resolve(files,name,reference,namespace)
            if reference in reference_targets and reference_targets[reference]!=target:
                raise Unavailable('Неоднозначная зависимость включаемой XSD: '+name)
            reference_targets[reference]=target
            visit(target,None if reference.tag=='{'+XS+'}import' else namespace)
    visit(entry)
    for name in files:
        if not any(path==name for path,_namespace in contexts):visit(name)
    local_paths={};original_paths={}
    for i,name in enumerate(files,1):
        basename=re.sub('[^A-Za-z0-9._-]','_',posixpath.basename(name))[:80]
        filename=f'{i:03d}_{basename}'
        if not filename.lower().endswith('.xsd'):filename+='.xsd'
        original_paths[name]='originals/'+filename
        local_paths[name]='message.xsd' if name==entry else 'schemas/'+filename
    output={};records=[]
    for name,(content,root) in files.items():
        changes=[]
        for reference in root:
            if reference.tag not in REFERENCES:continue
            target=reference_targets[reference]
            old=reference.get('schemaLocation');new=posixpath.relpath(local_paths[target],posixpath.dirname(local_paths[name]) or '.')
            reference.set('schemaLocation',new)
            if old!=new:changes.append(dict(kind=etree.QName(reference).localname,before=old,after=new,target_source_path=target))
        local=etree.tostring(root,encoding='UTF-8',xml_declaration=True)
        output[original_paths[name]]=content;output[local_paths[name]]=local
        records.append(dict(source_path=name,original_path=original_paths[name],local_path=local_paths[name],original_sha256=sha(content),local_sha256=sha(local),rewrites=changes))
    generated,constraints=_attribute_checks(row,selected,files,local_paths)
    output.update(generated)
    with tempfile.TemporaryDirectory(prefix='sedo-xsd-compile-') as folder:
        base=Path(folder)
        for name,content in output.items():
            if name.startswith('originals/'):continue
            target=base/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        compile_schema(base/'message.xsd',base)
        if constraints:compile_schema(base/constraints['supplementary_schema'],base)
    return output,records,constraints


def _zip(row, output, records, constraints=None):
    manifest={k:row.get(k) for k in ('number','environment','revision_id','document_version','label_date','root','namespace','schema_path','archive_sha256','archive_url','document_url','schema_source_type','schema_source_url','schema_sources')}
    manifest.update(format=1,entrypoint='message.xsd',files=records)
    manifest.update(constraints or {})
    output=dict(output)
    output['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode('utf8')
    output['validate.py']=Path(__file__).with_name('xsd_validate.py').read_text('utf-8-sig').encode('utf8')
    output['requirements.txt']=('lxml=='+LXML_VERSION+'\n').encode('ascii')
    constraint_note=('Дополнительная схема schemas/attribute-checks.xsd проверяет fixed на ссылках атрибутов:\n'
                     'это правило libxml2 пропускает при обычной XSD-проверке. Используйте validate.py\n'
                     'для полной проверки; стороннему валидатору требуется отдельная проверка этого правила.\n' if constraints else '')
    readme=f'''Комплект XSD сообщения СЭДО {row['number']}
Контур: {row['environment']}
Версия документа: {row.get('document_version') or 'не указана'}
Дата документа: {row.get('label_date') or 'не указана'}
Редакция: {row['revision_id']}
Корневой элемент: {row['root']}
Пространство имён: {row.get('namespace') or '(без namespace)'}

1. Распакуйте ZIP полностью в отдельную папку.
2. Корневая схема для вашего валидатора: message.xsd. Не удаляйте папку schemas.
3. Для проверки прилагаемым скриптом нужен Python 3.10+ и lxml.
   Один раз установите библиотеку (для установки нужен доступ к PyPI):
       python -m pip install -r requirements.txt
   Затем запустите из этой папки:
       python validate.py "путь/к/сообщению.xml"
   Только проверить готовность схем:
       python validate.py --check-schema
   Код завершения: 0 — соответствует XSD, 1 — XML не соответствует,
   2 — ошибка чтения, зависимости или комплекта.
4. Альтернативный пример для установленного xmllint:
       xmllint --nonet --noout --schema message.xsd "сообщение.xml"
   При использовании другого валидатора отдельно проверяйте корень и namespace,
   указанные выше: один исходный XSD может объявлять несколько сообщений.

Проверка XML выполняется локально. Скрипт не отправляет XML и не загружает
внешние ресурсы. Проверяются корень, структура, типы и ограничения XSD,
но не бизнес-проверки СФР и не подлинность электронной подписи.

originals — исходные XSD без изменения байтов; имена имеют числовой префикс,
чтобы избежать совпадений и длинных путей. Исходные пути находятся в manifest.json.
schemas и message.xsd — рабочие копии: изменены только пути schemaLocation
для автономного разрешения зависимостей. Типы, namespace и ограничения сохранены.
{constraint_note}manifest.json содержит контрольные суммы, изменения путей и дополнительные проверки.
Комплект относится к этой точной редакции и контуру, включая архивные версии.
Документация библиотеки: https://lxml.de/6.1/validation.html
'''
    output['README.txt']=readme.encode('utf8')
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,content in sorted(output.items()):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.create_system=3;info.external_attr=0o100644<<16;archive.writestr(info,content,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    return stream.getvalue()


def _field_namespaces(archive, row, paths):
    schema = Schema(archive, row['schema_path'], row['root'])
    schema.field(schema.root)
    addresses = {}
    for field in schema.fields:
        addresses.setdefault(field['path'], set()).add(field['ns'])
    return [sorted(addresses.get(path, set())) for path in paths]


def publish_bundles(catalog,state,site,navigation=None):
    archives={};prepared={};namespace_cache={}
    try:
        for row in catalog['revisions']:
            for key in ('schema_download','schema_filename','schema_bundle_sha256','schema_file_count','validation_download','validation_sha256'):row.pop(key,None)
            row['schema_export_status']='unavailable';row['schema_export_error']='Полный комплект XSD для этой редакции не сопоставлен.'
            if row.get('status')!='matched':continue
            rid=_digest(row['revision_id']);digest=_digest(row['archive_sha256'])
            if digest not in archives:
                source=Path(state)/'objects'/digest
                if not source.is_file():raise IntegrityError('Отсутствует исходный архив XSD: '+digest)
                content=source.read_bytes()
                if sha(content)!=digest:raise IntegrityError('Не совпадает контрольная сумма архива XSD: '+digest)
                try:archives[digest]=zipfile.ZipFile(io.BytesIO(content))
                except zipfile.BadZipFile as exc:raise IntegrityError('Повреждён исходный архив XSD') from exc
            selected=_selected(row,archives[digest]);key=(digest,row['schema_path'],row['root'],row.get('namespace'),tuple((n,sha(v)) for n,v in sorted(selected.items())))
            if key not in prepared:
                try:prepared[key]=_prepare(row,selected)
                except (Unavailable,etree.Error,OSError,ValueError) as exc:prepared[key]=str(exc)
            value=prepared[key]
            if isinstance(value,str):
                row['schema_export_error']='XSD не готова для локальной валидации: '+value[:500]
                continue
            output,records,constraints=value;content=_zip(row,output,records,constraints);relative='schemas/'+rid+'.zip';target=Path(site)/relative
            if not target.is_file() or target.read_bytes()!=content:write(target,content)
            version=row.get('document_version') or 'unversioned'
            row.update(schema_export_status='ready',schema_download=relative,schema_filename=re.sub('[^A-Za-z0-9._-]','_',f"sedo-{row['number']}-{row['environment']}-v{version}-{rid[:8]}-xsd.zip"),schema_bundle_sha256=sha(content),schema_file_count=len(records))
            pack={k:row.get(k) for k in ('number','environment','revision_id','document_version','label_date','root','namespace')}
            pack['fields']=list((navigation or {}).get(rid,[]))
            if pack['fields']:
                namespace_key=(key,tuple(pack['fields']))
                if namespace_key not in namespace_cache:namespace_cache[namespace_key]=_field_namespaces(archives[digest],row,pack['fields'])
                pack['field_namespaces']=namespace_cache[namespace_key]
            pack.update(constraints)
            pack.update(format=1,entrypoint='message.xsd',files={name:base64.b64encode(content).decode('ascii') for name,content in output.items() if not name.startswith('originals/')})
            browser=gzip.compress(json.dumps(pack,ensure_ascii=True,separators=(',',':'),sort_keys=True).encode('utf8'),compresslevel=9,mtime=0)
            browser=browser[:9]+b'\xff'+browser[10:]  # Stable gzip OS marker across Python/platform versions.
            relative='schemas/'+rid+'.validation.json.gz';target=Path(site)/relative
            if not target.is_file() or target.read_bytes()!=browser:write(target,browser)
            row.update(validation_download=relative,validation_sha256=sha(browser))
            row.pop('schema_export_error',None)
    finally:
        for archive in archives.values():archive.close()
