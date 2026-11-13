import copy
import io
import json
import os
import posixpath
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from lxml import etree
from sync import encode, sha, write
from xsd_bundle import publish_bundles

XS='http://www.w3.org/2001/XMLSchema'
PROJECT=Path(__file__).resolve().parents[1]

class XsdBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sedo-bundle-test-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.state=self.base/'state';self.site=self.base/'site'

    def revision(self,length=4,environment='prod',version='1.0'):
        main=f'''<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" xmlns:c="urn:common" targetNamespace="urn:main" elementFormDefault="qualified">
<xs:import namespace="urn:common" schemaLocation="https://example.test/service?xsd=Common.xsd"/><xs:include schemaLocation="Parts.xsd"/>
<xs:element name="Request"><xs:complexType><xs:sequence><xs:element name="code" type="c:Code"/><xs:element name="flag" type="m:Flag" minOccurs="0"/></xs:sequence></xs:complexType></xs:element><xs:element name="Other" type="xs:string"/></xs:schema>'''.encode()
        common=f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:common"><xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:length value="{length}"/></xs:restriction></xs:simpleType></xs:schema>'.encode()
        part=f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:simpleType name="Flag"><xs:restriction base="xs:string"><xs:enumeration value="yes"/><xs:enumeration value="no"/></xs:restriction></xs:simpleType></xs:schema>'.encode()
        sources={'pack/main/Request.xsd':main,'pack/main/Parts.xsd':part,'lib/Common.xsd':common}
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:
            for name,data in sources.items():z.writestr(name,data)
            z.writestr('other/Common.xsd',f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:other"/>');z.writestr('unrelated.txt','not needed')
        archive=raw.getvalue();digest=sha(archive);write(self.state/'objects'/digest,archive)
        deps=sorted([[posixpath.relpath(n,'pack/main'),sha(v)] for n,v in sources.items()])
        return dict(number='10110',environment=environment,revision_id=sha(encode([digest,environment,version])),status='matched',root='Request',namespace='urn:main',document_version=version,label_date='13.11.2026',archive_sha256=digest,schema_path='pack/main/Request.xsd',archive_url='https://example.test/source.zip',dependencies=deps),sources

    def bundle(self,row):
        publish_bundles(dict(revisions=[row]),self.state,self.site)
        self.assertEqual(row['schema_export_status'],'ready',row.get('schema_export_error'))
        return self.site/row['schema_download']

    def extract(self,row,name='bundle'):
        path=self.bundle(row);target=self.base/name
        with zipfile.ZipFile(path) as z:
            self.assertEqual(len(z.namelist()),len(set(z.namelist())))
            self.assertTrue(all(not n.startswith('/') and '..' not in n.split('/') for n in z.namelist()))
            z.extractall(target)
        return target

    def run_validator(self,directory,xml=None,*args):
        command=[sys.executable,str(directory/'validate.py')]
        if xml is not None:
            payload=self.base/'payload.xml';payload.write_bytes(xml);command.append(str(payload))
        return subprocess.run([*command,*args],cwd=directory,capture_output=True,text=True,encoding='utf8',timeout=15,env={**os.environ,'PYTHONIOENCODING':'utf-8'})

    def test_original_bytes_closure_local_imports_qnames_and_determinism(self):
        row,sources=self.revision();original=copy.deepcopy(row);directory=self.extract(row,'комплект со spaces')
        manifest=json.loads((directory/'manifest.json').read_bytes())
        self.assertEqual((manifest['number'],manifest['environment'],manifest['revision_id'],manifest['document_version']),('10110','prod',original['revision_id'],'1.0'))
        self.assertEqual(manifest['entrypoint'],'message.xsd');self.assertEqual({f['source_path'] for f in manifest['files']},set(sources))
        for f in manifest['files']:
            self.assertEqual((directory/f['original_path']).read_bytes(),sources[f['source_path']])
            self.assertEqual(sha((directory/f['original_path']).read_bytes()),f['original_sha256']);self.assertEqual(sha((directory/f['local_path']).read_bytes()),f['local_sha256'])
            root=etree.fromstring((directory/f['local_path']).read_bytes())
            for reference in root.findall('{'+XS+'}import')+root.findall('{'+XS+'}include'):
                loc=reference.get('schemaLocation');self.assertNotIn('://',loc);self.assertTrue((directory/f['local_path']).parent.joinpath(loc).resolve().is_file())
        for f in manifest['files']:
            original_root=etree.fromstring(sources[f['source_path']]);local_root=etree.fromstring((directory/f['local_path']).read_bytes())
            refs=lambda root:[e for e in root if e.tag in ['{'+XS+'}import','{'+XS+'}include']]
            for original_ref,local_ref in zip(refs(original_root),refs(local_root)):local_ref.set('schemaLocation',original_ref.get('schemaLocation'))
            self.assertEqual(etree.tostring(local_root,method='c14n'),etree.tostring(original_root,method='c14n'),'only schemaLocation may change, including QName bindings')
        first=(self.site/row['schema_download']).read_bytes();self.bundle(row);self.assertEqual((self.site/row['schema_download']).read_bytes(),first)
        self.assertFalse(any(p.name=='unrelated.txt' for p in directory.rglob('*')))
        for xml,want in [(None,0),(b'<Request xmlns="urn:main"><code>ABCD</code><flag>yes</flag></Request>',0),(b'<Request xmlns="urn:main"><code>ABCDE</code></Request>',1)]:
            result=self.run_validator(directory,xml,*(['--check-schema'] if xml is None else []));self.assertEqual(result.returncode,want,result.stderr)

    def test_zip_bytes_do_not_depend_on_platform_zipinfo_defaults(self):
        from unittest.mock import patch
        row,_=self.revision();real=zipfile.ZipInfo;outputs=[]
        for host in [0,3]:
            class HostInfo(real):
                def __init__(self,*args,**kwargs):
                    super().__init__(*args,**kwargs);self.create_system=host
            with patch('xsd_bundle.zipfile.ZipInfo',HostInfo):outputs.append(self.bundle(copy.deepcopy(row)).read_bytes())
        self.assertEqual(outputs[0],outputs[1],'Windows and Linux must produce identical bundle bytes')

    def test_exact_old_and_test_editions_do_not_substitute_new_dependencies(self):
        old,_=self.revision(4,'prod','1.0');new,_=self.revision(5,'prod','2.0');test,_=self.revision(6,'test','2.0')
        publish_bundles(dict(revisions=[old,new,test]),self.state,self.site);self.assertEqual(len({r['schema_download'] for r in [old,new,test]}),3)
        for row,want in [(old,1),(new,0),(test,1)]:
            directory=self.extract(row,row['environment']+row['document_version']);result=self.run_validator(directory,b'<Request xmlns="urn:main"><code>ABCDE</code></Request>');self.assertEqual(result.returncode,want,result.stderr)

    def test_validator_enforces_selected_root_and_rejects_dtd(self):
        row,_=self.revision();directory=self.extract(row)
        other=self.run_validator(directory,b'<Other xmlns="urn:main">valid for the same XSD</Other>');self.assertEqual(other.returncode,1,other.stderr)
        outside=self.base/'private.txt';outside.write_text('ABCD','utf8')
        dtd=('<!DOCTYPE Request [<!ENTITY secret SYSTEM "'+outside.as_uri()+'">]><Request xmlns="urn:main"><code>&secret;</code></Request>').encode()
        rejected=self.run_validator(directory,dtd);self.assertEqual(rejected.returncode,1,rejected.stderr);self.assertNotIn('ABCD',rejected.stdout+rejected.stderr)
        self.assertEqual(self.run_validator(directory,b'<Request').returncode,1)

    def test_native_local_filenames_are_supported_without_network_access(self):
        from unittest.mock import patch
        from urllib.parse import urlsplit
        from urllib.request import url2pathname
        from xsd_validate import compile_schema
        row,_=self.revision();directory=self.extract(row,'native path со spaces');real_parse=etree.parse
        def native_parse(source,parser):
            return real_parse(str(Path(url2pathname(urlsplit(source).path))),parser)
        # Linux XMLSchema loaders call resolvers with native absolute filenames.
        with patch('lxml.etree.parse',side_effect=native_parse):validator=compile_schema(directory/'message.xsd',directory)
        self.assertTrue(validator.validate(etree.fromstring(b'<Request xmlns="urn:main"><code>ABCD</code></Request>')))

    def test_validator_does_not_load_schema_files_outside_the_package(self):
        row,_=self.revision();directory=self.extract(row);outside=self.base/'Common.xsd'
        outside.write_text(f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:common"><xs:simpleType name="Code"><xs:restriction base="xs:string"/></xs:simpleType></xs:schema>','utf8')
        tree=etree.parse(str(directory/'message.xsd'));tree.getroot().find('{'+XS+'}import').set('schemaLocation',outside.as_uri());tree.write(str(directory/'message.xsd'))
        rejected=self.run_validator(directory,None,'--check-schema');self.assertEqual(rejected.returncode,2,rejected.stderr)

    def test_corrupt_archive_dependency_checksum_missing_file_and_unsafe_path_fail_closed(self):
        row,_=self.revision();source=self.state/'objects'/row['archive_sha256'];data=source.read_bytes();source.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'архив|Архив'):self.bundle(row)
        source.write_bytes(data);row['dependencies'][0][1]='0'*64
        with self.assertRaisesRegex(ValueError,'сумм|checksum'):self.bundle(row)
        row,_=self.revision();row['dependencies'].append(['missing.xsd','0'*64])
        with self.assertRaisesRegex(ValueError,'missing|Отсутств|отсутств'):self.bundle(row)
        row,_=self.revision();row['schema_path']='../../escape.xsd'
        with self.assertRaises(ValueError):self.bundle(row)

    def test_document_only_and_invalid_schema_have_no_ready_download(self):
        row=dict(number='10110',environment='prod',revision_id='a'*64,status='document_only');publish_bundles(dict(revisions=[row]),self.state,self.site)
        self.assertEqual(row['schema_export_status'],'unavailable');self.assertNotIn('schema_download',row)
        invalid,sources=self.revision();sources['pack/main/Request.xsd']=sources['pack/main/Request.xsd'].replace(b'type="c:Code"',b'type="m:Missing"');raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:
            for n,v in sources.items():z.writestr(n,v)
        digest=sha(raw.getvalue());write(self.state/'objects'/digest,raw.getvalue());invalid['archive_sha256']=digest;invalid['dependencies']=sorted([[posixpath.relpath(n,'pack/main'),sha(v)] for n,v in sources.items()])
        publish_bundles(dict(revisions=[invalid]),self.state,self.site);self.assertEqual(invalid['schema_export_status'],'unavailable');self.assertNotIn('schema_download',invalid);self.assertTrue(invalid['schema_export_error'])

    def test_build_adds_download_metadata_without_changing_source_catalog(self):
        import build_site
        from unittest.mock import patch
        row,_=self.revision();doc=io.BytesIO()
        with zipfile.ZipFile(doc,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
        digest=sha(doc.getvalue());write(self.state/'objects'/digest,doc.getvalue());row.update(document_sha256=digest,document_url='https://example.test/spec.docx')
        source=encode(dict(revisions=[row]));write(self.base/'data/catalog.json',source)
        write(self.base/'data/fields/index.json',encode([dict(revision_id=row['revision_id'],status='ready')]))
        write(self.base/'data/fields'/(row['revision_id']+'.json'),encode(dict(fields=[dict(name='Request',path='Request',parent=-1,children=[])])))
        write(self.base/'viewer-template.html',b'<html></html>')
        for name in ('index.html','app.js','app.css','compare-core.js','compare.js','comparison-export.js','mapping-draft.js','mapping-editor.js','comparison-mapping.js','vendor/exceljs.min.js','vendor/exceljs-LICENSE','vendor/README.md','journal.js','message-transitions.js','favicon.svg','validation.js','validation-core.js','validation-frame.js','validation-worker.js','validation-paths.js','vendor/xmllint/xmllint-browser.mjs','vendor/xmllint/xmllint.wasm','vendor/xmllint/COPYING','vendor/xmllint/README.md'):write(self.base/'web'/name,b'test')
        with patch.object(build_site,'HERE',self.base):build_site.main()
        published=json.loads((self.site/'catalog-data.js').read_text('utf8').removeprefix('window.SEDO_CATALOG=').removesuffix(';'))['revisions'][0]
        self.assertTrue((self.site/published['schema_download']).is_file());self.assertEqual((self.site/published['document_download']).read_bytes(),doc.getvalue());self.assertEqual((self.base/'data/catalog.json').read_bytes(),source)

    def test_browser_companion_is_exact_deterministic_and_does_not_change_zip(self):
        import gzip
        row,_=self.revision();self.bundle(row)
        self.assertIn('validation_download',row,'selected edition needs an offline browser schema companion')
        archive=(self.site/row['schema_download']).read_bytes()
        raw=(self.site/row['validation_download']).read_bytes()
        self.assertEqual(sha(raw),row['validation_sha256'])
        pack=json.loads(gzip.decompress(raw))
        self.assertEqual(pack['revision_id'],row['revision_id']);self.assertEqual(pack['number'],row['number'])
        self.assertEqual(pack['environment'],row['environment']);self.assertEqual(pack['root'],'Request')
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            manifest=json.loads(z.read('manifest.json'))
            self.assertEqual(set(pack['files']),{f['local_path'] for f in manifest['files']})
            for name,value in pack['files'].items():
                import base64
                self.assertEqual(base64.b64decode(value),z.read(name))
        self.bundle(row)
        self.assertEqual(raw,(self.site/row['validation_download']).read_bytes())
        self.assertEqual(archive,(self.site/row['schema_download']).read_bytes())

    def test_browser_navigation_paths_are_exact_public_tree_paths(self):
        import gzip
        row,_=self.revision()
        paths=['Request','Request/code','Request/@id']
        publish_bundles(dict(revisions=[row]),self.state,self.site,navigation={row['revision_id']:paths})
        pack=json.loads(gzip.decompress((self.site/row['validation_download']).read_bytes()))
        self.assertEqual(pack['fields'],paths)
        self.assertEqual(pack['field_namespaces'],[['urn:main'],['urn:main'],[]])
        paths.append('outside')
        self.assertNotIn('outside',pack['fields'])

    def test_navigation_namespaces_follow_forms_and_global_references(self):
        from xsd_bundle import _field_namespaces
        source=f'''<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" targetNamespace="urn:main" attributeFormDefault="qualified">
<xs:attribute name="global" type="xs:string"/><xs:element name="Child" type="xs:string"/>
<xs:element name="Request"><xs:complexType><xs:sequence>
<xs:element name="local" type="xs:string"/><xs:element name="qualified" type="xs:string" form="qualified"/><xs:element ref="m:Child"/>
</xs:sequence><xs:attribute name="plain" form="unqualified"/><xs:attribute name="qualified"/><xs:attribute ref="m:global"/>
</xs:complexType></xs:element></xs:schema>'''
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:z.writestr('main.xsd',source)
        with zipfile.ZipFile(data) as z:
            result=_field_namespaces(z,dict(schema_path='main.xsd',root='Request'),['Request','Request/local','Request/qualified','Request/Child','Request/@plain','Request/@qualified','Request/@global'])
        self.assertEqual(result,[['urn:main'],[''],['urn:main'],['urn:main'],[''],['urn:main'],['urn:main']])

    def test_all_archived_editions_compile_and_keep_original_dependency_bytes(self):
        catalog=json.loads((PROJECT/'data/catalog.json').read_bytes());publish_bundles(catalog,PROJECT/'state',self.site)
        for row in catalog['revisions']:
            if row['status']!='matched':continue
            self.assertEqual(row['schema_export_status'],'ready',(row['number'],row['environment'],row.get('schema_export_error')))
            with zipfile.ZipFile(self.site/row['schema_download']) as z:
                manifest=json.loads(z.read('manifest.json'));self.assertEqual(manifest['revision_id'],row['revision_id']);self.assertEqual(manifest['environment'],row['environment'])
                with zipfile.ZipFile(PROJECT/'state/objects'/row['archive_sha256']) as source:
                    for f in manifest['files']:self.assertEqual(z.read(f['original_path']),source.read(f['source_path']))

if __name__=='__main__':unittest.main()
