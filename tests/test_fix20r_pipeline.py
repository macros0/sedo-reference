import io, json, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from catalog import derive, _export, individual_number
from sync import SITES, encode, inspect_file, sha, sync, write
from tests.test_source_collection_regressions import archive, individual_document
from tests import test_fix22_update as update_fixtures
from tests.test_fix25_pipeline import word_table
from tests import test_collector as collector_fixtures
from collector import publish_inputs
from collector_snapshots import observation_path

class PublicationTests(unittest.TestCase):
    def test_offline_refuses_all_three_retirements_without_install(self):
        f=update_fixtures.LocalGenerationTests(); f.setUp(); self.addCleanup(f.doCleanups)
        for n in ('997','998'):
            name=f.name.replace('999',n)
            f.resources[SITES['prod']] += f'<a href="{name}">{n}</a>'.encode()
            f.resources[SITES['prod'].replace('/sedo.html',name)]=f.document
        f.run_update()
        before={p:p.read_bytes() for p in f.out.rglob('*') if p.is_file()}
        f.resources[SITES['prod']]=b'<a href="/specs_sedo/a.zip">XSD</a>'
        with patch('sync.get',side_effect=f.resources.__getitem__): sync(f.state)
        with patch('sys.argv',['update_catalog.py','--state',str(f.state),'--out',str(f.out),'--offline']):
            with self.assertRaisesRegex(ValueError,'Массовое исчезновение'): __import__('update_catalog').main()
        self.assertEqual({p:p.read_bytes() for p in before},before)

class ParsingTests(unittest.TestCase):
    def test_url_filename_ignores_query_fragment_and_query_slashes(self):
        for suffix in ('?r=/foo','#page=2','?download=Тип_сообщения_701'):
            self.assertEqual(individual_number(dict(kind='docx',sha256='x',url='https://a/Тип_сообщения_14.docx'+suffix)),'14')
        for suffix in ('?rev=2','#page=2'):
            row=dict(kind='docx',sha256='x',environment='test',label='x',url='https://a/Пр_12_Модуль.docx'+suffix,message_types=[dict(number='14',title='Title')])
            self.assertEqual(derive(dict(entries=[row]))['registry'][0]['appendix'],'12')
    def test_single_registry_row_and_empty_header(self):
        headers=['Код типа сообщения','Наименование','Описание','Приложение']
        raw=word_table([headers,['14','Title','Description','12']])
        self.assertEqual(len(inspect_file(raw,'docx').get('registry_tables',[])),1)
        self.assertNotIn('registry_tables',inspect_file(word_table([headers]),'docx'))
    def test_word_boundaries_and_runs_in_history(self):
        xml='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>№ версии документа</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Дата изменения</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Изменения</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>17.11.2026</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>обеспе</w:t></w:r><w:r><w:t>чения</w:t></w:r></w:p><w:p><w:r><w:t>и</w:t><w:tab/><w:t>проверки.</w:t><w:br/><w:t>2. Далее</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
        raw=archive('word/document.xml',xml.encode())
        from document_metadata import read_document_history
        expected='обеспечения и проверки. 2. Далее'
        self.assertEqual(inspect_file(raw,'docx')['document_history'][0]['description'],expected)
        self.assertEqual(read_document_history(raw)[0]['description'],expected)
    def test_current_unmatched_url_keeps_own_state_despite_matched_equal_bytes(self):
        match=dict(number='999',title='Title',root='r',namespace='urn:a',dependency_digest='deps',schema_path='main.xsd',archive_sha256='zip',archive_url='a.zip')
        def row(url,matched=False):
            d=dict(environment='test',kind='docx',sha256='document',url=url,label='v1',message_types=[dict(number='999',title='Title')])
            if matched:d['module_matches']=[match]
            return d
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);keys=[]
            for records in ([row('A',True)],[row('A'),row('B',True)]):
                raw=encode(dict(format=2,entries=records));key=sha(raw);keys.append(key);write(state/'snapshots'/(key+'.json'),raw)
            write(state/'history.json',encode(keys));write(state/'current.json',encode(dict(snapshot=key)))
            _export(state,state/'catalog.json');data=json.loads((state/'catalog.json').read_bytes())
        self.assertEqual([(r['document_url'],r['listed_now']) for r in data['document_sources']],[('A',True)])

class ReceiptTests(unittest.TestCase):
    def test_corrupt_existing_receipt_is_not_silently_unchanged(self):
        manifest,objects=collector_fixtures.CollectorTests().capture();api=collector_fixtures.FakeAPI();publish_inputs(api,[manifest],objects)
        name='state/'+observation_path('home',manifest['environment'],manifest['content_id'])
        api.tree[name]='corrupt';api.blobs['corrupt']=b'2026-11\n'
        with self.assertRaisesRegex(ValueError,'receipt|квитанц'):publish_inputs(api,[manifest],objects)

