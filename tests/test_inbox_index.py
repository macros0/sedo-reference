import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from import_inbox import collect, file_digest, read_checked


class InboxIndexTests(unittest.TestCase):
    def test_new_metadata_during_import_cannot_publish_an_incomplete_delivery(self):
        from import_inbox import stage_import, process_inbox
        from sync import encode, write
        from tests.test_fix15_sources import FilenameImportReviewTests
        from tests.test_source_collection_regressions import document
        fixture = FilenameImportReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        folder, filename = root / 'incoming/test', '999_v1.0.docx'
        write(folder / filename, document('manual'))
        previous = (root / 'data/catalog.json').read_bytes()
        def add_metadata(*args, **kwargs):
            write(folder / 'metadata.json', encode({filename: {'version': '2.0'}}))
            return stage_import(*args, **kwargs)
        with patch('import_inbox.stage_import', side_effect=add_metadata):
            with self.assertRaisesRegex(ValueError, 'incoming'):
                process_inbox(root)
        self.assertEqual((root / 'data/catalog.json').read_bytes(), previous)
        self.assertFalse((root / 'state/inbox.json').exists())
        self.assertEqual(process_inbox(root)['new_revisions'], 1)
        sources = json.loads((root / 'data/catalog.json').read_bytes())['update_sources']
        self.assertEqual([s['document_version'] for s in sources.values()], ['2.0'])

    def test_check_cli_handles_nonempty_delivery_without_python311_file_digest(self):
        import shutil
        import subprocess
        import sys
        from manual_import import fingerprint
        from sync import encode, sha, write
        from tests.test_source_collection_regressions import document
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(project / 'web', root / 'web')
            shutil.copy2(project / 'viewer-template.html', root)
            empty = dict(format=1, pages={}, entries=[])
            digest = sha(encode(empty))
            write(root / 'state/snapshots' / (digest + '.json'), encode(empty))
            write(root / 'state/history.json', encode([digest]))
            write(root / 'state/current.json', encode(dict(snapshot=digest)))
            write(root / 'data/catalog.json', encode(dict(revisions=[], registry=[], updates=[])))
            write(root / 'data/fields/index.json', encode([]))
            write(root / 'incoming/test/9997_v1.0.docx', document('manual', number='9997'))
            before = fingerprint(root)
            code = """import hashlib, runpy, sys
sys.path.insert(0, sys.argv[1])
if hasattr(hashlib, 'file_digest'):
    del hashlib.file_digest
script, root = sys.argv[1] + '/import_inbox.py', sys.argv[2]
sys.argv = [script, '--root', root, '--check', '--report', root + '/report.json']
runpy.run_path(script, run_name='__main__')
"""
            result = subprocess.run([sys.executable, '-B', '-c', code, str(project), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads((root / 'report.json').read_bytes())
            self.assertEqual((report['status'], report['check_only'], report['processed']), ('ok', True, 1))
            self.assertEqual(fingerprint(root), before)

    def test_digest_streams_large_payload_without_python311_file_digest(self):
        from sync import sha
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'payload'
            raw = b'content' * (1024 * 1024)
            path.write_bytes(raw)
            with patch('import_inbox.hashlib.file_digest', None, create=True), patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-file read')):
                self.assertEqual(file_digest(path), sha(raw))

    def test_documents_in_one_delivery_share_archive_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            incoming=root/'incoming/prod'
            incoming.mkdir(parents=True)
            for number in range(4):
                (incoming/f'{number}_v1.0.docx').write_bytes(b'document')
            (incoming/'schemas.zip').write_bytes(b'archive')
            def inspect(content, kind):
                if kind=='docx':
                    return {'structure': [['Корневой элемент', 'r'], ['Пространство имен', 'urn:main']]}
                return {'schemas': [{'path': 'main.xsd', 'namespace': 'urn:main', 'sha256': 'a'*64,
                                     'dependencies': [], 'roots': [{'name': 'r'}]}]}
            with patch('import_inbox.inspect_upload', side_effect=inspect) as parser:
                tasks, _ = collect(root)
            self.assertEqual(len(tasks), 4)
            self.assertEqual(sum(call.args[1]=='zip' for call in parser.call_args_list), 1)

    def test_payload_bytes_are_checked_against_the_index(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'spec.docx'
            path.write_bytes(b'original')
            digest=file_digest(path)
            with patch.object(Path, 'read_bytes', return_value=b'changed between reads'):
                with self.assertRaisesRegex(ValueError, 'incoming'):
                    read_checked(path, digest)

    def test_unknown_metadata_filename_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            incoming = root / 'incoming/prod'
            incoming.mkdir(parents=True)
            (incoming / '9997_v1.0.docx').write_bytes(b'document')
            (incoming / 'metadata.json').write_text(json.dumps({'9997_v1.0.DOCX': {'version': '1.1'}}))
            with self.assertRaisesRegex(ValueError, '9997_v1.0.DOCX'):
                collect(root)

    def test_index_keeps_paths_instead_of_all_payloads_and_skips_processed_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            incoming = root / 'incoming/prod'
            incoming.mkdir(parents=True)
            doc = incoming / '9997_v1.0.docx'
            doc.write_bytes(b'document')
            with patch('import_inbox.inspect_upload', return_value={'structure': []}):
                tasks, _ = collect(root)
            self.assertNotIn('document', tasks[0])
            self.assertEqual(tasks[0]['path'], doc)
            ledger = {'files': {'incoming/prod/9997_v1.0.docx': {
                'input_signature': tasks[0]['input_signature'], 'explicit_fields': []}}}
            with patch('import_inbox.inspect_upload', side_effect=AssertionError('unchanged input parsed')):
                pending, _ = collect(root, ledger=ledger)
            self.assertEqual(pending, [])
            doc.write_bytes(b'changed')
            with patch('import_inbox.inspect_upload', return_value={'structure': []}):
                pending, _ = collect(root, ledger=ledger)
            self.assertEqual(len(pending), 1)
