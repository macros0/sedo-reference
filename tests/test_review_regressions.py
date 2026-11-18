import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import auto_update
from catalog import derive
from collector_feed import import_feed, store_feed
from tests import test_collector_feed as feed_tests


def generation_value(base, folder):
    return base / folder / ('index.html' if folder == 'site' else 'value')


def mark_sites(root, stage):
    for base in (root, stage):
        (base/'site/catalog-data.js').write_text('window.SEDO_CATALOG={"revisions":[]};')
        (base/'site/.nojekyll').touch()


class ReviewRegressions(unittest.TestCase):
    def test_promotion_cleanup_interruption_never_rolls_back_committed_generation(self):
        for remaining in (('state', 'data', 'site'), ('data', 'site'), ('site',), ()):
            with self.subTest(remaining=remaining), tempfile.TemporaryDirectory() as tmp:
                root, stage = Path(tmp) / 'root', Path(tmp) / 'stage'
                for base in (root, stage):
                    for folder in ('state', 'data', 'site'):
                        (base / folder).mkdir(parents=True)
                        generation_value(base, folder).write_bytes(base.name.encode())
                mark_sites(root, stage)
                recovery = root / '.update-recovery'
                rmtree = auto_update.shutil.rmtree
                unlink = Path.unlink
                def interrupt_cleanup(path, *args, **kwargs):
                    if Path(path) == recovery/'previous/state/value':
                        unlink(recovery / 'committed', missing_ok=True)
                        unlink(recovery / 'preparing', missing_ok=True)
                        for folder in ('state', 'data', 'site'):
                            if folder not in remaining:
                                rmtree(recovery / 'previous' / folder)
                        raise SystemExit('interrupted cleanup')
                    return unlink(path, *args, **kwargs)
                with patch.object(Path, 'unlink', interrupt_cleanup):
                    with self.assertRaises(SystemExit):
                        auto_update.promote(root, stage)
                auto_update.recover_promotion(root)
                auto_update.recover_promotion(root)
                for folder in ('state', 'data', 'site'):
                    self.assertEqual(generation_value(root, folder).read_bytes(), b'stage')

    def test_shrinking_feed_keeps_confirmed_index(self):
        a, objects = feed_tests.FeedTests().capture()
        b, other_objects = feed_tests.FeedTests().capture(True)
        with tempfile.TemporaryDirectory() as remote, tempfile.TemporaryDirectory() as local:
            remote, local = Path(remote), Path(local)
            store_feed(remote, [a], objects)
            import_feed(local, lambda p: (remote / p).read_bytes())
            store_feed(remote, [b], other_objects)
            import_feed(local, lambda p: (remote / p).read_bytes())
            store_feed(remote, [a], objects)
            import_feed(local, lambda p: (remote / p).read_bytes())
            original = (local / 'collector/feed.json').read_bytes()
            for index in [dict(format=1, manifests=[], latest={}),
                          dict(format=1, manifests=json.loads(original)['manifests'], latest={}),
                          dict(format=1, manifests=[dict(environment=a['environment'], content_id=a['content_id'])],
                               latest={a['environment']: a['content_id']})]:
                with self.assertRaises(ValueError):
                    import_feed(local, lambda p: json.dumps(index).encode())
                self.assertEqual(original, (local / 'collector/feed.json').read_bytes())

    def test_equivalent_archives_are_one_candidate(self):
        doc = dict(kind='docx', environment='prod', url='https://example.org/Тип сообщения 999.docx',
                   label='v1.0', sha256='1'*64,
                   structure=[('Корневой элемент', 'r'), ('Пространство имен', 'urn:test')])
        schema = dict(path='r.xsd', namespace='urn:test', sha256='3'*64,
                      dependencies=['types.xsd'], roots=[dict(name='r', description='r')])
        dep = dict(path='types.xsd', namespace='urn:test', sha256='4'*64, dependencies=[], roots=[])
        archive = dict(kind='zip', environment='prod', url='https://example.org/a.zip',
                       sha256='2'*64, schemas=[schema, dep])
        other = copy.deepcopy(archive)
        other['url'] = 'https://example.org/b.zip'
        original = derive(dict(entries=[doc, archive]))['revisions'][0]
        for archives in ([archive, other], [other, archive]):
            result = derive(dict(entries=[doc, *archives]))
            self.assertEqual([], result['review'])
            self.assertEqual(original, result['revisions'][0])
        other['schemas'][1]['sha256'] = '5'*64
        self.assertTrue(derive(dict(entries=[doc, archive, other]))['review'])

    def test_interrupted_promotion_recovers_and_retries(self):
        for fail_at in range(1, 8):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as tmp:
                root, stage = Path(tmp) / 'root', Path(tmp) / 'stage'
                for base, text in [(root, 'old'), (stage, 'new')]:
                    for folder in ['state', 'data', 'site']:
                        (base / folder).mkdir(parents=True)
                        generation_value(base, folder).write_text(text)
                mark_sites(root, stage)
                replace = Path.replace
                calls = 0
                def interrupt(source, target):
                    nonlocal calls
                    calls += 1
                    if calls == fail_at:
                        raise SystemExit('simulated process interruption')
                    return replace(source, target)
                with patch.object(Path, 'replace', interrupt):
                    with self.assertRaises(SystemExit):
                        auto_update.promote(root, stage)
                auto_update.recover_promotion(root)
                for folder in ['state', 'data', 'site']:
                    self.assertEqual('old', generation_value(root, folder).read_text())
                auto_update.promote(root, stage)
                for folder in ['state', 'data', 'site']:
                    self.assertEqual('new', generation_value(root, folder).read_text())
                self.assertFalse((root / '.update-recovery').exists())

    def test_manual_import_recovers_complete_generation_after_interruption(self):
        from manual_import import promote_import
        with tempfile.TemporaryDirectory() as temp:
            root, stage = Path(temp) / "root", Path(temp) / "stage"
            files = ["state/objects/blob", "state/snapshots/snapshot", "state/history.json",
                     "state/observations.json", "state/inbox.json", "data/catalog.json",
                     "data/fields/index.json", "site/index.html"]
            for directory in (root, stage):
                for name in files:
                    path = directory / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(directory.name)
            mark_sites(root, stage)
            replace = Path.replace
            def interrupt(source, target):
                if target in (root / "data/catalog.json", root / "site"):
                    raise SystemExit("interrupted publication")
                return replace(source, target)
            with patch.object(Path, "replace", interrupt), self.assertRaises(SystemExit):
                promote_import(root, stage)
            auto_update.recover_promotion(root)
            self.assertTrue(all((root / name).read_text() == "root" for name in files))

    def test_inbox_cannot_publish_while_automatic_update_holds_the_lock(self):
        from import_inbox import process_inbox
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(auto_update, "_run", side_effect=lambda root: process_inbox(root)):
                with self.assertRaises(OSError):
                    auto_update.run(root)

    def test_partial_journal_write_keeps_previous_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, stage = Path(tmp) / 'root', Path(tmp) / 'stage'
            for base in [root, stage]:
                for folder in ['state', 'data', 'site']:
                    (base / folder).mkdir(parents=True)
                    generation_value(base, folder).write_text(base.name)
            mark_sites(root, stage)
            write_bytes = Path.write_bytes
            def disk_full(path, content):
                if path.name == 'journal.tmp':
                    write_bytes(path, b'{')
                    raise OSError('disk full')
                return write_bytes(path, content)
            with patch.object(Path, 'write_bytes', disk_full):
                with self.assertRaises(OSError):
                    auto_update.promote(root, stage)
            auto_update.recover_promotion(root)
            for folder in ['state', 'data', 'site']:
                self.assertEqual('root', generation_value(root, folder).read_text())