class ImportTests(unittest.TestCase):
    def fixture(self):
        from tests.test_fix15_sources import FilenameImportReviewTests
        f=FilenameImportReviewTests();f.setUp();self.addCleanup(f.doCleanups);return f.root
    def test_occupied_path_does_not_remove_other_editions_overrides(self):
        from import_inbox import process_inbox
        from tests.test_source_collection_regressions import document
        root=self.fixture()
        for name in ('A','B'):
            folder=root/'incoming/test'/name
            write(folder/'999.docx',document(name));write(folder/'metadata.json',encode({'999.docx':{'title':name}}))
        process_inbox(root)
        write(root/'incoming/test/A/999.docx',(root/'incoming/test/B/999.docx').read_bytes())
        (root/'incoming/test/A/metadata.json').unlink()
        process_inbox(root)
        self.assertEqual({r['title'] for r in json.loads((root/'data/catalog.json').read_bytes())['revisions']},{'A','B'})
    def test_module_explicit_number_collect_stage_and_audit(self):
        from import_inbox import collect
        from manual_import import stage_import
        import schema_review
        from fields import build
        root=self.fixture();project=Path(__file__).resolve().parents[1]
        data=json.loads((project/'data/catalog.json').read_bytes())
        for number in ('14','701'):
            source=next(r for r in data['revisions'] if r['number']==number and r.get('source_type')=='module')
            folder=root/'incoming'/source['environment']/number
            doc=(project/'state/objects'/source['document_sha256']).read_bytes()
            package=(project/'state/objects'/source['archive_sha256']).read_bytes()
            write(folder/(number+'.docx'),doc);write(folder/'schemas.zip',package)
        tasks,_=collect(root)
        self.assertEqual({t['metadata']['number'] for t in tasks},{'14','701'})
        for task in tasks:
            result=stage_import(root,task['path'].read_bytes(),task['path'].name,task['metadata'],task['archive'].read_bytes())
            revision=result['revision']
            self.assertEqual(revision['root'],{'14':'invalidDataFault','701':'restrictionResponse'}[revision['number']])
            self.assertEqual(revision['source_type'],'manual')
            self.assertEqual(revision.get('document_type'),'module')
            self.assertFalse(stage_import(root,task['path'].read_bytes(),task['path'].name,task['metadata'],task['archive'].read_bytes())['new_revision'])
            with patch.object(schema_review.audit_module_tables,'audit',return_value=(0,[])) as audit:
                schema_review.compare_revision(revision,[],root/'state/objects')
                audit.assert_called_once()

class DisplayTests(unittest.TestCase):
    def test_archived_display_enrichment_preserves_observation_evidence(self):
        raw=word_table([['Код типа сообщения','Наименование','Описание','Приложение'],['999','обеспечения и','Описание','12']])
        # Turn the ordinary separating space into a Word paragraph boundary.
        with zipfile.ZipFile(io.BytesIO(raw)) as z: xml=z.read('word/document.xml').replace('обеспечения и'.encode(),'обеспечения</w:t></w:r></w:p><w:p><w:r><w:t>и'.encode())
        raw=archive('word/document.xml',xml);digest=sha(raw)
        row=dict(environment='test',kind='docx',url='https://a/Пр_12_Модуль.docx',label='v1',sha256=digest,registry_tables=[['unused']],message_types=[dict(number='999',title='обеспеченияи')])
        row['registry_tables']=[[['Код типа сообщения','Наименование','Описание','Приложение'],['999','обеспеченияи','Описание','12']]]
        match=dict(number='999',title='обеспеченияи',root='r',namespace=None,dependency_digest='deps',schema_path='main.xsd',archive_sha256='zip',archive_url='a.zip')
        row['module_matches']=[match]
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);snapshot=encode(dict(format=2,entries=[row]));key=sha(snapshot)
            write(state/'snapshots'/(key+'.json'),snapshot);write(state/'current.json',encode(dict(snapshot=key)))
            write(state/'objects'/digest,raw);write(state/'observations.json',encode(dict(snapshots={key:'2026-11-16T00:00:00+00:00'})))
            _export(state,state/'catalog.json');data=json.loads((state/'catalog.json').read_bytes())
            self.assertEqual(data['revisions'][0]['title'],'обеспечения и')
            self.assertEqual(data['document_display'][digest]['text_replacements']['обеспеченияи'],'обеспечения и')
            self.assertEqual(data['update_sources'][data['revisions'][0]['revision_id']]['title'],'обеспеченияи')
            self.assertEqual(data['updates'][0]['observed_at'],'2026-11-16T00:00:00+00:00')
            self.assertEqual((state/'snapshots'/(key+'.json')).read_bytes(),snapshot)

