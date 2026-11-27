"""Materialize a catalog from successful inventories. Does not contact SFR."""
import json
import posixpath
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit
from sync import DOCX_EXTENSION_SNAPSHOT_FORMAT, encode, sha, write

from schema import dependency_set, resolved_dependencies
from edition_groups import SOURCE_KEYS, SCHEMA_SOURCE_KEYS, observed_datetime
from document_metadata import parse_label, root_metadata, root_matches, read_document_history, read_document_display, prefer_schema_location
from document_metadata import MODULE_FILENAME, schema_location, schema_location_candidates, snapshot_display, wsdl_location_matches
from wsdl_sources import archived_wsdl_member, explicit_wsdl_entrypoints, prefer_wsdl_inline_entries

INDIVIDUAL_WSDL_MATCH_VERSION = 3


def graph_digest(graph):
    return sha(encode(graph))


def schema_qname(row):
    return row.get('namespace') or '', row.get('root')


def schema_binding(row):
    return row.get('resolved_schema_graph'), *schema_qname(row)


def declaration_identity(row):
    return row['number'], row.get('root'), row.get('namespace')


def declaration_failure(document, binding):
    """Read the final decision, preserving uncertainty for older mixed declarations."""
    identity = declaration_identity(binding)
    for result in document.get('module_decisions', ()):
        if declaration_identity(result) == identity:
            return result.get('reason')
    number, root, namespace = identity
    peers = [b for b in document.get('module_bindings', ()) if (b['number'], b['root']) == (number, root)]
    matches = [m for m in document.get('module_matches', ()) if (m['number'], m['root']) == (number, root)
               and (namespace is None or (m.get('namespace') or '') == namespace)]
    issues = [i for i in document.get('schema_diagnostics', ()) if i.get('number') == number
              and i.get('root', root) == root and ('namespace' not in i or i['namespace'] == namespace)]
    if matches:
        if namespace is None and peers:
            explicit = {p['namespace'] for p in peers if p.get('namespace') is not None}
            if all((m.get('namespace') or '') in explicit for m in matches):
                return next((i['reason'] for i in issues if 'namespace' in i),
                            'Связь исходной декларации с готовой схемой не определена.')
        return None
    return '; '.join(dict.fromkeys(i['reason'] for i in issues)) or 'Не найдена полная XSD для сообщения модуля.'


def module_declarations(document, row):
    """Return the declarations that can own this particular archived schema."""
    decisions = [item for item in document.get('module_decisions', ())
                 if (item['number'], item['root']) == (row['number'], row['root'])]
    if decisions:
        return [item for item in decisions if 'reason' not in item and
                (item['archive_sha256'], item['schema_path'], item['effective_namespace'] or '') ==
                (row.get('archive_sha256'), row.get('schema_path'), row.get('namespace') or '')]
    bindings = [b for b in document.get('module_bindings', ()) if
                (b['number'], b['root']) == (row['number'], row['root'])]
    namespace = row.get('namespace') or ''
    return ([b for b in bindings if b.get('namespace') == namespace] +
            [b for b in bindings if b.get('namespace') is None]) or [row]


def module_declaration(document, row):
    declarations = module_declarations(document, row)
    return next((b for b in declarations if b.get('namespace') == (row.get('namespace') or '')),
                next(iter(declarations), row))


def schema_revision_id(environment, number, document_sha, dependency_digest, graph, known_revisions,
                       *, root=None, namespace=None):
    """Reserve the first issued ID for each graph and effective root QName."""
    legacy = sha(encode([environment, number, document_sha, dependency_digest]))
    if not graph or not isinstance(known_revisions, Mapping) or legacy not in known_revisions:
        return legacy
    qualified = (namespace or '', root)

    def owns_binding(previous):
        # Older inbox indexes could provide just the issued ID as a preference.
        # Missing QName metadata gives no evidence of a collision.
        qname_known = 'root' in previous or 'namespace' in previous
        return (previous.get('resolved_schema_graph') in (None, graph)
                and (not qname_known or schema_qname(previous) == qualified))

    owner = known_revisions[legacy]
    if owns_binding(owner):
        return legacy
    graph_id = sha(encode(['resolved-xsd-graph-v1', legacy, graph]))
    graph_owner = known_revisions.get(graph_id)
    if graph_owner is not None and owns_binding(graph_owner):
        return graph_id
    if graph_owner is None and owner.get('resolved_schema_graph') not in (None, graph):
        return graph_id
    return sha(encode(['resolved-xsd-qname-v1', legacy, graph, qualified]))


def archived_graph_reader(state):
    """Resolve an edition from its exact archived bytes, including legacy snapshots."""
    from sync import inspect_file

    @lru_cache(maxsize=None)
    def inventory(digest):
        path = state / 'objects' / digest
        if not path.is_file():
            return None
        raw = path.read_bytes()
        if sha(raw) != digest:
            raise ValueError('Historical XSD archive digest mismatch: ' + digest)
        return inspect_file(raw, 'zip', legacy_schema_roots=True)['schemas']

    @lru_cache(maxsize=None)
    def graph(digest, entry):
        schemas = inventory(digest)
        if schemas is not None:
            return graph_digest(resolved_dependencies(schemas, entry)[1])

    return graph


def known_catalog_revisions(rows, state):
    """Read older catalog graphs from immutable archives before comparing imports."""
    archive_graph = archived_graph_reader(state)
    result = {}
    archived_rows = {}
    for row in rows:
        if row.get('status') == 'matched' and not row.get('resolved_schema_graph'):
            graph = archive_graph(row['archive_sha256'], row['schema_path'])
            if graph:
                row = dict(row, resolved_schema_graph=graph)
        snapshot = row.get('snapshot')
        if 'namespace' not in row and snapshot and 'resolved_schema_graph' in row:
            if snapshot not in archived_rows:
                raw = (state / 'snapshots' / (snapshot + '.json')).read_bytes()
                if sha(raw) != snapshot:
                    raise ValueError('Historical replay snapshot digest mismatch: ' + snapshot)
                archived_rows[snapshot] = derive(json.loads(raw), archive_graph=archive_graph)['revisions']
            namespaces = {schema_qname(candidate)[0] for candidate in archived_rows[snapshot]
                          if candidate.get('status') == 'matched'
                          and binding_observation_identity(candidate)[:5] == binding_observation_identity(row)[:5]
                          and candidate['root'] == row['root']}
            if len(namespaces) != 1:
                raise ValueError(f'Невозможно восстановить QName отозванной редакции {row["revision_id"]} '
                                 f'в снимке {snapshot}')
            row = dict(row, namespace=namespaces.pop())
        result[row['revision_id']] = row
    return result


