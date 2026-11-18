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
from sync import encode, inspect_file, record_observation, sha, write
from verify import verify

PROJECT = Path(__file__).resolve().parents[1]


class OfflineReplayTests(unittest.TestCase):
    def test_fragment_only_and_multiple_aliases_replay_exact_snapshot(self):
        from sync import SITES
        from tests.test_source_collection_regressions import SourceCollectionTests
        from verify import failure_probe_url
        for fragments in (('#page=2',), ('#page=2', '#page=3')):
            with self.subTest(fragments=fragments):
                fixture = SourceCollectionTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                page = SITES['test']
                href = '/specs_sedo/file.zip'
                url = page.replace('/sedo.html', href)
                fixture.resources[page] = ''.join(f'<a href="{href}{frag}">Schema</a>' for frag in fragments).encode()
                self.assertEqual(fixture.collect(1).count(url), 1)
                self.assertEqual(failure_probe_url(fixture.current(), [page, url]), url)
                data = fixture.root / 'data'
                write(data / 'catalog.json', encode(dict(revisions=[
                    dict(number='10110', environment=e, revision_id=e, status='document_only', source_type='manual')
                    for e in SITES])))
                verify(fixture.state, data)

    def test_mass_loss_of_resources_rejects_candidate_with_unchanged_message_numbers(self):
        from catalog import export
        from sync import SITES
        from tests.test_source_collection_regressions import SourceCollectionTests
        fixture = SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root, page = fixture.root, SITES['prod']
        original = fixture.resources[page]
        def listing(count):
            links = []
            for n in range(count):
                href = f'/specs_sedo/guide{n}.pdf'
                fixture.resources[page.replace('/sedo.html', href)] = b'guide'
                links.extend(f'<a href="{href}#page={p}">Guide</a>' for p in (1, 2))
            fixture.resources[page] = original + ''.join(links).encode()
        listing(71)
        fixture.collect(1)
        export(fixture.state, root / 'data/catalog.json')
        write(root / 'data/fields/index.json', b'[]')
        shutil.copytree(PROJECT / 'web', root / 'web')
        shutil.copy2(PROJECT / 'viewer-template.html', root)
        pointer = (fixture.state / 'current.json').read_bytes()
        with patch('sync.get', side_effect=fixture.resources.__getitem__):
            listing(53)
            with self.assertRaisesRegex(ValueError, 'prod.*18'):
                run(root)
            self.assertEqual((fixture.state / 'current.json').read_bytes(), pointer)
            listing(70)
            run(root)
        self.assertNotEqual((fixture.state / 'current.json').read_bytes(), pointer)

    def test_mass_disappearance_is_rejected_but_one_removed_publication_is_allowed(self):
        from auto_update import validate_publications
        rows = [dict(number=str(i), environment='prod', listed_now=True) for i in range(132)]
        previous = dict(revisions=rows, registry=rows)
        for kept in (1, 100):
            with self.subTest(kept=kept), self.assertRaisesRegex(ValueError, 'prod'):
                validate_publications(previous, dict(revisions=rows[:kept], registry=rows[:kept]))
        validate_publications(previous, dict(revisions=rows[:-1], registry=rows[:-1]))
        validate_publications(dict(revisions=rows, registry=rows[:1]), dict(revisions=rows, registry=[]))

    def test_failure_probe_uses_a_replayed_catalog_page_without_standalone_zips(self):
        from sync import SITES
        from verify import failure_probe_url

        snapshot = dict(pages={'test': 'example'}, entries=[])
        self.assertEqual(failure_probe_url(snapshot, [SITES['test']]), SITES['test'])

    def test_failure_probe_uses_an_archive_from_current_snapshot(self):
        from sync import SITES

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            data = root / "data"
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w"):
                pass
            body = archive.getvalue()
            pages, entries = {}, []
            revisions = []
            for environment, site in SITES.items():
                href = "/specs_sedo/new-edition.zip"
                page = f'<a href="{href}">Current editions</a>'.encode()
                pages[environment] = sha(page)
                write(state / "objects" / sha(page), page)
                entries.append(dict(environment=environment, url=site.rsplit("/", 1)[0] + href,
                                    kind="zip", sha256=sha(body)))
                revisions.append(dict(number="10110", environment=environment, revision_id=environment,
                                      status="document_only", source_type="manual", root="example"))
            write(state / "objects" / sha(body), body)
            snapshot = encode(dict(pages=pages, entries=entries))
            digest = sha(snapshot)
            write(state / "snapshots" / (digest + ".json"), snapshot)
            write(state / "history.json", encode([digest]))
            write(state / "current.json", encode(dict(snapshot=digest)))
            write(data / "catalog.json", encode(dict(revisions=revisions)))
            verify(state, data)