class ReceiptEvidenceTests(unittest.TestCase):
    def test_local_recapture_without_receipt_or_ledger_leaves_current_capture_intact(self):
        import collector
        manifest, objects = collector_fixtures.CollectorTests().capture()
        env, digest = manifest["environment"], manifest["content_id"]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            args = ["collector.py", "--capture-to", tmp, "--environment", env]

            def capture(stamp):
                with patch("sys.argv", args), patch("collector.capture",
                        return_value=(dict(manifest, observed_at=stamp), objects)):
                    collector.main()

            capture("2026-11-01T00:00:00+00:00")
            (state / observation_path("home", env, digest)).unlink()
            self.assertEqual(json.loads((state / "collector" / env / "current.json").read_bytes())["content_id"], digest)
            self.assertFalse((state / "collector/processed.json").exists())
            before = {p: p.read_bytes() for p in state.rglob("*") if p.is_file()}
            with self.assertRaisesRegex(ValueError, "receipt"):
                capture("2026-11-12T00:00:00+00:00")
            self.assertEqual({p: p.read_bytes() for p in state.rglob("*") if p.is_file()}, before)

    def test_local_recapture_after_receipt_removal_replays_first_home_observation(self):
        import collector
        from collector_snapshots import feed_manifests
        manifest, objects = collector_fixtures.CollectorTests().capture()
        first = "2026-11-01T00:00:00+00:00"
        later = "2026-11-12T00:00:00+00:00"
        env, digest = manifest["environment"], manifest["content_id"]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            args = ["collector.py", "--capture-to", tmp, "--environment", env]
            def capture(stamp):
                with patch("sys.argv", args), patch("collector.capture",
                        return_value=(dict(manifest, observed_at=stamp), objects)):
                    collector.main()
            capture(first)
            (state / observation_path("home", env, digest)).unlink()
            write(state / "collector/processed.json", encode({"sources": {"home": {env: {digest: {
                "status": "unavailable", "observed_at": first
            }}}}}))
            capture(later)
            with patch.dict("os.environ", SEDO_COLLECTOR_SOURCE="home"):
                self.assertEqual([m["observed_at"] for m in feed_manifests(state)], [first])

    def test_local_corruption_refuses_then_recovers_exact_home_ledger_time(self):
        import collector
        from collector_snapshots import feed_manifests
        manifest,objects=collector_fixtures.CollectorTests().capture()
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);args=['collector.py','--capture-to',tmp,'--environment','test']
            def capture():
                with patch('sys.argv',args),patch('collector.capture',return_value=(manifest,objects)):collector.main()
            capture()
            receipt=state/observation_path('home',manifest['environment'],manifest['content_id'])
            first=receipt.read_bytes();capture();self.assertEqual(receipt.read_bytes(),first)
            receipt.write_bytes(b'2026-11\n')
            before={p:p.read_bytes() for p in state.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ValueError,'receipt'):capture()
            self.assertEqual({p:p.read_bytes() for p in before},before)
            historical='2026-11-12T01:02:03+00:00'
            write(state/'collector/processed.json',encode(dict(sources=dict(home={'test':{manifest['content_id']:dict(status='replayed',observed_at=historical)}}))))
            capture();self.assertEqual(receipt.read_text().strip(),historical)
            with patch.dict('os.environ',SEDO_COLLECTOR_SOURCE='home'):
                self.assertEqual(feed_manifests(state)[0]['observed_at'],historical)
    def test_github_corrupt_receipt_uses_home_ledger_not_shared_envelope(self):
        manifest,objects=collector_fixtures.CollectorTests().capture();api=collector_fixtures.FakeAPI();publish_inputs(api,[manifest],objects)
        receipt='state/'+observation_path('home','test',manifest['content_id'])
        api.tree[receipt]='bad';api.blobs['bad']=b'2026-11\n'
        processed='state/collector/processed.json';api.uploaded.add(processed);api.tree[processed]='ledger'
        stamp='2026-11-12T01:02:03+00:00'
        api.blobs['ledger']=encode(dict(sources=dict(home={'test':{manifest['content_id']:dict(status='replayed',observed_at=stamp)}})))
        self.assertTrue(publish_inputs(api,[manifest],objects))
        self.assertEqual(api.blobs[api.tree[receipt]].decode().strip(),stamp)
        self.assertFalse(publish_inputs(api,[manifest],objects))

