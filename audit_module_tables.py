"""Compare a module's numbered Word sections and shared type tables with its bound XSD tree."""

import posixpath
import re
import zipfile
from ctypes import c_float
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from lxml import etree

from module_xsd import W, word_text
from schema import FACETS, Schema, X, XS
from audit_individual_tables import (boolean_lexical, child_order_matches, constraint_label, constraint_qname,
                                     equal_constraint, particle_signature, particle_text, quoted_occurrence)


MESSAGE = re.compile(r'^Тип сообщения\s+(?:СЭДО\s*)?(?:№\s*)?(\d+)\s*[:—–-]', re.I)
APPENDIX = re.compile(r'^(?:\d+\s+)?(?:Приложение\s*\d|Атрибуты\s+типов$|Атрибутивный/элементный)', re.I)
TARGET = re.compile(r'Атрибуты\s+(?:(?:комплексного|простого|сложного)\s+)?(типа|элемента|атрибута|свойства)\s+([\w:./-]+)', re.I)
IDENTIFIER = re.compile(r'@?(?:[A-Za-z_][\w.-]*:)?[A-Za-z_][\w.-]*\Z')
SCALAR_BASES = {'string', 'normalizedString', 'token', 'boolean', 'decimal', 'integer',
                'int', 'long', 'short', 'byte', 'date', 'dateTime'}


def local_name(value):
    return value.rsplit(':', 1)[-1]


def clean(node):
    return ' '.join(word_text(node).split())


def scalar_validator(base, facets, *, fixed=None):
    schema = etree.Element(X+'schema', nsmap={'xs': XS})
    element = etree.SubElement(schema, X+'element', name='value')
    if fixed is not None:
        element.set('fixed', fixed)
    rule = etree.SubElement(etree.SubElement(element, X+'simpleType'), X+'restriction', base='xs:'+base)
    for key, values in facets.items():
        for value in values:
            etree.SubElement(rule, X+key, value=value)
    return etree.XMLSchema(schema)


def scalar_samples(quoted, actual, differences):
    values = dict.fromkeys([*quoted['enumeration'], *actual['enumeration']])
    for facets in (quoted, actual):
        for key in sorted(differences):
            for bound in facets[key]:
                if key in ('length', 'minLength', 'maxLength') and re.fullmatch(r'[0-9]{1,4}', bound):
                    for size in range(max(0, int(bound)-1), min(256, int(bound)+1)+1):
                        values['x'*size] = None
                if key in ('minInclusive', 'maxInclusive', 'minExclusive', 'maxExclusive'):
                    values[bound] = None
    if differences != {'enumeration'} or not (quoted['enumeration'] and actual['enumeration']):
        values.update(dict.fromkeys(('', 'x', '  x  ', 'a  b', '0', '1', '-1', '0.1', 'true', '2026-01-01')))
    return values


def negative_witness(field, numeric, facets):
    """Return True for an accepted negative value, False for a proven nonnegative domain, or None."""
    values = [value for value, _ in field.get('values', [])]
    fixed = field.get('flags', {}).get('fixed')
    domain = [fixed] if fixed is not None else values
    def negative(value):
        try:
            if numeric == 'double':return float(value) < 0
            if numeric == 'float':return c_float(float(value)).value < 0
            number = Decimal(value)
            return not number.is_nan() and number < 0
        except (InvalidOperation, ValueError, OverflowError):
            return False
    if domain and not any(negative(value) for value in domain):
        return False
    rules = {key: vals for key, vals in facets.items() if vals}
    if values:
        rules['enumeration'] = values
    patterns = field.get('patternGroups') or [rules.get('pattern', [])]
    try:
        validators = [scalar_validator(numeric, dict(rules, pattern=group), fixed=fixed) for group in patterns]
    except etree.XMLSchemaParseError:
        return None
    candidates = [*domain, '-1', '-2', '-0.1', '-0.01']
    for key in ('minInclusive', 'minExclusive', 'maxInclusive', 'maxExclusive'):
        candidates.extend(rules.get(key, []))
    probe = etree.Element('value')
    for value in candidates:
        if negative(value):
            probe.text = value
            if all(validator.validate(probe) for validator in validators):
                return True
    return None


