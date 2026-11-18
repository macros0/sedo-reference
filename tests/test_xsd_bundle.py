import copy
import base64
import gzip
from unittest.mock import patch
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
from xsd_bundle import publish_bundles, _browser_compatible

XS='http://www.w3.org/2001/XMLSchema'
PROJECT=Path(__file__).resolve().parents[1]

class XsdBundleTests(unittest.TestCase):
    def test_documented_entry_selects_namespace_include_without_arbitrary_wrapper_choice(self):
        from catalog import derive
        from sync import inspect_file
        for declared in ('', ' targetNamespace="urn:A"'):
            files={'main.xsd':f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:A"><xs:include schemaLocation="common.xsd"/></xs:schema>'.encode(),
                   'common.xsd':f'<xs:schema xmlns:xs="{XS}"{declared}><xs:element name="r" type="xs:string"/></xs:schema>'.encode()}
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as archive:
                for name,content in files.items():archive.writestr(name,content)
            doc=dict(kind='docx',environment='test',url='https://example.test/Тип_сообщения_999.docx',sha256='d'*64,label='Example',structure=[['Корневой элемент','r'],['Пространство имен файла XML-схемы','urn:A'],['Расположение файла XML-схемы','main.xsd']])
            package=dict(kind='zip',environment='test',url='https://example.test/schema.zip',sha256=sha(raw.getvalue()),**inspect_file(raw.getvalue(),'zip'))
            revision=derive(dict(entries=[doc,package]))['revisions'][0]
            self.assertEqual((revision['status'],revision.get('schema_path')),('matched','main.xsd'))
            if declared:
                doc['structure']=doc['structure'][:-1]
                self.assertEqual(derive(dict(entries=[doc,package]))['revisions'][0]['status'],'review')

    def test_global_root_from_chameleon_include_matches_tree_and_bundle(self):
        from schema import Schema
        from sync import inspect_file
        from xsd_bundle import _prepare
        files={'common.xsd':f'<xs:schema xmlns:xs="{XS}"><xs:element name="r" type="xs:int"/></xs:schema>'.encode()}
        packs=[]
        for ns in ('A','B'):
            files[ns+'.xsd']=f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:{ns}"><xs:include schemaLocation="common.xsd"/></xs:schema>'.encode()
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            for name,content in files.items(): archive.writestr(name,content)
        metadata=inspect_file(raw.getvalue(),'zip')['schemas']
        for ns in ('A','B'):
            entry=ns+'.xsd'
            self.assertEqual(next(item for item in metadata if item['path']==entry)['roots'],[{'name':'r','description':''}])
            with zipfile.ZipFile(raw) as archive:
                tree=Schema(archive,entry,'r');tree.field(tree.root)
            self.assertEqual(tree.fields[0]['ns'],'urn:'+ns)
            output,_,_=_prepare(dict(schema_path=entry,root='r',namespace='urn:'+ns),{entry:files[entry],'common.xsd':files['common.xsd']})
            packs.append(dict(format=1,number='999',environment='test',revision_id='a'*64,root='r',namespace='urn:'+ns,entrypoint='message.xsd',fields=['r'],field_namespaces=[['urn:'+ns]],files={name:base64.b64encode(content).decode('ascii') for name,content in output.items() if not name.startswith('originals/')}))
            with tempfile.TemporaryDirectory() as folder:
                base=Path(folder)
                for path,content in output.items():
                    target=base/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
                validator=etree.XMLSchema(etree.parse(str(base/'message.xsd')))
                self.assertTrue(validator.validate(etree.fromstring(f'<r xmlns="urn:{ns}">1</r>')))
                self.assertFalse(validator.validate(etree.fromstring('<r xmlns="urn:wrong">1</r>')))
        result=subprocess.run(['node',str(PROJECT/'tests/included_root_wasm.cjs')],input=json.dumps(packs),text=True,capture_output=True,cwd=PROJECT,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_typed_element_fixed_values_keep_instance_type_and_patterns(self):
        from tests.typed_fixed_fixture import fixture, CALENDAR_CASES, CASES, xml
        from xsd_bundle import _prepare, _zip
        row, files = fixture()
        original = etree.XMLSchema(etree.fromstring(files['main.xsd']))
        for body, _, valid in CALENDAR_CASES:
            with self.subTest(original=body):
                self.assertEqual(original.validate(etree.fromstring(xml(body).encode())), valid,
                                 str(original.error_log))
        directory = self.base/'typed-fixed'
        directory.mkdir()
        with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
            archive.extractall(directory)
        for body, valid in CASES:
            with self.subTest(body=body):
                result = self.run_validator(directory, xml(body).encode())
                self.assertEqual(result.returncode, 0 if valid else 1, result.stderr)
                if body.startswith('<utc>') and not valid or '<pattern>1.0</pattern>' in body:
                    self.assertIn("[facet 'pattern']", result.stderr)
                if body.startswith('<instant>') and not valid:
                    self.assertRegex(result.stderr, r"Element 'instant': .*does not match the fixed value constraint")
                    if '<decimal>2</decimal>' in body:
                        self.assertRegex(result.stderr, r"Element 'decimal': .*does not match the fixed value constraint")

    def test_downloaded_validator_enforces_exact_fixed_calendar_attributes(self):
        from tests.typed_fixed_fixture import attribute_fixture, chameleon_attribute_fixture
        from xsd_bundle import _prepare, _zip
        for kind in ('direct', 'reference', 'neighbor', 'required', 'chameleon'):
            row, files, cases = chameleon_attribute_fixture() if kind == 'chameleon' else attribute_fixture(kind)
            directory = self.base/kind
            directory.mkdir()
            with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
                archive.extractall(directory)
            for xml, valid in cases:
                with self.subTest(kind=kind, xml=xml):
                    result = self.run_validator(directory, xml.encode())
                    self.assertEqual(result.returncode, 0 if valid else 1, result.stderr)
                    if not valid and '6782Z"' in xml:
                        self.assertRegex(result.stderr, r"Element '(request|\{urn:calendar\}calendar)', attribute 'instant': .*does not match the fixed value")

    def test_downloaded_validator_enforces_fixed_calendar_union_and_list(self):
        from importlib.util import module_from_spec, spec_from_file_location
        from tests.typed_fixed_fixture import COMPOUND_CASES, compound_attribute_fixture
        from xsd_bundle import _prepare, _zip
        for kind, subject in COMPOUND_CASES:
            row, files, cases = compound_attribute_fixture(kind, subject)
            directory = self.base/(subject+'-'+kind)
            with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
                archive.extractall(directory)
            spec = spec_from_file_location('bundled_validator', directory/'validate.py')
            bundled = module_from_spec(spec)
            spec.loader.exec_module(bundled)
            validator = bundled.compile_schema(directory/'message.xsd', directory)
            for xml, valid in cases:
                with self.subTest(kind=kind, subject=subject, xml=xml):
                    self.assertEqual(validator.validate(etree.fromstring(xml.encode())), valid,
                                     str(validator.error_log))
                    if kind == 'calendar-facet' and not valid:
                        self.assertTrue(any('Не удалось точно сравнить' in error.message for error in validator.error_log))
                    if kind.startswith('patterned-') and not valid and 'note="bad"' not in xml:
                        reason = 'pattern' if ' 007' in xml else 'does not match the fixed value'
                        self.assertTrue(any(reason in error.message for error in validator.error_log))

    def test_compound_browser_limits_preserve_native_download(self):
        from tests.typed_fixed_fixture import compound_attribute_fixture, compound_depth_fixture, pack
        cases = []
        for depth in (64, 65):
            row, sources, xml = compound_depth_fixture(depth)
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as bundle:
                for name, content in sources.items():
                    bundle.writestr(name, content)
            digest = sha(archive.getvalue())
            write(self.state/'objects'/digest, archive.getvalue())
            row.update(status='matched', revision_id=digest, archive_sha256=digest,
                       dependencies=[[name, sha(content)] for name, content in sources.items()])
            download = self.bundle(row)
            directory = self.base/str(depth)
            with zipfile.ZipFile(download) as bundle:
                bundle.extractall(directory)
            result = self.run_validator(directory, xml.encode())
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = depth == 64
            self.assertEqual('validation_download' in row, expected)
            if not expected:
                self.assertIn('ZIP', row['validation_error'])
            cases.append((pack(row, sources), expected))
        base = pack(*compound_attribute_fixture('union')[:2])
        rule = base['attribute_checks'][0]
        # A rule and its union + two leaves consume four nodes.
        for count in (12500, 12501):
            cases.append((dict(base, attribute_checks=[rule]*count, attribute_type_checks=[]), count == 12500))
        for candidate, expected in cases:
            raw=json.dumps(candidate).encode()
            self.assertEqual(_browser_compatible(candidate, raw, gzip.compress(raw)), expected)
        check = """
          const {checkPack} = require('./web/validation-core.js');
          for (const [pack, expected] of JSON.parse(require('node:fs').readFileSync(0, 'utf8'))) {
            let accepted = true;
            try { checkPack(pack, pack); } catch { accepted = false; }
            require('node:assert/strict').equal(accepted, expected);
          }
        """
        result = subprocess.run(['node', '-e', check], input=json.dumps(cases), text=True,
                                capture_output=True, cwd=PROJECT, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wildcard_uses_fixed_global_attributes_when_it_validates_them(self):
        from tests.typed_fixed_fixture import wildcard_attribute_fixture
        from xsd_bundle import _prepare, _zip
        from xsd_validate import compile_schema
        from audit_xml_examples import _schema
        for label, explicit, implicit_only in (('mixed', True, False), ('wildcard', False, False), ('implicit', False, True)):
            row, files, cases = wildcard_attribute_fixture(explicit, implicit_only=implicit_only)
            directory = self.base/label
            with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
                archive.extractall(directory)
            source = self.base/(label+'.zip')
            with zipfile.ZipFile(source, 'w') as archive:
                for name, raw in files.items():
                    archive.writestr(name, raw)
            for validator in (compile_schema(directory/'message.xsd', directory), _schema(source, row)):
                for xml, valid in cases:
                    with self.subTest(kind=label, validator=type(validator).__name__, xml=xml):
                        self.assertEqual(validator.validate(etree.fromstring(xml.encode())), valid,
                                         str(validator.error_log))

    def test_audit_uses_same_typed_fixed_values_without_changing_originals(self):
        from tests.typed_fixed_fixture import fixture, CASES, xml
        from audit_xml_examples import _schema
        row, files = fixture()
        source = self.base/'typed-originals.zip'
        with zipfile.ZipFile(source, 'w') as archive:
            for name, raw in files.items():archive.writestr(name, raw)
        original_bytes = source.read_bytes()
        validator = _schema(source, row)
        for body, valid in CASES:
            with self.subTest(body=body):
                self.assertEqual(validator.validate(etree.fromstring(xml(body).encode())), valid,
                                 str(validator.error_log))
        self.assertEqual(source.read_bytes(), original_bytes)

    def test_quoted_boolean_declarations_in_appinfo_do_not_change_validator(self):
        from xsd_bundle import _prepare
        source = (f'<xs:schema xmlns:xs="{XS}"><xs:annotation><xs:appinfo>'
                  '<xs:element name="example" type="undeclared:foreign" fixed="true"/>'
                  '</xs:appinfo></xs:annotation><xs:element name="root" type="xs:string"/></xs:schema>').encode()
        row = dict(root='root', schema_path='root.xsd', namespace=None)
        _, _, constraints = _prepare(row, {'root.xsd':source})
        self.assertNotIn('boolean_fixed_replacements', constraints)

    def test_701_fixed_boolean_accepts_both_lexical_forms(self):
        catalog = json.loads((PROJECT/'data/catalog.json').read_bytes())
        row = next(r for r in catalog['revisions'] if r['revision_id'].startswith('bf8b73ce'))
        self.state = PROJECT/'state'
        directory = self.extract(row)
        for text, code in [('true',0), ('1',0), (' 1 ',0), ('',0), ('false',1), ('0',1), ('yes',1)]:
            xml = (f'<restrictionResponse xmlns="{row["namespace"]}">'
                   '<responseOn>00000000-0000-0000-0000-000000000000</responseOn>\n'
                   f'<notIdentified>{text}</notIdentified></restrictionResponse>')
            result = self.run_validator(directory, xml.encode())
            with self.subTest(text=text):
                self.assertEqual(result.returncode, code, result.stderr)
                if code: self.assertIn('Строка 2', result.stderr)

    def test_uppercase_xsd_suffix_is_browser_compatible(self):
        row,sources=self.revision()
        sources={name.replace('Parts.xsd','Part0.XSD'):content.replace(b'Parts.xsd',b'Part0.XSD') for name,content in sources.items()}
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            for name,content in sources.items():archive.writestr(name,content)
        data=raw.getvalue();row.update(archive_sha256=sha(data),dependencies=sorted([[posixpath.relpath(name,'pack/main'),sha(content)] for name,content in sources.items()]))
        write(self.state/'objects'/row['archive_sha256'],data)
        self.bundle(row)
        pack=json.loads(gzip.decompress((self.site/row['validation_download']).read_bytes()))
        self.assertTrue(all(name.endswith('.xsd') for name in pack['files']))
        main=base64.b64decode(pack['files']['message.xsd'])
        self.assertIn(b'Part0.xsd',main)
        checked=subprocess.run(['node','-e',"const p=JSON.parse(require('node:fs').readFileSync(0,'utf8'));require('./web/validation-core.js').checkPack(p,p);"],input=json.dumps(pack),text=True,capture_output=True,cwd=PROJECT)
        self.assertEqual(checked.returncode,0,checked.stderr)


    def test_browser_file_count_boundary_preserves_local_zip(self):
        for count in (256,257):
            with self.subTest(count=count):
                row,_=self.revision(version=str(count))
                output={'message.xsd':b'<schema/>'}
                output.update({f'schemas/p{i}.xsd':b'<schema/>' for i in range(count-1)})
                with patch('xsd_bundle._prepare',return_value=(output,[],{})):
                    self.bundle(row)
                self.assertEqual('validation_download' in row,count==256)
                if count==257:self.assertIn('ZIP',row['validation_error'])

    def test_browser_size_limits_preserve_local_zip(self):
        # Include full JSON overhead and navigation, not only schema bytes.
        for mode in ('unpacked','download'):
            with self.subTest(mode=mode):
                row,_=self.revision(version=mode)
                output={'message.xsd':b'<schema/>'}
                navigation={row['revision_id']:['x'*(32*1024*1024)]} if mode=='unpacked' else {}
                with patch('xsd_bundle._prepare',return_value=(output,[],{})), patch('xsd_bundle._field_namespaces',return_value=[[]]):
                    if mode=='download':
                        with patch('xsd_bundle.gzip.compress',return_value=b'x'*(8*1024*1024+1)):
                            self.bundle(row)
                    else:publish_bundles({'revisions':[row]},self.state,self.site,navigation)
                self.assertEqual(row['schema_export_status'],'ready')
                self.assertNotIn('validation_download',row)
                self.assertIn('ZIP',row['validation_error'])

    def test_browser_limits_match_consumer_boundaries(self):
        pack={'files':{'message.xsd':''}}
        self.assertTrue(_browser_compatible(pack,b'x'*(32*1024*1024),b'x'*(8*1024*1024)))
        self.assertFalse(_browser_compatible(pack,b'x'*(32*1024*1024+1),b''))
        self.assertFalse(_browser_compatible(pack,b'',b'x'*(8*1024*1024+1)))
        pack.update(attribute_checks=[],supplementary_namespace='urn:test',supplementary_schema='message.xsd')
        self.assertTrue(_browser_compatible(pack,b'',b''))
        pack['attribute_any_type_paths']=[[]]
        self.assertFalse(_browser_compatible(pack,b'',b''),'root anyType paths must identify an XML node')
        pack.pop('attribute_any_type_paths')
        pack['attribute_checks']=[dict(path=[['','root']]*257,attribute=['','attr'],element='value0',field='root/@attr')]
        self.assertFalse(_browser_compatible(pack,b'',b''))

    def test_unrelated_fixed_name_does_not_expand_unused_recursive_type(self):
        row,_=self.revision()
        sources={'main.xsd':f'''<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" targetNamespace="urn:main">
        <xs:import schemaLocation="attrs.xsd"/><xs:attribute name="editable" type="xs:boolean"/>
        <xs:element name="Request"><xs:complexType><xs:attribute ref="m:editable"/></xs:complexType></xs:element>
        <xs:complexType name="Unused"><xs:sequence><xs:element name="child" type="m:Unused" minOccurs="0"/></xs:sequence>
        <xs:attribute ref="m:editable"/></xs:complexType></xs:schema>'''.encode(),
        'attrs.xsd':f'<xs:schema xmlns:xs="{XS}"><xs:attribute name="editable" type="xs:boolean" fixed="false"/></xs:schema>'.encode()}
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            for name,content in sources.items():archive.writestr(name,content)
        data=raw.getvalue();row.update(schema_path='main.xsd',archive_sha256=sha(data),dependencies=[[name,sha(content)] for name,content in sources.items()])
        write(self.state/'objects'/row['archive_sha256'],data)
        directory=self.extract(row)
        self.assertFalse(json.loads((directory/'manifest.json').read_bytes()).get('attribute_checks'))
        result=self.run_validator(directory,b'<Request xmlns="urn:main" xmlns:m="urn:main" m:editable="true"/>')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_unused_recursive_fixed_type_keeps_download_and_browser_pack_available(self):
        from tests.test_attribute_ref_constraints import dynamic_fixture
        row,sources=dynamic_fixture('unused-recursive')
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            for name,content in sources.items():archive.writestr(name,content)
        data=raw.getvalue()
        row.update(status='matched',archive_sha256=sha(data),dependencies=[[name,sha(content)] for name,content in sources.items()])
        write(self.state/'objects'/row['archive_sha256'],data)
        self.bundle(row)
        self.assertEqual(row['schema_export_status'],'ready',row.get('schema_export_error'))
        with zipfile.ZipFile(self.site/row['schema_download']) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            self.assertNotIn('supplementary_schema',manifest)
            self.assertEqual(archive.read('originals/002_main.xsd'),sources['main.xsd'])
            directory=self.base/'unused-recursive'
            archive.extractall(directory)
        pack=json.loads(gzip.decompress((self.site/row['validation_download']).read_bytes()))
        self.assertNotIn('supplementary_schema',pack)
        result=self.run_validator(directory,b'<request xmlns="urn:main">text</request>')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_chameleon_global_fixed_attribute_gets_supplementary_probe(self):
        row,_=self.revision()
        sources={'main.xsd':f'<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" targetNamespace="urn:main"><xs:include schemaLocation="attrs.xsd"/><xs:element name="Request"><xs:complexType><xs:attribute ref="m:editable"/></xs:complexType></xs:element></xs:schema>'.encode(),
                 'attrs.xsd':f'<xs:schema xmlns:xs="{XS}"><xs:attribute name="editable" type="xs:boolean" fixed="false"/></xs:schema>'.encode()}
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            for name,content in sources.items():archive.writestr(name,content)
        data=raw.getvalue();row.update(schema_path='main.xsd',archive_sha256=sha(data),dependencies=[[name,sha(content)] for name,content in sources.items()])
        write(self.state/'objects'/row['archive_sha256'],data)
        directory=self.extract(row)
        self.assertTrue(json.loads((directory/'manifest.json').read_bytes()).get('attribute_checks'))
        for value,want in (('false',0),('0',0),('true',1),('1',1)):
            result=self.run_validator(directory,('<Request xmlns="urn:main" xmlns:m="urn:main" m:editable="'+value+'"/>').encode())
            self.assertEqual(result.returncode,want,result.stderr)

    def test_same_chameleon_file_compiles_in_two_namespaces(self):
        from schema import Schema, dependency_set
        from sync import inspect_file
        row,_=self.revision()
        sources={'main.xsd':f'''<xs:schema xmlns:xs="{XS}" xmlns:a="urn:a" xmlns:b="urn:b" targetNamespace="urn:main" elementFormDefault="qualified">
        <xs:import namespace="urn:a" schemaLocation="a.xsd"/><xs:import namespace="urn:b" schemaLocation="b.xsd"/>
        <xs:element name="Request"><xs:complexType><xs:sequence><xs:element name="a" type="a:Box"/>
        <xs:element name="b" type="b:Box"/></xs:sequence></xs:complexType></xs:element></xs:schema>'''.encode(),
        'common.xsd':f'''<xs:schema xmlns:xs="{XS}" elementFormDefault="qualified"><xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:length value="2"/></xs:restriction></xs:simpleType>
        <xs:complexType name="Box"><xs:sequence><xs:element name="code" type="Code"/></xs:sequence></xs:complexType></xs:schema>'''.encode()}
        for namespace in ('a','b'):
            sources[namespace+'.xsd']=f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:{namespace}"><xs:include schemaLocation="common.xsd"/></xs:schema>'.encode()
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            for name,content in sources.items():archive.writestr(name,content)
        data=raw.getvalue();row.update(schema_path='main.xsd',archive_sha256=sha(data))
        write(self.state/'objects'/row['archive_sha256'],data)
        row['dependencies']=dependency_set(inspect_file(data,'zip')['schemas'],row['schema_path'])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            tree=Schema(archive,'main.xsd','Request');tree.field(tree.root)
        self.assertEqual([f['ns'] for f in tree.fields if f['name']=='code'],['urn:a','urn:b'])
        directory=self.extract(row)
        good=b'<Request xmlns="urn:main"><a><code xmlns="urn:a">AB</code></a><b><code xmlns="urn:b">CD</code></b></Request>'
        for payload,want in ((good,0),(good.replace(b'CD',b'LONG'),1)):
            result=self.run_validator(directory,payload)
            self.assertEqual(result.returncode,want,result.stderr)

    def test_nested_chameleon_include_uses_effective_namespace_in_bundle(self):
        from schema import Schema, dependency_set
        from sync import inspect_file
        row,sources=self.revision()
        sources['pack/main/Parts.xsd']=f'<xs:schema xmlns:xs="{XS}"><xs:include schemaLocation="Defs.xsd"/></xs:schema>'.encode()
        sources['pack/main/Defs.xsd']=f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:simpleType name="Flag"><xs:restriction base="xs:string"/></xs:simpleType></xs:schema>'.encode()
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            for name,content in sources.items():archive.writestr(name,content)
        data=raw.getvalue();row['archive_sha256']=sha(data)
        write(self.state/'objects'/row['archive_sha256'],data)
        row['dependencies']=dependency_set(inspect_file(data,'zip')['schemas'],row['schema_path'])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            tree=Schema(archive,row['schema_path'],row['root']);tree.field(tree.root)
        directory=self.extract(row)
        result=self.run_validator(directory,b'<Request xmlns="urn:main"><code>ABCD</code><flag>yes</flag></Request>')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_chameleon_include_dependency_tree_and_offline_validation(self):
        from schema import Schema, dependency_set
        from sync import inspect_file
        row,sources=self.revision()
        sources['pack/main/Parts.xsd']=sources['pack/main/Parts.xsd'].replace(b' targetNamespace="urn:main"',b'')
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            for name,content in sources.items():archive.writestr(name,content)
        data=raw.getvalue();row['archive_sha256']=sha(data)
        write(self.state/'objects'/row['archive_sha256'],data)
        # The dependency collector must agree with both schema engines about
        # a no-namespace include adopting the including document's namespace.
        inventory=inspect_file(data,'zip')['schemas']
        row['dependencies']=dependency_set(inventory,row['schema_path'])
        self.assertEqual(len(row['dependencies']),3)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            tree=Schema(archive,row['schema_path'],row['root']);tree.field(tree.root)
        flag=next(f for f in tree.fields if f['name']=='flag')
        self.assertEqual(flag['typeNs'],'urn:main')
        self.assertEqual(flag['values'],[['yes',''],['no','']])
        directory=self.extract(row)
        for value,want in (('yes',0),('invalid',1)):
            result=self.run_validator(directory,('<Request xmlns="urn:main"><code>ABCD</code><flag>'+value+'</flag></Request>').encode())
            self.assertEqual(result.returncode,want,result.stderr)

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
        write(self.base/'data/fields'/(row['revision_id']+'.json'),encode(dict(revision_id=row['revision_id'],fields=[dict(name='Request',path='Request',parent=-1,children=[])])))
        write(self.base/'viewer-template.html',b'<html></html>')
        for name in ('review-journal.js','index.html','app.js','app.css','automation-status.js','compare-core.js','compare.js','comparison-export.js','mapping-draft.js','mapping-editor.js','comparison-mapping.js','vendor/exceljs.min.js','vendor/exceljs-LICENSE','vendor/README.md','journal.js','message-transitions.js','favicon.svg','validation.js','validation-core.js','validation-frame.js','validation-worker.js','validation-paths.js','validation-source.js','validation-messages.js','vendor/xmllint/xmllint-browser.mjs','vendor/xmllint/xmllint.wasm','vendor/xmllint/COPYING','vendor/xmllint/README.md'):write(self.base/'web'/name,b'test')
        with patch.object(build_site,'HERE',self.base):build_site.main()
        published=json.loads((self.site/'catalog-data.js').read_text('utf8').removeprefix('window.SEDO_CATALOG=').removesuffix(';'))['revisions'][0]
        self.assertEqual(published['viewer_sha256'],sha((self.site/'editions'/(row['revision_id']+'.html')).read_bytes()));self.assertTrue((self.site/published['schema_download']).is_file());self.assertEqual((self.site/published['document_download']).read_bytes(),doc.getvalue());self.assertEqual((self.base/'data/catalog.json').read_bytes(),source)

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
        from schema import Schema
        with zipfile.ZipFile(data) as z:
            schema=Schema(z,'main.xsd','Request')
            schema.field(schema.root)
        self.assertEqual([[f['ns']] for f in schema.fields],result)
        self.assertEqual(schema.fields[0]['contentModel'],['sequence','local','qualified','Child'])

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
