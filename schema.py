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
from urllib.parse import parse_qs, urlsplit, unquote

XS = "http://www.w3.org/2001/XMLSchema"
X = "{" + XS + "}"
HERE = Path(__file__).resolve().parent
MAX_FIELDS = 50000
FACETS = set("length minLength maxLength pattern enumeration maxInclusive minInclusive maxExclusive minExclusive totalDigits fractionDigits whiteSpace".split())

def import_basename(location):
    parsed=urlsplit(location)
    resource=parse_qs(parsed.query).get("xsd",[parsed.path if parsed.scheme else location])[0]
    return posixpath.basename(unquote(resource).replace("\\","/"))

def resolve_import(files,origin,location,namespace=None,kind="import"):
    allowed={namespace or ""}
    if kind in ("include", "redefine"):allowed.add("")
    def matches(meta):return (kind=="legacy" and namespace is None) or (meta.get("namespace") or "") in allowed
    expected=posixpath.normpath(posixpath.join(posixpath.dirname(origin),location.replace("\\","/")))
    if expected in files:
        if not matches(files[expected]):raise ValueError("Несовпадение namespace импорта "+location)
        return expected
    candidates=[path for path,meta in files.items() if posixpath.basename(path)==import_basename(location)
                and not (kind=="legacy" and namespace is None) and matches(meta)]
    if len({files[p]["sha256"] for p in candidates})==1:return sorted(candidates)[0]
    raise ValueError(("Неоднозначный импорт " if candidates else "Не найден импорт ")+location+" в "+origin)

def dependency_set(schemas,entry):
    files={s["path"]:s for s in schemas};visited={};contexts=set()
    def visit(path,inherited_namespace=None):
        s=files[path];effective=s.get("namespace") or inherited_namespace or ""
        if (path,effective) in contexts:return
        contexts.add((path,effective));visited[path]=s["sha256"]
        for loc in s["dependencies"]:
            if not loc:raise ValueError("Импорт без пути в "+path)
            reference=next((i for i in s.get("imports",[]) if i.get("location")==loc),{})
            # Old snapshots recorded only schemaLocation. Preserve their
            # exact-path closure without guessing an absent import namespace.
            kind=reference.get("kind","import") if reference else "legacy"
            namespace=effective if kind=="include" else reference.get("namespace")
            visit(resolve_import(files,path,loc,namespace,kind),effective if kind=="include" else None)
    visit(entry)
    return sorted((posixpath.relpath(p,posixpath.dirname(entry)),h) for p,h in visited.items())

def local(node):
    return node.tag.rsplit("}", 1)[-1]

def notes(node):
    return "\n".join(" ".join("".join(d.itertext()).split()) for d in node.findall(X+"annotation/"+X+"documentation"))

