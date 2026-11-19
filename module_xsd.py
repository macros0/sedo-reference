"""Read original module XSD packages as data and verify document/root bindings."""
import hashlib
import io
import json
import posixpath
import re
import struct
import zipfile
from array import array
from xml.etree import ElementTree as ET
from schema import Schema, X, dependency_set
from document_metadata import normalized_heading

W="{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_FILE=16*1024*1024
MAX_TOTAL=64*1024*1024
MAX_FILES=2048
# XML 1.0 NameStartChar and NameChar, excluding the QName separator.
NCNAME_START = (r"A-Z_a-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u02FF\u0370-\u037D"
                r"\u037F-\u1FFF\u200C-\u200D\u2070-\u218F\u2C00-\u2FEF\u3001-\uD7FF"
                r"\uF900-\uFDCF\uFDF0-\uFFFD\U00010000-\U000EFFFF")
NCNAME = rf"[{NCNAME_START}][{NCNAME_START}0-9.\-\u00B7\u0300-\u036F\u203F-\u2040]*"
QNAME = rf"{NCNAME}(?::{NCNAME})?"
FRAGMENT_TAG = re.compile(rf"<(/?)({QNAME})(?=[\s/>])")
FRAGMENT_ATTRIBUTES = re.compile(rf"""(?:\s+{QNAME}\s*=\s*(?:"[^"]*"|'[^']*'))*\s*/?>""")

def digest(content):return hashlib.sha256(content).hexdigest()
def text(node):return " ".join("".join(t.text or "" for t in node.iter(W+"t")).split())

def word_text(node):
    return "".join((n.text or "") if n.tag==W+"t" else "\n" if n.tag==W+"br" else "\t" if n.tag==W+"tab" else "" for n in node.iter())

def xml_fragments(source, *, max_length=MAX_FILE, stats=None, accept_root=None):
    """Read non-overlapping XML fragments without rescanning incomplete suffixes."""
    position = 0
    start = None
    stack = []
    # Compact character offsets retain complete subtrees until their enclosing
    # candidate closes. An unfinished prose placeholder can then be discarded.
    completed = array("Q")
    def recovered():
        for index in range(0, len(completed), 2):
            yield source[completed[index]:completed[index + 1]]
    while position < len(source):
        opening = source.find("<", position)
        if opening < 0:
            if stats is not None:
                stats["characters"] = stats.get("characters", 0) + len(source) - position
            if start is not None and len(source) - start > max_length:
                raise ValueError("Слишком большой XML-фрагмент")
            break
        if start is not None and opening - start > max_length:
            raise ValueError("Слишком большой XML-фрагмент")
        terminator = next((end for prefix, end in (("<!--", "-->"), ("<![CDATA[", "]]>"), ("<?", "?>"))
                           if source.startswith(prefix, opening)), None)
        if terminator:
            closing = source.find(terminator, opening + 2)
            end = len(source) if closing < 0 else closing + len(terminator)
        else:
            end = opening + 1
            quote = None
            abandoned = False
            while end < len(source):
                character = source[end]
                if quote:
                    if character == quote:
                        quote = None
                elif character in ('"', "'"):
                    quote = character
                elif character == ">":
                    end += 1
                    break
                elif character == "<":
                    abandoned = True
                    break
                end += 1
                if end - opening > 8192:
                    abandoned = True
                    break
            if abandoned:
                if stats is not None:
                    stats["characters"] = stats.get("characters", 0) + end - position
                position = end
                yield from recovered()
                del completed[:]
                stack.clear()
                start = None
                continue
        if stats is not None:
            stats["characters"] = stats.get("characters", 0) + end - position
        if start is not None and end - start > max_length:
            raise ValueError("Слишком большой XML-фрагмент")
        token = source[opening:end]
        position = end
        if terminator or not token.endswith(">"):
            continue
        name = FRAGMENT_TAG.match(token)
        if not name:
            continue
        tail = token[name.end():]
        valid_tail = re.fullmatch(r"\s*>", tail) if name[1] else FRAGMENT_ATTRIBUTES.fullmatch(tail)
        if not valid_tail:
            yield from recovered()
            del completed[:]
            stack.clear()
            start = None
            continue
        if not stack and accept_root is not None and not accept_root(name[2]):
            continue
        if name[1]:
            if not stack or stack[-1][0] != name[2]:
                yield from recovered()
                del completed[:]
                stack.clear()
                start = None
                continue
            fragment_start = stack.pop()[1]
        else:
            fragment_start = opening
            if not stack:
                start = opening
            if not token.rstrip().endswith("/>"):
                if len(stack) >= 512:
                    raise ValueError("Слишком глубокий XML-фрагмент")
                stack.append((name[2], opening))
        if name[1] or token.rstrip().endswith("/>"):
            while completed and completed[-2] >= fragment_start:
                del completed[-2:]
            if stack:
                completed.extend((fragment_start, end))
        if not stack and start is not None:
            yield source[start:end]
            del completed[:]
            start = None
    yield from recovered()

