"""Reproductions of source collection review findings 2, 5, 6 and 7."""
import copy
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from collector_feed import import_feed, store_feed
from collector_snapshots import document_snapshot_at, pending, replay_history
from import_inbox import detect_metadata, process_inbox
from manual_import import prepare_import
from schema import dependency_set
from sync import SITES, encode, sha, source_url, write
import sync as sync_module
from tests import test_collector_feed as feed_fixtures
from tests import test_source_collection_regressions as fixtures
from wsdl_sources import _archive, bridge


class SourceReviewTests(unittest.TestCase):
    def collection(self):
        fixture = fixtures.SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_fragment_links_fetch_one_validated_resource_and_retain_source_links(self):
        fixture = self.collection()
        page = SITES['test']
        resource = page.replace('sedo.html', 'specs_sedo/manual.pdf')
        urls = [resource, resource + '#page=2', resource + '#page=3']
        fixture.resources[page] += ''.join(f'<a href="{url}">Manual</a>' for url in urls).encode()
        fixture.resources[resource] = b'%PDF-1.7 fixture'
        requests = fixture.collect(1)
        self.assertEqual(requests.count(resource), 1)
        self.assertFalse(any('#' in url for url in requests))
        rows = [r for r in fixture.current()['entries'] if r['url'] in urls]
        self.assertEqual({r['url'] for r in rows}, set(urls))
        self.assertTrue(all(r.get('sha256') == sha(fixture.resources[resource]) for r in rows))
        for url in requests:
            source_url(url, url)

    def test_filename_version_does_not_consume_trailing_date(self):
        for version in ('3.10', '3.0.12'):
            with self.subTest(version=version):
                metadata = detect_metadata(f'10110_v{version}_12.11.2026.docx', 'test')
                self.assertEqual((metadata['version'], metadata['date']), (version, '2026-11-12'))

    def test_filename_date_accepts_parentheses_spaces_and_descriptive_suffix(self):
        for ending in (' (12.11.2026).docx', '_12.11.2026_final.docx', ' (12.11.26) .docx'):
            with self.subTest(ending=ending):
                metadata = detect_metadata('10110_v3.10' + ending, 'test')
                self.assertEqual((metadata['version'], metadata['date']), ('3.10', '2026-11-12'))

    def test_underscore_version_and_dotted_date_are_separate(self):
        metadata = detect_metadata('10110_v3_0_12_22.11.2026.docx', 'test')
        self.assertEqual((metadata['version'], metadata['date']), ('3.0.12', '2026-11-22'))

    def test_underscore_version_uses_final_overlapping_date_candidate(self):
        for name, version, date in (
                ('10110_v3_10_12_11_2026.docx', '3.10', '2026-11-12'),
                ('10110_v3_0_12_22_11_2026.docx', '3.0.12', '2026-11-22')):
            with self.subTest(name=name):
                metadata = detect_metadata(name, 'test')
                self.assertEqual((metadata['version'], metadata['date']), (version, date))

    def test_transport_validates_fragment_free_request_and_redirect(self):
        resource = SITES['test'].replace('sedo.html', 'specs_sedo/manual.pdf')
        class Opener:
            def open(self, request, **kwargs):
                self.request = request
                body = io.BytesIO(b'%PDF fixture')
                body.geturl = lambda: request.full_url
                return body
        opener = Opener()
        with patch('sync.build_opener', return_value=opener):
            self.assertEqual(sync_module._get(resource + '#page=2'), b'%PDF fixture')
        self.assertEqual(opener.request.full_url, resource)
        redirected = sync_module.SourceRedirect(resource).redirect_request(Request(resource), None, 302, '', {}, resource + '#page=3')
        self.assertEqual(redirected.full_url, resource)
        with self.assertRaises(ValueError):
            sync_module._get('https://example.test/specs_sedo/manual.pdf#page=2')

    def test_legacy_baseline_ignores_pdf_navigation_and_wsdl_derived_matches(self):
        doc = dict(environment='test', kind='docx', sha256='doc', module_bindings=[{'number': '999'}])
        schema = dict(environment='test', kind='zip', sha256='schema', schemas=[{'sha256': 'xsd'}])
        first = dict(entries=[doc, schema, dict(environment='test', kind='pdf', sha256='pdf-a')])
        second = copy.deepcopy(first)
        second['entries'][-1]['sha256'] = 'pdf-b'
        second['entries'].append(dict(environment='test', kind='link', url='https://example.test/nav'))
        second['entries'][0]['module_matches'] = [{'archive_sha256': 'new-wsdl'}]
        stamp = datetime(2026, 11, 14, 4, tzinfo=timezone.utc)
        known = {'a': '2026-11-14T01:00:00+00:00', 'b': '2026-11-14T02:00:00+00:00'}
        self.assertIsNotNone(document_snapshot_at(stamp, 'test', [], {'a': first, 'b': second}, known))
        for changed in ('docx', 'zip'):
            different = copy.deepcopy(second)
            next(r for r in different['entries'] if r['kind'] == changed)['sha256'] = 'changed'
            self.assertIsNone(document_snapshot_at(stamp, 'test', [], {'a': first, 'b': different}, known))

    def test_legacy_unavailable_retries_once_after_proof_is_improved(self):
        fixture = self.collection()
        resource = SITES['test'].replace('sedo.html', 'specs_sedo/manual.pdf')
        fixture.resources[SITES['test']] += b'<a href="/specs_sedo/manual.pdf">PDF</a>'
        fixture.resources[resource] = b'first'
        fixture.collect(1)
        fixture.resources[resource] = b'second'
        fixture.collect(2)
        active = json.loads((fixture.state / 'current.json').read_bytes())
        write(fixture.state / 'current.json', encode(dict(snapshot=active['snapshot'])))
        manifest, objects = feed_fixtures.FeedTests().capture(True)
        manifest['observed_at'] = '2026-11-14T04:00:00+00:00'
        remote = fixture.root / 'feed'
        store_feed(remote, [manifest], objects)
        import_feed(fixture.state, lambda name: (remote / name).read_bytes())
        write(fixture.state / 'collector/processed.json', encode(dict(format=1, manifests={'test': {
            manifest['content_id']: dict(status='unavailable', observed_at=manifest['observed_at'])}})))
        self.assertTrue(pending(fixture.root))
        def blob(raw):
            write(fixture.state / 'objects' / sha(raw), raw)
            return sha(raw)
        keys, ledger = replay_history(fixture.state, fixture.current(), blob)
        self.assertEqual(len(keys), 1)
        self.assertEqual(ledger['manifests']['test'][manifest['content_id']]['status'], 'replayed')
        write(fixture.state / 'collector/processed.json', encode(ledger))
        self.assertEqual(replay_history(fixture.state, fixture.current(), blob)[0], [])

    def test_unavailable_with_current_proof_retries_after_confirmed_transition(self):
        fixture = self.collection()
        fixture.collect(1)
        fixture.resources[SITES['test'].replace('sedo.html', 'specs_sedo/file.docx')] = fixtures.document('replacement')
        fixture.collect(2)
        pointer = json.loads((fixture.state / 'current.json').read_bytes())
        write(fixture.state / 'current.json', encode(dict(snapshot=pointer['snapshot'])))
        manifest, objects = feed_fixtures.FeedTests().capture(True)
        manifest['observed_at'] = '2026-11-14T04:00:00+00:00'
        remote = fixture.root / 'feed'
        store_feed(remote, [manifest], objects)
        import_feed(fixture.state, lambda name: (remote / name).read_bytes())
        def blob(raw):
            write(fixture.state / 'objects' / sha(raw), raw)
            return sha(raw)
        keys, ledger = replay_history(fixture.state, fixture.current(), blob)
        self.assertEqual(keys, [])
        self.assertEqual(ledger['manifests']['test'][manifest['content_id']]['status'], 'unavailable')
        write(fixture.state / 'collector/processed.json', encode(ledger))
        self.assertEqual(replay_history(fixture.state, fixture.current(), blob)[0], [])
        write(fixture.state / 'current.json', encode(pointer))
        self.assertTrue(pending(fixture.root))
        keys, _ = replay_history(fixture.state, fixture.current(), blob)
        self.assertEqual(len(keys), 1)

    def test_bridge_keeps_nested_include_effective_namespace_and_validates_xml(self):
        from lxml import etree
        xs = 'http://www.w3.org/2001/XMLSchema'
        root = f'<xs:schema xmlns:xs="{xs}" xmlns:m="urn:main" targetNamespace="urn:main"><xs:include schemaLocation="common.xsd"/><xs:element name="response" type="m:Code"/></xs:schema>'.encode()
        common = f'<xs:schema xmlns:xs="{xs}"><xs:include schemaLocation="types.xsd"/></xs:schema>'.encode()
        types = f'<xs:schema xmlns:xs="{xs}" targetNamespace="urn:main"><xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:enumeration value="ok"/></xs:restriction></xs:simpleType></xs:schema>'.encode()
        own_raw = _archive({'root.xsd': root})
        support_raw = _archive({'common.xsd': common, 'types.xsd': types})
        objects = {sha(own_raw): own_raw, sha(support_raw): support_raw}
        doc = dict(environment='test', url='https://example.test/doc.docx', embedded_archives=[dict(sha256=sha(own_raw))])
        support = dict(environment='test', sha256=sha(support_raw), source_url='https://example.test/?wsdl', source_files=[])
        result = bridge(doc, support, objects.__getitem__)[0]
        self.assertEqual(len(dependency_set(result['schemas'], 'document/root.xsd')), 3)
        from xsd_bundle import _prepare, _selected
        from xsd_validate import compile_schema
        revision = dict(schema_path='document/root.xsd', namespace='urn:main', root='response', dependencies=dependency_set(result['schemas'], 'document/root.xsd'))
        with zipfile.ZipFile(io.BytesIO(result['content'])) as archive:
            files, _, _ = _prepare(revision, _selected(revision, archive))
        with tempfile.TemporaryDirectory() as tmp:
            for name, raw in files.items():
                path = Path(tmp) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            validator = compile_schema(Path(tmp) / 'message.xsd', tmp)
            self.assertTrue(validator.validate(etree.fromstring(b'<response xmlns="urn:main">ok</response>')))
            self.assertFalse(validator.validate(etree.fromstring(b'<response xmlns="urn:main">bad</response>')))


class FilenameImportReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / 'web', self.root / 'web')
        shutil.copy2(project / 'viewer-template.html', self.root)
        empty = dict(format=1, pages={}, entries=[])
        digest = sha(encode(empty))
        write(self.root / 'state/snapshots' / (digest + '.json'), encode(empty))
        write(self.root / 'state/history.json', encode([digest]))
        write(self.root / 'state/current.json', encode(dict(snapshot=digest)))
        write(self.root / 'data/catalog.json', encode(dict(revisions=[], registry=[], updates=[])))
        write(self.root / 'data/fields/index.json', encode([]))

    def test_manual_fallback_and_inbox_share_date_version_and_correction(self):
        filename = '999_v3.0.12_22.11.2026.docx'
        doc = fixtures.document('manual')
        preview = prepare_import(self.root, doc, filename, dict(number='999', environment='test'))
        self.addCleanup(preview.close)
        self.assertEqual((preview.revision['document_version'], preview.revision['label_date']), ('3.0.12', '22.11.2026'))
        preview.save()
        write(self.root / 'incoming/test' / filename, doc)
        self.assertEqual(process_inbox(self.root)['new_revisions'], 0)
        self.assertEqual(process_inbox(self.root)['processed'], 0)
        write(self.root / 'incoming/test/metadata.json', encode({filename: dict(date='2026-11-21')}))
        report = process_inbox(self.root)
        self.assertEqual((report['processed'], report['new_revisions']), (1, 0))
        row = json.loads((self.root / 'data/catalog.json').read_bytes())['revisions'][0]
        self.assertEqual((row['document_version'], row['label_date']), ('3.0.12', '21.11.2026'))
