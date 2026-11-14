"""Resolve review locations against the actual field tree of a revision."""
import re


def _local_path(path):
    path = re.sub(r"\[[^]]*\]", "", path).strip("/")
    return "/".join(re.sub(r"^(@?)[\w.-]+:", r"\1", part) for part in path.split("/"))


def field_targets(path, fields):
    """Return all valid locations, or their nearest known containing blocks."""
    if not path:
        return [], ""
    path = _local_path(path)
    if fields and path.startswith("*/"):
        path = fields[0]["path"] + path[1:]
    known = {field["path"] for field in fields}
    requested = path
    while path:
        candidates = [path] if path in known else []
        head, _, tail = path.partition("/")
        for field in fields:
            if field.get("type", "").rsplit(":", 1)[-1] != head:
                continue
            candidate = field["path"] + ("/" + tail if tail else "")
            if candidate in known:
                candidates.append(candidate)
        if candidates:
            return list(dict.fromkeys(candidates)), "field" if path == requested else "parent"
        path = path.rpartition("/")[0]
    return [], ""


def attach_targets(finding, fields):
    path = finding.get("field_path")
    if path is None:
        if finding["kind"] == "Путь к XSD":
            path = fields[0]["path"] if fields else ""
        elif finding["kind"] == "XML-пример":
            location = finding["location"].rsplit(" — ", 1)[-1]
            path = location if location.startswith("/") else finding["xlocation"].rsplit(" → ", 1)[-1]
        else:
            path = re.split(r" — | · ", finding["xlocation"])[-1]
    targets, scope = field_targets(path, fields)
    return dict(finding, field_paths=targets, field_link_scope=scope)
