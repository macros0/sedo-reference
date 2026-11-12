import copy
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from manual_import import prepare_import
from auto_update import run
from catalog import export
from sync import encode, record_observation, sha, write

PROJECT = Path(__file__).resolve().parents[1]


class ManualImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("state", "data", "web"):
            shutil.copytree(PROJECT / folder, self.root / folder)
        shutil.copy2(PROJECT / "viewer-template.html", self.root / "viewer-template.html")
        catalog = json.loads((self.root / "data/catalog.json").read_bytes())
        self.original = copy.deepcopy(catalog)
        self.example = next(r for r in catalog["revisions"] if r["number"] == "10110" and r["environment"] == "prod" and r["status"] == "matched")
        self.archive = (self.root / "state/objects" / self.example["archive_sha256"]).read_bytes()
        self.doc = (self.root / "state/objects" / self.example["document_sha256"]).read_bytes()
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.doc)) as old, zipfile.ZipFile(buffer, "w") as new:
            for item in old.infolist():
                content = old.read(item.filename)
                if item.filename == "word/document.xml":
                    content += b"\n<!-- manual import fixture -->"
                new.writestr(item, content)
        self.new_doc = buffer.getvalue()
        self.metadata = dict(number="10110", environment="prod", version="9.9", date="2026-11-13", source="Telegram")

    def prepare(self, archive=None, doc=None):
        preview = prepare_import(self.root, doc or self.new_doc, "spec.docx", self.metadata, archive, "schemas.zip")
        self.addCleanup(preview.close)
        return preview

    def test_docx_only_preview_save_and_automatic_update_preserve_history(self):
        before = (self.root / "state/current.json").read_bytes()
        preview = self.prepare()
        self.assertEqual(self.original, json.loads((self.root / "data/catalog.json").read_bytes()))
        self.assertTrue(preview.changed)
        self.assertEqual(preview.revision["status"], "document_only")
        self.assertNotIn("archive_sha256", preview.revision)
        preview.save()
        current = json.loads((self.root / "data/catalog.json").read_bytes())
        self.assertEqual(len(current["revisions"]), len(self.original["revisions"]) + 1)
        self.assertEqual(before, (self.root / "state/current.json").read_bytes())
        rid = preview.revision["revision_id"]
        path = self.root / "site/documents" / (preview.revision["document_sha256"] + ".docx")
        self.assertEqual(path.read_bytes(), self.new_doc)
        with patch("auto_update.sync", return_value=None):
            run(self.root)
        updated = json.loads((self.root / "data/catalog.json").read_bytes())
        self.assertIn(rid, {r["revision_id"] for r in updated["revisions"]})
        self.assertEqual(current["updates"], updated["updates"])

    def test_docx_and_xsd_build_tree_and_comparison(self):
        preview = self.prepare(self.archive)
        self.assertEqual(preview.revision["status"], "matched")
        rid = preview.revision["revision_id"]
        self.assertTrue((preview.directory / "site/editions" / (rid + ".html")).exists())
        published = json.loads((preview.directory / "site/catalog-data.js").read_text("utf8").removeprefix("window.SEDO_CATALOG=").removesuffix(";"))
        event = next(e for e in published["updates"] if e["revision_id"] == rid)
        self.assertEqual(event["comparison"]["status"], "ready")
        preview.save()
        duplicate = self.prepare(self.archive)
        self.assertFalse(duplicate.changed)

    def test_original_official_document_is_not_duplicated_without_schema(self):
        preview = self.prepare(doc=self.doc)
        self.assertFalse(preview.changed)

    def test_invalid_docx_leaves_project_unchanged(self):
        with self.assertRaises(ValueError):
            self.prepare(doc=b"invalid DOCX")
        self.assertEqual(self.original, json.loads((self.root / "data/catalog.json").read_bytes()))

    def test_official_publication_keeps_manual_first_appearance(self):
        preview = self.prepare(self.archive)
        preview.save()
        before = json.loads((self.root / "data/catalog.json").read_bytes())
        rid = preview.revision["revision_id"]
        state = self.root / "state"
        key = json.loads((state / "current.json").read_bytes())["snapshot"]
        snapshot = json.loads((state / "snapshots" / (key + ".json")).read_bytes())
        doc = next(r for r in snapshot["entries"] if r["environment"] == "prod" and "сообщения_10110_" in r["url"])
        doc["sha256"] = sha(self.new_doc)
        doc["label"] = "Тип сообщения 10110 v9.9 (13.11.2026)"
        payload = encode(snapshot)
        key = sha(payload)
        write(state / "snapshots" / (key + ".json"), payload)
        history = json.loads((state / "history.json").read_bytes())
        history.append(key)
        write(state / "history.json", encode(history))
        write(state / "current.json", encode(dict(snapshot=key)))
        record_observation(state, key, "2026-11-14T04:17:00+00:00")
        export(state, self.root / "data/catalog.json")
        after = json.loads((self.root / "data/catalog.json").read_bytes())
        self.assertEqual(before["updates"], after["updates"])
        self.assertEqual(len(before["revisions"]), len(after["revisions"]))
        official = next(r for r in after["revisions"] if r["revision_id"] == rid)
        self.assertTrue(official["listed_now"])
        self.assertTrue(official["document_url"].startswith("https://"))
        self.assertEqual(official["manual_source"], preview.revision["manual_source"])

    def test_changed_project_rejects_stale_preview(self):
        preview = self.prepare()
        path = self.root / "data/catalog.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "изменил|повтор|предпросмотр"):
            preview.save()

    def test_edit_to_generated_site_is_not_overwritten(self):
        preview = self.prepare()
        path = self.root / "site/app.js"
        path.parent.mkdir(exist_ok=True)
        path.write_text("owner edit after preview", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "изменил|повтор|предпросмотр"):
            preview.save()
        self.assertEqual(path.read_text("utf8"), "owner edit after preview")


if __name__ == "__main__":
    unittest.main()
