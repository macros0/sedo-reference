import io
import unittest
import zipfile
from xml.etree import ElementTree as ET

from audit_individual_tables import audit
from module_xsd import W
from schema import Schema, XS


def paragraph(parent, value):
    run = ET.SubElement(ET.SubElement(parent, W + "p"), W + "r")
    ET.SubElement(run, W + "t").text = value


def document(heading, rows):
    body = ET.Element(W + "body")
    paragraph(body, heading)
    table = ET.SubElement(body, W + "tbl")
    for key, value in rows.items():
        row = ET.SubElement(table, W + "tr")
        paragraph(ET.SubElement(row, W + "tc"), key)
        cell = ET.SubElement(row, W + "tc")
        if isinstance(value, str):
            paragraph(cell, value)
        else:
            subtable = ET.SubElement(cell, W + "tbl")
            for values in value:
                subrow = ET.SubElement(subtable, W + "tr")
                for item in values:
                    paragraph(ET.SubElement(subrow, W + "tc"), item)
    return body


class AuditIndividualTablesTests(unittest.TestCase):
    def check(self, body, declarations, use_archive=True):
        file = io.BytesIO()
        with zipfile.ZipFile(file, "w") as archive:
            archive.writestr("main.xsd", f'<xs:schema xmlns:xs="{XS}" xmlns="urn:test" targetNamespace="urn:test">{declarations}</xs:schema>')
        with zipfile.ZipFile(file) as archive:
            schema = Schema(archive, "main.xsd", "root")
            schema.field(schema.root)
        revision = {"schema_path": "main.xsd", "root": "root"}
        return audit(revision, body, schema.fields, archive_path=file if use_archive else None)

    def test_typed_child_inline_length_changes_with_archive(self):
        open_element = '<xs:element name="ref" minOccurs="0"/>'
        restricted = '<xs:element name="ref" minOccurs="0"><xs:simpleType><xs:restriction base="xs:string"><xs:maxLength value="3"/></xs:restriction></xs:simpleType></xs:element>'
        template = '<xs:element name="root" type="Record"/><xs:complexType name="Record"><xs:sequence>{}</xs:sequence></xs:complexType>'
        body = document("Атрибуты элемента Record/ref", {"Схема": open_element})
        count, result = self.check(body, template.format(restricted))
        self.assertEqual(count, 1)
        self.assertEqual(len(result), 1)
        self.assertIn("maxLength", result[0]["xsd"])
        self.assertIn("Record/ref", result[0]["xlocation"])
        self.assertEqual(self.check(body, template.format(open_element)), (1, []))

    def test_contradictory_displayed_type_and_pattern(self):
        declaration = '<xs:attribute name="externalId" type="xs:string" use="required"/>'
        schema = '<xs:element name="root"><xs:complexType>' + declaration + '</xs:complexType></xs:element>'
        rows = {"Тип": "Identifier", "Свойства": [["use", "required"]],
                "Ограничения": [["Вид", "Значение"], ["Паттерн строки", "[A-Z]+"]], "Схема": declaration}
        body = document("Атрибут root/@externalId", rows)
        for use_archive in [False, True]:
            count, result = self.check(body, schema, use_archive)
            self.assertEqual(count, 1)
            self.assertIn("Identifier", " ".join(i["spec"] for i in result))
            self.assertIn("[A-Z]+", " ".join(i["spec"] for i in result))
        del rows["Ограничения"]
        rows["Тип"] = "xs:string"
        self.assertEqual(self.check(document("Атрибут root/@externalId", rows), schema), (1, []))

    def test_documented_field_before_required_child_is_not_a_trailing_wildcard(self):
        extra = '<xs:element name="newFlag" type="xs:boolean" minOccurs="0"/>'
        template = '<xs:element name="root"><xs:complexType><xs:sequence>{}<xs:element name="id" type="xs:long"/><xs:any minOccurs="0" processContents="skip"/></xs:sequence></xs:complexType></xs:element>'
        body = document("Атрибуты элемента root", {"Дочерние элементы": "newFlag id", "Схема": template.format(extra)})
        count, result = self.check(body, template.format(""))
        self.assertEqual(count, 1)
        self.assertIn("newFlag", result[0]["spec"])
        self.assertIn("xs:any", result[0]["xsd"])
        self.assertEqual(self.check(body, template.format(extra)), (1, []))

    def test_unused_type_becomes_available(self):
        declaration = '<xs:simpleType name="External"><xs:restriction base="xs:string"/></xs:simpleType>'
        root = '<xs:element name="root" type="xs:string"/>'
        body = document("Атрибуты типа External", {"Схема": declaration})
        self.assertEqual(len(self.check(body, root)[1]), 1)
        self.assertEqual(self.check(body, root + declaration), (1, []))

    def test_choice_bound_is_checked_on_group_not_on_each_field(self):
        group = '<xs:complexType><xs:choice minOccurs="{}"><xs:element name="a" type="xs:string"/><xs:element name="b" type="xs:string"/></xs:choice></xs:complexType>'
        template = '<xs:element name="root">{}</xs:element>'
        body = document("Атрибуты элемента root", {"Дочерние элементы": "a b", "Схема": template.format(group.format(0))})
        count, result = self.check(body, template.format(group.format(1)))
        self.assertEqual((count, len(result)), (1, 1))
        self.assertIn("choice(a, b)[0..1]", result[0]["spec"])
        self.assertEqual(self.check(body, template.format(group.format(0))), (1, []))

    def test_named_type_uses_same_child_and_attribute_checks_as_its_instance(self):
        name = '<xs:attribute name="token" type="xs:string" use="{}"/>'
        definition = '<xs:complexType name="Record">' + name + '</xs:complexType>'
        root = '<xs:element name="root" type="Record"/>'
        headings = ["Name", "Type", "Use", "Default", "Fixed"]
        rows = {"Атрибуты": [headings, ["token", "xs:string", "required", "", ""]],
                "Схема": definition.format("optional")}
        doc = document("Атрибуты комплексного типа Record", rows)
        count, result = self.check(doc, root + definition.format("optional"))
        self.assertEqual((count, len(result)), (1, 1))
        self.assertIn("optional", result[0]["xsd"])
        rows["Схема"] = definition.format("required")
        self.assertEqual(self.check(document("Атрибуты комплексного типа Record", rows),
                                    root + definition.format("required")), (1, []))