def document_filename(row, *, legacy=False):
    return unquote(row['url'].split('/')[-1] if legacy else urlsplit(row['url']).path.rsplit('/',1)[-1])


def individual_number(row, *, legacy=False):
    if row["kind"] == "docx" and row.get("sha256"):
        filename = document_filename(row,legacy=legacy)
        match = re.search(r"тип[ _]+сообщения[ _]+(\d+)", filename, re.I)
        return match[1] if match else None

def _individual_candidates(document, archives, root_info):
    env = document['environment']
    own = [dict(a, environment=env, url=document['url'] + '#embedded=' + a['container'],
                archive_name=a['filename'] + ' (из DOCX)') for a in document.get('embedded_archives', [])]
    return [(a, s) for a in [*archives, *own] if a['environment'] == env
            for s in a.get('schemas', []) if root_matches(s, *root_info)]


def named_wsdl_members(document, archives, *, legacy=False):
    structure = document.get('structure', [])
    libraries = [archive for archive in archives if archive.get('source_type') == 'wsdl'
                 and archive['environment'] == document['environment']]
    if legacy:
        root, namespace, declared = root_metadata(structure)
        candidates = [(archive, schema) for archive in libraries for schema in archive.get('schemas', [])
                      if root_matches(schema, root, namespace, declared and bool(schema['namespace']))]
        return [(archive, schema, None) for archive, schema in schema_location_candidates(candidates, structure)]
    location = schema_location(structure)
    if not location:
        return []
    return [(archive, archived_wsdl_member(archive, source), source) for archive in libraries
            for source in archive.get('source_files', []) if source['kind'] == 'xsd'
            and wsdl_location_matches(location, archive, source)]


def record_individual_wsdl_matches(entries, read_object, *, legacy_filenames=False,
                                   version=INDIVIDUAL_WSDL_MATCH_VERSION):
    """Persist verified entry-point evidence only for DOCX files naming a WSDL member."""
    archives = [row for row in entries if row.get('source_type') == 'wsdl']
    probes = {}
    recorded = False
    for doc in entries:
        doc.pop('wsdl_schema_matches', None)
        if not archives or not individual_number(doc, legacy=legacy_filenames):
            continue
        matches = []
        root_info = root_metadata(doc.get('structure', []))
        for archive, schema, source in named_wsdl_members(doc, archives, legacy=version < 3):
            match = dict(archive_url=archive['url'], archive_sha256=archive['sha256'])
            if schema is not None:
                key = (archive['url'], archive['sha256'], schema['path'], root_info)
                if key not in probes:
                    probes[key] = explicit_wsdl_entrypoints(archive, schema, root_info, read_object, legacy=version < 3)
            if version < 3:
                match.update(declaration=schema['path'], entrypoints=probes[key])
            else:
                match.update(resource_url=source['url'], resource_sha256=source['sha256'])
                if source.get('final_url'):
                    match['resource_final_url'] = source['final_url']
                if schema is not None:
                    match.update(document_path=schema['path'], **probes[key])
            matches.append(match)
        if matches:
            doc['wsdl_schema_matches'] = dict(version=version, members=matches)
            if version >= 3:
                doc['wsdl_schema_matches']['location'] = schema_location(doc.get('structure', []))
            recorded = True
    return recorded


def document_identity(row):
    return row['environment'], row['number'], row['document_sha256']


def binding_observation_identity(row):
    return (*document_identity(row), row['document_url'], *schema_binding(row))


def replayed_binding_rejections(rows, records, *, snapshot=None, resolve=None):
    """Resolve the ledger for one immutable snapshot to its exact observations."""
    if not records:
        return {}
    matched = [row for row in rows if row['status'] == 'matched']
    exact = {binding_observation_identity(row) for row in matched}
    rejected = {}
    legacy = isinstance(records, dict)
    entries = records.items() if legacy else ((tuple(i['observation']), i['reason']) for i in records)
    for address, reason in entries:
        if not legacy and address in exact:
            rejected[address] = reason
            continue
        if legacy:
            choices = []
            for row in matched:
                base = schema_revision_id(*document_identity(row), row['dependency_digest'],
                                          row['resolved_schema_graph'], {})
                if address in (row['revision_id'], base, sha(encode(
                        ['resolved-xsd-graph-v1', base, row['resolved_schema_graph']]))):
                    choices.append(row)
        else:
            choices = [row for row in matched if binding_observation_identity(row)[:5] == address]
        if len(choices) > 1 and resolve is not None:
            choices = resolve(choices)
        identities = {binding_observation_identity(row) for row in choices}
        if len(identities) != 1:
            labels = ', '.join(repr(schema_qname(row)) for row in choices)
            raise ValueError(f'Невозможно восстановить архивную привязку DOCX к XSD '
                             f'в снимке {snapshot or "?"}: {address!r} {labels}')
        rejected[identities.pop()] = reason
    return rejected


def individual_documents(snapshot):
    legacy = snapshot.get('format', 7) < 7
    for doc in snapshot.get('entries', []):
        number = individual_number(doc, legacy=legacy)
        if number:
            yield (doc['environment'], number, doc['sha256']), doc


def _resolved_members(row):
    directory = posixpath.dirname(row['schema_path'])
    return {posixpath.normpath(posixpath.join(directory, path)): digest for path, digest in row['dependencies']}


