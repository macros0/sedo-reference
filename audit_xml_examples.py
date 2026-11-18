"""Validate DOCX examples against the application schema of the same SEDO revision."""
import base64
import binascii
import hashlib
import re
import zipfile

from lxml import etree

from module_xsd import MAX_FILE, MAX_FILES, MAX_TOTAL, QNAME, W, word_text
from schema import X, SchemaFiles, archive_targets, check_dependency_bases, import_metadata, resolve_import


SECTION = re.compile(r"Тип сообщения\s+(?:СЭДО\s*)?(?:№\s*)?(\d+)\s*[:–—-]\s*\S", re.I)
EXAMPLE = re.compile(r"Пример(?:\s+(?:XML[- ]документа|(?:стр?уктуры\s+)?(?:запроса|ответа(?: на запрос)?)|сообщения|шаблона))?[.:]?", re.I)
NEXT_SECTION = re.compile(r"(?:XSD[- ]схема|Приложение(?:\s|$)|Атрибутивный\s*/\s*элементный|Атрибуты (?:типов|элемента)|Описание$|Структура$)", re.I)
OPEN_TAG = re.compile(rf"<({QNAME})(?=[\s/>]|$)")
XML_DECLARATION_START = re.compile(r"<\?xml(?=[ \t\r\n]|$)")
SPACE = re.compile(r"\s*")
LITERAL_START = re.compile(r"<!--|<!\[CDATA\[|<\?")
LITERAL_END = {"<!--": "-->", "<![CDATA[": "]]>", "<?": "?>"}
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


def _schema(path, revision):
    # Read only XSD members, never extract paths. Give libxml2 opaque, in-memory
    # URLs, including for the HTTP-style import locations in recovered WSDLs.
    inventory = {}
    roots = {}
    originals = {}
    entry = revision["schema_path"]
    with zipfile.ZipFile(path) as archive:
        exact_targets = archive_targets(archive)
        members = [m for m in archive.infolist() if m.filename.lower().endswith(".xsd")]
        if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_TOTAL:
            raise ValueError("Архив превышает лимит числа или объёма схем.")
        for member in members:
            if member.file_size > MAX_FILE or member.filename in inventory:
                raise ValueError("Слишком большая или дублированная схема в архиве.")
            data = archive.read(member)
            originals[member.filename] = data
            root = _parse(data)
            inventory[member.filename] = import_metadata(root, hashlib.sha256(data).hexdigest())
            inventory[member.filename]['import_targets'] = exact_targets.get(member.filename, {})
            roots[member.filename] = root
    if entry not in roots:
        raise ValueError("В архиве отсутствует входная XSD редакции.")
    urls = {name: f"sedo-memory:///schema-{i}.xsd" for i, name in enumerate(roots)}
    pending = [(entry,None)]
    resources = {}
    contexts=set()
    locations={reference:reference.get('schemaLocation') for root in roots.values() for reference in root
               if reference.tag in {X+'import',X+'include',X+'redefine'}}
    targets={}
    while pending:
        name,inherited_namespace = pending.pop()
        url = urls[name]
        root = roots[name]
        effective_namespace=root.get('targetNamespace') or inherited_namespace
        if (name,effective_namespace) in contexts:
            continue
        contexts.add((name,effective_namespace))
        check_dependency_bases(root, name)
        for imp in root:
            if imp.tag not in {X + "import", X + "include", X + "redefine"}:
                continue
            location = locations[imp]
            if not location:
                raise ValueError("Импорт XSD без schemaLocation.")
            namespace = imp.get("namespace") if imp.tag == X + "import" else effective_namespace
            target = resolve_import(inventory, name, location, namespace, imp.tag.rsplit('}',1)[-1])
            if imp in targets and targets[imp]!=target:
                raise ValueError('Неоднозначная зависимость включаемой XSD: '+name)
            targets[imp]=target
            imp.set("schemaLocation", urls[target])
            pending.append((target,None if imp.tag==X+'import' else effective_namespace))
        resources[url] = etree.tostring(root)
    primary = etree.XMLSchema(_parse(resources[urls[entry]], resources, urls[entry]))
    if any(root.xpath(".//xs:attribute[@ref and @fixed] | xs:attribute[@fixed] | .//xs:element[@fixed]",
                      namespaces={"xs": X[1:-1]}) for root in roots.values()):
        from xsd_bundle import _prepare
        from xsd_validate import AttributeUseValidator, BooleanFixedValidator
        selected = SchemaFiles((name, originals[name]) for name, url in urls.items() if url in resources)
        selected.import_targets = {name: {location: target for location, target in edges.items() if target in selected}
                                   for name, edges in exact_targets.items() if name in selected}
        output, _, constraints = _prepare(revision, selected)
        if constraints:
            prefix = "sedo-memory:///bundle/"
            local_resources = {prefix+name: raw for name, raw in output.items() if not name.startswith("originals/")}
            if constraints.get('boolean_fixed_replacements'):
                alternate = dict(local_resources)
                for name, replacement in constraints['boolean_fixed_replacements'].items():
                    alternate[prefix+name] = local_resources[prefix+replacement]
                entry_url = prefix+'message.xsd'
                primary = BooleanFixedValidator(primary, etree.XMLSchema(_parse(alternate[entry_url], alternate, entry_url)))
            if constraints.get("supplementary_schema"):
                source = prefix + constraints["supplementary_schema"]
                supplementary = etree.XMLSchema(_parse(local_resources[source], local_resources, source))
                return AttributeUseValidator(primary, supplementary, constraints)
    return primary


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
        rows.append(word_text(block))
    return rows


