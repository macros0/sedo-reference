import io
import tempfile
import unittest
import weakref
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import sync
import json


class SyncQueueTests(unittest.TestCase):
    def test_process_exit_releases_lock_and_export_restores_previous_generation(self):
        import subprocess
        import sys
        from catalog import export
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            sync.write(base/"current.json", sync.encode({"snapshot": "A"}))
            sync.write(base/"history.json", sync.encode(["A"]))
            sync.write(base/"snapshots/A.json", sync.encode({"entries": []}))
            code = """import os, sys
from pathlib import Path
from sync import confirmation_lock, write, encode, CONFIRMATION_FILES
base=Path(sys.argv[1])
with confirmation_lock(base):
    saved={name: (base/name).read_bytes().hex() if (base/name).exists() else None for name in CONFIRMATION_FILES}
    write(base/'.sync-recovery.json', encode(saved))
    write(base/'history.json', encode(['A', 'B']))
    write(base/'current.json', encode({'snapshot':'B'}))
    os._exit(17)
"""
            result = subprocess.run([sys.executable, "-c", code, str(base)], capture_output=True)
            self.assertEqual(result.returncode, 17)
            export(base, base/"catalog.json")
            self.assertEqual(json.loads((base/"current.json").read_bytes()), {"snapshot": "A"})
            self.assertEqual(json.loads((base/"history.json").read_bytes()), ["A"])
            self.assertEqual(json.loads((base/"catalog.json").read_bytes())["revisions"], [])

    def test_export_does_not_recover_a_running_collection(self):
        import subprocess
        import sys
        from catalog import export
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            code = """import sys, time
from pathlib import Path
from sync import confirmation_lock, write, encode, CONFIRMATION_FILES
base=Path(sys.argv[1])
with confirmation_lock(base):
    write(base/'.sync-recovery.json', encode(dict.fromkeys(CONFIRMATION_FILES)))
    print('locked', flush=True)
    sys.stdin.readline()
"""
            process = subprocess.Popen([sys.executable, "-c", code, str(base)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                with self.assertRaises((PermissionError, BlockingIOError)):
                    export(base, base/"catalog.json")
                self.assertTrue((base/".sync-recovery.json").exists())
            finally:
                process.communicate("\n", timeout=10)
            sync.recover_confirmation(base)
            self.assertFalse((base/".sync-recovery.json").exists())

    def test_restart_recovers_interrupted_confirmation_before_export(self):
        from catalog import export
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            saved = {"observations.json": b'{"snapshots":{}}', "history.json": b'["A"]',
                     "current.json": b'{"snapshot":"A"}', "collector/processed.json": None}
            sync.write(base/".sync-recovery.json", sync.encode({name: raw.hex() if raw else None for name, raw in saved.items()}))
            for name in saved:
                sync.write(base/name, b"interrupted")
            sync.write(base/"snapshots/A.json", sync.encode({"entries": []}))
            export(base, base/"catalog.json")
            self.assertEqual(json.loads((base/"catalog.json").read_bytes())["revisions"], [])
            for name, raw in saved.items():
                self.assertEqual((base/name).read_bytes() if (base/name).exists() else None, raw)
            self.assertFalse((base/".sync-recovery.json").exists())

    def test_failed_confirmation_rolls_back_all_metadata(self):
        metadata = ("observations.json", "history.json", "current.json", "collector/processed.json")
        for failure in metadata:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w"):
                    pass
                def get(url, **kwargs):
                    return b'<a href="/specs_sedo/a.zip">XSD</a>' if url in sync.SITES.values() else output.getvalue()
                with patch.object(sync, "get", side_effect=get), patch("collector_snapshots.load_library", return_value=None):
                    sync.sync(base)
                    before = {name: (base/name).read_bytes() if (base/name).exists() else None for name in metadata}
                    native = sync.write
                    fired = False
                    def write(path, raw):
                        nonlocal fired
                        if path == base/failure and not fired:
                            fired = True
                            raise OSError("confirmation interrupted")
                        return native(path, raw)
                    with patch.object(sync, "get", side_effect=lambda url, **kw: get(url) + (b" " if url in sync.SITES.values() else b"")), patch.object(sync, "write", side_effect=write), patch("collector_snapshots.replay_history", return_value=([], {"format": 1, "manifests": {}})):
                        with self.assertRaisesRegex(OSError, "interrupted"):
                            sync.sync(base)
                    self.assertTrue(fired)
                    self.assertEqual(before, {name: (base/name).read_bytes() if (base/name).exists() else None for name in metadata})

    def test_completed_downloads_do_not_accumulate_with_catalog_size(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w"):
            pass
        archive = output.getvalue()
        page = "".join('<a href="/specs_sedo/a'+str(n)+'.zip">XSD</a>' for n in range(24)).encode()
        futures = []
        native = ThreadPoolExecutor.submit
        peak = 0
        def submit(pool, *args, **kwargs):
            nonlocal peak
            future = native(pool, *args, **kwargs)
            futures.append(weakref.ref(future))
            peak = max(peak, sum(ref() is not None for ref in futures))
            return future
        def get(url, **kwargs):
            return page if url in sync.SITES.values() else archive
        with tempfile.TemporaryDirectory() as temp, patch.object(ThreadPoolExecutor, "submit", submit), patch.object(sync, "get", side_effect=get), patch("collector_snapshots.load_library", return_value=None):
            sync.sync(Path(temp))
        self.assertLessEqual(peak, 6, "retained futures must be bounded independently of the 48 downloads")
