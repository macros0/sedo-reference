import io
import tempfile
import unittest
import weakref
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import sync


class SyncQueueTests(unittest.TestCase):
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
