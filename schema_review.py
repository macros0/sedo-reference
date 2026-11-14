"""Compare a revision's published document with its same-contour schema during build."""
import zipfile
from functools import lru_cache
from xml.etree import ElementTree as ET

import audit_individual_tables
import audit_module_tables
import audit_xml_examples
from module_xsd import W, text


def compare_schema_location(body, schema_path):
    count, findings = 0, []
    for number, table in enumerate(body.findall(W + "tbl"), 1):
        for row in table.findall(W + "tr"):
            cells = row.findall(W + "tc")
            if len(cells) != 2 or text(cells[0]).casefold() != "расположение файла xml-схемы":
                continue
            declared = text(cells[1])
            relative = declared.replace("\\", "/").strip("/")
            if not relative:
                continue
            count += 1
            # Some deliveries put the contents of the logical xsd directory
            # directly under the dated archive wrapper.
            relative = relative.removeprefix("xsd/")
            if ("/" + schema_path).endswith("/" + relative):
                continue
            findings.append(dict(kind="Путь к XSD", location=f"Таблица {number}, строка «Расположение файла xml-схемы»",
                                 spec=declared, xlocation=schema_path, xsd=schema_path,
                                 impact="Путь из спецификации не совпадает с расположением схемы в поставке при поиске с учётом регистра."))
    return count, findings


@lru_cache(maxsize=4)
def _document_body(path, digest):
    # The object store is content-addressed. A changed document has a new digest.
    with zipfile.ZipFile(path / digest) as document:
        body = ET.fromstring(document.read("word/document.xml")).find(W + "body")
    if body is None:
        raise ValueError("В спецификации отсутствует основная часть документа")
    return body


@lru_cache(maxsize=4)
def _module_tables(objects, digest):
    return audit_module_tables.prepare(_document_body(objects, digest))


def compare_revision(revision, fields, objects):
    if revision.get("status") != "matched" or not revision.get("schema_path"):
        return dict(tables=0, examples=0, findings=[],
                    notes=["Для этой редакции не сопоставлен исходный комплект XSD."])
    body = _document_body(objects, revision["document_sha256"])
    archive = objects / revision["archive_sha256"]
    if revision.get("source_type") == "module":
        prepared = _module_tables(objects, revision["document_sha256"])
        tables, table_findings = audit_module_tables.audit(revision, prepared, fields)
    else:
        tables, table_findings = audit_individual_tables.audit(revision, body, fields, archive_path=archive)
        paths, path_findings = compare_schema_location(body, revision["schema_path"])
        tables += paths
        table_findings = [*table_findings, *path_findings]
    examples, example_findings, notes = audit_xml_examples.audit(revision, body, archive)
    keys = ("kind", "location", "spec", "xlocation", "xsd", "impact")
    findings = [{key: finding[key] for key in keys} for finding in [*table_findings, *example_findings]]
    return dict(tables=tables, examples=examples, findings=findings, notes=notes)
