"""Generate the offline SEDO 10110 browser reference from an SFR XSD archive."""
import argparse
import hashlib
import io
import json
import posixpath
import re
import sys
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from xml.etree import ElementTree as ET
from urllib.parse import parse_qs, urlsplit, unquote

XS = "http://www.w3.org/2001/XMLSchema"
X = "{" + XS + "}"
HERE = Path(__file__).resolve().parent
MAX_FIELDS = 50000
FACETS = set("length minLength maxLength pattern enumeration maxInclusive minInclusive maxExclusive minExclusive totalDigits fractionDigits whiteSpace".split())

BUILTIN_SIMPLE_TYPES = set('anySimpleType string boolean decimal float double duration dateTime time date gYearMonth gYear gMonthDay gDay gMonth hexBinary base64Binary anyURI QName NOTATION normalizedString token language NMTOKEN NMTOKENS Name NCName ID IDREF IDREFS ENTITY ENTITIES integer nonPositiveInteger negativeInteger long int short byte nonNegativeInteger unsignedLong unsignedInt unsignedShort unsignedByte positiveInteger'.split())
EXPANDED_NAME_TYPES = {'QName', 'NOTATION'}


def scalar_whitespace(builtin):
    return {'string': 'preserve', 'anySimpleType': 'preserve', 'normalizedString': 'replace'}.get(builtin, 'collapse')


def field_whitespace(field, *, before=None):
    explicit = next((value for key, value in reversed(field['facets'][:before]) if key == 'whiteSpace'), None)
    if explicit is not None:
        return explicit
    if field.get('baseNs') == XS:
        builtin = field['base'].rsplit(':', 1)[-1]
        if builtin in BUILTIN_SIMPLE_TYPES:
            return scalar_whitespace(builtin)
    return None


def occurrence(value):
    """Canonical lexical form of an XSD nonNegativeInteger occurrence."""
    value = str(value).strip()
    if value == 'unbounded': return value
    if not re.fullmatch(r'\+?[0-9]+', value):
        raise ValueError('Недопустимая кратность XSD: '+value)
    return str(int(value))


def inactive_particle(node):
    """A particle with zero maximum occurrences contributes no instance nodes."""
    maximum = node.get('maxOccurs')
    return maximum is not None and occurrence(maximum) == '0'


def check_dependency_bases(root, origin, *, edges=None):
    """A relative archive path cannot stand in for an xml:base-adjusted URI."""
    base = "{http://www.w3.org/XML/1998/namespace}base"
    references = {X+"import", X+"include", X+"redefine", X+"override", "{http://schemas.xmlsoap.org/wsdl/}import"}
    if not any(base in node.attrib for node in root.iter()):
        return
    if edges is None and root.tag == X + "schema":
        edges = set(root)
    pending = [(root, False)]
    while pending:
        node, inherited = pending.pop()
        active = inherited or bool(node.get(base))
        if active and node.tag in references and (edges is None or node in edges):
            raise ValueError("Не поддерживается xml:base для зависимости в " + origin)
        pending.extend((child, active) for child in node)

def import_basename(location):
    parsed=urlsplit(location)
    query=parse_qs(parsed.query)
    resource=query.get("xsd",[parsed.path if parsed.scheme else location])[0]
    if "xsd" in query and re.fullmatch(r"[A-Za-z0-9_-]+", resource):
        return "wsdl-query-" + hashlib.sha256(resource.encode("ascii")).hexdigest() + ".xsd"
    return posixpath.basename(unquote(resource).replace("\\","/"))

def import_metadata(root, digest):
    imports = [dict(location=edge.get("schemaLocation"), kind=local(edge), namespace=edge.get("namespace"))
               for edge in root if edge.tag in (X+"include", X+"import", X+"redefine")]
    return dict(sha256=digest, namespace=root.get("targetNamespace"),
                imports=imports, dependencies=[edge["location"] for edge in imports])


