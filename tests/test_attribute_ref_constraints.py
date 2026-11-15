import base64
import io
import json
import sys
import tempfile
import subprocess
import unittest
import zipfile
from pathlib import Path
from lxml import etree

from schema import Schema
from xsd_bundle import _prepare, _zip
from xsd_validate import compile_schema

XS = "http://www.w3.org/2001/XMLSchema"

def fixture():
    main = f'''<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" xmlns:a="urn:attrs" targetNamespace="urn:main">
<xs:import namespace="urn:attrs" schemaLocation="attrs.xsd"/>
<xs:element name="request"><xs:complexType><xs:sequence><xs:element name="item" minOccurs="0" maxOccurs="unbounded"><xs:complexType>
<xs:attribute ref="a:editable" fixed="false"/><xs:attribute ref="a:label" default="local"/><xs:attribute xmlns:k="urn:value" ref="a:kind" fixed="k:known"/><xs:attribute ref="a:token" fixed="a b"/>
</xs:complexType></xs:element></xs:sequence></xs:complexType></xs:element></xs:schema>'''.encode()
    attrs = f'''<xs:schema xmlns:xs="{XS}" targetNamespace="urn:attrs">
<xs:attribute name="editable" type="xs:boolean"/><xs:attribute name="label" type="xs:string" default="global"/><xs:attribute name="kind" type="xs:QName"/><xs:attribute name="token"><xs:simpleType><xs:restriction base="xs:token"/></xs:simpleType></xs:attribute>
</xs:schema>'''.encode()
    row = dict(number="42", environment="prod", revision_id="a"*64, schema_path="main.xsd", root="request", namespace="urn:main")
    return row, {"main.xsd": main, "attrs.xsd": attrs}

def dynamic_fixture(kind='dynamic'):
    row, files=fixture()
    if kind=='dynamic':
        declarations='''<xs:complexType name="Leaf"><xs:attribute ref="a:editable"/></xs:complexType>
<xs:complexType name="FixedLeaf"><xs:complexContent><xs:restriction base="m:Leaf"><xs:attribute ref="a:editable" fixed="false"/></xs:restriction></xs:complexContent></xs:complexType>
<xs:complexType name="Base"><xs:sequence><xs:element name="child" type="m:Leaf" minOccurs="0"/></xs:sequence><xs:attribute ref="a:editable"/></xs:complexType>
<xs:complexType name="Derived"><xs:complexContent><xs:restriction base="m:Base"><xs:sequence><xs:element name="child" type="m:FixedLeaf" minOccurs="0"/></xs:sequence><xs:attribute ref="a:editable" fixed="false"/></xs:restriction></xs:complexContent></xs:complexType>
<xs:element name="request" type="m:Base"/>'''
    else:
        declarations='''<xs:complexType name="BooleanWithFixed"><xs:simpleContent><xs:extension base="xs:boolean"><xs:attribute ref="a:editable" fixed="false"/></xs:extension></xs:simpleContent></xs:complexType><xs:element name="request" type="xs:boolean"/>'''
    if kind=='equivalent':
        declarations='''<xs:complexType name="Base"><xs:attribute ref="a:editable" fixed="false"/></xs:complexType><xs:complexType name="Derived"><xs:complexContent><xs:restriction base="m:Base"><xs:attribute ref="a:editable" fixed="0"/></xs:restriction></xs:complexContent></xs:complexType><xs:element name="request" type="m:Base"/>'''
    files['main.xsd']=(f'<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" xmlns:a="urn:attrs" targetNamespace="urn:main"><xs:import namespace="urn:attrs" schemaLocation="attrs.xsd"/>'+declarations+'</xs:schema>').encode()
    return row,files

