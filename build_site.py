"""Build the local/browser static reference from the compiled SFR catalog."""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from sync import inspect_file, sha, write
from document_metadata import apply_display_text
from schema_review import compare_revision
from review_field_links import review_sources
from source_provenance import publish_sources
from edition_groups import group_editions, display_updates, source_updates, prepare_observed_sources
from xsd_bundle import publish_bundles
HERE=Path(__file__).resolve().parent
SITE_ASSETS=("review-journal.js","app.js","app.css","automation-status.js","compare-core.js","compare.js","comparison-export.js","mapping-draft.js","mapping-editor.js","comparison-mapping.js","vendor/exceljs.min.js","vendor/exceljs-LICENSE","vendor/README.md","journal.js","message-transitions.js","favicon.svg","validation.js","validation-core.js","validation-frame.js","validation-worker.js","validation-paths.js","validation-source.js","validation-messages.js","vendor/xmllint/xmllint-browser.mjs","vendor/xmllint/xmllint.wasm","vendor/xmllint/COPYING","vendor/xmllint/README.md")
def packed(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":")).replace("<","\\u003c")


def share_document_histories(catalog):
    """Keep document change tables once in the browser's inlined data."""
    prepare_observed_sources(catalog)
    histories = catalog.pop('document_histories', {})
    displays = catalog.pop('document_display', {})
    seen = set()

    def visit(value):
        if isinstance(value, (dict, list)):
            if id(value) in seen:
                return
            seen.add(id(value))
        if isinstance(value, dict):
            digest = value.get('document_sha256')
            display = displays.get(digest, {})
            applied = value.pop('display_metadata_applied', False)
            if not applied:
                apply_display_text(value, display)
            if digest and ('document_history' in value or 'document_history' in display):
                history = value.pop('document_history', [])
                history = display.get('document_history', history)
                if digest in histories and histories[digest] != history:
                    raise ValueError('Разная история для одного оригинала DOCX ' + digest)
                histories[digest] = history
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(catalog)
    catalog['document_histories'] = histories

def archive_documents(catalog, state, site):
    """Publish exact original bytes, once per document hash; never fetch from SFR."""
    checked = set()
    documents = [*catalog["revisions"], *catalog.get("document_sources", []),
                 *(r for r in catalog.get("registry", []) if r.get("document_sha256"))]
    for r in documents:
        digest = r["document_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Некорректная контрольная сумма DOCX")
        relative = "documents/" + digest + ".docx"
        if digest not in checked:
            source = state / "objects" / digest
            if not source.is_file():
                raise ValueError("Отсутствует оригинал DOCX " + digest)
            content = source.read_bytes()
            if sha(content) != digest:
                raise ValueError("Не совпадает контрольная сумма DOCX " + digest)
            try:
                inspect_file(content, "docx")
            except Exception as exc:
                raise ValueError("Повреждён DOCX " + digest) from exc
            target=site/relative
            if not target.is_file() or target.read_bytes()!=content:
                write(target, content)
            checked.add(digest)
        version = r.get("document_version") or "unversioned"
        filename = f"sedo-{r['number']}-{r['environment']}-v{version}-{digest[:8]}.docx"
        r["document_download"] = relative
        r["document_filename"] = re.sub(r"[^A-Za-z0-9._-]", "_", filename)

def validate_field_index(catalog, rows):
    statuses = {row["revision_id"]: row for row in rows}
    revisions = catalog["revisions"]
    if len(statuses) != len(rows) or len({r["revision_id"] for r in revisions}) != len(revisions) or set(statuses) != {r["revision_id"] for r in revisions}:
        raise ValueError("Индекс полей не соответствует редакциям каталога")
    for row in revisions:
        if row["status"] != "matched" and not (row["status"] == "document_only" and row.get("source_type") == "manual"):
            raise ValueError("Некорректный статус редакции " + row["revision_id"])
        expected = "document_only" if row["status"] == "document_only" else "ready"
        if statuses[row["revision_id"]]["status"] != expected:
            raise ValueError("Редакция не готова к публикации " + row["revision_id"])
    return statuses


