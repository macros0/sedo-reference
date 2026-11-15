import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_selected_home_pointer_and_sourcecraft_rollback_are_independent(self):
        import os
        from unittest.mock import patch
        a, ao = self.capture()
        b, bo = self.capture(True)
        with tempfile.TemporaryDirectory() as remote, tempfile.TemporaryDirectory() as local:
            remote, local = Path(remote), Path(local)
            store_feed(remote, [a], ao)
            store_feed(remote, [b], bo)
            store_feed(remote, [a], ao)
            import_feed(local, lambda name: (remote / name).read_bytes())
            pointer = local / "collector/test/current"
            pointer.write_text(b["content_id"] + "\n", encoding="ascii")
            with patch.dict(os.environ, SEDO_COLLECTOR_SOURCE="home"):
                self.assertEqual(load_library(local, "test"), b["library"])
                pointer.write_text(a["content_id"] + "\n", encoding="ascii")
                self.assertEqual(load_library(local, "test"), a["library"])
            with patch.dict(os.environ, SEDO_COLLECTOR_SOURCE="sourcecraft"):
                pointer.write_text(b["content_id"] + "\n", encoding="ascii")
                self.assertEqual(load_library(local, "test"), a["library"])

    def test_missing_feed_contour_does_not_load_a_home_manifest(self):
        from unittest.mock import patch
        import os
        a, objects = self.capture()
        with tempfile.TemporaryDirectory() as remote, tempfile.TemporaryDirectory() as local:
            remote, local = Path(remote), Path(local)
            store_feed(remote, [a], objects)
            import_feed(local, lambda name: (remote / name).read_bytes())
            empty = {"format": 1, "latest": {}, "manifests": []}
            (local / "collector/feed.json").write_text(json.dumps(empty))
            with patch.dict(os.environ, SEDO_COLLECTOR_SOURCE="sourcecraft"):
                self.assertIsNone(load_library(local, "test"))

    def test_sourcecraft_pull_cannot_modify_state_during_a_generation_update(self):
        from subprocess import CompletedProcess
        from unittest.mock import patch
        from auto_update import update_lock
        from sourcecraft_pull import pull
        ref = CompletedProcess([], 0, stdout="a"*40 + " refs/heads/snapshots\n")
        with tempfile.TemporaryDirectory() as temp, patch("sourcecraft_pull.subprocess.run", return_value=ref), \
                patch("sourcecraft_pull.import_feed", return_value={}) as imported, update_lock(Path(temp)):
            with self.assertRaises(OSError):
                pull(Path(temp))
            imported.assert_not_called()

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

    def test_invalid_index_rejected_and_truncated_object_repaired_from_verified_bytes(self):
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
            self.assertTrue(store_feed(root, [a], objects))
            self.assertEqual(target.read_bytes(), objects[a["library"]["sha256"]])

    def test_import_recovers_truncated_objects_and_manifests(self):
        manifest, objects = self.capture()
        with tempfile.TemporaryDirectory() as remote, tempfile.TemporaryDirectory() as local:
            remote, local = Path(remote), Path(local)
            store_feed(remote, [manifest], objects)
            import_feed(local, lambda name: (remote / name).read_bytes())
            target = local / "objects" / manifest["library"]["sha256"]
            target.write_bytes(target.read_bytes()[:12])
            saved = local / "collector/test" / (manifest["content_id"] + ".json")
            saved.write_bytes(saved.read_bytes()[:12])
            import_feed(local, lambda name: (remote / name).read_bytes())
            self.assertEqual(target.read_bytes(), objects[manifest["library"]["sha256"]])
            self.assertEqual(load_library(local, "test"), manifest["library"])

    def test_interrupted_object_or_manifest_write_leaves_final_files_valid_and_retryable(self):
        a, ao = self.capture()
        b, bo = self.capture(True)
        for interrupted in (bo[b["library"]["sha256"]], json.dumps(b, ensure_ascii=False, indent=2).encode("utf8")):
            with self.subTest(kind="object" if interrupted.startswith(b"PK") else "manifest"), \
                    tempfile.TemporaryDirectory() as remote, tempfile.TemporaryDirectory() as local:
                remote, local = Path(remote), Path(local)
                store_feed(remote, [a], ao)
                import_feed(local, lambda name: (remote / name).read_bytes())
                before = (local / "collector/feed.json").read_bytes()
                store_feed(remote, [b], bo)
                object_write = interrupted.startswith(b"PK")
                target = local / ("objects/" + b["library"]["sha256"] if object_write else
                                  "collector/test/" + b["content_id"] + ".json")
                original = Path.write_bytes
                def fail(path, raw):
                    if (object_write and raw == bo[b["library"]["sha256"]]) or (not object_write and raw == (remote / "collector/test" / (b["content_id"] + ".json")).read_bytes()):
                        original(path, raw[:12])
                        raise OSError("interrupted write")
                    return original(path, raw)
                with patch.object(Path, "write_bytes", fail), self.assertRaisesRegex(OSError, "interrupted"):
                    import_feed(local, lambda name: (remote / name).read_bytes())
                self.assertEqual((local / "collector/feed.json").read_bytes(), before)
                self.assertFalse(target.exists(), "partial bytes must never become a published final file")
                import_feed(local, lambda name: (remote / name).read_bytes())
                self.assertEqual(load_library(local, "test"), b["library"])