class BoundaryTests(unittest.TestCase):
    def test_one_retirement_is_allowed_and_first_build_has_no_comparison(self):
        f=update_fixtures.LocalGenerationTests();f.setUp();self.addCleanup(f.doCleanups)
        name=f.name.replace('999','998');f.resources[SITES['prod']]+=f'<a href="{name}">998</a>'.encode();f.resources[SITES['prod'].replace('/sedo.html',name)]=f.document
        f.run_update()
        f.resources[SITES['prod']]=f.resources[SITES['prod']].replace(f'<a href="{name}">998</a>'.encode(),b'')
        with patch('sync.get',side_effect=f.resources.__getitem__):sync(f.state)
        with patch('sys.argv',['update_catalog.py','--state',str(f.state),'--out',str(f.out),'--offline']):__import__('update_catalog').main()
        data=json.loads((f.out/'catalog.json').read_bytes())
        self.assertEqual({r['number'] for r in data['revisions'] if r['listed_now']},{'999'})
    def test_docx_format_seven_gate_and_legacy_replay(self):
        from tests.test_source_collection_regressions import SourceCollectionTests
        f=SourceCollectionTests();f.setUp();self.addCleanup(f.doCleanups)
        url=SITES['test'].replace('sedo.html','specs_sedo/file.docx')
        f.resources[url]=word_table([['Код типа сообщения','Наименование','Описание','Приложение'],['999','Title','Description','12']])
        f.collect(1,snapshot_format=6);old=f.current()
        self.assertNotIn('registry_tables',next(r for r in old['entries'] if r['url']==url))
        f.collect(2);fresh=f.current();self.assertEqual(fresh['format'],7)
        self.assertTrue(next(r for r in fresh['entries'] if r['url']==url)['registry_tables'])
        f.collect(3,snapshot_format=6);self.assertEqual(f.current(),old)
    def test_zip_replacement_keeps_other_edition_title_and_same_edition_can_reset(self):
        from import_inbox import process_inbox
        from tests.test_fix15_sources import FilenameImportReviewTests
        f=FilenameImportReviewTests();f.setUp();self.addCleanup(f.doCleanups);root=f.root
        schema=b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>'
        for name in ('A','B'):
            folder=root/'incoming/test'/name;write(folder/'999.docx',individual_document('response'))
            write(folder/'schemas.zip',archive('main.xsd',schema.replace(b'xs:string',b'xs:int') if name=='B' else schema));write(folder/'metadata.json',encode({'999.docx':{'title':name}}))
        process_inbox(root)
        write(root/'incoming/test/A/schemas.zip',(root/'incoming/test/B/schemas.zip').read_bytes());(root/'incoming/test/A/metadata.json').unlink()
        process_inbox(root)
        self.assertEqual({r['title'] for r in json.loads((root/'data/catalog.json').read_bytes())['revisions']},{'A','B'})
        (root/'incoming/test/B/metadata.json').unlink();process_inbox(root)
        self.assertEqual({r['title'] for r in json.loads((root/'data/catalog.json').read_bytes())['revisions']},{'A','Сообщение 999'})
    def test_module_wrong_number_and_two_distinct_schemas_are_rejected(self):
        from import_inbox import collect
        from tests.test_source_collection_regressions import document
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'incoming/test';write(folder/'998.docx',document('Example'))
            schema=b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>'
            write(folder/'a.zip',archive('main.xsd',schema))
            with self.assertRaisesRegex(ValueError,'Номер сообщения'):collect(root)
            (folder/'998.docx').rename(folder/'999.docx')
            write(folder/'b.zip',archive('main.xsd',schema.replace(b'xs:string',b'xs:int')))
            with self.assertRaisesRegex(ValueError,'несколько'):collect(root)

class ModuleDisplayTests(unittest.TestCase):
    def test_registry_module_paragraph_boundary_repairs_edition_display(self):
        from document_metadata import read_document_display
        rows=[['Код типа сообщения','Наименование','Описание','Приложение'],['обеспечения и'],['999','Title','Description','12']]
        raw=word_table(rows)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:xml=z.read('word/document.xml').replace('обеспечения и'.encode(),'обеспечения</w:t></w:r></w:p><w:p><w:r><w:t>и'.encode())
        display=read_document_display(archive('word/document.xml',xml))
        self.assertEqual(display['text_replacements'].get('обеспеченияи'),'обеспечения и')

class MissingOriginalTests(unittest.TestCase):
    def test_absent_original_cannot_clear_saved_display_history(self):
        history=[dict(version='1',date='x',description='Saved history')]
        match=dict(number='999',title='Title',root='r',namespace=None,dependency_digest='deps',schema_path='main.xsd',archive_sha256='zip',archive_url='a.zip')
        row=dict(environment='test',kind='docx',url='module.docx',label='v1',sha256='missing',message_types=[dict(number='999',title='Title')],module_matches=[match],document_history=history)
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);raw=encode(dict(format=2,entries=[row]));key=sha(raw)
            write(state/'snapshots'/(key+'.json'),raw);write(state/'current.json',encode(dict(snapshot=key)));_export(state,state/'catalog.json')
            data=json.loads((state/'catalog.json').read_bytes())
            self.assertEqual(data['revisions'][0]['document_history'],history)
            self.assertNotIn('missing',data.get('document_display',{}))

