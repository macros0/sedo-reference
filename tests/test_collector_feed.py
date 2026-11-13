import json
import tempfile
import unittest
from pathlib import Path

from tests import test_wsdl_sources as fixtures
from collector_snapshots import make_manifest, load_library
from collector_feed import store_feed, import_feed


class FeedTests(unittest.TestCase):
    def capture(self, changed=False):
        fixture = fixtures.WsdlSourcesTests()
        resources = fixture.bundle()
        if changed:
            resources = {k: v.replace(b'value="10"', b'value="11"') for k, v in resources.items()}
        row, objects = fixture.collect(resources)
        return make_manifest(row, "2026-11-13T12:00:00+00:00"), objects

    def test_noop_history_and_return_to_older_content(self):
        a, ao = self.capture()
        b, bo = self.capture(True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(store_feed(root, [a], ao))
            original = (root / "index.json").read_bytes()
            later = dict(a, observed_at="2026-11-14T12:00:00+00:00")
            self.assertFalse(store_feed(root, [later], ao))
            self.assertEqual((root / "index.json").read_bytes(), original)
            first = (root / "collector/test" / (a["content_id"] + ".json")).read_bytes()
            store_feed(root, [b], bo)
            store_feed(root, [later], ao)
            index = json.loads((root / "index.json").read_bytes())
            self.assertEqual(len(index["manifests"]), 2)
            self.assertEqual(index["latest"]["test"], a["content_id"])
            self.assertEqual((root / "collector/test" / (a["content_id"] + ".json")).read_bytes(), first)

    def test_import_downloads_missing_only_and_keeps_history_and_current_pointer(self):
        a, ao = self.capture()
        b, bo = self.capture(True)
        b["observed_at"] = "2026-11-14T12:00:00+00:00"
        with tempfile.TemporaryDirectory() as remote, tempfile.TemporaryDirectory() as local:
            remote, local = Path(remote), Path(local)
            calls = []
            def read(path):
                calls.append(path)
                return (remote / path).read_bytes()
            store_feed(remote, [a], ao)
            self.assertEqual(import_feed(local, read)["new_manifests"], 1)
            calls.clear()
            self.assertEqual(import_feed(local, read)["new_manifests"], 0)
            self.assertEqual(calls, ["index.json"])
            store_feed(remote, [b], bo)
            import_feed(local, read)
            self.assertEqual(load_library(local, "test"), b["library"])
            store_feed(remote, [a], ao)
            import_feed(local, read)
            self.assertEqual(load_library(local, "test"), a["library"])
            self.assertEqual(len(list((local / "collector/test").glob("*.json"))), 2)

    def test_corrupt_remote_batch_writes_nothing(self):
        a, objects = self.capture()
        with tempfile.TemporaryDirectory() as remote, tempfile.TemporaryDirectory() as local:
            remote, local = Path(remote), Path(local)
            store_feed(remote, [a], objects)
            digest = a["library"]["source_files"][0]["sha256"]
            (remote / "objects" / digest).write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                import_feed(local, lambda p: (remote / p).read_bytes())
            self.assertEqual(list(local.iterdir()), [])

    def test_invalid_index_and_immutable_collision_rejected(self):
        a, objects = self.capture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for bad in [dict(format=1, manifests=[dict(environment="../../evil", content_id="a"*64)], latest={}),
                        dict(format=1, manifests=[], latest={"prod":"0"*64})]:
                with self.assertRaises(ValueError):
                    import_feed(root, lambda path: json.dumps(bad).encode())
            store_feed(root, [a], objects)
            target = root / "objects" / a["library"]["sha256"]
            target.write_bytes(b"broken existing object")
            with self.assertRaises(ValueError):
                store_feed(root, [a], objects)
