"""Parse the version and date from an official label or uploaded filename."""
import re
import posixpath
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

MODULE_FILENAME = re.compile(r"Пр[ _]+(\d+)[ _]+(.+?)(?:\s*\(|\.docx$)", re.I)


def schema_location(structure):
    locations = [value.strip() for label,value in structure
                 if 'расположение файла xml-схемы' in normalized_heading(label)]
    return locations[0] if len(locations) == 1 else None


def schema_location_candidates(candidates, structure):
    """Find the archive members named by the original Word table."""
    location = schema_location(structure)
    if not location:
        return []
    named = posixpath.normpath(unquote(urlsplit(location).path).replace('\\','/')).lstrip('/')
    if not named.lower().endswith('.xsd'):
        return []
    return [(archive,schema) for archive,schema in candidates
            if schema['path'] == named or schema['path'].endswith('/'+named)]


def wsdl_location_matches(location, archive, source):
    """Compare the DOCX locator to the requested and redirected XSD URLs."""
    try:
        location = location.replace('\\', '/')
        requested = urlsplit(location)
        query = dict(parse_qsl(requested.query))
        if not (unquote(requested.path).lower().endswith('.xsd') or query.get('xsd')):
            return False
        urls = [urlsplit(source[key]) for key in ('url', 'final_url') if source.get(key)]
        if not (requested.scheme or requested.netloc or requested.query or '/' in requested.path):
            # A bare filename refers to all resources of that name, including
            # schemas served as an xsd query, even if one is beside the WSDL.
            return any(unquote(requested.path) == posixpath.basename(
                dict(parse_qsl(url.query)).get('xsd') or unquote(url.path)) for url in urls)

        def key(url):
            return (url.scheme, url.hostname, url.port or 443, url.username, url.password,
                    posixpath.normpath(unquote(url.path)), sorted(parse_qsl(url.query, keep_blank_values=True)))

        bases = [archive['source_url']]
        bases.extend(item.get('final_url', item['url']) for item in archive['source_files'] if item['kind'] == 'wsdl')
        keys = [key(url) for url in urls]
        return any(key(urlsplit(urljoin(base, location))) in keys for base in bases)
    except ValueError:
        return False


def prefer_schema_location(candidates, structure):
    if len(candidates) < 2:
        return candidates
    return schema_location_candidates(candidates, structure) or candidates


def cell_text(node):
    """Keep Word layout boundaries, while joining formatting runs within words."""
    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    def parts(element):
        if element.tag == word + 't':
            yield element.text or ''
        elif element.tag in (word + 'tab', word + 'br', word + 'cr'):
            yield ' '
        else:
            for child in element:
                yield from parts(child)
            if element.tag == word + 'p':
                yield ' '
    return ' '.join(''.join(parts(node)).split())


def normalized_heading(value):
    return " ".join(value.casefold().replace("ё", "е").split())


def parse_label(name):
    # Prefer a complete year, then the last date. Lookahead permits overlapping
    # numeric candidates in labels where the version and date share a separator.
    dates = re.finditer(r"(?<![\dvвVВ.])(?=(?P<day>\d{2})(?P<sep>[._])(?P<month>\d{2})(?P=sep)(?P<year>\d{4}|\d{2})(?!\d))", name, re.I)
    date = max(dates, key=lambda m: (len(m['year']), m.start()), default=None)
    version_part = name[:date.start()] + " " + name[date.end('year'):] if date else name
    version = re.search(r"[vв](\d+(?:[._]\d+)*)(?!\d)", version_part, re.I)
    label_date = '.'.join(date[key] for key in ('day', 'month', 'year')) if date else None
    return version[1].replace('_', '.') if version else None, label_date


def filename_metadata(filename):
    version, label_date = parse_label(re.split(r"[\\/]", filename)[-1])
    date = ""
    if label_date:
        day, month, year = label_date.split('.')
        date = ('20' if len(year) == 2 else '') + year + '-' + month + '-' + day
    return dict(version=version or '', date=date)