class UnreadableReceiptTests(unittest.TestCase):
    def test_local_oversize_receipt_refuses_then_restores_home_envelope_time(self):
        import collector
        manifest,objects=collector_fixtures.CollectorTests().capture()
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            def capture():
                with patch('sys.argv',['collector.py','--capture-to',tmp,'--environment','test']),patch('collector.capture',return_value=(manifest,objects)):collector.main()
            capture();receipt=state/observation_path('home','test',manifest['content_id']);receipt.write_bytes(b'x'*257)
            before={p:p.read_bytes() for p in state.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ValueError,'receipt'):capture()
            self.assertEqual({p:p.read_bytes() for p in before},before)
            stamp='2026-11-10T01:02:03+00:00'
            write(state/'collector/processed.json',encode(dict(sources=dict(home={'test':{manifest['content_id']:dict(status='replayed',observed_at=stamp)}}))))
            envelope=state/'collector/test'/(manifest['content_id']+'.json');envelope.write_bytes(b'bad')
            write(state/observation_path('sourcecraft','test',manifest['content_id']),b'2026-11-11T00:00:00+00:00\n')
            capture()
            self.assertEqual(receipt.read_text().strip(),stamp)
            self.assertEqual(json.loads(envelope.read_bytes())['observed_at'],stamp)
    def test_github_unreadable_receipt_refuses_then_restores_home_envelope_time(self):
        import collector_feed
        manifest,objects=collector_fixtures.CollectorTests().capture()
        class UnreadableAPI(collector_fixtures.FakeAPI):
            def __call__(self,method,path,payload=None):
                if path=='git/blobs/oversize':return dict(encoding='base64',size=collector_feed.MAX_MANIFEST+1,content='')
                return super().__call__(method,path,payload)
        api=UnreadableAPI();publish_inputs(api,[manifest],objects)
        receipt='state/'+observation_path('home','test',manifest['content_id']);api.tree[receipt]='oversize'
        before=dict(api.tree)
        with self.assertRaisesRegex(ValueError,'receipt'):publish_inputs(api,[manifest],objects)
        self.assertEqual(api.tree,before)
        stamp='2026-11-10T01:02:03+00:00';processed='state/collector/processed.json';api.uploaded.add(processed);api.tree[processed]='ledger'
        api.blobs['ledger']=encode(dict(sources=dict(home={'test':{manifest['content_id']:dict(status='replayed',observed_at=stamp)}})))
        envelope='state/collector/test/'+manifest['content_id']+'.json';api.tree[envelope]='broken';api.blobs['broken']=b'bad'
        sc='state/'+observation_path('sourcecraft','test',manifest['content_id']);api.uploaded.add(sc);api.tree[sc]='sc';api.blobs['sc']=b'2026-11-11T00:00:00+00:00\n'
        self.assertTrue(publish_inputs(api,[manifest],objects))
        self.assertEqual(api.blobs[api.tree[receipt]].decode().strip(),stamp)
        self.assertEqual(json.loads(api.blobs[api.tree[envelope]])['observed_at'],stamp)

