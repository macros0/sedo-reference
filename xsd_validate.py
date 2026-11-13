"""Standalone offline validator, also copied into downloadable XSD bundles."""
import argparse
import io
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname


def compile_schema(entry, package_root):
    from lxml import etree
    base=Path(package_root).resolve()
    entry=Path(entry).resolve()
    if not entry.is_relative_to(base):
        raise ValueError('Корневая XSD находится за пределами комплекта')

    class LocalOnly(etree.Resolver):
        def resolve(self, url, public_id, context):
            parts=urlsplit(url)
            if parts.scheme!='file' or parts.netloc:
                raise OSError('Внешние ресурсы запрещены: '+url)
            target=Path(url2pathname(parts.path)).resolve()
            if not target.is_relative_to(base) or not target.is_file():
                raise OSError('XSD за пределами комплекта или отсутствует: '+url)
            return self.resolve_filename(str(target),context)

    parser=etree.XMLParser(no_network=True,resolve_entities=False,load_dtd=False)
    parser.resolvers.add(LocalOnly())
    document=etree.parse(entry.as_uri(),parser)
    if document.docinfo.doctype:
        raise ValueError('DTD в XSD не поддерживается')
    return etree.XMLSchema(document)


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