class UpdateAcceptanceTests(unittest.TestCase):
    def test_archived_tree_does_not_hide_missing_current_mapping(self):
        snapshot = copy.deepcopy(self.snapshot)
        old = next(row for row in self.original["revisions"]
                   if row["environment"] == "prod" and row["number"] == "6" and row["listed_now"])
        for row in snapshot["entries"]:
            if row["environment"] == "prod":
                row["module_matches"] = [match for match in row.get("module_matches", []) if match["number"] != "6"]
        current = self.execute(snapshot)
        report = json.loads((self.root / "update-report.json").read_bytes())
        coverage = json.loads((self.root / "data/schema-coverage.json").read_bytes())
        self.assertIn(("prod", "6"), {(r["environment"], r["number"]) for r in report["missing_trees"]})
        self.assertEqual(report["missing_trees"], coverage["missing"])
        archived = next(row for row in current["revisions"] if row["revision_id"] == old["revision_id"])
        self.assertFalse(archived["listed_now"])
        self.assertTrue((self.root / "site/editions" / (old["revision_id"] + ".html")).is_file())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("state", "data", "web"):
            shutil.copytree(PROJECT / folder, self.root / folder)
        shutil.copy2(PROJECT / "viewer-template.html", self.root / "viewer-template.html")
        # Freeze the source fixture. Live SFR revisions can retire 10110 or add
        # further editions without changing this history/recovery contract.
        key = '6581db63a1768b984f134e7b4cdae74f1a320df056ecd0aa6aa6a6a22a9839b7'
        history = json.loads((self.root / 'state/history.json').read_bytes())
        write(self.root / 'state/history.json', encode(history[:history.index(key) + 1]))
        write(self.root / 'state/current.json', encode(dict(snapshot=key)))
        from catalog import export
        export(self.root / 'state', self.root / 'data/catalog.json')
        self.snapshot = json.loads((self.root / "state/snapshots" / (key + ".json")).read_bytes())
        self.original = json.loads((self.root / "data/catalog.json").read_bytes())
        self.original_ids = {r["revision_id"] for r in self.original["revisions"]}
        self.pointer = (self.root / "state/current.json").read_bytes()
        self.observations = (self.root / "state/observations.json").read_bytes()

    def collector(self, snapshot):
        def collect(state):
            key = sha(encode(snapshot))
            write(state / "snapshots" / (key + ".json"), encode(snapshot))
            h = json.loads((state / "history.json").read_bytes())
            if key not in h:
                h.append(key)
            write(state / "history.json", encode(h))
            write(state / "current.json", encode({"snapshot": key}))
            timestamp = "2026-11-13T04:17:00+00:00"
            record_observation(state, key, timestamp)
            write(state / "last-run.json", encode({"observed_at": timestamp, "snapshot": key, "errors": []}))
        return collect

    def execute(self, snapshot):
        with patch("auto_update.sync", side_effect=self.collector(snapshot)):
            run(self.root)
        return json.loads((self.root / "data/catalog.json").read_bytes())

    def test_identical_sources_keep_revision_ids_and_field_bytes(self):
        current = self.execute(copy.deepcopy(self.snapshot))
        self.assertEqual(self.original_ids, {r["revision_id"] for r in current["revisions"]})
        for rid in self.original_ids:
            revision=next(r for r in self.original["revisions"] if r["revision_id"]==rid)
            if revision["status"]=="document_only":continue
            self.assertEqual((PROJECT / "data/fields" / (rid + ".json")).read_bytes(),
                             (self.root / "data/fields" / (rid + ".json")).read_bytes())
        self.assertEqual(json.loads((self.root / "update-report.json").read_bytes())["new_revisions"], [])
        self.assertEqual(current["updates"], self.original["updates"])

    def test_new_document_and_imported_schema_preserve_old_revisions(self):
        snapshot = copy.deepcopy(self.snapshot)
        previous_edition = next(r for r in self.original["revisions"]
                       if r["environment"] == "prod" and r["number"] == "10110" and r["listed_now"])
        archive = next(r for r in snapshot["entries"]
                       if r["environment"] == "prod" and r["url"] == previous_edition["archive_url"])
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
        # Same URL, replaced original: the old bytes must remain downloadable.
        buffer = io.BytesIO()
        with zipfile.ZipFile(self.root / "state/objects" / doc["sha256"]) as old, zipfile.ZipFile(buffer, "w") as new:
            for item in old.infolist():
                content = old.read(item.filename)
                if item.filename == "word/document.xml":
                    content += b"\n<!-- new-document-fixture -->"
                new.writestr(item, content)
        content = buffer.getvalue()
        doc["sha256"] = sha(content)
        write(self.root / "state/objects" / doc["sha256"], content)
        doc["label"] = "Тип_сообщения_10110 v3.1 (01.12.2026)"
        current = self.execute(snapshot)
        ids = {r["revision_id"] for r in current["revisions"]}
        self.assertLess(self.original_ids, ids)
        older = next(r for r in current["revisions"] if r["revision_id"] == previous_edition["revision_id"])
        self.assertFalse(older["listed_now"])
        self.assertTrue((self.root / "site/editions" / (older["revision_id"] + ".html")).exists())
        self.assertEqual((self.root / "site/documents" / (older["document_sha256"] + ".docx")).read_bytes(),
                         (PROJECT / "state/objects" / older["document_sha256"]).read_bytes())
        newest = next(r for r in current["revisions"] if r["number"] == "10110" and r["environment"] == "prod" and r["listed_now"])
        self.assertEqual(newest["document_version"], "3.1")
        event = next(e for e in current["updates"] if e["revision_id"]==newest["revision_id"])
        self.assertEqual(event["previous_revision_id"], older["revision_id"])
        self.assertEqual(event["observed_at"], "2026-11-13T04:17:00+00:00")
        self.assertEqual((self.root / "state/objects" / newest["document_sha256"]).read_bytes(), content)
        self.assertEqual((self.root / "site/documents" / (newest["document_sha256"] + ".docx")).read_bytes(), content)
        # This injected inventory intentionally differs from its archived HTML.
        # Exact source replay is covered separately by OfflineReplayTests.
        # A second run of the same new sources keeps exactly the same edition set.
        repeat = self.execute(snapshot)
        self.assertEqual(ids, {r["revision_id"] for r in repeat["revisions"]})
        self.assertEqual(repeat["updates"], current["updates"])

    def test_disappeared_link_keeps_previous_edition(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["entries"] = [r for r in snapshot["entries"] if not (r["environment"] == "prod" and "сообщения_10110_" in r["url"])]
        current = self.execute(snapshot)
        self.assertEqual(self.original_ids, {r["revision_id"] for r in current["revisions"]})
        self.assertEqual(current["updates"], self.original["updates"])
        self.assertFalse(next(r for r in current["revisions"] if r["number"] == "10110" and r["environment"] == "prod")["listed_now"])
        published = json.loads((self.root / "site/catalog-data.js").read_text("utf8").removeprefix("window.SEDO_CATALOG=").removesuffix(";"))
        older = next(r for r in published["revisions"] if r["number"] == "10110" and r["environment"] == "prod")
        self.assertEqual((self.root / "site" / older["document_download"]).read_bytes(),
                         (PROJECT / "state/objects" / older["document_sha256"]).read_bytes())

    def test_failed_download_keeps_confirmed_state(self):
        with patch("auto_update.sync", side_effect=OSError("СФР недоступен")):
            with self.assertRaises(ValueError):
                run(self.root)
        self.assertEqual(self.pointer, (self.root / "state/current.json").read_bytes())
        self.assertEqual(self.original, json.loads((self.root / "data/catalog.json").read_bytes()))
        self.assertEqual(json.loads((self.root / "update-report.json").read_bytes())["status"], "failed")
        self.assertEqual(self.observations, (self.root / "state/observations.json").read_bytes())

    def test_corrupt_original_keeps_confirmed_state(self):
        snapshot = copy.deepcopy(self.snapshot)
        doc = next(r for r in snapshot["entries"] if r["environment"] == "prod" and "сообщения_10110_" in r["url"])
        # Simulate collection returning bytes inconsistent with its inventory.
        doc["sha256"] = sha(b"expected DOCX")
        write(self.root / "state/objects" / doc["sha256"], b"different DOCX")
        with self.assertRaisesRegex(ValueError, "DOCX"):
            self.execute(snapshot)
        self.assertEqual(self.pointer, (self.root / "state/current.json").read_bytes())
        self.assertEqual(self.original, json.loads((self.root / "data/catalog.json").read_bytes()))
        self.assertEqual(self.observations, (self.root / "state/observations.json").read_bytes())

    def test_unsupported_schema_keeps_confirmed_state(self):
        with patch("auto_update.sync", side_effect=self.collector(copy.deepcopy(self.snapshot))), patch("auto_update.build", side_effect=ValueError("Новая конструкция XSD")):
            with self.assertRaises(ValueError):
                run(self.root)
        self.assertEqual(self.pointer, (self.root / "state/current.json").read_bytes())
        self.assertEqual(self.original, json.loads((self.root / "data/catalog.json").read_bytes()))


if __name__ == "__main__":
    unittest.main()

