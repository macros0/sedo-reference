"""Regression coverage for proven DOCX locations and redirected WSDL sources."""
import html
import json
import shutil
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from lxml import etree

from auto_update import _run, validate_saved_history
from catalog import binding_observation_identity, derive, export
from collector import store_local_inputs
from collector_feed import import_feed, store_feed
from collector_snapshots import (apply_pending, collector_source, make_manifest, pending_work, processed_manifests,
                                 confirmed_binding_index, feed_manifests, observation_path, replay_archived_inputs,
                                 replay_candidate, replay_context, selected_records)
from fields import build
from schema import SchemaFiles, dependency_set
from sync import RedirectedBytes, SITES, encode, sha, write
from tests import test_wsdl_sources as wsdl
from tests import test_source_collection_regressions as sources
from tests.test_embedded_xsd import chameleon_quotation, quoted_document
from tests.test_module_messages import docx, paragraph
from update_catalog import main
from wsdl_sources import _archive, collect
from xsd_bundle import _prepare, _selected
from xsd_validate import compile_schema


PROJECT = Path(__file__).resolve().parents[1]
BASE = wsdl.BASE.rsplit('/', 1)[0] + '/'
SCHEMA = f'<xs:schema xmlns:xs="{wsdl.XS}" xmlns:m="urn:main" targetNamespace="urn:main">'
NAMED = (SCHEMA + '<xs:element name="r" type="xs:int"/></xs:schema>').encode()
NEIGHBOR = '<xs:element name="r" type="xs:string"/>'


def definitions(*schemas):
    return ('<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/"><w:types>'
            + ''.join(SCHEMA + body + '</xs:schema>' for body in schemas)
            + '</w:types></w:definitions>').encode()


def include(path):
    return f'<xs:include schemaLocation="{path}"/>'


