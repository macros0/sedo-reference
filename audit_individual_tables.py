"""Compare individual Word specification cards with the schema of the same edition."""
import re
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from lxml import etree

from module_xsd import W, text, word_text
from schema import FACETS, Schema, X, XS, field_whitespace, local, occurrence, scalar_whitespace


HEADING = re.compile(r"^Атрибут(?:ы элемента|ы (?:комплексного )?типа)?\s+(\S+)$", re.I)
FACET_NAMES = {
    "Минимальное число символов": "minLength", "Максимальное число символов": "maxLength",
    "Число символов": "length", "Паттерн строки": "pattern",
    "Список допустимых значений": "enumeration", "Количество допустимых цифр": "totalDigits",
    "Максимальное число знаков после десятичной запятой": "fractionDigits",
    "Нижняя граница (значение должно быть больше или равно указанному)": "minInclusive",
    "Верхняя граница (значение должно быть меньше или равно указанному)": "maxInclusive",
}
PROPERTIES = {
    "minOcc": "min", "maxOcc": "max", "use": "use", "fixed": "fixed", "default": "default", "nillable": "nillable",
    "Минимальное число повторений элемента": "min", "Максимальное число повторений элемента": "max",
}
BOUNDS = {"minLength": max, "maxLength": min, "minInclusive": max, "maxInclusive": min,
          "minExclusive": max, "maxExclusive": min, "totalDigits": min, "fractionDigits": min}
VALUE_BOUNDS = {"minInclusive", "maxInclusive", "minExclusive", "maxExclusive"}
INTEGER_FACETS = {"length", "minLength", "maxLength", "totalDigits", "fractionDigits"}
INTEGER_TYPES = {
    "integer", "nonPositiveInteger", "negativeInteger", "long", "int", "short", "byte",
    "nonNegativeInteger", "unsignedLong", "unsignedInt", "unsignedShort", "unsignedByte", "positiveInteger",
}
INTEGER_LEXICAL = re.compile(r"[+-]?[0-9]+")
DECIMAL_LEXICAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
TIME_LEXICAL = re.compile(r"([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})?")
TIME_FRACTION = re.compile(r":[0-9]{2}\.([0-9]+)(?=Z|[+-]|$)")


def cards(body):
    heading = None
    table_number = 0
    for node in body:
        if node.tag == W + "p" and text(node):
            heading = HEADING.fullmatch(text(node))
        if node.tag != W + "tbl":
            continue
        table_number += 1
        if not heading:
            continue
        cells = {}
        for row in node.findall(W + "tr"):
            pair = row.findall(W + "tc")
            if len(pair) == 2:
                cells[text(pair[0])] = pair[1]
        yield table_number, heading[0], heading[1], cells
        heading = None


def cell_literal(cell):
    return "\n".join(word_text(p) for p in cell.iter(W + "p"))


def subrows(cell, literal_columns=()):
    return [[cell_literal(c) if index in literal_columns else text(c) for index, c in enumerate(row.findall(W + "tc"))]
            for row in cell.findall(W + "tbl/" + W + "tr")] if cell is not None else []


def fragment(cell):
    if cell is None:
        return None
    # Adjacent Word runs are contiguous, while paragraph boundaries separate XML tokens.
    source = cell_literal(cell)
    try:
        parser = etree.XMLParser(no_network=True, resolve_entities=False, load_dtd=False, remove_comments=True, remove_pis=True)
        wrapper = etree.fromstring(f'<wrapper xmlns:xs="{XS}" xmlns:xsd="{XS}">{source}</wrapper>'.encode(), parser)
    except etree.XMLSyntaxError:
        return None
    return wrapper[0] if len(wrapper) == 1 and wrapper[0].tag.startswith(X) else None


def declaration_index(schema):
    index = defaultdict(list)

    def visit(node, path, file):
        name = node.get("name") or node.get("ref", "").split(":")[-1]
        if name:
            part = "@" + name if node.tag == X + "attribute" else name
            path = f"{path}/{part}" if path else part
            index[(path, local(node))].append((file, node))
        for child in node:
            if child.tag != X + "annotation":
                visit(child, path, file)

    for file, (root, _, _) in schema.files.items():
        visit(root, "", file)
    return index


