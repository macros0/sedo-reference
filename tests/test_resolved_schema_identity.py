import json
import shutil
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from lxml import etree

import build_site
from auto_update import _run, validate_saved_history
from catalog import binding_observation_identity, derive, export, replayed_binding_rejections, schema_revision_id
from collector_feed import import_feed, store_feed
from collector_snapshots import make_manifest, replay_archived_inputs
from fields import build
from import_inbox import collect, process_inbox
from manual_import import fingerprint, prepare_import, stage_import
from schema import SchemaFiles
from sync import RedirectedBytes, SITES, encode, sha, write
from tests import test_source_collection_regressions as fixtures
from tests import test_fix15_sources, test_wsdl_sources
from tests.test_module_messages import docx, paragraph
from tests.test_wsdl_binding_protection import BASE, definitions, include
from tests.test_wsdl_sources import SEED, XS
from update_catalog import main
from wsdl_sources import _archive
from xsd_bundle import _prepare, _selected
from xsd_validate import compile_schema


PROJECT = Path(__file__).resolve().parents[1]


def schema(body, ns=None):
    target = f' targetNamespace="{ns}"' if ns else ''
    return (f'<xs:schema xmlns:xs="{XS}" xmlns:a="urn:a" xmlns:b="urn:b" '
            f'elementFormDefault="qualified"{target}>' + body + '</xs:schema>').encode()


ROOT = schema('<xs:import namespace="urn:a" schemaLocation="a/wrapper.xsd"/>'
              '<xs:import namespace="urn:b" schemaLocation="b/wrapper.xsd"/>'
              '<xs:element name="r"><xs:complexType><xs:sequence>'
              '<xs:element name="first" type="a:Code"/><xs:element name="second" type="b:Code"/>'
              '</xs:sequence></xs:complexType></xs:element>', 'urn:main')


def datatype(pattern):
    return schema('<xs:simpleType name="Code"><xs:restriction base="xs:string"/></xs:simpleType>'
                  f'<xs:simpleType name="Variant"><xs:restriction base="Code"><xs:pattern value="{pattern}"/>'
                  '</xs:restriction></xs:simpleType>')


def library(first, second):
    return test_wsdl_sources.WsdlSourcesTests().collect({
        SEED: definitions(include('root.xsd')), BASE + 'root.xsd': ROOT,
        BASE + 'a/wrapper.xsd': schema(include('value.xsd'), 'urn:a'),
        BASE + 'b/wrapper.xsd': schema(include('value.xsd'), 'urn:b'),
        BASE + 'a/value.xsd': RedirectedBytes(datatype(first), BASE + f'chameleon/{first}/value.xsd'),
        BASE + 'b/value.xsd': RedirectedBytes(datatype(second), BASE + f'chameleon/{second}/value.xsd'),
    })


def archive(first, second):
    files = {'root.xsd': ROOT, 'a/wrapper.xsd': schema(include('value.xsd'), 'urn:a'),
             'b/wrapper.xsd': schema(include('value.xsd'), 'urn:b'),
             'values/a/value.xsd': datatype('a'), 'values/b/value.xsd': datatype('b')}
    return _archive(SchemaFiles(files, import_targets={
        'a/wrapper.xsd': {'value.xsd': f'values/{first}/value.xsd'},
        'b/wrapper.xsd': {'value.xsd': f'values/{second}/value.xsd'},
    }))


def verdicts(fixture, row):
    state = fixture.root / 'state'
    with ZipFile(state / 'objects' / row['archive_sha256']) as bundle:
        output, _, _ = _prepare(row, _selected(row, bundle))
    directory = fixture.root / ('validator-' + row['revision_id'])
    for path, content in output.items():
        write(directory / path, content)
    validator = compile_schema(directory / 'message.xsd', directory)
    xml = ('<r xmlns="urn:main" xmlns:a="urn:a" xmlns:b="urn:b" '
           'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
           '<first xsi:type="a:Variant">{}</first><second xsi:type="b:Variant">{}</second></r>')
    return [validator.validate(etree.fromstring(xml.format(first, second)))
            for first, second in [('a', 'b'), ('b', 'a')]]


