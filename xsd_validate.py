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
_DURATION = re.compile(r'(-)?P(?:([0-9]+)Y)?(?:([0-9]+)M)?(?:([0-9]+)D)?'
                       r'(?:T(?:([0-9]+)H)?(?:([0-9]+)M)?(?:([0-9]+(?:\.[0-9]*)?|\.[0-9]+)S)?)?')
_MONTH_START = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


def calendar_value(kind, value):
    """Normalize XSD calendar values without rounding fractional seconds."""
    value = value.strip(' \t\r\n')
    if kind == 'duration':
        match = _DURATION.fullmatch(value)
        if not match or not any(match.groups()[1:]) or ('T' in value and not any(match.groups()[4:])):
            return None
        negative, years, months, days, hours, minutes, seconds = match.groups()
        whole, _, fraction = (seconds or '0').partition('.')
        fraction = fraction.rstrip('0')
        try:
            y, m, d, h, minute, second = (int((part or '').lstrip('0') or '0')
                                           for part in (years, months, days, hours, minutes, whole))
        except ValueError:
            return None
        total_months = y * 12 + m
        total_seconds = d * 86400 + h * 3600 + minute * 60 + second
        # Months are independent of elapsed seconds. Keep the fraction as
        # digits, including leading zeros, and treat both signs of zero alike.
        return bool(negative and (total_months or total_seconds or fraction)), total_months, total_seconds, fraction
    match = _CALENDAR.fullmatch(value)
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


_DURATION_DATES = ((1696, 9), (1697, 2), (1903, 3), (1903, 7))


def _duration_instant(year, month, value):
    negative, months, seconds, fraction = value
    year, month = divmod(year * 12 + month - 1 + (-months if negative else months), 12)
    previous = year - 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = 365 * previous + previous // 4 - previous // 100 + previous // 400
    days += _MONTH_START[month] + (leap and month >= 2)
    return days * 86400 + (-seconds if negative else seconds), fraction, negative


def _order(left, right):
    return (left > right) - (left < right)


def _compare_instant(left, right):
    first, fraction, negative = left
    second, other_fraction, other_negative = right
    sign = 0 if not fraction else -1 if negative else 1
    other_sign = 0 if not other_fraction else -1 if other_negative else 1
    return _order(first, second) or _order(sign, other_sign) or sign * _order(fraction, other_fraction)


def _calendar_order(kind, actual, bound):
    a, b = calendar_value(kind, actual), calendar_value(kind, bound)
    if a is None or b is None:
        return None
    if a == b:
        return 0
    if kind == 'duration':
        orders = {_compare_instant(_duration_instant(y, m, a), _duration_instant(y, m, b))
                  for y, m in _DURATION_DATES}
        return orders.pop() if len(orders) == 1 and 0 not in orders else None
    if a[0] == b[0]:
        return _order(a[1:], b[1:])
    # A timezone-less date or time is comparable only when all permitted
    # offsets put it strictly on the same side of the timezone-aware value.
    clock = lambda parts: (parts[1] * 86400 + parts[2], parts[3]) if kind == 'dateTime' else parts[1:]
    known, unknown = (clock(a), clock(b)) if a[0] else (clock(b), clock(a))
    outside = 1 if known > (unknown[0] + 14 * 3600, unknown[1]) else (
        -1 if known < (unknown[0] - 14 * 3600, unknown[1]) else None)
    return outside if outside is None or a[0] else -outside


def _calendar_facet_result(facets, value, resolve, compare_item, namespaces):
    kind, lexical = value
    if kind in ('unknown', 'excluded'):
        return kind
    enum_results = []
    for facet, bound, scope in facets:
        if facet == 'enumeration':
            expected = resolve(bound, scope)
            status, _ = _compare_compound(value, expected,
                lambda name, actual, fixed: compare_item(name, actual, fixed, namespaces, scope))
            enum_results.append(status)
            continue
        order = _calendar_order(kind, lexical, bound) if kind in ('dateTime', 'time', 'duration') else None
        if order is None:
            return 'excluded'
        if facet == 'minInclusive' and order < 0 or facet == 'minExclusive' and order <= 0 or (
                facet == 'maxInclusive' and order > 0 or facet == 'maxExclusive' and order >= 0):
            return 'excluded'
    if enum_results and 'equal' not in enum_results:
        return 'unknown' if 'unknown' in enum_results or 'native' in enum_results else 'excluded'
    return kind


