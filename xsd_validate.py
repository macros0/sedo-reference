"""Standalone offline validator, also copied into downloadable XSD bundles."""
import argparse
import io
import json
import re
import sys
from functools import lru_cache
from itertools import islice
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname
from types import SimpleNamespace


_CALENDAR = re.compile(r'(?:(-?[0-9]{4,})-([0-9]{2})-([0-9]{2})T)?'
                       r'([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})?')
_MONTH_START = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


def calendar_value(kind, value):
    """Normalize time/dateTime without rounding the fractional seconds."""
    match = _CALENDAR.fullmatch(value.strip(' \t\r\n'))
    if not match or bool(match[1]) != (kind == 'dateTime'):
        return None
    hour, minute, second = map(int, match.groups()[3:6])
    fraction = (match[7] or '').rstrip('0')
    if hour > 24 or minute > 59 or second > 59 or (hour == 24 and (minute or second or fraction)):
        return None
    zone = match[8]
    offset = 0
    if zone and zone != 'Z':
        hours, minutes = int(zone[1:3]), int(zone[4:])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            return None
        offset = (-1 if zone[0] == '-' else 1) * (hours * 60 + minutes)
    day_delta, seconds = divmod((hour * 60 + minute - offset) * 60 + second, 86400)
    if kind == 'time':
        return bool(zone), seconds, fraction
    try:
        year, month, day = map(int, match.groups()[:3])
    except ValueError:
        return None
    if not year or not 1 <= month <= 12:
        return None
    # The schema validates the date itself. Convert it to a Gregorian day
    # number so offsets can carry across months and years, skipping year zero.
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    prior = year - 1
    ordinal = 365 * prior + prior // 4 - prior // 100 + prior // 400 + (366 if year < 0 else 0)
    ordinal += _MONTH_START[month - 1] + day + (leap and month > 2)
    return bool(zone), ordinal + day_delta, seconds, fraction


def compile_schema(entry, package_root, *, replacements=None):
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
                    relative = target.relative_to(base).as_posix()
                    alternate = (base/(replacements or {}).get(relative, relative)).resolve()
                    if not alternate.is_relative_to(base):
                        raise OSError('XSD за пределами комплекта')
                    return self.resolve_string(alternate.read_bytes(),context,base_url=target.as_uri())
            raise OSError('XSD за пределами комплекта или отсутствует: '+url)

    parser=etree.XMLParser(no_network=True,resolve_entities=False,load_dtd=False)
    parser.resolvers.add(LocalOnly())
    document=etree.parse(entry.as_uri(),parser)
    if document.docinfo.doctype:
        raise ValueError('DTD в XSD не поддерживается')
    primary=etree.XMLSchema(document)
    manifest_file=base/'manifest.json'
    if replacements is None and manifest_file.is_file():
        manifest=json.loads(manifest_file.read_text('utf-8-sig'))
        if entry==(base/manifest.get('entrypoint','')).resolve():
            if manifest.get('boolean_fixed_replacements'):
                alternate = compile_schema(entry, base, replacements=manifest['boolean_fixed_replacements'])
                primary = BooleanFixedValidator(primary, alternate)
            if manifest.get('attribute_checks') or manifest.get('attribute_type_checks') or manifest.get('attribute_element_checks'):
                supplement=compile_schema(base/manifest['supplementary_schema'],base)
                return AttributeUseValidator(primary,supplement,manifest)
    return primary


class BooleanFixedValidator:
    """Recheck fixed-value aliases with an alternate schema and typed probes."""
    def __init__(self, primary, alternate):
        self.primary = primary
        self.alternate = alternate
        self.typed = False
        self.calendar_attributes = False
        self.error_log = []

    def validate(self, document):
        valid = self.primary.validate(document)
        self.error_log = list(self.primary.error_log)
        if valid:
            return True
        def alias(error):
            if self.calendar_attributes and error.type_name == 'SCHEMAV_CVC_AU':
                match = re.search(r": The value '([^']*)' does not match the fixed value constraint '([^']*)'\.$", error.message)
                if match:
                    kind = 'dateTime' if 'T' in match[2] else 'time'
                    actual = calendar_value(kind, match[1])
                    if actual is not None and actual == calendar_value(kind, match[2]):
                        return True
            match = re.search(r": The actual value '[ \t\r\n]*(true|1|false|0)[ \t\r\n]*' does not match the fixed value constraint '[ \t\r\n]*(true|1|false|0)[ \t\r\n]*'\.$", error.message)
            if self.typed and error.type_name == 'SCHEMAV_CVC_ELT_5_2_2_2_2' and not match:
                return True
            return (error.type_name == 'SCHEMAV_CVC_ELT_5_2_2_2_2' and match
                    and ((match[1] in ('true','1')) == (match[2] in ('true','1'))))
        if not any(alias(error) for error in self.error_log):
            return False
        # The alternate retains datatypes, patterns, and instance xsi:type.
        # Typed probes enforce scalar fixed; legacy packs retain disagreements.
        retained = [error for error in self.error_log if not alias(error)]
        alternate_valid = self.alternate.validate(document)
        identities = {(e.path, e.message) for e in retained}
        self.error_log = retained + [e for e in self.alternate.error_log if (e.path, e.message) not in identities]
        return alternate_valid and not retained


