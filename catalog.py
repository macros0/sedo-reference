"""Materialize a catalog from successful inventories. Does not contact SFR."""
import json
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from sync import encode,sha,write

from schema import dependency_set, equivalent_closure
from edition_groups import SOURCE_KEYS, SCHEMA_SOURCE_KEYS
from document_metadata import parse_label, root_metadata, root_matches, read_document_history, read_document_display, prefer_schema_location
from document_metadata import MODULE_FILENAME, snapshot_display
from wsdl_sources import prefer_wsdl_inline_entries

def document_filename(row, *, legacy=False):
    return unquote(row['url'].split('/')[-1] if legacy else urlsplit(row['url']).path.rsplit('/',1)[-1])


def individual_number(row, *, legacy=False):
    if row["kind"] == "docx" and row.get("sha256"):
        filename = document_filename(row,legacy=legacy)
        match = re.search(r"тип[ _]+сообщения[ _]+(\d+)", filename, re.I)
        return match[1] if match else None

def derive(snapshot, known_revisions=(), *, preferred_revision_id=None, display_registry=None):
    if snapshot.get("source_type")=="manual":
        return dict(revisions=[dict(snapshot["revision"])],registry=[],review=[])
    rows=snapshot["entries"];catalog=[]; registry=[]; issues=[]
    legacy_filenames = snapshot.get('format',7) < 7
    document_sources = []
    for r in rows:
        for table in r.get("registry_tables",[]):
            if table[0][:2]!=["Код типа сообщения","Наименование"]:continue
            group=""
            version, date = parse_label(r.get("label", ""))
            for cells in table[1:]:
                if len(cells)==1:group=cells[0]
                elif len(cells)>=4 and cells[0].isdigit():
                    registry.append(dict(environment=r["environment"],number=cells[0],title=cells[1],description=cells[2],appendix=cells[3],module=group,source=r["url"],
                                         document_url=r["url"], document_sha256=r.get("sha256"),
                                         document_version=version, label_date=date))
    # Module declarations supplement, rather than overwrite, the general list.
    known = {(r["environment"], r["number"]) for r in registry}
    official_modules = {}
    for row in registry:
        official_modules.setdefault((row['environment'], row['appendix']), set()).add(row['module'])
    modules = {}; appendices = {}
    for r in rows:
        if r["kind"] != "docx" or not r.get("sha256"): continue
        filename = document_filename(r,legacy=legacy_filenames)
        module = MODULE_FILENAME.search(filename)
        appendix = str(int(module[1])) if module else None
        name = module[2].replace("_", " ").strip() if module else r.get("label", filename)
        names = official_modules.get((r["environment"], appendix), set())
        modules[r["environment"], r["url"]] = next(iter(names)) if len(names) == 1 else name
        appendices[r['environment'], r['url']] = appendix
        version, date = parse_label(r.get('label', filename))
        matched = {m['number'] for m in r.get('module_matches', [])}
        for declaration in r.get("message_types", []):
            identity = (r["environment"], declaration["number"])
            if declaration["number"] not in matched:
                reasons = dict.fromkeys(issue['reason'] for issue in r.get('schema_diagnostics', [])
                                        if issue.get('number') == declaration['number'] and issue.get('reason'))
                document_sources.append(dict(declaration, environment=r["environment"], appendix=appendix,
                    module=modules[r["environment"], r["url"]], document_version=version, label_date=date,
                    document_url=r["url"], document_sha256=r["sha256"],
                    version_tables=r.get("version_tables", []), document_history=r.get("document_history", []),
                    schema_reason='; '.join(reasons) or "Полная XSD для этого документа не сопоставлена."))
            if not module or identity in known: continue
            registry.append(dict(environment=r["environment"], number=declaration["number"],
                                 title=declaration["title"], description="Тип описан в отдельной спецификации модуля.",
                                 appendix=appendix, module=name, source=r["url"],
                                 source_type="module", document_url=r["url"], document_sha256=r["sha256"],
                                 document_version=version, label_date=date))
            known.add(identity)
    for r in rows:
        number=individual_number(r,legacy=legacy_filenames)
        if not number:continue
        fname=document_filename(r,legacy=legacy_filenames);e=r["environment"]
        root, ns, namespace_declared = root_metadata(r.get("structure", []))
        own=[dict(a,environment=e,url=r["url"]+"#embedded="+a["container"],archive_name=a["filename"]+" (из DOCX)") for a in r.get("embedded_archives",[])]
        archives=[a for a in rows if a["environment"]==e and a["kind"]=="zip"]+own
        candidates=[(a,s) for a in archives for s in a.get("schemas",[]) if root_matches(s, root, ns, namespace_declared)]
        if len(candidates) > 1:
            # An explicit XSD path in Word takes precedence over WSDL's
            # automatic choice of entry point for the same declaration.
            if snapshot.get('format',7) >= 7 or any(s.get("wsdl_entry_members") for _,s in candidates):
                candidates = prefer_schema_location(candidates,r.get('structure',[]))
            candidates = prefer_wsdl_inline_entries(candidates, root)
        equivalent = {}
        for archive, schema in sorted(candidates, key=lambda pair: (pair[0]["url"], pair[0]["sha256"], pair[1]["path"])):
            preference = 0
            try:
                closure = dependency_set(archive["schemas"], schema["path"])
            except (ValueError, KeyError):
                # An unresolved candidate must still require review.
                key = ("unresolved", archive["url"], schema["path"])
            else:
                key = ("resolved", equivalent_closure(closure, schema["path"]))
                if known_revisions:
                    rid = sha(encode([e, number, r["sha256"], sha(encode(closure))]))
                    if rid in known_revisions:
                        preference = 2 if rid == preferred_revision_id else 1
            if key not in equivalent or preference > equivalent[key][0]:
                equivalent[key] = (preference, archive, schema)
        candidates = [(archive, schema) for _, archive, schema in equivalent.values()]
        title_record=next((x for x in registry if x["number"]==number and x["environment"]==e),None)
        title=title_record["title"] if title_record is not None else r["label"]
        version,date=parse_label(r["label"])
        d=dict(number=number,environment=e,title=title,document_version=version,label_date=date,document_url=r["url"],document_sha256=r["sha256"],root=root,namespace=ns,namespace_declared=namespace_declared,version_tables=r.get("version_tables",[]),document_history=r.get("document_history",[]),status="review")
        if len(candidates)==1:
            a,s=candidates[0]
            try:
                deps=dependency_set(a["schemas"],s["path"])
                root_text=next(x["description"] for x in s["roots"] if x["name"]==root)
                if d["title"]==r["label"] and root_text:
                    d["title"]=re.split(r"\s*Тип сообщения СЭДО",root_text,flags=re.I)[0].rstrip(". ") or r["label"]
                d["module"]="ВНиМ, ССВ" if "ВНиМ" in fname else "Возмещения" if "Возмещения" in fname else None
                d.update(status="matched",namespace=s["namespace"] or None,archive_url=a["url"],archive_sha256=a["sha256"],schema_path=s["path"],dependency_digest=sha(encode(deps)),dependencies=deps)
                if a.get("archive_name"):d["archive_name"]=a["archive_name"]
                if a.get("source_type") in ("wsdl", "wsdl_bridge"):
                    d.update(schema_source_type=a["source_type"], schema_source_url=a["source_url"],
                             schema_sources=a["source_files"])
            except (ValueError,KeyError) as ex:issues.append(str(ex))
        if d["status"]!="matched":issues.append([e,number,"Требуется проверка сопоставления",len(candidates),
            sorted({"{"+s["namespace"]+"}"+root if s["namespace"] else root+" (без пространства имён)" for _,s in candidates})])
        d["revision_id"]=sha(encode([e,number,r["sha256"],d.get("dependency_digest")]))
        if display_registry is not None and title_record is not None and d['title'] == title_record['title']:
            display_registry[d['revision_id'], r['url']] = ('title', title_record)
        catalog.append(d)
    for r in rows:
        for match in r.get("module_matches", []):
            version,date=parse_label(r["label"])
            document = (r["environment"], r["url"])
            module_record = next((record for record in registry
                           if (record['environment'], record['number'], record['appendix'])
                           == (r['environment'], match['number'], appendices.get(document))), None)
            module = module_record['module'] if module_record is not None else modules.get(document, r['label'])
            row=dict(match,environment=r["environment"],document_url=r["url"],document_sha256=r["sha256"],
                     document_version=version,label_date=date,
                     module=module,
                     source_type="module",status="matched",version_tables=r.get("version_tables",[]),document_history=r.get("document_history",[]))
            row["revision_id"]=sha(encode([r["environment"],row["number"],r["sha256"],row["dependency_digest"]]))
            if display_registry is not None and module_record is not None:
                display_registry[row['revision_id'], r['url']] = ('module', module_record)
            catalog.append(row)
    omitted = {row["revision_id"] for row in snapshot.get("collector_observation", {}).get("unmatched_documents", [])}
    if omitted:
        catalog = [row for row in catalog if row["status"] == "matched" or row["revision_id"] not in omitted]
    return dict(revisions=catalog,registry=registry,document_sources=document_sources,review=issues)