def prepared(kind='static'):
    row, files = fixture() if kind=='static' else dynamic_fixture(kind)
    result = _prepare(row, files)
    output, records = result[:2]
    # The downloadable and browser entrypoints must use the same schema probes.
    with zipfile.ZipFile(io.BytesIO(_zip(row, *result))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    pack = {k:row[k] for k in ("number", "environment", "revision_id", "root", "namespace")}
    pack.update(format=1, entrypoint="message.xsd", files={name:base64.b64encode(content).decode() for name,content in output.items() if not name.startswith("originals/")})
    for key in ("attribute_checks", "attribute_type_checks", "supplementary_schema", "supplementary_namespace"):
        if key in manifest: pack[key] = manifest[key]
    return pack, output, manifest

class AttributeRefConstraintTests(unittest.TestCase):
    def test_field_preserves_value_constraint_from_specific_attribute_use(self):
        row, files = fixture(); raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            for name, content in files.items(): archive.writestr(name, content)
        with zipfile.ZipFile(raw) as archive:
            tree = Schema(archive, "main.xsd", "request"); tree.field(tree.root)
        fields = {f["path"]:f for f in tree.fields}
        self.assertEqual(fields["request/item/@editable"]["flags"], {"fixed":"false"})
        self.assertEqual(fields["request/item/@label"]["flags"], {"default":"local"})

    def test_downloaded_python_validator_enforces_fixed_boolean_value_space(self):
        pack, output, manifest = prepared()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name, content in output.items():
                if name.startswith("originals/"): continue
                path = base/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
            (base/"manifest.json").write_text(json.dumps(manifest), "utf8")
            validator = compile_schema(base/"message.xsd", base)
            for value, expected in [(None,True), ("false",True), ("0",True), ("true",False), ("1",False)]:
                attribute = '' if value is None else ' a:editable="'+value+'"'
                xml = '<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item'+attribute+'/></m:request>'
                with self.subTest(value=value):
                    self.assertEqual(validator.validate(etree.fromstring(xml.encode())), expected)
            repeated = etree.fromstring(b'<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item a:editable="false"/><item a:editable="true"/></m:request>')
            self.assertFalse(validator.validate(repeated))
            invalid_first=etree.fromstring(b'<m:request xmlns:m="urn:main" xmlns:a="urn:attrs">\n<item a:editable="true"/>\n<item a:editable="false"/>\n</m:request>')
            self.assertFalse(validator.validate(invalid_first))
            self.assertEqual(validator.error_log[0].line,2)
            self.assertIn("attribute '{urn:attrs}editable'",validator.error_log[0].message)
            for attributes,expected in [('xmlns:v="urn:value" a:kind="v:known"',True),('xmlns:v="urn:other" a:kind="v:known"',False),('a:token="a   b"',True),('a:token="wrong"',False)]:
                xml='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item '+attributes+'/></m:request>'
                with self.subTest(attributes=attributes):self.assertEqual(validator.validate(etree.fromstring(xml.encode())),expected)

    def test_xsi_type_applies_fixed_constraints_at_root_and_nested_paths(self):
        for kind in ('dynamic','scalar','equivalent'):
            pack,output,manifest=prepared(kind)
            with tempfile.TemporaryDirectory() as folder:
                base=Path(folder)
                for name,content in output.items():
                    if name.startswith('originals/'):continue
                    path=base/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
                (base/'manifest.json').write_text(json.dumps(manifest),'utf8')
                validator=compile_schema(base/'message.xsd',base)
                for value,expected in [('false',True),('0',True),('true',False),('1',False)]:
                    xtype='m:BooleanWithFixed' if kind=='scalar' else 'm:Derived'
                    xml='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="'+xtype+'" a:editable="'+value+'">'+('true' if kind=='scalar' else '')+'</m:request>'
                    with self.subTest(kind=kind,value=value):self.assertEqual(validator.validate(etree.fromstring(xml.encode())),expected)
                if kind=='dynamic':
                    for xml,expected in [
                        ('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" a:editable="true"/>',True),
                        ('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:Base" a:editable="true"/>',True),
                        ('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:Derived"><child a:editable="true"/></m:request>',False),
                        ('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><child xsi:type="m:FixedLeaf" a:editable="true"/></m:request>',False)]:
                        with self.subTest(xml=xml):self.assertEqual(validator.validate(etree.fromstring(xml.encode())),expected)

    def test_unused_recursive_types_do_not_disable_schemas_without_fixed_refs(self):
        row,files=fixture()
        for unrelated_fixed_type in (False,True):
            extra='<xs:complexType name="Fixed"><xs:attribute ref="a:editable" fixed="false"/></xs:complexType>' if unrelated_fixed_type else ''
            source=(f'''<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" xmlns:a="urn:attrs" targetNamespace="urn:main"><xs:import namespace="urn:attrs" schemaLocation="attrs.xsd"/><xs:complexType name="Recursive"><xs:sequence><xs:element name="child" type="m:Recursive" minOccurs="0"/></xs:sequence></xs:complexType><xs:element name="request" type="xs:string"/>'''+extra+'</xs:schema>').encode()
            try:output,records,constraints=_prepare(row,{'main.xsd':source,'attrs.xsd':files['attrs.xsd']})
            except ValueError as error:self.fail('Unused recursive type disabled an unchanged schema: '+str(error))
            if unrelated_fixed_type:self.assertEqual(len(constraints['attribute_type_checks']),1)
            else:self.assertEqual(constraints,{})

    def test_shared_attribute_use_diagnostics_identify_the_actual_element_occurrence(self):
        row,files=fixture()
        files['main.xsd']=f'''<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" xmlns:a="urn:attrs" targetNamespace="urn:main"><xs:import namespace="urn:attrs" schemaLocation="attrs.xsd"/><xs:complexType name="Block"><xs:attribute ref="a:editable" fixed="false"/></xs:complexType><xs:element name="request"><xs:complexType><xs:sequence><xs:element name="a" type="m:Block"/><xs:element name="b" type="m:Block"/></xs:sequence></xs:complexType></xs:element></xs:schema>'''.encode()
        output,records,constraints=_prepare(row,files)
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with zipfile.ZipFile(io.BytesIO(_zip(row,output,records,constraints))) as archive:archive.extractall(base)
            validator=compile_schema(base/'message.xsd',base)
            prefix='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs">'
            for separator in ('','\n'):
                for invalid,expected in [('a',1 if not separator else 2),('b',1 if not separator else 3)]:
                    body=separator+'<a a:editable="'+('true' if invalid=='a' else 'false')+'"/>'+separator+'<b a:editable="'+('true' if invalid=='b' else 'false')+'"/>'
                    document=etree.fromstring((prefix+body+'</m:request>').encode())
                    with self.subTest(separator=separator,invalid=invalid):
                        self.assertFalse(validator.validate(document))
                        self.assertEqual(len(validator.error_log),1)
                        self.assertEqual(validator.error_log[0].line,expected)
                        self.assertTrue(validator.error_log[0].message.startswith("Element '"+invalid+"', attribute '{urn:attrs}editable':"),validator.error_log[0].message)

    def test_copied_download_script_applies_supplementary_manifest(self):
        row,files=fixture();result=_prepare(row,files)
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with zipfile.ZipFile(io.BytesIO(_zip(row,*result))) as archive:archive.extractall(base)
            payload=base/'payload.xml';payload.write_text('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item a:editable="true"/></m:request>','utf8')
            result=subprocess.run([sys.executable,str(base/'validate.py'),str(payload)],capture_output=True,text=True,encoding='utf8',timeout=15)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            self.assertIn('editable',result.stderr)

if __name__ == "__main__":
    if "--pack" in sys.argv: print(json.dumps(prepared(sys.argv[2] if len(sys.argv)>2 else "static")[0]))
    else: unittest.main()
