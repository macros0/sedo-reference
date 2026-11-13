import io
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import build_site
from sync import encode, sha, write


class DocumentArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
        self.document = buffer.getvalue()
        self.digest = sha(self.document)
        write(self.root / "state/objects" / self.digest, self.document)
        revision = dict(revision_id="edition1", document_sha256=self.digest, document_version="3.0", number="10110", environment="prod")
        write(self.root / "data/catalog.json", encode(dict(revisions=[revision, dict(revision, revision_id="edition2")])))
        write(self.root / "data/fields/index.json", b"[]")
        write(self.root / "viewer-template.html", b"<html></html>")
        for name in ("index.html", "app.js", "app.css", "automation-status.js", "compare-core.js", "compare.js", "comparison-export.js", "mapping-draft.js", "mapping-editor.js", "comparison-mapping.js", "vendor/exceljs.min.js", "vendor/exceljs-LICENSE", "vendor/README.md", "journal.js", "message-transitions.js", "favicon.svg", "validation.js", "validation-core.js", "validation-frame.js", "validation-worker.js", "validation-paths.js", "validation-source.js", "validation-messages.js", "vendor/xmllint/xmllint-browser.mjs", "vendor/xmllint/xmllint.wasm", "vendor/xmllint/COPYING", "vendor/xmllint/README.md"):
            write(self.root / "web" / name, b"test")

    def build(self):
        with patch.object(build_site, "HERE", self.root):
            build_site.main()

    def test_exact_bytes_shared_by_editions_and_offline_rebuild(self):
        self.build()
        published = json.loads((self.root / "site/catalog-data.js").read_text("utf8").removeprefix("window.SEDO_CATALOG=").removesuffix(";"))
        for r in published["revisions"]:
            self.assertEqual((self.root / "site" / r["document_download"]).read_bytes(), self.document)
            self.assertTrue(r["document_filename"].endswith(".docx"))
        self.assertEqual(len(list((self.root / "site/documents").glob("*.docx"))), 1)
        self.build()
        self.assertEqual(len(list((self.root / "site/documents").glob("*.docx"))), 1)

    def test_identical_original_is_not_replaced_when_open_elsewhere(self):
        target=self.root/'site/documents'/(self.digest+'.docx')
        write(target,self.document)
        catalog=json.loads((self.root/'data/catalog.json').read_bytes())
        with patch('build_site.write',side_effect=PermissionError('document is open')):
            build_site.archive_documents(catalog,self.root/'state',self.root/'site')
        self.assertEqual(target.read_bytes(),self.document)

    def test_grouped_journal_keeps_original_events_for_import_reports(self):
        catalog=json.loads((self.root/'data/catalog.json').read_bytes())
        fields=[dict(name='fault',path='fault',parent=-1,children=[],kind='element',min=1,max=1,facets=[],values=[],flags={})]
        for row in catalog['revisions']:
            row.update(root='fault',namespace='urn:fault',label_date=None,archive_url='https://example.test/schema.zip',
                       archive_sha256='xsd-archive',dependencies=[['Fault.xsd','same-xsd']],
                       document_url='https://example.test/'+row['revision_id']+'.docx')
            write(self.root/'data/fields'/(row['revision_id']+'.json'),encode(dict(fields=fields)))
        write(self.root/'data/fields/index.json',encode([dict(revision_id=r['revision_id'],status='ready') for r in catalog['revisions']]))
        catalog['updates']=[dict(revision_id='edition1',previous_revision_id=None,snapshot='initial',observed_at='2026-11-12T12:00:00Z'),
                            dict(revision_id='edition2',previous_revision_id='edition1',snapshot='later',observed_at='2026-11-13T12:00:00Z')]
        write(self.root/'data/catalog.json',encode(catalog))
        self.build()
        published=json.loads((self.root/'site/catalog-data.js').read_text('utf8').removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
        self.assertEqual(len(published['schema_updates']),1)
        source_event=next(e for e in published['updates'] if e['revision_id']=='edition2')
        self.assertEqual(source_event['comparison']['status'],'ready')
        self.assertEqual(source_event['comparison']['counts'],dict(added=0,removed=0,changed=0))
        self.assertEqual(len(published['updates']),2)
        self.assertEqual(len(published['source_updates']),1)
        self.assertEqual(published['source_updates'][0]['revision_id'],'edition1')
        self.assertEqual(published['source_updates'][0]['source']['document_url'],'https://example.test/edition2.docx')
        self.assertEqual(len(published['schema_updates'][0]['event_sources']),1)

    def test_missing_document_blocks_build(self):
        (self.root / "state/objects" / self.digest).unlink()
        with self.assertRaisesRegex(ValueError, "DOCX"):
            self.build()

    def test_changed_script_gets_new_asset_url(self):
        write(self.root / "web/index.html", b'<link href="app.css"><script src="catalog-data.js"></script><script src="app.js"></script><script src="journal.js"></script>')
        self.build()
        first=(self.root / "site/index.html").read_text("utf8")
        urls=re.findall(r'(?:src|href)="([^"]+)"',first)
        self.assertEqual(len(urls),4)
        self.assertTrue(all("?v=" in url for url in urls))
        write(self.root / "web/app.js", b"updated script")
        self.build()
        second=(self.root / "site/index.html").read_text("utf8")
        self.assertNotEqual(re.search(r'src="(app.js[^"]+)"',first)[1],re.search(r'src="(app.js[^"]+)"',second)[1])

    def test_wrong_checksum_blocks_build(self):
        write(self.root / "state/objects" / self.digest, b"replaced document")
        with self.assertRaisesRegex(ValueError, "DOCX"):
            self.build()

    def test_non_docx_blocks_build_even_with_matching_checksum(self):
        invalid = b"not a Word document"
        digest = sha(invalid)
        write(self.root / "state/objects" / digest, invalid)
        catalog = json.loads((self.root / "data/catalog.json").read_bytes())
        for r in catalog["revisions"]:
            r["document_sha256"] = digest
        write(self.root / "data/catalog.json", encode(catalog))
        with self.assertRaisesRegex(ValueError, "DOCX"):
            self.build()


if __name__ == "__main__":
    unittest.main()
