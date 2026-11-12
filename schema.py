"""Generate the offline SEDO 10110 browser reference from an SFR XSD archive."""
import argparse
import hashlib
import io
import json
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

XS = "http://www.w3.org/2001/XMLSchema"
X = "{" + XS + "}"
HERE = Path(__file__).resolve().parent
FACETS = set("length minLength maxLength pattern enumeration maxInclusive minInclusive maxExclusive minExclusive totalDigits fractionDigits whiteSpace".split())

def resolve_import(files,origin,location,namespace=None):
    expected=posixpath.normpath(posixpath.join(posixpath.dirname(origin),location.replace("\\","/")))
    if expected in files:return expected
    candidates=[path for path,meta in files.items() if posixpath.basename(path)==posixpath.basename(location) and namespace and meta.get("namespace")==namespace]
    if len({files[p]["sha256"] for p in candidates})==1:return sorted(candidates)[0]
    raise ValueError(("Неоднозначный импорт " if candidates else "Не найден импорт ")+location+" в "+origin)

def dependency_set(schemas,entry):
    files={s["path"]:s for s in schemas};visited={}
    def visit(path):
        if path in visited:return
        s=files[path];visited[path]=s["sha256"]
        for loc in s["dependencies"]:
            if not loc:raise ValueError("Импорт без пути в "+path)
            namespace=next((i.get("namespace") for i in s.get("imports",[]) if i.get("location")==loc),None)
            visit(resolve_import(files,path,loc,namespace))
    visit(entry)
    return sorted((posixpath.relpath(p,posixpath.dirname(entry)),h) for p,h in visited.items())

def local(node):
    return node.tag.rsplit("}", 1)[-1]

def notes(node):
    return "\n".join(" ".join("".join(d.itertext()).split()) for d in node.findall(X+"annotation/"+X+"documentation"))

