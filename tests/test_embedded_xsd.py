import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from sync import inspect_file, sha
from catalog import derive
from module_xsd import extract_archives, match_document, unpack_schemas, native_package

ROOT=Path(__file__).resolve().parents[1]
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'

def document():
    out=io.BytesIO()
    xml=f'''<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>Тип сообщения 600: Решение о скидке</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Элемент/Атрибут</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Тип</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>DecisionDiscountRust</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>DocumentType</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('word/document.xml',xml)
        z.writestr('word/embeddings/object.bin',(ROOT/'tests/fixtures/rust-xsd.ole').read_bytes())
    return out.getvalue()

class EmbeddedSchemaTests(unittest.TestCase):
    def test_missing_7z_reader_is_an_environment_error(self):
        from unittest.mock import patch
        with patch.dict("sys.modules", {"py7zr": None}):
            with self.assertRaisesRegex(ImportError, "py7zr"):
                extract_archives(document())

    def test_module_evidence_keeps_known_empty_and_unknown_namespaces_distinct(self):
        from module_xsd import document_bindings
        from tests.test_schema_recovery import archive, docx
        xs = 'http://www.w3.org/2001/XMLSchema'
        objects, archives = {}, []
        for namespace in ('urn:other', ''):
            attr = f' targetNamespace="{namespace}"' if namespace else ''
            xsd = f'<xs:schema xmlns:xs="{xs}"{attr}><xs:element name="result" type="xs:string"/></xs:schema>'
            raw = archive({'main.xsd': xsd})
            objects[sha(raw)] = raw
            archives.append(dict(kind='zip', environment='prod', url='https://example.test/'+sha(raw),
                                 sha256=sha(raw), **inspect_file(raw, 'zip')))
        for example in ('<result/>', f'<xs:schema xmlns:xs="{xs}"><xs:element name="result"/></xs:schema>'):
            _, doc = docx(['Тип сообщения 777: Результат', example])
            item = dict(environment='prod', url='doc', module_bindings=document_bindings(doc))
            self.assertEqual(match_document(item, archives[:1], objects.__getitem__)[0], [])
            matches, _ = match_document(item, archives, objects.__getitem__)
            self.assertEqual([m['archive_sha256'] for m in matches], [archives[1]['sha256']])
        # Old tables and stored bindings without namespace evidence are still usable.
        item['module_bindings'] = [dict(number='777', root='result', namespace=None)]
        self.assertEqual(match_document(item, archives[:1], objects.__getitem__)[0][0]['namespace'], 'urn:other')
        from tests.test_audit_module_tables import make_body, HEADER
        from xml.etree import ElementTree as ET
        body = make_body('Тип сообщения 777: Результат', [HEADER, ['result', 'DocumentType', '-', '1', '']],
                         '<result/>')
        doc = ET.Element('{'+W+'}document'); doc.append(body)
        self.assertEqual(document_bindings(doc)[0]['namespace'], '')

    def test_imported_named_qname_and_legacy_snapshot_contract(self):
        from tests.test_schema_recovery import archive
        archives, objects = [], {}
        types = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:t"><xs:simpleType name="Code"><xs:restriction base="xs:QName"/></xs:simpleType></xs:schema>'
        for p in ('xs','xsd'):
            main = (f'<{p}:schema xmlns:{p}="http://www.w3.org/2001/XMLSchema" xmlns:t="urn:t" targetNamespace="urn:m">'
                    f'<{p}:import namespace="urn:t" schemaLocation="types.xsd"/><{p}:element name="r" type="t:Code" fixed="{p}:int"/></{p}:schema>')
            raw = archive({'main.xsd':main, 'types.xsd':types})
            objects[sha(raw)]=raw
            archives.append(dict(environment='prod',kind='zip',url='https://example/'+p,sha256=sha(raw),**inspect_file(raw,'zip')))
        doc=dict(environment='prod',url='doc',module_bindings=[dict(number='777',root='r',namespace='urn:m')])
        self.assertEqual(len(match_document(doc,archives,objects.__getitem__)[0]),1)
        self.assertEqual(match_document(doc,archives,objects.__getitem__,legacy=True)[0],[])

    def test_prefix_equivalent_archives_replay_with_original_snapshot_format(self):
        from tests.test_source_collection_regressions import SourceCollectionTests, archive
        from sync import SITES
        fixture = SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for page in SITES.values():
            base = page.rsplit('/', 1)[0] + '/specs_sedo/'
            fixture.resources[page] = (b'<a href="/specs_sedo/file.docx">DOCX</a>'
                                       b'<a href="/specs_sedo/xs.zip">A</a><a href="/specs_sedo/xsd.zip">B</a>')
            for p in ('xs', 'xsd'):
                raw = (f'<{p}:schema xmlns:{p}="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main">'
                       f'<{p}:element name="response" type="{p}:string"/></{p}:schema>').encode()
                fixture.resources[base + p + '.zip'] = archive('main.xsd', raw)
        for expected_format in (2, 3, 4):
            options = {} if expected_format == 4 else {'snapshot_format': expected_format}
            fixture.collect(1, wsdl_entries=[], **options)
            original = json.loads((fixture.state / 'current.json').read_bytes())['snapshot']
            snapshot = fixture.current()
            self.assertEqual(snapshot['format'], expected_format)
            rows = [row for row in snapshot['entries'] if row['kind'] == 'docx']
            self.assertEqual(len(rows), 2)
            for row in rows:
                if expected_format < 4:
                    self.assertEqual(row['schema_diagnostics'][0]['reason'], 'Неоднозначные схемы')
                else:
                    self.assertEqual(len(row['module_matches']), 1)
            fixture.collect(2, wsdl_entries=[], snapshot_format=expected_format)
            replay = json.loads((fixture.state / 'current.json').read_bytes())['snapshot']
            self.assertEqual(replay, original)

    def test_contract_compares_used_qnames_by_uri_and_string_literals_verbatim(self):
        from module_xsd import xml_contract
        def contract(prefix, uri, typ, value):
            return xml_contract((f'<{prefix}:schema xmlns:{prefix}="http://www.w3.org/2001/XMLSchema" '
                                 f'xmlns:v="{uri}"><{prefix}:element name="r" type="{prefix}:{typ}" '
                                 f'fixed="{value}"/></{prefix}:schema>').encode())
        self.assertEqual(contract('xs', 'urn:unused', 'string', 'ok'), contract('xsd', 'urn:unused2', 'string', 'ok'))
        self.assertNotEqual(contract('xs', 'urn:a', 'QName', 'v:ok'), contract('xs', 'urn:b', 'QName', 'v:ok'))
        self.assertNotEqual(contract('xs', 'urn:a', 'string', 'xs:int'), contract('xsd', 'urn:a', 'string', 'xsd:int'))
        self.assertEqual(contract('xs', 'urn:a', 'QName', 'xs:int'), contract('xsd', 'urn:a', 'QName', 'xsd:int'))
        def selector(prefix, ns):
            return xml_contract((f'<s:schema xmlns:s="http://www.w3.org/2001/XMLSchema" xmlns:{prefix}="{ns}">'
                                 f'<s:key name="k"><s:selector xpath=".//{prefix}:row"/>'
                                 f'<s:field xpath="@{prefix}:id"/></s:key></s:schema>').encode())
        self.assertEqual(selector('a', 'urn:test'), selector('b', 'urn:test'))
        self.assertNotEqual(selector('a', 'urn:test'), selector('a', 'urn:other'))

    def test_text_fragments_are_scanned_once_and_preserve_nested_xml(self):
        from module_xsd import xml_fragments
        source = '<outer><inner a=">"/><!-- <ignored> --><![CDATA[<text>]]></outer><next/>'
        stats = {}
        self.assertEqual(list(xml_fragments(source, stats=stats)),
                         [source[:source.index('<next/>')], '<next/>'])
        self.assertLessEqual(stats['characters'], len(source) * 2)
        unfinished = '<xs:schema>' * 400
        stats = {}
        self.assertEqual(list(xml_fragments(unfinished, stats=stats)), [])
        self.assertLessEqual(stats['characters'], len(unfinished) * 2)

    def test_text_fragment_limits_and_recovery_after_mismatched_tag(self):
        from module_xsd import xml_fragments
        self.assertEqual(list(xml_fragments('<broken></other><valid/>')), ['<valid/>'])
        self.assertEqual(list(xml_fragments('< prose ' + 'x' * 9000 + '<valid/>')), ['<valid/>'])
        self.assertEqual(list(xml_fragments('<broken text <valid/>')), ['<valid/>'])
        with self.assertRaisesRegex(ValueError, 'фрагмент'):
            list(xml_fragments('<item>' + 'x' * 100 + '</item>', max_length=32))
        with self.assertRaisesRegex(ValueError, 'глубокий'):
            list(xml_fragments('<item>' * 513))

    def test_inline_schema_identity_ignores_xml_examples_and_unclosed_prose(self):
        import html
        schema = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"></xs:schema>'
        def packages(prefix):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                archive.writestr('word/document.xml', f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{html.escape(prefix + schema)}</w:t></w:r></w:p></w:body></w:document>')
            result, issues = extract_archives(output.getvalue())
            self.assertFalse(issues)
            return result
        plain = packages('')
        self.assertEqual(packages('<r>valid</r>'), plain)
        self.assertEqual(packages('<example> prose '), plain)

    def test_service_documents_keep_their_archived_inline_schema_identities(self):
        state = ROOT / 'state'
        current = json.loads((state / 'current.json').read_bytes())['snapshot']
        entries = json.loads((state / 'snapshots' / (current + '.json')).read_bytes())['entries']
        checked = 0
        for row in entries:
            expected = [item for item in row.get('embedded_archives', [])
                        if item.get('original_sha256', '').startswith(('74dd3b60', '235809a4'))]
            if not expected:
                continue
            packages, _ = extract_archives((state / 'objects' / row['sha256']).read_bytes())
            for item in expected:
                package = next(p for p in packages if p['container'] == item['container'])
                self.assertEqual(package['original_sha256'], item['original_sha256'])
                self.assertEqual(sha(package['content']), item['sha256'])
                checked += 1
        self.assertEqual(checked, 2)

    def test_embedded_inline_and_combined_archives_use_platform_independent_headers(self):
        import html
        schema = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"></xs:schema>'
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as document_zip:
            document_zip.writestr('word/embeddings/one.xsd', schema)
            document_zip.writestr('word/embeddings/two.xsd', schema.replace('><', ' targetNamespace="urn:two"><'))
            inline = html.escape(schema.replace('><', ' targetNamespace="urn:inline"><'))
            document_zip.writestr('word/document.xml', f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{inline}</w:t></w:r></w:p></w:body></w:document>')
        packages, issues = extract_archives(output.getvalue())
        self.assertFalse(issues)
        self.assertEqual(len(packages), 4)
        for package in packages:
            with zipfile.ZipFile(io.BytesIO(package['content'])) as archive:
                self.assertTrue(all(item.create_system == 3 for item in archive.infolist()), package['container'])

    def test_embedded_xsd_is_deterministic_and_retains_original_bytes(self):
        first,issues=extract_archives(document());second,_=extract_archives(document())
        self.assertFalse(issues);self.assertEqual(len(first),1)
        self.assertEqual(first[0]['content'],second[0]['content'])
        self.assertEqual(first[0]['filename'],'XSD.7z')
        with zipfile.ZipFile(io.BytesIO(first[0]['content'])) as z:
            self.assertIn('v01/rust/RustDocumentsAsv.xsd',z.namelist())

    def test_module_binding_and_match_uses_all_dependencies(self):
        raw=document();meta=inspect_file(raw,'docx')
        self.assertEqual(meta['module_bindings'][0]['root'],'DecisionDiscountRust')
        packages,_=extract_archives(raw);objects={};archives=[]
        for package in packages:
            content=package.pop('content');digest=sha(content);objects[digest]=content
            archives.append(dict(package,sha256=digest,**inspect_file(content,'zip')))
        r=dict(meta,environment='prod',kind='docx',url='https://example.test/spec.docx',label='module',sha256=sha(raw),embedded_archives=archives)
        matches,issues=match_document(r,[],lambda digest:objects[digest])
        self.assertFalse(issues);self.assertEqual(len(matches),1)
        self.assertEqual(matches[0]['number'],'600')
        self.assertEqual(matches[0]['schema_path'],'v01/rust/RustDocumentsAsv.xsd')
        self.assertGreater(len(matches[0]['dependencies']),1)
        compiled=derive(dict(entries=[dict(r,module_matches=matches)]))
        self.assertEqual(compiled['revisions'][0]['root'],'DecisionDiscountRust')
        self.assertEqual(compiled['revisions'][0]['source_type'],'module')

    def test_sync_discovers_companion_archives_and_binds_both_contours(self):
        from unittest.mock import patch
        from sync import sync, SITES
        raw=document()
        # Use the real embedded fixture to prove sync records extracted objects,
        # while the auxiliary page separately proves the previously missed crawl.
        sources={}
        empty=io.BytesIO()
        with zipfile.ZipFile(empty,'w') as z:z.writestr('empty.xsd',b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>')
        for env,url in SITES.items():
            base=url.rsplit('/',1)[0]
            companion_host = 'https://lk.fss.ru' if env == 'prod' else base
            sources[url]=(f'<a href="/specs_sedo/source.zip">XSD</a><a href="prab.html">Process</a><a href="{companion_host}//mchd.html">MCHD</a><a href="/specs_sedo/module.docx">v1.0 (01.11.2026)</a>').encode()
            sources[base+'/specs_sedo/source.zip']=empty.getvalue();sources[base+'/specs_sedo/module.docx']=raw
            sources[base+'/prab.html']=b'<a href="/prab/card.zip">Card</a><a href="https://other.example/prab/foreign.zip">Ignore</a>'
            sources[base+'/prab/card.zip']=empty.getvalue()
            sources[base+'/mchd.html']=b'<a href="/mchd/mchd-xsd.zip">XSD</a>'
            sources[base+'/mchd/mchd-xsd.zip']=empty.getvalue()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('sync.get',side_effect=lambda u:sources[u]):sync(root)
            key=json.loads((root/'current.json').read_bytes())['snapshot']
            snapshot=json.loads((root/'snapshots'/(key+'.json')).read_bytes())
            docs=[r for r in snapshot['entries'] if r['kind']=='docx']
            self.assertEqual({r['environment'] for r in docs},{'prod','test'})
            for doc in docs:
                self.assertEqual(doc['module_matches'][0]['number'],'600')
                self.assertTrue((root/'objects'/doc['module_matches'][0]['archive_sha256']).exists())
            self.assertEqual(len([r for r in snapshot['entries'] if r.get('source_page')]),4)

    def test_conflicting_rules_are_not_deduplicated(self):
        objects={};archives=[]
        for length in [5,6]:
            out=io.BytesIO()
            schema=f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:test"><xs:element name="item"><xs:simpleType><xs:restriction base="xs:string"><xs:maxLength value="{length}"/></xs:restriction></xs:simpleType></xs:element></xs:schema>'
            with zipfile.ZipFile(out,'w') as z:z.writestr('main.xsd',schema)
            content=out.getvalue();digest=sha(content);objects[digest]=content
            archives.append(dict(environment='prod',kind='zip',url='https://example/'+str(length)+'.zip',sha256=digest,**inspect_file(content,'zip')))
        doc=dict(environment='prod',url='https://example/doc.docx',module_bindings=[dict(number='88',title='notice',root='item',namespace='urn:test')])
        matches,issues=match_document(doc,archives,lambda d:objects[d])
        self.assertFalse(matches);self.assertTrue(any('Неоднозначные' in i['reason'] for i in issues))

    def test_namespace_form_defaults_are_not_deduplicated(self):
        objects={};archives=[]
        for form in ['qualified','unqualified']:
            out=io.BytesIO()
            schema=f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:test" elementFormDefault="{form}"><xs:element name="item"><xs:complexType><xs:sequence><xs:element name="child" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
            with zipfile.ZipFile(out,'w') as z:z.writestr('main.xsd',schema)
            content=out.getvalue();digest=sha(content);objects[digest]=content
            archives.append(dict(environment='prod',kind='zip',url='https://example/'+form+'.zip',sha256=digest,**inspect_file(content,'zip')))
        doc=dict(environment='prod',url='https://example/doc.docx',module_bindings=[dict(number='88',title='notice',root='item',namespace='urn:test')])
        matches,issues=match_document(doc,archives,lambda d:objects[d])
        self.assertFalse(matches);self.assertTrue(any('Неоднозначные' in i['reason'] for i in issues))

    def test_direct_xsd_limit_and_embedding_limit_before_read(self):
        from unittest.mock import patch
        with patch('module_xsd.MAX_FILE',4):
            with self.assertRaises(ValueError):unpack_schemas('large.xsd',b'12345')
            packages,issues=extract_archives(document())
            self.assertFalse(packages);self.assertTrue(issues)

    def test_fixed_qname_namespace_context_is_distinct(self):
        objects={};archives=[]
        for namespace in ['urn:A','urn:B']:
            out=io.BytesIO()
            schema=f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:p="{namespace}" targetNamespace="urn:test"><xs:element name="item" type="xs:QName" fixed="p:value"/></xs:schema>'
            with zipfile.ZipFile(out,'w') as z:z.writestr('main.xsd',schema)
            content=out.getvalue();digest=sha(content);objects[digest]=content
            archives.append(dict(environment='prod',kind='zip',url='https://example/archive.zip',sha256=digest,**inspect_file(content,'zip')))
        doc=dict(environment='prod',url='https://example/doc.docx',module_bindings=[dict(number='88',title='notice',root='item',namespace='urn:test')])
        matches,issues=match_document(doc,archives,lambda d:objects[d])
        self.assertFalse(matches);self.assertTrue(any('Неоднозначные' in i['reason'] for i in issues))

    def test_uncompressed_budget_is_shared_by_embedded_packages(self):
        from unittest.mock import patch
        archives=[]
        for marker in ['a','b']:
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('a.xsd',marker.encode()*150)
            archives.append(out.getvalue())
        self.assertLess(sum(map(len,archives)),250)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            for i,content in enumerate(archives):z.writestr('word/embeddings/'+str(i)+'.zip',content)
        with patch('module_xsd.MAX_TOTAL',250):packages,issues=extract_archives(out.getvalue())
        self.assertTrue(issues)
        self.assertLessEqual(len(packages),1)

    def test_unsafe_archive_path_is_rejected(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('../escape.xsd',b'<schema/>')
        with self.assertRaises(ValueError):unpack_schemas('bad.zip',out.getvalue())

    def test_native_package_rejects_truncated_payload(self):
        with self.assertRaises(ValueError):native_package(b'bad')

    def test_external_archive_respects_contour_and_namespace(self):
        raw=b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:test"><xs:element name="item" type="xs:string"/></xs:schema>'
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('main.xsd',raw)
        content=out.getvalue();digest=sha(content)
        archive=dict(environment='test',kind='zip',url='https://test.example/schemas.zip',sha256=digest,**inspect_file(content,'zip'))
        doc=dict(environment='prod',url='https://example/spec.docx',module_bindings=[dict(number='88',title='notice',root='item',namespace='urn:test')])
        matches,issues=match_document(doc,[archive],lambda _:content)
        self.assertFalse(matches);self.assertTrue(issues)
        doc['environment']='test';matches,issues=match_document(doc,[archive],lambda _:content)
        self.assertEqual(len(matches),1);self.assertFalse(issues)

if __name__=='__main__':unittest.main()