def document_bindings(doc, *, legacy=False, changes=None):
    bindings=[];active=None
    def finish():
        if not active:return
        examples=[];inline_roots=[]
        source="\n".join(active["paragraphs"])
        for fragment in xml_fragments(source):
            try:
                element=ET.fromstring(fragment);namespace,_,name=element.tag[1:].partition("}") if element.tag.startswith("{") else (None if legacy else "","",element.tag)
                if namespace=="http://www.w3.org/2001/XMLSchema":
                    if name=="schema":inline_roots.extend((e.get("name"),element.get("targetNamespace", None if legacy else "")) for e in element.findall("{http://www.w3.org/2001/XMLSchema}element"))
                    continue
                if (name,namespace) not in examples:examples.append((name,namespace))
            except ET.ParseError:continue
        candidate=active.get("schema") or active.get("document_root") or (inline_roots[0] if len(set(inline_roots))==1 else None) or (examples[0] if len(examples)==1 else None) or active.get("table")
        if candidate and candidate[1] is None and not legacy:
            evidence = {ns for name, ns in [*inline_roots, *examples] if name == candidate[0]}
            if len(evidence) == 1:
                candidate = (candidate[0], evidence.pop())
                if changes is not None:changes.add('module_namespace')
        if candidate and re.fullmatch(NCNAME,candidate[0]):
            ns = candidate[1]
            if ns == '' and changes is not None:changes.add('module_namespace')
            bindings.append(dict(number=active["number"],title=active["title"],root=candidate[0],namespace=ns))
    for child in doc.find(W+"body"):
        if child.tag==W+"p":
            style=child.find(W+"pPr/"+W+"pStyle")
            if style is not None and re.search(r"toc|contents|оглав",style.get(W+"val",""),re.I):continue
            declaration=re.fullmatch(r"Тип сообщения\s+(?:СЭДО\s*)?(?:№\s*)?(\d{1,5})\s*[:—–-]\s*(.+)",text(child),re.I)
            if declaration:
                finish();active=dict(number=declaration[1],title=declaration[2],paragraphs=[])
            elif re.match(r"^(?:Приложение|Атрибуты\s+типов$|Атрибутивный/элементный\s+состав|В данном разделе.*всех элементов)",text(child),re.I):
                finish();active=None
            elif active:active["paragraphs"].append(word_text(child))
        elif active and child.tag==W+"tbl":
            rows=[[text(c) for c in tr.findall(W+"tc")] for tr in child.findall(W+"tr")]
            namespace=next((r[1] for r in rows if len(r)==2 and normalized_heading(r[0])=="пространство имен"),None)
            if not legacy and namespace == '':
                namespace = None
                if changes is not None:changes.add('module_namespace')
            for i,row in enumerate(rows[:-1]):
                if row and re.sub(r"\s+","",row[0]).casefold() in ("элемент/атрибут","атрибут/элемент"):
                    active.setdefault("table",(rows[i+1][0],namespace))
                    if len(rows[i+1])>=5 and rows[i+1][1].split(":")[-1]=="DocumentType":active.setdefault("document_root",(rows[i+1][0],namespace))
                    break
            for cells in rows:
                if len(cells)==2 and cells[0]=="Схема":
                    declaration=re.match(r'<(?:xs|xsd):element\s+[^>]*name="([^"]+)"',cells[1])
                    if declaration:active.setdefault("schema",(declaration[1],namespace))
    finish()
    return bindings