def preserves_wsdl_binding(previous, current):
    """Recognize a fresh binding or an equivalent, explicitly named ZIP member."""
    location = previous['schema_docx_location']
    if current.get('schema_docx_location') == location and current.get('schema_docx_path'):
        return True
    if (not current.get('archive_sha256') or current.get('schema_source_type')
            or current.get('root') != previous.get('root')
            or (current.get('namespace') or None) != (previous.get('namespace') or None)):
        return False
    locator = urlsplit(location.replace('\\', '/'))
    if locator.scheme or locator.netloc or locator.query:
        return False
    structure = [('Расположение файла XML-схемы', location)]
    # A named chameleon member can be reachable through a distinct entry
    # schema. Resolve the member from the same closure used by the tree.
    named_path = previous['schema_docx_path']
    named_digest = _resolved_members(previous).get(named_path)
    if not named_digest:
        return False
    members = [path for path, digest in _resolved_members(current).items() if digest == named_digest]
    # WSDL packaging can rewrite the relative DOCX locator to an archive path.
    named_member = named_path in members or schema_location_candidates(
        [(None, {'path': path}) for path in members], structure)
    graph = previous.get('resolved_schema_graph')
    return bool(named_member and graph and graph == current.get('resolved_schema_graph'))


def named_binding_rows(snapshot, resolutions=None):
    """Read confirmed evidence even when the catalog chose an equivalent ZIP."""
    if not any(doc.get('wsdl_schema_matches') for _, doc in individual_documents(snapshot)):
        return
    if resolutions is None:
        resolutions = {}
        derive(snapshot, resolutions=resolutions)
    yield from (row for row in resolutions.values() if row.get('schema_docx_path'))


def _wsdl_entry_signatures(archive, entries):
    schemas = {row['path']: row['sha256'] for row in archive['schemas']}
    resources = {}
    for source in archive['source_files']:
        if source['kind'] == 'xsd' and (schema := archived_wsdl_member(archive, source)) is not None:
            resources[schema['path']] = source.get('final_url', source['url'])

    def signature(entry):
        path = entry['path']

        def identity(member):
            if member == path:
                return ('entry',)
            if member in resources:
                return ('url', resources[member])
            return ('archive', member)

        _, graph = resolved_dependencies(archive['schemas'], path, member_identity=identity)
        owner = entry['declaration']
        return identity(owner), schemas[owner], graph

    return frozenset(signature(entry) for entry in entries)


def _equivalent_wsdl_members(matches, archives, location):
    """Collapse only complete aliases of the same fetched XSD and entry closures."""
    if len(matches) < 2:
        return matches
    unique = {}
    for index, match in enumerate(matches):
        archive_key = match['archive_url'], match['archive_sha256']
        archive = archives[archive_key]
        paths = match.get('entrypoints', [])
        key = ('unverified', index)
        if paths:
            try:
                entries = _wsdl_entry_signatures(archive, paths)
            except (ValueError, KeyError):
                pass
            else:
                key = ('verified', match.get('resource_final_url', match['resource_url']),
                       match['resource_sha256'], entries)
        direct = wsdl_location_matches(location, archive, {'url': match['resource_url']})
        if key not in unique or (direct and not unique[key][0]):
            unique[key] = direct, match
    return [match for _, match in unique.values()]


def _apply_explicit_wsdl_matches(document, archives, candidates):
    evidence = document['wsdl_schema_matches']
    matches = evidence['members'] if isinstance(evidence, dict) else evidence
    modern = isinstance(evidence, dict) and evidence.get('version', 1) >= 3
    indexed = {(a['url'], a['sha256']): a for a in archives if a['environment'] == document['environment']}
    locator = urlsplit(evidence['location'].replace('\\', '/')) if modern else None
    selected = [(a, s) for a, s in schema_location_candidates(candidates, document.get('structure', []))
                if a.get('source_type') != 'wsdl' and (not locator or not (locator.scheme or locator.netloc))]
    if modern:
        matches = _equivalent_wsdl_members(matches, indexed, evidence['location'])
        if not matches:
            # Replay previously confirmed this location and could no longer
            # prove it. A QName-only match cannot supply a new edition.
            selected = []
    declarations, incomplete, locations = {}, [], {}
    for match in matches:
        url, digest = match['archive_url'], match['archive_sha256']
        archive = indexed[url, digest]
        schemas = {s['path']: s for s in archive['schemas']}
        member = match.get('document_path') if modern else match['declaration']
        paths = match.get('entrypoints', [])
        if not paths:
            incomplete.append(match)
        for entry in paths:
            path, declaration = (entry['path'], entry['declaration']) if modern else (entry, member)
            selected.append((archive, schemas[path]))
            declarations[url, digest, path] = schemas[declaration]
            if modern:
                locations[url, digest, path] = dict(schema_docx_location=evidence['location'],
                    schema_docx_path=member, schema_docx_url=match['resource_url'])
    return selected, declarations, incomplete, locations, matches


def _legacy_binding_location(document, matches, archive, entry, dependencies):
    # Formats 1 and 2 saved the verified entry and member, without a public
    # location. Recover it for the guard using the original DOCX metadata.
    location = schema_location(document.get('structure', []))
    if not location:
        return None
    paths = _resolved_members(dict(schema_path=entry, dependencies=dependencies))
    named = {match['declaration'] for match in matches if match['archive_url'] == archive['url']
             and match['archive_sha256'] == archive['sha256'] and entry in match['entrypoints']
             and match['declaration'] in paths}
    if len(named) == 1:
        return dict(schema_docx_location=location, schema_docx_path=named.pop())