def field_index(fields):
    """Also index paths relative to a named type, as used in Word headings."""
    index = defaultdict(list)
    for field in fields:
        path = field["path"]
        index[path].append(field)
        parent = field["parent"]
        while parent >= 0:
            ancestor = fields[parent]
            if ancestor["type"]:
                alias = ancestor["type"].split(":")[-1] + path[len(ancestor["path"]):]
                index[alias].append(field)
            parent = ancestor["parent"]
    return index


def local_rule(node):
    """Local datatype and facets, excluding declarations of descendant fields."""
    if node.get("type"):
        return node.get("type").strip(), (), node
    for child in node:
        if child.tag == X + "simpleType" or node.tag in (X + "simpleType", X + "complexType"):
            restriction = child if child.tag == X + "restriction" else child.find(X + "restriction")
            if restriction is not None:
                return restriction.get("base", "").strip(), tuple(sorted(
                    (local(c), c.get("value", "")) for c in restriction if local(c) in FACETS)), restriction
    if node.tag == X + "element" and node.find(X + "complexType") is None and not node.get("ref"):
        return "xs:anyType", (), None
    return "", (), None


def rule_text(rule):
    base, facets, _ = rule
    return " ".join([base] + [f"{key}={value}" for key, value in facets])


def render_facets(pairs):
    return ", ".join(f"{key}={value}" for key, value in sorted(pairs))


def effective_facets(field):
    facets = set(map(tuple, field["facets"]))
    for key, select in BOUNDS.items():
        bounds = {pair for pair in facets if pair[0] == key}
        if len(bounds) > 1:
            try:
                effective = select(bounds, key=lambda pair: Decimal(pair[1]))
            except InvalidOperation:
                continue  # Bounds on dates and other non-numeric types keep their lexical forms.
            facets -= bounds - {effective}
    return [*facets, *(("enumeration", value) for value, _ in field["values"])]


def unprefixed(qname):
    return qname.rsplit(":", 1)[-1]


def quoted_occurrence(value, note=None):
    """Word text can be prose or an invalid quote; preserve it for reporting."""
    try:return occurrence(value)
    except ValueError:
        if note:note('Кратность в DOCX не распознана: '+str(value))
        return str(value).strip()


def model(node, references=None):
    """Summarise immediate particles, preserving group bounds and choice branches."""
    tag = local(node)
    if tag in ("element", "any", "group"):
        name = node.get("name") or node.get("ref") or "xs:any"
        if references is not None:
            references.append(node)
            if node.get("ref"):
                name = unprefixed(name)
        if tag == "any":
            name += f'({node.get("namespace", "##any")}, {node.get("processContents", "strict")})'
    elif tag in ("sequence", "all", "choice"):
        particles = [c for c in node if local(c) in ("sequence", "all", "choice", "group", "any", "element")]
        if tag in ('all', 'choice'):
            particles.sort(key=lambda child: model(child, []))
        children = [model(c, references) for c in particles]
        name = f'{tag}({", ".join(children)})'
    else:
        for child in node:
            if local(child) in ("complexType", "complexContent", "extension", "restriction"):
                nested = model(child, references)
                if nested:
                    return nested
            if local(child) in ("sequence", "all", "choice", "group"):
                return model(child, references)
        return ""
    bounds = quoted_occurrence(node.get("minOccurs", "1")), quoted_occurrence(node.get("maxOccurs", "1"))
    return name if bounds == ("1", "1") else f"{name}[{bounds[0]}..{bounds[1]}]"


def content_model(declaration, references=None):
    if declaration.tag == X + "element":
        declaration = declaration.find(X + "complexType")
    return model(declaration, references) if declaration is not None else ""


def rule_namespace(rule, schema):
    value, _, owner = rule
    prefix, _, _ = value.rpartition(":")
    if owner is None:
        return XS if value == 'xs:anyType' else None
    if prefix == 'xml':
        return 'http://www.w3.org/XML/1998/namespace'
    if hasattr(owner, 'nsmap'):
        # Quoted Word fragments frequently omit the surrounding schema's
        # prefix declarations. An unknown binding cannot establish a mismatch.
        return owner.nsmap.get(prefix or None)
    return schema.qname_namespace(value, owner) if schema is not None else None