def xml_contract(data, *, tree=None, document=None, legacy=False):
    """Compare full XSD syntax including namespace forms and QName bindings."""
    contexts={};parents={};stack=[];pending={};root=None
    for event,item in ET.iterparse(io.BytesIO(data),events=("start-ns","start","end")):
        if event=="start-ns":pending[item[0]]=item[1]
        elif event=="start":
            namespaces=dict(contexts[stack[-1]]) if stack else {};namespaces.update(pending);pending={}
            if stack:parents[item]=stack[-1]
            contexts[item]=namespaces;stack.append(item)
            if root is None:root=item
        else:stack.pop()
    qnames={"type","ref","base","itemType","memberTypes","substitutionGroup","refer","defaultAttributes"}
    def expanded(token, node):
        prefix, _, name = token.rpartition(':')
        if prefix == 'xml':
            return ('http://www.w3.org/XML/1998/namespace', name)
        scope = contexts[node]
        return (scope.get(prefix, ('unbound', prefix) if prefix else ''), name)
    named = {(root.get('targetNamespace', ''), node.get('name')):node for node in root.findall(X+'simpleType')}
    if tree is not None:
        named.update({(ns, name):node for (ns, kind, name),node in tree.symbol.items() if kind == 'simpleType'})
    def scalar_type(node):
        if node.tag == X+'enumeration':
            node = parents[node]
        seen = set()
        while node is not None and node not in seen:
            seen.add(node)
            ref = node.get('type') or node.get('base')
            if ref:
                if tree is not None and node in tree.owner:
                    identity = tree.qname_namespace(ref, node), ref.rsplit(':', 1)[-1]
                else:
                    identity = expanded(ref.strip(), node)
                    if tree is not None and not identity[0] and not root.get('targetNamespace'):
                        identity = (document[2], identity[1])
                if identity[0] == X[1:-1]:return identity[1]
                node = named.get(identity)
            else:
                restriction = node.find(X+'restriction')
                node = restriction if restriction is not None else node.find(X+'simpleType')
        return None
    def literal(value, node):
        typ = scalar_type(node)
        if typ in ('QName', 'NOTATION') and re.fullmatch(QNAME, value.strip()):
            return expanded(value.strip(), node)
        if typ is not None:
            return value
        # Imported types, unions and lists can contain QName values. Retain
        # their lexical spelling and the bindings of potentially significant words.
        words = [word for word in value.split() if re.fullmatch(QNAME, word)]
        return (value, [expanded(word, node) for word in words]) if words else value
    def xpath(value, node):
        # selector/field XPath uses a restricted grammar without string literals.
        tokens = re.split(rf'({NCNAME}:(?:{NCNAME}|\*))', value)
        return [expanded(token, node) if i % 2 else token for i, token in enumerate(tokens)]
    def normalize(node):
        attrs=[]
        for key,value in sorted(node.attrib.items()):
            # XMLSpy temporary copies add this compatibility hint; all actual
            # schema declarations, rules and namespace defaults remain compared.
            if node is root and key=="{http://www.w3.org/2007/XMLSchema-versioning}minVersion":continue
            if key in qnames:
                value=[expanded(token, node) for token in value.split()]
            elif not legacy and (key in ('default', 'fixed') or key == 'value' and node.tag == X+'enumeration'):
                value=literal(value, node)
            elif not legacy and key=="xpath" and node.tag in (X+'selector', X+'field'):
                value=xpath(value, node)
            attrs.append((key,value))
        scope = sorted((p,u) for p,u in contexts[node].items() if u!='http://www.w3.org/2007/XMLSchema-versioning') if legacy else []
        return (node.tag,attrs,scope,(node.text or "").strip(),(node.tail or "").strip(),[normalize(child) for child in node])
    return normalize(root)

