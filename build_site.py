"""Build the local/browser static reference from the compiled SFR catalog."""
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit
from sync import inspect_file, sha, write
from edition_groups import group_editions, display_updates
HERE=Path(__file__).resolve().parent
def packed(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":")).replace("<","\\u003c")
def archive_documents(catalog, state, site):
    """Publish exact original bytes, once per document hash; never fetch from SFR."""
    checked = set()
    documents = [*catalog["revisions"], *(r for r in catalog.get("registry", []) if r.get("document_sha256"))]
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

def main():
    catalog=json.loads((HERE/"data/catalog.json").read_bytes())
    transitions=HERE/"data/message-transitions.json"
    if transitions.exists():catalog["message_transitions"]=json.loads(transitions.read_bytes())
    statuses={r["revision_id"]:r for r in json.loads((HERE/"data/fields/index.json").read_bytes())}
    site=HERE/"site";editions=site/"editions";editions.mkdir(parents=True,exist_ok=True)
    comparison=site/"fields";comparison.mkdir(exist_ok=True)
    archive_documents(catalog, HERE/"state", site)
    template=(HERE/"viewer-template.html").read_text("utf-8-sig")
    template=template.replace("<title>СЭДО · Справочник 10110</title>","<title>Справочник XML-полей</title>")
    # The containing catalog carries the selected message and revision.
    template=template.replace("</style>","header,h1,.sub{display:none}.page{padding-top:0}.layout{margin-top:0}body{background:#f3f6f5}</style>")
    template=template.replace("function select(i,show=false){current=i;","function select(i,show=false){current=i;location.hash=new URLSearchParams({field:fields[i].path}).toString();")
    restore="""
function restoreField(){
 const path=new URLSearchParams(location.hash.slice(1)).get('field');
 const idx=fields.findIndex(f=>f.path===path);
 if(idx>=0 && idx!==current){current=idx;for(let p=fields[idx].parent;p>=0;p=fields[p].parent)expanded.add(p);render()}
}
window.addEventListener('hashchange',()=>{restoreField();if(parent!==window)parent.postMessage({type:'sedo-field',revision:data.revision,path:fields[current].path},'*')});
restoreField();
"""
    template=template.replace("</script></html>",restore+"</script></html>")
    field_cache={}
    for r in catalog["revisions"]:
        rid=r["revision_id"];ready=statuses.get(rid,{}).get("status")=="ready";r["ready"]=ready
        if not ready:continue
        fields=json.loads((HERE/"data/fields"/(rid+".json")).read_bytes())["fields"]
        field_cache[rid]=fields
        (comparison/(rid+".js")).write_text("window.SEDO_FIELDS=window.SEDO_FIELDS||{};window.SEDO_FIELDS["+packed(rid)+"]="+packed(fields)+";",encoding="utf8")
        data=dict(fields=fields,revision=rid,version=r["document_version"]or"Версия не указана",date=r["label_date"],archive=r.get("archive_name") or unquote(urlsplit(r["archive_url"]).path.split("/")[-1]),files=len(r["dependencies"]),sha256=r["archive_sha256"])
        (editions/(rid+".html")).write_text(template.replace("__CATALOG_JSON__",packed(data)),encoding="utf8")
    catalog=group_editions(catalog,field_cache.__getitem__)
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
    (site/"catalog-data.js").write_text("window.SEDO_CATALOG="+packed(catalog)+";",encoding="utf8")
    assets=("app.js","app.css","compare-core.js","compare.js","comparison-export.js","mapping-draft.js","mapping-editor.js","comparison-mapping.js","vendor/exceljs.min.js","vendor/exceljs-LICENSE","vendor/README.md","journal.js","message-transitions.js","favicon.svg")
    for file in assets:
        (site/file).parent.mkdir(parents=True,exist_ok=True)
        (site/file).write_bytes((HERE/"web"/file).read_bytes())
    # Content versions prevent cached old routing code from breaking new navigation.
    html=(HERE/"web/index.html").read_text("utf8")
    for file in (*assets,"catalog-data.js"):
        version=sha((site/file).read_bytes())[:16]
        html=html.replace('"'+file+'"','"'+file+'?v='+version+'"')
    (site/"index.html").write_text(html,encoding="utf8")
    print(f"Built {len(catalog['revisions'])} revisions in {site}")
if __name__=="__main__":main()