def reference_difference(quoted, source, schema, *, note=None, quoted_target=None):
    names = [(node.get('ref') or node.get('name')).strip() for node in (quoted, source)]
    namespaces = []
    for name, node in zip(names, (quoted, source)):
        if node.get('ref'):
            namespace = rule_namespace((name, (), node), schema)
        elif node is quoted:
            # A quoted local declaration can omit its enclosing schema and
            # elementFormDefault. Namespace declarations alone do not supply it.
            namespace = {'unqualified': '', 'qualified': quoted_target}.get(node.get('form'))
        else:
            namespace = schema.instance_namespace(node) if schema is not None else None
        namespaces.append(namespace)
    if (unprefixed(names[0]) != unprefixed(names[1])
            or None not in namespaces and namespaces[0] != namespaces[1]):
        return [f'{name} ({ns or "без пространства имён"})' if ns is not None else name
                for name, ns in zip(names, namespaces)]
    if note is not None and None in namespaces and bool(quoted.get('ref')) != bool(source.get('ref')):
        note(f'Для элемента {unprefixed(names[0])} пространство имён цитаты не установлено.')


def constraint_qname(value, owner, schema=None):
    prefix, _, name = value.strip().rpartition(':')
    if prefix == 'xml': return ('http://www.w3.org/XML/1998/namespace', name)
    if owner is None: return None
    scope = owner.nsmap if hasattr(owner, 'nsmap') else schema.prefixes[owner] if schema else {}
    binding = prefix or (None if hasattr(owner, 'nsmap') else '')
    if prefix and binding not in scope: return None
    return (scope.get(binding, ''), name)


def constraint_label(value, field, owner, key, *, schema=None, actual=False):
    if value is not None and field and field.get('baseNs') == XS and unprefixed(field.get('base', '')) == 'QName':
        name = constraint_qname(value, owner, schema)
        if name is None and actual:
            name = field.get('flagQNames', {}).get(key)
        if name is not None:
            return value+' ('+(name[0] or 'без пространства имён')+')'
    return value


def boolean_lexical(value):
    value = value.strip(' \t\r\n')
    return {'1': 'true', '0': 'false'}.get(value, value)


def equal_decimal(left, right, lexical):
    left, right = left.strip(' \t\r\n'), right.strip(' \t\r\n')
    return bool(lexical.fullmatch(left) and lexical.fullmatch(right) and Decimal(left) == Decimal(right))


def time_value(value):
    match = TIME_LEXICAL.fullmatch(value.strip(' \t\r\n'))
    if not match:
        return None
    hour, minute, second = map(int, match.groups()[:3])
    fraction = (match[4] or '').rstrip('0')
    if hour > 24 or minute > 59 or second > 59 or (hour == 24 and (minute or second or fraction)):
        return None
    zone = match[5]
    offset = 0
    if zone and zone != 'Z':
        hours, minutes = int(zone[1:3]), int(zone[4:])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            return None
        offset = (-1 if zone[0] == '-' else 1) * (hours * 60 + minutes)
    # xs:time recurs daily, including when UTC normalization crosses midnight.
    utc_seconds = (hour * 3600 + (minute - offset) * 60 + second) % 86400
    return bool(zone), utc_seconds, fraction


def equal_constraint(left, right, field, *, quoted=None, source=None, schema=None, key=None, note=None):
    if left is None or right is None or not field or field.get('baseNs') != XS:
        return left == right
    base = unprefixed(field.get('base', ''))
    if base == 'QName':
        a = constraint_qname(left, quoted, schema)
        b = constraint_qname(right, source, schema)
        if b is None and key in field.get('flagQNames', {}): b = tuple(field['flagQNames'][key])
        if a is None or b is None:
            if note: note('Для значения QName контекст пространства имён цитаты не установлен; равенство автоматически не проверено.')
            return True
        return a == b
    if left == right: return True
    if base in INTEGER_TYPES:
        return equal_decimal(left, right, INTEGER_LEXICAL)
    if base == 'decimal':
        return equal_decimal(left, right, DECIMAL_LEXICAL)
    if base == 'time':
        known = time_value(right)
        return known is not None and time_value(left) == known
    if base == 'dateTime':
        def fraction(value):
            match = TIME_FRACTION.search(value.strip(' \t\r\n'))
            return match[1].rstrip('0') if match else ''
        if fraction(left) != fraction(right):
            return False
    # libxml2 compares attribute fixed in the XSD value space.
    try:
        root = etree.Element(X+'schema', nsmap={'xs': XS})
        element = etree.SubElement(root, X+'element', name='value')
        attr = etree.SubElement(etree.SubElement(element, X+'complexType'), X+'attribute', name='v', fixed=right)
        restriction = etree.SubElement(etree.SubElement(attr, X+'simpleType'), X+'restriction', base='xs:'+base)
        for facet, value in field.get('facets', []):
            if facet == 'whiteSpace': etree.SubElement(restriction, X+facet, value=value)
        validator = etree.XMLSchema(root)
        return validator.validate(etree.Element('value', v=left))
    except (etree.LxmlError, ValueError):
        if note: note('Не удалось сравнить fixed/default в пространстве значений типа '+base+'.')
        return True


