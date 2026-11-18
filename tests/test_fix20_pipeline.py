import io
import json
import unittest
import zipfile

from catalog import derive
from collector import publish_inputs
from collector_feed import import_feed, store_feed
from collector_snapshots import make_manifest, pending_work, replay_archived_inputs, validate_manifest
from schema import Schema
from sync import RedirectedBytes, SITES, encode, sha, write
from tests import test_collector as home
from tests import test_collector_feed as feed
from tests import test_source_collection_regressions as sources
from tests import test_wsdl_sources as wsdl
from verify import verify


class PipelineReviewTests(unittest.TestCase):
    def sources(self):
        fixture = sources.SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_page_and_companion_redirect_bases_survive_exact_offline_replay(self):
        f = self.sources()
        page = SITES['test']
        host = page.rsplit('/', 1)[0]
        doc = host + '/specs_sedo/notice.docx'
        archive = host + '/mchd/editions/schema.zip'
        f.resources[page] = RedirectedBytes(
            b'<a href="/specs_sedo/file.zip">ZIP</a><a href="notice.docx">DOCX</a><a href="/mchd.html">MCHD</a>',
            host + '/specs_sedo/catalog.html')
        f.resources[host + '/mchd.html'] = RedirectedBytes(b'<a href="schema.zip">XSD</a>', host + '/mchd/editions/index.html')
        f.resources[doc] = sources.document('notice')
        f.resources[archive] = f.schema
        f.collect(1)
        hashes = {r['url']: r.get('sha256') for r in f.current()['entries']}
        self.assertEqual(hashes.get(doc), sha(f.resources[doc]))
        self.assertEqual(hashes.get(archive), sha(f.schema))
        self.verify_fixture(f)

    def verify_fixture(self, f):
        data = f.root / 'data'
        write(data / 'catalog.json', encode(dict(revisions=[
            dict(number='10110', environment=env, revision_id=env, status='document_only', source_type='manual')
            for env in SITES])))
        verify(f.state, data)

    def test_companion_same_contour_alias_and_fragment_download_one_zip(self):
        f = self.sources()
        page = SITES['prod']
        host = page.rsplit('/', 1)[0]
        zip_url = host + '/mchd/schema.zip'
        alias = zip_url.replace('lk.sfr.gov.ru', 'lk.fss.ru') + '#files'
        other = zip_url.replace('lk.sfr.gov.ru', 'lk-test.fss.ru')
        f.resources[page] += b'<a href="/mchd.html">MCHD</a>'
        f.resources[host + '/mchd.html'] = ''.join(f'<a href="{u}">XSD</a>' for u in (alias, zip_url, other)).encode()
        f.resources[zip_url] = f.schema
        calls = f.collect(1)
        rows = {r['url']: r.get('sha256') for r in f.current()['entries'] if r.get('source_page')}
        self.assertEqual(rows, {alias: sha(f.schema), zip_url: sha(f.schema)})
        self.assertEqual(calls.count(zip_url), 1)
        self.assertNotIn(other, calls)
        self.verify_fixture(f)
        f.collect(2, snapshot_format=2)
        old_rows = [r['url'] for r in f.current()['entries'] if r.get('source_page')]
        self.assertEqual(old_rows, [zip_url])
        self.verify_fixture(f)

    def test_offline_replay_preserves_companion_rejections_on_primary_host(self):
        for rejected in ('http://lk.sfr.gov.ru/mchd/schema.zip',
                         'https://lk.sfr.gov.ru:8443/mchd/schema.zip',
                         'https://lk.sfr.gov.ru:invalid/mchd/schema.zip'):
            with self.subTest(rejected=rejected):
                f = self.sources()
                page = SITES['prod']
                companion = page.replace('sedo.html', 'mchd.html')
                f.resources[page] += b'<a href="/mchd.html">MCHD</a>'
                f.resources[companion] = f'<a href="{rejected}">XSD</a>'.encode()
                calls = f.collect(1)
                self.assertNotIn(rejected, calls)
                self.assertIn(page.replace('sedo.html', 'specs_sedo/file.zip'), calls)
                self.verify_fixture(f)
                self.assertEqual(f.current()['format'], 3)

    def test_two_opaque_xsd_queries_resolve_in_archive_without_changing_originals(self):
        from xsd_bundle import _prepare
        f = wsdl.WsdlSourcesTests()
        original = f.bundle()
        resources = {url.replace('main.xsd', '1').replace('Common.xsd', '2'):
                     raw.replace(b'main.xsd', b'1').replace((wsdl.BASE + '?xsd=Common.xsd').encode(), b'?xsd=2')
                     for url, raw in original.items()}
        row, objects = f.collect(resources)
        self.assertEqual([s['url'] for s in row['source_files']], [wsdl.SEED, wsdl.BASE + '?xsd=1', wsdl.BASE + '?xsd=2'])
        with zipfile.ZipFile(io.BytesIO(objects[row['sha256']])) as bundle:
            self.assertEqual({bundle.read(name) for name in bundle.namelist()}, {resources[wsdl.BASE + '?xsd=' + i] for i in ('1', '2')})
            main = next(s['path'] for s in row['schemas'] if s['namespace'] == 'urn:main')
            tree = Schema(bundle, main, 'response')
            tree.field(tree.root)
            self.assertIn(['length', '10'], tree.fields[0]['facets'])
            packed, mappings, _ = _prepare(dict(schema_path=main, namespace='urn:main', root='response'),
                                            {name: bundle.read(name) for name in bundle.namelist()})
            local_main = next(m for m in mappings if m['source_path'] == main)
            edge = local_main['rewrites'][0]
            self.assertEqual(edge['before'], '?xsd=2')
            common = next(m for m in mappings if m['source_path'] == edge['target_source_path'])
            self.assertEqual(packed[common['original_path']], resources[wsdl.BASE + '?xsd=2'])
        self.assertEqual(validate_manifest(make_manifest(row, '2026-11-16T00:00:00+00:00'), objects.__getitem__), row)

    def test_home_collection_repairs_bad_object_and_preserves_first_receipt(self):
        manifest, objects = home.CollectorTests().capture()
        api = home.FakeAPI()
        publish_inputs(api, [manifest], objects)
        baseline = dict(api.tree)
        name = 'state/objects/' + manifest['library']['sha256']
        api.tree[name] = '0' * 40
        api.blobs[api.tree[name]] = b'broken'
        later = dict(manifest, observed_at='2026-11-16T06:00:00+00:00')
        self.assertTrue(publish_inputs(api, [later], objects))
        prefix = f"state/collector/{manifest['environment']}/"
        pointer = prefix + 'current.json'
        self.assertEqual(json.loads(api.blobs[api.tree[pointer]]),
                         dict(format=1, content_id=manifest['content_id'], capture_started_at=later['observed_at']))
        self.assertEqual(api.tree | {pointer: baseline[pointer]}, baseline)
        self.assertFalse(publish_inputs(api, [later], objects))

    def test_historical_module_with_no_matched_messages_is_unavailable(self):
        self.check_module_history(mixed=False)

    def test_historical_module_records_each_missing_message_and_keeps_matched_one(self):
        self.check_module_history(mixed=True)

    def check_module_history(self, mixed):
        f = self.sources()
        a, ao = feed.FeedTests().capture()
        service = wsdl.WsdlSourcesTests()
        inputs = service.bundle()
        main = wsdl.BASE + '?xsd=main.xsd'
        new_root = b'other' if mixed else b'unrelated'
        inputs[main] = inputs[main].replace(b'name="response"', b'name="' + new_root + b'"')
        lib, bo = service.collect(inputs)
        b = make_manifest(lib, '2026-11-14T12:00:00+00:00')
        if mixed:
            doc_url = SITES['test'].replace('sedo.html', 'specs_sedo/file.docx')
            with zipfile.ZipFile(io.BytesIO(f.resources[doc_url])) as original:
                xml = original.read('word/document.xml')
            start, _, tail = xml.partition(b'<w:body>')
            declarations = tail.split(b'</w:body>')[0]
            extra = declarations.replace(b'999', b'998').replace(b'response', b'other')
            f.resources[doc_url] = sources.archive('word/document.xml', start + b'<w:body>' + declarations + extra + b'</w:body></w:document>')
        remote = f.root / 'remote'
        read = lambda name: (remote / name).read_bytes()
        store_feed(remote, [a], ao)
        import_feed(f.state, read)
        f.collect(1)
        store_feed(remote, [b], bo)
        store_feed(remote, [a], ao)
        import_feed(f.state, read)
        # Upgrade a previous release's false "replayed" result, also for existing installations.
        pointer = json.loads((f.state / 'current.json').read_bytes())
        ledger_path = f.state / 'collector/processed.json'
        ledger = json.loads(ledger_path.read_bytes())
        ledger['manifests']['test'][b['content_id']] = dict(status='replayed', observed_at=b['observed_at'], snapshot=pointer['snapshot'])
        write(ledger_path, encode(ledger))
        self.assertEqual(pending_work(f.root), 'history')
        replay_archived_inputs(f.state)
        record = json.loads(ledger_path.read_bytes())['manifests']['test'][b['content_id']]
        self.assertEqual(record['status'], 'replayed' if mixed else 'unavailable')
        self.assertEqual([(r['number'], r['root']) for r in record['unmatched_documents']], [('999', 'response')])
        if mixed:
            past = json.loads((f.state / 'snapshots' / (record['snapshot'] + '.json')).read_bytes())
            self.assertEqual([r['number'] for r in derive(past)['revisions']], ['998'])
        self.assertIsNone(pending_work(f.root))
