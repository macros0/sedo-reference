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
from functools import lru_cache
from pathlib import Path, PurePosixPath
from lxml import etree
from xml.etree import ElementTree as ET
from schema import (archive_import_path, check_dependency_bases, import_basename, Schema, X, SchemaFiles,
                    archive_targets, write_archive_targets, BUILTIN_SIMPLE_TYPES, IDENTITY_CONSTRAINTS, inactive_particle)
from sync import sha, write
from xsd_validate import schema_mentions_id, _calendar_order, _compound_value, compile_schema, compound_probes

XS='http://www.w3.org/2001/XMLSchema'
REFERENCES={'{'+XS+'}'+name for name in ('import','include','redefine','override')}
LXML_VERSION='6.1.3'
NCNAME_START = ('A-Z_a-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u02ff\u0370-\u037d\u037f-\u1fff'
                '\u200c\u200d\u2070-\u218f\u2c00-\u2fef\u3001-\ud7ff\uf900-\ufdcf\ufdf0-\ufffd'
                '\U00010000-\U000effff')
NCNAME = re.compile(f'[{NCNAME_START}][{NCNAME_START}.0-9\u00b7\u0300-\u036f\u203f-\u2040-]*')


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
    result=SchemaFiles()
    for relative,digest in row['dependencies']:
        name=_source_path(posixpath.normpath(posixpath.join(base,relative)))
        if name in result:raise IntegrityError('Повторный путь зависимости XSD: '+name)
        try:content=archive.read(name)
        except KeyError as exc:raise IntegrityError('Отсутствует XSD: '+name) from exc
        if sha(content)!=_digest(digest):raise IntegrityError('Не совпадает контрольная сумма XSD: '+name)
        result[name]=content
    if entry not in result:raise IntegrityError('Отсутствует корневая XSD в зависимостях')
    result.import_targets={path: dict(edges)
                           for path,edges in archive_targets(archive).items() if path in result}
    return result


def _resolve(files, source, reference, namespace, targets=None):
    location=reference.get('schemaLocation')
    if not location:raise Unavailable('Не задан schemaLocation в '+source)
    is_import=reference.tag=='{'+XS+'}import'
    allowed={reference.get('namespace')} if is_import else {namespace,None}
    exact=(targets or {}).get(source,{}).get(location)
    if exact is not None and exact not in files:raise Unavailable('Не найдена точная зависимость XSD: '+location)
    expected=exact or archive_import_path(source,location)
    if expected in files:
        if files[expected][1].get('targetNamespace') not in allowed:raise Unavailable('Не совпадает namespace зависимости '+location)
        return expected
    candidates=[n for n,(content,root) in files.items() if posixpath.basename(n)==import_basename(location) and root.get('targetNamespace') in allowed]
    if candidates and len({sha(files[n][0]) for n in candidates})==1:return sorted(candidates)[0]
    raise Unavailable(('Неоднозначная' if candidates else 'Не найдена')+' зависимость XSD: '+location)


def _element_scalar_source(tree, node, restrictions=None):
    """Find the scalar type of simpleType or complexType/simpleContent.

    For simpleContent, the primary schema retains all derivation facets and
    attributes. Its scalar ancestor supplies the fixed value-space comparison.
    """
    return tree.scalar_source(node, restrictions)


CALENDAR_TYPES = ('dateTime', 'time', 'duration')
CALENDAR_VALUE_FACETS = {'enumeration', 'minInclusive', 'minExclusive', 'maxInclusive', 'maxExclusive'}
CALENDAR_FACET_TAGS = {X+name for name in CALENDAR_VALUE_FACETS}
ORDERED_FACETS = CALENDAR_VALUE_FACETS - {'enumeration'}
ORDERED_FACET_TAGS = {X+name for name in ORDERED_FACETS}


def _scalar_type(tree, node):
    reference = node.get('ref')
    if reference:
        node = tree.resolve(reference, node, ['attribute'])
    if node.find(X+'simpleType') is None and not (node.get('type') or node.get('base')):
        return None
    return tree.scalar_member(node)


def _calendar_restriction(tree, restriction, shape):
    facets = [[facet.tag[len(X):], facet.get('value'),
               {prefix or '': uri for prefix, uri in tree._value_scope(facet).items()}]
              for facet in restriction if facet.tag in CALENDAR_FACET_TAGS]
    inherited = []
    primitive = shape
    while isinstance(primitive, tuple) and primitive[0] in ('calendar-facets', 'whitespace'):
        kind, (primitive, data) = primitive
        if kind == 'calendar-facets':
            inherited.extend(data)
    if primitive in CALENDAR_TYPES:
        for name, value, _ in facets:
            if name not in ORDERED_FACETS:
                continue
            for base_name, base_value, _ in inherited:
                if base_name not in ORDERED_FACETS or name[:3] != base_name[:3]:
                    continue
                order = _calendar_order(primitive, value, base_value)
                direction = 1 if name.startswith('min') else -1
                if (order is None or order * direction < 0
                        or order == 0 and name.endswith('Inclusive') and base_name.endswith('Exclusive')):
                    raise Unavailable('Точное календарное ограничение '+name+' ослабляет базовое '+base_name
                                      +' в '+tree.owner[restriction])
    return ('calendar-facets', (shape, facets)) if facets else shape


def _value_shape(tree, member, stack=(), *, list_item=False):
    """Describe the exact calendar comparisons within a scalar, list or union."""
    if member is None or isinstance(member, str):
        if member in CALENDAR_TYPES:
            return member, True
        if not list_item or not member:
            return 'native', False
        shape = ('atomic', member)
        if member == 'normalizedString':
            shape = ('whitespace', (shape, 'replace'))
        elif member in {'token', 'language', 'NMTOKEN', 'Name', 'NCName', 'ID', 'IDREF', 'ENTITY'}:
            shape = ('whitespace', (shape, 'collapse'))
        return shape, False
    if member in stack:
        raise ValueError('Циклический простой тип XSD')
    stack = (*stack, member)
    restriction = member.find(X+'restriction')
    if restriction is not None:
        base = restriction.get('base')
        source = restriction if base else restriction.find(X+'simpleType')
        shape, calendar = _value_shape(tree, tree._enum_member_type(base, source), stack, list_item=list_item)
        if list_item and (facet := restriction.find(X+'whiteSpace')) is not None:
            shape = ('whitespace', (shape, facet.get('value')))
        return (_calendar_restriction(tree, restriction, shape) if calendar else shape), calendar
    union = member.find(X+'union')
    if union is not None:
        choices, calendar = [], False
        for name, source in tree._union_members(union):
            shape, calendar_member = _value_shape(
                tree, tree._enum_member_type(name, source), stack, list_item=True)
            choices.append((name, source, shape))
            calendar |= calendar_member
        if calendar or list_item and any(shape != 'native' for _, _, shape in choices):
            return ('union', choices), calendar
    sequence = member.find(X+'list')
    if sequence is not None:
        item = sequence.get('itemType')
        source = sequence if item else sequence.find(X+'simpleType')
        shape, calendar = _value_shape(tree, tree._enum_member_type(item, source), stack, list_item=True)
        return ('list', shape), calendar
    return 'native', False


def _shape_has_calendar_facets(shape):
    if isinstance(shape, str):
        return False
    kind, content = shape
    if kind == 'calendar-facets':
        return True
    if kind == 'list':
        return _shape_has_calendar_facets(content)
    if kind == 'whitespace':
        return _shape_has_calendar_facets(content[0])
    return kind == 'union' and any(_shape_has_calendar_facets(member) for _, _, member in content)


def _ordered_calendar_facets(shape):
    if isinstance(shape, str):
        return set()
    kind, content = shape
    if kind == 'calendar-facets':
        nested, facets = content
        return {name for name, _, _ in facets if name in ORDERED_FACETS} | _ordered_calendar_facets(nested)
    if kind == 'list':
        return _ordered_calendar_facets(content)
    if kind == 'whitespace':
        return _ordered_calendar_facets(content[0])
    if kind == 'union':
        return set().union(*(_ordered_calendar_facets(member) for _, _, member in content))
    return set()


DOCUMENT_VALUE_TYPES = frozenset({'ID', 'IDREF', 'IDREFS', 'ENTITY', 'ENTITIES'})


def _has_document_value_type(shape):
    if isinstance(shape, str):
        return False
    kind, content = shape
    if kind == 'atomic':
        return content in DOCUMENT_VALUE_TYPES
    if kind in ('calendar-facets', 'whitespace'):
        return _has_document_value_type(content[0])
    if kind == 'list':
        return _has_document_value_type(content)
    return kind == 'union' and any(_has_document_value_type(member) for _, _, member in content)


def _calendar_bound_literal(value):
    return bool(value and re.match(r'^\s*(?:-?P|[^\s]*T\d\d:|\d\d:)', value))


def _boolean_element_type(tree,node):
    """Boolean equality needs only the two standard aliases, not extra probes."""
    seen=set()
    while node is not None and node not in seen:
        seen.add(node)
        qname=node.get('type') or node.get('base')
        if qname:
            ns=tree.qname_namespace(qname,node);name=qname.rsplit(':',1)[-1]
            if ns==XS:return name=='boolean'
            node=tree.symbol.get((ns,'simpleType',name),tree.symbol.get((ns,'complexType',name)))
        else:node=next((child for child in node if child.tag in {
            X+'simpleType',X+'complexType',X+'simpleContent',X+'restriction',X+'extension'}),None)
    return False


def _schema_components(root, *, skip_inactive_particles=False):
    """Ignore quoted XSD declarations in annotations and extension payloads."""
    pending=[root]
    while pending:
        node=pending.pop()
        if skip_inactive_particles and inactive_particle(node):continue
        yield node
        pending.extend(child for child in reversed(list(node))
                       if isinstance(child.tag,str) and child.tag.startswith(X) and child.tag!=X+'annotation')


def _copy_type(node, parent, adopted_alias=None):
    # Moving a detached subtree can discard prefixes used only in QName
    # values, or inherit a new default namespace from its destination.
    scope = dict(node.nsmap)
    if parent.nsmap.get(None) and None not in scope:
        scope[None] = ''
    child = etree.SubElement(parent, node.tag, attrib=dict(node.attrib), nsmap=scope)
    child.text, child.tail = node.text, node.tail
    if adopted_alias and not node.nsmap.get(None):
        for key in ('base', 'itemType', 'memberTypes'):
            if node.get(key):
                child.set(key, ' '.join(q if ':' in q else adopted_alias+':'+q for q in node.get(key).split()))
    for nested in node:
        if isinstance(nested.tag, str):
            _copy_type(nested, child, adopted_alias)
        else:
            child.append(copy.deepcopy(nested))
    return child


def _type_references(node):
    """Resolve the QName-valued attributes of a generated simple type."""
    for child in _schema_components(node):
        for key in ('base', 'itemType', 'memberTypes'):
            for value in child.get(key, '').split():
                prefix, _, name = value.rpartition(':')
                yield key, child.nsmap.get(prefix or None, ''), name