def document_history(tables):
    """Read the Word document change log, independently of XSD correspondence."""
    result = []
    for table in tables:
        if not table or len(table[0]) < 3:
            continue
        headers = [re.sub(r"\s+", "", normalized_heading(c)) for c in table[0][:3]]
        if (headers[0] not in ("№версиидокумента", "№версиидок-та")
                or headers[1] != "датаизменения" or headers[2] != "изменения"):
            continue
        for row in table[1:]:
            if len(row) >= 3 and any(row[:3]):
                result.append(dict(version=row[0], date=row[1], description=row[2]))
    return result


def root_metadata(structure):
    """Distinguish an absent namespace row from an explicitly empty namespace."""
    fields = dict(structure)
    root = next((v for k, v in fields.items() if "корневой элемент" in normalized_heading(k)), None)
    declared = any("пространство имен" in normalized_heading(k) for k in fields)
    namespace = next((v for k, v in fields.items() if "пространство имен" in normalized_heading(k)), None)
    namespace = namespace.strip() or None if isinstance(namespace, str) else namespace
    return root, namespace, declared


def root_matches(schema, root, namespace, namespace_declared):
    return ((not namespace_declared or namespace == (schema.get("namespace") or None))
            and any(r["name"] == root for r in schema["roots"]))


def read_document_display(raw, *, legacy=False):
    """Restore display strings from original Word bytes without changing observations."""
    import io
    import zipfile
    from xml.etree import ElementTree as ET
    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        member = archive.getinfo("word/document.xml")
        if member.file_size > 96 * 1024 * 1024:
            raise ValueError("XML exceeds limit")
        document = ET.fromstring(archive.read(member))
    def cell(node):
        return ' '.join(''.join(t.text or '' for t in node.iter(word+'t')).split()) if legacy else cell_text(node)
    tables = [[[cell(c) for c in row.findall(word + "tc")] for row in table.findall(word + "tr")]
              for table in document.findall(".//" + word + "body/" + word + "tbl")]
    replacements = {}
    titles = {}
    registry_labels = {}
    def old_text(node):
        return ' '.join(''.join(t.text or '' for t in node.iter(word+'t')).split())
    def remember(before, after):
        if before:
            replacements.setdefault(before, set()).add(after)
    if not legacy:
        # Record every interpretation, including unchanged cells. If the same
        # legacy string has two meanings, it cannot safely be replaced globally.
        for table in document.findall('.//'+word+'body/'+word+'tbl'):
            rows = [row.findall(word+'tc') for row in table.findall(word+'tr')]
            header = ['Код типа сообщения', 'Наименование']
            legacy_registry = len(rows) > 2 and [old_text(cell) for cell in rows[0][:2]] == header
            if not rows or not legacy_registry and [cell_text(cell) for cell in rows[0][:2]] != header:
                continue
            group = ('', '')
            for cells in rows[1:]:
                node = cells[0] if len(cells) == 1 else cells[1] if len(cells) >= 2 and cell_text(cells[0]).isdigit() else None
                if node is not None:
                    remember(old_text(node), cell_text(node))
                if len(cells) == 1:
                    group = (old_text(cells[0]), cell_text(cells[0]))
                elif node is not None:
                    number, before, after = old_text(cells[0]), old_text(node), cell_text(node)
                    titles.setdefault((number, before), set()).add(after)
                    if legacy_registry and len(cells) >= 4 and number.isdigit():
                        # The original five values identify the recorded row. Keep
                        # duplicate rows in Word order to restore each separately.
                        identity = tuple(old_text(c) for c in cells[:4]) + (group[0],)
                        registry_labels.setdefault(identity, []).append((after, group[1]))
        declaration = r'Тип сообщения\s+(?:СЭДО\s*)?(?:№\s*)?(\d{1,5})\s*[:—–-]\s*(.+)'
        for paragraph in document.findall(word+'body/'+word+'p'):
            style = paragraph.find(word+'pPr/'+word+'pStyle')
            if style is not None and re.search(r'toc|contents|оглав',style.get(word+'val',''),re.I):
                continue
            before = re.fullmatch(declaration, old_text(paragraph), re.I)
            after = re.fullmatch(declaration, cell_text(paragraph), re.I)
            if before and after and before[1] == after[1]:
                remember(before[2].strip(), after[2].strip())
                titles.setdefault((before[1], before[2].strip()), set()).add(after[2].strip())
    exact = {before: next(iter(values)) for before,values in sorted(replacements.items())
             if len(values) == 1 and before not in values}
    result = dict(document_history=document_history(tables), text_replacements=exact)
    # Older module matches can carry a title without a binding to a specific
    # table row. Only use text with one meaning in that original document.
    for (number, before), values in titles.items():
        if len(values) == 1 and before in exact:
            result.setdefault('titles', {}).setdefault(number, {})[before] = exact[before]
    result['registry_labels'] = {key: values for key, values in registry_labels.items()
                                 if any(labels != (key[1], key[-1]) for labels in values)}
    return result



