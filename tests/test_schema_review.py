import unittest
from xml.etree import ElementTree as ET

from module_xsd import W
from schema_review import compare_schema_location


class SchemaLocationTests(unittest.TestCase):
    def test_rechecks_current_doc_and_archive_path(self):
        body = ET.Element(W + "body")
        row = ET.SubElement(ET.SubElement(body, W + "tbl"), W + "tr")
        for value in ("Расположение файла xml-схемы", r"\xsd\v3.0\request.xsd"):
            cell = ET.SubElement(row, W + "tc")
            ET.SubElement(cell, W + "t").text = value
        count, findings = compare_schema_location(body, "Схемы/v3.0/Request.xsd")
        self.assertEqual(count, 1)
        self.assertEqual(findings[0]["kind"], "Путь к XSD")
        self.assertEqual(compare_schema_location(body, "Новая поставка/v3.0/request.xsd"), (1, []))
        self.assertEqual(compare_schema_location(ET.Element(W + "body"), "request.xsd"), (0, []))
