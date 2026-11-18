import io
import json
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import build_site
from sync import encode, sha, write


class DocumentArchiveTests(unittest.TestCase):
    def test_legacy_registry_titles_follow_the_number_into_historical_events(self):
        from catalog import _export, derive
        from document_metadata import read_document_display, snapshot_display
        from edition_groups import display_updates, group_editions
        from sync import inspect_file
        from tests.test_fix20r_pipeline import archive, word_table
        from tests.test_source_collection_regressions import individual_document

        state = self.root / 'title-state'
        header = ['Код типа сообщения', 'Наименование', 'Описание', 'Приложение']
        table = word_table([header, ['14', 'broken', '', '2'], ['15', 'ab', '', '2'],
                            ['16', 'broken', '', '2'], ['16', 'ab', '', '2']])
        with zipfile.ZipFile(io.BytesIO(table)) as doc:
            original = archive('word/document.xml', doc.read('word/document.xml').replace(
                b'<w:t>broken</w:t>', b'<w:t>a</w:t><w:br/><w:t>b</w:t>'))
        def document(raw, filename, fmt):
            digest = sha(raw)
            write(state / 'objects' / digest, raw)
            return dict(inspect_file(raw, 'docx', legacy_docx=fmt < 7), kind='docx', environment='prod',
                        url='https://example.test/'+filename, label=filename, sha256=digest)

        def snapshot(fmt):
            doc = individual_document('r')
            entries = [document(original, 'registry.docx', fmt)]
            entries += [document(doc, f'Тип сообщения {number}.docx', fmt) for number in ('14', '15', '16')]
            schema = b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:element name="r" type="xs:string"/></xs:schema>'
            package = archive('main.xsd', schema)
            entries.append(dict(inspect_file(package, 'zip'), kind='zip', environment='prod',
                                url='https://example.test/schemas.zip', sha256=sha(package)))
            raw = encode(dict(format=fmt, entries=entries))
            sid = sha(raw)
            write(state / 'snapshots' / (sid+'.json'), raw)
            return sid, raw

        old, modern = snapshot(6), snapshot(7)
        for (sid, raw), legacy_format in ((old, True), (modern, False)):
            data = derive(json.loads(raw))
            present = snapshot_display(data['registry'], {sha(original): read_document_display(original)}, {},
                                       legacy=legacy_format)
            self.assertEqual([(r['number'], present(r, registry_row=True)['title']) for r in data['registry']],
                             [('14', 'a b'), ('15', 'ab'), ('16', 'a b'), ('16', 'ab')], sid)
        exports = []
        for history in ([old], [old, modern]):
            write(state / 'history.json', encode([sid for sid, _ in history]))
            write(state / 'current.json', encode(dict(snapshot=history[-1][0])))
            target = self.root / 'titles.json'
            _export(state, target)
            exports.append(json.loads(target.read_bytes()))
        legacy, latest = exports
        self.assertEqual(legacy['source_observations'], latest['source_observations'])
        self.assertEqual(legacy['updates'], latest['updates'])
        self.assertEqual({x['source']['number']: x['source']['title'] for x in latest['source_observations']},
                         {'14': 'ab', '15': 'ab', '16': 'ab'})
        self.assertEqual({r['number']: r['title'] for r in latest['revisions']}, {'14': 'a b', '15': 'ab', '16': 'a b'})
        expected_legacy = {'14': 'a b', '15': 'ab', '16': 'a b'}
        self.assertEqual({r['number']: r['title'] for r in legacy['revisions']}, expected_legacy)
        public = group_editions(latest, lambda _: [])
        public['schema_updates'] = display_updates(public)
        build_site.share_document_histories(public)
        self.assertEqual({s['number']: s['title'] for event in public['schema_updates'] for s in event['event_sources']},
                         expected_legacy)
        for sid, raw in (old, modern):
            self.assertEqual((state / 'snapshots' / (sid+'.json')).read_bytes(), raw)

    def test_duplicate_legacy_registry_rows_keep_their_original_word_cells(self):
        from catalog import derive
        from document_metadata import read_document_display, snapshot_display
        from sync import inspect_file
        from tests.test_fix20r_pipeline import archive, word_table

        cells = ['14', 'Title', 'Description', '2']
        table = word_table([['Код типа сообщения','Наименование','Описание','Приложение'],
                            ['ab'], cells, ['ab'], cells])
        with zipfile.ZipFile(io.BytesIO(table)) as doc:
            xml = doc.read('word/document.xml').replace(b'<w:t>ab</w:t>', b'<w:t>a</w:t><w:br/><w:t>b</w:t>', 1)
        raw = archive('word/document.xml',xml)
        registry = dict(inspect_file(raw, 'docx', legacy_docx=True), kind='docx', environment='test',
                        url='https://example.test/registry.docx', label='Registry', sha256=sha(raw))
        match = dict(number='14',title='Title',dependency_digest='deps')
        module = dict(kind='docx',environment='test',url='https://example.test/Пр_02_T.docx',
                      sha256='module',label='Module',module_matches=[match])
        bindings = {}
        data = derive(dict(format=6, entries=[registry,module]), display_registry=bindings)
        present = snapshot_display(data['registry'], {sha(raw):read_document_display(raw)}, bindings, legacy=True)
        self.assertEqual([present(r, registry_row=True)['module'] for r in data['registry']], ['a b','ab'])
        self.assertEqual(present(data['revisions'][0])['module'], 'a b')

    def test_snapshot_binds_legacy_module_and_current_title_to_its_source(self):
        import os
        import subprocess
        import sys
        from edition_groups import display_updates, group_editions, source_updates
        from sync import inspect_file
        from tests.test_fix20r_pipeline import archive, word_table

        state = self.root / 'state'
        header = ['Код типа сообщения', 'Наименование', 'Описание', 'Приложение']
        registry = word_table([header, ['ab'], ['14', 'Service', '', '2'], ['701', 'Service', '', '2']])
        def replace_word(raw, replacements):
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                xml = z.read('word/document.xml')
            for before, after in replacements:
                xml = xml.replace(before.encode(), after.encode())
            return archive('word/document.xml', xml)

        first_registry = replace_word(registry, [('<w:t>ab</w:t>', '<w:t>a</w:t><w:br/><w:t>b</w:t>')])
        paragraphs = ''.join('<w:p><w:r><w:t>Тип сообщения '+number+': '+title+'</w:t></w:r></w:p>'
                             for number, title in [('14', 'a</w:t><w:br/><w:t>bc'),
                                                   ('701', 'a b</w:t><w:br/><w:t>c'),
                                                   ('20', 'a</w:t><w:br/><w:t>b')])
        module = replace_word(word_table([['Поле', 'Описание']]), [('</w:body>', paragraphs+'</w:body>')])
        def entry(raw, filename, fmt):
            write(state / 'objects' / sha(raw), raw)
            return dict(inspect_file(raw, 'docx', legacy_docx=fmt < 7), kind='docx', environment='prod',
                        url='https://example.test/'+filename, label=filename, sha256=sha(raw))

        for key, raw, fmt in [('first', first_registry, 6), ('second', registry, 6), ('modern', registry, 7)]:
            doc = entry(module, 'Пр_02_'+key+'.docx', fmt)
            doc['module_matches'] = [dict(message, root='r', namespace=None, dependency_digest='deps',
                                           schema_path='main.xsd', archive_sha256='archive', archive_url='x.zip')
                                     for message in doc['message_types'] if message['number'] in ('14', '701')]
            write(state/'snapshots'/(key+'.json'), encode(dict(format=fmt, entries=[entry(raw, 'registry.docx', fmt), doc])))
        write(state/'history.json', encode(['first', 'second', 'modern']))
        write(state/'current.json', encode(dict(snapshot='modern')))
        target = self.root / 'catalog.json'
        outputs = []
        for seed in ('1', '2', '3'):
            subprocess.run([sys.executable, '-c', 'from catalog import _export; from pathlib import Path; import sys; '
                            '_export(Path(sys.argv[1]), Path(sys.argv[2]))', str(state), str(target)],
                           env={**os.environ, 'PYTHONHASHSEED':seed}, capture_output=True, check=True)
            outputs.append(target.read_bytes())
        self.assertEqual(len(set(outputs)), 1)
        data = json.loads(outputs[0])
        raw = [s['source'] for s in data['source_observations'] if s['source']['number'] == '14']
        self.assertEqual([r['title'] for r in raw], ['abc', 'abc', 'a bc'])
        self.assertEqual([r['module'] for r in raw], ['ab'] * 3)
        expected_titles = {'14':'a bc', '701':'a b c'}
        self.assertEqual({r['number']:r['title'] for r in data['revisions']}, expected_titles)
        public = group_editions(data, lambda _: [])
        public['schema_updates'] = display_updates(public)
        public['source_updates'] = source_updates(public)
        build_site.share_document_histories(public)
        for row in public['revisions']:
            self.assertEqual([s['module'] for s in row['edition_sources']], ['a b','ab','ab'])
            self.assertEqual([s['title'] for s in row['edition_sources']], [expected_titles[row['number']]] * 3)
        self.assertTrue(all(e['event_sources'][0]['module'] == 'a b' for e in public['schema_updates']))
        self.assertTrue(all(e['source']['module'] == 'ab' for e in public['source_updates']))
        self.assertTrue(all(e['source']['title'] == expected_titles[e['source']['number']] for e in public['source_updates']))
        once = encode(public)
        build_site.share_document_histories(public)
        self.assertEqual(encode(public), once)

    def test_registry_display_correction_stays_with_contour_and_message(self):
        from catalog import _export
        from edition_groups import group_editions
        from sync import inspect_file
        from tests.test_fix20r_pipeline import archive, word_table

        header = ['Код типа сообщения', 'Наименование', 'Описание', 'Приложение']
        def registry(*numbers, appendix='2'):
            return word_table([header, ['ab'], *[[number, 'Service', '', appendix] for number in numbers]])

        with zipfile.ZipFile(io.BytesIO(registry('14', '16'))) as z:
            xml = z.read('word/document.xml').replace(
                b'<w:t>ab</w:t>', b'<w:t>a</w:t><w:br/><w:t>b</w:t>', 1)
        prod = archive('word/document.xml', xml)
        with zipfile.ZipFile(io.BytesIO(word_table([['Поле', 'Описание'], ['tag', 'Value']]))) as z:
            module = z.read('word/document.xml')
        shared = archive('word/document.xml', module.replace(
            b'</w:body>', '<w:p><w:r><w:t>Тип сообщения 14: a</w:t><w:br/><w:t>b</w:t></w:r></w:p></w:body>'.encode()))
        state = self.root / 'state'

        def document(raw, env, filename, **extra):
            digest = sha(raw)
            write(state / 'objects' / digest, raw)
            return dict(inspect_file(raw, 'docx', legacy_docx=True),
                        kind='docx', environment=env, url='https://example.test/'+filename,
                        label='Document', sha256=digest, **extra)

        def match(number):
            return dict(number=number, title='ab' if number == '14' else 'Service', root='r', namespace=None,
                        dependency_digest='deps', schema_path='main.xsd',
                        archive_sha256='archive', archive_url='x.zip')

        # A registry can spell the same appendix as '02'; derive selects
        # the literal '2' entry for the module parsed from Пр_02.
        entries = [document(registry('14', '16', appendix='02'), 'prod', 'padded.docx'),
                   document(prod, 'prod', 'registry.docx'),
                   document(registry('15', '17'), 'prod', 'other-registry.docx'),
                   document(registry('14', '18', appendix='12'), 'prod', 'other-appendix.docx'),
                   document(registry('14', '15'), 'test', 'registry.docx'),
                   document(shared, 'prod', 'Пр_02_Module.docx', module_matches=[match('14'), match('15')]),
                   document(shared, 'test', 'Пр_02_Module.docx', module_matches=[match('14'), match('15')])]
        snapshot = encode(dict(format=6, entries=entries))
        sid = sha(snapshot)
        write(state / 'snapshots' / (sid+'.json'), snapshot)
        write(state / 'current.json', encode(dict(snapshot=sid)))
        write(state / 'history.json', encode([sid]))
        target = self.root / 'catalog.json'
        _export(state, target)
        data = json.loads(target.read_bytes())
        expected = {('prod', '14'): 'a b', ('prod', '15'): 'ab', ('test', '14'): 'ab', ('test', '15'): 'ab'}
        self.assertTrue(all(row['source']['module'] == 'ab' for row in data['source_observations']))
        self.assertEqual({(r['environment'], r['number']): r['module'] for r in data['revisions']}, expected)
        self.assertTrue(all(r['title'] == 'a b' for r in data['revisions'] if r['number'] == '14'))
        public = group_editions(data, lambda _: [])
        build_site.share_document_histories(public)
        for rows in ([r['edition_sources'][0] for r in public['revisions']],
                     [r['source'] for r in public['source_observations']]):
            self.assertEqual({(r['environment'], r['number']): r['module'] for r in rows}, expected)

    def test_display_replacements_apply_once_to_corrected_and_shared_sources(self):
        source = dict(document_sha256=self.digest, number='14', title='abc')
        catalog = dict(revisions=[dict(source, title='ab c', display_metadata_applied=True)],
                       source_observations=[dict(source=source)], update_sources={'r':source},
                       document_display={self.digest:dict(text_replacements={'abc':'ab c','ab c':'a b c'})})
        build_site.share_document_histories(catalog)
        self.assertEqual(catalog['revisions'][0]['title'], 'ab c')
        self.assertNotIn('display_metadata_applied', catalog['revisions'][0])
        self.assertEqual(catalog['source_observations'][0]['source']['title'], 'ab c')
        self.assertEqual(catalog['update_sources']['r']['title'], 'ab c')

    def test_public_display_enrichment_keeps_saved_source_evidence(self):
        catalog = json.loads((self.root/'data/catalog.json').read_bytes())
        old = [dict(version='1', date='17.11.2026', description='wordstogether')]
        corrected = [dict(version='1', date='17.11.2026', description='words together')]
        for row in catalog['revisions']:
            row.update(title='Old title', module='Old module', document_history=old)
        catalog['registry'] = [dict(catalog['revisions'][0], source_type='module')]
        catalog['document_display'] = {self.digest:dict(document_history=corrected,
            text_replacements={'Old title':'Correct title', 'Old module':'Correct module'})}
        write(self.root/'data/catalog.json', encode(catalog))
        saved = (self.root/'data/catalog.json').read_bytes()
        self.build()
        public = json.loads((self.root/'site/catalog-data.js').read_text('utf8')
                            .removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
        self.assertEqual(public['document_histories'][self.digest], corrected)
        self.assertEqual(public['revisions'][0]['title'], 'Old title')
        self.assertEqual(public['registry'][0]['title'], 'Correct title')
        self.assertEqual(public['registry'][0]['module'], 'Correct module')
        self.assertNotIn('document_display', public)
        self.assertEqual((self.root/'data/catalog.json').read_bytes(), saved)

    def test_document_history_is_shared_by_hash_in_public_catalog(self):
        catalog = json.loads((self.root/'data/catalog.json').read_bytes())
        changes = [dict(version='1.0', date='17.11.2026', description='Original history')]
        for row in catalog['revisions']:
            row['document_history'] = changes
        write(self.root/'data/catalog.json', encode(catalog))
        self.build()
        published = json.loads((self.root/'site/catalog-data.js').read_text('utf8')
                               .removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
        self.assertEqual(published['document_histories'], {self.digest: changes})
        self.assertTrue(all('document_history' not in row for row in published['revisions']))
        self.assertEqual(json.loads((self.root/'data/catalog.json').read_bytes()), catalog)

    def test_first_cli_build_accepts_precreated_empty_output(self):
        target = self.root/'preview'
        target.mkdir()
        with patch.object(build_site, 'HERE', self.root):
            build_site.cli(target)
        self.assertTrue((target/'index.html').is_file())

    def test_cli_preserves_cname_and_rejects_unknown_files_before_replacement(self):
        self.build()
        cname = b'example.test\n'
        write(self.root/'site/CNAME', cname)
        with patch.object(build_site, 'HERE', self.root):
            build_site.cli()
        self.assertEqual((self.root/'site/CNAME').read_bytes(), cname)
        write(self.root/'site/owner-notes.txt', b'keep')
        before = self.site_bytes()
        with patch.object(build_site, 'HERE', self.root), self.assertRaisesRegex(ValueError, 'owner-notes.txt'):
            build_site.cli()
        self.assertEqual(self.site_bytes(), before)

    def test_full_promotion_preserves_cname_and_rejects_unknown_files(self):
        from auto_update import promote
        self.build()
        stage = self.root/'staged'
        for folder in ('state', 'data', 'site'):
            shutil.copytree(self.root/folder, stage/folder)
        write(self.root/'site/CNAME', b'example.test\n')
        promote(self.root, stage)
        self.assertEqual((self.root/'site/CNAME').read_bytes(), b'example.test\n')
        self.assert_site_unknown_file_blocks_promotion()

    def assert_site_unknown_file_blocks_promotion(self):
        from auto_update import promote
        unknown = self.root / 'site/owner-notes.txt'
        unknown.write_bytes(b'keep')
        before = self.site_bytes()
        stage = self.root / 'stage'
        for folder in ('state', 'data', 'site'):
            (stage/folder).mkdir(parents=True, exist_ok=True)
        with self.assertRaisesRegex(ValueError, 'owner-notes.txt'):
            promote(self.root, stage)
        self.assertEqual(self.site_bytes(), before)
        self.assertFalse((self.root/'.update-recovery').exists())

    def test_cli_preserves_unknown_files_and_removes_retired_generated_pages(self):
        self.build()
        write(self.root/'site/owner-notes.txt', b'keep')
        before = self.site_bytes()
        with patch.object(build_site, 'HERE', self.root), self.assertRaisesRegex(ValueError, 'owner-notes.txt'):
            build_site.cli()
        self.assertEqual(self.site_bytes(), before)
        (self.root/'site/owner-notes.txt').unlink()
        retired = self.root/'site/editions'/('a'*64+'.html')
        write(retired, b'old generated page')
        with patch.object(build_site, 'HERE', self.root):
            build_site.cli()
        self.assertFalse(retired.exists())

    def site_bytes(self):
        return {p.relative_to(self.root / 'site').as_posix(): p.read_bytes()
                for p in (self.root / 'site').rglob('*') if p.is_file()}

    def test_failed_cli_build_keeps_the_complete_previous_generation(self):
        self.build()
        before = self.site_bytes()
        write(self.root / 'web/app.js', b'new generation')
        (self.root / 'web/vendor/xmllint/README.md').unlink()
        with patch.object(build_site, 'HERE', self.root), self.assertRaises(FileNotFoundError):
            build_site.cli()
        self.assertEqual(self.site_bytes(), before)

    def test_output_directory_is_separate_from_inputs_and_active_site(self):
        self.build()
        before = self.site_bytes()
        write(self.root / 'web/app.js', b'isolated generation')
        with patch.object(build_site, 'HERE', self.root):
            build_site.main(output=self.root / 'candidate')
        self.assertEqual(self.site_bytes(), before)
        self.assertEqual((self.root / 'candidate/app.js').read_bytes(), b'isolated generation')

    def test_interrupted_site_promotion_is_recovered_before_rebuild(self):
        self.build()
        before = self.site_bytes()
        write(self.root / 'web/app.js', b'new generation')
        replace = Path.replace
        def interrupt(path, target):
            if Path(target) == self.root / 'site' and Path(path).name == 'candidate':
                raise SystemExit('interrupted site replacement')
            return replace(path, target)
        with patch.object(build_site, 'HERE', self.root), patch.object(Path, 'replace', interrupt):
            with self.assertRaises(SystemExit):
                build_site.cli()
        # Other writers recover the site too, before starting their transaction.
        from auto_update import update_lock
        with update_lock(self.root):
            self.assertEqual(self.site_bytes(), before)
        with update_lock(self.root):
            self.assertEqual(self.site_bytes(), before)
        # A failing retry must still restore all prior files before touching inputs.
        (self.root / 'web/app.js').unlink()
        with patch.object(build_site, 'HERE', self.root), self.assertRaises(FileNotFoundError):
            build_site.cli()
        self.assertEqual(self.site_bytes(), before)
        write(self.root / 'web/app.js', b'new generation')
        with patch.object(build_site, 'HERE', self.root):
            build_site.cli()
        self.assertEqual((self.root / 'site/app.js').read_bytes(), b'new generation')
        self.assertFalse((self.root / '.site-recovery').exists())

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
        revision = dict(revision_id="edition1", document_sha256=self.digest, document_version="3.0", number="10110", environment="prod", status="document_only", source_type="manual")
        write(self.root / "data/catalog.json", encode(dict(revisions=[revision, dict(revision, revision_id="edition2")])))
        write(self.root / "data/fields/index.json", encode([dict(revision_id=rid,status="document_only") for rid in ("edition1","edition2")]))
        write(self.root / "viewer-template.html", b"<html></html>")
        for name in ("review-journal.js", "index.html", "app.js", "app.css", "automation-status.js", "compare-core.js", "compare.js", "comparison-export.js", "mapping-draft.js", "mapping-editor.js", "comparison-mapping.js", "vendor/exceljs.min.js", "vendor/exceljs-LICENSE", "vendor/README.md", "journal.js", "message-transitions.js", "favicon.svg", "validation.js", "validation-core.js", "validation-frame.js", "validation-worker.js", "validation-paths.js", "validation-source.js", "validation-messages.js", "vendor/xmllint/xmllint-browser.mjs", "vendor/xmllint/xmllint.wasm", "vendor/xmllint/COPYING", "vendor/xmllint/README.md"):
            write(self.root / "web" / name, b"test")

    def build(self):
        with patch.object(build_site, "HERE", self.root):
            build_site.main()

    def test_exact_bytes_shared_by_editions_and_offline_rebuild(self):
        self.build()
        self.assertTrue((self.root / "site/.nojekyll").is_file())
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
        for name in ('publish_sources', 'publish_bundles'):
            stub=patch.object(build_site,name)
            stub.start()
            self.addCleanup(stub.stop)
        catalog=json.loads((self.root/'data/catalog.json').read_bytes())
        fields=[dict(name='fault',path='fault',parent=-1,children=[],kind='element',min=1,max=1,facets=[],values=[],flags={})]
        for row in catalog['revisions']:
            row.update(status='matched',root='fault',namespace='urn:fault',label_date=None,archive_url='https://example.test/schema.zip',
                       archive_sha256='xsd-archive',dependencies=[['Fault.xsd','same-xsd']],
                       document_url='https://example.test/'+row['revision_id']+'.docx')
            write(self.root/'data/fields'/(row['revision_id']+'.json'),encode(dict(revision_id=row['revision_id'],fields=fields)))
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
        first_hashes={r['revision_id']:r['fields_sha256'] for r in published['revisions']}
        for rid,digest in first_hashes.items():
            self.assertEqual(digest,sha((self.root/'site/fields'/(rid+'.js')).read_bytes()))
        fields[0]['doc']='Updated schema description'
        for row in catalog['revisions']:
            write(self.root/'data/fields'/(row['revision_id']+'.json'),encode(dict(revision_id=row['revision_id'],fields=fields)))
        self.build()
        rebuilt=json.loads((self.root/'site/catalog-data.js').read_text('utf8').removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
        for row in rebuilt['revisions']:
            self.assertNotEqual(row['fields_sha256'],first_hashes[row['revision_id']])


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
