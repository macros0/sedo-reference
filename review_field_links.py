"""Resolve review locations against the actual field tree of a revision."""
import re


def attach_schema_sources(revision, findings, archive_path):
    """Attribute rules to their actual declarations, including inherited facets.

    The tree's ``file`` identifies the element declaration. Value constraints can
    live further down a named type's base chain in a different imported file.
    """
    import hashlib
    import zipfile
    from decimal import Decimal, InvalidOperation
    from schema import FACETS, Schema, X, local
    from source_provenance import original_schema_source

    if not findings:
        return findings
    with zipfile.ZipFile(archive_path) as archive:
        schema = Schema(archive, revision['schema_path'], revision['root'])
        schema.field(schema.root)
        by_path = {}
        for field in schema.fields:
            by_path.setdefault(field['path'], []).append(field)
        resources = {}
        declaration_index = None

        def resource(path):
            if path not in resources:
                digest = hashlib.sha256(archive.read(path)).hexdigest()
                item = dict(path=path, sha256=digest, download='sources/' + digest + '.xsd')
                remote = original_schema_source(revision.get('schema_sources', []), path, digest)
                if remote and remote.get('url'):
                    item['url'] = remote['url']
                resources[path] = item
            return resources[path]

        def rules(node, seen=()):
            if node in seen:
                return [], {}
            seen += (node,)
            owners, facets = [], {}
            reference = node.get('ref') or node.get('type') or node.get('base')
            if reference:
                kinds = [local(node)] if node.get('ref') else ['simpleType', 'complexType']
                target = schema.resolve(reference, node, kinds)
                if target is not None:
                    owners, facets = rules(target, seen)
            if local(node) in ('simpleType', 'complexType'):
                owners = [schema.owner[node], *owners]
            # Traverse only this value's definition, never descendant fields.
            for child in node:
                kind = local(child)
                if kind in ('simpleType', 'complexType', 'simpleContent', 'complexContent', 'restriction', 'extension'):
                    more_owners, more_facets = rules(child, seen)
                    owners.extend(more_owners)
                    for key, paths in more_facets.items():
                        if key == 'enumeration':
                            facets[key] = paths
                        else:
                            facets.setdefault(key, []).extend(paths)
                elif kind in FACETS:
                    if kind == 'enumeration' and kind not in facets:
                        facets[kind] = []
                    facets.setdefault(kind, []).append((schema.owner[child], child.get('value', '')))
            # A derived enumeration replaces its base enumeration.
            if node.find(X + 'enumeration') is not None:
                facets['enumeration'] = [(schema.owner[node], child.get('value', '')) for child in node.findall(X + 'enumeration')]
            return list(dict.fromkeys(owners)), facets

        for finding in findings:
            requested = finding.get('field_paths') or [finding.get('field_path', '')]
            fields = [field for path in requested for field in by_path.get(path, [])]
            selected = []
            facet_names = set(finding.get('rule_facets', []))
            facet_names.update(re.findall(r'\b(' + '|'.join(sorted(FACETS)) + r')\b', finding['xsd']))
            facet_names.update({'Перечень значений': ['enumeration'],
                                'Формат значения': ['pattern']}.get(finding['kind'], []))
            declarations = [schema.field_declarations[id(field)] for field in fields]
            if finding.get('rule_children'):
                # Composition belongs to the child declarations, which can be
                # supplied by an imported type or a referenced model group.
                children = [schema.fields[index] for field in fields for index in field['children']
                            if schema.fields[index]['name'] in finding['rule_children']]
                if children:
                    declarations = [schema.field_declarations[id(child)] for child in children]
            if not declarations:
                # Named type cards need not have a use in the application root.
                from audit_individual_tables import declaration_index as index_declarations
                if declaration_index is None:
                    declaration_index = index_declarations(schema)
                location = re.split(r' — | · | → ', finding['xlocation'], maxsplit=1)
                if len(location) > 1:
                    matches = [(file, node) for (path, _), candidates in declaration_index.items()
                               if path == location[1] for file, node in candidates if file == location[0]]
                    if len(matches) == 1:
                        declarations = [(matches[0][1], matches[0][1])]
            for declaration, source in declarations:
                if (finding['kind'] == 'Кратность'
                        or finding['kind'] in ('Свойство объявления', 'Свойство поля')
                        and re.match(r'^(?:minOccurs|maxOccurs|min|max|use)\s*=', finding['xsd'])):
                    selected.append(schema.owner[declaration])
                    continue
                if finding.get('rule_children'):
                    # A ref use proves membership in this content model. Its
                    # global target supplies the value, not the containing rule.
                    selected.append(schema.owner[declaration])
                    continue
                owners, facets = rules(source)
                paths = []
                for facet in sorted(facet_names):
                    facet_declarations = facets.get(facet, [])
                    select = {'minLength': max, 'maxLength': min, 'minInclusive': max, 'maxInclusive': min,
                              'minExclusive': max, 'maxExclusive': min, 'totalDigits': min, 'fractionDigits': min}.get(facet)
                    if select and facet_declarations:
                        try:
                            effective = select(Decimal(value) for _, value in facet_declarations)
                            facet_declarations = [(path, value) for path, value in facet_declarations if Decimal(value) == effective]
                        except InvalidOperation:
                            pass
                    paths.extend(path for path, _ in facet_declarations)
                # A pattern or enumeration may itself contain the word "fixed".
                # Only the rendered property/diagnostic prefix identifies it.
                fixed = re.match(r'^(?:fixed\s*=|Фиксированное значение\s*:)', finding['xsd']) is not None
                if fixed:
                    paths = [schema.owner[declaration if declaration.get('fixed') is not None else source]]
                if not paths:
                    value_rule = facet_names or finding['kind'] in ('Тип данных', 'Ограничение значения', 'Ограничение длины', 'Диапазон значения')
                    paths = owners[:1] if value_rule and owners else [schema.owner[source]]
                selected.extend(paths)
            if not selected:
                # Unused type cards have no instance path. Their declaration file
                # is still an exact archive member recorded by the table audit.
                selected = [path for path in schema.files if finding['xlocation'].startswith(path + ' ')
                            or finding['xlocation'] == path]
            selected = list(dict.fromkeys(selected))
            if selected:
                sources = [resource(path) for path in selected]
                finding['xsd_source'] = sources[0]
                if len(sources) > 1:
                    finding['xsd_sources'] = sources
                # Keep the location readable for legacy consumers as well.
                suffix = re.split(r' — | · | → ', finding['xlocation'], maxsplit=1)
                location = suffix[1] if len(suffix) > 1 else ''
                if finding['kind'] == 'XML-пример' and fields and ' — /' in finding.get('location',''):
                    location = fields[0]['path']
                    if finding.get('field_link_scope') == 'parent':
                        location += ' (родительский блок)'
                finding['xlocation'] = selected[0] + (' · ' + location if location else '')
    return findings


