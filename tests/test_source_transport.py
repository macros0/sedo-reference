import io
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

import sync


class SourceCraftRemote:
    a, b = "a" * 40, "b" * 40

    def __init__(self, files_a, files_b):
        self.files = {self.a: files_a, self.b: files_b}
        self.head = self.a
        self.ref_calls = 0
        self.ref_unavailable = False
        self.requests = []
        self.before_fetch = lambda commit, path: None

    def ls_remote(self, args, **kwargs):
        self.ref_calls += 1
        if self.ref_unavailable:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, stdout=self.head + " refs/heads/snapshots\n")

    def fetch(self, request, **kwargs):
        from sourcecraft_pull import RAW
        if not request.full_url.startswith(RAW):
            raise AssertionError("Unexpected SourceCraft URL " + request.full_url)
        commit, path = request.full_url[len(RAW):].split("/", 1)
        self.requests.append((commit, path))
        self.before_fetch(commit, path)
        return io.BytesIO(self.files[commit][path])


def sourcecraft_files(manifests, objects):
    from collector_feed import manifest_path
    entries = []
    files = {"objects/" + digest: raw for digest, raw in objects.items()}
    for manifest in sorted(manifests, key=lambda m: m["content_id"]):
        env, digest = manifest["environment"], manifest["content_id"]
        entries.append({"environment": env, "content_id": digest})
        files[manifest_path(env, digest)] = sync.encode(manifest)
    latest = {m["environment"]: m["content_id"] for m in manifests}
    files["index.json"] = sync.encode({"format": 1, "manifests": entries, "latest": latest})
    return files


def sourcecraft_budget(requests=256, max_bytes=128 * 1024 * 1024):
    from collector_feed import FeedDownloads
    download = FeedDownloads.download
    def bounded(batch, *args, **kwargs):
        batch.max_requests = requests
        batch.max_bytes = max_bytes
        return download(batch, *args, **kwargs)
    return patch.object(FeedDownloads, "download", bounded)


