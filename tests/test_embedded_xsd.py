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
            sources[url]=b'<a href="/specs_sedo/source.zip">XSD</a><a href="prab.html">Process</a><a href="/specs_sedo/module.docx">v1.0 (01.11.2026)</a>'
            sources[base+'/specs_sedo/source.zip']=empty.getvalue();sources[base+'/specs_sedo/module.docx']=raw
            sources[base+'/prab.html']=b'<a href="/prab/card.zip">Card</a><a href="https://other.example/prab/foreign.zip">Ignore</a>'
            sources[base+'/prab/card.zip']=empty.getvalue()
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
            self.assertEqual(len([r for r in snapshot['entries'] if r.get('source_page')]),2)

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
