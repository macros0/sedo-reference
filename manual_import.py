"""Stage DOCX/XSD revisions for transactional incoming-folder imports."""
import io
import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import build_site
from auto_update import promote as promote_import, update_lock, validate_history, build_history_baseline
from catalog import derive, export, graph_digest, known_catalog_revisions
from document_metadata import filename_metadata, root_metadata, root_matches
from schema import resolved_dependencies
from fields import build
from sync import MAX_BYTES, encode, inspect_file, record_observation, sha, write


def fingerprint(root):
    files=[]
    for folder in ("data","site","state","web"):
        for path in sorted((root/folder).rglob("*")):
            if path.is_file():files.append((path.relative_to(root).as_posix(),sha(path.read_bytes())))
    template=root/"viewer-template.html"
    if template.exists():files.append((template.name,sha(template.read_bytes())))
    return sha(encode(files))


def inspect_upload(content, kind):
    if not content or len(content) > MAX_BYTES:
        raise ValueError("Пустой или слишком большой файл")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > 4000 or sum(x.file_size for x in infos) > 256 * 1024 * 1024:
                raise ValueError("Слишком большой распакованный архив")
        return inspect_file(content, kind)
    except Exception as exc:
        raise ValueError("Не удалось прочитать " + kind.upper() + ": " + str(exc)) from exc


def import_structure(info, number, schemas=None):
    """Select a declared module binding without changing the import provenance."""
    bindings = info.get('module_bindings', [])
    if not bindings:
        return info.get('structure', []), False
    matches = [row for row in bindings if row['number'] == str(number)]
    if len(matches) > 1 and schemas is not None:
        owners = set()
        for binding in matches:
            choices = {(schema['namespace'] or '', binding['root'], graph_digest(
                resolved_dependencies(members, schema['path'])[1]))
                for members in schemas for schema in members
                if root_matches(schema, binding['root'], binding.get('namespace') or None, binding.get('namespace') is not None)}
            if len(choices) != 1:
                raise ValueError('Неоднозначное сопоставление деклараций модульного DOCX и XSD')
            owners.update(choices)
        if len(owners) == 1:
            namespace, root, _ = owners.pop()
            matches = [dict(root=root, namespace=namespace)]
    if len(matches) != 1:
        raise ValueError('Номер сообщения отсутствует или неоднозначен в модульном DOCX')
    selected = matches[0]
    structure = [['Корневой элемент', selected['root']]]
    if selected.get('namespace') is not None:
        structure.append(['Пространство имен файла XML-схемы', selected['namespace']])
    return structure, True


class ImportPreview:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.original_fingerprint = fingerprint(self.root)
        self._temp = tempfile.TemporaryDirectory(prefix="sedo-manual-")
        self.directory = Path(self._temp.name)
        self.changed = False
        self.new_revision = False
        self.revision = None

    def close(self):
        self._temp.cleanup()

    def save(self):
        with update_lock(self.root):
            if fingerprint(self.root) != self.original_fingerprint:
                raise ValueError("Проект изменился. Повторите предпросмотр перед сохранением.")
            if self.changed:
                promote_import(self.root, self.directory)
                self.original_fingerprint = fingerprint(self.root)


def prepare_import(root, document, filename, metadata, archive=None, archive_filename="schemas.zip", *, correction_fields=None):
    with update_lock(root) as locked_root:
        return _prepare_import(locked_root, document, filename, metadata, archive, archive_filename,
                               set(metadata) if correction_fields is None else set(correction_fields))


def _prepare_import(root, document, filename, metadata, archive, archive_filename, correction_fields):
    previous = json.loads((root / "data/catalog.json").read_bytes())
    preview = ImportPreview(root)
    try:
        for folder in ("state", "data", "web"):
            shutil.copytree(root / folder, preview.directory / folder)
        build_history_baseline(preview.directory)
        shutil.copy2(root / "viewer-template.html", preview.directory / "viewer-template.html")
        result = stage_import(preview.directory, document, filename, metadata, archive, archive_filename,
                              correction_fields=correction_fields)
        preview.revision = result["revision"]
        preview.new_revision = result["new_revision"]
        preview.changed = result["changed"]
        if not preview.changed:
            export(preview.directory / "state", preview.directory / "data/catalog.json")
        build(preview.directory / "data/catalog.json", preview.directory / "state", preview.directory / "data/fields")
        current = json.loads((preview.directory / "data/catalog.json").read_bytes())
        with patch.object(build_site, "HERE", preview.directory):
            build_site.main()
        validate_history(previous, current, preview.directory)
        return preview
    except Exception:
        preview.close()
        raise