def child_order_matches(field, listed, actual):
    """Require sequence order; all/choice branches do not impose cross-order."""
    if set(listed) != set(actual): return False
    # A flat list can repeat names from separate choice branches. It provides
    # membership, while the branch boundaries needed to prove order are absent.
    if len(listed) != len(set(listed)):
        return True
    positions = {name: index for index, name in enumerate(listed)}
    def visit(node):
        if isinstance(node, str): return [positions[node]] if node in positions else []
        if not node: return []
        children = [visit(child) for child in node[1:]]
        if any(child is None for child in children): return None
        if node[0] == 'sequence':
            nonempty = [child for child in children if child]
            if any(max(a) > min(b) for a, b in zip(nonempty, nonempty[1:])): return None
        return [position for child in children for position in child]
    content = field.get('contentModel')
    return visit(content) is not None if content else list(dict.fromkeys(listed)) == actual


def quoted_enum_whitespace(rule, schema, fallback):
    base, _, owner = rule
    if owner is None:
        return fallback
    anonymous = owner.find(X + 'simpleType') if not base else None
    if anonymous is not None:
        inner = local_rule(anonymous)
        explicit = next((value for key, value in inner[1] if key == 'whiteSpace'), None)
        return explicit or quoted_enum_whitespace(inner, schema, fallback)
    namespace = rule_namespace(rule, schema)
    if namespace == XS:
        return scalar_whitespace(unprefixed(base))
    named = schema.symbol.get((namespace, 'simpleType', unprefixed(base))) if schema is not None else None
    if named is not None:
        try:
            return field_whitespace(expanded(schema, named)[0]) or fallback
        except ValueError:
            pass
    return fallback


