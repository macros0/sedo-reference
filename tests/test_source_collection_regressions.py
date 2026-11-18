import io
import json
import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from collector_feed import import_feed, store_feed
from collector_snapshots import replay_history
from import_inbox import collect, process_inbox
from sync import SITES, encode, sha, sync, write
from tests import test_collector_feed as feed_fixtures
from tests import test_wsdl_sources as wsdl_fixtures


def archive(path, raw):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo(path), raw)
    return buf.getvalue()


def document(title, number='999'):
    xml = (f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           f'<w:p><w:r><w:t>Тип сообщения {number}: {title}</w:t></w:r></w:p>'
           f'<w:p><w:r><w:t>&lt;m:response xmlns:m="urn:main"&gt;&lt;/m:response&gt;</w:t></w:r></w:p>'
           '</w:body></w:document>')
    return archive("word/document.xml", xml.encode())

def individual_document(root):
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>'
    for name, value in (('Корневой элемент', root), ('Пространство имен файла xml-схемы', 'urn:main')):
        xml += '<w:tr>' + ''.join('<w:tc><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:tc>' for text in (name, value)) + '</w:tr>'
    return archive('word/document.xml', (xml+'</w:tbl></w:body></w:document>').encode())


class SourceCollectionTests(unittest.TestCase):
    def test_individual_uses_only_its_own_embedded_schema_and_preserves_original(self):
        from catalog import derive
        from fields import build
        from build_site import archive_documents
        schema = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>'
        def embedded(root, text):
            raw = individual_document(root)
            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(raw)) as z, zipfile.ZipFile(output, 'w') as out:
                xml = z.read('word/document.xml').decode().replace('</w:body>', '<w:p><w:r><w:t>'+text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')+'</w:t></w:r></w:p></w:body>')
                out.writestr('word/document.xml', xml)
            return output.getvalue()
        page = SITES['test']; base = page.rsplit('/', 1)[0]
        url = base+'/specs_sedo/Тип_сообщения_999.docx'
        other = base+'/specs_sedo/Тип_сообщения_998.docx'
        original = embedded('response', schema)
        self.resources[url] = original
        self.resources[other] = embedded('response', schema.replace('xs:string', 'xs:int'))
        self.resources[page] = ('<a href="/specs_sedo/file.zip">XSD</a><a href="'+url+'">One</a><a href="'+other+'">Two</a>').encode()
        self.collect(1)
        catalog = derive(self.current())
        rows = {r['number']: r for r in catalog['revisions']}
        self.assertEqual({r['status'] for r in rows.values()}, {'matched'})
        self.assertNotEqual(rows['999']['archive_sha256'], rows['998']['archive_sha256'])
        self.assertTrue(rows['999']['archive_url'].startswith(url+'#embedded='))
        write(self.root/'catalog.json', encode(catalog))
        build(self.root/'catalog.json', self.state, self.root/'fields')
        fields = json.loads((self.root/'fields'/(rows['999']['revision_id']+'.json')).read_bytes())['fields']
        self.assertEqual(fields[0]['base'], 'xs:string')
        archive_documents(catalog, self.state, self.root/'site')
        self.assertEqual((self.root/'site'/rows['999']['document_download']).read_bytes(), original)
        # A conflicting external archive is not silently preferred over this DOCX.
        self.resources[base+'/specs_sedo/file.zip'] = archive('main.xsd', schema.replace('xs:string', 'xs:boolean').encode())
        self.collect(2)
        self.assertEqual({r['status'] for r in derive(self.current())['revisions']}, {'review'})

    def test_companion_schema_escaped_and_literal_links_share_bytes(self):
        from urllib.parse import quote
        for menu in ("mchd", "eln"):
            page = SITES['test']
            companion = page.replace('sedo.html', menu + '.html')
            self.resources[page] += f'<a href="{companion}">Menu</a>'.encode()
            path = f'/{menu}/схемы.zip'
            urls = [page.replace('/sedo.html', p) for p in (path, quote(path), quote(path).lower())]
            self.resources[companion] = ''.join(f'<a href="{u}">Скачать</a>' for u in urls).encode()
            for u in urls:
                self.resources[u] = self.schema
            requests = self.collect(1)
            rows = [r for r in self.current()['entries'] if r.get('source_page') == companion]
            self.assertEqual({r['url'] for r in rows}, set(urls))
            self.assertEqual({r['sha256'] for r in rows}, {sha(self.schema)})
            self.assertEqual(sum(requests.count(u) for u in urls), 1)

    def test_history_only_pending_uses_archived_individual_docx(self):
        self.check_individual_replay("matched")

    def test_history_with_missing_individual_root_does_not_block_active_edition(self):
        self.check_individual_replay("missing")

    def test_history_keeps_matched_individual_edition_when_another_root_is_missing(self):
        self.check_individual_replay("mixed")

    def check_individual_replay(self, scenario):
        from collector_snapshots import pending
        from catalog import derive
        from collector_snapshots import apply_pending
        a, ao = feed_fixtures.FeedTests().capture()
        b, bo = feed_fixtures.FeedTests().capture(True)
        from collector_snapshots import make_manifest
        fixture = wsdl_fixtures.WsdlSourcesTests()
        url = wsdl_fixtures.BASE + '?xsd=main.xsd'
        if scenario == 'missing':
            resources = fixture.bundle()
            resources[url] = resources[url].replace(b'name="response"', b'name="other"')
            library, bo = fixture.collect(resources)
            b = make_manifest(library, b['observed_at'])
        if scenario == 'mixed':
            resources = fixture.bundle()
            resources[url] = resources[url].replace(b'</xs:schema>', b'<xs:element name="other" type="xs:string"/></xs:schema>')
            library, ao = fixture.collect(resources)
            a = make_manifest(library, a['observed_at'])
        b["observed_at"] = "2026-11-14T12:00:00+00:00"
        p = SITES["test"]
        href = "/specs_sedo/Тип_сообщения_999.docx"
        self.resources[p] = ('<a href="/specs_sedo/file.zip">XSD</a><a href="'+href+'">Individual</a>').encode()
        self.resources[p.replace("/sedo.html", href)] = individual_document('response')
        if scenario == 'mixed':
            other_href = href.replace('999', '998')
            self.resources[p] += f'<a href="{other_href}">Another individual</a>'.encode()
            self.resources[p.replace('/sedo.html', other_href)] = individual_document('other')
        feed = self.root / 'remote'
        store_feed(feed, [a], ao)
        import_feed(self.state, lambda name: (feed / name).read_bytes())
        self.collect(1)
        from catalog import export
        from fields import build
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / 'web', self.root / 'web')
        shutil.copy2(project / 'viewer-template.html', self.root)
        export(self.state, self.root / 'data/catalog.json')
        build(self.root / 'data/catalog.json', self.state, self.root / 'data/fields')
        store_feed(feed, [b], bo)
        store_feed(feed, [a], ao)
        import_feed(self.state, lambda name: (feed / name).read_bytes())
        self.assertTrue(pending(self.root))
        pointer = (self.state / 'current.json').read_bytes()
        with patch('sync.get', side_effect=AssertionError('unexpected network')):
            apply_pending(self.root)
            apply_pending(self.root)
        ledger = json.loads((self.state / 'collector/processed.json').read_bytes())
        record = ledger['manifests']['test'][b['content_id']]
        if scenario == 'missing':
            self.assertEqual(record['status'], 'unavailable')
            self.assertEqual([r['number'] for r in record['unmatched_documents']], ['999'])
            self.assertFalse(pending(self.root))
            return
        if scenario == 'mixed':
            self.assertEqual([r['number'] for r in record['unmatched_documents']], ['998'])
        self.assertEqual((record['status'], record['observed_at']), ('replayed', b['observed_at']))
        self.assertEqual(json.loads((self.state/'history.json').read_bytes()).count(record['snapshot']), 1)
        past = json.loads((self.state/'snapshots'/(record['snapshot']+'.json')).read_bytes())
        edition = derive(past)['revisions'][0]
        self.assertEqual(edition['archive_sha256'], b['library']['sha256'])
        expected_id = sha(encode([edition['environment'], edition['number'], edition['document_sha256'],
                                  edition['dependency_digest']]))
        self.assertEqual(edition['revision_id'], expected_id)
        site = json.loads((self.root/'site/catalog-data.js').read_text('utf8').removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
        published = next(row for row in site['revisions'] if row['revision_id'] == edition['revision_id'])
        self.assertEqual((published.get('schema_source_type'), published.get('schema_source_url'),
                          published.get('schema_sources')), ('wsdl', b['library']['source_url'], b['library']['source_files']))
        self.assertEqual(published['schema_publication_page'], SITES['test'])
        if scenario == 'matched':
            import subprocess
            subprocess.run(['node', str(project/'tests/test_schema_origin.cjs'),
                            str(self.root/'site/catalog-data.js'), published['revision_id']], check=True)
        self.assertEqual((self.state / 'current.json').read_bytes(), pointer)
        self.assertFalse(pending(self.root))

    def test_unavailable_test_observation_stays_settled_after_prod_pdf_change(self):
        from collector_snapshots import pending
        self.collect(1)
        manifest, objects = feed_fixtures.FeedTests().capture()
        manifest['observed_at'] = '2026-11-13T00:00:00+00:00'
        feed = self.root / 'feed'
        store_feed(feed, [manifest], objects)
        import_feed(self.state, lambda name: (feed / name).read_bytes())
        self.collect(2)
        self.assertFalse(pending(self.root))
        page = SITES['prod']
        self.resources[page] += b'<a href="/specs_sedo/note.pdf">PDF</a>'
        self.resources[page.replace('sedo.html', 'specs_sedo/note.pdf')] = b'example'
        self.collect(3)
        self.assertFalse(pending(self.root))

    def test_catalog_alias_downloads_one_resource_and_skips_navigation_documents(self):
        from catalog import export
        page = SITES["prod"]
        path = "/specs_sedo/Тип_сообщения_999.docx"
        official = page.replace("/sedo.html", path)
        alias = official.replace("lk.sfr.gov.ru", "lk.fss.ru")
        nav = page.replace("/sedo.html", "/help/Тип_сообщения_90.docx")
        other = "https://example.org" + path
        self.resources[page] = b'<a href="/specs_sedo/file.zip">Schema</a>' + ''.join(
            f'<a href="{u}">Specification</a>' for u in (alias, official, nav, other)).encode()
        self.resources[official] = individual_document('response')
        self.resources[page.replace('sedo.html', 'specs_sedo/file.zip')] = archive('main.xsd',
            b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="response" type="xs:string"/></xs:schema>')
        requests = self.collect(1)
        rows = {r["url"]: r for r in self.current()["entries"]}
        self.assertEqual(rows[alias].get("sha256"), sha(self.resources[official]))
        self.assertEqual(requests.count(official), 1)
        self.assertFalse({alias, nav, other}.intersection(requests))
        export(self.state, self.root / "catalog.json")
        output = json.loads((self.root / "catalog.json").read_bytes())
        self.assertEqual([r["number"] for r in output["revisions"]], ["999"])
        self.assertEqual(output['review'], [])
        self.assertEqual(output['revisions'][0]['status'], 'matched')
        before = (self.state / "current.json").read_bytes()
        del self.resources[official]
        with self.assertRaisesRegex(ValueError, "file errors"):
            self.collect(2)
        self.assertEqual((self.state / "current.json").read_bytes(), before)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.resources = {}
        self.schema = archive("a.xsd", b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>')
        for env, page in SITES.items():
            base = page.rsplit("/", 1)[0]
            self.resources[page] = b'<a href="/specs_sedo/file.zip">Schema</a><a href="/specs_sedo/file.docx">DOCX</a>'
            self.resources[base + "/specs_sedo/file.zip"] = self.schema
            self.resources[base + "/specs_sedo/file.docx"] = document(env)

    def collect(self, hour, **options):
        with patch("sync.get", side_effect=self.resources.__getitem__) as get, patch("sync.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 14, hour, tzinfo=timezone.utc)
            sync(self.state, **options)
            return [call.args[0] for call in get.call_args_list]

    def current(self):
        key = json.loads((self.state / "current.json").read_bytes())["snapshot"]
        return json.loads((self.state / "snapshots" / (key + ".json")).read_bytes())

    def test_companion_page_retains_menu_label_and_reports_label_changes(self):
        page = SITES["test"]
        companion = page.replace("sedo.html", "prab.html")
        self.resources[page] += b'<a href="prab.html">Before</a>'
        self.resources[companion] = b"<p>Resources</p>"
        self.collect(1)
        self.resources[page] = self.resources[page].replace(b"Before", b"After")
        self.collect(2)
        rows = [r for r in self.current()["entries"] if r["url"] == companion]
        self.assertEqual([(r["label"], r.get("sha256")) for r in rows], [("After", sha(self.resources[companion]))])
        def changes():
            return json.loads((self.state / "last-run.json").read_bytes())["diff"]["changed"]
        self.assertIn(["test", companion], changes())
        self.collect(3)
        self.assertEqual(changes(), [])

    def test_companion_link_to_another_contour_or_unapproved_host_is_not_rewritten(self):
        prod = SITES['prod']
        for href in ('https://lk-test.fss.ru/prab.html', 'https://example.org/prab.html',
                     'http://lk.fss.ru/prab.html', 'https://lk.fss.ru:444/prab.html',
                     'https://user@lk.fss.ru/prab.html'):
            with self.subTest(href=href):
                self.resources[prod] = b'<a href="/specs_sedo/file.zip">Schema</a>' + f'<a href="{href}">PRAB</a>'.encode()
                companion = prod.replace('sedo.html', 'prab.html')
                self.resources[companion] = b'<a href="/prab/false.zip">Schema</a>'
                self.resources[prod.replace('sedo.html', 'prab/false.zip')] = self.schema
                requests = self.collect(1)
                self.assertNotIn(companion, requests)
                self.assertFalse(any(r.get('source_page') == companion for r in self.current()['entries']))

    def test_companion_accepts_same_contour_official_alias_and_double_slash_path(self):
        for env, href in (('prod', 'https://lk.fss.ru//mchd.html'), ('test', '//lk-test.fss.ru//mchd.html')):
            page = SITES[env]
            self.resources[page] += f'<a href="{href}">MCHD</a>'.encode()
            companion = page.replace('sedo.html', 'mchd.html')
            resource = page.replace('sedo.html', 'mchd/schemas.zip')
            self.resources[companion] = b'<a href="/mchd/schemas.zip">XSD</a>'
            self.resources[resource] = self.schema
        requests = self.collect(1)
        for env, page in SITES.items():
            companion = page.replace('sedo.html', 'mchd.html')
            self.assertIn(companion, requests)
            row = next(r for r in self.current()['entries'] if r['url'] == page.replace('sedo.html', 'mchd/schemas.zip'))
            self.assertEqual((row['environment'], row['source_page']), (env, companion))

    def test_sourcecraft_observation_uses_its_document_after_home_capture(self):
        from collector_snapshots import feed_manifests
        manifest, objects = feed_fixtures.FeedTests().capture(True)
        digest = manifest["content_id"]
        manifest["observed_at"] = "2026-11-14T04:00:00+00:00"
        self.collect(1)
        old_doc = self.resources[SITES["test"].replace("sedo.html", "specs_sedo/file.docx")]
        self.resources[SITES["test"].replace("sedo.html", "specs_sedo/file.docx")] = document("replacement")
        self.collect(12)
        home = dict(manifest, observed_at="2026-11-14T19:00:00+00:00")
        legacy = self.state / "collector/test" / (digest + ".json")
        write(legacy, encode(home))
        write(self.state / "collector/test/current", digest.encode())
        for key, raw in objects.items():
            write(self.state / "objects" / key, raw)
        # Record the input of the historical home run before switching modes.
        self.collect(19, wsdl_entries=[home["library"]])
        remote = self.root / "sourcecraft"
        store_feed(remote, [manifest], objects)
        import_feed(self.state, lambda name: (remote / name).read_bytes())
        self.assertEqual(feed_manifests(self.state)[0]["observed_at"], manifest["observed_at"])
        self.assertEqual(json.loads(legacy.read_bytes())["observed_at"], home["observed_at"])
        replayed, _ = replay_history(self.state, self.current(), sha)
        self.assertEqual(len(replayed), 1, "the later home snapshot has a different DOCX")
        # A legacy run may already have acknowledged the wrong observation.
        from collector_snapshots import pending
        write(self.state / "collector/processed.json", encode(dict(format=1, manifests={"test": {
            digest: dict(status="unavailable", observed_at=home["observed_at"])}})))
        with patch("collector_snapshots.load_library", return_value=None):
            self.assertTrue(pending(self.root))
        keys, _ = replay_history(self.state, self.current(), sha)
        snapshot = json.loads((self.state / "snapshots" / (keys[0] + ".json")).read_bytes())
        self.assertEqual(next(r["sha256"] for r in snapshot["entries"] if r["kind"] == "docx" and r["environment"] == "test"), sha(old_doc))

    def test_companion_archive_already_linked_on_main_is_downloaded_once(self):
        for page in SITES.values():
            base = page.rsplit("/", 1)[0]
            self.resources[page] += b'<a href="/prab.html">PRAB</a><a href="/prab/one.zip">Archive</a>'
            self.resources[base + "/prab.html"] = b'<a href="/prab/one.zip">Archive</a>'
            self.resources[base + "/prab/one.zip"] = self.schema
        requests = self.collect(1)
        for env, page in SITES.items():
            base = page.rsplit("/", 1)[0]
            url = base + "/prab/one.zip"
            self.assertEqual(requests.count(url), 1)
            rows = [r for r in self.current()["entries"] if r["environment"] == env and r["url"] == url]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sha256"], sha(self.schema))
            self.assertEqual(rows[0]["source_page"], base + "/prab.html")

    def test_download_repairs_damaged_existing_docx_zip_and_page(self):
        originals = set(self.resources.values())
        for raw in originals:
            damaged = raw[:12] if raw.startswith(b"PK") else b" " * len(raw)
            write(self.state / "objects" / sha(raw), damaged)
        self.collect(1)
        pointer = self.current()
        self.collect(2)
        for raw in originals:
            self.assertEqual((self.state / "objects" / sha(raw)).read_bytes(), raw)
        self.assertEqual(self.current(), pointer)

    def test_delayed_manifest_uses_returned_document_and_legacy_gap_is_explicit(self):
        path = SITES["test"].rsplit("/", 1)[0] + "/specs_sedo/file.docx"
        first = self.resources[path]
        self.collect(1)
        self.resources[path] = document("replacement")
        self.collect(2)
        self.resources[path] = first
        self.collect(3)
        manifest, objects = feed_fixtures.FeedTests().capture(True)
        manifest["observed_at"] = "2026-11-14T04:00:00+00:00"
        remote = self.root / "feed"
        store_feed(remote, [manifest], objects)
        import_feed(self.state, lambda name: (remote / name).read_bytes())
        keys, _ = replay_history(self.state, self.current(), sha)
        past = json.loads((self.state / "snapshots" / (keys[0] + ".json")).read_bytes())
        self.assertEqual([r["sha256"] for r in past["entries"] if r["kind"] == "docx"],
                         [sha(document("prod")), sha(first)])
        # Historical installations only persisted first observations and the current pointer.
        pointer = json.loads((self.state / "current.json").read_bytes())
        write(self.state / "current.json", encode({"snapshot": pointer["snapshot"]}))
        keys, ledger = replay_history(self.state, self.current(), sha)
        self.assertEqual(keys, [])
        self.assertEqual(ledger["manifests"]["test"][manifest["content_id"]]["status"], "unavailable")

    def test_include_namespace_is_checked_through_chameleons_and_cached_urls(self):
        fixture = wsdl_fixtures.WsdlSourcesTests()
        base, seed, xs = wsdl_fixtures.BASE, wsdl_fixtures.SEED, wsdl_fixtures.XS
        def schema(body, namespace=""):
            target = f' targetNamespace="{namespace}"' if namespace else ""
            return f'<xs:schema xmlns:xs="{xs}"{target}>{body}</xs:schema>'.encode()
        def include(name):
            return f'<xs:include schemaLocation="{base}?xsd={name}.xsd"/>'
        resources = fixture.bundle()
        resources[base + "?xsd=main.xsd"] = schema(include("middle"), "urn:main")
        resources[base + "?xsd=middle.xsd"] = schema(include("leaf"))
        for namespace in ("", "urn:main", "urn:wrong"):
            resources[base + "?xsd=leaf.xsd"] = schema("", namespace)
            if namespace == "urn:wrong":
                with self.assertRaisesRegex(ValueError, "namespace"):
                    fixture.collect(resources)
            else:
                fixture.collect(resources)
        # Reuse one chameleon under two different effective namespaces.
        resources[base + "?xsd=leaf.xsd"] = schema("", "urn:main")
        resources[base + "?xsd=other.xsd"] = schema(include("middle"), "urn:other")
        resources[seed] = resources[seed].replace(b"</xs:schema>",
            f'<xs:import namespace="urn:other" schemaLocation="{base}?xsd=other.xsd"/></xs:schema>'.encode())
        with self.assertRaisesRegex(ValueError, "namespace"):
            fixture.collect(resources)


class ManualCorrectionTests(unittest.TestCase):
    def test_removing_title_override_restores_original_title_and_preserves_identity_and_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = Path(__file__).resolve().parents[1]
            shutil.copytree(project / 'web', root / 'web')
            shutil.copy2(project / 'viewer-template.html', root)
            empty = dict(format=1, pages={}, entries=[])
            digest = sha(encode(empty))
            write(root / 'state/snapshots' / (digest + '.json'), encode(empty))
            write(root / 'state/history.json', encode([digest]))
            write(root / 'state/current.json', encode(dict(snapshot=digest)))
            write(root / 'data/catalog.json', encode(dict(revisions=[], registry=[], updates=[])))
            write(root / 'data/fields/index.json', encode([]))
            folder, filename = root / 'incoming/test', '9997_v1.0.docx'
            write(folder / filename, document('manual', number='9997'))
            self.assertEqual(process_inbox(root)['new_revisions'], 1)
            original = json.loads((root / 'data/catalog.json').read_bytes())
            write(folder / 'metadata.json', encode({filename: dict(title='Removed title')}))
            process_inbox(root)
            overridden = json.loads((root / 'data/catalog.json').read_bytes())
            self.assertEqual(overridden['revisions'][0]['title'], 'Removed title')
            write(folder / 'metadata.json', encode({filename: {}}))
            report = process_inbox(root)
            restored = json.loads((root / 'data/catalog.json').read_bytes())
            self.assertEqual((report['processed'], report['new_revisions']), (1, 0))
            self.assertEqual(restored['revisions'][0]['title'], original['revisions'][0]['title'])
            self.assertEqual(restored['revisions'][0]['revision_id'], original['revisions'][0]['revision_id'])
            for key in ('updates', 'source_observations', 'update_sources'):
                self.assertEqual(restored[key], original[key])

    def test_inbox_updates_only_manual_metadata_without_rewriting_first_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = Path(__file__).resolve().parents[1]
            shutil.copytree(project / "web", root / "web")
            shutil.copy2(project / "viewer-template.html", root)
            empty = dict(format=1, pages={}, entries=[])
            digest = sha(encode(empty))
            write(root / "state/snapshots" / (digest + ".json"), encode(empty))
            write(root / "state/history.json", encode([digest]))
            write(root / "state/current.json", encode(dict(snapshot=digest)))
            write(root / "data/catalog.json", encode(dict(revisions=[], registry=[], updates=[])))
            write(root / "data/fields/index.json", encode([]))
            folder = root / "incoming/test"
            filename = "9997_v1.0.docx"
            write(folder / filename, document("manual", number='9997'))
            write(folder / "metadata.json", encode({filename: dict(title="Original")}))
            initial = process_inbox(root)
            old = json.loads((root / "data/catalog.json").read_bytes())
            self.assertEqual(initial["new_revisions"], 1)
            write(folder / "metadata.json", encode({filename: dict(version="1.1", date="2026-11-16",
                                                                   title="Corrected", source="New label")}))
            report = process_inbox(root)
            new = json.loads((root / "data/catalog.json").read_bytes())
            self.assertEqual((report["processed"], report["new_revisions"]), (1, 0))
            self.assertEqual(len(new["revisions"]), 1)
            row = new["revisions"][0]
            self.assertEqual((row["document_version"], row["label_date"], row["title"]), ("1.1", "16.11.2026", "Corrected"))
            self.assertEqual(row["manual_source"]["label"], "New label")
            self.assertEqual(row["revision_id"], old["revisions"][0]["revision_id"])
            for key in ("updates", "source_observations", "update_sources"):
                self.assertEqual(old[key], new[key])
            published = json.loads((root / "site/catalog-data.js").read_text("utf8").removeprefix("window.SEDO_CATALOG=").removesuffix(";"))
            self.assertEqual(published["revisions"][0]["edition_sources"][0]["document_version"], "1.1")
            self.assertEqual(published["schema_updates"][0]["event_sources"][0]["document_version"], "1.0")
            self.assertEqual(process_inbox(root)["processed"], 0)

            # Prior importers could acknowledge a signature without applying
            # its metadata. An unchanged incoming folder must repair that ledger.
            write(folder / "metadata.json", encode({filename: dict(version="1.2", title="Restored")}))
            task = collect(root)[0][0]
            ledger_path = root / "state/inbox.json"
            ledger = json.loads(ledger_path.read_bytes())
            saved = ledger["files"][task["path"].relative_to(root).as_posix()]
            saved["signature"] = task["signature"]
            del saved["explicit_fields"]
            write(ledger_path, encode(ledger))
            self.assertEqual(process_inbox(root)["new_revisions"], 0)
            recovered = json.loads((root / "data/catalog.json").read_bytes())
            self.assertEqual(recovered["revisions"][0]["document_version"], "1.2")
            self.assertEqual(recovered["updates"], old["updates"])
            self.assertEqual(process_inbox(root)["processed"], 0)