# Primitive and list types trace back to anySimpleType. These built-in
# restrictions are intermediate bases of user-defined simple-content types.
BUILTIN_TYPE_BASES = {
    'anySimpleType': 'anyType', 'normalizedString': 'string', 'token': 'normalizedString',
    'language': 'token', 'NMTOKEN': 'token', 'Name': 'token', 'NCName': 'Name',
    'ID': 'NCName', 'IDREF': 'NCName', 'ENTITY': 'NCName',
    'integer': 'decimal', 'nonPositiveInteger': 'integer', 'negativeInteger': 'nonPositiveInteger',
    'long': 'integer', 'int': 'long', 'short': 'int', 'byte': 'short',
    'nonNegativeInteger': 'integer', 'positiveInteger': 'nonNegativeInteger',
    'unsignedLong': 'nonNegativeInteger', 'unsignedInt': 'unsignedLong',
    'unsignedShort': 'unsignedInt', 'unsignedByte': 'unsignedShort',
}
ANY_TYPE = (XS, 'anyType')


def _wildcard_accepts(allowed, target, namespace):
    return ('##any' in allowed or namespace in allowed
            or ('##targetNamespace' in allowed and namespace == target)
            or ('##local' in allowed and not namespace)
            or ('##other' in allowed and namespace and namespace != target))


def _reachable_profile_types(tree, global_elements):
    """Find element declarations and effective types in the selected document."""
    def resolve_type(qname, source):
        declaration = tree.resolve(qname, source, ['simpleType', 'complexType'])
        return declaration if declaration is not None else (XS, qname.rsplit(':', 1)[-1])

    @lru_cache(maxsize=None)
    def direct_base(declaration):
        if isinstance(declaration, tuple):
            base = BUILTIN_TYPE_BASES.get(declaration[1], 'anySimpleType')
            return (XS, base), 'restriction'
        if declaration.tag == X+'simpleType':
            restriction = declaration.find(X+'restriction')
            if restriction is None:
                return (XS, 'anySimpleType'), 'restriction'
            qname = restriction.get('base')
            base = resolve_type(qname, restriction) if qname else restriction.find(X+'simpleType')
            return base, 'restriction'
        for path in ('simpleContent/extension', 'simpleContent/restriction',
                     'complexContent/extension', 'complexContent/restriction'):
            derivation = declaration.find('/'.join(X+part for part in path.split('/')))
            if derivation is not None:
                return resolve_type(derivation.get('base'), derivation), derivation.tag[len(X):]
        return ANY_TYPE, 'restriction'

    derived = {}
    for name in sorted(BUILTIN_SIMPLE_TYPES):
        declaration = (XS, name)
        base, method = direct_base(declaration)
        derived.setdefault(base, []).append((declaration, method))
    substitutes = {}
    affiliation = {}
    for (_,kind,_), member in tree.symbol.items():
        if kind == 'element' and (head := member.get('substitutionGroup')):
            identity = (tree.qname_namespace(head, member), 'element', head.rsplit(':', 1)[-1])
            if (declaration := tree.symbol.get(identity)) is not None:
                substitutes.setdefault(declaration, []).append(member)
                affiliation[member] = declaration
    indexed = set()
    pending = [node for (_, kind, _), node in tree.symbol.items() if kind in ('simpleType', 'complexType')]
    while pending:
        declaration = pending.pop()
        if declaration in indexed:
            continue
        indexed.add(declaration)
        base, method = direct_base(declaration)
        derived.setdefault(base, []).append((declaration, method))
        if isinstance(base, ET.Element) and base not in indexed:
            pending.append(base)

    def union_options(declaration):
        # A restriction of a union keeps the underlying member types.
        visited = set()
        while isinstance(declaration, ET.Element) and declaration.tag == X+'simpleType' and declaration not in visited:
            visited.add(declaration)
            union = declaration.find(X+'union')
            if union is not None:
                yield from (resolve_type(qname, union) for qname in union.get('memberTypes', '').split())
                yield from union.findall(X+'simpleType')
                return
            declaration, _ = direct_base(declaration)

    derivations = frozenset({'extension', 'restriction'})

    def block_tokens(declaration):
        return declaration.get('block', tree.context[declaration][0].get('blockDefault', '')).split()

    def blocked_methods(tokens):
        return derivations if '#all' in tokens else derivations.intersection(tokens)

    def type_block(declaration):
        return (blocked_methods(block_tokens(declaration))
                if isinstance(declaration, ET.Element) and declaration.tag == X+'complexType' else frozenset())

    @lru_cache(maxsize=None)
    def element_type(element):
        seen = set()
        while element not in seen:
            seen.add(element)
            if qname := element.get('type'):
                return resolve_type(qname, element)
            for declaration in element:
                if declaration.tag in (X+'simpleType', X+'complexType'):
                    return declaration
            if element not in affiliation:
                return ANY_TYPE
            element = affiliation[element]
        raise ValueError('Циклическая группа подстановки XSD')

    def permits_substitution(head_type, member_type, blocked):
        pending = [head_type]
        bases_seen = set()
        while pending:
            base = pending.pop()
            if base in bases_seen:
                continue
            bases_seen.add(base)
            current = member_type
            methods = set()
            native_methods = set()
            prohibited = blocked
            visited = set()
            while current is not None and current not in visited:
                if current == base:
                    if not methods.intersection(prohibited):
                        return True
                    # libxml2 can omit an extension encountered after a
                    # restriction while walking from member to head. A
                    # potentially admitted branch still needs exact checks.
                    if not native_methods.intersection(prohibited):
                        return True
                    break
                if current == ANY_TYPE:
                    break
                visited.add(current)
                current, method = direct_base(current)
                methods.add(method)
                if method != 'extension' or 'restriction' not in native_methods:
                    native_methods.add(method)
                prohibited |= type_block(current)
            if 'restriction' not in blocked:
                pending.extend(union_options(base))
        return False

    @lru_cache(maxsize=None)
    def particle_elements(particle):
        if not particle.get('ref'):
            return (particle,)
        head = tree.resolve(particle.get('ref'), particle, ['element'])
        block = block_tokens(head)
        choices = [head]
        if '#all' not in block and 'substitution' not in block:
            blocked = blocked_methods(block)
            head_type = element_type(head)
            pending = list(substitutes.get(head, ()))
            visited = {head}
            while pending:
                candidate = pending.pop()
                if candidate in visited:
                    continue
                visited.add(candidate)
                # Intermediate element blocks apply only to a reference to
                # that element. Follow its affiliations even if it is blocked.
                pending.extend(substitutes.get(candidate, ()))
                if permits_substitution(head_type, element_type(candidate), blocked):
                    choices.append(candidate)
        return tuple(element for element in choices if element.get('abstract') not in ('true', '1'))

    def particles(declaration):
        # An extension inherits its base's particles. A restriction supplies
        # its own particles. Walk reusable groups without expanding paths.
        pending = [declaration]
        models_seen = set()
        while pending:
            node = pending.pop()
            if node in models_seen or inactive_particle(node):
                continue
            models_seen.add(node)
            if node.tag in (X+'element', X+'any'):
                yield node
            elif node.tag == X+'group' and node.get('ref'):
                pending.append(tree.resolve(node.get('ref'), node, ['group']))
            elif node.tag == X+'complexType' and (content := node.find(X+'complexContent')) is not None:
                extension = content.find(X+'extension')
                if extension is not None:
                    base = resolve_type(extension.get('base'), extension)
                    if isinstance(base, ET.Element):
                        pending.append(base)
                    elif base == ANY_TYPE:
                        yield ANY_TYPE
                    pending.append(extension)
                else:
                    pending.extend(content.findall(X+'restriction'))
            else:
                pending.extend(child for child in node if child.tag in {
                    X+'element', X+'any', X+'group', X+'sequence', X+'choice', X+'all',
                })

    @lru_cache(maxsize=None)
    def children_of_type(declaration):
        if declaration == ANY_TYPE:
            return tuple(global_elements.values()), {('##any', '')}
        if (not isinstance(declaration, ET.Element) or declaration.tag != X+'complexType'
                or declaration.get('abstract') in ('true', '1')):
            return (), set()
        children = []
        unknown = set()
        for particle in particles(declaration):
            if particle == ANY_TYPE:
                children.extend(global_elements.values())
                unknown.add(('##any', ''))
            elif particle.tag == X+'element':
                children.extend(particle_elements(particle))
            elif particle.get('processContents', 'strict') != 'skip':
                allowed = particle.get('namespace', '##any').split()
                target = tree.context[particle][2]
                children.extend(element for (namespace,_), element in global_elements.items()
                                if _wildcard_accepts(allowed, target, namespace))
                if particle.get('processContents') == 'lax':
                    unknown.add((' '.join(allowed), target))
        return tuple(children), unknown

    def derived_types(declaration, blocked):
        yield from ((child, blocked) for child, method in derived.get(declaration, ()) if method not in blocked)
        if 'restriction' not in blocked:
            yield from ((child, blocked) for child in union_options(declaration))

    def type_context(element):
        # xsi:type uses the element and its declared type's blocks for the
        # whole derivation. Intermediate type blocks apply to substitution
        # groups, or when that intermediate type is itself declared.
        declaration = element_type(element)
        return declaration, blocked_methods(block_tokens(element)) | type_block(declaration)

    @lru_cache(maxsize=None)
    def children_of_element(element):
        children = set()
        unknown = set()
        pending = [type_context(element) if element is not None else (ANY_TYPE, frozenset())]
        visited = set()
        while pending:
            declaration, blocked = pending.pop()
            if (declaration, blocked) in visited:
                continue
            visited.add((declaration, blocked))
            actual, other = children_of_type(declaration)
            children.update(actual)
            unknown |= other
            pending.extend(derived_types(declaration, blocked))
        return children, unknown

    elements = [tree.root]
    elements_seen = set()
    types = []
    types_seen = set()
    typed_contexts = set()
    while elements or types:
        while elements:
            element = elements.pop()
            if element in elements_seen:
                continue
            elements_seen.add(element)
            types.append(type_context(element))
        if not types:
            break
        declaration, blocked = types.pop()
        if (declaration, blocked) in typed_contexts:
            continue
        typed_contexts.add((declaration, blocked))
        types.extend(derived_types(declaration, blocked))
        if declaration not in types_seen:
            types_seen.add(declaration)
            children, unknown = children_of_type(declaration)
            elements.extend(children)
            if unknown:
                # Unknown lax children and their descendants can use anyType.
                types.append((ANY_TYPE, frozenset()))
    return types_seen, elements_seen, children_of_element, element_type, particle_elements


def _reachable_attribute_uses(tree, reachable_types):
    """Collect attributes of reachable types, including groups and typed wildcards."""
    def direct_uses(declaration, seen):
        if declaration in seen:
            return
        for child in declaration:
            if child.tag == X+'attributeGroup':
                group = tree.resolve(child.get('ref'), child, ['attributeGroup'])
                yield from direct_uses(group, seen | {declaration})
            elif child.tag in (X+'attribute', X+'anyAttribute'):
                yield child

    def identity(attribute):
        source = tree.resolve(attribute.get('ref'), attribute, ['attribute']) if attribute.get('ref') else attribute
        return tree.instance_namespace(source), source.get('name')

    def uses(declaration, excluded=frozenset(), seen=frozenset()):
        if declaration in seen:
            return
        derivation = next((child for parent in declaration if parent.tag in (X+'simpleContent', X+'complexContent')
                           for child in parent if child.tag in (X+'extension', X+'restriction')), None)
        local = list(direct_uses(derivation if derivation is not None else declaration, set()))
        for attribute in local:
            if attribute.tag == X+'anyAttribute':
                yield attribute
            elif attribute.get('use') != 'prohibited' and identity(attribute) not in excluded:
                yield attribute
        if derivation is not None and (base := derivation.get('base')):
            base_type = tree.resolve(base, derivation, ['complexType', 'simpleType'])
            if base_type is not None and base_type.tag == X+'complexType':
                overridden = {identity(attribute) for attribute in local if attribute.tag == X+'attribute'}
                yield from uses(base_type, excluded | overridden, seen | {declaration})

    selected = {attribute for declaration in reachable_types if isinstance(declaration, ET.Element)
                and declaration.tag == X+'complexType' and declaration.get('abstract') not in ('true', '1')
                for attribute in uses(declaration)}
    global_attributes = [(ns, node) for (ns, kind, _), node in tree.symbol.items() if kind == 'attribute']
    if ANY_TYPE in reachable_types:
        selected.update(node for _, node in global_attributes)
    for wildcard in [node for node in selected if node.tag == X+'anyAttribute']:
        selected.remove(wildcard)
        if wildcard.get('processContents', 'strict') != 'skip':
            allowed = wildcard.get('namespace', '##any').split()
            target = tree.context[wildcard][2]
            selected.update(node for ns, node in global_attributes if _wildcard_accepts(allowed, target, ns))
    return selected


