"""Preserve the publication page and exact XSD bytes for every built edition."""
import json
import posixpath
import zipfile
from urllib.parse import urlsplit
from sync import SITES, sha, write


def publish_sources(catalog, state, site):
    publications, snapshots = {}, {}
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
            if urlsplit(url).hostname != urlsplit(SITES[env]).hostname:
                continue
            page = entry.get("source_page") or SITES[env]
            if urlsplit(page).hostname != urlsplit(SITES[env]).hostname:
                continue
            snapshot_publications[(env, url, entry["sha256"])] = page
        snapshots[key] = snapshot_publications
        publications.update(snapshot_publications)
    first_snapshots = {event["revision_id"]: event.get("snapshot") for event in catalog.get("updates", [])}
    for row in catalog["revisions"]:
        row.pop("schema_publication_page", None)
        row.pop("schema_file_download", None)
        row.pop("schema_file_name", None)
        row.pop("schema_files", None)
        if row.get("status") != "matched":
            continue
        origin = row.get("archive_url", "").split("#")[0]
        embedded = "#embedded=" in row.get("archive_url", "") or row.get("schema_source_type") in ("wsdl", "wsdl_bridge")
        resource = row.get("document_url") if embedded else origin
        digest = row.get("document_sha256") if embedded else row.get("archive_sha256")
        identity = (row["environment"], resource, digest)
        # Archived cards cite the publication recorded with their first event.
        # Current cards prefer the current publication; fallback is the last
        # confirmed occurrence of these exact bytes and URL.
        event_sources = snapshots.get(first_snapshots.get(row.get("revision_id")), {})
        page = (event_sources.get(identity) if not row.get("listed_now", True) else None) or publications.get(identity)
        if page:
            row["schema_publication_page"] = page
        source = state / "objects" / row["archive_sha256"]
        if sha(source.read_bytes()) != row["archive_sha256"]:
            raise ValueError("Не совпадает контрольная сумма исходного комплекта XSD")
        with zipfile.ZipFile(source) as bundle:
            content = bundle.read(row["schema_path"])
            paths = {row["schema_path"]}
            paths.update(posixpath.normpath(posixpath.join(posixpath.dirname(row["schema_path"]),name))
                         for name,digest in row.get("dependencies",[]))
            files=[]
            for path in sorted(paths):
                raw=bundle.read(path)
                relative="sources/"+sha(raw)+".xsd"
                output=site/relative
                if not output.is_file() or output.read_bytes()!=raw:
                    write(output,raw)
                files.append(dict(path=path,download=relative,sha256=sha(raw)))
            row["schema_files"]=files
        filename = row["schema_path"].rsplit("/", 1)[-1]
        relative = "sources/" + sha(content) + ".xsd"
        output = site / relative
        if not output.is_file() or output.read_bytes() != content:
            write(output, content)
        row["schema_file_download"] = relative
        row["schema_file_name"] = filename