class PublishedBindingTests(unittest.TestCase):
    def fixture(self, *, other=True, initial_zip=False, named=NAMED, neighbor=True, legacy=False, location='root.xsd'):
        fixture = sources.SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for page in SITES.values():
            fixture.resources[page] = b'<a href="/specs_sedo/file.zip">Schema</a>'
        url = fixture.add_individual('999', 'r', location)
        if other:
            # Keep another publication so the volume guard permits removal.
            fixture.add_individual('998', 'other', 'a.xsd')
            fixture.resources[SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')] = sources.archive(
                'a.xsd', (SCHEMA + '<xs:element name="other" type="xs:int"/></xs:schema>').encode())
        bodies = (include(location), NEIGHBOR) if neighbor else (include(location),)
        original, objects = wsdl.WsdlSourcesTests().collect(
            {wsdl.SEED: definitions(*bodies), BASE + location: named})
        for digest, raw in objects.items():
            write(fixture.state / 'objects' / digest, raw)
        if initial_zip:
            zip_url = SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')
            original_zip = fixture.resources[zip_url]
            fixture.resources[zip_url] = sources.archive('root.xsd', NAMED)
            fixture.collect(1, wsdl_entries=[])
            fixture.resources[zip_url] = original_zip
            fixture.collect(2, wsdl_entries=[original])
            fixture.resources[zip_url] = sources.archive('root.xsd', NAMED)
            fixture.collect(3, wsdl_entries=[])
        else:
            options = dict(snapshot_format=7, individual_wsdl=2) if legacy else {}
            fixture.collect(1, wsdl_entries=[original], **options)
        root = fixture.root
        shutil.copytree(PROJECT / 'web', root / 'web')
        shutil.copy2(PROJECT / 'viewer-template.html', root)
        index = root / 'data/catalog.json'
        export(fixture.state, index)
        build(index, fixture.state, root / 'data/fields')
        expected = next(row for row in json.loads(index.read_bytes())['revisions'] if row['document_url'] == url)
        if initial_zip:
            self.assertNotIn('schema_docx_location', expected)
            self.assertEqual(expected['schema_path'], 'root.xsd')
        elif legacy:
            self.assertNotIn('schema_docx_location', expected)
            self.assertTrue(expected['schema_path'].endswith('/root.xsd'))
        else:
            self.assertEqual(expected['schema_docx_location'], location)
            self.assertTrue(expected['schema_docx_path'].endswith('/root.xsd'))
        return fixture, expected

    def feed(self, fixture, resources, *, hour='01'):
        library, objects = wsdl.WsdlSourcesTests().collect(resources)
        remote = fixture.root / 'feed'
        manifest = make_manifest(library, f'2026-11-14T{hour}:30:00+00:00')
        store_feed(remote, [manifest], objects)
        import_feed(fixture.state, lambda name: (remote / name).read_bytes())
        return manifest

    def confirmed(self, fixture, expected):
        files = (fixture.state / 'current.json', fixture.state / 'history.json',
                 fixture.root / 'data/catalog.json', fixture.root / 'data/fields/index.json',
                 fixture.root / 'data/fields' / (expected['revision_id'] + '.json'))
        return [sha(path.read_bytes()) for path in files]

    def update(self, fixture, command, *, out=None, state=None):
        with patch('sync.get', side_effect=fixture.resources.__getitem__):
            if command == 'auto_update':
                _run(fixture.root)
            else:
                with patch.object(sys, 'argv', ['update_catalog.py', '--state', str(state or fixture.state),
                                               '--out', str(out or fixture.root / 'data')]):
                    main()

    def test_relocated_document_still_requires_its_proven_binding(self):
        for command in ('auto_update', 'update_catalog'):
            with self.subTest(command=command):
                fixture, expected = self.fixture()
                before = self.confirmed(fixture, expected)
                old_url = expected['document_url']
                new_url = old_url.replace('999.docx', '999_v2.docx')
                page = SITES['test']
                fixture.resources[new_url] = fixture.resources.pop(old_url)
                fixture.resources[page] = fixture.resources[page].replace(b'999.docx', b'999_v2.docx')
                self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR)})
                with self.assertRaisesRegex(ValueError, r'999 .*999_v2\.docx.*root\.xsd'):
                    self.update(fixture, command)
                self.assertEqual(self.confirmed(fixture, expected), before)

                self.feed(fixture, {wsdl.SEED: definitions(include('root.xsd'), NEIGHBOR),
                                    BASE + 'root.xsd': NAMED}, hour='02')
                self.update(fixture, command)
                catalog = json.loads((fixture.root / 'data/catalog.json').read_bytes())
                row, = (r for r in catalog['revisions'] if r['number'] == '999' and r['listed_now'])
                self.assertEqual((row['revision_id'], row['document_url']), (expected['revision_id'], new_url))
                self.assertEqual([item['source']['document_url'] for item in catalog['source_observations']
                                  if item['revision_id'] == row['revision_id']], [old_url, new_url])

    def test_same_document_number_with_changed_bytes_can_use_new_schema(self):
        for command in ('auto_update', 'update_catalog'):
            with self.subTest(command=command):
                fixture, expected = self.fixture()
                url = expected['document_url']
                fixture.resources[url] = sources.individual_document('r', 'new.xsd')
                self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR)})
                self.update(fixture, command)
                catalog = json.loads((fixture.root / 'data/catalog.json').read_bytes())
                row, = (r for r in catalog['revisions'] if r['number'] == '999' and r['listed_now'])
                self.assertNotEqual(row['document_sha256'], expected['document_sha256'])
                self.assertTrue(row['schema_path'].endswith('/inline-0.xsd'))

    def test_online_new_output_uses_confirmed_inventory_for_binding_guard(self):
        fixture, expected = self.fixture()
        before = self.confirmed(fixture, expected)
        new_out = fixture.root / 'new-data'
        self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR)})
        for out in (fixture.root / 'data', new_out):
            with self.subTest(out=out.name), self.assertRaisesRegex(ValueError, r'999 .*root\.xsd'):
                self.update(fixture, 'update_catalog', out=out)
            self.assertEqual(self.confirmed(fixture, expected), before)
        self.assertFalse((new_out / 'catalog.json').exists())

        self.feed(fixture, {wsdl.SEED: definitions(include('root.xsd'), NEIGHBOR),
                            BASE + 'root.xsd': NAMED}, hour='02')
        self.update(fixture, 'update_catalog', out=new_out)
        catalog = json.loads((new_out / 'catalog.json').read_bytes())
        self.assertIn(expected['revision_id'], {r['revision_id'] for r in catalog['revisions'] if r['listed_now']})
        page = SITES['test']
        fixture.resources[page] += b'<a href="/specs_sedo/named.zip">XSD</a>'
        fixture.resources[page.replace('/sedo.html', '/specs_sedo/named.zip')] = sources.archive('root.xsd', NAMED)
        empty_out = fixture.root / 'first-data'
        self.update(fixture, 'update_catalog', out=empty_out, state=fixture.root / 'first-state')
        initial = json.loads((empty_out / 'catalog.json').read_bytes())
        self.assertIn(expected['revision_id'], {r['revision_id'] for r in initial['revisions'] if r['listed_now']})

    def test_document_can_leave_one_contour_and_be_added_independently_in_another(self):
        for command in ('auto_update', 'update_catalog'):
            with self.subTest(command=command):
                fixture, expected = self.fixture()
                old_url = expected['document_url']
                filename = old_url.rsplit('/', 1)[1]
                prod, test = SITES['prod'], SITES['test']
                link = f'<a href="/specs_sedo/{filename}">Individual 999</a>'.encode()
                fixture.resources[test] = fixture.resources[test].replace(link, b'')
                fixture.resources[prod] += link
                new_url = prod.replace('/sedo.html', '/specs_sedo/' + filename)
                fixture.resources[new_url] = fixture.resources.pop(old_url)
                fixture.resources[prod.replace('/sedo.html', '/specs_sedo/file.zip')] = sources.archive(
                    'unrelated.xsd', (SCHEMA + NEIGHBOR + '</xs:schema>').encode())
                self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR)})
                self.update(fixture, command)
                catalog = json.loads((fixture.root / 'data/catalog.json').read_bytes())
                current, = (r for r in catalog['revisions'] if r['number'] == '999' and r['listed_now'])
                self.assertEqual((current['environment'], current['document_sha256'], current['schema_path']),
                                 ('prod', expected['document_sha256'], 'unrelated.xsd'))
                archived, = (r for r in catalog['revisions'] if r['revision_id'] == expected['revision_id'])
                self.assertFalse(archived['listed_now'])

    def test_equivalent_zip_chameleon_member_remains_bound_through_wrapper(self):
        standalone = NAMED.replace(b' targetNamespace="urn:main"', b'')
        for command in ('auto_update', 'update_catalog', 'replay'):
            with self.subTest(command=command):
                fixture, expected = self.fixture(other=False, named=standalone, neighbor=False)
                self.assertTrue(expected['schema_path'].endswith('/inline-0.xsd'))
                zip_url = SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')
                fixture.resources[zip_url] = (fixture.state / 'objects' / expected['archive_sha256']).read_bytes()
                if command == 'replay':
                    fixture.collect(2, wsdl_entries=[])
                manifest = self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR.replace('name="r"', 'name="other"'))}, hour='02')
                if command == 'replay':
                    _run(fixture.root, history_only=True)
                    report = json.loads((fixture.root / 'update-report.json').read_bytes())
                    row, = (r for r in report['collector_replay'] if r['content_id'] == manifest['content_id'])
                    self.assertEqual(row['status'], 'replayed')
                    self.assertNotIn('unmatched_documents', row)
                else:
                    self.update(fixture, command)
                rows = json.loads((fixture.root / 'data/catalog.json').read_bytes())['revisions']
                current, = (row for row in rows if row['listed_now'])
                self.assertEqual(current['revision_id'], expected['revision_id'])
                self.assertEqual(current['archive_url'], zip_url)

    def test_zip_must_resolve_named_member_and_unchanged_dependencies(self):
        standalone = NAMED.replace(b' targetNamespace="urn:main"', b'')
        for command in ('auto_update', 'update_catalog', 'replay'):
            for altered in ('outside_closure', 'changed_member'):
                with self.subTest(command=command, altered=altered):
                    fixture, expected = self.fixture(named=standalone, neighbor=False, other=False)
                    entry, named = expected['schema_path'], expected['schema_docx_path']
                    with ZipFile(BytesIO((fixture.state / 'objects' / expected['archive_sha256']).read_bytes())) as archive:
                        files = {name: archive.read(name) for name in archive.namelist()}
                    if altered == 'outside_closure':
                        files[entry] = (SCHEMA + NEIGHBOR + '</xs:schema>').encode()
                    else:
                        files[named] = standalone.replace(b'xs:int', b'xs:string')
                    zip_url = SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')
                    fixture.resources[zip_url] = _archive(files)
                    if command == 'replay':
                        fixture.collect(2, wsdl_entries=[])
                    before = self.confirmed(fixture, expected)
                    manifest = self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR.replace('name="r"', 'name="other"'))}, hour='02')
                    if command == 'replay':
                        records = replay_archived_inputs(fixture.state)
                        row, = (r for r in records if r['content_id'] == manifest['content_id'])
                        self.assertEqual(row['status'], 'unavailable')
                        self.assertIn('root.xsd', row['unmatched_documents'][0]['reason'])
                    else:
                        with self.assertRaisesRegex(ValueError, r'999 .*root\.xsd'):
                            self.update(fixture, command)
                        self.assertEqual(self.confirmed(fixture, expected), before)

    def test_byte_exact_zip_keeps_member_at_its_archived_path_for_relative_locator(self):
        standalone = NAMED.replace(b' targetNamespace="urn:main"', b'')
        fixture, expected = self.fixture(other=False, named=standalone, neighbor=False,
                                        location='published/root.xsd')
        archive_path = expected['schema_docx_path']
        self.assertFalse(archive_path.endswith('/published/root.xsd'))
        zip_url = SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')
        fixture.resources[zip_url] = (fixture.state / 'objects' / expected['archive_sha256']).read_bytes()
        self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR.replace('name="r"', 'name="other"'))})
        self.update(fixture, 'update_catalog')
        catalog = json.loads((fixture.root / 'data/catalog.json').read_bytes())
        row, = (r for r in catalog['revisions'] if r['listed_now'])
        self.assertEqual((row['revision_id'], row['archive_url']), (expected['revision_id'], zip_url))

    def test_live_update_rejects_qname_fallback_after_named_xsd_disappears(self):
        for command in ('auto_update', 'update_catalog'):
            with self.subTest(command=command):
                fixture, expected = self.fixture()
                before = self.confirmed(fixture, expected)
                self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR)})
                with self.assertRaisesRegex(ValueError, r'999 .*root\.xsd'):
                    self.update(fixture, command)
                self.assertEqual(self.confirmed(fixture, expected), before)
                if command == 'auto_update':
                    report = json.loads((fixture.root / 'update-report.json').read_bytes())
                    self.assertEqual(report['status'], 'failed')
                    self.assertIn('root.xsd', report['error'])

    def test_archived_v2_binding_blocks_qname_fallback(self):
        for command in ('auto_update', 'update_catalog', 'replay'):
            with self.subTest(command=command):
                fixture, expected = self.fixture(other=False, legacy=True, neighbor=False)
                before = self.confirmed(fixture, expected)
                manifest = self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR)})
                if command == 'replay':
                    records = replay_archived_inputs(fixture.state)
                    row, = (r for r in records if r['content_id'] == manifest['content_id'])
                    self.assertEqual(row['status'], 'unavailable')
                    self.assertIn('root.xsd', row['unmatched_documents'][0]['reason'])
                else:
                    with self.assertRaisesRegex(ValueError, r'999 .*root\.xsd'):
                        self.update(fixture, command)
                    self.assertEqual(self.confirmed(fixture, expected), before)

    def test_both_commands_keep_ready_tree_when_named_xsd_loses_its_type(self):
        for command in ('auto_update', 'update_catalog'):
            with self.subTest(command=command):
                fixture, expected = self.fixture()
                before = self.confirmed(fixture, expected)
                self.feed(fixture, {wsdl.SEED: definitions(include('root.xsd'), NEIGHBOR),
                                    BASE + 'root.xsd': NAMED.replace(b'xs:int', b'm:Missing')})
                with self.assertRaisesRegex(ValueError, r'999 .*точка входа'):
                    self.update(fixture, command)
                self.assertEqual(self.confirmed(fixture, expected), before)

    def test_historical_replay_records_missing_locator_without_an_inferred_edition(self):
        fixture, expected = self.fixture(other=False)
        before = self.confirmed(fixture, expected)
        manifest = self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR)})
        _run(fixture.root, history_only=True)
        self.assertEqual(self.confirmed(fixture, expected), before)
        report = json.loads((fixture.root / 'update-report.json').read_bytes())
        record, = (r for r in report['collector_replay'] if r['content_id'] == manifest['content_id'])
        self.assertEqual(record['status'], 'unavailable')
        unmatched, = record['unmatched_documents']
        self.assertEqual(unmatched['number'], '999')
        self.assertIn('root.xsd', unmatched['reason'])

    def test_equivalent_named_member_in_a_published_zip_can_replace_wsdl(self):
        for command in ('auto_update', 'update_catalog'):
            with self.subTest(command=command):
                fixture, expected = self.fixture()
                fixture.resources[SITES['test'].replace('/sedo.html', '/specs_sedo/equivalent.zip')] = sources.archive(
                    'root.xsd', NAMED)
                fixture.resources[SITES['test']] += b'<a href="/specs_sedo/equivalent.zip">XSD</a>'
                self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR.replace('name="r"', 'name="unrelated"'))})
                self.update(fixture, command)
                rows = json.loads((fixture.root / 'data/catalog.json').read_bytes())['revisions']
                current, = (row for row in rows if row['number'] == '999' and row['listed_now'])
                self.assertEqual(current['revision_id'], expected['revision_id'])
                self.assertTrue(current['archive_url'].endswith('/equivalent.zip'))

    def test_published_binding_survives_when_the_first_observation_was_a_zip(self):
        for command in ('auto_update', 'update_catalog'):
            with self.subTest(command=command):
                fixture, expected = self.fixture(other=False, initial_zip=True)
                before = self.confirmed(fixture, expected)
                catalog = json.loads((fixture.root / 'data/catalog.json').read_bytes())
                first = catalog['observation_presentations'][expected['revision_id']][expected['document_url']]
                self.assertNotIn('schema_docx_location', first)
                zip_url = SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')
                fixture.resources[zip_url] = sources.archive('unrelated.xsd', (SCHEMA + NEIGHBOR + '</xs:schema>').encode())
                with self.assertRaisesRegex(ValueError, r'999 .*root\.xsd'):
                    self.update(fixture, command)
                self.assertEqual(self.confirmed(fixture, expected), before)

    def test_replay_after_return_to_zip_checks_intervening_wsdl_binding(self):
        fixture, expected = self.fixture(other=False, initial_zip=True)
        before = self.confirmed(fixture, expected)
        manifest = self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR)}, hour='03')
        _run(fixture.root, history_only=True)
        self.assertEqual(self.confirmed(fixture, expected), before)
        report = json.loads((fixture.root / 'update-report.json').read_bytes())
        record, = (r for r in report['collector_replay'] if r['content_id'] == manifest['content_id'])
        self.assertEqual(record['status'], 'unavailable')
        self.assertIn('root.xsd', record['unmatched_documents'][0]['reason'])

    def test_equivalent_zip_cannot_hide_simultaneous_named_wsdl_binding(self):
        for command in ('auto_update', 'update_catalog', 'replay'):
            with self.subTest(command=command), patch.dict('os.environ', SEDO_COLLECTOR_SOURCE='sourcecraft'):
                fixture = sources.SourceCollectionTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                page = SITES['prod']
                document_path = '/specs_sedo/Тип_сообщения_999.docx'
                before_url = page.replace('/sedo.html', document_path)
                zip_url = page.replace('/sedo.html', '/specs_sedo/file.zip')
                fixture.resources[page] += f'<a href="{document_path}">Individual 999</a>'.encode()
                fixture.resources[before_url] = sources.individual_document('r', 'root.xsd')
                fixture.resources[zip_url] = sources.archive('root.xsd', NAMED)
                seed = 'https://sedo.fss.ru/service/SedoGatewaySimple?wsdl'
                objects = {}

                def blob(raw):
                    digest = sha(raw)
                    objects[digest] = raw
                    return digest

                resources = {seed: definitions(include('root.xsd')),
                             seed.rsplit('/', 1)[0] + '/root.xsd': NAMED}
                library = collect(seed, 'prod', resources.__getitem__, blob)
                for digest, raw in objects.items():
                    write(fixture.state / 'objects' / digest, raw)
                fixture.collect(1, wsdl_entries=[library])
                out = fixture.root / 'data'
                export(fixture.state, out / 'catalog.json')
                build(out / 'catalog.json', fixture.state, out / 'fields')
                expected, = (r for r in derive(fixture.current())['revisions'] if r['number'] == '999')
                self.assertEqual(expected['archive_url'], zip_url)

                new_url = before_url.replace('999.docx', '999_v2.docx')
                fixture.resources[new_url] = fixture.resources.pop(before_url)
                fixture.resources[page] = fixture.resources[page].replace(b'999.docx', b'999_v2.docx')
                fixture.resources[zip_url] = sources.archive('unrelated.xsd', (SCHEMA + NEIGHBOR + '</xs:schema>').encode())
                if command == 'replay':
                    fixture.collect(2, wsdl_entries=[])
                before = self.confirmed(fixture, expected)
                next_library = collect(seed, 'prod', {seed: definitions(NEIGHBOR.replace('name="r"', 'name="other"'))}.__getitem__, blob)
                remote = fixture.root / 'feed'
                manifest = make_manifest(next_library, '2026-11-14T02:30:00+00:00')
                store_feed(remote, [manifest], objects)
                import_feed(fixture.state, lambda name: (remote / name).read_bytes())
                if command == 'replay':
                    row, = (r for r in replay_archived_inputs(fixture.state) if r['content_id'] == manifest['content_id'])
                    self.assertEqual(row['status'], 'unavailable')
                    self.assertIn('root.xsd', row['unmatched_documents'][0]['reason'])
                else:
                    shutil.copytree(PROJECT / 'web', fixture.root / 'web')
                    shutil.copy2(PROJECT / 'viewer-template.html', fixture.root)
                    with self.assertRaisesRegex(ValueError, r'999 .*999_v2\.docx.*root\.xsd'):
                        self.update(fixture, command)
                self.assertEqual(self.confirmed(fixture, expected), before)

    def test_same_member_bytes_with_swapped_chameleon_edges_are_not_equivalent(self):
        def schema(body, namespace=None):
            target = f' targetNamespace="{namespace}"' if namespace else ''
            return (f'<xs:schema xmlns:xs="{wsdl.XS}" xmlns:a="urn:a" xmlns:b="urn:b" '
                    f'elementFormDefault="qualified"{target}>' + body + '</xs:schema>').encode()

        def datatype(pattern):
            return schema('<xs:simpleType name="Code"><xs:restriction base="xs:string"/></xs:simpleType>'
                          f'<xs:simpleType name="Variant"><xs:restriction base="Code"><xs:pattern value="{pattern}"/>'
                          '</xs:restriction></xs:simpleType>')

        def verdicts(directory, row, raw):
            with ZipFile(BytesIO(raw)) as archive:
                output, _, _ = _prepare(row, _selected(row, archive))
            for path, content in output.items():
                write(directory / path, content)
            validator = compile_schema(directory / 'message.xsd', directory)
            xml = ('<r xmlns="urn:main" xmlns:a="urn:a" xmlns:b="urn:b" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                   '<first xsi:type="a:Variant">{}</first><second xsi:type="b:Variant">{}</second></r>')
            return [validator.validate(etree.fromstring(xml.format(first, second)))
                    for first, second in (('a', 'b'), ('b', 'a'))]

        root = schema('<xs:import namespace="urn:a" schemaLocation="a/wrapper.xsd"/>'
                      '<xs:import namespace="urn:b" schemaLocation="b/wrapper.xsd"/>'
                      '<xs:element name="r"><xs:complexType><xs:sequence>'
                      '<xs:element name="first" type="a:Code"/><xs:element name="second" type="b:Code"/>'
                      '</xs:sequence></xs:complexType></xs:element>', 'urn:main')

        for command in ('auto_update', 'update_catalog', 'replay'):
            with self.subTest(command=command), patch.dict('os.environ', SEDO_COLLECTOR_SOURCE='sourcecraft'):
                fixture = sources.SourceCollectionTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                for page in SITES.values():
                    fixture.resources[page] = b'<a href="/specs_sedo/file.zip">Schema</a>'
                fixture.add_individual('999', 'r', 'root.xsd')
                library, objects = wsdl.WsdlSourcesTests().collect({
                    wsdl.SEED: definitions(include('root.xsd')), BASE + 'root.xsd': root,
                    BASE + 'a/wrapper.xsd': schema(include('value.xsd'), 'urn:a'),
                    BASE + 'b/wrapper.xsd': schema(include('value.xsd'), 'urn:b'),
                    BASE + 'a/value.xsd': datatype('a'), BASE + 'b/value.xsd': datatype('b'),
                })
                for digest, raw in objects.items():
                    write(fixture.state / 'objects' / digest, raw)
                fixture.collect(1, wsdl_entries=[library])
                out = fixture.root / 'data'
                export(fixture.state, out / 'catalog.json')
                build(out / 'catalog.json', fixture.state, out / 'fields')
                expected, = derive(fixture.current())['revisions']
                original = objects[library['sha256']]
                self.assertEqual(verdicts(fixture.root / 'before', expected, original), [True, False])
                with ZipFile(BytesIO(original)) as archive:
                    files = {path: archive.read(path) for path in archive.namelist()}

                def member(path):
                    return next(s['archive_path'] for s in library['source_files'] if s['url'] == BASE + path)

                swapped = _archive(SchemaFiles(files, import_targets={
                    member('a/wrapper.xsd'): {'value.xsd': member('b/value.xsd')},
                    member('b/wrapper.xsd'): {'value.xsd': member('a/value.xsd')},
                }))
                # The saved dependency list cannot distinguish these inputs,
                # whereas the compiled schema accepts different XML.
                from sync import inspect_file
                swapped_meta = inspect_file(swapped, 'zip')
                self.assertEqual(dependency_set(swapped_meta['schemas'], expected['schema_path']), expected['dependencies'])
                self.assertEqual(verdicts(fixture.root / 'swapped', expected, swapped), [False, True])
                zip_url = SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')
                fixture.resources[zip_url] = swapped
                if command == 'replay':
                    fixture.collect(2, wsdl_entries=[])
                before = self.confirmed(fixture, expected)
                manifest = self.feed(fixture, {wsdl.SEED: definitions(NEIGHBOR.replace('name="r"', 'name="other"'))},
                                     hour='02')
                if command == 'replay':
                    row, = (r for r in replay_archived_inputs(fixture.state) if r['content_id'] == manifest['content_id'])
                    self.assertEqual(row['status'], 'unavailable')
                    self.assertIn('root.xsd', row['unmatched_documents'][0]['reason'])
                else:
                    shutil.copytree(PROJECT / 'web', fixture.root / 'web')
                    shutil.copy2(PROJECT / 'viewer-template.html', fixture.root)
                    with self.assertRaisesRegex(ValueError, r'999 .*root\.xsd'):
                        self.update(fixture, command)
                self.assertEqual(self.confirmed(fixture, expected), before)


class ReplayedBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture = sources.SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for page in SITES.values():
            fixture.resources[page] = b'<a href="/specs_sedo/file.zip">Schema</a>'
        self.url = fixture.add_individual('999', 'r', 'root.xsd')
        shutil.copytree(PROJECT / 'web', fixture.root / 'web')
        shutil.copy2(PROJECT / 'viewer-template.html', fixture.root)
        self.index = fixture.root / 'data/catalog.json'
        self.a = {wsdl.SEED: definitions(include('root.xsd')),
                  BASE + 'root.xsd': NAMED.replace(b'name="r"', b'name="other"')}
        self.b = {wsdl.SEED: definitions(include('root.xsd')), BASE + 'root.xsd': NAMED}
        self.c = {wsdl.SEED: definitions(NEIGHBOR)}
        home = patch.dict('os.environ', SEDO_COLLECTOR_SOURCE='home')
        home.start()
        self.addCleanup(home.stop)
        prod = 'https://ecp.sfr.gov.ru/sedo/fss/api/soap/SedoGatewaySimple?wsdl'
        self.capture({prod: definitions(NEIGHBOR.replace('name="r"', 'name="prod"'))}, '01:00', env='prod')
        self.capture(self.a, '01:00')
        fixture.collect(1)
        export(fixture.state, self.index)
        build(self.index, fixture.state, fixture.root / 'data/fields')
        self.pointer = (fixture.state / 'current.json').read_bytes()

    def test_upgrade_restores_live_observation_and_checks_its_evidence(self):
        self.check_restoration(keep_rejection=False)

    def test_upgrade_restores_live_observation_with_another_rejection(self):
        self.check_restoration(keep_rejection=True)

    def check_restoration(self, *, keep_rejection):
        from copy import deepcopy
        fixture, state = self.fixture, self.fixture.state
        def add(number):
            path = f'/specs_sedo/m{number}.docx'
            page = SITES['test']
            fixture.resources[page] += f'<a href="{path}">Module {number}</a>'.encode()
            fixture.resources[page.removesuffix('/sedo.html') + path] = quoted_document(
                f'Тип сообщения {number}: Example',
                f'<xs:schema xmlns:xs="{wsdl.XS}"><xs:element name="r{number}" type="xs:string"/></xs:schema>')
        add(42)
        receipt = self.capture(self.c, '02:00')
        fixture.collect(2)
        key = json.loads((state / 'current.json').read_bytes())['snapshot']
        row = next(r for r in derive(fixture.current())['revisions'] if r['number'] == '42')
        add(43)
        fixture.collect(3)
        ledger = processed_manifests(state)
        selected_records(ledger)['test'][receipt]['module_decision_version'] = 1
        ledger['rejected_bindings'] = {key: [dict(observation=binding_observation_identity(row), reason='Прежний отзыв')]}
        if keep_rejection:
            other = next(r for r in derive(fixture.current())['revisions'] if r['number'] == '43')
            later = json.loads((state / 'current.json').read_bytes())['snapshot']
            ledger['rejected_bindings'][later] = [dict(observation=binding_observation_identity(other), reason='Отдельный отзыв')]
        write(state / 'collector/processed.json', encode(ledger))
        export(state, self.index)
        previous = json.loads(self.index.read_bytes())
        # Installed pre-upgrade data has no immutable nested source evidence.
        previous['rejected_binding_observations'][0].pop('source_observation')
        write(self.index, encode(previous))
        self.assertEqual(pending_work(fixture.root), 'history')
        original_replace = Path.replace
        def interrupted(path, target):
            if path == fixture.root / '.update-recovery/candidate/data':
                raise KeyboardInterrupt()
            return original_replace(path, target)
        with patch('sync.get', side_effect=AssertionError('unexpected network')):
            with patch.object(Path, 'replace', interrupted), self.assertRaises(KeyboardInterrupt):
                apply_pending(fixture.root)
            self.assertTrue((fixture.root / '.update-recovery/journal.json').is_file())
            apply_pending(fixture.root)
        rid = row['revision_id']
        current = json.loads(self.index.read_bytes())
        event = next(e for e in current['updates'] if e['revision_id'] == rid)
        self.assertEqual(event['snapshot'], key)
        remaining = current.get('rejected_binding_observations', [])
        self.assertEqual([r['number'] for r in remaining], ['43'] if keep_rejection else [])
        if remaining:
            self.assertEqual(remaining[0]['update_event'], previous['rejected_binding_observations'][-1]['update_event'])
        self.assertTrue((fixture.root / 'site/schemas' / (rid + '.zip')).is_file())
        self.assertIsNone(pending_work(fixture.root))
        for what in ('source', 'time', 'owner'):
            with self.subTest(what=what):
                poisoned = deepcopy(current)
                if what == 'source':
                    poisoned['update_sources'][rid]['document_url'] += '#bad'
                elif what == 'time':
                    next(e for e in poisoned['updates'] if e['revision_id'] == rid)['observed_at'] = None
                else:
                    next(r for r in poisoned['revisions'] if r['revision_id'] == rid)['root'] = 'other'
                with self.assertRaises(ValueError):
                    validate_saved_history(previous, poisoned, state=state)

    def capture(self, resources, time, *, env='test'):
        objects = {}
        def blob(raw):
            objects[sha(raw)] = raw
            return sha(raw)
        library = collect(next(iter(resources)), env, resources.__getitem__, blob)
        receipt = make_manifest(library, f'2026-11-14T{time}:00+00:00')
        if collector_source() == 'sourcecraft':
            remote = self.fixture.root / 'feed'
            store_feed(remote, [receipt], objects)
            import_feed(self.fixture.state, lambda name: (remote / name).read_bytes())
        else:
            store_local_inputs(self.fixture.state, [receipt], objects)
        return receipt['content_id']

    def replay(self):
        self.capture(self.a, '04:00')  # restore Home's newest library to the active collection
        self.assertEqual(pending_work(self.fixture.root), 'history')
        with patch('sync.get', side_effect=AssertionError('Unexpected network access')):
            apply_pending(self.fixture.root)
        self.assertEqual((self.fixture.state / 'current.json').read_bytes(), self.pointer)
        self.assertIsNone(pending_work(self.fixture.root))
        report = json.loads((self.fixture.root / 'update-report.json').read_bytes())
        return {r['content_id']: r for r in report['collector_replay']}

    def assert_missing(self, records, digest):
        record = records[digest]
        self.assertEqual(record['status'], 'unavailable')
        document, = record['unmatched_documents']
        self.assertEqual(document['number'], '999')
        self.assertIn('root.xsd', document['reason'])
        catalog = json.loads(self.index.read_bytes())
        only, = catalog['revisions']
        self.assertEqual(only['schema_docx_location'], 'root.xsd')
        self.assertFalse(only['listed_now'])

    def test_replay_establishes_and_reuses_binding_within_one_import(self):
        b = self.capture(self.b, '02:00')
        c = self.capture(self.c, '03:00')
        records = self.replay()
        self.assertEqual(records[b]['status'], 'replayed')
        self.assert_missing(records, c)

    def test_pending_live_collection_checks_binding_replayed_in_same_transaction(self):
        self.capture(self.b, '02:00')
        self.capture(self.c, '03:00')
        self.assertEqual(pending_work(self.fixture.root), 'collection')
        with patch('sync.get', side_effect=self.fixture.resources.__getitem__), \
                self.assertRaisesRegex(ValueError, r'999 .*root\.xsd'):
            apply_pending(self.fixture.root)
        self.assertEqual((self.fixture.state / 'current.json').read_bytes(), self.pointer)

    def test_replay_retains_binding_from_previous_import(self):
        b = self.capture(self.b, '02:00')
        self.assertEqual(self.replay()[b]['status'], 'replayed')
        c = self.capture(self.c, '03:00')
        self.assert_missing(self.replay(), c)

    def test_earlier_proof_rechecks_confirmed_replay_and_keeps_raw_history(self):
        c = self.capture(self.c, '03:00')
        old = self.replay()[c]
        self.assertEqual(old['status'], 'replayed')
        state = self.fixture.state
        archive = state / 'snapshots' / (old['snapshot'] + '.json')
        raw = archive.read_bytes()
        history = json.loads((state / 'history.json').read_bytes())
        b = self.capture(self.b, '02:00')
        records = self.replay()
        self.assert_missing(records, c)
        self.assertEqual(archive.read_bytes(), raw)
        self.assertEqual(json.loads((state / 'history.json').read_bytes()), [*history, records[b]['snapshot']])
        ledger_before = (state / 'collector/processed.json').read_bytes()
        self.assertEqual({r['content_id']: r for r in replay_archived_inputs(state)}, records)
        self.assertEqual((state / 'collector/processed.json').read_bytes(), ledger_before)

    def assert_late_graph_revocation(self, manual):
        from import_inbox import process_inbox
        from tests.test_resolved_schema_identity import library, verdicts

        fixture = self.fixture
        state = fixture.state
        a, objects_a = library('a', 'b')
        b, objects_b = library('b', 'a')
        doc = fixture.resources[self.url]
        if manual:
            folder = fixture.root / 'incoming/test'
            folder.mkdir(parents=True)
            (folder / '999.docx').write_bytes(doc)
            (folder / 'schema.zip').write_bytes(objects_a[a['sha256']])
            process_inbox(fixture.root)
        zip_url = SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')
        fixture.resources[zip_url] = objects_b[b['sha256']]
        fixture.collect(3)
        self.pointer = (state / 'current.json').read_bytes()
        c = self.capture(self.c, '03:30')
        old = self.replay()[c]
        initial = json.loads(self.index.read_bytes())
        good = next((r for r in initial['revisions'] if r['archive_sha256'] == a['sha256']), None)
        bad, = [r for r in initial['revisions'] if r['archive_sha256'] == b['sha256']]
        bad_id = bad['revision_id']
        snapshot = state / 'snapshots' / (old['snapshot'] + '.json')
        raw = snapshot.read_bytes()
        site = fixture.root / 'site'
        self.assertTrue((site / 'schemas' / (bad_id + '.zip')).is_file())
        self.assertEqual(verdicts(fixture, bad), [False, True])

        manifest = make_manifest(a, '2026-11-14T02:00:00+00:00')
        store_local_inputs(state, [manifest], objects_a)
        records = self.replay()
        self.assert_missing(records, c)
        self.assertEqual(snapshot.read_bytes(), raw)
        actual = json.loads(self.index.read_bytes())
        [replacement] = actual['revisions']
        self.assertEqual(replacement['archive_sha256'], a['sha256'])
        self.assertNotEqual(replacement['revision_id'], bad_id)
        self.assertEqual(verdicts(fixture, replacement), [True, False])
        if good:
            self.assertEqual(replacement['revision_id'], good['revision_id'])
            self.assertEqual(actual['updates'], [event for event in initial['updates']
                                                if event['revision_id'] == good['revision_id']])
        self.assertEqual({row['revision_id'] for row in actual['updates']}, {replacement['revision_id']})
        [revoked] = actual['revoked_revisions']
        [rejected] = actual['rejected_binding_observations']
        old_event, = [event for event in initial['updates'] if event['revision_id'] == bad_id]
        self.assertEqual(rejected['update_event'], old_event)
        self.assertEqual(rejected['snapshot'], old['snapshot'])
        self.assertEqual(rejected['observed_at'], old['observed_at'])
        report = json.loads((fixture.root / 'update-report.json').read_bytes())
        self.assertEqual(report['revoked_revisions'], [revoked])
        self.assertEqual(revoked['revision_id'], bad_id)
        self.assertEqual(revoked['resolved_schema_graph'], bad['resolved_schema_graph'])
        self.assertIn('root.xsd', revoked['schema_reason'])
        self.assertIn('Редакция отозвана', (site / 'editions' / (bad_id + '.html')).read_text('utf8'))
        self.assertEqual((site / 'documents' / (bad['document_sha256'] + '.docx')).read_bytes(), doc)
        self.assertFalse((site / 'schemas' / (bad_id + '.zip')).exists())
        self.assertTrue((site / 'schemas' / (replacement['revision_id'] + '.zip')).is_file())
        ledger = state / 'collector/processed.json'
        confirmed = ledger.read_bytes()
        self.assertEqual({r['content_id']: r for r in replay_archived_inputs(state)}, records)
        self.assertEqual(ledger.read_bytes(), confirmed)

        # The original ledger recorded a standalone ID without historical graph reservations.
        old_id, = (r['revision_id'] for r in derive(json.loads(raw))['revisions'])
        processed = json.loads(confirmed)
        processed['rejected_bindings'][old['snapshot']] = {old_id: revoked['schema_reason']}
        write(ledger, json.dumps(processed).encode())
        export(state, self.index)
        recovered = json.loads(self.index.read_bytes())
        self.assertEqual(recovered.pop('checked_at'), '2026-11-14T03:00:00+00:00')
        self.assertEqual(actual.pop('checked_at'), '2026-11-14T01:00:00+00:00')
        self.assertEqual(recovered, actual)

    def test_late_graph_revocation_preserves_an_existing_manual_id(self):
        self.assert_late_graph_revocation(manual=True)

    def test_late_graph_revocation_reserves_the_original_id(self):
        self.assert_late_graph_revocation(manual=False)

    def test_late_graph_revocation_uses_a_surviving_sibling_as_predecessor(self):
        from copy import deepcopy
        from catalog import binding_observation_identity
        from import_inbox import process_inbox
        from schema import archive_targets
        from tests.test_resolved_schema_identity import library

        fixture = self.fixture
        state, site = fixture.state, fixture.root / 'site'
        good, good_objects = library('a', 'b')
        bad, bad_objects = library('b', 'a')
        zip_url = SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')
        sibling_href = '/specs_sedo/archive/Тип_сообщения_999.docx'
        sibling_url = SITES['test'].replace('/sedo.html', sibling_href)
        old_sibling = (SCHEMA + '<xs:element name="s" type="xs:string"/></xs:schema>').encode()
        new_sibling = old_sibling.replace(b'xs:string', b'xs:int')
        fixture.resources[SITES['test']] = (f'<a href="{sibling_href}">Sibling v2</a>'.encode()
                                           + fixture.resources[SITES['test']])
        fixture.resources[sibling_url] = sources.individual_document('s', 'sibling.xsd')
        with ZipFile(BytesIO(bad_objects[bad['sha256']])) as archive:
            files = SchemaFiles({name: archive.read(name) for name in archive.namelist() if name.endswith('.xsd')},
                                import_targets=archive_targets(archive))
        files['old-sibling.xsd'] = old_sibling
        fixture.resources[zip_url] = _archive(files)
        fixture.collect(3)
        self.pointer = (state / 'current.json').read_bytes()
        capture = self.capture({wsdl.SEED: definitions(include('sibling.xsd')),
                                BASE + 'sibling.xsd': new_sibling}, '03:30')
        record = self.replay()[capture]
        self.assertEqual(record['status'], 'replayed')
        original_snapshot = state / 'snapshots' / (record['snapshot'] + '.json')
        original_bytes = original_snapshot.read_bytes()
        initial = json.loads(self.index.read_bytes())
        bad_row, = [r for r in initial['revisions'] if r['root'] == 'r']
        sibling_row, = [r for r in initial['revisions'] if r['root'] == 's' and r.get('schema_source_type') == 'wsdl']
        a, b = sibling_row['revision_id'], bad_row['revision_id']
        folder = fixture.root / 'incoming/test'
        folder.mkdir(parents=True)
        (folder / '999_v3.docx').write_bytes(sources.individual_document('manual'))
        c = process_inbox(fixture.root)['files'][0]['revision_id']
        before = json.loads(self.index.read_bytes())
        events = {r['revision_id']: r for r in before['updates']}
        baseline_id = events[a]['previous_revision_id']
        self.assertEqual(events[b]['previous_revision_id'], baseline_id)
        self.assertEqual(events[c]['previous_revision_id'], b)
        self.assertTrue((site / 'schemas' / (b + '.zip')).is_file())

        isolated = fixture.root / 'alternate-state'
        shutil.copytree(state, isolated)
        for revoked, expected in (([sibling_row], b), ([sibling_row, bad_row], baseline_id)):
            with self.subTest(revoked=[row['revision_id'] for row in revoked]):
                ledger = processed_manifests(state)
                ledger['rejected_bindings'] = {record['snapshot']: [
                    dict(observation=binding_observation_identity(row), reason='Подтверждённый отзыв') for row in revoked]}
                write(isolated / 'collector/processed.json', json.dumps(ledger).encode())
                candidate = fixture.root / 'alternate-catalog.json'
                export(isolated, candidate)
                alternate = json.loads(candidate.read_bytes())
                validate_saved_history(before, alternate)
                actual = {r['revision_id']: r for r in alternate['updates']}
                self.assertEqual(actual[c]['previous_revision_id'], expected)
                raw = alternate.get('original_update_events', {}).get(c, actual[c])
                self.assertEqual(raw, events[c])

        manifest = make_manifest(good, '2026-11-14T02:00:00+00:00')
        store_local_inputs(state, [manifest], good_objects)
        self.capture(self.a, '04:00')
        self.assertEqual(pending_work(fixture.root), 'history')
        with patch('sync.get', side_effect=AssertionError('Unexpected network access')):
            apply_pending(fixture.root)
        self.assertEqual(original_snapshot.read_bytes(), original_bytes)
        after = json.loads(self.index.read_bytes())
        actual = {r['revision_id']: r for r in after['updates']}
        self.assertEqual(actual[c]['previous_revision_id'], a)
        self.assertEqual(after['original_update_events'][c], events[c])
        public = json.loads((site / 'catalog-data.js').read_text('utf8').removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
        [shown] = [r for r in public['schema_updates'] if r['revision_id'] == c]
        self.assertEqual(shown['previous_revision_id'], a)
        rejected = [row for row in after['rejected_binding_observations'] if row['revision_id'] == b]
        self.assertEqual(rejected[0]['update_event'], events[b])
        report_path = fixture.root / 'update-report.json'
        report_bytes = report_path.read_bytes()
        self.assertEqual([r['revision_id'] for r in json.loads(report_bytes)['revoked_revisions']], [b])
        self.assertFalse((site / 'schemas' / (b + '.zip')).exists())
        self.assertEqual((site / 'documents' / (bad_row['document_sha256'] + '.docx')).read_bytes(), fixture.resources[self.url])
        published = self.index.read_bytes()
        other_contour = next((r['revision_id'] for r in after['revisions'] if r['environment'] == 'prod'), None)
        if other_contour is None:
            other_contour = 'other-contour'
            other = dict(after['revisions'][0], revision_id=other_contour, environment='prod')
            after['revisions'].append(other)
            after['updates'].append(dict(events[c], revision_id=other_contour, previous_revision_id=None))
        for forged in (c, other_contour):
            invalid = deepcopy(after)
            next(r for r in invalid['updates'] if r['revision_id'] == c)['previous_revision_id'] = forged
            with self.assertRaisesRegex(ValueError, 'событие журнала|ссылка сравнения'):
                validate_saved_history(before, invalid)
            invalid = deepcopy(after)
            invalid['original_update_events'][c]['previous_revision_id'] = forged
            with self.assertRaisesRegex(ValueError, 'исходное событие журнала'):
                validate_saved_history(before, invalid)
        ledger = state / 'collector/processed.json'
        immutable = ledger.read_bytes()
        with patch('sync.get', side_effect=AssertionError('Unexpected network access')):
            apply_pending(fixture.root)
        self.assertEqual(ledger.read_bytes(), immutable)
        self.assertEqual(self.index.read_bytes(), published)
        self.assertEqual(report_path.read_bytes(), report_bytes)

    def assert_replay_retries_module(self, *, partial=False, ambiguous=False):
        from module_xsd import FORMAT8_QUOTATIONS, MODULE_MATCH_VERSION
        from sync import match_modules
        from tests.test_embedded_xsd import quoted_document

        def quoted_schema(prefix, namespace):
            return f'''<xs:schema xmlns:xs="{wsdl.XS}">
              <xs:simpleType name="Item"><xs:union memberTypes="xs:boolean xs:QName"/></xs:simpleType>
              <xs:simpleType name="Items"><xs:list itemType="Item"/></xs:simpleType>
              <xs:complexType name="Base"><xs:simpleContent><xs:extension base="Items"/></xs:simpleContent></xs:complexType>
              <xs:complexType name="Allowed"><xs:simpleContent><xs:restriction base="Base">
                <xs:simpleType><xs:restriction base="Items"/></xs:simpleType>
                <xs:enumeration xmlns:{prefix}="{namespace}" value="{prefix}:a {prefix}:b"/>
              </xs:restriction></xs:simpleContent></xs:complexType>
              <xs:element name="r" type="Allowed"/>
            </xs:schema>'''

        fixture = self.fixture
        state = fixture.state
        page = SITES['test']
        fixture.resources[page] = b'<a href="/specs_sedo/module.docx">Module</a><a href="/specs_sedo/file.zip">Schema</a>'
        fixture.resources[page.replace('/sedo.html', '/specs_sedo/module.docx')] = quoted_document(
            'Тип сообщения 42: Example', quoted_schema('p', 'urn:choice'),
            quoted_schema('q', 'urn:other' if ambiguous else 'urn:choice'))
        if partial:
            fixture.resources[page] += b'<a href="/specs_sedo/other.docx">Other</a>'
            fixture.resources[page.replace('/sedo.html', '/specs_sedo/other.docx')] = quoted_document(
                'Тип сообщения 43: Already matched',
                f'<xs:schema xmlns:xs="{wsdl.XS}"><xs:element name="other" type="xs:string"/></xs:schema>')
        fixture.collect(1, snapshot_format=8)
        self.pointer = (state / 'current.json').read_bytes()
        export(state, self.index)
        build(self.index, state, fixture.root / 'data/fields')
        digest = self.capture(self.c, '02:00')
        with patch('sync.match_modules', side_effect=lambda *args, **kwargs:
                   match_modules(*args, **kwargs, quotation_rules=FORMAT8_QUOTATIONS)), \
                patch('module_xsd.MODULE_MATCH_VERSION', 8):
            prior = self.replay()[digest]
        self.assertEqual(prior['status'], 'replayed' if partial else 'unavailable')
        [unmatched] = prior['unmatched_documents']
        self.assertIn('Неоднозначные схемы', unmatched['reason'])
        snapshot = state / 'snapshots' / (prior['document_snapshot'] + '.json')
        raw = snapshot.read_bytes()
        if partial:
            replay = state / 'snapshots' / (prior['snapshot'] + '.json')
            old_replay = replay.read_bytes()
        prior_catalog = json.loads(self.index.read_bytes())
        ledger = state / 'collector/processed.json'
        legacy = processed_manifests(state)
        old = selected_records(legacy)['test'][digest]
        del old['module_match_version']
        old['unmatched_documents'][0].pop('document_url')
        write(ledger, json.dumps(legacy).encode())

        records = self.replay()
        updated = records[digest]
        self.assertEqual(updated['status'], 'unavailable' if ambiguous else 'replayed')
        self.assertEqual(updated['module_match_version'], MODULE_MATCH_VERSION)
        self.assertEqual(snapshot.read_bytes(), raw)
        self.assertEqual(sha(raw), updated['document_snapshot'])
        attempts = [row for row in processed_manifests(state)['matcher_attempts'] if row['content_id'] == digest]
        self.assertEqual(attempts, [dict(source='home', environment='test', content_id=digest, **old)])
        if ambiguous:
            self.assertIn('Неоднозначные схемы', updated['unmatched_documents'][0]['reason'])
        else:
            self.assertNotIn('unmatched_documents', updated)
            actual = json.loads(self.index.read_bytes())
            self.assertTrue(any(r['number'] == '42' and r['environment'] == 'test' for r in actual['revisions']))
            if partial:
                self.assertEqual(replay.read_bytes(), old_replay)
                current_rows = {row['revision_id']: row for row in actual['revisions']}
                self.assertTrue(prior_catalog['revisions'])
                for row in prior_catalog['revisions']:
                    self.assertEqual(current_rows[row['revision_id']], row)
        confirmed = ledger.read_bytes()
        with patch('sync.match_modules', side_effect=AssertionError('Historical evidence already checked')):
            self.assertEqual({r['content_id']: r for r in replay_archived_inputs(state)}, records)
        self.assertEqual(ledger.read_bytes(), confirmed)

    def test_upgraded_module_matching_retries_old_unavailable_decision(self):
        self.assert_replay_retries_module()

    def test_upgraded_module_matching_preserves_successful_parts(self):
        self.assert_replay_retries_module(partial=True)

    def test_upgraded_module_matching_records_ambiguity_once(self):
        self.assert_replay_retries_module(ambiguous=True)

    def assert_recorded_matcher_upgrade(self, *, ambiguous=False, false_success=False, independent=False,
                                        replayed=False, repeated=False, active=False):
        from module_xsd import FORMAT10_QUOTATIONS, MODULE_MATCH_VERSION
        from sync import match_modules
        from tests.test_embedded_xsd import chameleon_quotation, quoted_document
        from tests.test_schema_recovery import archive

        fixture = self.fixture
        state = fixture.state
        page = SITES['test']
        original_page = fixture.resources[page]
        prefix = page.replace('/sedo.html', '/specs_sedo/')
        doc, control = prefix + 'module.docx', prefix + 'control.docx'

        def quote(p, uri):
            return f'''<xs:schema xmlns:xs="{wsdl.XS}">
            <xs:simpleType name="Union"><xs:union memberTypes="xs:boolean xs:QName"/></xs:simpleType>
            <xs:simpleType name="Items"><xs:list itemType="Union"/></xs:simpleType>
            <xs:complexType name="Base"><xs:simpleContent><xs:extension base="Items"/></xs:simpleContent></xs:complexType>
            <xs:complexType name="Allowed"><xs:simpleContent><xs:restriction base="Base">
            <xs:simpleType><xs:restriction base="Items"/></xs:simpleType>
            <xs:enumeration xmlns:{p}="{uri}" value="{p}:a {p}:b"/>
            </xs:restriction></xs:simpleContent></xs:complexType><xs:element name="r" type="Allowed"/>
            </xs:schema>'''

        fixture.resources[control] = quoted_document('Тип сообщения 43: Control',
            f'<xs:schema xmlns:xs="{wsdl.XS}"><xs:element name="control" type="xs:string"/></xs:schema>')
        fixture.resources[page] = (b'<a href="/specs_sedo/module.docx">Module</a>'
                                   b'<a href="/specs_sedo/control.docx">Control</a>'
                                   b'<a href="/specs_sedo/file.zip">Schema</a>')
        if false_success:
            fixture.resources[doc] = quoted_document('Тип сообщения 42: Example', '<r/>')
            for filename, uri in (('a.zip', 'urn:p'), ('b.zip', 'urn:q')):
                fixture.resources[prefix + filename] = archive(chameleon_quotation(uri))
            fixture.resources[page] += b'<a href="/specs_sedo/a.zip">A</a>'
            if independent:
                fixture.collect(2, snapshot_format=10, individual_wsdl=3)
            fixture.resources[page] += b'<a href="/specs_sedo/b.zip">B</a>'
        else:
            fixture.resources[doc] = quoted_document('Тип сообщения 42: Example', quote('p', 'urn:ok'),
                                                    quote('q', 'urn:other' if ambiguous else 'urn:ok'))

        old_version = 10 if false_success else 8
        with patch('module_xsd.MODULE_MATCH_VERSION', old_version):
            if replayed:
                fixture.collect(2, snapshot_format=2, individual_wsdl=3)
                self.assertFalse(any(r['number'] == '42' for r in derive(fixture.current())['revisions']))
                self.pointer = (state / 'current.json').read_bytes()
            library = ({wsdl.SEED: definitions(NEIGHBOR.replace('name="r"', 'name="unrelated"'))}
                       if replayed else self.c)
            target = self.capture(library, '03:00')
            if replayed:
                with patch('sync.match_modules', side_effect=lambda *args, **kwargs:
                           match_modules(*args, **kwargs, quotation_rules=FORMAT10_QUOTATIONS)):
                    self.replay()
            else:
                fixture.collect(3, snapshot_format=old_version, individual_wsdl=3)
                first = fixture.current()
                if repeated:
                    fixture.resources[page] = fixture.resources[page].replace(b'>Control<', b'>Updated control<')
                fixture.collect(4, snapshot_format=old_version, individual_wsdl=3)
                if repeated:
                    self.assertNotEqual(first, fixture.current())
            receipts = state / 'collector/processed.json'
            ledger = json.loads(receipts.read_bytes())
            record = selected_records(ledger)['test'][target]
            self.assertEqual(record['status'], 'replayed' if replayed else 'recorded')
            historic_path = state / 'snapshots' / (record['snapshot'] + '.json')
            raw = historic_path.read_bytes()
            historic = json.loads(raw)
            if not replayed:
                self.assertEqual(historic['format'], old_version)
            initial = [r for r in derive(historic)['revisions'] if r['number'] == '42']
            self.assertEqual(len(initial), int(false_success))
            # Cover receipts written before matcher evidence was included.
            if not replayed:
                record = dict(status='recorded', snapshot=record['snapshot'], observed_at=record['observed_at'])
            if not active:
                fixture.resources[page] = original_page
            self.capture(self.a, '05:00')
            fixture.collect(5, snapshot_format=old_version, individual_wsdl=3)
        ledger = json.loads(receipts.read_bytes())
        test_records = selected_records(ledger)['test']
        test_records[target] = record
        earlier_attempts = [row for row in ledger.get('matcher_attempts', ()) if row['content_id'] == target]
        if repeated:
            selected_records(ledger)['test'] = dict(reversed(list(test_records.items())))
        write(receipts, json.dumps(ledger).encode())

        pointer = (state / 'current.json').read_bytes()
        self.assertEqual(doc in {row['url'] for row in fixture.current()['entries']}, active)
        export(state, self.index)
        build(self.index, state, fixture.root / 'data/fields')
        previous = json.loads(self.index.read_bytes())
        [control_row] = [row for row in previous['revisions'] if row['number'] == '43']
        before_snapshots = set((state / 'snapshots').glob('*.json'))
        observed_snapshots = {path: sha(path.read_bytes()) for path in before_snapshots} if repeated else {}
        self.assertEqual(pending_work(fixture.root), 'history')
        if repeated and not independent and not active:
            before_ledger = receipts.read_bytes()
            with patch('auto_update.promote', side_effect=OSError('interrupted promotion')):
                with patch('sync.get', side_effect=AssertionError('Unexpected network access')):
                    with self.assertRaisesRegex(OSError, 'interrupted promotion'):
                        apply_pending(fixture.root)
            self.assertEqual(receipts.read_bytes(), before_ledger)
            self.assertEqual((state / 'current.json').read_bytes(), pointer)
            self.assertEqual(pending_work(fixture.root), 'history')
        with patch('sync.get', side_effect=AssertionError('Unexpected network access')):
            apply_pending(fixture.root)
        self.assertEqual({path: sha(path.read_bytes()) for path in observed_snapshots}, observed_snapshots)
        self.assertEqual(historic_path.read_bytes(), raw)
        self.assertEqual(sha(raw), record['snapshot'])
        self.assertEqual((state / 'current.json').read_bytes(), pointer)
        after = json.loads(self.index.read_bytes())
        self.assertIn(control_row, after['revisions'])
        ledger = json.loads(receipts.read_bytes())
        updated = selected_records(ledger)['test'][target]
        self.assertEqual(updated['module_match_version'], MODULE_MATCH_VERSION)
        self.assertEqual([row for row in ledger['matcher_attempts'] if row['content_id'] == target],
                         [*earlier_attempts, dict(source=collector_source(), environment='test', content_id=target, **record)])
        site = fixture.root / 'site'
        if false_success:
            old, = [row for row in previous['revisions'] if row['number'] == '42']
            rid = old['revision_id']
            self.assertEqual([r['revision_id'] for r in after['revisions'] if r['number'] == '42'],
                             [rid] if independent else [])
            dropped = [row for row in after['rejected_binding_observations'] if row['number'] == '42']
            self.assertEqual(len(dropped), 3 if repeated and active else 2 if repeated else 1)
            for row in dropped:
                self.assertEqual(row['revision_id'], rid)
                self.assertIn('Неоднозначные схемы', row['schema_reason'])
            self.assertEqual((site / 'schemas' / (rid + '.zip')).is_file(), independent)
            self.assertEqual((site / 'schemas' / (rid + '.validation.json.gz')).is_file(), independent)
            self.assertEqual((site / 'documents' / (old['document_sha256'] + '.docx')).read_bytes(), fixture.resources[doc])
            if active:
                available, = [r for r in after['document_sources'] if r['number'] == '42']
                self.assertTrue(available['listed_now'])
                report = json.loads((fixture.root / 'update-report.json').read_bytes())
                source, = [r for r in report['schema_diagnostics'] if r['source'] == doc]
                self.assertEqual(source['environment'], 'test')
                issue, = [i for i in source['issues'] if i['number'] == '42']
                self.assertEqual(issue['reason'], available['schema_reason'])
                coverage = json.loads((fixture.root / 'data/schema-coverage.json').read_bytes())
                missing, = [r for r in coverage['missing'] if r['number'] == '42']
                self.assertEqual(missing['reasons'], [issue['reason']])
            elif repeated:
                report = json.loads((fixture.root / 'update-report.json').read_bytes())
                self.assertFalse(any(r['source'] == doc for r in report['schema_diagnostics']))
        elif ambiguous:
            self.assertFalse(any(row['number'] == '42' for row in after['revisions']))
            self.assertIn('Неоднозначные схемы', updated['unmatched_documents'][0]['reason'])
            self.assertEqual(set((state / 'snapshots').glob('*.json')), before_snapshots)
        else:
            [edition] = [row for row in after['revisions'] if row['number'] == '42']
            self.assertEqual(edition['document_sha256'], sha(fixture.resources[doc]))
            self.assertTrue((site / 'schemas' / (edition['revision_id'] + '.zip')).is_file())
        self.assertIsNone(pending_work(fixture.root))
        confirmed = receipts.read_bytes()
        with patch('sync.match_modules', side_effect=AssertionError('Historical evidence already checked')):
            apply_pending(fixture.root)
        self.assertEqual(receipts.read_bytes(), confirmed)

    def test_upgraded_module_matching_retries_recorded_failure(self):
        self.assert_recorded_matcher_upgrade()

    def test_upgraded_module_matching_retries_sourcecraft_recorded_failure(self):
        with patch.dict('os.environ', SEDO_COLLECTOR_SOURCE='sourcecraft'):
            prod = 'https://ecp.sfr.gov.ru/sedo/fss/api/soap/SedoGatewaySimple?wsdl'
            self.capture({prod: definitions(NEIGHBOR.replace('name="r"', 'name="prod"'))}, '01:00', env='prod')
            self.capture(self.a, '01:00')
            self.assert_recorded_matcher_upgrade()

    def test_upgraded_module_matching_records_unchanged_recorded_ambiguity(self):
        self.assert_recorded_matcher_upgrade(ambiguous=True)

    def test_upgraded_module_matching_revokes_recorded_false_success(self):
        self.assert_recorded_matcher_upgrade(false_success=True)

    def test_upgraded_module_matching_revokes_replayed_false_success(self):
        self.assert_recorded_matcher_upgrade(false_success=True, replayed=True)

    def test_upgraded_module_matching_keeps_independently_proven_graph(self):
        self.assert_recorded_matcher_upgrade(false_success=True, independent=True)

    def test_upgraded_matching_revokes_each_repeated_observation(self):
        self.assert_recorded_matcher_upgrade(false_success=True, repeated=True)

    def test_upgraded_matching_keeps_independent_proof_beside_repeats(self):
        self.assert_recorded_matcher_upgrade(false_success=True, repeated=True, independent=True)

    def test_revoked_active_module_has_same_reason_in_report_and_coverage(self):
        self.assert_recorded_matcher_upgrade(false_success=True, repeated=True, active=True)

    def test_module_replay_extracts_the_archived_docx_with_current_rules(self):
        self.assert_current_archive_extraction()

    def test_module_replay_recovers_an_omitted_binding_from_archived_bytes(self):
        self.assert_current_archive_extraction(without_old_binding=True)

    def test_module_replay_recovers_omitted_binding_beside_a_previously_recognized_module(self):
        self.assert_current_archive_extraction(without_old_binding=True, mixed=True)

    def assert_current_archive_extraction(self, *, without_old_binding=False, mixed=False):
        from module_xsd import MODULE_MATCH_VERSION
        from sync import inspect_file
        from tests.test_embedded_xsd import quoted_document

        fixture = self.fixture
        page = SITES['test']
        previous_page = fixture.resources[page]
        url = page.replace('/sedo.html', '/specs_sedo/historical.docx')
        fixture.resources[page] += b'<a href="/specs_sedo/historical.docx">Module</a>'
        quotation = (f'<xs:schema xmlns:xs="{wsdl.XS}" id="{"x"*8200}">'
                     '<xs:element name="r" type="xs:string"/></xs:schema>')
        other = ['Тип сообщения 43: Control',
                 f'<xs:schema xmlns:xs="{wsdl.XS}"><xs:element name="t" type="xs:string"/></xs:schema>'] if mixed else []
        raw = fixture.resources[url] = quoted_document('Тип сообщения 42: Example', '<r/>', quotation, *other)
        fresh = sources.SourceCollectionTests()
        fresh.setUp()
        self.addCleanup(fresh.doCleanups)
        fresh.resources = dict(fixture.resources)
        fresh.collect(2, wsdl_entries=[])
        expected = {r['number']: r for r in derive(fresh.current())['revisions'] if r['document_url'] == url}
        self.assertEqual(set(expected), {'42', '43'} if mixed else {'42'})
        def legacy_inspection(content, kind, **options):
            metadata = inspect_file(content, kind, **options)
            if without_old_binding and kind == 'docx' and content == raw:
                retained = [b for b in metadata.pop('module_bindings') if b['number'] != '42']
                if retained:
                    metadata['module_bindings'] = retained
                metadata['embedded_archives'] = [a for a in metadata.get('embedded_archives', ())
                                                 if any(r['name'] == 't' for x in a['schemas'] for r in x['roots'])]
            return metadata
        with patch('module_xsd.MODULE_MATCH_VERSION', 7):
            target = self.capture(self.c, '03:00')
            with patch('sync.inspect_file', side_effect=legacy_inspection):
                fixture.collect(3, snapshot_format=7, individual_wsdl=3)
            old, = [row for row in fixture.current()['entries'] if row['url'] == url]
            self.assertEqual(bool(old.get('embedded_archives')), mixed)
            self.assertEqual({m['number'] for m in old.get('module_matches', ())}, {'43'} if mixed else set())
            self.assertEqual({b['number'] for b in old.get('module_bindings', ())},
                             {'43'} if mixed else set() if without_old_binding else {'42'})
            receipt = selected_records(processed_manifests(fixture.state))['test'][target]
            old_snapshot = fixture.state / 'snapshots' / (receipt['snapshot'] + '.json')
            fixture.resources[page] = previous_page
            self.capture(self.a, '05:00')
            fixture.collect(5, snapshot_format=7, individual_wsdl=3)
        old_bytes = old_snapshot.read_bytes()
        self.assertEqual(sha(old_bytes), receipt['snapshot'])
        self.assertEqual(pending_work(fixture.root), 'history')
        with patch('sync.get', side_effect=AssertionError('Unexpected network access')):
            apply_pending(fixture.root)
        catalog = json.loads(self.index.read_bytes())
        actual = {r['number']: r for r in catalog['revisions'] if r['document_url'] == url}
        self.assertEqual(set(actual), set(expected))
        # Newly discovered examples match today's fresh collection of the
        # same bytes. Older successful paths retain their published ID.
        for key in ('revision_id', 'schema_path', 'resolved_schema_graph', 'document_sha256', 'document_url'):
            self.assertEqual(actual['42'][key], expected['42'][key])
        self.assertEqual(old_snapshot.read_bytes(), old_bytes)
        self.assertEqual((fixture.state / 'objects' / sha(raw)).read_bytes(), raw)
        updated = selected_records(processed_manifests(fixture.state))['test'][target]
        self.assertEqual(updated['module_match_version'], MODULE_MATCH_VERSION)
        self.assertIsNone(pending_work(fixture.root))

    def test_restoring_one_binding_keeps_its_same_snapshot_siblings_revoked(self):
        from catalog import archived_graph_reader, binding_observation_identity
        from collector_snapshots import _clear_restored_bindings
        from tests.test_embedded_xsd import quoted_document

        fixture = self.fixture
        for env, page in SITES.items():
            for number in ('42', '43') if env == 'test' else ('42',):
                doc = f'/specs_sedo/module-{number}.docx'
                url = page.removesuffix('/sedo.html') + doc
                fixture.resources[page] += f'<a href="{doc}">Module {number}</a>'.encode()
                fixture.resources[url] = quoted_document(f'Тип сообщения {number}: Example',
                    f'<xs:schema xmlns:xs="{wsdl.XS}"><xs:element name="r" type="xs:string"/></xs:schema>')
        fixture.collect(2)
        snapshot = fixture.current()
        digest = json.loads((fixture.state / 'current.json').read_bytes())['snapshot']
        graphs = archived_graph_reader(fixture.state)
        rows = [r for r in derive(snapshot, archive_graph=graphs)['revisions'] if r['source_type'] == 'module']
        self.assertEqual(len(rows), 3)
        restored, = [r for r in rows if r['environment'] == 'test' and r['number'] == '42']
        revocations = [dict(observation=binding_observation_identity(r), reason='prior reason') for r in rows]
        ledger = {'rejected_bindings': {digest: revocations.copy()}}
        source = next(row for row in snapshot['entries'] if row['url'] == restored['document_url'])
        modules = {(restored['document_url'], restored['document_sha256']): source['module_bindings']}
        _clear_restored_bindings(ledger, digest, snapshot, 'test', modules, set(), graphs)
        self.assertEqual(ledger['rejected_bindings'][digest], [row for row in revocations
            if row['observation'] != binding_observation_identity(restored)])

    def test_module_matching_rechecks_changed_confirmed_inputs_at_same_version(self):
        from module_xsd import MODULE_MATCH_VERSION
        from tests.test_embedded_xsd import chameleon_quotation, quoted_document

        fixture = self.fixture
        state = fixture.state
        page = SITES['test']
        prefix = page.replace('/sedo.html', '/specs_sedo/')
        fixture.resources[page] += (b'<a href="/specs_sedo/module.docx">Module</a>'
                                    b'<a href="/specs_sedo/control.docx">Control</a>'
                                    b'<a href="/specs_sedo/a.zip">A</a>')
        fixture.resources[prefix + 'module.docx'] = quoted_document('Тип сообщения 42: Example', '<r/>')
        fixture.resources[prefix + 'control.docx'] = quoted_document('Тип сообщения 43: Control',
            f'<xs:schema xmlns:xs="{wsdl.XS}"><xs:element name="control" type="xs:string"/></xs:schema>')
        fixture.resources[prefix + 'a.zip'] = _archive(chameleon_quotation('urn:p'))
        fixture.collect(2)
        pointer = state / 'current.json'
        self.pointer = pointer.read_bytes()
        target = self.capture(self.c, '03:00')
        old = self.replay()[target]
        self.assertEqual(old['status'], 'replayed')
        self.assertEqual(old['module_match_version'], MODULE_MATCH_VERSION)
        previous = json.loads(self.index.read_bytes())
        [edition] = [row for row in previous['revisions'] if row['number'] == '42']

        fixture.resources[page] += b'<a href="/specs_sedo/b.zip">B</a>'
        fixture.resources[prefix + 'b.zip'] = _archive(chameleon_quotation('urn:q'))
        # A later run confirms what was published at the capture time itself.
        fixture.collect(3)
        recovered = pointer.read_bytes()
        self.assertEqual(pending_work(fixture.root), 'history')
        with patch('sync.get', side_effect=AssertionError('Unexpected network access')):
            apply_pending(fixture.root)
        self.assertEqual(pointer.read_bytes(), recovered)
        ledger_path = state / 'collector/processed.json'
        confirmed = ledger_path.read_bytes()
        updated = selected_records(json.loads(confirmed))['test'][target]
        self.assertEqual(updated['module_match_version'], MODULE_MATCH_VERSION)
        self.assertNotEqual(updated['replay_proof'], old['replay_proof'])
        self.assertIn('Неоднозначные схемы', updated['unmatched_documents'][0]['reason'])
        after = json.loads(self.index.read_bytes())
        self.assertIn(edition['revision_id'], {row['revision_id'] for row in after['revisions']})
        self.assertTrue((fixture.root / 'site/schemas' / (edition['revision_id'] + '.zip')).is_file())
        with patch('sync.match_modules', side_effect=AssertionError('Historical evidence already checked')):
            self.assertIsNone(pending_work(fixture.root))
            apply_pending(fixture.root)
        self.assertEqual(ledger_path.read_bytes(), confirmed)

    def test_carried_test_binding_does_not_survive_home_revocation(self):
        self.assert_copied_module_revocation('home', 'sourcecraft', 'test')

    def test_carried_prod_binding_does_not_survive_sourcecraft_revocation(self):
        self.assert_copied_module_revocation('sourcecraft', 'home', 'prod')

    def test_legacy_partial_replays_do_not_turn_two_copies_into_independent_observations(self):
        fixture = self.fixture
        state = fixture.state
        page = SITES['test']
        url = page.replace('/sedo.html', '/specs_sedo/module.docx')
        fixture.resources[page] += b'<a href="/specs_sedo/module.docx">Module</a>'
        document = fixture.resources[url] = quoted_document('Тип сообщения 42: Example',
            f'<xs:schema xmlns:xs="{wsdl.XS}"><xs:element name="z" type="xs:string"/></xs:schema>')
        with patch('module_xsd.MODULE_MATCH_VERSION', 10):
            fixture.collect(2, snapshot_format=10, individual_wsdl=3)
            self.pointer = (state / 'current.json').read_bytes()
            capture = self.capture(self.c, '03:00')
            self.replay()
        ledger = processed_manifests(state)
        records = selected_records(ledger)['test']
        original = records[capture]['snapshot']
        source = json.loads((state / 'snapshots' / (original + '.json')).read_bytes())
        [edition] = [r for r in derive(source)['revisions'] if r['number'] == '42']
        observation = binding_observation_identity(edition)
        history_file = state / 'history.json'
        history = json.loads(history_file.read_bytes())
        copies = []
        for version in (2, 3):
            prior = records[capture]
            ledger.setdefault('matcher_attempts', []).append(
                dict(source='home', environment='test', content_id=capture, **prior))
            # Previous versions could make another immutable snapshot for a
            # named-WSDL retry while leaving its module rows untouched.
            updated = dict(source, collector_observation=dict(source['collector_observation'],
                unmatched_documents=[dict(revision_id=None, number='999', reason=f'WSDL {version}')]))
            content = encode(updated)
            digest = sha(content)
            write(state / 'snapshots' / (digest + '.json'), content)
            records[capture] = dict(prior, snapshot=digest, wsdl_match_version=version)
            copies.append(digest)
        write(history_file, encode([*history, *copies]))
        reason = 'Подтверждённый отзыв'
        input_snapshot = json.loads(self.pointer)['snapshot']
        ledger['rejected_bindings'] = {key: [dict(observation=observation, reason=reason)] for key in
                                       (original, input_snapshot)}
        write(state / 'collector/processed.json', encode(ledger))
        export(state, self.index)
        catalog = json.loads(self.index.read_bytes())
        self.assertFalse(any(r['number'] == '42' for r in catalog['revisions']))
        self.assertEqual({r['snapshot'] for r in catalog['rejected_binding_observations'] if r['number'] == '42'},
                         {original, input_snapshot, *copies})
        self.assertEqual((state / 'objects' / edition['document_sha256']).read_bytes(), document)

    def test_repaired_observation_time_preserves_the_earlier_replay_receipt(self):
        fixture = self.fixture
        state = fixture.state
        page = SITES['test']
        url = page.replace('/sedo.html', '/specs_sedo/module.docx')
        fixture.resources[page] += b'<a href="/specs_sedo/module.docx">Module</a>'
        fixture.resources[url] = quoted_document('Тип сообщения 42: Example',
            f'<xs:schema xmlns:xs="{wsdl.XS}"><xs:element name="z" type="xs:string"/></xs:schema>')
        fixture.collect(2)
        self.pointer = (state / 'current.json').read_bytes()
        capture = self.capture(self.c, '03:00')
        original = self.replay()[capture]
        old_file = state / 'snapshots' / (original['snapshot'] + '.json')
        original_bytes = old_file.read_bytes()
        receipt = state / observation_path('home', 'test', capture)
        write(receipt, b'2026-11-14T03:15:00+00:00\n')
        self.assertEqual(pending_work(fixture.root), 'history')
        with patch('sync.get', side_effect=AssertionError('unexpected network')):
            apply_pending(fixture.root)
        ledger = processed_manifests(state)
        updated = selected_records(ledger)['test'][capture]
        self.assertEqual(updated['replay_proof'], original['replay_proof'])
        self.assertNotEqual(updated['snapshot'], original['snapshot'])
        self.assertIn(dict(source='home', **original), ledger.get('matcher_attempts', ()))
        self.assertEqual(old_file.read_bytes(), original_bytes)
        self.assertIsNone(pending_work(fixture.root))

    def assert_copied_module_revocation(self, owner, foreign, environment):
        f = self.fixture
        neighbor = 'prod' if environment == 'test' else 'test'
        host, other = SITES[environment], SITES[neighbor]
        original = f.resources[host]

        def make_url(page, name):
            return page.replace('/sedo.html', '/specs_sedo/' + name)

        for page in (host, other):
            f.resources[page] += b'<a href="/specs_sedo/module.docx">Module</a>'
        f.resources[host] += b'<a href="/specs_sedo/a.zip">A</a><a href="/specs_sedo/b.zip">B</a>'
        f.resources[make_url(host, 'module.docx')] = quoted_document('Тип сообщения 42: Example', '<r/>')
        f.resources[make_url(other, 'module.docx')] = quoted_document('Тип сообщения 43: Control',
            f'<xs:schema xmlns:xs="{wsdl.XS}"><xs:element name="z" type="xs:string"/></xs:schema>')
        f.resources[make_url(host, 'a.zip')] = _archive(chameleon_quotation('urn:p'))
        f.resources[make_url(host, 'b.zip')] = _archive(chameleon_quotation('urn:q'))
        seeds = {'test': wsdl.SEED, 'prod': 'https://ecp.sfr.gov.ru/sedo/fss/api/soap/SedoGatewaySimple?wsdl'}
        base = {'test': self.a, 'prod': {seeds['prod']: definitions(NEIGHBOR.replace('name="r"', 'name="prod"'))}}
        library = self.c if environment == 'test' else {
            seeds[environment]: definitions(NEIGHBOR.replace('name="r"', 'name="active"'))}
        with patch.dict('os.environ', SEDO_COLLECTOR_SOURCE=owner), patch('module_xsd.MODULE_MATCH_VERSION', 10):
            for env, resources in base.items():
                self.capture(resources, '01:00', env=env)
            self.capture(library, '03:00', env=environment)
            f.collect(3, snapshot_format=10, individual_wsdl=3)
            live = json.loads((f.state/'current.json').read_bytes())['snapshot']
            [published] = [r for r in derive(f.current())['revisions'] if r['number'] == '42']
            export(f.state, self.index)
            build(self.index, f.state, f.root/'data/fields')
            with patch.dict('os.environ', SEDO_COLLECTOR_SOURCE=foreign):
                foreign_id = self.capture({seeds[neighbor]: definitions(NEIGHBOR.replace('name="r"', 'name="changed"'))},
                                          '03:30', env=neighbor)
                self.capture(base[neighbor], '04:00', env=neighbor)
                self.capture(library, '04:00', env=environment)
                self.assertEqual(pending_work(f.root), 'history')
                with patch('sync.get', side_effect=AssertionError('unexpected network')):
                    apply_pending(f.root)
                receipt = selected_records(processed_manifests(f.state))[neighbor][foreign_id]
                self.assertEqual(receipt['status'], 'replayed')
                replayed = json.loads((f.state/'snapshots'/(receipt['snapshot']+'.json')).read_bytes())
                self.assertEqual(replayed['collector_observation']['environment'], neighbor)
                self.assertIn('42', {r['number'] for r in derive(replayed)['revisions']})
            f.resources[host] = original
            f.collect(5, snapshot_format=10, individual_wsdl=3)
        snapshots = {p: p.read_bytes() for p in (f.state/'snapshots').glob('*.json')}
        with patch.dict('os.environ', SEDO_COLLECTOR_SOURCE=owner):
            self.assertEqual(pending_work(f.root), 'history')
            with patch('sync.get', side_effect=AssertionError('unexpected network')):
                apply_pending(f.root)
            self.assertIsNone(pending_work(f.root))
        after = json.loads(self.index.read_bytes())
        affected = [r for r in after['revisions'] if r['number'] == '42']
        revoked = [r for r in after['rejected_binding_observations'] if r['number'] == '42']
        self.assertEqual(affected, [])
        self.assertEqual({r['snapshot'] for r in revoked}, {live, receipt['snapshot']})
        self.assertEqual([r['revision_id'] for r in after['revoked_revisions']], [published['revision_id']])
        schema = f.root/'site/schemas'/published['revision_id']
        self.assertFalse(schema.with_suffix('.zip').exists())
        self.assertFalse(schema.with_suffix('.validation.json.gz').exists())
        self.assertEqual({p: p.read_bytes() for p in snapshots}, snapshots)
        self.assertEqual((f.state/'objects'/published['document_sha256']).read_bytes(),
                         f.resources[make_url(host, 'module.docx')])

    def test_published_live_input_also_revoked_when_receipt_points_at_old_inventory(self):
        f = self.fixture
        page = SITES['test']
        prefix = page.replace('/sedo.html', '/specs_sedo/')
        f.resources[page] += b'<a href="/specs_sedo/module.docx">Module</a><a href="/specs_sedo/a.zip">A</a><a href="/specs_sedo/b.zip">B</a>'
        f.resources[prefix+'module.docx'] = quoted_document('Тип сообщения 42: Example','<r/>')
        for name, uri in [('a.zip','urn:p'), ('b.zip','urn:q')]:
            f.resources[prefix+name] = _archive(chameleon_quotation(uri))
        with patch('module_xsd.MODULE_MATCH_VERSION', 10):
            content_id = self.capture(self.c, '03:00')
            f.collect(3, snapshot_format=10, individual_wsdl=3)
        live_key = json.loads((f.state/'current.json').read_bytes())['snapshot']
        [edition] = [r for r in derive(f.current())['revisions'] if r['number']=='42']
        before = selected_records(processed_manifests(f.state))['test'][content_id]
        self.assertNotEqual(before.get('snapshot'), live_key)
        if old_key := before.get('snapshot'):
            old = json.loads((f.state/'snapshots'/(old_key+'.json')).read_bytes())
            self.assertFalse(any(r['number']=='42' for r in derive(old)['revisions']))
        active, snapshots, observations = replay_context(f.state)
        index = confirmed_binding_index(active, snapshots, observations)
        manifest = next(m for m in feed_manifests(f.state) if m['content_id'] == content_id)
        [checked_key, _], proof, _ = replay_candidate(manifest, active, snapshots, observations, index)
        self.assertEqual(checked_key, live_key)
        ledger = processed_manifests(f.state)
        selected_records(ledger)['test'][content_id].update(
            replay_proof=proof, module_match_version=11, module_input_version=1)
        ledger_path = f.state/'collector/processed.json'
        write(ledger_path, json.dumps(ledger).encode())
        before_ledger = ledger_path.read_bytes()
        export(f.state, self.index)
        build(self.index, f.state, f.root/'data/fields')
        self.assertEqual(pending_work(f.root), 'history')
        with patch('sync.get', side_effect=AssertionError('unexpected network')):
            with patch('auto_update.promote', side_effect=OSError('interrupted promotion')):
                with self.assertRaisesRegex(OSError, 'interrupted promotion'):
                    apply_pending(f.root)
            self.assertEqual(ledger_path.read_bytes(), before_ledger)
            self.assertEqual(pending_work(f.root), 'history')
            apply_pending(f.root)
            settled = ledger_path.read_bytes()
            apply_pending(f.root)
            self.assertEqual(ledger_path.read_bytes(), settled)
        self.assertIsNone(pending_work(f.root))
        after = selected_records(processed_manifests(f.state))['test'][content_id]
        self.assertIn('Неоднозначные схемы', str(after['unmatched_documents']))
        catalog = json.loads(self.index.read_bytes())
        self.assertFalse(any(r['number'] == '42' for r in catalog['revisions']))
        self.assertTrue(any(r['number'] == '999' for r in catalog['revisions']))
        self.assertEqual([r['revision_id'] for r in catalog['revoked_revisions']], [edition['revision_id']])
        self.assertEqual(after['replay_proof'], proof)

    def test_changed_effective_root_of_same_graph_gets_new_binding(self):
        f = self.fixture
        page = SITES['test']
        prefix = page.replace('/sedo.html', '/specs_sedo/')
        url = prefix + 'module.docx'
        f.resources[page] += b'<a href="/specs_sedo/module.docx">Module</a><a href="/specs_sedo/roots.zip">Roots</a>'
        table = '<w:tbl>' + ''.join('<w:tr>' + ''.join('<w:tc>'+paragraph(s)+'</w:tc>' for s in row) +
                                    '</w:tr>' for row in [('Элемент/атрибут', 'Тип'), ('wrong', 'string')]) + '</w:tbl>'
        f.resources[url] = docx(paragraph('Тип сообщения 42: Example') + table +
                                paragraph(html.escape(f'<correct a="{"x"*8250}"/>')))
        f.resources[prefix+'roots.zip'] = _archive({'main.xsd':
              f'<xs:schema xmlns:xs="{wsdl.XS}"><xs:element name="wrong" type="xs:string"/>'
              '<xs:element name="correct"><xs:complexType><xs:attribute name="a" type="xs:string"/></xs:complexType></xs:element></xs:schema>'})
        with patch('module_xsd.MODULE_MATCH_VERSION', 7):
            manifest = self.capture(self.c, '03:00')
            f.collect(3, snapshot_format=7, individual_wsdl=3)
            f.collect(4, snapshot_format=7, individual_wsdl=3)
        fresh = sources.SourceCollectionTests()
        fresh.setUp()
        self.addCleanup(fresh.doCleanups)
        fresh.resources = dict(f.resources)
        fresh.collect(3, wsdl_entries=[])
        [modern] = [r for r in derive(fresh.current())['revisions'] if r['number']=='42']
        self.assertEqual(modern['root'], 'correct')
        old = f.current()
        [wrong] = [r for r in derive(old)['revisions'] if r['number']=='42']
        self.assertEqual(wrong['root'], 'wrong')
        self.assertEqual(wrong['resolved_schema_graph'], modern['resolved_schema_graph'])
        self.assertEqual(wrong['revision_id'], modern['revision_id'])
        export(f.state, self.index)
        build(self.index, f.state, f.root/'data/fields')
        self.assertEqual(pending_work(f.root), 'history')
        with patch('sync.get', side_effect=AssertionError('unexpected network')):
            apply_pending(f.root)
        self.assertIsNone(pending_work(f.root))
        after = json.loads(self.index.read_bytes())
        found = [(r['root'], r['revision_id'], r['resolved_schema_graph']) for r in after['revisions'] if r['number']=='42']
        self.assertEqual([root for root, _, _ in found], ['correct'])
        self.assertNotEqual(found[0][1], wrong['revision_id'])
        [revoked] = [r for r in after['revoked_revisions'] if r['number'] == '42']
        self.assertEqual((revoked['revision_id'], revoked['root']), (wrong['revision_id'], 'wrong'))
        current = f.root/'site/schemas'/found[0][1]
        self.assertTrue(current.with_suffix('.zip').is_file())
        self.assertTrue(current.with_suffix('.validation.json.gz').is_file())
        self.assertIn('Редакция отозвана', (f.root/'site/editions'/(wrong['revision_id']+'.html')).read_text('utf8'))

    def test_report_keeps_revoked_namespace_beside_independent_match(self):
        f = self.fixture
        page = SITES['test']
        prefix = page.replace('/sedo.html', '/specs_sedo/')
        url = prefix + 'module.docx'
        f.resources[page] += b'<a href="/specs_sedo/module.docx">Module</a><a href="/specs_sedo/a.zip">A</a><a href="/specs_sedo/b.zip">B</a>'
        own = f'<xs:schema xmlns:xs="{wsdl.XS}" targetNamespace="urn:own"><xs:element name="r" type="xs:string"/></xs:schema>'
        table = '<w:tbl><w:tr>' + ''.join('<w:tc>'+paragraph(html.escape(s))+'</w:tc>'
                                         for s in ('Схема', '<xs:element name="r"/>')) + '</w:tr></w:tbl>'
        f.resources[url] = docx(paragraph(html.escape(own)) + paragraph('Тип сообщения 42: Example') + table +
                                paragraph('Тип сообщения 42: Example') + paragraph(html.escape('<r xmlns="urn:external"/>')))
        for filename, uri in [('a.zip','urn:p'), ('b.zip','urn:q')]:
            schema = chameleon_quotation(uri)
            schema['main.xsd'] = schema['main.xsd'].replace('<xs:schema ', '<xs:schema targetNamespace="urn:external" ', 1)
            f.resources[prefix+filename] = _archive(schema)
        with patch('module_xsd.MODULE_MATCH_VERSION', 10):
            self.capture(self.c, '03:00')
            f.collect(3, snapshot_format=10, individual_wsdl=3)
            f.collect(4, snapshot_format=10, individual_wsdl=3)
        old = f.current()
        module = next(row for row in old['entries'] if row['url'] == url)
        self.assertEqual({(b['root'], b['namespace']) for b in module['module_bindings']}, {('r',None), ('r','urn:external')})
        self.assertEqual({r['namespace'] for r in derive(old)['revisions'] if r['number']=='42'}, {'urn:own','urn:external'})
        export(f.state, self.index)
        build(self.index, f.state, f.root/'data/fields')
        self.assertEqual(pending_work(f.root), 'history')
        with patch('sync.get', side_effect=AssertionError('unexpected network')):
            apply_pending(f.root)
        self.assertIsNone(pending_work(f.root))
        after = json.loads(self.index.read_bytes())
        [revoked] = [r for r in after['rejected_binding_observations'] if r['number']=='42']
        self.assertIn('Неоднозначные схемы', revoked['schema_reason'])
        report = json.loads((f.root/'update-report.json').read_bytes())
        issues = [i for r in report['schema_diagnostics'] if r['source']==url for i in r['issues'] if i['number']=='42']
        [issue] = issues
        self.assertEqual((issue['root'], issue['namespace'], issue['snapshot'], issue['resolved_schema_graph']),
                         ('r', 'urn:external', revoked['snapshot'], revoked['resolved_schema_graph']))
        self.assertFalse(any(row['number'] == '42' for row in report['missing_trees']))
        [ready] = [r for r in after['revisions'] if r['number'] == '42']
        self.assertEqual(ready['namespace'], 'urn:own')
        output = f.root/'site/schemas'/ready['revision_id']
        self.assertTrue(output.with_suffix('.zip').is_file())
        self.assertTrue(output.with_suffix('.validation.json.gz').is_file())

    def test_replay_does_not_apply_a_proof_from_future_observation(self):
        b = self.capture(self.b, '03:00')
        self.assertEqual(self.replay()[b]['status'], 'replayed')
        c = self.capture(self.c, '02:00')
        records = self.replay()
        self.assertEqual(records[c]['status'], 'replayed')
        self.assertEqual(len(json.loads(self.index.read_bytes())['revisions']), 2)


class RedirectedBindingTests(unittest.TestCase):
    def catalog(self, document_location, resources):
        fixture = wsdl.WsdlSourcesTests()
        self.addCleanup(fixture.doCleanups)
        library, objects = fixture.collect(resources)
        project = fixture.collect_individual_wsdl(library, objects, location=document_location)
        target = project.root / 'catalog.json'
        export(project.state, target)
        return json.loads(target.read_bytes()), project.current()

    def test_equal_redirected_alias_uses_direct_url_and_preserves_both_sources(self):
        root_url = BASE + 'root.xsd'
        alias_url = BASE + 'legacy.xsd'
        catalog, inventory = self.catalog(root_url, {
            wsdl.SEED: definitions(include('legacy.xsd'), include('root.xsd')),
            root_url: NAMED,
            alias_url: RedirectedBytes(NAMED, root_url),
        })
        self.assertEqual(len(catalog['revisions']), 1, catalog['document_sources'])
        revision, = catalog['revisions']
        self.assertEqual(revision['schema_docx_url'], root_url)
        self.assertTrue(revision['schema_path'].endswith('/root.xsd'))
        self.assertEqual(revision['dependencies'], [['root.xsd', sha(NAMED)]])
        resources = {row['url']: row for row in revision['schema_sources'] if row['kind'] == 'xsd'}
        self.assertEqual(set(resources), {root_url, alias_url})
        self.assertEqual(resources[alias_url]['final_url'], root_url)
        doc, = (row for row in inventory['entries'] if row.get('wsdl_schema_matches'))
        self.assertEqual({row['resource_url'] for row in doc['wsdl_schema_matches']['members']},
                         {root_url, alias_url})

    def test_same_bytes_at_distinct_final_urls_remain_ambiguous(self):
        catalog, _ = self.catalog('root.xsd', {
            wsdl.SEED: definitions(include('published/root.xsd'), include('draft/root.xsd')),
            BASE + 'published/root.xsd': NAMED,
            BASE + 'draft/root.xsd': NAMED,
        })
        self.assertEqual(catalog['revisions'], [])
        self.assertTrue(any('Неоднозначные' in row['schema_reason'] for row in catalog['document_sources']))

    def test_redirected_alias_resolves_same_dependency_from_different_archive_dirs(self):
        root_url = BASE + 'current/root.xsd'
        alias_url = BASE + 'legacy/root.xsd'
        entry = (SCHEMA + include('common.xsd') + '<xs:element name="r" type="m:Code"/></xs:schema>').encode()
        common = (SCHEMA + '<xs:simpleType name="Code"><xs:restriction base="xs:int"/>'
                  '</xs:simpleType></xs:schema>').encode()
        catalog, _ = self.catalog(root_url, {
            wsdl.SEED: definitions(include('legacy/root.xsd'), include('current/root.xsd'),
                                   include('other/common.xsd')),
            root_url: entry,
            alias_url: RedirectedBytes(entry, root_url),
            BASE + 'current/common.xsd': common,
            BASE + 'other/common.xsd': common.replace(b'xs:int', b'xs:string'),
        })
        self.assertEqual(len(catalog['revisions']), 1, catalog['document_sources'])
        revision, = catalog['revisions']
        self.assertEqual(revision['schema_docx_url'], root_url)
        self.assertEqual({digest for _, digest in revision['dependencies']}, {sha(entry), sha(common)})

    def test_same_resource_with_different_usable_entry_dependencies_remains_ambiguous(self):
        root_url = BASE + 'root.xsd'
        raw = (SCHEMA.replace(' targetNamespace="urn:main"', '')
               + '<xs:element name="r" type="m:Code"/></xs:schema>').encode()
        def wrapper(path, datatype):
            return (include(path) + f'<xs:simpleType name="Code"><xs:restriction base="{datatype}"/>'
                    '</xs:simpleType>')
        catalog, inventory = self.catalog(root_url, {
            wsdl.SEED: definitions(wrapper('root.xsd', 'xs:int'), wrapper('legacy.xsd', 'xs:string')),
            root_url: raw,
            BASE + 'legacy.xsd': RedirectedBytes(raw, root_url),
        })
        doc, = (row for row in inventory['entries'] if row.get('wsdl_schema_matches'))
        matches = doc['wsdl_schema_matches']['members']
        self.assertEqual(len(matches), 2)
        self.assertTrue(all(row['entrypoints'] for row in matches), matches)
        self.assertEqual(catalog['revisions'], [])
        self.assertTrue(any('Неоднозначные' in row['schema_reason'] for row in catalog['document_sources']))
