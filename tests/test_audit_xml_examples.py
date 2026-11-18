import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from audit_xml_examples import audit
from module_xsd import W
from schema import XS


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
    def test_exact_wsdl_imports_survive_example_and_fixed_validation(self):
        from schema import dependency_set
        from tests.test_wsdl_sources import WsdlSourcesTests

        case = WsdlSourcesTests()
        base, resources = case.branch_resources()
        for version in ('v1', 'v2'):
            url = base + version + '/root.xsd'
            resources[url] = resources[url].replace(b'"Common.xsd"', ('"'+base+version+'/Common.xsd"').encode())
            resources[url] = resources[url].replace(
                b'<xs:element name="response" type="t:Code"/>',
                b'<xs:attribute name="editable" type="xs:boolean"/>'
                b'<xs:element name="response"><xs:complexType><xs:simpleContent><xs:extension base="t:Code">'
                b'<xs:attribute ref="t:editable" fixed="false"/>'
                b'</xs:extension></xs:simpleContent></xs:complexType></xs:element>')
        library, objects = case.collect(resources)
        self.archive.write_bytes(objects[library['sha256']])
        for version in ('v1', 'v2'):
            ns = 'urn:' + version
            entry = next(s['path'] for s in library['schemas'] if s['namespace'] == ns)
            revision = dict(number='55', environment='test', revision_id=version, schema_path=entry,
                            root='response', namespace=ns, dependencies=dependency_set(library['schemas'], entry))
            for text, value, valid in ((version, 'false', True), ('invalid', 'false', False), (version, 'true', False)):
                with self.subTest(version=version, text=text, value=value):
                    xml = f'<response xmlns="{ns}" xmlns:t="{ns}" t:editable="{value}">{text}</response>'
                    document = body('Тип сообщения 55: запрос', 'Пример', xml)
                    count, findings, notes = audit(revision, document, self.archive)
                    self.assertEqual((count, bool(findings), notes), (1, not valid, []))

    def test_xml_character_spaces_survive_word_paragraph_boundaries(self):
        self.write_schema('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:example"><xs:element name="request"><xs:simpleType><xs:restriction base="xs:string"><xs:length value="5"/></xs:restriction></xs:simpleType></xs:element></xs:schema>')
        self.assertEqual(self.check('  Пример  ', '<request xmlns="urn:example">a ', ' b</request>')[:2], (1, []))

    def test_string_placeholder_hides_datatype_error_but_not_unknown_element(self):
        count, findings, notes = self.check('Пример', GOOD.replace('>1<', '>string<'))
        self.assertEqual((count, findings), (1, []))
        self.assertTrue(notes)
        count, findings, _ = self.check('Пример', GOOD.replace('<number xmlns="">1</number>', '<unknown xmlns="">string</unknown>'))
        self.assertEqual((count, len(findings)), (1, 1))
        self.assertTrue(findings[0]['location'].endswith('/request/unknown'), findings)

    def test_string_placeholder_does_not_hide_children_in_a_simple_typed_element(self):
        _, findings, _ = self.check('Пример', GOOD.replace('>1<', '>string<extra/><'))
        self.assertTrue(any('дочерн' in item['xsd'] or 'Element content' in item['xsd'] for item in findings), findings)

    def test_namespace_collision_diagnostic_preserves_both_qnames(self):
        for qualified in (False, True):
            self.write_schema(XSD.replace('name="number"', 'name="number" form="qualified"') if qualified else XSD)
            wrong = GOOD if qualified else GOOD.replace(' xmlns=""', '')
            _, findings, _ = self.check('Пример', wrong)
            self.assertEqual(len(findings), 1)
            finding = findings[0]
            self.assertIn('{urn:example}number', str(finding))
            self.assertIn('number (без пространства имён)', str(finding))
            self.assertTrue(finding['location'].endswith('/request/number'))

    def test_rebound_prefix_diagnostic_uses_value_of_invalid_element(self):
        schema = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
            xmlns:a="urn:example" xmlns:b="urn:other" targetNamespace="urn:example">
          <xs:import namespace="urn:other" schemaLocation="other.xsd"/>
          <xs:element name="item" type="xs:string"/>
          <xs:element name="request"><xs:complexType><xs:sequence>
            <xs:element ref="a:item"/><xs:element ref="b:item"/><xs:element ref="a:item"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        other = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:other">
          <xs:element name="item" type="xs:integer"/>
        </xs:schema>'''
        self.write_schema(schema, **{'other.xsd': other})
        for last_value in ('three', 'string'):
            with self.subTest(last_value=last_value):
                example = ('<request xmlns="urn:example" xmlns:p="urn:example">'
                           '<p:item>one</p:item><p:item xmlns:p="urn:other">bad</p:item>'
                           f'<p:item>{last_value}</p:item></request>')
                count, findings, notes = self.check('Пример', example)
                self.assertEqual((count, len(findings), notes), (1, 1, []))
                self.assertTrue(findings[0]['location'].endswith('/request/item'))
                self.assertIn('«bad»', findings[0]['spec'])
                self.assertIn('integer', findings[0]['xsd'])

    def test_xml_declaration_boundary_respects_misc_and_literal_text(self):
        from audit_xml_examples import _instances, _xml_bytes
        declaration = '<?xml version="1.0"?>'
        stylesheet = '<?xml-stylesheet href="style.xsl" encoding="windows-1251"?>'
        for interior in ([stylesheet], ['<!--', declaration, '-->'],
                         ['<![CDATA[', declaration, ']]>']):
            with self.subTest(interior=interior):
                parts = ['<request>', *interior, '</request>']
                self.assertEqual(list(_instances([*parts, '<next/>'])),
                                 [('\n'.join(parts), None), ('<next/>', None)])
        self.assertEqual(_xml_bytes(stylesheet + '<next/>'), (stylesheet + '<next/>').encode())
        self.assertEqual(list(_instances(['<broken>', declaration, '<next/>'])),
                         [('<broken>', None), (declaration + '\n<next/>', None)])

    def test_example_boundary_respects_comments_cdata_quotes_and_truncation(self):
        self.write_schema('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:example"><xs:element name="request"/></xs:schema>')
        root = '<request xmlns="urn:example"'
        for parts in ([root+'>', '<!-- example </request>', 'continues -->', '</request>'],
                      [root+'>', '<![CDATA[ example </request>', 'continues ]]>', '</request>'],
                      [root+' code="/>', 'still the same attribute">', '</request>']):
            with self.subTest(parts=parts):
                self.assertEqual(self.check('Пример', *parts, root+'/>')[:2], (2, []))
        self.assertEqual(len(self.check('Пример', root+'>', '<!-- example </request>')[1]), 1)

    def test_nested_chameleon_dependencies_use_including_namespace(self):
        xs='http://www.w3.org/2001/XMLSchema'
        main=XSD.replace('targetNamespace=', 'xmlns:t="urn:example" targetNamespace=').replace(
            '<xs:element name="request">','<xs:include schemaLocation="types.xsd"/><xs:element name="request">').replace('type="xs:int"','type="t:Code"')
        self.write_schema(main, **{'types.xsd':f'<xs:schema xmlns:xs="{xs}"><xs:include schemaLocation="defs.xsd"/></xs:schema>',
            'defs.xsd':f'<xs:schema xmlns:xs="{xs}" targetNamespace="urn:example"><xs:simpleType name="Code"><xs:restriction base="xs:int"/></xs:simpleType></xs:schema>'})
        self.assertEqual(self.check('Пример',GOOD)[:2],(1,[]))

    def test_xml_declaration_is_validated_before_unicode_encoding_is_normalized(self):
        for encoding in ('UTF-8', 'windows-1251'):
            for separator in ('', '\n'):
                declaration = '<?xml version="1.0" encoding="'+encoding+'"?>'
                paragraphs = [declaration, GOOD] if separator else [declaration+GOOD]
                self.assertEqual(self.check('Пример', *paragraphs)[:2], (1, []))
        for declaration in ('<?xml version="oops"?>', '<?xml encoding="UTF-8"?>', '<?xml version="1.0" standalone="oops"?>'):
            for paragraphs in ([declaration+GOOD], [declaration, GOOD]):
                count, findings, _ = self.check('Пример', *paragraphs)
                self.assertEqual((count, len(findings)), (1, 1), declaration)
                self.assertIn('пример 1', findings[0]['location'])
                self.assertIn('Строка 1', findings[0]['spec'])

    def test_xml_declaration_can_span_word_paragraphs(self):
        for encoding in ('UTF-8','windows-1251'):
            count,findings,_=self.check('Пример','<?xml version="1.0"',' encoding="'+encoding+'"?>',GOOD)
            self.assertEqual((count,findings),(1,[]))
        count,findings,_=self.check('Пример','<?xml version="oops"',' encoding="UTF-8"?>',GOOD)
        self.assertEqual((count,len(findings)),(1,1))
        count,findings,_=self.check('Пример','<?xml version="1.0"',GOOD)
        self.assertEqual((count,len(findings)),(1,1))

    def test_prolog_comments_and_processing_instructions_keep_example_counts(self):
        for prefix in ('<!-- note -->', '<?annotation text?>',
                       '<?xml version="1.0"?><!-- note --><?annotation text?>',
                       '<!-- misleading <other/> -->'):
            for value, expected in ((GOOD, 0), (GOOD.replace('>1<', '>bad<'), 1)):
                for parts in ((prefix + value,), (prefix, value)):
                    with self.subTest(prefix=prefix, parts=parts):
                        count, findings, _ = self.check('Пример', *parts)
                        self.assertEqual((count, len(findings)), (1, expected))

    def test_required_attribute_diagnostic_links_to_attribute(self):
        self.write_schema(XSD.replace("</xs:sequence>", '</xs:sequence><xs:attribute name="stamp" use="required"/>'))
        _, findings, _ = self.check("Пример", GOOD)
        self.assertTrue(findings[0]["location"].endswith("/request/@stamp"), findings)

    def test_locally_declared_child_prefix_keeps_required_attribute_path(self):
        self.write_schema('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:example"><xs:element name="request"><xs:complexType><xs:sequence><xs:element name="child" form="qualified"><xs:complexType><xs:attribute name="stamp" use="required"/></xs:complexType></xs:element></xs:sequence></xs:complexType></xs:element></xs:schema>')
        _, findings, _ = self.check("Пример", '<request xmlns="urn:example"><local:child xmlns:local="urn:example"/></request>')
        self.assertTrue(findings[0]["location"].endswith("/request/child/@stamp"), findings)

    def test_fixed_attribute_ref_matches_validator_even_in_invalid_template(self):
        main = XSD.replace('targetNamespace=', 'xmlns:a="urn:attrs" targetNamespace=').replace(
            '<xs:element name="request">', '<xs:import namespace="urn:attrs" schemaLocation="attrs.xsd"/><xs:element name="request">').replace(
            '</xs:sequence>', '</xs:sequence><xs:attribute ref="a:editable" fixed="false"/>')
        attrs = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:attrs"><xs:attribute name="editable" type="xs:boolean"/></xs:schema>'
        self.write_schema(main, **{"attrs.xsd": attrs})
        for value, invalid in (("true",True), ("1",True), ("false",False), ("0",False)):
            for template in (False,True):
                xml = GOOD.replace('xmlns="urn:example"', 'xmlns="urn:example" xmlns:a="urn:attrs" a:editable="'+value+'"')
                if template: xml = xml.replace(">1<", ">string<")
                _, findings, _ = self.check("Пример шаблона" if template else "Пример", xml)
                self.assertEqual(bool(findings), invalid, (value,template,findings))

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

    def test_word_break_separates_complete_examples_in_one_paragraph(self):
        bad = GOOD.replace('>1<', '>NaN<')
        doc = body('Пример', GOOD)
        run = doc[-1].find(W + 'r')
        ET.SubElement(run, W + 'br')
        ET.SubElement(run, W + 't').text = bad
        result = audit(self.revision, doc, self.archive)
        self.assertEqual(result, self.check('Пример', GOOD, bad))
        count, found, notes = result
        self.assertEqual((count, len(found), notes), (2, 1, []))
        self.assertTrue(found[0]['location'].endswith('пример 2 — /request/number'), found)
        self.assertIn('«NaN»', found[0]['spec'])

    def test_shared_paragraph_boundary_respects_comments_cdata_and_quoted_text(self):
        from audit_xml_examples import _instances

        first = '<request code="/> >"><!-- fake </request> --><![CDATA[fake </request>]]>\n</request>'
        second = '<?xml version="1.0"?><next/>'
        self.assertEqual(list(_instances([first + '\n' + second + '<last/>'])),
                         [(first, None), (second, None), ('<last/>', None)])

    def test_xml_epilog_keeps_syntax_diagnostics_and_next_declaration(self):
        first = GOOD + '<!-- trailing note --><?note trailing?>'
        later = '<?xml version="1.0"?>' + GOOD
        self.assertEqual(self.check('Пример', first + '\n' + later), self.check('Пример', first, later))
        for epilog in (' unexpected text', '<!-- unfinished'):
            with self.subTest(epilog=epilog):
                count, found, _ = self.check('Пример', GOOD + epilog)
                self.assertEqual((count, len(found)), (1, 1))
                self.assertIn('синтаксис', found[0]['action'])

    def test_four_byte_root_ends_before_the_next_example(self):
        self.write_schema('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                          '<xs:element name="r" type="xs:string"/></xs:schema>')
        self.revision.update(root='r', namespace='')
        doc = body('Пример', '<r/>')
        run = doc[-1].find(W + 'r')
        ET.SubElement(run, W + 'br')
        ET.SubElement(run, W + 't').text = '<s/>'
        result = audit(self.revision, doc, self.archive)
        self.assertEqual(result, self.check('Пример', '<r/>', '<s/>'))
        self.assertEqual(result[0], 2)
        self.assertIn('Корень примера s.', result[1][0]['spec'])

    def test_xml_epilog_can_continue_into_the_next_word_paragraph(self):
        bad = GOOD.replace('>1<', '>NaN<')
        expected = self.check('Пример', GOOD, bad)
        for start, finish in [('<!-- notes', 'continued -->'), ('<?note first', 'next?>')]:
            with self.subTest(start=start):
                doc = body('Пример', GOOD + '\n' + start, finish)
                run = doc[-1].find(W + 'r')
                ET.SubElement(run, W + 'br')
                ET.SubElement(run, W + 't').text = bad
                self.assertEqual(audit(self.revision, doc, self.archive), expected)

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

    def test_embedded_and_base64_soap_errors_have_identical_payload_locations(self):
        import base64
        from review_field_links import attach_targets
        bad = GOOD.replace('>1<', '>banana<')
        envelope = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                    '<s:Body><send><messageType>6</messageType><message>{}</message>'
                    '</send></s:Body></s:Envelope>')
        results = []
        for payload in (bad, base64.b64encode(bad.encode()).decode()):
            count, findings, _ = self.check('Пример запроса', envelope.format(payload))
            self.assertEqual((count, len(findings)), (1, 1))
            finding = attach_targets(findings[0], [{'path': 'request'}, {'path': 'request/number'}])
            self.assertEqual(finding['field_paths'], ['request/number'])
            self.assertIn('banana', finding['spec'])
            results.append((finding['location'], finding['spec'], finding['xsd']))
        self.assertEqual(results[0], results[1])

    def test_nested_application_message_does_not_hide_soap_validation(self):
        import base64
        self.write_schema(f'''<xs:schema xmlns:xs="{XS}" targetNamespace="urn:example" elementFormDefault="qualified">
          <xs:element name="request"><xs:complexType><xs:sequence><xs:element name="message">
            <xs:complexType><xs:sequence><xs:element name="messageType" type="xs:int"/>
              <xs:element name="status"><xs:simpleType><xs:restriction base="xs:string">
                <xs:enumeration value="OK"/>
              </xs:restriction></xs:simpleType></xs:element>
            </xs:sequence></xs:complexType>
          </xs:element></xs:sequence></xs:complexType></xs:element>
        </xs:schema>''')
        invalid = '<request xmlns="urn:example"><message><messageType>6</messageType><status>BAD</status></message></request>'
        valid = invalid.replace('>BAD<', '>OK<')
        operation = '<api:send><api:messageType>{}</api:messageType><api:message>{}</api:message></api:send>'
        envelope = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:api="urn:operation">'
                    '<s:Body>{}</s:Body></s:Envelope>')
        expected = self.check('Пример запроса', invalid)
        self.assertEqual((expected[0], len(expected[1])), (1, 1))
        self.assertIn('BAD', expected[1][0]['spec'])
        for payload in (invalid, base64.b64encode(invalid.encode()).decode()):
            with self.subTest(payload=payload):
                self.assertEqual(self.check('Пример запроса', envelope.format(operation.format(6, payload))), expected)
        self.assertEqual(self.check('Пример запроса', envelope.format(operation.format(6, valid))), (1, [], []))
        self.assertEqual(self.check('Пример запроса', envelope.format(operation.format(7, valid))),
                         (0, [], ['Номер сообщения внутри SOAP не подтверждён.']))
        self.assertEqual(self.check('Пример запроса', envelope.format(operation.format(6, valid) * 2)),
                         (0, [], ['Не удалось однозначно выделить поле message из SOAP Body.']))
        for prefix, number in (('<api:messageType/>', '6'), ('', '6<api:part>7</api:part>')):
            with self.subTest(prefix=prefix, number=number):
                transport = operation.format(number, valid).replace('<api:send>', '<api:send>' + prefix)
                self.assertEqual(self.check('Пример запроса', envelope.format(transport)),
                                 (0, [], ['Номер сообщения внутри SOAP не подтверждён.']))

    def test_soap_message_ignores_comments_and_processing_instructions(self):
        import base64
        encoded = base64.b64encode(GOOD.encode()).decode()
        envelope = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                    '<s:Body><send><messageType>6</messageType><message>{}</message>'
                    '</send></s:Body></s:Envelope>')
        for marker in ('<!-- annotation -->', '<?annotation text?>'):
            for payload in (marker + encoded, encoded[:7] + marker + encoded[7:],
                            marker + GOOD + marker):
                with self.subTest(payload=payload):
                    self.assertEqual(self.check('Пример запроса', envelope.format(payload)), (1, [], []))
        count, findings, notes = self.check('Пример запроса', envelope.format(GOOD + '<!-- -->' + GOOD))
        self.assertEqual((count, findings), (0, []))
        self.assertIn('В SOAP message несколько дочерних элементов.', notes)

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
