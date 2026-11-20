import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from catalog import export
from collector_feed import import_feed, store_feed
from collector_snapshots import apply_pending, make_manifest, pending_work
from fields import build
from sync import SITES, sync, write
from tests import test_source_collection_regressions as fixtures
from tests import test_wsdl_sources as wsdl


def late_import_journal(fixture):
    import build_site
    from auto_update import edition_changes

    page = SITES['test']
    base = page.rsplit('/', 1)[0]
    schema = (f'<xs:schema xmlns:xs="{wsdl.XS}" targetNamespace="urn:main">'
              '<xs:element name="response" type="xs:string"/></xs:schema>').encode()
    fixture.resources[base + '/specs_sedo/file.zip'] = fixtures.archive('main.xsd', schema)
    paths = {key: f'/specs_sedo/Тип_сообщения_999_{key}.docx' for key in 'AB'}
    raw = fixtures.individual_document('response')
    distinct = BytesIO(raw)
    with ZipFile(distinct, 'a') as archive:
        archive.comment = b'distinct source bytes for the same schema'
    fixture.resources[base + paths['A']] = raw
    fixture.resources[base + paths['B']] = distinct.getvalue()
    index = fixture.root / 'data/catalog.json'
    catalogs = []
    for microsecond, key in ((2, 'A'), (1, 'B')):
        fixture.resources[page] = (f'<a href="/specs_sedo/file.zip">Schema</a>'
                                   f'<a href="{paths[key]}">DOCX {key}</a>').encode()
        with patch('sync.get', side_effect=fixture.resources.__getitem__), patch('sync.datetime') as clock:
            stamp = datetime(2026, 11, 14, 1, microsecond=microsecond, tzinfo=timezone.utc)
            clock.now.return_value = stamp.astimezone(timezone(timedelta(hours=3, seconds=15))) if key == 'B' else stamp
            sync(fixture.state)
        export(fixture.state, index)
        catalogs.append(json.loads(index.read_bytes()))
    fields = fixture.root / 'data/fields'
    build(index, fixture.state, fields)
    project = Path(__file__).resolve().parents[1]
    shutil.copytree(project / 'web', fixture.root / 'web')
    shutil.copy2(project / 'viewer-template.html', fixture.root)
    with patch.object(build_site, 'HERE', fixture.root):
        summary = build_site.main()
    _, sources = edition_changes(*catalogs, fields, journal=summary)
    public = json.loads((fixture.root / 'site/catalog-data.js').read_text('utf8')
                        .removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
    return public, catalogs, sources


class ReplayedJournalTests(unittest.TestCase):
    def test_late_import_respects_microseconds_in_site_and_report(self):
        fixture = fixtures.SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        public, catalogs, sources = late_import_journal(fixture)
        first, = public['schema_updates']
        added, = public['source_updates']
        report, = sources
        self.assertEqual(len({r['revision_id'] for r in public['revisions']}), 2)
        self.assertEqual([{key: event[key] for key in original} for event, original in
                          zip(public['updates'], catalogs[1]['updates'], strict=True)], catalogs[1]['updates'])
        self.assertTrue(first['event_sources'][0]['document_url'].endswith('_B.docx'))
        self.assertTrue(added['source']['document_url'].endswith('_A.docx'))
        self.assertLess(datetime.fromisoformat(first['observed_at']), datetime.fromisoformat(added['observed_at']))
        self.assertEqual(added['event_id'], report['event_id'])
        self.assertEqual(first['observed_at'], '2026-11-14T04:00:15.000001+03:00:15')
        self.assertEqual(first['observed_at_utc'], '2026-11-14T01:00:00.000001Z')
        self.assertEqual(added['observed_at_utc'], '2026-11-14T01:00:00.000002Z')
        self.assertEqual(int(added['observed_at_us']) - int(first['observed_at_us']), 1)
        self.assertEqual(report['observed_at'], added['observed_at'])

    def test_each_source_uses_its_earliest_confirmed_snapshot(self):
        import build_site

        from auto_update import edition_changes

        for captures in ([(3, 'A'), (4, 'B'), (1, 'A'), (2, 'B')],
                         [(1, 'A'), (2, 'B'), (3, 'A'), (4, 'B')],
                         [(2, 'B'), (3, 'B'), (1, 'A'), (4, 'A')]):
            with self.subTest(captures=captures):
                fixture = fixtures.SourceCollectionTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                page = SITES['test']
                base = page.rsplit('/', 1)[0]
                schema = (f'<xs:schema xmlns:xs="{wsdl.XS}" targetNamespace="urn:main">'
                          '<xs:element name="response" type="xs:string"/></xs:schema>').encode()
                fixture.resources[base + '/specs_sedo/file.zip'] = fixtures.archive('main.xsd', schema)
                urls = {key: f'/specs_sedo/Тип_сообщения_999_{key}.docx' for key in 'AB'}
                for url in urls.values():
                    fixture.resources[base + url] = fixtures.individual_document('response')
                index = fixture.root / 'data/catalog.json'
                for offset, (hour, key) in enumerate(captures):
                    fixture.resources[page] = ('<a href="/specs_sedo/file.zip">Schema</a>'
                                               f'<a href="{urls[key]}">DOCX {key} at {hour}</a>').encode()
                    fixture.collect(hour)
                    if offset == 0:
                        export(fixture.state, index)
                        previous = json.loads(index.read_bytes())
                export(fixture.state, index)
                catalog = json.loads(index.read_bytes())
                raw = catalog['source_observations']
                self.assertEqual([int(event['observed_at'][11:13]) for event in raw],
                                 [hour for offset, (hour, key) in enumerate(captures)
                                  if key not in {old_key for _, old_key in captures[:offset]}])
                snapshots = json.loads((fixture.state / 'observations.json').read_bytes())['snapshots']
                build(index, fixture.state, fixture.root / 'data/fields')
                project = Path(__file__).resolve().parents[1]
                shutil.copytree(project / 'web', fixture.root / 'web')
                shutil.copy2(project / 'viewer-template.html', fixture.root)
                with patch.object(build_site, 'HERE', fixture.root):
                    summary = build_site.main()
                public = json.loads((fixture.root / 'site/catalog-data.js').read_text('utf8')
                                    .removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
                event, = public['source_updates']
                self.assertEqual(public['schema_updates'][0]['observed_at'][11:16], '01:00')
                self.assertEqual(event['observed_at'][11:16], '02:00')
                self.assertEqual(snapshots[event['snapshot']][11:16], '02:00')
                self.assertTrue(event['source']['document_url'].endswith('_B.docx'))
                self.assertTrue(event['source']['document_download'])
                schemas, report = edition_changes(previous, catalog, fixture.root / 'data/fields', journal=summary)
                self.assertEqual(schemas, [])
                reported, = report
                for key in ('event_id', 'snapshot', 'observed_at'):
                    self.assertEqual(reported[key], event[key])
                self.assertEqual(edition_changes(catalog, catalog, fixture.root / 'data/fields', journal=summary), ([], []))

    def test_replay_merges_source_dates_across_equivalent_raw_revision_ids(self):
        from tests.test_wsdl_binding_protection import NEIGHBOR, definitions

        fixture = fixtures.SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for page in SITES.values():
            fixture.resources[page] = b'<a href="/specs_sedo/file.zip">Schema</a>'
        first_doc = fixture.add_individual('999', 'r', None)
        other = NEIGHBOR.replace('name="r"', 'name="other"')
        collect = wsdl.WsdlSourcesTests().collect
        later, later_objects = collect({wsdl.SEED: definitions(NEIGHBOR)})
        unavailable, unavailable_objects = collect({wsdl.SEED: definitions(other)})
        recovered, recovered_objects = collect({wsdl.SEED: definitions(other, NEIGHBOR)})
        for digest, raw in (later_objects | unavailable_objects).items():
            write(fixture.state / 'objects' / digest, raw)
        fixture.collect(0, wsdl_entries=[later])
        second_doc = first_doc.replace('999.docx', '999_v2.docx')
        raw = BytesIO(fixture.resources.pop(first_doc))
        with ZipFile(raw, 'a') as doc:
            doc.comment = b'distinct original with identical structure'
        fixture.resources[second_doc] = raw.getvalue()
        fixture.resources[SITES['test']] = fixture.resources[SITES['test']].replace(b'999.docx', b'999_v2.docx')
        fixture.collect(1, wsdl_entries=[unavailable])
        fixture.collect(3, wsdl_entries=[later])
        out = fixture.root / 'data/catalog.json'
        export(fixture.state, out)
        build(out, fixture.state, fixture.root / 'data/fields')
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / 'web', fixture.root / 'web')
        shutil.copy2(project / 'viewer-template.html', fixture.root)
        remote = fixture.root / 'feed'
        for hour, library, objects in [(2, recovered, recovered_objects), (3, later, later_objects)]:
            store_feed(remote, [make_manifest(library, f'2026-11-14T0{hour}:00:00+00:00')], objects)
        import_feed(fixture.state, lambda file: (remote / file).read_bytes())
        with patch('sync.get', side_effect=AssertionError('Unexpected network access')):
            self.assertEqual(pending_work(fixture.root), 'history')
            apply_pending(fixture.root)
            self.assertFalse(pending_work(fixture.root))
        public = json.loads((fixture.root / 'site/catalog-data.js').read_text('utf8')
                            .removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
        catalog = json.loads(out.read_bytes())
        observations = [item for item in catalog['source_observations'] if item['source']['document_url'] == second_doc]
        self.assertEqual([item['observed_at'][11:16] for item in observations], ['03:00', '02:00'])
        self.assertNotEqual(observations[0]['revision_id'], observations[1]['revision_id'])
        event, = public['source_updates']
        self.assertEqual(event['observed_at'][11:16], '02:00')
        self.assertEqual(event['snapshot'], observations[1]['snapshot'])
        self.assertEqual(event['source_revision_id'], observations[1]['revision_id'])
        self.assertTrue(event['source']['schema_file_download'])
        report = json.loads((fixture.root / 'update-report.json').read_bytes())
        self.assertEqual(report['new_source_events'], [])

    def test_replayed_equivalent_schema_keeps_its_id_and_displays_earliest_proven_event(self):
        from auto_update import edition_changes
        from edition_groups import display_updates, group_editions, source_updates
        from tests import test_source_updates as source_fixtures

        catalog, trees = source_fixtures.SourceUpdatesTests().fixture()
        original, alias = catalog['updates']
        original.update(observed_at='2026-11-12T02:00:00Z', initial_import=False)
        alias.update(observed_at='2026-11-12T01:00:00Z', previous_revision_id='middle')
        catalog['revisions'].append(dict(catalog['revisions'][0], revision_id='middle', document_sha256='doc3',
                                         dependencies=[['other.xsd', 'different']]))
        trees['middle'] = trees['first']
        catalog['updates'].insert(1, dict(revision_id='middle', snapshot='middle', observed_at='2026-11-12T01:30:00Z',
                                          previous_revision_id='first', initial_import=False))
        grouped = group_editions(catalog, trees.__getitem__)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for rid, tree in trees.items():
                (directory / (rid + '.json')).write_text(json.dumps(dict(fields=tree)), 'utf8')
            (directory / 'index.json').write_text(json.dumps([dict(revision_id=rid, status='ready') for rid in trees]), 'utf8')
            command = ['node', str(Path(__file__).resolve().parents[1] / 'summarize_updates.cjs'), str(directory)]
            result = subprocess.run(command, input=json.dumps(grouped), text=True, encoding='utf8', capture_output=True, check=True)
            grouped['updates'] = summaries = json.loads(result.stdout)
            first, middle = display_updates(grouped)
            self.assertEqual(first['revision_id'], 'first')
            self.assertEqual(first['observed_at'], alias['observed_at'])
            self.assertIsNone(first['previous_revision_id'])
            self.assertEqual(first['event_sources'][0]['document_sha256'], 'doc2')
            self.assertEqual(middle['previous_revision_id'], 'first')
            self.assertEqual(middle['comparison']['left'], 'first')
            added, = source_updates(grouped)
            self.assertEqual(added['source']['document_sha256'], 'doc1')
            report, reported_sources = edition_changes(dict(revisions=[], updates=[]), catalog, directory, journal=summaries)
            self.assertEqual([(e['revision_id'], e['observed_at'], e['previous_revision_id']) for e in report],
                             [(e['revision_id'], e['observed_at'], e['previous_revision_id']) for e in (first, middle)])
            self.assertEqual([e['source']['document_sha256'] for e in reported_sources], ['doc1'])
            previous = dict(catalog, revisions=[r for r in catalog['revisions'] if r['revision_id'] != 'second'],
                            updates=[original, catalog['updates'][1]])
            schemas, new_sources = edition_changes(previous, catalog, directory, journal=summaries)
            self.assertEqual(schemas, [])
            self.assertEqual([e['event_id'] for e in new_sources], [added['event_id']])

    def test_replayed_revision_keeps_raw_link_and_uses_chronological_predecessor(self):
        for earlier_ready in (True, False):
            with self.subTest(earlier_ready=earlier_ready):
                self.check_late_replay(earlier_ready)

    def test_exact_replayed_revision_displays_earlier_confirmed_appearance(self):
        public, report, previous, current = self.replay_individual([
            ('00:00', 'm:Missing', 0, False), ('01:00', 'xs:string', None, True),
            ('02:00', 'xs:date', None, True), ('03:00', 'xs:string', 3, False),
            ('04:00', 'xs:boolean', 4, True),
        ])
        self.assertEqual(current['updates'][:len(previous['updates'])], previous['updates'])
        self.assertEqual(current['source_observations'][:len(previous['source_observations'])],
                         previous['source_observations'])
        revisions = {row['revision_id'] for row in public['revisions'] if row['number'] == '999'}
        journal = {row['observed_at'][11:16]: row for row in public['schema_updates']
                   if row['revision_id'] in revisions}
        self.assertEqual(set(journal), {'01:00', '02:00', '04:00'})
        first, middle = journal['01:00'], journal['02:00']
        raw = next(row for row in current['updates'] if row['revision_id'] == first['revision_id'])
        self.assertEqual(raw['observed_at'][11:16], '03:00')
        authored = next(row for row in public['updates'] if row['revision_id'] == first['revision_id'])
        self.assertEqual(authored['observed_at'], raw['observed_at'])
        self.assertEqual(authored['snapshot'], raw['snapshot'])
        self.assertIsNone(first['previous_revision_id'])
        self.assertTrue(first['event_sources'][0]['schema_file_download'])
        self.assertEqual(middle['previous_revision_id'], first['revision_id'])
        self.assertEqual(middle['comparison']['left'], first['revision_id'])
        self.assertEqual(journal['04:00']['previous_revision_id'], middle['revision_id'])
        event, = [row for row in report['new_schema_revisions'] if row['revision_id'] in revisions]
        self.assertEqual(event['previous_revision_id'], first['revision_id'])
        self.assertFalse(any(row['revision_id'] == first['revision_id'] for row in public['source_updates']))

    def replay_individual(self, captures):
        fixture = fixtures.SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.add_individual('999', 'r', 'root.xsd')
        schema_url = wsdl.BASE.rsplit('/', 1)[0] + '/root.xsd'
        schema = (f'<xs:schema xmlns:xs="{wsdl.XS}" xmlns:m="urn:main" '
                  'targetNamespace="urn:main">')
        wrapper = ('<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/"><w:types>'
                   + schema + '<xs:include schemaLocation="root.xsd"/></xs:schema></w:types></w:definitions>').encode()
        remote = fixture.root / 'feed'
        for time, xsd_type, hour, feed in captures:
            raw = (schema + f'<xs:element name="r" type="{xsd_type}"/></xs:schema>').encode()
            library, objects = wsdl.WsdlSourcesTests().collect({wsdl.SEED: wrapper, schema_url: raw})
            if hour is not None:
                for digest, data in objects.items():
                    write(fixture.state / 'objects' / digest, data)
                fixture.collect(hour, wsdl_entries=[library])
            if feed:
                store_feed(remote, [make_manifest(library, f'2026-11-14T{time}:00+00:00')], objects)
        import_feed(fixture.state, lambda name: (remote / name).read_bytes())
        catalog = fixture.root / 'data/catalog.json'
        export(fixture.state, catalog)
        previous = json.loads(catalog.read_bytes())
        build(catalog, fixture.state, fixture.root / 'data/fields')
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(project / 'web', fixture.root / 'web')
        shutil.copy2(project / 'viewer-template.html', fixture.root)
        self.assertEqual(pending_work(fixture.root), 'history')
        apply_pending(fixture.root)

        public = json.loads((fixture.root / 'site/catalog-data.js').read_text('utf8')
                            .removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
        report = json.loads((fixture.root / 'update-report.json').read_bytes())
        return public, report, previous, json.loads(catalog.read_bytes())

    def check_late_replay(self, earlier_ready):
        first_type = 'xs:int' if earlier_ready else 'm:Missing'
        public, report, previous, current = self.replay_individual([
            ('01:00', first_type, 1, False), ('01:30', 'xs:date', None, True),
            ('02:00', 'xs:string', 2, True),
        ])
        raw_updates = current['updates']
        self.assertEqual(raw_updates[:len(previous['updates'])], previous['updates'])
        revisions = {row['revision_id'] for row in public['revisions'] if row['number'] == '999'}
        journal = {row['observed_at'][11:16]: row for row in public['schema_updates']
                   if row['revision_id'] in revisions}
        restored = journal['01:30']
        latest = journal['02:00']
        prior = journal['01:00']['revision_id'] if earlier_ready else None
        self.assertEqual(raw_updates[-1]['previous_revision_id'], latest['revision_id'])
        self.assertEqual(restored['previous_revision_id'], prior)
        self.assertEqual(restored['comparison']['left'], prior)
        self.assertEqual(latest['previous_revision_id'], restored['revision_id'])
        self.assertEqual(latest['comparison']['left'], restored['revision_id'])
        event, = [row for row in report['new_schema_revisions'] if row['revision_id'] in revisions]
        self.assertEqual(event['revision_id'], restored['revision_id'])
        self.assertEqual(event['previous_revision_id'], prior)