class Schema:
    def __init__(self, archive, entry, root_name):
        self.files = {}
        # A chameleon document can contribute distinct components to several
        # namespaces. Keep a separate parsed tree for every effective namespace,
        # while retaining real archive paths for provenance and old consumers.
        self.documents = {}
        self.context = {}
        self.owner = {}
        self.prefixes = {}
        self.symbol = {}
        self.fields = []
        self.field_declarations = {}
        self.expanded_fields = 0
        self.main = entry
        self.inventory = None
        self.load(archive, self.main)
        self.root = self.files[self.main][0].find(X+f"element[@name='{root_name}']")
        if self.root is None:
            raise ValueError("Корневой элемент " + root_name + " не найден.")

    def load(self, archive, path, inherited_namespace=None):
        data = archive.read(path)
        parser = ET.iterparse(io.BytesIO(data), events=("start-ns", "start", "end"))
        scopes = [{}]
        declarations = {}
        for event, value in parser:
            if event == "start-ns":
                prefix, uri = value
                declarations[prefix] = uri
            elif event == "start":
                scope = scopes[-1] | declarations if declarations else scopes[-1]
                scopes.append(scope)
                declarations.clear()
                self.prefixes[value] = scope
                self.owner[value] = path
            else:
                scopes.pop()
        root = parser.root
        declared = root.get("targetNamespace", "")
        target = declared or inherited_namespace or ""
        if (path,target) in self.documents:return
        document=(root,self.prefixes[root],target)
        self.documents[path,target]=document
        self.files.setdefault(path,document)
        for node in root.iter():self.context[node]=document
        for e in root:
            if e.get("name"):
                key = (target, local(e), e.get("name"))
                if key in self.symbol:
                    raise ValueError("Повторное определение: "+str(key))
                self.symbol[key] = e
        for imp in list(root.findall(X+"import")) + list(root.findall(X+"include")):
            location=imp.attrib["schemaLocation"]
            if self.inventory is None:
                self.inventory={name:dict(namespace=ET.fromstring(archive.read(name)).get("targetNamespace"),sha256=hashlib.sha256(archive.read(name)).hexdigest()) for name in archive.namelist() if name.lower().endswith(".xsd")}
            kind=local(imp)
            imported=resolve_import(self.inventory,path,location,imp.get("namespace") if kind=="import" else target,kind)
            self.load(archive,imported,target if kind=="include" else None)

    def qname_namespace(self, qname, source):
        prefix, _, _name = qname.rpartition(":")
        scope = self.prefixes[source]
        if prefix and prefix not in scope:
            raise ValueError("Необъявленный префикс "+prefix+" в "+self.owner[source])
        uri=scope.get(prefix, "")
        schema,_,effective=self.context.get(source,self.files[self.owner[source]])
        if not prefix and not uri and not schema.get("targetNamespace"):
            uri=effective
        return uri

    def resolve(self, qname, source, kinds):
        prefix, _, name = qname.rpartition(":")
        uri = self.qname_namespace(qname,source)
        if uri == XS:
            return None
        for k in kinds:
            if (uri,k,name) in self.symbol:
                return self.symbol[(uri,k,name)]
        raise ValueError("Не удалось раскрыть тип или ссылку "+qname+" в "+self.owner[source])

    def instance_namespace(self, declaration):
        schema, _, target = self.context.get(declaration,self.files[self.owner[declaration]])
        kind = local(declaration)
        if self.symbol.get((target, kind, declaration.get("name"))) is declaration:
            return target
        default = "attributeFormDefault" if kind in ("attribute", "anyAttribute") else "elementFormDefault"
        return target if declaration.get("form", schema.get(default, "unqualified")) == "qualified" else ""

    def coalesce_choice_leaves(self):
        """One XML path can be the same leaf in alternative choice branches.

        Merge only identical leaf declarations with the same parent and distinct
        branches of one choice. Different constraints or sequential repetitions
        remain unsupported rather than being silently combined.
        """
        occurrences={}
        for i,field in enumerate(self.fields):occurrences.setdefault(field["path"],[]).append(i)
        aliases={}
        for indices in occurrences.values():
            if len(indices)<2:continue
            leaves=[self.fields[i] for i in indices]
            contexts=[re.fullmatch(r"(.*Выбор \d+), вариант (\d+)",f["group"]) for f in leaves]
            rules=[{k:v for k,v in f.items() if k not in ("group","children")} for f in leaves]
            if (any(f["children"] for f in leaves) or any(c is None for c in contexts) or
                len({c[1] for c in contexts})!=1 or len({c[2] for c in contexts})!=len(leaves) or
                any(rule!=rules[0] for rule in rules[1:])):
                continue
            leaves[0]["group"]=" ИЛИ ".join(f["group"] for f in leaves)
            for i in indices[1:]:aliases[i]=indices[0]
        if not aliases:return
        kept=[i for i in range(len(self.fields)) if i not in aliases]
        positions={old:new for new,old in enumerate(kept)}
        for i in aliases:positions[i]=positions[aliases[i]]
        compact=[]
        for i in kept:
            field=self.fields[i]
            field["parent"]=positions[field["parent"]] if field["parent"]>=0 else -1
            field["children"]=list(dict.fromkeys(positions[child] for child in field["children"]))
            compact.append(field)
        self.fields=compact

    def field(self, decl, parent=-1, group="", chain=()):
        if self.expanded_fields >= MAX_FIELDS:
            raise ValueError("Слишком большой каталог.")
        self.expanded_fields += 1
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
                 ns=self.instance_namespace(src),
                 file=self.owner[src].split("/")[-1],flags={k:v for k,v in src.attrib.items() if k in ("fixed","default","nillable","namespace","processContents")})
        if tag == "attribute" and src is not decl and any(k in decl.attrib for k in ("fixed", "default")):
            # An attribute use owns its value constraint; the referenced global
            # declaration supplies the type and QName, not the local fixed/default.
            for key in ("fixed", "default"): d["flags"].pop(key, None)
            d["flags"].update({k:decl.get(k) for k in ("fixed", "default") if k in decl.attrib})
        self.field_declarations[id(d)] = (decl, src)
        self.fields.append(d)
        if parent >= 0:
            self.fields[parent]["children"].append(idx)
        nodegroups = 0
        content = []
        complex_type = False
        def expand_type(q,elem,stack,particles,attributes_only=False,excluded_attributes=frozenset()):
            if elem is src and q == src.get("type"):
                d["typeNs"] = self.qname_namespace(q,elem)
            obj = self.resolve(q,elem,["simpleType","complexType"])
            if obj is None:
                if not attributes_only:
                    d["base"]=q
                    d["baseNs"]=self.qname_namespace(q,elem)
            else:
                if obj in stack:
                    raise ValueError("Рекурсивный тип в "+path)
                d.setdefault("typeFile", self.owner[obj].split("/")[-1])
                walk(obj, "", stack+(obj,),particles,attributes_only=attributes_only,excluded_attributes=excluded_attributes)
        def attribute_key(e):
            obj=self.resolve(e.get("ref"),e,["attribute"]) if e.get("ref") else e
            return self.instance_namespace(obj),obj.get("name")
        def restriction_attributes(e,visited=()):
            keys=set()
            for child in e:
                if local(child)=="attribute":keys.add(attribute_key(child))
                elif local(child)=="attributeGroup":
                    obj=self.resolve(child.get("ref"),child,["attributeGroup"])
                    if obj in visited:raise ValueError("Рекурсивная группа в "+path)
                    keys.update(restriction_attributes(obj,visited+(obj,)))
            return keys
        def walk(e,group,stack,particles,attributes_only=False,excluded_attributes=frozenset(),complex_content=False):
            nonlocal nodegroups, complex_type
            kind = local(e)
            if kind == "complexType" and not complex_type:
                complex_type = True
                complex_content = e.find(X+"complexContent")
                mixed = (complex_content.get("mixed", e.get("mixed", "false"))
                         if complex_content is not None else e.get("mixed", "false"))
                if mixed in ("true", "1"):
                    d["flags"]["mixed"] = "true"
                else:
                    d["flags"].pop("mixed", None)
            if attributes_only and kind in ("choice","sequence","all","group"):return
            if kind in ("choice", "sequence", "all"):
                model = [kind]
                particles.append(model)
                particles = model
            if local(e)=="choice":
                nodegroups+=1
                choice_id=nodegroups
                option=0
            if local(e) in ("choice","sequence","all") and (e.get("minOccurs","1"),e.get("maxOccurs","1"))!=("1","1"):
                group=(group+" / " if group else "")+local(e)+" ["+e.get("minOccurs","1")+".."+e.get("maxOccurs","1")+"]"
            if local(e) in ("restriction","extension") and e.get("base"):
                restrict_complex=kind=="restriction" and complex_content
                inherited_exclusions=excluded_attributes | restriction_attributes(e) if restrict_complex else excluded_attributes
                expand_type(e.get("base"),e,stack,particles,attributes_only or restrict_complex,inherited_exclusions)
                if kind == "restriction" and e.find(X+"enumeration") is not None:
                    d["values"].clear()
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
                    if attributes_only and t not in ("attribute","anyAttribute"):continue
                    # A restriction inherits named attribute uses, but its
                    # wildcard is its own declaration (absence removes it).
                    if attributes_only and t=="anyAttribute":continue
                    if t=="attribute" and (c.get("use")=="prohibited" or attribute_key(c) in excluded_attributes):continue
                    child = self.field(c,idx,child_group,stack)
                    if t in ("element", "any"):
                        particles.append(self.fields[child]["name"])
                elif t in ("complexType","simpleType","complexContent","simpleContent","extension","restriction","sequence","choice","all"):
                    walk(c,child_group,stack,particles,attributes_only,excluded_attributes,kind=="complexContent")
                elif t in ("attributeGroup","group"):
                    obj=self.resolve(c.get("ref"),c,[t]) if c.get("ref") else c
                    if obj in stack:raise ValueError("Рекурсивная группа в "+path)
                    group_label=child_group
                    if t=="group" and (c.get("minOccurs","1"),c.get("maxOccurs","1"))!=("1","1"):
                        group_label+=(" / " if group_label else "")+"group ["+c.get("minOccurs","1")+".."+c.get("maxOccurs","1")+"]"
                    walk(obj,group_label,stack+(obj,),particles,attributes_only,excluded_attributes)
                elif t=="union":
                    alternatives=[]
                    variants=[(q,None) for q in c.get("memberTypes","").split()]+[(None,n) for n in c.findall(X+"simpleType")]
                    if not variants:raise ValueError("Пустое объединение типов в "+path)
                    for q,obj in variants:
                        temporary=ET.Element(X+"element",dict(name="unionOption",**({"type":q} if q else {})))
                        self.owner[temporary]=self.owner[c]
                        self.prefixes[temporary]=self.prefixes[c]
                        self.context[temporary]=self.context[c]
                        if obj is not None:temporary.append(obj)
                        original=self.fields
                        try:
                            self.fields=[];self.field(temporary,chain=stack)
                            if len(self.fields)!=1:raise ValueError("Нескалярный вариант union в "+path)
                            option=self.fields[0]
                            alternatives.append({k:option[k] for k in ("type","base","facets","values","flags","typeNs","baseNs") if k in option}|({"alternatives":option["alternatives"]} if option.get("alternatives") else {}))
                        finally:self.fields=original
                    d["alternatives"]=alternatives
                elif t=="enumeration":
                    d["values"].append([c.get("value"),notes(c)])
                elif t in FACETS:
                    d["facets"].append([t,c.get("value")])
                else:
                    raise ValueError("Неподдерживаемая конструкция "+t+" в "+path)
        if src.get("type"):
            expand_type(src.get("type"),src,chain,content)
        else:
            walk(src,"",chain,content)
            if tag == "element" and not complex_type and src.find(X+"simpleType") is None:
                d["base"] = "xs:anyType"
                d["baseNs"] = XS
        if content:
            d["contentModel"] = content[0] if len(content) == 1 else ["sequence", *content]
        elif complex_type and not d["base"]:
            d["contentModel"] = ["empty"]
        if parent==-1:self.coalesce_choice_leaves()
        return idx