def prepare(body):
    """Extract reusable, read-only table data once per DOCX during a batch build."""
    number = caption = context = ''
    appendix = False
    ordinal = 0
    tables = []
    for block in body:
        if block.tag == W+'p':
            style = block.find(W+'pPr/'+W+'pStyle')
            if style is not None and re.search('toc|оглав|contents', style.get(W+'val', ''), re.I):
                continue
            value = clean(block)
            heading = MESSAGE.match(value)
            if heading:
                number, caption, context, appendix = heading[1], '', '', False
            elif APPENDIX.match(value):
                number, caption, context, appendix = '', '', '', True
            elif re.match(r'^Таблица\s*\d+', value, re.I) or TARGET.match(value):
                caption = value
            elif re.match(r'^Описание параметров', value, re.I):
                target = re.search(r'\b[\w-]+:\s*([a-zA-Z_][\w.-]*)', value)
                context = target[1] if target else ''
        elif block.tag == W+'tbl':
            ordinal += 1
            if not number and not (appendix and TARGET.match(caption)):
                continue
            rows = []
            for tr in block.findall(W+'tr'):
                cells = tr.findall(W+'tc')
                values = [clean(c) for c in cells]
                if len(cells) == 2 and values[0] == 'Схема':
                    # XML attribute values can contain significant repeated spaces.
                    values[1] = '\n'.join(word_text(p) for p in cells[1].iter(W+'p'))
                rows.append(values)
            header = next(((i, col) for i, row in enumerate(rows) if len(row) >= 3
                           and 'тип' in row[1].lower()
                           for col in (2, 3) if col < len(row) and 'вхожд' in row[col].lower()), None)
            properties = {r[0]: r[1] for r in rows if len(r) == 2}
            if header is not None or 'Схема' in properties:
                data = None
                if header is not None:
                    offset, occurrence_col = header
                    data = rows[offset+1:]
                    if occurrence_col == 2:
                        data = [r[:2]+['']+r[2:] for r in data]
                tables.append((number, caption or f'Таблица DOCX № {ordinal}', context,
                               data, properties))
            context = caption = ''
    return tables


def nearby(name, candidates):
    """Suggest a single case variant or one-character omission, only among siblings."""
    def similar(other):
        a, b = sorted((name.lower(), other.lower()), key=len)
        return (a == b or (len(a) >= 6 and len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1)
                or (len(a) >= 4 and len(b) == len(a)+1
                    and any(a == b[:i]+b[i+1:] for i in range(len(b)))))
    matches = [key for key in candidates if similar(key)]
    return matches[0] if len(matches) == 1 else None