class Schema:
    def __init__(self, archive, entry, root_name):
        self.files = {}
        self.owner = {}
        self.symbol = {}
        self.fields = []
        self.main = entry
        self.inventory = None
        self.load(archive, self.main)
        self.root = self.files[self.main][0].find(X+f"element[@name='{root_name}']")
        if self.root is None:
            raise ValueError("Корневой элемент " + root_name + " не найден.")

    def load(self, archive, path):
        if path in self.files:
            return
        data = archive.read(path)
        ns = dict(n for _, n in ET.iterparse(io.BytesIO(data), events=("start-ns",)))
        root = ET.fromstring(data)
        target = root.get("targetNamespace", "")
        self.files[path] = (root, ns, target)
        for e in root.iter():
            self.owner[e] = path
        for e in root:
            if e.get("name"):
                key = (target, local(e), e.get("name"))
                if key in self.symbol:
                    raise ValueError("Повторное определение: "+str(key))
                self.symbol[key] = e
        for imp in list(root.findall(X+"import")) + list(root.findall(X+"include")):
            location=imp.attrib["schemaLocation"]
            target=posixpath.normpath(posixpath.join(posixpath.dirname(path),location))
            if target not in archive.namelist():
                if self.inventory is None:
                    self.inventory={name:dict(namespace=ET.fromstring(archive.read(name)).get("targetNamespace"),sha256=hashlib.sha256(archive.read(name)).hexdigest()) for name in archive.namelist() if name.lower().endswith(".xsd")}
                target=resolve_import(self.inventory,path,location,imp.get("namespace"))
            self.load(archive,target)

    def resolve(self, qname, source, kinds):
        prefix, _, name = qname.rpartition(":")
        uri = self.files[self.owner[source]][1].get(prefix, "")
        if uri == XS:
            return None
        for k in kinds:
            if (uri,k,name) in self.symbol:
                return self.symbol[(uri,k,name)]
        raise ValueError("Не удалось раскрыть тип или ссылку "+qname+" в "+self.owner[source])

    def field(self, decl, parent=-1, group="", chain=()):
        if len(self.fields) >= 50000:
            raise ValueError("Слишком большой каталог.")
        tag = local(decl)
        src = decl
        if decl.get("ref"):
            src = self.resolve(decl.get("ref"),decl,[tag])
        name = ("@" if tag in ("attribute","anyAttribute") else "") + src.get("name","*")
        path = (self.fields[parent]["path"]+"/" if parent >= 0 else "")+name
        idx = len(self.fields)
        d = dict(name=name,path=path,parent=parent,group=group,doc=notes(src),kind=tag,
                 min=decl.get("minOccurs","0" if tag in ("attribute","anyAttribute") and decl.get("use")!="required" else "1"),
                 max=decl.get("maxOccurs","unbounded" if tag=="anyAttribute" else "1"),type=src.get("type",""),base="",facets=[],values=[],children=[],
                 ns=self.files[self.owner[src]][2] if tag!="attribute" else "",
                 file=self.owner[src].split("/")[-1],flags={k:v for k,v in src.attrib.items() if k in ("fixed","default","nillable","namespace","processContents")})
        self.fields.append(d)
        if parent >= 0:
            self.fields[parent]["children"].append(idx)
        nodegroups = 0
        def expand_type(q,elem,stack):
            obj = self.resolve(q,elem,["simpleType","complexType"])
            if obj is None:
                d["base"]=q
            else:
                if obj in stack:
                    raise ValueError("Рекурсивный тип в "+path)
                d["typeFile"]=self.owner[obj].split("/")[-1]
                walk(obj, "", stack+(obj,))
        def walk(e,group,stack):
            nonlocal nodegroups
            if local(e)=="choice":
                nodegroups+=1
                choice_id=nodegroups
                option=0
            if local(e) in ("choice","sequence","all") and (e.get("minOccurs","1"),e.get("maxOccurs","1"))!=("1","1"):
                group=(group+" / " if group else "")+local(e)+" ["+e.get("minOccurs","1")+".."+e.get("maxOccurs","1")+"]"
            if local(e) in ("restriction","extension") and e.get("base"):
                expand_type(e.get("base"),e,stack)
            for c in e:
                t=local(c)
                if t=="annotation":
                    continue
                child_group=group
                if local(e)=="choice":
                    option+=1
                    label=f"Выбор {choice_id}, вариант {option}"
                    child_group=f"{group} / {label}" if group else label
                if t in ("element","attribute","any","anyAttribute"):
                    self.field(c,idx,child_group,stack)
                elif t in ("complexType","simpleType","complexContent","simpleContent","extension","restriction","sequence","choice","all"):
                    walk(c,child_group,stack)
                elif t in ("attributeGroup","group"):
                    obj=self.resolve(c.get("ref"),c,[t]) if c.get("ref") else c
                    if obj in stack:raise ValueError("Рекурсивная группа в "+path)
                    group_label=child_group
                    if t=="group" and (c.get("minOccurs","1"),c.get("maxOccurs","1"))!=("1","1"):
                        group_label+=(" / " if group_label else "")+"group ["+c.get("minOccurs","1")+".."+c.get("maxOccurs","1")+"]"
                    walk(obj,group_label,stack+(obj,))
                elif t=="union":
                    alternatives=[]
                    variants=[(q,None) for q in c.get("memberTypes","").split()]+[(None,n) for n in c.findall(X+"simpleType")]
                    if not variants:raise ValueError("Пустое объединение типов в "+path)
                    for q,obj in variants:
                        temporary=ET.Element(X+"element",dict(name="unionOption",**({"type":q} if q else {})))
                        self.owner[temporary]=self.owner[c]
                        if obj is not None:temporary.append(obj)
                        original=self.fields
                        try:
                            self.fields=[];self.field(temporary,chain=stack)
                            if len(self.fields)!=1:raise ValueError("Нескалярный вариант union в "+path)
                            option=self.fields[0]
                            alternatives.append({k:option[k] for k in ("type","base","facets","values","flags")}|({"alternatives":option["alternatives"]} if option.get("alternatives") else {}))
                        finally:self.fields=original
                    d["alternatives"]=alternatives
                elif t=="enumeration":
                    d["values"].append([c.get("value"),notes(c)])
                elif t in FACETS:
                    d["facets"].append([t,c.get("value")])
                else:
                    raise ValueError("Неподдерживаемая конструкция "+t+" в "+path)
        if src.get("type"):
            expand_type(src.get("type"),src,chain)
        else:
            walk(src,"",chain)
        return idx

