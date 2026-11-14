"""Compare individual Word specification cards with the schema of the same edition."""
import re
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from module_xsd import W, text
from schema import FACETS, Schema, X, XS, local


HEADING = re.compile(r"^Атрибут(?:ы элемента|ы (?:комплексного )?типа)?\s+(\S+)$", re.I)
FACET_NAMES = {
    "Минимальное число символов": "minLength", "Максимальное число символов": "maxLength",
    "Число символов": "length", "Паттерн строки": "pattern",
    "Список допустимых значений": "enumeration", "Количество допустимых цифр": "totalDigits",
    "Максимальное число знаков после десятичной запятой": "fractionDigits",
    "Нижняя граница (значение должно быть больше или равно указанному)": "minInclusive",
    "Верхняя граница (значение должно быть меньше или равно указанному)": "maxInclusive",
}
PROPERTIES = {
    "minOcc": "min", "maxOcc": "max", "use": "use", "fixed": "fixed", "nillable": "nillable",
    "Минимальное число повторений элемента": "min", "Максимальное число повторений элемента": "max",
}
BOUNDS = {"minLength": max, "maxLength": min, "minInclusive": max, "maxInclusive": min,
          "minExclusive": max, "maxExclusive": min, "totalDigits": min, "fractionDigits": min}


def cards(body):
    heading = None
    table_number = 0
    for node in body:
        if node.tag == W + "p" and text(node):
            heading = HEADING.fullmatch(text(node))
        if node.tag != W + "tbl":
            continue
        table_number += 1
        if not heading:
            continue
        cells = {}
        for row in node.findall(W + "tr"):
            pair = row.findall(W + "tc")
            if len(pair) == 2:
                cells[text(pair[0])] = pair[1]
        yield table_number, heading[0], heading[1], cells
        heading = None


def subrows(cell):
    return [[text(c) for c in row.findall(W + "tc")]
            for row in cell.findall(W + "tbl/" + W + "tr")] if cell is not None else []


def fragment(cell):
    if cell is None:
        return None
    # Adjacent Word runs are contiguous, while paragraph boundaries separate XML tokens.
    source = " ".join(text(p) for p in cell.iter(W + "p"))
    try:
        wrapper = ET.fromstring(f'<wrapper xmlns:xs="{XS}" xmlns:xsd="{XS}">{source}</wrapper>')
    except ET.ParseError:
        return None
    return wrapper[0] if len(wrapper) == 1 and wrapper[0].tag.startswith(X) else None


def declaration_index(schema):
    index = defaultdict(list)

    def visit(node, path, file):
        name = node.get("name") or node.get("ref", "").split(":")[-1]
        if name:
            part = "@" + name if node.tag == X + "attribute" else name
            path = f"{path}/{part}" if path else part
            index[(path, local(node))].append((file, node))
        for child in node:
            if child.tag != X + "annotation":
                visit(child, path, file)

    for file, (root, _, _) in schema.files.items():
        visit(root, "", file)
    return index


def field_index(fields):
    """Also index paths relative to a named type, as used in Word headings."""
    index = defaultdict(list)
    for field in fields:
        path = field["path"]
        index[path].append(field)
        parent = field["parent"]
        while parent >= 0:
            ancestor = fields[parent]
            if ancestor["type"]:
                alias = ancestor["type"].split(":")[-1] + path[len(ancestor["path"]):]
                index[alias].append(field)
            parent = ancestor["parent"]
    return index


def local_rule(node):
    """Local datatype and facets, excluding declarations of descendant fields."""
    if node.get("type"):
        return node.get("type").strip(), ()
    for child in node:
        if child.tag == X + "simpleType" or node.tag in (X + "simpleType", X + "complexType"):
            restriction = child if child.tag == X + "restriction" else child.find(X + "restriction")
            if restriction is not None:
                return restriction.get("base", "").strip(), tuple(sorted(
                    (local(c), c.get("value", "")) for c in restriction if local(c) in FACETS))
    if node.tag == X + "element" and node.find(X + "complexType") is None and not node.get("ref"):
        return "xs:anyType", ()
    return "", ()


def rule_text(rule):
    base, facets = rule
    return " ".join([base] + [f"{key}={value}" for key, value in facets])


def render_facets(pairs):
    return ", ".join(f"{key}={value}" for key, value in sorted(pairs))


def effective_facets(field):
    facets = set(map(tuple, field["facets"])) | {("enumeration", value) for value, _ in field["values"]}
    for key, select in BOUNDS.items():
        bounds = {pair for pair in facets if pair[0] == key}
        if len(bounds) > 1:
            try:
                effective = select(bounds, key=lambda pair: Decimal(pair[1]))
            except InvalidOperation:
                continue  # Bounds on dates and other non-numeric types keep their lexical forms.
            facets -= bounds - {effective}
    return facets


