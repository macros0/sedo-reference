import json
import tempfile
import unittest
from pathlib import Path

from sync import encode, sha
from tests import test_wsdl_sources as fixtures
from collector_snapshots import make_manifest, validate_manifest, load_library
from collector import publish_inputs


class CollectorTests(unittest.TestCase):
    def capture(self):
        fixture = fixtures.WsdlSourcesTests()
        row, objects = fixture.collect(fixture.bundle())
        return make_manifest(row, "2026-11-13T00:00:00+00:00"), objects

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

    def test_publisher_noop_atomic_commit_and_retry_preserve_concurrent_tree(self):
        manifest, objects = self.capture()
        api = FakeAPI()
        self.assertTrue(publish_inputs(api, [manifest], objects))
        self.assertEqual(api.updates, 2)
        self.assertEqual(api.parents, ["head1", "head2"])
        self.assertTrue(all(p.startswith(("state/objects/", "state/collector/test/")) for p in api.uploaded))
        self.assertFalse(publish_inputs(api, [manifest], objects))

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
        response.__enter__.return_value.read.return_value = b"fixture"
        with patch("sync.ssl.create_default_context", return_value=context), patch("sync.urlopen", return_value=response):
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


class FakeAPI:
    def __init__(self):
        self.updates = 0
        self.parents = []
        self.uploaded = set()
        self.committed = False
    def __call__(self, method, path, payload=None):
        if path == "git/ref/heads/main":
            return {"object": {"sha": "head1" if not self.updates else "head2"}}
        if path.startswith("git/commits/"):
            return {"tree": {"sha": "tree"}}
        if path.startswith("git/trees/"):
            return {"tree": [{"path": p, "type": "blob"} for p in self.uploaded] if self.committed else [], "truncated": False}
        if path == "git/blobs":
            return {"sha": "blob"}
        if path == "git/trees":
            self.uploaded.update(x["path"] for x in payload["tree"])
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
