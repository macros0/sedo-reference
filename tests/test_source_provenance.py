import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from source_provenance import publish_sources
from sync import sha, write, encode


def embedded_alias_catalog(root, current, *, legacy_external=False):
    from catalog import _export
    from edition_groups import group_editions, display_updates, source_updates
    from tests.test_fix20r_pipeline import archive, word_table
    from build_site import share_document_histories

    state = root / 'state'
    raw = b'<schema/>'
    package = archive('r.xsd', raw)
    container = io.BytesIO(word_table([['Element', 'Value']]))
    with zipfile.ZipFile(container, 'a') as word:
        word.writestr(zipfile.ZipInfo('word/embeddings/sample.bin'), package)
    doc = container.getvalue()
    digest = sha(package)
    write(state / 'objects' / digest, package)
    write(state / 'objects' / sha(doc), doc)
    pages = {'first':'sedo.html', 'second':'prab.html'}
    for key, page in pages.items():
        url = 'https://lk.sfr.gov.ru/specs_sedo/'+('fixed' if legacy_external else key)+'.docx'
        match = dict(number='14', title='Title', root='r', namespace=None, dependencies=[['r.xsd',sha(raw)]],
                     dependency_digest='closure', schema_path='r.xsd', archive_sha256=digest,
                     archive_url=url+'#embedded=word/embeddings/sample.bin', archive_name='sample.bin (из DOCX)')
        entry = dict(kind='docx', environment='prod', url=url, label=key, sha256=sha(doc),
                     source_page='https://lk.sfr.gov.ru/'+page, module_matches=[match])
        entries = [entry]
        if legacy_external:
            match['archive_url'] = 'https://lk.sfr.gov.ru/specs_sedo/'+key+'.zip'
            match.pop('archive_name')
            entries.append(dict(kind='zip', environment='prod', url=match['archive_url'], sha256=digest,
                                source_page=entry['source_page']))
        write(state/'snapshots'/(key+'.json'), encode(dict(format=7,entries=entries)))
    write(state/'history.json', encode(list(pages)))
    write(state/'current.json', encode(dict(snapshot=current)))
    _export(state, root/'catalog.json')
    catalog = json.loads((root/'catalog.json').read_bytes())
    if legacy_external:
        catalog.pop('observation_presentations')
    publish_sources(catalog, state, root/'site')
    catalog = group_editions(catalog, lambda _: [])
    catalog['schema_updates'] = display_updates(catalog)
    catalog['source_updates'] = source_updates(catalog)
    share_document_histories(catalog)
    return catalog


