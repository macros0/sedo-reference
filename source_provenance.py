"""Preserve the publication page and exact XSD bytes for every built edition."""
import json
import posixpath
import zipfile
from document_metadata import root_metadata, schema_location
from edition_groups import prepare_observed_sources, SCHEMA_SOURCE_KEYS
from schema import Schema
from sync import SITES, catalog_resource, sha, write


def original_schema_source(sources, path, digest):
    matches = [source for source in sources if source.get('kind') == 'xsd'
               and source.get('sha256') == digest]
    exact = [source for source in matches if source.get('archive_path') == path]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1 and not matches[0].get('archive_path'):
        return matches[0]


def restore_legacy_wsdl_location(row, document, archive, files):
    """Read the old named member and recompute the owner of the confirmed root."""
    evidence = document.get('wsdl_schema_matches')
    if not evidence or (isinstance(evidence, dict) and evidence.get('version', 1) >= 3):
        return
    location = schema_location(document.get('structure', []))
    if not location:
        return
    matches = [match for match in (evidence['members'] if isinstance(evidence, dict) else evidence)
               if match['archive_url'] == row['archive_url'] and match['archive_sha256'] == row['archive_sha256']
               and row['schema_path'] in match['entrypoints'] and match['declaration'] in files]
    if len(matches) != 1:
        return
    member = matches[0]['declaration']
    root, namespace, declared = root_metadata(document.get('structure', []))
    try:
        with zipfile.ZipFile(archive) as bundle:
            tree = Schema(bundle, row['schema_path'], root)
    except (ValueError, KeyError):
        return
    declaration = tree.owner[tree.root]
    if declaration in files and (not declared or (tree.context[tree.root][2] or None) == namespace):
        row.update(schema_docx_location=location, schema_docx_path=member, schema_declaration_path=declaration)