def derive(snapshot, known_revisions=(), *, preferred_revision_id=None, display_registry=None, resolutions=None,
           archive_graph=None):
    reservations = dict(known_revisions) if isinstance(known_revisions, Mapping) else {}

    def revision_graph(archive, entry, fallback=None):
        exact = archive_graph(archive, entry) if archive_graph is not None else None
        return exact or fallback

    def assign(row):
        row['revision_id'] = schema_revision_id(row['environment'], row['number'], row['document_sha256'],
            row.get('dependency_digest'), row.get('resolved_schema_graph'), reservations,
            root=row.get('root'), namespace=row.get('namespace'))
        if row['status'] == 'matched':
            reservations.setdefault(row['revision_id'], row)

    if snapshot.get("source_type")=="manual":
        row = dict(snapshot["revision"])
        if row.get('status') == 'matched' and not row.get('resolved_schema_graph'):
            graph = revision_graph(row['archive_sha256'], row['schema_path'])
            if graph:
                row['resolved_schema_graph'] = graph
        return dict(revisions=[row],registry=[],review=[])
    rows=snapshot["entries"];catalog=[]; registry=[]; issues=[]
    legacy_filenames = snapshot.get('format',7) < 7
    document_sources = []
    archives_by_digest = {}
    for r in rows:
        for archive in (r, *r.get('embedded_archives', []), *r.get('wsdl_archives', [])):
            if archive.get('schemas') and archive.get('sha256'):
                archives_by_digest[archive['sha256']] = archive['schemas']
        for table in r.get("registry_tables",[]):
            if table[0][:2]!=["Код типа сообщения","Наименование"]:continue
            group=""
            version, date = parse_label(r.get("label", ""))
            for cells in table[1:]:
                if len(cells)==1:group=cells[0]
                elif len(cells)>=4 and cells[0].isdigit():
                    registry.append(dict(environment=r["environment"],number=cells[0],title=cells[1],description=cells[2],appendix=cells[3],module=group,source=r["url"],
                                         document_url=r["url"], document_sha256=r.get("sha256"),
                                         document_version=version, label_date=date))
    # Module declarations supplement, rather than overwrite, the general list.
    known = {(r["environment"], r["number"]) for r in registry}
    official_modules = {}
    for row in registry:
        official_modules.setdefault((row['environment'], row['appendix']), set()).add(row['module'])
    modules = {}; appendices = {}
    for r in rows:
        if r["kind"] != "docx" or not r.get("sha256"): continue
        filename = document_filename(r,legacy=legacy_filenames)
        module = MODULE_FILENAME.search(filename)
        appendix = str(int(module[1])) if module else None
        name = module[2].replace("_", " ").strip() if module else r.get("label", filename)
        names = official_modules.get((r["environment"], appendix), set())
        modules[r["environment"], r["url"]] = next(iter(names)) if len(names) == 1 else name
        appendices[r['environment'], r['url']] = appendix
        version, date = parse_label(r.get('label', filename))
        matched = {m['number'] for m in r.get('module_matches', [])}
        named_individual = individual_number(r, legacy=legacy_filenames) if r.get('wsdl_schema_matches') else None
        for declaration in r.get("message_types", []):
            if declaration['number'] == named_individual:
                continue
            identity = (r["environment"], declaration["number"])
            decisions = [item for item in r.get('module_decisions', ()) if item['number'] == declaration['number']]
            failures = [item for item in decisions if 'reason' in item]
            if not decisions:
                siblings = [item for item in r.get('module_bindings', ()) if item['number'] == declaration['number']]
                if declaration['number'] in matched or len(siblings) == 1:
                    failures = [dict(item, reason=reason) for item in siblings
                                if (reason := declaration_failure(r, item))]
            for failure in failures or ([None] if declaration['number'] not in matched and not decisions else []):
                reasons = dict.fromkeys(issue['reason'] for issue in r.get('schema_diagnostics', [])
                                        if issue.get('number') == declaration['number'] and issue.get('reason'))
                source = dict(declaration, environment=r["environment"], appendix=appendix,
                    module=modules[r["environment"], r["url"]], document_version=version, label_date=date,
                    document_url=r["url"], document_sha256=r["sha256"],
                    version_tables=r.get("version_tables", []), document_history=r.get("document_history", []),
                    schema_reason='; '.join(reasons) or "Полная XSD для этого документа не сопоставлена.")
                if failure:
                    source.update(root=failure['root'], namespace=failure.get('namespace'), schema_reason=failure['reason'],
                                  schema_diagnostic=dict(code='module_binding_unmatched', root=failure['root'],
                                      declared_namespace=failure.get('namespace'), document_sha256=r['sha256']))
                document_sources.append(source)
            if not module or identity in known: continue
            registry.append(dict(environment=r["environment"], number=declaration["number"],
                                 title=declaration["title"], description="Тип описан в отдельной спецификации модуля.",
                                 appendix=appendix, module=name, source=r["url"],
                                 source_type="module", document_url=r["url"], document_sha256=r["sha256"],
                                 document_version=version, label_date=date))
            known.add(identity)
    for r in rows:
        number=individual_number(r,legacy=legacy_filenames)
        if not number:continue
        fname=document_filename(r,legacy=legacy_filenames);e=r["environment"]
        root, ns, namespace_declared = root_metadata(r.get("structure", []))
        archives=[a for a in rows if a["kind"]=="zip"]
        candidates=_individual_candidates(r, archives, (root, ns, namespace_declared))
        declarations, incomplete, locations, members = {}, [], {}, []
        explicit_wsdl = r.get('wsdl_schema_matches')
        if explicit_wsdl:
            candidates, declarations, incomplete, locations, members = _apply_explicit_wsdl_matches(r, archives, candidates)
        elif len(candidates) > 1:
            # Older snapshots retain the original path selection and IDs.
            if snapshot.get('format',7) >= 7 or any(s.get("wsdl_entry_members") for _,s in candidates):
                candidates = prefer_schema_location(candidates,r.get('structure',[]))
            candidates = prefer_wsdl_inline_entries(candidates, root)
        modern = isinstance(explicit_wsdl, dict) and explicit_wsdl.get('version', 1) >= 3
        equivalent, proofs = {}, {}
        for archive, schema in sorted(candidates, key=lambda pair: (pair[0]["url"], pair[0]["sha256"], pair[1]["path"])):
            preference = 0
            try:
                closure, graph = resolved_dependencies(archive["schemas"], schema["path"])
            except (ValueError, KeyError):
                # An unresolved candidate must still require review.
                key = ("unresolved", archive["url"], schema["path"])
                closure = None
            else:
                key = ("resolved", graph)
                if known_revisions:
                    digest = revision_graph(archive['sha256'], schema['path'], graph_digest(graph))
                    rid = schema_revision_id(e, number, r["sha256"], sha(encode(closure)), digest, reservations,
                                             root=root, namespace=schema['namespace'])
                    if rid in known_revisions:
                        preference = 2 if rid == preferred_revision_id else 1
                if resolutions is not None and explicit_wsdl and archive.get('source_type') == 'wsdl':
                    location = (locations.get((archive['url'], archive['sha256'], schema['path'])) if modern else
                                _legacy_binding_location(r, members, archive, schema['path'], closure))
                    if location:
                        proofs.setdefault(key, dict(location, schema_path=schema['path'], dependencies=closure,
                                                    archive_url=archive['url'], archive_sha256=archive['sha256']))
            if key not in equivalent or preference > equivalent[key][0]:
                equivalent[key] = (preference, archive, schema, closure)
        candidates = [(archive, schema) for _, archive, schema, _ in equivalent.values()]
        title_record=next((x for x in registry if x["number"]==number and x["environment"]==e),None)
        title=title_record["title"] if title_record is not None else r["label"]
        version,date=parse_label(r["label"])
        d=dict(number=number,environment=e,title=title,document_version=version,label_date=date,document_url=r["url"],document_sha256=r["sha256"],root=root,namespace=ns,namespace_declared=namespace_declared,version_tables=r.get("version_tables",[]),document_history=r.get("document_history",[]),status="review")
        if (snapshot.get('format', 1) >= DOCX_EXTENSION_SNAPSHOT_FORMAT and
                r.get('download_url') and not fname.casefold().endswith('.docx')):
            d['document_official_url'] = r['download_url']
        ambiguous_location = modern and len(members) > 1
        if len(candidates)==1 and not incomplete and not ambiguous_location:
            key, (_, a, s, deps) = next(iter(equivalent.items()))
            try:
                if deps is None:
                    # Keep the original diagnostic for the unresolved member.
                    deps, _ = resolved_dependencies(a["schemas"], s["path"])
                declaration = declarations.get((a['url'], a['sha256'], s['path']), s)
                root_text=next((x["description"] for x in declaration["roots"] if x["name"]==root), '')
                if d["title"]==r["label"] and root_text:
                    d["title"]=re.split(r"\s*Тип сообщения СЭДО",root_text,flags=re.I)[0].rstrip(". ") or r["label"]
                d["module"]="ВНиМ, ССВ" if "ВНиМ" in fname else "Возмещения" if "Возмещения" in fname else None
                d.update(status="matched",namespace=s["namespace"] or None,archive_url=a["url"],archive_sha256=a["sha256"],schema_path=s["path"],dependency_digest=sha(encode(deps)),dependencies=deps,
                         resolved_schema_graph=revision_graph(a['sha256'], s['path'], graph_digest(key[1])))
                if a.get("archive_name"):d["archive_name"]=a["archive_name"]
                if a.get("source_type") in ("wsdl", "wsdl_bridge"):
                    d.update(schema_source_type=a["source_type"], schema_source_url=a["source_url"],
                             schema_sources=a["source_files"])
                location = locations.get((a['url'], a['sha256'], s['path']))
                if declaration['path'] != s['path'] or location:
                    d['schema_declaration_path'] = declaration['path']
                if location:
                    d.update(location)
                if resolutions is not None:
                    resolutions[document_identity(d), d['document_url']] = dict(d, **proofs.get(key, {}))
            except (ValueError,KeyError) as ex:issues.append(str(ex))
        if explicit_wsdl and d['status'] != 'matched':
            if modern and not members:
                reason = 'Утрачена подтверждённая привязка DOCX к XSD: ' + explicit_wsdl['location'] + '.'
            elif ambiguous_location or len(candidates) + len(incomplete) > 1:
                reason = 'Неоднозначные схемы по пути XSD, указанному в DOCX.'
            elif incomplete:
                match = incomplete[0]
                member = match.get('document_path') or match.get('resource_url') or match['declaration']
                if modern and not match.get('document_path'):
                    reason = 'Для ресурса ' + member + ' не удалось однозначно установить файл в архиве WSDL.'
                elif modern and not match['has_root']:
                    qname = ('{' + ns + '}' if ns else '') + (root or '(не указан)')
                    reason = 'В цепочке файла ' + member + ' не найден корневой элемент ' + qname + '.'
                else:
                    reason = 'Для файла ' + member + ' не найдена полная точка входа в подтверждённом WSDL.'
            else:
                reason = 'Не найдена полная XSD по пути, указанному в DOCX.'
            if modern:
                d['schema_docx_location'] = explicit_wsdl['location']
                if len(members) == 1:
                    match, = members
                    d['schema_docx_url'] = match['resource_url']
                    if match.get('document_path'):
                        d['schema_docx_path'] = match['document_path']
            document_sources.append({k: v for k, v in d.items() if k != 'status'} | dict(schema_reason=reason))
            continue
        if d["status"]!="matched":issues.append([e,number,"Требуется проверка сопоставления",len(candidates),
            sorted({"{"+s["namespace"]+"}"+root if s["namespace"] else root+" (без пространства имён)" for _,s in candidates})])
        assign(d)
        if display_registry is not None and title_record is not None and d['title'] == title_record['title']:
            display_registry[d['revision_id'], r['url']] = ('title', title_record)
        catalog.append(d)
    for r in rows:
        named_individual = individual_number(r, legacy=legacy_filenames) if r.get('wsdl_schema_matches') else None
        for match in r.get("module_matches", []):
            if match['number'] == named_individual:
                continue
            version,date=parse_label(r["label"])
            document = (r["environment"], r["url"])
            module_record = next((record for record in registry
                           if (record['environment'], record['number'], record['appendix'])
                           == (r['environment'], match['number'], appendices.get(document))), None)
            module = module_record['module'] if module_record is not None else modules.get(document, r['label'])
            row=dict(match,environment=r["environment"],document_url=r["url"],document_sha256=r["sha256"],
                     document_version=version,label_date=date,
                     module=module,
                     source_type="module",status="matched",version_tables=r.get("version_tables",[]),document_history=r.get("document_history",[]))
            if match.get('archive_sha256') and match.get('schema_path'):
                archive, entry = match['archive_sha256'], match['schema_path']
                graph = revision_graph(archive, entry)
                if graph is None and archive in archives_by_digest:
                    graph = graph_digest(resolved_dependencies(archives_by_digest[archive], entry)[1])
                if graph:
                    row['resolved_schema_graph'] = graph
            assign(row)
            if display_registry is not None and module_record is not None:
                display_registry[row['revision_id'], r['url']] = ('module', module_record)
            catalog.append(row)
    omitted = {row["revision_id"] for row in snapshot.get("collector_observation", {}).get("unmatched_documents", [])}
    if omitted:
        catalog = [row for row in catalog if row["status"] == "matched" or row["revision_id"] not in omitted]
    return dict(revisions=catalog,registry=registry,document_sources=document_sources,review=issues)