class AttributeUseValidator:
    """Apply native attribute fixed comparison to attribute uses and scalar text."""
    def __init__(self, primary, supplementary, manifest):
        self.primary=primary;self.supplementary=supplementary
        if isinstance(primary, BooleanFixedValidator):
            rules = [rule for checks in [manifest['attribute_checks'], *[p['checks'] for p in manifest.get('attribute_type_checks', [])],
                                        *[p['checks'] for p in manifest.get('attribute_element_checks', [])]] for rule in checks]
            primary.typed = any(rule.get('element_value') for rule in rules)
            primary.calendar_attributes = any('calendar_fixed' in rule and not rule.get('element_value') for rule in rules)
        self.namespace=manifest['supplementary_namespace']
        self.complete_types = manifest.get('attribute_type_profiles_complete', False)
        self.type_whitespace={tuple(item['type']):item['mode'] for item in manifest.get('element_type_whitespace',[])}
        self.error_log=[]
        def profile(checks, paths, wildcards, any_types):
            rules={}
            for rule in checks:rules.setdefault(tuple(map(tuple,rule['path'])),[]).append(rule)
            modes={}
            for wildcard in wildcards:
                modes.setdefault(tuple(map(tuple,wildcard['path'])),[]).append(wildcard)
            return rules,{tuple(map(tuple,path)) for path in paths},modes,{tuple(map(tuple,path)) for path in any_types}
        self.root_profile=profile(manifest['attribute_checks'],manifest.get('attribute_wildcard_paths',[]),manifest.get('attribute_wildcards',[]),manifest.get('attribute_any_type_paths',[]))
        self.known_elements=set(map(tuple,manifest.get('attribute_known_elements',[])))
        self.by_type={tuple(item['type']):profile(item['checks'],item.get('wildcard_paths',[]),item.get('wildcards',[]),item.get('any_type_paths',[]))
                      for item in manifest.get('attribute_type_checks',[])}
        self.by_element={tuple(item['element']):profile(item['checks'],item.get('wildcard_paths',[]),item.get('wildcards',[]),item.get('any_type_paths',[]))
                         for item in manifest.get('attribute_element_checks',[])}

    def validate(self, document, *, check_attributes_on_invalid=False):
        from lxml import etree
        primary_valid = self.primary.validate(document)
        if not primary_valid and not check_attributes_on_invalid:
            self.error_log=self.primary.error_log;return False
        primary_errors = list(self.primary.error_log)
        root=document.getroot() if hasattr(document,'getroot') else document
        prefix='sedoprobe'
        while any(prefix in node.nsmap for node in root.iter() if isinstance(node.tag,str)):prefix+='x'
        probes=etree.Element('{'+self.namespace+'}checks',nsmap={prefix:self.namespace})
        subjects={}

        @lru_cache(maxsize=128)
        def child_layout(parent):
            children=list(islice((child for child in parent if isinstance(child.tag,str)),257))
            return ([[name.namespace or '',name.localname] for name in map(etree.QName,children)],
                    {child:index for index,child in enumerate(children)})

        def matches_guard(node,guard):
            for _ in range(guard['up']):
                node=node.getparent()
                if node is None:return False
            parent=node.getparent()
            if parent is None:return False
            names,positions=child_layout(parent)
            return any(layout['index']==positions.get(node) and layout['children']==names
                       for layout in guard['layouts'])

        def visit(node,path,scopes,lax=False):
            if not isinstance(node.tag,str):return
            qname=etree.QName(node);path=path+((qname.namespace or '',qname.localname),)
            ns,name=path[-1]
            mode='lax' if lax else None
            for depth,(_,_,wildcards,_) in scopes:
                for wildcard in wildcards.get(path[depth:-1],[]):
                    allowed=wildcard['namespace'].split();target=wildcard['target_namespace']
                    if ([ns,name] not in wildcard['explicit']
                        and ('##any' in allowed or ns in allowed or ('##targetNamespace' in allowed and ns==target)
                             or ('##local' in allowed and not ns) or ('##other' in allowed and ns and ns!=target))):
                        mode=wildcard['process']
            if mode=='skip':return
            if lax or any(path[depth:] in routes for depth,(_,routes,_,_) in scopes):
                profile=self.by_element.get(path[-1])
                if profile:scopes=(*scopes,(len(path),profile))
            # fixed belongs to the element declaration even when xsi:type
            # replaces its declared type and descendant content model.
            fixed_rules=[rule for depth,(rules,_,_,_) in scopes
                         for rule in rules.get(path[depth:],[]) if rule.get('element_value')]
            xtype=node.get('{http://www.w3.org/2001/XMLSchema-instance}type')
            whitespace=None
            lax_children=(mode=='lax' and path[-1] not in self.known_elements
                          or any(path[depth:] in any_types for depth,(_,_,_,any_types) in scopes))
            if xtype:
                prefix,_,name=xtype.strip().rpartition(':');namespace=node.nsmap.get(prefix or None,'')
                whitespace=({'string':'preserve','anySimpleType':'preserve','normalizedString':'replace'}.get(name,'collapse')
                            if namespace=='http://www.w3.org/2001/XMLSchema' else self.type_whitespace.get((namespace,name)))
                lax_children=(namespace,name)==('http://www.w3.org/2001/XMLSchema','anyType')
                if self.complete_types:
                    scopes=()
                profile=self.by_type.get((namespace,name))
                if profile:scopes=(*scopes,(len(path),profile))
            applicable=list(fixed_rules)
            for depth,(rules,_,_,_) in scopes:applicable.extend(rules.get(path[depth:],[]))
            seen=set()
            for rule in applicable:
                if not all(matches_guard(node,guard) for guard in rule.get('guards',())):continue
                identity=(tuple(rule['attribute']),rule['element'])
                if identity in seen:continue
                seen.add(identity)
                ns,name=rule['attribute']
                value=''.join(node.itertext()) if rule.get('element_value') else node.get(('{'+ns+'}' if ns else '')+name)
                # Only absent original character content receives fixed/default.
                # Whitespace that normalizes to empty is still an actual value.
                if rule.get('element_value') and value=='':continue
                if rule.get('element_value') and whitespace in ('replace','collapse'):
                    value=re.sub(r'[\t\r\n]',' ',value)
                    if whitespace=='collapse':value=re.sub(' +',' ',value).strip(' ')
                if value is None:continue
                probe=etree.SubElement(probes,'{'+self.namespace+'}'+rule['element'],nsmap=node.nsmap)
                probe.set('value',value);probe.sourceline=node.sourceline
                subjects[probe]=(node,rule)
            for child in node:visit(child,path,scopes,lax_children)
        visit(root,(),((0,self.root_profile),))
        valid=self.supplementary.validate(probes);self.error_log=primary_errors
        failed=set()
        if not valid:
            probe_tree=probes.getroottree()
            by_probe_path={probe_tree.getpath(probe):probe for probe in subjects}
            for error in self.supplementary.error_log:
                # One declaration can be reused at several XML paths and on the
                # same line. The generated probe XPath identifies its exact use.
                probe=by_probe_path.get(error.path)
                subject=subjects.get(probe)
                if subject:
                    failed.add(probe)
                    node,rule=subject;ns,name=rule['attribute'];attribute=('{'+ns+'}' if ns else '')+name
                    if rule.get('element_value'):
                        reason=re.sub(r"^Element '[^']+', attribute 'value':\s*",'',error.message)
                        message="Element '"+node.tag+"': "+reason
                    else:message="Element '"+node.tag+"', attribute '"+attribute+"': "+error.message
                else:message=error.message
                self.error_log.append(SimpleNamespace(line=error.line,column=error.column,message=message,
                    path=node.getroottree().getpath(node) if subject else None))
        for probe, (node, rule) in subjects.items():
            if 'calendar_fixed' not in rule or probe in failed:
                continue
            kind, fixed = rule['calendar_fixed']
            actual = probe.get('value')
            parsed = calendar_value(kind, actual)
            if parsed is not None and parsed == calendar_value(kind, fixed):
                continue
            valid = False
            if parsed is None:
                reason = f"'{actual}' is not a valid value of the atomic type 'xs:{kind}'."
                type_name = 'SCHEMAV_CVC_DATATYPE_VALID_1_2_1'
            else:
                reason = f"The actual value '{actual}' does not match the fixed value constraint '{fixed}'."
                type_name = 'SCHEMAV_CVC_ELT_5_2_2_2_2' if rule.get('element_value') else 'SCHEMAV_CVC_AU'
            ns, name = rule['attribute']
            attribute = name if not ns else '{'+ns+'}'+name
            subject = f"Element '{node.tag}'"
            if not rule.get('element_value'):
                subject += f", attribute '{attribute}'"
            self.error_log.append(SimpleNamespace(line=node.sourceline, column=0, type_name=type_name,
                message=f"{subject}: {reason}", path=node.getroottree().getpath(node)))
        return primary_valid and valid


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
