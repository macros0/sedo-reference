import io
import json
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from audit_module_tables import audit, prepare, Comparison
from module_xsd import W
from schema import Schema, XS


ROOT = Path(__file__).resolve().parents[1]
HEADER = ['Элемент/Атрибут', 'Тип', 'Ограничения', 'Количество вхождений', 'Описание']


def make_body(*blocks):
    body = ET.Element(W + 'body')
    for block in blocks:
        if isinstance(block, str):
            ET.SubElement(ET.SubElement(body, W + 'p'), W + 't').text = block
        else:
            table = ET.SubElement(body, W + 'tbl')
            for values in block:
                row = ET.SubElement(table, W + 'tr')
                for value in values:
                    cell = ET.SubElement(row, W + 'tc')
                    ET.SubElement(ET.SubElement(cell, W + 'p'), W + 't').text = value
    return body


def field(name, path, parent, typ, children=()):
    return dict(name=name, path=path, parent=parent, children=children, type=typ, base=typ,
                kind='element', min='1', max='1', group='', ns='urn:test', file='Example.xsd',
                facets=[], flags={}, values=[])


class ModuleAuditTests(unittest.TestCase):
    def test_schema_fragment_resolves_element_and_attribute_reference_namespaces(self):
        from tempfile import TemporaryDirectory
        from schema import Schema, XS

        fragment = ('<xs:element xmlns:a="urn:a" xmlns:b="urn:b" name="request">'
                    '<xs:complexType><xs:sequence><xs:element ref="a:item"/></xs:sequence>'
                    '<xs:attribute ref="a:flag"/></xs:complexType></xs:element>')
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'schema.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('main.xsd', f'<xs:schema xmlns:xs="{XS}">'
                                             '<xs:import namespace="urn:a" schemaLocation="a.xsd"/>'
                                             '<xs:import namespace="urn:b" schemaLocation="b.xsd"/>'
                                             + fragment + '</xs:schema>')
                for prefix in ('a', 'b'):
                    archive.writestr(prefix+'.xsd', f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:{prefix}">'
                                     '<xs:element name="item" type="xs:boolean" fixed="true"/>'
                                     '<xs:attribute name="flag" type="xs:boolean" fixed="true"/></xs:schema>')
            with zipfile.ZipFile(path) as archive:
                tree=Schema(archive,'main.xsd','request')
                tree.field(tree.root)
            revision=dict(number='1', root='request', schema_path='main.xsd')
            def check(quote):
                notes=[]
                _, findings=audit(revision, make_body('Тип сообщения 1: пример', [['Схема', quote]]),
                                  tree.fields, notes=notes, archive_path=path)
                return findings, notes

            alias=fragment.replace('xmlns:a', 'xmlns:q').replace('ref="a:', 'ref="q:')
            self.assertEqual(check(alias), ([], []))
            findings, notes=check(fragment.replace('ref="a:', 'ref="b:'))
            self.assertEqual({r['xlocation'].split(' · ')[-1] for r in findings}, {'request/item', 'request/@flag'})
            self.assertTrue(all('urn:b' in r['spec'] and 'urn:a' in r['xsd'] for r in findings))
            self.assertFalse(notes)
            findings, notes=check(fragment.replace('ref="a:', 'ref="unknown:'))
            self.assertFalse(findings)
            self.assertTrue(any('unknown:item' in note for note in notes))
            self.assertTrue(any('unknown:flag' in note for note in notes))
            local = fragment.replace('<xs:element ref="a:item"/>',
                                     '<xs:element name="item" type="xs:boolean" fixed="true"/>')
            findings, notes = check(local)
            self.assertEqual(len(findings), 1, findings)
            self.assertEqual(findings[0]['kind'], 'Пространство имён объявления')
            self.assertIn('urn:a', findings[0]['xsd'])
            self.assertFalse(notes)
            forbidden = fragment.replace('ref="a:flag"/>', 'ref="a:flag" use="prohibited"/>')
            findings, notes = check(forbidden)
            self.assertEqual(len(findings), 1, findings)
            self.assertEqual(findings[0]['kind'], 'Запрет атрибута')
            self.assertFalse(notes)

            both = fragment.replace('<xs:element ref="a:item"/>',
                                    '<xs:element ref="a:item"/><xs:element ref="b:item"/>')
            both = both.replace('<xs:attribute ref="a:flag"/>',
                                '<xs:attribute ref="a:flag"/><xs:attribute ref="b:flag"/>')
            with zipfile.ZipFile(path) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            files['main.xsd'] = files['main.xsd'].replace(fragment.encode(), both.encode())
            with zipfile.ZipFile(path, 'w') as archive:
                for name, raw in files.items():
                    archive.writestr(name, raw)
            with zipfile.ZipFile(path) as archive:
                tree = Schema(archive, 'main.xsd', 'request')
                tree.field(tree.root)
            self.assertEqual(check(both), ([], []))
            pair = '<xs:element ref="a:item"/><xs:element ref="b:item"/>'
            reordered = both.replace(pair, '<xs:element ref="b:item"/><xs:element ref="a:item"/>')
            findings, notes = check(reordered)
            self.assertEqual(len(findings), 1, findings)
            self.assertEqual(findings[0]['kind'], 'Модель содержимого')
            self.assertIn('{urn:b}item', findings[0]['spec'])
            self.assertFalse(notes)

            self.assertEqual(check(both.replace('xmlns:a', 'xmlns:q').replace('ref="a:', 'ref="q:')), ([], []))
            choice = both.replace('xs:sequence', 'xs:choice')
            with zipfile.ZipFile(path, 'w') as archive:
                files['main.xsd'] = files['main.xsd'].replace(both.encode(), choice.encode())
                for name, raw in files.items():
                    archive.writestr(name, raw)
            with zipfile.ZipFile(path) as archive:
                tree = Schema(archive, 'main.xsd', 'request')
                tree.field(tree.root)
            self.assertEqual(check(reordered.replace('xs:sequence', 'xs:choice')), ([], []))

    def check_schema_card(self, source, quote, *, notes=None):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('main.xsd', f'<xs:schema xmlns:xs="{XS}">{source}</xs:schema>')
        with zipfile.ZipFile(archive) as bundle:
            schema = Schema(bundle, 'main.xsd', 'request')
            schema.field(schema.root)
        body = make_body('Тип сообщения 55: запрос', [['Схема', quote]])
        revision = dict(number='55', root='request', schema_path='main.xsd')
        return audit(revision, body, schema.fields, notes=notes, archive_path=archive)

    def test_prohibited_local_attribute_matches_absence_without_hiding_required_use(self):
        def element(use):
            return (f'<xs:element name="request"><xs:complexType>'
                    f'<xs:attribute name="flag" type="xs:boolean" use="{use}"/>'
                    '</xs:complexType></xs:element>')

        for use in ('optional', 'required'):
            with self.subTest(use=use):
                count, findings = self.check_schema_card(element(use), element('prohibited'))
                self.assertEqual(count, 2)
                self.assertEqual([finding['kind'] for finding in findings], ['Запрет атрибута'])
        count, findings = self.check_schema_card(element('prohibited'), element('prohibited'))
        self.assertEqual((count, findings), (1, []))
        _, findings = self.check_schema_card(element('required'), '<xs:element name="request"><xs:complexType/></xs:element>')
        self.assertEqual([finding['kind'] for finding in findings], ['Состав сообщения'])
        self.assertIn('@flag', findings[0]['xlocation'])
        notes = []
        quote = ('<xs:element name="request"><xs:complexType><xs:complexContent><xs:restriction base="UnknownBase">'
                 '<xs:attributeGroup ref="UnknownGroup"/></xs:restriction></xs:complexContent></xs:complexType></xs:element>')
        _, findings = self.check_schema_card(element('optional'), quote, notes=notes)
        self.assertEqual(findings, [])
        self.assertTrue(any('Наследуемые атрибуты' in note for note in notes))

    def test_standalone_schema_card_compares_declaration_properties(self):
        def element(flags):
            return f'<xs:element name="request" type="xs:boolean"{flags}/>'

        for key, source, quote in [('fixed', 'true', 'false'), ('default', 'false', 'true'),
                                   ('nillable', 'true', 'false'), ('fixed', 'true', None),
                                   ('default', None, 'true'), ('nillable', None, 'true')]:
            with self.subTest(key=key, source=source, quote=quote):
                actual = element('' if source is None else f' {key}="{source}"')
                documented = element('' if quote is None else f' {key}="{quote}"')
                count, findings = self.check_schema_card(actual, documented)
                self.assertEqual((count, len(findings)), (1, 1), findings)
                self.assertEqual(findings[0]['xlocation'], 'main.xsd · request')
                self.assertTrue(findings[0]['spec'].startswith(key+'='))
                self.assertTrue(findings[0]['xsd'].startswith(key+'='))

    def test_nested_element_and_attribute_link_to_their_own_properties(self):
        source = ('<xs:element name="request"><xs:complexType><xs:sequence>'
                  '<xs:element name="child" type="xs:boolean" fixed="true" nillable="true"/>'
                  '</xs:sequence><xs:attribute name="flag" type="xs:boolean" default="true"/>'
                  '</xs:complexType></xs:element>')
        quote = source.replace('="true"', '="false"')
        count, findings = self.check_schema_card(source, quote)
        self.assertEqual(count, 3)
        self.assertEqual({(finding['spec'], finding['xsd'], finding['xlocation']) for finding in findings}, {
            ('fixed=false', 'fixed=true', 'main.xsd · request/child'),
            ('nillable=false', 'nillable=true', 'main.xsd · request/child'),
            ('default=false', 'default=true', 'main.xsd · request/@flag'),
        })

    def test_declaration_properties_compare_typed_values(self):
        for primitive, key, actual, documented in [
                ('boolean', 'fixed', 'true', '1'), ('boolean', 'default', 'false', '0'),
                ('boolean', 'nillable', 'true', '1'), ('boolean', 'nillable', 'false', '0'),
                ('boolean', 'nillable', 'false', None), ('decimal', 'fixed', '1.0', '1.00'),
                ('time', 'fixed', '00:30:00.12345678901Z', '23:30:00.123456789010-01:00')]:
            with self.subTest(primitive=primitive, key=key, documented=documented):
                stem = f'<xs:element name="request" type="xs:{primitive}"'
                source = stem+f' {key}="{actual}"/>'
                quote = stem+('/>' if documented is None else f' {key}="{documented}"/>')
                self.assertEqual(self.check_schema_card(source, quote), (1, []))

    def test_fixed_time_retains_zone_presence_and_exact_fraction(self):
        cases = [('00:30:00Z', '00:30:00'),
                 ('00:30:00.12345678901Z', '23:30:00.12345678902-01:00')]
        template = '<xs:element name="request" type="xs:time" fixed="{}"/>'
        for actual, documented in cases:
            with self.subTest(actual=actual, documented=documented):
                count, findings = self.check_schema_card(template.format(actual), template.format(documented))
                self.assertEqual((count, len(findings)), (1, 1))
                self.assertEqual((findings[0]['spec'], findings[0]['xsd']), (f'fixed={documented}', f'fixed={actual}'))

    def test_fixed_qname_uses_local_declaration_contexts(self):
        source = '<xs:element name="request" type="xs:QName" xmlns:p="urn:actual" fixed="p:code"/>'
        quote = source.replace('urn:actual', 'urn:quoted')
        count, findings = self.check_schema_card(source, quote)
        self.assertEqual((count, len(findings)), (1, 1))
        self.assertIn('urn:quoted', findings[0]['spec'])
        self.assertIn('urn:actual', findings[0]['xsd'])
        self.assertEqual(self.check_schema_card(source, source.replace('p:', 'q:').replace('xmlns:p', 'xmlns:q')), (1, []))
        notes = []
        self.assertEqual(self.check_schema_card(source, source.replace(' xmlns:p="urn:actual"', ''), notes=notes), (1, []))
        self.assertTrue(any('QName' in note for note in notes), notes)

    def test_union_constraints_compare_the_declared_member_and_expanded_name(self):
        for key in ('fixed', 'default'):
            source = (f'<xs:element name="request"><xs:complexType><xs:attribute name="code" xmlns:p="urn:one" {key}="p:value">'
                      '<xs:simpleType><xs:union memberTypes="xs:QName xs:integer"/></xs:simpleType>'
                      '</xs:attribute></xs:complexType></xs:element>')
            for quote, differs, limited in [
                    (source.replace('urn:one', 'urn:two'), True, False),
                    (source.replace('xmlns:p', 'xmlns:q').replace('p:value', 'q:value'), False, False),
                    (source.replace(' xmlns:p="urn:one"', ''), False, True),
                    (source.replace('xs:QName', 'xs:string'), True, False)]:
                with self.subTest(key=key, quote=quote):
                    notes = []
                    count, issues = self.check_schema_card(source, quote, notes=notes)
                    self.assertEqual(count, 2)
                    self.assertEqual(bool(issues), differs, issues)
                    self.assertEqual(any('union' in message for message in notes), limited, notes)
                    if differs:
                        self.assertIn('urn:one', issues[0]['xsd'])

    def test_referenced_attribute_compares_local_override_to_inherited_default(self):
        global_flag = '<xs:attribute name="flag" type="xs:boolean" default="true"/>'
        template = ('<xs:element name="request"><xs:complexType>'
                    '<xs:attribute ref="flag"{}/></xs:complexType></xs:element>')
        quote = template.format('')
        self.assertEqual(self.check_schema_card(global_flag+quote, quote), (2, []))
        source = global_flag+template.format(' default="false"')
        count, findings = self.check_schema_card(source, quote)
        self.assertEqual((count, len(findings)), (2, 1))
        self.assertEqual(findings[0]['xlocation'], 'main.xsd · request/@flag')
        self.assertEqual((findings[0]['spec'], findings[0]['xsd']), ('default=true', 'default=false'))

    def test_complete_choice_fragment_checks_local_leaf_occurrences(self):
        import io
        from schema import Schema, XS
        source = '<xs:element name="request"><xs:complexType><xs:choice><xs:element name="a" type="xs:string" maxOccurs="2"/><xs:element name="b" type="xs:string"/></xs:choice></xs:complexType></xs:element>'
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            archive.writestr('main.xsd',f'<xs:schema xmlns:xs="{XS}">{source}</xs:schema>')
        with zipfile.ZipFile(stream) as archive:
            schema=Schema(archive,'main.xsd','request');schema.field(schema.root)
        for maximum, count in [('1',1),(' 1 ',1),('2',0),(' 02 ',0)]:
            comparison=Comparison(dict(schema_path='main.xsd',root='request'),schema.fields)
            comparison.card(schema.fields[0],'Request',{'Схема':source.replace('maxOccurs="2"',f'maxOccurs="{maximum}"')})
            self.assertEqual(len(comparison.findings),count,comparison.findings)
            card=Comparison(dict(schema_path='main.xsd',root='request'),schema.fields)
            card.card(schema.fields[1],'request/a',{'Схема':f'<xs:element name="a" type="xs:string" maxOccurs="{maximum}"/>'})
            self.assertEqual(len(card.findings),count,card.findings)
        comparison=Comparison(dict(schema_path='main.xsd',root='request'),schema.fields)
        comparison.compare_field(schema.fields[1],'flat','xs:string','1')
        self.assertEqual(comparison.findings,[])

    def test_fragment_preserves_compositor_order_and_group_occurrences(self):
        import io
        from schema import Schema, XS
        template = '<xs:element name="request"><xs:complexType>{}</xs:complexType></xs:element>'
        for compositor, nested, quote_kind, mismatch in [
                ('all', False, 'reverse', False), ('sequence', False, 'reverse', True),
                ('sequence', True, 'reverse', True), ('sequence', True, 'bounds', True),
                ('sequence', True, 'same', False)]:
            group = '<xs:element name="a" type="xs:string"/><xs:element name="b" type="xs:string"/>'
            if nested: group = '<xs:sequence minOccurs="0">'+group+'</xs:sequence>'
            source = template.format(f'<xs:{compositor}>{group}</xs:{compositor}>')
            quote = source
            if quote_kind == 'reverse': quote = quote.replace('name="a"','name="TEMP"').replace('name="b"','name="a"').replace('name="TEMP"','name="b"')
            if quote_kind == 'bounds': quote = quote.replace('minOccurs="0"','minOccurs="1"')
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('main.xsd', f'<xs:schema xmlns:xs="{XS}">{source}</xs:schema>')
            with zipfile.ZipFile(stream) as archive:
                schema = Schema(archive, 'main.xsd', 'request'); schema.field(schema.root)
            comparison = Comparison(dict(schema_path='main.xsd', root='request'), schema.fields)
            comparison.card(schema.fields[0], 'Таблица модели request', {'Схема':quote})
            self.assertEqual(bool(comparison.findings), mismatch, (compositor, quote_kind, comparison.findings))
            if mismatch:
                self.assertTrue(any(item['kind'] == 'Модель содержимого' for item in comparison.findings), comparison.findings)

    def test_child_group_does_not_hide_parent_sequence_difference(self):
        import io
        from schema import Schema, XS
        source = '<xs:element name="request"><xs:complexType><xs:sequence><xs:element name="a"><xs:complexType><xs:group ref="Child"/></xs:complexType></xs:element><xs:element name="b" type="xs:string"/></xs:sequence></xs:complexType></xs:element>'
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('main.xsd', f'<xs:schema xmlns:xs="{XS}"><xs:group name="Child"><xs:sequence><xs:element name="x" type="xs:string"/></xs:sequence></xs:group>{source}</xs:schema>')
        with zipfile.ZipFile(stream) as archive:
            schema = Schema(archive, 'main.xsd', 'request')
            schema.field(schema.root)
        quote = source.replace('<xs:sequence><xs:element name="a">', '<xs:sequence><xs:element name="b" type="xs:string"/><xs:element name="a">').replace('</xs:element><xs:element name="b" type="xs:string"/>', '</xs:element>')
        comparison = Comparison(dict(schema_path='main.xsd', root='request'), schema.fields)
        comparison.card(schema.fields[0], 'Таблица request', {'Схема': quote})
        self.assertTrue(any(item['kind'] == 'Модель содержимого' for item in comparison.findings), comparison.findings)

    def test_fragment_qname_uses_its_own_namespace_scope(self):
        fields = [field('request', 'request', -1, 'a:Code', [1]),
                  field('child','request/child',0,'a:Code')]
        for item in fields: item['typeNs'] = 'urn:A'
        for binding, count in [('xmlns:b="urn:B"',2), ('xmlns:b="urn:A"',0), ('',0)]:
            comparison = Comparison(dict(schema_path='main.xsd'), fields)
            for snippet in [f'<xs:element {binding} name="request" type="b:Code"/>',
                            f'<xs:element {binding} name="request"><xs:complexType><xs:sequence>'
                            '<xs:element name="child" type="b:Code"/></xs:sequence></xs:complexType></xs:element>']:
                comparison.card(fields[0], 'Карточка', {'Схема':snippet})
            differences = [item for item in comparison.findings if item['kind']=='Тип данных']
            self.assertEqual(len(differences), count, binding)
            if count:self.assertIn('urn:B', differences[0]['spec'])

    def test_unmarked_attribute_name_matches_its_only_declaration(self):
        fields = [field('root', 'root', -1, 'Root', [1]),
                  field('@responseOn', 'root/@responseOn', 0, 'xs:string')]
        fields[1]['kind'] = 'attribute'
        comparison = Comparison(dict(schema_path='main.xsd'), fields)
        comparison.flat(fields[0], 'Table', [['responseOn', 'xs:string', '', '1']])
        self.assertEqual(comparison.checked, 1)
        self.assertEqual(comparison.findings, [])

    def test_exact_length_implied_by_both_bounds(self):
        table = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                          [HEADER, ['code', 'Code', 'Длина: 5', '1', '']])
        for low, mismatch in [('5', False), ('4', True)]:
            self.fields[1].update(type='Code', base='xs:string', facets=[['minLength', low], ['maxLength', '5']])
            checked, findings = audit(self.revision, table, self.fields)
            self.assertEqual((checked, bool(findings)), (1, mismatch))

    def test_nonnegative_claim_respects_fixed_enumeration_and_pattern(self):
        table = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                          [HEADER, ['code', 'Code', 'Неотрицательное число', '1', '']])
        for values, flags, facets, mismatch in [
                (['1', '2'], {}, [], False), (['-7', '2'], {}, [], True),
                ([], {'fixed': '2'}, [], False), ([], {'fixed': '-8'}, [], True),
                ([], {}, [('pattern', '[0-9]+')], False),
                (['-7', '2'], {}, [('pattern', '[0-9]+')], False)]:
            self.fields[1].update(type='Code', base='xs:integer', values=[[v, ''] for v in values], flags=flags, facets=facets)
            notes = []
            checked, findings = audit(self.revision, table, self.fields, notes=notes)
            self.assertEqual((checked, bool(findings)), (1, mismatch), (values, flags, facets))
            if not values and not flags and not mismatch:
                self.assertTrue(notes)

    def test_archived_109_enumeration_keeps_significant_spaces_and_links_its_xsd(self):
        from schema_review import compare_revision
        editions = {r['revision_id']: r for r in json.loads((ROOT/'data/catalog.json').read_bytes())['revisions']}
        for environment, rid in [
            ('prod', 'e07b8127f69f14dd3bf63af993f8977d1f385cf19d6499973ba3831e0a1547c5'),
            ('test', '12b54a5efe1749ced4068b77453c67da948ae34394166eadaa4ce090e640f1de')]:
            with self.subTest(environment=environment):
                revision = editions[rid]
                self.assertEqual(revision['environment'], environment)
                fields = json.loads((ROOT/'data/fields'/(rid+'.json')).read_bytes())['fields']
                review = compare_revision(revision, fields, ROOT/'state/objects')
                matches = [f for f in review['findings'] if 'InsuredCategoriesType' in f['location']
                           and ' ENDOFMILITARY ' in f['spec']]
                self.assertEqual(len(matches), 1)
                self.assertIn('ENDOFMILITARY', matches[0]['xsd'])
                self.assertEqual(matches[0]['field_paths'], ['eventLife/event/category'])
                self.assertTrue(matches[0]['xsd_source']['path'].endswith('/dic/InsuredCategories.xsd'))

    def test_simple_restrictions_compare_values_under_their_xsd_whitespace_rules(self):
        for base, facets, doc_value, schema_value, mismatch in [
            ('string', [], 'a  b', 'a b', True),
            # Enumeration literals belong to the base's value space. A collapse
            # facet at this same restriction level applies to the instance.
            ('string', [('whiteSpace', 'collapse')], 'a  b', 'a b', True),
            ('token', [], '  a b ', 'a b', False),
            ('normalizedString', [], 'a  b', 'a b', True),
            ('integer', [], ' 01 ', '1', False),
        ]:
            with self.subTest(base=base, facets=facets):
                restriction = ''.join(f'<xs:{k} value="{v}"/>' for k, v in facets)
                source = (f'<xs:simpleType name="Code"><xs:restriction base="xs:{base}">'
                          f'{restriction}<xs:enumeration value="{doc_value}"/></xs:restriction></xs:simpleType>')
                body = make_body('Приложение 1', 'Атрибуты типа Code', [['Схема', source]])
                self.fields[1].update(type='Code', base='xs:'+base, facets=facets, values=[[schema_value, '']])
                checked, findings = audit(self.revision, body, self.fields)
                self.assertEqual(checked, 1)
                self.assertEqual(bool(findings), mismatch)

    def test_simple_type_compares_length_and_marks_unresolved_base_as_unchecked(self):
        self.fields[1].update(type='Code', base='xs:string', facets=[['maxLength', '4']])
        def check(base):
            snippet = f'<xs:simpleType name="Code"><xs:restriction base="{base}"><xs:maxLength value="3"/></xs:restriction></xs:simpleType>'
            return make_body('Приложение 1', 'Атрибуты типа Code', [['Схема', snippet]])
        checked, findings = audit(self.revision, check('xs:string'), self.fields)
        self.assertEqual((checked, len(findings)), (1, 1))
        notes = []
        self.assertEqual(audit(self.revision, check('unknown:Text'), self.fields, notes=notes), (0, []))
        self.assertTrue(any('Code' in note and 'unknown:Text' in note for note in notes))

    def test_simple_type_detects_added_and_removed_enumerations(self):
        for quoted_enum, actual_enum in [('', [['x', '']]), ('<xs:enumeration value="x"/>', [])]:
            with self.subTest(quoted=quoted_enum):
                snippet = '<xs:simpleType name="Code"><xs:restriction base="xs:string">'+quoted_enum+'</xs:restriction></xs:simpleType>'
                body = make_body('Приложение 1', 'Атрибуты типа Code', [['Схема', snippet]])
                self.fields[1].update(type='Code', base='xs:string', values=actual_enum)
                self.assertEqual(len(audit(self.revision, body, self.fields)[1]), 1)

    def test_length_labels_distinguish_max_min_and_exact(self):
        for facet, labels in [('maxLength', ['Максимальная длина', 'макс. длина', 'макс длина']),
                              ('minLength', ['Минимальная длина', 'мин. длина', 'мин длина']),
                              ('length', ['Точная длина', 'Точн. длина', 'Длина'])]:
            for label in labels:
                with self.subTest(label=label):
                    table = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                                      [HEADER, ['code', 'Code', label+': 30', '1', '']])
                    self.fields[1].update(type='Code', base='xs:string', facets=[[facet, '30']])
                    self.assertEqual(audit(self.revision, table, self.fields)[1], [])
                    self.fields[1]['facets'] = [[facet, '20']]
                    self.assertIn(facet+'=30', audit(self.revision, table, self.fields)[1][0]['spec'])

    def test_real_word_cells_joined_without_whitespace_keep_the_length_qualifier(self):
        for limit, facet in [('stringМакс.длинна: 4', 'maxLength'), ('StringДлина 4Паттерн: [0-9]*', 'length')]:
            with self.subTest(limit=limit):
                self.fields[1].update(type='Code', base='xs:string', facets=[[facet, '3']])
                table = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                                  [HEADER, ['code', 'Code', limit, '1', '']])
                findings = audit(self.revision, table, self.fields)[1]
                self.assertTrue(any(facet+'=4' in f['spec'] for f in findings))

    def test_revision_records_coverage_when_the_source_enumeration_inherits_whitespace(self):
        import tempfile
        from schema import Schema, XS
        from schema_review import compare_revision
        source = (f'<xs:schema xmlns:xs="{XS}"><xs:simpleType name="Collapsed">'
                  '<xs:restriction base="xs:string"><xs:whiteSpace value="collapse"/></xs:restriction></xs:simpleType>'
                  '<xs:simpleType name="Code"><xs:restriction base="Collapsed"><xs:enumeration value="a  b"/>'
                  '</xs:restriction></xs:simpleType><xs:element name="notice" type="Code"/></xs:schema>')
        snippet = '<xs:simpleType name="Code"><xs:restriction base="xs:token"><xs:enumeration value="a b"/></xs:restriction></xs:simpleType>'
        body = make_body('Приложение 1', 'Атрибуты типа Code', [['Схема', snippet]])
        document = ET.Element(W+'document')
        document.append(body)
        with tempfile.TemporaryDirectory() as temporary:
            objects = Path(temporary)
            with zipfile.ZipFile(objects/'doc', 'w') as archive:
                archive.writestr('word/document.xml', ET.tostring(document))
            with zipfile.ZipFile(objects/'xsd', 'w') as archive:
                archive.writestr('main.xsd', source)
            with zipfile.ZipFile(objects/'xsd') as archive:
                tree = Schema(archive, 'main.xsd', 'notice')
                tree.field(tree.root)
            revision = dict(self.revision, status='matched', source_type='module', archive_sha256='xsd',
                            document_sha256='doc', schema_path='main.xsd')
            review = compare_revision(revision, tree.fields, objects)
        self.assertEqual((review['tables'], review['findings']), (0, []))
        self.assertTrue(any('Collapsed' in note and 'Code' in note for note in review['notes']))

    def test_original_85_and_612_documents_link_case_errors_to_the_field(self):
        from schema_review import compare_revision
        rows = {r['revision_id']: r for r in json.loads((ROOT / 'data/catalog.json').read_bytes())['revisions']}
        for ids, path, actual in [
            (('6964bfe61efdc0b25068c6c4b50784eca91eddc9953cb6ff275b13c417bed64c',
              'bf725d54324f1e94aae2b9d926d5852c283374a098623a235a66d4a434896282'),
             'paymentStatusResponse/errorList', 'ErrorListType'),
            (('590f0173a7eaad846760c6bab2bce493024608e3680f3897c0d8d71965026766',
              'b0a7ff7502d82517d74e7320911d0a97038df8d9ddf31fbc08c896de3ba2a39e'),
             'DecisionOkvedVp/regNum', 'org:regNum')]:
            for rid in ids:
                with self.subTest(revision=rid):
                    fields = json.loads((ROOT / 'data/fields' / (rid + '.json')).read_bytes())['fields']
                    findings = compare_revision(rows[rid], fields, ROOT / 'state/objects')['findings']
                    self.assertIn(('Тип данных', 'Тип '+actual+'.', [path]),
                                  [(f['kind'], f['xsd'], f['field_paths']) for f in findings])

    def test_exact_case_for_named_types_and_qualified_builtin_types(self):
        self.fields[1].update(type='org:regNum', base='xs:string')
        for label, mismatch in [('regnum', True), ('org:regnum', True), ('regNum', False),
                                ('org:regNum', False), ('xs:String', True), ('String', False)]:
            with self.subTest(label=label):
                body = make_body('Тип сообщения 55: извещение', 'Таблица 1', [HEADER, ['code', label, '', '1', '']])
                findings = audit(self.revision, body, self.fields)[1]
                self.assertEqual([f['kind'] for f in findings], ['Тип данных'] if mismatch else [])

    def test_length_uses_effective_inherited_bound(self):
        self.fields[1].update(type='Code', base='xs:string',
                              facets=[['maxLength', '20'], ['maxLength', '10']])
        for maximum, expected in (('20', True), ('10', False)):
            table = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                              [HEADER, ['code', 'Code', 'Макс. длина: '+maximum, '1', '']])
            self.assertEqual(bool(audit(self.revision, table, self.fields)[1]), expected)

    def test_nonnegative_requires_effective_lower_bound_at_least_zero(self):
        self.fields[1].update(type='Amount', base='xs:decimal')
        for bounds, expected in (([], True), ([['minInclusive', '-10']], True),
                                 ([['minExclusive', '-10']], True),
                                 ([['minInclusive', '-10'], ['minInclusive', '0']], False),
                                 ([['minExclusive', '0']], False)):
            self.fields[1]['facets'] = bounds
            table = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                              [HEADER, ['code', 'Amount', 'Неотрицательное число', '1', '']])
            self.assertEqual(bool(audit(self.revision, table, self.fields)[1]), expected, bounds)

    def test_nonnegative_integer_bounds_use_the_integer_value_space(self):
        from lxml import etree
        cases = [('integer', 'minExclusive', '-1', False),
                 ('integer', 'minInclusive', '-1', True),
                 ('integer', 'minExclusive', '-2', True),
                 ('int', 'minExclusive', '-1', False),
                 ('decimal', 'minExclusive', '-1', True),
                 ('decimal', 'minExclusive', '-0.5', True),
                 ('decimal', 'minInclusive', '-0.5', True)]
        table = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                          [HEADER, ['code', 'Amount', 'Неотрицательное число', '1', '']])
        for primitive, facet, bound, warns in cases:
            with self.subTest(primitive=primitive, facet=facet, bound=bound):
                source = ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                          '<xs:element name="code"><xs:simpleType><xs:restriction base="xs:'+primitive+'">'
                          '<xs:'+facet+' value="'+bound+'"/></xs:restriction></xs:simpleType></xs:element></xs:schema>')
                validator = etree.XMLSchema(etree.fromstring(source.encode()))
                witness = '-1' if primitive in ('integer', 'int') else '-0.25'
                self.assertEqual(validator.validate(etree.fromstring(('<code>'+witness+'</code>').encode())), warns)
                self.assertTrue(validator.validate(etree.fromstring(b'<code>0</code>')))
                self.fields[1].update(type='Amount', base='xs:'+primitive, facets=[[facet, bound]])
                self.assertEqual(bool(audit(self.revision, table, self.fields)[1]), warns)
        # Fractional bounds can occur in supplied flattened metadata. Apply the
        # same integer domain threshold rather than treating them as decimals.
        for facet in ('minInclusive', 'minExclusive'):
            self.fields[1].update(type='Amount', base='xs:integer', facets=[[facet, '-0.5']])
            self.assertEqual(audit(self.revision, table, self.fields)[1], [])

    def test_nonnegative_double_at_the_smallest_negative_magnitude(self):
        table = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                          [HEADER, ['code', 'Amount', 'Неотрицательное число', '1', '']])
        for facet, bound, mismatch in [('minExclusive', '-5E-324', False),
                                       ('minInclusive', '-5E-324', True),
                                       ('minExclusive', '-1E-323', True),
                                       ('minInclusive', '-1E-325', False)]:
            with self.subTest(facet=facet, bound=bound):
                self.fields[1].update(type='Amount', base='xs:double', facets=[[facet, bound]])
                self.assertEqual(bool(audit(self.revision, table, self.fields)[1]), mismatch)

    def test_effective_limits_match_lxml_for_inherited_types(self):
        import io
        from lxml import etree
        from schema import Schema
        for primitive, base_facets, derived_facets, accepted, rejected, limit, mismatch in (
            ('string', '<xs:maxLength value="20"/>', '<xs:maxLength value="10"/>', 'x'*10, 'x'*15, 'Макс. длина: 20', True),
            ('string', '<xs:maxLength value="20"/>', '<xs:maxLength value="10"/>', 'x'*10, 'x'*15, 'Макс. длина: 10', False),
            ('decimal', '<xs:minInclusive value="-10"/>', '', '0', '-11', 'Неотрицательное число', True),
            ('decimal', '<xs:minInclusive value="-10"/>', '<xs:minInclusive value="0"/>', '0', '-5', 'Неотрицательное число', False)):
            source = ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                      '<xs:simpleType name="Base"><xs:restriction base="xs:'+primitive+'">'+base_facets+'</xs:restriction></xs:simpleType>'
                      '<xs:simpleType name="Code"><xs:restriction base="Base">'+derived_facets+'</xs:restriction></xs:simpleType>'
                      '<xs:element name="notice"><xs:complexType><xs:sequence><xs:element name="code" type="Code"/></xs:sequence></xs:complexType></xs:element></xs:schema>')
            validator = etree.XMLSchema(etree.fromstring(source.encode()))
            self.assertTrue(validator.validate(etree.fromstring(('<notice><code>'+accepted+'</code></notice>').encode())))
            self.assertFalse(validator.validate(etree.fromstring(('<notice><code>'+rejected+'</code></notice>').encode())))
            if primitive == 'decimal' and mismatch:
                self.assertTrue(validator.validate(etree.fromstring(b'<notice><code>-5</code></notice>')))
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, 'w') as archive: archive.writestr('main.xsd', source)
            with zipfile.ZipFile(raw) as archive:
                tree = Schema(archive, 'main.xsd', 'notice'); tree.field(tree.root)
            table = make_body('Тип сообщения 55: извещение', 'Таблица 1', [HEADER, ['code', 'Code', limit, '1', '']])
            self.assertEqual(bool(audit(self.revision, table, tree.fields)[1]), mismatch)

    def test_numeric_limit_documents_only_the_maximum_string_length(self):
        body = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                         [HEADER, ['code', 'String', '60', '1', 'Фамилия']])
        self.fields[1].update(type='com:Name', base='xs:string', facets=[['maxLength', '60']])
        self.assertEqual(audit(self.revision, body, self.fields)[1], [])
        self.fields[1]['facets'].append(['minLength', '1'])
        findings = audit(self.revision, body, self.fields)[1]
        self.assertEqual(len(findings), 1)
        self.assertIn('minLength=1', findings[0]['xsd'])
        self.assertNotIn('maxLength=60', findings[0]['xsd'])

    def test_shared_full_name_card_links_to_both_uses_in_published_review(self):
        from schema_review import compare_revision
        revision_id = 'f19097fb0b88622f9eb8133531c50c146a82619ee90eb6857cb1b437ed629357'
        rows = json.loads((ROOT / 'data/catalog.json').read_bytes())['revisions']
        revision = next(row for row in rows if row['revision_id'] == revision_id)
        fields = json.loads((ROOT / 'data/fields' / (revision_id + '.json')).read_bytes())['fields']
        review = compare_revision(revision, fields, ROOT / 'state/objects')
        findings = [f for f in review['findings'] if f['location'] == 'Атрибуты типа fullNameType · lastName'
                    and f['kind'] == 'Ограничение значения']
        self.assertEqual(len(findings), 1)
        self.assertCountEqual(findings[0]['field_paths'], [
            'additionalVacationStatement/insurerInfo/documentsSendPerson/fullName/lastName',
            'additionalVacationStatement/insuredInfo/fullNamePU/lastName'])
        self.assertEqual(findings[0]['field_link_scope'], 'field')

    def test_primitive_base_does_not_document_named_type_restrictions(self):
        self.fields[1].update(type='org:regNum', base='xs:string', facets=[['pattern', r'\d{10}']])
        def document(typ, limit='', description=''):
            return make_body('Тип сообщения 55: извещение', 'Таблица 1',
                             [HEADER, ['code', typ, limit, '1', description]])
        findings = audit(self.revision, document('xs:string'), self.fields)[1]
        self.assertTrue(any(f['kind'] == 'Ограничение значения' and 'notice/code' in f['xlocation'] for f in findings))
        self.assertEqual(audit(self.revision, document('org:regNum'), self.fields)[1], [])
        self.assertEqual(audit(self.revision, document('xs:string', r'\d{10}'), self.fields)[1], [])
        self.assertEqual(audit(self.revision, document('xs:string', '', 'Ровно 10 цифр'), self.fields)[1], [])

    def test_message_111_restricted_empl_reg_no_is_reported_in_own_environment(self):
        from schema import Schema
        revisions = json.loads((ROOT/'data/catalog.json').read_bytes())['revisions']
        for environment in ('prod', 'test'):
            revision = next(r for r in revisions if r['number'] == '111' and r['environment'] == environment
                            and r.get('source_type') == 'module')
            with zipfile.ZipFile(ROOT/'state/objects'/revision['archive_sha256']) as archive:
                tree = Schema(archive, revision['schema_path'], revision['root']); tree.field(tree.root)
            with zipfile.ZipFile(ROOT/'state/objects'/revision['document_sha256']) as document:
                body = ET.fromstring(document.read('word/document.xml')).find(W+'body')
            findings = audit(revision, body, tree.fields)[1]
            self.assertTrue(any(f['kind'] == 'Ограничение значения' and f['xlocation'].endswith('/emplRegNo2') for f in findings), environment)

    def setUp(self):
        self.revision = dict(number='55', root='notice', namespace='urn:test', schema_path='Example.xsd')
        self.fields = [field('notice', 'notice', -1, 'NoticeType', [1]),
                       field('code', 'notice/code', 0, 'xs:string')]

    def test_type_card_matches_declared_type_namespace_for_unqualified_use(self):
        self.fields[0].update(ns='', typeNs='urn:test', typeFile='Imported.xsd')
        body=make_body('Приложение 1', 'Атрибуты типа NoticeType', [
            ['Пространство имен', 'urn:test'], HEADER, ['code', 'xs:string', 'Макс. длина: 2', '1', '']])
        self.fields[1]['facets']=[['maxLength','3']]
        checked,findings=audit(self.revision,body,self.fields)
        self.assertGreater(checked,0)
        self.assertTrue(any(f['kind']=='Ограничение длины' for f in findings))

    def test_real_notice_send_type_card_is_audited_and_detects_changed_card(self):
        from schema import Schema
        rows=json.loads((ROOT/'data/catalog.json').read_bytes())['revisions']
        revision=next(r for r in rows if r['number']=='110' and r['environment']=='prod' and r.get('document_version')=='2.28')
        with zipfile.ZipFile(ROOT/'state/objects'/revision['archive_sha256']) as archive:
            tree=Schema(archive,revision['schema_path'],revision['root']);tree.field(tree.root)
        with zipfile.ZipFile(ROOT/'state/objects'/revision['document_sha256']) as document:
            tables=prepare(ET.fromstring(document.read('word/document.xml')).find(W+'body'))
        table=next(t for t in tables if 'NoticeSendType' in t[1])
        number,title,context,rows,properties=table
        targets=Comparison(revision,tree.fields).targets(number,title,context,properties)
        self.assertEqual(len(targets),1)
        self.assertEqual(targets[0]['ns'],'')
        self.assertEqual(targets[0]['typeNs'],properties['Пространство имен'])
        self.assertGreater(audit(revision,[table],tree.fields)[0],0)
        changed=dict(properties)
        changed['Дочерние элементы']=properties.get('Дочерние элементы','')+' regressionProbe'
        issues=audit(revision,[(number,title,context,None,changed)],tree.fields)[1]
        self.assertTrue(any('regressionProbe' in issue['spec'] for issue in issues))

    def test_current_constraints_recomputed_after_either_source_changes(self):
        def document(name, typ, limit):
            return make_body('Тип сообщения 56: другое', 'Таблица 1', [HEADER, ['other', '', '', '1', '']],
                             'Тип сообщения 55: извещение', 'Таблица 2', [HEADER, [name, typ, limit, '1', '']])
        body = document('Code', 'integer', 'Макс. длина: 2')
        self.fields[1].update(min='0', facets=[['maxLength', '3']])
        checked, findings = audit(self.revision, body, self.fields)
        self.assertGreater(checked, 0)
        self.assertEqual({f['kind'] for f in findings}, {'Имя элемента', 'Кратность', 'Тип данных', 'Ограничение длины'})
        self.assertTrue(all('Таблица 2' in f['location'] and 'notice/code' in f['xlocation'] for f in findings))
        self.fields[1].update(min='1', facets=[['maxLength', '2']])
        self.assertEqual(audit(self.revision, document('code', 'string', 'Макс. длина: 2'), self.fields)[1], [])

    def test_root_card_checks_child_list_and_skips_another_section(self):
        body = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                         [['Тип', 'NoticeType'], ['Дочерние элементы', 'code extra'],
                          ['Схема', '<xs:element name="notice" type="NoticeType"/>']],
                         'Тип сообщения 56: другое', 'Таблица 2', [HEADER, ['nonsense', 'string', '', '1', '']])
        findings = audit(self.revision, body, self.fields)[1]
        self.assertEqual(len(findings), 1)
        self.assertIn('extra', findings[0]['spec'])
        self.fields[0]['children'].append(2)
        self.fields.append(field('extra', 'notice/extra', 0, 'xs:string'))
        self.assertEqual(audit(self.revision, body, self.fields)[1], [])

    def test_current_service_message_six_scanned(self):
        editions = json.loads((ROOT/'data/catalog.json').read_bytes())['revisions']
        r = next(r for r in editions if r['number'] == '6' and r.get('source_type') == 'module'
                 and r['environment'] == 'test')
        with zipfile.ZipFile(ROOT/'state/objects'/r['document_sha256']) as z:
            body = ET.fromstring(z.read('word/document.xml')).find(W + 'body')
        fields = json.loads((ROOT/'data/fields'/(r['revision_id']+'.json')).read_bytes())['fields']
        checked, findings = audit(r, body, fields)
        self.assertGreater(checked, 0)
        self.assertFalse(any('Таблица 1 ' in f['location'] for f in findings))

    def test_nested_schema_fragment_checks_missing_child_and_attributes(self):
        self.fields[0]['children'].append(2)
        self.fields.append(field('@id', 'notice/@id', 0, 'xs:string'))
        self.fields[2].update(kind='attribute', min='0')
        def fragment(content):
            return make_body('Тип сообщения 55: извещение', 'Таблица 1', [
                ['Схема', '<xs:element name="notice"><xs:complexType>'+content+'</xs:complexType></xs:element>']])
        _, issues = audit(self.revision, fragment('<xs:sequence/>'), self.fields)
        self.assertEqual({i['xlocation'].split(' · ')[-1] for i in issues}, {'notice/code', 'notice/@id'})
        documented = '<xs:sequence><xs:element name="code" type="xs:string"/></xs:sequence><xs:attribute name="id" type="xs:string"/>'
        self.assertEqual(audit(self.revision, fragment(documented), self.fields)[1], [])

    def test_different_deadline_events_and_corrected_annotation(self):
        body = make_body('Тип сообщения 55: извещение', 'Таблица 4', [HEADER, [
            'code', 'date', '', '1', 'Крайняя дата получения подтверждения прочтения сообщения']])
        self.fields[1].update(type='xs:date', base='xs:date', doc='Крайняя дата выгрузки документа из СЭДО')
        self.assertEqual([f['kind'] for f in audit(self.revision, body, self.fields)[1]], ['Описание поля'])
        self.fields[1]['doc'] = 'Крайняя дата получения подтверждения прочтения'
        self.assertEqual(audit(self.revision, body, self.fields)[1], [])
