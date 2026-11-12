import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import manual_publish
from manual_publish import PendingPublication, check_owner, git


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "work"
        self.root.mkdir()
        subprocess.run(["git", "init", "--bare", str(base / "remote.git")], capture_output=True, check=True)
        git(self.root, "init", "-b", "main")
        for folder in ("state", "data", "site"):
            (self.root / folder).mkdir()
            (self.root / folder / "initial.txt").write_text("initial", encoding="utf8")
        git(self.root, "add", ".")
        # Environment identity only: never change local or global Git config.
        with patch.dict(os.environ, GIT_AUTHOR_NAME="macros0", GIT_COMMITTER_NAME="macros0", GIT_AUTHOR_EMAIL="macros0@users.noreply.github.com", GIT_COMMITTER_EMAIL="macros0@users.noreply.github.com"):
            git(self.root, "commit", "-m", "Initial")
        git(self.root, "remote", "add", "origin", str(base / "remote.git"))
        git(self.root, "push", "origin", "main")
        self.head = git(self.root, "rev-parse", "HEAD")
        self.revision = dict(number="10110", environment="prod", document_version="9.9")

    def test_changed_preview_is_not_committed(self):
        path = self.root / "site/imported.txt"
        path.write_text("import", encoding="utf8")
        pending = PendingPublication(self.root, self.revision)
        path.write_text("changed later", encoding="utf8")
        with patch("manual_publish.check_owner"), self.assertRaisesRegex(ValueError, "изменил"):
            pending.publish()
        self.assertEqual(self.head, git(self.root, "rev-parse", "HEAD"))

    def test_unrelated_files_cannot_be_published(self):
        (self.root / "private.txt").write_text("unrelated", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "посторонние"):
            PendingPublication(self.root, self.revision)

    def test_push_retry_does_not_create_another_commit(self):
        (self.root / "site/imported.txt").write_text("import", encoding="utf8")
        pending = PendingPublication(self.root, self.revision)
        original_git = manual_publish.git
        def fail_push(root, *args, **kwargs):
            if args[0] == "push":
                raise ValueError("temporary network failure")
            return original_git(root, *args, **kwargs)
        identity = dict(GIT_AUTHOR_NAME="macros0", GIT_COMMITTER_NAME="macros0", GIT_AUTHOR_EMAIL="macros0@users.noreply.github.com", GIT_COMMITTER_EMAIL="macros0@users.noreply.github.com")
        with patch.dict(os.environ, identity), patch("manual_publish.check_owner"):
            with patch("manual_publish.git", side_effect=fail_push), self.assertRaises(ValueError):
                pending.publish()
            commit = pending.commit
            self.assertTrue(commit)
            restored=PendingPublication.load(self.root)
            self.assertEqual(restored.publish(), commit)
            self.assertFalse(restored.receipt.exists())
        self.assertEqual(git(self.root, "rev-list", "--count", "HEAD"), "2")
        self.assertEqual(git(self.root, "ls-remote", "origin", "refs/heads/main").split()[0], commit)

    def test_local_save_can_be_published_after_restart(self):
        (self.root/"site/imported.txt").write_text("import",encoding="utf8")
        pending=PendingPublication(self.root,self.revision)
        restored=PendingPublication.load(self.root)
        self.assertEqual(restored.expected,pending.expected)
        identity=dict(GIT_AUTHOR_NAME="macros0",GIT_COMMITTER_NAME="macros0",GIT_AUTHOR_EMAIL="macros0@users.noreply.github.com",GIT_COMMITTER_EMAIL="macros0@users.noreply.github.com")
        with patch.dict(os.environ,identity),patch("manual_publish.check_owner"):
            restored.publish()
        self.assertFalse(restored.receipt.exists())

    def test_other_github_account_is_rejected(self):
        result = subprocess.CompletedProcess([], 0, stdout=b"other-user\n", stderr=b"")
        with patch("manual_publish.git", return_value="https://github.com/macros0/sedo-reference.git"), patch("manual_publish.subprocess.run", return_value=result):
            with self.assertRaisesRegex(ValueError, "только владелец"):
                check_owner(self.root)


if __name__ == "__main__":
    unittest.main()