def stage_import(root, document, filename, metadata, archive=None, archive_filename="schemas.zip", *, correction_fields=None,
                 preferred_revision_id=None, reset_fields=(), previous_revision_id=None):
    """Add one document to an isolated candidate; the caller builds and promotes the batch."""
    correction_fields = set(metadata) if correction_fields is None else set(correction_fields)
    number = str(metadata.get("number", "")).strip()
    env = metadata.get("environment")
    if not re.fullmatch(r"\d{1,8}", number) or env not in ("prod", "test"):
        raise ValueError("Укажите номер сообщения и контур")
    filename = re.split(r"[\\/]", filename)[-1]
    if not filename.lower().endswith(".docx"):
        raise ValueError("Выберите исходный DOCX")
    inferred = filename_metadata(filename)
    version = str(metadata.get("version", "")).strip().lstrip("vвVВ") or inferred["version"]
    if version and not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise ValueError("Номер версии должен содержать цифры и точки")
    date = str(metadata.get("date", inferred["date"])).strip()
    if date:
        try:
            date = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError as exc:
            raise ValueError("Укажите корректную дату документа") from exc
    observed_at = datetime.now(timezone.utc).isoformat()
    doc_meta = inspect_upload(document, "docx")
    archive_meta = inspect_upload(archive, "zip") if archive is not None else None
    structure, module_document = import_structure(doc_meta, number, [archive_meta.get("schemas", [])] if archive_meta else None)
    if module_document:
        doc_meta = dict(doc_meta, structure=structure)
    doc_hash = sha(document)
    label = f"Тип сообщения {number}" + (" v" + version if version else "") + (" (" + date + ")" if date else "")
    entry = dict(environment=env, url="manual:///" + quote(f"Тип_сообщения_{number}.docx"), label=label, kind="docx", sha256=doc_hash, **doc_meta)
    entries = [entry]
    if archive is not None:
        if not str(archive_filename).lower().endswith(".zip"):
            raise ValueError("Схемы нужно загрузить в ZIP со всеми импортируемыми XSD")
        if not archive_meta.get("schemas"):
            raise ValueError("В ZIP нет XSD-схем")
        entries.append(dict(environment=env, url="manual:///" + quote(re.split(r"[\\/]", archive_filename)[-1]), label="Ручной архив XSD", kind="zip", sha256=sha(archive), **archive_meta))
    previous = json.loads((root / "data/catalog.json").read_bytes())
    known_revisions = known_catalog_revisions([*previous['revisions'], *previous.get('revoked_revisions', [])], root / 'state')
    result = derive(dict(entries=entries), known_revisions, preferred_revision_id=preferred_revision_id)
    revision = result["revisions"][0]
    if archive is not None and revision["status"] != "matched":
        raise ValueError("Не удалось однозначно сопоставить DOCX и XSD по корню и пространству имён")
    if archive is None:
        revision["status"] = "document_only"
    revision.update(document_url=None, document_version=version or None, label_date=date or None, source_type="manual",
                    manual_source=dict(label=str(metadata.get("source") or "Telegram")[:1000], filename=filename, imported_at=observed_at))
    if module_document:
        revision['document_type'] = 'module'
    title = str(metadata.get("title") or "").strip()
    if not title:
        # Manual rows can contain the very override being removed. Only a
        # confirmed source from this contour may replace the computed default.
        candidates = [r for r in previous["revisions"] if r["number"] == number and r["environment"] == env
                      and r.get("source_type") != "manual"]
        current = [r for r in candidates if r.get("listed_now", True)]
        title = next((r["title"] for r in reversed(current or candidates)), "Сообщение " + number)
    revision["title"] = title[:1000]
    if archive is None:
        candidates = [r for r in previous["revisions"] if r["number"] == number and r["environment"] == env
                      and r["document_sha256"] == doc_hash]
        duplicate = next((r for r in candidates if r["revision_id"] == preferred_revision_id), None)
        if duplicate is None:
            if len(candidates) > 1:
                raise ValueError("Для этого DOCX есть несколько редакций. Добавьте соответствующий ZIP.")
            duplicate = next(iter(candidates), None)
    else:
        duplicate = next((r for r in previous["revisions"] if r["revision_id"] == revision["revision_id"]), None)
    if duplicate is not None:
        if duplicate['revision_id'] == previous_revision_id:
            correction_fields.update(reset_fields)
        corrected = dict(duplicate)
        if duplicate.get("source_type") == "manual":
            for key, field in (("version", "document_version"), ("date", "label_date"), ("title", "title")):
                if key in correction_fields:
                    corrected[field] = revision[field]
            if "source" in correction_fields:
                corrected["manual_source"] = dict(duplicate["manual_source"], label=revision["manual_source"]["label"])
        revision = corrected
    changed = duplicate is None or revision != duplicate
    if changed:
        state = root / "state"
        write(state / "objects" / doc_hash, document)
        if archive is not None:
            write(state / "objects" / sha(archive), archive)
        snapshot = dict(format=1, source_type="manual", revision={k: v for k, v in revision.items() if k != 'display_metadata_applied'})
        if duplicate is not None:
            # Corrections are observations of the same edition, including a return to an earlier label.
            snapshot["revision"] = {k: v for k, v in revision.items() if k not in ("listed_now", "display_metadata_applied")}
            snapshot["observed_at"] = observed_at
        payload = encode(snapshot)
        digest = sha(payload)
        write(state / "snapshots" / (digest + ".json"), payload)
        history = json.loads((state / "history.json").read_bytes())
        history.append(digest)
        write(state / "history.json", encode(history))
        record_observation(state, digest, observed_at)
        export(state, root / "data/catalog.json")
    return dict(revision=revision, new_revision=duplicate is None, changed=changed)
