import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from catalog import derive
from import_inbox import collect
from sync import inspect_file, sha
from tests.test_schema_recovery import archive
from tests.test_source_collection_regressions import individual_document
from tests.test_schema_recovery import docx
from module_xsd import document_bindings, extract_archives
from schema import dependency_set
from import_inbox import detect_metadata


class NamespaceSelectionTests(unittest.TestCase):
    def test_absent_namespace_keeps_different_qnames_ambiguous(self):
        metadata = inspect_file(individual_document('response'), 'docx')
        metadata['structure'] = [pair for pair in metadata['structure'] if pair[0] == 'Корневой элемент']
        schemas = []
        for namespace in ('', 'targetNamespace="urn:main"'):
            raw = archive({'main.xsd': ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
                                      + namespace + '><xs:element name="response" type="xs:string"/></xs:schema>').encode()})
            schemas.append(dict(environment='test', kind='zip', url='https://lk-test.fss.ru/'+str(len(schemas))+'.zip',
                                sha256=sha(raw), **inspect_file(raw, 'zip')))
        source = dict(environment='test', kind='docx', url='https://lk-test.fss.ru/Тип_сообщения_999.docx',
                      label='Тип сообщения 999', sha256='d'*64, **metadata)
        row = derive(dict(entries=[source, *schemas]))['revisions'][0]
        self.assertEqual(row['status'], 'review')
        self.assertNotIn('archive_sha256', row)

    def test_unicode_root_and_integer_filename_version_are_preserved(self):
        _, document = docx(['Тип сообщения 700: Описание', '<Сообщение xmlns="urn:ru"/>'])
        self.assertEqual([b['root'] for b in document_bindings(document)], ['Сообщение'])
        self.assertEqual(detect_metadata('999_v1.docx', 'test')['version'], '1')
        self.assertEqual(detect_metadata('999_v1_17.11.2026.docx', 'test')['version'], '1')

    def test_equal_embedded_bytes_keep_names_needed_by_include(self):
        common = b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:simpleType name="Code"><xs:restriction base="xs:string"/></xs:simpleType></xs:schema>'
        main = b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:include schemaLocation="z_common.xsd"/><xs:element name="root" type="Code"/></xs:schema>'
        for earlier in ('a_notes.txt', 'a_common.xsd'):
            with self.subTest(earlier=earlier):
                raw = archive({'word/embeddings/' + earlier: common,
                               'word/embeddings/z_common.xsd': common,
                               'word/embeddings/main.xsd': main,
                               'word/document.xml': b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>'})
                packages, issues = extract_archives(raw)
                self.assertFalse(issues)
                names = [p['filename'] for p in packages]
                self.assertIn('z_common.xsd', names)
                combined = next(p['content'] for p in packages if p['container'].endswith('#combined'))
                schemas = inspect_file(combined, 'zip')['schemas']
                entry = next(s['path'] for s in schemas if s['path'].endswith('/main.xsd'))
                self.assertEqual(len(dependency_set(schemas, entry)), 2)

    def test_yo_heading_selects_the_declared_namespace_in_catalog_and_inbox(self):
        schema = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" {ns}><xs:element name="response" type="xs:string"/></xs:schema>'
        for heading in ('Пространство имен', 'Пространство имён', 'ПРОСТРАНСТВО ИМЁН'):
            raw = individual_document('response')
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                xml = z.read('word/document.xml').decode().replace('Пространство имен', heading)
            doc = archive({'word/document.xml': xml.encode()})
            correct = archive({'main.xsd': schema.format(ns='targetNamespace="urn:main"').encode()})
            other = archive({'main.xsd': schema.format(ns='').encode()})
            rows = [dict(environment='test', kind='docx', url='https://lk-test.fss.ru/Тип_сообщения_999.docx',
                         label='Тип сообщения 999', sha256=sha(doc), **inspect_file(doc, 'docx'))]
            for index, payload in enumerate((correct, other)):
                rows.append(dict(environment='test', kind='zip', url=f'https://lk-test.fss.ru/{index}.zip',
                                 sha256=sha(payload), **inspect_file(payload, 'zip')))
            with self.subTest(heading=heading):
                row = derive(dict(entries=rows))['revisions'][0]
                self.assertEqual((row['status'], row['namespace'], row['archive_sha256']),
                                 ('matched', 'urn:main', sha(correct)))
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    folder = root / 'incoming/test'
                    folder.mkdir(parents=True)
                    (folder / '999_v1.0.docx').write_bytes(doc)
                    (folder / 'correct.zip').write_bytes(correct)
                    (folder / 'other.zip').write_bytes(other)
                    tasks, _ = collect(root)
                    self.assertEqual(tasks[0]['archive'].name, 'correct.zip')


class PublicationFixtureTests(unittest.TestCase):
    def test_historical_checks_survive_current_document_repacking(self):
        from tests import test_fix19_catalog as checks
        snapshot = checks.current_snapshot()
        for row in snapshot['entries']:
            if row.get('sha256') == '987d87743ee6bb7c13aede75cc2993d72e5fb22347c24b36d21a96f4c8d038d2':
                row['sha256'] = 'd' * 64
        with patch.object(checks, 'current_snapshot', return_value=snapshot):
            for name in ('test_real_document_without_scheme_remains_downloadable_for_each_message',
                         'test_current_unmatched_document_survives_a_historical_match_and_filename_change'):
                result = checks.CatalogRegressions(name).run()
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