def validate_output(output):
    target, project = Path(output).resolve(), HERE.resolve()
    protected = [project/name for name in ("data", "state", "web", "tests", "docs", "incoming", ".git", ".venv", "certs", "deploy", ".github")]
    if project.is_relative_to(target) or any(target.is_relative_to(path.resolve()) or path.resolve().is_relative_to(target) for path in protected):
        raise ValueError("Каталог сайта пересекается с исходными данными проекта")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        try:
            raw = (target/"catalog-data.js").read_text("utf8")
            catalog = json.loads(raw.removeprefix("window.SEDO_CATALOG=").rstrip(";\r\n"))
            recognized = isinstance(catalog["revisions"], list) and (target/"index.html").is_file() and (target/".nojekyll").is_file()
        except (OSError, ValueError, KeyError, TypeError):
            recognized = False
        if not recognized:
            raise ValueError("Каталог назначения содержит посторонние файлы")
    return target


def main(output=None):
    catalog=json.loads((HERE/"data/catalog.json").read_bytes())
    transitions=HERE/"data/message-transitions.json"
    if transitions.exists():catalog["message_transitions"]=json.loads(transitions.read_bytes())
    statuses=validate_field_index(catalog, json.loads((HERE/"data/fields/index.json").read_bytes()))
    site=Path(output) if output is not None else HERE/"site";editions=site/"editions";editions.mkdir(parents=True,exist_ok=True)
    comparison=site/"fields";comparison.mkdir(exist_ok=True)
    archive_documents(catalog, HERE/"state", site)
    publish_sources(catalog, HERE/"state", site)
    field_cache={}
    for rid, status in statuses.items():
        if status["status"] != "ready":
            continue
        saved = json.loads((HERE/"data/fields"/(rid+".json")).read_bytes())
        if saved.get("revision_id") != rid:
            raise ValueError("Дерево полей относится к другой редакции " + rid)
        field_cache[rid] = saved["fields"]
    publish_bundles(catalog, HERE/"state", site, navigation={rid:[f["path"] for f in fields] for rid,fields in field_cache.items()})
    template=(HERE/"viewer-template.html").read_text("utf-8-sig")
    template=template.replace("<title>СЭДО · Справочник 10110</title>","<title>Справочник XML-полей</title>")
    # The containing catalog carries the selected message and revision.
    template=template.replace("</style>","header,h1,.sub{display:none}.page{padding-top:0}.layout{margin-top:0}body{background:#f3f6f5}</style>")
    template=template.replace("function select(i,show=false){current=i;","function select(i,show=false,writeHash=true){current=i;if(writeHash)location.hash=new URLSearchParams({field:fields[i].path}).toString();")
    restore="""
function restoreField(){
 const path=new URLSearchParams(location.hash.slice(1)).get('field');
 const idx=path===null?0:fields.findIndex(f=>f.path===path);
 if(idx>=0&&idx!==current)select(idx,true,false)
}
window.addEventListener('message',event=>{
 if(event.source!==parent||event.origin!==location.origin||event.data?.type!=='sedo-select-field'||event.data.revision!==data.revision||typeof event.data.path!=='string')return;
 const idx=fields.findIndex(f=>f.path===event.data.path);if(idx>=0)select(idx,true);
});
window.addEventListener('hashchange',()=>{restoreField();if(parent!==window)parent.postMessage({type:'sedo-field',revision:data.revision,path:location.hash?fields[current].path:null},'*')});
restoreField();
"""
    template=template.replace("</script></html>",restore+"</script></html>")
    for r in catalog["revisions"]:
        rid=r["revision_id"];r["ready"]=statuses.get(rid,{}).get("status")=="ready"
        if r["ready"]:
            r["schema_review"]=compare_revision(r,field_cache[rid],HERE/"state"/"objects")
    catalog=group_editions(catalog,field_cache.__getitem__)
    for r in catalog["revisions"]:
        rid=r["revision_id"]
        if not r["ready"]:continue
        fields=field_cache[rid]
        (comparison/(rid+".js")).write_text("window.SEDO_FIELDS=window.SEDO_FIELDS||{};window.SEDO_FIELDS["+packed(rid)+"]="+packed(fields)+";",encoding="utf8",newline="\n")
        r["fields_sha256"]=sha((comparison/(rid+".js")).read_bytes())
        r["review_sources"]=review_sources(catalog,r)
        data=dict(fields=fields,review_sources=r["review_sources"],revision=rid,version=r["document_version"]or"Версия не указана",date=r["label_date"],archive=r.get("archive_name") or unquote(urlsplit(r["archive_url"]).path.split("/")[-1]),files=len(r["dependencies"]),sha256=r["archive_sha256"])
        page=editions/(rid+".html")
        html=template.replace("__CATALOG_JSON__",packed(data)).encode("utf8")
        if not page.is_file() or page.read_bytes()!=html:
            write(page,html)
        r["viewer_sha256"]=sha(page.read_bytes())
    if catalog.get("updates"):
        try:
            result=subprocess.run(["node",str(Path(__file__).with_name("summarize_updates.cjs")),str(HERE/"data/fields")],
                                  input=packed(catalog),capture_output=True,text=True,encoding="utf8",check=True,timeout=120)
        except (OSError,subprocess.SubprocessError) as exc:
            detail=getattr(exc,"stderr",None) or str(exc)
            raise ValueError("Не удалось посчитать изменения журнала (нужен Node.js 18+): "+detail[:1500]) from exc
        catalog["updates"]=json.loads(result.stdout)
        # Keep complete source events for importer reports and old workbook IDs;
        # the browser journal consumes only the grouped schema events.
        catalog["schema_updates"]=display_updates(catalog)
        catalog["source_updates"]=source_updates(catalog)
    share_document_histories(catalog)
    (site/"catalog-data.js").write_text("window.SEDO_CATALOG="+packed(catalog)+";",encoding="utf8",newline="\n")
    assets=SITE_ASSETS
    for file in assets:
        (site/file).parent.mkdir(parents=True,exist_ok=True)
        (site/file).write_bytes((HERE/"web"/file).read_bytes())
    validator_assets=[dict(key=key,path=name,sha=sha((site/name).read_bytes())) for key,name in (
        ('engine','vendor/xmllint/xmllint-browser.mjs'),('wasm','vendor/xmllint/xmllint.wasm'),
        ('source','validation-source.js'),('messages','validation-messages.js'),('core','validation-core.js'),('paths','validation-paths.js'),('worker','validation-worker.js'),('frame','validation-frame.js'))]
    loader=(site/'validation.js').read_text('utf8').replace('__VALIDATOR_ASSETS__',packed(validator_assets))
    (site/'validation.js').write_text(loader,encoding='utf8',newline='\n')
    # Content versions prevent cached old routing code from breaking new navigation.
    html=(HERE/"web/index.html").read_text("utf8")
    for file in (*assets,"catalog-data.js"):
        version=sha((site/file).read_bytes())[:16]
        html=html.replace('"'+file+'"','"'+file+'?v='+version+'"')
    (site/"index.html").write_text(html,encoding="utf8",newline="\n")
    (site/".nojekyll").touch()
    print(f"Built {len(catalog['revisions'])} revisions in {site}")
