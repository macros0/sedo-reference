import copy
import json
import unittest
from pathlib import Path

from edition_groups import group_editions, visible_editions, display_updates


class EditionGroupsTests(unittest.TestCase):
    def fixture(self):
        row = dict(number="14", environment="test", revision_id="first", root="fault", namespace="urn:fault",
                   status="matched", ready=True, listed_now=True, document_version="3.1", label_date="12.12.2024",
                   document_sha256="doc1", document_url="https://example.test/first.docx", document_download="documents/doc1.docx",
                   dependencies=[["Fault.xsd", "xsd1"], ["Common.xsd", "xsd2"]])
        second = dict(row, revision_id="second", document_version="5.5", document_sha256="doc2",
                      document_url="https://example.test/rust.docx", document_download="documents/doc2.docx")
        fields = [dict(name="fault", path="fault", parent=-1, children=[1], file="Fault.xsd", doc="Fault"),
                  dict(name="code", path="fault/code", parent=0, children=[], facets=[["length", "10"]], typeFile="Common.xsd")]
        return dict(revisions=[row, second], updates=[]), {"first": fields, "second": copy.deepcopy(fields)}

    def test_swapped_dependency_locations_remain_distinct_editions(self):
        catalog, fields = self.fixture()
        catalog['revisions'][0]['dependencies'] = [['root.xsd', 'root'], ['a/types.xsd', 'one'], ['b/types.xsd', 'two']]
        catalog['revisions'][1]['dependencies'] = [['root.xsd', 'root'], ['a/types.xsd', 'two'], ['b/types.xsd', 'one']]
        self.assertEqual(len(visible_editions(group_editions(catalog, fields.__getitem__))), 2)

    def test_entry_filename_is_only_packaging_metadata(self):
        catalog, fields = self.fixture()
        for row, name in zip(catalog['revisions'], ['old.xsd', 'new.xsd']):
            row['schema_path'] = 'folder/' + name
            row['dependencies'] = [[name, 'same']]
        self.assertEqual(len(visible_editions(group_editions(catalog, fields.__getitem__))), 1)

    def test_identical_schema_has_one_representative_with_all_document_sources(self):
        catalog, fields = self.fixture()
        previous = copy.deepcopy(catalog)
        result = group_editions(catalog, fields.__getitem__)
        self.assertEqual(catalog, previous, "source records must not be mutated")
        self.assertEqual(len(result["revisions"]), 2, "legacy revision IDs stay available")
        visible = visible_editions(result)
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["revision_id"], "first")
        self.assertEqual({s["document_version"] for s in visible[0]["edition_sources"]}, {"3.1", "5.5"})
        self.assertEqual({s["document_download"] for s in visible[0]["edition_sources"]}, {"documents/doc1.docx", "documents/doc2.docx"})
        self.assertEqual(result["revisions"][1]["display_revision_id"], "first")

    def test_contour_number_root_namespace_dependency_and_constraints_cannot_merge(self):
        changes = [dict(environment="prod"), dict(number="15"), dict(root="another"), dict(namespace="urn:another"),
                   dict(dependencies=[["Fault.xsd", "changed-xsd"], ["Common.xsd", "xsd2"]]),
                   dict(ready=False, status="document_only"), dict(dependencies=[])]
        for change in changes:
            with self.subTest(change=change):
                catalog, fields = self.fixture()
                catalog["revisions"][1].update(change)
                self.assertEqual(len(visible_editions(group_editions(catalog, fields.__getitem__))), 2)
        for key, value in [("facets", [["length", "11"]]), ("doc", "Other description"), ("min", 1), ("values", [["A", "caption"]])]:
            with self.subTest(property=key):
                catalog, fields = self.fixture()
                fields["second"][1][key] = value
                self.assertEqual(len(visible_editions(group_editions(catalog, fields.__getitem__))), 2)
        catalog, fields = self.fixture()
        fields["first"][1]["flags"] = {"file": "A"}
        fields["second"][1]["flags"] = {"file": "B"}
        self.assertEqual(len(visible_editions(group_editions(catalog, fields.__getitem__))), 2,
                         "properties named file inside flags are not source locations")

    def test_locations_can_differ_and_first_source_is_stable_when_added_or_unlisted(self):
        catalog, fields = self.fixture()
        catalog["revisions"][1]["dependencies"] = [["other/Common.xsd", "xsd2"], ["other/Fault.xsd", "xsd1"]]
        fields["second"][0]["file"] = "other/Fault.xsd"
        fields["second"][1]["typeFile"] = "other/Common.xsd"
        catalog["revisions"][0]["listed_now"] = False
        result = group_editions(catalog, fields.__getitem__)
        self.assertEqual(len(visible_editions(result)), 1)
        representative = visible_editions(result)[0]
        self.assertEqual(representative["revision_id"], "first")
        self.assertTrue(representative["listed_now"])
        self.assertEqual([s["listed_now"] for s in representative["edition_sources"]], [False, True])

    def test_group_publishes_current_title_without_rewriting_historical_titles(self):
        catalog, fields = self.fixture()
        first, second = catalog["revisions"]
        first.update(title="Первоначальный заголовок", listed_now=False)
        second.update(title="Новый заголовок", label_date="16.11.2026")
        group = group_editions(catalog, fields.__getitem__)
        representative, alias = group["revisions"]
        self.assertEqual(representative["display_revision_id"], first["revision_id"])
        self.assertEqual(representative["title"], first["title"])
        self.assertEqual(alias["title"], second["title"])
        self.assertEqual([(s["title"], s["listed_now"]) for s in representative["edition_sources"]],
                         [(first["title"], False), (second["title"], True)])

    def test_journal_groups_events_and_follows_previous_ids_without_self_comparison(self):
        catalog, fields = self.fixture()
        third = dict(catalog["revisions"][0], revision_id="third", dependencies=[["Fault.xsd", "xsd3"]])
        catalog["revisions"].append(third)
        fields["third"] = copy.deepcopy(fields["first"])
        catalog["updates"] = [dict(revision_id="first", previous_revision_id=None, observed_at="2026-11-12T00:00:00Z"),
                              dict(revision_id="second", previous_revision_id="first", observed_at="2026-11-13T00:00:00Z"),
                              dict(revision_id="third", previous_revision_id="second", observed_at="2026-11-14T00:00:00Z")]
        original = copy.deepcopy(catalog["updates"])
        catalog["updates"][2]["comparison"] = dict(status="ready", left="second", right="third", counts=dict(added=0,removed=0,changed=0))
        grouped = group_editions(catalog, fields.__getitem__)
        result = display_updates(grouped)
        self.assertEqual([e["revision_id"] for e in result], ["first", "third"])
        self.assertEqual(result[1]["previous_revision_id"], "first")
        self.assertEqual(result[1]["comparison"]["left"], "first")
        self.assertEqual(grouped["updates"][2]["comparison"]["left"], "second")
        self.assertEqual(catalog["updates"][:2], original[:2])

    def test_additional_sources_and_new_schemas_preserve_representatives(self):
        catalog, fields = self.fixture()
        original = catalog["revisions"][0]
        for index in range(2, 4):
            rid = "extra-" + str(index)
            catalog["revisions"].append(dict(original, revision_id=rid,
                document_sha256=rid, document_url="https://example.test/" + rid + ".docx"))
            fields[rid] = copy.deepcopy(fields["first"])
        catalog["revisions"].append(dict(original, revision_id="new-schema",
            dependencies=[["Fault.xsd", "new-content"]]))
        fields["new-schema"] = copy.deepcopy(fields["first"])
        grouped = group_editions(catalog, fields.__getitem__)
        visible = visible_editions(grouped)
        self.assertEqual([row["revision_id"] for row in visible], ["first", "new-schema"])
        self.assertEqual(len(visible[0]["edition_sources"]), 4)
        self.assertEqual(len(grouped["revisions"]), 5)

    def test_real_message_14_is_one_edition_per_contour_with_three_sources(self):
        root = Path(__file__).resolve().parents[1]
        catalog = json.loads((root / "data/catalog.json").read_bytes())
        catalog["revisions"] = [dict(r, ready=True) for r in catalog["revisions"] if r["revision_id"] in {
            "c609735e71013bead408bab1727fda5c67aaf82c41305d5c4bfb3227dcc2ffcc",
            "2ba54c5b3a6267ae0b9b3840351e8db3af695ecfe8e1fe7d923f8d737d996f52",
            "ca171570170bd8ec700521228ed868c46c61190e23ee68a885c0e2023ba3ba81",
            "88fc9955e7fd81897e3add92f535565322b1ab061608b94c0b229e21a3d5627d",
            "e190f8c786ef641eac047ce14118f8394ab1d4ddec4d12a1669d94d296693080",
            "d071619c8286d5e2d2bd3f4d427b5dfcaf48888168a0b6d927aa81ca65bb0ffd"}]
        result = group_editions(catalog, lambda rid: json.loads((root / "data/fields" / (rid + ".json")).read_bytes())["fields"])
        visible = visible_editions(result)
        self.assertEqual(len(visible), 2)
        self.assertEqual({r["environment"] for r in visible}, {"prod", "test"})
        self.assertTrue(all(len(r["edition_sources"]) == 3 for r in visible))


if __name__ == "__main__":
    unittest.main()