def _normalize_whitespace(value, mode):
    if mode not in ('replace', 'collapse'):
        return value
    value = re.sub(r'[\t\r\n]', ' ', value)
    return re.sub(' +', ' ', value).strip(' ') if mode == 'collapse' else value


def _compound_value(shape, value, matches, compare_item=None, namespaces=None):
    namespaces = namespaces or {}

    def resolve(nested, lexical, scope=namespaces):
        return _compound_value(nested, lexical, matches, compare_item, scope)

    if isinstance(shape, str):
        return shape, value
    kind, members = shape[:2]
    if kind == 'limited':
        return 'unknown', None
    if kind == 'whitespace':
        return resolve(members, _normalize_whitespace(value, shape[2]))
    if kind == 'duration-enumeration':
        parsed = calendar_value('duration', value)
        if parsed is None or not any(parsed == calendar_value('duration', bound) for bound in shape[2]):
            return 'excluded', None
        return resolve(members, value)
    if kind == 'calendar-facets':
        actual = resolve(members, value)
        outcome = _calendar_facet_result(shape[2], actual, lambda lexical, scope: resolve(members, lexical, scope),
                                         compare_item, namespaces)
        return actual if outcome not in ('excluded', 'unknown') else (outcome, None)
    if kind == 'list':
        item_probe = shape[2] if len(shape) == 3 else None
        items = [resolve(members, token) for token in re.findall(r'[^ \t\r\n]+', value)]
        for failure in ('excluded', 'unknown'):
            if any(item[0] == failure for item in items):
                return failure, None
        return kind, (item_probe, items)
    excluded = False
    for probe, member in members:
        if matches(probe, value, namespaces):
            result = resolve(member, value)
            if result[0] != 'excluded':
                return result
            excluded = True
    return ('excluded' if excluded else 'unknown'), None


def _compare_compound(actual, expected, compare_item):
    kind, value = actual
    expected_kind, fixed = expected
    if 'excluded' in (kind, expected_kind):
        return 'different', ''
    if 'unknown' in (kind, expected_kind):
        return 'unknown', ''
    if kind != expected_kind:
        return 'different', ''
    if kind == 'native':
        return 'native', value
    if kind.startswith('item'):
        return compare_item(kind, value, fixed), ''
    if kind in ('dateTime', 'time', 'duration'):
        parsed = calendar_value(kind, value)
        return ('equal', fixed) if parsed is not None and parsed == calendar_value(kind, fixed) else ('different', '')
    probe, items = value
    fixed_probe, fixed_items = fixed
    if len(items) != len(fixed_items):
        return 'different', ''
    results = []
    for left, right in zip(items, fixed_items):
        status, token = _compare_compound(left, right, compare_item)
        if status == 'native' and (probe or fixed_probe):
            status = compare_item(probe, left[1], right[1]) if probe == fixed_probe else 'unknown'
        results.append((status, token))
    kinds = {status for status, _ in results}
    for failure in ('different', 'unknown'):
        if failure in kinds:
            return failure, ''
    if 'native' in kinds:
        # Legacy validation packs used a fixed probe for the whole list.
        return 'native', ' '.join(token for _, token in results)
    return 'equal', ''


def _same_scoped_literal(actual, fixed, namespaces, fixed_namespaces):
    if actual != fixed:
        return False
    prefixes = {token.rpartition(':')[0] for token in re.findall(r'[^ \t\r\n]+', fixed)}
    return all(namespaces.get(prefix, '') == fixed_namespaces.get(prefix, '') for prefix in prefixes)


def compound_probes(supplementary, namespace, prefix='sedoprobe'):
    from lxml import etree

    def probe_root():
        return etree.Element('{'+namespace+'}checks', nsmap={prefix:namespace})

    def bindings(scope):
        return {key or None: uri for key, uri in scope}

    @lru_cache(maxsize=1024)
    def accepts(element, value, scope):
        root = probe_root()
        candidate = etree.SubElement(root, '{'+namespace+'}'+element, nsmap=bindings(scope))
        candidate.set('value', value)
        result = supplementary.validate(root)
        return result, any(error.type_name != 'SCHEMAV_CVC_AU' for error in supplementary.error_log)

    @lru_cache(maxsize=1024)
    def compare(element, actual, expected, actual_scope, fixed_scope):
        root = probe_root()
        pair = etree.SubElement(root, '{'+namespace+'}'+element)
        for child, value, scope in (('expected', expected, fixed_scope), ('actual', actual, actual_scope)):
            node = etree.SubElement(pair, '{'+namespace+'}'+child, nsmap=bindings(scope))
            node.set('value', value)
        if supplementary.validate(root):
            return 'equal'
        errors = supplementary.error_log
        if errors and all(error.type_name == 'SCHEMAV_CVC_IDC' and 'No match found for key-sequence' in error.message
                          for error in errors):
            return 'different'
        return 'unknown'

    def compare_item(element, actual, fixed, scope, expected):
        return compare(element, actual, fixed, tuple(sorted(scope.items())), tuple(sorted(expected.items())))

    return accepts, compare_item