class ProvenanceTests(unittest.TestCase):
    def test_legacy_event_at_fixed_docx_url_recovers_its_external_zip_from_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = embedded_alias_catalog(Path(tmp), 'second', legacy_external=True)
            edition = catalog['revisions'][0]['edition_sources'][0]
            event = catalog['schema_updates'][0]['event_sources'][0]
            self.assertEqual(edition['document_url'], event['document_url'])
            self.assertEqual(edition['archive_url'], 'https://lk.sfr.gov.ru/specs_sedo/second.zip')
            self.assertEqual(event['archive_url'], 'https://lk.sfr.gov.ru/specs_sedo/first.zip')
            self.assertEqual(event['schema_publication_page'], 'https://lk.sfr.gov.ru/sedo.html')

    def test_embedded_archive_tracks_document_alias_at_observation_and_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = []
            for current in ('second','first'):
                catalog = embedded_alias_catalog(root, current)
                row, = catalog['revisions']
                first, second = row['edition_sources']
                self.assertEqual([s['listed_now'] for s in (first,second)], [current=='first', current=='second'])
                original = catalog['schema_updates'][0]['event_sources'][0]
                added, = catalog['source_updates']
                ids.append((row['revision_id'], added['event_id']))
                for source, name, page in [(first,'first','sedo'), (second,'second','prab'),
                                           (original,'first','sedo'), (added['source'],'second','prab')]:
                    url = 'https://lk.sfr.gov.ru/specs_sedo/'+name+'.docx'
                    self.assertEqual(source['document_url'], url)
                    self.assertEqual(source.get('archive_url'), url+'#embedded=word/embeddings/sample.bin')
                    self.assertEqual(source['archive_name'], 'sample.bin (из DOCX)')
                    self.assertEqual(source['schema_publication_page'], 'https://lk.sfr.gov.ru/'+page+'.html')
                    self.assertEqual((root/'site'/source['schema_file_download']).read_bytes(), b'<schema/>')
                self.assertEqual(first['schema_file_download'], second['schema_file_download'])
            self.assertEqual(ids[0], ids[1])

    def test_document_fragment_preserves_current_and_archived_publication_pages(self):
        from wsdl_sources import _archive
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            archive = _archive({'main.xsd': b'<schema/>'})
            digest = sha(archive)
            write(state / 'objects' / digest, archive)
            doc = 'https://lk.sfr.gov.ru/specs_sedo/volume.docx#part'
            pages = ['https://lk.sfr.gov.ru/sedo.html', 'https://lk.sfr.gov.ru/prab.html']
            for key, page in zip(['old', 'now'], pages):
                write(state / 'snapshots' / (key+'.json'), encode(dict(entries=[
                    dict(environment='prod', url=doc, sha256='doc-hash', source_page=page)])))
            write(state / 'current.json', encode(dict(snapshot='now')))
            write(state / 'history.json', encode(['old','now']))
            revision = dict(environment='prod', status='matched', document_url=doc, document_sha256='doc-hash',
                archive_url=doc+'#embedded=schemas', archive_sha256=digest, schema_path='main.xsd')
            rows = [dict(revision, revision_id='a', listed_now=False), dict(revision, revision_id='b', listed_now=True)]
            publish_sources(dict(revisions=rows, updates=[dict(revision_id='a', snapshot='old')]), state, Path(tmp) / 'site')
            self.assertEqual([r.get('schema_publication_page') for r in rows], pages)
            self.assertEqual([r['document_url'] for r in rows], [doc,doc])

    def test_archive_from_official_alias_keeps_original_url_and_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state'
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr('main.xsd', b'<schema/>')
            digest = sha(buffer.getvalue())
            write(state / 'objects' / digest, buffer.getvalue())
            original = 'https://lk.fss.ru/specs_sedo/schemas.zip'
            page = 'https://lk.sfr.gov.ru/sedo.html'
            entry = dict(environment='prod', url=original, sha256=digest, source_page=page,
                         download_url='https://lk.sfr.gov.ru/specs_sedo/schemas.zip')
            write(state / 'snapshots/one.json', encode(dict(entries=[entry])))
            write(state / 'current.json', encode(dict(snapshot='one')))
            row = dict(status='matched', environment='prod', archive_url=original,
                       archive_sha256=digest, schema_path='main.xsd')
            publish_sources(dict(revisions=[row]), state, Path(tmp) / 'site')
            self.assertEqual(row.get('schema_publication_page'), page)
            self.assertEqual(row['archive_url'], original)

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
            write(state/"history.json",encode(["one"]))
            write(state/"current.json",encode(dict(snapshot="one")))
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

    def test_only_confirmed_snapshots_and_pointer_determine_publication_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as bundle:
                bundle.writestr("main.xsd", b"schema")
            digest = sha(data.getvalue())
            write(state / "objects" / digest, data.getvalue())
            url = "https://lk.sfr.gov.ru/specs_sedo/schemas.zip"
            def snapshot(key, page):
                write(state / "snapshots" / (key + ".json"), encode(dict(entries=[
                    dict(environment="prod", url=url, sha256=digest, source_page=page)])))
            old, new = "https://lk.sfr.gov.ru/sedo.html", "https://lk.sfr.gov.ru/prab.html"
            snapshot("z-old", old)
            snapshot("a-new", new)
            snapshot("zz-unconfirmed", "https://lk.sfr.gov.ru/eln.html")
            write(state / "history.json", encode(["z-old", "a-new"]))
            write(state / "current.json", encode(dict(snapshot="a-new")))
            row = dict(status="matched", environment="prod", revision_id="current", listed_now=True,
                       archive_url=url, archive_sha256=digest, schema_path="main.xsd")
            archived = dict(row, revision_id="archive", listed_now=False)
            catalog = dict(revisions=[row, archived], updates=[dict(revision_id="archive", snapshot="z-old")])
            publish_sources(catalog, state, root / "site")
            self.assertEqual(row["schema_publication_page"], new)
            self.assertEqual(archived["schema_publication_page"], old)
            write(state / "current.json", encode(dict(snapshot="z-old")))
            publish_sources(catalog, state, root / "site")
            self.assertEqual(row["schema_publication_page"], old)
