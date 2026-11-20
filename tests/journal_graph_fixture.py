"""Build the review journal fixture from two archived, resolved XSD graphs."""
import html
import json
import shutil
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import build_site
from catalog import export
from collector_feed import import_feed, store_feed
from collector_snapshots import make_manifest, replay_archived_inputs
from fields import build
from sync import SITES, sha, write
from tests.test_module_messages import paragraph
from tests.test_resolved_schema_identity import BASE, ROOT, datatype, library, schema, verdicts
from tests.test_source_collection_regressions import SourceCollectionTests, archive
from tests.test_wsdl_binding_protection import definitions, include
from tests.test_wsdl_sources import SEED
from wsdl_sources import collect


def journal_graph_fixture(*, move=False, publication=None):
    fixture = SourceCollectionTests()
    fixture.setUp()
    try:
        return _build_journal_graph_fixture(fixture, move=move, publication=publication)
    finally:
        fixture.doCleanups()


def publication_library(service, member):
    seed = SEED.replace('?wsdl', service + '?wsdl')
    entry = 'root.xsd?xsd=' + member + '/root.xsd'
    resources = {
        seed: definitions(include(entry)),
        BASE + entry: ROOT,
        BASE + 'a/wrapper.xsd': schema(include('value.xsd'), 'urn:a'),
        BASE + 'b/wrapper.xsd': schema(include('value.xsd'), 'urn:b'),
        BASE + 'a/value.xsd': datatype('a'),
        BASE + 'b/value.xsd': datatype('b'),
    }
    objects = {}

    def blob(raw):
        digest = sha(raw)
        objects[digest] = raw
        return digest

    return collect(seed, 'test', resources.__getitem__, blob), objects, dict(wsdl=seed, xsd=BASE + entry)


def _build_journal_graph_fixture(fixture, *, move, publication):
    project = Path(__file__).resolve().parents[1]
    shutil.copytree(project / 'web', fixture.root / 'web')
    shutil.copy2(project / 'viewer-template.html', fixture.root)
    for page in SITES.values():
        fixture.resources[page] = b'<a href="/specs_sedo/file.zip">Schema</a>'
    url = fixture.add_individual('999', 'r', 'root.xsd')
    with ZipFile(BytesIO(fixture.resources[url])) as doc:
        xml = doc.read('word/document.xml').decode()
    example = html.escape('<r xmlns="urn:main"><first>ABC</first></r>')
    extra = paragraph('Пример XML-документа') + paragraph(example)
    fixture.resources[url] = archive('word/document.xml', xml.replace('</w:body>', extra + '</w:body>').encode())

    if publication:
        prior, prior_objects, old_links = publication_library('', 'old')
        current, current_objects, new_links = publication_library('2' if publication == 'wsdl' else '', 'new')
    else:
        prior, prior_objects = library('a', 'b')
        current, current_objects = library('b', 'a')
    for digest, raw in (prior_objects | current_objects).items():
        write(fixture.state / 'objects' / digest, raw)
    fixture.collect(1, wsdl_entries=[prior])
    index = fixture.root / 'data/catalog.json'
    fields = fixture.root / 'data/fields'
    export(fixture.state, index)
    build(index, fixture.state, fields)
    if move or publication:
        new_url = url
        if move:
            new_url = url.replace('/specs_sedo/', '/specs_sedo/reissued/')
            old_path = url.split('.ru', 1)[1].encode()
            new_path = new_url.split('.ru', 1)[1].encode()
            fixture.resources[SITES['test']] = fixture.resources[SITES['test']].replace(old_path, new_path)
            fixture.resources[new_url] = fixture.resources.pop(url)
        fixture.collect(2, wsdl_entries=[current if publication else prior])
    else:
        remote = fixture.root / 'feed'
        store_feed(remote, [make_manifest(current, '2026-11-14T01:30:00+00:00')], current_objects)
        import_feed(fixture.state, lambda path: (remote / path).read_bytes())
        with patch('sync.get', side_effect=fixture.resources.__getitem__):
            [record] = replay_archived_inputs(fixture.state)
        assert record['status'] == 'replayed', record
    export(fixture.state, index)
    build(index, fixture.state, fields)
    with patch.object(build_site, 'HERE', fixture.root):
        build_site.main()

    site = fixture.root / 'site'
    catalog = json.loads((site / 'catalog-data.js').read_text('utf8')
                         .removeprefix('window.SEDO_CATALOG=').removesuffix(';'))
    result = dict(catalog=catalog, viewers={p.name: p.read_text('utf8')
                                            for p in (site / 'editions').glob('*.html')})
    if move or publication:
        [row] = catalog['revisions']
        if move:
            assert [(source['document_url'], source['listed_now']) for source in row['edition_sources']] == [
                (url, False), (new_url, True)]
        result.update(old_url=url, new_url=new_url,
                      document=b64encode((site / row['document_download']).read_bytes()).decode('ascii'))
        if publication:
            result.update(old_links=old_links, new_links=new_links,
                          files={p.name: b64encode(p.read_bytes()).decode('ascii')
                                 for p in (site / 'sources').glob('*.xsd')})
    else:
        first, second = catalog['revisions']
        assert first['dependency_digest'] == second['dependency_digest']
        assert first['resolved_schema_graph'] != second['resolved_schema_graph']
        assert [verdicts(fixture, row) for row in (first, second)] == [[True, False], [False, True]]
        assert first['schema_review']['findings'] == second['schema_review']['findings']
    return result