def native_package(data):
    if len(data)<10:raise ValueError("Повреждён OLE Native package")
    offset=6
    def string():
        nonlocal offset
        end=data.find(b"\0",offset)
        if end<0:raise ValueError("Оборванная строка OLE package")
        value=data[offset:end];offset=end+1;return value
    label=string().decode("cp1251");string();offset+=8;string()
    if offset+4>len(data):raise ValueError("Нет длины вложения OLE")
    size=struct.unpack_from("<I",data,offset)[0];offset+=4
    if size>MAX_TOTAL or offset+size>len(data):raise ValueError("Недопустимый размер вложения OLE")
    return label,data[offset:offset+size]

def safe_name(name):
    name=name.replace("\\","/")
    if not name or name.startswith("/") or ":" in name or any(p in ("..","") for p in name.rstrip("/").split("/")):
        raise ValueError("Недопустимый путь вложения: "+name)
    return name.rstrip("/")

def unpack_schemas(filename,content,budget=None):
    budget=budget if budget is not None else [MAX_TOTAL]
    extension=filename.lower().rsplit(".",1)[-1]
    files={}
    if len(content)>MAX_TOTAL:raise ValueError("Слишком большое вложение")
    if extension=="xsd":
        if len(content)>MAX_FILE or len(content)>budget[0]:raise ValueError("Слишком большой XSD")
        budget[0]-=len(content)
        return {safe_name(filename):content}
    if extension=="zip":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries=archive.infolist()
            if len(entries)>MAX_FILES or sum(i.file_size for i in entries)>budget[0]:raise ValueError("Слишком большой ZIP")
            for item in entries:
                name=safe_name(item.filename)
                if item.is_dir() or not name.lower().endswith(".xsd"):continue
                if item.file_size>MAX_FILE or name in files:raise ValueError("Недопустимый XSD в ZIP")
                files[name]=archive.read(item)
                budget[0]-=len(files[name])
    elif extension=="7z":
        import py7zr
        from py7zr.io import WriterFactory
        from threading import Lock
        lock=Lock()
        class Buffer(io.BytesIO):
            def write(self,value):
                with lock:
                    if self.tell()+len(value)>MAX_FILE or len(value)>budget[0]:raise ValueError("Превышен лимит XSD")
                    budget[0]-=len(value)
                    return super().write(value)
            def size(self):return len(self.getvalue())
            def close(self):pass  # py7zr closes sinks before the caller reads their bytes.
        class Factory(WriterFactory):
            def __init__(self):self.products={}
            def create(self,filename):
                name=safe_name(filename)
                if name in self.products:raise ValueError("Повторное имя XSD")
                result=Buffer();self.products[name]=result;return result
        with py7zr.SevenZipFile(io.BytesIO(content),"r") as archive:
            entries=archive.list()
            if archive.needs_password() or len(entries)>MAX_FILES or sum(i.uncompressed or 0 for i in entries)>budget[0]:raise ValueError("Недопустимый 7z")
            targets=[]
            for item in entries:
                name=safe_name(item.filename)
                if not item.is_directory and name.lower().endswith(".xsd"):
                    if item.uncompressed>MAX_FILE:raise ValueError("Слишком большой XSD")
                    targets.append(item.filename)
            factory=Factory()
            if targets:archive.extract(targets=targets,factory=factory)
            files={n:b.getvalue() for n,b in factory.products.items()}
    if sum(map(len,files.values()))>MAX_TOTAL:raise ValueError("Слишком много XSD")
    return files