XSD = 'http://www.w3.org/2001/XMLSchema'
XSI = 'http://www.w3.org/2001/XMLSchema-instance'


def schema_mentions_id(root):
    return any(token.rsplit(':', 1)[-1] == 'ID'
               and node.nsmap.get(token.partition(':')[0] if ':' in token else None) == XSD
               for node in root.iter() if isinstance(node.tag, str) and node.tag.startswith('{'+XSD+'}')
               for attr in ('type', 'base', 'memberTypes') for token in node.get(attr, '').split())


def schema_has_skip(roots):
    return any(node.get('processContents') == 'skip' for root in roots
               for node in root.iter('{'+XSD+'}any'))


def compile_schema(entry, package_root, *, replacements=None):
    from lxml import etree
    base=Path(package_root).resolve()
    entry=Path(entry).resolve()
    if not entry.is_relative_to(base):
        raise ValueError('Корневая XSD находится за пределами комплекта')

    loaded_sources = {}

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
                    content = alternate.read_bytes()
                    loaded_sources[target] = content
                    return self.resolve_string(content,context,base_url=target.as_uri())
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
            source_roots = [etree.fromstring(raw, parser) for raw in loaded_sources.values()]
            if not manifest.get('document_ids_complete') and any(schema_mentions_id(root) for root in source_roots):
                raise ValueError('В старом комплекте отсутствуют профили ID. Соберите комплект заново.')
            supplementary = (compile_schema(base/manifest['supplementary_schema'],base)
                             if manifest.get('supplementary_schema') else None)
            return AttributeUseValidator(primary,supplementary,manifest,
                                         unprofiled_skip=supplementary is None and schema_has_skip(source_roots))
    return primary


