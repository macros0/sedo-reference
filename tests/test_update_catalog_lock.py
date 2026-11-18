import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from auto_update import update_lock


class CatalogLockTests(unittest.TestCase):
    def test_interrupted_index_install_is_rolled_back_by_original_cli_pair(self):
        import update_catalog
        from collector_feed import store_feed
        from sync import write
        from tests.test_collector_feed import FeedTests
        from tests.test_fix22_update import LocalGenerationTests
        f = LocalGenerationTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        a, ao = FeedTests().capture()
        b, bo = FeedTests().capture(True)
        f.run_update()
        store_feed(f.state, [a], ao)
        before = f.disk()
        def interrupted(path, raw):
            write(path, raw)
            if path == f.state / "index.json":
                raise OSError("interrupted after replacing producer index")
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            store_feed(stage / "state", [a], ao)
            store_feed(stage / "state", [b], bo)
            with patch("update_catalog.write", side_effect=interrupted), \
                    patch("update_catalog.recover_catalog"), \
                    self.assertRaisesRegex(OSError, "interrupted after replacing producer index"):
                update_catalog.install(f.state, f.out, stage)
            self.assertEqual((f.state / "index.json").read_bytes(), (stage / "state/index.json").read_bytes())
        args = ["update_catalog.py", "--state", str(f.state), "--out", str(f.out), "--offline"]
        with patch("sys.argv", args):
            update_catalog.main()
        self.assertEqual(f.disk(), before)

    def populated_catalog(self):
        from tests.test_fix22_update import LocalGenerationTests
        from sync import SITES
        fixture = LocalGenerationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        page = SITES['prod']
        base = b'<a href="/specs_sedo/a.zip">XSD</a>'
        links = []
        for number in ('997', '998', '999'):
            path = '/specs_sedo/Тип_сообщения_' + number + '.docx'
            fixture.resources[page.replace('/sedo.html', path)] = fixture.document
            links.append(f'<a href="{path}">{number}</a>'.encode())
        fixture.resources[page] = base + b''.join(links)
        fixture.run_update()
        return fixture, page, base, links

    def test_online_mass_disappearance_preserves_every_confirmed_byte(self):
        fixture, page, base, links = self.populated_catalog()
        before = fixture.disk()
        fixture.resources[page] = base
        with self.assertRaisesRegex(ValueError, 'исчезновение'):
            fixture.run_update()
        self.assertEqual(fixture.disk(), before)

    def test_online_single_retirement_and_initial_collection_are_allowed(self):
        import json
        fixture, page, base, links = self.populated_catalog()
        fixture.resources[page] = base + b''.join(links[:2])
        fixture.run_update()
        current = json.loads((fixture.out / 'catalog.json').read_bytes())
        self.assertEqual(len(current['revisions']), 3)
        self.assertEqual(sum(row['listed_now'] for row in current['revisions']), 2)

    def test_unrelated_data_ancestor_does_not_own_independent_cli_paths(self):
        from auto_update import catalog_locks
        with tempfile.TemporaryDirectory() as folder:
            unrelated = Path(folder)
            project = unrelated / 'data/projects/reference'
            project.mkdir(parents=True)
            with update_lock(unrelated), catalog_locks(project / 'independent-input', project / 'independent-output'):
                self.assertTrue((project / 'independent-output/.update.lock').is_file())

    def test_individual_clis_lock_the_output_project_and_allow_independent_output(self):
        from sync import encode, write
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / 'separate-state'
            index = root / 'inputs/catalog.json'
            write(state / 'current.json', encode({'snapshot': 'A'}))
            write(state / 'snapshots/A.json', encode({'entries': []}))
            write(index, encode({'revisions': []}))
            for script, args, target in (
                    ('catalog.py', [], Path('catalog.json')),
                    ('fields.py', ['--index', str(index)], Path('fields'))):
                with self.subTest(script=script), update_lock(root):
                    def run(output):
                        return subprocess.run([sys.executable, '-B', str(project / script), '--state', str(state),
                                               '--output', str(output), *args], capture_output=True, text=True)
                    blocked = run(root / 'data' / target)
                    self.assertNotEqual(blocked.returncode, 0)
                    self.assertRegex(blocked.stderr, 'PermissionError|BlockingIOError')
                    self.assertFalse((root / 'data' / target).exists())
                    allowed = run(root / 'separate-output' / target)
                    self.assertEqual(allowed.returncode, 0, allowed.stderr)
                    self.assertTrue((root / 'separate-output' / target).exists())

    def test_site_cli_respects_project_lock_before_reading_catalog(self):
        project = Path(__file__).resolve().parents[1]
        code = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import build_site
build_site.HERE = Path(sys.argv[2])
build_site.cli()
"""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with update_lock(root):
                result = subprocess.run([sys.executable, "-c", code, str(project), str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertRegex(result.stderr, "PermissionError|BlockingIOError")
            self.assertNotIn("catalog.json", result.stderr)
            self.assertFalse((root/"site").exists())

    def run_local_sync_cli(self, state):
        project = Path(__file__).resolve().parents[1]
        code = """import io, runpy, sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from tests.test_source_collection_regressions import archive
from sync import SITES
resources = {}
raw = archive('main.xsd', b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>')
for page in SITES.values():
    resources[page] = b'<a href="/specs_sedo/test.zip">Schema</a>'
    resources[page.replace('sedo.html', 'specs_sedo/test.zip')] = raw
script, state = sys.argv[1] + '/sync.py', sys.argv[2]
sys.argv = [script, '--state', state]
def open_resource(req, **kwargs):
    response = io.BytesIO(resources[req.full_url])
    response.geturl = lambda: req.full_url
    return response
with patch('urllib.request.build_opener', return_value=SimpleNamespace(open=open_resource)):
    runpy.run_path(script, run_name='__main__')
"""
        return subprocess.run([sys.executable, "-B", "-c", code, str(project), str(state)], capture_output=True, text=True)

    def test_sync_cli_respects_project_lock_before_first_state_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with update_lock(root):
                result = self.run_local_sync_cli(root / 'state')
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertFalse((root / 'state').exists(), result.stderr)

    def test_sync_cli_collects_into_independent_state_while_project_is_locked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with update_lock(root):
                result = self.run_local_sync_cli(root / 'independent-state')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / 'independent-state/current.json').is_file())
            self.assertTrue((root / 'independent-state/history.json').is_file())

    def test_independent_sibling_directories_can_build_while_project_is_locked(self):
        import update_catalog
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            target=root/'independent-data'
            (target/'fields').mkdir(parents=True)
            (target/'fields/index.json').write_text('[]')
            arguments=['update_catalog.py', '--offline', '--state', str(root/'independent-state'), '--out', str(target)]
            with update_lock(root), patch('sys.argv', arguments), patch('update_catalog.export'), patch('update_catalog.build'):
                update_catalog.main()

    def test_cli_respects_shared_state_lock(self):
        script = Path(__file__).resolve().parents[1] / 'update_catalog.py'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'state').mkdir()
            (root / 'data').mkdir()
            with update_lock(root):
                result = subprocess.run([sys.executable, str(script), '--offline', '--state', str(root/'state'), '--out', str(root/'data')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('lock', result.stderr.lower())
            self.assertFalse((root/'data/catalog.json').exists())
