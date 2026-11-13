import copy
import unittest

from edition_groups import group_editions, source_updates, display_updates
from tests import test_edition_groups as fixtures


class SourceUpdatesTests(unittest.TestCase):
    def fixture(self):
        catalog, fields = fixtures.EditionGroupsTests().fixture()
        catalog["updates"] = [dict(revision_id="first", snapshot="initial", observed_at="2026-11-12T12:00:00+00:00", initial_import=True),
                              dict(revision_id="second", snapshot="later", observed_at="2026-11-13T12:00:00+00:00", initial_import=False)]
        return catalog, fields

    def test_new_source_is_an_event_of_the_existing_edition(self):
        catalog, fields = self.fixture()
        grouped = group_editions(catalog, fields.__getitem__)
        original = copy.deepcopy(grouped)
        events = source_updates(grouped)
        self.assertEqual(grouped, original)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event_type"], "source_added")
        self.assertEqual(event["revision_id"], "first")
        self.assertEqual(event["source_revision_id"], "second")
        self.assertEqual(event["observed_at"], catalog["updates"][1]["observed_at"])
        self.assertEqual(event["source"]["document_version"], "5.5")
        self.assertEqual(event["source"]["document_download"], "documents/doc2.docx")
        self.assertFalse(event.get("comparison"))
        self.assertEqual(source_updates(grouped), events)

    def test_initial_snapshot_and_unknown_dates_are_not_later_additions(self):
        for batch in ("initial", None):
            catalog, fields = self.fixture()
            for event in catalog["updates"]:
                event["snapshot"] = batch
                event["observed_at"] = None
            self.assertEqual(source_updates(group_editions(catalog, fields.__getitem__)), [])

    def test_repeated_source_under_another_revision_id_does_not_repeat_event(self):
        catalog, fields = self.fixture()
        duplicate = dict(catalog["revisions"][1], revision_id="repeat")
        catalog["revisions"].append(duplicate)
        fields["repeat"] = fields["second"]
        catalog["updates"].append(dict(revision_id="repeat", snapshot="third", observed_at="2026-11-14T12:00:00+00:00"))
        first = source_updates(group_editions(catalog, fields.__getitem__))
        self.assertEqual(len(first), 1)
        catalog["revisions"][1]["listed_now"] = False
        self.assertEqual(source_updates(group_editions(catalog, fields.__getitem__))[0]["event_id"], first[0]["event_id"])

    def test_new_schema_or_other_contour_is_not_a_source_addition(self):
        for change in (dict(environment="prod"), dict(dependencies=[["Fault.xsd", "changed"]])):
            catalog, fields = self.fixture()
            catalog["revisions"][1].update(change)
            self.assertEqual(source_updates(group_editions(catalog, fields.__getitem__)), [])

    def test_new_sources_in_same_later_batch_each_appear_once(self):
        catalog, fields = self.fixture()
        row = dict(catalog["revisions"][1], revision_id="third", document_sha256="doc3", document_url="https://example.test/third.docx")
        catalog["revisions"].append(row)
        fields["third"] = fields["second"]
        catalog["updates"].append(dict(catalog["updates"][1], revision_id="third"))
        events = source_updates(group_editions(catalog, fields.__getitem__))
        self.assertEqual(len(events), 2)
        self.assertEqual(len({e["event_id"] for e in events}), 2)
        self.assertEqual({e["revision_id"] for e in events}, {"first"})

    def test_initial_event_lists_initial_sources_without_future_documents(self):
        catalog, fields = self.fixture()
        third = dict(catalog["revisions"][1], revision_id="third", document_sha256="doc3", document_url="https://example.test/third.docx")
        catalog["revisions"].append(third)
        fields["third"] = fields["second"]
        catalog["updates"].insert(1, dict(catalog["updates"][0], revision_id="third"))
        grouped = group_editions(catalog, fields.__getitem__)
        self.assertEqual({s["document_sha256"] for s in display_updates(grouped)[0]["event_sources"]}, {"doc1", "doc3"})
        self.assertEqual([e["source"]["document_sha256"] for e in source_updates(grouped)], ["doc2"])


if __name__ == "__main__":
    unittest.main()