class BooleanFixedValidator:
    """Recheck fixed-value aliases with an alternate schema and typed probes."""
    def __init__(self, primary, alternate):
        self.primary = primary
        self.alternate = alternate
        self.typed = False
        self.calendar_attributes = False
        self.compound_attributes = False
        self.error_log = []

    def validate(self, document):
        valid = self.primary.validate(document)
        self.error_log = list(self.primary.error_log)
        if valid:
            return True
        def alias(error):
            if (self.calendar_attributes or self.compound_attributes) and error.type_name == 'SCHEMAV_CVC_AU':
                match = re.search(r": The value '(.*)' does not match the fixed value constraint '(.*)'\.$", error.message, re.S)
                if match:
                    if self.compound_attributes:
                        # The alternate and the exact probes together keep
                        # unrelated string or datatype constraints in force.
                        return True
                    fixed = match[2].lstrip(' \t\r\n')
                    kind = 'duration' if fixed.startswith(('P', '-P')) else 'dateTime' if 'T' in fixed else 'time'
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
    def __init__(self, primary, supplementary, manifest, *, unprofiled_skip=False):
        self.unprofiled_skip=unprofiled_skip
        self.primary=primary;self.supplementary=supplementary
        global_rules=manifest.get('attribute_global_checks', [])
        self.global_attributes={('{'+ns+'}' if ns else '')+name: rule for rule in global_rules
                                for ns,name in [rule['attribute']]}
        if isinstance(primary, BooleanFixedValidator) and supplementary is not None:
            rules = [rule for checks in [manifest['attribute_checks'], global_rules, *[p['checks'] for p in manifest.get('attribute_type_checks', [])],
                                        *[p['checks'] for p in manifest.get('attribute_element_checks', [])]] for rule in checks]
            primary.typed = any(rule.get('element_value') for rule in rules)
            primary.calendar_attributes = any('calendar_fixed' in rule and not rule.get('element_value') for rule in rules)
            primary.compound_attributes = any('calendar_compound' in rule and not rule.get('element_value') for rule in rules)
        self.namespace=manifest.get('supplementary_namespace', 'urn:sedo:attribute-value-checks')
        self.id_types={tuple(item['type']):item['shape'] for item in manifest.get('id_types', [])}
        self.document_value_probes = frozenset(manifest.get('document_value_probes', ()))
        self.complete_types = manifest.get('attribute_type_profiles_complete', False)
        self.type_whitespace={tuple(item['type']):item['mode'] for item in manifest.get('element_type_whitespace',[])}
        self.type_values={tuple(item['type']):item for item in
                          manifest.get('element_type_values', manifest.get('element_type_unions', []))}
        self.error_log=[]
        def profile(checks, paths, wildcards, any_types):
            rules={}
            for rule in checks:rules.setdefault(tuple(map(tuple,rule['path'])),[]).append(rule)
            modes={}
            for wildcard in wildcards:
                modes.setdefault(tuple(map(tuple,wildcard['path'])),[]).append(wildcard)
            return rules,{tuple(map(tuple,path)) for path in paths},modes,{tuple(map(tuple,path)) for path in any_types}
        self.root_profile=profile(manifest.get('attribute_checks',[]),manifest.get('attribute_wildcard_paths',[]),manifest.get('attribute_wildcards',[]),manifest.get('attribute_any_type_paths',[]))
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
        isolated=[]
        subjects={}
        owners={}
        id_errors=[]
        accepts, item = compound_probes(self.supplementary, self.namespace, prefix)

        assessed_ids=None
        def assessed_id(node):
            nonlocal assessed_ids
            if not self.unprofiled_skip:return True
            if assessed_ids is None:
                # One native pass distinguishes typed nodes from unassessed
                # skip subtrees when a package has no path profiles.
                from copy import deepcopy
                copied=deepcopy(root)
                candidates=[]
                for original,target in zip(root.iter(),copied.iter()):
                    value=original.get('{'+XSI+'}type','').strip()
                    prefix,_,name=value.rpartition(':')
                    if name=='ID' and original.nsmap.get(prefix or None)==XSD:
                        target.set('{'+XSI+'}type', ':sedoId'+str(len(candidates)))
                        candidates.append(original)
                self.primary.validate(copied)
                assessed_ids=set()
                for error in self.primary.error_log:
                    match=re.search(r"attribute '\{"+re.escape(XSI)+r"\}type': ':sedoId(\d+)' is not a valid value",error.message)
                    if match and int(match[1])<len(candidates):assessed_ids.add(candidates[int(match[1])])
            return node in assessed_ids

        def record_id(node, owner, shape, value, attribute=None):
            scope=tuple(sorted((key or '', uri) for key,uri in node.nsmap.items()))
            while isinstance(shape, list):
                shape=next((nested for name,nested in shape if accepts(name,value,scope)[0]),False)
            if not shape or owner is None:return
            value=_normalize_whitespace(value,'collapse')
            prior=owners.get(value)
            if prior is None:
                owners[value]=(owner,node)
                return
            if prior[0] is owner:
                if self.unprofiled_skip and not assessed_id(prior[1]):owners[value]=(owner,node)
                return
            if not assessed_id(prior[1]):
                owners[value]=(owner,node)
                return
            if not assessed_id(node):return
            subject=f"Element '{node.tag}'" + (f", attribute '{attribute}'" if attribute else '')
            id_errors.append(SimpleNamespace(line=node.sourceline,column=0,type_name='SCHEMAV_CVC_IDC',
                message=f"{subject}: Значение ID '{value}' уже принадлежит другому элементу.",
                path=node.getroottree().getpath(node)))

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
            effective_value=None
            lax_children=(mode=='lax' and path[-1] not in self.known_elements
                          or any(path[depth:] in any_types for depth,(_,_,_,any_types) in scopes))
            if xtype:
                prefix,_,name=xtype.strip().rpartition(':');namespace=node.nsmap.get(prefix or None,'')
                whitespace=({'string':'preserve','anySimpleType':'preserve','normalizedString':'replace'}.get(name,'collapse')
                            if namespace=='http://www.w3.org/2001/XMLSchema' else self.type_whitespace.get((namespace,name)))
                shape=self.id_types.get((namespace,name), (namespace,name)==(XSD,'ID'))
                if shape and node.get('{'+XSI+'}nil') not in ('true','1'):
                    record_id(node,node.getparent(),shape,''.join(node.itertext()))
                effective_value=self.type_values.get((namespace,name))
                lax_children=(namespace,name)==('http://www.w3.org/2001/XMLSchema','anyType')
                if self.complete_types:
                    scopes=()
                profile=self.by_type.get((namespace,name))
                if profile:scopes=(*scopes,(len(path),profile))
            applicable=list(fixed_rules)
            for depth,(rules,_,_,_) in scopes:applicable.extend(rules.get(path[depth:],[]))
            if lax_children and self.global_attributes:
                applicable.extend(self.global_attributes[name] for name in node.attrib if name in self.global_attributes)
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
                if rule.get('element_value'):
                    if effective_value is not None and (fixed := rule.get('fixed_value')) is not None:
                        rule = dict(rule, element=effective_value['element'], instance_value=True,
                                    calendar_compound=dict(shape=effective_value['shape'], fixed=fixed['lexical'],
                                                           namespaces=fixed['namespaces']))
                        rule.pop('calendar_fixed', None)
                    else:
                        value=_normalize_whitespace(value, whitespace)
                if value is None:continue
                if rule.get('id_shape') and (not rule.get('element_value') or not xtype):
                    owner=node.getparent() if rule.get('element_value') else node
                    record_id(node,owner,rule['id_shape'],value,None if rule.get('element_value') else name)
                if rule.get('identity_only'):continue
                container = probes
                if rule['element'] in self.document_value_probes:
                    container = etree.Element(probes.tag, nsmap=probes.nsmap)
                    isolated.append(container)
                probe=etree.SubElement(container,'{'+self.namespace+'}'+rule['element'],nsmap=node.nsmap)
                probe.set('value',value);probe.sourceline=node.sourceline
                subjects[probe]=(node,rule)
            for child in node:visit(child,path,scopes,lax_children)
        visit(root,(),((0,self.root_profile),))
        valid=not id_errors
        self.error_log=primary_errors+id_errors
        failed=set()
        for batch in (probes, *isolated):
            if not len(batch) or self.supplementary.validate(batch):
                continue
            valid=False
            probe_tree=batch.getroottree()
            by_probe_path={probe_tree.getpath(probe):probe for probe in batch}
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
                self.error_log.append(SimpleNamespace(line=node.sourceline if subject else error.line,column=error.column,message=message,
                    path=node.getroottree().getpath(node) if subject else None))
        def matches(name, value, scope):
            return accepts(name, value, tuple(sorted(scope.items())))[0]

        for probe, (node, rule) in subjects.items():
            if probe in failed or not ('calendar_fixed' in rule or 'calendar_compound' in rule):
                continue
            actual = probe.get('value')
            type_name = 'SCHEMAV_CVC_ELT_5_2_2_2_2' if rule.get('element_value') else 'SCHEMAV_CVC_AU'
            if 'calendar_compound' in rule:
                compound = rule['calendar_compound']
                fixed, shape = compound.get('fixed'), compound['shape']
                namespaces = {key or '': uri for key, uri in node.nsmap.items()}
                fixed_namespaces = compound.get('namespaces', {})
                outcome = 'unknown'
                if not compound.get('limited'):
                    left = _compound_value(shape, actual, matches, item, namespaces)
                    if fixed is None:
                        if left[0] not in ('unknown', 'excluded'):
                            continue
                    elif matches(rule['element'], fixed, fixed_namespaces):
                        right = _compound_value(shape, fixed, matches, item, fixed_namespaces)
                        outcome, lexical = _compare_compound(left, right,
                            lambda name, a, b: item(name, a, b, namespaces, fixed_namespaces))
                        if outcome == 'native' and 'comparison' in compound:
                            equivalent, facet_error = accepts(compound['comparison'], lexical, tuple(sorted(namespaces.items())))
                            outcome = 'equal' if equivalent else 'unknown' if facet_error and lexical != actual else 'different'
                if (outcome == 'unknown' and fixed is not None and not rule.get('instance_value')
                        and _same_scoped_literal(actual, fixed, namespaces, fixed_namespaces)):
                    outcome = 'equal'
                if outcome == 'equal':
                    continue
                if fixed is None:
                    reason = f"Значение '{actual}' нарушает точное календарное ограничение типа XSD."
                    type_name = 'SCHEMAV_CVC_DATATYPE_VALID_1_2_1'
                else:
                    reason = (f"Не удалось точно сравнить значение '{actual}' с fixed '{fixed}'." if outcome == 'unknown'
                              else f"The actual value '{actual}' does not match the fixed value constraint '{fixed}'.")
            else:
                kind, fixed = rule['calendar_fixed']
                parsed = calendar_value(kind, actual)
                if parsed is not None and parsed == calendar_value(kind, fixed):
                    continue
                if parsed is None:
                    reason = f"'{actual}' is not a valid value of the atomic type 'xs:{kind}'."
                    type_name = 'SCHEMAV_CVC_DATATYPE_VALID_1_2_1'
                else:
                    reason = f"The actual value '{actual}' does not match the fixed value constraint '{fixed}'."
            valid = False
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
