"""Windows browser acceptance; uses a temporary repository and never publishes."""
import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from manual_server import ImportHandler, LocalImportServer

PROJECT = Path(__file__).resolve().parents[1]
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


@unittest.skipUnless(EDGE.exists(), "Windows Edge is unavailable")
class ManualBrowserTests(unittest.TestCase):
    def test_upload_preview_confirm_duplicate_and_restart(self):
        # Edge/OneAuth may briefly retain profile files after headless exit on Windows.
        with tempfile.TemporaryDirectory(prefix="sedo-ui-test-",ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            root = base / "project"
            root.mkdir()
            for folder in ("state", "data", "web"):
                shutil.copytree(PROJECT / folder, root / folder)
            shutil.copy2(PROJECT / "viewer-template.html", root / "viewer-template.html")
            env = {**os.environ, "GIT_AUTHOR_NAME": "macros0", "GIT_COMMITTER_NAME": "macros0", "GIT_AUTHOR_EMAIL": "macros0@users.noreply.github.com", "GIT_COMMITTER_EMAIL": "macros0@users.noreply.github.com"}
            for args in (("init", "-b", "main"), ("add", "."), ("commit", "-m", "Fixture")):
                subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, check=True)
            catalog = json.loads((root / "data/catalog.json").read_bytes())
            revision = next(r for r in catalog["revisions"] if r["number"] == "10110" and r["environment"] == "prod")
            buffer = io.BytesIO()
            with zipfile.ZipFile(root / "state/objects" / revision["document_sha256"]) as old, zipfile.ZipFile(buffer, "w") as new:
                for item in old.infolist():
                    content = old.read(item.filename)
                    if item.filename == "word/document.xml":
                        content += b"\n<!-- browser upload fixture -->"
                    new.writestr(item, content)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            checks = r'''<script>window.addEventListener('load',async()=>{const assert=(v,m)=>{if(!v)throw Error(m)};try{
const bytes=Uint8Array.from(atob('DOCUMENT_BYTES'),c=>c.charCodeAt(0)),files=new DataTransfer();files.items.add(new File([bytes],'spec.docx'));document.getElementById('document').files=files.files;document.getElementById('number').value='10110';document.getElementById('version').value='9.9';document.getElementById('date').value='2026-11-13';await document.getElementById('form').onsubmit({preventDefault(){}});
assert(!document.getElementById('preview').hidden,'preview');assert(document.getElementById('details').textContent.includes('XSD не загружена'),'document only');assert(!document.getElementById('save').disabled,'save available');assert(document.getElementById('status').textContent.includes('ещё не изменён'),'no automatic save');await confirm(false);assert(document.getElementById('status').textContent.includes('сохранена локально'),'confirmed save');assert(!document.getElementById('retry').hidden,'publish saved available');const response=await fetch('/reference/catalog-data.js');assert((await response.text()).includes('document_only'),'saved reference');await document.getElementById('form').onsubmit({preventDefault(){}});assert(!document.getElementById('duplicate').hidden&&document.getElementById('save').disabled,'duplicate');assert(!document.getElementById('retry').hidden,'duplicate retains publish saved');
const result=document.createElement('div');result.id='browser-check-result';result.textContent='PASS';document.body.append(result)}catch(e){const result=document.createElement('div');result.id='browser-check-result';result.textContent='FAIL '+e.message;document.body.append(result)}});</script>'''.replace("DOCUMENT_BYTES", encoded)

            class BrowserHandler(ImportHandler):
                def do_GET(self):
                    if self.path == "/":
                        html = (PROJECT / "manual-admin.html").read_text("utf8").replace("__TOKEN__", self.server.token).replace("</body>", checks + "</body>")
                        self.send(html.encode("utf8"), "text/html; charset=utf-8")
                    else:
                        super().do_GET()

            server = LocalImportServer(("127.0.0.1", 0), root)
            server.RequestHandlerClass = BrowserHandler
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                result = subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + str(base / "profile"), "--virtual-time-budget=30000", "--dump-dom", "http://127.0.0.1:" + str(server.server_port) + "/"], capture_output=True, encoding="utf8", timeout=60)
                self.assertIn('<div id="browser-check-result">PASS</div>', result.stdout[-1400:])
            finally:
                server.shutdown()
                server.server_close()
            server = LocalImportServer(("127.0.0.1", 0), root)
            self.assertIsNotNone(server.pending)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                result = subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + str(base / "profile"), "--virtual-time-budget=2000", "--dump-dom", "http://127.0.0.1:" + str(server.server_port) + "/"], capture_output=True, encoding="utf8", timeout=30)
                self.assertIn("Найден подтверждённый импорт", result.stdout)
                self.assertIn('id="retry"', result.stdout)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
