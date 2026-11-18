import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from sync import SITES, encode, inspect_file, sha, sync, write
from catalog import export, derive
from tests import test_fix22_update as updates
from tests import test_source_collection_regressions as sources
from tests import test_collector_feed as feeds
from tests import test_update_catalog_lock as locks


def word_table(rows):
    from xml.sax.saxutils import escape
    body = ''.join('<w:tr>'+''.join('<w:tc><w:p><w:r><w:t>'+escape(c)+'</w:t></w:r></w:p></w:tc>' for c in row)+'</w:tr>' for row in rows)
    return sources.archive('word/document.xml', ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>'+body+'</w:tbl></w:body></w:document>').encode())


class HistoryTests(unittest.TestCase):
    def summaries(self, root):
        raw = (root / 'site/catalog-data.js').read_text('utf-8')
        return json.loads(raw.removeprefix('window.SEDO_CATALOG=').rstrip(';\r\n'))['schema_updates']

    def test_journal_compares_late_sourcecraft_captures_in_observation_order(self):
        import build_site
        from collector_feed import store_feed
        from collector_snapshots import replay_archived_inputs
        from fields import build
        from tests.test_collector import CollectorTests as Captures

        fixture = sources.SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        page = SITES['test']
        href = '/specs_sedo/Тип_сообщения_999.docx'
        fixture.resources[page] = f'<a href="/specs_sedo/file.zip">XSD</a><a href="{href}">DOCX</a>'.encode()
        fixture.resources[page.replace('/sedo.html', href)] = sources.individual_document('response')
        captures = Captures()
        first, first_objects = captures.capture(10, '2026-11-14T01:00:00+00:00')
        late, late_objects = captures.capture(11, '2026-11-14T03:00:00+00:00')
        latest, latest_objects = captures.capture(12, '2026-11-14T04:00:00+00:00')
        with patch.dict(os.environ, SEDO_COLLECTOR_SOURCE='sourcecraft'):
            store_feed(fixture.state, [first], first_objects)
            fixture.collect(2)
            store_feed(fixture.state, [latest], latest_objects)
            fixture.collect(5)
            store_feed(fixture.state, [late], late_objects)
            store_feed(fixture.state, [latest], latest_objects)
            replay_archived_inputs(fixture.state)

        root = fixture.root
        export(fixture.state, root / 'data/catalog.json')
        build(root / 'data/catalog.json', fixture.state, root / 'data/fields')
        shutil.copytree(build_site.HERE / 'web', root / 'web')
        shutil.copy2(build_site.HERE / 'viewer-template.html', root)
        with patch.object(build_site, 'HERE', root):
            build_site.main()
        rows = {row['observed_at']: row for row in self.summaries(root)}
        earliest = rows['2026-11-14T02:00:00+00:00']
        historical = rows['2026-11-14T03:00:00+00:00']
        current = rows['2026-11-14T04:00:00+00:00']
        self.assertEqual(historical['comparison']['left'], earliest['revision_id'])
        self.assertEqual(current['comparison']['left'], historical['revision_id'])

    def test_journal_compares_inbox_zip_with_previous_ready_edition(self):
        from import_inbox import process_inbox
        from tests.test_fix15_sources import FilenameImportReviewTests

        fixture = FilenameImportReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        incoming = root / 'incoming/test'
        doc = sources.individual_document('response')
        schema = (b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main">'
                  b'<xs:element name="response" type="xs:string"/></xs:schema>')
        write(incoming / 'first/999_v1.docx', doc)
        write(incoming / 'first/schema.zip', sources.archive('main.xsd', schema))
        first = process_inbox(root)['files'][0]
        with zipfile.ZipFile(io.BytesIO(doc)) as package:
            next_doc = package.read('word/document.xml').replace(
                b'</w:body>', b'<w:p><w:r><w:t>Revision 2</w:t></w:r></w:p></w:body>')
        write(incoming / 'next/999_v2.docx', sources.archive('word/document.xml', next_doc))
        doc_only = process_inbox(root)['files'][0]
        self.assertEqual(doc_only['status'], 'document_only')
        write(incoming / 'next/schema.zip', sources.archive('main.xsd', schema.replace(b'xs:string', b'xs:int')))
        next_edition = process_inbox(root)['files'][0]
        updated = next(row for row in self.summaries(root) if row['revision_id'] == next_edition['revision_id'])
        self.assertEqual(updated['comparison']['left'], first['revision_id'])
        self.assertEqual(updated['comparison']['counts']['changed'], 1)

    def test_both_update_modes_refuse_truncated_ready_history_before_install(self):
        for offline in (False, True):
            with self.subTest(offline=offline):
                f = updates.LocalGenerationTests(); f.setUp()
                try:
                    f.run_update()
                    url = SITES['prod'].replace('sedo.html', 'specs_sedo/a.zip')
                    f.resources[url] = sources.archive('main.xsd', b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:int"/></xs:schema>')
                    f.run_update()
                    old = (f.out/'catalog.json').read_bytes()
                    pointer = json.loads((f.state/'current.json').read_bytes())['snapshot']
                    write(f.state/'history.json', encode([pointer]))
                    args = ['update_catalog.py', '--state', str(f.state), '--out', str(f.out), *(['--offline'] if offline else [])]
                    with patch('sys.argv', args), patch('sync.get', side_effect=f.resources.__getitem__), self.assertRaisesRegex(ValueError, 'редакц|событи'):
                        updates.main()
                    self.assertEqual((f.out/'catalog.json').read_bytes(), old)
                finally: f.doCleanups()

    def test_archive_wrapper_rename_retains_identity_and_current_entry(self):
        f = updates.LocalGenerationTests(); f.setUp(); self.addCleanup(f.doCleanups)
        url = SITES['prod'].replace('sedo.html', 'specs_sedo/a.zip')
        raw = f.resources[url]
        with zipfile.ZipFile(io.BytesIO(raw)) as z: schema = z.read('main.xsd')
        ids = []
        for entry in ('old/main.xsd', 'release/main.xsd', 'old/main.xsd'):
            f.resources[url] = sources.archive(entry, schema)
            f.run_update()
            catalog = json.loads((f.out/'catalog.json').read_bytes())
            self.assertEqual(len(catalog['revisions']), 1)
            row = catalog['revisions'][0]; ids.append(row['revision_id'])
            self.assertEqual(row['schema_path'], entry)
            self.assertEqual(row['archive_sha256'], sha(f.resources[url]))
        self.assertEqual(len(set(ids)), 1)

    def test_history_tables_are_independent_and_hydrated_offline(self):
        history = [['№ версии док-та', 'Дата изменения', 'Изменения'], ['2.1', '02.12.2021', 'Первая версия']]
        raw = word_table(history)
        parsed = inspect_file(raw, 'docx')
        expected = [dict(version='2.1', date='02.12.2021', description='Первая версия')]
        self.assertEqual(parsed.get('document_history'), expected)
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            row = dict(environment='test', kind='docx', url='https://lk-test.fss.ru/specs_sedo/module.docx', label='v2.1', sha256=sha(raw), message_types=[dict(number='317', title='History')])
            snapshot = dict(format=2, entries=[row]); key = sha(encode(snapshot))
            write(state/'objects'/sha(raw), raw)
            write(state/'snapshots'/(key+'.json'), encode(snapshot)); write(state/'current.json', encode(dict(snapshot=key)))
            export(state, state/'catalog.json')
            self.assertEqual(json.loads((state/'catalog.json').read_bytes())['document_sources'][0].get('document_history'), expected)
            self.assertEqual(sha((state/'snapshots'/(key+'.json')).read_bytes()), key)

    def test_inbox_without_namespace_matches_and_detects_second_namespace(self):
        from import_inbox import collect
        raw = sources.individual_document('response')
        with zipfile.ZipFile(io.BytesIO(raw)) as z: xml = z.read('word/document.xml').decode()
        # Remove the namespace row while retaining the root declaration.
        import re
        xml = re.sub(r'<w:tr>(?:(?!</w:tr>).)*Пространство(?:(?!</w:tr>).)*</w:tr>', '', xml)
        raw = sources.archive('word/document.xml', xml.encode())
        self.assertFalse(any('пространство' in k.lower() for k,v in inspect_file(raw,'docx').get('structure',[])))
        schema = b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); incoming = root/'incoming/test'; incoming.mkdir(parents=True)
            (incoming/'999_v1.docx').write_bytes(raw)
            (incoming/'a.zip').write_bytes(sources.archive('main.xsd',schema))
            self.assertEqual(collect(root)[0][0]['archive'].name,'a.zip')
            (incoming/'b.zip').write_bytes(sources.archive('main.xsd',schema.replace(b'urn:main',b'urn:other')))
            with self.assertRaisesRegex(ValueError,'несколько'): collect(root)


class ExtraSourceTests(unittest.TestCase):
    def fixture(self):
        f = sources.SourceCollectionTests()
        f.setUp(); self.addCleanup(f.doCleanups)
        return f

    def test_query_base_and_duplicate_labels_use_published_resources(self):
        from catalog import derive
        f = self.fixture(); p = SITES['test']; base = p.rsplit('/',1)[0]
        doc = '/specs_sedo/Тип_сообщения_999.docx'
        f.resources[base+doc] = sources.individual_document('response')
        f.resources[p] = ('<base href="/specs_sedo/"><a href="'+doc+'"></a><a href="'+doc+'">v2.49 18.12.2025</a><a href="extra.zip">XSD</a><a href="/mchd.html?edition=1">One</a><a href="/mchd.html?edition=2">Two</a>').encode()
        f.resources[base+'/specs_sedo/extra.zip'] = f.schema
        for v in ('1','2'):
            f.resources[base+'/mchd.html?edition='+v] = f'<a href="/mchd/xsd{v}.zip">XSD</a>'.encode()
            f.resources[base+'/mchd/xsd'+v+'.zip'] = f.schema
        f.resources[base+'/mchd.html'] = b'<a href="/mchd/old.zip">XSD</a>'
        f.resources[base+'/mchd/old.zip'] = f.schema
        requests = f.collect(1)
        self.assertIn(base+'/specs_sedo/extra.zip', requests)
        self.assertIn(base+'/mchd.html?edition=1', requests)
        self.assertIn(base+'/mchd.html?edition=2', requests)
        self.assertNotIn(base+'/mchd.html', requests)
        self.assertEqual(requests.count(base+doc), 1)
        row = next(r for r in derive(f.current())['revisions'] if r['number']=='999')
        self.assertEqual((row['document_version'],row['label_date']),('2.49','18.12.2025'))

    def test_root_wsdl_query_dependencies_and_boundaries(self):
        from wsdl_sources import collect, _allowed
        for path in ('/Service','/Service/'):
            base = 'https://ecp-test.sfr.gov.ru'+path; seed=base+'?wsdl'
            resources = {seed:b'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="http://www.w3.org/2001/XMLSchema"><w:types><xs:schema><xs:import namespace="urn:main" schemaLocation="?xsd=1"/></xs:schema></w:types></w:definitions>',base+'?xsd=1':b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>'}
            row = collect(seed,'test',resources.__getitem__,sha)
            self.assertEqual(len(row['schemas']),1)
            with self.assertRaises(ValueError): _allowed('https://other.test/Service?xsd=1',seed)
        with self.assertRaises(ValueError): _allowed('https://ecp-test.sfr.gov.ru/else/a.xsd','https://ecp-test.sfr.gov.ru/nested/Service?wsdl')

    def test_nested_sync_state_respects_project_lock(self):
        f = locks.CatalogLockTests()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); write(root/'viewer-template.html',b'project')
            from auto_update import update_lock
            with update_lock(root): result = f.run_local_sync_cli(root/'data/nested-state')
            self.assertNotEqual(result.returncode,0,result.stdout)
            self.assertFalse((root/'data/nested-state/current.json').exists())


class CollectorTests(unittest.TestCase):
    def test_sync_cli_records_producer_history_with_and_without_historical_docx(self):
        from datetime import datetime, timezone
        from collector_feed import store_feed
        from collector_snapshots import processed_manifests
        from sync import main
        for expected in ("replayed", "unavailable"):
            with self.subTest(expected=expected):
                f = sources.SourceCollectionTests()
                f.setUp()
                self.addCleanup(f.doCleanups)
                a, ao = feeds.FeedTests().capture()
                b, bo = feeds.FeedTests().capture(True)
                a["observed_at"] = "2026-11-14T01:00:00+00:00"
                b["observed_at"] = "2026-11-14T03:00:00+00:00"
                args = ["sync.py", "--state", str(f.state)]
                with patch.dict(os.environ, SEDO_COLLECTOR_SOURCE="sourcecraft"), \
                        patch("sys.argv", args), patch("sync.get", side_effect=f.resources.__getitem__):
                    store_feed(f.state, [a], ao)
                    if expected == "replayed":
                        with patch("sync.datetime") as clock:
                            clock.now.return_value = datetime(2026, 11, 14, 2, tzinfo=timezone.utc)
                            main()
                    store_feed(f.state, [b], bo)
                    store_feed(f.state, [dict(a, observed_at="2026-11-14T04:00:00+00:00")], ao)
                    main()
                records = processed_manifests(f.state).get("manifests", {}).get("test", {})
                self.assertIn(b["content_id"], records)
                record = records[b["content_id"]]
                self.assertEqual(record["status"], expected)
                self.assertEqual(record["observed_at"], b["observed_at"])
                if expected == "replayed":
                    catalog_path = f.root / "catalog.json"
                    export(f.state, catalog_path)
                    catalog = json.loads(catalog_path.read_bytes())
                    row = next(r for r in catalog["revisions"] if r.get("archive_sha256") == b["library"]["sha256"])
                    self.assertEqual(row["document_sha256"], sha(sources.document("test")))
                    self.assertFalse(row["listed_now"])
                else:
                    self.assertIn("DOCX", record["reason"])

    def test_online_staged_update_accepts_custom_producer_state(self):
        from collector_feed import store_feed
        f = updates.LocalGenerationTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        a, ao = feeds.FeedTests().capture()
        store_feed(f.state, [a], ao)
        index = (f.state / "index.json").read_bytes()
        with patch.dict(os.environ, SEDO_COLLECTOR_SOURCE="sourcecraft"):
            f.run_update()
        self.assertEqual((f.state / "index.json").read_bytes(), index)
        key = json.loads((f.state / "current.json").read_bytes())["snapshot"]
        snapshot = json.loads((f.state / "snapshots" / (key + ".json")).read_bytes())
        self.assertIn(a["library"], snapshot["entries"])
        self.assertTrue((f.out / "catalog.json").is_file())

    def test_repaired_producer_preserves_date_for_old_and_clean_consumers(self):
        from collector_feed import store_feed, import_feed, manifest_path
        from collector_snapshots import feed_manifests, observation_path
        manifest, objects = feeds.FeedTests().capture()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); remote=root/'remote'; old=root/'old'; clean=root/'clean'
            store_feed(remote,[manifest],objects)
            fetch=lambda name:(remote/name).read_bytes()
            import_feed(old,fetch)
            (remote/manifest_path('test',manifest['content_id'])).write_bytes(b'broken')
            store_feed(remote,[dict(manifest,observed_at='2026-11-17T12:00:00+00:00')],objects)
            import_feed(old,fetch); import_feed(clean,fetch)
            self.assertEqual(feed_manifests(clean)[0]['observed_at'],manifest['observed_at'])
            self.assertEqual(feed_manifests(old)[0]['observed_at'],manifest['observed_at'])
            receipt=remote/observation_path('sourcecraft','test',manifest['content_id'])
            receipt.unlink()
            (remote/manifest_path('test',manifest['content_id'])).write_bytes(b'broken')
            with self.assertRaisesRegex(ValueError,'observation|date|дат'): store_feed(remote,[manifest],objects)

    def test_home_a_b_a_history_is_pending_and_source_ledgers_are_independent(self):
        from collector_snapshots import feed_manifests, observation_path, pending_work, replay_archived_inputs
        from collector_feed import manifest_path
        a,ao=feeds.FeedTests().capture(); b,bo=feeds.FeedTests().capture(True)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,SEDO_COLLECTOR_SOURCE='home'):
            root=Path(folder); state=root/'state'
            for manifest,objects in ((a,ao),(b,bo)):
                write(state/manifest_path('test',manifest['content_id']),encode(manifest))
                write(state/observation_path('home','test',manifest['content_id']),manifest['observed_at'].encode())
                for h,raw in objects.items(): write(state/'objects'/h,raw)
            write(state/'collector/test/current',a['content_id'].encode())
            # No prod library is needed for this isolated history decision.
            snapshot=dict(format=2,entries=[a['library']]); key=sha(encode(snapshot))
            write(state/'snapshots'/(key+'.json'),encode(snapshot)); write(state/'current.json',encode(dict(snapshot=key)))
            write(state/'history.json',encode([key]))
            self.assertEqual({m['content_id'] for m in feed_manifests(state)},{a['content_id'],b['content_id']})
            with patch('collector_snapshots.load_library',side_effect=lambda base,env:a['library'] if env=='test' else None):
                self.assertEqual(pending_work(root),'history')
                replay_archived_inputs(state)
                self.assertIsNone(pending_work(root))
                ledger=json.loads((state/'collector/processed.json').read_bytes())
                self.assertEqual(ledger['sources']['home']['test'][b['content_id']]['status'],'unavailable')
                self.assertIn('DOCX',ledger['sources']['home']['test'][b['content_id']]['reason'])

