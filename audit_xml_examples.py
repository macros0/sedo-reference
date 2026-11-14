"""Validate DOCX examples against the application schema of the same SEDO revision."""
import base64
import binascii
import hashlib
import re
import zipfile

from lxml import etree

from module_xsd import MAX_FILE, MAX_FILES, MAX_TOTAL, W, word_text
from schema import X, resolve_import


SECTION = re.compile(r"Тип сообщения\s+(?:СЭДО\s*)?(?:№\s*)?(\d+)\s*[:–—-]\s*\S", re.I)
EXAMPLE = re.compile(r"Пример(?:\s+(?:XML[- ]документа|(?:стр?уктуры\s+)?(?:запроса|ответа(?: на запрос)?)|сообщения|шаблона))?[.:]?", re.I)
NEXT_SECTION = re.compile(r"(?:XSD[- ]схема|Приложение(?:\s|$)|Атрибутивный\s*/\s*элементный|Атрибуты (?:типов|элемента)|Описание$|Структура$)", re.I)
OPEN_TAG = re.compile(r"<([A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?)(?=[\s/>]|$)")
MAX_EXAMPLE = 2 * 1024 * 1024
SOAP = {"http://schemas.xmlsoap.org/soap/envelope/", "http://www.w3.org/2003/05/soap-envelope"}


class _ArchiveResolver(etree.Resolver):
    def __init__(self, resources):
        self.resources = resources

    def resolve(self, url, public_id, context):
        if url not in self.resources:
            raise ValueError("Ссылка ведёт за пределы выбранных схем архива.")
        return self.resolve_string(self.resources[url], context, base_url=url)


def _parse(data, resources=None, base_url=None):
    parser = etree.XMLParser(no_network=True, resolve_entities=False, load_dtd=False,
                             huge_tree=False, recover=False)
    parser.resolvers.add(_ArchiveResolver(resources if resources is not None else {}))
    root = etree.fromstring(data, parser, base_url=base_url)
    if root.getroottree().docinfo.doctype:
        raise ValueError("XML с DTD не поддерживается.")
    return root


def _schema(path, entry):
    # Read only XSD members, never extract paths. Give libxml2 opaque, in-memory
    # URLs, including for the HTTP-style import locations in recovered WSDLs.
    inventory = {}
    roots = {}
    with zipfile.ZipFile(path) as archive:
        members = [m for m in archive.infolist() if m.filename.lower().endswith(".xsd")]
        if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_TOTAL:
            raise ValueError("Архив превышает лимит числа или объёма схем.")
        for member in members:
            if member.file_size > MAX_FILE or member.filename in inventory:
                raise ValueError("Слишком большая или дублированная схема в архиве.")
            data = archive.read(member)
            root = _parse(data)
            inventory[member.filename] = dict(namespace=root.get("targetNamespace"),
                                               sha256=hashlib.sha256(data).hexdigest())
            roots[member.filename] = root
    if entry not in roots:
        raise ValueError("В архиве отсутствует входная XSD редакции.")
    urls = {name: f"sedo-memory:///schema-{i}.xsd" for i, name in enumerate(roots)}
    pending = [entry]
    resources = {}
    while pending:
        name = pending.pop()
        url = urls[name]
        if url in resources:
            continue
        root = roots[name]
        for imp in root:
            if imp.tag not in {X + "import", X + "include", X + "redefine"}:
                continue
            location = imp.get("schemaLocation")
            if not location:
                raise ValueError("Импорт XSD без schemaLocation.")
            namespace = imp.get("namespace") if imp.tag == X + "import" else root.get("targetNamespace")
            target = resolve_import(inventory, name, location, namespace)
            imp.set("schemaLocation", urls[target])
            pending.append(target)
        resources[url] = etree.tostring(root)
    return etree.XMLSchema(_parse(resources[urls[entry]], resources, urls[entry]))


def _paragraphs(body):
    # Only top-level headings can select a message. Version history and the
    # generated schema tables often quote other headings and examples verbatim.
    rows = []
    for block in body:
        if block.tag == W + "tbl":
            rows.append(None)
            continue
        if block.tag != W + "p":
            continue
        style = block.find(W + "pPr/" + W + "pStyle")
        if style is not None and re.search(r"toc|contents|оглав", style.get(W + "val", ""), re.I):
            continue
        rows.append(word_text(block).strip())
    return rows


