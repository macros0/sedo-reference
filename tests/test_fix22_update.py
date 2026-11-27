import json
import subprocess
import sys
import tempfile
import unittest
import update_catalog
from pathlib import Path
from unittest.mock import patch

from catalog import export
from fields import build
from sync import SITES, encode, sha, sync, write
from tests.test_source_collection_regressions import archive, individual_document
from update_catalog import main


class LocalGenerationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'one' / 'inputs'
        self.out = self.root / 'another' / 'output'
        self.name = '/specs_sedo/Тип_сообщения_999.docx'
        self.document = individual_document('response')
        self.resources = {}
        for env, page in SITES.items():
            self.resources[page] = b'<a href="/specs_sedo/a.zip">XSD</a>'
            self.resources[page.replace('sedo.html', 'specs_sedo/a.zip')] = self.schema('response')
        self.resources[SITES['prod']] += f'<a href="{self.name}">999</a>'.encode()
        self.resources[SITES['prod'].replace('/sedo.html', self.name)] = self.document

    def schema(self, root):
        return archive('main.xsd', ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
                     f'targetNamespace="urn:main"><xs:element name="{root}" type="xs:string"/></xs:schema>').encode())

    def run_update(self):
        args = ['update_catalog.py', '--state', str(self.state), '--out', str(self.out)]
        with patch('sys.argv', args), patch('sync.get', side_effect=self.resources.__getitem__):
            main()

    def disk(self):
        return {str(p.relative_to(self.root)): sha(p.read_bytes()) for p in self.root.rglob('*')
                if p.is_file() and not p.name.endswith('.lock')}

    def test_failed_candidate_keeps_every_confirmed_file_and_retry_succeeds(self):
        self.run_update()
        before = self.disk()
        source = SITES['prod'].replace('sedo.html', 'specs_sedo/a.zip')
        self.resources[source] = self.schema('wrong')
        with self.assertRaises(SystemExit):
            self.run_update()
        self.assertEqual(self.disk(), before)
        self.resources[source] = self.schema('response')
        self.run_update()
        self.assertEqual(len(json.loads((self.out/'catalog.json').read_bytes())['revisions']), 1)

    def test_online_update_imports_replaced_volume_without_losing_the_old_edition(self):
        host = SITES['prod'].removesuffix('/sedo.html')
        names = ['/specs_sedo/Приложение_10_ВНиМ_Том_16_Тип_сообщения_10110_v3.0.4.docx',
                 '/specs_sedo/Приложение_10_ВНиМ_Том_16_Тип_сообщения_10110_v3.1.0.docx']
        self.resources[SITES['prod']] = (f'<a href="/specs_sedo/a.zip">XSD</a>'
                                       f'<a href="{names[0]}">v3.0.4</a>'
                                       '<a href="/retired.html">Retired</a>').encode()
        self.resources[host + names[0]] = self.document
        self.run_update()

        self.resources[SITES['prod']] = (f'<a href="/specs_sedo/a.zip">XSD</a>'
                                       f'<a href="{names[1]}">v3.1.0</a>').encode()
        self.resources[host + names[1]] = individual_document('response', 'main.xsd')
        self.run_update()
        rows = json.loads((self.out / 'catalog.json').read_bytes())['revisions']
        self.assertEqual({(row['document_url'], row['listed_now']) for row in rows},
                         {(host + names[0], False), (host + names[1], True)})

    def test_recover_legacy_review_without_losing_snapshots_docx_or_ready_edition(self):
        self.run_update()
        previous = json.loads((self.out/'catalog.json').read_bytes())
        source = SITES['prod'].replace('sedo.html', 'specs_sedo/a.zip')
        good = self.resources[source]
        self.resources[source] = self.schema('wrong')
        with patch('sync.get', side_effect=self.resources.__getitem__):
            sync(self.state)
        export(self.state, self.out/'catalog.json')
        build(self.out/'catalog.json', self.state, self.out/'fields')
        old_snapshot = json.loads((self.state/'current.json').read_bytes())['snapshot']
        self.resources[source] = good
        self.run_update()
        current = json.loads((self.out/'catalog.json').read_bytes())
        self.assertEqual(current['updates'], previous['updates'])
        self.assertEqual(current['revisions'][0]['revision_id'], previous['revisions'][0]['revision_id'])
        self.assertEqual([r['status'] for r in json.loads((self.out/'fields/index.json').read_bytes())], ['ready'])
        self.assertEqual((self.state/'objects'/sha(self.document)).read_bytes(), self.document)
        self.assertEqual(sha((self.state/'snapshots'/(old_snapshot+'.json')).read_bytes()), old_snapshot)
        self.assertIn(old_snapshot, json.loads((self.state/'history.json').read_bytes()))

    def test_recover_historical_fields_review_after_matched_archive_cannot_build(self):
        self.run_update()
        source = SITES['prod'].replace('sedo.html', 'specs_sedo/a.zip')
        good = self.resources[source]
        self.resources[source] = archive('main.xsd', b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response"><xs:simpleType><xs:restriction base="xs:string"><xs:assertion test="true()"/></xs:restriction></xs:simpleType></xs:element></xs:schema>')
        with patch('sync.get', side_effect=self.resources.__getitem__):
            sync(self.state)
        export(self.state, self.out/'catalog.json')
        build(self.out/'catalog.json', self.state, self.out/'fields')
        old = json.loads((self.out/'fields/index.json').read_bytes())
        self.assertEqual([r['status'] for r in old], ['ready', 'review'])
        self.resources[source] = good
        self.run_update()
        export(self.state, self.out/'catalog.json')
        current = json.loads((self.out/'catalog.json').read_bytes())
        self.assertEqual([r['revision_id'] for r in current['revisions']], [old[0]['revision_id']])

    def test_cleanup_interruption_cannot_revert_installed_generation(self):
        self.run_update()
        source = SITES['prod'].replace('sedo.html', 'specs_sedo/a.zip')
        self.resources[source] = archive('main.xsd', b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:int"/></xs:schema>')
        unlink = Path.unlink
        def partial_cleanup(path, *args, **kwargs):
            if path.name == 'committed' and path.parent.name == '.catalog-recovery':
                unlink(path, missing_ok=True)
                unlink(path.parent/'preparing', missing_ok=True)
                raise OSError('cleanup interrupted')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', partial_cleanup), self.assertRaisesRegex(OSError,'cleanup interrupted'):
            self.run_update()
        expected = (self.out/'catalog.json').read_bytes()
        self.assertEqual(len(json.loads(expected)['revisions']), 2)
        update_catalog.recover_catalog(self.state, self.out)
        self.assertEqual((self.out/'catalog.json').read_bytes(), expected)

    def test_repair_only_the_predecessor_of_a_ready_edition_after_a_legacy_review(self):
        self.run_update()
        source = SITES['prod'].replace('sedo.html', 'specs_sedo/a.zip')
        unsupported = archive('main.xsd', b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response"><xs:simpleType><xs:restriction base="xs:string"><xs:assertion test="true()"/></xs:restriction></xs:simpleType></xs:element></xs:schema>')
        for content in (unsupported, archive('main.xsd', b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:int"/></xs:schema>')):
            self.resources[source] = content
            with patch('sync.get', side_effect=self.resources.__getitem__):
                sync(self.state)
        export(self.state, self.out/'catalog.json')
        build(self.out/'catalog.json', self.state, self.out/'fields')
        old = json.loads((self.out/'catalog.json').read_bytes())['updates']
        self.assertEqual(len(old), 3)
        self.assertEqual(old[2]['previous_revision_id'], old[1]['revision_id'])
        self.run_update()
        events = json.loads((self.out/'catalog.json').read_bytes())['updates']
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0], old[0])
        self.assertEqual(events[1], dict(old[2], previous_revision_id=old[0]['revision_id']))
        self.assertEqual(events[1]['previous_revision_id'], events[0]['revision_id'])

    def test_custom_state_recovers_direct_collection_before_copying_confirmed_bytes(self):
        self.run_update()
        pointer = (self.state/'current.json').read_bytes()
        write(self.state/'.sync-recovery.json', encode({'current.json':pointer.hex()}))
        write(self.state/'current.json', encode({'snapshot':'missing'}))
        args = ['update_catalog.py', '--state', str(self.state), '--out', str(self.out), '--offline']
        with patch('sys.argv', args):
            main()
        self.assertEqual((self.state/'current.json').read_bytes(), pointer)
        self.assertFalse((self.state/'.sync-recovery.json').exists())

    def test_home_capture_respects_offline_install_lock_with_separate_output(self):
        from collector import store_local_inputs
        from tests.test_collector import CollectorTests

        a, ao = CollectorTests().capture()
        b, _ = CollectorTests().capture(11, "2026-11-16T04:30:00+00:00")
        updater = Path(update_catalog.__file__).resolve().parent
        code = """import sys
import collector
from tests.test_collector import CollectorTests
collector.capture = lambda env: CollectorTests().capture(11, "2026-11-16T04:30:00+00:00")
sys.argv = ["collector.py", "--capture-to", sys.argv[1], "--environment", "test"]
try:
    collector.main()
except OSError as exc:
    sys.exit(str(exc))
"""
        for name in ("state", "custom-inputs"):
            with self.subTest(name=name):
                project = self.root / name
                self.state = project / name
                self.out = self.root / (name + "-output")
                self.run_update()
                (project / "viewer-template.html").touch()
                store_local_inputs(self.state, [a], ao)
                pointer = self.state / "collector/test/current.json"
                args = ["update_catalog.py", "--offline", "--state", str(self.state), "--out", str(self.out)]

                def run_capture():
                    return subprocess.run([sys.executable, "-B", "-c", code, str(self.state)],
                                          cwd=updater, capture_output=True, text=True, timeout=20)

                attempts = []

                def during_copy(source, target, names):
                    copy_inputs(source, target, names)
                    if source == self.state:
                        result = run_capture()
                        attempts.append((result, json.loads(pointer.read_bytes())["content_id"]))

                copy_inputs = update_catalog.copy_inputs
                with patch("sys.argv", args), patch("update_catalog.copy_inputs", side_effect=during_copy):
                    main()
                (attempt, during), = attempts
                after = json.loads(pointer.read_bytes())["content_id"]
                self.assertNotEqual(attempt.returncode, 0, f"current was {during} during install and {after} after")
                self.assertRegex(attempt.stderr, r"\.update\.lock|Permission denied|Resource temporarily unavailable")
                self.assertEqual(during, a["content_id"])
                self.assertEqual(after, a["content_id"])
                result = run_capture()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(pointer.read_bytes())["content_id"], b["content_id"])

    def test_home_capture_waits_for_interrupted_catalog_install_to_recover(self):
        import collector
        from tests.test_collector import CollectorTests

        self.run_update()
        a, ao = CollectorTests().capture()
        b, bo = CollectorTests().capture(11, "2026-11-16T04:30:00+00:00")
        c, co = CollectorTests().capture(12, "2026-11-17T04:30:00+00:00")
        collector.store_local_inputs(self.state, [a], ao)
        pointer = self.state / "collector/test/current.json"

        def interrupted(path, raw):
            write(path, raw)
            if path == pointer:
                raise OSError("interrupted after replacing Home pointer")

        stage = self.root / "interrupted"
        collector.store_local_inputs(stage / "state", [b], bo)
        offline = ["update_catalog.py", "--offline", "--state", str(self.state), "--out", str(self.out)]
        capture = ["collector.py", "--capture-to", str(self.state), "--environment", "test"]
        with patch("update_catalog.write", side_effect=interrupted), patch("update_catalog.recover_catalog"), \
                self.assertRaisesRegex(OSError, "interrupted"):
            update_catalog.install(self.state, self.out, stage)
        self.assertEqual(json.loads(pointer.read_bytes())["content_id"], b["content_id"])
        with patch("collector.capture", return_value=(c, co)):
            with patch("sys.argv", capture), self.assertRaisesRegex(ValueError, "recovery is pending"):
                collector.main()
            self.assertEqual(json.loads(pointer.read_bytes())["content_id"], b["content_id"])
            with patch("sys.argv", offline):
                main()
            self.assertEqual(json.loads(pointer.read_bytes())["content_id"], a["content_id"])
            with patch("sys.argv", capture):
                collector.main()
        self.assertEqual(json.loads(pointer.read_bytes())["content_id"], c["content_id"])

    def test_promotion_error_rolls_back_state_data_and_preserves_unrelated_files(self):
        self.run_update()
        (self.state/'notes.txt').write_text('saved')
        (self.out/'other.json').write_text('saved too')
        before = self.disk()
        source = SITES['prod'].replace('sedo.html','specs_sedo/a.zip')
        self.resources[source] = archive('main.xsd', b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:int"/></xs:schema>')
        fail_once = [True]
        def interrupted(path, content):
            if path == self.out/'catalog.json' and fail_once:
                fail_once.pop()
                raise OSError('incomplete install')
            write(path, content)
        with patch('update_catalog.write', side_effect=interrupted), self.assertRaisesRegex(OSError, 'incomplete install'):
            self.run_update()
        self.assertEqual(self.disk(), before)
