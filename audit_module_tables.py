"""Compare a module's numbered Word sections and shared type tables with its bound XSD tree."""

import posixpath
import re
from collections import defaultdict
from xml.etree import ElementTree as ET

from module_xsd import W, word_text


MESSAGE = re.compile(r'^Тип сообщения\s+(?:СЭДО\s*)?(?:№\s*)?(\d+)\s*[:—–-]', re.I)
APPENDIX = re.compile(r'^(?:\d+\s+)?(?:Приложение\s*\d|Атрибуты\s+типов$|Атрибутивный/элементный)', re.I)
TARGET = re.compile(r'Атрибуты\s+(?:(?:комплексного|простого|сложного)\s+)?(типа|элемента|атрибута|свойства)\s+([\w:./-]+)', re.I)
IDENTIFIER = re.compile(r'@?(?:[A-Za-z_][\w.-]*:)?[A-Za-z_][\w.-]*\Z')


def local_name(value):
    return value.rsplit(':', 1)[-1]


def clean(node):
    return ' '.join(word_text(node).split())


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
            rows = [[clean(c) for c in tr.findall(W+'tc')] for tr in block.findall(W+'tr')]
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
    def __init__(self, revision, fields):
        self.revision = revision
        self.fields = fields
        self.by_type = defaultdict(list)
        self.by_name = defaultdict(list)
        self.checked = 0
        self.findings = []
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
        self.findings.append(dict(kind=kind, location=where, spec=spec, xlocation=filename+' · '+field['path'],
                                  xsd=xsd, impact=impact))

    def targets(self, number, title, context, props):
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
            # The field export has no namespace for a named type distinct from its declaration.
            if props.get('Пространство имен'):
                matches = [f for f in matches if f.get('ns') == props['Пространство имен']
                           and f.get('typeFile', f.get('file')) == f.get('file')]
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
        definitions = {(f.get('ns'), f.get('typeFile'), f.get('file'), f.get('type'),
                        tuple(self.fields[k]['name'] for k in f['children'])) for f in matches}
        return matches[:1] if len(definitions) == 1 else []

    def compare_field(self, field, where, typ, occurrence):
        self.checked += 1
        actual = field.get('type', '')
        # Anonymous restrictions retain their primitive base but the flattened tree does
        # not retain intermediate named bases. They cannot prove a named type mismatch.
        if not actual and typ.startswith(('xs:', 'xsd:')):
            actual = field.get('base', '')
        if typ and IDENTIFIER.fullmatch(typ) and actual:
            types = {local_name(t) for t in (actual, field.get('base', ''))}
            if local_name(typ).lower() not in {t.lower() for t in types}:
                self.record('Тип данных', where, field, 'Тип '+typ+'.', 'Тип '+actual+'.',
                            'Генерация XML по таблице может задать другой формат значения.')
        count = re.fullmatch(r'(\d+)(?:\s*(?:\.\.|[-–…])\s*(\d+|∞|unbounded))?', occurrence)
        if count and not field.get('group'):
            low, high = count[1], (count[2] or count[1]).replace('∞', 'unbounded')
            if (low, high) != (field['min'], field['max']):
                self.record('Кратность', where, field, f'Кратность {low}..{high}.',
                            f"Кратность {field['min']}..{field['max']}.",
                            'Таблица и проверка по схеме допускают разное число вхождений.')

    def limits(self, field, where, limit, description):
        facets = defaultdict(list)
        for key, val in field.get('facets', []):
            facets[key].append(val)
        annotation = field.get('doc', '')
        if (local_name(field.get('base', '')) in ('date', 'dateTime')
                and 'крайняя дата' in description.lower() and 'крайняя дата' in annotation.lower()
                and 'подтверждения прочтения' in description.lower() and 'выгрузки' in annotation.lower()
                and 'подтверждения прочтения' not in annotation.lower()):
            self.record('Описание поля', where, field, description, annotation,
                        'Выгрузка документа и получение подтверждения прочтения обозначают разные события.')
        length = re.search(r'(макс\.?\s*|мин\.?\s*)?длинн?а\s*:?\s*(\d+)', limit, re.I)
        if length or (limit.isdigit() and local_name(field.get('base', '')) == 'string'):
            if length:
                qualifier = (length[1] or '').lower().strip()
                facet = 'maxLength' if qualifier.startswith('макс') else 'minLength' if qualifier else 'length'
                value = length[2]
            else:
                facet, value = 'maxLength', limit
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
            if local_name(field.get('base', '')) == 'decimal' and not (facets['minInclusive'] or facets['minExclusive']):
                self.record('Диапазон значения', where, field, limit, 'Тип decimal без нижней границы.',
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
        children = {self.fields[i]['name'].lstrip('@'): self.fields[i] for i in parent['children']}
        seen = set()
        for row in rows:
            if len(row) < 4 or not IDENTIFIER.fullmatch(row[0]):
                continue
            name = local_name(row[0]).lstrip('@')
            location = where+' · '+row[0]
            candidates = dict(children)
            if parent['parent'] < 0:
                candidates[parent['name']] = parent
            matched = name if name in candidates else nearby(name, candidates)
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
            if matched != name:
                self.record('Имя элемента', location, field, 'В таблице '+name+'.', 'Имя в XSD '+matched+'.',
                            'XML чувствителен к регистру и точному написанию имени.')
            self.compare_field(field, location, row[1], row[3])
            self.limits(field, location, row[2], row[4] if len(row) > 4 else '')
        if seen:
            for name, field in children.items():
                if name not in seen and field['min'] != '0' and not field.get('group'):
                    self.record('Состав сообщения', where, field,
                                'В таблице отсутствует '+field['name']+'.',
                                f"Обязательное поле {field['name']}, кратность {field['min']}..{field['max']}.",
                                'Сообщение без поля не пройдёт проверку по XSD.')

    def fragment(self, field, where, declaration):
        """A fully inlined complex type can be compared within its enclosing field."""
        content = declaration if declaration.tag == 'complexType' else declaration.find('complexType')
        if content is None:
            return
        # The DOCX fragment does not supply inherited group or extension declarations.
        # Comparing such fragments to the expanded tree would mark inherited fields missing.
        if any(child.tag in ('complexContent', 'simpleContent', 'group', 'attributeGroup', 'any')
               for child in content):
            return
        children = {self.fields[i]['name']: self.fields[i] for i in field['children']}
        seen = set()

        def visit(group):
            for node in group:
                if node.tag in ('sequence', 'choice', 'all'):
                    if not visit(node):
                        return False
                elif node.tag in ('element', 'attribute'):
                    name = node.get('name') or local_name(node.get('ref', ''))
                    name = ('@' if node.tag == 'attribute' else '')+name.strip()
                    seen.add(name)
                    child = children.get(name)
                    if child is None:
                        self.record('Состав сообщения', where, field, 'Во фрагменте указан '+name+'.',
                                    'Среди дочерних полей '+field['name']+' такого имени нет.',
                                    'При использовании фрагмента DOCX может появиться недопустимое поле.')
                        continue
                    if node.tag == 'attribute':
                        occurrence = '1' if node.get('use') == 'required' else '0..1'
                    else:
                        occurrence = node.get('minOccurs', '1')+'..'+node.get('maxOccurs', '1')
                    self.compare_field(child, where+' · '+name, node.get('type', ''), occurrence)
                    self.fragment(child, where, node)
                elif node.tag != 'annotation':
                    return False
            return True

        if visit(content):
            for name, child in children.items():
                if name not in seen:
                    cardinality = f"Кратность {child['min']}..{child['max']}."
                    if child.get('group'):
                        cardinality += ' В контексте группы: '+child['group']+'.'
                    self.record('Состав сообщения', where, child, 'Во фрагменте схемы отсутствует '+name+'.',
                                f'Поле {name}. '+cardinality,
                                'Структура XML по фрагменту спецификации отличается от действующей XSD.')

    def card(self, field, where, props):
        snippet = re.sub(r'(<\/?)(?:xs|xsd):', r'\1', props['Схема'])
        try:
            declaration = ET.fromstring(snippet)
        except ET.ParseError:
            return
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
            self.compare_field(field, where, declaration.get('type', ''), occurring)
        else:
            if local_name(field.get('type', '')).lower() != name.lower():
                return
            self.checked += 1
        self.fragment(field, where, declaration)
        listed = props.get('Дочерние элементы', '').split()
        if listed and all(IDENTIFIER.fullmatch(item) for item in listed):
            expected = [local_name(item) for item in listed]
            actual = [self.fields[i]['name'] for i in field['children'] if self.fields[i]['kind'] == 'element']
            if set(expected) != set(actual):
                self.record('Состав сообщения', where, field, 'Дочерние элементы: '+', '.join(expected)+'.',
                            'Дочерние элементы: '+(', '.join(actual) or 'отсутствуют')+'.',
                            'Перечень в таблице отличается от структуры XML.')


def audit(revision, body, fields):
    """Return number of compared declarations and findings for one environment-bound edition."""
    if not fields:
        return 0, []
    comparison = Comparison(revision, fields)
    tables = prepare(body) if isinstance(body, ET.Element) else body
    for number, caption, context, rows, props in tables:
        for parent in comparison.targets(number, caption, context, props):
            if rows is not None:
                comparison.flat(parent, caption, rows)
            else:
                comparison.card(parent, caption, props)
    return comparison.checked, comparison.findings
