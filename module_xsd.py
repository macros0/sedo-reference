"""Read original module XSD packages as data and verify document/root bindings."""
import hashlib
import io
import json
import posixpath
import re
import struct
import zipfile
from xml.etree import ElementTree as ET
from schema import Schema, dependency_set

W="{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_FILE=16*1024*1024
MAX_TOTAL=64*1024*1024
MAX_FILES=2048

def digest(content):return hashlib.sha256(content).hexdigest()
def text(node):return " ".join("".join(t.text or "" for t in node.iter(W+"t")).split())

def word_text(node):
    return "".join((n.text or "") if n.tag==W+"t" else "\n" if n.tag==W+"br" else "\t" if n.tag==W+"tab" else "" for n in node.iter())

def document_bindings(doc):
    bindings=[];active=None
    def finish():
        if not active:return
        examples=[];inline_roots=[]
        source="\n".join(active["paragraphs"])
        for match in re.finditer(r'<(?P<name>[A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?)\b[^>]*>.*?</(?P=name)\s*>',source,re.S):
            try:
                element=ET.fromstring(match[0]);namespace,_,name=element.tag[1:].partition("}") if element.tag.startswith("{") else (None,"",element.tag)
                if namespace=="http://www.w3.org/2001/XMLSchema":
                    if name=="schema":inline_roots.extend((e.get("name"),element.get("targetNamespace")) for e in element.findall("{http://www.w3.org/2001/XMLSchema}element"))
                    continue
                if (name,namespace) not in examples:examples.append((name,namespace))
            except ET.ParseError:continue
        candidate=active.get("schema") or active.get("document_root") or (inline_roots[0] if len(set(inline_roots))==1 else None) or (examples[0] if len(examples)==1 else None) or active.get("table")
        if candidate and re.fullmatch(r"[A-Za-z_][\w.-]*",candidate[0]):
            bindings.append(dict(number=active["number"],title=active["title"],root=candidate[0],namespace=candidate[1]))
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
            namespace=next((r[1] for r in rows if len(r)==2 and r[0]=="Пространство имен"),None)
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

def xml_contract(data):
    """Compare full XSD syntax including namespace forms and QName bindings."""
    contexts={};stack=[];pending={};root=None
    for event,item in ET.iterparse(io.BytesIO(data),events=("start-ns","start","end")):
        if event=="start-ns":pending[item[0]]=item[1]
        elif event=="start":
            namespaces=dict(stack[-1]) if stack else {};namespaces.update(pending);pending={}
            contexts[item]=namespaces;stack.append(namespaces)
            if root is None:root=item
        else:stack.pop()
    qnames={"type","ref","base","itemType","memberTypes","substitutionGroup","refer","defaultAttributes"}
    def normalize(node):
        attrs=[];namespaces=contexts[node]
        for key,value in sorted(node.attrib.items()):
            # XMLSpy temporary copies add this compatibility hint; all actual
            # schema declarations, rules and namespace defaults remain compared.
            if node is root and key=="{http://www.w3.org/2007/XMLSchema-versioning}minVersion":continue
            if key in qnames:
                resolved=[]
                for token in value.split():
                    prefix,separator,name=token.partition(":")
                    resolved.append((namespaces.get(prefix if separator else "",""),name if separator else prefix))
                value=resolved
            elif key=="xpath":
                prefixes=re.findall(r"(?:^|[^\w.-])([A-Za-z_][\w.-]*):",value)
                value=(value,[(prefix,namespaces.get(prefix,"")) for prefix in sorted(set(prefixes))])
            attrs.append((key,value))
        context=sorted((prefix,uri) for prefix,uri in namespaces.items() if uri!="http://www.w3.org/2007/XMLSchema-versioning")
        return (node.tag,attrs,context,(node.text or "").strip(),(node.tail or "").strip(),[normalize(child) for child in node])
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
    packages=[];issues=[];seen=set();budget=[MAX_TOTAL]
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
                key=digest(payload)
                if key in seen:continue
                seen.add(key)
                if filename.lower().rsplit(".",1)[-1] not in ("7z","zip","xsd"):continue
                files=unpack_schemas(filename,payload,budget)
                if not files:continue
                output=io.BytesIO()
                with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_STORED) as archive:
                    for name,data in sorted(files.items()):archive.writestr(zipfile.ZipInfo(name,(1980,1,1,0,0,0)),data)
                packages.append(dict(container=path,filename=filename,original_sha256=key,content=output.getvalue()))
            except Exception as exc:issues.append(dict(container=path,reason=str(exc)))
    # Some modules publish the full XSD as paragraphs rather than OLE objects.
    with zipfile.ZipFile(io.BytesIO(document)) as doc:
        if "word/document.xml" not in doc.namelist():return packages,issues
        if doc.getinfo("word/document.xml").file_size>MAX_TOTAL:raise ValueError("Слишком большой XML документа")
        body=ET.fromstring(doc.read("word/document.xml"))
        source="\n".join(word_text(p) for p in body.iter(W+"p"))
    for index,match in enumerate(re.finditer(r'<(?P<p>xs|xsd):schema\b.*?</(?P=p):schema\s*>',source,re.S),1):
        try:
            payload=match[0].encode("utf8");ET.fromstring(payload)
            key=digest(payload)
            if key in seen:continue
            seen.add(key);filename="inline-"+str(index)+".xsd"
            files=unpack_schemas(filename,payload,budget);output=io.BytesIO()
            with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_STORED) as archive:
                for name,data in files.items():archive.writestr(zipfile.ZipInfo(name,(1980,1,1,0,0,0)),data)
            packages.append(dict(container="word/document.xml#schema-"+str(index),filename=filename,original_sha256=key,content=output.getvalue()))
        except Exception as exc:issues.append(dict(container="word/document.xml#schema-"+str(index),reason=str(exc)))
    if len(packages)>1:
        combined={}
        for index,package in enumerate(packages):
            with zipfile.ZipFile(io.BytesIO(package["content"])) as archive:
                for name in archive.namelist():combined["package-"+str(index)+"/"+name]=archive.read(name)
        output=io.BytesIO()
        with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_STORED) as archive:
            for name,data in sorted(combined.items()):archive.writestr(zipfile.ZipInfo(name,(1980,1,1,0,0,0)),data)
        packages.append(dict(container="word/document.xml#combined",filename="Схемы одного DOCX.zip",original_sha256=digest(document),content=output.getvalue()))
    return packages,issues