class Comparison:
    def __init__(self, revision, fields, archive_path=None):
        self.revision = revision
        self.fields = fields
        self.by_type = defaultdict(list)
        self.by_name = defaultdict(list)
        self.shared_type_paths = []
        self.checked = 0
        self.findings = []
        self.notes = []
        self.archive_path = archive_path
        self.schema = None
        for field in fields:
            self.by_name[field['name'].lstrip('@')].append(field)
            if field.get('type'):
                self.by_type[local_name(field['type'])].append(field)

    def record(self, kind, where, field, spec, xsd, impact):
        filename = field.get('file', '')
        entry = self.revision.get('schema_path', '')
        paths = [p for p, _ in self.revision.get('dependencies', [])
                 if posixpath.basename(p) == filename]
        if len(paths) == 1:
            filename = posixpath.normpath(posixpath.join(posixpath.dirname(entry), paths[0]))
        finding = dict(kind=kind, location=where, spec=spec, xlocation=filename+' · '+field['path'],
                       xsd=xsd, impact=impact)
        if self.shared_type_paths:
            # A single Word type card describes the same relative field in every use.
            suffix = field['path'][len(self.shared_type_paths[0]):]
            finding['field_paths'] = [path + suffix for path in self.shared_type_paths]
        self.findings.append(finding)

    def targets(self, number, title, context, props):
        self.shared_type_paths = []
        root = self.fields[0]
        if number:
            if number != str(self.revision['number']):
                return []
            if context:
                return [f for f in self.by_name.get(context, []) if f['parent'] == 0]
            return [root]
        match = TARGET.match(title)
        if not match:
            return []
        kind, trail = match[1].lower(), [local_name(p) for p in match[2].strip('.').split('/')]
        if kind == 'типа':
            matches = self.by_type.get(trail[-1], [])
            if not matches:
                candidates = [name for name in self.by_type if name.lower() == trail[-1].lower()]
                if len(candidates) == 1:
                    matches = self.by_type[candidates[0]]
            # A type card can describe an imported type sharing its name with the local type.
            if props.get('Пространство имен'):
                matches = [f for f in matches if f.get('typeNs', f.get('ns')) == props['Пространство имен']]
        else:
            matches = self.by_name.get(trail[-1], [])
            if len(trail) == 1:
                matches = [root] if root['name'] == trail[0] else []
            else:
                def same_path(field):
                    for expected in reversed(trail[:-1]):
                        if field['parent'] < 0:
                            return False
                        field = self.fields[field['parent']]
                        if expected not in (field['name'], local_name(field.get('type', ''))):
                            return False
                    return True
                matches = [f for f in matches if same_path(f)]
            if props.get('Пространство имен'):
                matches = [f for f in matches if f.get('ns') == props['Пространство имен']]
        # A shared named type or a local declaration can be reused at multiple XML paths.
        # Compare a single instance only if its definition is unambiguous.
        if kind == 'типа':
            definitions = {(f.get('typeNs', f.get('ns')), f.get('typeFile', f.get('file')),
                            local_name(f.get('type', '')), tuple(self.fields[k]['name'] for k in f['children'])) for f in matches}
        else:
            definitions = {(f.get('ns'), f.get('typeFile'), f.get('file'), f.get('type'),
                            tuple(self.fields[k]['name'] for k in f['children'])) for f in matches}
        if len(definitions) != 1:
            return []
        if kind == 'типа' and len(matches) > 1:
            self.shared_type_paths = [f['path'] for f in matches]
        return matches[:1]

    def compare_field(self, field, where, typ, occurrence, *, descriptive_type=False, declaration=None):
        self.checked += 1
        actual = field.get('type', '')
        # Anonymous restrictions retain their primitive base but the flattened tree does
        # not retain intermediate named bases. They cannot prove a named type mismatch.
        if not actual and typ.startswith(('xs:', 'xsd:')):
            actual = field.get('base', '')
        if typ and IDENTIFIER.fullmatch(typ) and actual:
            base = field.get('base', '')
            names = {local_name(t) for t in (actual, base)}
            # A plain table label such as "String" names the primitive in prose.
            # An explicit QName or an application-defined type is case-sensitive.
            primitive_label = (descriptive_type and ':' not in typ
                               and (field.get('baseNs') == XS or base.startswith(('xs:', 'xsd:')))
                               and typ.casefold() == local_name(base).casefold())
            quoted_ns = declaration.nsmap.get(typ.rpartition(':')[0] or None) if declaration is not None else None
            actual_ns = field.get('typeNs') if field.get('type') else field.get('baseNs')
            if quoted_ns is not None and actual_ns is not None and field.get('type'):
                if (quoted_ns, local_name(typ)) != (actual_ns, local_name(actual)):
                    self.record('Тип данных', where, field, f'Тип {typ} ({quoted_ns}).', f'Тип {actual} ({actual_ns}).',
                                'Генерация XML по таблице может выбрать другое объявление типа.')
            elif local_name(typ) not in names and not primitive_label:
                self.record('Тип данных', where, field, 'Тип '+typ+'.', 'Тип '+actual+'.',
                            'Генерация XML по таблице может задать другой формат значения.')
        if declaration is not None and occurrence:
            def note(message):self.notes.append(where+': '+message)
            occurrence='..'.join(quoted_occurrence(bound,note) for bound in occurrence.split('..'))
        count = re.fullmatch(r'(\d+)(?:\s*(?:\.\.|[-–…])\s*(\d+|∞|unbounded))?', occurrence)
        if count and (declaration is not None or not field.get('group')):
            low, high = str(int(count[1])), (count[2] or count[1]).replace('∞', 'unbounded')
            if high != 'unbounded': high = str(int(high))
            if (low, high) != (field['min'], field['max']):
                self.record('Кратность', where, field, f'Кратность {low}..{high}.',
                            f"Кратность {field['min']}..{field['max']}.",
                            'Таблица и проверка по схеме допускают разное число вхождений.')
        if declaration is not None:
            self.declaration_properties(field, where, declaration)

    def source_schema(self):
        if self.schema is None and self.archive_path is not None:
            with zipfile.ZipFile(self.archive_path) as archive:
                self.schema = Schema(archive, self.revision['schema_path'], self.revision['root'])
        return self.schema

    def declaration_properties(self, field, where, declaration):
        flags = field.get('flags', {})
        keys = ('fixed', 'default', 'nillable') if declaration.tag == 'element' else ('fixed', 'default')
        reference = declaration.get('ref')
        def note(message):
            self.notes.append(f'{where} · {field["path"]}: {message}')

        name = constraint_qname(reference, declaration) if reference else None
        if reference:
            namespace, local = field.get('ns') or '', field['name'].lstrip('@')
            if name is None or (':' not in reference and None not in declaration.nsmap and namespace):
                note(f'Не установлено пространство имён ссылки {reference}; свойства не проверены.')
                return
            if name != (namespace, local):
                self.record('Пространство имён ссылки', where, field,
                            f'{reference} ({name[0] or "без пространства имён"})',
                            f'{local} ({namespace or "без пространства имён"})',
                            'XML по цитате обращается к другому объявлению XSD.')
                return
        schema = self.source_schema() if reference or field.get('alternatives') else None
        target = schema.symbol.get((name[0], declaration.tag, name[1])) if reference and schema is not None else None
        local_constraint = any(key in declaration.attrib for key in ('fixed', 'default'))

        for key in keys:
            owner = declaration
            documented = declaration.get(key)
            actual = flags.get(key)
            if reference and documented is None and (key == 'nillable' or not local_constraint):
                if target is None:
                    if actual is not None:
                        note(f'Не установлено объявление ссылки {reference}; свойство {key} не проверено.')
                    continue
                owner = target
                documented = owner.get(key)
            if key == 'nillable':
                documented = 'false' if documented is None else documented
                actual = 'false' if actual is None else actual
                if boolean_lexical(documented) == boolean_lexical(actual):
                    continue
            elif equal_constraint(documented, actual, field, quoted=owner, schema=schema, key=key, note=note):
                continue
            documented = constraint_label(documented, field, owner, key, schema=schema)
            actual = constraint_label(actual, field, None, key, actual=True)
            self.record('Свойство объявления', where, field,
                        f'{key}={documented if documented is not None else "не задано"}',
                        f'{key}={actual if actual is not None else "не задано"}',
                        'Правила проверки поля различаются.')

    def limits(self, field, where, limit, description, documented_type=''):
        facets = defaultdict(list)
        for key, val in field.get('facets', []):
            facets[key].append(val)
        # Inherited restrictions are intersections, rather than alternatives.
        # Keep the strongest bound so a weaker base cannot satisfy the table.
        for key, select in {'maxLength': min, 'minLength': max, 'totalDigits': min,
                            'fractionDigits': min, 'minInclusive': max, 'minExclusive': max,
                            'maxInclusive': min, 'maxExclusive': min}.items():
            if facets[key]:
                try:
                    facets[key] = [select(facets[key], key=Decimal)]
                except InvalidOperation:
                    pass
        if not facets['length'] and facets['minLength'] and facets['maxLength']:
            low, high = (str(facets[name][0]).strip().lstrip('+') for name in ('minLength', 'maxLength'))
            if low.isdecimal() and high.isdecimal() and low.lstrip('0') == high.lstrip('0'):
                facets['length'] = [low.lstrip('0') or '0']
        if facets['length']:
            facets.pop('maxLength', None)
            facets.pop('minLength', None)
        actual, base = field.get('type', ''), field.get('base', '')
        length = re.search(r'(?:(макс(?:имальная)?|мин(?:имальная)?|точная)\.?\s*)?длинн?а\s*:?\s*(\d+)', limit, re.I)
        documented_length = None
        if length:
            qualifier = (length[1] or '').lower()
            facet = 'maxLength' if qualifier.startswith('макс') else 'minLength' if qualifier.startswith('мин') else 'length'
            documented_length = (facet, length[2])
        elif limit.isdigit() and local_name(base) == 'string':
            documented_length = ('maxLength', limit)
        if (actual and local_name(actual).lower() != local_name(base).lower()
                and local_name(documented_type).lower() == local_name(base).lower()
                and (facets or field.get('values'))):
            # Naming the actual type includes its restrictions by reference.
            # Naming only its primitive base requires those restrictions in text.
            text = limit+' '+description
            def described(key, value):
                if (key, value) == documented_length:
                    return True
                if key == 'pattern':
                    if value in text:return True
                    digits = re.fullmatch(r'\\d\{(\d+)\}|\[0-9\]\{(\d+)\}', value)
                    return bool(digits and re.search(r'(?<!\d)'+(digits[1] or digits[2])+r'\s+цифр', text, re.I))
                labels={'length':r'(?<![\w.])(?:ровно\s+)?длинн?а', 'minLength':r'мин(?:имальная)?\.?\s*длинн?а',
                        'maxLength':r'макс(?:имальная)?\.?\s*длинн?а'}
                if key in labels:return bool(re.search(labels[key]+r'\s*:?\s*'+re.escape(value)+r'(?!\d)', text, re.I))
                return bool(re.search(r'\b'+re.escape(key)+r'\s*[=:]\s*'+re.escape(value)+r'(?![\w.])', text))
            missing=[f'{key}={value}' for key, values in facets.items() for value in values if not described(key,value)]
            if field.get('values') and not all(re.search(r'(?<!\w)'+re.escape(value)+r'(?!\w)',text) for value,_ in field['values']):
                missing.append('перечисление '+', '.join(value for value,_ in field['values']))
            if missing:
                self.record('Ограничение значения', where, field,
                            'Указана примитивная база '+documented_type+' без полного ограничения именованного типа.',
                            'Тип '+actual+': '+'; '.join(missing)+'.',
                            'Значение, допустимое по таблице, может быть отклонено XSD.')
        annotation = field.get('doc', '')
        if (local_name(field.get('base', '')) in ('date', 'dateTime')
                and 'крайняя дата' in description.lower() and 'крайняя дата' in annotation.lower()
                and 'подтверждения прочтения' in description.lower() and 'выгрузки' in annotation.lower()
                and 'подтверждения прочтения' not in annotation.lower()):
            self.record('Описание поля', where, field, description, annotation,
                        'Выгрузка документа и получение подтверждения прочтения обозначают разные события.')
        if documented_length:
            facet, value = documented_length
            if local_name(field.get('base', '')) == 'string' and not field.get('alternatives'):
                actual = facets[facet] or facets['length']
                pattern_lengths = [re.fullmatch(r'\\d\{(\d+)\}', p) for p in facets['pattern']]
                if not actual and pattern_lengths and all(pattern_lengths):
                    possible = {int(m[1]) for m in pattern_lengths}
                    if facet == 'maxLength':
                        actual = [str(max(possible))]
                    elif facet == 'minLength':
                        actual = [str(min(possible))]
                    elif possible != {int(value)}:
                        actual = [', '.join(map(str, sorted(possible)))]
                    else:
                        actual = [value]
                if value not in actual and (actual or not facets['pattern'] or not facets[facet]
                                           and facets['maxLength'] and facet == 'length'):
                    self.record('Ограничение длины', where, field, f'Указано {facet}={value}.',
                                f"{facet}: {', '.join(actual) if actual else 'не задано'}.",
                                'Допустимая длина по таблице может отличаться от проверки схемой.')
        digit_choices = re.fullmatch(r'\\d\{(\d+)\}\s*или\s*\\d\{(\d+)\}', limit, re.I)
        if digit_choices and len(facets['pattern']) == 1:
            interval = re.fullmatch(r'\\d\{(\d+),(\d+)\}', facets['pattern'][0])
            if interval and int(interval[2])-int(interval[1]) > 1:
                self.record('Формат значения', where, field, limit, 'Шаблон '+facets['pattern'][0]+'.',
                            'Схема допускает дополнительные длины последовательности цифр.')
        if re.search(r'\\d\{\d+;\d+\}', limit) and facets['pattern']:
            self.record('Формат значения', where, field, limit, 'Шаблон '+', '.join(facets['pattern'])+'.',
                        'Разделитель в квантификаторе таблицы не соответствует синтаксису регулярных выражений XSD.')
        if local_name(field.get('base', '')) == 'date' and 'ГГГГ-ММ-01' in description and not facets['pattern']:
            self.record('Формат значения', where, field, 'Указан формат ГГГГ-ММ-01.', 'Тип date без ограничения дня.',
                        'Проверка схемы пропускает другие дни месяца.')
        if 'неотрицательн' in limit.lower():
            numeric = local_name(field.get('base', ''))
            if numeric in ('decimal', 'integer', 'int', 'long', 'short', 'byte', 'float', 'double'):
                lower = [(key, value) for key in ('minInclusive', 'minExclusive') for value in facets[key]]
                try:
                    if numeric in ('integer', 'int', 'long', 'short', 'byte'):
                        # -1 is the greatest negative integer. A bound that
                        # excludes it excludes every negative integer, including
                        # minExclusive=-1 and fractional bounds above -1.
                        nonnegative = any(Decimal(value) > -1 or
                                          key == 'minExclusive' and Decimal(value) == -1
                                          for key, value in lower)
                    elif numeric in ('float', 'double'):
                        smallest_negative = -(2.0 ** (-149 if numeric == 'float' else -1074))
                        bounds = [(key, c_float(float(value)).value if numeric == 'float' else float(value))
                                  for key, value in lower]
                        nonnegative = any(value > smallest_negative or
                                          key == 'minExclusive' and value == smallest_negative
                                          for key, value in bounds)
                    else:
                        nonnegative = any(Decimal(value) >= 0 for _, value in lower)
                except InvalidOperation:
                    nonnegative = False
                if not nonnegative:
                    # The lower bound alone is decisive only in the absence
                    # of other restrictions. Prove intersections before reporting.
                    narrowed = (field.get('values') or 'fixed' in field.get('flags', {})
                                or any(vals for key, vals in facets.items() if key not in ('minInclusive', 'minExclusive')))
                    if narrowed:
                        evidence = negative_witness(field, numeric, facets)
                        if evidence is None:
                            self.notes.append(f'Для «{where}» не подтверждено, разрешают ли ограничения XSD отрицательное значение.')
                        nonnegative = evidence is not True
                if not nonnegative:
                    rule = '; '.join(key+'='+value for key, value in lower) if lower else 'нижняя граница не задана'
                    self.record('Диапазон значения', where, field, limit, 'Тип '+numeric+': '+rule+'.',
                            'Отрицательное значение проходит проверку по XSD.')
            elif field['children']:
                self.record('Ограничение значения', where, field, limit, 'Элемент имеет дочерние поля.',
                            'Числовое ограничение неприменимо к блоку данных.')
        if 'допустимые значения' in description.lower() and field.get('values'):
            tokens = set(re.findall(r'(?<![\w.])(\w+)\s*[–—]\s*', description))
            actual = {v for v, _ in field['values']}
            if tokens and tokens != actual:
                self.record('Перечень значений', where, field, 'Перечислены '+', '.join(sorted(tokens))+'.',
                            'Перечислены '+', '.join(sorted(actual))+'.',
                            'Допустимые значения таблицы отличаются от перечисления в XSD.')

    def flat(self, parent, where, rows):
        children = {self.fields[i]['name']: self.fields[i] for i in parent['children']}
        seen = set()
        for row in rows:
            if len(row) < 4 or not IDENTIFIER.fullmatch(row[0]):
                continue
            name = ('@' if row[0].startswith('@') else '') + local_name(row[0].lstrip('@'))
            location = where+' · '+row[0]
            candidates = dict(children)
            if parent['parent'] < 0:
                candidates[parent['name']] = parent
            # Word tables commonly omit @ when only the attribute is present.
            # Keep its internal identity distinct from a same-named element.
            attribute_alias = not name.startswith('@') and name not in candidates and '@' + name in candidates
            matched = '@' + name if attribute_alias else name if name in candidates else nearby(name, candidates)
            if not matched:
                if name in self.by_name:
                    # Flat Word tables can legitimately mix multiple levels.
                    continue
                self.checked += 1
                self.record('Состав сообщения', location, parent, 'В таблице указан '+name+'.',
                            'Такого поля нет в дереве сообщения.',
                            'Схема не описывает тег с таким именем.')
                continue
            field = candidates[matched]
            seen.add(matched)
            if matched != name and not attribute_alias:
                self.record('Имя элемента', location, field, 'В таблице '+name+'.', 'Имя в XSD '+matched+'.',
                            'XML чувствителен к регистру и точному написанию имени.')
            self.compare_field(field, location, row[1], row[3], descriptive_type=True)
            self.limits(field, location, row[2], row[4] if len(row) > 4 else '',row[1])
        if seen:
            for name, field in children.items():
                if name not in seen and field['min'] != '0' and not field.get('group'):
                    self.record('Состав сообщения', where, field,
                                'В таблице отсутствует '+field['name']+'.',
                                f"Обязательное поле {field['name']}, кратность {field['min']}..{field['max']}.",
                                'Сообщение без поля не пройдёт проверку по XSD.')

    def fragment_context(self, field, declaration):
        schema = self.source_schema()
        if schema is None:
            return None
        if declaration.tag == 'complexType' and field.get('type'):
            typename = field['type'].rsplit(':', 1)[-1]
            actual = schema.symbol.get((field.get('typeNs', ''), 'complexType', typename))
            if actual is not None:
                document, _, namespace = schema.context[actual]
                return document, namespace
        if field['parent'] < 0:
            document, _, namespace = schema.context[schema.root]
            return document, namespace
        contexts = {(document, namespace) for node, (document, _, namespace) in schema.context.items()
                    if schema.owner[node].rsplit('/', 1)[-1] == field.get('file')}
        return next(iter(contexts)) if len(contexts) == 1 else None

    @staticmethod
    def quoted_declaration_name(node, context, options):
        reference = node.get('ref')
        if reference:
            if ':' not in reference and None not in node.nsmap and options and all(child.get('ns') for child in options):
                return None
            return constraint_qname(reference, node)
        name = node.get('name')
        form = node.get('form')
        if form == 'unqualified':
            return '', name
        if context is None:
            return None
        document, namespace = context
        form = form or document.get(node.tag+'FormDefault', 'unqualified')
        return ((namespace or '') if form == 'qualified' else ''), name

    def qualified_particle_signatures(self, content, actual_model, children, context):
        names = {name: [child.get('ns') or '' for child in options] for name, options in children.items()}
        repeated = {name: iter(uris) for name, uris in names.items() if len(set(uris)) > 1}
        if not repeated:
            return None
        cited, actual = [], []

        def label(qname):
            namespace, name = qname
            return f'{{{namespace}}}{name}' if namespace else name

        def source_leaf(name):
            default = names.get(name, [''])[0]
            namespace = next(repeated[name], None) if name in repeated else default
            actual.append((namespace, name))
            return label(actual[-1])

        def quoted_leaf(node):
            name = node.get('name') or local_name(node.get('ref', '')) or '*'
            qname = self.quoted_declaration_name(node, context, children.get(name, ())) if node.tag == 'element' else ('', name)
            cited.append(qname)
            return label(qname) if qname is not None else name

        quoted = particle_signature(content, quoted_leaf)
        source = particle_signature(actual_model, source_leaf)
        # Namespace changes are diagnosed at the field itself. Compare order
        # only when both models have the same proven collection of identities.
        if None not in cited and all(ns is not None for ns, _ in actual) and sorted(cited) == sorted(actual):
            return quoted, source
        return None

    def fragment(self, field, where, declaration):
        """A fully inlined complex type can be compared within its enclosing field."""
        content = declaration if declaration.tag == 'complexType' else declaration.find('complexType')
        if content is None:
            return
        children = defaultdict(list)
        for index in field['children']:
            child = self.fields[index]
            children[child['name']].append(child)
        context = self.fragment_context(field, declaration)
        actual_model = field.get('contentModelBounds')
        def complete_model(node):
            if node.tag in ('element', 'attribute', 'annotation'):
                return True
            if node.tag in ('group', 'complexContent', 'simpleContent'):
                return False
            return all(complete_model(child) for child in node)
        if actual_model and complete_model(content):
            quoted_model = particle_signature(content)
            source_model = particle_signature(actual_model)
            qualified = self.qualified_particle_signatures(content, actual_model, children, context)
            if quoted_model == source_model and qualified is not None:
                quoted_model, source_model = qualified
            if quoted_model != source_model:
                self.record('Модель содержимого', where, field, particle_text(quoted_model), particle_text(source_model),
                            'Порядок, группировка или кратность дочерних элементов отличаются от XSD.')
        # The DOCX fragment does not supply inherited group or extension declarations.
        # Comparing such fragments to the expanded tree would mark inherited fields missing.
        partial = any(child.tag in ('complexContent', 'simpleContent', 'group', 'attributeGroup', 'any')
                      for child in content)
        if partial:
            # An explicit use in a restriction is comparable even though the
            # inherited remainder of the model is unavailable in the quote.
            restrictions = content.findall('complexContent/restriction')+content.findall('simpleContent/restriction')
            candidates = [attr for restriction in restrictions for attr in restriction.findall('attribute')]
            if restrictions:
                self.notes.append(f'{where} · {field["path"]}: Наследуемые атрибуты за пределами явных объявлений ограничения не проверены.')
        else:
            candidates = content
        seen = set()

        def visit(group):
            for node in group:
                if node.tag in ('sequence', 'choice', 'all'):
                    if not visit(node):
                        return False
                elif node.tag in ('element', 'attribute'):
                    name = node.get('name') or local_name(node.get('ref', ''))
                    name = ('@' if node.tag == 'attribute' else '')+name.strip()
                    options = children.get(name, [])
                    ref = node.get('ref')
                    qname = self.quoted_declaration_name(node, context, options)
                    matches = [child for child in options if qname == (child.get('ns') or '', name.lstrip('@'))]
                    if node.tag == 'attribute' and node.get('use') == 'prohibited':
                        seen.update(map(id, matches))
                        if matches:
                            for child in matches:
                                self.checked += 1
                                actual = 'required' if child['min'] == '1' else 'optional'
                                self.record('Запрет атрибута', where, child, f'{ref or name}: use=prohibited.',
                                            f'{name}: use={actual}.', 'Цитата запрещает атрибут, который принимает XSD.')
                        elif (qname is None and options) or '@*' in children:
                            seen.update(map(id, options))
                            self.notes.append(f'{where} · {name}: запрет атрибута не подтверждён по модели схемы.')
                        continue
                    if not options:
                        self.record('Состав сообщения', where, field, 'Во фрагменте указан '+name+'.',
                                    'Среди дочерних полей '+field['name']+' такого имени нет.',
                                    'При использовании фрагмента DOCX может появиться недопустимое поле.')
                        continue
                    if len(matches) == 1:
                        child = matches[0]
                    elif qname is not None and not matches:
                        seen.update(map(id, options))
                        namespaces = sorted({child.get('ns') or 'без пространства имён' for child in options})
                        self.checked += 1
                        self.record('Пространство имён ссылки' if ref else 'Пространство имён объявления',
                                    where, options[0] if len(options) == 1 else field,
                                    f'{ref or name} ({qname[0] or "без пространства имён"})',
                                    'Объявления поля '+name+': '+', '.join(namespaces),
                                    'XML по цитате обращается к другому объявлению XSD.')
                        continue
                    elif len(options) > 1:
                        seen.update(map(id, options))
                        self.notes.append(f'{where} · {name}: не установлено однозначное объявление.')
                        continue
                    else:
                        child = options[0]
                        if not ref and qname is None and child.get('ns'):
                            self.notes.append(f'{where} · {name}: не установлено пространство имён локального объявления.')
                    seen.add(id(child))
                    if node.tag == 'attribute':
                        occurrence = '1' if node.get('use') == 'required' else '0..1'
                    else:
                        occurrence = node.get('minOccurs', '1')+'..'+node.get('maxOccurs', '1')
                    self.compare_field(child, where+' · '+name, node.get('type', ''), occurrence, declaration=node)
                    self.fragment(child, where, node)
                elif node.tag != 'annotation':
                    return False
            return True

        if visit(candidates) and not partial:
            for index in field['children']:
                child = self.fields[index]
                if id(child) not in seen:
                    name = child['name']
                    cardinality = f"Кратность {child['min']}..{child['max']}."
                    if child.get('group'):
                        cardinality += ' В контексте группы: '+child['group']+'.'
                    self.record('Состав сообщения', where, child, 'Во фрагменте схемы отсутствует '+name+'.',
                                f'Поле {name}. '+cardinality,
                                'Структура XML по фрагменту спецификации отличается от действующей XSD.')

    def simple_type(self, field, where, declaration):
        restriction = declaration.find('restriction')
        base = restriction.get('base', '') if restriction is not None else ''
        prefix, _, primitive = base.rpartition(':')
        actual_base = field.get('base', '')
        if (restriction is None or restriction.nsmap.get(prefix or None) != XS or primitive not in SCALAR_BASES
                or local_name(actual_base) not in SCALAR_BASES or field.get('alternatives')
                or field.get('baseNs', XS if actual_base.startswith(('xs:', 'xsd:')) else '') != XS):
            self.notes.append(f'Не проверена карточка «{where}»: конструкция simpleType или база {base or actual_base} не поддерживается.')
            return
        if self.archive_path is not None:
            # Identical flattened facets can have different value spaces when an
            # enumeration and whiteSpace belong to different restriction levels.
            schema = self.source_schema()
            source_type = schema.symbol.get((field.get('typeNs', ''), 'simpleType', local_name(field['type'])))
            rule = source_type.find(X+'restriction') if source_type is not None else None
            source_base = rule.get('base') if rule is not None else None
            if not source_base or schema.qname_namespace(source_base, rule) != XS:
                self.notes.append(f'Не проверена карточка «{where}»: исходная XSD использует вложенный или именованный базовый тип {source_base or ""}.')
                return
        quoted = defaultdict(list)
        for node in restriction:
            if node.tag == 'annotation':
                continue
            if node.tag not in FACETS or node.get('value') is None:
                self.notes.append(f'Не проверена карточка «{where}»: неподдерживаемое ограничение {node.tag}.')
                return
            quoted[node.tag].append(node.get('value'))
        actual = defaultdict(list)
        for key, value in field.get('facets', []):
            actual[key].append(value)
        for value, _ in field.get('values', []):
            actual['enumeration'].append(value)
        if (len(field.get('patternGroups', [])) > 1
                or any(len(values) > 1 for key, values in actual.items() if key not in ('pattern', 'enumeration'))):
            self.notes.append(f'Не проверена карточка «{where}»: присутствуют ограничения нескольких уровней наследования.')
            return
        differences = {key for key in actual.keys() | quoted.keys() if set(actual[key]) != set(quoted[key])}
        if primitive == local_name(actual_base) and not differences:
            self.checked += 1
            return
        try:
            validators = (scalar_validator(primitive, quoted), scalar_validator(local_name(actual_base), actual))
        except etree.XMLSchemaParseError:
            self.notes.append(f'Не проверена карточка «{where}»: отдельное объявление типа не компилируется.')
            return
        # Only report an incompatibility demonstrated in the value space of both types.
        # A differently written pattern or numeric boundary need not change the accepted XML.
        accepted = [[], []]
        probe = etree.Element('value')
        for value in scalar_samples(quoted, actual, differences):
            probe.text = value
            doc_ok, xsd_ok = (validator.validate(probe) for validator in validators)
            if doc_ok != xsd_ok:
                accepted[0 if doc_ok else 1].append(value)
        self.checked += 1
        if any(accepted):
            def describe(own, other):
                return ' '.join(action + ' «' + values[0] + '».' for action, values in
                                [('Допускается', own), ('Отклоняется', other)] if values)
            self.record('Перечень значений' if differences == {'enumeration'} else 'Ограничение значения',
                        where, field, describe(*accepted), describe(*reversed(accepted)),
                        'Проверка одного и того же значения по объявлению из документа и по XSD даёт разные результаты.')
            self.findings[-1]['rule_facets'] = sorted(differences)
        elif not (primitive == local_name(actual_base) and differences == {'enumeration'}
                  and quoted['enumeration'] and actual['enumeration']):
            self.notes.append(f'В карточке «{where}» объявления отличаются, на проверенных значениях расхождение не подтверждено.')

    def card(self, field, where, props):
        snippet = props['Схема']
        try:
            wrapper = etree.fromstring((f'<wrapper xmlns:xs="{XS}" xmlns:xsd="{XS}">'+snippet+'</wrapper>').encode(),
                                       etree.XMLParser(resolve_entities=False, no_network=True, remove_comments=True, remove_pis=True))
        except etree.XMLSyntaxError:
            return
        if len(wrapper) != 1:
            return
        declaration = wrapper[0]
        for node in declaration.iter():
            if node.tag.startswith(X):
                node.tag = node.tag[len(X):]
        if declaration.tag not in ('element', 'attribute', 'simpleType', 'complexType'):
            return
        name = declaration.get('name', '').strip()
        if declaration.tag in ('element', 'attribute'):
            if name != field['name'].lstrip('@'):
                self.checked += 1
                self.record('Имя элемента', where, field, 'Объявлено '+name+'.', 'Имя в XSD '+field['name']+'.',
                            'Схема ожидает другое имя XML.')
                return
            occurring = ''
            if field['parent'] >= 0 and declaration.tag == 'element':
                occurring = declaration.get('minOccurs', '1')+'..'+declaration.get('maxOccurs', '1')
            self.compare_field(field, where, declaration.get('type', ''), occurring, declaration=declaration)
        else:
            if local_name(field.get('type', '')).lower() != name.lower():
                return
            if declaration.tag == 'simpleType':
                self.simple_type(field, where, declaration)
                return
            self.checked += 1
        self.fragment(field, where, declaration)
        listed = props.get('Дочерние элементы', '').split()
        if listed and all(IDENTIFIER.fullmatch(item) for item in listed):
            expected = [local_name(item) for item in listed]
            actual = [self.fields[i]['name'] for i in field['children'] if self.fields[i]['kind'] == 'element']
            if not child_order_matches(field, expected, actual):
                self.record('Состав сообщения', where, field, 'Дочерние элементы: '+', '.join(expected)+'.',
                            'Дочерние элементы: '+(', '.join(actual) or 'отсутствуют')+'.',
                            'Перечень в таблице отличается от структуры XML.')
                self.findings[-1]['rule_children'] = [name for name in actual if name not in expected] or actual


def audit(revision, body, fields, *, notes=None, archive_path=None):
    """Return number of compared declarations and findings for one environment-bound edition."""
    if not fields:
        return 0, []
    comparison = Comparison(revision, fields, archive_path=archive_path)
    tables = prepare(body) if isinstance(body, ET.Element) else body
    for number, caption, context, rows, props in tables:
        for parent in comparison.targets(number, caption, context, props):
            if rows is not None:
                comparison.flat(parent, caption, rows)
            else:
                comparison.card(parent, caption, props)
    if notes is not None:
        notes.extend(comparison.notes)
    return comparison.checked, comparison.findings