def _examples(revision, rows):
    multi = any(SECTION.match(p) for p in rows if p)
    selected = not multi and revision.get("source_type") != "module"
    heading = None
    parts = []
    for p in [*rows, None]:
        section = SECTION.match(p) if p else None
        example = EXAMPLE.fullmatch(p) if p else None
        boundary = p is None or section or example or NEXT_SECTION.match(p)
        if boundary:
            if heading:
                yield heading, parts
            heading, parts = None, []
            if section:
                selected = section[1] == str(revision["number"])
            if selected and example:
                heading = p
        elif heading:
            parts.append(p)


def _instances(parts):
    """Split at a complete root, preserving malformed XML for syntax diagnostics."""
    i = 0
    while i < len(parts):
        line = parts[i]
        i += 1
        if not line:
            continue
        if len(line) > MAX_EXAMPLE:
            yield None, "XML-пример превышает лимит объёма."
            return
        # A declaration can be on the same line as the root. DOCX text is
        # Unicode, so ignore its original byte-encoding declaration.
        line = re.sub(r"^<\?xml\b[^?]*\?>\s*", "", line)
        if not line:
            continue
        if line.startswith("<!DOCTYPE"):
            yield None, "Пример с DTD пропущен."
            return
        match = OPEN_TAG.search(line)
        if not match or (match.start() and not re.fullmatch(r"[A-Za-z]", line[:match.start()])):
            continue
        tag = match[1]
        local = tag.rsplit(":", 1)[-1]
        # Also stop at a malformed close with a missing/wrong prefix. The XML
        # parser diagnoses the spelling; a later sample stays independent.
        end = re.compile(r"</(?:[\w.-]+:\s*)?" + re.escape(local) + r"\s*>")
        lines = [line]
        length = len(line)
        opening = line[match.start():]
        closing_candidates = 0
        while True:
            if end.search(lines[-1]):
                # A descendant can have the same QName as the root.
                closing_candidates += 1
                if closing_candidates > 32:
                    yield None, "Неоднозначные границы вложенного XML-примера."
                    return
                try:
                    _parse("\n".join(lines).encode("utf-8"))
                except etree.XMLSyntaxError as exc:
                    if "Premature end of data" not in exc.msg:
                        break
                except ValueError:
                    break
                else:
                    break
            if opening is not None and ">" in opening:
                if opening.partition(">")[0].rstrip().endswith("/"):
                    break
                opening = None
            if i == len(parts) or parts[i].startswith("<?xml"):
                break
            current = parts[i]
            length += 1 + len(current)
            if length > MAX_EXAMPLE:
                break
            lines.append(current)
            if opening is not None:
                opening += "\n" + current
            i += 1
        if length > MAX_EXAMPLE:
            yield None, "XML-пример превышает лимит объёма."
            return
        # Some specifications append just the closing transport tags after a
        # standalone application root. Validate the payload, not that envelope.
        close = end.search(lines[-1])
        if close and re.fullmatch(r"(?:\s*</(?:[\w.-]+:)?(?:message|putMessageRequest|Body|Envelope)>)+\s*", lines[-1][close.end():]):
            lines[-1] = lines[-1][:close.end()]
        yield "\n".join(lines), None


def _payload(node, number):
    namespace = etree.QName(node).namespace
    if namespace not in SOAP or etree.QName(node).localname != "Envelope":
        return node
    body = node.find(f"{{{namespace}}}Body")
    if body is None:
        raise ValueError("SOAP Body отсутствует.")
    values = body.xpath('.//*[local-name()="message"]')
    if len(values) != 1:
        raise ValueError("Не удалось однозначно выделить поле message из SOAP Body.")
    types = values[0].getparent().xpath('./*[local-name()="messageType"]/text()')
    if types != [str(number)]:
        raise ValueError("Номер сообщения внутри SOAP не подтверждён.")
    if len(values[0]) == 1:
        return values[0][0]
    if len(values[0]):
        raise ValueError("В SOAP message несколько дочерних элементов.")
    try:
        data = base64.b64decode("".join((values[0].text or "").split()), validate=True)
        return _parse(data)
    except (ValueError, binascii.Error, etree.XMLSyntaxError) as exc:
        raise ValueError("Внутренний SOAP message не распознан как XML в Base64.") from exc