def unprefixed(qname):
    return qname.rsplit(":", 1)[-1]


def model(node):
    """Summarise immediate particles, preserving group bounds and choice branches."""
    tag = local(node)
    if tag in ("element", "any", "group"):
        name = node.get("name") or node.get("ref") or "xs:any"
        if tag == "any":
            name += f'({node.get("namespace", "##any")}, {node.get("processContents", "strict")})'
    elif tag in ("sequence", "all", "choice"):
        children = [model(c) for c in node if local(c) in ("sequence", "all", "choice", "group", "any", "element")]
        name = f'{tag}({", ".join(children)})'
    else:
        for child in node:
            if local(child) in ("complexType", "complexContent", "extension", "restriction"):
                nested = model(child)
                if nested:
                    return nested
            if local(child) in ("sequence", "all", "choice", "group"):
                return model(child)
        return ""
    bounds = node.get("minOccurs", "1"), node.get("maxOccurs", "1")
    return name if bounds == ("1", "1") else f"{name}[{bounds[0]}..{bounds[1]}]"


def content_model(declaration):
    if declaration.tag == X + "element":
        declaration = declaration.find(X + "complexType")
    return model(declaration) if declaration is not None else ""


def check_card(cells, field, children, embedded, source, report):
    labels = {key: text(cell) for key, cell in cells.items()}
    rule = local_rule(source) if source is not None else (field["type"] or field["base"], ())
    display_type = labels.get("Тип", "")
    stated_type = re.sub(r"^(?:restriction of|extension of|Ограничение типа|Расширение типа)\s+", "", display_type)
    if stated_type and rule[0] and unprefixed(stated_type) != unprefixed(rule[0]):
        report("Имя типа", "Тип", display_type, rule[0], "По названию из таблицы будет выбран другой тип.")
    if embedded is not None and source is not None:
        expected = local_rule(embedded)
        # Compare names without prefixes only for display labels. Both embedded and
        # source declarations normally use the namespace prefixes of the source file.
        if expected != rule and (expected[0] or rule[0]):
            report("Объявление типа", "Схема", rule_text(expected) or "Комплексное содержимое",
                   rule_text(rule) or "Комплексное содержимое", "Объявления задают разные правила проверки значения.")
        for key, default in (("name", None), ("ref", None), ("minOccurs", "1"), ("maxOccurs", "1"), ("use", "optional"),
                             ("fixed", None), ("default", None), ("nillable", "false")):
            a, b = embedded.get(key, default), source.get(key, default)
            if a != b:
                report("Свойство объявления", "Схема", f"{key}={a if a is not None else 'не задано'}",
                       f"{key}={b if b is not None else 'не задано'}", "Правила проверки поля различаются.")
    if field is None:
        return

    actual_props = dict(field.get("flags", {}), min=field["min"], max=field["max"],
                        use="required" if field["min"] == "1" else "optional")
    actual_props.setdefault("nillable", "false")
    for row in subrows(cells.get("Свойства")):
        if len(row) < 2 or row[0] not in PROPERTIES:
            continue
        key = PROPERTIES[row[0]]
        expected = "unbounded" if row[1] == "Неограниченное число раз" else row[1]
        actual = actual_props.get(key)
        if expected != actual:
            report("Свойство поля", "Свойства", f"{row[0]} = {row[1]}", f"{key} = {actual or 'не задано'}",
                   "Таблица и схема задают разные требования к этому полю.")

    facets = effective_facets(field)
    facet_rows = subrows(cells.get("Ограничения"))
    stated = {(FACET_NAMES.get(row[0], row[0]), row[1]) for row in facet_rows if len(row) >= 2}
    stated = {(k, v) for k, v in stated if k in FACETS}
    # Additional inherited facets may be redundant (e.g. minInclusive 2000 in a
    # base type narrowed to 2001). Only assert disagreements supported by a row.
    missing = stated - facets
    extra_enums = {pair for pair in facets - stated if pair[0] == "enumeration"}
    if missing or (extra_enums and any(key == "enumeration" for key, _ in stated)):
        affected = {key for key, _ in missing | extra_enums}
        report("Ограничения значения", "Ограничения", render_facets({p for p in stated if p[0] in affected}),
               render_facets({p for p in facets if p[0] in affected}) or "Ограничение данного вида отсутствует.",
               "Допустимые форматы или значения поля расходятся.")

    children_mismatch = False
    if "Дочерние элементы" in labels:
        listed = labels["Дочерние элементы"].replace(",", " ").split()
        names = [c["name"] for c in children if not c["name"].startswith("@")]
        actual = list(dict.fromkeys(n for n in names if n != "*"))
        if list(dict.fromkeys(listed)) != actual:
            children_mismatch = True
            model = ["xs:any" if n == "*" else n for n in names]
            report("Состав и порядок элементов", "Дочерние элементы", " ".join(listed),
                   " ".join(model) or "Дочерних элементов нет.",
                   "XML по списку полей DOCX может нарушить модель содержимого XSD.")
    if not children_mismatch and embedded is not None and source is not None:
        documented_model = content_model(embedded)
        source_model = content_model(source)
        if documented_model != source_model:
            report("Модель содержимого", "Схема", documented_model or "Локальная группа не задана.",
                   source_model or "Локальная группа не задана.",
                   "Состав, группировка или кратность дочерних элементов расходятся.")

    attributes = {c["name"][1:]: c for c in children if c["name"].startswith("@")}
    attr_rows = subrows(cells.get("Атрибуты"))[1:]
    for row in attr_rows:
        if len(row) < 5:
            continue
        name, label, use, default, fixed = row[:5]
        attr = attributes.get(name)
        if attr is None:
            report("Состав атрибутов", "Атрибуты", name, "Именованный атрибут отсутствует.",
                   "Схема не объявляет атрибут из таблицы.")
            continue
        expected = [unprefixed(label), use or "optional", default, fixed]
        flags = attr["flags"]
        observed = [unprefixed(attr["type"] or attr["base"]), "required" if attr["min"] == "1" else "optional",
                    flags.get("default", ""), flags.get("fixed", "")]
        if expected != observed:
            report("Свойства атрибута", "Атрибуты", name + " " + " ".join(expected).strip(),
                   name + " " + " ".join(observed).strip(), "Описания типа или использования атрибута расходятся.")
    if attr_rows and all(len(row) >= 5 for row in attr_rows):
        omitted = set(attributes) - {row[0] for row in attr_rows} - {"*"}
        if omitted:
            report("Состав атрибутов", "Атрибуты", "Отсутствуют строки для " + ", ".join(sorted(omitted)),
                   "Объявлены атрибуты " + ", ".join(sorted(omitted)), "Таблица не перечисляет все атрибуты.")