def facet_difference(left, right, field, quoted, source, schema, note=None, *, effective=False):
    """Compare facet values in each declaration's datatype and namespace context."""
    qname = bool(field and field.get('baseNs') == XS and unprefixed(field.get('base', '')) == 'QName')
    string_enum = bool(field and field.get('baseNs') == XS
                       and unprefixed(field.get('base', '')) in ('string', 'normalizedString', 'token'))
    if string_enum:
        source_mode = field.get('enumWhiteSpace', field_whitespace(field))
        quoted_mode = source_mode
        if quoted is not None and not effective:
            quoted_mode = quoted_enum_whitespace(local_rule(quoted), schema, source_mode)

    def normalized(value, mode):
        if mode in ('replace', 'collapse'):
            value = re.sub(r'[\t\r\n]', ' ', value)
        return re.sub(' +', ' ', value).strip(' ') if mode == 'collapse' else value

    def records(pairs, root, actual=False):
        restriction=local_rule(root)[2] if root is not None else None
        for key,value in pairs:
            if key != 'enumeration' or not qname:
                yield key,value,None,root
                continue
            # Several equal lexical tokens may denote distinct values, because
            # xmlns can be rebound on each enumeration declaration.
            nodes=[node for node in restriction if local(node) == 'enumeration' and node.get('value') == value] if restriction is not None else []
            resolved=[]
            if actual and effective:
                resolved=[tuple(name) for (lexical,_),name in zip(field.get('values',[]),field.get('valueQNames',[])) if lexical == value]
            if not resolved:
                resolved=[constraint_qname(value,node,schema) for node in nodes] if nodes else [constraint_qname(value,root,schema)]
            for name in resolved:
                if name is None and note:
                    note('Для перечисления QName контекст пространства имён цитаты не установлен; равенство автоматически не проверено.')
                yield key,value,name,root
    a_rows=list(records(left,quoted)); b_rows=list(records(right,source,True))
    def same(a,b):
        if a[0] != b[0]:return False
        if a[0] == 'enumeration' and qname:
            return a[2] is None or b[2] is None or a[2] == b[2]
        if a[1] == b[1]:return True
        if a[0] in INTEGER_FACETS:
            return equal_decimal(a[1], b[1], INTEGER_LEXICAL)
        if a[0] == 'enumeration' or a[0] in VALUE_BOUNDS:
            return equal_constraint(a[1],b[1],field,quoted=a[3],source=b[3],schema=schema,note=note)
        return False
    def display(row, *, actual):
        key,value,name,_=row
        if key == 'enumeration' and qname and name is not None:
            value += ' ({'+name[0]+'}'+name[1]+')'
        if key == 'enumeration' and string_enum and quoted_mode != source_mode:
            value += ' (whiteSpace базы: '+(source_mode if actual else quoted_mode)+')'
        return key,value
    a_exact={(row[0],row[1]) for row in a_rows};b_exact={(row[0],row[1]) for row in b_rows}
    if string_enum:
        a_enums = {normalized(value, quoted_mode) for key, value, _, _ in a_rows if key == 'enumeration'}
        b_enums = {normalized(value, source_mode) for key, value, _, _ in b_rows if key == 'enumeration'}
    def exact_scalar(row,exact):
        # Equal scalar spelling proves equal value without compiling a probe.
        # QName spelling needs its declaration URI. Keep every opposite row for
        # unmatched aliases: {1,1.0} and {1} still have the same decimal values.
        return not qname and (row[0],row[1]) in exact
    def missing(row, opposite, exact, *, actual):
        if string_enum and row[0] == 'enumeration':
            if actual:
                return normalized(row[1], source_mode) not in a_enums
            return normalized(row[1], quoted_mode) not in b_enums
        return not exact_scalar(row, exact) and not any(same(other,row) if actual else same(row,other) for other in opposite)
    # The typed probe always keeps Word on the left and source on the right:
    # invalid Word tokens are rejected by the valid source constraint.
    return ({display(a,actual=False) for a in a_rows if missing(a,b_rows,b_exact,actual=False)},
            {display(b,actual=True) for b in b_rows if missing(b,a_rows,a_exact,actual=True)})


def particle_signature(node, leaf=None):
    """Canonical immediate model, preserving compositor bounds and nested groups."""
    if isinstance(node, str): return leaf(node) if leaf else node
    if isinstance(node, dict):
        kind, low, high = node['kind'], occurrence(node.get('min', '1')), occurrence(node.get('max', '1'))
        children = [particle_signature(child, leaf) for child in node['children']]
    else:
        kind = local(node)
        if kind in ('element', 'any', 'group'):
            return leaf(node) if leaf else node.get('name') or unprefixed(node.get('ref', '')) or '*'
        if kind not in ('sequence', 'all', 'choice'):
            for child in node:
                if local(child) in ('complexType', 'complexContent', 'extension', 'restriction', 'sequence', 'all', 'choice', 'group'):
                    result = particle_signature(child, leaf)
                    if result is not None: return result
            return None
        low, high = quoted_occurrence(node.get('minOccurs', '1')), quoted_occurrence(node.get('maxOccurs', '1'))
        children = [particle_signature(child, leaf) for child in node if local(child) in ('sequence', 'all', 'choice', 'group', 'any', 'element')]
    if kind in ('all', 'choice'): children.sort(key=repr)
    return kind, low, high, tuple(children)


def particle_text(signature):
    if signature is None: return 'Модель содержимого отсутствует.'
    if isinstance(signature, str): return signature
    kind, low, high, children = signature
    label = kind+'('+', '.join(particle_text(child) for child in children)+')'
    return label if (low, high) == ('1', '1') else label+f'[{low}..{high}]'