def _examples(revision, rows):
    multi = any(SECTION.match(p.strip()) for p in rows if p)
    selected = not multi and revision.get("source_type") != "module"
    heading = None
    parts = []
    for p in [*rows, None]:
        heading_text = p.strip() if p is not None else ''
        section = SECTION.match(heading_text)
        example = EXAMPLE.fullmatch(heading_text)
        boundary = p is None or section or example or NEXT_SECTION.match(heading_text)
        if boundary:
            if heading:
                yield heading, parts
            heading, parts = None, []
            if section:
                selected = section[1] == str(revision["number"])
            elif p and (multi or revision.get("source_type") == "module") and re.match(r"Приложение(?:\s|$)", heading_text, re.I):
                selected = False
            if selected and example:
                heading = heading_text
        elif heading:
            parts.append(p)


def _open_literal(chunk, terminator):
    """Track literals across Word paragraphs when recovering at a new declaration."""
    position = 0
    while True:
        if terminator:
            closing = chunk.find(terminator, position)
            if closing < 0:
                return terminator
            position = closing + len(terminator)
            terminator = None
        opening = LITERAL_START.search(chunk, position)
        if opening is None:
            return None
        terminator = LITERAL_END[opening[0]]
        position = opening.end()


def _feed_instance(scanner, chunk, depth):
    """Feed through each possible tag end to find the parser's root boundary."""
    start = 0
    while (closing := chunk.find('>', start)) != -1:
        end = closing + 1
        scanner.feed(chunk[start:end].encode('utf-8'))
        for event, _ in scanner.read_events():
            depth += 1 if event == 'start' else -1
            if not depth:
                return depth, end
        start = end
    if start < len(chunk):
        scanner.feed(chunk[start:].encode('utf-8'))
    return depth, None


def _following_instance(chunk, boundary):
    """Find the next sample or the terminator of an open epilog comment or PI."""
    while True:
        start = SPACE.match(chunk, boundary).end()
        if (OPEN_TAG.match(chunk, start) or XML_DECLARATION_START.match(chunk, start)
                or chunk.startswith('<!DOCTYPE', start)):
            return boundary, None
        if chunk.startswith('<!--', start):
            marker, size = '-->', 4
        elif chunk.startswith('<?', start):
            marker, size = '?>', 2
        else:
            return None, None
        end = chunk.find(marker, start + size)
        if end < 0:
            return None, marker
        boundary = end + len(marker)