def expanded(schema, source):
    """Use the catalog's resolver for a field or a stand-alone named type."""
    declaration = source
    if local(source) in ("complexType", "simpleType"):
        declaration = ET.Element(X + "element", {"name": source.get("name", "")})
        declaration.append(source)
        schema.owner[declaration] = schema.owner[source]
    schema.fields = []
    schema.field(declaration)
    field = schema.fields[0]
    return field, [schema.fields[i] for i in field["children"]]


def audit(revision, body, fields, *, archive_path=None):
    """Return the number of comparable Word cards and their current differences.

    Passing the edition's ZIP enables declaration-level and unused-type checks.
    Without it only cards resolvable against the expanded field tree are compared.
    """
    schema = None
    declarations = {}
    if archive_path is not None:
        with zipfile.ZipFile(archive_path) as archive:
            schema = Schema(archive, revision["schema_path"], revision["root"])
        declarations = declaration_index(schema)
    by_path = field_index(fields)
    findings = []
    checked = 0
    for number, title, path, cells in cards(body):
        embedded = fragment(cells.get("Схема"))
        kind = local(embedded) if embedded is not None else ("attribute" if "/@" in path else "element")
        candidates = declarations.get((path, kind), [])
        namespace = text(cells["Пространство имен"]) if "Пространство имен" in cells else None
        if namespace and schema and len(candidates) > 1:
            candidates = [(f, n) for f, n in candidates if schema.files[f][2] == namespace]
        # Several alternatives in a choice can share the same field path. Avoid
        # attributing a difference to an arbitrary declaration from another branch.
        if len(candidates) > 1:
            main = [(f, n) for f, n in candidates if f == revision["schema_path"]]
            if main:
                candidates = main
        if len(candidates) > 1:
            continue
        field_candidates = by_path.get(path, [])
        field = field_candidates[0] if field_candidates else None
        source_file, source = candidates[0] if candidates else (revision.get("schema_path", ""), None)
        if source is None and field is None and (schema is None or embedded is None):
            continue
        checked += 1
        def report(kind, row, spec, xsd, impact):
            findings.append(dict(kind=kind, location=f"{title} · таблица {number} · {row}",
                                 spec=spec, xlocation=f"{source_file} — {path}", xsd=xsd, impact=impact))
        if source is None and field is None:
            report("Отсутствующее объявление", "Схема", f'{kind} name="{embedded.get("name", path)}"',
                   "Такого объявления по указанному пути в схеме и её зависимостях нет.",
                   "Описанный в документе тип или поле нельзя найти по указанному пути.")
            continue
        children = [fields[i] for i in field["children"]] if field is not None else []
        if source is not None:
            field, children = expanded(schema, source)
        check_card(cells, field, children, embedded, source, report)
    return checked, findings