def extract_archives(document):
    import olefile
    packages=[];issues=[];seen=set();seen_packages=set();budget=[MAX_TOTAL]
    with zipfile.ZipFile(io.BytesIO(document)) as doc:
        members=[i for i in doc.infolist() if i.filename.startswith("word/embeddings/") and not i.is_dir()]
        if len(members)>MAX_FILES or sum(i.file_size for i in members)>MAX_TOTAL:
            return [],[dict(reason="Превышен общий лимит OLE-вложений")]
        for member in sorted(members,key=lambda i:i.filename):
            path=member.filename
            try:
                if member.file_size>MAX_FILE:raise ValueError("Слишком большое OLE-вложение")
                raw=doc.read(member)
                if olefile.isOleFile(data=raw):
                    with olefile.OleFileIO(io.BytesIO(raw)) as ole:
                        if not ole.exists("\x01Ole10Native"):continue
                        filename,payload=native_package(ole.openstream("\x01Ole10Native").read())
                else:filename,payload=path.rsplit("/",1)[-1],raw
                if filename.lower().rsplit(".",1)[-1] not in ("7z","zip","xsd"):continue
                key=digest(payload)
                identity=(filename if filename.lower().endswith(".xsd") else "", key)
                if identity in seen_packages:continue
                files=unpack_schemas(filename,payload,budget)
                if not files:continue
                seen_packages.add(identity)
                seen.add(key)
                output=io.BytesIO()
                with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_STORED) as archive:
                    for name,data in sorted(files.items()):
                        info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3
                        archive.writestr(info,data)
                packages.append(dict(container=path,filename=filename,original_sha256=key,content=output.getvalue()))
            except ImportError:
                # A missing archive reader is an environment error.
                raise
            except Exception as exc:issues.append(dict(container=path,reason=str(exc)))
    # Some modules publish the full XSD as paragraphs rather than OLE objects.
    with zipfile.ZipFile(io.BytesIO(document)) as doc:
        if "word/document.xml" not in doc.namelist():return packages,issues
        if doc.getinfo("word/document.xml").file_size>MAX_TOTAL:raise ValueError("Слишком большой XML документа")
        body=ET.fromstring(doc.read("word/document.xml"))
        source="\n".join(word_text(p) for p in body.iter(W+"p"))
    # The old ordinal is part of public source IDs. Allocate its original slots
    # first, even for a malformed quotation; append newly recognized spellings.
    legacy = {"xs:schema", "xsd:schema"}
    def schema_fragments():
        yield from xml_fragments(source, accept_root=legacy.__contains__)
        yield from xml_fragments(source, accept_root=lambda q: q.rsplit(":", 1)[-1] == "schema" and q not in legacy)
    for index,fragment in enumerate(schema_fragments(),1):
        try:
            payload=fragment.encode("utf8")
            if ET.fromstring(payload).tag != X + "schema":
                continue
            key=digest(payload)
            if key in seen:continue
            seen.add(key);filename="inline-"+str(index)+".xsd"
            files=unpack_schemas(filename,payload,budget);output=io.BytesIO()
            with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_STORED) as archive:
                for name,data in files.items():
                    info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3
                    archive.writestr(info,data)
            packages.append(dict(container="word/document.xml#schema-"+str(index),filename=filename,original_sha256=key,content=output.getvalue()))
        except Exception as exc:issues.append(dict(container="word/document.xml#schema-"+str(index),reason=str(exc)))
    if len(packages)>1:
        combined={}
        for index,package in enumerate(packages):
            with zipfile.ZipFile(io.BytesIO(package["content"])) as archive:
                for name in archive.namelist():combined["package-"+str(index)+"/"+name]=archive.read(name)
        output=io.BytesIO()
        with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_STORED) as archive:
            for name,data in sorted(combined.items()):
                info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3
                archive.writestr(info,data)
        packages.append(dict(container="word/document.xml#combined",filename="Схемы одного DOCX.zip",original_sha256=digest(document),content=output.getvalue()))
    return packages,issues

