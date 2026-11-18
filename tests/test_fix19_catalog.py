import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from build_site import archive_documents
from catalog import _export, derive
from module_xsd import extract_archives
from schema import Schema, XS, dependency_set
from sync import encode, inspect_file, sha, write
from tests.test_schema_recovery import archive, docx

ROOT = Path(__file__).resolve().parents[1]


def xsd(body, namespace=''):
    return f'<xs:schema xmlns:xs="{XS}" {namespace}>{body}</xs:schema>'


def current_snapshot():
    pointer = json.loads((ROOT / 'state/current.json').read_bytes())
    return json.loads((ROOT / 'state/snapshots' / (pointer['snapshot'] + '.json')).read_bytes())


def historical_snapshot():
    digest = '6581db63a1768b984f134e7b4cdae74f1a320df056ecd0aa6aa6a6a22a9839b7'
    return json.loads((ROOT / 'state/snapshots' / (digest + '.json')).read_bytes())


class CatalogRegressions(unittest.TestCase):
    def test_all_matched_urls_suppress_only_their_document_only_cards(self):
        match = dict(number='999', title='Answer', root='result', namespace='urn:test', dependency_digest='deps', schema_path='main.xsd', archive_sha256='archive', archive_url='schemas.zip')
        def document(url, matched=False, digest='document'):
            row = dict(environment='test', kind='docx', url=url, label='v1', sha256=digest, message_types=[dict(number='999', title='Answer')])
            if matched:
                row['module_matches'] = [match]
            return row
        for urls in (('A', 'B'), ('B', 'A')):
            with self.subTest(urls=urls), tempfile.TemporaryDirectory() as folder:
                state = Path(folder); history = []
                for rows in ([document('A')], [*[document(url, True) for url in urls], document('C', digest='unmatched')]):
                    raw = encode(dict(entries=rows, format=2)); key = sha(raw); history.append(key)
                    write(state/'snapshots'/(key+'.json'), raw)
                write(state/'current.json', encode(dict(snapshot=key))); write(state/'history.json', encode(history))
                _export(state, state/'catalog.json')
                result = json.loads((state/'catalog.json').read_bytes())
                self.assertEqual({s['document_url'] for s in result['revisions'][0]['listed_sources']}, {'A', 'B'})
                self.assertEqual({s['document_url'] for s in result['document_sources']}, {'C'})

    def test_recovered_import_checks_both_transitive_closures_in_inherited_namespace(self):
        def package(location, right):
            entry = xsd(f'<xs:include schemaLocation="{location}"/><xs:element name="root" type="c:Code"/>',
                        'targetNamespace="urn:test" xmlns:c="urn:test"')
            common = xsd('<xs:include schemaLocation="leaf.xsd"/>')
            def leaf(typ):
                return xsd('<xs:include schemaLocation="common.xsd"/>'
                           f'<xs:simpleType name="Code"><xs:restriction base="xs:{typ}"/></xs:simpleType>')
            return archive({'main.xsd': entry, 'a/common.xsd': common, 'b/common.xsd': common,
                            'a/leaf.xsd': leaf('boolean'), 'b/leaf.xsd': leaf(right)})

        conflicting = package('common.xsd', 'integer')
        with self.assertRaisesRegex(ValueError, 'Неоднозначный'):
            dependency_set(inspect_file(conflicting, 'zip')['schemas'], 'main.xsd')
        with zipfile.ZipFile(io.BytesIO(conflicting)) as bundle, self.assertRaisesRegex(ValueError, 'Неоднозначный'):
            Schema(bundle, 'main.xsd', 'root')
        for raw, base in [(package('common.xsd', 'boolean'), 'xs:boolean'),
                          (package('b/common.xsd', 'integer'), 'xs:integer')]:
            self.assertEqual(len(dependency_set(inspect_file(raw, 'zip')['schemas'], 'main.xsd')), 3)
            with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
                tree = Schema(bundle, 'main.xsd', 'root')
                tree.field(tree.root)
                self.assertEqual(tree.fields[0]['base'], base)

    def test_diamond_registers_only_accepted_nodes_and_parses_once_per_context(self):
        raw = archive({'main.xsd': xsd('<xs:include schemaLocation="a.xsd"/><xs:include schemaLocation="b.xsd"/>'
                                       '<xs:element name="root" type="xs:string"/>'),
                       'a.xsd': xsd('<xs:include schemaLocation="common.xsd"/>'),
                       'b.xsd': xsd('<xs:include schemaLocation="common.xsd"/>'), 'common.xsd': xsd('')})
        import schema
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle, patch('schema.ET.iterparse', wraps=schema.ET.iterparse) as parsed:
            tree = Schema(bundle, 'main.xsd', 'root')
        self.assertEqual(parsed.call_count, len(tree.documents))
        self.assertEqual(set(tree.owner), set(tree.context))
        self.assertEqual(set(tree.prefixes), set(tree.context))

    def test_real_document_without_scheme_remains_downloadable_for_each_message(self):
        catalog = derive(historical_snapshot())
        digest = '987d87743ee6bb7c13aede75cc2993d72e5fb22347c24b36d21a96f4c8d038d2'
        sources = [r for r in catalog.get('document_sources', []) if r['document_sha256'] == digest]
        self.assertEqual({r['number'] for r in sources}, {'317', '318', '319'})
        self.assertTrue(all(r['schema_reason'] and r['document_version'] == '2.0' for r in sources))
        with tempfile.TemporaryDirectory() as tmp:
            archive_documents(dict(revisions=[], document_sources=sources), ROOT / 'state', Path(tmp))
            for row in sources:
                self.assertEqual((Path(tmp) / row['document_download']).read_bytes(), (ROOT / 'state/objects' / digest).read_bytes())

    def test_original_module_14_sources_use_their_own_appendices(self):
        editions = [r for r in derive(historical_snapshot())['revisions'] if r['number'] == '14']
        for environment in ('prod', 'test'):
            rows = [r for r in editions if r['environment'] == environment]
            self.assertEqual(len(rows), 3)
            for marker, module in [('_02_', 'служебных'), ('_05_', 'Реестр страхователей'), ('_12_', 'Доходная часть')]:
                self.assertIn(module, next(r['module'] for r in rows if marker in r['document_url']))

    def test_current_unmatched_document_survives_a_historical_match_and_filename_change(self):
        rows = historical_snapshot()['entries']
        source = next(r for r in rows if r.get('sha256', '').startswith('987d87743ee6'))
        renamed = dict(source, url='https://lk.sfr.gov.ru/specs_sedo/module.docx')
        old_match = next(m for row in rows for m in row.get('module_matches', []) if m['number'] == '317')
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            history = []
            for records in ([dict(renamed, module_matches=[old_match])], [renamed]):
                raw = encode(dict(entries=records, format=2))
                key = sha(raw)
                write(state/'snapshots'/(key+'.json'), raw)
                history.append(key)
            write(state/'history.json', encode(history))
            write(state/'current.json', encode(dict(snapshot=key)))
            _export(state, state/'catalog.json')
            result = json.loads((state/'catalog.json').read_bytes())
        self.assertFalse(result['revisions'][0]['listed_now'])
        self.assertEqual({r['number'] for r in result['document_sources'] if r['listed_now']}, {'317','318','319'})

    def test_official_versions_are_separate_from_dates_in_both_orders(self):
        snapshot = current_snapshot()
        for label in ('Тип сообщения 10110 v3.10_12.11.2026.docx', 'Тип сообщения 10110 12.11.2026_v3.10.docx'):
            for entry in snapshot['entries']:
                if entry.get('structure') or entry.get('module_matches'):
                    entry['label'] = label
            rows = derive(snapshot)['revisions']
            self.assertTrue(rows)
            self.assertEqual({r['document_version'] for r in rows}, {'3.10'})
            self.assertEqual({r['label_date'] for r in rows}, {'12.11.2026'})

    def test_inline_schema_does_not_depend_on_prefix_and_keeps_legacy_name(self):
        def inline(prefix, ns):
            binding = 'xmlns:' + prefix[:-1] if prefix else 'xmlns'
            return f'<{prefix}schema {binding}="{ns}"><{prefix}element name="root" type="{prefix}string"/></{prefix}schema>'
        original = inline('xs:', XS)
        new = [inline('any:', XS), inline('сх:', XS), inline('', XS)]
        raw, _ = docx([inline('wrong:', 'urn:other'), *new, original])
        packages, issues = extract_archives(raw)
        self.assertFalse(issues)
        files = {}
        for source in packages:
            if source['container'].endswith('#combined'):
                continue
            with zipfile.ZipFile(io.BytesIO(source['content'])) as bundle:
                files[source['filename']] = bundle.read(source['filename']).decode()
        self.assertEqual(files.get('inline-1.xsd'), original)
        self.assertEqual(set(files.values()), {*new, original})
