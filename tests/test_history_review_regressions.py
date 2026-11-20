import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from sync import SITES, encode, sha, write
from verify import verify


class OfflineIntegrityTests(unittest.TestCase):
    def test_both_workflows_check_supplementary_candidate_schemas_before_promotion(self):
        root=Path(__file__).resolve().parents[1]
        for name,builder in [('pages.yml','python build_site.py'),('check-sfr.yml','python auto_update.py')]:
            workflow=(root/'.github/workflows'/name).read_text('utf8')
            command='node tests/test_attribute_ref_constraints.cjs --all-editions'
            with self.subTest(workflow=name):
                self.assertIn(command,workflow)
                self.assertLess(workflow.index(builder),workflow.index(command))
                self.assertLess(workflow.index(command),workflow.index('git push'))

    def test_replay_uses_captured_libraries_without_published_wsdl_links(self):
        import os
        from tests.test_collector_feed import FeedTests
        from sync import sync
        manifest, objects = FeedTests().capture()
        for mode in ("sourcecraft", "home"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                state, data, _ = self.fixture(Path(temp))
                snapshot = json.loads((state / "snapshots" / (json.loads((state / "current.json").read_bytes())["snapshot"] + ".json")).read_bytes())
                snapshot["entries"].append(manifest["library"])
                payload = encode(snapshot)
                write(state / "snapshots" / (sha(payload) + ".json"), payload)
                write(state / "current.json", encode(dict(snapshot=sha(payload))))
                for digest, raw in objects.items():
                    write(state / "objects" / digest, raw)
                with patch.dict(os.environ, SEDO_COLLECTOR_SOURCE=mode):
                    verify(state, data)

    def test_daily_workflow_checks_browser_and_snapshot_before_push(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/check-sfr.yml").read_text("utf8")
        for command in ("python verify.py", "npm run test:browser"):
            self.assertIn(command, workflow)
            self.assertLess(workflow.index(command), workflow.index("git push"))
        candidate_tests=workflow.index('name: Test generated candidate invariants')
        self.assertLess(workflow.index('python auto_update.py'),candidate_tests)
        self.assertLess(candidate_tests,workflow.index('git push'))


    def fixture(self, root):
        state, data = root / 'state', root / 'data'
        page = b'<a href="/specs_sedo/archive.zip">Schemas</a>'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w'):
            pass
        archive = buffer.getvalue()
        write(state / 'objects' / sha(page), page)
        write(state / 'objects' / sha(archive), archive)
        snapshot = dict(format=1, pages={env: sha(page) for env in SITES}, entries=[
            dict(environment=env, url=site.rsplit('/', 1)[0] + '/specs_sedo/archive.zip',
                 label='Schemas', kind='zip', sha256=sha(archive), bytes=len(archive), schemas=[])
            for env, site in SITES.items()])
        payload = encode(snapshot)
        digest = sha(payload)
        write(state / 'snapshots' / (digest + '.json'), payload)
        write(state / 'current.json', encode(dict(snapshot=digest)))
        write(state / 'history.json', encode([digest]))
        write(data / 'catalog.json', encode(dict(revisions=[
            dict(environment=env, number='10110', revision_id=env, root='example',
                 status='document_only', source_type='manual') for env in SITES])))
        return state, data, sha(page)

    def test_missing_required_page_object_is_an_error_before_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            state, data, digest = self.fixture(Path(temp))
            (state / 'objects' / digest).unlink()
            with self.assertRaisesRegex(ValueError, digest), patch('verify.sync') as replay:
                verify(state, data)
            replay.assert_not_called()

    def test_replay_must_match_expected_current_snapshot_not_only_itself(self):
        with tempfile.TemporaryDirectory() as temp:
            state, data, _ = self.fixture(Path(temp))
            def changed(base, **kwargs):
                from sync import get
                get(next(iter(SITES.values())))
                write(base / 'current.json', encode(dict(snapshot='0' * 64)))
                write(base / 'last-run.json', encode(dict(diff={})))
            with patch('verify.sync', side_effect=changed), self.assertRaisesRegex(AssertionError, 'snapshot|сним'):
                verify(state, data)


class ReportGroupingTests(unittest.TestCase):
    def test_source_report_matches_journal_at_equal_and_unknown_times(self):
        import auto_update
        import build_site
        from edition_groups import group_editions, source_updates
        from tests.test_source_updates import SourceUpdatesTests

        for stamp in ('2026-11-12T12:00:00+00:00', None):
            with self.subTest(stamp=stamp), tempfile.TemporaryDirectory() as temp:
                current, fields = SourceUpdatesTests().fixture()
                for event in current['updates']:
                    event['observed_at'] = stamp
                previous = dict(current, revisions=current['revisions'][:1], updates=current['updates'][:1])
                directory = Path(temp)
                write(directory / 'index.json', encode([dict(revision_id=rid, status='ready') for rid in fields]))
                for rid, tree in fields.items():
                    write(directory / (rid + '.json'), encode(dict(fields=tree)))
                grouped = group_editions(current, fields.__getitem__)
                grouped['updates'] = journal = build_site.summarize_journal(grouped, directory)
                expected, = source_updates(grouped)
                schemas, sources = auto_update.edition_changes(previous, current, directory, journal=journal)
                self.assertEqual(schemas, [])
                self.assertEqual(sources, [expected])

    def test_new_docx_for_existing_schema_reports_source_without_new_xml_edition(self):
        import auto_update
        from tests.test_source_updates import SourceUpdatesTests
        current, fields = SourceUpdatesTests().fixture()
        current['updates'][0]['previous_revision_id'] = None
        current['updates'][1]['previous_revision_id'] = 'first'
        previous = copy.deepcopy(current)
        previous['revisions'] = previous['revisions'][:1]
        previous['updates'] = previous['updates'][:1]
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            write(directory / 'index.json', encode([dict(revision_id=rid, status='ready') for rid in fields]))
            for rid, tree in fields.items():
                write(directory / (rid + '.json'), encode(dict(fields=tree)))
            schemas, sources = auto_update.edition_changes(previous, current, directory, journal=current['updates'])
        self.assertEqual(schemas, [])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['source_revision_id'], 'second')


class ManualTitleTests(unittest.TestCase):
    def test_default_title_prefers_current_same_contour(self):
        from manual_import import prepare_import
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ('state', 'data/fields', 'web'):
                (root / folder).mkdir(parents=True)
            (root / 'viewer-template.html').write_text('fixture')
            rows = [dict(revision_id='prod', number='32210', environment='prod', title='Production title', listed_now=True),
                    dict(revision_id='old', number='32210', environment='test', title='Old test title', listed_now=False),
                    dict(revision_id='new', number='32210', environment='test', title='Current test title', listed_now=True)]
            for row in rows:
                row['document_sha256'] = row['revision_id']
            write(root / 'data/catalog.json', encode(dict(revisions=rows)))
            for key, row in enumerate(rows):
                write(root / 'state/snapshots' / (str(key) + '.json'), encode(dict(source_type='manual', revision=row)))
            write(root / 'state/history.json', encode(['0', '1', '2']))
            write(root / 'state/current.json', encode(dict(snapshot='2')))
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as document:
                document.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
            with patch('manual_import.build'), patch('manual_import.build_history_baseline'), patch('manual_import.build_site.main'), patch('manual_import.validate_history'):
                preview = prepare_import(root, buffer.getvalue(), 'new.docx', dict(number='32210', environment='test'))
            try:
                self.assertEqual(preview.revision['title'], 'Current test title')
            finally:
                preview.close()
