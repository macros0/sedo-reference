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
            def changed(base):
                from sync import get
                get(next(iter(SITES.values())))
                write(base / 'current.json', encode(dict(snapshot='0' * 64)))
                write(base / 'last-run.json', encode(dict(diff={})))
            with patch('verify.sync', side_effect=changed), self.assertRaisesRegex(AssertionError, 'snapshot|сним'):
                verify(state, data)


class ReportGroupingTests(unittest.TestCase):
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
            schemas, sources = auto_update.edition_changes(previous, current, directory)
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
            with patch('manual_import.build'), patch('manual_import.build_site.main'), patch('manual_import.validate_history'):
                preview = prepare_import(root, buffer.getvalue(), 'new.docx', dict(number='32210', environment='test'))
            try:
                self.assertEqual(preview.revision['title'], 'Current test title')
            finally:
                preview.close()
