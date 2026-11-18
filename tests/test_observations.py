import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sync import SITES, sync


class ObservationTests(unittest.TestCase):
    def test_export_after_process_exit_marks_uncommitted_attempt_failed(self):
        from catalog import export
        first, second = '2026-11-12T01:00:00+00:00', '2026-11-12T02:00:00+00:00'
        self.collect(first)
        unlink = Path.unlink
        def interrupted(path, *args, **kwargs):
            if path.name == '.sync-recovery.json':
                raise SystemExit(17)
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', interrupted), self.assertRaises(SystemExit):
            self.collect(second)
        self.assertTrue((self.state / '.sync-recovery.json').exists())
        export(self.state, self.state / 'export.json')
        self.assertEqual(json.loads((self.state / 'export.json').read_bytes())['checked_at'], first)
        attempt = json.loads((self.state / 'last-run.json').read_bytes())
        self.assertEqual(attempt['status'], 'failed')
        self.assertTrue(attempt['errors'])

    def test_late_failure_cannot_advance_checked_at_even_for_identical_snapshot(self):
        from catalog import export
        first = '2026-11-12T01:00:00+00:00'
        second = '2026-11-12T02:00:00+00:00'
        third = '2026-11-12T03:00:00+00:00'
        self.collect(first)
        # Exercise migration from a store with only the old successful report.
        (self.state / 'last-success.json').unlink(missing_ok=True)
        with patch('collector_snapshots.replay_history', side_effect=ValueError('late failure')):
            with self.assertRaisesRegex(ValueError, 'late failure'):
                self.collect(second)
        target = self.state / 'export.json'
        export(self.state, target)
        self.assertEqual(json.loads(target.read_bytes())['checked_at'], first)
        failed = json.loads((self.state / 'last-run.json').read_bytes())
        self.assertEqual(failed['observed_at'], second)
        self.assertIn('late failure', str(failed['errors']))
        self.collect(third)
        export(self.state, target)
        self.assertEqual(json.loads(target.read_bytes())['checked_at'], third)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("README.txt", "test")
        self.archive = buffer.getvalue()
        self.page = b'<a href="/specs_sedo/schema.zip">Schemas</a>'

    def collect(self, timestamp, failure=False):
        def get(url):
            if url in SITES.values():
                return self.page
            if failure:
                raise OSError("download failed")
            return self.archive
        with patch("sync.get", side_effect=get), patch("sync.datetime") as clock:
            clock.now.return_value = datetime.fromisoformat(timestamp)
            sync(self.state)

    def test_repeat_does_not_overwrite_first_successful_observation(self):
        first = "2026-11-12T12:00:00+00:00"
        self.collect(first)
        digest = json.loads((self.state / "current.json").read_bytes())["snapshot"]
        self.collect("2026-11-13T12:00:00+00:00")
        observations = json.loads((self.state / "observations.json").read_bytes())["snapshots"]
        self.assertEqual(observations, {digest: first})

    def test_failed_collection_keeps_successful_observations(self):
        self.collect("2026-11-12T12:00:00+00:00")
        before = (self.state / "observations.json").read_bytes()
        pointer = (self.state / "current.json").read_bytes()
        with self.assertRaises(ValueError):
            self.collect("2026-11-13T12:00:00+00:00", failure=True)
        self.assertEqual((self.state / "observations.json").read_bytes(), before)
        self.assertEqual((self.state / "current.json").read_bytes(), pointer)


if __name__ == "__main__":
    unittest.main()
