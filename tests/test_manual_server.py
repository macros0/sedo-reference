import json
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from manual_server import LocalImportServer


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = LocalImportServer(("127.0.0.1", 0), Path(self.temp.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def test_local_form_and_protected_mutations(self):
        with urlopen(self.base + "/", timeout=5) as response:
            html = response.read().decode("utf8")
            self.assertIn("Предпросмотр", html)
            self.assertIn(self.server.token, html)
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        for headers in ({}, {"X-Import-Token": self.server.token, "Origin": "https://example.org"}):
            request = Request(self.base + "/api/confirm", data=b"{}", headers={"Content-Type": "application/json", **headers})
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=5)
            self.assertEqual(error.exception.code, 403)

    def test_invalid_input_is_reported_without_writing_project(self):
        request = Request(self.base + "/api/preview", data=b"{}", headers={"Content-Type": "application/json", "Origin": self.base, "X-Import-Token": self.server.token})
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 400)
        self.assertIn("error", json.loads(error.exception.read()))
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_unapproved_preview_is_not_served(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/preview/../manual_server.py", timeout=5)
        self.assertEqual(error.exception.code, 404)

    def test_active_preview_rejects_parent_drive_and_unc_paths(self):
        directory=Path(self.temp.name)
        (directory/"site").mkdir()
        self.server.preview=SimpleNamespace(directory=directory,close=Mock())
        for path in ("/preview/../manual_server.py","/preview/C:%5CWindows%5Cwin.ini","/preview/%5C%5Cexample.org%5Cshare"):
            with self.assertRaises(HTTPError) as error:urlopen(self.base+path,timeout=5)
            self.assertEqual(error.exception.code,404)

    def test_dirty_or_unversioned_workspace_cannot_be_saved(self):
        preview=SimpleNamespace(changed=True,save=Mock(),close=Mock())
        self.server.preview=preview
        self.server.preview_id="preview"
        body=json.dumps(dict(id="preview",publish=False)).encode("utf8")
        request=Request(self.base+"/api/confirm",data=body,headers={"Content-Type":"application/json","Origin":self.base,"X-Import-Token":self.server.token})
        with self.assertRaises(HTTPError) as error:urlopen(request,timeout=5)
        self.assertEqual(error.exception.code,400)
        preview.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