class ReplayUrlTests(unittest.TestCase):
    def test_legacy_query_number_survives_offline_rebuild_and_fresh_uses_path(self):
        from fields import build
        f=update_fixtures.LocalGenerationTests();f.setUp();self.addCleanup(f.doCleanups)
        query='/specs_sedo/guide.docx?download=Тип_сообщения_701'
        f.resources[SITES['prod']]=f.resources[SITES['prod']].replace(f.name.encode(),query.encode())
        f.resources[SITES['prod'].replace('/sedo.html',query)]=f.document
        with patch('sync.get',side_effect=f.resources.__getitem__):sync(f.state,snapshot_format=6)
        _export(f.state,f.out/'catalog.json');build(f.out/'catalog.json',f.state,f.out/'fields')
        before=json.loads((f.out/'catalog.json').read_bytes());self.assertEqual([r['number'] for r in before['revisions']],['701'])
        with patch('sys.argv',['update_catalog.py','--state',str(f.state),'--out',str(f.out),'--offline']):__import__('update_catalog').main()
        after=json.loads((f.out/'catalog.json').read_bytes());self.assertEqual(after['updates'],before['updates']);self.assertEqual(after['source_observations'],before['source_observations'])
        f.resources[SITES['prod']]=f.resources[SITES['prod']].replace(query.encode(),(f.name+'?r=/foo').encode())
        f.resources[SITES['prod'].replace('/sedo.html',f.name+'?r=/foo')]=f.document
        with patch('sync.get',side_effect=f.resources.__getitem__):sync(f.state)
        ptr=json.loads((f.state/'current.json').read_bytes())['snapshot'];fresh=json.loads((f.state/'snapshots'/(ptr+'.json')).read_bytes())
        self.assertEqual(fresh['format'],7);self.assertEqual([r['number'] for r in derive(fresh)['revisions']],['999'])
    def test_inbox_explicit_schema_location_resolves_included_roots(self):
        from import_inbox import collect
        from manual_import import stage_import
        from tests.test_fix15_sources import FilenameImportReviewTests
        f=FilenameImportReviewTests();f.setUp();self.addCleanup(f.doCleanups);root=f.root
        raw=individual_document('response')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:xml=z.read('word/document.xml').replace(b'</w:tbl>',b'<w:tr><w:tc><w:p><w:r><w:t>'+('Расположение файла XML-схемы').encode()+b'</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>main.xsd</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
        doc=archive('word/document.xml',xml)
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            z.writestr('main.xsd','<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:include schemaLocation="common.xsd"/></xs:schema>')
            z.writestr('common.xsd','<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>')
        write(root/'incoming/test/999.docx',doc);write(root/'incoming/test/a.zip',buffer.getvalue())
        task=collect(root)[0][0];result=stage_import(root,doc,'999.docx',task['metadata'],buffer.getvalue())
        self.assertEqual(result['revision']['schema_path'],'main.xsd')

class DownloadAliasUrlTests(unittest.TestCase):
    def test_successful_aliases_enable_url_format_independently_of_link_order(self):
        fresh_numbers=[]
        for reverse in (False,True):
            with self.subTest(reverse=reverse):
                f=update_fixtures.LocalGenerationTests();f.setUp();self.addCleanup(f.doCleanups)
                path='/specs_sedo/guide.docx';alias=path+'#Тип_сообщения_701'
                paths=[path,alias]
                if reverse:paths.reverse()
                f.resources[SITES['prod']]=b'<a href="/specs_sedo/a.zip">XSD</a>'+''.join(f'<a href="{url}">Guide</a>' for url in paths).encode()
                f.resources[SITES['prod'].replace('/sedo.html',path)]=f.document
                with patch('sync.get',side_effect=f.resources.__getitem__):sync(f.state,snapshot_format=6)
                key=json.loads((f.state/'current.json').read_bytes())['snapshot'];old=json.loads((f.state/'snapshots'/(key+'.json')).read_bytes())
                self.assertEqual([r['number'] for r in derive(old)['revisions']],['701'])
                with patch('sync.get',side_effect=f.resources.__getitem__) as fetch:sync(f.state)
                key=json.loads((f.state/'current.json').read_bytes())['snapshot'];fresh=json.loads((f.state/'snapshots'/(key+'.json')).read_bytes())
                self.assertEqual(fresh['format'],7)
                fresh_numbers.append([r['number'] for r in derive(fresh)['revisions']])
                self.assertEqual(sum(call.args[0].endswith('guide.docx') for call in fetch.call_args_list),1)
        self.assertEqual(fresh_numbers,[[],[]])

class RecordedHomeReceiptTests(unittest.TestCase):
    def test_recorded_home_ledger_is_confirmed_historical_evidence(self):
        from collector import repaired_home_receipt
        manifest,_=collector_fixtures.CollectorTests().capture()
        stamp='2026-11-10T01:02:03+00:00'
        ledger=dict(format=1,sources=dict(home={'test':{manifest['content_id']:dict(status='recorded',snapshot='a'*64,observed_at=stamp)}}))
        repaired=repaired_home_receipt(b'broken',manifest,lambda:encode(ledger))
        self.assertEqual(repaired,(stamp+'\n').encode())

class HistoricalUrlReplayTests(unittest.TestCase):
    def test_old_query_edition_without_intermediate_root_is_unavailable(self):
        from collector_snapshots import make_manifest,replay_history
        from collector_feed import store_feed,import_feed
        from tests import test_wsdl_sources as wsdl
        fixture=wsdl.WsdlSourcesTests();original,objects=fixture.collect(fixture.bundle())
        resources=fixture.bundle();url=wsdl.BASE+'?xsd=main.xsd';resources[url]=resources[url].replace(b'name="response"',b'name="other"')
        replacement,new_objects=fixture.collect(resources);objects.update(new_objects)
        manifest=make_manifest(replacement,'2026-11-14T12:00:00+00:00')
        raw=individual_document('response');doc=dict(environment='test',kind='docx',url='https://lk-test.fss.ru/specs_sedo/guide.docx?download=Тип_сообщения_701',label='v1',sha256=sha(raw),**inspect_file(raw,'docx',legacy_docx=True))
        baseline=dict(format=6,entries=[doc,original]);key=sha(encode(baseline))
        self.assertEqual([r['number'] for r in derive(baseline)['revisions']],['701'])
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'state';remote=Path(tmp)/'remote';write(state/'objects'/sha(raw),raw)
            for digest,content in objects.items():write(state/'objects'/digest,content)
            write(state/'snapshots'/(key+'.json'),encode(baseline));write(state/'current.json',encode(dict(snapshot=key)));write(state/'history.json',encode([key]));write(state/'observations.json',encode(dict(snapshots={key:'2026-11-12T00:00:00+00:00'})))
            store_feed(remote,[manifest],objects);import_feed(state,lambda name:(remote/name).read_bytes())
            def blob(content):write(state/'objects'/sha(content),content);return sha(content)
            with patch.dict('os.environ',SEDO_COLLECTOR_SOURCE='sourcecraft'):replayed,ledger=replay_history(state,baseline,blob)
            self.assertEqual(replayed,[])
            record=ledger['manifests']['test'][manifest['content_id']]
            self.assertEqual(record['status'],'unavailable');self.assertEqual([r['number'] for r in record['unmatched_documents']],['701'])

class ContextualModuleDisplayTests(unittest.TestCase):
    def test_repeated_message_in_registry_preserves_each_module_membership(self):
        rows=[['Код типа сообщения','Наименование','Описание','Приложение'],['обеспечения и'],['14','Service','One','2'],['Income'],['14','Service','Two','12']]
        raw=word_table(rows)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:xml=z.read('word/document.xml').replace('обеспечения и'.encode(),'обеспечения</w:t></w:r></w:p><w:p><w:r><w:t>и'.encode())
        raw=archive('word/document.xml',xml);digest=sha(raw)
        entry=dict(kind='docx',environment='test',url='https://a/registry.docx',label='Registry',sha256=digest,**inspect_file(raw,'docx',legacy_docx=True))
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);snapshot=encode(dict(format=6,entries=[entry]));key=sha(snapshot)
            write(state/'objects'/digest,raw);write(state/'snapshots'/(key+'.json'),snapshot);write(state/'current.json',encode(dict(snapshot=key)))
            _export(state,state/'catalog.json');data=json.loads((state/'catalog.json').read_bytes())
            self.assertEqual([row['module'] for row in data['registry']],['обеспечения и','Income'])
            replacements=data['document_display'][digest].get('text_replacements',{})
            self.assertEqual(replacements.get('обеспеченияи'),'обеспечения и')
            self.assertNotIn('Income',replacements)
    def test_original_14_current_sources_preserve_module_title_and_url_by_contour(self):
        from edition_groups import group_editions
        from build_site import share_document_histories
        project=Path(__file__).resolve().parents[1]
        key=json.loads((project/'state/current.json').read_bytes())['snapshot'];snapshot=json.loads((project/'state/snapshots'/(key+'.json')).read_bytes())
        expected={(r['environment'],r['document_url']):(r['title'],r['module']) for r in derive(snapshot)['revisions'] if r['number']=='14'}
        self.assertEqual({env for env,url in expected},{'prod','test'})
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.json';_export(project/'state',path);data=json.loads(path.read_bytes())
        for category,minimum in (('registry',80),('revisions',38)):
            self.assertFalse(any('обеспеченияи' in (row.get('module') or '') for row in data[category]))
            self.assertGreaterEqual(sum('обеспечения и' in (row.get('module') or '') for row in data[category]),minimum)
        rows=[dict(r,ready=True) for r in data['revisions'] if r['number']=='14']
        for row in rows:
            key=(row['environment'],row['document_url'])
            if row.get('listed_now') and key in expected:self.assertEqual((row['title'],row['module']),expected[key])
        subset=dict(revisions=rows,source_observations=[o for o in data['source_observations'] if o['source'].get('number')=='14'],document_display=data['document_display'])
        grouped=group_editions(subset,lambda rid:json.loads((project/'data/fields'/(rid+'.json')).read_bytes())['fields'])
        share_document_histories(grouped)
        actual={}
        for row in grouped['revisions']:
            for source in row['edition_sources']:
                key=(source['environment'],source.get('document_url'))
                if source.get('listed_now') and key in expected:actual[key]=(source['title'],source['module'])
        self.assertEqual(actual,expected)