def _instances(parts):
    """Split at a complete root, preserving malformed XML for syntax diagnostics."""
    i = 0
    declaration = []
    declaration_length = 0
    remainder = ''
    while remainder or i < len(parts):
        if remainder:
            line = remainder.lstrip()
            remainder = ''
        else:
            line = parts[i].lstrip()
            i += 1
        declaration_inline = False
        if not line:
            continue
        if len(line) > MAX_EXAMPLE:
            yield None, "XML-пример превышает лимит объёма."
            return
        if declaration and not declaration[-1].endswith('?>') and not line.startswith('<'):
            declaration_length += len(line)+1
            if declaration_length > MAX_EXAMPLE:
                yield None, "XML-пример превышает лимит объёма."
                return
            if '?>' not in line:
                declaration.append(line)
                continue
            prefix,_,line=line.partition('?>')
            declaration.append(prefix+'?>')
            line=line.lstrip()
            declaration_inline=bool(line)
        # Retain the declaration for syntax checking, including when Word puts
        # it in a separate paragraph from the root element.
        if XML_DECLARATION_START.match(line):
            match = re.match(r'<\?xml\b.*?\?>',line,re.S)
            if match:
                declaration=[match[0]]
                declaration_length=len(match[0])
                line=line[match.end():].lstrip()
                declaration_inline=bool(line)
            else:
                declaration=[line]
                declaration_length=len(line)
                continue
        if not line:
            continue
        # XML Misc may precede the document element. Retain it for syntax
        # validation, but never mistake a tag quoted in a comment for the root.
        while line.startswith(('<!--', '<?')):
            terminator = '-->' if line.startswith('<!--') else '?>'
            while terminator not in line and i < len(parts):
                line += '\n' + parts[i]
                i += 1
                if len(line) + declaration_length > MAX_EXAMPLE:
                    yield None, 'XML-пример превышает лимит объёма.'
                    return
            if terminator not in line:
                break
            marker, _, line = line.partition(terminator)
            declaration.append(marker + terminator)
            declaration_length += len(marker) + len(terminator) + 1
            if declaration_length > MAX_EXAMPLE:
                yield None, 'XML-пример превышает лимит объёма.'
                return
            line = line.lstrip()
            declaration_inline = bool(line)
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
        lines = [*declaration[:-1],declaration[-1]+line] if declaration_inline else [*declaration,line]
        declaration=[]
        length = sum(map(len,lines))+len(lines)-1
        # Feed only the instance here. The full original declaration and any
        # stray leading characters stay in lines for the actual syntax report.
        scanner = etree.XMLPullParser(events=('start', 'end'), no_network=True,
                                      resolve_entities=False, load_dtd=False)
        # Complete encoding detection before the root, even for <r/>. This
        # padding exists only in the boundary scanner, never in the audited XML.
        scanner.feed(b'    ')
        depth = 0
        literal = None
        chunk = line[match.start():]
        while True:
            if scanner is not None:
                try:
                    depth, boundary = _feed_instance(scanner, chunk, depth)
                except etree.XMLSyntaxError:
                    # Keep the malformed sample together until its apparent
                    # close or a new declaration so it produces one diagnostic.
                    scanner = None
                else:
                    if boundary is not None:
                        following, terminator = _following_instance(chunk, boundary)
                        while terminator and i < len(parts):
                            chunk = parts[i]
                            length += 1 + len(chunk)
                            if length > MAX_EXAMPLE:
                                break
                            lines.append(chunk)
                            i += 1
                            closing = chunk.find(terminator)
                            if closing != -1:
                                following, terminator = _following_instance(chunk, closing + len(terminator))
                        if following is not None:
                            remainder = chunk[following:]
                            lines[-1] = lines[-1][:-len(remainder)]
                        break
            if scanner is None:
                if end.search(lines[-1]) or lines[-1].rstrip().endswith('/>') and len(lines) == 1:
                    break
            if i == len(parts):
                break
            literal = _open_literal(chunk, literal)
            if not literal and XML_DECLARATION_START.match(parts[i]):
                break
            current = parts[i]
            length += 1 + len(current)
            if length > MAX_EXAMPLE:
                break
            lines.append(current)
            chunk = "\n" + current
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


def _xml_bytes(text):
    """Check XML declaration grammar before changing Unicode's byte encoding."""
    if XML_DECLARATION_START.match(text):
        declaration=re.match(r'<\?xml\b.*?\?>',text,re.S)
        if declaration:
            original=declaration[0]
            # Preserve the declaration and its attribute order. Only replace a
            # syntactically valid encoding value; malformed declarations still
            # reach the XML parser intact and produce a located finding.
            encoding=re.search(r'''\bencoding\s*=\s*(['"])([A-Za-z][A-Za-z0-9._-]*)\1''',original)
            normalized=original
            if encoding:normalized=original[:encoding.start(2)]+'UTF-8'+original[encoding.end(2):]
            _parse((normalized+'<declaration-check/>').encode('utf-8'))
            text=normalized+text[declaration.end():]
    return text.encode('utf-8')


