import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog import export
from sync import encode, write


class JournalTests(unittest.TestCase):
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

    def test_removed_and_returned_edition_is_not_a_new_appearance(self):
        self.add("first", [self.revision("p1")])
        first = self.export()
        self.add("removed", [])
        self.add("returned", [self.revision("p1")])
        self.assertEqual(self.export(), first)

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
