"""Preview and confirm owner-supplied DOCX/XSD without changing official inventories."""
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
from auto_update import validate_history
from catalog import derive, export
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


class ImportPreview:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.original_fingerprint = fingerprint(self.root)
        self._temp = tempfile.TemporaryDirectory(prefix="sedo-manual-")
        self.directory = Path(self._temp.name)
        self.changed = False
        self.revision = None

    def close(self):
        self._temp.cleanup()

    def save(self):
        if fingerprint(self.root) != self.original_fingerprint:
            raise ValueError("Проект изменился. Повторите предпросмотр перед сохранением.")
        if not self.changed:
            return
        for folder in ("objects", "snapshots"):
            for source in (self.directory / "state" / folder).iterdir():
                target = self.root / "state" / folder / source.name
                if not target.exists():
                    write(target, source.read_bytes())
        shutil.copytree(self.directory / "data/fields", self.root / "data/fields", dirs_exist_ok=True)
        shutil.copytree(self.directory / "site", self.root / "site", dirs_exist_ok=True)
        for name in ("history.json", "observations.json"):
            write(self.root / "state" / name, (self.directory / "state" / name).read_bytes())
        write(self.root / "data/catalog.json", (self.directory / "data/catalog.json").read_bytes())
        self.original_fingerprint = fingerprint(self.root)


def prepare_import(root, document, filename, metadata, archive=None, archive_filename="schemas.zip"):
    root = Path(root).resolve()
    number = str(metadata.get("number", "")).strip()
    env = metadata.get("environment")
    if not re.fullmatch(r"\d{1,8}", number) or env not in ("prod", "test"):
        raise ValueError("Укажите номер сообщения и контур")
    filename = re.split(r"[\\/]", filename)[-1]
    if not filename.lower().endswith(".docx"):
        raise ValueError("Выберите исходный DOCX")
    version = str(metadata.get("version", "")).strip().lstrip("vвVВ")
    if not version:
        match = re.search(r"[vв](\d+(?:[._]\d+)+)", filename, re.I)
        version = match[1].replace("_", ".") if match else ""
    if version and not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise ValueError("Номер версии должен содержать цифры и точки")
    date = str(metadata.get("date", "")).strip()
    if date:
        try:
            date = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError as exc:
            raise ValueError("Укажите корректную дату документа") from exc
    observed_at = datetime.now(timezone.utc).isoformat()
    doc_meta = inspect_upload(document, "docx")
    doc_hash = sha(document)
    label = f"Тип сообщения {number}" + (" v" + version if version else "") + (" (" + date + ")" if date else "")
    entry = dict(environment=env, url="manual:///" + quote(f"Тип_сообщения_{number}.docx"), label=label, kind="docx", sha256=doc_hash, **doc_meta)
    entries = [entry]
    if archive is not None:
        if not str(archive_filename).lower().endswith(".zip"):
            raise ValueError("Схемы нужно загрузить в ZIP со всеми импортируемыми XSD")
        archive_meta = inspect_upload(archive, "zip")
        if not archive_meta.get("schemas"):
            raise ValueError("В ZIP нет XSD-схем")
        entries.append(dict(environment=env, url="manual:///" + quote(re.split(r"[\\/]", archive_filename)[-1]), label="Ручной архив XSD", kind="zip", sha256=sha(archive), **archive_meta))
    result = derive(dict(entries=entries))
    revision = result["revisions"][0]
    if archive is not None and revision["status"] != "matched":
        raise ValueError("Не удалось однозначно сопоставить DOCX и XSD по корню и пространству имён")
    if archive is None:
        revision["status"] = "document_only"
    revision.update(document_url=None, document_version=version or None, label_date=date or None, source_type="manual",
                    manual_source=dict(label=str(metadata.get("source") or "Telegram")[:1000], filename=filename, imported_at=observed_at))
    previous = json.loads((root / "data/catalog.json").read_bytes())
    title = str(metadata.get("title") or "").strip()
    if not title:
        title = next((r["title"] for r in previous["revisions"] if r["number"] == number), "Сообщение " + number)
    revision["title"] = title[:1000]
    duplicate = next((r for r in previous["revisions"] if r["revision_id"] == revision["revision_id"] or
                      (archive is None and r["number"] == number and r["environment"] == env and r["document_sha256"] == doc_hash)), None)
    preview = ImportPreview(root)
    try:
        for folder in ("state", "data", "web"):
            shutil.copytree(root / folder, preview.directory / folder)
        shutil.copytree(root / "data/fields", preview.directory / "previous-fields")
        shutil.copy2(root / "viewer-template.html", preview.directory / "viewer-template.html")
        preview.revision = duplicate or revision
        preview.changed = duplicate is None
        if preview.changed:
            state = preview.directory / "state"
            write(state / "objects" / doc_hash, document)
            if archive is not None:
                write(state / "objects" / sha(archive), archive)
            snapshot = dict(format=1, source_type="manual", revision=revision)
            payload = encode(snapshot)
            digest = sha(payload)
            write(state / "snapshots" / (digest + ".json"), payload)
            history = json.loads((state / "history.json").read_bytes())
            history.append(digest)
            write(state / "history.json", encode(history))
            record_observation(state, digest, observed_at)
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
