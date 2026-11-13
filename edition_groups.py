"""Group exact schema editions for display without rewriting source history."""
import copy
import json

from sync import sha

SOURCE_KEYS = ("revision_id", "document_version", "label_date", "document_url", "document_sha256",
               "document_download", "document_filename", "source_type", "manual_source", "module", "listed_now")


def _tree(value):
    if isinstance(value, dict):
        return {k: _tree(v) if k == "alternatives" else v
                for k, v in value.items() if k not in ("file", "typeFile")}
    if isinstance(value, list):
        return [_tree(v) for v in value]
    return value


def group_editions(catalog, load_fields):
    result = copy.deepcopy(catalog)
    groups = {}
    for row in result["revisions"]:
        rid = row["revision_id"]
        key = ("unmatched", rid)
        if row.get("ready") and row.get("dependencies"):
            identity = [row["environment"], row["number"], row.get("root"), row.get("namespace"),
                        sorted({digest for path, digest in row["dependencies"]}), _tree(load_fields(rid))]
            key = sha(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8"))
        groups.setdefault(key, []).append(row)
    for members in groups.values():
        # Source history is chronological and append-only. Keep the first ID so
        # adding another document cannot invalidate existing bookmarks/mappings.
        representative = members[0]["revision_id"]
        sources = {}
        for row in members:
            identity = (row.get("document_sha256"), row.get("document_url"))
            source = {k: copy.deepcopy(row[k]) for k in SOURCE_KEYS if k in row}
            source.setdefault("listed_now", True)
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


def _source_history(catalog):
    revisions = {r["revision_id"]: r for r in catalog["revisions"]}
    seen = set()
    for event in catalog.get("updates", []):
        row = revisions[event["revision_id"]]
        rid = row.get("display_revision_id", row["revision_id"])
        if not row.get("document_sha256"):
            continue
        identity = (row["document_sha256"], row.get("document_url"))
        if (rid, identity) in seen:
            continue
        seen.add((rid, identity))
        source = next((s for s in row.get("edition_sources", [])
                       if (s.get("document_sha256"), s.get("document_url")) == identity),
                      {k: row[k] for k in SOURCE_KEYS if k in row})
        yield event, rid, copy.deepcopy(source)


def display_updates(catalog):
    canonical = {r["revision_id"]: r.get("display_revision_id", r["revision_id"]) for r in catalog["revisions"]}
    events = {event["revision_id"]: event for event in catalog.get("updates", [])}
    first_batches, initial_sources = {}, {}
    for event, rid, source in _source_history(catalog):
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
        previous = event.get("previous_revision_id")
        visited = set()
        while previous and canonical[previous] == rid:
            if previous in visited:
                raise ValueError("Cycle in source edition history")
            visited.add(previous)
            previous = events[previous].get("previous_revision_id")
        displayed = dict(event, revision_id=rid, previous_revision_id=canonical[previous] if previous else None)
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

    Original update order is the append-only snapshot order. Document dates are
    deliberately not used to infer when a source was first found.
    """
    first_batch, result = {}, []
    for event, rid, source in _source_history(catalog):
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
