import io
import unittest
import zipfile
from schema import Schema


class SchemaContentTests(unittest.TestCase):
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
