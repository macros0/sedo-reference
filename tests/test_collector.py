import json
import tempfile
import unittest
from pathlib import Path

from sync import encode, sha
from tests import test_wsdl_sources as fixtures
from collector_snapshots import make_manifest, validate_manifest, load_library
from collector import publish_inputs


class CollectorTests(unittest.TestCase):
    def test_legacy_home_without_receipt_migrates_same_and_replaced_content(self):
        from unittest.mock import patch
        from collector import store_local_inputs
        from collector_snapshots import feed_manifests, observation_path
        from sync import write

        old, old_objects = self.capture(9, '2026-11-16T01:00:00+00:00')
        new, new_objects = self.capture(10, '2026-11-16T04:00:00+00:00')
        identity = old['content_id']
        old_receipt = observation_path('home', 'test', identity)
        for incoming, objects in ((dict(old, observed_at=new['observed_at']), old_objects), (new, new_objects)):
            for marker in (False, True):
                with self.subTest(same=incoming['content_id'] == identity, marker=marker), \
                        tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', SEDO_COLLECTOR_SOURCE='home'):
                    state = Path(tmp)
                    folder = state / 'collector/test'
                    write(folder / 'current', (identity + '\n').encode())
                    original = encode(old).replace(b'\n', b'\r\n')
                    write(folder / (identity + '.json'), original)
                    if marker:
                        write(folder / 'current-observed-at', (identity + '\n2026-11-16T03:00:00+00:00\n').encode())
                    for digest, raw in old_objects.items():
                        write(state / 'objects' / digest, raw)
                    store_local_inputs(state, [incoming], objects)
                    self.assertEqual(load_library(state, 'test'), incoming['library'])
                    self.assertEqual((folder / (identity + '.json')).read_bytes(), original)
                    self.assertEqual((state / old_receipt).read_text().strip(), old['observed_at'])
                    self.assertEqual(feed_manifests(state), [old] if incoming['content_id'] == identity else [old, new])

    def test_legacy_home_shared_envelope_requires_confirmed_home_first_observation(self):
        from unittest.mock import patch
        from collector import store_local_inputs
        from collector_snapshots import feed_manifests, observation_path
        from sync import write

        shared, objects = self.capture(9, '2026-11-16T01:00:00+00:00')
        prod, prod_objects = self.capture(10, '2026-11-16T01:00:00+00:00', environment='prod')
        returned = dict(shared, observed_at='2026-11-16T04:00:00+00:00')
        identity = shared['content_id']
        stamp = '2026-11-16T02:00:00+00:00'
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', SEDO_COLLECTOR_SOURCE='home'):
            state = Path(tmp)
            folder = state / 'collector/test'
            write(folder / 'current', (identity + '\n').encode())
            write(folder / (identity + '.json'), encode(shared))
            write(folder / 'current-observed-at', (identity + '\n2026-11-16T03:00:00+00:00\n').encode())
            write(state / observation_path('sourcecraft', 'test', identity), (shared['observed_at'] + '\n').encode())
            for digest, raw in objects.items():
                write(state / 'objects' / digest, raw)
            with self.assertRaisesRegex(ValueError, 'Ambiguous legacy Home first observation'):
                store_local_inputs(state, [prod, returned], objects | prod_objects)
            self.assertFalse((state / 'collector/prod/current.json').exists())
            self.assertFalse((folder / 'current.json').exists())
            self.assertEqual(feed_manifests(state), [])

            write(state / observation_path('home', 'test', identity), (stamp + '\n').encode())
            store_local_inputs(state, [prod, returned], objects | prod_objects)
            self.assertEqual(json.loads((folder / 'current.json').read_bytes())['capture_started_at'], returned['observed_at'])
            self.assertEqual(load_library(state, 'prod'), prod['library'])
            self.assertEqual(feed_manifests(state), [prod, dict(shared, observed_at=stamp)])

    def test_git_migrates_legacy_receipt_with_original_date_after_cas_conflict(self):
        from collector import git_blob_sha
        from collector_snapshots import observation_path

        old, old_objects = self.capture(9, '2026-11-16T01:00:00+00:00')
        incoming = dict(old, observed_at='2026-11-16T03:00:00+00:00')
        prod, prod_objects = self.capture(10, '2026-11-16T02:00:00+00:00', environment='prod')
        api = AtomicGitAPI()
        identity = old['content_id']
        prefix = 'state/collector/test/'
        for path, raw in {prefix + 'current': (identity + '\n').encode(),
                          prefix + identity + '.json': encode(old),
                          **{'state/objects/' + digest: raw for digest, raw in old_objects.items()}}.items():
            blob = git_blob_sha(raw)
            api.blobs[blob] = raw
            api.trees['empty'][path] = blob
        api.before_update = lambda: publish_inputs(api, [prod], prod_objects)
        self.assertTrue(publish_inputs(api, [incoming], old_objects))
        self.assertEqual(api.conflicts, 1)
        self.assertEqual(json.loads(api.read(prefix + 'current.json'))['capture_started_at'], incoming['observed_at'])
        self.assertEqual(api.read(prefix + identity + '.json'), encode(old))
        receipt = 'state/' + observation_path('home', 'test', identity)
        self.assertEqual(api.read(receipt).decode().strip(), old['observed_at'])
        self.assertEqual(json.loads(api.read('state/collector/prod/current.json'))['content_id'], prod['content_id'])

    def test_same_id_recapture_repairs_incomplete_legacy_home_with_confirmed_receipt(self):
        from unittest.mock import patch
        from collector import git_blob_sha, store_local_inputs
        from collector_snapshots import feed_manifests, observation_path
        from sync import write

        old, objects = self.capture(9, '2026-11-16T01:00:00+00:00')
        returned = dict(old, observed_at='2026-11-16T04:00:00+00:00')
        other, other_objects = self.capture(10, returned['observed_at'])
        identity = old['content_id']
        prefix = 'collector/test/'
        envelope = prefix + identity + '.json'
        archive = 'objects/' + old['library']['sha256']
        receipt = observation_path('home', 'test', identity)
        files = {prefix + 'current': (identity + '\r\n').encode(),
                 envelope: encode(old), receipt: (old['observed_at'] + '\n').encode(),
                 **{'objects/' + digest: raw for digest, raw in objects.items()}}

        for publish in (False, True):
            for damaged in (envelope, archive):
                for missing in (False, True):
                    with self.subTest(publish=publish, damaged=damaged, missing=missing), \
                            tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', SEDO_COLLECTOR_SOURCE='home'):
                        state = Path(tmp)
                        api = AtomicGitAPI()
                        for name, raw in files.items():
                            if name == damaged:
                                if missing:
                                    continue
                                raw = b'partial write'
                            if publish:
                                blob = git_blob_sha(raw)
                                api.blobs[blob] = raw
                                api.trees['empty']['state/' + name] = blob
                            else:
                                write(state / name, raw)

                        save = (lambda manifest, blobs: publish_inputs(api, [manifest], blobs)) if publish else (
                            lambda manifest, blobs: store_local_inputs(state, [manifest], blobs))
                        read = (lambda name: api.read('state/' + name)) if publish else (
                            lambda name: (state / name).read_bytes())
                        with self.assertRaisesRegex(ValueError, 'Cannot validate legacy Home manifest'):
                            save(other, other_objects)
                        with self.assertRaises(KeyError if publish else FileNotFoundError):
                            read(prefix + 'current.json')
                        self.assertEqual(read(prefix + 'current'), files[prefix + 'current'])
                        save(returned, objects)
                        self.assertEqual(read(prefix + 'current'), files[prefix + 'current'])
                        self.assertEqual(read(receipt), files[receipt])
                        self.assertEqual(read(envelope), files[envelope])
                        self.assertEqual(read(archive), files[archive])
                        self.assertEqual(json.loads(read(prefix + 'current.json')),
                                         dict(format=1, content_id=identity,
                                              capture_started_at=returned['observed_at']))
                        if not publish:
                            self.assertEqual(load_library(state, 'test'), old['library'])
                            self.assertEqual(feed_manifests(state), [old])

    def test_interrupted_legacy_switch_preserves_receipt_and_validated_active_content(self):
        from unittest.mock import patch
        from collector import store_local_inputs
        from collector_snapshots import feed_manifests, observation_path
        from sync import write

        old, old_objects = self.capture(9, '2026-11-16T01:00:00+00:00')
        new, new_objects = self.capture(10, '2026-11-16T04:00:00+00:00')
        identity = old['content_id']
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', SEDO_COLLECTOR_SOURCE='home'):
            state = Path(tmp)
            folder = state / 'collector/test'
            pointer = folder / 'current.json'
            write(folder / 'current', (identity + '\n').encode())
            write(folder / (identity + '.json'), encode(old))
            for digest, raw in old_objects.items():
                write(state / 'objects' / digest, raw)
            archive = state / 'objects' / old['library']['sha256']
            archive.write_bytes(b'incomplete archive')
            with self.assertRaisesRegex(ValueError, 'Cannot validate legacy Home manifest'):
                store_local_inputs(state, [new], new_objects)
            self.assertFalse(pointer.exists())
            self.assertFalse((folder / (new['content_id'] + '.json')).exists())
            archive.write_bytes(old_objects[archive.name])

            with patch('collector_feed.atomic_write', side_effect=OSError('interrupted')), \
                    self.assertRaisesRegex(OSError, 'interrupted'):
                store_local_inputs(state, [new], new_objects)
            self.assertFalse(pointer.exists())
            self.assertEqual(load_library(state, 'test'), old['library'])
            receipt = state / observation_path('home', 'test', identity)
            self.assertEqual(receipt.read_text().strip(), old['observed_at'])
            store_local_inputs(state, [new], new_objects)
            self.assertEqual(load_library(state, 'test'), new['library'])
            self.assertEqual(feed_manifests(state), [old, new])

    def test_home_switch_is_one_commit_and_older_capture_can_follow_a_failed_switch(self):
        from unittest.mock import patch
        from collector import store_local_inputs
        from collector_snapshots import feed_manifests
        from collector_feed import atomic_write

        first, first_objects = self.capture(9, "2026-11-16T00:00:00+00:00")
        later, later_objects = self.capture(10, "2026-11-16T00:00:01+00:00")
        latest, latest_objects = self.capture(11, "2026-11-16T00:00:02+00:00")
        for initial in (False, True):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as tmp, \
                    patch.dict("os.environ", SEDO_COLLECTOR_SOURCE="home"):
                state = Path(tmp)
                folder = state / "collector/test"
                pointer = folder / "current.json"
                if initial:
                    store_local_inputs(state, [first], first_objects)

                def interrupt(path, raw):
                    if path == pointer:
                        raise OSError("pointer replacement interrupted")
                    atomic_write(path, raw)

                with patch("collector_feed.atomic_write", side_effect=interrupt), self.assertRaisesRegex(OSError, "interrupted"):
                    store_local_inputs(state, [latest], latest_objects)
                if initial:
                    self.assertEqual(load_library(state, "test"), first["library"])
                else:
                    with self.assertRaisesRegex(ValueError, "no confirmed pointer"):
                        load_library(state, "test")
                store_local_inputs(state, [later], later_objects)
                self.assertEqual(load_library(state, "test"), later["library"])
                store_local_inputs(state, [latest], latest_objects)
                store_local_inputs(state, [later], later_objects)
                self.assertEqual(load_library(state, "test"), latest["library"])
                self.assertEqual(feed_manifests(state), ([first] if initial else []) + [later, latest])
                self.assertFalse((folder / "current-observed-at").exists())
                self.assertFalse((folder / "current").exists())

    def test_home_migrates_a_split_pointer_and_ignores_later_legacy_writes(self):
        from unittest.mock import patch
        from collector import store_local_inputs
        from collector_snapshots import observation_path
        from sync import write

        first, first_objects = self.capture(9, "2026-11-16T00:00:00+00:00")
        late, late_objects = self.capture(10, "2026-11-16T00:00:01+00:00")
        latest, latest_objects = self.capture(11, "2026-11-16T00:00:03+00:00")
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", SEDO_COLLECTOR_SOURCE="home"):
            state = Path(tmp)
            folder = state / "collector/test"
            digest = first["content_id"]
            legacy = folder / "current"
            marker = folder / "current-observed-at"
            for key, raw in first_objects.items():
                write(state / "objects" / key, raw)
            write(folder / (digest + ".json"), encode(first))
            write(state / observation_path("home", "test", digest), (first["observed_at"] + "\n").encode())
            write(legacy, (digest + "\r\n").encode())
            write(marker, (latest["content_id"] + "\n2026-11-16T00:00:02+00:00\n").encode())
            self.assertEqual(load_library(state, "test"), first["library"])
            store_local_inputs(state, [late], late_objects)
            self.assertEqual(load_library(state, "test"), first["library"])
            store_local_inputs(state, [latest], latest_objects)
            self.assertEqual(json.loads((folder / "current.json").read_bytes()),
                             dict(format=1, content_id=latest["content_id"], capture_started_at=latest["observed_at"]))
            self.assertEqual(legacy.read_text().strip(), digest)
            write(legacy, (late["content_id"] + "\n").encode())
            write(marker, b"obsolete marker")
            self.assertEqual(load_library(state, "test"), latest["library"])
            store_local_inputs(state, [late], late_objects)
            self.assertEqual(load_library(state, "test"), latest["library"])

            # A corrupt authoritative record must not reactivate a legacy pointer.
            write(folder / "current.json", b'{"content_id":')
            with self.assertRaisesRegex(ValueError, "Invalid Home current.json"):
                load_library(state, "test")

    def test_home_publisher_and_local_batch_reject_oversize_before_any_write(self):
        from unittest.mock import Mock, patch
        import collector
        from wsdl_sources import collect
        fixture = fixtures.WsdlSourcesTests()
        test, objects = self.capture()
        def prod(value):
            return value.replace('ecp-test.', 'ecp.')
        resources = {prod(url): prod(raw.decode()).encode() for url, raw in fixture.bundle().items()}
        def blob(raw):
            objects[sha(raw)] = raw
            return sha(raw)
        production = make_manifest(collect(prod(fixtures.SEED), 'prod', resources.__getitem__, blob), test['observed_at'])
        size = len(encode(test))
        self.assertLess(len(encode(production)), size)
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp)
            api = Mock(wraps=FakeAPI())
            with patch('collector_feed.MAX_MANIFEST', size - 1):
                with self.assertRaisesRegex(ValueError, 'size limit'):
                    publish_inputs(api, [production, test], objects)
                api.assert_not_called()
                with patch('sys.argv', ['collector.py', '--capture-to', tmp]), patch('collector.capture',
                        side_effect=[(production, objects), (test, objects)]), self.assertRaisesRegex(ValueError, 'size limit'):
                    collector.main()
                self.assertEqual([p.name for p in local.iterdir() if p.name != ".update.lock"], [])
            with patch('collector_feed.MAX_MANIFEST', size):
                self.assertTrue(publish_inputs(api, [production, test], objects))
                with patch('sys.argv', ['collector.py', '--capture-to', tmp]), patch('collector.capture',
                        side_effect=[(production, objects), (test, objects)]):
                    collector.main()
                with patch.dict('os.environ', SEDO_COLLECTOR_SOURCE='home'):
                    self.assertEqual(load_library(local, 'test'), test['library'])

    def test_active_and_historical_readers_check_serialized_bytes(self):
        from unittest.mock import patch
        from collector_feed import import_feed, manifest_path, store_feed
        from collector_snapshots import feed_manifests
        manifest, objects = self.capture()
        raw = encode(manifest)
        with tempfile.TemporaryDirectory() as tmp:
            local, remote = Path(tmp)/'local', Path(tmp)/'remote'
            store_feed(remote, [manifest], objects)
            import_feed(local, lambda name: (remote/name).read_bytes())
            name = manifest_path('test', manifest['content_id'])
            with patch('collector_feed.MAX_MANIFEST', len(raw)):
                self.assertEqual(load_library(local,'test'), manifest['library'])
                self.assertEqual(feed_manifests(local), [manifest])
                (local/name).write_bytes(raw+b' ')
                for read in [lambda:load_library(local,'test'), lambda:feed_manifests(local)]:
                    with self.assertRaisesRegex(ValueError, 'size limit'):
                        read()

    def test_documented_installations_replay_both_archived_manifests_in_isolation(self):
        import re
        import shutil
        import subprocess
        import sys
        project = Path(__file__).resolve().parents[1]
        instructions = [
            (project / "docs/russian-collector.md").read_text("utf8").split("В `/opt/sedo-collector`", 1)[1].split("\n", 1)[0],
            (project / "docs/sourcecraft-collector.md").read_text("utf8").split("Общие модули ", 1)[1].split("Пользовательские", 1)[0],
        ]
        for installation in instructions:
            with self.subTest(installation=installation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for name in set(re.findall(r"\b\w+\.py\b", installation)):
                    shutil.copy2(project / name, root / name)
                manifests = list((project / "state/collector/prod").glob("*.json")) + list((project / "state/collector/test").glob("*.json"))
                self.assertEqual({json.loads(path.read_bytes())["environment"] for path in manifests}, {"prod", "test"})
                for path in manifests:
                    manifest = json.loads(path.read_bytes())
                    target = root / "state/collector" / manifest["environment"] / path.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    (root / "state/objects").mkdir(exist_ok=True)
                    for digest in [manifest["library"]["sha256"], *(r["sha256"] for r in manifest["library"]["source_files"])]:
                        shutil.copy2(project / "state/objects" / digest, root / "state/objects" / digest)
                code = """import sys, pathlib, json, socket, ssl
sys.path.insert(0, str(pathlib.Path.cwd()))
socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network forbidden'))
from collector_snapshots import validate_manifest
from sync import encode, sha
for path in sorted(pathlib.Path('state/collector').glob('*/*.json')):
    manifest = json.loads(path.read_bytes())
    library = validate_manifest(manifest, lambda h: pathlib.Path('state/objects', h).read_bytes())
    assert sha(encode(library)) == manifest['content_id']
    print(manifest['environment'], manifest['content_id'])
"""
                result = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                for path in manifests:
                    self.assertIn(path.stem, result.stdout)

    def capture(self, value=10, observed_at="2026-11-13T00:00:00+00:00", *, environment='test'):
        fixture = fixtures.WsdlSourcesTests()
        bundle = {url: raw.replace(b'value="10"', f'value="{value}"'.encode("ascii"))
                  for url, raw in fixture.bundle().items()}
        if environment == 'prod':
            from wsdl_sources import collect
            resources = {url.replace('ecp-test.', 'ecp.'): raw.replace(b'ecp-test.', b'ecp.')
                         for url, raw in bundle.items()}
            objects = {}
            def blob(raw):
                digest = sha(raw)
                objects[digest] = raw
                return digest
            row = collect(fixtures.SEED.replace('ecp-test.', 'ecp.'), environment, resources.__getitem__, blob)
        else:
            row, objects = fixture.collect(bundle)
        return make_manifest(row, observed_at), objects

    def test_home_capture_promotes_each_contour_by_start_time(self):
        from datetime import datetime
        from unittest.mock import patch
        from collector import main
        from collector_snapshots import observation_path
        from sync import SITES
        from wsdl_sources import collect

        for publish in (False, True):
            with self.subTest(publish=publish), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                api = AtomicGitAPI()
                token = root / "token"
                token.write_text("github_pat_fixture")
                args = ["collector.py"] + (["--token-file", str(token)] if publish else ["--capture-to", tmp])
                bundles = {}
                for env in SITES:
                    host = "ecp." if env == "prod" else "ecp-test."
                    for version in (10, 11):
                        def convert(value):
                            return value.replace("ecp-test.", host).replace('value="10"', f'value="{version}"')
                        bundles[env, version] = {convert(url): convert(raw.decode()).encode()
                            for url, raw in fixtures.WsdlSourcesTests().bundle().items()}
                        bundles[env, version][SITES[env]] = f'<a href="{convert(fixtures.SEED)}">WSDL</a>'.encode()
                values = {}
                first_stamp = "2026-11-16T01:00:00+00:00"
                second_stamp = "2026-11-16T01:00:01+00:00"
                third_stamp = "2026-11-16T01:00:02+00:00"
                stamp = first_stamp

                class CaptureClock(datetime):
                    @classmethod
                    def now(cls, tz):
                        return datetime.fromisoformat(stamp)

                def read(path):
                    return api.read("state/" + path) if publish else (root / path).read_bytes()

                def fetch(url, **kwargs):
                    nonlocal stamp
                    env = "test" if url == SITES["test"] or "ecp-test." in url else "prod"
                    version = values[env]
                    if url == SITES["test"] and stamp == first_stamp:
                        stamp = second_stamp
                        values.update(prod=11, test=11)
                        main()
                        values.update(prod=10, test=10)
                    return bundles[env, version][url]

                with patch("sys.argv", args), patch("collector.datetime", CaptureClock), \
                        patch("collector.get", side_effect=fetch), patch("collector.GitHub", return_value=api):
                    values.update(prod=10, test=10)
                    main()
                    current = {env: json.loads(read(f"collector/{env}/current.json"))["content_id"] for env in SITES}
                    originals = {}
                    for env in SITES:
                        seed = fixtures.SEED.replace("ecp-test.", "ecp.") if env == "prod" else fixtures.SEED
                        identities = [sha(encode(collect(seed, env, bundles[env, v].__getitem__, sha)))
                                      for v in (10, 11)]
                        for expected, identity in zip((first_stamp, second_stamp), identities):
                            self.assertEqual(read(observation_path("home", env, identity)).decode().strip(), expected)
                        self.assertEqual(current[env], identities[1])
                        originals[env] = identities[0]
                    stamp = third_stamp
                    args.extend(["--environment", "prod"])
                    main()
                    self.assertEqual(json.loads(read("collector/prod/current.json"))["content_id"], originals["prod"])
                    self.assertEqual(json.loads(read("collector/test/current.json"))["content_id"], current["test"])
                    self.assertEqual(read(observation_path("home", "prod", originals["prod"])),
                                     (first_stamp + "\n").encode())

    def test_git_retry_rechecks_recency_against_the_cas_winner(self):
        old, old_objects = self.capture(10, "2026-11-16T00:00:00+00:00")
        new, new_objects = self.capture(11, "2026-11-16T00:00:01+00:00")
        for stale_first in (False, True):
            with self.subTest(stale_first=stale_first):
                api = AtomicGitAPI()
                first, second = ((old, old_objects), (new, new_objects)) if stale_first else (
                    (new, new_objects), (old, old_objects))
                api.before_update = lambda: publish_inputs(api, [second[0]], second[1])
                self.assertTrue(publish_inputs(api, [first[0]], first[1]))
                self.assertEqual(api.conflicts, 1)
                self.assertEqual(json.loads(api.read("state/collector/test/current.json"))["content_id"], new["content_id"])
                for manifest in (old, new):
                    archived = f"state/collector/test/{manifest['content_id']}.json"
                    self.assertEqual(json.loads(api.read(archived)), manifest)

    def test_git_migration_retries_on_legacy_update_and_publishes_one_pointer(self):
        import base64

        initial, initial_objects = self.capture(9, "2026-11-16T00:00:00+00:00")
        legacy, legacy_objects = self.capture(10, "2026-11-16T00:00:01+00:00")
        incoming, incoming_objects = self.capture(11, "2026-11-16T00:00:02+00:00")
        api = AtomicGitAPI()
        prefix = "state/collector/test/"

        def legacy_api(method, path, payload=None):
            if method == "POST" and path == "git/trees":
                for entry in payload["tree"]:
                    if entry["path"] == prefix + "current.json":
                        digest = json.loads(api.blobs[entry["sha"]])["content_id"]
                        raw = base64.b64encode((digest + "\n").encode("ascii")).decode("ascii")
                        blob = api("POST", "git/blobs", {"content": raw, "encoding": "base64"})
                        entry.update(path=prefix + "current", sha=blob["sha"])
            return api(method, path, payload)

        self.assertTrue(publish_inputs(legacy_api, [initial], initial_objects))
        api.before_update = lambda: publish_inputs(legacy_api, [legacy], legacy_objects)
        self.assertTrue(publish_inputs(api, [incoming], incoming_objects))
        self.assertEqual(api.conflicts, 1)
        self.assertEqual(api.read(prefix + "current").decode().strip(), legacy["content_id"])
        with self.assertRaises(KeyError):
            api.read(prefix + "current.json")
        self.assertEqual(json.loads(api.read(prefix + incoming["content_id"] + ".json")), incoming)

        fresh, objects = self.capture(11, "2026-11-16T00:00:03+00:00")
        self.assertTrue(publish_inputs(api, [fresh], objects))
        expected = dict(format=1, content_id=fresh["content_id"], capture_started_at=fresh["observed_at"])
        self.assertEqual(json.loads(api.read(prefix + "current.json")), expected)
        self.assertEqual(api.read(prefix + "current").decode().strip(), legacy["content_id"])
        returned, objects = self.capture(9, "2026-11-16T00:00:04+00:00")
        self.assertTrue(publish_inputs(legacy_api, [returned], objects))
        self.assertEqual(json.loads(api.read(prefix + "current.json")), expected)
        with self.assertRaises(KeyError):
            api.read(prefix + "current-observed-at")

    def test_later_confirmation_of_same_content_preserves_pointer_against_stale_capture(self):
        from collector import store_local_inputs
        from collector_snapshots import observation_path

        old, old_objects = self.capture(10, "2026-11-16T00:00:00+00:00")
        in_flight, other_objects = self.capture(11, "2026-11-16T00:00:01+00:00")
        later = dict(old, observed_at="2026-11-16T00:00:02+00:00")
        api = AtomicGitAPI()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            for manifest, objects in ((old, old_objects), (later, old_objects), (in_flight, other_objects)):
                store_local_inputs(state, [manifest], objects)
                self.assertTrue(publish_inputs(api, [manifest], objects))
            receipt = observation_path("home", "test", old["content_id"])
            expected = dict(format=1, content_id=old["content_id"], capture_started_at=later["observed_at"])
            self.assertEqual(json.loads((state / "collector/test/current.json").read_bytes()), expected)
            self.assertEqual(json.loads(api.read("state/collector/test/current.json")), expected)
            paths = [receipt]
            expected = [old["observed_at"]]
            for path, value in zip(paths, expected):
                self.assertEqual((state / path).read_text("ascii").strip(), value)
                self.assertEqual(api.read("state/" + path).decode().strip(), value)
            self.assertFalse(publish_inputs(api, [later], old_objects))

    def test_local_capture_preserves_newer_current_written_by_legacy_home(self):
        from unittest.mock import patch
        from collector import store_local_inputs
        from collector_snapshots import feed_manifests, observation_path
        from sync import write

        initial, initial_objects = self.capture(9, "2026-11-16T00:00:00+00:00")
        earlier, earlier_objects = self.capture(10, "2026-11-16T00:00:01+00:00")
        latest, latest_objects = self.capture(11, "2026-11-16T00:00:02+00:00")
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", SEDO_COLLECTOR_SOURCE="home"):
            state = Path(tmp)
            store_local_inputs(state, [initial], initial_objects)
            folder = state / "collector/test"
            # Simulate the previous split format before its first migration.
            (folder / "current.json").unlink()
            write(folder / "current-observed-at",
                  (initial["content_id"] + "\n" + initial["observed_at"] + "\n").encode())
            identity = latest["content_id"]
            for digest, raw in latest_objects.items():
                write(state / "objects" / digest, raw)
            write(folder / (identity + ".json"), encode(latest))
            write(state / observation_path("home", "test", identity), (latest["observed_at"] + "\n").encode())
            write(folder / "current", (identity + "\n").encode())
            store_local_inputs(state, [earlier], earlier_objects)
            self.assertEqual(load_library(state, "test"), latest["library"])
            self.assertEqual(feed_manifests(state), [initial, earlier, latest])
            fresh = dict(earlier, observed_at="2026-11-16T00:00:03+00:00")
            store_local_inputs(state, [fresh], earlier_objects)
            self.assertEqual(load_library(state, "test"), earlier["library"])
            self.assertEqual(feed_manifests(state), [initial, earlier, latest])

    def test_sourcecraft_legacy_lookup_refuses_unconfirmed_home_manifest(self):
        from unittest.mock import patch
        from collector import store_local_inputs
        from collector_feed import import_feed, store_feed
        from collector_snapshots import feed_manifests, observation_path
        from sync import write

        legacy, original_objects = self.capture()
        home, objects = self.capture(11, "2026-11-16T00:00:00+00:00")
        for with_legacy in (False, True):
            with self.subTest(legacy=with_legacy), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / "local"
                receipt = state / observation_path("home", "test", home["content_id"])
                with patch.dict("os.environ", SEDO_COLLECTOR_SOURCE="sourcecraft"):
                    if with_legacy:
                        write(state / "collector/test" / (legacy["content_id"] + ".json"), encode(legacy))
                        for digest, raw in original_objects.items():
                            write(state / "objects" / digest, raw)
                        self.assertEqual(load_library(state, "test"), legacy["library"])
                    else:
                        self.assertIsNone(load_library(state, "test"))

                    def interrupt(path, raw):
                        if path == receipt:
                            raise OSError("Interrupted Home receipt")
                        write(path, raw)

                    with patch("sync.write", side_effect=interrupt), self.assertRaisesRegex(OSError, "Interrupted"):
                        store_local_inputs(state, [home], objects)
                    self.assertTrue(receipt.with_suffix(".pending-at").is_file())
                    with self.assertRaisesRegex(ValueError, "Missing SourceCraft feed"):
                        load_library(state, "test")
                    with patch.dict("os.environ", SEDO_COLLECTOR_SOURCE="home"):
                        self.assertEqual(feed_manifests(state), [])
                        with self.assertRaisesRegex(ValueError, "no confirmed pointer"):
                            load_library(state, "test")
                        store_local_inputs(state, [dict(home, observed_at="2026-11-17T00:00:00+00:00")], objects)
                        self.assertEqual(feed_manifests(state), [home])
                        self.assertEqual(load_library(state, "test"), home["library"])
                    remote = Path(tmp) / "remote"
                    store_feed(remote, [legacy], original_objects)
                    import_feed(state, lambda path: (remote / path).read_bytes())
                    self.assertEqual(load_library(state, "test"), legacy["library"])
                    self.assertEqual(feed_manifests(state), [legacy])

    def test_home_object_write_failure_preserves_first_observation(self):
        from unittest.mock import patch
        from collector import store_local_inputs
        from collector_snapshots import feed_manifests, observation_path
        from sync import write

        manifest, objects = self.capture(11, "2026-11-16T00:00:00+00:00")
        middle = manifest["library"]["source_files"][1]["sha256"]
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", SEDO_COLLECTOR_SOURCE="home"):
            state = Path(tmp)
            def interrupt(path, raw):
                if path.name == middle:
                    raise OSError("Interrupted Home object")
                write(path, raw)

            with patch("sync.write", side_effect=interrupt), self.assertRaisesRegex(OSError, "Interrupted"):
                store_local_inputs(state, [manifest], objects)
            self.assertEqual(feed_manifests(state), [])
            with self.assertRaisesRegex(ValueError, "no confirmed pointer"):
                load_library(state, "test")
            retry = dict(manifest, observed_at="2026-11-17T00:00:00+00:00")
            store_local_inputs(state, [retry], objects)
            receipt = state / observation_path("home", "test", manifest["content_id"])
            self.assertEqual(receipt.read_bytes(), b"2026-11-16T00:00:00+00:00\n")
            self.assertEqual(feed_manifests(state), [manifest])
            self.assertEqual(load_library(state, "test"), manifest["library"])
            previous = {p: p.read_bytes() for p in state.rglob("*") if p.is_file()}
            store_local_inputs(state, [retry], objects)
            self.assertEqual({p: p.read_bytes() for p in state.rglob("*") if p.is_file()}, previous)

    def test_interrupted_home_manifest_does_not_confirm_receipt_or_redate_retry(self):
        from unittest.mock import patch
        from collector import store_local_inputs
        from collector_feed import manifest_path
        from collector_snapshots import feed_manifests, observation_path
        from sync import write

        a, ao = self.capture()
        b, bo = self.capture(11, "2026-11-14T00:00:00+00:00")
        c, co = self.capture(12, "2026-11-15T00:00:00+00:00")
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", SEDO_COLLECTOR_SOURCE="home"):
            state = Path(tmp)
            pointer = state / "collector/test/current.json"
            missing = state / manifest_path("test", b["content_id"])
            receipt = state / observation_path("home", "test", b["content_id"])
            store_local_inputs(state, [a], ao)

            def interrupt_manifest(path, raw):
                if path == missing:
                    raise OSError("interrupted Home manifest write")
                write(path, raw)

            with patch("sync.write", side_effect=interrupt_manifest), self.assertRaisesRegex(OSError, "interrupted Home"):
                store_local_inputs(state, [b], bo)
            self.assertEqual(json.loads(pointer.read_bytes())["content_id"], a["content_id"])
            store_local_inputs(state, [c], co)
            self.assertEqual(json.loads(pointer.read_bytes())["content_id"], c["content_id"])
            self.assertFalse(receipt.exists())
            self.assertEqual(feed_manifests(state), [a, c])

            sourcecraft = state / observation_path("sourcecraft", "test", b["content_id"])
            sourcecraft_stamp = b"2026-11-12T00:00:00+00:00\n"
            write(sourcecraft, sourcecraft_stamp)
            store_local_inputs(state, [dict(b, observed_at="2026-11-16T00:00:00+00:00")], bo)
            self.assertEqual(json.loads(pointer.read_bytes())["content_id"], b["content_id"])
            self.assertEqual(feed_manifests(state), [a, b, c])
            self.assertEqual(sourcecraft.read_bytes(), sourcecraft_stamp)

    def test_legacy_home_orphan_has_identity_diagnostic_and_recovers_original_date(self):
        import re
        from unittest.mock import patch
        from collector import store_local_inputs
        from collector_feed import manifest_path
        from collector_snapshots import feed_manifests, observation_path
        from sync import write

        a, ao = self.capture()
        b, bo = self.capture(11, "2026-11-14T00:00:00+00:00")
        key = b["content_id"]
        missing = manifest_path("test", key)
        for isolate in (False, True):
            with self.subTest(isolate=isolate), tempfile.TemporaryDirectory() as tmp, \
                    patch.dict("os.environ", SEDO_COLLECTOR_SOURCE="home"):
                state = Path(tmp)
                store_local_inputs(state, [a], ao)
                receipt = state / observation_path("home", "test", key)
                original = b"2026-11-14T00:00:00+00:00\r\n"
                write(receipt, original)
                with self.assertRaisesRegex(ValueError, rf"Missing Home manifest.*test.*{key}.*{re.escape(missing)}"):
                    feed_manifests(state)

                if isolate:
                    preserved = receipt.parent / "orphans" / receipt.name
                    preserved.parent.mkdir()
                    receipt.replace(preserved)
                    self.assertEqual(feed_manifests(state), [a])
                    self.assertEqual(preserved.read_bytes(), original)
                store_local_inputs(state, [dict(b, observed_at="2026-11-16T00:00:00+00:00")], bo)
                self.assertEqual(feed_manifests(state), [a, b])
                self.assertEqual(receipt.read_bytes(), original)

    def test_offline_replay_validates_original_bytes(self):
        manifest, objects = self.capture()
        self.assertEqual(validate_manifest(manifest, objects.__getitem__), manifest["library"])
        broken = dict(objects)
        broken[manifest["library"]["source_files"][0]["sha256"]] += b" "
        with self.assertRaises(ValueError):
            validate_manifest(manifest, broken.__getitem__)

    def test_manifest_deduplicates_time_and_rejects_metadata_and_contour_changes(self):
        manifest, objects = self.capture()
        other = make_manifest(manifest["library"], "2026-11-14T00:00:00+00:00")
        self.assertEqual(manifest["content_id"], other["content_id"])
        for changed in (dict(manifest, environment="prod"),
                        dict(manifest, content_id="0" * 64)):
            with self.assertRaises(ValueError):
                validate_manifest(changed, objects.__getitem__)
        changed = json.loads(json.dumps(manifest))
        changed["library"]["schemas"][0]["path"] = "invented.xsd"
        changed["content_id"] = sha(encode(changed["library"]))
        with self.assertRaises(ValueError):
            validate_manifest(changed, objects.__getitem__)

    def test_load_retains_manifest_and_objects_separately_by_contour(self):
        manifest, objects = self.capture()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "collector/test").mkdir(parents=True)
            (base / "objects").mkdir()
            for digest, raw in objects.items():
                (base / "objects" / digest).write_bytes(raw)
            (base / "collector/test" / (manifest["content_id"] + ".json")).write_bytes(encode(manifest))
            self.assertIsNone(load_library(base, "prod"))
            self.assertEqual(load_library(base, "test"), manifest["library"])

    def test_pages_collector_entry_recovers_before_reading_current_snapshot(self):
        import os
        import shutil
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recovery = root / ".update-recovery"
            previous = recovery / "previous/state"
            (previous / "snapshots").mkdir(parents=True)
            (previous / "current.json").write_bytes(encode({"snapshot": "old"}))
            (previous / "snapshots/old.json").write_bytes(encode({"entries": []}))
            (recovery / "journal.json").write_bytes(encode({"state": True, "data": False, "site": False}))
            project = Path(__file__).resolve().parents[1]
            entry = root / "collector_snapshots.py"
            shutil.copyfile(project / entry.name, entry)
            result = subprocess.run([sys.executable, "-B", str(entry)], cwd=root,
                                    env=dict(os.environ, PYTHONPATH=str(project), SEDO_COLLECTOR_SOURCE="sourcecraft"),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(recovery.exists())
            self.assertTrue((root / "state/snapshots/old.json").is_file())

    def test_publisher_noop_atomic_commit_and_retry_preserve_concurrent_tree(self):
        manifest, objects = self.capture()
        api = FakeAPI()
        self.assertTrue(publish_inputs(api, [manifest], objects))
        self.assertEqual(api.updates, 2)
        self.assertEqual(api.parents, ["head1", "head2"])
        self.assertTrue(all(p.startswith(("state/objects/", "state/collector/test/")) for p in api.uploaded))
        self.assertFalse(publish_inputs(api, [manifest], objects))

    def test_home_publisher_sets_pointer_on_return_to_archived_content(self):
        from tests.test_collector_feed import FeedTests
        api = FakeAPI()
        fixture = FeedTests()
        a, ao = fixture.capture()
        b, bo = fixture.capture(True)
        b["observed_at"] = "2026-11-14T12:00:00+00:00"
        publish_inputs(api, [a], ao)
        publish_inputs(api, [b], bo)
        returned = dict(a, observed_at="2026-11-15T12:00:00+00:00")
        self.assertTrue(publish_inputs(api, [returned], ao))
        name = "state/collector/test/current.json"
        self.assertEqual(json.loads(api.blobs[api.tree[name]])["content_id"], a["content_id"])
        self.assertFalse(publish_inputs(api, [returned], ao))

    def test_home_records_own_time_when_sourcecraft_already_published_the_library(self):
        from collector_snapshots import observation_path
        manifest, objects = self.capture()
        digest = manifest["content_id"]
        env = manifest["environment"]
        api = FakeAPI()
        shared = f"state/collector/{env}/{digest}.json"
        api.committed = True
        api.updates = 2
        api.uploaded.add(shared)
        api.tree[shared] = "original-sourcecraft-envelope"
        api.blobs[api.tree[shared]] = encode(manifest)
        processed = "state/collector/processed.json"
        api.uploaded.add(processed)
        api.tree[processed] = "sourcecraft-ledger"
        api.blobs[api.tree[processed]] = encode({"manifests": {env: {digest: {
            "status": "replayed", "observed_at": manifest["observed_at"]
        }}}})
        home = dict(manifest, observed_at="2026-11-14T19:00:00+00:00")
        self.assertTrue(publish_inputs(api, [home], objects))
        receipt = "state/" + observation_path("home", env, digest)
        self.assertEqual(api.blobs[api.tree[receipt]].decode().strip(), home["observed_at"])
        self.assertEqual(json.loads(api.blobs[api.tree[shared]])["observed_at"], manifest["observed_at"])
        later = dict(home, observed_at="2026-11-15T19:00:00+00:00")
        self.assertTrue(publish_inputs(api, [later], objects))
        self.assertEqual(api.blobs[api.tree[receipt]].decode().strip(), home["observed_at"])
        self.assertFalse(publish_inputs(api, [later], objects))

    def test_home_publisher_restores_deleted_receipt_from_home_ledger(self):
        from collector_snapshots import observation_path
        manifest, objects = self.capture()
        first = "2026-11-01T00:00:00+00:00"
        manifest["observed_at"] = first
        env, digest = manifest["environment"], manifest["content_id"]
        api = FakeAPI()
        publish_inputs(api, [manifest], objects)
        receipt = "state/" + observation_path("home", env, digest)
        api.uploaded.remove(receipt)
        del api.tree[receipt]
        processed = "state/collector/processed.json"
        api.uploaded.add(processed)
        api.tree[processed] = "home-ledger"
        api.blobs["home-ledger"] = encode({"sources": {"home": {env: {digest: {
            "status": "replayed", "observed_at": first
        }}}}})
        newer = dict(manifest, observed_at="2026-11-12T00:00:00+00:00")
        self.assertTrue(publish_inputs(api, [newer], objects))
        self.assertEqual(api.blobs[api.tree[receipt]], b"2026-11-01T00:00:00+00:00\n")

    def test_home_publisher_refuses_to_redate_current_capture_without_receipt_or_ledger(self):
        from collector_snapshots import observation_path
        manifest, objects = self.capture()
        manifest["observed_at"] = "2026-11-01T00:00:00+00:00"
        digest = manifest["content_id"]
        api = FakeAPI()
        self.assertTrue(publish_inputs(api, [manifest], objects))
        receipt = "state/" + observation_path("home", "test", digest)
        api.uploaded.remove(receipt)
        del api.tree[receipt]
        self.assertEqual(json.loads(api.blobs[api.tree["state/collector/test/current.json"]])["content_id"], digest)
        self.assertNotIn("state/collector/processed.json", api.tree)
        before, updates = dict(api.tree), api.updates
        newer = dict(manifest, observed_at="2026-11-12T00:00:00+00:00")
        with self.assertRaisesRegex(ValueError, "receipt"):
            publish_inputs(api, [newer], objects)
        self.assertEqual(api.tree, before)
        self.assertEqual(api.updates, updates)

        # A checkout may reformat the pointer as valid JSON with CRLF.
        pointer = "state/collector/test/current.json"
        alternate = api.blobs[api.tree[pointer]].replace(b"\n", b"\r\n")
        from collector import git_blob_sha
        api.tree[pointer] = git_blob_sha(alternate)
        api.blobs[api.tree[pointer]] = alternate
        before = dict(api.tree)
        with self.assertRaisesRegex(ValueError, "receipt"):
            publish_inputs(api, [newer], objects)
        self.assertEqual(api.tree, before)
        self.assertEqual(api.updates, updates)

    def test_local_capture_cannot_write_into_a_generation_being_replaced(self):
        import shutil
        from unittest.mock import patch
        from auto_update import promote, update_lock
        from collector import main
        from sync import SITES

        resources = fixtures.WsdlSourcesTests().bundle()
        resources[SITES["test"]] = f'<a href="{fixtures.SEED}">WSDL</a>'.encode()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            stage = Path(tmp) / "stage"
            state = root / "state"
            pointer = state / "collector/test/current.json"
            state.mkdir(parents=True)
            (root / "viewer-template.html").touch()
            for folder in ("data", "site"):
                (stage / folder).mkdir(parents=True)
            args = ["collector.py", "--capture-to", str(state), "--environment", "test"]
            with patch("sys.argv", args), patch("collector.get", side_effect=lambda url, **_: resources[url]):
                with update_lock(root):
                    shutil.copytree(state, stage / "state")
                    (stage / "state/installed").touch()
                    with self.assertRaises(OSError):
                        main()
                    self.assertEqual(list(state.iterdir()), [])
                    promote(root, stage)
                main()
            self.assertTrue((state / "installed").is_file())
            self.assertEqual(json.loads(pointer.read_bytes())["content_id"], self.capture()[0]["content_id"])

    def test_standalone_home_can_capture_to_state_with_only_documented_files(self):
        import re
        import shutil
        import subprocess
        import sys
        from sync import SITES

        resources = fixtures.WsdlSourcesTests().bundle()
        resources[SITES["test"]] = f'<a href="{fixtures.SEED}">WSDL</a>'.encode()
        project = Path(__file__).resolve().parents[1]
        install = (project / "docs/russian-collector.md").read_text("utf8").split("В `/opt/sedo-collector`", 1)[1].splitlines()[0]
        code = """import json, sys
sys.path.insert(0, '.')
import collector
resources = json.load(sys.stdin)
collector.get = lambda url, **_: resources[url].encode()
sys.argv = ['collector.py', '--capture-to', 'state', '--environment', 'test']
collector.main()
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in set(re.findall(r"\b\w+\.py\b", install)):
                shutil.copy2(project / name, root / name)
            (root / "viewer-template.html").touch()
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], cwd=root,
                                    input=json.dumps({url: raw.decode() for url, raw in resources.items()}),
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            pointer = root / "state/collector/test/current.json"
            self.assertEqual(json.loads(pointer.read_bytes())["content_id"], self.capture()[0]["content_id"])

    def test_retry_keeps_another_home_writers_newer_pointer_and_archives_our_inputs(self):
        from collector import git_blob_sha
        from collector_snapshots import observation_path

        first, first_objects = self.capture(9, '2026-11-16T01:00:00+00:00')
        incoming, objects = self.capture(10, '2026-11-16T02:00:00+00:00')
        winner, winner_objects = self.capture(11, '2026-11-16T03:00:00+00:00')
        api = AtomicGitAPI()
        prefix = 'state/collector/test/'

        def legacy_commit(manifest, resources):
            identity = manifest['content_id']
            tree = dict(api.trees[api.commits[api.head][1]])
            for path, raw in {prefix + 'current': (identity + '\n').encode(),
                              prefix + identity + '.json': encode(manifest),
                              **{'state/objects/' + digest: raw for digest, raw in resources.items()}}.items():
                blob = git_blob_sha(raw)
                api.blobs[blob] = raw
                tree[path] = blob
            name = 'legacy-' + identity
            api.trees[name] = tree
            api.commits[name] = (api.head, name)
            api.head = name

        legacy_commit(first, first_objects)
        api.before_update = lambda: legacy_commit(winner, winner_objects)
        self.assertTrue(publish_inputs(api, [incoming], objects))
        self.assertEqual(api.conflicts, 1)
        self.assertEqual(api.read(prefix + 'current').decode().strip(), winner['content_id'])
        with self.assertRaises(KeyError):
            api.read(prefix + 'current.json')
        for manifest in (first, incoming, winner):
            archived = api.read(prefix + manifest['content_id'] + '.json')
            self.assertEqual(json.loads(archived), manifest)
            receipt = observation_path('home', 'test', manifest['content_id'])
            self.assertEqual(api.read('state/' + receipt).decode().strip(), manifest['observed_at'])
        self.assertEqual(api.read('state/objects/' + incoming['library']['sha256']),
                         objects[incoming['library']['sha256']])

    def test_publisher_rejects_forged_input_before_api_calls(self):
        manifest, objects = self.capture()
        manifest["environment"] = "../../.github/workflows"
        api = FakeAPI()
        with self.assertRaises(ValueError):
            publish_inputs(api, [manifest], objects)
        self.assertEqual(api.updates, 0)

    def test_sync_uses_validated_snapshot_without_live_wsdl(self):
        import io
        import zipfile
        from unittest.mock import patch
        from sync import sync, SITES
        manifest, objects = self.capture()
        resources = {}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("fixture.xsd", fixtures.WsdlSourcesTests().bundle()[fixtures.BASE + "?xsd=Common.xsd"])
        for page in SITES.values():
            resources[page] = ('<a href="' + fixtures.SEED + '">WSDL</a><a href="/specs_sedo/fixture.zip">XSD</a>').encode()
            resources[page.replace("/sedo.html", "/specs_sedo/fixture.zip")] = archive.getvalue()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "collector/test").mkdir(parents=True)
            (base / "objects").mkdir()
            (base / "collector/test" / (manifest["content_id"] + ".json")).write_bytes(encode(manifest))
            for digest, raw in objects.items():
                (base / "objects" / digest).write_bytes(raw)
            with patch("sync.get", side_effect=lambda url, **kw: resources[url]) as fetch:
                sync(base)
            self.assertNotIn(fixtures.SEED, [c.args[0] for c in fetch.call_args_list])
            current = json.loads((base / "current.json").read_bytes())
            snapshot = json.loads((base / "snapshots" / (current["snapshot"] + ".json")).read_bytes())
            self.assertIn(manifest["library"], snapshot["entries"])

    def test_test_gateway_uses_host_specific_ca_with_certificate_validation(self):
        from unittest.mock import patch, MagicMock
        from sync import get
        context = MagicMock()
        response = MagicMock()
        response.__enter__.return_value.headers = {}
        response.__enter__.return_value.read.return_value = b"fixture"
        response.__enter__.return_value.geturl.return_value = fixtures.SEED
        with patch("sync.ssl.create_default_context", return_value=context), patch("sync.HTTPSHandler"), patch("sync.build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(get(fixtures.SEED), b"fixture")
        context.load_verify_locations.assert_called_once()
        self.assertTrue(context.load_verify_locations.call_args.kwargs["cafile"].endswith("ecp-test.sfr.gov.ru.pem"))

    def test_bundle_bytes_do_not_depend_on_windows_or_linux_zip_defaults(self):
        import io
        import zipfile
        from unittest.mock import patch
        from wsdl_sources import _archive
        original = zipfile.ZipInfo
        class LinuxInfo(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.create_system = 3
        with patch("wsdl_sources.zipfile.ZipInfo", LinuxInfo):
            raw = _archive({"main.xsd": b"fixture"})
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(archive.getinfo("main.xsd").create_system, 0)


class AtomicGitAPI:
    """Store unreferenced commits and update the main ref only from its current parent."""
    def __init__(self):
        self.head = "initial"
        self.commits = {self.head: (None, "empty")}
        self.trees = {"empty": {}}
        self.blobs = {}
        self.before_update = None
        self.conflicts = 0

    def read(self, path):
        return self.blobs[self.trees[self.commits[self.head][1]][path]]

    def __call__(self, method, path, payload=None):
        import base64
        from urllib.error import HTTPError
        from collector import git_blob_sha

        if method == "GET":
            if path == "git/ref/heads/main":
                return {"object": {"sha": self.head}}
            if path.startswith("git/commits/"):
                return {"tree": {"sha": self.commits[path.split("/")[-1]][1]}}
            if path.startswith("git/trees/"):
                tree = self.trees[path.split("/")[-1].partition("?")[0]]
                return {"tree": [dict(path=name, type="blob", sha=digest) for name, digest in tree.items()]}
            if path.startswith("git/blobs/"):
                raw = self.blobs[path.split("/")[-1]]
                return dict(encoding="base64", size=len(raw), content=base64.b64encode(raw).decode())
        if method == "POST":
            if path == "git/blobs":
                raw = base64.b64decode(payload["content"])
                key = git_blob_sha(raw)
                self.blobs[key] = raw
                return {"sha": key}
            if path == "git/trees":
                key = f"tree-{len(self.trees)}"
                self.trees[key] = dict(self.trees[payload["base_tree"]])
                self.trees[key].update((row["path"], row["sha"]) for row in payload["tree"])
                return {"sha": key}
            if path == "git/commits":
                key = f"commit-{len(self.commits)}"
                parent, = payload["parents"]
                self.commits[key] = parent, payload["tree"]
                return {"sha": key}
        if method == "PATCH" and path == "git/refs/heads/main":
            if self.before_update is not None:
                update, self.before_update = self.before_update, None
                update()
            assert payload["force"] is False
            if self.commits[payload["sha"]][0] != self.head:
                self.conflicts += 1
                raise HTTPError("https://api.github.com", 422, "not a fast-forward", {}, None)
            self.head = payload["sha"]
            return {}
        raise AssertionError((method, path))


class FakeAPI:
    def __init__(self):
        self.updates = 0
        self.parents = []
        self.uploaded = set()
        self.tree = {}
        self.blobs = {}
        self.committed = False
    def __call__(self, method, path, payload=None):
        if path == "git/ref/heads/main":
            return {"object": {"sha": "head1" if not self.updates else "head2"}}
        if path.startswith("git/commits/"):
            return {"tree": {"sha": "tree"}}
        if path.startswith("git/trees/"):
            return {"tree": [{"path": p, "type": "blob", "sha": self.tree[p]} for p in self.uploaded] if self.committed else [], "truncated": False}
        if path == "git/blobs":
            import base64
            import hashlib
            raw = base64.b64decode(payload["content"])
            digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            self.blobs[digest] = raw
            return {"sha": digest}
        if method == "GET" and path.startswith("git/blobs/"):
            import base64
            raw = self.blobs[path.rsplit("/", 1)[-1]]
            return {"encoding": "base64", "content": base64.b64encode(raw).decode(), "size": len(raw)}
        if path == "git/trees":
            self.uploaded.update(x["path"] for x in payload["tree"])
            self.tree.update((x["path"], x["sha"]) for x in payload["tree"])
            return {"sha": "newtree"}
        if path == "git/commits":
            self.parents.extend(payload["parents"])
            return {"sha": "commit"}
        if path == "git/refs/heads/main":
            from urllib.error import HTTPError
            self.updates += 1
            if self.updates == 1:
                raise HTTPError("https://api.github.com", 422, "concurrent update", {}, None)
            self.committed = True
            return {}
        raise AssertionError((method, path))
