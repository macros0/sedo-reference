"""Preserve the publication page and exact XSD bytes for every built edition."""
import json
import zipfile
from urllib.parse import urlsplit
from sync import SITES, sha, write


def publish_sources(catalog, state, site):
    publications = {}
    for snapshot in sorted((state / "snapshots").glob("*.json")):
        data = json.loads(snapshot.read_bytes())
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
            publications[(env, url, entry["sha256"])] = page
    for row in catalog["revisions"]:
        row.pop("schema_publication_page", None)
        row.pop("schema_file_download", None)
        row.pop("schema_file_name", None)
        if row.get("status") != "matched":
            continue
        origin = row.get("archive_url", "").split("#")[0]
        embedded = "#embedded=" in row.get("archive_url", "") or row.get("schema_source_type") in ("wsdl", "wsdl_bridge")
        resource = row.get("document_url") if embedded else origin
        digest = row.get("document_sha256") if embedded else row.get("archive_sha256")
        page = publications.get((row["environment"], resource, digest))
        if page:
            row["schema_publication_page"] = page
        source = state / "objects" / row["archive_sha256"]
        if sha(source.read_bytes()) != row["archive_sha256"]:
            raise ValueError("Не совпадает контрольная сумма исходного комплекта XSD")
        with zipfile.ZipFile(source) as bundle:
            content = bundle.read(row["schema_path"])
        filename = row["schema_path"].rsplit("/", 1)[-1]
        relative = "sources/" + sha(content) + ".xsd"
        output = site / relative
        if not output.is_file() or output.read_bytes() != content:
            write(output, content)
        row["schema_file_download"] = relative
        row["schema_file_name"] = filename