def check_card(cells, field, children, embedded, source, report, schema=None, *, note=None):
    labels = {key: text(cell) for key, cell in cells.items()}
    rule = local_rule(source) if source is not None else (field["type"] or field["base"], (), None)
    display_type = labels.get("Тип", "")
    stated_type = re.sub(r"^(?:restriction of|extension of|Ограничение типа|Расширение типа)\s+", "", display_type)
    if stated_type and rule[0] and unprefixed(stated_type) != unprefixed(rule[0]):
        report("Имя типа", "Тип", display_type, rule[0], "По названию из таблицы будет выбран другой тип.")
    if embedded is not None and source is not None:
        expected = local_rule(embedded)
        declared_ns, actual_ns = rule_namespace(expected,schema), rule_namespace(rule,schema)
        different_namespace = declared_ns is not None and actual_ns is not None and declared_ns != actual_ns
        facet_missing, facet_extra = facet_difference(expected[1], rule[1], field, embedded, source, schema, note)
        if different_namespace or unprefixed(expected[0]) != unprefixed(rule[0]) or facet_missing or facet_extra:
            documented = rule_text(expected) or "Комплексное содержимое"
            actual = rule_text(rule) or "Комплексное содержимое"
            if documented == actual or field and field.get('baseNs') == XS and unprefixed(field.get('base','')) == 'QName':
                if facet_missing: documented += " · " + render_facets(facet_missing)
                if facet_extra: actual += " · " + render_facets(facet_extra)
            if different_namespace:
                documented += " (" + (declared_ns or "без пространства имён") + ")"
                actual += " (" + (actual_ns or "без пространства имён") + ")"
            report("Объявление типа", "Схема", documented, actual, "Объявления задают разные правила проверки значения.")
        for key, default in (("name", None), ("ref", None), ("minOccurs", "1"), ("maxOccurs", "1"), ("use", "optional"),
                             ("fixed", None), ("default", None), ("nillable", "false")):
            a, b = embedded.get(key, default), source.get(key, default)
            if key in ('minOccurs','maxOccurs'):a, b = quoted_occurrence(a,note), occurrence(b)
            if key == 'nillable':
                a, b = boolean_lexical(a), boolean_lexical(b)
            if key in ('fixed', 'default') and equal_constraint(a, b, field, quoted=embedded, source=source, schema=schema, key=key, note=note):
                continue
            if key == 'ref' and a and b:
                difference = reference_difference(embedded, source, schema)
                if not difference:
                    continue
                a, b = difference
            if a != b or key in ('fixed', 'default'):
                if key in ('fixed', 'default'):
                    a = constraint_label(a, field, embedded, key, schema=schema)
                    b = constraint_label(b, field, source, key, schema=schema, actual=True)
                report("Свойство объявления", "Схема", f"{key}={a if a is not None else 'не задано'}",
                       f"{key}={b if b is not None else 'не задано'}", "Правила проверки поля различаются.")
    if field is None:
        return

    actual_props = dict(field.get("flags", {}), min=field["min"], max=field["max"],
                        use="required" if field["min"] == "1" else "optional")
    actual_props.setdefault("nillable", "false")
    for row in subrows(cells.get("Свойства"), literal_columns=(1,)):
        if len(row) < 2 or row[0] not in PROPERTIES:
            continue
        key = PROPERTIES[row[0]]
        actual = actual_props.get(key)
        value = row[1] if key in ('fixed', 'default') else ' '.join(row[1].split())
        if key in ('fixed', 'default') and not value.strip() and actual is None:
            continue
        expected = "unbounded" if value == "Неограниченное число раз" else value
        if key in ('min','max'):expected, actual = quoted_occurrence(expected,note), occurrence(actual)
        if key == 'nillable':
            expected, actual = boolean_lexical(expected), boolean_lexical(actual)
        if key in ('fixed', 'default') and equal_constraint(expected, actual, field, quoted=embedded, source=source, schema=schema, key=key, note=note):
            continue
        if expected != actual or key in ('fixed', 'default'):
            expected = constraint_label(expected, field, embedded, key, schema=schema)
            actual = constraint_label(actual, field, source, key, schema=schema, actual=True)
            displayed = expected if field.get('baseNs') == XS and unprefixed(field.get('base', '')) == 'QName' else row[1]
            report("Свойство поля", "Свойства", f"{row[0]} = {displayed}", f"{key} = {actual or 'не задано'}",
                   "Таблица и схема задают разные требования к этому полю.")

    facets = effective_facets(field)
    facet_rows = subrows(cells.get("Ограничения"), literal_columns=(1,))
    stated = {(FACET_NAMES.get(row[0], row[0]), row[1]) for row in facet_rows if len(row) >= 2}
    stated = {(k, v) for k, v in stated if k in FACETS}
    # Additional inherited facets may be redundant (e.g. minInclusive 2000 in a
    # base type narrowed to 2001). Only assert disagreements supported by a row.
    missing, extra = facet_difference(stated, facets, field, embedded, source, schema, note, effective=True)
    extra_enums = {pair for pair in extra if pair[0] == "enumeration"}
    if missing or (extra_enums and any(key == "enumeration" for key, _ in stated)):
        affected = {key for key, _ in missing | extra_enums}
        report("Ограничения значения", "Ограничения", render_facets({p for p in stated if p[0] in affected} | missing),
               render_facets({p for p in facets if p[0] in affected} | extra_enums) or "Ограничение данного вида отсутствует.",
               "Допустимые форматы или значения поля расходятся.")

    children_mismatch = False
    if "Дочерние элементы" in labels:
        listed = labels["Дочерние элементы"].replace(",", " ").split()
        names = [c["name"] for c in children if not c["name"].startswith("@")]
        actual = list(dict.fromkeys(n for n in names if n != "*"))
        if not child_order_matches(field, listed, actual):
            children_mismatch = True
            model = ["xs:any" if n == "*" else n for n in names]
            report("Состав и порядок элементов", "Дочерние элементы", " ".join(listed),
                   " ".join(model) or "Дочерних элементов нет.",
                   "XML по списку полей DOCX может нарушить модель содержимого XSD.")
    if not children_mismatch and embedded is not None and source is not None:
        quoted_refs, source_refs = [], []
        documented_model = content_model(embedded, quoted_refs)
        source_model = content_model(source, source_refs)
        if documented_model == source_model and len(quoted_refs) == len(source_refs):
            # A card with a nonempty declaration namespace also supplies the
            # targetNamespace for qualified descendants of a local parent.
            quoted_target = None
            if schema is not None:
                quoted_target = labels.get('Пространство имен') or labels.get('Пространство имён')
            for quoted, declared in zip(quoted_refs, source_refs):
                a_ref, b_ref = quoted.get('ref'), declared.get('ref')
                if quoted.tag != declared.tag:
                    continue
                if not (a_ref and b_ref) and quoted.tag != X+'element':
                    continue
                difference = reference_difference(quoted, declared, schema, note=note, quoted_target=quoted_target)
                if difference:
                    documented_model += (' · ref=' if a_ref else ' · name=') + difference[0]
                    source_model += (' · ref=' if b_ref else ' · name=') + difference[1]
        if documented_model != source_model:
            report("Модель содержимого", "Схема", documented_model or "Локальная группа не задана.",
                   source_model or "Локальная группа не задана.",
                   "Состав, группировка или кратность дочерних элементов расходятся.")

    attributes = {c["name"][1:]: c for c in children if c["name"].startswith("@")}
    attr_rows = subrows(cells.get("Атрибуты"), literal_columns=(3, 4))[1:]
    for row in attr_rows:
        if len(row) < 5:
            continue
        name, label, use, default, fixed = row[:5]
        attr = attributes.get(name)
        if attr is None:
            report("Состав атрибутов", "Атрибуты", name, "Именованный атрибут отсутствует.",
                   "Схема не объявляет атрибут из таблицы.")
            continue
        flags = attr["flags"]
        # Empty Word columns can contain padding. A declared whitespace-only
        # XSD value still has to be compared literally.
        if not default.strip() and 'default' not in flags:
            default = ''
        if not fixed.strip() and 'fixed' not in flags:
            fixed = ''
        expected = [unprefixed(label), use or "optional", default, fixed]
        observed = [unprefixed(attr["type"] or attr["base"]), "required" if attr["min"] == "1" else "optional",
                    flags.get("default", ""), flags.get("fixed", "")]
        quoted_attribute = next((node for node in embedded.iter() if local(node) == 'attribute' and (node.get('name') or unprefixed(node.get('ref', ''))) == name), embedded) if embedded is not None else None
        same_constraints = True
        for value, key in zip(expected[2:], ('default', 'fixed')):
            actual = flags.get(key)
            if actual is None and not value: value = None
            if not equal_constraint(value, actual, attr, quoted=quoted_attribute, key=key, note=note):
                same_constraints = False
        if expected[:2] != observed[:2] or not same_constraints:
            report("Свойства атрибута", "Атрибуты", name + " " + " ".join(expected).strip(),
                   name + " " + " ".join(observed).strip(), "Описания типа или использования атрибута расходятся.")
    if attr_rows and all(len(row) >= 5 for row in attr_rows):
        omitted = set(attributes) - {row[0] for row in attr_rows} - {"*"}
        if omitted:
            report("Состав атрибутов", "Атрибуты", "Отсутствуют строки для " + ", ".join(sorted(omitted)),
                   "Объявлены атрибуты " + ", ".join(sorted(omitted)), "Таблица не перечисляет все атрибуты.")