class SchemaFiles(dict):
    """Schema bytes plus exact fetched import edges, stored outside originals."""
    def __init__(self, *args, import_targets=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.import_targets = import_targets or {}


def archive_targets(archive):
    marker = b'SEDO-XSD-imports-1\n'
    if not archive.comment.startswith(marker): return {}
    data=json.loads(archive.comment[len(marker):])
    if not isinstance(data,dict) or any(not isinstance(edges,dict) for edges in data.values()):
        raise ValueError('Некорректные связи импорта XSD')
    for path,edges in data.items():
        if path not in archive.namelist() or any(not isinstance(loc,str) or target not in archive.namelist() for loc,target in edges.items()):
            raise ValueError('Не найден путь связи импорта XSD')
    return data


def write_archive_targets(archive, targets):
    if targets:
        comment=b'SEDO-XSD-imports-1\n'+json.dumps(targets,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')
        if len(comment)>65535: raise ValueError('Слишком много связей импорта XSD для архива')
        archive.comment=comment


_snapshot_match = ContextVar('snapshot_match', default=(False, None))


@contextmanager
def snapshot_matching(legacy, changes):
    token = _snapshot_match.set((legacy, changes))
    try:
        yield
    finally:
        _snapshot_match.reset(token)


def archive_import_path(origin, location):
    legacy, changes = _snapshot_match.get()
    old_path = posixpath.normpath(posixpath.join(posixpath.dirname(origin),location.replace("\\","/")))
    if legacy:
        return old_path
    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc or parsed.query:
        return None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(origin), unquote(parsed.path).replace("\\", "/")))
    if changes is not None and old_path != path:
        changes.add('archive_uri')
    return path


def _import_candidates(files,origin,location,namespace,kind):
    allowed={namespace or ""}
    if kind in ("include", "redefine"):allowed.add("")
    def matches(meta):return (kind=="legacy" and namespace is None) or (meta.get("namespace") or "") in allowed
    exact=files[origin].get('import_targets',{}).get(location)
    if exact is not None and exact not in files:
        raise ValueError('Не найден точный импорт '+location+' в '+origin)
    expected=exact or archive_import_path(origin,location)
    if expected in files:
        if not matches(files[expected]):raise ValueError("Несовпадение namespace импорта "+location)
        return [expected]
    candidates=[path for path,meta in files.items() if posixpath.basename(path)==import_basename(location)
                and not (kind=="legacy" and namespace is None) and matches(meta)]
    if not candidates:raise ValueError("Не найден импорт "+location+" в "+origin)
    return sorted(candidates)


def _edges(meta, effective):
    for index, location in enumerate(meta['dependencies']):
        if not location:raise ValueError("Импорт без пути")
        imports = meta.get('imports', [])
        reference = imports[index] if index < len(imports) else {}
        kind = reference.get('kind', 'import') if reference else 'legacy'
        namespace = effective if kind in ('include', 'redefine') else reference.get('namespace')
        yield location, namespace, kind


def resolve_import(files, origin, location, namespace=None, kind="import"):
    candidates = _import_candidates(files, origin, location, namespace, kind)
    if len(candidates) == 1:
        return candidates[0]
    # Compare corresponding edges from every possible starting directory. A
    # shared physical file needs no comparison. A cycle repeats a proven group.
    pending = [(tuple(candidates), namespace if kind in ('include', 'redefine') else None)]
    seen = set()
    while pending:
        paths, inherited = pending.pop()
        if len(paths) == 1 or (paths, inherited) in seen:
            continue
        seen.add((paths, inherited))
        first = files[paths[0]]
        if ('dependencies' not in first or any(files[path]['sha256'] != first['sha256'] for path in paths)):
            raise ValueError("Неоднозначный импорт " + location + " в " + origin)
        effective = first.get('namespace') or inherited or ''
        for edge, required, relation in _edges(first, effective):
            children = {target for path in paths for target in _import_candidates(files, path, edge, required, relation)}
            child_namespace = effective if relation in ('include', 'redefine') else None
            pending.append((tuple(sorted(children)), child_namespace))
    return candidates[0]