def _finding(revision, location, spec, xsd, impact, action):
    return dict(number=str(revision["number"]), env=revision["environment"], rev=revision["revision_id"],
                kind="XML-пример", location=location, spec=spec, xlocation=revision["schema_path"] + " → " + revision["root"],
                xsd=xsd, impact=impact, action=action)


def _syntax_detail(exc):
    mismatch = re.search(r"tag mismatch: (\S+) line \d+ and ([\w.-]+)", exc.msg)
    if mismatch:
        return f"Открыт элемент {mismatch[1]}, но встречен закрывающий тег {mismatch[2]}."
    if "Failed to parse QName" in exc.msg:
        return "Некорректно записано квалифицированное имя элемента (префикс и локальное имя)."
    if "invalid element name" in exc.msg:
        return "После символа «<» отсутствует допустимое имя элемента."
    if "Start tag expected" in exc.msg:
        return "Перед корневым элементом обнаружен посторонний текст."
    if "Premature end of data" in exc.msg:
        return "XML заканчивается до закрытия открытого элемента."
    return exc.msg[:220]


def _diagnostic(node, error):
    namespaces = {key: uri for key, uri in node.nsmap.items() if key}
    try:
        hits = node.xpath(error.path, namespaces=namespaces) if error.path else []
    except etree.XPathError:
        hits = []
    element = hits[0] if hits and isinstance(hits[0], etree._Element) else None
    path = error.path or etree.QName(node).localname
    message = error.message
    readable = re.sub(r"\{(?:https?://|urn:)[^}]*\}", "", message)
    value = element.text if element is not None else None
    if element is not None:
        path = "/" + "/".join(etree.QName(p).localname for p in [*reversed(list(element.iterancestors())), element])
        attribute = re.search(r", attribute '([^']+)'", message)
        if attribute:
            key = attribute[1]
            path += "/@" + key.rsplit("}", 1)[-1]
            value = element.get(key)
    if value and re.fullmatch(r"(?:string)+", value.strip(), re.I):
        return None
    if "base64Binary" in message and ("..." in message or "…" in message):
        return None
    value_summary = None
    if value is not None and value.strip():
        if "base64Binary" in message:
            value_summary = f"Длина значения Base64 без пробельных символов — {len(''.join(value.split()))}."
        elif len(value) <= 72:
            value_summary = "В примере указано «" + value + "»."
        else:
            value_summary = f"В примере указано значение длиной {len(value)} знаков."
    expected = re.search(r"Expected is (?:one of )?\( (.*?) \)", readable)
    required = re.search(r"attribute '([^']+)' is required", readable)
    fixed = re.search(r"fixed value constraint '([^']+)'", readable)
    enumeration = re.search(r"set \{([^}]*)\}", readable)
    datatype = re.search(r"(?:atomic type|list type|union type) '([^']+)'", readable)
    pattern = re.search(r"pattern '(.+)'", readable)
    limit = re.search(r"(maximum length|minimum length|allowed length) of '(\d+)'", readable)
    if "Missing child element" in readable:
        spec = "В примере блок закрыт до обязательного дочернего элемента."
        rule = ("Далее XSD допускает " + expected[1] + ".") if expected else readable[:500]
    elif required:
        spec = "В примере пропущен атрибут " + required[1] + "."
        rule = "Атрибут " + required[1] + " обязателен."
    elif "not expected" in readable:
        spec = "В примере в этой позиции передан элемент " + path.rsplit("/", 1)[-1] + "."
        rule = "В этой позиции XSD допускает " + expected[1] + "." if expected else "Модель XSD не допускает этот элемент в данной позиции."
    else:
        spec = value_summary or "Значение или атрибут узла примера нарушает ограничение."
        if enumeration:
            rule = "Допустимые значения: " + enumeration[1].replace("'", "") + "."
        elif fixed:
            rule = "Фиксированное значение: " + fixed[1] + "."
        elif datatype:
            rule = "Требуется значение типа " + datatype[1] + "."
        elif pattern:
            rule = "Шаблон значения в XSD: " + pattern[1] + "."
        elif limit:
            unit = {"maximum length": "Максимальная длина", "minimum length": "Минимальная длина", "allowed length": "Допустимая длина"}
            rule = unit[limit[1]] + " — " + limit[2] + "."
        else:
            rule = readable[:500]
    return path, spec, rule


