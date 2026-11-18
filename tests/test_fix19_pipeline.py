import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import automation_status
import build_site
import sync
from auto_update import recover_promotion
from update_catalog import recover_catalog


class PipelineReviewTests(unittest.TestCase):
    def test_pending_import_uses_current_generator_for_history_baseline(self):
        import fields
        from import_inbox import process_inbox
        from tests.test_fix15_sources import FilenameImportReviewTests
        from tests.test_source_collection_regressions import individual_document
        from wsdl_sources import _archive
        fixture = FilenameImportReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        sync.write(root/'incoming/test/one/999.docx', individual_document('response'))
        sync.write(root/'incoming/test/one/schema.zip', _archive({'main.xsd': b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:int"/></xs:schema>'}))
        process_inbox(root)
        before = (root/'data/catalog.json').read_bytes()
        sync.write(root/'incoming/test/two/998.docx', individual_document('reply'))
        original = fields.Schema
        class Updated(original):
            def field(self, *args, **kwargs):
                result = super().field(*args, **kwargs)
                self.fields[0]['doc'] = 'Updated annotation'
                return result
        with patch.object(fields, 'Schema', Updated):
            self.assertEqual(process_inbox(root, check=True)['processed'], 1)
        self.assertEqual((root/'data/catalog.json').read_bytes(), before)

    def test_field_file_must_identify_its_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sync.write(root/'data/catalog.json', sync.encode({'revisions':[{'revision_id':'prod','status':'matched'}]}))
            sync.write(root/'data/fields/index.json', sync.encode([{'revision_id':'prod','status':'ready'}]))
            sync.write(root/'data/fields/prod.json', sync.encode({'revision_id':'test','fields':[]}))
            with patch.object(build_site, 'HERE', root), patch.object(build_site, 'archive_documents'), patch.object(build_site, 'publish_sources'):
                with self.assertRaisesRegex(ValueError, 'редакц'):
                    build_site.main(root/'site')

    def test_recovery_preserves_unknown_files(self):
        for name, recover in (('.update-recovery', lambda p: recover_promotion(p)),
                              ('.catalog-recovery', lambda p: recover_catalog(p, p/'data'))):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                note = root/name/'operator-notes.txt'
                sync.write(note, b'keep')
                with self.assertRaises(ValueError):
                    recover(root)
                self.assertEqual(note.read_bytes(), b'keep')

    def test_recovery_requires_ownership_even_for_plausible_nested_layout(self):
        for name, relative, recover in (
                ('.update-recovery', 'candidate/data/operator-notes.txt', recover_promotion),
                ('.catalog-recovery', 'data/fields/operator-notes.txt', lambda p: recover_catalog(p, p/'data'))):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                note = root/name/relative
                sync.write(note, b'keep nested notes')
                with self.assertRaises(ValueError):recover(root)
                self.assertEqual(note.read_bytes(), b'keep nested notes')

    def test_unknown_file_beside_real_recovery_journal_preserves_all_evidence(self):
        for name, saved, recover in (
                ('.update-recovery', {'state': False, 'data': False, 'site': False}, recover_promotion),
                ('.catalog-recovery', None, lambda p: recover_catalog(p, p/'data'))):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                saved = saved or {'output': str((root/'data').resolve()), 'files': []}
                journal = root/name/'journal.json'
                sync.write(journal, sync.encode(saved))
                sync.write(root/name/'operator-notes.txt', b'keep notes')
                with self.assertRaises(ValueError):recover(root)
                self.assertEqual(journal.read_bytes(), sync.encode(saved))
                self.assertEqual((root/name/'operator-notes.txt').read_bytes(), b'keep notes')

    def test_query_iri_is_encoded_without_changing_delimiters(self):
        url = 'https://lk.sfr.gov.ru/specs_sedo/a.docx?редакция=ноябрь&x=%2F+%26&x=2'
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'ok'
            def geturl(self): return url
        class Opener:
            def open(self, request, **kwargs):
                request.selector.encode('ascii')
                self_request.append(request.selector)
                return Response()
        self_request = []
        with patch.object(sync, 'build_opener', return_value=Opener()):
            sync._get(url, attempts=1)
        self.assertIn('&x=%2F+%26&x=2', self_request[0])

    def test_deployed_attempt_survives_cancelled_refresh(self):
        run = dict(id=7, run_attempt=2, status='completed', conclusion='cancelled',
                   updated_at='2026-11-17T09:00:00Z')
        jobs = {'jobs':[{'name':'deploy','conclusion':'success','completed_at':'2026-11-17T08:00:00Z'}]}
        with patch.object(automation_status, 'api', return_value=jobs):
            result = automation_status.publication_evidence(run)
        self.assertEqual(automation_status.run_info(result)['state'], 'success')
        self.assertEqual(result['updated_at'], '2026-11-17T08:00:00Z')

    def test_cancelled_before_deploy_stays_cancelled(self):
        run = dict(id=7, run_attempt=2, status='completed', conclusion='cancelled')
        with patch.object(automation_status, 'api', return_value={'jobs':[{'name':'deploy','conclusion':'skipped'}]}):
            self.assertEqual(automation_status.publication_evidence(run), run)

    def test_collect_status_uses_deploy_for_current_and_previous_publication(self):
        from tests.test_automation_status import run
        check = dict(run(1), path='.github/workflows/check-sfr.yml')
        page = dict(run(2, conclusion='cancelled'), run_attempt=2)
        reports = {'update-report.json': {'status':'ok','collector_source':'home'},
                   'publication-target.json': {'update_run_id':1,'head_sha':'a'*40}}
        def api(path):
            if '/jobs?' in path:
                self.assertIn('/attempts/2/', path)
                return {'jobs':[{'name':'deploy','conclusion':'success','completed_at':page['updated_at']}]}
            return {'workflow_runs':[check] if 'check-sfr' in path else [page]}
        with patch.object(automation_status,'api',side_effect=api), patch.object(automation_status,'sourcecraft_status',return_value={}), patch.object(automation_status,'run_reports',return_value=reports), patch.object(automation_status,'check_history',side_effect=lambda x:x), patch.object(automation_status,'active_check_runs',return_value=[]), patch.object(automation_status,'successful_prior_attempts',return_value=[]):
            result = automation_status.collect_status()['github']
        self.assertEqual(result['publication']['state'], 'success')
        self.assertEqual(result['last_successful_publication']['state'], 'success')

    def test_legacy_publication_is_found_beyond_three_runs(self):
        from tests.test_automation_status import run
        check = dict(run(1), path='.github/workflows/check-sfr.yml')
        pages = [run(i, sha=str(i)*40) for i in (5, 4, 3, 2)]
        reports = {'update-report.json': {'status':'ok', 'checked_at':'match', 'collector_source':'home'}}
        def api(path):
            return {'workflow_runs':[check] if 'check-sfr' in path else pages}
        def catalog(url):
            return json.dumps({'checked_at':'match' if '2'*40 in url else 'other'}).encode()
        with patch.object(automation_status,'api',side_effect=api), patch.object(automation_status,'read_public',side_effect=catalog), patch.object(automation_status,'sourcecraft_status',return_value={}), patch.object(automation_status,'run_reports',return_value=reports), patch.object(automation_status,'check_history',side_effect=lambda x:x), patch.object(automation_status,'active_check_runs',return_value=[]), patch.object(automation_status,'successful_prior_attempts',return_value=[]):
            result = automation_status.collect_status()
        self.assertEqual(result['github']['publication']['run_id'], 2)