def _identity_xpath(tree, xpath, source):
    """Parse the child and attribute steps of an XSD 1.0 identity XPath."""
    parts = [step.strip() for step in xpath.strip().split('/')]
    descendant = parts[:2] == ['.', '']
    if descendant:
        parts = parts[2:]
    steps = []
    attribute = None
    for index, step in enumerate(parts):
        if step == '.':
            continue
        attr = re.match(r'(?:@|attribute\s*::)\s*', step)
        if attr and index != len(parts) - 1:
            return None
        name = step[attr.end():] if attr else re.sub(r'^child\s*::\s*', '', step)
        prefix, colon, local = name.rpartition(':')
        if not (local == '*' or NCNAME.fullmatch(local)) or colon and not NCNAME.fullmatch(prefix):
            return None
        if prefix == 'xml':
            namespace = 'http://www.w3.org/XML/1998/namespace'
        elif prefix:
            namespace = tree.prefixes[source].get(prefix)
            if namespace is None:
                return None
        else:
            namespace = '*' if local == '*' else ''
        if attr:
            attribute = namespace, local
        else:
            steps.append((namespace, local))
    return descendant, steps, attribute


def _identity_attribute_scopes(tree, elements, children_of, globals_):
    """Yield each attribute field test with the reachable element declarations it can select."""
    def select(elements, unknown, path):
        descendant, steps, _ = path
        if descendant:
            elements = set(elements)
            pending = list(elements)
            if unknown:
                dynamic, _ = children_of(None)
                pending.extend(dynamic)
                elements.update(dynamic)
            while pending:
                children, other = children_of(pending.pop())
                if other and not unknown:
                    children = children.union(children_of(None)[0])
                new = children - elements
                elements.update(new)
                pending.extend(new)
                unknown |= bool(other)
        for ns, local in steps:
            # An undeclared lax child may use anyType or an explicit xsi:type.
            # Its next step can reach global declarations and local particles
            # of a named complex type. A known global QName cannot be unknown.
            children = set(children_of(None)[0]) if unknown else set()
            may_be_undeclared = ns == '*' or local == '*' or (ns, local) not in globals_
            next_unknown = unknown and may_be_undeclared
            for element in elements:
                declared, wildcards = children_of(element)
                children.update(declared)
                if may_be_undeclared:
                    next_unknown |= any(ns == '*' or _wildcard_accepts(allowed.split(), target, ns)
                                        for allowed, target in wildcards)
            elements = {child for child in children if (local == '*' or child.get('name') == local)
                        and (ns == '*' or tree.instance_namespace(child) == ns)}
            unknown = next_unknown
        return elements, unknown

    for element in elements:
        for constraint in element:
            if constraint.tag not in IDENTITY_CONSTRAINTS:
                continue
            selector = constraint.find(X+'selector')
            selected = set()
            unknown = False
            ambiguous_selector = False
            for expression in selector.get('xpath', '').split('|') if selector is not None else ('',):
                path = _identity_xpath(tree, expression, selector)
                if path is None or path[2] is not None:
                    ambiguous_selector = True
                    continue
                candidates, other = select({element}, False, path)
                selected.update(candidates)
                unknown |= other
            for field in constraint.findall(X+'field'):
                for expression in field.get('xpath', '').split('|'):
                    path = _identity_xpath(tree, expression, field)
                    if path is None:
                        yield ('*', '*'), set(), bool(selected) or unknown or ambiguous_selector
                    elif path[2] is not None:
                        candidates, other = select(selected, unknown, path)
                        yield path[2], candidates, ambiguous_selector


def _scalar_whitespace(tree, node):
    restrictions = []
    source = tree.scalar_source(node, restrictions)
    if source is None:
        return None
    for layer in restrictions:
        facet = layer.find(X+'whiteSpace')
        if facet is not None:
            return facet.get('value')
    member = tree.scalar_member(source)
    seen = set()
    while member is not None and member not in seen:
        if isinstance(member, str):
            return {'string': 'preserve', 'anySimpleType': 'preserve',
                    'normalizedString': 'replace'}.get(member, 'collapse')
        seen.add(member)
        restriction = member.find(X+'restriction')
        if restriction is None:
            return 'collapse' if member.find(X+'list') is not None or member.find(X+'union') is not None else None
        facet = restriction.find(X+'whiteSpace')
        if facet is not None:
            return facet.get('value')
        member = tree.scalar_member(restriction)
    return None


def _fixed_scope_layouts(fields, parent):
    """Prove child positions for small bounded sequence/choice models.

    The field tree expands groups and type derivations in particle order.
    Attach those occurrence IDs to the bounded model's leaves before comparing
    the complete child QName sequence, including siblings after the subject.
    """
    def unsupported():
        return Unavailable('Неподдерживаемая дополнительная проверка fixed: '
                           'не подтверждена ветка модели содержимого в '+fields[parent]['path'])

    leaves=iter(i for i in fields[parent]['children'] if fields[i]['kind'] in ('element','any'))

    def expand(model):
        if isinstance(model,str):
            index=next(leaves,None)
            if index is None or fields[index]['name']!=model or fields[index]['kind']!='element':
                raise unsupported()
            minimum,maximum=fields[index]['min'],fields[index]['max']
            variants=[(index,)]
        else:
            minimum,maximum=model['min'],model['max']
            if model['kind']=='choice':
                variants=[]
                for child in model['children']:
                    variants.extend(expand(child))
                    if len(variants)>256:raise unsupported()
            elif model['kind'] in ('sequence','group'):
                variants=[()]
                for child in model['children']:
                    options=expand(child)
                    if len(variants)*len(options)>256:raise unsupported()
                    variants=[left+right for left in variants for right in options]
                    if any(len(variant)>256 for variant in variants):raise unsupported()
            else:
                raise unsupported()
        if maximum=='0':return [()]
        if maximum!='1' or minimum not in ('0','1'):raise unsupported()
        if minimum=='0':variants=[(),*variants]
        if len(variants)>256:raise unsupported()
        return variants

    model=fields[parent].get('contentModelBounds')
    parent_field=fields[parent]
    if model is None or (parent_field.get('baseNs'),parent_field['base'].rsplit(':',1)[-1])==ANY_TYPE:
        raise unsupported()
    by_names={}
    for indices in expand(model):
        names=tuple((fields[i]['ns'],fields[i]['name']) for i in indices)
        if names in by_names and by_names[names]!=indices:raise unsupported()
        by_names[names]=indices
    if next(leaves,None) is not None:raise unsupported()
    scopes={}
    for names,indices in by_names.items():
        children=[list(name) for name in names]
        for position,index in enumerate(indices):
            scopes.setdefault(index,[]).append(dict(children=children,index=position))
    return scopes


