import unittest
from review_field_links import review_sources


class FieldReviewSourcesTests(unittest.TestCase):
    def test_only_same_schema_number_and_contour_sources_are_combined(self):
        finding = dict(kind="Тип", field_paths=["root/value"], spec="В документе", xsd="В XSD")
        first = dict(number="6", environment="prod", ready=True, revision_id="one", display_revision_id="group",
                     document_sha256="doc1", document_url="https://lk.sfr.gov.ru/first.docx",
                     schema_review=dict(findings=[finding]), document_version="1")
        second = dict(first, revision_id="two", document_sha256="doc2",
                      document_url="https://lk.sfr.gov.ru/second.docx", document_version="2")
        other_env = dict(first, environment="test", document_url="https://lk-test.fss.ru/test.docx")
        other_schema = dict(first, display_revision_id="different", document_url="https://lk.sfr.gov.ru/other.docx")
        other_number = dict(first, number="7", document_url="https://lk.sfr.gov.ru/seven.docx")
        sources = review_sources(dict(revisions=[first, second, first, other_env, other_schema, other_number]), first)
        self.assertEqual([s["name"] for s in sources], ["first.docx", "second.docx"])
        self.assertEqual([len(s["findings"]) for s in sources], [1, 1])

    def test_rebuilding_with_resolved_or_unlinked_findings_removes_field_notice(self):
        row = dict(number="6", environment="prod", ready=True, revision_id="one",
                   schema_review=dict(findings=[dict(kind="Тип", field_paths=["root/value"])]))
        self.assertEqual(len(review_sources(dict(revisions=[row]), row)), 1)
        row["schema_review"]["findings"] = [dict(kind="Отсутствующее объявление", field_paths=[])]
        self.assertEqual(review_sources(dict(revisions=[row]), row), [])
        row["schema_review"]["findings"] = []
        self.assertEqual(review_sources(dict(revisions=[row]), row), [])