def protect_replayed_bindings(snapshot, resolutions, bindings):
    """Record lost bindings before an inferred tree can enter archived history."""
    missing = {}
    for key, doc in individual_documents(snapshot):
        binding = bindings.get(key)
        if binding and not preserves_wsdl_binding(binding, resolutions.get((key, doc['url']), {})):
            location = binding['schema_docx_location']
            missing[key, doc['url']] = f'Утрачена подтверждённая привязка DOCX к XSD: {location}.'
            doc.setdefault('wsdl_schema_matches', dict(version=INDIVIDUAL_WSDL_MATCH_VERSION, members=[],
                                                       location=location))
    return missing


def update_predecessors(events, revisions):
    """Assign each new edition the last surviving edition from an earlier batch."""
    from itertools import groupby

    identities = {row['revision_id']: (row['environment'], row['number']) for row in revisions}
    previous, result = {}, {}
    for _, batch in groupby(events, key=lambda event: event['snapshot']):
        added = {}
        for event in batch:
            rid = event['revision_id']
            identity = identities[rid]
            result[rid] = previous.get(identity)
            added[identity] = rid
        previous.update(added)
    return result


def export(state,target):
    from sync import confirmation_lock, recover_confirmation
    with confirmation_lock(state):
        recover_confirmation(state)
        _export(state, target)