def audit(revision, body, archive_path):
    """Return (concrete examples examined, findings, coverage notes) for one revision."""
    checked = 0
    findings = []
    notes = []
    schema = None
    expected = revision.get("root")
    if not expected or not revision.get("schema_path") or body is None:
        return 0, [], ["Отсутствует текст документа или привязка к корневой XSD."]
    namespace = revision.get("namespace") or None
    for heading, parts in _examples(revision, _paragraphs(body)):
        for index, (text, note) in enumerate(_instances(parts), 1):
            location = f"Тип сообщения {revision['number']}, «{heading}», пример {index}"
            if note:
                notes.append(note)
                continue
            template = ("шаблон" in heading.casefold() or "You have a CHOICE" in text or
                        "<!--Optional:" in text and re.search(r">string(?:string)*<", text))
            try:
                node = _parse(text.encode("utf-8"))
                node = _payload(node, revision["number"])
            except etree.XMLSyntaxError as exc:
                checked += 1
                findings.append(_finding(revision, location, f"Строка {exc.lineno}. " + _syntax_detail(exc),
                                         "Обязателен корректно сформированный XML-документ.",
                                         "Разбор завершается до проверки XSD.", "Исправить синтаксис XML-примера."))
                continue
            except ValueError as exc:
                notes.append(str(exc))
                continue
            name = etree.QName(node)
            if name.namespace == X[1:-1] or (name.localname.startswith("simpleSign") and name.localname != expected):
                notes.append("Опубликована схема или оболочка электронной подписи.")
                continue
            if schema is None:
                try:
                    schema = _schema(archive_path, revision["schema_path"])
                except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, etree.LxmlError) as exc:
                    notes.append("Не удалось подготовить XSD редакции: " + str(exc)[:220])
                    return checked, findings, list(dict.fromkeys(notes))
            if (name.localname, name.namespace) != (expected, namespace):
                checked += 1
                findings.append(_finding(revision, location, f"Корень примера {node.tag}.",
                                         f"Для номера указан корень {expected}, пространство имён {namespace or 'отсутствует'}.",
                                         "Пример не соответствует корню редакции.", "Сверить имя корня и пространство имён."))
                continue
            valid = schema.validate(node)
            if template:
                # A literal boolean contradicting fixed="true" is meaningful
                # even in a template. Placeholder values and mutually exclusive
                # branches are intentionally displayed together and skipped.
                errors = [e for e in schema.error_log if "fixed value constraint" in e.message]
                notes.append("Шаблон проверен только по корню и явно фиксированным значениям.")
                if not errors:
                    continue
            else:
                errors = schema.error_log
            checked += 1
            if valid:
                continue
            seen = set()
            for error in errors:
                detail = _diagnostic(node, error)
                if detail is None:
                    notes.append("Условные значения или сокращённые вложения исключены из замечаний.")
                    continue
                if detail in seen:
                    continue
                seen.add(detail)
                path, spec, rule = detail
                if "{http://www.w3.org/2000/09/xmldsig#}Signature" in error.message:
                    impact = "Проверка подписанного XML целиком по прикладной XSD отклоняет подпись."
                else:
                    impact = "Пример отклоняется при проверке по XSD этой редакции."
                findings.append(_finding(revision, location + " — " + path, spec, rule,
                                         impact, "Сверить указанный узел примера с объявлением XSD."))
    if not checked and not notes:
        notes.append("В разделе сообщения не найден самостоятельный XML-пример.")
    return checked, findings, list(dict.fromkeys(notes))
