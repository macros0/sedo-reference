import io
import json
import tempfile
import shutil
from unittest.mock import patch
import unittest
import zipfile
from pathlib import Path

from catalog import derive, export
from build_site import archive_documents
from sync import inspect_file, sha, encode, write

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
def docx(body):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr('word/document.xml',f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
    return out.getvalue()
def paragraph(text,style=''):
    props=f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ''
    return f'<w:p>{props}<w:r><w:t>{text}</w:t></w:r></w:p>'
def entry(env='prod',number='611'):
    content=docx(paragraph(f'Тип сообщения {number}: Решение по проверке'))
    return dict(environment=env,kind='docx',url='https://example.test/Спецификация v2_7_Пр_12_Доходная часть (09.06.2026).docx',label='Спецификация v2.7 (09.06.2026)',sha256=sha(content),**inspect_file(content,'docx')),content


def collect_declaration_fixture(fixture, mode):
    from html import escape
    from tests.test_wsdl_sources import SEED, XS
    from sync import SITES
    from wsdl_sources import collect

    title = paragraph('Тип сообщения 42: Example')
    table = '<w:tbl><w:tr>' + ''.join('<w:tc>' + paragraph(escape(v)) + '</w:tc>' for v in
                                     ('Схема', '<xs:element name="r"/>')) + '</w:tr></w:tbl>'
    own = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:own"><xs:element name="r" type="xs:string"/></xs:schema>'
    external = (f'<xs:schema xmlns:xs="{XS}" xmlns:d="urn:dep" targetNamespace="urn:external">'
                '<xs:import namespace="urn:dep" schemaLocation="dep.xsd"/>'
                '<xs:element name="r" type="d:custom"/></xs:schema>')
    dep = (f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:dep">'
           '<xs:simpleType name="custom"><xs:restriction base="xs:int"/></xs:simpleType></xs:schema>')
    definitions = (f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/"><w:types><xs:schema xmlns:xs="{XS}">'
                   '<xs:import namespace="urn:dep" schemaLocation="dep.xsd"/></xs:schema></w:types></w:definitions>')
    page = SITES['test']
    url = page.replace('/sedo.html', '/specs_sedo/module.docx')
    fixture.resources[page] += b'<a href="/specs_sedo/module.docx">Module</a>'
    schemas = [own, external]
    if mode == 'ambiguous':
        schemas = [own, own.replace('urn:own', 'urn:other'), own.replace('urn:own', 'urn:external')]
    fixture.resources[url] = docx(''.join(paragraph(escape(x)) for x in schemas) + title + table + title +
                                  paragraph(escape('<r xmlns="urn:external"/>')))
    def blob(content):
        digest = sha(content)
        write(fixture.state / 'objects' / digest, content)
        return digest
    libraries = []
    if mode in ('same', 'foreign'):
        seed = SEED if mode == 'same' else SEED.replace('ecp-test.', 'ecp.')
        libraries.append(collect(seed, 'test' if mode == 'same' else 'prod',
            {seed: definitions.encode(), seed.rsplit('/', 1)[0] + '/dep.xsd': dep.encode()}.__getitem__, blob))
    fixture.collect(1, wsdl_entries=libraries)
    return url


class ModuleMessageTests(unittest.TestCase):
    def test_conflicting_roots_are_reviewed_in_both_section_orders(self):
        from module_xsd import match_document
        from tests.test_schema_recovery import archive, docx as make_docx
        raw = archive({'main.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:test"><xs:element name="result" type="xs:string"/><xs:element name="report" type="xs:string"/></xs:schema>'})
        zipped = dict(environment='test', kind='zip', url='schemas.zip', sha256=sha(raw), **inspect_file(raw, 'zip'))
        results = []
        for roots in (('result', 'report'), ('report', 'result'), ('result', 'result')):
            content, _ = make_docx([part for root in roots for part in ('Тип сообщения 999: Ответ', f'<{root} xmlns="urn:test"/>')])
            document = dict(environment='test', kind='docx', url='module.docx', label='v1', sha256=sha(content), **inspect_file(content, 'docx'))
            matches, issues = match_document(document, [zipped], {sha(raw): raw}.__getitem__)
            document['module_matches'] = matches
            document['schema_diagnostics'] = issues
            if roots[0] == roots[1]:
                self.assertEqual(len(matches), 1)
                self.assertEqual(len(derive(dict(entries=[document]))['revisions']), 1)
            else:
                self.assertEqual(matches, [])
                self.assertEqual(len(derive(dict(entries=[document]))['document_sources']), 1)
                self.assertIn('{urn:test}result', str(issues))
                self.assertIn('{urn:test}report', str(issues))
                results.append(issues)
        self.assertEqual(results[0], results[1])

    def test_declaration_results_survive_bridge_and_archive_proofs(self):
        from catalog import archived_graph_reader
        from collector_snapshots import ArchivedModuleProofs, unmatched_module_bindings
        from tests.test_source_collection_regressions import SourceCollectionTests
        from verify import verify_snapshot_replay

        for mode in ('same', 'missing', 'foreign', 'ambiguous'):
            with self.subTest(mode=mode):
                fixture = SourceCollectionTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                url = collect_declaration_fixture(fixture, mode)

                def blob(content):
                    digest = sha(content)
                    write(fixture.state / 'objects' / digest, content)
                    return digest

                snapshot = fixture.current()
                self.assertEqual(snapshot['format'], 12)
                document = next(row for row in snapshot['entries'] if row['url'] == url)
                proofs = ArchivedModuleProofs(fixture.state, blob, archived_graph_reader(fixture.state))
                decisions = {d['namespace']: d for d in document['module_decisions']}
                self.assertEqual(set(decisions), {None, 'urn:external'})
                for ns, decision in decisions.items():
                    owners, reason = proofs.decision(snapshot, document, ('42', 'r', ns))
                    if 'reason' in decision:
                        self.assertEqual((owners, reason), (set(), decision['reason']))
                    else:
                        self.assertEqual({item[1] for item in owners}, {decision['effective_namespace']})
                failures = unmatched_module_bindings([document])
                if mode == 'same':
                    self.assertEqual(failures, [])
                    self.assertEqual([m.get('schema_source_type') for m in document['module_matches']], [None, 'wsdl_bridge'])
                    key = json.loads((fixture.state / 'current.json').read_bytes())['snapshot']
                    verify_snapshot_replay(fixture.state, snapshot, key)
                else:
                    self.assertEqual(len(failures), 1)
                    self.assertEqual(failures[0]['namespace'], None if mode == 'ambiguous' else 'urn:external')
                    if mode == 'ambiguous':
                        self.assertEqual(failures[0]['reason'], 'Неоднозначные схемы')
                        self.assertEqual([r['schema_reason'] for r in derive(snapshot)['document_sources'] if
                                          r['document_url'] == url], [failures[0]['reason']])

    def test_export_refuses_colliding_revision_with_different_schema_root(self):
        match = dict(number='999', root='result', namespace='urn:test', dependency_digest='same', schema_path='main.xsd', archive_sha256='archive', archive_url='schemas.zip')
        document = dict(environment='test', kind='docx', url='module.docx', sha256='document', label='v1', module_matches=[match, dict(match, root='report')])
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            raw = encode(dict(entries=[document])); key = sha(raw)
            write(state/'snapshots'/(key+'.json'), raw)
            write(state/'current.json', encode(dict(snapshot=key)))
            write(state/'catalog.json', b'previous catalog')
            with self.assertRaisesRegex(ValueError, 'идентичност|корн|QName'):
                export(state, state/'catalog.json')
            self.assertEqual((state/'catalog.json').read_bytes(), b'previous catalog')

    def test_extract_declarations_not_contents_examples_or_mentions(self):
        body=paragraph('Тип сообщения 611: Решение по проверке','Heading1')
        body+=paragraph('Тип сообщения 611.')+paragraph('Пример типа сообщения 612: не заголовок')
        body+=paragraph('Тип сообщения 999: Оглавление','TOC1')
        body+='<w:tbl><w:tr><w:tc>'+paragraph('Тип сообщения 777: XML пример')+'</w:tc></w:tr></w:tbl>'
        body+=paragraph('Тип сообщения 612: Второе решение')
        self.assertEqual(inspect_file(docx(body),'docx')['message_types'],[dict(number='611',title='Решение по проверке'),dict(number='612',title='Второе решение')])

    def test_missing_types_supplement_registry_and_keep_source(self):
        r,_=entry()
        rows=derive(dict(entries=[r]))['registry']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['number'],'611')
        self.assertEqual(rows[0]['module'],'Доходная часть')
        self.assertEqual(rows[0]['appendix'],'12')
        self.assertEqual(rows[0]['document_sha256'],r['sha256'])
        self.assertEqual(rows[0]['document_version'],'2.7')
        self.assertEqual(rows[0]['label_date'],'09.06.2026')
        self.assertEqual(rows[0]['source_type'],'module')
        self.assertEqual(derive(dict(entries=[r]))['revisions'],[])

    def test_general_registry_wins_with_contour_isolation_and_no_duplicate(self):
        prod,_=entry();test,_=entry('test')
        general=dict(environment='test',kind='docx',url='https://example.test/list.docx',registry_tables=[[['Код типа сообщения','Наименование','Описание','Приложение'],['Модуль'],['611','Официальное название','Описание','12']]])
        rows=derive(dict(entries=[prod,test,prod,general]))['registry']
        self.assertEqual(len(rows),2)
        self.assertEqual(next(r for r in rows if r['environment']=='test')['title'],'Официальное название')
        self.assertEqual(next(r for r in rows if r['environment']=='prod')['source_type'],'module')

    def test_module_document_archived_exactly(self):
        r,content=entry();rows=derive(dict(entries=[r]))['registry']
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write(root/'state/objects'/r['sha256'],content)
            archive_documents(dict(revisions=[],registry=rows),root/'state',root/'site')
            self.assertEqual((root/'site'/rows[0]['document_download']).read_bytes(),content)

    def test_update_promotes_new_module_original_and_reports_type(self):
        from auto_update import run
        from sync import record_observation
        r,content=entry()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=Path(__file__).resolve().parents[1]
            shutil.copytree(project/'web',root/'web');shutil.copy2(project/'viewer-template.html',root/'viewer-template.html')
            (root/'state/objects').mkdir(parents=True)
            old=encode(dict(format=1,entries=[]));before=sha(old)
            write(root/'state/snapshots'/(before+'.json'),old)
            write(root/'state/current.json',encode(dict(snapshot=before)));write(root/'state/history.json',encode([before]))
            write(root/'data/catalog.json',encode(dict(revisions=[],registry=[],review=[],updates=[])))
            write(root/'data/fields/index.json',encode([]))
            def collect(state):
                payload=encode(dict(format=1,entries=[r]));key=sha(payload)
                write(state/'snapshots'/(key+'.json'),payload);write(state/'objects'/r['sha256'],content)
                write(state/'current.json',encode(dict(snapshot=key)));write(state/'history.json',encode([before,key]))
                write(state/'last-run.json',encode(dict(snapshot=key,observed_at='2026-11-12T12:00:00+00:00',errors=[])))
                record_observation(state,key,'2026-11-12T12:00:00+00:00')
            with patch('auto_update.sync',side_effect=collect):run(root)
            self.assertEqual((root/'state/objects'/r['sha256']).read_bytes(),content)
            self.assertEqual((root/'site/documents'/(r['sha256']+'.docx')).read_bytes(),content)
            report=json.loads((root/'update-report.json').read_bytes())
            self.assertEqual(report['new_messages'],[dict(environment='prod',number='611')])

    def test_module_disappearing_is_retained_in_history(self):
        r,_=entry();a=dict(format=1,entries=[r]);b=dict(format=1,entries=[])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);keys=[]
            for snapshot in [a,b]:
                data=encode(snapshot);key=sha(data);keys.append(key);write(root/'snapshots'/(key+'.json'),data)
            write(root/'current.json',encode(dict(snapshot=keys[-1])));write(root/'history.json',encode(keys))
            export(root,root/'catalog.json')
            rows=json.loads((root/'catalog.json').read_bytes())['registry']
            self.assertEqual(len(rows),1);self.assertFalse(rows[0]['listed_now'])

if __name__=='__main__':unittest.main()