def _attribute_checks(row, selected, files, local_paths):
    """Compile attribute-use and scalar-element fixed as typed attribute probes.

    libxml2 ignores fixed on an attribute ref. Local attribute fixed constraints use the
    same XSD type/value-space comparison and are enforced in both shipped engines.
    The same typed comparison fixes libxml2's lexical comparison for elements;
    original documents and every independent type constraint are preserved.
    """
    fixed_globals={(root.get('targetNamespace',''),node.get('name')) for _,root in files.values()
                   for node in root.findall('{'+XS+'}attribute') if node.get('fixed') is not None}
    def affected(node):
        if node.tag==X+'element' and node.get('fixed') is not None:return True
        reference=node.get('ref')
        if node.tag!='{'+XS+'}attribute' or not reference:return False
        prefix,_,name=reference.rpartition(':')
        # A global attribute from a no-targetNamespace include adopts the
        # including namespace. Its physical document alone cannot identify all
        # effective QNames, so include that name in the inexpensive candidate
        # check; the expanded Schema below selects the actual fixed uses.
        return (node.get('fixed') is not None or (node.nsmap.get(prefix or None,''),name) in fixed_globals
                or ('',name) in fixed_globals)
    candidates = [node for _, root in files.values() for node in _schema_components(root)]
    ordered_candidates = any(node.tag in ORDERED_FACET_TAGS
                             and _calendar_bound_literal(node.get('value')) for node in candidates)
    id_candidates = any(schema_mentions_id(root) for _,root in files.values())
    if not id_candidates and not ordered_candidates and not any(affected(node) or node.tag==X+'attribute' and node.get('fixed') is not None
                                          for node in candidates):
        return {},{},set(),{}
    archive_bytes=io.BytesIO()
    with zipfile.ZipFile(archive_bytes,'w') as archive:
        write_archive_targets(archive,getattr(selected,'import_targets',{}))
        for name,content in selected.items():archive.writestr(name,content)
    with zipfile.ZipFile(archive_bytes) as archive:
        tree=Schema(archive,row['schema_path'],row['root'])
    # Existing boolean-only packs need no profiles for unrelated named types.
    # Their equality is fully checked by the primary boolean-alias path.
    @lru_cache(maxsize=None)
    def attribute_shape(node):
        return _value_shape(tree, _scalar_type(tree, node))

    @lru_cache(maxsize=None)
    def element_shape(node):
        restrictions = []
        member = tree.scalar_member(_element_scalar_source(tree, node, restrictions))
        shape, calendar = _value_shape(tree, member, list_item=True)
        for restriction in reversed(restrictions):
            if calendar:
                shape = _calendar_restriction(tree, restriction, shape)
            if (facet := restriction.find(X+'whiteSpace')) is not None:
                shape = ('whitespace', (shape, facet.get('value')))
        return shape, calendar

    global_elements = {(ns, name): node for (ns, kind, name), node in tree.symbol.items() if kind == 'element'}
    wildcard_elements = {key: node for key, node in global_elements.items()
                         if node.get('abstract') not in ('true', '1')}
    reachable_types, reachable_elements, children_of, element_type, particle_elements = _reachable_profile_types(tree, wildcard_elements)
    nodes, parents = {}, {}
    for (name,_namespace),(root,_,_) in tree.documents.items():
        native=[e for e in files[name][1].iter() if isinstance(e.tag,str)]
        nodes.update(zip(root.iter(),native))
        parents.update((child,parent) for parent in root.iter() for child in parent)
    single_context, shared_context = set(), set()
    for path, _ in tree.documents:
        if path in single_context:
            shared_context.add(path)
        single_context.add(path)
    single_context -= shared_context
    exact_uses = {}
    explicit_calendar_types = {}
    if ordered_candidates:
        def unsupported(node, facets):
            source = nodes[node]
            raise Unavailable('Точная проверка календарного ограничения '
                              + ', '.join(sorted(facets)) + ' недоступна для '
                              + tree.owner[node] + ': ' + source.getroottree().getpath(source))

        identity_scopes = tuple(_identity_attribute_scopes(tree, reachable_elements, children_of, wildcard_elements))

        def affects_identity(attribute):
            ns, name = tree.instance_namespace(attribute), attribute.get('name')
            owner = parents[attribute]
            return any(field_ns in ('*', ns) and field_name in ('*', name)
                       and (unknown or any(element_type(candidate) is owner for candidate in candidates))
                       for (field_ns, field_name), candidates, unknown in identity_scopes)

        position = lambda node: (tree.owner[node], nodes[node].getroottree().getpath(nodes[node]))
        for node in sorted(_reachable_attribute_uses(tree, reachable_types), key=position):
            shape, _ = attribute_shape(node)
            if facets := _ordered_calendar_facets(shape):
                owner = nodes[node].getparent()
                # A changed union branch must preserve document-wide identity
                # semantics. Only an inline attribute with known uses can be projected.
                if (node.get('ref') or owner.tag != X+'complexType' or owner.getparent().tag != X+'element'
                        or tree.owner[node] not in single_context or affects_identity(node)
                        or _has_document_value_type(shape)):
                    unsupported(node, facets)
                exact_uses[node] = shape
        # An unknown lax child can select a named calendar type with xsi:type.
        # Reuse the probe for the exact attribute that declares that same type.
        projected_types = {}
        for attribute in exact_uses:
            reference = attribute.get('type')
            if reference and tree.qname_namespace(reference, attribute) != XS:
                projected_types[tree.resolve(reference, attribute, ['simpleType'])] = attribute
        scalar_sites = reachable_elements | {node for node in reachable_types if isinstance(node, ET.Element)}
        for node in sorted(scalar_sites, key=position):
            if facets := _ordered_calendar_facets(element_shape(node)[0]):
                restriction = node.find(X+'restriction')
                if (node not in projected_types or tree.owner[node] not in single_context or restriction is None
                        or tree.qname_namespace(restriction.get('base', ''), restriction) != XS
                        or restriction.get('base', '').rsplit(':', 1)[-1] not in CALENDAR_TYPES):
                    unsupported(node, facets)
                explicit_calendar_types[node] = projected_types[node]

    calendar_declarations={node for root,_,_ in tree.documents.values() for node in _schema_components(root)
                           if node.tag==X+'attribute' and node.get('fixed') is not None
                           and attribute_shape(node)[1]}
    attribute_constraints=bool(calendar_declarations or exact_uses) or any(
        affected(node) for _,root in files.values() for node in _schema_components(root) if node.tag==X+'attribute')
    scalar_constraints=any(node.tag==X+'element' and node.get('fixed') is not None
                           and _element_scalar_source(tree,node) is not None and not _boolean_element_type(tree,node)
                           for root,_,_ in tree.documents.values() for node in _schema_components(root))
    @lru_cache(maxsize=None)
    def id_shape(member):
        if isinstance(member, str) or member is None:
            return member == 'ID'
        restriction = member.find(X+'restriction')
        if restriction is not None:
            base = restriction.get('base')
            source = restriction if base else restriction.find(X+'simpleType')
            return id_shape(tree._enum_member_type(base, source))
        union = member.find(X+'union')
        if union is not None:
            choices = [(name, source, id_shape(tree._enum_member_type(name, source)))
                       for name, source in tree._union_members(union)]
            if any(shape for _, _, shape in choices):
                return choices
        return False

    @lru_cache(maxsize=None)
    def declaration_id(node):
        return id_candidates and id_shape(tree.scalar_member(_element_scalar_source(tree, node)))

    if not id_candidates and not attribute_constraints and not scalar_constraints:return {},{},set(),{}
    tree.field(tree.root, coalesce_choices=False, skip_inactive_particles=True, native_identity_constraints=True, validation_particles=particle_elements)
    # One chameleon declaration can have a calendar type in one namespace and
    # a different type in another. Relaxing it requires a probe in each context.
    calendar_sources={nodes[node] for node in calendar_declarations}
    global_attributes={(ns,name):node for (ns,kind,name),node in tree.symbol.items()
                       if kind=='attribute' and (node.get('fixed') is not None and nodes[node] in calendar_sources or declaration_id(node))}
    all_global_attributes=frozenset(global_attributes)
    relaxed_attributes=set()
    calendar_projections={}
    namespace='urn:sedo:attribute-value-checks'
    while any(root.get('targetNamespace')==namespace for _,root in files.values()):namespace+=':checks'
    checks=etree.Element('{'+XS+'}schema',nsmap={'xs':XS,'probe':namespace},targetNamespace=namespace)
    groups={}
    for name,(_,root) in files.items():
        declared = root.get('targetNamespace', '')
        # A chameleon reached only by include already belongs to its including
        # schema. Importing it again in no namespace can leave its types unresolved.
        if (name, declared) in tree.documents:
            groups.setdefault(declared, []).append(name)
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
    member_probes={}
    item_probes={}
    compound_probes={}
    lexical_types={}
    instance_values=[]
    id_types=[]
    document_value_probes=set()

    @lru_cache(maxsize=None)
    def attribute_wildcard(declaration):
        if declaration is None or declaration.tag != X+'complexType':
            return None
        derivation = next((selected for kind in ('simpleContent','complexContent')
                           if (content := declaration.find(X+kind)) is not None
                           for selected in content if selected.tag in (X+'extension',X+'restriction')), None)
        local = wildcard_constraints(derivation if derivation is not None else declaration)
        if derivation is None or derivation.tag != X+'extension':
            return local
        base = derivation.get('base')
        kind = (tree.qname_namespace(base, derivation), base.rsplit(':',1)[-1])
        inherited = ((all_global_attributes, 'lax') if kind == ANY_TYPE
                     else attribute_wildcard(tree.resolve(base, derivation, ['complexType','simpleType'])))
        if local is None:
            return inherited
        if inherited is None:
            return local
        return inherited[0] | local[0], local[1]

    @lru_cache(maxsize=None)
    def wildcard_constraints(declaration):
        wildcard = declaration.find(X+'anyAttribute')
        parts = []
        if wildcard is not None:
            namespace = tree.context[wildcard][2]
            allowed = wildcard.get('namespace','##any').split()
            parts.append((frozenset(key for key in global_attributes if _wildcard_accepts(allowed, namespace, key[0])),
                          wildcard.get('processContents','strict')))
        for group in declaration.findall(X+'attributeGroup'):
            constraints = wildcard_constraints(tree.resolve(group.get('ref'),group,['attributeGroup']))
            if constraints is not None:
                parts.append(constraints)
        if parts:
            return parts[0][0].intersection(*(keys for keys,_ in parts[1:])), parts[0][1]

    def wildcard_attributes(element, fields, field):
        if (qname := element.get('type')):
            declaration = tree.resolve(qname, element, ['complexType','simpleType'])
        else:
            declaration = element.find(X+'complexType')
        # Built-in anyType and unknown lax children are handled at runtime.
        effective = attribute_wildcard(declaration)
        if not effective or effective[1] == 'skip':
            return frozenset()
        explicit = {(fields[i]['ns'],fields[i]['name'][1:]) for i in field['children']
                    if fields[i]['kind'] == 'attribute'}
        return effective[0] - explicit

    def collect(fields, relative=False, global_only=False, instance_types=False):
        definitions={}
        locations={}
        declarations={}
        for index,field in enumerate(fields):
            if field['kind'] not in ('attribute','element'):continue
            decl,source=tree.field_declarations[id(field)]
            current=index if field['kind']=='element' else field['parent']
            ancestry=[]
            while current>=0:
                ancestry.append(current);current=fields[current]['parent']
            ancestry=tuple(reversed(ancestry))
            path=tuple((fields[i]['ns'],fields[i]['name']) for i in ancestry)
            if relative:path=path[1:]
            if global_attributes and field['kind']=='element':
                matches=wildcard_attributes(source, fields, field)
                locations.setdefault(path,[]).append((ancestry, matches))
            key=(path,field['kind'],field['ns'],field['name'])
            constraint=decl if 'fixed' in decl.attrib else source
            fixed=field['flags'].get('fixed')
            needs_probe=(declaration_id(source) or source in exact_uses or fixed is not None and
                         (decl.get('ref') or nodes[constraint] in calendar_sources if field['kind']=='attribute' else
                          _element_scalar_source(tree,source) is not None and not _boolean_element_type(tree,source)))
            signature=(fixed,tree.owner[source],tree.context[source][2],etree.tostring(nodes[source],with_tail=False),
                       frozenset(tree.prefixes[constraint].items())) if needs_probe else None
            definitions.setdefault(key,[]).append((index,ancestry,signature))

        for path,locations_at_path in locations.items():
            for ns,name in sorted(set().union(*(matches for _,matches in locations_at_path))):
                source=global_attributes[(ns,name)]
                field_path='/'.join(name for _,name in path)
                entries=definitions.setdefault((path,'wildcard-attribute',ns,name),[])
                for ancestry,matches in locations_at_path:
                    index=len(fields)+len(declarations)
                    declarations[index]=(source,source,False,ns,name,'/'.join(filter(None,(field_path,'@'+name))))
                    entries.append((index,ancestry,source if (ns,name) in matches else None))

        plans={};parent_layouts={}
        for (path,*_),entries in definitions.items():
            differing=len({signature for _,_,signature in entries})>1
            seen=set()
            for index,ancestry,signature in entries:
                if signature is None:continue
                guards=[]
                if differing:
                    for depth,(parent,child) in enumerate(zip(ancestry,ancestry[1:])):
                        field=fields[child]
                        if not any(other!=child and fields[other]['kind']=='element'
                                   and (fields[other]['ns'],fields[other]['name'])==(field['ns'],field['name'])
                                   for other in fields[parent]['children']):continue
                        if parent not in parent_layouts:parent_layouts[parent]=_fixed_scope_layouts(fields,parent)
                        guards.append(dict(up=len(ancestry)-2-depth,layouts=parent_layouts[parent].get(child,[])))
                identity=(signature,ancestry if guards else ())
                if any(not guard['layouts'] for guard in guards):
                    if index < len(fields):
                        _, source = tree.field_declarations[id(fields[index])]
                        if source in exact_uses:
                            unsupported(source, _ordered_calendar_facets(exact_uses[source]))
                    continue
                if identity in seen:continue
                seen.add(identity)
                plans[index]=([list(name) for name in path],guards)

        if global_only:
            for index, ((ns,name), source) in enumerate(sorted(global_attributes.items())):
                declarations[index]=(source,source,False,ns,name,'@'+name)
                plans[index]=([],[])

        rules=[]

        def lexical_selector(member):
            # Native probes retain all lexical facets. Rebuild named scalar
            # types only where exact value facets are evaluated by the model.
            if member in lexical_types:
                return lexical_types[member]
            shape, _ = _value_shape(tree, member)
            if not _shape_has_calendar_facets(shape):
                lexical_types[member] = None
                return None
            name = 'selector'+str(len(lexical_types))
            clone = etree.SubElement(checks, X+'simpleType', name=name)
            lexical_types[member] = name

            def member_identity(qname, source):
                projected = lexical_selector(tree._enum_member_type(qname, source))
                if projected:
                    return namespace, projected
                if qname:
                    return tree.qname_namespace(qname, source), qname.rsplit(':', 1)[-1]
                return None

            def typed_child(parent, kind, key, refs):
                bindings, values = {}, []
                for uri, name in refs:
                    if uri == namespace:
                        name = 'probe:' + name
                    elif uri == XS:
                        name = 'xs:' + name
                    elif uri:
                        prefix = f't{len(bindings)}'
                        bindings[prefix] = uri
                        name = prefix + ':' + name
                    values.append(name)
                attributes = {key: ' '.join(values)} if values else {}
                return etree.SubElement(parent, X+kind, nsmap=bindings, **attributes)

            def append_inline(parent, source, projected):
                if projected is not None:
                    typed_child(etree.SubElement(parent, X+'simpleType'), 'restriction', 'base', [projected])
                else:
                    adopted = tree.context[source][2] if not tree.context[source][0].get('targetNamespace') else None
                    alias = None
                    if adopted:
                        alias = 'sedoselector'
                        while any(alias in child.nsmap for child in nodes[source].iter()):
                            alias += 'x'
                        etree.SubElement(parent, X+'simpleType', nsmap={alias: adopted})
                        scope = parent[-1]
                        for child in nodes[source]:
                            _copy_type(child, scope, alias)
                    else:
                        _copy_type(nodes[source], parent)

            restriction = member.find(X+'restriction')
            if restriction is not None:
                base = restriction.get('base')
                source = restriction if base else restriction.find(X+'simpleType')
                identity = member_identity(base, source)
                target = typed_child(clone, 'restriction', 'base', [identity] if base else [])
                if not base:
                    append_inline(target, source, identity)
                for facet in restriction:
                    if facet.tag != X+'simpleType' and not (shape[0] == 'calendar-facets' and facet.tag in CALENDAR_FACET_TAGS):
                        _copy_type(nodes[facet], target)
                return name
            union = member.find(X+'union')
            if union is not None:
                named, inline = [], []
                for qname, source in tree._union_members(union):
                    identity = member_identity(qname, source)
                    (named if qname else inline).append(identity if qname else (source, identity))
                target = typed_child(clone, 'union', 'memberTypes', named)
                for source, projected in inline:
                    append_inline(target, source, projected)
                return name
            sequence = member.find(X+'list')
            qname = sequence.get('itemType')
            source = sequence if qname else sequence.find(X+'simpleType')
            identity = member_identity(qname, source)
            target = typed_child(clone, 'list', 'itemType', [identity] if qname else [])
            if not qname:
                append_inline(target, source, identity)
            return name

        def inline_selector(name, parent):
            selector = checks.find(f"{X}simpleType[@name='{name}']")
            anonymous = _copy_type(selector, parent)
            del anonymous.attrib['name']

            def projected(node, reference):
                prefix, _, local = reference.rpartition(':')
                return local if node.nsmap.get(prefix or None) == namespace else None

            for node in tuple(anonymous.iter()):
                if node.tag in (X+'restriction', X+'list'):
                    attribute = 'base' if node.tag == X+'restriction' else 'itemType'
                    if target := projected(node, node.get(attribute, '')):
                        del node.attrib[attribute]
                        inline = inline_selector(target, node)
                        node.insert(int(node.find(X+'annotation') is not None), inline)
                elif node.tag == X+'union':
                    members = node.get('memberTypes', '').split()
                    if not any(projected(node, member) for member in members):
                        continue
                    del node.attrib['memberTypes']
                    offset = int(node.find(X+'annotation') is not None)
                    for index, member in enumerate(members, offset):
                        if target := projected(node, member):
                            inline = inline_selector(target, node)
                        else:
                            inline = etree.SubElement(node, X+'simpleType')
                            etree.SubElement(inline, X+'restriction', base=member)
                        node.insert(index, inline)
            return anonymous

        def typed_attribute(parent, qname, source):
            attributes = {'name': 'value', 'use': 'required'}
            bindings = {}
            alias = None
            projected = lexical_selector(tree._enum_member_type(qname, source))
            if projected:
                return etree.SubElement(parent, X+'attribute', type='probe:'+projected, **attributes)
            if qname:
                ns = tree.qname_namespace(qname, source)
                local_name = qname.rsplit(':', 1)[-1]
                attributes['type'] = 'sedotype:'+local_name if ns else local_name
                if ns:
                    bindings['sedotype'] = ns
            else:
                anonymous = nodes[source]
                ns = tree.context[source][2] if not tree.context[source][0].get('targetNamespace') else None
                if ns:
                    alias = 'sedochameleon'
                    while any(alias in node.nsmap for node in anonymous.iter()):
                        alias += 'x'
                    bindings[alias] = ns
            probe = etree.SubElement(parent, X+'attribute', nsmap=bindings, **attributes)
            if not qname:
                _copy_type(anonymous, probe, alias)

        def item_comparison(builtin):
            # Select the full member type first. Its primitive type supplies
            # the common value space across separately declared list branches.
            while builtin in BUILTIN_TYPE_BASES and builtin != 'anySimpleType':
                builtin = BUILTIN_TYPE_BASES[builtin]
            if builtin not in item_probes:
                name = item_probes[builtin] = 'item'+str(len(item_probes))
                scalar = etree.SubElement(checks, X+'complexType', name=name)
                # NOTATION and QName have distinct value spaces. After a
                # NOTATION selector succeeds, compare its expanded names.
                probe_type = 'QName' if builtin == 'NOTATION' else builtin
                if probe_type in DOCUMENT_VALUE_TYPES:
                    document_value_probes.add(name)
                etree.SubElement(scalar, X+'attribute', name='value', use='required', type='xs:'+probe_type)
                pair = etree.SubElement(checks, X+'element', name=name)
                children = etree.SubElement(etree.SubElement(pair, X+'complexType'), X+'sequence')
                for child in ('expected', 'actual'):
                    etree.SubElement(children, X+'element', name=child, form='qualified', type='probe:'+name)
                key = etree.SubElement(pair, X+'key', name=name+'Expected')
                etree.SubElement(key, X+'selector', xpath='probe:expected')
                etree.SubElement(key, X+'field', xpath='@value')
                keyref = etree.SubElement(pair, X+'keyref', name=name+'Actual', refer='probe:'+name+'Expected')
                etree.SubElement(keyref, X+'selector', xpath='probe:actual')
                etree.SubElement(keyref, X+'field', xpath='@value')
            return item_probes[builtin]

        def compound_shape(shape):
            if isinstance(shape, str):
                return shape
            kind, content = shape
            if kind == 'atomic':
                return item_comparison(content)
            if kind == 'list':
                return [kind, compound_shape(content)]
            if kind == 'calendar-facets':
                nested, facets = content
                return [kind, compound_shape(nested), facets]
            if kind == 'whitespace':
                nested, mode = content
                return [kind, compound_shape(nested), mode]
            members = []
            for qname, source, nested in content:
                identity = (qname, source)
                name = member_probes.get(identity)
                if name is None:
                    name = member_probes[identity] = 'member'+str(len(member_probes))
                    element = etree.SubElement(checks, X+'element', name=name)
                    typed_attribute(etree.SubElement(element, X+'complexType'), qname, source)
                    if _has_document_value_type(nested):
                        document_value_probes.add(name)
                members.append([name, compound_shape(nested)])
            return [kind, members]

        def id_selector(shape):
            if isinstance(shape, bool):
                return shape
            members = []
            for qname, source, nested in shape:
                identity = (qname, source)
                name = member_probes.get(identity)
                if name is None:
                    name = member_probes[identity] = 'member'+str(len(member_probes))
                    element = etree.SubElement(checks, X+'element', name=name)
                    typed_attribute(etree.SubElement(element, X+'complexType'), qname, source)
                document_value_probes.add(name)
                members.append([name, id_selector(nested)])
            return members

        def scalar_probe(name, scalar_source, member, restrictions, bindings, fixed=None, has_calendar=False):
            attributes = {'name': 'value', 'use': 'required'}
            if fixed is not None:
                attributes['fixed'] = fixed
            actual = nodes[scalar_source]
            anonymous = actual if scalar_source.tag == X+'simpleType' else actual.find(X+'simpleType')
            if anonymous is actual and actual.get('name'):
                anonymous = copy.deepcopy(actual)
                anonymous.attrib.pop('name', None)
                anonymous.attrib.pop('final', None)
            type_reference = (actual.get('type') or actual.get('base')) if anonymous is None else None
            projected = lexical_selector(member) if has_calendar else None
            type_scope = dict(actual.nsmap)
            if projected:
                anonymous = None
                prefix = 'probe'
                while bindings.get(prefix, namespace) != namespace or type_scope.get(prefix, namespace) != namespace:
                    prefix += 'x'
                if prefix != 'probe':
                    bindings[prefix] = type_scope[prefix] = namespace
                type_reference = attributes['type'] = prefix+':'+projected
            separate_type_scope = False
            if type_reference and not projected:
                local = type_reference.rsplit(':', 1)[-1]
                ns = tree.qname_namespace(type_reference, scalar_source)
                if ns:
                    alias = 'sedotype'
                    while alias in bindings:alias += 'x'
                    bindings[alias] = ns
                    attributes['type'] = alias+':'+local
                else:
                    attributes['type'] = local
                    separate_type_scope = bool(bindings.get(None))
            elif not type_reference and anonymous is None:
                alias = 'sedotype'
                while alias in bindings:alias += 'x'
                bindings[alias] = XS
                attributes['type'] = alias+':anySimpleType'
            if restrictions or separate_type_scope:
                # Rebuild the effective simpleContent facets around the scalar.
                if anonymous is None:
                    anonymous = etree.Element(X+'simpleType', nsmap=type_scope)
                    etree.SubElement(anonymous, X+'restriction', base=type_reference)
                facets = {X+name for name in ('minExclusive','minInclusive','maxExclusive','maxInclusive',
                          'totalDigits','fractionDigits','length','minLength','maxLength','enumeration',
                          'whiteSpace','pattern')}
                for layer in reversed(restrictions):
                    native = nodes[layer]
                    wrapper = etree.Element(X+'simpleType', nsmap=dict(native.nsmap))
                    restriction = etree.SubElement(wrapper, X+'restriction')
                    _copy_type(anonymous, restriction)
                    for facet in native:
                        if facet.tag in facets and (not has_calendar or facet.tag not in CALENDAR_FACET_TAGS):
                            _copy_type(facet, restriction)
                    anonymous = wrapper
                attributes.pop('type', None)
            adopted = (not projected and anonymous is not None
                       and not tree.context[scalar_source][0].get('targetNamespace') and tree.context[scalar_source][2])
            if adopted:
                alias = 'sedochameleon'
                while alias in bindings or any(alias in node.nsmap for node in anonymous.iter()):alias += 'x'
                bindings[alias] = tree.context[scalar_source][2]
            probe_element = etree.SubElement(checks, X+'element', name=name)
            probe = etree.SubElement(etree.SubElement(probe_element, X+'complexType'), X+'attribute',
                                     nsmap=bindings, **attributes)
            if anonymous is not None:
                _copy_type(anonymous, probe, alias if adopted else None)
            if _has_document_value_type(_value_shape(tree, member, list_item=True)[0]):
                document_value_probes.add(name)
            return probe_element, projected

        if instance_types and id_candidates:
            for (uri, kind, name), decl in sorted(tree.symbol.items()):
                if kind in ('simpleType','complexType') and decl in reachable_types and (shape := declaration_id(decl)):
                    id_types.append(dict(type=[uri,name], shape=id_selector(shape)))

        if instance_types and scalar_constraints:
            for builtin in sorted(name for uri,name in reachable_types.intersection(
                    (XS,name) for name in BUILTIN_SIMPLE_TYPES) if name != 'NOTATION'):
                probe = 'effective'+str(len(instance_values))
                element = etree.SubElement(checks, X+'element', name=probe)
                etree.SubElement(etree.SubElement(element, X+'complexType'), X+'attribute',
                                 name='value', use='required', type='xs:'+builtin)
                if builtin in DOCUMENT_VALUE_TYPES:
                    document_value_probes.add(probe)
                shape, _ = _value_shape(tree, builtin, list_item=True)
                instance_values.append(dict(type=[XS,builtin], element=probe, shape=compound_shape(shape)))
            for (ns, kind, name), declaration in sorted(tree.symbol.items()):
                if kind not in ('simpleType', 'complexType') or declaration not in reachable_types:
                    continue
                restrictions = []
                source = _element_scalar_source(tree, declaration, restrictions)
                if source is None:
                    continue
                shape, has_calendar = element_shape(declaration)
                probe = 'effective'+str(len(instance_values))
                scalar_probe(probe, source, tree.scalar_member(source), restrictions, dict(nodes[source].nsmap),
                             has_calendar=has_calendar)
                instance_values.append(dict(type=[ns,name], element=probe, shape=compound_shape(shape)))

        for index,(path,guards) in sorted(plans.items()):
            if index in declarations:
                decl,source,element_value,field_ns,field_name,field_path=declarations[index]
            else:
                field=fields[index]
                decl,source=tree.field_declarations[id(field)]
                element_value=field['kind']=='element'
                field_ns,field_path=field['ns'],field['path']
                field_name=field['name'] if element_value else field['name'][1:]
            constraint=decl if 'fixed' in decl.attrib else source
            fixed=constraint.get('fixed')
            probe_key=(constraint,source)
            restrictions=[]
            scalar_source=_element_scalar_source(tree,source,restrictions) if element_value else source
            member = tree.scalar_member(scalar_source) if element_value else _scalar_type(tree, scalar_source)
            shape, has_calendar = (_value_shape(tree, member) if element_value else attribute_shape(source))
            if has_calendar:
                for restriction in reversed(restrictions):
                    shape = _calendar_restriction(tree, restriction, shape)
            compound = has_calendar and isinstance(shape, tuple)
            calendar = has_calendar and not compound
            if probe_key not in probe_names:
                bindings=dict(nodes[constraint].nsmap)
                element_name='value'+str(len(probe_names));probe_names[probe_key]=element_name
                probe_element, projected = scalar_probe(element_name, scalar_source, member, restrictions, bindings,
                                                        None if has_calendar else fixed, has_calendar)
                if compound:
                    check = compound_probes[probe_key] = dict(shape=compound_shape(shape))
                    if fixed is not None:
                        fixed_name = 'compound'+str(len(compound_probes))
                        native_probe = copy.deepcopy(probe_element)
                        native_probe.set('name', fixed_name)
                        native_probe.find(X+'complexType/'+X+'attribute').set('fixed', fixed)
                        checks.append(native_probe)
                        if element_name in document_value_probes:
                            document_value_probes.add(fixed_name)
                        check.update(fixed=fixed, comparison=fixed_name, namespaces={
                            prefix or '': uri for prefix, uri in tree._value_scope(constraint).items()})
                    if source in exact_uses:
                        calendar_projections[nodes[source]] = dict(
                            replacement=inline_selector(projected, etree.Element(X+'attribute')),
                            element=element_name, shape=check['shape'],
                            facets=_ordered_calendar_facets(exact_uses[source]),
                            namespaces={prefix or '': uri for prefix, uri in tree._value_scope(source).items()})
            rule=dict(path=path,attribute=[field_ns,field_name],element=probe_names[probe_key],field=field_path)
            if id_model := declaration_id(source):
                rule['id_shape'] = id_selector(id_model)
                if fixed is None and source not in exact_uses:rule['identity_only']=True
            if element_value:
                rule['element_value']=True
                if instance_values and fixed is not None:
                    rule['fixed_value'] = dict(lexical=fixed, namespaces={
                        prefix or '': uri for prefix, uri in tree._value_scope(constraint).items()})
            if compound:
                rule['calendar_compound'] = compound_probes[probe_key]
                if fixed is not None and not element_value:relaxed_attributes.add(nodes[constraint])
            elif calendar and fixed is not None:
                rule['calendar_fixed']=[shape,fixed]
                if not element_value:relaxed_attributes.add(nodes[constraint])
            if guards:rule['guards']=guards
            rules.append(rule)
        return rules
    def any_type_element(node):
        if node.tag!='{'+XS+'}element' or node.get('ref'):return False
        qname=node.get('type')
        if qname:return qname.rsplit(':',1)[-1]=='anyType' and tree.qname_namespace(qname,node)==XS
        return node.find('{'+XS+'}complexType') is None and node.find('{'+XS+'}simpleType') is None
    needs_cache={}
    def needs_checks(declaration):
        if declaration in needs_cache:return needs_cache[declaration]
        result=contains_constraint_or_wildcard(declaration)
        needs_cache[declaration]=result
        return result
    def contains_constraint_or_wildcard(declaration):
        pending=[declaration];visited=set()
        while pending:
            node=pending.pop()
            if node in visited:continue
            visited.add(node)
            for child in _schema_components(node,skip_inactive_particles=True):
                if child.tag in (X+'element', X+'attribute') and declaration_id(child):return True
                if child.tag=='{'+XS+'}any' or any_type_element(child):return True
                if global_attributes and child.tag==X+'anyAttribute':return True
                if child.tag==X+'element' and child.get('fixed') is not None:return True
                if child.tag==X+'attribute':
                    if child.get('ref'):
                        attribute=tree.resolve(child.get('ref'),child,['attribute'])
                        if child.get('fixed') is not None or attribute.get('fixed') is not None:return True
                    elif child in exact_uses or nodes[child] in calendar_sources:
                        return True
                qname=child.get('type') or child.get('base') or child.get('ref')
                if not qname:continue
                kinds=['simpleType','complexType'] if child.get('type') or child.get('base') else [child.tag.rsplit('}',1)[-1]]
                target=tree.resolve(qname,child,kinds)
                if target is not None:
                    pending.append(target)
                elif child.tag == X+'extension' and qname.rsplit(':', 1)[-1] == 'anyType':
                    return True
        return False
    reachable={}
    def wildcard_rules(fields, relative=False):
        paths=set();wildcards=[];any_types=[]
        for index,field in enumerate(fields):
            declaration,source=tree.field_declarations[id(field)]
            any_type=field['kind']=='element' and any_type_element(source)
            # Schema.field retains the base of complex-content extensions.
            # A restriction of anyType supplies its own particle model.
            inherits_any_type = (field['kind'] == 'element' and not any_type
                                 and (field.get('baseNs'), field['base'].rsplit(':', 1)[-1]) == ANY_TYPE)
            if field['kind']!='any' and not (any_type or inherits_any_type):continue
            namespace=tree.context[declaration][2]
            allowed=field['flags'].get('namespace','##any').split()
            process='lax' if inherits_any_type else field['flags'].get('processContents','strict')
            parent=index if any_type or inherits_any_type else field['parent'];path=[]
            parent_field=fields[parent]
            while parent>=0:
                ancestor=fields[parent];path.append((ancestor['ns'],ancestor['name']));parent=ancestor['parent']
            path.reverse()
            if relative:path=path[1:]
            if any_type:
                # The built-in type supplies lax content unless xsi:type picks
                # a more specific declaration at this occurrence.
                any_types.append([list(part) for part in path])
                for key,target in wildcard_elements.items():
                    if needs_checks(target):reachable.setdefault(key,target)
                continue
            explicit={(fields[i]['ns'],fields[i]['name']) for i in parent_field['children'] if fields[i]['kind']=='element'}
            def accepts(ns):
                return _wildcard_accepts(allowed, namespace, ns)
            if any(accepts(ns) for ns,_ in explicit):
                raise Unavailable('Неоднозначный путь явного элемента и wildcard: '+parent_field['path'])
            wildcards.append(dict(path=[list(part) for part in path],namespace=' '.join(allowed), target_namespace=namespace,
                                  process=process,explicit=[list(key) for key in sorted(explicit)]))
            if process=='skip':continue
            for key,target in wildcard_elements.items():
                ns,_=key
                # An unknown lax child has anyType content. Its descendants can
                # belong to any namespace, independently of the outer wildcard.
                if (process=='lax' or accepts(ns)) and needs_checks(target):
                    reachable.setdefault(key,target)
                    if accepts(ns):paths.add((*path,key))
                if len(paths)>50000:raise Unavailable('Слишком много путей wildcard в XSD')
        return [[list(part) for part in path] for path in sorted(paths)],wildcards,any_types
    rules=collect(tree.fields, instance_types=True)
    root_paths,root_wildcards,root_any_types=wildcard_rules(tree.fields)
    type_rules=[]
    # xsi:type can replace the static type at any reachable element. Only
    # materialize those profiles, sharing the field budget with the root tree.
    global_rules=collect([], global_only=True) if global_attributes and ANY_TYPE in reachable_types else []
    for (namespace_uri,kind,name),declaration in sorted(tree.symbol.items()):
        if (kind != 'complexType' or declaration not in reachable_types
                or declaration.get('abstract') in ('true', '1') or not needs_checks(declaration)):
            continue
        bindings=dict(tree.prefixes[declaration]);alias='sedotyperef'
        while alias in bindings:alias+='x'
        if namespace_uri:bindings[alias]=namespace_uri;qname=alias+':'+name
        else:bindings['']='';qname=name
        temporary=ET.Element('{'+XS+'}element',name='typeRoot',type=qname)
        tree.owner[temporary]=tree.owner[declaration];tree.prefixes[temporary]=bindings
        tree.context[temporary]=tree.context[declaration]
        tree.fields=[]
        tree.field(temporary, coalesce_choices=False, skip_inactive_particles=True, native_identity_constraints=True, validation_particles=particle_elements)
        applicable=collect(tree.fields,relative=True)
        paths,wildcards,any_types=wildcard_rules(tree.fields,relative=True)
        if applicable or wildcards or any_types:
            profile=dict(type=[namespace_uri,name],checks=applicable)
            if paths:profile['wildcard_paths']=paths
            if wildcards:profile['wildcards']=wildcards
            if any_types:profile['any_type_paths']=any_types
            type_rules.append(profile)
    element_rules=[]
    # A global element can reach another wildcard or itself. Compile each
    # declaration once, then activate its relative rules at an actual XML node.
    visited=set()
    while reachable:
        key,declaration=reachable.popitem()
        if key in visited:continue
        visited.add(key)
        tree.fields=[]
        tree.field(declaration, coalesce_choices=False, skip_inactive_particles=True, native_identity_constraints=True, validation_particles=particle_elements)
        applicable=collect(tree.fields,relative=True)
        paths,wildcards,any_types=wildcard_rules(tree.fields,relative=True)
        profile=dict(element=list(key),checks=applicable)
        if paths:profile['wildcard_paths']=paths
        if wildcards:profile['wildcards']=wildcards
        if any_types:profile['any_type_paths']=any_types
        element_rules.append(profile)
    uncovered = exact_uses.keys() - {node for node in exact_uses if nodes[node] in calendar_projections}
    if uncovered:
        node = min(uncovered, key=lambda item: (tree.owner[item], nodes[item].getroottree().getpath(nodes[item])))
        unsupported(node, _ordered_calendar_facets(exact_uses[node]))
    for declaration, attribute in explicit_calendar_types.items():
        ns, name = tree.context[declaration][2] or '', declaration.get('name')
        projection = calendar_projections[nodes[attribute]]
        type_rules.append(dict(type=[ns,name], checks=[dict(path=[], attribute=[ns,name], field=name,
            element=projection['element'], element_value=True, calendar_compound=dict(shape=projection['shape']))]))
        calendar_projections[nodes[declaration]] = dict(explicit_type=True)
    if not probe_names and not id_types:return {},{},set(),{}
    root=etree.SubElement(checks,'{'+XS+'}element',name='checks')
    choice=etree.SubElement(etree.SubElement(root,'{'+XS+'}complexType'),'{'+XS+'}choice',minOccurs='0',maxOccurs='unbounded')
    for element_name in [*probe_names.values(), *member_probes.values(), *item_probes.values(),
                         *(rule['element'] for rule in instance_values),
                         *(rule['comparison'] for rule in compound_probes.values() if 'comparison' in rule)]:
        etree.SubElement(choice,'{'+XS+'}element',ref='probe:'+element_name)
    generated[entry]=etree.tostring(checks,encoding='UTF-8',xml_declaration=True)
    manifest=dict(attribute_checks=rules,attribute_type_checks=type_rules,attribute_type_profiles_complete=True,supplementary_schema=entry,supplementary_namespace=namespace,generated_files=[dict(local_path=name,local_sha256=sha(content)) for name,content in sorted(generated.items())])
    if id_types:manifest['id_types']=id_types
    manifest['document_value_probes']=sorted(document_value_probes)
    if instance_values:manifest['element_type_values']=instance_values
    if any(rule.get('element_value') for profile in [rules,*[item['checks'] for item in type_rules],*[item['checks'] for item in element_rules]] for rule in profile):
        manifest['element_type_whitespace']=[dict(type=[ns,name],mode=mode)
            for (ns,kind,name),node in sorted(tree.symbol.items()) if kind in ('simpleType','complexType')
            if (mode:=_scalar_whitespace(tree,node)) is not None]
    if root_paths:manifest['attribute_wildcard_paths']=root_paths
    if root_wildcards:manifest['attribute_wildcards']=root_wildcards
    if root_any_types:manifest['attribute_any_type_paths']=root_any_types
    if root_wildcards or root_any_types or any(profile.get('wildcards') or profile.get('any_type_paths') for profile in [*type_rules,*element_rules]):
        manifest['attribute_known_elements']=[list(key) for key in sorted(global_elements)]
    if element_rules:manifest['attribute_element_checks']=sorted(element_rules,key=lambda item:item['element'])
    if global_rules:manifest['attribute_global_checks']=global_rules
    return generated,manifest,relaxed_attributes,calendar_projections