class AmbiguousDisplayTextTests(unittest.TestCase):
    def test_same_joined_title_with_two_meanings_is_not_replaced(self):
        from document_metadata import read_document_display
        raw=word_table([['Код типа сообщения','Наименование','Описание','Приложение'],['14','ab','Description','2'],['701','a b','Description','12']])
        with zipfile.ZipFile(io.BytesIO(raw)) as z:xml=z.read('word/document.xml').replace(b'<w:t>a b</w:t>',b'<w:t>a</w:t></w:r></w:p><w:p><w:r><w:t>b</w:t>')
        display=read_document_display(archive('word/document.xml',xml))
        self.assertNotIn('ab',display['text_replacements'])

class AmbiguousRegistryPropagationTests(unittest.TestCase):
    def test_registry_correction_does_not_reenter_an_ambiguous_document(self):
        def raw_doc(number, split, second=False):
            raw=word_table([['Код типа сообщения','Наименование','Описание','Приложение'],['Group'],[number,'ab','Description','2']]+([['15','a b','Description','2']] if second else []))
            with zipfile.ZipFile(io.BytesIO(raw)) as z:xml=z.read('word/document.xml')
            if split:xml=xml.replace(b'<w:t>ab</w:t>',b'<w:t>a</w:t><w:br/><w:t>b</w:t>')
            if second:xml=xml.replace(b'<w:t>a b</w:t>',b'<w:t>a</w:t><w:br/><w:t>b</w:t>')
            return archive('word/document.xml',xml)
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);entries=[]
            for name,raw in [('ambiguous',raw_doc('14',False,True)),('simple',raw_doc('16',True))]:
                digest=sha(raw);write(state/'objects'/digest,raw);entries.append(dict(environment='test',kind='docx',url='https://a/'+name+'.docx',label=name,sha256=digest,**inspect_file(raw,'docx',legacy_docx=True)))
            payload=encode(dict(format=6,entries=entries));key=sha(payload);write(state/'snapshots'/(key+'.json'),payload);write(state/'current.json',encode(dict(snapshot=key)));_export(state,state/'catalog.json');data=json.loads((state/'catalog.json').read_bytes())
            self.assertNotIn('ab', data['document_display'][entries[0]['sha256']]['text_replacements'])
            self.assertEqual({r['number']:r['title'] for r in data['registry']},{'14':'ab','15':'a b','16':'a b'})

