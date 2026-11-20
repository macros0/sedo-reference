"""Group exact schema editions for display without rewriting source history."""
import copy
import json
import posixpath
import re
from observation_time import observed_datetime

from sync import sha

SOURCE_KEYS = ("revision_id", "number", "environment", "title", "document_version", "label_date", "document_url", "document_sha256",
               "document_download", "document_filename", "source_type", "manual_source", "module", "listed_now", "document_history", "display_metadata_applied")
SCHEMA_SOURCE_KEYS = ("archive_url", "archive_sha256", "archive_name", "schema_path", "schema_declaration_path", "schema_source_type",
                      "schema_source_url", "schema_sources", "dependencies",
                      "schema_docx_location", "schema_docx_path", "schema_docx_url")
PUBLIC_SOURCE_KEYS = SOURCE_KEYS + tuple(k for k in SCHEMA_SOURCE_KEYS if k not in ("dependencies", "schema_sources")) + (
    "schema_publication_page", "schema_file_download", "schema_file_name", "schema_original_url",
    "schema_declaration_download", "schema_declaration_url", "schema_docx_download")


def prepare_observed_sources(catalog):
    """Apply snapshot evidence only to the public copy of each original observation."""
    details = catalog.pop("observation_presentations", {})
    for source in [*(e["source"] for e in catalog.get("source_observations", []) if "source" in e),
                   *catalog.get("update_sources", {}).values()]:
        item = details.get(source.get("revision_id"), {}).get(source.get("document_url") or "")
        if item is not None:
            source.update(item, display_metadata_applied=True)


def _tree(value):
    if isinstance(value, dict):
        return {k: _tree(v) if k == "alternatives" else v
                for k, v in value.items() if k not in ("file", "typeFile")}
    if isinstance(value, list):
        return [_tree(v) for v in value]
    return value


def _source_record(observed, current_rows):
    source = {key: copy.deepcopy(observed[key]) for key in PUBLIC_SOURCE_KEYS if key in observed}
    identity = (source.get("document_sha256"), source.get("document_url"))
    source["listed_now"] = any((item.get("document_sha256"), item.get("document_url")) == identity
                               for row in current_rows
                               for item in row.get("listed_sources", [row] if row.get("listed_now", True) else []))
    downloadable = next((row for row in current_rows if row.get("document_sha256") == identity[0]
                         and row.get("document_download")), None)
    if downloadable:
        source["document_download"] = downloadable["document_download"]
        filename = f"sedo-{source.get('number', downloadable['number'])}-{source.get('environment', downloadable['environment'])}-v{source.get('document_version') or 'unversioned'}-{source['document_sha256'][:8]}.docx"
        source["document_filename"] = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    return source