def expanded(schema, source):
    """Use the catalog's resolver for a field or a stand-alone named type."""
    declaration = source
    state = schema.fields, schema.field_declarations, schema.expanded_fields
    if local(source) in ("complexType", "simpleType"):
        declaration = ET.Element(X + "element", {"name": source.get("name", "")})
        declaration.append(source)
        schema.owner[declaration] = schema.owner[source]
        schema.prefixes[declaration] = schema.prefixes[source]
        schema.context[declaration] = schema.context[source]
    try:
        schema.fields, schema.field_declarations, schema.expanded_fields = [], {}, 0
        schema.field(declaration)
        field = schema.fields[0]
        return field, [schema.fields[i] for i in field["children"]]
    finally:
        schema.fields, schema.field_declarations, schema.expanded_fields = state
        if declaration is not source:
            for mapping in (schema.owner, schema.prefixes, schema.context):
                mapping.pop(declaration, None)


def audit(revision, body, fields, *, archive_path=None, notes=None):
    """Return the number of comparable Word cards and their current differences.

    Passing the edition's ZIP enables declaration-level and unused-type checks.
    Without it only cards resolvable against the expanded field tree are compared.
    """
    notes = notes if notes is not None else []
    schema = None
    declarations = {}
    if archive_path is not None:
        with zipfile.ZipFile(archive_path) as archive:
            schema = Schema(archive, revision["schema_path"], revision["root"])
        declarations = declaration_index(schema)
    by_path = field_index(fields)
    findings = []
    checked = 0
    for number, title, path, cells in cards(body):
        embedded = fragment(cells.get("Схема"))
        kind = local(embedded) if embedded is not None else ("attribute" if "/@" in path else "element")
        candidates = declarations.get((path, kind), [])
        namespace = text(cells["Пространство имен"]) if "Пространство имен" in cells else None
        if namespace and schema and len(candidates) > 1:
            candidates = [(f, n) for f, n in candidates if schema.files[f][2] == namespace]
        # Several alternatives in a choice can share the same field path. Avoid
        # attributing a difference to an arbitrary declaration from another branch.
        if len(candidates) > 1:
            main = [(f, n) for f, n in candidates if f == revision["schema_path"]]
            if main:
                candidates = main
        if len(candidates) > 1:
            continue
        field_candidates = by_path.get(path, [])
        field = field_candidates[0] if field_candidates else None
        source_file, source = candidates[0] if candidates else (revision.get("schema_path", ""), None)
        if source is None and field is None and (schema is None or embedded is None):
            continue
        def report(kind, row, spec, xsd, impact):
            findings.append(dict(kind=kind, location=f"{title} · таблица {number} · {row}",
                                 spec=spec, xlocation=f"{source_file} — {path}", xsd=xsd, impact=impact))
        if source is None and field is None:
            checked += 1
            report("Отсутствующее объявление", "Схема", f'{kind} name="{embedded.get("name", path)}"',
                   "Такого объявления по указанному пути в схеме и её зависимостях нет.",
                   "Описанный в документе тип или поле нельзя найти по указанному пути.")
            continue
        children = [fields[i] for i in field["children"]] if field is not None else []
        if source is not None:
            try:
                field, children = expanded(schema, source)
            except ValueError as exc:
                notes.append(f"Пропущена карточка {title} · таблица {number} · {source_file} — {path}: {exc}")
                continue
        checked += 1
        def note(message):
            notes.append(f'{title} · таблица {number}: {message}')
        check_card(cells, field, children, embedded, source, report, schema, note=note)
    return checked, findings
