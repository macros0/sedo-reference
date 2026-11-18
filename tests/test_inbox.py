import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from import_inbox import detect_metadata, process_inbox

PROJECT = Path(__file__).resolve().parents[1]


class InboxMetadataTests(unittest.TestCase):
    def test_date_and_version_in_either_order_with_override(self):
        for year in ('26', '2026'):
            for tokens in (f'16.11.{year}_v3.2', f'v3.2_16.11.{year}'):
                name = f'999_{tokens}.docx'
                with self.subTest(name=name):
                    data = detect_metadata(name, 'test')
                    self.assertEqual((data['version'], data['date']), ('3.2', '2026-11-16'))
                    override = detect_metadata(name, 'test', {'version': '4.1', 'date': '2026-11-15'})
                    self.assertEqual((override['version'], override['date']), ('4.1', '2026-11-15'))

    def test_date_override_repairs_invalid_filename_and_final_date_is_validated(self):
        name = "10110_v1.2_31.02.2026.docx"
        self.assertEqual(detect_metadata(name, "prod", {"date": "2026-02-28"})["date"], "2026-02-28")
        for override in (None, {"date": "2026-02-31"}, {"date": "nonsense"}):
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, "дата"):
                detect_metadata(name, "prod", override)

    def test_official_and_short_names(self):
        data=detect_metadata("Приложение_6_Том_3_Тип_сообщения_12010_v3.3_08.06.26.docx", "prod")
        self.assertEqual(data["number"],"12010")
        self.assertEqual(data["version"],"3.3")
        self.assertEqual(data["date"],"2026-06-08")
        data=detect_metadata("10110_v3.0.4.docx","test")
        self.assertEqual(data["number"],"10110")
        self.assertEqual(data["version"],"3.0.4")
        self.assertEqual(data["environment"],"test")

    def test_generic_name_needs_override(self):
        with self.assertRaises(ValueError):detect_metadata("spec.docx","prod")
        data=detect_metadata("spec.docx","prod",dict(number="10110",version="3.1",date="2026-11-12"))
        self.assertEqual(data["number"],"10110")
        self.assertEqual(data["environment"],"prod")


class InboxEditionIdentityTests(unittest.TestCase):
    def test_removing_archive_keeps_confirmed_edition_even_with_document_only_history(self):
        from tests.test_fix15_sources import FilenameImportReviewTests
        from tests.test_source_collection_regressions import individual_document
        from sync import encode, write
        from wsdl_sources import _archive
        fixture = FilenameImportReviewTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        incoming = root / 'incoming/test'
        doc = individual_document('response')
        write(incoming / 'original/999.docx', doc)
        first = process_inbox(root)['files'][0]
        self.assertEqual(first['status'], 'document_only')
        for name, datatype in [('a', 'int'), ('b', 'string')]:
            folder = incoming / name
            write(folder / '999.docx', doc)
            write(folder / 'metadata.json', encode({'999.docx': {'title': name}}))
            schema = (f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main">'
                      f'<xs:element name="response" type="xs:{datatype}"/></xs:schema>')
            write(folder / 'schema.zip', _archive({'main.xsd': schema.encode()}))
        initial = process_inbox(root)['files']
        confirmed = {r['path']: r['revision_id'] for r in [first, *initial]}
        b = 'incoming/test/b/999.docx'
        (incoming / 'b/schema.zip').unlink()
        write(incoming / 'b/metadata.json', encode({'999.docx': {'title': 'B corrected'}}))
        corrected = process_inbox(root)['files'][0]
        self.assertEqual((corrected['path'], corrected['revision_id']), (b, confirmed[b]))
        rows = json.loads((root / 'data/catalog.json').read_bytes())['revisions']
        titles = {r['revision_id']: r['title'] for r in rows}
        self.assertEqual(titles[confirmed[b]], 'B corrected')
        self.assertEqual(titles[confirmed['incoming/test/a/999.docx']], 'a')
        self.assertEqual(titles[first['revision_id']], 'Сообщение 999')
        ledger = json.loads((root / 'state/inbox.json').read_bytes())['files']
        self.assertEqual({p: r['revision_id'] for p, r in ledger.items()}, confirmed)
        self.assertEqual(process_inbox(root)['processed'], 0)
        write(incoming / 'new/999.docx', doc)
        with self.assertRaisesRegex(ValueError, 'несколько редакций'):
            process_inbox(root)


class InboxImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for folder in ("state","data","web"):shutil.copytree(PROJECT/folder,self.root/folder)
        shutil.copy2(PROJECT/"viewer-template.html",self.root/"viewer-template.html")
        self.before=(self.root/"data/catalog.json").read_bytes()
        c=json.loads(self.before)
        self.example=next(r for r in c["revisions"] if r["number"]=="10110" and r["environment"]=="prod" and r["status"]=="matched")
        self.archive=(self.root/"state/objects"/self.example["archive_sha256"]).read_bytes()
        buffer=io.BytesIO()
        with zipfile.ZipFile(self.root/"state/objects"/self.example["document_sha256"]) as old,zipfile.ZipFile(buffer,"w") as new:
            for item in old.infolist():
                content=old.read(item.filename)
                if item.filename=="word/document.xml":content+=b"\n<!-- inbox fixture -->"
                new.writestr(item,content)
        self.document=buffer.getvalue()
        self.folder=self.root/"incoming/prod/batch";self.folder.mkdir(parents=True)
        (self.folder/"10110_v9.9_12.11.2026.docx").write_bytes(self.document)

    def test_dry_run_then_import_then_later_xsd_and_repeat(self):
        report=process_inbox(self.root,check=True)
        self.assertEqual(report["new_revisions"],1)
        self.assertEqual((self.root/"data/catalog.json").read_bytes(),self.before)
        self.assertFalse((self.root/"state/inbox.json").exists())
        report=process_inbox(self.root)
        self.assertEqual(report["new_revisions"],1)
        after=json.loads((self.root/"data/catalog.json").read_bytes())
        saved=next(r for r in after["revisions"] if r.get("document_version")=="9.9")
        self.assertEqual(saved["status"],"document_only")
        first=(self.root/"data/catalog.json").read_bytes()
        self.assertEqual(process_inbox(self.root)["processed"],0)
        self.assertEqual((self.root/"data/catalog.json").read_bytes(),first)
        (self.folder/"schemas.zip").write_bytes(self.archive)
        report=process_inbox(self.root)
        self.assertEqual(report["new_revisions"],1)
        after=json.loads((self.root/"data/catalog.json").read_bytes())
        self.assertIn(saved["revision_id"],{r["revision_id"] for r in after["revisions"]})
        self.assertTrue(any(r.get("document_version")=="9.9" and r["status"]=="matched" for r in after["revisions"]))
        before_removal=(self.root/"data/catalog.json").read_bytes()
        (self.folder/"10110_v9.9_12.11.2026.docx").unlink()
        (self.folder/"schemas.zip").unlink()
        self.assertEqual(process_inbox(self.root)["processed"],0)
        self.assertEqual((self.root/"data/catalog.json").read_bytes(),before_removal)

    def test_bad_batch_does_not_change_reference(self):
        (self.folder/"99999_v1.0.docx").write_bytes(b"bad DOCX")
        with self.assertRaises(ValueError):process_inbox(self.root)
        self.assertEqual((self.root/"data/catalog.json").read_bytes(),self.before)
        self.assertFalse((self.root/"state/inbox.json").exists())

    def test_new_submission_of_existing_edition_and_metadata_only_repeat(self):
        path=self.folder/"10110_v9.9_12.11.2026.docx"
        path.write_bytes((self.root/"state/objects"/self.example["document_sha256"]).read_bytes())
        report=process_inbox(self.root,check=True)
        self.assertEqual(report["processed"],1)
        self.assertEqual(report["new_revisions"],0)
        report=process_inbox(self.root)
        self.assertEqual(report["new_revisions"],0)
        self.assertEqual((self.root/"data/catalog.json").read_bytes(),self.before)
        (self.folder/"metadata.json").write_text(json.dumps({path.name:{"source":"Проверка повтора"}},ensure_ascii=False),encoding="utf8")
        report=process_inbox(self.root)
        self.assertEqual(report["processed"],1)
        self.assertEqual(report["new_revisions"],0)
        self.assertEqual((self.root/"data/catalog.json").read_bytes(),self.before)

    def test_later_failure_rolls_back_the_entire_batch(self):
        from manual_import import stage_import
        (self.folder/"10110_v10.0_12.11.2026.docx").write_bytes(self.document)
        calls=[]
        def prepare(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise ValueError("second import failed")
            return stage_import(*args,**kwargs)
        with patch("import_inbox.stage_import",side_effect=prepare),self.assertRaises(ValueError):
            process_inbox(self.root)
        self.assertEqual(len(calls),2)
        self.assertEqual((self.root/"data/catalog.json").read_bytes(),self.before)
        self.assertFalse((self.root/"state/inbox.json").exists())

    def test_ambiguous_schema_versions_need_separate_folders(self):
        (self.folder/"one.zip").write_bytes(self.archive)
        buffer=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.archive)) as old,zipfile.ZipFile(buffer,"w") as new:
            for item in old.infolist():
                content=old.read(item.filename)
                if item.filename==self.example["schema_path"]:content+=b"\n<!-- other schema edition -->"
                new.writestr(item,content)
        (self.folder/"two.zip").write_bytes(buffer.getvalue())
        with self.assertRaisesRegex(ValueError,"папк|несколько|Неоднознач"):
            process_inbox(self.root)
        self.assertEqual((self.root/"data/catalog.json").read_bytes(),self.before)


if __name__=="__main__":unittest.main()
