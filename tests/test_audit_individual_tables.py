import io
import unittest
import zipfile
from xml.etree import ElementTree as ET
from lxml import etree

from audit_individual_tables import audit, expanded
from module_xsd import W
from schema import Schema, XS


def paragraph(parent, value):
    run = ET.SubElement(ET.SubElement(parent, W + "p"), W + "r")
    ET.SubElement(run, W + "t").text = value


def document(heading, rows):
    body = ET.Element(W + "body")
    paragraph(body, heading)
    table = ET.SubElement(body, W + "tbl")
    for key, value in rows.items():
        row = ET.SubElement(table, W + "tr")
        paragraph(ET.SubElement(row, W + "tc"), key)
        cell = ET.SubElement(row, W + "tc")
        if isinstance(value, str):
            paragraph(cell, value)
        else:
            subtable = ET.SubElement(cell, W + "tbl")
            for values in value:
                subrow = ET.SubElement(subtable, W + "tr")
                for item in values:
                    paragraph(ET.SubElement(subrow, W + "tc"), item)
    return body


class AuditIndividualTablesTests(unittest.TestCase):
    def test_enumeration_whitespace_uses_the_base_of_its_own_restriction(self):
        prefix = ('<xs:simpleType name="Collapsed"><xs:restriction base="xs:string">'
                  '<xs:whiteSpace value="collapse"/></xs:restriction></xs:simpleType>')
        template = ('<xs:element name="root"><xs:simpleType><xs:restriction base="{}">'
                    '<xs:whiteSpace value="collapse"/><xs:enumeration value="{}"/>'
                    '</xs:restriction></xs:simpleType></xs:element>')
        for base, word, differs in [('xs:string', 'A B', True), ('xs:string', 'A  B', False),
                                    ('Collapsed', 'A B', False)]:
            with self.subTest(base=base, word=word):
                actual = template.format(base, 'A  B')
                quoted = template.format(base, word)
                if differs:
                    xml = etree.fromstring(b'<root xmlns="urn:test">A B</root>')
                    def native(declaration):
                        return etree.XMLSchema(etree.fromstring(
                            f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:test">{declaration}</xs:schema>'.encode()))
                    self.assertFalse(native(actual).validate(xml))
                    self.assertTrue(native(quoted).validate(xml))
                rows = {'Схема': quoted, 'Ограничения': [['Список допустимых значений', word]]}
                count, findings = self.check(document('Атрибуты элемента root', rows), prefix + actual)
                self.assertEqual(count, 1)
                self.assertEqual({finding['kind'] for finding in findings},
                                 {'Объявление типа', 'Ограничения значения'} if differs else set())
                if differs:
                    self.assertTrue(all('enumeration=A  B' in finding['xsd'] for finding in findings), findings)
                    self.assertTrue(all('enumeration=A B' in finding['spec'] for finding in findings), findings)

    def test_quoted_enum_uses_its_own_anonymous_base(self):
        actual = ('<xs:element name="root"><xs:simpleType><xs:restriction><xs:simpleType>'
                  '<xs:restriction base="xs:string"><xs:whiteSpace value="preserve"/></xs:restriction>'
                  '</xs:simpleType><xs:enumeration value="A  B"/></xs:restriction></xs:simpleType></xs:element>')
        quoted = actual.replace('value="preserve"', 'value="collapse"')
        count, findings = self.check(document('Атрибуты элемента root', {'Схема': quoted}), actual)
        self.assertEqual(count, 1)
        self.assertEqual([finding['kind'] for finding in findings], ['Объявление типа'])

    def test_union_enumeration_uses_its_own_member_and_namespace(self):
        prefix = '<xs:simpleType name="Mixed"><xs:union memberTypes="xs:QName xs:int"/></xs:simpleType>'
        def element(value, binding=''):
            return ('<xs:element name="root" xmlns:t="urn:test"><xs:simpleType><xs:restriction base="t:Mixed">'
                    f'<xs:enumeration {binding} value="{value}"/>'
                    '</xs:restriction></xs:simpleType></xs:element>')
        source = element('p:code', 'xmlns:p="urn:one"')
        for quote, actual, differs, limited in (
            (element('p:code', 'xmlns:p="urn:two"'), source, True, False),
            (element('q:code', 'xmlns:q="urn:one"'), source, False, False),
            (element('p:code'), source, False, True),
            (element('+01'), element('1'), False, False),
        ):
            with self.subTest(quote=quote):
                notes = []
                card = document('Атрибуты элемента root', {'Схема': quote})
                count, findings = self.check(card, prefix+actual, notes=notes)
                self.assertEqual((count, bool(findings), bool(notes)), (1, differs, limited))
                if differs:
                    self.assertIn('urn:one', findings[0]['xsd'])
                    self.assertIn('urn:two', findings[0]['spec'])

    def test_equivalent_facets_match_in_schema_and_restrictions_table(self):
        cases = [
            ('decimal', 'minInclusive', '1.0', '1.00'),
            ('string', 'length', '1', '01'),
            ('decimal', 'totalDigits', '5', '+05'),
            ('decimal', 'fractionDigits', '2', '02'),
            ('dateTime', 'maxInclusive', '2026-11-17T09:00:00Z', '2026-11-17T12:00:00+03:00'),
            ('decimal', 'maxExclusive', '1.000000000000000000000000000001', '1.0000000000000000000000000000010'),
            ('time', 'maxInclusive', '23:30:00.12345678901Z', '00:30:00.123456789010+01:00'),
            ('time', 'minExclusive', '00:30:00Z', '23:30:00-01:00'),
            ('time', 'minInclusive', '00:00:00', '24:00:00.0'),
        ]
        for base, facet, actual, quoted in cases:
            with self.subTest(base=base, facet=facet, actual=actual, quoted=quoted):
                template = (f'<xs:element name="root"><xs:simpleType><xs:restriction base="xs:{base}">'
                            f'<xs:{facet} value="{{}}"/></xs:restriction></xs:simpleType></xs:element>')
                rows = {'Схема': template.format(quoted), 'Ограничения': [[facet, quoted]]}
                self.assertEqual(self.check(document('Атрибуты элемента root', rows), template.format(actual)), (1, []))

    def test_distinct_or_invalid_facets_keep_original_word_and_schema_values(self):
        cases = [
            ('decimal', 'minInclusive', '1.0', '2.0'),
            ('decimal', 'minInclusive', '1.0', '1e0'),
            ('string', 'length', '1', '1.0'),
            ('string', 'pattern', '[0-9]', '[0123456789]'),
            ('decimal', 'maxExclusive', '1.000000000000000000000000000001', '1.000000000000000000000000000002'),
            ('dateTime', 'maxInclusive', '2026-11-17T09:00:00.12345678901Z', '2026-11-17T09:00:00.12345678902Z'),
            ('dateTime', 'maxInclusive', '2026-11-17T09:00:00Z', '2026-11-17T09:00:00'),
            ('time', 'maxInclusive', '23:30:00.12345678901Z', '00:30:00.12345678902+01:00'),
            ('time', 'maxInclusive', '00:00:00Z', '00:00:00'),
            ('time', 'minInclusive', '00:00:00Z', '14:01:00+14:01'),
            ('time', 'minInclusive', '00:00:00.001Z', '24:00:00.001Z'),
        ]
        for base, facet, actual, quoted in cases:
            with self.subTest(base=base, facet=facet, actual=actual, quoted=quoted):
                template = (f'<xs:element name="root"><xs:simpleType><xs:restriction base="xs:{base}">'
                            f'<xs:{facet} value="{{}}"/></xs:restriction></xs:simpleType></xs:element>')
                rows = {'Схема': template.format(quoted), 'Ограничения': [[facet, quoted]]}
                count, findings = self.check(document('Атрибуты элемента root', rows), template.format(actual))
                self.assertEqual(count, 1)
                self.assertEqual({finding['kind'] for finding in findings}, {'Объявление типа', 'Ограничения значения'})
                self.assertTrue(all(f'{facet}={quoted}' in finding['spec'] for finding in findings), findings)
                self.assertTrue(all(f'{facet}={actual}' in finding['xsd'] for finding in findings), findings)

    def test_invalid_word_enum_keeps_word_source_orientation_in_both_differences(self):
        from audit_individual_tables import facet_difference
        notes=[]
        field=dict(baseNs=XS,base='xs:int',facets=[],values=[])
        self.assertEqual(facet_difference([('enumeration','not-an-int')],[('enumeration','1')],field,None,None,None,note=notes.append),
                         ({('enumeration','not-an-int')},{('enumeration','1')}))
        self.assertEqual(notes,[])

    def test_exact_scalar_enumerations_avoid_quadratic_schema_compilation(self):
        from unittest.mock import patch
        from lxml import etree
        from audit_individual_tables import facet_difference
        field=dict(baseNs=XS,base='xs:string',facets=[],values=[])
        rows=[('enumeration',f'value{index:03}') for index in range(100)]
        native=etree.XMLSchema
        with patch.object(etree,'XMLSchema',wraps=native) as compiled:
            self.assertEqual(facet_difference(rows,rows,field,None,None,None),(set(),set()))
            self.assertEqual(compiled.call_count,0)
        decimal=dict(field,base='xs:decimal')
        self.assertEqual(facet_difference([('enumeration','1'),('enumeration','1.0')],[('enumeration','1')],decimal,None,None,None),(set(),set()))
        self.assertEqual(facet_difference([('enumeration','1')],[('enumeration','1'),('enumeration','1.0')],decimal,None,None,None),(set(),set()))
        owners=[etree.fromstring(f'<xs:element xmlns:xs="{XS}" xmlns:p="{uri}" name="root" type="xs:QName"/>') for uri in ('urn:A','urn:B')]
        difference=facet_difference([('enumeration','p:value')],[('enumeration','p:value')],dict(field,base='xs:QName'),*owners,None)
        self.assertTrue(difference[0] and difference[1])

    def test_unrecognized_word_occurrence_prose_reports_without_aborting(self):
        source='<xs:element name="root" type="xs:string"/>'
        notes=[]
        count,findings=self.check(document('Атрибуты элемента root',{'Свойства':[['maxOcc','без ограничения']]}),source,notes=notes)
        self.assertEqual(count,1)
        self.assertTrue(findings or notes)
        quote='<xs:element name="root" type="xs:string" maxOccurs="без ограничения"/>'
        count,findings=self.check(document('Атрибуты элемента root',{'Схема':quote}),source,notes=notes)
        self.assertEqual(count,1)
        self.assertTrue(findings or notes)

    def test_equal_lexical_qname_enums_keep_each_declaration_binding(self):
        from lxml import etree
        def declaration(enums):
            return '<xs:element name="root"><xs:simpleType><xs:restriction base="xs:QName">'+''.join(f'<xs:enumeration xmlns:{prefix}="{uri}" value="{prefix}:value"/>' for prefix,uri in enums)+'</xs:restriction></xs:simpleType></xs:element>'
        single=declaration([('p','urn:A')])
        both=declaration([('p','urn:A'),('p','urn:B')])
        aliases=declaration([('a','urn:A'),('b','urn:B')])
        for source,quote,expected,source_accepts_b,quote_accepts_b in [
                (single,both,1,False,True),(both,single,1,True,False),(both,aliases,0,True,True)]:
            with self.subTest(source=source,quote=quote):
                count,findings=self.check(document('Атрибуты элемента root',{'Схема':quote}),source)
                self.assertEqual((count,len(findings)),(1,expected),findings)
                for rule,accepts in ((source,source_accepts_b),(quote,quote_accepts_b)):
                    native=etree.XMLSchema(etree.fromstring(f'<xs:schema xmlns:xs="{XS}">{rule}</xs:schema>'))
                    self.assertEqual(native.validate(etree.fromstring('<root xmlns:p="urn:B">p:value</root>')),accepts)

    def test_effective_qname_enumeration_keeps_duplicate_lexical_values(self):
        source='<xs:simpleType name="Code"><xs:restriction base="xs:QName"><xs:enumeration xmlns:p="urn:A" value="p:value"/><xs:enumeration xmlns:p="urn:B" value="p:value"/></xs:restriction></xs:simpleType><xs:element name="root" type="Code"/>'
        quote='<xs:element xmlns:p="urn:A" name="root" type="Code"/>'
        count,findings=self.check(document('Атрибуты элемента root',{'Схема':quote,'Ограничения':[['Список допустимых значений','p:value']]}),source)
        self.assertEqual((count,len(findings)),(1,1),findings)
        self.assertEqual(findings[0]['kind'],'Ограничения значения')

    def test_unknown_binding_among_same_lexical_qname_enums_records_note(self):
        source='<xs:element name="root"><xs:simpleType><xs:restriction base="xs:QName"><xs:enumeration xmlns:p="urn:A" value="p:value"/></xs:restriction></xs:simpleType></xs:element>'
        quote=source.replace('</xs:restriction>','<xs:enumeration value="p:value"/></xs:restriction>')
        notes=[]
        self.assertEqual(self.check(document('Атрибуты элемента root',{'Схема':quote}),source,notes=notes),(1,[]))
        self.assertTrue(any('QName' in message and 'контекст' in message for message in notes),notes)

    def test_quoted_occurrence_aliases_match_canonical_fields_and_models(self):
        source='<xs:element name="root"><xs:complexType><xs:sequence minOccurs="0" maxOccurs="1"><xs:element name="a" type="xs:string" minOccurs="0" maxOccurs="1"/></xs:sequence></xs:complexType></xs:element>'
        quote=source.replace('minOccurs="0"','minOccurs="00"').replace('maxOccurs="1"','maxOccurs="01"')
        self.assertEqual(self.check(document('Атрибуты элемента root',{'Схема':quote,'Свойства':[['minOcc','01'],['maxOcc','01']]}),source),(1,[]))
        child='<xs:element name="a" type="xs:string" minOccurs="00" maxOccurs="01"/>'
        self.assertEqual(self.check(document('Атрибуты элемента root/a',{'Схема':child,'Свойства':[['minOcc',' 0 '],['maxOcc','01']]}),source),(1,[]))
    def test_enumerations_use_typed_values_and_each_namespace_scope(self):
        for base, actual, quoted, binding, expected in [
                ('decimal','1.00','1.0','',0),
                ('QName','p:value','p:value','xmlns:p="urn:two"',2),
                ('QName','p:value','q:value','xmlns:q="urn:one"',0)]:
            source=f'<xs:element name="root" xmlns:p="urn:one"><xs:simpleType><xs:restriction base="xs:{base}"><xs:enumeration value="{actual}"/></xs:restriction></xs:simpleType></xs:element>'
            quote=f'<xs:element name="root" {binding}><xs:simpleType><xs:restriction base="xs:{base}"><xs:enumeration value="{quoted}"/></xs:restriction></xs:simpleType></xs:element>'
            card=document('Атрибуты элемента root',{'Схема':quote,'Ограничения':[['Список допустимых значений',quoted]]})
            checked, issues=self.check(card,source)
            self.assertEqual((checked,len(issues)),(1,expected),issues)
            from lxml import etree
            for declaration, accepted in ((source,True),(quote,expected == 0)):
                validator=etree.XMLSchema(etree.fromstring(f'<xs:schema xmlns:xs="{XS}">{declaration}</xs:schema>'.encode()))
                probe=etree.fromstring((f'<root xmlns:p="urn:one">{actual}</root>').encode())
                self.assertEqual(validator.validate(probe),accepted)

    def test_enum_with_unknown_quoted_qname_binding_records_coverage_limit(self):
        source='<xs:element name="root" xmlns:p="urn:one"><xs:simpleType><xs:restriction base="xs:QName"><xs:enumeration value="p:value"/></xs:restriction></xs:simpleType></xs:element>'
        notes=[]
        card=document('Атрибуты элемента root',{'Схема':source.replace(' xmlns:p="urn:one"',''),'Ограничения':[['Список допустимых значений','p:value']]})
        self.assertEqual(self.check(card,source,notes=notes),(1,[]))
        self.assertTrue(any('QName' in message and 'контекст' in message for message in notes),notes)

    def test_repeated_choice_labels_do_not_imply_a_single_field_order(self):
        from audit_individual_tables import child_order_matches
        model = {'contentModel': ['choice', ['sequence', 'a', 'b'], ['sequence', 'b', 'a']]}
        self.assertTrue(child_order_matches(model, ['a', 'b', 'b', 'a'], ['a', 'b']))
        self.assertFalse(child_order_matches({'contentModel': ['sequence', 'a', 'b']}, ['b', 'a'], ['a', 'b']))

    def test_fixed_default_compare_typed_numeric_temporal_and_qname_values(self):
        for key in ('fixed', 'default'):
            for base, actual, quote, binding, mismatch in [
                    ('decimal', '1.00', '1.0', '', False), ('integer', '+001', '1', '', False),
                    ('dateTime', '2026-11-17T12:00:00+03:00', '2026-11-17T09:00:00Z', '', False),
                    ('decimal', '1.00', '2', '', True),
                    ('QName', 'k:value', 'v:value', 'xmlns:v="urn:one"', False),
                    ('QName', 'k:value', 'k:value', 'xmlns:k="urn:two"', True)]:
                with self.subTest(key=key, base=base, quote=quote):
                    source = f'<xs:element name="root" xmlns:k="urn:one" type="xs:{base}" {key}="{actual}"/>'
                    snippet = f'<xs:element name="root" {binding} type="xs:{base}" {key}="{quote}"/>'
                    rows = {'Схема': snippet}
                    if base != 'QName': rows['Свойства'] = [[key, quote]]
                    checked, issues = self.check(document('Атрибуты элемента root', rows), source)
                    self.assertEqual((checked, len(issues)), (1, (1 if base == 'QName' else 2) if mismatch else 0))

    def test_unknown_qname_context_is_reported_as_a_coverage_note(self):
        source = '<xs:element name="root" xmlns:k="urn:one" type="xs:QName" fixed="k:value"/>'
        notes = []
        card = document('Атрибуты элемента root', {'Свойства': [['fixed', 'k:value']]})
        self.assertEqual(self.check(card, source, notes=notes), (1, []))
        self.assertTrue(any('QName' in item for item in notes), notes)

    def test_calendar_and_list_constraints_use_typed_equality_in_word_cards(self):
        calendar = '2026-11-17T00:00:00.1234567890123456781Z'
        day = '2026-12-01T00:00:00Z'
        for key in ('fixed', 'default'):
            for declaration, actual, quoted, different in (
                ('<xs:restriction base="xs:dateTime"/>', calendar,
                 '2026-11-16T23:00:00.1234567890123456781000-01:00', False),
                ('<xs:union memberTypes="xs:dateTime xs:integer"/>', calendar,
                 '2026-11-16T23:00:00.1234567890123456781000-01:00', False),
                ('<xs:restriction base="xs:dateTime"/>', calendar, calendar.replace('6781Z', '6782Z'), True),
                ('<xs:restriction base="xs:dateTime"/>', day, '2026-11-31T00:00:00Z', True),
                ('<xs:list itemType="xs:int"/>', '1 2', '01   +02', False),
                ('<xs:list itemType="xs:int"/>', '1 2', '1  3', True),
            ):
                with self.subTest(key=key, declaration=declaration, quoted=quoted):
                    def element(value):
                        return f'<xs:element name="root" {key}="{value}"><xs:simpleType>{declaration}</xs:simpleType></xs:element>'
                    rows = {'Схема': element(quoted), 'Свойства': [[key, quoted]]}
                    checked, issues = self.check(document('Атрибуты элемента root', rows), element(actual))
                    self.assertEqual(checked, 1)
                    self.assertEqual(bool(issues), different, issues)

    def test_list_item_with_unproved_union_uses_coverage_note(self):
        value = ('<xs:element name="root" {} fixed="{}"><xs:simpleType><xs:list>'
                 '<xs:simpleType><xs:union memberTypes="xs:int xs:QName"/></xs:simpleType>'
                 '</xs:list></xs:simpleType></xs:element>')
        for quote, source, limited in (
            (value.format('', '01 +02'), value.format('', '1 2'), False),
            (value.format('', 'p:code'), value.format('xmlns:p="urn:test"', 'p:code'), True),
        ):
            notes = []
            card = document('Атрибуты элемента root', {'Схема': quote})
            self.assertEqual(self.check(card, source, notes=notes), (1, []))
            self.assertEqual(any('тип элемента списка' in message for message in notes), limited, notes)

    def test_union_constraints_compare_the_declared_member_and_expanded_name(self):
        for key in ('fixed', 'default'):
            with self.subTest(key=key):
                source = (f'<xs:attribute name="code" xmlns:p="urn:one" {key}="p:value">'
                          '<xs:simpleType><xs:union memberTypes="xs:QName xs:integer"/></xs:simpleType>'
                          '</xs:attribute>')
                root = '<xs:element name="root"><xs:complexType>{}</xs:complexType></xs:element>'
                heading = 'Атрибут root/@code'
                for quote, differs, limited in [
                        (source.replace('urn:one', 'urn:two'), True, False),
                        (source.replace('xmlns:p', 'xmlns:q').replace('p:value', 'q:value'), False, False),
                        (source.replace(' xmlns:p="urn:one"', ''), False, True),
                        (source.replace('xs:QName', 'xs:string'), True, False)]:
                    notes = []
                    count, issues = self.check(document(heading, {'Схема': quote}), root.format(source), notes=notes)
                    self.assertEqual(count, 1)
                    self.assertEqual(bool(issues), differs, issues)
                    self.assertEqual(any('union' in message for message in notes), limited, notes)
                    if differs:
                        self.assertIn('urn:one', issues[0]['xsd'])

    def test_attribute_table_distinguishes_qname_constraint_presence(self):
        for key in ('fixed', 'default'):
            for constrained, differs in [(False, True), (True, False)]:
                source = ('<xs:element name="root"><xs:complexType><xs:attribute name="code" xmlns:k="urn:one" type="xs:QName"'
                          +(f' {key}="k:value"' if constrained else '')+'/></xs:complexType></xs:element>')
                rows = {'Схема': source, 'Атрибуты': [['Name', 'Type', 'Use', 'Default', 'Fixed'],
                        ['code', 'xs:QName', 'optional', 'k:value' if key == 'default' else '', 'k:value' if key == 'fixed' else '']]}
                self.assertEqual(bool(self.check(document('Атрибуты элемента root', rows), source)[1]), differs)

    def test_all_child_order_is_unordered_but_sequence_and_nested_groups_are_ordered(self):
        for compositor, mismatch in [('all', False), ('sequence', True)]:
            source = f'<xs:element name="root"><xs:complexType><xs:{compositor}><xs:element name="a"/><xs:element name="b"/></xs:{compositor}></xs:complexType></xs:element>'
            quoted = source.replace('name="a"', 'name="TEMP"').replace('name="b"', 'name="a"').replace('name="TEMP"', 'name="b"')
            for rows in ({'Дочерние элементы': 'b,a'}, {'Схема': quoted}):
                self.assertEqual(bool(self.check(document('Атрибуты элемента root', rows), source)[1]), mismatch, (compositor, rows))
        source = '<xs:element name="root"><xs:complexType><xs:sequence><xs:sequence minOccurs="0"><xs:element name="a"/><xs:element name="b"/></xs:sequence></xs:sequence></xs:complexType></xs:element>'
        for quoted in (source.replace('minOccurs="0"', 'minOccurs="1"'), source.replace('name="a"','name="TEMP"').replace('name="b"','name="a"').replace('name="TEMP"','name="b"')):
            self.assertTrue(self.check(document('Атрибуты элемента root', {'Схема': quoted}), source)[1])

    def test_boolean_constraints_use_the_typed_value_in_fragment_and_table(self):
        for key in ('fixed','default'):
            for actual, quoted, different in [('true',' 1 ',False),('false','0',False),('0','1',True)]:
                source = f'<xs:element name="root" type="xs:boolean" {key}="{actual}"/>'
                card = document('Атрибуты элемента root', {'Схема':source.replace(actual, quoted),
                                'Свойства':[[key,quoted]]})
                _, issues = self.check(card, source)
                self.assertEqual(len(issues), 2 if different else 0, (key, quoted))

    def test_fixed_and_default_table_values_preserve_literal_spaces(self):
        headings = ["Name", "Type", "Use", "Default", "Fixed"]
        for key in ("fixed", "default"):
            for actual, mismatch in ((" A  B ", False), ("A B", True), ("  ", False)):
                literal = "  " if actual == "  " else " A  B "
                with self.subTest(key=key, actual=actual):
                    prop = document("Атрибуты элемента root", {"Свойства": [[key, literal]]})
                    source = f'<xs:element name="root" type="xs:string" {key}="{actual}"/>'
                    self.assertEqual(bool(self.check(prop, source)[1]), mismatch)
                    values = [literal if k == key else "  " for k in ("default", "fixed")]
                    attr = document("Атрибуты элемента root", {"Атрибуты": [
                        headings, ["code", "xs:string", "optional", *values]]})
                    source = (f'<xs:element name="root"><xs:complexType><xs:attribute name="code"'
                              f' type="xs:string" {key}="{actual}"/></xs:complexType></xs:element>')
                    self.assertEqual(bool(self.check(attr, source)[1]), mismatch)

    def test_word_retains_literal_spaces_inside_schema_and_in_facet_rows(self):
        snippet = ('<xs:element name="root"><xs:simpleType><xs:restriction base="xs:string">'
                   '<xs:enumeration value=" A  B "/></xs:restriction></xs:simpleType></xs:element>')
        card = document('Атрибуты элемента root', {'Схема': snippet,
                        'Ограничения': [['Список допустимых значений', ' A  B ']]})
        self.assertEqual(self.check(card, snippet), (1, []))
        _, issues = self.check(card, snippet.replace(' A  B ', ' A B '))
        self.assertEqual({issue['kind'] for issue in issues}, {'Объявление типа', 'Ограничения значения'})

    def test_ref_compares_expanded_names_and_handles_missing_word_bindings(self):
        schema = ('<xs:element name="child" type="xs:string"/><xs:element name="root"><xs:complexType>'
                  '<xs:sequence><xs:element xmlns:a="urn:test" ref="a:child"/></xs:sequence></xs:complexType></xs:element>')
        for reference, binding, mismatch in [('b:child', 'xmlns:b="urn:test"', False),
                                              ('a:child', 'xmlns:a="urn:other"', True),
                                              ('unspecified:child', '', False),
                                              ('unspecified:other', '', True)]:
            with self.subTest(reference=reference, binding=binding):
                card = document('Атрибуты элемента root/child', {
                    'Схема': f'<xs:element {binding} ref="{reference}"/>'})
                checked, findings = self.check(card, schema)
                self.assertEqual(checked, 1)
                self.assertEqual(bool(findings), mismatch)
                if 'urn:other' in binding:
                    self.assertIn('urn:other', findings[0]['spec'])
                    self.assertIn('urn:test', findings[0]['xsd'])

    def test_document_type_names_use_their_own_namespace_bindings(self):
        for documented, source, match in (
            ('xmlns:q="'+XS+'" type="q:string"', 'type="xs:string"', True),
            ('xmlns:q="urn:other" type="q:Local"', 'xmlns:q="urn:test" type="q:Local"', False),
        ):
            prefix = '<xs:simpleType name="Local"><xs:restriction base="xs:string"/></xs:simpleType>'
            card = document('Атрибуты элемента root', {'Схема': '<xs:element name="root" '+documented+'/>'})
            count, findings = self.check(card, prefix+'<xs:element name="root" '+source+'/>')
            self.assertEqual(count, 1)
            self.assertEqual(bool(findings), not match, (documented, findings))
            if not match:
                self.assertIn('urn:other', findings[0]['spec'])
                self.assertIn('urn:test', findings[0]['xsd'])

    def test_root_content_model_resolves_nested_references(self):
        def root(binding, prefix):
            return (f'<xs:element name="root" {binding}><xs:complexType><xs:sequence>'
                    f'<xs:element ref="{prefix}:child"/></xs:sequence></xs:complexType></xs:element>')
        source = '<xs:element name="child" type="xs:string"/>'+root('xmlns:a="urn:test"', 'a')
        for binding, prefix, differs in [('xmlns:b="urn:test"', 'b', False), ('', 'b', False),
                                         ('xmlns:a="urn:other"', 'a', True)]:
            with self.subTest(binding=binding):
                card = document('Атрибуты элемента root', {'Схема': root(binding, prefix)})
                self.assertEqual(bool(self.check(card, source)[1]), differs)

    def test_content_model_compares_references_with_the_namespace_of_local_elements(self):
        from lxml import etree
        ref = '<xs:element xmlns:a="urn:test" ref="a:child"/>'
        local = '<xs:element name="child" type="xs:string"{} />'
        definition = '<xs:element name="root"><xs:complexType><xs:sequence>{}</xs:sequence></xs:complexType></xs:element>'
        global_child = '<xs:element name="child" type="xs:string"/>'
        xml = [etree.fromstring(value) for value in (
            b'<a:root xmlns:a="urn:test"><a:child>x</a:child></a:root>',
            b'<a:root xmlns:a="urn:test"><child>x</child></a:root>')]
        for quoted, declared, source_default, differs in [
                (ref, local.format(''), 'unqualified', True),
                (ref, local.format(''), 'qualified', False),
                (ref, local.format(' form="unqualified"'), 'qualified', True),
                (local.format(' form="unqualified"'), ref, 'qualified', True)]:
            with self.subTest(quoted=quoted, declared=declared, source_default=source_default):
                samples = []
                for source in (quoted, declared):
                    xml_schema = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:test" elementFormDefault="{source_default}">{global_child}{definition.format(source)}</xs:schema>'
                    validator = etree.XMLSchema(etree.fromstring(xml_schema.encode()))
                    samples.append([validator.validate(x) for x in xml])
                self.assertEqual(samples[0] != samples[1], differs)
                body = document('Атрибуты элемента root', {'Схема': definition.format(quoted)})
                count, findings = self.check(body, global_child+definition.format(declared), form=source_default)
                self.assertEqual(count, 1)
                self.assertEqual(bool(findings), differs)
                if differs:
                    self.assertIn('urn:test', str(findings))
                    self.assertIn('без пространства имён', str(findings))

        body = document('Атрибуты элемента root', {'Схема': definition.format(local.format(''))})
        notes = []
        self.assertEqual(self.check(body, global_child+definition.format(ref), notes=notes), (1, []))
        self.assertTrue(any('child' in note and 'пространств' in note for note in notes), notes)
        for namespace, differs in [('urn:test', False), ('urn:other', True)]:
            body = document('Атрибуты элемента root', {'Пространство имен': namespace,
                            'Схема': definition.format(local.format(' form="qualified"'))})
            notes = []
            count, findings = self.check(body, global_child+definition.format(ref), notes=notes)
            self.assertEqual((count, bool(findings), notes), (1, differs, []))

    def check(self, body, declarations, use_archive=True, *, form='unqualified', notes=None):
        file = io.BytesIO()
        with zipfile.ZipFile(file, "w") as archive:
            archive.writestr("main.xsd", f'<xs:schema xmlns:xs="{XS}" xmlns="urn:test" targetNamespace="urn:test" elementFormDefault="{form}">{declarations}</xs:schema>')
        with zipfile.ZipFile(file) as archive:
            schema = Schema(archive, "main.xsd", "root")
            schema.field(schema.root)
        revision = {"schema_path": "main.xsd", "root": "root"}
        return audit(revision, body, schema.fields, archive_path=file if use_archive else None, notes=notes)

    def test_typed_child_inline_length_changes_with_archive(self):
        open_element = '<xs:element name="ref" minOccurs="0"/>'
        restricted = '<xs:element name="ref" minOccurs="0"><xs:simpleType><xs:restriction base="xs:string"><xs:maxLength value="3"/></xs:restriction></xs:simpleType></xs:element>'
        template = '<xs:element name="root" type="Record"/><xs:complexType name="Record"><xs:sequence>{}</xs:sequence></xs:complexType>'
        body = document("Атрибуты элемента Record/ref", {"Схема": open_element})
        count, result = self.check(body, template.format(restricted))
        self.assertEqual(count, 1)
        self.assertEqual(len(result), 1)
        self.assertIn("maxLength", result[0]["xsd"])
        self.assertIn("Record/ref", result[0]["xlocation"])
        self.assertEqual(self.check(body, template.format(open_element)), (1, []))

    def test_contradictory_displayed_type_and_pattern(self):
        declaration = '<xs:attribute name="externalId" type="xs:string" use="required"/>'
        schema = '<xs:element name="root"><xs:complexType>' + declaration + '</xs:complexType></xs:element>'
        rows = {"Тип": "Identifier", "Свойства": [["use", "required"]],
                "Ограничения": [["Вид", "Значение"], ["Паттерн строки", "[A-Z]+"]], "Схема": declaration}
        body = document("Атрибут root/@externalId", rows)
        for use_archive in [False, True]:
            count, result = self.check(body, schema, use_archive)
            self.assertEqual(count, 1)
            self.assertIn("Identifier", " ".join(i["spec"] for i in result))
            self.assertIn("[A-Z]+", " ".join(i["spec"] for i in result))
        del rows["Ограничения"]
        rows["Тип"] = "xs:string"
        self.assertEqual(self.check(document("Атрибут root/@externalId", rows), schema), (1, []))

    def test_documented_field_before_required_child_is_not_a_trailing_wildcard(self):
        extra = '<xs:element name="newFlag" type="xs:boolean" minOccurs="0"/>'
        template = '<xs:element name="root"><xs:complexType><xs:sequence>{}<xs:element name="id" type="xs:long"/><xs:any minOccurs="0" processContents="skip"/></xs:sequence></xs:complexType></xs:element>'
        body = document("Атрибуты элемента root", {"Дочерние элементы": "newFlag id", "Схема": template.format(extra)})
        count, result = self.check(body, template.format(""))
        self.assertEqual(count, 1)
        self.assertIn("newFlag", result[0]["spec"])
        self.assertIn("xs:any", result[0]["xsd"])
        self.assertEqual(self.check(body, template.format(extra)), (1, []))

    def test_unused_type_becomes_available(self):
        declaration = '<xs:simpleType name="External"><xs:restriction base="xs:string"/></xs:simpleType>'
        root = '<xs:element name="root" type="xs:string"/>'
        body = document("Атрибуты типа External", {"Схема": declaration})
        self.assertEqual(len(self.check(body, root)[1]), 1)
        self.assertEqual(self.check(body, root + declaration), (1, []))

    def test_choice_bound_is_checked_on_group_not_on_each_field(self):
        group = '<xs:complexType><xs:choice minOccurs="{}"><xs:element name="a" type="xs:string"/><xs:element name="b" type="xs:string"/></xs:choice></xs:complexType>'
        template = '<xs:element name="root">{}</xs:element>'
        body = document("Атрибуты элемента root", {"Дочерние элементы": "a b", "Схема": template.format(group.format(0))})
        count, result = self.check(body, template.format(group.format(1)))
        self.assertEqual((count, len(result)), (1, 1))
        self.assertIn("choice(a, b)[0..1]", result[0]["spec"])
        self.assertEqual(self.check(body, template.format(group.format(0))), (1, []))

    def test_named_type_uses_same_child_and_attribute_checks_as_its_instance(self):
        name = '<xs:attribute name="token" type="xs:string" use="{}"/>'
        definition = '<xs:complexType name="Record">' + name + '</xs:complexType>'
        root = '<xs:element name="root" type="Record"/>'
        headings = ["Name", "Type", "Use", "Default", "Fixed"]
        rows = {"Атрибуты": [headings, ["token", "xs:string", "required", "", ""]],
                "Схема": definition.format("optional")}
        doc = document("Атрибуты комплексного типа Record", rows)
        count, result = self.check(doc, root + definition.format("optional"))
        self.assertEqual((count, len(result)), (1, 1))
        self.assertIn("optional", result[0]["xsd"])
        rows["Схема"] = definition.format("required")
        self.assertEqual(self.check(document("Атрибуты комплексного типа Record", rows),
                                    root + definition.format("required")), (1, []))

    def test_cards_have_independent_expansion_budgets(self):
        declarations = '<xs:element name="root" type="xs:string"/>'
        body = ET.Element(W + "body")
        for i in range(100):
            name = f"Record{i}"
            definition = f'<xs:complexType name="{name}"><xs:sequence>' + ''.join(
                f'<xs:element name="v{j}" type="xs:string"/>' for j in range(500)) + '</xs:sequence></xs:complexType>'
            declarations += definition
            body.extend(document("Атрибуты типа " + name, {"Схема": definition}))
        self.assertEqual(self.check(body, declarations), (100, []))

    def test_unsupported_unused_card_is_not_counted_and_next_card_is_checked(self):
        root = '<xs:element name="root" type="xs:string"/>'
        tags = ('<xs:simpleType name="Tags"><xs:restriction base="xs:string">'
                '<xs:assertion test="true()"/></xs:restriction></xs:simpleType>')
        body = document("Атрибуты типа Tags", {"Схема": tags})
        body.extend(document("Атрибуты элемента root", {"Тип": "xs:int", "Схема": root}))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("main.xsd", f'<xs:schema xmlns:xs="{XS}">{root}{tags}</xs:schema>')
        with zipfile.ZipFile(archive) as bundle:
            schema = Schema(bundle, "main.xsd", "root")
            schema.field(schema.root)
        notes = []
        count, findings = audit({"schema_path": "main.xsd", "root": "root"}, body,
                                schema.fields, archive_path=archive, notes=notes)
        self.assertEqual(count, 1)
        self.assertEqual([finding["kind"] for finding in findings], ["Имя типа"])
        self.assertEqual(len(notes), 1)
        self.assertIn("Tags", notes[0])
        self.assertIn("assertion", notes[0])

    def test_expansion_releases_temporary_state_on_success_and_failure(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("main.xsd", f'<xs:schema xmlns:xs="{XS}"><xs:element name="root" type="xs:string"/>'
                            '<xs:simpleType name="Code"><xs:restriction base="xs:string"/></xs:simpleType>'
                            '<xs:simpleType name="Tags"><xs:list itemType="xs:string"/></xs:simpleType>'
                            '<xs:simpleType name="Unsupported"><xs:restriction base="xs:string">'
                            '<xs:assertion test="true()"/></xs:restriction></xs:simpleType></xs:schema>')
        with zipfile.ZipFile(archive) as bundle:
            schema = Schema(bundle, "main.xsd", "root")
            schema.field(schema.root)
        fields, declarations, count = schema.fields, schema.field_declarations, schema.expanded_fields
        metadata_counts = [len(mapping) for mapping in (schema.owner, schema.prefixes, schema.context)]
        expanded(schema, schema.symbol[('', 'simpleType', 'Code')])
        tags, _ = expanded(schema, schema.symbol[('', 'simpleType', 'Tags')])
        self.assertEqual(tags['listItem']['base'], 'xs:string')
        with self.assertRaisesRegex(ValueError, 'assertion'):
            expanded(schema, schema.symbol[('', 'simpleType', 'Unsupported')])
        self.assertIs(schema.fields, fields)
        self.assertIs(schema.field_declarations, declarations)
        self.assertEqual(schema.expanded_fields, count)
        self.assertEqual([len(mapping) for mapping in (schema.owner, schema.prefixes, schema.context)], metadata_counts)