def _calendar_projection_error(error_log, files, local_paths, base, projections):
    """Permit a second compile only for projected value constraints.

    After projection, native validation proves the retained lexical facets,
    and the exact probes check the original value constraint across all members.
    """
    def compound_kinds(shape):
        if isinstance(shape, str):
            return set()
        kind, nested = shape[:2]
        if kind == 'union':
            return {kind}.union(*(compound_kinds(member) for _, member in nested))
        return {kind} | compound_kinds(nested)

    candidates = {}
    for name, (_, root) in files.items():
        for node in _schema_components(root):
            if node not in projections or 'fixed' not in node.attrib and 'default' not in node.attrib:
                continue
            profile = projections[node]
            allowed = {'SCHEMAV_CVC_'+facet.upper()+'_VALID' for facet in profile['facets']}
            kinds = compound_kinds(profile['shape'])
            if 'list' in kinds:
                allowed.add('SCHEMAV_CVC_DATATYPE_VALID_1_2_2')
            if 'union' in kinds:
                allowed.add('SCHEMAV_CVC_DATATYPE_VALID_1_2_3')
            candidates[(base/local_paths[name]).as_uri(), root.getroottree().getpath(node)] = allowed
    diagnostics = {}
    for error in error_log:
        key = error.filename, error.path
        if key not in candidates:
            return False
        diagnostics.setdefault(key, set()).add(error.type_name)
    fixed_error = 'SCHEMAP_A_PROPS_CORRECT_2'
    return bool(diagnostics) and all(fixed_error in kinds and 1 < len(kinds)
                                     and kinds <= candidates[key] | {fixed_error}
                                     for key, kinds in diagnostics.items())


