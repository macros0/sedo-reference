import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublishedIntegrityTests(unittest.TestCase):
    def test_snapshot_bytes_match_content_address(self):
        for snapshot in (ROOT / 'state/snapshots').glob('*.json'):
            with self.subTest(snapshot=snapshot.name):
                self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(), snapshot.stem)

    def test_validator_resources_match_manifest_in_checkout(self):
        loader = (ROOT / 'site/validation.js').read_text('utf8')
        assets = json.loads(re.search(r'const ASSETS=(.*?);', loader)[1])
        for asset in assets:
            with self.subTest(path=asset['path']):
                raw = (ROOT / 'site' / asset['path']).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), asset['sha'])

    def test_downloadable_source_names_match_original_bytes(self):
        for source in (ROOT / 'site/sources').glob('*.xsd'):
            with self.subTest(path=source.name):
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source.stem)

    def test_edition_pages_keep_the_published_version_hash(self):
        catalog = json.loads((ROOT / 'site/catalog-data.js').read_text('utf8')
                             .removeprefix('window.SEDO_CATALOG=').rstrip(';\r\n'))
        for row in catalog['revisions']:
            if not row.get('viewer_sha256'):
                continue
            with self.subTest(revision=row['revision_id']):
                raw = (ROOT / 'site/editions' / (row['revision_id'] + '.html')).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), row['viewer_sha256'])