class DisplayBoundaryRegressionTests(unittest.TestCase):
    def export_documents(self,raw_documents):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);state=Path(temp.name);entries=[]
        for index,(raw,numbers) in enumerate(raw_documents):
            digest=sha(raw);write(state/'objects'/digest,raw);meta=inspect_file(raw,'docx',legacy_docx=True)
            titles={row[0]:row[1] for table in meta['registry_tables'] for row in table[1:] if len(row)>=2 and row[0].isdigit()}
            matches=[dict(number=number,title=titles[number],root='r',namespace=None,dependency_digest='deps',schema_path='main.xsd',archive_sha256='archive',archive_url='x.zip') for number in numbers]
            entries.append(dict(environment='test',kind='docx',url='https://a/doc'+str(index)+'.docx',label='Document',sha256=digest,module_matches=matches,**meta))
        raw=encode(dict(format=6,entries=entries));key=sha(raw);write(state/'snapshots'/(key+'.json'),raw);write(state/'current.json',encode(dict(snapshot=key)));_export(state,state/'catalog.json');return json.loads((state/'catalog.json').read_bytes())
    def test_corrected_title_does_not_replace_genuine_other_document_title(self):
        a=word_table([['Код типа сообщения','Наименование','Описание','Приложение'],['A'],['14','ab','Description','2']])
        with zipfile.ZipFile(io.BytesIO(a)) as z:xml=z.read('word/document.xml').replace(b'<w:t>ab</w:t>',b'<w:t>a</w:t><w:br/><w:t>b</w:t>')
        a=archive('word/document.xml',xml)
        b=word_table([['Код типа сообщения','Наименование','Описание','Приложение'],['B'],['701','ab','Description','12']])
        data=self.export_documents([(a,['14']),(b,['701'])])
        self.assertEqual({r['number']:r['title'] for r in data['revisions']},{'14':'a b','701':'ab'})
        self.assertEqual({r['number']:r['title'] for r in data['registry']},{'14':'a b','701':'ab'})
    def test_corrected_titles_are_applied_once_in_public_edition_sources(self):
        from edition_groups import group_editions
        from build_site import share_document_histories
        raw=word_table([['Код типа сообщения','Наименование','Описание','Приложение'],['A'],['14','abc','Description','2'],['701','a bc','Description','12']])
        with zipfile.ZipFile(io.BytesIO(raw)) as z:xml=z.read('word/document.xml').replace(b'<w:t>abc</w:t>',b'<w:t>a</w:t><w:br/><w:t>bc</w:t>').replace(b'<w:t>a bc</w:t>',b'<w:t>a b</w:t><w:br/><w:t>c</w:t>')
        data=self.export_documents([(archive('word/document.xml',xml),['14','701'])])
        expected={'14':'a bc','701':'a b c'}
        self.assertEqual({r['number']:r['title'] for r in data['revisions']},expected)
        self.assertTrue(all('display_metadata_applied' not in o['source'] for o in data['source_observations']))
        grouped=group_editions(data,lambda rid:[]);share_document_histories(grouped)
        self.assertEqual({r['number']:r['title'] for r in grouped['revisions']},expected)
        self.assertEqual({s['number']:s['title'] for r in grouped['revisions'] for s in r['edition_sources']},expected)
        self.assertFalse(any('display_metadata_applied' in r for r in grouped['revisions']))