class AdditionalPipelineTests(unittest.TestCase):
    def test_home_replays_intermediate_edition_with_confirmed_docx_and_separate_sourcecraft_receipt(self):
        from collector_feed import store_feed, import_feed, manifest_path
        from collector_snapshots import observation_path, pending_work, replay_archived_inputs, load_library
        f = sources.SourceCollectionTests(); f.setUp(); self.addCleanup(f.doCleanups)
        a,ao=feeds.FeedTests().capture(); b,bo=feeds.FeedTests().capture(True)
        a=dict(a,observed_at='2026-11-14T01:00:00+00:00'); b=dict(b,observed_at='2026-11-14T02:00:00+00:00')
        def home(manifest,objects):
            write(f.state/manifest_path('test',manifest['content_id']),encode(manifest))
            receipt=f.state/observation_path('home','test',manifest['content_id'])
            if not receipt.exists(): write(receipt,manifest['observed_at'].encode())
            for h,raw in objects.items(): write(f.state/'objects'/h,raw)
            write(f.state/'collector/test/current',manifest['content_id'].encode())
        def selected(base,env): return load_library(base,env) if env=='test' else None
        with patch.dict(os.environ,SEDO_COLLECTOR_SOURCE='home'):
            home(a,ao)
            with patch('collector_snapshots.load_library',side_effect=selected): f.collect(1)
            home(b,bo); home(a,ao)
            with patch('collector_snapshots.load_library',side_effect=selected):
                self.assertEqual(pending_work(f.root),'history')
                replay_archived_inputs(f.state)
                export(f.state,f.root/'catalog.json')
                cat=json.loads((f.root/'catalog.json').read_bytes())
                row=next(r for r in cat['revisions'] if r.get('archive_sha256')==b['library']['sha256'])
                event=next(e for e in cat['updates'] if e['revision_id']==row['revision_id'])
                self.assertEqual(event['observed_at'],b['observed_at'])
                self.assertEqual(row['document_sha256'],sha(sources.document('test')))
                self.assertFalse(row['listed_now'])
                self.assertIsNone(pending_work(f.root))
                before=(f.state/'collector/processed.json').read_bytes()
                replay_archived_inputs(f.state)
                self.assertEqual((f.state/'collector/processed.json').read_bytes(),before)
            remote=f.root/'feed'; store_feed(remote,[a],ao); store_feed(remote,[b],bo); store_feed(remote,[a],ao)
            import_feed(f.state,lambda name:(remote/name).read_bytes())
            with patch.dict(os.environ,SEDO_COLLECTOR_SOURCE='sourcecraft'):
                self.assertEqual(pending_work(f.root),'history')
                replay_archived_inputs(f.state)
                self.assertIsNone(pending_work(f.root))
            with patch('collector_snapshots.load_library',side_effect=selected): self.assertIsNone(pending_work(f.root))

    def test_real_history122_and_document_only317_are_hydrated_from_archived_bytes(self):
        root=Path(__file__).resolve().parents[1]
        key='6581db63a1768b984f134e7b4cdae74f1a320df056ecd0aa6aa6a6a22a9839b7'
        snapshot=json.loads((root/'state/snapshots'/(key+'.json')).read_bytes())
        docs=[r for r in snapshot['entries'] if r.get('sha256','').startswith(('af85e316','987d87743ee6'))]
        self.assertTrue(docs)
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder); subset=dict(snapshot,entries=docs); digest=sha(encode(subset))
            for doc in docs: write(state/'objects'/doc['sha256'],(root/'state/objects'/doc['sha256']).read_bytes())
            write(state/'snapshots'/(digest+'.json'),encode(subset)); write(state/'current.json',encode(dict(snapshot=digest)))
            with patch('sync.inspect_file', side_effect=AssertionError('Full DOCX inspection is unnecessary for history hydration')):
                export(state,state/'catalog.json')
            cat=json.loads((state/'catalog.json').read_bytes())
            candidates=[r for r in cat['revisions'] if r['number']=='122' and r['environment']=='test']
            self.assertEqual(len(candidates),1)
            history=candidates[0]['document_history']
            self.assertEqual(len(history),49)
            self.assertEqual((history[0]['version'],history[0]['date']),('2.1','02.12.2021'))
            self.assertEqual((history[-1]['version'],history[-1]['date']),('2.49','18.12.2025'))
            unmatched=[r for r in cat['document_sources'] if r['number'] in ('317','318','319')]
            self.assertEqual({r['number'] for r in unmatched},{'317','318','319'})
            self.assertTrue(all('document_history' in r and 'version_tables' in r for r in unmatched))

    def test_old_url_rules_and_new_url_rules_are_each_byte_replayable(self):
        f=sources.SourceCollectionTests(); f.setUp(); self.addCleanup(f.doCleanups)
        p=SITES['test']; base=p.rsplit('/',1)[0]
        f.resources[p]=b'<base href="/specs_sedo/"><a href="/specs_sedo/file.zip">XSD</a><a href="extra.zip">XSD</a><a href="/mchd.html?edition=2">MCHD</a>'
        f.resources[base+'/specs_sedo/extra.zip']=f.schema
        for suffix in ('','?edition=2'): f.resources[base+'/mchd.html'+suffix]=b'<a href="/mchd/schemas.zip">XSD</a>'
        f.resources[base+'/mchd/schemas.zip']=f.schema
        for version in (5,6):
            f.collect(1,snapshot_format=version,wsdl_entries=[]); old=json.loads((f.state/'current.json').read_bytes())['snapshot']
            f.collect(2,snapshot_format=version,wsdl_entries=[]); self.assertEqual(json.loads((f.state/'current.json').read_bytes())['snapshot'],old)
            entries=f.current()['entries']
            self.assertEqual(any(r.get('sha256') and r['url']==base+'/specs_sedo/extra.zip' for r in entries),version==6)
            self.assertEqual(any(r.get('sha256') and r['url']==base+'/mchd.html?edition=2' for r in entries),version==6)

