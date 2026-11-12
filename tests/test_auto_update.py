import copy
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from auto_update import run
from sync import encode, inspect_file, sha, write

PROJECT = Path(__file__).resolve().parents[1]


class UpdateAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("state", "data", "web"):
            shutil.copytree(PROJECT / folder, self.root / folder)
        shutil.copy2(PROJECT / "viewer-template.html", self.root / "viewer-template.html")
        key = json.loads((self.root / "state/current.json").read_bytes())["snapshot"]
        self.snapshot = json.loads((self.root / "state/snapshots" / (key + ".json")).read_bytes())
        self.original = json.loads((self.root / "data/catalog.json").read_bytes())
        self.original_ids = {r["revision_id"] for r in self.original["revisions"]}
        self.pointer = (self.root / "state/current.json").read_bytes()

    def collector(self, snapshot):
        def collect(state):
            key = sha(encode(snapshot))
            write(state / "snapshots" / (key + ".json"), encode(snapshot))
            h = json.loads((state / "history.json").read_bytes())
            if key not in h:
                h.append(key)
            write(state / "history.json", encode(h))
            write(state / "current.json", encode({"snapshot": key}))
            write(state / "last-run.json", encode({"observed_at": "2026-11-12T05:00:00+00:00", "errors": []}))
        return collect

    def execute(self, snapshot):
        with patch("auto_update.sync", side_effect=self.collector(snapshot)):
            run(self.root)
        return json.loads((self.root / "data/catalog.json").read_bytes())

    def test_identical_sources_keep_revision_ids_and_field_bytes(self):
        current = self.execute(copy.deepcopy(self.snapshot))
        self.assertEqual(self.original_ids, {r["revision_id"] for r in current["revisions"]})
        for rid in self.original_ids:
            self.assertEqual((PROJECT / "data/fields" / (rid + ".json")).read_bytes(),
                             (self.root / "data/fields" / (rid + ".json")).read_bytes())
        self.assertEqual(json.loads((self.root / "update-report.json").read_bytes())["new_revisions"], [])

    def test_new_document_and_imported_schema_preserve_old_revisions(self):
        snapshot = copy.deepcopy(self.snapshot)
        archive = next(r for r in snapshot["entries"] if r["environment"] == "prod" and r["kind"] == "zip" and "19_09" in r["url"])
        source = self.root / "state/objects" / archive["sha256"]
        buffer = io.BytesIO()
        with zipfile.ZipFile(source) as old, zipfile.ZipFile(buffer, "w") as new:
            for item in old.infolist():
                content = old.read(item.filename)
                if item.filename.endswith("/type/Proactive.xsd"):
                    content += b"\n<!-- imported schema change acceptance fixture -->"
                new.writestr(item, content)
        content = buffer.getvalue()
        archive.update(sha256=sha(content), bytes=len(content), **inspect_file(content, "zip"))
        write(source.parent / archive["sha256"], content)
        doc = next(r for r in snapshot["entries"] if r["environment"] == "prod" and "сообщения_10110_" in r["url"])
        doc["sha256"] = sha(b"new-document-fixture")
        doc["label"] = "Тип_сообщения_10110 v3.1 (01.12.2026)"
        current = self.execute(snapshot)
        ids = {r["revision_id"] for r in current["revisions"]}
        self.assertLess(self.original_ids, ids)
        older = next(r for r in current["revisions"] if r["number"] == "10110" and r["environment"] == "prod" and r["revision_id"] in self.original_ids)
        self.assertFalse(older["listed_now"])
        self.assertTrue((self.root / "site/editions" / (older["revision_id"] + ".html")).exists())
        newest = next(r for r in current["revisions"] if r["number"] == "10110" and r["environment"] == "prod" and r["listed_now"])
        self.assertEqual(newest["document_version"], "3.1")
        # A second run of the same new sources keeps exactly the same edition set.
        repeat = self.execute(snapshot)
        self.assertEqual(ids, {r["revision_id"] for r in repeat["revisions"]})

    def test_disappeared_link_keeps_previous_edition(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["entries"] = [r for r in snapshot["entries"] if not (r["environment"] == "prod" and "сообщения_10110_" in r["url"])]
        current = self.execute(snapshot)
        self.assertEqual(self.original_ids, {r["revision_id"] for r in current["revisions"]})
        self.assertFalse(next(r for r in current["revisions"] if r["number"] == "10110" and r["environment"] == "prod")["listed_now"])

    def test_failed_download_keeps_confirmed_state(self):
        with patch("auto_update.sync", side_effect=OSError("СФР недоступен")):
            with self.assertRaises(ValueError):
                run(self.root)
        self.assertEqual(self.pointer, (self.root / "state/current.json").read_bytes())
        self.assertEqual(self.original, json.loads((self.root / "data/catalog.json").read_bytes()))
        self.assertEqual(json.loads((self.root / "update-report.json").read_bytes())["status"], "failed")

    def test_unsupported_schema_keeps_confirmed_state(self):
        with patch("auto_update.sync", side_effect=self.collector(copy.deepcopy(self.snapshot))), patch("auto_update.build", side_effect=ValueError("Новая конструкция XSD")):
            with self.assertRaises(ValueError):
                run(self.root)
        self.assertEqual(self.pointer, (self.root / "state/current.json").read_bytes())
        self.assertEqual(self.original, json.loads((self.root / "data/catalog.json").read_bytes()))


if __name__ == "__main__":
    unittest.main()

