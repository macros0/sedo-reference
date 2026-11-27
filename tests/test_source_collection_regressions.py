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

def individual_document(root, location=None):
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>'
    rows = [('Корневой элемент', root), ('Пространство имен файла xml-схемы', 'urn:main')]
    if location is not None:
        rows.append(('Расположение файла XML-схемы', location))
    for name, value in rows:
        xml += '<w:tr>' + ''.join('<w:tc><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:tc>' for text in (name, value)) + '</w:tr>'
    return archive('word/document.xml', (xml+'</w:tbl></w:body></w:document>').encode())


class SourceCollectionTests(unittest.TestCase):
    def add_individual(self, number, root, location):
        page = SITES['test']
        href = f'/specs_sedo/Тип_сообщения_{number}.docx'
        self.resources[page] += f'<a href="{href}">Individual {number}</a>'.encode()
        url = page.replace('/sedo.html', href)
        self.resources[url] = individual_document(root, location)
        return url

    def test_update_publishes_an_incomplete_individual_docx_with_diagnostics(self):
        from auto_update import _run
        from catalog import export
        from collector_snapshots import make_manifest
        from fields import build
        from wsdl_sources import _archive

        seed, xs = wsdl_fixtures.SEED, wsdl_fixtures.XS
        base = wsdl_fixtures.BASE.rsplit('/', 1)[0] + '/'
        schema = f'<xs:schema xmlns:xs="{xs}" xmlns:m="urn:main" targetNamespace="urn:main">'
        wrapper = ('<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/"><w:types>' + schema
                   + '<xs:include schemaLocation="root.xsd"/></xs:schema></w:types></w:definitions>').encode()
        incomplete = (schema + '<xs:element name="r" type="m:Code"/></xs:schema>').encode()
        healthy = (schema + '<xs:element name="other" type="xs:string"/></xs:schema>').encode()
        package = SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')
        self.resources[package] = _archive({'other.xsd': healthy})
        ready_url = self.add_individual('998', 'other', 'other.xsd')
        self.collect(1, wsdl_entries=[])
        index = self.root/'data/catalog.json'
        export(self.state, index)
        build(index, self.state, self.root/'data/fields')
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project/'web', self.root/'web')
        shutil.copy2(project/'viewer-template.html', self.root)
        original = (self.state/'current.json').read_bytes()

        missing_url = self.add_individual('999', 'r', 'root.xsd')
        support, objects = wsdl_fixtures.WsdlSourcesTests().collect({seed:wrapper, base+'root.xsd':incomplete})
        remote = self.root/'feed'
        store_feed(remote, [make_manifest(support,'2026-11-14T01:30:00+00:00')], objects)
        import_feed(self.state, lambda name: (remote/name).read_bytes())
        with patch('sync.get', side_effect=self.resources.__getitem__):
            _run(self.root)

        published = json.loads((self.root/'site/catalog-data.js').read_text('utf8').removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
        ready = next(row for row in published['revisions'] if row['document_url'] == ready_url)
        missing = next(row for row in published['document_sources'] if row['document_url'] == missing_url)
        self.assertTrue(ready['ready'])
        self.assertIn('root.xsd', missing['schema_reason'])
        for doc in (ready, missing):
            self.assertEqual((self.root/'site'/doc['document_download']).read_bytes(), self.resources[doc['document_url']])
        report = json.loads((self.root/'update-report.json').read_bytes())
        self.assertEqual(report['status'], 'ok')
        diagnosis = next(row for row in report['schema_diagnostics'] if row['source'] == missing_url)
        self.assertEqual(diagnosis['issues'][0]['reason'], missing['schema_reason'])
        module_url = SITES['test'].replace('/sedo.html', '/specs_sedo/file.docx')
        module_diagnosis = next(row for row in report['schema_diagnostics'] if row['source'] == module_url)
        [module_issue] = module_diagnosis['issues']
        self.assertEqual((module_issue['number'], module_issue['root'], module_issue['declared_namespace']),
                         ('999', 'response', 'urn:main'))
        self.assertEqual((module_issue['code'], module_issue['reason']),
                         ('module_binding_unmatched', 'Не найдена полная схема'))
        coverage = json.loads((self.root/'data/schema-coverage.json').read_bytes())
        self.assertEqual(next(row for row in coverage['missing'] if row['number'] == '999' and row['environment'] == 'test')['reasons'],
                         [module_issue['reason'], missing['schema_reason']])
        promoted = (self.state/'current.json').read_bytes()
        self.assertNotEqual(promoted, original)

        self.resources[package] = _archive({'other.xsd': healthy.replace(b'xs:string', b'm:MissingType')})
        with patch('sync.get', side_effect=self.resources.__getitem__), self.assertRaises(ValueError):
            _run(self.root)
        self.assertEqual((self.state/'current.json').read_bytes(), promoted)
        self.assertEqual(json.loads((self.root/'update-report.json').read_bytes())['status'], 'failed')

    def test_explicit_wsdl_evidence_does_not_change_other_document_editions(self):
        from catalog import export
        from wsdl_sources import _archive

        seed, xs = wsdl_fixtures.SEED, wsdl_fixtures.XS
        base = wsdl_fixtures.BASE.rsplit('/', 1)[0] + '/'
        schema = f'<xs:schema xmlns:xs="{xs}" targetNamespace="urn:main">'
        wrapper = (f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/"><w:types><xs:schema xmlns:xs="{xs}">'
                   '<xs:import namespace="urn:main" schemaLocation="root.xsd"/></xs:schema></w:types></w:definitions>').encode()
        root = (schema + '<xs:element name="r" type="xs:int"/></xs:schema>').encode()
        other = (schema + '<xs:element name="other" type="xs:string"/></xs:schema>').encode()
        support, objects = wsdl_fixtures.WsdlSourcesTests().collect({seed:wrapper, base+'root.xsd':root})
        self.resources[SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')] = _archive({'a.xsd':other, 'b.xsd':other})
        self.add_individual('998', 'other', 'b.xsd')
        self.add_individual('999', 'r', 'root.xsd')
        for digest, raw in objects.items():
            write(self.state/'objects'/digest, raw)
        index = self.root/'catalog.json'
        def editions():
            export(self.state, index)
            return json.loads(index.read_bytes())
        with patch('catalog.record_individual_wsdl_matches', return_value=False):
            self.collect(1, wsdl_entries=[support])
        baseline = editions()
        before, = [row for row in baseline['revisions'] if row['number'] == '998']
        self.assertEqual(before['schema_path'], 'a.xsd')
        self.collect(2, wsdl_entries=[support])
        recovered = editions()
        unaffected = [row for row in recovered['revisions'] if row['number'] == '998']
        self.assertEqual([row['schema_path'] for row in unaffected], ['a.xsd'])
        after, = unaffected
        self.assertEqual(after, before)
        self.assertEqual([event for event in recovered['updates'] if event['revision_id'] == after['revision_id']],
                         [event for event in baseline['updates'] if event['revision_id'] == before['revision_id']])
        self.assertEqual(recovered['source_observations'], baseline['source_observations'])

    def test_update_rejects_loss_of_ready_tree_for_same_published_docx(self):
        from auto_update import _run
        from catalog import export
        from collector_snapshots import make_manifest
        from fields import build

        seed, xs = wsdl_fixtures.SEED, wsdl_fixtures.XS
        base = wsdl_fixtures.BASE.rsplit('/', 1)[0] + '/'
        schema = f'<xs:schema xmlns:xs="{xs}" xmlns:m="urn:main" targetNamespace="urn:main">'
        self.add_individual('998', 'other', 'a.xsd')
        url = self.add_individual('999', 'r', 'root.xsd')
        self.resources[SITES['test'].replace('/sedo.html', '/specs_sedo/file.zip')] = archive(
            'a.xsd', (schema+'<xs:element name="other" type="xs:string"/></xs:schema>').encode())
        fixture = wsdl_fixtures.WsdlSourcesTests()
        def version(element):
            wrapper = ('<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/"><w:types>'+schema
                       +'<xs:include schemaLocation="root.xsd"/></xs:schema>'+schema
                       +'<xs:element name="r" type="xs:string"/></xs:schema></w:types></w:definitions>').encode()
            return fixture.collect({seed: wrapper, base+'root.xsd': (schema+element+'</xs:schema>').encode()})
        healthy, objects = version('<xs:element name="r" type="xs:int"/>')
        for digest, raw in objects.items():
            write(self.state/'objects'/digest, raw)
        self.collect(1, wsdl_entries=[healthy])
        index = self.root/'data/catalog.json'
        export(self.state, index)
        build(index, self.state, self.root/'data/fields')
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project/'web', self.root/'web')
        shutil.copy2(project/'viewer-template.html', self.root)
        before = json.loads(index.read_bytes())
        expected = next(row for row in before['revisions'] if row['document_url'] == url)
        self.assertEqual({r['number'] for r in before['revisions'] if r['listed_now']}, {'998', '999'})
        files = [self.state/'current.json', index,
                 self.root/'data/fields'/(expected['revision_id']+'.json')]
        original = [sha(path.read_bytes()) for path in files]
        remote = self.root/'feed'
        for minute, element, reason in [('30', '<xs:element name="r" type="m:Missing"/>', 'точка входа'),
                                        ('45', '<xs:element name="formerlyR" type="xs:int"/>', '{urn:main}r')]:
            broken, objects = version(element)
            store_feed(remote, [make_manifest(broken,f'2026-11-14T01:{minute}:00+00:00')], objects)
            import_feed(self.state, lambda name: (remote/name).read_bytes())
            with patch('sync.get', side_effect=self.resources.__getitem__), self.assertRaises(ValueError):
                _run(self.root)
            report = json.loads((self.root/'update-report.json').read_bytes())
            self.assertEqual(report['status'], 'failed')
            self.assertIn('999', report['error'])
            self.assertIn(reason, report['error'])
            self.assertEqual([sha(path.read_bytes()) for path in files], original)

        self.resources[SITES['test']] = self.resources[SITES['test']].replace(
            b'<a href="/specs_sedo/'+('Тип_сообщения_999.docx').encode()+b'">Individual 999</a>', b'')
        with patch('sync.get', side_effect=self.resources.__getitem__):
            _run(self.root)
        after = json.loads(index.read_bytes())
        self.assertEqual({r['number'] for r in after['revisions'] if r['listed_now']}, {'998'})
        self.assertFalse(next(r for r in after['revisions'] if r['revision_id'] == expected['revision_id'])['listed_now'])
        self.assertEqual(sha(files[-1].read_bytes()), original[-1])

    def test_history_completes_legacy_partial_receipt_at_original_observation(self):
        from catalog import derive, export
        from collector_snapshots import make_manifest, pending_work, replay_archived_inputs

        seed, xs = wsdl_fixtures.SEED, wsdl_fixtures.XS
        base = wsdl_fixtures.BASE.rsplit('/', 1)[0] + '/'
        schema = f'<xs:schema xmlns:xs="{xs}" xmlns:m="urn:main" targetNamespace="urn:main">'
        def wrapper(content):
            return ('<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/"><w:types>'+schema+content
                    +'</xs:schema></w:types></w:definitions>').encode()
        fixture = wsdl_fixtures.WsdlSourcesTests()
        root = (schema+'<xs:element name="r" type="m:Code"/></xs:schema>').encode()
        other = '<xs:element name="other" type="xs:int"/>'
        include = '<xs:include schemaLocation="published/root.xsd"/>'
        active, active_objects = fixture.collect({seed:wrapper(include+other),
            base+'published/root.xsd': root.replace(b'm:Code', b'xs:string')})
        historical, historical_objects = fixture.collect({seed:wrapper(include+other+
            '<xs:simpleType name="Code"><xs:restriction base="xs:int"/></xs:simpleType>'),
            base+'published/root.xsd': root})
        unrelated, unrelated_objects = fixture.collect({seed:wrapper(other)})
        self.resources[SITES['test']] = b'<a href="/specs_sedo/file.zip">Schema</a>'
        self.add_individual('998', 'other', None)
        self.add_individual('999', 'r', 'published/root.xsd')
        for digest, raw in active_objects.items():
            write(self.state/'objects'/digest, raw)
        self.collect(1, snapshot_format=6, wsdl_entries=[active])
        pointer = (self.state/'current.json').read_bytes()
        remote = self.root/'feed'
        active_receipt = make_manifest(active, '2026-11-14T02:00:00+00:00')
        receipt = make_manifest(historical, '2026-11-14T01:30:00+00:00')
        unrelated_receipt = make_manifest(unrelated, '2026-11-14T01:45:00+00:00')
        for item, received in ((receipt, historical_objects), (unrelated_receipt, unrelated_objects),
                               (active_receipt, active_objects)):
            store_feed(remote, [item], received)
        import_feed(self.state, lambda name: (remote/name).read_bytes())
        clean = self.root/'clean-state'
        shutil.copytree(self.state, clean)
        clean_record = next(row for row in replay_archived_inputs(clean) if row['content_id'] == receipt['content_id'])
        clean_snapshot = json.loads((clean/'snapshots'/(clean_record['snapshot']+'.json')).read_bytes())
        with patch('catalog.record_individual_wsdl_matches', return_value=False):
            old = {row['content_id']: row for row in replay_archived_inputs(self.state)}
        old_record = old[receipt['content_id']]
        self.assertEqual(old_record['status'], 'replayed')
        self.assertEqual([row['number'] for row in old_record['unmatched_documents']], ['999'])
        snapshots = self.state/'snapshots'
        old_path = snapshots/(old_record['snapshot']+'.json')
        original = old_path.read_bytes()
        before = derive(json.loads(original))
        self.assertEqual([row['number'] for row in before['revisions']], ['998'])
        old_history = json.loads((self.state/'history.json').read_bytes())
        self.assertEqual(pending_work(self.root), 'history')
        updated = {row['content_id']: row for row in replay_archived_inputs(self.state)}
        record = updated[receipt['content_id']]
        self.assertEqual(record['status'], 'replayed')
        self.assertEqual(record['observed_at'], receipt['observed_at'])
        self.assertNotIn('unmatched_documents', record)
        after = derive(json.loads((snapshots/(record['snapshot']+'.json')).read_bytes()))
        self.assertEqual(after, derive(clean_snapshot))
        self.assertEqual({row['number'] for row in after['revisions']}, {'998','999'})
        self.assertIn(before['revisions'][0], after['revisions'])
        self.assertEqual(old_path.read_bytes(), original)
        self.assertEqual(json.loads((self.state/'history.json').read_bytes()), [*old_history, record['snapshot']])
        self.assertEqual((self.state/'current.json').read_bytes(), pointer)
        for item in (active_receipt, unrelated_receipt):
            self.assertEqual(updated[item['content_id']], old[item['content_id']])
        destination = self.root/'catalog.json'
        export(self.state, destination)
        recovered, = [event for event in json.loads(destination.read_bytes())['updates']
                      if event['snapshot'] == record['snapshot']]
        self.assertEqual(recovered['observed_at'], receipt['observed_at'])
        self.assertIsNone(pending_work(self.root))
        self.assertEqual({row['content_id']: row for row in replay_archived_inputs(self.state)}, updated)

    def test_history_retries_only_eligible_unavailable_explicit_wsdl_receipt(self):
        from catalog import derive
        from collector_snapshots import make_manifest, pending_work, replay_archived_inputs

        seed, xs = wsdl_fixtures.SEED, wsdl_fixtures.XS
        base = wsdl_fixtures.BASE.rsplit('/', 1)[0] + '/'
        schema = f'<xs:schema xmlns:xs="{xs}" xmlns:m="urn:main" targetNamespace="urn:main">'
        def wrapper(content):
            return ('<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/"><w:types>' + schema + content
                    + '</xs:schema></w:types></w:definitions>').encode()
        root = (schema + '<xs:element name="r" type="m:Code"/></xs:schema>').encode()
        types = (schema + '<xs:simpleType name="Code"><xs:restriction base="xs:int"/></xs:simpleType></xs:schema>').encode()
        fixture = wsdl_fixtures.WsdlSourcesTests()
        include = '<xs:include schemaLocation="root.xsd"/>'
        current, active_objects = fixture.collect({seed:wrapper(include), base+'root.xsd':root.replace(b'm:Code', b'xs:string')})
        archival, archival_objects = fixture.collect({seed:wrapper(include + '<xs:include schemaLocation="types.xsd"/>'),
            base+'root.xsd':root, base+'types.xsd':types}, legacy_wsdl_layout=True)
        unrelated, unrelated_objects = fixture.collect({seed:wrapper('<xs:element name="other" type="xs:int"/>')})
        incomplete, incomplete_objects = fixture.collect({seed:wrapper(include), base+'root.xsd':root}, legacy_wsdl_layout=True)
        self.add_individual('999', 'r', 'root.xsd')
        for digest, raw in active_objects.items():
            write(self.state/'objects'/digest, raw)
        self.collect(1, snapshot_format=6, wsdl_entries=[current])
        pointer = (self.state/'current.json').read_bytes()
        snapshot = self.state/'snapshots'/(json.loads(pointer)['snapshot']+'.json')
        snapshot_raw = snapshot.read_bytes()
        remote = self.root/'feed'
        manifests = [make_manifest(library, f'2026-11-14T{time}:00+00:00') for library, time in (
            (current, '02:00'), (archival, '01:30'), (unrelated, '01:45'), (incomplete, '01:50'))]
        for index, objects in ((1, archival_objects), (2, unrelated_objects), (3, incomplete_objects), (0, active_objects)):
            store_feed(remote, [manifests[index]], objects)
        import_feed(self.state, lambda name: (remote/name).read_bytes())
        active_id, archive_id, unrelated_id, incomplete_id = [manifest['content_id'] for manifest in manifests]
        with patch('catalog.record_individual_wsdl_matches', return_value=False):
            old = {row['content_id']: row for row in replay_archived_inputs(self.state)}
        self.assertEqual([old[key]['status'] for key in (archive_id, unrelated_id, incomplete_id, active_id)],
                         ['unavailable', 'unavailable', 'unavailable', 'recorded'])
        self.assertEqual(pending_work(self.root), 'history')
        updated = {row['content_id']: row for row in replay_archived_inputs(self.state)}
        self.assertEqual(updated[archive_id]['status'], 'replayed')
        for key in (unrelated_id, active_id):
            self.assertEqual(updated[key], old[key])
        self.assertEqual(updated[incomplete_id]['status'], 'unavailable')
        self.assertIn('root.xsd', updated[incomplete_id]['unmatched_documents'][0]['reason'])
        self.assertEqual(updated[archive_id]['observed_at'], old[archive_id]['observed_at'])
        saved = json.loads((self.state/'snapshots'/(updated[archive_id]['snapshot']+'.json')).read_bytes())
        edition, = derive(saved)['revisions']
        self.assertTrue(edition['schema_path'].endswith('/inline-0.xsd'))
        self.assertEqual(edition['archive_sha256'], archival['sha256'])
        self.assertEqual((self.state/'objects'/archival['sha256']).read_bytes(), archival_objects[archival['sha256']])
        self.assertEqual((self.state/'current.json').read_bytes(), pointer)
        self.assertEqual(snapshot.read_bytes(), snapshot_raw)
        self.assertIsNone(pending_work(self.root))
        self.assertEqual({row['content_id']: row for row in replay_archived_inputs(self.state)}, updated)

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

    def test_numbered_docx_without_extension_is_downloaded_and_replayed(self):
        from urllib.error import HTTPError
        from catalog import derive
        from verify import verify_snapshot_replay

        page = SITES['test']
        path = '/specs_sedo/Приложение_6_Возмещения_Том_3_Тип_сообщения_12010_v3.4.0_тест_23.11.26'
        url = page.replace('/sedo.html', path)
        label = 'Приложение_6 ВОЗМ Том_3_Тип_сообщения_12010 v3.4.0 (23.11.2026).docx'
        self.resources[page] += f'<a href="{path}">{label}</a>'.encode()
        docx = individual_document('response')
        def get(candidate, **kwargs):
            if candidate == url:
                raise HTTPError(url, 404, 'Not found', {}, None)
            return self.resources[candidate]
        self.resources[url + '.docx'] = docx
        self.resources[page.replace('/sedo.html', '/specs_sedo/file.zip')] = archive('root.xsd',
            b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main">'
            b'<xs:element name="response" type="xs:string"/></xs:schema>')
        with patch('sync.get', side_effect=get):
            sync(self.state, wsdl_entries=[])
        snapshot = self.current()
        row = next(row for row in snapshot['entries'] if row['url'] == url)
        self.assertEqual((row['kind'], row['sha256']), ('docx', sha(docx)))
        self.assertEqual(row['download_url'], url + '.docx')
        key = json.loads((self.state/'current.json').read_bytes())['snapshot']
        verify_snapshot_replay(self.state, snapshot, key)
        volume = next(row for row in derive(snapshot)['revisions'] if row['number'] == '12010')
        self.assertEqual(volume['status'], 'matched')
        self.assertEqual(volume['document_url'], url)
        self.assertEqual(volume['document_official_url'], url + '.docx')

    def test_numbered_volume_does_not_repair_a_link_to_another_version(self):
        page = SITES['test']
        path = '/specs_sedo/Приложение_6_Том_3_Тип_сообщения_12010_v3.3.0_тест_23.11.26'
        label = 'Приложение_6 Том_3_Тип_сообщения_12010 v3.4.0 (23.11.2026).docx'
        self.resources[page] += f'<a href="{path}">{label}</a>'.encode()
        self.collect(1, wsdl_entries=[])
        row = next(row for row in self.current()['entries'] if row['url'].endswith(path))
        self.assertNotIn('sha256', row)

    def test_missing_numbered_docx_keeps_previous_snapshot(self):
        from urllib.error import HTTPError

        self.collect(1)
        previous = (self.state / 'current.json').read_bytes()
        page = SITES['test']
        href = '/specs_sedo/Приложение_6_Том_5_Тип_сообщения_12210_v3.2.0_23.11.26'
        label = 'Приложение_6 Том_5_Тип_сообщения_12210 v3.2.0.docx'
        self.resources[page] += f'<a href="{href}">{label}</a>'.encode()
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                def get(url, **kwargs):
                    if href in url:
                        if corrupt:
                            return b'<html>Error page</html>'
                        raise HTTPError(url, 404, 'Not found', {}, None)
                    return self.resources[url]
                with patch('sync.get', side_effect=get), self.assertRaisesRegex(ValueError, '1 file errors'):
                    sync(self.state, wsdl_entries=[])
                self.assertEqual((self.state / 'current.json').read_bytes(), previous)

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
