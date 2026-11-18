import unittest
from review_field_links import attach_targets, field_targets


class ReviewFieldLinkTests(unittest.TestCase):
    def setUp(self):
        self.fields = [
            dict(path="notice", type=""),
            dict(path="notice/benefit", type="n:benefitType"),
            dict(path="notice/benefit/rowNumber", type="xs:string"),
            dict(path="notice/other", type="n:benefitType"),
            dict(path="notice/other/rowNumber", type="xs:string"),
            dict(path="notice/@responseOn", type="xs:string"),
        ]

    def test_direct_and_attribute_paths(self):
        self.assertEqual(field_targets("/n:notice/@n:responseOn", self.fields),
                         (["notice/@responseOn"], "field"))

    def test_common_type_links_all_contexts(self):
        self.assertEqual(field_targets("benefitType/rowNumber", self.fields),
                         (["notice/benefit/rowNumber", "notice/other/rowNumber"], "field"))

    def test_missing_element_links_containing_block(self):
        self.assertEqual(field_targets("notice/benefit/missing", self.fields),
                         (["notice/benefit"], "parent"))
        self.assertEqual(field_targets("unknown/rowNumber", self.fields), ([], ""))

    def test_xml_diagnostic_uses_specific_instance_path(self):
        finding = dict(kind="XML-пример", location="Пример — /n:notice/n:benefit[2]/n:rowNumber",
                       xlocation="main.xsd → notice")
        self.assertEqual(attach_targets(finding, self.fields)["field_paths"], ["notice/benefit/rowNumber"])
        corrected = dict(finding, location="Пример — /notice/@responseOn")
        self.assertEqual(attach_targets(corrected, self.fields)["field_paths"], ["notice/@responseOn"])