class SourceCraftResumeTests(unittest.TestCase):
    def test_malformed_xml_closure_falls_back_to_confirmable_commit(self):
        from copy import deepcopy
        from collector_feed import FeedBudgetReached, manifest_path
        from sourcecraft_pull import _pull
        from tests.test_collector_feed import FeedTests
        valid, objects = FeedTests().capture()
        invalid = deepcopy(valid)
        malformed = b"<unclosed"
        digest = sync.sha(malformed)
        invalid["library"]["source_files"][0].update(sha256=digest, bytes=len(malformed))
        invalid["content_id"] = sync.sha(sync.encode(invalid["library"]))
        files_a = sourcecraft_files([invalid], {**objects, digest: malformed})
        files_b = sourcecraft_files([valid], objects)
        for cached_locally in (False, True):
            with self.subTest(cached_locally=cached_locally), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                remote = SourceCraftRemote(files_a, files_b)
                pointer = root / "state/collector/feed.json"
                prior = sourcecraft_files([], {})["index.json"]
                sync.write(pointer, prior)
                with patch("sourcecraft_pull.subprocess.run", side_effect=remote.ls_remote), \
                        patch("sourcecraft_pull.urlopen", side_effect=remote.fetch):
                    with sourcecraft_budget(1), self.assertRaises(FeedBudgetReached):
                        _pull(root)
                    if cached_locally:
                        sync.write(root / "state" / manifest_path("test", invalid["content_id"]), sync.encode(invalid))
                        for name, raw in files_a.items():
                            if name.startswith("objects/"):
                                sync.write(root / "state" / name, raw)
                    self.assertEqual(pointer.read_bytes(), prior)
                    remote.head = remote.b
                    self.assertEqual(_pull(root)["sourcecraft_commit"], remote.b)
                    self.assertEqual(pointer.read_bytes(), files_b["index.json"])

    def test_missing_pinned_raw_commit_falls_back_but_transient_failure_keeps_it(self):
        from urllib.error import HTTPError
        from collector_feed import FeedBudgetReached
        from sourcecraft_pull import RAW, _pull
        from tests.test_collector_feed import FeedTests
        a, objects = FeedTests().capture()
        files = sourcecraft_files([a], objects)
        for status in (404, 410, 503):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                remote = SourceCraftRemote(files, files)
                pointer = root / "state/collector/feed.json"
                with patch("sourcecraft_pull.subprocess.run", side_effect=remote.ls_remote), \
                        patch("sourcecraft_pull.urlopen", side_effect=remote.fetch):
                    with sourcecraft_budget(1), self.assertRaises(FeedBudgetReached):
                        _pull(root)
                    remote.head = remote.b
                    remote.requests.clear()
                    def fail_pinned(commit, path):
                        if commit == remote.a:
                            raise HTTPError(RAW + commit + "/" + path, status, "fixture HTTP error", {}, None)
                    remote.before_fetch = fail_pinned
                    if status == 503:
                        with self.assertRaises(HTTPError) as raised:
                            _pull(root)
                        self.assertEqual(raised.exception.code, status)
                        self.assertEqual(remote.ref_calls, 1)
                        self.assertEqual(remote.requests, [(remote.a, "index.json")])
                        self.assertFalse(pointer.exists())
                    else:
                        self.assertEqual(_pull(root)["sourcecraft_commit"], remote.b)
                        self.assertEqual(remote.ref_calls, 2)
                        self.assertEqual(remote.requests[:2], [(remote.a, "index.json"), (remote.b, "index.json")])
                        self.assertEqual(pointer.read_bytes(), files["index.json"])

    def test_restored_cache_falls_back_to_fixed_envelope_with_one_shared_budget(self):
        from collector_feed import FeedBudgetReached
        from sourcecraft_pull import _pull
        from tests.test_collector_feed import FeedTests
        fixture = FeedTests()
        a, ao = fixture.capture()
        b, bo = fixture.capture(True)
        files_b = sourcecraft_files([a, b], {**ao, **bo})
        manifests = sorted(name for name in files_b if name.startswith("collector/"))
        first, second = manifests
        invalid = json.loads(files_b[second])
        invalid["observed_at"] = "2026-11-13T12:00:00"
        files_a = dict(files_b, **{second: sync.encode(invalid)})
        one_snapshot_work = sum(
            len(files_b[name]) + m["library"]["bytes"] + sum(s["bytes"] for s in m["library"]["source_files"])
            for name, m in zip(manifests, sorted([a, b], key=lambda row: row["content_id"]))
        )
        completed_on_fallback = ("enough", "verification")
        for limit in ("requests", "bytes", *completed_on_fallback, "invalid B"):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "first"
                remote = SourceCraftRemote(files_a, files_a if limit == "invalid B" else files_b)
                cache = root / ".sourcecraft-import"
                for name, raw in files_a.items():
                    if name.startswith("objects/"):
                        sync.write(cache / name, raw)
                with patch("sourcecraft_pull.subprocess.run", side_effect=remote.ls_remote), \
                        patch("sourcecraft_pull.urlopen", side_effect=remote.fetch):
                    with sourcecraft_budget(1), self.assertRaises(FeedBudgetReached):
                        _pull(root)
                    self.assertEqual(remote.requests, [(remote.a, "index.json"), (remote.a, first)])

                    restored = Path(tmp) / "restored"
                    restored_cache = restored / ".sourcecraft-import"
                    shutil.copytree(cache, restored_cache)
                    remote.head = remote.b
                    remote.requests.clear()
                    pointer = restored / "state/collector/feed.json"
                    def check_fallback_binding(commit, path):
                        self.assertFalse(pointer.exists())
                        if (commit, path) == (remote.b, "index.json"):
                            marker = json.loads((restored_cache / "pending.json").read_bytes())
                            self.assertEqual(marker["commit"], remote.b)
                    remote.before_fetch = check_fallback_binding
                    requests = 2 if limit == "requests" else 256
                    byte_budget = len(files_a[second]) + len(files_b[first]) if limit == "bytes" else 128 * 1024 * 1024
                    offline_budget = one_snapshot_work if limit == "verification" else 2 * 1024**3
                    with sourcecraft_budget(requests, byte_budget), \
                            patch("collector_feed.MAX_VERIFICATION_BYTES", offline_budget):
                        if limit in completed_on_fallback:
                            report = _pull(restored)
                        elif limit == "invalid B":
                            with self.assertRaisesRegex(ValueError, "requires timezone"):
                                _pull(restored)
                            self.assertFalse(pointer.exists())
                        else:
                            with self.assertRaises(FeedBudgetReached):
                                _pull(restored)
                            self.assertFalse(pointer.exists())
                    expected = [(remote.a, "index.json"), (remote.a, second), (remote.b, "index.json"), (remote.b, first)]
                    if limit in (*completed_on_fallback, "invalid B"):
                        expected.append((remote.b, second))
                    self.assertEqual(remote.requests, expected)
                    self.assertEqual(remote.ref_calls, 2)
                    if limit == "invalid B":
                        continue
                    if limit not in completed_on_fallback:
                        remote.ref_unavailable = True
                        remote.requests.clear()
                        report = _pull(restored)
                        self.assertEqual(remote.requests, [(remote.b, "index.json"), (remote.b, second)])
                        self.assertEqual(remote.ref_calls, 2)
                    self.assertEqual(report["sourcecraft_commit"], remote.b)
                    self.assertEqual(pointer.read_bytes(), files_b["index.json"])
                    self.assertFalse((restored_cache / "pending.json").exists())

    def test_changed_consumer_feed_and_invalid_checkpoint_discard_pinned_commit(self):
        from collector_feed import FeedBudgetReached
        from sourcecraft_pull import _pull
        from tests.test_collector_feed import FeedTests
        a, objects = FeedTests().capture()
        first = "2026-11-01T00:00:00+00:00"
        later = "2026-11-12T00:00:00+00:00"
        for change in ("baseline", "malformed marker", "invalid commit", "invalid baseline", "invalid format"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                original = sourcecraft_files([dict(a, observed_at=first)], objects)
                updated = sourcecraft_files([dict(a, observed_at=later)], objects)
                remote = SourceCraftRemote(original, updated)
                with patch("sourcecraft_pull.subprocess.run", side_effect=remote.ls_remote), \
                        patch("sourcecraft_pull.urlopen", side_effect=remote.fetch):
                    with sourcecraft_budget(2), self.assertRaises(FeedBudgetReached):
                        _pull(root)
                    fetched_object = next(name for commit, name in remote.requests if name.startswith("objects/"))
                    marker = root / ".sourcecraft-import/pending.json"
                    if change == "baseline":
                        sync.write(root / "state/collector/feed.json",
                                   sync.encode({"format": 1, "manifests": [], "latest": {}}))
                    elif change == "malformed marker":
                        marker.write_bytes(b"{")
                    else:
                        fields = {"invalid commit": {"commit": "bad"}, "invalid baseline": {"baseline": "bad"},
                                  "invalid format": {"format": 2}}
                        marker.write_bytes(sync.encode(dict({"commit": remote.a, "baseline": None, "format": 1}, **fields[change])))
                    remote.head = remote.b
                    remote.requests.clear()
                    self.assertEqual(_pull(root)["sourcecraft_commit"], remote.b)
                    self.assertTrue(remote.requests)
                    self.assertTrue(all(commit == remote.b for commit, _ in remote.requests))
                    self.assertNotIn((remote.b, fetched_object), remote.requests)
                    self.assertEqual(remote.ref_calls, 2)
                from collector_snapshots import observation_path
                receipt = root / "state" / observation_path("sourcecraft", a["environment"], a["content_id"])
                self.assertEqual(receipt.read_text("ascii").strip(), later)

    def test_checkpoint_baseline_hash_matches_exact_consumer_feed_bytes(self):
        from collector_feed import FeedBudgetReached
        from sourcecraft_pull import _pull
        from tests.test_collector_feed import FeedTests
        a, objects = FeedTests().capture()
        files = sourcecraft_files([a], objects)
        remote = SourceCraftRemote(files, files)
        with tempfile.TemporaryDirectory() as tmp, \
                patch("sourcecraft_pull.subprocess.run", side_effect=remote.ls_remote), \
                patch("sourcecraft_pull.urlopen", side_effect=remote.fetch):
            root = Path(tmp)
            pointer = root / "state/collector/feed.json"
            confirmed = sync.encode({"format": 1, "manifests": [], "latest": {}})
            sync.write(pointer, confirmed)
            with sourcecraft_budget(2), self.assertRaises(FeedBudgetReached):
                _pull(root)
            marker = json.loads((root / ".sourcecraft-import/pending.json").read_bytes())
            self.assertEqual(marker["baseline"], sync.sha(confirmed))
            self.assertEqual(pointer.read_bytes(), confirmed)
            manifest = next(name for _, name in remote.requests if name.startswith("collector/"))
            saved_object = next(name for _, name in remote.requests if name.startswith("objects/"))

            pointer.write_bytes(confirmed + b" ")
            remote.requests.clear()
            self.assertEqual(_pull(root)["sourcecraft_commit"], remote.a)
            self.assertEqual(remote.ref_calls, 2)
            self.assertIn((remote.a, manifest), remote.requests)
            self.assertNotIn((remote.a, saved_object), remote.requests)
            self.assertEqual(pointer.read_bytes(), files["index.json"])


class SourceTransportTests(unittest.TestCase):
    def test_request_keeps_escaped_octets_and_encodes_raw_text(self):
        from types import SimpleNamespace
        path = '/specs_sedo/Volume%2Fpart%C0/%d0%b0 смета.pdf'
        encoded = '/specs_sedo/Volume%2Fpart%C0/%d0%b0%20%D1%81%D0%BC%D0%B5%D1%82%D0%B0.pdf'
        base = 'https://lk.sfr.gov.ru'
        def open_file(request, **kwargs):
            self.assertEqual(request.full_url, base + encoded)
            file = io.BytesIO(b'document')
            file.geturl = lambda: base + '/specs_sedo/moved%2Ffile.pdf'
            return file
        with patch.object(sync, 'build_opener', return_value=SimpleNamespace(open=open_file)):
            result = sync._get(base + path)
            self.assertEqual(result.final_url, base + '/specs_sedo/moved%2Ffile.pdf')
        with self.assertRaises(ValueError):
            sync.SourceRedirect(base + path).redirect_request(Request(base), None, 302, '', {},
                base + '/specs_sedo/%2e%2e/private.pdf')

    def response(self, body, headers=b''):
        from http.client import HTTPResponse
        from types import SimpleNamespace
        response = HTTPResponse(SimpleNamespace(makefile=lambda *args: io.BytesIO(
            b'HTTP/1.1 200 OK\r\n' + headers + b'\r\n' + body)))
        response.begin()
        response.geturl = lambda: sync.SITES['prod']
        return response

    def fetch_response(self, body, headers):
        from types import SimpleNamespace
        opener = SimpleNamespace(open=lambda *args, **kwargs: self.response(body, headers))
        with patch.object(sync, 'build_opener', return_value=opener):
            return sync._get(sync.SITES['prod'], attempts=1)

    def test_content_length_short_read_is_rejected(self):
        with self.assertRaisesRegex(OSError, 'length|Length|обрыв'):
            self.fetch_response(b'%PDF-short', b'Content-Length: 120\r\n')

    def test_complete_and_unframed_responses_remain_supported(self):
        for headers in (b'Content-Length: 10\r\n', b''):
            with self.subTest(headers=headers):
                self.assertEqual(self.fetch_response(b'%PDF-short', headers), b'%PDF-short')

    def test_chunked_response_uses_transfer_framing_instead_of_content_length(self):
        self.assertEqual(self.fetch_response(b'3\r\nabc\r\n0\r\n\r\n',
            b'Transfer-Encoding: chunked\r\nContent-Length: 120\r\n'), b'abc')

    def test_short_read_retries_keep_existing_attempt_limit(self):
        from types import SimpleNamespace
        responses = [self.response(b'bad', b'Content-Length: 10\r\n'),
                     self.response(b'ok', b'Content-Length: 2\r\n')]
        with patch.object(sync, 'build_opener', return_value=SimpleNamespace(open=lambda *a, **k: responses.pop(0))), patch.object(sync.time, 'sleep'):
            self.assertEqual(sync._get(sync.SITES['prod'], attempts=2), b'ok')
        self.assertEqual(responses, [])

    def test_short_catalog_response_cannot_replace_confirmed_snapshot(self):
        import json
        from types import SimpleNamespace
        from tests.test_source_collection_regressions import SourceCollectionTests
        fixture = SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.collect(1)
        previous = {name: (fixture.state / name).read_bytes() for name in ('current.json', 'history.json')}
        def open_resource(request, **kwargs):
            url = request.full_url
            raw = fixture.resources[url]
            length = len(raw) + (120 if url == sync.SITES['prod'] else 0)
            response = self.response(raw, f'Content-Length: {length}\r\n'.encode())
            response.geturl = lambda: url
            return response
        with patch.object(sync, 'build_opener', return_value=SimpleNamespace(open=open_resource)), patch.object(sync.time, 'sleep'):
            with self.assertRaises(OSError):
                sync.sync(fixture.state)
        for name, content in previous.items():
            self.assertEqual((fixture.state / name).read_bytes(), content)
        report = json.loads((fixture.state / 'last-run.json').read_bytes())
        self.assertTrue(report['errors'])

    def test_redirect_checks_each_destination_before_request(self):
        seed = 'https://ecp.sfr.gov.ru/sedo/soap/SedoGateway?wsdl'
        handler = sync.SourceRedirect(seed)
        allowed = 'https://ecp.sfr.gov.ru/sedo/soap/types.xsd'
        self.assertEqual(handler.redirect_request(Request(seed), None, 302, '', {}, allowed).full_url, allowed)
        response = io.BytesIO(b"redirect")
        handler.redirect_request(Request(seed), response, 302, '', {}, allowed)
        self.assertEqual(response.read(), b"")
        explicit_port = 'https://ecp.sfr.gov.ru:443/sedo/soap/types.xsd'
        self.assertEqual(handler.redirect_request(Request(seed), None, 302, '', {}, explicit_port).full_url, explicit_port)
        for url in ['http://ecp.sfr.gov.ru/sedo/soap/types.xsd',
                    'https://ecp-test.sfr.gov.ru/sedo/soap/types.xsd',
                    'https://ecp.sfr.gov.ru:444/sedo/soap/types.xsd',
                    'https://ecp.sfr.gov.ru/other/types.xsd',
                    'https://ecp.sfr.gov.ru/sedo/soap/%2e%2e/secret.xsd']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler.redirect_request(Request(seed), None, 302, '', {}, url)

    def test_wsdl_transport_keeps_service_boundary_on_redirect_and_response(self):
        from types import SimpleNamespace
        seed = 'https://ecp.sfr.gov.ru/sedo/soap/SedoGateway?wsdl'
        original = 'https://ecp.sfr.gov.ru/sedo/soap/v1/main.xsd'
        allowed = 'https://ecp.sfr.gov.ru/sedo/soap/v2/main.xsd'
        outside = ['https://ecp.sfr.gov.ru/sedo/elsewhere/main.xsd',
                   'https://ecp-test.sfr.gov.ru/sedo/soap/v2/main.xsd']
        for final in [allowed, *outside]:
            for check_redirect in (False, True):
                with self.subTest(final=final, check_redirect=check_redirect):
                    def opener(redirect, transport):
                        def open_resource(request, **kwargs):
                            if check_redirect:
                                redirect.redirect_request(request, None, 302, '', {}, final)
                            response = self.response(b'<schema/>')
                            response.geturl = lambda: final
                            return response
                        return SimpleNamespace(open=open_resource)

                    with patch.object(sync, 'build_opener', side_effect=opener):
                        if final == allowed:
                            result = sync._get(original, attempts=1, wsdl_seed=seed)
                            self.assertEqual(result, b'<schema/>')
                            self.assertEqual(result.final_url, final)
                        else:
                            with self.assertRaisesRegex(ValueError, 'outside approved'):
                                sync._get(original, attempts=1, wsdl_seed=seed)

    def test_catalog_policy_rejects_scheme_port_and_escape(self):
        seed = sync.SITES['prod']
        sync.source_url('https://lk.sfr.gov.ru/specs_sedo/next.zip', seed)
        for url in ['http://lk.sfr.gov.ru/specs_sedo/next.zip',
                    'https://lk.sfr.gov.ru:8443/specs_sedo/next.zip',
                    'https://lk.sfr.gov.ru/specs_sedo/%2e%2e/private.zip',
                    'https://user@lk.sfr.gov.ru/specs_sedo/next.zip']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                sync.source_url(url, seed)

    def test_archive_budget_checked_before_reading_entries(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('one.xsd', b'abc')
            archive.writestr('two.xsd', b'def')
        with patch.object(sync, 'MAX_ARCHIVE_BYTES', 5), patch.object(zipfile.ZipFile, 'read') as read:
            with self.assertRaisesRegex(ValueError, 'archive'):
                sync.inspect_file(output.getvalue(), 'zip')
            read.assert_not_called()
        with patch.object(sync, 'MAX_ARCHIVE_FILES', 1), self.assertRaises(ValueError):
            sync.inspect_file(output.getvalue(), 'zip')

    def test_duplicate_names_cannot_repeat_the_last_large_entry(self):
        import warnings
        output = io.BytesIO()
        with warnings.catch_warnings(), zipfile.ZipFile(output, 'w') as archive:
            warnings.simplefilter('ignore', UserWarning)
            archive.writestr('same.xsd', b'')
            archive.writestr('same.xsd', b'')
            archive.writestr('same.xsd', b'<schema/>')
        with patch.object(zipfile.ZipFile, 'read', return_value=b'<schema/>') as read:
            with self.assertRaisesRegex(ValueError, '[Dd]uplicate'):
                sync.inspect_file(output.getvalue(), 'zip')
            read.assert_not_called()

    def test_url_kind_is_independent_of_windows_unc_paths(self):
        self.assertEqual(sync.source_kind('https://lk.fss.ru//mchd.html'), 'html')

    def test_rejected_initial_source_cannot_open_connection(self):
        with patch.object(sync, 'build_opener') as opener:
            with self.assertRaises(ValueError):
                sync._get('https://elsewhere.test/specs_sedo/schema.zip')
            opener.assert_not_called()
