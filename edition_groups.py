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


def display_updates(catalog):
    canonical = {r["revision_id"]: r.get("display_revision_id", r["revision_id"]) for r in catalog["revisions"]}
    events = {event["revision_id"]: event for event in catalog.get("updates", [])}
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
        result.append(dict(event, revision_id=rid, previous_revision_id=canonical[previous] if previous else None))
    return result
