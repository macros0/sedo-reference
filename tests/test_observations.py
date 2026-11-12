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