def review_sources(catalog, revision):
    """Collect each document's findings for the same displayed schema and contour."""
    from urllib.parse import unquote, urlsplit
    import json
    group = revision.get("display_revision_id", revision["revision_id"])
    sources, seen = {}, {}
    for row in catalog["revisions"]:
        if (not row.get("ready") or row["environment"] != revision["environment"]
                or row["number"] != revision["number"]
                or row.get("display_revision_id", row["revision_id"]) != group):
            continue
        findings = [f for f in row.get("schema_review", {}).get("findings", []) if f.get("field_paths")]
        if not findings:
            continue
        key = (row.get("document_sha256"), row.get("document_url"))
        if key not in sources:
            url = row.get("document_url")
            name = (unquote(urlsplit(url).path.split("/")[-1]) if url
                    else row.get("manual_source", {}).get("filename") or row.get("document_filename") or "Загруженная спецификация")
            sources[key] = dict(name=name, version=row.get("document_version"), date=row.get("label_date"),
                                url=url, download=row.get("document_download"), findings=[])
            seen[key] = set()
        for finding in findings:
            identity = json.dumps(finding, ensure_ascii=False, sort_keys=True)
            if identity not in seen[key]:
                seen[key].add(identity)
                sources[key]["findings"].append(finding)
    return list(sources.values())



def _local_path(path):
    path = re.sub(r"\[[^]]*\]", "", path).strip("/")
    return "/".join(re.sub(r"^(@?)[\w.-]+:", r"\1", part) for part in path.split("/"))


def field_targets(path, fields):
    """Return all valid locations, or their nearest known containing blocks."""
    if not path:
        return [], ""
    path = _local_path(path)
    if fields and path.startswith("*/"):
        path = fields[0]["path"] + path[1:]
    known = {field["path"] for field in fields}
    requested = path
    while path:
        candidates = [path] if path in known else []
        head, _, tail = path.partition("/")
        for field in fields:
            if field.get("type", "").rsplit(":", 1)[-1] != head:
                continue
            candidate = field["path"] + ("/" + tail if tail else "")
            if candidate in known:
                candidates.append(candidate)
        if candidates:
            return list(dict.fromkeys(candidates)), "field" if path == requested else "parent"
        path = path.rpartition("/")[0]
    return [], ""


def attach_targets(finding, fields):
    if finding.get("field_paths"):
        known = {field["path"] for field in fields}
        exact = list(dict.fromkeys(p for p in finding["field_paths"] if p in known))
        if exact:
            return dict(finding, field_paths=exact, field_link_scope="field")
    path = finding.get("field_path")
    if path is None:
        if finding["kind"] == "Путь к XSD":
            path = fields[0]["path"] if fields else ""
        elif finding["kind"] == "XML-пример":
            location = finding["location"].rsplit(" — ", 1)[-1]
            path = location if location.startswith("/") else finding["xlocation"].rsplit(" → ", 1)[-1]
        else:
            path = re.split(r" — | · ", finding["xlocation"])[-1]
    targets, scope = field_targets(path, fields)
    return dict(finding, field_paths=targets, field_link_scope=scope)