def dependency_set(schemas,entry):
    files={s["path"]:s for s in schemas};visited={};contexts=set()
    def visit(path,inherited_namespace=None):
        s=files[path];effective=s.get("namespace") or inherited_namespace or ""
        if (path,effective) in contexts:return
        contexts.add((path,effective));visited[path]=s["sha256"]
        for loc,namespace,kind in _edges(s,effective):
            visit(resolve_import(files,path,loc,namespace,kind),effective if kind=="include" else None)
    visit(entry)
    return sorted((posixpath.relpath(p,posixpath.dirname(entry)),h) for p,h in visited.items())


def included_roots(files, entry, *, chameleon_only=False):
    """Global roots contributed to an entry by includes, excluding imports."""
    result = []; seen = set()
    def visit(path, inherited=None):
        meta=files[path]; effective=meta.get('namespace') or inherited or ''
        if (path,effective) in seen: return
        seen.add((path,effective))
        if path == entry or not chameleon_only or not meta.get('namespace'): result.extend(meta.get('roots', []))
        for location,namespace,kind in _edges(meta,effective):
            if kind == 'include': visit(resolve_import(files,path,location,namespace,kind),effective)
    visit(entry)
    return result


def equivalent_closure(dependencies, entry):
    """The entry filename is packaging metadata. Retain the other resolved paths and bytes."""
    root = posixpath.basename(entry)
    return tuple(sorted(("" if path == root else path, digest) for path, digest in dependencies))

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
        self._enum_probes = {}
        self.substitution_heads = None
        self.expanded_fields = 0
        self.main = entry
        self.inventory = None
        self.load(archive, self.main)
        main_namespace = self.files[self.main][2]
        candidates = {path: import_metadata(doc[0], '') | {'roots': [node for node in doc[0] if node.tag == X+'element'], 'import_targets': archive_targets(archive).get(path,{})}
                      for (path, namespace), doc in self.documents.items() if namespace == main_namespace}
        self.root = next((node for node in included_roots(candidates,self.main) if node.get('name') == root_name), None)
        if self.root is None:
            raise ValueError("Корневой элемент " + root_name + " не найден.")

    def load(self, archive, path, inherited_namespace=None):
        if path in self.files:
            declared = self.files[path][0].get("targetNamespace")
            if (path, declared or inherited_namespace or "") in self.documents:
                return
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
        check_dependency_bases(root, path)
        declared = root.get("targetNamespace", "")
        target = declared or inherited_namespace or ""
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
                self.inventory = {}
                for name in archive.namelist():
                    if name.lower().endswith(".xsd"):
                        raw = archive.read(name)
                        self.inventory[name] = import_metadata(ET.fromstring(raw), hashlib.sha256(raw).hexdigest())
                        if name in archive_targets(archive): self.inventory[name]['import_targets']=archive_targets(archive)[name]
            kind=local(imp)
            imported=resolve_import(self.inventory,path,location,imp.get("namespace") if kind=="import" else target,kind)
            self.load(archive,imported,target if kind=="include" else None)

    def qname_namespace(self, qname, source):
        prefix, _, _name = qname.rpartition(":")
        if prefix == 'xml':
            return 'http://www.w3.org/XML/1998/namespace'
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
            if ('simpleType' in kinds and name in BUILTIN_SIMPLE_TYPES
                    or 'complexType' in kinds and name == 'anyType'):
                return None
            raise ValueError('Неизвестный компонент XSD или неверный вид: '+qname+' в '+self.owner[source])
        for k in kinds:
            if (uri,k,name) in self.symbol:
                return self.symbol[(uri,k,name)]
        raise ValueError("Не удалось раскрыть тип или ссылку "+qname+" в "+self.owner[source])

    def _value_qname(self, node, value):
        prefix, _, name = value.strip(" \t\r\n").rpartition(":")
        if prefix == "xml":
            return ["http://www.w3.org/XML/1998/namespace", name]
        scope = self._value_scope(node)
        if prefix and prefix not in scope:
            owner = self.owner.get(node)
            raise ValueError("Необъявленный префикс значения QName " + prefix + (" в " + owner if owner else ""))
        return [scope.get(prefix or None, ""), name]

    def _value_scope(self, node):
        if hasattr(node, 'nsmap'):
            return node.nsmap
        return {prefix or None:uri for prefix,uri in self.prefixes[node].items() if uri}

    @staticmethod
    def _type_child(node, kind):
        return next((child for child in node if child.tag in (X+kind, kind)), None)

    @staticmethod
    def _union_members(union):
        for qname in union.get("memberTypes", "").split():
            yield qname, union
        for member in union.findall(X+"simpleType"):
            yield None, member

    def _enum_member_type(self, qname, source):
        if qname is None:
            return source
        if hasattr(source, 'nsmap'):
            prefix, _, name = qname.rpartition(':')
            namespace = source.nsmap.get(prefix or None, '')
            if prefix and not namespace:
                raise ValueError('Необъявленный префикс типа '+qname)
            if namespace == XS and name in BUILTIN_SIMPLE_TYPES:
                return name
            member = self.symbol.get((namespace, 'simpleType', name))
            if member is None:
                raise ValueError('Не удалось раскрыть тип '+qname)
            return member
        member = self.resolve(qname, source, ["simpleType"])
        return qname.rsplit(":", 1)[-1] if member is None else member

    def _enum_probe(self, etree, member):
        if member in self._enum_probes:
            return self._enum_probes[member]
        root = etree.Element(X+"schema", nsmap={"xs": XS})
        names = {}

        def type_name(selected):
            if isinstance(selected, str):
                if selected == 'NOTATION':
                    raise ValueError('Тип NOTATION требует перечисления')
                return "xs:"+selected
            if selected not in names:
                names[selected] = "Type"+str(len(names))
                simple = etree.SubElement(root, X+"simpleType", name=names[selected])
                append_content(simple, selected)
            return names[selected]

        def append_content(simple, selected):
            if selected is None:
                raise ValueError("Отсутствует тип значения")
            restriction = self._type_child(selected, 'restriction')
            if restriction is not None:
                target = etree.SubElement(simple, X+"restriction")
                base = restriction.get("base")
                if base:
                    member = self._enum_member_type(base, restriction)
                    if member == 'NOTATION':
                        # For a valid NOTATION restriction the listed values are
                        # declared expanded names. QName implements that same
                        # membership test without importing notation schemas.
                        enums = [facet for facet in restriction if local(facet) == 'enumeration']
                        if not enums:
                            raise ValueError('Тип NOTATION требует перечисления')
                        for facet in enums:
                            namespace, name = self._value_qname(facet, facet.get('value', ''))
                            if (namespace, 'notation', name) not in self.symbol:
                                raise ValueError('Не установлено объявление NOTATION')
                        target.set('base', 'xs:QName')
                    else:
                        target.set("base", type_name(member))
                else:
                    append_content(etree.SubElement(target, X+"simpleType"), self._type_child(restriction, 'simpleType'))
                for facet in restriction:
                    if local(facet) in FACETS:
                        etree.SubElement(target, X+local(facet), attrib=dict(facet.attrib),
                                         nsmap=self._value_scope(facet))
                return
            union = self._type_child(selected, 'union')
            if union is None:
                raise ValueError("Неподдерживаемый тип перечисления union")
            target = etree.SubElement(simple, X+"union")
            named = []
            for qname, source in self._union_members(union):
                if qname:
                    named.append(type_name(self._enum_member_type(qname, source)))
                else:
                    append_content(etree.SubElement(target, X+"simpleType"), source)
            if named:
                target.set("memberTypes", " ".join(named))

        try:
            etree.SubElement(root, X+"element", name="probe", type=type_name(member))
            probe = etree.XMLSchema(root)
        except (ValueError, etree.XMLSchemaParseError):
            # An unavailable member cannot be skipped without changing union order.
            probe = None
        self._enum_probes[member] = probe
        return probe

    def _typed_value(self, node, lexical, *, union=None, member=None):
        from lxml import etree

        scope = self._value_scope(node)
        value = etree.Element("probe", nsmap=scope)
        value.text = lexical
        prefix, sep, name = lexical.strip(' \t\r\n').partition(':')
        unbound = hasattr(node, 'nsmap') and sep and prefix != 'xml' and prefix not in scope
        if unbound:
            try:
                etree.QName('urn:probe', prefix)
                etree.QName('urn:probe', name)
            except ValueError:
                unbound = False

        def uses_names(selected):
            if isinstance(selected, str):
                return selected in EXPANDED_NAME_TYPES
            options = self._type_child(selected, 'union')
            if options is not None:
                return any(uses_names(self._enum_member_type(qname, source))
                           for qname, source in self._union_members(options))
            restriction = self._type_child(selected, 'restriction')
            if restriction is None:
                return False
            base = restriction.get('base')
            source = restriction if base else self._type_child(restriction, 'simpleType')
            return uses_names(self._enum_member_type(base, source))

        def choose(options):
            for qname, source in self._union_members(options):
                member = self._enum_member_type(qname, source)
                probe = self._enum_probe(etree, member)
                if probe is None or unbound and uses_names(member):
                    return None
                if probe.validate(value):
                    return atomic(member)
            return None

        def atomic(member):
            if isinstance(member, str):
                return member, scalar_whitespace(member)
            union = self._type_child(member, 'union')
            if union is not None:
                return choose(union)
            restriction = self._type_child(member, 'restriction')
            base = restriction.get("base")
            source = restriction if base else self._type_child(restriction, 'simpleType')
            selected = atomic(self._enum_member_type(base, source))
            whitespace = self._type_child(restriction, 'whiteSpace')
            return (selected[0], whitespace.get("value")) if selected and whitespace is not None else selected

        if union is not None:
            selected = choose(union)
        else:
            probe = self._enum_probe(etree, member)
            selected = atomic(member) if probe is not None and not (unbound and uses_names(member)) and probe.validate(value) else None
        return list(selected) if selected else None

    def quoted_value_type(self, declaration, lexical, *, owner=None):
        """Resolve a quoted scalar type using the quote's own namespace bindings."""
        node = declaration
        try:
            if node.get('ref'):
                prefix, _, name = node.get('ref').rpartition(':')
                namespace = self._value_scope(node).get(prefix or None, '')
                if prefix and not namespace:
                    return None
                node = self.symbol.get((namespace, local(node), name))
            while node is not None:
                qname = node.get('type') or node.get('base')
                if qname or local(node) == 'simpleType':
                    member = self._enum_member_type(qname, node)
                    return self._typed_value(owner if owner is not None else declaration, lexical, member=member)
                node = next((child for child in node if local(child) in
                             ('simpleType', 'complexType', 'simpleContent', 'restriction', 'extension')), None)
        except (ValueError, RecursionError):
            return None

    def instance_namespace(self, declaration):
        schema, _, target = self.context.get(declaration,self.files[self.owner[declaration]])
        kind = local(declaration)
        if self.symbol.get((target, kind, declaration.get("name"))) is declaration:
            return target
        default = "attributeFormDefault" if kind in ("attribute", "anyAttribute") else "elementFormDefault"
        return target if declaration.get("form", schema.get(default, "unqualified")) == "qualified" else ""

    def check_substitution_rules(self, node):
        """Do not publish an incomplete tree for reachable substitution rules."""
        kind = local(node)
        if kind not in ('element', 'complexType'):
            return
        document, _, _ = self.context.get(node, self.files[self.owner[node]])
        key = 'block' if 'block' in node.attrib else 'blockDefault'
        value = node.get('block', document.get('blockDefault', ''))
        if kind == 'element' and node.get('substitutionGroup', '').strip():
            key, value = 'substitutionGroup', node.get('substitutionGroup')
        if not value.strip() and kind == 'element':
            # Members are not ordinary child references. Reaching a head also
            # reaches its allowed substitutes, even if they are otherwise unused.
            if self.substitution_heads is None:
                self.substitution_heads = {
                    (self.qname_namespace(member.get('substitutionGroup'), member),
                     member.get('substitutionGroup').rsplit(':', 1)[-1])
                    for member in self.symbol.values()
                    if local(member) == 'element' and member.get('substitutionGroup')}
            identity = (self.instance_namespace(node), node.get('name'))
            if (identity in self.substitution_heads
                    and self.symbol.get((identity[0], 'element', identity[1])) is node):
                key, value = 'substitutionGroup', node.get('name')
        if value.strip():
            raise ValueError('Требуется проверка: неподдерживаемое правило XSD '
                             + key + ' в ' + self.owner[node])

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

    def field(self, decl, parent=-1, group="", chain=(), *, coalesce_choices=True, skip_inactive_particles=False):
        if self.expanded_fields >= MAX_FIELDS:
            raise ValueError("Слишком большой каталог.")
        self.expanded_fields += 1
        tag = local(decl)
        src = decl
        if decl.get("ref"):
            src = self.resolve(decl.get("ref"),decl,[tag])
        self.check_substitution_rules(src)
        name = ("@" if tag in ("attribute","anyAttribute") else "") + src.get("name","*")
        path = (self.fields[parent]["path"]+"/" if parent >= 0 else "")+name
        idx = len(self.fields)
        d = dict(name=name,path=path,parent=parent,group=group,doc=notes(src),kind=tag,
                 min=occurrence(decl.get("minOccurs","0" if tag in ("attribute","anyAttribute") and decl.get("use")!="required" else "1")),
                 max=occurrence(decl.get("maxOccurs","unbounded" if tag=="anyAttribute" else "1")),type=src.get("type",""),base="",facets=[],values=[],children=[],
                 ns=self.instance_namespace(src),
                 file=self.owner[src].split("/")[-1],flags={k:v for k,v in src.attrib.items() if k in ("fixed","default","nillable","namespace","processContents")})
        if tag in ("any", "anyAttribute"):
            # A wildcard constrains a set of namespaces and has no instance QName.
            target = self.context[src][2] or ""
            allowed = src.get("namespace", "##any").split()
            if allowed == ["##other"] and target:
                constraint = f"##other (targetNamespace={target})"
            else:
                constraint = " ".join(sorted({(target or "##local") if n == "##targetNamespace" else n
                                               for n in allowed}))
            d["ns"] = ""
            d["wildcardNamespace"] = constraint
        if tag == "element" and src.get("abstract") in ("true", "1"):
            d["flags"]["abstract"] = "true"
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
        content_bounds = []
        pattern_groups = []
        enum_nodes = []
        enum_union = None
        enum_whitespace = None
        complex_type = False
        def expand_type(q,elem,stack,particles,bounded_particles,restricted_base=None,excluded_attributes=frozenset(),kinds=None):
            if elem is src and q == src.get("type"):
                d["typeNs"] = self.qname_namespace(q,elem)
            obj = self.resolve(q,elem,kinds or (["simpleType"] if tag == 'attribute' else ["simpleType","complexType"]))
            if obj is None:
                if restricted_base != "complexContent":
                    d["base"]=q
                    d["baseNs"]=self.qname_namespace(q,elem)
            else:
                if obj in stack:
                    raise ValueError("Рекурсивный тип в "+path)
                d.setdefault("typeFile", self.owner[obj].split("/")[-1])
                walk(obj, "", stack+(obj,),particles,bounded_particles,restricted_base=restricted_base,excluded_attributes=excluded_attributes)
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
        def walk(e,group,stack,particles,bounded_particles,restricted_base=None,excluded_attributes=frozenset(),parent_kind=None):
            nonlocal nodegroups, complex_type, enum_union, enum_whitespace
            kind = local(e)
            replace_enum = kind == "restriction"
            if kind == 'complexType':
                self.check_substitution_rules(e)
            if kind == "complexType" and not complex_type:
                complex_type = True
                if e.get("abstract") in ("true", "1"):
                    d["flags"]["typeAbstract"] = "true"
                complex_content = e.find(X+"complexContent")
                mixed = (complex_content.get("mixed", e.get("mixed", "false"))
                         if complex_content is not None else e.get("mixed", "false"))
                if mixed in ("true", "1"):
                    d["flags"]["mixed"] = "true"
                else:
                    d["flags"].pop("mixed", None)
            if restricted_base and kind in ("choice","sequence","all","group"):return
            if kind in ("choice", "sequence", "all"):
                model = [kind]
                particles.append(model)
                particles = model
                bounded = dict(kind=kind,min=occurrence(e.get('minOccurs','1')),max=occurrence(e.get('maxOccurs','1')),children=[])
                bounded_particles.append(bounded)
                bounded_particles = bounded['children']
            if local(e)=="choice":
                nodegroups+=1
                choice_id=nodegroups
                option=0
            if local(e) in ("choice","sequence","all") and (occurrence(e.get("minOccurs","1")),occurrence(e.get("maxOccurs","1")))!=("1","1"):
                group=(group+" / " if group else "")+local(e)+" ["+occurrence(e.get("minOccurs","1"))+".."+occurrence(e.get("maxOccurs","1"))+"]"
            if local(e) in ("restriction","extension") and e.get("base"):
                base_mode = restricted_base
                inherited_exclusions = excluded_attributes
                if kind == "restriction" and parent_kind in ("complexContent", "simpleContent"):
                    base_mode = restricted_base or parent_kind
                    inherited_exclusions = excluded_attributes | restriction_attributes(e)
                kinds = ['simpleType'] if parent_kind == 'simpleType' else ['complexType'] if parent_kind == 'complexContent' else None
                expand_type(e.get("base"),e,stack,particles,bounded_particles,base_mode,inherited_exclusions,kinds)
            inherited_facets = len(d['facets'])
            if kind == "restriction":
                patterns = [pattern.get("value") for pattern in e.findall(X+"pattern")]
                if patterns:
                    pattern_groups.append(patterns)
            for c in e:
                t=local(c)
                if t=="annotation" or (skip_inactive_particles and inactive_particle(c)):
                    continue
                child_group=group
                if local(e)=="choice":
                    option+=1
                    label=f"Выбор {choice_id}, вариант {option}"
                    child_group=f"{group} / {label}" if group else label
                if t in ("element","attribute","any","anyAttribute"):
                    # Both content restrictions inherit named attribute uses only.
                    # A simple-content restriction also keeps its scalar base and facets.
                    if restricted_base and t != "attribute":continue
                    if t=="attribute" and (c.get("use")=="prohibited" or attribute_key(c) in excluded_attributes):continue
                    child = self.field(c,idx,child_group,stack,skip_inactive_particles=skip_inactive_particles)
                    if t in ("element", "any"):
                        particles.append(self.fields[child]["name"])
                        bounded_particles.append(self.fields[child]["name"])
                elif t in ("complexType","simpleType","complexContent","simpleContent","extension","restriction","sequence","choice","all"):
                    walk(c,child_group,stack,particles,bounded_particles,restricted_base,excluded_attributes,kind)
                    if kind == 'restriction' and t == 'simpleType':
                        inherited_facets = len(d['facets'])
                elif t in ("attributeGroup","group"):
                    obj=self.resolve(c.get("ref"),c,[t]) if c.get("ref") else c
                    if obj in stack:raise ValueError("Рекурсивная группа в "+path)
                    group_label=child_group
                    if t=="group" and (occurrence(c.get("minOccurs","1")),occurrence(c.get("maxOccurs","1")))!=("1","1"):
                        group_label+=(" / " if group_label else "")+"group ["+occurrence(c.get("minOccurs","1"))+".."+occurrence(c.get("maxOccurs","1"))+"]"
                    group_particles=bounded_particles
                    if t=='group':
                        bounded=dict(kind='group',min=occurrence(c.get('minOccurs','1')),max=occurrence(c.get('maxOccurs','1')),children=[])
                        bounded_particles.append(bounded);group_particles=bounded['children']
                    walk(obj,group_label,stack+(obj,),particles,group_particles,restricted_base,excluded_attributes)
                elif t=="union":
                    enum_union=c
                    alternatives=[]
                    variants=list(self._union_members(c))
                    if not variants:raise ValueError("Пустое объединение типов в "+path)
                    for q,source in variants:
                        if q:self.resolve(q,c,['simpleType'])
                        temporary=ET.Element(X+"element",dict(name="unionOption",**({"type":q} if q else {})))
                        self.owner[temporary]=self.owner[c]
                        self.prefixes[temporary]=self.prefixes[c]
                        self.context[temporary]=self.context[c]
                        if q is None:temporary.append(source)
                        original=self.fields
                        try:
                            self.fields=[];self.field(temporary,chain=stack)
                            if len(self.fields)!=1:raise ValueError("Нескалярный вариант union в "+path)
                            option=self.fields[0]
                            alternatives.append({k:option[k] for k in ("type","base","facets","values","flags","typeNs","baseNs","patternGroups","alternatives","valueQNames","flagQNames","unionValueTypes","enumWhiteSpace") if k in option})
                        finally:self.fields=original
                    d["alternatives"]=alternatives
                elif t=="enumeration":
                    if replace_enum:
                        d["values"].clear()
                        enum_nodes.clear()
                        # A facet value is parsed against the base of its
                        # restriction, independently of later whitespace rules.
                        enum_whitespace = field_whitespace(d, before=inherited_facets)
                        replace_enum=False
                    d["values"].append([c.get("value"),notes(c)])
                    enum_nodes.append(c)
                elif t in FACETS:
                    d["facets"].append([t,c.get("value")])
                else:
                    raise ValueError("Неподдерживаемая конструкция "+t+" в "+path)
        if src.get("type"):
            # A named type supplies the content, but the element itself can
            # carry identity constraints that are not represented by this tree.
            for child in src:
                if child.tag != X+"annotation":
                    raise ValueError("Неподдерживаемая конструкция "+local(child)+" в "+path)
            expand_type(src.get("type"),src,chain,content,content_bounds)
        else:
            walk(src,"",chain,content,content_bounds)
            if tag == "element" and not complex_type and src.find(X+"simpleType") is None:
                d["base"] = "xs:anyType"
                d["baseNs"] = XS
        value_flags = {key: (decl if key in decl.attrib else src, d["flags"][key])
                       for key in ("fixed", "default") if key in d["flags"]}
        if d.get("baseNs") == XS and d["base"].rsplit(":", 1)[-1] in EXPANDED_NAME_TYPES:
            if enum_nodes:
                d["valueQNames"] = [self._value_qname(node,node.get("value")) for node in enum_nodes]
            if value_flags:
                d["flagQNames"] = {key: self._value_qname(node, lexical)
                                  for key, (node, lexical) in value_flags.items()}
        elif enum_union is not None:
            if enum_nodes:
                selected = [self._typed_value(node, node.get("value"), union=enum_union) for node in enum_nodes]
                d["unionValueTypes"] = selected
                names = [self._value_qname(node,node.get("value")) if member and member[0] in EXPANDED_NAME_TYPES else None
                         for node,member in zip(enum_nodes,selected)]
                if any(names):
                    d["valueQNames"] = names
            if value_flags:
                selections = d["unionFlagTypes"] = {key: self._typed_value(node, lexical, union=enum_union)
                                                   for key, (node, lexical) in value_flags.items()}
                names = {key: self._value_qname(node, lexical) for key, (node, lexical) in value_flags.items()
                         if selections[key] and selections[key][0] in EXPANDED_NAME_TYPES}
                if names:
                    d["flagQNames"] = names
        if enum_nodes and enum_union is None and enum_whitespace is not None and enum_whitespace != field_whitespace(d):
            d['enumWhiteSpace'] = enum_whitespace
        if content:
            d["contentModel"] = content[0] if len(content) == 1 else ["sequence", *content]
            d['contentModelBounds'] = content_bounds[0] if len(content_bounds)==1 else dict(kind='sequence',min='1',max='1',children=content_bounds)
        elif complex_type and not d["base"]:
            d["contentModel"] = ["empty"]
        if sum(map(len, pattern_groups)) > 1:
            d["patternGroups"] = pattern_groups
        if parent==-1 and coalesce_choices:self.coalesce_choice_leaves()
        return idx