def export(state,target):
    from sync import confirmation_lock, recover_confirmation
    with confirmation_lock(state):
        recover_confirmation(state)
        _export(state, target)


def _export(state,target):
    ptr=json.loads((state/"current.json").read_bytes())["snapshot"]
    history=json.loads((state/"history.json").read_bytes()) if (state/"history.json").exists() else [ptr]
    observation_file=state/"observations.json"
    observations=json.loads(observation_file.read_bytes())["snapshots"] if observation_file.exists() else {}
    archived_path = state / "archived-reviews.json"
    archived_reviews = json.loads(archived_path.read_bytes()) if archived_path.is_file() else {}
    updates=[];update_sources={};source_observations=[];seen_sources=set();previous_by_message={}
    versions={};current_ids=set();registry={};current_registry=set();listed_sources={}
    documents = {}; current_documents = set()
    metadata_cache = {}
    legacy_history_cache = {}
    document_display = {}
    observation_presentations = {}
    for batch,key in enumerate(dict.fromkeys([*history,ptr])):
        snapshot=json.loads((state/"snapshots"/(key+".json")).read_bytes())
        # Enrich the in-memory inventory only: snapshot names and bytes are immutable.
        for document in snapshot.get("entries", []):
            digest = document.get("sha256")
            if document.get("kind") != "docx" or not digest:
                continue
            if digest not in metadata_cache:
                path = state / "objects" / digest
                if path.is_file():
                    raw = path.read_bytes()
                    if sha(raw) != digest:
                        raise ValueError("Historical DOCX digest mismatch: " + digest)
                    metadata_cache[digest] = read_document_display(raw)
                    legacy_history_cache[digest] = read_document_display(raw, legacy=True)['document_history']
                else:
                    metadata_cache[digest] = None
                    legacy_history_cache[digest] = []
            if metadata_cache[digest] is not None:
                document_display[digest] = metadata_cache[digest]
            if 'document_history' not in document:
                document["document_history"] = legacy_history_cache[digest]
        display_registry = {}
        d=derive(snapshot, versions, display_registry=display_registry)
        present = snapshot_display(d['registry'], document_display, display_registry, legacy=snapshot.get('format', 1) < 7)
        historical_modules = {}
        if snapshot.get("format", 1) == 1:
            # Format 1 events used the first registry membership of the number.
            for record in d['registry']:
                historical_modules.setdefault((record['number'], record['environment']), record['module'])
        document_rows = list(d.get('document_sources', []))
        if key != ptr:
            # Older local updates confirmed an inventory before checking its
            # individual XSD matches. Keep those documents and snapshot bytes
            # as historical sources; only a matched schema is an edition.
            accepted = []
            for row in d["revisions"]:
                reason = archived_reviews.get(key, {}).get(row["revision_id"])
                if reason or row.get("status") == "review":
                    document_rows.append({field: row[field] for field in SOURCE_KEYS
                                          if field in row and field != "revision_id"} |
                                         dict(schema_reason=reason or "На дату наблюдения полная XSD не была сопоставлена."))
                else:
                    accepted.append(row)
            d["revisions"] = accepted
        for row in document_rows:
            document_key = (row['number'], row['environment'], row['document_sha256'], row['document_url'])
            if key == ptr or document_key not in current_documents:
                documents[document_key] = present(row)
            if key == ptr:
                current_documents.add(document_key)
        added={}
        for row in d["revisions"]:
            identity=(row["number"],row["environment"])
            source = {name: row[name] for name in SOURCE_KEYS
                      if name in row and name not in ("listed_now", "document_download", "document_filename")}
            observed_source = source
            if row.get('source_type') == 'module' and identity in historical_modules:
                observed_source = dict(source, module=historical_modules[identity])
            source_identity = (row["revision_id"], row.get("document_sha256"), row.get("document_url"))
            if row.get("document_sha256") and source_identity not in seen_sources:
                source_observations.append(dict(revision_id=row["revision_id"], snapshot=key,
                    observed_at=observations.get(key), initial_import=batch==0, source=observed_source))
                # Keep the immutable observation and its snapshot-derived public
                # labels/packaging separate, including after a URL moves or returns.
                displayed = present(observed_source, first_membership=observed_source is not source)
                details = {k: displayed[k] for k in ('title', 'module') if displayed.get(k) != observed_source.get(k)}
                if row.get('status') == 'matched':
                    details.update({k: row[k] for k in SCHEMA_SOURCE_KEYS if k in row})
                observation_presentations.setdefault(row['revision_id'], {})[row.get('document_url') or ''] = details
                seen_sources.add(source_identity)
            if row["revision_id"] not in versions:
                updates.append(dict(revision_id=row["revision_id"],snapshot=key,
                                    observed_at=observations.get(key),initial_import=batch==0,
                                    previous_revision_id=previous_by_message.get(identity)))
                update_sources[row["revision_id"]] = observed_source
                added[identity]=row["revision_id"]
            old=versions.get(row["revision_id"])
            if old:
                def schema_identity(record):
                    return (record.get('root'), record.get('namespace') or None,
                            posixpath.basename(record.get('schema_path') or ''), record.get('dependency_digest'))
                if schema_identity(old) != schema_identity(row):
                    raise ValueError("Конфликт идентичности схемы для редакции " + row['revision_id'] +
                                     ": " + repr(schema_identity(old)) + " / " + repr(schema_identity(row)))
            # A previously observed snapshot can become current again. Keep its
            # source URL and label even when later history contains the same bytes.
            may_replace = key == ptr or row["revision_id"] not in current_ids
            if may_replace and (not old or row.get("source_type")!="manual" or old.get("source_type")=="manual"):
                if old and old.get("manual_source") and not row.get("manual_source"):
                    row=dict(row,manual_source=old["manual_source"])
                versions[row["revision_id"]]=present(row)
            if key==ptr:
                current_ids.add(row["revision_id"])
                listed = present(source)
                listed.update({k: row[k] for k in SCHEMA_SOURCE_KEYS if k in row})
                listed_sources.setdefault(row["revision_id"], []).append(listed)
        # Editions discovered in the same batch share a previous historical edition.
        previous_by_message.update(added)
        for row in d["registry"]:
            identity=sha(encode(row))
            if key == ptr or identity not in current_registry:
                registry[identity]=present(row, registry_row=True)
            if key==ptr:current_registry.add(identity)
        if key==ptr:latest=d
    latest["revisions"]=[dict(r,listed_now=i in current_ids) |
                         (dict(listed_sources=listed_sources[i]) if len(listed_sources.get(i, [])) > 1 else {})
                         for i,r in versions.items()]
    latest["registry"]=[dict(row,listed_now=identity in current_registry) for identity,row in registry.items()]
    current_matched = {(row['number'], row['environment'], source['document_sha256'], source['document_url'])
                       for rid, sources in listed_sources.items() for source in sources
                       for row in [versions[rid]] if row.get('status') == 'matched'
                       and source.get('document_sha256') and source.get('document_url')}
    matched_documents = {(row['number'], row['environment'], source['document_sha256'], source['document_url'])
                         for rid, row in versions.items() if row.get('status') == 'matched'
                         for source in [row, *listed_sources.get(rid, []),
                                        *(item['source'] for item in source_observations if item['revision_id'] == rid)]
                         if source.get('document_sha256') and source.get('document_url')}
    latest['document_sources'] = [dict(row, listed_now=identity in current_documents)
                                  for identity, row in documents.items()
                                  if identity not in current_matched
                                  and (identity in current_documents or identity not in matched_documents)]
    latest["updates"]=updates
    # Keep the legacy events unchanged; provenance is fixed at first observation
    # and reconstructed from archived snapshots, rather than the mutable row.
    latest["update_sources"]=update_sources
    latest["source_observations"]=source_observations
    latest['observation_presentations'] = observation_presentations
    latest['document_display'] = {digest: {k: display[k] for k in ('document_history', 'text_replacements')}
                                  for digest, display in document_display.items()}
    from sync import confirmed_time
    checked_at = confirmed_time(state, ptr)
    if checked_at:
        latest["checked_at"] = checked_at
    write(target,encode(latest))
    print(len(versions),"revisions;",len(latest["registry"]),"registry rows;",len(latest["review"]),"review items")

if __name__=="__main__":
    import argparse
    from auto_update import catalog_locks
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state",type=Path,default=Path("state"));p.add_argument("--output",type=Path,default=Path("catalog.json"))
    a=p.parse_args()
    with catalog_locks(a.state, a.output.parent):
        export(a.state,a.output)

