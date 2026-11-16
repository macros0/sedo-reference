import io
import unittest
import zipfile
from schema import Schema


class SchemaContentTests(unittest.TestCase):
    def test_dependency_metadata_distinguishes_legacy_unknown_from_no_namespace_import(self):
        from schema import dependency_set
        source={'path':'main.xsd','namespace':'urn:main','sha256':'main','dependencies':['types.xsd']}
        target={'path':'types.xsd','namespace':'urn:types','sha256':'types','dependencies':[]}
        self.assertEqual(dependency_set([source,target],'main.xsd'),[('main.xsd','main'),('types.xsd','types')])
        source['imports']=[{'location':'types.xsd','namespace':None,'kind':'import'}]
        with self.assertRaisesRegex(ValueError,'namespace'):
            dependency_set([source,target],'main.xsd')

    def tree(self, sources):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, content in sources.items():
                archive.writestr(name, content)
        with zipfile.ZipFile(output) as archive:
            tree = Schema(archive, "main.xsd", "r")
            tree.field(tree.root)
        return tree

    def test_complex_restriction_replaces_particles_and_inherits_attributes(self):
        from lxml import etree
        source = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
          <xs:complexType name="Base"><xs:sequence><xs:element name="optional" minOccurs="0"/></xs:sequence>
          <xs:attribute name="kept"/><xs:attribute name="removed"/><xs:attribute name="changed"/><xs:anyAttribute processContents="lax"/></xs:complexType>
          <xs:element name="r"><xs:complexType><xs:complexContent><xs:restriction base="Base">
          <xs:attribute name="removed" use="prohibited"/><xs:attribute name="changed" use="required"/>
          </xs:restriction></xs:complexContent></xs:complexType></xs:element></xs:schema>'''
        validator = etree.XMLSchema(etree.fromstring(source.encode()))
        self.assertTrue(validator.validate(etree.fromstring(b'<r changed="x" kept="y"/>')))
        self.assertFalse(validator.validate(etree.fromstring(b'<r changed="x"><optional/></r>')))
        self.assertFalse(validator.validate(etree.fromstring(b'<r changed="x" wildcard="removed"/>')))
        tree = self.tree({'main.xsd': source})
        self.assertEqual({f['path'] for f in tree.fields}, {'r', 'r/@kept', 'r/@changed'})
        self.assertEqual(tree.fields[0]['contentModel'], ['empty'])
        self.assertEqual(next(f for f in tree.fields if f['name'] == '@changed')['min'], '1')

    def test_chameleon_include_resolves_unprefixed_components_in_each_namespace(self):
        xs = 'http://www.w3.org/2001/XMLSchema'
        common = f'''<xs:schema xmlns:xs="{xs}" elementFormDefault="qualified">
        <xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:length value="2"/></xs:restriction></xs:simpleType>
        <xs:complexType name="Box"><xs:sequence><xs:element name="code" type="Code"/></xs:sequence></xs:complexType></xs:schema>'''
        sources = {'main.xsd': f'''<xs:schema xmlns:xs="{xs}" xmlns:a="urn:a" xmlns:b="urn:b">
        <xs:import namespace="urn:a" schemaLocation="a.xsd"/><xs:import namespace="urn:b" schemaLocation="b.xsd"/>
        <xs:element name="r"><xs:complexType><xs:sequence><xs:element name="a" type="a:Box"/>
        <xs:element name="b" type="b:Box"/></xs:sequence></xs:complexType></xs:element></xs:schema>''',
        'common.xsd': common}
        for ns in ('a', 'b'):
            sources[ns+'.xsd'] = f'<xs:schema xmlns:xs="{xs}" targetNamespace="urn:{ns}"><xs:include schemaLocation="common.xsd"/></xs:schema>'
        tree = self.tree(sources)
        for ns in ('a', 'b'):
            field = next(f for f in tree.fields if f['path'] == 'r/'+ns+'/code')
            self.assertEqual((field['ns'], field['typeNs'], field['baseNs']), ('urn:'+ns, 'urn:'+ns, xs))
            self.assertEqual(field['facets'], [['length', '2']])

    def test_import_namespace_mismatch_is_rejected_even_at_exact_path(self):
        with self.assertRaisesRegex(ValueError, 'namespace'):
            self.tree({'main.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:import namespace="urn:a" schemaLocation="other.xsd"/><xs:element name="r"/></xs:schema>',
                       'other.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:b"/>'})

    def test_both_boolean_forms_of_mixed_content_are_preserved(self):
        for value in ("true", "1"):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("main.xsd", '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="r"><xs:complexType mixed="'+value+'"/></xs:element></xs:schema>')
            with zipfile.ZipFile(output) as archive:
                tree = Schema(archive, "main.xsd", "r"); tree.field(tree.root)
            self.assertEqual(tree.fields[0]["flags"].get("mixed"), "true")
        from lxml import etree
        for declaration in ('mixed="true"', 'mixed="1"'):
            schema = ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:complexType name="Base" mixed="false"/>'
                      '<xs:element name="r"><xs:complexType '+declaration+'><xs:complexContent><xs:extension base="Base"/>'
                      '</xs:complexContent></xs:complexType></xs:element></xs:schema>')
            validator = etree.XMLSchema(etree.fromstring(schema.encode()))
            self.assertTrue(validator.validate(etree.fromstring(b"<r>text</r>")))
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("main.xsd", schema)
            with zipfile.ZipFile(output) as archive:
                tree = Schema(archive, "main.xsd", "r"); tree.field(tree.root)
            self.assertEqual(tree.fields[0]["flags"].get("mixed"), "true")

    def test_implicit_any_type_differs_from_empty_complex_type(self):
        fields = []
        for content in ("", "<xs:complexType/>"):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("main.xsd", '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="r">'+content+'</xs:element></xs:schema>')
            with zipfile.ZipFile(output) as archive:
                tree = Schema(archive, "main.xsd", "r")
                tree.field(tree.root)
                fields.append(tree.fields[0])
        self.assertEqual(fields[0]["base"], "xs:anyType")
        self.assertNotEqual(fields[0], fields[1])
        self.assertEqual(fields[1]["contentModel"], ["empty"])