def group_editions(catalog, load_fields):
    result = copy.deepcopy(catalog)
    prepare_observed_sources(result)
    groups = {}
    for row in result["revisions"]:
        rid = row["revision_id"]
        key = ("unmatched", rid)
        if row.get("ready") and row.get("dependencies"):
            from schema import equivalent_closure
            fields = load_fields(rid)
            # Retain each byte digest at its resolved position in the graph.
            # A common outer folder and the entry's filename are packaging only.
            prefix = posixpath.commonpath([posixpath.dirname(path) or '.' for path, _ in row['dependencies']])
            dependencies = [(posixpath.relpath(path, prefix or '.'), digest) for path, digest in row['dependencies']]
            entry = row.get('schema_path') or next((f.get('file') for f in fields if f.get('parent') == -1), '') or ''
            graph = row.get('resolved_schema_graph')
            authority = ('resolved', graph) if graph else ('legacy', equivalent_closure(dependencies, entry))
            identity = [row["environment"], row["number"], row.get("root"), row.get("namespace"),
                        authority, _tree(fields)]
            key = sha(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8"))
        groups.setdefault(key, []).append(row)
    observed_sources = {}
    for observation in result.get("source_observations", []):
        observed_sources.setdefault(observation["revision_id"], []).append(observation["source"])
    for members in groups.values():
        # Source history is chronological and append-only. Keep the first ID so
        # adding another document cannot invalidate existing bookmarks/mappings.
        representative = members[0]["revision_id"]
        sources = {}
        observed = [source for row in members for source in observed_sources.get(row["revision_id"], [])]
        current_sources = {(row.get("document_sha256"), row.get("document_url")): row
                           for row in members if row.get("listed_now", True) or row.get("source_type") == "manual"}
        for row in members:
            for source in row.get("listed_sources", []):
                # Older catalogs store only a hash and URL here. Their display
                # metadata still comes from the recorded observation.
                if "revision_id" in source:
                    current_sources[(source.get("document_sha256"), source.get("document_url"))] = source
        for row in [*observed, *members]:
            identity = (row.get("document_sha256"), row.get("document_url"))
            # Labels on the card describe the current publication. Event labels
            # remain in source_observations and are read separately below.
            source = _source_record(current_sources.get(identity, row), members)
            if identity in sources:
                sources[identity]["listed_now"] |= source["listed_now"]
            else:
                sources[identity] = source
        for row in members:
            row["display_revision_id"] = representative
            row["edition_sources"] = list(sources.values())
            row["listed_now"] = any(source["listed_now"] for source in sources.values())
    return result


def visible_editions(catalog):
    return [r for r in catalog["revisions"] if r.get("display_revision_id", r["revision_id"]) == r["revision_id"]]


def _event_batch(event):
    return (("snapshot", event["snapshot"]) if event.get("snapshot") else
            ("observation", event["observed_at"]) if event.get("observed_at") else ("unknown",))


def _journal_anchors(catalog, canonical):
    return {canonical[event["revision_id"]]: event | event.get("chronological_observation", {})
            for event in catalog.get("updates", [])
            if event.get("chronological_first")}


def _source_history(catalog, canonical, initial_batches):
    revisions = {r["revision_id"]: r for r in catalog["revisions"]}
    observations = catalog.get("source_observations", catalog.get("updates", []))
    if catalog.get('earlier_source_observations'):
        observations = [*observations, *catalog['earlier_source_observations']]
    if initial_batches:
        recovered = [dict(revision_id=rid, snapshot=earlier['snapshot'], observed_at=earlier['observed_at'], source=source)
                     for rid, earlier in catalog.get('earlier_revision_observations', {}).items()
                     for source in earlier['sources']]
        # Deduplicate source appearances after visiting the verified initial batch.
        observations = sorted([*recovered, *observations],
                              key=lambda e: _event_batch(e) != initial_batches.get(canonical[e["revision_id"]]))
    first = {}
    def rank(event, rid):
        stamp = observed_datetime(event.get('observed_at'))
        return (stamp is None, stamp, _event_batch(event) != initial_batches.get(rid),
                event.get('snapshot') or '', event['revision_id'])

    for event in observations:
        rid = canonical[event['revision_id']]
        row = revisions[event["revision_id"]]
        saved = event.get("source", catalog.get("update_sources", {}).get(event["revision_id"]))
        observed = saved if saved is not None else row
        if not observed.get("document_sha256"):
            continue
        key = (rid, (observed['document_sha256'], observed.get('document_url')))
        order = rank(event, rid)
        if key not in first or order < first[key][0]:
            first[key] = (order, event, row, saved, observed)

    for (rid, identity), (_, event, row, saved, observed) in first.items():
        current = next((s for s in row.get("edition_sources", [row])
                        if (s.get("document_sha256"), s.get("document_url")) == identity), None)
        source = {k: copy.deepcopy(observed[k]) for k in PUBLIC_SOURCE_KEYS if k in observed}
        if saved is not None:
            # Download bytes are addressed by digest; labels stay those seen at
            # this event, even when the current row uses a later URL/version.
            source = _source_record(source, row.get("edition_sources", [row]))
        elif current:
            source = copy.deepcopy(current)
        yield event, rid, copy.deepcopy(source)


def display_updates(catalog):
    canonical = {r["revision_id"]: r.get("display_revision_id", r["revision_id"]) for r in catalog["revisions"]}
    events = {event["revision_id"]: event for event in catalog.get("updates", [])}
    anchors = _journal_anchors(catalog, canonical)
    first_batches = {rid: _event_batch(event) for rid, event in anchors.items()}
    initial_sources = {}
    for event, rid, source in _source_history(catalog, canonical, first_batches):
        batch = _event_batch(event)
        first_batches.setdefault(rid, batch)
        if first_batches[rid] == batch:
            initial_sources.setdefault(rid, []).append(source)
    result, seen = [], set()
    for event in catalog.get("updates", []):
        rid = canonical[event["revision_id"]]
        if rid in seen:
            continue
        seen.add(rid)
        event = anchors.get(rid, event)
        if "chronological_previous_revision_id" in event:
            previous = event["chronological_previous_revision_id"]
        else:
            previous = event.get("previous_revision_id")
            visited = set()
            while previous and canonical[previous] == rid:
                if previous in visited:
                    raise ValueError("Cycle in source edition history")
                visited.add(previous)
                previous = events[previous].get("previous_revision_id")
        displayed = dict(event, revision_id=rid, previous_revision_id=canonical[previous] if previous else None)
        displayed.pop("chronological_first", None)
        displayed.pop("chronological_previous_revision_id", None)
        displayed.pop("chronological_observation", None)
        if rid in initial_sources:
            displayed["event_sources"] = initial_sources[rid]
        if event.get("comparison"):
            comparison = event["comparison"]
            displayed["comparison"] = dict(comparison, left=canonical.get(comparison.get("left")),
                                            right=canonical.get(comparison.get("right")))
        result.append(displayed)
    return result


def source_updates(catalog):
    """First appearances of additional documents for an already known schema.

    Initial batches come from confirmed observations when the site has a journal projection.
    """
    canonical = {r["revision_id"]: r.get("display_revision_id", r["revision_id"]) for r in catalog["revisions"]}
    first_batch = {rid: _event_batch(event) for rid, event in _journal_anchors(catalog, canonical).items()}
    result = []
    for event, rid, source in _source_history(catalog, canonical, first_batch):
        batch = _event_batch(event)
        first_batch.setdefault(rid, batch)
        if batch == first_batch[rid]:
            continue
        stable_key = ["source_added", rid, source["document_sha256"], source.get("document_url")]
        event_id = sha(json.dumps(stable_key, ensure_ascii=False, separators=(",", ":")).encode("utf8"))
        result.append(dict(event_type="source_added", event_id=event_id, revision_id=rid,
                           source_revision_id=event["revision_id"], source=source,
                           snapshot=event.get("snapshot"), observed_at=event.get("observed_at"),
                           initial_import=False))
    return result
