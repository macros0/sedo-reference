import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fields
import update_catalog
from sync import encode, inspect_file, sha, write
from tests.test_schema_recovery import archive
from tests.test_source_collection_regressions import individual_document


class PublicationBuildTests(unittest.TestCase):
    def test_unknown_builtin_rejects_offline_candidate_and_preserves_previous_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            document=individual_document('response')
            history=[]
            argv=['update_catalog.py','--offline','--state',str(root/'state'),'--out',str(root/'data')]
            for builtin in ('string','strng'):
                schema=archive({'response.xsd': f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:{builtin}"/></xs:schema>'.encode()})
                rows=[]
                for content,kind,url in ((document,'docx','Тип_сообщения_999.docx'),(schema,'zip','schemas.zip')):
                    digest=sha(content);write(root/'state/objects'/digest,content)
                    rows.append(dict(environment='test',kind=kind,url='https://lk-test.fss.ru/'+url,label='Тип сообщения 999 v1.0',sha256=digest,**inspect_file(content,kind)))
                snapshot=encode(dict(format=2,entries=rows,pages={}))
                digest=sha(snapshot);history.append(digest)
                write(root/'state/snapshots'/(digest+'.json'),snapshot)
                write(root/'state/current.json',encode(dict(snapshot=digest)))
                write(root/'state/history.json',encode(history))
                if builtin == 'string':
                    with patch.object(sys,'argv',argv): update_catalog.main()
                    previous={str(path.relative_to(root/'data')):path.read_bytes() for path in (root/'data').rglob('*') if path.is_file()}
                else:
                    with patch.object(sys,'argv',argv), self.assertRaisesRegex(SystemExit,'xs:strng'):
                        update_catalog.main()
                    self.assertEqual({str(path.relative_to(root/'data')):path.read_bytes() for path in (root/'data').rglob('*') if path.is_file()},previous)

    def test_pr_checks_the_same_inputs_and_artifacts_as_pages(self):
        project = Path(__file__).resolve().parents[1]
        for name in ('pages.yml', 'validate-incoming.yml'):
            workflow = (project / '.github/workflows' / name).read_text('utf8')
            steps = ('python collector_snapshots.py', 'python import_inbox.py', 'python update_catalog.py --offline',
                     'python verify.py', 'python build_site.py', 'node tests/test_browser_validation.cjs --all-editions',
                     'node tests/test_attribute_ref_constraints.cjs --all-editions', 'npm run test:browser')
            positions = [workflow.index(command) for command in steps]
            self.assertEqual(positions, sorted(positions))
            if name.startswith('validate'):
                self.assertIn('python collector_snapshots.py --offline', workflow)

    def test_all_workflows_run_discovered_node_suites_on_candidate(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('pages.yml', 'check-sfr.yml', 'validate-incoming.yml'):
            workflow = (root / '.github/workflows' / name).read_text('utf8')
            with self.subTest(workflow=name):
                self.assertIn('npm run test:node', workflow)
                self.assertLess(workflow.index('python build_site.py') if name != 'check-sfr.yml' else workflow.index('python auto_update.py'), workflow.rindex('npm run test:node'))

    def test_offline_candidate_refuses_an_unknown_feed_pointer_before_build(self):
        from collector_snapshots import apply_pending
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root/'state/current.json', encode({'snapshot':'A'}))
            write(root/'state/snapshots/A.json', encode({'entries':[]}))
            write(root/'state/collector/feed.json', encode({'format':1,'manifests':[],'latest':{'prod':'b'*64}}))
            with self.assertRaisesRegex(ValueError, 'missing from history'):
                apply_pending(root, offline=True)

    def test_offline_and_pages_apply_identical_inputs_to_identical_catalogs(self):
        from datetime import datetime, timezone
        from collector_feed import store_feed, import_feed
        from collector_snapshots import apply_pending
        from tests.test_source_collection_regressions import SourceCollectionTests
        from tests.test_collector_feed import FeedTests
        from catalog import export
        project = Path(__file__).resolve().parents[1]
        f = SourceCollectionTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.collect(1)
        shutil.copytree(project/'web', f.root/'web')
        shutil.copy2(project/'viewer-template.html', f.root)
        export(f.state, f.root/'data/catalog.json')
        fields.build(f.root/'data/catalog.json', f.state, f.root/'data/fields')
        manifest, objects = FeedTests().capture()
        remote = f.root/'remote'
        store_feed(remote, [manifest], objects)
        import_feed(f.state, lambda name: (remote/name).read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            pr = Path(tmp)/'pr'
            shutil.copytree(f.root, pr)
            with patch('sync.datetime') as clock:
                clock.now.return_value = datetime(2026,11,17,tzinfo=timezone.utc)
                with patch('sync.get', side_effect=f.resources.__getitem__):
                    apply_pending(f.root)
                with patch('sync._get', side_effect=AssertionError('unexpected live HTTP')):
                    apply_pending(pr, offline=True)
            for name in ('state/current.json','data/catalog.json','data/fields/index.json','site/catalog-data.js'):
                self.assertEqual((f.root/name).read_bytes(), (pr/name).read_bytes(), name)

    def test_pr_and_pages_rebuild_captured_fields_before_site(self):
        root = Path(__file__).resolve().parents[1]
        for workflow in ('pages.yml', 'validate-incoming.yml'):
            content = (root / '.github/workflows' / workflow).read_text('utf8')
            blocks = re.findall(r'        run: \|\n((?:          [^\n]*\n)+)', content)
            build = next(block for block in blocks if 'python build_site.py' in block)
            with self.subTest(workflow=workflow):
                self.assertIn('python update_catalog.py --offline', build)
                self.assertLess(build.index('python update_catalog.py --offline'), build.index('python build_site.py'))

    def test_offline_entry_recomputes_fields_with_stable_edition_and_events(self):
        document = individual_document('response')
        schema = archive({'response.xsd': b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>'})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            for content, kind, url in ((document, 'docx', 'Тип_сообщения_999.docx'), (schema, 'zip', 'schemas.zip')):
                digest = sha(content)
                write(root/'state/objects'/digest, content)
                rows.append(dict(environment='test', kind=kind, url='https://lk-test.fss.ru/'+url,
                                 label='Тип сообщения 999 v1.0', sha256=digest, **inspect_file(content, kind)))
            snapshot = encode(dict(format=2, entries=rows, pages={}))
            digest = sha(snapshot)
            write(root/'state/snapshots'/(digest+'.json'), snapshot)
            write(root/'state/current.json', encode(dict(snapshot=digest)))
            write(root/'state/history.json', encode([digest]))
            argv = ['update_catalog.py', '--offline', '--state', str(root/'state'), '--out', str(root/'data')]
            with patch.object(sys, 'argv', argv):
                update_catalog.main()
            before = json.loads((root/'data/catalog.json').read_bytes())
            rid = before['revisions'][0]['revision_id']
            original = fields.Schema
            class UpdatedSchema(original):
                def field(self, *args, **kwargs):
                    result = super().field(*args, **kwargs)
                    self.fields[0]['doc'] = 'Updated generator annotation'
                    return result
            with patch.object(sys, 'argv', argv), patch.object(fields, 'Schema', UpdatedSchema):
                update_catalog.main()
            current = json.loads((root/'data/catalog.json').read_bytes())
            self.assertEqual(current['updates'], before['updates'])
            self.assertEqual(current['revisions'][0]['revision_id'], rid)
            self.assertEqual(json.loads((root/'data/fields'/(rid+'.json')).read_bytes())['fields'][0]['doc'],
                             'Updated generator annotation')


class BuildSafetyTests(unittest.TestCase):
    def test_output_cannot_replace_sources_or_unrelated_files(self):
        import build_site
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'project'
            root.mkdir()
            (root/'data').mkdir()
            (root/'data/catalog.json').write_text('original')
            other = Path(tmp)/'other'
            other.mkdir()
            (other/'important.txt').write_text('keep')
            with patch.object(build_site, 'HERE', root):
                for target in (root, root/'data', root/'data/preview', root.parent, other):
                    with self.subTest(target=target), self.assertRaises(ValueError):
                        build_site.validate_output(target)
            self.assertEqual((root/'data/catalog.json').read_text(), 'original')
            self.assertEqual((other/'important.txt').read_text(), 'keep')

    def test_output_accepts_empty_and_recognized_legacy_sites(self):
        import build_site
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'project'
            root.mkdir()
            site = root/'preview'
            site.mkdir()
            with patch.object(build_site, 'HERE', root):
                build_site.validate_output(site)
                (site/'index.html').write_text('site')
                (site/'catalog-data.js').write_text('window.SEDO_CATALOG={"revisions":[]};')
                (site/'.nojekyll').touch()
                build_site.validate_output(site)

    def test_partial_or_duplicate_field_index_is_rejected(self):
        import build_site
        catalog = {'revisions': [{'revision_id':'a','status':'matched'}]}
        for rows in ([], [{'revision_id':'a','status':'ready'}]*2,
                     [{'revision_id':'a','status':'review'}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                build_site.validate_field_index(catalog, rows)
        self.assertEqual(build_site.validate_field_index(catalog,[{'revision_id':'a','status':'ready'}])['a']['status'],'ready')
        manual = {'revisions':[{'revision_id':'m','status':'document_only','source_type':'manual'}]}
        self.assertIn('m',build_site.validate_field_index(manual,[{'revision_id':'m','status':'document_only'}]))


    def test_cli_refuses_source_output_before_build_or_recovery(self):
        import build_site
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'project'
            (root/'data').mkdir(parents=True)
            original=root/'data/catalog.json'
            original.write_text('preserve')
            with patch.object(build_site,'HERE',root), patch.object(build_site,'main') as build:
                with self.assertRaises(ValueError):
                    build_site.cli(root/'data')
                build.assert_not_called()
            self.assertEqual(original.read_text(),'preserve')


    def test_duplicate_catalog_and_invalid_ready_status_are_rejected(self):
        import build_site
        ready=[{'revision_id':'a','status':'ready'}]
        for revisions in ([{'revision_id':'a','status':'matched'}]*2,
                          [{'revision_id':'a','status':'review'}],
                          [{'revision_id':'a','status':'document_only'}]):
            with self.subTest(revisions=revisions),self.assertRaises(ValueError):
                build_site.validate_field_index({'revisions':revisions},ready)

    def test_unknown_recovery_directory_is_preserved(self):
        import build_site
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            sidecar=root/'.preview-recovery'
            sidecar.mkdir()
            (sidecar/'important').write_text('keep')
            with self.assertRaises(ValueError):
                build_site.recover_site(root,'preview')
            self.assertEqual((sidecar/'important').read_text(),'keep')


    def test_interrupted_journal_write_can_be_recovered(self):
        import build_site
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            sidecar=root/'.preview-recovery'
            candidate=sidecar/'candidate'
            candidate.mkdir(parents=True)
            (candidate/'index.html').write_text('site')
            (candidate/'catalog-data.js').write_text('window.SEDO_CATALOG={"revisions":[]};')
            (candidate/'.nojekyll').touch()
            (sidecar/'journal.partial').write_text('{"existed":')
            build_site.recover_site(root,'preview')
            self.assertFalse(sidecar.exists())


    def test_committed_sidecar_with_foreign_content_is_preserved(self):
        import build_site
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            recovery=root/'.preview-recovery'
            recovery.mkdir()
            (recovery/'committed').touch()
            (recovery/'important').write_text('keep')
            with self.assertRaises(ValueError):
                build_site.recover_site(root,'preview')
            self.assertEqual((recovery/'important').read_text(),'keep')


    def test_cleanup_resumes_after_removing_a_release_marker(self):
        import build_site
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            recovery=root/'.preview-recovery'
            previous=recovery/'previous'
            previous.mkdir(parents=True)
            (previous/'.nojekyll').touch()
            (previous/'index.html').write_text('site')
            (previous/'catalog-data.js').write_text('window.SEDO_CATALOG={"revisions":[]};')
            (recovery/'committed').touch()
            original=Path.unlink
            def interrupt(path,*args,**kwargs):
                result=original(path,*args,**kwargs)
                if path.name=='.nojekyll':
                    raise SystemExit('interrupted cleanup')
                return result
            with patch.object(Path,'unlink',interrupt),self.assertRaises(SystemExit):
                build_site.recover_site(root,'preview')
            build_site.recover_site(root,'preview')
            self.assertFalse(recovery.exists())
