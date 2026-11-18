import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from catalog import derive
from schema import Schema, XS
from sync import sha, encode, inspect_file
from tests.test_schema_recovery import archive
from tests import test_audit_individual_tables as individual
from tests.test_audit_module_tables import Comparison, field
from tests import test_audit_xml_examples as examples


class SchemaReviewTests(unittest.TestCase):
    def test_individual_namespace_missing_empty_and_explicit_are_distinct(self):
        schemas = []
        for namespace in ('', 'urn:other'):
            target = f' targetNamespace="{namespace}"' if namespace else ''
            raw = archive({'main.xsd': f'<xs:schema xmlns:xs="{XS}"{target}><xs:element name="result" type="xs:string"/></xs:schema>'})
            schemas.append(dict(environment='test', kind='zip', url=namespace+'.zip', sha256=sha(raw), **inspect_file(raw, 'zip')))
        for value, status, expected in ((None, 'review', None), ('', 'matched', None), ('urn:other', 'matched', 'urn:other')):
            structure = [['Корневой элемент', 'result']]
            if value is not None:
                structure.append(['Пространство имен', value])
            document = dict(environment='test', kind='docx', url='https://example.test/Тип_сообщения_999.docx', label='v1', sha256='doc', structure=structure)
            for embedded in (False, True):
                with self.subTest(namespace=value, embedded=embedded):
                    if embedded:
                        document['embedded_archives'] = [dict(schema, container='word/document.xml#schema-'+str(index), filename='main.xsd') for index, schema in enumerate(schemas)]
                    result = derive(dict(entries=[document, *([] if embedded else schemas)]))
                    row = result['revisions'][0]
                    self.assertEqual(row['status'], status)
                    self.assertEqual(row['namespace'], expected)
                    if status == 'review':
                        self.assertIn('{urn:other}result', str(result['review']))
                        self.assertIn('без пространства', str(result['review']))

    def test_registry_retains_exact_document_bytes(self):
        table = [['Код типа сообщения', 'Наименование', 'Описание', 'Приложение'], ['1', 'Title', 'Description', '2']]
        row = dict(environment='test', kind='docx', url='https://example.test/list.docx', sha256='document', label='v1.0', registry_tables=[table])
        item = derive(dict(entries=[row]))['registry'][0]
        self.assertEqual((item.get('document_sha256'), item.get('document_url')), ('document', row['url']))

    def test_blank_namespace_matches_unnamespaced_schema(self):
        raw = archive({'main.xsd': f'<xs:schema xmlns:xs="{XS}"><xs:element name="response" type="xs:string"/></xs:schema>'})
        doc = dict(environment='test', kind='docx', url='https://example.test/Тип_сообщения_999.docx', sha256='doc', label='v1.0', structure=[['Корневой элемент', 'response'], ['Пространство имен', '']])
        zipped = dict(environment='test', kind='zip', url='https://example.test/s.zip', sha256=sha(raw), **inspect_file(raw, 'zip'))
        item = derive(dict(entries=[doc, zipped]))['revisions'][0]
        self.assertEqual(item['status'], 'matched')
        self.assertIsNone(item['namespace'])

    def test_combined_document_archive_can_use_wsdl_dependencies(self):
        from wsdl_sources import bridge, _archive
        main = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:include schemaLocation="own.xsd"/><xs:element name="response" type="xs:string"/></xs:schema>'.encode()
        own = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:import namespace="urn:common" schemaLocation="Common.xsd"/></xs:schema>'.encode()
        common = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:common"/>'.encode()
        raw, support_raw = _archive({'main.xsd': main, 'own.xsd': own}), _archive({'Common.xsd': common})
        objects = {sha(raw): raw, sha(support_raw): support_raw}
        doc = dict(environment='test', url='doc', embedded_archives=[dict(container='doc#combined', sha256=sha(raw))])
        support = dict(environment='test', sha256=sha(support_raw), source_url='wsdl', source_files=[])
        result = bridge(doc, support, objects.__getitem__)
        self.assertEqual(len(result), 1)
        from schema import dependency_set
        self.assertEqual(len(dependency_set(result[0]['schemas'], 'document/main.xsd')), 3)

    def test_flat_table_distinguishes_attribute_and_element(self):
        fields = [field('root', 'root', -1, 'Root', [1, 2]), field('code', 'root/code', 0, 'xs:string'), field('@code', 'root/@code', 0, 'xs:int')]
        fields[2]['kind'] = 'attribute'
        comparison = Comparison(dict(schema_path='main.xsd'), fields)
        comparison.flat(fields[0], 'Table', [['code', 'xs:string', '', '1'], ['@code', 'xs:int', '', '1']])
        self.assertEqual(comparison.findings, [])

    def test_named_local_element_namespace_is_compared(self):
        source = '<xs:element name="root"><xs:complexType><xs:sequence><xs:element name="code" form="unqualified" type="xs:string"/></xs:sequence></xs:complexType></xs:element>'
        card = individual.document('Атрибуты элемента root', {'Пространство имен': 'urn:test', 'Схема': source.replace('unqualified', 'qualified')})
        count, issues = individual.AuditIndividualTablesTests().check(card, source)
        self.assertEqual(count, 1)
        self.assertTrue(any(issue['kind'] == 'Модель содержимого' for issue in issues))

    def test_nillable_lexical_equivalents_are_equal(self):
        source = '<xs:element name="root" type="xs:string" nillable="1"/>'
        card = individual.document('Атрибуты элемента root', {'Схема': source.replace('"1"', '"true"'), 'Свойства': [['nillable', 'true']]})
        self.assertEqual(individual.AuditIndividualTablesTests().check(card, source), (1, []))

    def test_appendix_example_does_not_belong_to_last_message(self):
        fixture = examples.ExampleAuditTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.revision.update(number='2', source_type='module')
        result = fixture.check('Тип сообщения 2: Ответ', 'Пример', examples.GOOD, 'Приложение 1', 'Пример', '<independent/>')
        self.assertEqual(result[:2], (1, []))

    def test_inbox_rename_prefers_known_revision_across_all_paths(self):
        import json
        from import_inbox import collect
        from schema import dependency_set
        from tests.test_source_collection_regressions import individual_document
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            incoming = root / 'incoming/test'
            incoming.mkdir(parents=True)
            (root / 'data').mkdir()
            raw_doc = individual_document('response')
            (incoming / '999_v1.docx').write_bytes(raw_doc)
            schema = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>'
            for name, entry in [('a.zip', 'new.xsd'), ('b.zip', 'old.xsd')]:
                (incoming / name).write_bytes(archive({entry: schema}))
            info = inspect_file((incoming / 'b.zip').read_bytes(), 'zip')
            closure = dependency_set(info['schemas'], 'old.xsd')
            known_id = sha(encode(['test', '999', sha(raw_doc), sha(encode(closure))]))
            (root / 'data/catalog.json').write_text(json.dumps(dict(revisions=[dict(revision_id=known_id)])), 'utf8')
            tasks, _ = collect(root, ledger=dict(files={'incoming/test/999.docx': dict(revision_id=known_id)}))
            self.assertEqual(tasks[0]['archive'].name, 'b.zip')

    def test_inbox_renamed_document_keeps_one_schema_event(self):
        import json
        from import_inbox import process_inbox
        from tests import test_fix15_sources as fixtures
        from tests.test_source_collection_regressions import individual_document
        fixture = fixtures.FilenameImportReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        incoming = root / 'incoming/test'
        incoming.mkdir(parents=True)
        original = incoming / '999.docx'
        original.write_bytes(individual_document('response'))
        schema = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>'
        (incoming / 'b.zip').write_bytes(archive({'old.xsd': schema}))
        self.assertEqual(process_inbox(root)['new_revisions'], 1)
        original.rename(incoming / '999_v1.docx')
        (incoming / 'a.zip').write_bytes(archive({'new.xsd': schema}))
        self.assertEqual(process_inbox(root)['new_revisions'], 0)
        catalog = json.loads((root / 'data/catalog.json').read_bytes())
        self.assertEqual(len(catalog['revisions']), 1)
        self.assertEqual(len(catalog['updates']), 1)

    def test_inbox_blank_namespace_matches_unnamespaced_schema(self):
        from import_inbox import collect
        from tests.test_source_collection_regressions import individual_document
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            incoming = root / 'incoming/test'
            incoming.mkdir(parents=True)
            raw = individual_document('response')
            stream = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(stream, 'w') as changed:
                for item in original.infolist():
                    changed.writestr(item, original.read(item.filename).replace(b'urn:main', b''))
            (incoming / '999.docx').write_bytes(stream.getvalue())
            (incoming / 's.zip').write_bytes(archive({'root.xsd': f'<xs:schema xmlns:xs="{XS}"><xs:element name="response" type="xs:string"/></xs:schema>'}))
            tasks, _ = collect(root)
            self.assertEqual(tasks[0]['archive'].name, 's.zip')

    def test_individual_specification_appendix_header_keeps_its_example(self):
        fixture = examples.ExampleAuditTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result = fixture.check('Приложение № 10 к Спецификации', 'Пример XML-документа', examples.GOOD)
        self.assertEqual(result[:2], (1, []))

    def test_combined_bridge_replays_each_historical_format_and_versions_new_behavior(self):
        import json
        from sync import SITES, write
        from tests import test_source_collection_regressions as sources
        fixture = sources.SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        main = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:include schemaLocation="own.xsd"/><xs:element name="response" type="xs:string"/></xs:schema>'
        own = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:import namespace="urn:common" schemaLocation="Common.xsd"/></xs:schema>'
        common = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:common"/>'
        with zipfile.ZipFile(io.BytesIO(sources.document('combined'))) as original:
            doc = archive({'word/document.xml': original.read('word/document.xml'), 'word/embeddings/main.xsd': main, 'word/embeddings/own.xsd': own})
        support_raw = archive({'Common.xsd': common})
        digest = sha(support_raw)
        write(fixture.state / 'objects' / digest, support_raw)
        supports = []
        for env, page in SITES.items():
            fixture.resources[page.rsplit('/', 1)[0] + '/specs_sedo/file.docx'] = doc
            supports.append(dict(environment=env, kind='zip', source_type='wsdl', url='https://example.test/'+env, source_url='https://example.test/'+env, source_files=[], sha256=digest, **inspect_file(support_raw, 'zip')))
        for version in (1, 2, 3, 4):
            fixture.collect(1, wsdl_entries=supports, snapshot_format=version)
            original = (fixture.state / 'current.json').read_bytes()
            docs = [r for r in fixture.current()['entries'] if r['kind'] == 'docx']
            self.assertTrue(all(not r.get('module_matches') for r in docs), version)
            fixture.collect(2, wsdl_entries=supports, snapshot_format=version)
            self.assertEqual((fixture.state / 'current.json').read_bytes(), original)
        fixture.collect(3, wsdl_entries=supports)
        self.assertEqual(fixture.current()['format'], 5)
        docs = [r for r in fixture.current()['entries'] if r['kind'] == 'docx']
        self.assertTrue(all(len(r.get('module_matches', [])) == 1 for r in docs))
        original = (fixture.state / 'current.json').read_bytes()
        fixture.collect(4, wsdl_entries=supports, snapshot_format=5)
        self.assertEqual((fixture.state / 'current.json').read_bytes(), original)

    def test_bridge_prefers_document_combined_type_over_wsdl_substitute(self):
        from wsdl_sources import bridge, _archive
        from module_xsd import match_document
        main = f'<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" targetNamespace="urn:main"><xs:include schemaLocation="own.xsd"/><xs:element name="response" type="m:Code"/></xs:schema>'.encode()
        own = f'<xs:schema xmlns:xs="{XS}" xmlns:c="urn:common" targetNamespace="urn:main"><xs:import namespace="urn:common" schemaLocation="Common.xsd"/><xs:simpleType name="Code"><xs:restriction base="c:Code"/></xs:simpleType></xs:schema>'.encode()
        library_own = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:simpleType name="Code"><xs:restriction base="xs:int"/></xs:simpleType></xs:schema>'.encode()
        common = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:common"><xs:simpleType name="Code"><xs:restriction base="xs:string"/></xs:simpleType></xs:schema>'.encode()
        objects, embedded = {}, []
        for container, files in [('main', {'main.xsd': main}), ('own', {'own.xsd': own}), ('doc#combined', {'package-0/main.xsd': main, 'package-1/own.xsd': own})]:
            raw = _archive(files)
            objects[sha(raw)] = raw
            embedded.append(dict(container=container, filename=container+'.zip', sha256=sha(raw), **inspect_file(raw, 'zip')))
        raw = _archive({'own.xsd': library_own, 'Common.xsd': common})
        objects[sha(raw)] = raw
        support = dict(environment='test', sha256=sha(raw), source_url='wsdl', source_files=[])
        doc = dict(environment='test', url='doc', embedded_archives=embedded, module_bindings=[dict(number='999', root='response', namespace='urn:main')])
        doc['wsdl_archives'] = bridge(doc, support, objects.__getitem__)
        for row in doc['wsdl_archives']:
            objects[row['sha256']] = row['content']
        matches, issues = match_document(doc, [], objects.__getitem__)
        self.assertEqual(len(matches), 1, issues)
        with zipfile.ZipFile(io.BytesIO(objects[matches[0]['archive_sha256']])) as zipped:
            schema = Schema(zipped, matches[0]['schema_path'], 'response')
            schema.field(schema.root)
        self.assertEqual(schema.fields[0]['base'], 'xs:string')

    def test_local_qualified_parent_card_supplies_quoted_child_namespace(self):
        container = '<xs:element name="container" form="qualified"><xs:complexType><xs:sequence><xs:element name="code" form="unqualified" type="xs:string"/></xs:sequence></xs:complexType></xs:element>'
        source = '<xs:element name="root"><xs:complexType><xs:sequence>'+container+'</xs:sequence></xs:complexType></xs:element>'
        quoted = container.replace('name="code" form="unqualified"', 'name="code" form="qualified"')
        card = individual.document('Атрибуты элемента root/container', {'Пространство имен': 'urn:test', 'Схема': quoted})
        count, findings = individual.AuditIndividualTablesTests().check(card, source)
        self.assertEqual(count, 1)
        self.assertTrue(any(f['kind'] == 'Модель содержимого' for f in findings))