class IdentityAndMetadataBoundaryTests(unittest.TestCase):
    def test_entrypoint_swap_with_same_dependency_set_remains_a_conflict(self):
        # Both entrypoints resolve the identical cyclic closure, so the legacy
        # edition hash collides. The concrete entry filename must still differ.
        match=dict(number='999',title='T',root='response',namespace='urn:main',dependency_digest='identical-set',archive_sha256='zip',archive_url='a.zip')
        document=dict(kind='docx',environment='test',url='module.docx',label='v1',sha256='doc')
        for modified in (dict(schema_path='b.xsd'),dict(schema_path='a.xsd',root='other'),dict(schema_path='a.xsd',namespace='urn:other')):
            with self.subTest(modified=modified),tempfile.TemporaryDirectory() as folder:
                state=Path(folder);history=[]
                for row in (dict(match,schema_path='old/a.xsd'),dict(match,**modified)):
                    snapshot=dict(format=2,entries=[dict(document,module_matches=[row])]);key=sha(encode(snapshot));history.append(key)
                    write(state/'snapshots'/(key+'.json'),encode(snapshot))
                write(state/'history.json',encode(history));write(state/'current.json',encode(dict(snapshot=key)))
                with self.assertRaisesRegex(ValueError,'Конфликт идентичности'): export(state,state/'catalog.json')

    def test_inbox_explicit_empty_and_explicit_uri_match_official_semantics(self):
        from import_inbox import collect
        schemas={namespace:sources.archive('main.xsd',('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"'+(' targetNamespace="'+namespace+'"' if namespace else '')+'><xs:element name="response" type="xs:string"/></xs:schema>').encode()) for namespace in ('','urn:main')}
        for namespace in ('','urn:main'):
            with self.subTest(namespace=namespace),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);incoming=root/'incoming/test';incoming.mkdir(parents=True)
                raw=word_table([['Корневой элемент','response'],['Пространство имен файла XML-схемы',namespace]])
                (incoming/'999_v1.docx').write_bytes(raw)
                for index,content in enumerate(schemas.values()): (incoming/(str(index)+'.zip')).write_bytes(content)
                tasks,_=collect(root)
                self.assertEqual(tasks[0]['archive'].read_bytes(),schemas[namespace])

    def test_document_and_xsd_version_tables_keep_their_separate_meanings(self):
        first=word_table([['№версии документа','Датаизменения','Изменения'],['2.1','01.02.2026','History']])
        second=word_table([['Номер версии XML-схемы','Номер версии документа'],['1.0','2.1']])
        with zipfile.ZipFile(io.BytesIO(first)) as z: a=z.read('word/document.xml')
        with zipfile.ZipFile(io.BytesIO(second)) as z: b=z.read('word/document.xml')
        raw=sources.archive('word/document.xml',a.replace(b'</w:body>',b[b.index(b'<w:tbl>'):b.index(b'</w:body>')]+b'</w:body>'))
        metadata=inspect_file(raw,'docx')
        self.assertEqual(metadata['document_history'],[dict(version='2.1',date='01.02.2026',description='History')])
        self.assertEqual(len(metadata['version_tables']),1)

class WsdlTrailingSlashBoundaryTests(unittest.TestCase):
    def test_trailing_slash_service_allows_own_query_and_children_but_rejects_siblings(self):
        from wsdl_sources import _allowed
        for path in ('/Service/', '/nested/Service/'):
            seed='https://ecp-test.sfr.gov.ru'+path+'?wsdl'
            _allowed('https://ecp-test.sfr.gov.ru'+path+'?xsd=1',seed)
            _allowed('https://ecp-test.sfr.gov.ru'+path+'schemas/a.xsd',seed)
            outside=path.rstrip('/').rsplit('/',1)[0]+'/other/a.xsd'
            with self.assertRaisesRegex(ValueError,'outside approved'): _allowed('https://ecp-test.sfr.gov.ru'+outside,seed)