class ResolvedSchemaIdentityTests(unittest.TestCase):
    def collection(self):
        fixture = fixtures.SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for page in SITES.values():
            fixture.resources[page] = b'<a href="/specs_sedo/file.zip">Schema</a>'
        shutil.copytree(PROJECT / 'web', fixture.root / 'web')
        shutil.copy2(PROJECT / 'viewer-template.html', fixture.root)
        return fixture

    def test_distinct_namespace_only_roots_reserve_distinct_ids_in_one_inventory(self):
        document = 'd' * 64
        graph = 'a' * 64
        dep = 'b' * 64
        primary = dict(number='42', title='Документ', namespace='urn:one', root='r',
                       resolved_schema_graph=graph, dependency_digest=dep)
        second = dict(primary, namespace='urn:two')
        entry = dict(environment='test', kind='docx', url='https://test.example/module.docx',
                     sha256=document, label='Документ', module_matches=[primary, second])
        snapshot = dict(format=11, entries=[entry])
        old = sha(encode(['test', '42', document, dep]))
        rows = derive(snapshot)['revisions']
        self.assertEqual([(row['namespace'], row['root']) for row in rows], [('urn:one', 'r'), ('urn:two', 'r')])
        first, new = [row['revision_id'] for row in rows]
        self.assertEqual(first, old)
        self.assertNotEqual(new, old)
        reservations = {row['revision_id']: row for row in rows}
        reversed_rows = derive(dict(snapshot, entries=[dict(entry, module_matches=[second, primary])]),
                               reservations)['revisions']
        self.assertEqual([row['revision_id'] for row in reversed_rows], [new, old])
        reason = 'Отзыв прежнего утверждения'
        first_address, second_address = map(binding_observation_identity, rows)
        self.assertEqual(replayed_binding_rejections(rows, {first: reason}, snapshot='confirmed',
                         resolve=lambda choices: [row for row in choices if row['namespace'] == 'urn:one']),
                         {first_address: reason})
        legacy = [dict(observation=first_address[:5], reason=reason)]
        with self.assertRaisesRegex(ValueError, r'confirmed.*urn:one.*urn:two'):
            replayed_binding_rejections(rows, legacy, snapshot='confirmed')
        self.assertNotEqual(first_address, second_address)
        poisoned = dict(rows[0], namespace='urn:two')
        with self.assertRaisesRegex(ValueError, 'QName'):
            validate_saved_history({'revisions': rows}, {'revisions': [poisoned, rows[1]]})

    def test_legacy_graph_id_keeps_its_qname(self):
        document, dep, graph = 'a' * 64, 'b' * 64, 'c' * 64
        legacy = sha(encode(['test', '42', document, dep]))
        graph_id = sha(encode(['resolved-xsd-graph-v1', legacy, graph]))
        reservations = {legacy: dict(root='old', namespace='urn:one', resolved_schema_graph='d' * 64),
                        graph_id: dict(root='r', namespace='urn:two', resolved_schema_graph=graph)}
        first = schema_revision_id('test', '42', document, dep, graph, reservations, root='r', namespace='urn:two')
        second = schema_revision_id('test', '42', document, dep, graph, reservations, root='r', namespace='urn:three')
        self.assertEqual(first, graph_id)
        self.assertNotIn(second, reservations)

    def test_compatible_module_declarations_preview_save_and_import_one_graph(self):
        import html
        from tempfile import TemporaryDirectory
        fixture = self.collection()
        fixture.collect(1, wsdl_entries=[])
        export(fixture.state, fixture.root / 'data/catalog.json')
        title = paragraph('Тип сообщения 42: Example')
        table = '<w:tbl><w:tr>' + ''.join('<w:tc>' + paragraph(html.escape(value)) + '</w:tc>'
                 for value in ('Схема', '<xs:element name="r"/>')) + '</w:tr></w:tbl>'
        document = docx(title + table + title + paragraph(html.escape('<r xmlns="urn:external"/>')))
        package = _archive({'root.xsd': schema('<xs:element name="r" type="xs:string"/>', 'urn:external')})
        name = '42_v1.0.docx'
        write(fixture.root / 'incoming/test' / name, document)
        for filename in ('one.zip', 'two.zip'):
            write(fixture.root / 'incoming/test' / filename, package)
        with TemporaryDirectory() as tmp:
            isolated = Path(tmp) / 'project'
            shutil.copytree(fixture.root, isolated)
            before = fingerprint(isolated)
            checked = process_inbox(isolated, check=True)
            self.assertEqual(before, fingerprint(isolated))
            self.assertFalse((isolated / 'state/inbox.json').exists())
            with self.subTest('manual'):
                preview = prepare_import(fixture.root, document, name, dict(number='42', environment='test', version='1.0'), package)
                try:
                    rid = preview.revision['revision_id']
                    self.assertEqual(preview.revision['namespace'], 'urn:external')
                    preview.save()
                finally:
                    preview.close()
                self.assertIn(rid, {r['revision_id'] for r in json.loads((fixture.root / 'data/catalog.json').read_bytes())['revisions']})
            imported = process_inbox(isolated)
            self.assertEqual([checked['files'][0]['revision_id'], imported['files'][0]['revision_id']], [rid, rid])
            index = isolated / 'state/inbox.json'
            old = json.loads(index.read_bytes())
            old['files']['incoming/test/' + name]['graph_match_version'] = 2
            write(index, encode(old))
            self.assertEqual(process_inbox(isolated)['new_revisions'], 0)
            self.assertEqual(process_inbox(isolated)['processed'], 0)

    def test_inbox_and_manual_import_respect_the_previously_reserved_root(self):
        fixture = self.collection()
        state = fixture.state
        fixture.collect(1, wsdl_entries=[])
        raw = fixtures.individual_document('r')
        archive = _archive({'main.xsd': schema('<xs:element name="r" type="xs:string"/>'
                                             '<xs:element name="other" type="xs:int"/>', 'urn:main')})
        digest = sha(raw)
        for content in (raw, archive):
            write(state / 'objects' / sha(content), content)
        source_url = 'https://test.example/Тип_сообщения_42.docx'
        from sync import inspect_file
        entries = [
            dict(kind='docx', url=source_url, environment='test', label='Тип сообщения 42', sha256=digest,
                 structure=[['Корневой элемент', 'other'], ['Пространство имен файла XML-схемы', 'urn:main']]),
            dict(kind='zip', url='https://test.example/archive.zip', environment='test', sha256=sha(archive),
                 **inspect_file(archive, 'zip')),
        ]
        [old] = derive(dict(entries=entries))['revisions']
        snapshot = dict(format=1, source_type='manual', revision=dict(old, source_type='manual', document_url=None,
                        manual_source=dict(label='Архив', filename='42.docx', imported_at='2026-11-14T01:30:00+00:00')))
        payload = encode(snapshot)
        key = sha(payload)
        write(state / 'snapshots' / (key + '.json'), payload)
        history = json.loads((state / 'history.json').read_bytes())
        write(state / 'history.json', encode([*history, key]))
        export(state, fixture.root / 'data/catalog.json')
        incoming = fixture.root / 'incoming/test'
        write(incoming / '42.docx', raw)
        write(incoming / 'archive.zip', archive)
        [task], _ = collect(fixture.root)
        result = stage_import(fixture.root, raw, '42.docx', task['metadata'], archive)
        new = result['revision']
        self.assertEqual(new['root'], 'r')
        self.assertNotEqual(new['revision_id'], old['revision_id'])
        self.assertEqual(new['revision_id'], task['preferred_revision_id'])
        rows = json.loads((fixture.root / 'data/catalog.json').read_bytes())['revisions']
        self.assertEqual([(row['root'], row['revision_id']) for row in rows if row['number'] == '42'],
                         [('other', old['revision_id']), ('r', new['revision_id'])])

    def test_direct_wsdl_update_preserves_two_distinct_graphs(self):
        for command in ('update_catalog', 'auto_update', 'replay', 'different-docx'):
            with self.subTest(command=command):
                fixture = self.collection()
                url = fixture.add_individual('999', 'r', 'root.xsd')
                prior, old_objects = library('a', 'b')
                current, objects = library('b', 'a')
                self.assertEqual([(s['path'], s['sha256']) for s in prior['schemas']],
                                 [(s['path'], s['sha256']) for s in current['schemas']])
                for digest, raw in old_objects.items():
                    write(fixture.state / 'objects' / digest, raw)
                fixture.collect(1, wsdl_entries=[prior])
                data = fixture.root / 'data'
                index = data / 'catalog.json'
                export(fixture.state, index)
                build(index, fixture.state, data / 'fields')
                initial = json.loads(index.read_bytes())
                old = initial['revisions'][0]
                old_path = data / 'fields' / (old['revision_id'] + '.json')
                previous_fields = old_path.read_bytes()
                if command == 'different-docx':
                    content = BytesIO(fixture.resources[url])
                    with ZipFile(content, 'a') as doc:
                        doc.comment = b'Second official document'
                    fixture.resources[url] = content.getvalue()
                    for digest, raw in objects.items():
                        write(fixture.state / 'objects' / digest, raw)
                    fixture.collect(2, wsdl_entries=[current])
                    export(fixture.state, index)
                    build(index, fixture.state, data / 'fields')
                else:
                    remote = fixture.root / 'feed'
                    store_feed(remote, [make_manifest(current, '2026-11-14T01:30:00+00:00')], objects)
                    import_feed(fixture.state, lambda path: (remote / path).read_bytes())
                    with patch.dict('os.environ', {'SEDO_COLLECTOR_SOURCE': 'sourcecraft'}), \
                            patch('sync.get', side_effect=fixture.resources.__getitem__):
                        if command == 'auto_update':
                            _run(fixture.root)
                        elif command == 'update_catalog':
                            with patch.object(sys, 'argv', ['update_catalog.py', '--state', str(fixture.state), '--out', str(data)]):
                                main()
                        else:
                            [record] = replay_archived_inputs(fixture.state)
                            self.assertEqual(record['status'], 'replayed')
                            export(fixture.state, index)
                            build(index, fixture.state, data / 'fields')
                installed = json.loads(index.read_bytes())
                self.assertEqual(len(installed['revisions']), 2)
                first, second = installed['revisions']
                self.assertEqual(first['revision_id'], old['revision_id'])
                self.assertNotEqual(second['revision_id'], first['revision_id'])
                self.assertEqual(first['dependencies'], second['dependencies'])
                self.assertEqual(first['archive_sha256'], old['archive_sha256'])
                self.assertEqual(old_path.read_bytes(), previous_fields)
                self.assertEqual([verdicts(fixture, row) for row in installed['revisions']],
                                 [[True, False], [False, True]])
                self.assertEqual(installed['updates'][:1], initial['updates'])
                with patch.object(build_site, 'HERE', fixture.root):
                    build_site.main()
                public = json.loads((fixture.root / 'site/catalog-data.js').read_text('utf8')
                                    .removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
                self.assertEqual(len(public['schema_updates']), 2)
                self.assertEqual([row['display_revision_id'] for row in public['revisions']],
                                 [first['revision_id'], second['revision_id']])

    def test_module_quotes_keep_different_external_graphs_ambiguous(self):
        for first, second in [('a', 'b'), ('b', 'a')]:
            with self.subTest(first=first):
                fixture = self.collection()
                page = SITES['test']
                folder = page.rsplit('/', 1)[0] + '/specs_sedo/'
                fixture.resources[page] = (b'<a href="/specs_sedo/module.docx">Module</a>'
                                           b'<a href="/specs_sedo/a.zip">A</a><a href="/specs_sedo/b.zip">B</a>')
                fixture.resources[folder + 'module.docx'] = docx(paragraph('Тип сообщения 999: Ответ')
                    + paragraph('&lt;r xmlns="urn:main"/&gt;'))
                fixture.resources[folder + 'a.zip'] = archive(first, second)
                fixture.resources[folder + 'b.zip'] = archive(second, first)
                fixture.collect(1, wsdl_entries=[])
                [document] = [e for e in fixture.current()['entries'] if e.get('module_bindings')]
                self.assertFalse(document.get('module_matches'))
                self.assertEqual(document['schema_diagnostics'][0]['reason'], 'Неоднозначные схемы')
                self.assertEqual(fixture.current()['format'], 9)

    def test_module_update_keeps_previous_archive_and_snapshot_format(self):
        from verify import verify_snapshot_replay

        fixture = self.collection()
        page = SITES['test']
        folder = page.rsplit('/', 1)[0] + '/specs_sedo/'
        fixture.resources[page] = (b'<a href="/specs_sedo/module.docx">Module</a>'
                                   b'<a href="/specs_sedo/first.zip">Schema</a>')
        fixture.resources[folder + 'module.docx'] = docx(paragraph('Тип сообщения 999: Ответ')
            + paragraph('&lt;r xmlns="urn:main"/&gt;'))
        first, second = archive('a', 'b'), archive('b', 'a')
        fixture.resources[folder + 'first.zip'] = first
        fixture.collect(1, wsdl_entries=[], snapshot_format=8)
        key = json.loads((fixture.state / 'current.json').read_bytes())['snapshot']
        verify_snapshot_replay(fixture.state, fixture.current(), key)
        data = fixture.root / 'data'
        index = data / 'catalog.json'
        export(fixture.state, index)
        build(index, fixture.state, data / 'fields')
        old = json.loads(index.read_bytes())['revisions'][0]
        fixture.resources[folder + 'first.zip'] = second
        with patch('sync.get', side_effect=fixture.resources.__getitem__), \
                patch.object(sys, 'argv', ['update_catalog.py', '--state', str(fixture.state), '--out', str(data)]):
            main()
        rows = json.loads(index.read_bytes())['revisions']
        self.assertEqual([row['archive_sha256'] for row in rows], [sha(first), sha(second)])
        self.assertEqual(rows[0]['revision_id'], old['revision_id'])
        self.assertEqual([verdicts(fixture, row) for row in rows], [[True, False], [False, True]])

    def test_inbox_requires_unambiguous_graph_and_preserves_both_sequential_imports(self):
        fixture = test_fix15_sources.FilenameImportReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        first, second = archive('a', 'b'), archive('b', 'a')
        folder = fixture.root / 'incoming/test'
        write(folder / '999.docx', fixtures.individual_document('r'))
        write(folder / 'a.zip', first)
        write(folder / 'b.zip', second)
        with self.assertRaisesRegex(ValueError, 'несколько разных XSD'):
            collect(fixture.root)
        (folder / 'b.zip').unlink()
        self.assertEqual(process_inbox(fixture.root)['new_revisions'], 1)
        write(folder / 'a.zip', second)
        pending, _ = collect(fixture.root)
        # Even an already indexed legacy delivery has to be checked if the
        # newly submitted file got the old revision ID by mistake.
        record = {'input_signature': pending[0]['input_signature'], 'explicit_fields': []}
        replay, _ = collect(fixture.root, {'files': {'incoming/test/999.docx': record}})
        self.assertEqual(len(replay), 1)
        self.assertEqual(process_inbox(fixture.root)['new_revisions'], 1)
        catalog = json.loads((fixture.root / 'data/catalog.json').read_bytes())
        self.assertEqual([row['archive_sha256'] for row in catalog['revisions']], [sha(first), sha(second)])
        self.assertEqual([verdicts(fixture, row) for row in catalog['revisions']], [[True, False], [False, True]])
        self.assertEqual(process_inbox(fixture.root)['processed'], 0)
