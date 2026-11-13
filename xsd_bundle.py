"""Publish exact-edition XSD closures with byte-identical originals and offline copies."""
import io
import base64
import gzip
import json
import posixpath
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from lxml import etree
from schema import import_basename, Schema, local
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


def _prepare(row, selected):
    files={}
    for name,content in sorted(selected.items()):
        root=etree.fromstring(content,etree.XMLParser(no_network=True,resolve_entities=False,load_dtd=False))
        if root.tag!='{'+XS+'}schema' or root.getroottree().docinfo.doctype:raise Unavailable('Файл не является автономной XSD: '+name)
        files[name]=(content,root)
    entry=row['schema_path'];root=files[entry][1]
    if root.get('targetNamespace')!=row.get('namespace') or not any(e.get('name')==row['root'] for e in root.findall('{'+XS+'}element')):
        raise Unavailable('Корень или namespace не соответствует выбранной редакции')
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
            target=_resolve(files,name,reference,root.get('targetNamespace'))
            old=reference.get('schemaLocation');new=posixpath.relpath(local_paths[target],posixpath.dirname(local_paths[name]) or '.')
            reference.set('schemaLocation',new)
            if old!=new:changes.append(dict(kind=etree.QName(reference).localname,before=old,after=new,target_source_path=target))
        local=etree.tostring(root,encoding='UTF-8',xml_declaration=True)
        output[original_paths[name]]=content;output[local_paths[name]]=local
        records.append(dict(source_path=name,original_path=original_paths[name],local_path=local_paths[name],original_sha256=sha(content),local_sha256=sha(local),rewrites=changes))
    with tempfile.TemporaryDirectory(prefix='sedo-xsd-compile-') as folder:
        base=Path(folder)
        for name,content in output.items():
            if name.startswith('originals/'):continue
            target=base/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        compile_schema(base/'message.xsd',base)
    return output,records


def _zip(row, output, records):
    manifest={k:row.get(k) for k in ('number','environment','revision_id','document_version','label_date','root','namespace','schema_path','archive_sha256','archive_url','document_url','schema_source_type','schema_source_url','schema_sources')}
    manifest.update(format=1,entrypoint='message.xsd',files=records)
    output=dict(output)
    output['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode('utf8')
    output['validate.py']=Path(__file__).with_name('xsd_validate.py').read_text('utf-8-sig').encode('utf8')
    output['requirements.txt']=('lxml=='+LXML_VERSION+'\n').encode('ascii')
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
manifest.json содержит контрольные суммы и список изменений путей.
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
    class NavigationSchema(Schema):
        def __init__(self, *args):
            self.addresses = {}
            super().__init__(*args)

        def field(self, decl, parent=-1, group="", chain=()):
            tag = local(decl)
            src = self.resolve(decl.get('ref'), decl, [tag]) if decl.get('ref') else decl
            name = ('@' if tag in ('attribute', 'anyAttribute') else '') + src.get('name', '*')
            path = (self.fields[parent]['path']+'/' if parent >= 0 else '')+name
            owner = self.files[self.owner[src]]
            qualified = src in list(owner[0]) or bool(decl.get('ref')) or src.get('form', owner[0].get(('attribute' if tag == 'attribute' else 'element')+'FormDefault', 'unqualified')) == 'qualified'
            self.addresses.setdefault(path, set()).add(owner[2] if qualified else '')
            return super().field(decl, parent, group, chain)

    schema = NavigationSchema(archive, row['schema_path'], row['root'])
    schema.field(schema.root)
    return [sorted(schema.addresses.get(path, set())) for path in paths]


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
            output,records=value;content=_zip(row,output,records);relative='schemas/'+rid+'.zip';target=Path(site)/relative
            if not target.is_file() or target.read_bytes()!=content:write(target,content)
            version=row.get('document_version') or 'unversioned'
            row.update(schema_export_status='ready',schema_download=relative,schema_filename=re.sub('[^A-Za-z0-9._-]','_',f"sedo-{row['number']}-{row['environment']}-v{version}-{rid[:8]}-xsd.zip"),schema_bundle_sha256=sha(content),schema_file_count=len(records))
            pack={k:row.get(k) for k in ('number','environment','revision_id','document_version','label_date','root','namespace')}
            pack['fields']=list((navigation or {}).get(rid,[]))
            if pack['fields']:
                namespace_key=(key,tuple(pack['fields']))
                if namespace_key not in namespace_cache:namespace_cache[namespace_key]=_field_namespaces(archives[digest],row,pack['fields'])
                pack['field_namespaces']=namespace_cache[namespace_key]
            pack.update(format=1,entrypoint='message.xsd',files={f['local_path']:base64.b64encode(output[f['local_path']]).decode('ascii') for f in records})
            browser=gzip.compress(json.dumps(pack,ensure_ascii=True,separators=(',',':'),sort_keys=True).encode('utf8'),compresslevel=9,mtime=0)
            browser=browser[:9]+b'\xff'+browser[10:]  # Stable gzip OS marker across Python/platform versions.
            relative='schemas/'+rid+'.validation.json.gz';target=Path(site)/relative
            if not target.is_file() or target.read_bytes()!=browser:write(target,browser)
            row.update(validation_download=relative,validation_sha256=sha(browser))
            row.pop('schema_export_error',None)
    finally:
        for archive in archives.values():archive.close()
