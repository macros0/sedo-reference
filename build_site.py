"""Build the local/browser static reference from the compiled SFR catalog."""
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from sync import inspect_file, sha, write
HERE=Path(__file__).resolve().parent
def packed(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":")).replace("<","\\u003c")
def archive_documents(catalog, state, site):
    """Publish exact original bytes, once per document hash; never fetch from SFR."""
    checked = set()
    for r in catalog["revisions"]:
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
            write(site / relative, content)
            checked.add(digest)
        version = r.get("document_version") or "unversioned"
        filename = f"sedo-{r['number']}-{r['environment']}-v{version}-{digest[:8]}.docx"
        r["document_download"] = relative
        r["document_filename"] = re.sub(r"[^A-Za-z0-9._-]", "_", filename)

def main():
    catalog=json.loads((HERE/"data/catalog.json").read_bytes())
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
    for r in catalog["revisions"]:
        rid=r["revision_id"];ready=statuses.get(rid,{}).get("status")=="ready";r["ready"]=ready
        if not ready:continue
        fields=json.loads((HERE/"data/fields"/(rid+".json")).read_bytes())["fields"]
        (comparison/(rid+".js")).write_text("window.SEDO_FIELDS=window.SEDO_FIELDS||{};window.SEDO_FIELDS["+packed(rid)+"]="+packed(fields)+";",encoding="utf8")
        data=dict(fields=fields,revision=rid,version=r["document_version"]or"Версия не указана",date=r["label_date"],archive=unquote(urlsplit(r["archive_url"]).path.split("/")[-1]),files=len(r["dependencies"]),sha256=r["archive_sha256"])
        (editions/(rid+".html")).write_text(template.replace("__CATALOG_JSON__",packed(data)),encoding="utf8")
    (site/"catalog-data.js").write_text("window.SEDO_CATALOG="+packed(catalog)+";",encoding="utf8")
    for file in ("index.html","app.js","app.css","compare-core.js","compare.js","journal.js"):
        (site/file).write_bytes((HERE/"web"/file).read_bytes())
    print(f"Built {len(catalog['revisions'])} revisions in {site}")
if __name__=="__main__":main()

