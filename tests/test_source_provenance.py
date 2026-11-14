import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from source_provenance import publish_sources
from sync import sha, write, encode


class ProvenanceTests(unittest.TestCase):
    def test_same_contour_publication_and_original_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            state=root/"state"
            raw=b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>'
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,"w") as archive:
                archive.writestr("eln-xsd/xsd/v01/eln/Notice.xsd",raw)
                archive.writestr("eln-xsd/xsd/v01/type/Common.xsd",raw+b"\n")
            digest=sha(buffer.getvalue())
            write(state/"objects"/digest,buffer.getvalue())
            entries=[]
            rows=[]
            for env,host in (("prod","lk.sfr.gov.ru"),("test","lk-test.fss.ru")):
                url=f"https://{host}/prab/eln-xsd.zip"
                page=f"https://{host}/prab.html"
                entries.append(dict(environment=env,url=url,sha256=digest,source_page=page))
                rows.append(dict(status="matched",environment=env,archive_url=url,archive_sha256=digest,
                                 schema_path="eln-xsd/xsd/v01/eln/Notice.xsd",
                                 dependencies=[["../type/Common.xsd",sha(raw+b"\n")]]))
            write(state/"snapshots"/"one.json",encode(dict(entries=entries)))
            publish_sources(dict(revisions=rows),state,root/"site")
            for row,entry in zip(rows,entries):
                self.assertEqual(row["schema_publication_page"],entry["source_page"])
                self.assertEqual((root/"site"/row["schema_file_download"]).read_bytes(),raw)
                dependency=next(s for s in row["schema_files"] if s["path"].endswith("Common.xsd"))
                self.assertEqual((root/"site"/dependency["download"]).read_bytes(),raw+b"\n")
            self.assertEqual(rows[0]["schema_file_download"],rows[1]["schema_file_download"])
            # An unknown delivery must not inherit an unrelated publication page.
            rows[0]["archive_url"]="https://lk.sfr.gov.ru/specs_sedo/unknown.zip"
            publish_sources(dict(revisions=rows),state,root/"site")
            self.assertNotIn("schema_publication_page",rows[0])

    def test_no_matched_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            publish_sources(dict(revisions=[dict(status="document_only")]),root,root/"site")