def publish_sources(catalog, state, site):
    publications, snapshots, documents = {}, {}, {}
    history_file, current_file = state / "history.json", state / "current.json"
    history = json.loads(history_file.read_bytes()) if history_file.exists() else []
    current = json.loads(current_file.read_bytes())["snapshot"] if current_file.exists() else None
    # The current pointer can return to a previously seen snapshot. Apply it last
    # instead of inferring chronology from a content hash or first observation.
    for key in [*dict.fromkeys(h for h in history if h != current), *([current] if current else [])]:
        data = json.loads((state / "snapshots" / (key + ".json")).read_bytes())
        snapshot_publications = {}
        for entry in data.get("entries", []):
            env = entry.get("environment")
            url = entry.get("url", "").split("#")[0]
            # Only recorded official publications establish a page relationship.
            if env not in SITES or not entry.get("sha256"):
                continue
            if entry.get('wsdl_schema_matches'):
                documents[key, env, entry['url'], entry['sha256']] = entry
            if not catalog_resource(url, env):
                continue
            page = entry.get("source_page") or SITES[env]
            if not catalog_resource(page, env):
                continue
            snapshot_publications[(env, url, entry["sha256"])] = page
        snapshots[key] = snapshot_publications
        publications.update(snapshot_publications)
    first_snapshots = {event["revision_id"]: event.get("snapshot") for event in catalog.get("updates", [])}
    prepare_observed_sources(catalog)
    packages = {}
    prior_editions = {}
    revisions = {row.get("revision_id"): row for row in catalog["revisions"]}
    from catalog import archived_graph_reader, derive
    archive_graph = archived_graph_reader(state)

    def publish(row, snapshot, *, revision=False, observation=False):
        row.pop("schema_publication_page", None)
        row.pop("schema_file_download", None)
        row.pop("schema_file_name", None)
        row.pop("schema_files", None)
        row.pop("schema_original_url", None)
        row.pop("schema_declaration_download", None)
        row.pop("schema_declaration_url", None)
        row.pop("schema_docx_download", None)
        if not row.get("schema_path") and not revision and snapshot in snapshots and revisions.get(row.get("revision_id"), {}).get("status") == "matched":
            # Old exports lack per-observation packaging. Recover it from the
            # original inventory, including when an external ZIP moved alone.
            if snapshot not in prior_editions:
                saved = json.loads((state / "snapshots" / (snapshot + ".json")).read_bytes())
                prior_editions[snapshot] = {(r["revision_id"], r.get("document_sha256"), r.get("document_url")): r
                                            for r in derive(saved, revisions, archive_graph=archive_graph)["revisions"]
                                            if r.get("status") == "matched"}
            match = prior_editions[snapshot].get((row.get("revision_id"),row.get("document_sha256"),row.get("document_url")), {})
            row.update({k: match[k] for k in SCHEMA_SOURCE_KEYS if k in match})
        if not row.get("schema_path") or not row.get("archive_sha256"):
            return
        origin = row.get("archive_url", "").split("#")[0]
        embedded = "#embedded=" in row.get("archive_url", "") or row.get("schema_source_type") in ("wsdl", "wsdl_bridge")
        resource = (row.get("document_url") or "").split("#")[0] if embedded else origin
        digest = row.get("document_sha256") if embedded else row.get("archive_sha256")
        identity = (row["environment"], resource, digest)
        # Archived cards cite the publication recorded with their first event.
        # Current cards prefer the current publication; fallback is the last
        # confirmed occurrence of these exact bytes and URL.
        page = snapshots.get(snapshot, {}).get(identity)
        if not page and revision:
            page = publications.get(identity)
        if page:
            row["schema_publication_page"] = page
        entry = row["schema_path"]
        paths = {entry}
        paths.update(posixpath.normpath(posixpath.join(posixpath.dirname(entry), name))
                     for name, _ in row.get("dependencies", []))
        package = row["archive_sha256"], tuple(sorted(paths))
        if package not in packages:
            archive = state / "objects" / row["archive_sha256"]
            if sha(archive.read_bytes()) != row["archive_sha256"]:
                raise ValueError("Не совпадает контрольная сумма исходного комплекта XSD")
            files = {}
            with zipfile.ZipFile(archive) as bundle:
                for path in sorted(paths):
                    raw = bundle.read(path)
                    digest = sha(raw)
                    relative = "sources/" + digest + ".xsd"
                    output = site / relative
                    if not output.is_file() or output.read_bytes() != raw:
                        write(output, raw)
                    files[path] = dict(path=path, download=relative, sha256=digest)
            packages[package] = files
        files = packages[package]
        if revision:
            row["schema_files"] = list(files.values())
        if observation:
            sources = row.get('schema_sources', [])
            row['schema_files'] = []
            for record in files.values():
                original = original_schema_source(sources, record['path'], record['sha256'])
                row['schema_files'].append(dict(record, official_url=original['url']) if original else record)
        row["schema_file_download"] = files[entry]["download"]
        row["schema_file_name"] = posixpath.basename(entry)
        if row.get("schema_source_type") == "wsdl":
            sources = row.get('schema_sources', [])
            original = original_schema_source(sources, entry, files[entry]['sha256'])
            if original:
                row['schema_original_url'] = original['url']
            original_document = documents.get((snapshot, row['environment'], row.get('document_url'),
                                               row.get('document_sha256')))
            if original_document and not row.get('schema_docx_location'):
                restore_legacy_wsdl_location(row, original_document, state / 'objects' / row['archive_sha256'], files)
            named = row.get('schema_docx_path')
            if named in files:
                row['schema_docx_download'] = files[named]['download']
                original = original_schema_source(sources, named, files[named]['sha256'])
                if original:
                    row.setdefault('schema_docx_url', original['url'])
            declaration = row.get('schema_declaration_path')
            if declaration:
                row['schema_declaration_download'] = files[declaration]['download']
                original = original_schema_source(sources, declaration, files[declaration]['sha256'])
                if original:
                    row['schema_declaration_url'] = original['url']
        if not revision:
            row.pop("dependencies", None)
            row.pop("schema_sources", None)

    schema_observations = catalog.pop('schema_source_observations', {})
    for row in catalog["revisions"]:
        if row.get("status") != "matched":
            continue
        snapshot = current if row.get("listed_now", True) else first_snapshots.get(row.get("revision_id"))
        publish(row, snapshot, revision=True)
        variants = schema_observations.get(row.get('revision_id'), [])
        if variants:
            row['schema_source_observations'] = []
        for source in sorted(variants, key=lambda source: source['snapshot'] != snapshot):
            if source.get('document_sha256') != row.get('document_sha256') or (
                    source.get('resolved_schema_graph'), source.get('root'), source.get('namespace') or ''
                    ) != (row.get('resolved_schema_graph'), row.get('root'), row.get('namespace') or ''):
                continue
            publish(source, source['snapshot'], observation=True)
            if not source.get('schema_files'):
                continue
            if row.get('document_download'):
                source['document_download'] = row['document_download']
                source['document_filename'] = row['document_filename']
            row['schema_source_observations'].append(source)
        for source in row.get("listed_sources", []):
            publish(source, current)
    for event in [*catalog.get("source_observations", []), *catalog.get('earlier_source_observations', [])]:
        if "source" in event:
            publish(event["source"], event.get("snapshot"))
    for observation in catalog.get('earlier_revision_observations', {}).values():
        for source in observation['sources']:
            publish(source, observation['snapshot'])
    for rid, source in catalog.get("update_sources", {}).items():
        publish(source, first_snapshots.get(rid))
