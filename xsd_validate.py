"""Standalone offline validator, also copied into downloadable XSD bundles."""
import argparse
import io
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname
from types import SimpleNamespace


def compile_schema(entry, package_root):
    from lxml import etree
    base=Path(package_root).resolve()
    entry=Path(entry).resolve()
    if not entry.is_relative_to(base):
        raise ValueError('Корневая XSD находится за пределами комплекта')

    class LocalOnly(etree.Resolver):
        def resolve(self, url, public_id, context):
            parts=urlsplit(url)
            if parts.scheme=='file' and not parts.netloc:
                candidates=[Path(url2pathname(parts.path))]
            elif not parts.netloc and (not parts.scheme or (len(parts.scheme)==1 and url[1:3] in (':\\',':/'))):
                # libxml2 uses native absolute filenames for schema imports on Linux.
                if url.startswith(('//','\\\\')):raise OSError('Внешние ресурсы запрещены: '+url)
                candidates=[Path(url),Path(url2pathname(url))]
            else:
                raise OSError('Внешние ресурсы запрещены: '+url)
            for candidate in candidates:
                if not candidate.is_absolute() or not candidate.is_relative_to(base):continue
                target=candidate.resolve()
                if target.is_relative_to(base) and target.is_file():
                    return self.resolve_string(target.read_bytes(),context,base_url=target.as_uri())
            raise OSError('XSD за пределами комплекта или отсутствует: '+url)

    parser=etree.XMLParser(no_network=True,resolve_entities=False,load_dtd=False)
    parser.resolvers.add(LocalOnly())
    document=etree.parse(entry.as_uri(),parser)
    if document.docinfo.doctype:
        raise ValueError('DTD в XSD не поддерживается')
    primary=etree.XMLSchema(document)
    manifest_file=base/'manifest.json'
    if manifest_file.is_file():
        manifest=json.loads(manifest_file.read_text('utf-8-sig'))
        if entry==(base/manifest.get('entrypoint','')).resolve() and (manifest.get('attribute_checks') or manifest.get('attribute_type_checks')):
            supplement=compile_schema(base/manifest['supplementary_schema'],base)
            return AttributeUseValidator(primary,supplement,manifest)
    return primary


class AttributeUseValidator:
    """Apply libxml2's typed fixed comparison to each actual constrained use."""
    def __init__(self, primary, supplementary, manifest):
        self.primary=primary;self.supplementary=supplementary
        self.namespace=manifest['supplementary_namespace']
        self.rules=manifest['attribute_checks'];self.error_log=[]
        self.by_path={}
        for rule in self.rules:self.by_path.setdefault(tuple(map(tuple,rule['path'])),[]).append(rule)
        self.by_type={tuple(profile['type']):{} for profile in manifest.get('attribute_type_checks',[])}
        for profile in manifest.get('attribute_type_checks',[]):
            for rule in profile['checks']:self.by_type[tuple(profile['type'])].setdefault(tuple(map(tuple,rule['path'])),[]).append(rule)

    def validate(self, document):
        from lxml import etree
        if not self.primary.validate(document):
            self.error_log=self.primary.error_log;return False
        root=document.getroot() if hasattr(document,'getroot') else document
        prefix='sedoprobe'
        while any(prefix in node.nsmap for node in root.iter() if isinstance(node.tag,str)):prefix+='x'
        probes=etree.Element('{'+self.namespace+'}checks',nsmap={prefix:self.namespace})
        subjects={}
        def visit(node,path,scopes):
            if not isinstance(node.tag,str):return
            qname=etree.QName(node);path=path+((qname.namespace or '',qname.localname),)
            xtype=node.get('{http://www.w3.org/2001/XMLSchema-instance}type')
            if xtype:
                prefix,_,name=xtype.strip().rpartition(':');namespace=node.nsmap.get(prefix or None,'')
                profile=self.by_type.get((namespace,name))
                if profile:scopes=(*scopes,(len(path),profile))
            applicable=list(self.by_path.get(path,[]))
            for depth,profile in scopes:applicable.extend(profile.get(path[depth:],[]))
            seen=set()
            for rule in applicable:
                identity=(tuple(rule['attribute']),rule['element'])
                if identity in seen:continue
                seen.add(identity)
                ns,name=rule['attribute'];value=node.get(('{'+ns+'}' if ns else '')+name)
                if value is None:continue
                probe=etree.SubElement(probes,'{'+self.namespace+'}'+rule['element'],nsmap=node.nsmap)
                probe.set('value',value);probe.sourceline=node.sourceline
                subjects[probe]=(node,rule)
            for child in node:visit(child,path,scopes)
        visit(root,(),())
        valid=self.supplementary.validate(probes);self.error_log=[]
        if not valid:
            probe_tree=probes.getroottree()
            by_probe_path={probe_tree.getpath(probe):subject for probe,subject in subjects.items()}
            for error in self.supplementary.error_log:
                # One declaration can be reused at several XML paths and on the
                # same line. The generated probe XPath identifies its exact use.
                subject=by_probe_path.get(error.path)
                if subject:
                    node,rule=subject;ns,name=rule['attribute'];attribute=('{'+ns+'}' if ns else '')+name
                    message="Element '"+node.tag+"', attribute '"+attribute+"': "+error.message
                else:message=error.message
                self.error_log.append(SimpleNamespace(line=error.line,column=error.column,message=message))
        return valid


def main(argv=None):
    parser=argparse.ArgumentParser(description='Локальная проверка XML выбранного сообщения СЭДО по комплекту XSD')
    parser.add_argument('xml',nargs='?',help='Путь к проверяемому XML')
    parser.add_argument('--check-schema',action='store_true',help='Только проверить готовность комплекта XSD')
    args=parser.parse_args(argv)
    if not args.xml and not args.check_schema:parser.error('Укажите XML или --check-schema')
    base=Path(__file__).resolve().parent
    try:
        from lxml import etree
        manifest=json.loads((base/'manifest.json').read_text('utf-8-sig'))
        if manifest.get('format')!=1:raise ValueError('Неизвестный формат комплекта')
        validator=compile_schema(base/manifest['entrypoint'],base)
    except ImportError:
        print('Установите зависимость: python -m pip install -r requirements.txt',file=sys.stderr)
        return 2
    except Exception as exc:
        print('Комплект XSD недоступен: '+str(exc),file=sys.stderr)
        return 2
    if args.check_schema:
        print('Комплект XSD готов для сообщения '+str(manifest['number'])+' ('+manifest['environment']+').')
        if not args.xml:return 0
    try:
        content=Path(args.xml).read_bytes()
    except OSError as exc:
        print('Не удалось прочитать XML: '+str(exc),file=sys.stderr)
        return 2
    try:
        document=etree.parse(io.BytesIO(content),etree.XMLParser(no_network=True,resolve_entities=False,load_dtd=False))
        if document.docinfo.doctype:
            print('XML с DTD запрещён: внешние данные не загружаются.',file=sys.stderr)
            return 1
        expected=('{'+manifest['namespace']+'}' if manifest.get('namespace') else '')+manifest['root']
        if document.getroot().tag!=expected:
            print('Неверный корень XML. Ожидается '+expected+'; получен '+str(document.getroot().tag),file=sys.stderr)
            return 1
        if validator.validate(document):
            print('XML соответствует XSD сообщения '+str(manifest['number'])+'.')
            return 0
        for error in list(validator.error_log)[:30]:
            print('Строка '+str(error.line)+', позиция '+str(error.column)+': '+error.message,file=sys.stderr)
        return 1
    except (etree.XMLSyntaxError,ValueError) as exc:
        print('Некорректный XML: '+str(exc),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
