import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from audit_xml_examples import audit
from module_xsd import W


XSD = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:example">
  <xs:element name="request"><xs:complexType><xs:sequence>
    <xs:element name="number" type="xs:int"/>
  </xs:sequence></xs:complexType></xs:element>
  <xs:element name="reply" type="xs:string"/>
</xs:schema>'''
GOOD = '<request xmlns="urn:example"><number xmlns="">1</number></request>'


def body(*paragraphs):
    result = ET.Element(W + 'body')
    for content in paragraphs:
        p = ET.SubElement(result, W + 'p')
        ET.SubElement(ET.SubElement(p, W + 'r'), W + 't').text = content
    return result


class ExampleAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.archive = Path(temporary.name) / 'schemas.zip'
        self.revision = dict(number='6', environment='test', revision_id='revision',
                             root='request', namespace='urn:example', schema_path='main.xsd')
        self.write_schema(XSD)

    def write_schema(self, schema, **members):
        with zipfile.ZipFile(self.archive, 'w') as bundle:
            bundle.writestr('main.xsd', schema)
            for name, content in members.items():
                bundle.writestr(name, content)

    def check(self, *paragraphs):
        return audit(self.revision, body(*paragraphs), self.archive)

    def test_editing_a_document_or_schema_recalculates_findings(self):
        bad = GOOD.replace('>1<', '>invalid<')
        self.assertTrue(self.check('Пример XML-документа', bad)[1])
        self.assertEqual(self.check('Пример XML-документа', GOOD)[:2], (1, []))
        self.write_schema(XSD.replace('xs:int', 'xs:string'))
        self.assertEqual(self.check('Пример XML-документа', bad)[:2], (1, []))

    def test_exact_section_and_registered_root_even_when_other_root_is_globally_valid(self):
        count, found, _ = self.check('Тип сообщения 11: другой раздел', 'Пример', GOOD,
                                     'Тип сообщения 6: текущий раздел', 'Пример структуры запроса',
                                     '<reply xmlns="urn:example"><!--Optional:--><uuid>string</uuid></reply>',
                                     'Тип сообщения 7: следующий раздел', 'Пример', '<bad/>')
        self.assertEqual((count, len(found)), (1, 1))
        self.assertIn('reply', found[0]['spec'])
        self.assertEqual(found[0]['env'], 'test')
        self.assertIn('request', found[0]['xsd'])

    def test_templates_and_xsd_fragments_have_no_instance_verdict(self):
        count, found, notes = self.check('Тип сообщения 6: документ', 'Пример шаблона',
                                         '<request xmlns="urn:example"/>', 'Пример', XSD)
        self.assertEqual((count, found), (0, []))
        self.assertTrue(notes)

    def test_multiple_complete_examples_and_malformed_closing_tag(self):
        count, found, _ = self.check('Пример', '<?xml version="1.0"?>', GOOD,
                                     '<?xml version="1.0"?>', GOOD.replace('</request>', '</other>'))
        self.assertEqual((count, len(found)), (2, 1))
        self.assertIn('XML', found[0]['kind'])

    def test_archive_only_resolution_with_a_nested_relative_import(self):
        common = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:common"><xs:simpleType name="Code"><xs:restriction base="xs:int"/></xs:simpleType></xs:schema>'
        main = XSD.replace('targetNamespace=', 'xmlns:c="urn:common" targetNamespace=').replace(
            '<xs:element name="request">', '<xs:import namespace="urn:common" schemaLocation="types/common.xsd"/><xs:element name="request">').replace('type="xs:int"', 'type="c:Code"')
        self.write_schema(main, **{'types/common.xsd': common})
        self.assertEqual(self.check('Пример', GOOD)[:2], (1, []))
        self.write_schema(main.replace('types/common.xsd', 'file:///unavailable.xsd'))
        count, findings, notes = self.check('Пример', GOOD)
        self.assertEqual((count, findings), (0, []))
        self.assertTrue(notes)

    def test_signed_transport_is_not_checked_against_the_application_schema(self):
        import base64
        message = base64.b64encode(GOOD.encode()).decode()
        envelope = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                    '<s:Body><send><messageType>6</messageType><message>' + message
                    + '</message></send></s:Body></s:Envelope>')
        self.assertEqual(self.check('Пример запроса', envelope)[:2], (1, []))
        self.assertEqual(self.check('Пример запроса', envelope.replace('>6<', '>7<'))[0], 0)

    def test_readable_and_deduplicated_constraint_for_repeated_values(self):
        self.write_schema(XSD.replace('type="xs:int"', 'type="xs:int" maxOccurs="unbounded"'))
        bad = GOOD.replace('<number xmlns="">1</number>', '<number xmlns="">abc</number>' * 2)
        count, findings, _ = self.check('Пример XML-документа', bad)
        self.assertEqual((count, len(findings)), (1, 1))
        self.assertIn('abc', findings[0]['spec'])
        self.assertIn('xs:int', findings[0]['xsd'])
        self.assertIn('/request/number', findings[0]['location'])

    def test_explicit_boolean_in_template_contradicts_fixed_value(self):
        self.write_schema(XSD.replace('type="xs:int"', 'type="xs:boolean" fixed="true"'))
        _, findings, notes = self.check('Пример шаблона', GOOD.replace('>1<', '>false<'))
        self.assertTrue(notes)
        self.assertIn('false', findings[0]['spec'])
        self.assertIn('true', findings[0]['xsd'])
        self.assertFalse(self.check('Пример шаблона', GOOD.replace('>1<', '>true<'))[1])

    def test_dtd_is_not_followed_or_reported_as_a_schema_discrepancy(self):
        count, found, notes = self.check('Пример', '<!DOCTYPE request [<!ENTITY v "example">]>', GOOD)
        self.assertEqual((count, found), (0, []))
        self.assertTrue(notes)

    def test_nested_root_name_and_nonparagraph_bookmarks_do_not_split_example(self):
        recursive = XSD.replace('<xs:element name="number" type="xs:int"/>',
                                '<xs:any minOccurs="0" maxOccurs="unbounded" processContents="skip"/>')
        self.write_schema(recursive)
        doc = body('Пример', '<request xmlns="urn:example">', '<request>', '</request>', '</request>')
        doc.insert(4, ET.Element(W + 'bookmarkEnd'))
        self.assertEqual(audit(self.revision, doc, self.archive)[:2], (1, []))