def _payload(node, number):
    namespace = etree.QName(node).namespace
    if namespace not in SOAP or etree.QName(node).localname != "Envelope":
        return node
    body = node.find(f"{{{namespace}}}Body")
    if body is None:
        raise ValueError("SOAP Body отсутствует.")
    # Transport fields belong to the SOAP operation. Application XML can
    # itself contain message and messageType elements at a deeper level.
    values = body.xpath('./*[*[local-name()="messageType"]]/*[local-name()="message"]')
    if len(values) != 1:
        raise ValueError("Не удалось однозначно выделить поле message из SOAP Body.")
    types = values[0].getparent().xpath('./*[local-name()="messageType"]')
    if len(types) != 1 or types[0].xpath('./*') or types[0].xpath('string(.)') != str(number):
        raise ValueError("Номер сообщения внутри SOAP не подтверждён.")
    message = values[0]
    elements = message.xpath("./*")
    if len(elements) == 1:
        # libxml2 diagnostics use absolute document paths. A payload still owned
        # by the SOAP document gives wrapper-relative paths and loses values.
        return _parse(etree.tostring(elements[0], with_tail=False))
    if elements:
        raise ValueError("В SOAP message несколько дочерних элементов.")
    try:
        data = base64.b64decode("".join(message.xpath("string(.)").split()), validate=True)
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
    element = None
    if error.path:
        tree = node.getroottree()
        namespaces = {key: uri for key, uri in node.nsmap.items() if key}
        try:
            hits = node.xpath(error.path, namespaces=namespaces)
        except etree.XPathError:
            hits = []
        # Prefixes and positions in a diagnostic path describe the original
        # node, which can locally bind the prefix to a different namespace.
        if (len(hits) == 1 and isinstance(hits[0], etree._Element)
                and tree.getpath(hits[0]) == error.path):
            element = hits[0]
        else:
            matches = (item for item in node.iter() if isinstance(item.tag, str)
                       and tree.getpath(item) == error.path)
            match = next(matches, None)
            if match is not None and next(matches, None) is None:
                element = match
    path = error.path or etree.QName(node).localname
    message = error.message
    readable = re.sub(r"\{(?:https?://|urn:)[^}]*\}", "", message)
    value = element.text if element is not None else None
    if element is not None:
        path = "/" + "/".join(etree.QName(p).localname for p in [*reversed(list(element.iterancestors())), element])
        attribute = re.search(r", attribute '([^']+)'|The attribute '([^']+)' is required", message)
        if attribute:
            key = attribute[1] or attribute[2]
            path += "/@" + key.rsplit("}", 1)[-1]
            value = element.get(key)
    value_error = bool(re.fullmatch(r'SCHEMAV_CVC_(?:DATATYPE_VALID_1_2_[123]|(?:ENUMERATION|PATTERN|MINLENGTH|MAXLENGTH|LENGTH|MININCLUSIVE|MAXINCLUSIVE|MINEXCLUSIVE|MAXEXCLUSIVE|TOTALDIGITS|FRACTIONDIGITS)_VALID)',
                                    getattr(error, 'type_name', '')))
    if value_error and value and re.fullmatch(r"(?:string)+", value.strip(), re.I):
        return None
    if value_error and value and "base64Binary" in message and ("..." in value or "…" in value):
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
        actual = element.tag if element is not None else None
        raw_expected = re.search(r"Expected is (?:one of )?\( (.*?) \)", message)
        expected_names = [name.strip() for name in raw_expected[1].split(', ')] if raw_expected else []
        collision = actual is not None and any(name.rsplit('}', 1)[-1] == etree.QName(actual).localname
                                               and name != actual for name in expected_names)
        def qname_label(name):
            return name if name.startswith('{') else name + ' (без пространства имён)'
        actual_label = qname_label(actual) if collision else path.rsplit("/", 1)[-1]
        expected_label = ', '.join(qname_label(name) for name in expected_names) if collision else expected[1] if expected else None
        spec = "В примере в этой позиции передан элемент " + actual_label + "."
        rule = "В этой позиции XSD допускает " + expected_label + "." if expected_label else "Модель XSD не допускает этот элемент в данной позиции."
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
                node = _parse(_xml_bytes(text))
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
                    schema = _schema(archive_path, revision)
                except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, etree.LxmlError) as exc:
                    notes.append("Не удалось подготовить XSD редакции: " + str(exc)[:220])
                    return checked, findings, list(dict.fromkeys(notes))
            if (name.localname, name.namespace) != (expected, namespace):
                checked += 1
                findings.append(_finding(revision, location, f"Корень примера {node.tag}.",
                                         f"Для номера указан корень {expected}, пространство имён {namespace or 'отсутствует'}.",
                                         "Пример не соответствует корню редакции.", "Сверить имя корня и пространство имён."))
                continue
            from xsd_validate import AttributeUseValidator
            valid = (schema.validate(node, check_attributes_on_invalid=True)
                     if isinstance(schema, AttributeUseValidator) else schema.validate(node))
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
                finding = _finding(revision, location + " — " + path, spec, rule,
                                   impact, "Сверить указанный узел примера с объявлением XSD.")
                facet = re.search(r'CVC_(ENUMERATION|PATTERN|MINLENGTH|MAXLENGTH|LENGTH|MININCLUSIVE|MAXINCLUSIVE|MINEXCLUSIVE|MAXEXCLUSIVE|TOTALDIGITS|FRACTIONDIGITS)_VALID', getattr(error, 'type_name', ''))
                if facet:
                    from schema import FACETS
                    finding['rule_facets'] = [key for key in FACETS if key.upper() == facet[1]]
                findings.append(finding)
    if not checked and not notes:
        notes.append("В разделе сообщения не найден самостоятельный XML-пример.")
    return checked, findings, list(dict.fromkeys(notes))
