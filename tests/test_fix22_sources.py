import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from catalog import derive, export
from collector import git_blob_sha, publish_inputs
from import_inbox import collect as inbox
from schema import dependency_set
from sync import encode, inspect_file, sha, write
from tests import test_collector as home
from tests.test_source_collection_regressions import individual_document
from tests import test_wsdl_sources as wsdl
from xsd_bundle import _prepare
from wsdl_sources import _archive


class SourceTests(unittest.TestCase):
    def test_wsdl_links_are_independent_and_fragment_free(self):
        rows = [dict(environment="test", url=url) for url in (
            wsdl.SEED.replace(".ru/", ".ru:bad/"), wsdl.SEED + "#service", wsdl.SEED)]
        self.assertEqual(wsdl.discover(rows, "test"), [wsdl.SEED])

    def test_broken_ipv6_link_does_not_prevent_capture_or_catalog_download(self):
        from unittest.mock import patch
        import collector
        from sync import SITES
        from tests.test_source_collection_regressions import SourceCollectionTests
        malformed = b'<a href="https://[not-an-ipv6]/">Unrelated</a>'
        resources = wsdl.WsdlSourcesTests().bundle()
        resources[SITES['test']] = malformed + ('<a href="'+wsdl.SEED+'#service">WSDL</a>').encode()
        with patch('collector.get', side_effect=lambda url, **kwargs: resources[url]):
            manifest, _ = collector.capture('test')
        self.assertEqual(manifest['library']['source_url'], wsdl.SEED)
        f = SourceCollectionTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        page = SITES['test']
        f.resources[page] += malformed + b'<a href="/mchd.html">MCHD</a>'
        f.resources[page.replace('sedo.html','mchd.html')] = malformed + b'<a href="/mchd/schema.zip">XSD</a>'
        expected = page.replace('sedo.html','mchd/schema.zip')
        f.resources[expected] = f.schema
        self.assertIn(expected, f.collect(1))

    def test_home_repairs_manifest_using_confirmed_receipt_then_is_idempotent(self):
        manifest, objects = home.CollectorTests().capture()
        api = home.FakeAPI()
        publish_inputs(api, [manifest], objects)
        path = next(p for p in api.tree if p.endswith('.json'))
        bad = b'broken envelope'
        api.blobs[git_blob_sha(bad)] = bad
        api.tree[path] = git_blob_sha(bad)
        later = dict(manifest, observed_at='2026-11-16T06:00:00+00:00')
        self.assertTrue(publish_inputs(api, [later], objects))
        self.assertEqual(api.blobs[api.tree[path]], encode(manifest))
        self.assertFalse(publish_inputs(api, [later], objects))

    def test_local_capture_repairs_corrupt_manifest_and_keeps_first_observation(self):
        from unittest.mock import patch
        import collector
        manifest, objects = home.CollectorTests().capture()
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            args = ['collector.py','--capture-to',temp,'--environment','test']
            def capture(observation):
                with patch('sys.argv', args), patch('collector.capture', return_value=(observation, objects)):
                    collector.main()
            capture(manifest)
            current = folder/'collector/test/current.json'
            target = current.parent/(json.loads(current.read_bytes())["content_id"]+'.json')
            first = target.read_bytes()
            receipt = current.parent/'home'/(manifest['content_id']+'.observed-at')
            original_receipt = receipt.read_bytes()
            target.write_bytes(b'broken')
            later = dict(manifest, observed_at='2026-11-16T06:00:00+00:00')
            capture(later)
            self.assertEqual(target.read_bytes(),first)
            self.assertEqual(receipt.read_bytes(),original_receipt)
            capture(later)
            self.assertEqual(target.read_bytes(),first)

    def test_renamed_entry_is_equivalent_only_with_the_same_resolved_dependencies(self):
        root = b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:include schemaLocation="types.xsd"/><xs:element name="response" type="xs:string"/></xs:schema>'
        child = b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"/>'
        doc = individual_document('response')
        def packaged(name, content):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as z:
                z.writestr(name, root)
                z.writestr('types.xsd', content)
            return buf.getvalue()
        def entry(url, kind, data):
            return dict(environment='test', label=url, url=url, kind=kind, sha256=sha(data), **inspect_file(data, kind))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'incoming/test'
            folder.mkdir(parents=True)
            (folder/'999.docx').write_bytes(doc)
            first = packaged('old.xsd', child)
            (folder/'a.zip').write_bytes(first)
            for changed in (False, True):
                second = packaged('new.xsd', child+b'<!-- changed -->' if changed else child)
                (folder/'b.zip').write_bytes(second)
                rows = [entry('Тип_сообщения_999.docx','docx',doc), entry('a.zip','zip',first), entry('b.zip','zip',second)]
                catalog = derive(dict(entries=rows))
                old = derive(dict(entries=rows[:2]))['revisions'][0]
                if changed:
                    self.assertTrue(catalog['review'])
                    with self.assertRaisesRegex(ValueError, 'несколько'):
                        inbox(Path(tmp))
                else:
                    self.assertFalse(catalog['review'])
                    self.assertEqual(catalog['revisions'][0]['revision_id'], old['revision_id'])
                    self.assertEqual(len(inbox(Path(tmp))[0]), 1)

    def test_encoded_path_selects_exact_zip_member_and_wsdl_keeps_original_bytes(self):
        header = b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        main = header+b'<xs:include schemaLocation="v1/types%20file.xsd"/><xs:element name="root" type="T"/></xs:schema>'
        child = header+b'<xs:simpleType name="T"><xs:restriction base="xs:string"/></xs:simpleType></xs:schema>'
        files = {'main.xsd':main, 'v1/types file.xsd':child, 'v2/types file.xsd':child.replace(b'xs:string', b'xs:int')}
        meta = inspect_file(_archive(files), 'zip')
        deps = dependency_set(meta['schemas'], 'main.xsd')
        self.assertEqual({path for path,digest in deps}, {'main.xsd','v1/types file.xsd'})
        _, records, _ = _prepare(dict(schema_path='main.xsd',root='root',namespace=None),files)
        self.assertEqual(next(r for r in records if r['source_path']=='main.xsd')['rewrites'][0]['target_source_path'], 'v1/types file.xsd')
        fixture = wsdl.WsdlSourcesTests()
        resources = fixture.bundle()
        prefix = wsdl.BASE.rsplit('/',1)[0]+'/'
        resources = {u.replace(wsdl.BASE+'?xsd=main.xsd', prefix+'types%20v1.xsd'):
                     raw.replace((wsdl.BASE+'?xsd=main.xsd').encode(), (prefix+'types%20v1.xsd').encode()) for u,raw in resources.items()}
        row, objects = fixture.collect(resources)
        self.assertIn(prefix+'types%20v1.xsd', [s['url'] for s in row['source_files']])
        self.assertTrue(any(s['path'].endswith('/types v1.xsd') for s in row['schemas']))

    def test_new_equivalent_zip_sorted_first_preserves_confirmed_edition_and_inbox_choice(self):
        from tests.test_source_collection_regressions import archive
        document = individual_document('response')
        xml = b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>'
        def row(name, kind, content):
            return dict(environment='test', label=name, url=name, kind=kind, sha256=sha(content), **inspect_file(content,kind))
        doc = row('Тип_сообщения_999.docx','docx',document)
        a, b = archive('new.xsd',xml), archive('old.xsd',xml)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            state = root/'state'
            rows = [doc, row('b.zip','zip',b)]
            history=[]
            for entries in (rows, [*rows, row('a.zip','zip',a)]):
                raw=encode(dict(entries=entries))
                key=sha(raw)
                history.append(key)
                write(state/'snapshots'/(key+'.json'),raw)
                write(state/'current.json',encode(dict(snapshot=key)))
                write(state/'history.json',encode(history))
                export(state,root/'catalog.json')
                catalog=json.loads((root/'catalog.json').read_bytes())
                self.assertEqual(len(catalog['updates']),1)
            rid=catalog['updates'][0]['revision_id']
            folder = root/'incoming/test'
            write(folder/'999.docx',document)
            write(folder/'a.zip',a)
            write(folder/'b.zip',b)
            ledger={'files':{'incoming/test/999.docx':{'revision_id':rid}}}
            self.assertEqual(inbox(root,ledger)[0][0]['archive'].name,'b.zip')

    def test_new_member_of_same_inbox_zip_preserves_edition_and_event(self):
        from manual_import import stage_import
        xml = b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / 'state'
            snapshot = encode(dict(entries=[]))
            key = sha(snapshot)
            write(state/'snapshots'/(key+'.json'), snapshot)
            write(state/'current.json', encode(dict(snapshot=key)))
            write(state/'history.json', encode([key]))
            catalog_path = root/'data/catalog.json'
            export(state, catalog_path)
            folder = root/'incoming/test'
            write(folder/'999.docx', individual_document('response'))
            def process(names, ledger=None):
                write(folder/'schemas.zip', _archive({name:xml for name in names}))
                task = inbox(root, ledger)[0][0]
                previous = (ledger or {}).get('files',{}).get('incoming/test/999.docx',{})
                return stage_import(root, task['path'].read_bytes(), task['path'].name, task['metadata'],
                                    task['archive'].read_bytes(), task['archive_name'],
                                    preferred_revision_id=previous.get('revision_id'))
            first = process(['old.xsd'])
            self.assertTrue(first['new_revision'])
            rid = first['revision']['revision_id']
            before = catalog_path.read_bytes()
            repeated = process(['new.xsd', 'old.xsd'], {'files':{'incoming/test/999.docx':{'revision_id':rid}}})
            self.assertFalse(repeated['new_revision'])
            self.assertEqual(repeated['revision']['revision_id'], rid)
            self.assertEqual(repeated['revision']['schema_path'], 'old.xsd')
            self.assertEqual(catalog_path.read_bytes(), before)
            # A legacy catalog can already contain IDs for both archive member names.
            other = process(['new.xsd'])
            self.assertNotEqual(other['revision']['revision_id'], rid)
            repeated = process(['new.xsd','old.xsd'], {'files':{'incoming/test/999.docx':{'revision_id':rid}}})
            self.assertFalse(repeated['new_revision'])
            self.assertEqual(repeated['revision']['revision_id'], rid)