def cleanup_site_recovery(recovery):
    marker = recovery / 'cleanup.json'
    if marker.exists():
        saved = json.loads(marker.read_bytes())
    else:
        saved = {p.relative_to(recovery).as_posix(): sha(p.read_bytes())
                 for p in recovery.rglob('*') if p.is_file()}
        write(marker, packed(saved).encode())
    for path in recovery.rglob('*'):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('Ссылка внутри каталога восстановления сайта')
        if path == marker or path.is_dir():
            continue
        relative = path.relative_to(recovery).as_posix()
        if path.is_symlink() or relative not in saved or sha(path.read_bytes()) != saved[relative]:
            raise ValueError("Изменились файлы восстанавливаемого выпуска")
    for path in sorted(recovery.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if path == marker:
            continue
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
    marker.unlink()
    recovery.rmdir()


def recover_site(root, name='site'):
    """Finish a directory rollback before disposing of its transaction evidence."""
    recovery = root / ('.' + name + '-recovery')
    if recovery.is_symlink() or getattr(recovery, 'is_junction', lambda: False)():
        raise ValueError('Каталог восстановления сайта является ссылкой')
    if not recovery.exists():
        return
    if (recovery / 'cleanup.json').is_file():
        cleanup_site_recovery(recovery)
        return
    journal = recovery / 'journal.json'
    committed = recovery / 'committed'
    allowed = {'journal.json', 'journal.partial', 'cleanup.partial', 'committed', 'candidate', 'previous', 'discard'}
    for entry in recovery.iterdir():
        if entry.name not in allowed or entry.is_symlink() or getattr(entry, 'is_junction', lambda: False)():
            raise ValueError("Неизвестное содержимое каталога восстановления сайта")
        if entry.name in ('candidate', 'previous', 'discard'):
            if not entry.is_dir():
                raise ValueError("Некорректный каталог восстановления сайта")
            validate_output(entry)
        elif not entry.is_file():
            raise ValueError("Некорректный маркер восстановления сайта")
    if committed.exists() and committed.stat().st_size:
        raise ValueError("Некорректный маркер завершения сборки")
    if journal.exists():
        saved = json.loads(journal.read_bytes())
        if not isinstance(saved, dict) or set(saved) != {'existed'} or type(saved['existed']) is not bool:
            raise ValueError("Некорректный журнал восстановления сайта")
    elif any(recovery.iterdir()) and not committed.exists() and not (recovery/'candidate').is_dir():
        raise ValueError("Неизвестное содержимое каталога восстановления сайта")
    target, previous = root / name, recovery / 'previous'
    if journal.exists() and not (recovery / 'committed').exists():
        existed = json.loads(journal.read_bytes())['existed']
        if previous.exists():
            if target.exists():
                validate_output(target)
                target.replace(recovery / 'discard')
            previous.replace(target)
        elif not existed and target.exists():
            validate_output(target)
            target.replace(recovery / 'discard')
    cleanup_site_recovery(recovery)


def preserve_publication_files(target, candidate):
    """Check release-owned paths before replacement and carry deployment configuration."""
    if not target.exists() or target.is_dir() and not any(target.iterdir()):
        return
    try:
        catalog = json.loads((target / "catalog-data.js").read_text("utf8")
                             .removeprefix("window.SEDO_CATALOG=").rstrip(";\r\n"))
        revisions = {row["revision_id"] for row in catalog["revisions"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Не распознан предыдущий выпуск сайта " + str(target)) from exc
    assets = {*SITE_ASSETS, "index.html", "catalog-data.js", ".nojekyll", "CNAME"}
    for path in target.rglob("*"):
        relative = path.relative_to(target).as_posix()
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Ссылка внутри выпуска сайта " + relative)
        if path.is_dir():
            if relative not in {"editions", "fields", "schemas", "documents", "sources"} and not any(
                    name.startswith(relative + "/") for name in assets):
                raise ValueError("Посторонний каталог в выпуске сайта " + relative)
            continue
        generated = re.fullmatch(
            r"(?:editions/[0-9a-f]{64}\.html|fields/[0-9a-f]{64}\.js|"
            r"schemas/[0-9a-f]{64}\.(?:zip|validation\.json\.gz)|"
            r"documents/[0-9a-f]{64}\.docx|sources/[0-9a-f]{64}\.xsd)", relative)
        known_revision = any(relative == folder + "/" + rid + suffix
                             for rid in revisions for folder, suffix in (("editions", ".html"), ("fields", ".js")))
        if relative not in assets and not generated and not known_revision:
            raise ValueError("Посторонний файл в выпуске сайта " + relative)
    cname = target / "CNAME"
    if cname.is_file():
        write(candidate / "CNAME", cname.read_bytes())


def promote_site(candidate, target):
    preserve_publication_files(target, candidate)
    recovery = target.parent / ('.' + target.name + '-recovery')
    recovery.mkdir()
    try:
        candidate.replace(recovery / 'candidate')
        write(recovery / 'journal.json', packed(dict(existed=target.exists())).encode())
        if target.exists():
            target.replace(recovery / 'previous')
        (recovery / 'candidate').replace(target)
        (recovery / 'committed').touch()
    except Exception:
        recover_site(target.parent, target.name)
        raise
    recover_site(target.parent, target.name)


def cli(output=None):
    from auto_update import catalog_locks
    target = validate_output(output if output is not None else HERE / 'site')
    # The candidate and directory renames stay on the destination's volume.
    with catalog_locks(HERE, target.parent):
        recover_site(target.parent, target.name)
        validate_output(target)
        with tempfile.TemporaryDirectory(prefix='.site-build-', dir=target.parent) as tmp:
            candidate = Path(tmp) / 'site'
            main(output=candidate)
            promote_site(candidate, target)


if __name__=="__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE / 'site')
    cli(parser.parse_args().output)

