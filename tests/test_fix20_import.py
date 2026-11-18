import unittest
from unittest.mock import patch

import build_site
from import_inbox import process_inbox
from sync import write
from tests.test_source_collection_regressions import document


class BatchBuildTests(unittest.TestCase):
    def setUp(self):
        from tests.test_fix15_sources import FilenameImportReviewTests
        self.fixture = FilenameImportReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def test_three_documents_build_once_and_repeat_skips_project_fingerprint(self):
        for number in ('991', '992', '993'):
            write(self.root / 'incoming/test' / (number + '_v1.0.docx'), document(number, number=number))
        with patch.object(build_site, 'main', wraps=build_site.main) as build:
            report = process_inbox(self.root)
        self.assertEqual((report['processed'], report['new_revisions']), (3, 3))
        self.assertEqual(build.call_count, 1)
        self.assertEqual({row['number'] for row in report['files']}, {'991', '992', '993'})
        self.assertTrue(all(row['comparison'] for row in report['files']))
        with patch('import_inbox.fingerprint', side_effect=AssertionError('unchanged site hashed')):
            self.assertEqual(process_inbox(self.root)['processed'], 0)

    def test_final_build_failure_rolls_back_all_documents(self):
        before = (self.root / 'data/catalog.json').read_bytes()
        for number in ('991', '992', '993'):
            write(self.root / 'incoming/test' / (number + '_v1.0.docx'), document(number, number=number))
        with patch.object(build_site, 'main', side_effect=ValueError('candidate failed')):
            with self.assertRaisesRegex(ValueError, 'candidate failed'):
                process_inbox(self.root)
        self.assertEqual((self.root / 'data/catalog.json').read_bytes(), before)
        self.assertFalse((self.root / 'state/inbox.json').exists())
