import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog import export
from sync import encode, write


class JournalTests(unittest.TestCase):
    def test_current_label_updates_card_but_not_first_observation(self):
        from edition_groups import group_editions, display_updates
        row = dict(self.revision("same"), document_sha256="a"*64, document_url="https://example.test/doc.docx",
                   label_date="01.01.2026", document_download="documents/doc.docx")
        self.add("old", [row])
        self.add("current", [dict(row, document_version="3.1", label_date="02.02.2026")])
        self.export()
        catalog = json.loads((self.state / "catalog.json").read_bytes())
        grouped = group_editions(catalog, lambda rid: [])
        source = grouped["revisions"][0]["edition_sources"][0]
        self.assertEqual(source["document_version"], "3.1")
        self.assertEqual(source["label_date"], "02.02.2026")
        self.assertIn("-v3.1-", source["document_filename"])
        original = display_updates(grouped)[0]["event_sources"][0]
        self.assertEqual(original["document_version"], "3.0")
        self.assertEqual(original["label_date"], "01.01.2026")
        self.assertIn("-v3.0-", original["document_filename"])

    def test_two_current_urls_of_one_revision_remain_current_independently(self):
        from edition_groups import group_editions
        row = dict(self.revision("same"), document_sha256="doc", document_url="https://example.test/one.docx")
        other = dict(row, document_url="https://example.test/two.docx")
        self.add("both", [row, other])
        self.export()
        def sources():
            catalog = json.loads((self.state / "catalog.json").read_bytes())
            return group_editions(catalog, lambda rid: [])["revisions"][0]["edition_sources"]
        self.assertTrue(all(s["listed_now"] for s in sources()))
        self.add("one", [row])
        self.export()
        self.assertEqual({s["document_url"]: s["listed_now"] for s in sources()},
                         {row["document_url"]: True, other["document_url"]: False})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.revisions = {}
        self.observations = {}

    def revision(self, rid, env="prod", number="10110"):
        return dict(revision_id=rid, environment=env, number=number, document_version="3.0")

    def add(self, key, rows, observed_at=None):
        self.revisions[key] = copy.deepcopy(rows)
        write(self.state / "snapshots" / (key + ".json"), encode(dict(key=key)))
        if observed_at:
            self.observations[key] = observed_at
        write(self.state / "observations.json", encode(dict(format=1, snapshots=self.observations)))
        write(self.state / "history.json", encode(list(self.revisions)))
        write(self.state / "current.json", encode(dict(snapshot=key)))

    def export(self):
        def derive(snapshot):
            return dict(revisions=copy.deepcopy(self.revisions[snapshot["key"]]), registry=[], review=[])
        with patch("catalog.derive", side_effect=derive):
            export(self.state, self.state / "catalog.json")
        catalog = json.loads((self.state / "catalog.json").read_bytes())
        self.assertIn("updates", catalog)
        return catalog["updates"]

    def test_initial_import_and_repeat_keep_first_observation(self):
        self.add("first", [self.revision("p1"), self.revision("t1", "test")], "2026-11-12T12:38:50+00:00")
        first = self.export()
        self.add("repeat", [self.revision("p1"), self.revision("t1", "test")], "2026-11-13T10:00:00+00:00")
        self.assertEqual(self.export(), first)
        self.assertEqual(len(first), 2)
        self.assertTrue(all(e["initial_import"] for e in first))
        self.assertTrue(all(e["previous_revision_id"] is None for e in first))
        self.assertTrue(all(e["observed_at"] == "2026-11-12T12:38:50+00:00" for e in first))

    def test_new_revision_compares_with_same_contour_and_message(self):
        self.add("first", [self.revision("p1"), self.revision("t1", "test"), self.revision("other", number="32")])
        self.add("next", [self.revision("p2"), self.revision("t1", "test"), self.revision("other", number="32")], "2026-11-14T04:17:00+00:00")
        event = self.export()[-1]
        self.assertEqual(event["revision_id"], "p2")
        self.assertEqual(event["previous_revision_id"], "p1")
        self.assertFalse(event["initial_import"])
        self.assertEqual(event["observed_at"], "2026-11-14T04:17:00+00:00")

    def test_third_10110_edition_uses_immediately_previous_even_after_retirement(self):
        self.add('A', [self.revision('A'), self.revision('test', 'test')])
        self.add('B', [self.revision('B'), self.revision('test', 'test')])
        self.add('retired', [self.revision('test', 'test')])
        self.add('C', [self.revision('C'), self.revision('test', 'test')])
        events = self.export()
        self.assertEqual(events[-1]['previous_revision_id'], 'B')
        self.assertEqual([e['revision_id'] for e in events], ['A', 'test', 'B', 'C'])

    def test_removed_and_returned_edition_is_not_a_new_appearance(self):
        self.add("first", [self.revision("p1")])
        first = self.export()
        self.add("removed", [])
        self.add("returned", [self.revision("p1")])
        self.assertEqual(self.export(), first)

    def test_return_to_saved_snapshot_restores_current_metadata_and_keeps_events(self):
        from edition_groups import display_updates, group_editions, source_updates
        first = dict(self.revision("same"), document_url="/A", document_version="1.0", document_sha256="same-bytes")
        second = dict(first, document_url="/B", document_version="1.1")
        self.add("A", [first])
        self.add("B", [second])
        events = self.export()
        catalog = json.loads((self.state / "catalog.json").read_bytes())
        source = display_updates(catalog)[0]["event_sources"][0]
        self.assertEqual(source["document_url"], "/A")
        self.assertEqual(source["document_version"], "1.0")
        self.assertEqual(source["environment"], "prod")
        grouped = group_editions(catalog, lambda rid: [])
        self.assertEqual([s['document_url'] for s in grouped['revisions'][0]['edition_sources']], ['/A', '/B'])
        added_sources = source_updates(grouped)
        self.assertEqual(len(added_sources), 1)
        self.assertEqual(added_sources[0]['source']['document_url'], '/B')
        self.assertEqual(added_sources[0]['source']['document_version'], '1.1')
        write(self.state / "current.json", encode(dict(snapshot="A")))
        self.assertEqual(self.export(), events)
        returned = json.loads((self.state / "catalog.json").read_bytes())
        listed = returned['revisions']
        self.assertEqual(listed, [dict(first, listed_now=True)])
        restored_sources = source_updates(group_editions(returned, lambda rid: []))
        self.assertEqual([event['event_id'] for event in restored_sources], [event['event_id'] for event in added_sources])
        self.assertEqual([s['listed_now'] for s in group_editions(returned, lambda rid: [])['revisions'][0]['edition_sources']], [True, False])

    def test_missing_observation_stays_unknown(self):
        self.add("first", [self.revision("p1")])
        self.assertIsNone(self.export()[0]["observed_at"])

    def test_new_editions_in_one_batch_do_not_compare_to_each_other(self):
        self.add("first", [self.revision("p1")])
        self.add("batch", [self.revision("p2"), self.revision("p3"), self.revision("new_message", number="32")])
        events = self.export()[1:]
        self.assertEqual([e["previous_revision_id"] for e in events], ["p1", "p1", None])


if __name__ == "__main__":
    unittest.main()
