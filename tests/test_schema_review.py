import unittest
from xml.etree import ElementTree as ET

from module_xsd import W
from schema_review import compare_schema_location


class SchemaLocationTests(unittest.TestCase):
    def test_literal_fixed_in_imported_facets_is_not_a_fixed_property(self):
        import tempfile
        import zipfile
        from pathlib import Path
        from review_field_links import attach_schema_sources
        xs = 'http://www.w3.org/2001/XMLSchema'
        main = f'<xs:schema xmlns:xs="{xs}" xmlns:t="urn:types"><xs:import namespace="urn:types" schemaLocation="types.xsd"/><xs:element name="request" type="t:Code" fixed="fixed"/></xs:schema>'
        imported = f'<xs:schema xmlns:xs="{xs}" targetNamespace="urn:types"><xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:pattern value="fixed"/><xs:enumeration value="fixed"/></xs:restriction></xs:simpleType></xs:schema>'
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'schemas.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('main.xsd', main)
                bundle.writestr('types.xsd', imported)
            for kind, rule, facets, expected in [
                ('Перечень значений', 'Перечислены fixed.', [], 'types.xsd'),
                ('Ограничения значения', 'pattern=fixed', [], 'types.xsd'),
                ('XML-пример', 'Допустимые значения: fixed.', ['enumeration'], 'types.xsd'),
                ('XML-пример', 'Шаблон значения в XSD: fixed.', ['pattern'], 'types.xsd'),
                ('Свойство поля', 'fixed = fixed', [], 'main.xsd'),
                ('XML-пример', 'Фиксированное значение: fixed.', [], 'main.xsd'),
                ('XML-пример', 'Фиксированное значение: pattern.', [], 'main.xsd'),
            ]:
                with self.subTest(kind=kind, rule=rule):
                    finding = dict(kind=kind, xsd=rule, rule_facets=facets, xlocation='main.xsd · request', field_paths=['request'])
                    result = attach_schema_sources(dict(schema_path='main.xsd', root='request'), [finding], archive)[0]
                    self.assertEqual(result['xsd_source']['path'], expected)

    def test_unused_unsupported_card_becomes_coverage_note_in_revision_review(self):
        import hashlib
        import io
        import tempfile
        import zipfile
        from pathlib import Path
        from schema import Schema, XS
        from schema_review import compare_revision
        from tests.test_audit_individual_tables import document
        root = '<xs:element name="root" type="xs:string"/>'
        tags = ('<xs:simpleType name="Tags"><xs:restriction base="xs:string">'
                '<xs:assertion test="true()"/></xs:restriction></xs:simpleType>')
        body = document('Атрибуты типа Tags', {'Схема': tags})
        body.extend(document('Атрибуты элемента root', {'Тип': 'xs:int', 'Схема': root}))
        with tempfile.TemporaryDirectory() as temporary:
            objects = Path(temporary)
            archive_bytes, doc_bytes = io.BytesIO(), io.BytesIO()
            with zipfile.ZipFile(archive_bytes, 'w') as bundle:
                bundle.writestr('main.xsd', f'<xs:schema xmlns:xs="{XS}">{root}{tags}</xs:schema>')
            with zipfile.ZipFile(doc_bytes, 'w') as document_zip:
                wrapper = ET.Element(W + 'document')
                wrapper.append(body)
                document_zip.writestr('word/document.xml', ET.tostring(wrapper))
            archive_digest = hashlib.sha256(archive_bytes.getvalue()).hexdigest()
            doc_digest = hashlib.sha256(doc_bytes.getvalue()).hexdigest()
            (objects / archive_digest).write_bytes(archive_bytes.getvalue())
            (objects / doc_digest).write_bytes(doc_bytes.getvalue())
            with zipfile.ZipFile(archive_bytes) as bundle:
                schema = Schema(bundle, 'main.xsd', 'root')
                schema.field(schema.root)
            revision = dict(number='6', environment='test', revision_id='revision', status='matched',
                            schema_path='main.xsd', root='root', archive_sha256=archive_digest, document_sha256=doc_digest)
            review = compare_revision(revision, schema.fields, objects)
        self.assertEqual(review['tables'], 1)
        self.assertEqual([finding['kind'] for finding in review['findings']], ['Имя типа'])
        self.assertTrue(any('Tags' in note and 'assertion' in note for note in review['notes']))

    def test_imported_base_facet_has_its_exact_bytes_and_preserves_field_link(self):
        import hashlib
        import tempfile
        import zipfile
        from pathlib import Path
        from review_field_links import attach_schema_sources
        xs = 'http://www.w3.org/2001/XMLSchema'
        main = f'<xs:schema xmlns:xs="{xs}" xmlns:t="urn:types"><xs:import namespace="urn:types" schemaLocation="types/derived.xsd"/><xs:element name="request" type="t:Derived"/></xs:schema>'
        derived = f'<xs:schema xmlns:xs="{xs}" xmlns:b="urn:base" targetNamespace="urn:types"><xs:import namespace="urn:base" schemaLocation="base.xsd"/><xs:simpleType name="Derived"><xs:restriction base="b:Code"><xs:maxLength value="10"/></xs:restriction></xs:simpleType></xs:schema>'
        base = f'<xs:schema xmlns:xs="{xs}" targetNamespace="urn:base"><xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:pattern value="[A-Z]+"/><xs:maxLength value="20"/></xs:restriction></xs:simpleType></xs:schema>'
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'schema.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                for path, raw in [('main.xsd', main), ('types/derived.xsd', derived), ('types/base.xsd', base)]:
                    bundle.writestr(path, raw)
            finding = dict(kind='Ограничение значения', xsd='pattern=[A-Z]+',
                           xlocation='main.xsd · request', field_paths=['request'])
            length = dict(finding, kind='Ограничение длины', xsd='maxLength=10')
            unused = dict(finding, xlocation='types/derived.xsd — Derived', field_paths=[])
            result, length_result, unused_result = attach_schema_sources(dict(schema_path='main.xsd', root='request'), [finding, length, unused], archive)
        digest = hashlib.sha256(base.encode()).hexdigest()
        self.assertEqual(result['xsd_source'], dict(path='types/base.xsd', sha256=digest, download='sources/'+digest+'.xsd'))
        self.assertEqual(result['xlocation'], 'types/base.xsd · request')
        self.assertEqual(result['field_paths'], ['request'])
        self.assertEqual(length_result['xsd_source']['path'], 'types/derived.xsd')
        self.assertNotIn('xsd_sources', length_result)
        self.assertEqual(unused_result['xsd_source'], result['xsd_source'])

    def test_original_10910_and_111_restrictions_link_exact_imported_xsd(self):
        import json
        import hashlib
        import zipfile
        from pathlib import Path
        from schema_review import compare_revision
        root = Path(__file__).resolve().parents[1]
        rows = json.loads((root / 'data/catalog.json').read_bytes())['revisions']
        for number, path, filename in [
            ('10910', 'eventLife/benefitInfo/benefit1/insuredInfo/category', 'InsuredCategories.xsd'),
            ('111', 'changeElnNotice/emplRegNo2', 'Organization.xsd')]:
            for env in ('prod', 'test'):
                revision = next(row for row in rows if row['number'] == number and row['environment'] == env and row.get('status') == 'matched')
                fields = json.loads((root / 'data/fields' / (revision['revision_id'] + '.json')).read_bytes())['fields']
                review = compare_revision(revision, fields, root / 'state/objects')
                found = [f for f in review['findings'] if path in f['field_paths'] and f.get('xsd_source', {}).get('path', '').endswith('/' + filename)]
                self.assertTrue(found, (number, env, review['findings']))
                for finding in found:
                    source = finding['xsd_source']
                    with zipfile.ZipFile(root / 'state/objects' / revision['archive_sha256']) as bundle:
                        self.assertEqual(hashlib.sha256(bundle.read(source['path'])).hexdigest(), source['sha256'])

    def test_rechecks_current_doc_and_archive_path(self):
        body = ET.Element(W + "body")
        row = ET.SubElement(ET.SubElement(body, W + "tbl"), W + "tr")
        for value in ("Расположение файла xml-схемы", r"\xsd\v3.0\request.xsd"):
            cell = ET.SubElement(row, W + "tc")
            ET.SubElement(cell, W + "t").text = value
        count, findings = compare_schema_location(body, "Схемы/v3.0/Request.xsd")
        self.assertEqual(count, 1)
        self.assertEqual(findings[0]["kind"], "Путь к XSD")
        self.assertEqual(compare_schema_location(body, "Новая поставка/v3.0/request.xsd"), (1, []))
        self.assertEqual(compare_schema_location(ET.Element(W + "body"), "request.xsd"), (0, []))
