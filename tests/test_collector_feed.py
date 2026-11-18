import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_wsdl_sources as fixtures
from collector_snapshots import make_manifest, load_library, feed_manifests
from collector_feed import store_feed, import_feed


class FeedTests(unittest.TestCase):
    def test_pull_completes_pinned_257_manifest_batch_before_new_head(self):
        from collector_feed import FeedBudgetReached
        from sourcecraft_pull import _pull
        from sync import write
        from tests.test_source_transport import SourceCraftRemote, sourcecraft_files
        fixture = fixtures.WsdlSourcesTests()
        bundle = fixture.bundle()
        manifests, objects = [], {}
        for number in range(257):
            resources = {url: raw.replace(b'value="10"', f'value="{number}"'.encode("ascii"))
                         for url, raw in bundle.items()}
            row, closure = fixture.collect(resources)
            manifests.append(make_manifest(row, "2026-11-13T12:00:00+00:00"))
            objects.update(closure)
        files_a = sourcecraft_files(manifests, objects)
        files_b = sourcecraft_files(manifests[::-1], objects)
        remote = SourceCraftRemote(files_a, files_b)
        with tempfile.TemporaryDirectory() as tmp, \
                patch("sourcecraft_pull.subprocess.run", side_effect=remote.ls_remote), \
                patch("sourcecraft_pull.urlopen", side_effect=remote.fetch):
            root = Path(tmp)
            for name, raw in files_a.items():
                if name.startswith("objects/"):
                    write(root / ".sourcecraft-import" / name, raw)
            pointer = root / "state/collector/feed.json"
            with self.assertRaises(FeedBudgetReached):
                _pull(root)
            self.assertFalse(pointer.exists())
            downloaded = {name for commit, name in remote.requests if name != "index.json"}
            self.assertEqual(len(downloaded), 256)
            remaining, = set(name for name in files_a if name.startswith("collector/")) - downloaded

            remote.head = remote.b
            remote.requests.clear()
            report = _pull(root)
            self.assertEqual(report["sourcecraft_commit"], remote.a)
            self.assertEqual(report["history_size"], 257)
            self.assertEqual(remote.requests, [(remote.a, "index.json"), (remote.a, remaining)])
            self.assertEqual(remote.ref_calls, 1)
            self.assertEqual(pointer.read_bytes(), files_a["index.json"])

            remote.requests.clear()
            report = _pull(root)
            self.assertEqual(report["sourcecraft_commit"], remote.b)
            self.assertEqual(remote.requests, [(remote.b, "index.json")])
            self.assertEqual(remote.ref_calls, 2)
            self.assertEqual(pointer.read_bytes(), files_b["index.json"])

    def test_old_override_manifest_is_rejected_before_feed_pointer_publication(self):
        import copy
        import io
        import zipfile
        from collector_snapshots import validate_manifest
        from collector_feed import manifest_path
        from sync import encode, sha, write
        from wsdl_sources import _archive
        fixture = fixtures.WsdlSourcesTests()
        main_url = fixtures.BASE+'?xsd=main.xsd'
        source = ('<xs:schema xmlns:xs="'+fixtures.XS+'" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>').encode()
        row, objects = fixture.collect(fixture.bundle(source))
        # Recreate a historical envelope whose collector silently skipped override.
        old = copy.deepcopy(row)
        unsupported = source.replace(b'<xs:element ', b'<xs:override schemaLocation="base.xsd"/><xs:element ')
        with zipfile.ZipFile(io.BytesIO(objects[row['sha256']])) as z:
            files = {name: z.read(name).replace(source, unsupported) for name in z.namelist()}
        raw = _archive(files)
        objects[sha(raw)] = raw
        objects[sha(unsupported)] = unsupported
        old.update(sha256=sha(raw), bytes=len(raw))
        for schema in old['schemas']:
            if schema['sha256'] == sha(source):schema['sha256'] = sha(unsupported)
        for item in old['source_files']:
            if item['url'] == main_url:item.update(sha256=sha(unsupported), bytes=len(unsupported))
        invalid = make_manifest(old, '2026-11-17T12:00:00+00:00')
        with self.assertRaisesRegex(ValueError, 'override'):
            validate_manifest(invalid, objects.__getitem__)
        valid = make_manifest(row, '2026-11-13T12:00:00+00:00')
        with tempfile.TemporaryDirectory() as folder:
            remote, local = Path(folder)/'remote', Path(folder)/'local'
            store_feed(remote, [valid], objects)
            fetch = lambda name: (remote/name).read_bytes()
            import_feed(local, fetch)
            producer_before = (remote/'index.json').read_bytes()
            before = (local/'collector/feed.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'override'):store_feed(remote, [invalid], objects)
            self.assertEqual((remote/'index.json').read_bytes(), producer_before)
            # A feed published by an old collector is revalidated by the consumer.
            for digest, content in objects.items():write(remote/'objects'/digest, content)
            write(remote/manifest_path('test', invalid['content_id']), encode(invalid))
            index = json.loads(producer_before)
            index['manifests'].append(dict(environment='test', content_id=invalid['content_id']))
            index['latest']['test'] = invalid['content_id']
            write(remote/'index.json', encode(index))
            with self.assertRaisesRegex(ValueError, 'override'):import_feed(local, fetch)
            self.assertEqual((local/'collector/feed.json').read_bytes(), before)
            self.assertEqual(load_library(local, 'test'), row)

    def test_producer_and_consumer_enforce_the_same_serialized_size(self):
        from collector_feed import manifest_path
        from sync import encode
        a, objects = self.capture()
        size = len(encode(a))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, local = root / 'remote', root / 'local'
            with patch('collector_feed.MAX_MANIFEST', size):
                self.assertTrue(store_feed(remote, [a], objects))
                import_feed(local, lambda path: (remote / path).read_bytes())
                self.assertEqual(load_library(local, 'test'), a['library'])
            previous = (remote / 'index.json').read_bytes()
            path = remote / manifest_path('test', a['content_id'])
            path.write_bytes(b'broken envelope')
            with patch('collector_feed.MAX_MANIFEST', size - 1), self.assertRaisesRegex(ValueError, 'size limit'):
                store_feed(remote, [a], objects)
            self.assertEqual((remote / 'index.json').read_bytes(), previous)
            self.assertEqual(path.read_bytes(), b'broken envelope')
            b, new_objects = self.capture(True)
            with patch('collector_feed.MAX_MANIFEST', len(encode(b)) - 1), self.assertRaisesRegex(ValueError, 'size limit'):
                store_feed(remote, [b], new_objects)
            self.assertEqual((remote / 'index.json').read_bytes(), previous)

    def test_previously_published_large_manifest_is_readable(self):
        from sync import encode
        f = fixtures.WsdlSourcesTests()
        resources = f.bundle()
        url = fixtures.BASE + '?xsd=main.xsd'
        resources[url] = resources[url].replace(b'type="c:Code"/>', b'type="c:Code"><xs:annotation><xs:documentation>'
            + b'a' * (1024 * 1024) + b'</xs:documentation></xs:annotation></xs:element>')
        row, objects = f.collect(resources)
        manifest = make_manifest(row, '2026-11-17T00:00:00+00:00')
        self.assertGreater(len(encode(manifest)), 1024 * 1024)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / 'producer'
            store_feed(remote, [manifest], objects)
            import_feed(root / 'consumer', lambda name: (remote / name).read_bytes())
            self.assertEqual(load_library(root / 'consumer', 'test'), row)
            self.assertEqual(load_library(remote, 'test'), row)
        from collector import publish_inputs
        from tests.test_collector import FakeAPI
        api = FakeAPI()
        self.assertTrue(publish_inputs(api, [manifest], objects))
        later = dict(manifest, observed_at='2026-11-18T00:00:00+00:00')
        self.assertTrue(publish_inputs(api, [later], objects))
        name = f"state/collector/test/{manifest['content_id']}.json"
        self.assertEqual(api.blobs[api.tree[name]], encode(manifest))
        self.assertFalse(publish_inputs(api, [later], objects))

    def test_bounded_import_resumes_verified_objects_and_publishes_only_complete_index(self):
        a, ao = self.capture()
        b, bo = self.capture(True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, local, cache = root / 'remote', root / 'state', root / 'resume'
            store_feed(remote, [a], ao)
            fetch = lambda name: (remote / name).read_bytes()
            import_feed(local, fetch)
            previous = (local/'collector/feed.json').read_bytes()
            store_feed(remote, [b], bo)
            requests = []
            def read(name):
                requests.append(name)
                return fetch(name)
            from collector_feed import FeedBudgetReached
            for _ in range(8):
                try:
                    result = import_feed(local, read, checkpoint=cache, max_requests=1, max_bytes=32768)
                    break
                except FeedBudgetReached:
                    self.assertEqual((local/'collector/feed.json').read_bytes(), previous)
                    self.assertEqual(load_library(local, 'test'), a['library'])
            else:
                self.fail('bounded imports made no progress')
            self.assertEqual(result['latest']['test'], b['content_id'])
            objects = [r for r in requests if r.startswith('objects/')]
            self.assertTrue(objects)
            self.assertEqual(len(objects), len(set(objects)))

    def test_byte_limit_and_invalid_cached_object_leave_pointer_recoverable(self):
        a, ao = self.capture()
        b, bo = self.capture(True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, local, cache = root/'remote', root/'state', root/'resume'
            store_feed(remote, [a], ao)
            fetch = lambda name: (remote/name).read_bytes()
            import_feed(local, fetch)
            previous = (local/'collector/feed.json').read_bytes()
            store_feed(remote, [b], bo)
            from collector_feed import FeedBudgetReached
            manifest_size = (remote / ("collector/test/"+b["content_id"]+".json")).stat().st_size
            with self.assertRaises(FeedBudgetReached):
                import_feed(local, fetch, checkpoint=cache, max_bytes=manifest_size)
            self.assertEqual((local/'collector/feed.json').read_bytes(), previous)
            digest = b["library"]["sha256"]
            target = cache/'objects'/digest
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(b'incomplete')
            with patch('collector_feed.MAX_CHECKPOINT_BYTES', 1), self.assertRaisesRegex(ValueError, 'storage budget'):
                import_feed(local, fetch, checkpoint=cache)
            self.assertEqual((local/'collector/feed.json').read_bytes(), previous)
            import_feed(local, fetch, checkpoint=cache)
            self.assertEqual((local/'objects'/digest).read_bytes(), bo[digest])


    def test_sourcecraft_legacy_lookup_requires_an_unmarked_store(self):
        import os
        from collector_snapshots import observation_path
        from sync import encode, write
        manifest, objects = self.capture()
        digest = manifest['content_id']
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, SEDO_COLLECTOR_SOURCE='sourcecraft'):
            state = Path(tmp)
            for key, raw in objects.items():
                write(state / 'objects' / key, raw)
            write(state / 'collector/test' / (digest + '.json'), encode(manifest))
            self.assertEqual(load_library(state, 'test'), manifest['library'])
            for name in ('collector/test/current', 'collector/test/current.json',
                         'collector/test/current-observed-at', observation_path('home', 'test', digest)):
                marker = state / name
                write(marker, digest.encode())
                with self.assertRaisesRegex(ValueError, 'SourceCraft'):
                    load_library(state, 'test')
                marker.unlink()

    def test_repull_repairs_source_observation_without_touching_shared_manifest(self):
        from collector_snapshots import feed_manifests, observation_path
        from sync import encode
        manifest, objects = self.capture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, local = root / "remote", root / "local"
            store_feed(remote, [manifest], objects)
            fetch = lambda name: (remote / name).read_bytes()
            import_feed(local, fetch)
            receipt = local / observation_path("sourcecraft", "test", manifest["content_id"])
            receipt.write_text("broken")
            import_feed(local, fetch)
            self.assertEqual(feed_manifests(local)[0]["observed_at"], manifest["observed_at"])
            self.assertEqual((local / ("collector/test/" + manifest["content_id"] + ".json")).read_bytes(), encode(manifest))
    def test_replay_uses_historical_docx_and_reports_an_unrecoverable_pair(self):
        import io
        import zipfile
        from catalog import derive
        from collector_snapshots import replay_history
        from sync import encode, inspect_file, record_observation, sha, write
        a, ao = self.capture()
        b, bo = self.capture(True)
        b["observed_at"] = "2026-11-14T12:00:00+00:00"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Тип сообщения 999: Fixture</w:t></w:r></w:p><w:p><w:r><w:t>&lt;m:response xmlns:m="urn:main"&gt;&lt;/m:response&gt;</w:t></w:r></w:p></w:body></w:document>')
        document = archive.getvalue()
        doc = dict(environment="test", kind="docx", label="Fixture v1.0", url="https://lk-test.fss.ru/specs_sedo/fixture.docx",
                   sha256=sha(document), **inspect_file(document, "docx"))
        snapshot = dict(format=1, pages={}, entries=[doc, a["library"]])
        for confirmed, active in ((True, a), (True, b), (False, a)):
            with self.subTest(confirmed=confirmed, active=active["content_id"]), tempfile.TemporaryDirectory() as remote, tempfile.TemporaryDirectory() as local:
                remote, state = Path(remote), Path(local)
                def blob(raw):
                    digest = sha(raw)
                    write(state / "objects" / digest, raw)
                    return digest
                blob(document)
                if confirmed:
                    key = sha(encode(snapshot))
                    write(state / "snapshots" / (key + ".json"), encode(snapshot))
                    write(state / "history.json", encode([key]))
                    write(state / "current.json", encode(dict(snapshot=key)))
                    record_observation(state, key, a["observed_at"])
                store_feed(remote, [a], ao)
                store_feed(remote, [b], bo)
                store_feed(remote, [active], ao if active is a else bo)
                import_feed(state, lambda path: (remote / path).read_bytes())
                current = dict(snapshot, entries=[dict(doc, sha256=blob(document+b"new bytes")), active["library"]])
                replayed, ledger = replay_history(state, current, blob)
                record = ledger["manifests"]["test"][b["content_id"]]
                if confirmed:
                    self.assertEqual(record["status"], "replayed")
                    self.assertEqual(record["observed_at"], b["observed_at"])
                    self.assertEqual(len(replayed), 1)
                    past = json.loads((state / "snapshots" / (replayed[0] + ".json")).read_bytes())
                    rows = derive(past)["revisions"]
                    self.assertEqual(rows[0]["document_sha256"], sha(document))
                    self.assertEqual(rows[0]["archive_sha256"], b["library"]["sha256"])
                else:
                    self.assertEqual(replayed, [])
                    self.assertEqual(record["status"], "unavailable")
                    self.assertIn("DOCX", record["reason"])

    def test_replay_a_b_a_retains_intermediate_edition_and_retries_after_failure(self):
        from datetime import datetime, timezone
        from catalog import export
        from collector_snapshots import pending
        from sync import sync, encode, write
        import io
        import zipfile
        a, ao = self.capture()
        b, bo = self.capture(True)
        b["observed_at"] = "2026-11-14T12:00:00+00:00"
        resources = {}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w"):
            pass
        document = io.BytesIO()
        with zipfile.ZipFile(document, "w") as bundle:
            bundle.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Тип сообщения 999: Fixture</w:t></w:r></w:p><w:p><w:r><w:t>&lt;m:response xmlns:m="urn:main"&gt;&lt;/m:response&gt;</w:t></w:r></w:p></w:body></w:document>')
        for host in ("https://lk.sfr.gov.ru", "https://lk-test.fss.ru"):
            resources[host+"/sedo.html"] = b'<a href="/specs_sedo/schemas.zip">XSD</a>'
            resources[host+"/specs_sedo/schemas.zip"] = archive.getvalue()
        resources["https://lk-test.fss.ru/sedo.html"] += b'<a href="/specs_sedo/fixture.docx">Fixture v1.0</a>'
        resources["https://lk-test.fss.ru/specs_sedo/fixture.docx"] = document.getvalue()
        with tempfile.TemporaryDirectory() as remote, tempfile.TemporaryDirectory() as local:
            remote, root = Path(remote), Path(local)
            state = root / "state"
            store_feed(remote, [a], ao)
            import_feed(state, lambda path: (remote / path).read_bytes())
            with patch("sync.get", side_effect=lambda url, **kwargs: resources[url]), patch("sync.datetime") as clock:
                clock.now.return_value = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
                sync(state)
            before = (state / "current.json").read_bytes()
            store_feed(remote, [b], bo)
            store_feed(remote, [a], ao)
            import_feed(state, lambda path: (remote / path).read_bytes())
            self.assertTrue(pending(root), "an intermediate manifest must trigger replay even when latest returned to A")
            original_write = __import__("sync").write
            def fail(path, raw):
                if path.name == "history.json":
                    raise OSError("interrupted replay")
                return original_write(path, raw)
            with patch("sync.get", side_effect=lambda url, **kwargs: resources[url]), \
                    patch("sync.write", side_effect=fail), self.assertRaisesRegex(OSError, "interrupted"):
                sync(state)
            self.assertEqual((state / "current.json").read_bytes(), before)
            self.assertTrue(pending(root))
            with patch("sync.get", side_effect=lambda url, **kwargs: resources[url]):
                sync(state)
            export(state, root / "catalog.json")
            catalog = json.loads((root / "catalog.json").read_bytes())
            self.assertEqual(len(catalog["revisions"]), 2)
            self.assertEqual(sum(r["listed_now"] for r in catalog["revisions"]), 1)
            current = next(r for r in catalog["revisions"] if r["listed_now"])
            self.assertEqual(current["archive_sha256"], a["library"]["sha256"])
            intermediate = next(r for r in catalog["revisions"] if not r["listed_now"])
            event = next(e for e in catalog["updates"] if e["revision_id"] == intermediate["revision_id"])
            self.assertEqual(event["observed_at"], b["observed_at"])
            captured = json.loads((state / "snapshots" / (event["snapshot"] + ".json")).read_bytes())
            self.assertEqual(captured["collector_observation"]["content_id"], b["content_id"])
            self.assertFalse(pending(root))
            history = (state / "history.json").read_bytes()
            with patch("sync.get", side_effect=lambda url, **kwargs: resources[url]):
                sync(state)
            self.assertEqual((state / "history.json").read_bytes(), history)
            export(state, root / "catalog.json")
            self.assertEqual(json.loads((root / "catalog.json").read_bytes())["updates"], catalog["updates"])

    def test_invalid_cached_manifest_envelope_is_replaced_and_retry_is_idempotent(self):
        manifest, objects = self.capture()
        with tempfile.TemporaryDirectory() as remote, tempfile.TemporaryDirectory() as local:
            remote, local = Path(remote), Path(local)
            store_feed(remote, [manifest], objects)
            read = lambda name: (remote / name).read_bytes()
            import_feed(local, read)
            name = "collector/test/" + manifest["content_id"] + ".json"
            broken = dict(manifest, observed_at="2026-11-13T12:00:00")
            (local / name).write_text(json.dumps(broken))
            import_feed(local, read)
            self.assertEqual(json.loads((local / name).read_bytes()), manifest)
            self.assertEqual(import_feed(local, read)["new_manifests"], 0)

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

    def test_producer_history_retains_first_observations_after_return_to_a(self):
        a, ao = self.capture()
        b, bo = self.capture(True)
        a["observed_at"] = "2026-11-01T00:00:00+00:00"
        b["observed_at"] = "2026-11-02T00:00:00+00:00"
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", SEDO_COLLECTOR_SOURCE="sourcecraft"):
            root = Path(tmp)
            store_feed(root, [a], ao)
            store_feed(root, [b], bo)
            store_feed(root, [dict(a, observed_at="2026-11-03T00:00:00+00:00")], ao)
            self.assertEqual(load_library(root, "test"), a["library"])
            self.assertEqual([(m["content_id"], m["observed_at"]) for m in feed_manifests(root)],
                             [(a["content_id"], "2026-11-01T00:00:00+00:00"),
                              (b["content_id"], "2026-11-02T00:00:00+00:00")])

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
        from unittest.mock import patch
        from auto_update import update_lock
        from sourcecraft_pull import pull
        with tempfile.TemporaryDirectory() as temp, patch("sourcecraft_pull._pull") as imported, update_lock(Path(temp)):
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