def _boolean_fixed_copies(row, selected, files, local_paths, relaxed_attributes):
    """Relax scalar fixed for a second pass; typed probes enforce value equality.

    The manifest key and file names retain their legacy boolean-only spelling.
    """
    kinds = (X+'element', X+'attribute') if relaxed_attributes else (X+'element',)
    def candidates(root):
        return [node for node in _schema_components(root) if node.tag in kinds and node.get('fixed') is not None]

    if not any(candidates(root) for _, root in files.values()):
        return {}, {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        write_archive_targets(archive,getattr(selected,'import_targets',{}))
        for name, content in selected.items():
            archive.writestr(name, content)
    with zipfile.ZipFile(buffer) as archive:
        tree = Schema(archive, row['schema_path'], row['root'])

    generated, replacements = {}, {}
    for index, (name, (_, root)) in enumerate(files.items()):
        declarations = candidates(root)
        if not declarations:
            continue
        # A chameleon file can acquire different types from different owners.
        contexts = [candidates(doc[0]) for (path, _), doc in tree.documents.items() if path == name]
        indices = [i for i, declaration in enumerate(declarations)
                   if declaration in relaxed_attributes or declaration.tag == X+'element' and contexts
                   and all(_element_scalar_source(tree,nodes[i]) is not None for nodes in contexts)]
        if not indices:
            continue
        alternate = copy.deepcopy(root)
        nodes = candidates(alternate)
        for i in indices:
            value = nodes[i].attrib.pop('fixed')
            if nodes[i].tag == X+'element' or nodes[i].get('use', 'optional') == 'optional':
                nodes[i].set('default', value)
        alternate_name = f'schemas/boolean-fixed-{index}.xsd'
        generated[alternate_name] = etree.tostring(alternate, encoding='UTF-8', xml_declaration=True)
        replacements[local_paths[name]] = alternate_name
    return generated, replacements


def _prepare(row, selected):
    files={}
    for name,content in sorted(selected.items()):
        root=etree.fromstring(content,etree.XMLParser(no_network=True,resolve_entities=False,load_dtd=False))
        if root.tag!='{'+XS+'}schema' or root.getroottree().docinfo.doctype:raise Unavailable('Файл не является автономной XSD: '+name)
        check_dependency_bases(root, name)
        files[name]=(content,root)
    entry=row['schema_path'];root=files[entry][1]
    from schema import included_roots, import_metadata
    targets=getattr(selected,'import_targets',{})
    inventory={name:import_metadata(node,sha(raw)) | {'roots': node.findall(X+'element'), 'import_targets': targets.get(name,{})} for name,(raw,node) in files.items()}
    if root.get('targetNamespace')!=row.get('namespace') or not any(e.get('name')==row['root'] for e in included_roots(inventory,entry)):
        raise Unavailable('Корень или namespace не соответствует выбранной редакции')
    reference_targets={};contexts=set()
    def visit(name,inherited_namespace=None):
        document=files[name][1]
        namespace=document.get('targetNamespace') or inherited_namespace
        if (name,namespace) in contexts:return
        contexts.add((name,namespace))
        for reference in document:
            if reference.tag not in REFERENCES:continue
            target=_resolve(files,name,reference,namespace,targets)
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
        if filename.lower().endswith('.xsd'):filename=filename[:-4]+'.xsd'
        else:filename+='.xsd'
        original_paths[name]='originals/'+filename
        local_paths[name]='message.xsd' if name==entry else 'schemas/'+filename
    output={};records={}
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
        records[name]=dict(source_path=name,original_path=original_paths[name],local_path=local_paths[name],original_sha256=sha(content),local_sha256=sha(local),rewrites=changes)
    generated,constraints,relaxed_attributes,projections=_attribute_checks(row,selected,files,local_paths)
    if any(schema_mentions_id(root) for _,root in files.values()):constraints['document_ids_complete'] = True
    output.update(generated)
    with tempfile.TemporaryDirectory(prefix='sedo-xsd-compile-') as folder:
        base=Path(folder).resolve()
        def write_local(name, content):
            target=base/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(content)

        for name,content in output.items():
            if not name.startswith('originals/'):
                write_local(name,content)
        try:
            compile_schema(base/'message.xsd',base)
        except etree.XMLSchemaParseError as exc:
            if not _calendar_projection_error(exc.error_log, files, local_paths, base, projections):
                raise
        if projections:
            # Even when the fixed literal compiles, a timezone alias can hit
            # rounded facet comparisons before the exact validation runs.
            for name, (_, root) in files.items():
                affected = [node for node in _schema_components(root) if node in projections]
                if not affected:
                    continue
                projected_names = {}
                for node in affected:
                    if projections[node].get('explicit_type'):
                        restriction = node.find(X+'restriction')
                        for facet in list(restriction):
                            if facet.tag in ORDERED_FACET_TAGS:
                                restriction.remove(facet)
                        records[name].setdefault('calendar_fixed_projections', []).append(root.getroottree().getpath(node))
                        continue
                    node.attrib.pop('type', None)
                    existing = node.find(X+'simpleType')
                    if existing is not None:
                        node.remove(existing)
                    replacement = projections[node]['replacement']
                    projected = _copy_type(replacement, node)
                    projected_names[root.getroottree().getelementpath(projected)] = tuple(_type_references(replacement))
                    records[name].setdefault('calendar_fixed_projections', []).append(root.getroottree().getpath(node))
                path=local_paths[name]
                output[path]=etree.tostring(root,encoding='UTF-8',xml_declaration=True)
                reparsed = etree.fromstring(output[path])
                if any(tuple(_type_references(reparsed.find(location))) != expected
                       for location, expected in projected_names.items()):
                    raise Unavailable('Изменилось пространство имён после переноса типа: '+name)
                records[name]['local_sha256']=sha(output[path])
                write_local(path,output[path])
            compile_schema(base/'message.xsd',base)
        if 'supplementary_schema' in constraints:
            supplementary = compile_schema(base/constraints['supplementary_schema'], base)
            if projections:
                accepts, compare = compound_probes(supplementary, constraints['supplementary_namespace'])

                def matches(probe, value, scope):
                    return accepts(probe, value, tuple(sorted(scope.items())))[0]

                for node, profile in projections.items():
                    value = node.get('fixed', node.get('default'))
                    if value is None:
                        continue
                    scope = profile['namespaces']
                    exact, _ = _compound_value(profile['shape'], value, matches, compare, scope)
                    if exact in ('unknown', 'excluded') or not matches(profile['element'], value, scope):
                        raise Unavailable('Значение fixed/default нарушает точное календарное ограничение: '
                                          + node.getroottree().getpath(node))
        copies, replacements = _boolean_fixed_copies(row, selected, files, local_paths, relaxed_attributes)
        if replacements:
            output.update(copies)
            constraints['boolean_fixed_replacements'] = replacements
            constraints.setdefault('generated_files', []).extend(dict(local_path=name,local_sha256=sha(content))
                                                                 for name,content in copies.items())
            for name,content in copies.items():
                write_local(name,content)
            compile_schema(base/'message.xsd', base, replacements=replacements)
    return output,list(records.values()),constraints


def _zip(row, output, records, constraints=None):
    manifest={k:row.get(k) for k in ('number','environment','revision_id','document_version','label_date','root','namespace','schema_path','archive_sha256','archive_url','document_url','schema_source_type','schema_source_url','schema_sources')}
    for key in ('schema_declaration_path', 'schema_docx_location', 'schema_docx_path'):
        if row.get(key):
            manifest[key] = row[key]
    manifest.update(format=1,entrypoint='message.xsd',files=records)
    manifest.update(constraints or {})
    output=dict(output)
    output['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode('utf8')
    output['validate.py']=Path(__file__).with_name('xsd_validate.py').read_text('utf-8-sig').encode('utf8')
    output['requirements.txt']=('lxml=='+LXML_VERSION+'\n').encode('ascii')
    constraint_note=('Дополнительная схема schemas/attribute-checks.xsd участвует в проверке значений атрибутов и элементов.\n'
                     'Используйте validate.py для полной проверки значений с учётом типа XSD.\n'
                     if constraints and 'supplementary_schema' in constraints else '')
    if constraints and constraints.get('boolean_fixed_replacements'):
        constraint_note += 'Скрипт validate.py сравнивает fixed в пространстве значений типа XSD, сохраняя pattern и фактический xsi:type.\n'
    if any(record.get('calendar_fixed_projections') for record in records):
        local_note = ('В рабочих XSD изменены пути schemaLocation и типы отдельных атрибутов.\n'
                      'Полная проверка их точных календарных ограничений выполняется скриптом validate.py.\n')
    else:
        local_note = ('schemas и message.xsd — рабочие копии: изменены только пути schemaLocation\n'
                      'для автономного разрешения зависимостей. Типы, namespace и ограничения сохранены.\n')
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
{local_note}{constraint_note}manifest.json содержит контрольные суммы, изменения путей и дополнительные проверки.
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
    def particle_elements(particle):
        return (schema.resolve(particle.get('ref'), particle, ['element']),)
    if any(node.get('substitutionGroup') for (_, kind, _), node in schema.symbol.items() if kind == 'element'):
        globals_ = {(ns, name): node for (ns, kind, name), node in schema.symbol.items()
                    if kind == 'element' and node.get('abstract') not in ('true', '1')}
        *_, particle_elements = _reachable_profile_types(schema, globals_)
    schema.field(schema.root, native_identity_constraints=True, validation_particles=particle_elements)
    addresses = {}
    for field in schema.fields:
        addresses.setdefault(field['path'], set()).add(field['ns'])
    return [sorted(addresses.get(path, set())) for path in paths]


def _browser_compatible(pack, raw, compressed):
    """Keep the producer within validation.js/checkPack resource contracts."""
    files=pack['files']
    if not 1<=len(files)<=256 or len(raw)>32*1024*1024 or len(compressed)>8*1024*1024:return False
    if sum(len(value) for value in files.values())>32*1024*1024:return False
    if any(not re.fullmatch(r'(?:message\.xsd|schemas/[A-Za-z0-9_.-]+\.xsd)',name) or '..' in name for name in files):return False
    if 'document_ids_complete' in pack and not isinstance(pack['document_ids_complete'],bool):return False
    if 'attribute_checks' not in pack:return 'id_types' not in pack
    def pair(value):
        return isinstance(value,list) and len(value)==2 and all(isinstance(part,str) and len(part)<=8192 for part in value) and bool(value[1])
    def path(value,relative):
        return isinstance(value,list) and (relative or bool(value)) and len(value)<=256 and all(pair(part) for part in value)
    def guards(rule,relative):
        nonlocal total
        for guard in rule.get('guards',[]):
            up=guard['up'];layouts=guard['layouts'];total+=len(layouts)
            if not 0<=up<len(rule['path'])-(not relative) or not 1<=len(layouts)<=256:return False
            subject=rule['path'][-up-1]
            if not all(path(layout['children'],False) and 0<=layout['index']<len(layout['children'])
                       and layout['children'][layout['index']]==subject for layout in layouts):return False
        return True
    def compound_shape(shape,depth=0):
        nonlocal total
        total+=1
        if depth>64 or total>50000:return False
        if isinstance(shape,str):return shape == 'native' or shape in CALENDAR_TYPES or bool(re.fullmatch(r'item[0-9]+',shape))
        if not isinstance(shape,list) or len(shape) not in (2,3):return False
        kind,content=shape[:2]
        if kind=='list':
            return (len(shape)==2 or isinstance(shape[2],str) and bool(re.fullmatch(r'item[0-9]+',shape[2]))) and compound_shape(content,depth+1)
        if kind=='whitespace':
            return len(shape)==3 and shape[2] in ('preserve','replace','collapse') and compound_shape(content,depth+1)
        if kind == 'limited':
            return len(shape) == 2 and compound_shape(content, depth+1)
        if kind == 'duration-enumeration':
            return (len(shape) == 3 and isinstance(shape[2], list) and 0 < len(shape[2]) <= 50000
                    and all(isinstance(value, str) for value in shape[2]) and compound_shape(content, depth+1))
        if kind == 'calendar-facets':
            if len(shape) != 3 or not isinstance(shape[2], list) or not shape[2]:
                return False
            total += len(shape[2])
            return (total <= 50000 and compound_shape(content, depth+1)
                    and all(isinstance(facet, list) and len(facet) == 3
                            and facet[0] in CALENDAR_VALUE_FACETS and isinstance(facet[1], str)
                            and isinstance(facet[2], dict)
                            and all((not prefix or NCNAME.fullmatch(prefix)) and isinstance(uri, str)
                                    for prefix, uri in facet[2].items())
                            for facet in shape[2]))
        return (len(shape)==2 and kind=='union' and isinstance(content,list) and bool(content)
                and all(isinstance(member,list) and len(member)==2 and isinstance(member[0],str)
                        and re.fullmatch(r'member[0-9]+',member[0]) and compound_shape(member[1],depth+1)
                        for member in content))
    def id_shape(shape,depth=0):
        nonlocal total
        total+=1
        if depth>64 or total>50000:return False
        if isinstance(shape,bool):return True
        return isinstance(shape,list) and bool(shape) and all(isinstance(choice,list) and len(choice)==2
            and isinstance(choice[0],str) and re.fullmatch(r'member[0-9]+',choice[0])
            and id_shape(choice[1],depth+1) for choice in shape)
    def scalar(value):
        if 'id_shape' in value and not id_shape(value['id_shape']):return False
        if 'identity_only' in value and (value['identity_only'] is not True or 'id_shape' not in value):return False
        if 'element_value' in value and not isinstance(value['element_value'],bool):return False
        if 'fixed_value' in value:
            fixed = value['fixed_value']
            if not (value.get('element_value') and isinstance(fixed, dict) and isinstance(fixed.get('lexical'), str)
                    and scope(fixed.get('namespaces'))):return False
        if 'calendar_fixed' in value:
            fixed=value['calendar_fixed']
            if not (isinstance(fixed,list) and len(fixed)==2 and fixed[0] in CALENDAR_TYPES and isinstance(fixed[1],str)):return False
        if 'calendar_compound' not in value:return True
        check=value['calendar_compound']
        if not (isinstance(check,dict) and 'calendar_fixed' not in value
                and isinstance(check.get('shape'),list) and compound_shape(check['shape'])):return False
        if 'fixed' not in check:return set(check)=={'shape'}
        return (isinstance(check['fixed'],str) and isinstance(check.get('comparison'),str)
                and bool(re.fullmatch(r'compound[0-9]+',check['comparison']))
                and ('limited' not in check or isinstance(check['limited'],bool)) and scope(check.get('namespaces')))
    def scope(value):
        return isinstance(value,dict) and all((not prefix or NCNAME.fullmatch(prefix)) and isinstance(uri,str)
                                              for prefix,uri in value.items())
    def rule(value,relative):
        return (path(value['path'],relative) and pair(value['attribute']) and bool(re.fullmatch(r'value[0-9]+',value['element']))
                and isinstance(value['field'],str) and len(value['field'])<=8192 and guards(value,relative) and scalar(value))
    total=len(pack['attribute_checks'])
    id_types=pack.get('id_types',[])
    if not isinstance(id_types,list) or not all(isinstance(p,dict) and pair(p.get('type')) and id_shape(p.get('shape')) for p in id_types):return False
    if not pack.get('supplementary_namespace') or len(pack['supplementary_namespace'])>8192 or pack.get('supplementary_schema') not in files:return False
    document_probes = pack.get('document_value_probes', [])
    if (not isinstance(document_probes, list) or len(document_probes)>50000
            or not all(isinstance(name, str) and len(name)<=8192 and NCNAME.fullmatch(name)
                       for name in document_probes)):
        return False
    if not all(rule(value,False) for value in pack['attribute_checks']):return False
    for key, prefix in (('element_type_unions', 'union'), ('element_type_values', 'effective')):
        profiles = pack.get(key, [])
        total += len(profiles)
        if not all(pair(value['type']) and re.fullmatch(prefix+r'[0-9]+',value['element'])
                   and compound_shape(value['shape']) for value in profiles):
            return False
    global_attributes=pack.get('attribute_global_checks',[])
    total+=len(global_attributes)
    if not all(rule(value,True) and not value['path'] and not value.get('element_value') for value in global_attributes):return False
    def profile(value,relative):
        nonlocal total
        for key in ('wildcard_paths','any_type_paths'):
            rows=value.get(key,[]);total+=len(rows)
            if not all(path(row,relative) for row in rows):return False
        rows=value.get('wildcards',[]);total+=len(rows)
        return all(path(row['path'],relative) and row['process'] in ('skip','lax','strict') and len(row['explicit'])<=50000 and all(pair(item) for item in row['explicit']) for row in rows)
    root={key:pack.get('attribute_'+key,[]) for key in ('wildcard_paths','any_type_paths','wildcards')}
    if not profile(root,False):return False
    known=pack.get('attribute_known_elements',[])
    if len(known)>50000 or not all(pair(value) for value in known):return False
    for key,label in (('attribute_type_checks','type'),('attribute_element_checks','element')):
        rows=pack.get(key,[])
        if len(rows)>50000:return False
        for value in rows:
            total+=len(value['checks'])
            if not pair(value[label]) or not all(rule(item,True) for item in value['checks']) or not profile(value,True):return False
    return total<=50000


def publish_bundles(catalog,state,site,navigation=None):
    archives={};prepared={};namespace_cache={}
    try:
        for row in catalog['revisions']:
            for key in ('schema_download','schema_filename','schema_bundle_sha256','schema_file_count','validation_download','validation_sha256','validation_error'):row.pop(key,None)
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
            raw=json.dumps(pack,ensure_ascii=True,separators=(',',':'),sort_keys=True).encode('utf8')
            browser=gzip.compress(raw,compresslevel=9,mtime=0)
            if not _browser_compatible(pack,raw,browser):
                row['validation_error']='Браузерная проверка недоступна: комплект превышает ограничения браузерного формата. Скачайте XSD (ZIP) для локальной проверки.'
                row.pop('schema_export_error',None)
                continue
            browser=browser[:9]+b'\xff'+browser[10:]  # Stable gzip OS marker across Python/platform versions.
            relative='schemas/'+rid+'.validation.json.gz';target=Path(site)/relative
            if not target.is_file() or target.read_bytes()!=browser:write(target,browser)
            row.update(validation_download=relative,validation_sha256=sha(browser))
            row.pop('schema_export_error',None)
    finally:
        for archive in archives.values():archive.close()