def match_document(document,entries,read_object,*,legacy=False,changes=None):
    from wsdl_sources import prefer_wsdl_inline_entries
    matches=[];issues=[]
    bindings=document.get("module_bindings",[])
    conflicts={}
    for binding in bindings:
        conflicts.setdefault(binding['number'], set()).add((binding['root'], binding.get('namespace')))
    conflicts={number: identities for number, identities in conflicts.items()
               if len({root for root, _ in identities}) > 1 or
               len({ns for _, ns in identities if ns is not None}) > 1}
    for number, identities in sorted(conflicts.items()):
        qnames=sorted('{'+namespace+'}'+root if namespace else root+
                      (' (без пространства имён)' if namespace == '' else ' (пространство имён не указано)')
                      for root, namespace in identities)
        issues.append(dict(number=number,reason='Несовместимые корни одного номера: '+', '.join(qnames),candidates=qnames))
    own=[dict(a,environment=document["environment"],url=document["url"]+"#embedded="+a["container"],archive_name=a["filename"]+" (из DOCX)") for a in document.get("embedded_archives",[])]
    external=[a for a in entries if a["kind"]=="zip" and a["environment"]==document["environment"]]
    bridged=document.get("wsdl_archives",[])
    native=[a for a in bridged if "#schema-" not in a.get("document_container","")]
    inline=[a for a in bridged if "#schema-" in a.get("document_container","")]
    for binding in bindings:
        if binding['number'] in conflicts:continue
        candidates=[]
        for archives in (own,external,native,inline):
            for archive in archives:
                for schema in archive.get("schemas",[]):
                    # A parsed XML root can prove the empty namespace. Old
                    # records and table-only hints without this evidence use None.
                    known = bool(binding.get("namespace")) if legacy else binding.get("namespace") is not None
                    if known and binding["namespace"]!=(schema["namespace"] or ""):continue
                    if not any(r["name"]==binding["root"] for r in schema["roots"]):continue
                    try:
                        dependencies=dependency_set(archive["schemas"],schema["path"])
                        with zipfile.ZipFile(io.BytesIO(read_object(archive["sha256"]))) as source:
                            tree=Schema(source,schema["path"],binding["root"]);tree.field(tree.root)
                            def contract_for(path):
                                return xml_contract(source.read(path), tree=tree, document=tree.files[path], legacy=legacy)
                            contracts=[(posixpath.relpath(path,posixpath.dirname(schema["path"])),contract_for(path)) for path in tree.files if path!=schema["path"]]
                            contract=(contract_for(schema["path"]),sorted(contracts))
                        if len({f["path"] for f in tree.fields})!=len(tree.fields):raise ValueError("Повторные XML-пути в дереве")
                        signature=digest(json.dumps(contract,ensure_ascii=False,sort_keys=True).encode("utf8"))
                        item=dict(binding,namespace=schema["namespace"],archive_sha256=archive["sha256"],archive_url=archive["url"],
                                  archive_name=archive.get("archive_name",archive["url"].rsplit("/",1)[-1]),schema_path=schema["path"],dependencies=dependencies,
                                  dependency_digest=digest(json.dumps(dependencies,ensure_ascii=False,indent=2).encode("utf8")))
                        if archive.get("source_type") in ("wsdl","wsdl_bridge"):
                            item["schema_source_type"]=archive["source_type"]
                            item["schema_source_url"]=archive["source_url"]
                            item["schema_sources"]=archive["source_files"]
                        candidates.append((archive,schema,signature,item))
                    except Exception as exc:issues.append(dict(number=binding["number"],root=binding["root"],reason=str(exc)))
            if candidates:break
        candidates=prefer_wsdl_inline_entries(candidates,binding["root"])
        unique={}
        for _,_,signature,item in candidates:unique.setdefault(signature,item)
        if changes is not None and len({item['dependency_digest'] for _,_,_,item in candidates}) > 1:
            changes.add('module_contract')
        if len(unique)==1:
            item=next(iter(unique.values()))
            identity=lambda m:(m['number'],m['root'],m['namespace'],m['schema_path'],m['dependency_digest'])
            if not any(identity(m)==identity(item) for m in matches):matches.append(item)
        else:issues.append(dict(number=binding["number"],root=binding["root"],reason="Не найдена полная схема" if not unique else "Неоднозначные схемы"))
    return matches,issues