def match_document(document,entries,read_object):
    matches=[];issues=[]
    own=[dict(a,environment=document["environment"],url=document["url"]+"#embedded="+a["container"],archive_name=a["filename"]+" (из DOCX)") for a in document.get("embedded_archives",[])]
    external=[a for a in entries if a["kind"]=="zip" and a["environment"]==document["environment"]]
    for binding in document.get("module_bindings",[]):
        candidates=[]
        for archives in (own,external):
            for archive in archives:
                for schema in archive.get("schemas",[]):
                    if binding.get("namespace") and binding["namespace"]!=schema["namespace"]:continue
                    if not any(r["name"]==binding["root"] for r in schema["roots"]):continue
                    try:
                        dependencies=dependency_set(archive["schemas"],schema["path"])
                        with zipfile.ZipFile(io.BytesIO(read_object(archive["sha256"]))) as source:
                            tree=Schema(source,schema["path"],binding["root"]);tree.field(tree.root)
                            contracts=[(posixpath.relpath(path,posixpath.dirname(schema["path"])),xml_contract(source.read(path))) for path in tree.files if path!=schema["path"]]
                            contract=(xml_contract(source.read(schema["path"])),sorted(contracts))
                        if len({f["path"] for f in tree.fields})!=len(tree.fields):raise ValueError("Повторные XML-пути в дереве")
                        signature=digest(json.dumps(contract,ensure_ascii=False,sort_keys=True).encode("utf8"))
                        item=dict(binding,namespace=schema["namespace"],archive_sha256=archive["sha256"],archive_url=archive["url"],
                                  archive_name=archive.get("archive_name",archive["url"].rsplit("/",1)[-1]),schema_path=schema["path"],dependencies=dependencies,
                                  dependency_digest=digest(json.dumps(dependencies,ensure_ascii=False,indent=2).encode("utf8")))
                        candidates.append((signature,item))
                    except Exception as exc:issues.append(dict(number=binding["number"],root=binding["root"],reason=str(exc)))
            if candidates:break
        unique={}
        for signature,item in candidates:unique.setdefault(signature,item)
        if len(unique)==1:matches.append(next(iter(unique.values())))
        else:issues.append(dict(number=binding["number"],root=binding["root"],reason="Не найдена полная схема" if not unique else "Неоднозначные схемы"))
    return matches,issues