def read_document_history(raw):
    return read_document_display(raw)['document_history']


def display_appendix(row):
    if 'appendix' in row:
        value = row['appendix'] or ''
        return str(int(value)) if re.fullmatch(r'[0-9]+', value) else value
    url = row.get('document_url') or ''
    filename = unquote(urlsplit(url).path.rsplit('/', 1)[-1])
    match = MODULE_FILENAME.search(filename)
    return str(int(match[1])) if match else ''


def apply_display_text(row, display):
    if row.get('source_type') == 'manual':
        return
    replacements = display.get('text_replacements', {})
    inherited = display.get('module_replacements', {}).get(row.get('environment'), {}).get(row.get('number'), {})
    if inherited:
        inherited = inherited.get(display_appendix(row), {})
    for field in ('title', 'module'):
        before = row.get(field)
        if field == 'module' and before in inherited:
            row[field] = inherited[before]
        elif before in replacements:
            row[field] = replacements[before]


def snapshot_display(registry, documents, bindings, *, legacy):
    """Resolve labels before the publication and its registry get separated."""
    def own_title(row):
        if id(row) in registry_labels:
            return registry_labels[id(row)][0]
        titles = documents.get(row.get('document_sha256'), {}).get('titles', {})
        before = row.get('title')
        return titles.get(row['number'], {}).get(before, before)

    memberships = {}
    registry_labels = {}
    occurrences = {}
    for item in registry:
        memberships.setdefault((item['environment'], item['number']), []).append(item)
        if not legacy or item.get('source_type') == 'module':
            continue
        fields = tuple(item.get(k) for k in ('number','title','description','appendix','module'))
        document = item.get('document_sha256')
        values = documents.get(document, {}).get('registry_labels', {}).get(fields, [])
        key = (document, item.get('document_url'), item['environment'], fields)
        index = occurrences.get(key, 0)
        occurrences[key] = index + 1
        if index < len(values):
            registry_labels[id(item)] = values[index]

    def registry_module(row):
        return registry_labels[id(row)][1] if id(row) in registry_labels else row.get('module')

    def present(row, *, registry_row=False, first_membership=False):
        result = dict(row, display_metadata_applied=True)
        display = documents.get(row.get('document_sha256'), {})
        if 'document_history' in row and display:
            result['document_history'] = display['document_history']
        if not legacy or row.get('source_type') == 'manual':
            return result
        records = memberships.get((row['environment'], row['number']), [])
        field, record = bindings.get((row.get('revision_id'), row.get('document_url')), (None, None))
        if 'title' in row:
            result['title'] = own_title(record if field == 'title' else row)
        if registry_row:
            result['module'] = registry_module(row)
        elif row.get('module') and records:
            appendix = display_appendix(row)
            if first_membership:
                records = records[:1]
            elif field == 'module':
                records = [record]
            elif appendix:
                records = [r for r in records if r.get('appendix') == appendix]
            values = {registry_module(r) for r in records if r['module'] == row['module']}
            if len(values) == 1:
                result['module'] = next(iter(values))
        return result
    return present