def _export(state,target):
    from collector_snapshots import ModuleReplayOrigins, processed_manifests, replay_context
    ptr=json.loads((state/"current.json").read_bytes())["snapshot"]
    history=json.loads((state/"history.json").read_bytes()) if (state/"history.json").exists() else [ptr]
    observation_file=state/"observations.json"
    observations=json.loads(observation_file.read_bytes())["snapshots"] if observation_file.exists() else {}
    archived_path = state / "archived-reviews.json"
    archived_reviews = json.loads(archived_path.read_bytes()) if archived_path.is_file() else {}
    processed = processed_manifests(state)
    rejected_bindings = processed.get('rejected_bindings', {})
    updates=[];update_sources={};source_observations=[];seen_sources=set()
    schema_source_observations = {}
    versions={};current_ids=set();registry={};current_registry=set();listed_sources={}
    graph_reservations = {}
    rejected_observations = []
    raw_events, raw_revisions = {}, []
    documents = {}; current_documents = set()
    metadata_cache = {}
    legacy_history_cache = {}
    document_display = {}
    observation_presentations = {}
    observation_times = {}
    earlier_revision_observations = {}
    source_times = {}
    earlier_sources = {}
    archive_graph = archived_graph_reader(state)
    _, confirmed, _ = replay_context(state)
    module_origins = ModuleReplayOrigins(confirmed, processed, archive_graph)
    may_reject = rejected_bindings or any('collector_observation' in snapshot for snapshot in confirmed.values())
    resolved_rejections = {}

    def rejections_at(key, rows=None):
        if key not in resolved_rejections:
            if key in rejected_bindings:
                snapshot = confirmed[key]
                if rows is None:
                    rows = derive(snapshot, archive_graph=archive_graph)['revisions']
                resolved_rejections[key] = replayed_binding_rejections(rows, rejected_bindings[key], snapshot=key)
            else:
                resolved_rejections[key] = {}
        return resolved_rejections[key]

    for batch,key in enumerate(dict.fromkeys([*history,ptr])):
        snapshot=confirmed[key]
        observed_at = observations.get(key)
        observed_time = observed_datetime(observed_at)
        # Enrich the in-memory inventory only: snapshot names and bytes are immutable.
        for document in snapshot.get("entries", []):
            digest = document.get("sha256")
            if document.get("kind") != "docx" or not digest:
                continue
            if digest not in metadata_cache:
                path = state / "objects" / digest
                if path.is_file():
                    raw = path.read_bytes()
                    if sha(raw) != digest:
                        raise ValueError("Historical DOCX digest mismatch: " + digest)
                    metadata_cache[digest] = read_document_display(raw)
                    legacy_history_cache[digest] = read_document_display(raw, legacy=True)['document_history']
                else:
                    metadata_cache[digest] = None
                    legacy_history_cache[digest] = []
            if metadata_cache[digest] is not None:
                document_display[digest] = metadata_cache[digest]
            if 'document_history' not in document:
                document["document_history"] = legacy_history_cache[digest]
        display_registry = {}
        d=derive(snapshot, graph_reservations, display_registry=display_registry, archive_graph=archive_graph)
        for row in d['revisions']:
            if row.get('status') == 'matched':
                graph_reservations.setdefault(row['revision_id'], row)
        if may_reject:
            for row in d['revisions']:
                rid = row['revision_id']
                if rid in raw_events or rid in archived_reviews.get(key, {}) or row.get('status') == 'review':
                    continue
                raw_revisions.append(row)
                raw_events[rid] = dict(revision_id=rid, snapshot=key, observed_at=observed_at,
                                       initial_import=batch == 0)
        present = snapshot_display(d['registry'], document_display, display_registry, legacy=snapshot.get('format', 1) < 7)
        historical_modules = {}
        if snapshot.get("format", 1) == 1:
            # Format 1 events used the first registry membership of the number.
            for record in d['registry']:
                historical_modules.setdefault((record['number'], record['environment']), record['module'])
        document_rows = list(d.get('document_sources', []))
        if key != ptr or key in rejected_bindings:
            # Older local updates confirmed an inventory before checking its
            # individual XSD matches. Keep those documents and snapshot bytes
            # as historical sources; only a matched schema is an edition.
            accepted = []
            rejections = rejections_at(key, d['revisions'])
            docs = {(r['environment'], r['url'], r['sha256']): r for r in snapshot.get('entries', ())
                    if r.get('module_matches')}
            for row in d["revisions"]:
                rid = row["revision_id"]
                is_module = row.get('source_type') == 'module' and row.get('status') == 'matched'
                reason = (rejections.get(binding_observation_identity(row))
                          if rejections and row.get('status') == 'matched'
                          and (key != ptr or is_module) else None)
                if is_module and not reason and 'collector_observation' in snapshot:
                    origin = module_origins.origin(key, row)
                    if origin is None:
                        reason = 'Не удалось подтвердить происхождение архивной привязки DOCX к XSD.'
                    elif origin != key:
                        reason = rejections_at(origin).get(binding_observation_identity(row))
                diagnostic = None
                if reason and is_module:
                    doc = docs[row['environment'], row['document_url'], row['document_sha256']]
                    declaration = module_declaration(doc, row)
                    diagnostic = dict(code='module_binding_revoked', snapshot=key,
                        root=row['root'], namespace=schema_qname(row)[0],
                        declared_namespace=declaration.get('namespace'), document_sha256=row['document_sha256'],
                        resolved_schema_graph=row['resolved_schema_graph'])
                if reason:
                    fields = (*SOURCE_KEYS, 'root', 'namespace', 'resolved_schema_graph')
                    rejected_observations.append(present({field: row[field] for field in fields if field in row}
                        | dict(snapshot=key, observed_at=observed_at, schema_reason=reason)))
                    saved_source = {name: row[name] for name in SOURCE_KEYS
                                    if name in row and name not in ('listed_now', 'document_download', 'document_filename')}
                    if is_module and (row['number'], row['environment']) in historical_modules:
                        saved_source['module'] = historical_modules[row['number'], row['environment']]
                    rejected_observations[-1]['source_observation'] = dict(revision_id=rid, snapshot=key,
                        observed_at=observed_at, initial_import=batch == 0, source=saved_source)
                    event = raw_events.get(rid)
                    if event and event['snapshot'] == key:
                        rejected_observations[-1]['update_event'] = event
                if key != ptr:
                    reason = reason or archived_reviews.get(key, {}).get(rid)
                if reason or (key != ptr and row.get("status") == "review"):
                    source = {field: row[field] for field in SOURCE_KEYS if field in row and field != 'revision_id'}
                    source['schema_reason'] = reason or 'На дату наблюдения полная XSD не была сопоставлена.'
                    if diagnostic:
                        source.update(root=row['root'], namespace=diagnostic['namespace'], schema_diagnostic=diagnostic)
                    document_rows.append(source)
                else:
                    accepted.append(row)
            d["revisions"] = accepted
        for row in document_rows:
            address = row['number'], row['environment'], row['document_sha256'], row['document_url']
            diagnostic = row.get('schema_diagnostic')
            if diagnostic:
                diagnostic.setdefault('snapshot', key)
            document_key = (*address, *schema_binding(diagnostic), diagnostic.get('declared_namespace')) if diagnostic else address
            if key == ptr or document_key not in current_documents:
                documents[document_key] = present(row)
            if key == ptr:
                current_documents.add(document_key)
        for row in d["revisions"]:
            identity=(row["number"],row["environment"])
            source = {name: row[name] for name in SOURCE_KEYS
                      if name in row and name not in ("listed_now", "document_download", "document_filename")}
            observed_source = source
            if row.get('source_type') == 'module' and identity in historical_modules:
                observed_source = dict(source, module=historical_modules[identity])
            if row.get('status') == 'matched' and row.get('document_sha256'):
                chain_keys = ('revision_id', 'number', 'environment', 'document_url', 'document_sha256',
                              'document_version', 'label_date', 'manual_source', 'root', 'namespace',
                              'resolved_schema_graph', *SCHEMA_SOURCE_KEYS)
                chain = {name: row[name] for name in chain_keys if name in row}
                signature = sha(encode([chain.get('document_url'), chain.get('document_sha256'),
                                        [chain.get(name) for name in SCHEMA_SOURCE_KEYS]]))
                variants = schema_source_observations.setdefault(row['revision_id'], {})
                if signature not in variants or key == ptr:
                    variants[signature] = dict(chain, snapshot=key)
            source_identity = (row["revision_id"], row.get("document_sha256"), row.get("document_url"))
            if row.get("document_sha256") and source_identity not in seen_sources:
                source_observations.append(dict(revision_id=row["revision_id"], snapshot=key,
                    observed_at=observations.get(key), initial_import=batch==0, source=observed_source))
                # Keep the immutable observation and its snapshot-derived public
                # labels/packaging separate, including after a URL moves or returns.
                displayed = present(observed_source, first_membership=observed_source is not source)
                details = {k: displayed[k] for k in ('title', 'module') if displayed.get(k) != observed_source.get(k)}
                if row.get('status') == 'matched':
                    details.update({k: row[k] for k in SCHEMA_SOURCE_KEYS if k in row})
                observation_presentations.setdefault(row['revision_id'], {})[row.get('document_url') or ''] = details
                seen_sources.add(source_identity)
                source_times[source_identity] = observed_time
            elif row.get('document_sha256') and observed_time is not None:
                first_time = source_times[source_identity]
                if first_time is None or observed_time < first_time:
                    source_times[source_identity] = observed_time
                    displayed = present(observed_source, first_membership=observed_source is not source)
                    displayed.update({name: row[name] for name in SCHEMA_SOURCE_KEYS if name in row})
                    earlier_sources[source_identity] = dict(revision_id=row['revision_id'], snapshot=key,
                        observed_at=observed_at, initial_import=batch == 0, source=displayed)
            rid = row["revision_id"]
            if rid not in versions:
                updates.append(dict(revision_id=row["revision_id"],snapshot=key,
                                    observed_at=observed_at,initial_import=batch==0))
                update_sources[rid] = observed_source
                observation_times[rid] = observed_time
            elif observed_time is not None:
                first_time = observation_times[rid]
                if first_time is None or observed_time < first_time:
                    observation_times[rid] = observed_time
                    earlier_revision_observations[rid] = dict(snapshot=key, observed_at=observed_at,
                                                              initial_import=batch == 0, sources=[])
            earlier = earlier_revision_observations.get(rid)
            if earlier and earlier['snapshot'] == key and row.get('document_sha256'):
                displayed = present(observed_source, first_membership=observed_source is not source)
                displayed.update({name: row[name] for name in SCHEMA_SOURCE_KEYS if name in row})
                earlier['sources'].append(displayed)
            old=versions.get(rid)
            if old:
                def schema_identity(record):
                    return (record.get('root'), record.get('namespace') or None,
                            posixpath.basename(record.get('schema_path') or ''), record.get('dependency_digest'))
                if (schema_identity(old) != schema_identity(row)
                        or old.get('resolved_schema_graph') and row.get('resolved_schema_graph')
                        and old['resolved_schema_graph'] != row['resolved_schema_graph']):
                    raise ValueError("Конфликт идентичности схемы для редакции " + row['revision_id'] +
                                     ": " + repr(schema_identity(old)) + " / " + repr(schema_identity(row)))
            # A previously observed snapshot can become current again. Keep its
            # source URL and label even when later history contains the same bytes.
            may_replace = key == ptr or row["revision_id"] not in current_ids
            if may_replace and (not old or row.get("source_type")!="manual" or old.get("source_type")=="manual"):
                if old and old.get("manual_source") and not row.get("manual_source"):
                    row=dict(row,manual_source=old["manual_source"])
                versions[row["revision_id"]]=present(row)
            if key==ptr:
                current_ids.add(row["revision_id"])
                listed = present(source)
                listed.update({k: row[k] for k in SCHEMA_SOURCE_KEYS if k in row})
                listed_sources.setdefault(row["revision_id"], []).append(listed)
        for row in d["registry"]:
            identity=sha(encode(row))
            if key == ptr or identity not in current_registry:
                registry[identity]=present(row, registry_row=True)
            if key==ptr:current_registry.add(identity)
        if key==ptr:latest=d
    latest["revisions"]=[dict(r,listed_now=i in current_ids) |
                         (dict(listed_sources=listed_sources[i]) if len(listed_sources.get(i, [])) > 1 else {})
                         for i,r in versions.items()]
    latest["registry"]=[dict(row,listed_now=identity in current_registry) for identity,row in registry.items()]
    current_matched = {(row['number'], row['environment'], source['document_sha256'], source['document_url'])
                       for rid, sources in listed_sources.items() for source in sources
                       for row in [versions[rid]] if row.get('status') == 'matched'
                       and source.get('document_sha256') and source.get('document_url')}
    matched_documents = {(row['number'], row['environment'], source['document_sha256'], source['document_url'])
                         for rid, row in versions.items() if row.get('status') == 'matched'
                         for source in [row, *listed_sources.get(rid, []),
                                        *(item['source'] for item in source_observations if item['revision_id'] == rid)]
                         if source.get('document_sha256') and source.get('document_url')}
    latest['document_sources'] = [dict(row, listed_now=identity in current_documents)
                                  for identity, row in documents.items()
                                  if row.get('schema_diagnostic') or (identity not in current_matched
                                  and (identity in current_documents or identity not in matched_documents))]
    if rejected_observations:
        for rid, before in update_predecessors(raw_events.values(), raw_revisions).items():
            raw_events[rid]['previous_revision_id'] = before
        latest['rejected_binding_observations'] = rejected_observations
        revoked = {row['revision_id']: row for row in reversed(rejected_observations)
                   if row['revision_id'] not in versions}
        latest['revoked_revisions'] = [{key: value for key, value in row.items() if key not in ('update_event', 'source_observation')}
                                       for row in reversed(revoked.values())]
    predecessors = update_predecessors(updates, latest['revisions'])
    for event in updates:
        event['previous_revision_id'] = predecessors[event['revision_id']]
    if rejected_observations:
        original_updates = {event['revision_id']: raw_events[event['revision_id']] for event in updates
                            if event['revision_id'] in raw_events and raw_events[event['revision_id']] != event}
        if original_updates:
            latest['original_update_events'] = original_updates
    latest["updates"]=updates
    # Keep the legacy events unchanged; provenance is fixed at first observation
    # and reconstructed from archived snapshots, rather than the mutable row.
    latest["update_sources"]=update_sources
    latest["source_observations"]=source_observations
    latest['schema_source_observations'] = {rid: list(variants.values())
                                            for rid, variants in schema_source_observations.items()
                                            if rid in versions and len(variants) > 1}
    if earlier_revision_observations:
        latest['earlier_revision_observations'] = earlier_revision_observations
    if earlier_sources:
        latest['earlier_source_observations'] = list(earlier_sources.values())
    latest['observation_presentations'] = observation_presentations
    latest['document_display'] = {digest: {k: display[k] for k in ('document_history', 'text_replacements')}
                                  for digest, display in document_display.items()}
    from sync import confirmed_time
    checked_at = confirmed_time(state, ptr)
    if checked_at:
        latest["checked_at"] = checked_at
    write(target,encode(latest))
    print(len(versions),"revisions;",len(latest["registry"]),"registry rows;",len(latest["review"]),"review items")

if __name__=="__main__":
    import argparse
    from auto_update import catalog_locks
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state",type=Path,default=Path("state"));p.add_argument("--output",type=Path,default=Path("catalog.json"))
    a=p.parse_args()
    with catalog_locks(a.state, a.output.parent):
        export(a.state,a.output)

