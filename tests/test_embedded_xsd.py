import html
import io
import itertools
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from sync import inspect_file, sha
from catalog import derive
from module_xsd import extract_archives, match_document, unpack_schemas, native_package

ROOT=Path(__file__).resolve().parents[1]
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XS='http://www.w3.org/2001/XMLSchema'


def quoted_document(*quotes, stamp=(1980, 1, 1, 0, 0, 0)):
    output = io.BytesIO()
    paragraphs = ''.join(f'<w:p><w:r><w:t>{html.escape(quote)}</w:t></w:r></w:p>' for quote in quotes)
    with zipfile.ZipFile(output, 'w') as archive:
        entry = zipfile.ZipInfo('word/document.xml', stamp)
        entry.create_system = 0
        archive.writestr(entry, f'<w:document xmlns:w="{W}"><w:body>{paragraphs}</w:body></w:document>')
    return output.getvalue()


def chameleon_quotation(uri, *, imports=('a', 'b'), qname=True):
    main = (f'<xs:schema xmlns:xs="{XS}" xmlns:a="urn:a" xmlns:b="urn:b">'
            + ''.join(f'<xs:import namespace="urn:{ns}" schemaLocation="{ns}.xsd"/>' for ns in imports)
            + '<xs:element name="r"><xs:complexType><xs:sequence>'
            '<xs:element ref="a:alpha"/><xs:element ref="b:beta"/>'
            '</xs:sequence></xs:complexType></xs:element></xs:schema>')
    def wrapper(ns, name, members):
        return (f'<xs:schema xmlns:xs="{XS}" xmlns:t="urn:{ns}" targetNamespace="urn:{ns}">'
                '<xs:include schemaLocation="leaf.xsd"/>'
                f'<xs:simpleType name="Items"><xs:union memberTypes="{members}"/></xs:simpleType>'
                f'<xs:element name="{name}" type="t:Limited"/></xs:schema>')
    return {'main.xsd': main, 'a.xsd': wrapper('a', 'alpha', 'xs:string xs:QName'),
            'b.xsd': wrapper('b', 'beta', 'xs:QName xs:boolean' if qname else 'xs:string xs:QName'),
            'leaf.xsd': (f'<xs:schema xmlns:xs="{XS}" xmlns:p="{uri}">'
                         '<xs:simpleType name="Limited"><xs:restriction base="Items">'
                         '<xs:enumeration value="p:one"/></xs:restriction></xs:simpleType></xs:schema>')}


def document():
    out=io.BytesIO()
    xml=f'''<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>Тип сообщения 600: Решение о скидке</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Элемент/Атрибут</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Тип</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>DecisionDiscountRust</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>DocumentType</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('word/document.xml',xml)
        z.writestr('word/embeddings/object.bin',(ROOT/'tests/fixtures/rust-xsd.ole').read_bytes())
    return out.getvalue()

class EmbeddedSchemaTests(unittest.TestCase):
    def test_chameleon_quotation_requires_equivalence_in_each_namespace(self):
        from lxml import etree
        from tests.test_schema_recovery import archive
        from xsd_validate import compile_schema

        doc = dict(environment='prod', url='doc', module_bindings=[dict(number='42', root='r', namespace='')])
        namespaces = ('urn:p', 'urn:q')
        with tempfile.TemporaryDirectory() as temporary:
            for imports, qname in itertools.product(itertools.permutations(('a', 'b')), (True, False)):
                with self.subTest(imports=imports, qname=qname):
                    objects, candidates = {}, []
                    for namespace in namespaces:
                        directory = Path(temporary)
                        files = chameleon_quotation(namespace, imports=imports, qname=qname)
                        for name, content in files.items():
                            (directory / name).write_text(content, encoding='utf8')
                        schema = compile_schema(directory / 'main.xsd', directory)
                        verdicts = []
                        for instance_namespace in namespaces:
                            xml = ('<r xmlns:a="urn:a" xmlns:b="urn:b" '
                                   f'xmlns:q="{instance_namespace}"><a:alpha>p:one</a:alpha>'
                                   f'<b:beta>{"q" if qname else "p"}:one</b:beta></r>')
                            verdicts.append(schema.validate(etree.fromstring(xml)))
                        self.assertEqual(verdicts, [not qname or namespace == ns for ns in namespaces])
                        raw = archive(files)
                        digest = sha(raw)
                        objects[digest] = raw
                        candidates.append(dict(environment='prod', kind='zip', url='https://example.test/' + digest,
                                               sha256=digest, **inspect_file(raw, 'zip')))
                    for candidate in candidates:
                        matches, issues = match_document(doc, [candidate], objects.__getitem__)
                        self.assertEqual(issues, [])
                        self.assertEqual([item['archive_url'] for item in matches], [candidate['url']])
                    for order in (candidates, list(reversed(candidates))):
                        matches, issues = match_document(doc, order, objects.__getitem__)
                        if qname:
                            self.assertEqual(matches, [])
                            self.assertEqual([issue['reason'] for issue in issues], ['Неоднозначные схемы'])
                        else:
                            self.assertEqual(issues, [])
                            self.assertEqual([item['archive_url'] for item in matches], [order[0]['url']])

    def test_chameleon_quotation_snapshot_replays_with_its_recorded_rules(self):
        from sync import SITES, encode
        from tests.test_source_collection_regressions import SourceCollectionTests
        from verify import verify_snapshot_replay
        from wsdl_sources import _archive

        fixture = SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        originals = [quoted_document('Тип сообщения 42: Пример', '<r/>'),
                     *(_archive(chameleon_quotation(uri)) for uri in ('urn:p', 'urn:q'))]
        for page in SITES.values():
            fixture.resources[page] = (b'<a href="/specs_sedo/module.docx">DOCX</a>'
                                       b'<a href="/specs_sedo/a.zip">A</a><a href="/specs_sedo/b.zip">B</a>')
            prefix = page.replace('/sedo.html', '/specs_sedo/')
            for name, raw in zip(('module.docx', 'a.zip', 'b.zip'), originals):
                fixture.resources[prefix + name] = raw
        fixture.collect(1, wsdl_entries=[], snapshot_format=10)
        old = fixture.current()
        self.assertEqual(old['format'], 10)
        self.assertTrue(all(row.get('module_matches') for row in old['entries'] if row['kind'] == 'docx'))
        old_digest = '8c2ed8ebad1e269881efad4c7858f87792ea3ce1bf2e9f4912321a1f3f9543af'
        self.assertEqual(sha(encode(old)), old_digest)
        verify_snapshot_replay(fixture.state, old, old_digest)

        fixture.collect(2, wsdl_entries=[])
        current = fixture.current()
        self.assertEqual(current['format'], 11)
        for row in current['entries']:
            if row['kind'] == 'docx':
                self.assertFalse(row.get('module_matches'))
                self.assertEqual([issue['reason'] for issue in row['schema_diagnostics']], ['Неоднозначные схемы'])
        verify_snapshot_replay(fixture.state, current, sha(encode(current)))
        self.assertEqual((fixture.state / 'snapshots' / (old_digest + '.json')).read_bytes(), encode(old))
        for raw in originals:
            self.assertEqual((fixture.state / 'objects' / sha(raw)).read_bytes(), raw)

    def test_missing_7z_reader_is_an_environment_error(self):
        from unittest.mock import patch
        with patch.dict("sys.modules", {"py7zr": None}):
            with self.assertRaisesRegex(ImportError, "py7zr"):
                extract_archives(document())

    def test_module_evidence_keeps_known_empty_and_unknown_namespaces_distinct(self):
        from module_xsd import document_bindings
        from tests.test_schema_recovery import archive, docx
        xs = 'http://www.w3.org/2001/XMLSchema'
        objects, archives = {}, []
        for namespace in ('urn:other', ''):
            attr = f' targetNamespace="{namespace}"' if namespace else ''
            xsd = f'<xs:schema xmlns:xs="{xs}"{attr}><xs:element name="result" type="xs:string"/></xs:schema>'
            raw = archive({'main.xsd': xsd})
            objects[sha(raw)] = raw
            archives.append(dict(kind='zip', environment='prod', url='https://example.test/'+sha(raw),
                                 sha256=sha(raw), **inspect_file(raw, 'zip')))
        for example in ('<result/>', f'<xs:schema xmlns:xs="{xs}"><xs:element name="result"/></xs:schema>'):
            _, doc = docx(['Тип сообщения 777: Результат', example])
            item = dict(environment='prod', url='doc', module_bindings=document_bindings(doc))
            self.assertEqual(match_document(item, archives[:1], objects.__getitem__)[0], [])
            matches, _ = match_document(item, archives, objects.__getitem__)
            self.assertEqual([m['archive_sha256'] for m in matches], [archives[1]['sha256']])
        # Old tables and stored bindings without namespace evidence are still usable.
        item['module_bindings'] = [dict(number='777', root='result', namespace=None)]
        self.assertEqual(match_document(item, archives[:1], objects.__getitem__)[0][0]['namespace'], 'urn:other')
        from tests.test_audit_module_tables import make_body, HEADER
        from xml.etree import ElementTree as ET
        body = make_body('Тип сообщения 777: Результат', [HEADER, ['result', 'DocumentType', '-', '1', '']],
                         '<result/>')
        doc = ET.Element('{'+W+'}document'); doc.append(body)
        self.assertEqual(document_bindings(doc)[0]['namespace'], '')

    def test_imported_named_qname_and_legacy_snapshot_contract(self):
        from tests.test_schema_recovery import archive
        archives, objects = [], {}
        types = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:t"><xs:simpleType name="Code"><xs:restriction base="xs:QName"/></xs:simpleType></xs:schema>'
        for p in ('xs','xsd'):
            main = (f'<{p}:schema xmlns:{p}="http://www.w3.org/2001/XMLSchema" xmlns:t="urn:t" targetNamespace="urn:m">'
                    f'<{p}:import namespace="urn:t" schemaLocation="types.xsd"/><{p}:element name="r" type="t:Code" fixed="{p}:int"/></{p}:schema>')
            raw = archive({'main.xsd':main, 'types.xsd':types})
            objects[sha(raw)]=raw
            archives.append(dict(environment='prod',kind='zip',url='https://example/'+p,sha256=sha(raw),**inspect_file(raw,'zip')))
        doc=dict(environment='prod',url='doc',module_bindings=[dict(number='777',root='r',namespace='urn:m')])
        self.assertEqual(len(match_document(doc,archives,objects.__getitem__)[0]),1)
        self.assertEqual(match_document(doc,archives,objects.__getitem__,legacy=True)[0],[])

    def test_imported_qname_list_item_restriction_keeps_declaration_scope(self):
        from tests.test_schema_recovery import archive
        archives, objects = [], {}
        types = (f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:type">'
                 '<xs:simpleType name="Item"><xs:restriction base="xs:QName"/></xs:simpleType></xs:schema>')
        for prefix in ('p', 'q'):
            main = (f'<xs:schema xmlns:xs="{XS}" xmlns:t="urn:type" targetNamespace="urn:root">'
                    '<xs:import namespace="urn:type" schemaLocation="types.xsd"/>'
                    f'<xs:element name="r" xmlns:{prefix}="urn:item" fixed="{prefix}:a {prefix}:b">'
                    '<xs:simpleType><xs:list itemType="t:Item"/></xs:simpleType></xs:element></xs:schema>')
            raw = archive({'main.xsd': main, 'types.xsd': types})
            digest = sha(raw)
            objects[digest] = raw
            archives.append(dict(environment='prod', kind='zip', url='https://example.test/'+prefix,
                                 sha256=digest, **inspect_file(raw, 'zip')))
        doc = dict(environment='prod', url='doc', module_bindings=[dict(number='42', root='r', namespace='urn:root')])
        selected, issues = match_document(doc, archives, objects.__getitem__)
        self.assertEqual(issues, [])
        self.assertEqual([item['archive_url'] for item in selected], [archives[0]['url']])

    def test_prefix_equivalent_archives_replay_with_original_snapshot_format(self):
        from tests.test_source_collection_regressions import SourceCollectionTests, archive
        from sync import SITES
        fixture = SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for page in SITES.values():
            base = page.rsplit('/', 1)[0] + '/specs_sedo/'
            fixture.resources[page] = (b'<a href="/specs_sedo/file.docx">DOCX</a>'
                                       b'<a href="/specs_sedo/xs.zip">A</a><a href="/specs_sedo/xsd.zip">B</a>')
            for p in ('xs', 'xsd'):
                raw = (f'<{p}:schema xmlns:{p}="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main">'
                       f'<{p}:element name="response" type="{p}:string"/></{p}:schema>').encode()
                fixture.resources[base + p + '.zip'] = archive('main.xsd', raw)
        for expected_format in (2, 3, 4):
            options = {} if expected_format == 4 else {'snapshot_format': expected_format}
            fixture.collect(1, wsdl_entries=[], **options)
            original = json.loads((fixture.state / 'current.json').read_bytes())['snapshot']
            snapshot = fixture.current()
            self.assertEqual(snapshot['format'], expected_format)
            rows = [row for row in snapshot['entries'] if row['kind'] == 'docx']
            self.assertEqual(len(rows), 2)
            for row in rows:
                if expected_format < 4:
                    self.assertEqual(row['schema_diagnostics'][0]['reason'], 'Неоднозначные схемы')
                else:
                    self.assertEqual(len(row['module_matches']), 1)
            fixture.collect(2, wsdl_entries=[], snapshot_format=expected_format)
            replay = json.loads((fixture.state / 'current.json').read_bytes())['snapshot']
            self.assertEqual(replay, original)

    def test_contract_compares_used_qnames_by_uri_and_string_literals_verbatim(self):
        from module_xsd import xml_contract
        def contract(prefix, uri, typ, value):
            return xml_contract((f'<{prefix}:schema xmlns:{prefix}="http://www.w3.org/2001/XMLSchema" '
                                 f'xmlns:v="{uri}"><{prefix}:element name="r" type="{prefix}:{typ}" '
                                 f'fixed="{value}"/></{prefix}:schema>').encode())
        self.assertEqual(contract('xs', 'urn:unused', 'string', 'ok'), contract('xsd', 'urn:unused2', 'string', 'ok'))
        self.assertNotEqual(contract('xs', 'urn:a', 'QName', 'v:ok'), contract('xs', 'urn:b', 'QName', 'v:ok'))
        self.assertNotEqual(contract('xs', 'urn:a', 'string', 'xs:int'), contract('xsd', 'urn:a', 'string', 'xsd:int'))
        self.assertEqual(contract('xs', 'urn:a', 'QName', 'xs:int'), contract('xsd', 'urn:a', 'QName', 'xsd:int'))
        def selector(prefix, ns):
            return xml_contract((f'<s:schema xmlns:s="http://www.w3.org/2001/XMLSchema" xmlns:{prefix}="{ns}">'
                                 f'<s:key name="k"><s:selector xpath=".//{prefix}:row"/>'
                                 f'<s:field xpath="@{prefix}:id"/></s:key></s:schema>').encode())
        self.assertEqual(selector('a', 'urn:test'), selector('b', 'urn:test'))
        self.assertNotEqual(selector('a', 'urn:test'), selector('a', 'urn:other'))

    def test_text_fragments_are_scanned_once_and_preserve_nested_xml(self):
        from module_xsd import xml_fragments
        source = '<outer><inner a=">"/><!-- <ignored> --><![CDATA[<text>]]></outer><next/>'
        stats = {}
        self.assertEqual(list(xml_fragments(source, stats=stats)),
                         [source[:source.index('<next/>')], '<next/>'])
        self.assertLessEqual(stats['characters'], len(source) * 2)
        unfinished = '<xs:schema>' * 400
        stats = {}
        self.assertEqual(list(xml_fragments(unfinished, stats=stats)), [])
        self.assertLessEqual(stats['characters'], len(unfinished) * 2)

    def test_text_fragment_limits_and_recovery_after_mismatched_tag(self):
        from module_xsd import xml_fragments
        self.assertEqual(list(xml_fragments('<broken></other><valid/>')), ['<valid/>'])
        self.assertEqual(list(xml_fragments('< prose ' + 'x' * 9000 + '<valid/>')), ['<valid/>'])
        self.assertEqual(list(xml_fragments('<broken text <valid/>')), ['<valid/>'])
        with self.assertRaisesRegex(ValueError, 'фрагмент'):
            list(xml_fragments('<item>' + 'x' * 100 + '</item>', max_length=32))
        with self.assertRaisesRegex(ValueError, 'глубокий'):
            list(xml_fragments('<item>' * 513))

    def test_inline_schema_identity_ignores_xml_examples_and_unclosed_prose(self):
        import html
        schema = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"></xs:schema>'
        def packages(prefix):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                archive.writestr('word/document.xml', f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{html.escape(prefix + schema)}</w:t></w:r></w:p></w:body></w:document>')
            result, issues = extract_archives(output.getvalue())
            self.assertFalse(issues)
            return result
        plain = packages('')
        self.assertEqual(packages('<r>valid</r>'), plain)
        self.assertEqual(packages('<example> prose '), plain)

    def test_long_inline_schema_tag_keeps_existing_containers_and_original_bytes(self):
        def quote(size):
            return (f'<xs:schema xmlns:xs="{XS}"><xs:element name="r" type="xs:string" '
                    f'fixed="{"a" * size}"/></xs:schema>')

        short, long = quote(8000), quote(8200)
        alternate = f'<s:schema xmlns:s="{XS}"><s:element name="s" type="s:string"/></s:schema>'
        baseline, _ = extract_archives(quoted_document(short, alternate))
        for quotes in ((long,), (long, '<example> unclosed prose', short, alternate)):
            with self.subTest(quotes=len(quotes)):
                packages, issues = extract_archives(quoted_document(*quotes))
                self.assertEqual(issues, [])
                if len(quotes) > 1:
                    self.assertEqual(packages[:2], baseline[:2])
                entry = next((item for item in packages if item['original_sha256'] == sha(long.encode())), None)
                self.assertIsNotNone(entry, 'valid quotation with a long start tag must be retained')
                self.assertEqual(entry['container'], f'word/document.xml#schema-{1 if len(quotes) == 1 else 3}')
                with zipfile.ZipFile(io.BytesIO(entry['content'])) as archive:
                    self.assertEqual(archive.read(entry['filename']), long.encode())

    def test_long_inline_root_does_not_extract_nested_example_as_a_schema(self):
        nested = f'<xs:schema xmlns:xs="{XS}"><xs:element name="r" type="xs:string"/></xs:schema>'
        next_quote = f'<xs:schema xmlns:xs="{XS}"><xs:element name="s" type="xs:string"/></xs:schema>'
        for size, first_index in ((8000, 1), (8200, 3)):
            with self.subTest(size=size):
                outer = (f'<xs:schema xmlns:xs="{XS}" id="{"x" * size}">'
                         f'<xs:annotation><xs:appinfo>{nested}</xs:appinfo></xs:annotation>'
                         '<xs:element name="r" type="xs:int"/></xs:schema>')
                packages, issues = extract_archives(quoted_document(outer, next_quote))
                self.assertEqual(issues, [])
                by_digest = {item['original_sha256']: item for item in packages if not item['container'].endswith('#combined')}
                self.assertEqual(set(by_digest), {sha(outer.encode()), sha(next_quote.encode())})
                self.assertTrue(by_digest[sha(outer.encode())]['container'].endswith(f'#schema-{first_index}'))
                self.assertTrue(by_digest[sha(next_quote.encode())]['container'].endswith('#schema-2'))
                for original in (outer, next_quote):
                    entry = by_digest[sha(original.encode())]
                    with zipfile.ZipFile(io.BytesIO(entry['content'])) as archive:
                        self.assertEqual(archive.read(entry['filename']), original.encode())

    def test_simple_content_qname_lists_keep_local_type_and_value_scope(self):
        from lxml import etree
        from module_xsd import xml_contract

        def schema(prefix, *, namespace='urn:item', order=('a', 'b'), base='Items', fixed=False):
            value = ' '.join(f'{prefix}:{name}' for name in order)
            return (f'<xs:schema xmlns:xs="{XS}">'
                    '<xs:simpleType name="Items"><xs:list itemType="xs:QName"/></xs:simpleType>'
                    '<xs:complexType name="Extended"><xs:simpleContent><xs:extension base="Items"/>'
                    '</xs:simpleContent></xs:complexType>'
                    '<xs:complexType name="Allowed"><xs:simpleContent><xs:restriction base="Extended">'
                    f'<xs:simpleType><xs:restriction base="{base}"/></xs:simpleType>'
                    f'<xs:enumeration xmlns:{prefix}="{namespace}" value="{value}"/>'
                    '</xs:restriction></xs:simpleContent></xs:complexType>'
                    + (f'<xs:element name="r" type="Extended" xmlns:{prefix}="{namespace}" fixed="{value}"/>'
                       if fixed else '<xs:element name="r" type="Allowed"/>') + '</xs:schema>').encode()

        accepted = etree.fromstring(b'<r xmlns:v="urn:item">v:a v:b</r>')
        reversed_order = etree.fromstring(b'<r xmlns:v="urn:item">v:b v:a</r>')
        first, alias = schema('p'), schema('q')
        for source in (first, alias):
            validator = etree.XMLSchema(etree.fromstring(source))
            self.assertTrue(validator.validate(accepted))
            self.assertFalse(validator.validate(reversed_order))
        contract = xml_contract(first)
        self.assertEqual(contract, xml_contract(alias))
        self.assertEqual(xml_contract(schema('p', fixed=True)), xml_contract(schema('q', fixed=True)))
        for different in (schema('q', namespace='urn:different'), schema('q', order=('b', 'a')),
                          schema('q', base='xs:string')):
            self.assertNotEqual(contract, xml_contract(different))
        sources = {}
        candidates = []
        for raw in (first, alias):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                archive.writestr('main.xsd', raw)
            packed = output.getvalue()
            digest = sha(packed)
            sources[digest] = packed
            candidates.append(dict(environment='prod', kind='zip', url='https://example.test/'+digest,
                                   sha256=digest, **inspect_file(packed, 'zip')))
        document = dict(environment='prod', url='doc', module_bindings=[dict(number='42', root='r', namespace='')])
        selected, issues = match_document(document, candidates, sources.__getitem__)
        self.assertEqual(issues, [])
        self.assertEqual([row['archive_url'] for row in selected], [candidates[0]['url']])

    def test_frozen_quotation_snapshots_replay_with_original_semantics(self):
        from tests.test_source_collection_regressions import SourceCollectionTests
        from module_xsd import UNVERSIONED_QUOTATIONS
        from sync import SITES, encode
        from verify import verify_snapshot_replay

        long = (f'<xs:schema xmlns:xs="{XS}"><xs:element name="request" type="xs:string" '
                f'fixed="{"a" * 8200}"/></xs:schema>')
        aliases = [f'<xs:schema xmlns:xs="{XS}" xmlns:{p}="urn:item">'
                   '<xs:simpleType name="Items"><xs:list itemType="xs:QName"/></xs:simpleType>'
                   f'<xs:element name="request" type="Items" fixed="{p}:choice"/></xs:schema>'
                   for p in ('p', 'q')]
        # Frozen from 40de4d9 and the later, unversioned working copy.
        # Keep Word and archive bytes identical across both replays.
        expected = {
            'long': ('afe1e04b15c28188c58274ba5cc43e885e809e9c029c4e377665b69967e9fa67',
                     '3a5afe5c3395a8b85949695c0277c9396a01408a7602cd32fc42372927c8e911',
                     'a32ddb170e76db1081e93c0b0811d9aa354fbeff5fcee0a33302b69ca3d33783'),
            'qname': ('c9cc4741bf2732ea5d0a963e13da36d996432ef32ef9f029677fe85ecb5f8b33',
                      'f8c445b37cba5cd4febd5dacc073ce942dbb775cf0fe5e0a5cf585caae505846',
                      'cb4c466228748e7768d4e3e310ffcb1afa1ef8b8d1e8a3ee381f1c66996649d9'),
        }
        transitional = {
            'long': ('6ed3a541d63fe288d1ef8a5366561c68b8665e4109c0068f4cf8c8e9999a3d0a',
                     'bb57ab53b7e40118143d134cead6c66236a6e3bc3d037de00a3a7b43c6328ccd',
                     '648080bc3c666303eae104fc5b43f5a7821669e503148d9bbd24e63e6516502f'),
            'qname': (expected['qname'][0],
                      '7f12e6e3d64cebf5b16f41442b4e473a2011394a3c8d28ec7936c2595289f60c',
                      'e5db11c5b5406193039117a0a5f95a06ed25149d7315153589f6010e86b67d08'),
        }
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, 'w') as archive:
            entry = zipfile.ZipInfo('other.xsd')
            entry.create_system = 0
            archive.writestr(entry, f'<xs:schema xmlns:xs="{XS}"><xs:element name="other" type="xs:string"/></xs:schema>')
        for name, quotes in (('long', [long]), ('qname', aliases)):
            for format, digest, newer_digest in zip((2, 4, 7), expected[name], transitional[name]):
                with self.subTest(name=name, format=format):
                    fixture = SourceCollectionTests()
                    fixture.setUp()
                    self.addCleanup(fixture.doCleanups)
                    document = quoted_document('Тип сообщения 42: Example', *quotes, stamp=(2026, 11, 19, 19, 51, 4))
                    for page in SITES.values():
                        fixture.resources[page] = (b'<a href="/specs_sedo/fixture.docx">DOCX</a>'
                                                   b'<a href="/specs_sedo/fixture.zip">ZIP</a>')
                        base = page.rsplit('/', 1)[0] + '/specs_sedo/'
                        fixture.resources[base + 'fixture.docx'] = document
                        fixture.resources[base + 'fixture.zip'] = archive_bytes.getvalue()
                    fixture.collect(1, wsdl_entries=[], snapshot_format=format)
                    recorded = json.loads((fixture.state / 'current.json').read_bytes())['snapshot']
                    self.assertEqual(recorded, digest)
                    if digest != newer_digest:
                        fixture.collect(2, wsdl_entries=[], snapshot_format=format, quotation_rules=UNVERSIONED_QUOTATIONS)
                        self.assertEqual(json.loads((fixture.state / 'current.json').read_bytes())['snapshot'],
                                         newer_digest)
                        verify_snapshot_replay(fixture.state, fixture.current(), newer_digest)
                    fixture.collect(3, wsdl_entries=[])
                    snapshot = fixture.current()
                    self.assertEqual(snapshot['format'], 8)
                    rows = [row for row in snapshot['entries'] if row['kind'] == 'docx']
                    self.assertTrue(all(row['module_matches'] for row in rows))
                    fixture.collect(4, wsdl_entries=[], snapshot_format=snapshot['format'])
                    self.assertEqual(fixture.current(), snapshot)
                    verify_snapshot_replay(fixture.state, snapshot, sha(encode(snapshot)))

    def test_qname_union_in_simple_content_has_versioned_value_identity(self):
        from lxml import etree
        from module_xsd import xml_contract
        from sync import SITES, encode
        from tests.test_source_collection_regressions import SourceCollectionTests
        from verify import verify_snapshot_replay

        def schema(prefix, first='xs:boolean', uri='urn:item', names=('a', 'b'), *, value=None, namespace=''):
            if value is None:
                value = ' '.join(f'{prefix}:{name}' for name in names)
            return (f'<xs:schema xmlns:xs="{XS}">'
                    f'<xs:simpleType name="Item"><xs:union memberTypes="{first} xs:QName"/></xs:simpleType>'
                    '<xs:simpleType name="Items"><xs:list itemType="Item"/></xs:simpleType>'
                    '<xs:complexType name="Base"><xs:simpleContent><xs:extension base="Items"/></xs:simpleContent></xs:complexType>'
                    '<xs:complexType name="Limited"><xs:simpleContent><xs:restriction base="Base">'
                    '<xs:simpleType><xs:restriction base="Items"/></xs:simpleType>'
                    f'<xs:enumeration xmlns:{prefix}="{uri}"{namespace} value="{value}"/>'
                    '</xs:restriction></xs:simpleContent></xs:complexType>'
                    '<xs:element name="r" type="Limited"/></xs:schema>')

        first, alias = schema('p'), schema('q')
        for text in (first, alias):
            validator = etree.XMLSchema(etree.fromstring(text.encode()))
            self.assertTrue(validator.validate(etree.fromstring(b'<r xmlns:x="urn:item">x:a x:b</r>')))
            self.assertFalse(validator.validate(etree.fromstring(b'<r xmlns:x="urn:item">x:b x:a</r>')))

        fixture = SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        raw = quoted_document('Тип сообщения 42: Example', first, alias)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            entry = zipfile.ZipInfo('other.xsd')
            entry.create_system = 0
            archive.writestr(entry, f'<xs:schema xmlns:xs="{XS}"><xs:element name="other"/></xs:schema>')
        neighbor = buffer.getvalue()
        for site in SITES.values():
            fixture.resources[site] = (b'<a href="/specs_sedo/fixture.docx">DOCX</a>'
                                       b'<a href="/specs_sedo/other.zip">ZIP</a>')
            folder = site.rsplit('/', 1)[0]+'/specs_sedo/'
            fixture.resources[folder+'fixture.docx'] = raw
            fixture.resources[folder+'other.zip'] = neighbor
        fixture.collect(1, wsdl_entries=[], snapshot_format=8)
        old = fixture.current()
        self.assertTrue(all(not row.get('module_matches') for row in old['entries'] if row['kind']=='docx'))
        frozen = 'ee2b9ecd396fe4153493e4eec9bd59f278a3e3e6163311765430d84ce9afa096'
        self.assertEqual(sha(encode(old)), frozen)
        verify_snapshot_replay(fixture.state, old, frozen)

        self.assertEqual(xml_contract(first.encode()), xml_contract(alias.encode()))
        self.assertNotEqual(xml_contract(first.encode()), xml_contract(schema('q', uri='urn:other').encode()))
        self.assertNotEqual(xml_contract(first.encode()), xml_contract(schema('q', names=('b', 'a')).encode()))
        self.assertNotEqual(xml_contract(schema('p', first='xs:string').encode()),
                            xml_contract(schema('q', first='xs:string').encode()))
        fixture.collect(2, wsdl_entries=[], snapshot_format=9)
        old = fixture.current()
        self.assertTrue(all(row.get('module_matches') for row in old['entries'] if row['kind']=='docx'))
        frozen = 'd888a1df1cba67f667cda72c6be913824db37e18d4c18d75d99b0945b9b96893'
        self.assertEqual(sha(encode(old)), frozen)
        verify_snapshot_replay(fixture.state, old, frozen)

        qname = schema('p', value='p:a x:true', namespace=' xmlns:x="boolean"')
        boolean = schema('p', value='p:a true')
        samples = (b'<r xmlns:p="urn:item" xmlns:x="boolean">p:a x:true</r>',
                   b'<r xmlns:p="urn:item">p:a true</r>')
        for index, candidate in enumerate((qname, boolean)):
            validator = etree.XMLSchema(etree.fromstring(candidate.encode()))
            self.assertEqual([validator.validate(etree.fromstring(raw)) for raw in samples],
                             [index == 0, index == 1])
        self.assertNotEqual(xml_contract(qname.encode()), xml_contract(boolean.encode()))
        self.assertEqual(xml_contract(boolean.encode()), xml_contract(schema('q', value='q:a 1').encode()))
        self.assertNotEqual(xml_contract(boolean.encode()), xml_contract(schema('q', value='q:a 0').encode()))
        self.assertEqual(xml_contract(schema('p', first='xs:string').encode()),
                         xml_contract(schema('p', first='xs:string', uri='urn:unused').encode()))
        self.assertNotEqual(xml_contract(qname.encode()), xml_contract(schema('p', value='p:a x:true').encode()))

        for site in SITES.values():
            folder = site.rsplit('/', 1)[0]+'/specs_sedo/'
            fixture.resources[folder+'fixture.docx'] = quoted_document('Тип сообщения 42: Example', qname, boolean)
        fixture.collect(3, wsdl_entries=[])
        current = fixture.current()
        self.assertEqual(current['format'], 10)
        self.assertTrue(all(not row.get('module_matches') for row in current['entries'] if row['kind']=='docx'))
        verify_snapshot_replay(fixture.state, current, sha(encode(current)))

    def test_quotation_format_does_not_change_other_document_revision(self):
        from tests.test_source_collection_regressions import SourceCollectionTests, archive
        from sync import SITES
        fixture = SourceCollectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for page in SITES.values():
            fixture.resources[page.replace('/sedo.html', '/specs_sedo/file.zip')] = archive(
                'root.xsd', (f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main">'
                             '<xs:element name="response" type="xs:string"/></xs:schema>').encode())
        fixture.collect(1, wsdl_entries=[])
        previous = {row['document_url']: row['revision_id'] for row in derive(fixture.current())['revisions']}
        self.assertTrue(previous)
        page = SITES['test']
        url = page.replace('/sedo.html', '/specs_sedo/quote.docx')
        fixture.resources[page] += b'<a href="/specs_sedo/quote.docx">Quote</a>'
        quote = (f'<xs:schema xmlns:xs="{XS}" id="{"x" * 8200}">'
                 '<xs:element name="additional" type="xs:string"/></xs:schema>')
        fixture.resources[url] = quoted_document('Тип сообщения 42: Example', quote)
        fixture.collect(2, wsdl_entries=[])
        snapshot = fixture.current()
        self.assertEqual(snapshot['format'], 8)
        current = {row['document_url']: row['revision_id'] for row in derive(snapshot)['revisions']}
        self.assertEqual({key: current[key] for key in previous}, previous)
        self.assertIn(url, current)

    def test_oversized_inline_tag_reports_limit_and_recovers_later_schema(self):
        from unittest.mock import patch
        oversized = f'<xs:schema xmlns:xs="{XS}" id="{"a" * 11000}"/>'
        valid = f'<xs:schema xmlns:xs="{XS}"><xs:element name="r" type="xs:string"/></xs:schema>'
        document = quoted_document('Тип сообщения 42: Test', oversized, valid)
        with patch('module_xsd.MAX_FILE', 10000):
            packages, issues = extract_archives(document)
            meta = inspect_file(document, 'docx')
        self.assertEqual(len(issues), 1)
        self.assertRegex(issues[0]['reason'], r'позиции \d+.*лимит 10000')
        self.assertEqual([item['original_sha256'] for item in packages], [sha(valid.encode())])
        self.assertEqual([item['root'] for item in meta['module_bindings']], ['r'])

    def test_inline_qname_lists_use_ordered_expanded_names_for_all_values(self):
        def quote(prefix, kind, *, namespace='urn:item', names=('a', 'b'), item='xs:QName'):
            list_type = f'<xs:simpleType name="Items"><xs:list itemType="{item}"/></xs:simpleType>'
            value = ' '.join(prefix+':'+name for name in names)
            if kind == 'enumeration':
                declaration = ('<xs:simpleType name="Allowed"><xs:restriction base="Items">'
                               f'<xs:enumeration value="{value}"/></xs:restriction></xs:simpleType>'
                               '<xs:element name="r" type="Allowed"/>')
            else:
                declaration = f'<xs:element name="r" type="Items" {kind}="{value}"/>'
            return f'<xs:schema xmlns:xs="{XS}" xmlns:{prefix}="{namespace}">{list_type}{declaration}</xs:schema>'

        def matches(*quotes):
            packages, issues = extract_archives(quoted_document(*quotes))
            self.assertEqual(issues, [])
            objects, embedded = {}, []
            for package in packages:
                raw = package['content']
                digest = sha(raw)
                objects[digest] = raw
                embedded.append(dict(package, sha256=digest, **inspect_file(raw, 'zip')))
            document = dict(environment='prod', url='https://example.test/module.docx', embedded_archives=embedded,
                            module_bindings=[dict(number='42', title='Test', root='r', namespace='')])
            return *match_document(document, [], objects.__getitem__), objects

        for kind in ('enumeration', 'fixed', 'default'):
            with self.subTest(kind=kind):
                first, alias = quote('p', kind), quote('q', kind)
                for src in (first, alias):
                    selected, issues, _ = matches(src)
                    self.assertEqual(issues, [])
                    self.assertEqual(len(selected), 1)
                selected, issues, objects = matches(first, alias)
                self.assertEqual(issues, [])
                self.assertEqual(len(selected), 1)
                item = selected[0]
                self.assertEqual(item['archive_url'], 'https://example.test/module.docx#embedded=word/document.xml#schema-1')
                with zipfile.ZipFile(io.BytesIO(objects[item['archive_sha256']])) as archive:
                    self.assertEqual(archive.read(item['schema_path']), first.encode())
                for different in (quote('q', kind, namespace='urn:other'), quote('q', kind, names=('b', 'a'))):
                    selected, issues, _ = matches(first, different)
                    self.assertEqual(selected, [])
                    self.assertEqual(issues[0]['reason'], 'Неоднозначные схемы')
                selected, issues, _ = matches(quote('p', kind, item='xs:string'), quote('q', kind, item='xs:string'))
                self.assertEqual(selected, [])
                self.assertEqual(issues[0]['reason'], 'Неоднозначные схемы')

    def test_service_documents_keep_their_archived_inline_schema_identities(self):
        state = ROOT / 'state'
        pointer = json.loads((state / 'current.json').read_bytes())
        snapshots = [pointer['snapshot'], *(item['snapshot'] for item in reversed(pointer['transitions']))]
        remaining = {
            '74dd3b6042f6290108956b859825451f403d6c683213d10eb36074f96e7840c6',
            '235809a495df2a8e31e8b6014a4518186c1d1c7c2412a9859ae887e4d41da601',
        }
        # An official replacement can remove the old DOCX from the active snapshot.
        for snapshot in dict.fromkeys(snapshots):
            entries = json.loads((state / 'snapshots' / f'{snapshot}.json').read_bytes())['entries']
            for row in entries:
                for item in row.get('embedded_archives', []):
                    original = item.get('original_sha256')
                    if original not in remaining:
                        continue
                    packages, _ = extract_archives((state / 'objects' / row['sha256']).read_bytes())
                    package = next(p for p in packages if p['container'] == item['container'])
                    self.assertEqual(package['original_sha256'], original)
                    self.assertEqual(sha(package['content']), item['sha256'])
                    remaining.remove(original)
            if not remaining:
                break
        self.assertEqual(remaining, set(), 'Historic inline schema missing from snapshot history')

    def test_embedded_inline_and_combined_archives_use_platform_independent_headers(self):
        import html
        schema = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"></xs:schema>'
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as document_zip:
            document_zip.writestr('word/embeddings/one.xsd', schema)
            document_zip.writestr('word/embeddings/two.xsd', schema.replace('><', ' targetNamespace="urn:two"><'))
            inline = html.escape(schema.replace('><', ' targetNamespace="urn:inline"><'))
            document_zip.writestr('word/document.xml', f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{inline}</w:t></w:r></w:p></w:body></w:document>')
        packages, issues = extract_archives(output.getvalue())
        self.assertFalse(issues)
        self.assertEqual(len(packages), 4)
        for package in packages:
            with zipfile.ZipFile(io.BytesIO(package['content'])) as archive:
                self.assertTrue(all(item.create_system == 3 for item in archive.infolist()), package['container'])

    def test_embedded_xsd_is_deterministic_and_retains_original_bytes(self):
        first,issues=extract_archives(document());second,_=extract_archives(document())
        self.assertFalse(issues);self.assertEqual(len(first),1)
        self.assertEqual(first[0]['content'],second[0]['content'])
        self.assertEqual(first[0]['filename'],'XSD.7z')
        with zipfile.ZipFile(io.BytesIO(first[0]['content'])) as z:
            self.assertIn('v01/rust/RustDocumentsAsv.xsd',z.namelist())

    def test_module_binding_and_match_uses_all_dependencies(self):
        raw=document();meta=inspect_file(raw,'docx')
        self.assertEqual(meta['module_bindings'][0]['root'],'DecisionDiscountRust')
        packages,_=extract_archives(raw);objects={};archives=[]
        for package in packages:
            content=package.pop('content');digest=sha(content);objects[digest]=content
            archives.append(dict(package,sha256=digest,**inspect_file(content,'zip')))
        r=dict(meta,environment='prod',kind='docx',url='https://example.test/spec.docx',label='module',sha256=sha(raw),embedded_archives=archives)
        matches,issues=match_document(r,[],lambda digest:objects[digest])
        self.assertFalse(issues);self.assertEqual(len(matches),1)
        self.assertEqual(matches[0]['number'],'600')
        self.assertEqual(matches[0]['schema_path'],'v01/rust/RustDocumentsAsv.xsd')
        self.assertGreater(len(matches[0]['dependencies']),1)
        compiled=derive(dict(entries=[dict(r,module_matches=matches)]))
        self.assertEqual(compiled['revisions'][0]['root'],'DecisionDiscountRust')
        self.assertEqual(compiled['revisions'][0]['source_type'],'module')

    def test_sync_discovers_companion_archives_and_binds_both_contours(self):
        from unittest.mock import patch
        from sync import sync, SITES
        raw=document()
        # Use the real embedded fixture to prove sync records extracted objects,
        # while the auxiliary page separately proves the previously missed crawl.
        sources={}
        empty=io.BytesIO()
        with zipfile.ZipFile(empty,'w') as z:z.writestr('empty.xsd',b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>')
        for env,url in SITES.items():
            base=url.rsplit('/',1)[0]
            companion_host = 'https://lk.fss.ru' if env == 'prod' else base
            sources[url]=(f'<a href="/specs_sedo/source.zip">XSD</a><a href="prab.html">Process</a><a href="{companion_host}//mchd.html">MCHD</a><a href="/specs_sedo/module.docx">v1.0 (01.11.2026)</a>').encode()
            sources[base+'/specs_sedo/source.zip']=empty.getvalue();sources[base+'/specs_sedo/module.docx']=raw
            sources[base+'/prab.html']=b'<a href="/prab/card.zip">Card</a><a href="https://other.example/prab/foreign.zip">Ignore</a>'
            sources[base+'/prab/card.zip']=empty.getvalue()
            sources[base+'/mchd.html']=b'<a href="/mchd/mchd-xsd.zip">XSD</a>'
            sources[base+'/mchd/mchd-xsd.zip']=empty.getvalue()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('sync.get',side_effect=lambda u:sources[u]):sync(root)
            key=json.loads((root/'current.json').read_bytes())['snapshot']
            snapshot=json.loads((root/'snapshots'/(key+'.json')).read_bytes())
            docs=[r for r in snapshot['entries'] if r['kind']=='docx']
            self.assertEqual({r['environment'] for r in docs},{'prod','test'})
            for doc in docs:
                self.assertEqual(doc['module_matches'][0]['number'],'600')
                self.assertTrue((root/'objects'/doc['module_matches'][0]['archive_sha256']).exists())
            self.assertEqual(len([r for r in snapshot['entries'] if r.get('source_page')]),4)

    def test_conflicting_rules_are_not_deduplicated(self):
        objects={};archives=[]
        for length in [5,6]:
            out=io.BytesIO()
            schema=f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:test"><xs:element name="item"><xs:simpleType><xs:restriction base="xs:string"><xs:maxLength value="{length}"/></xs:restriction></xs:simpleType></xs:element></xs:schema>'
            with zipfile.ZipFile(out,'w') as z:z.writestr('main.xsd',schema)
            content=out.getvalue();digest=sha(content);objects[digest]=content
            archives.append(dict(environment='prod',kind='zip',url='https://example/'+str(length)+'.zip',sha256=digest,**inspect_file(content,'zip')))
        doc=dict(environment='prod',url='https://example/doc.docx',module_bindings=[dict(number='88',title='notice',root='item',namespace='urn:test')])
        matches,issues=match_document(doc,archives,lambda d:objects[d])
        self.assertFalse(matches);self.assertTrue(any('Неоднозначные' in i['reason'] for i in issues))

    def test_namespace_form_defaults_are_not_deduplicated(self):
        objects={};archives=[]
        for form in ['qualified','unqualified']:
            out=io.BytesIO()
            schema=f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:test" elementFormDefault="{form}"><xs:element name="item"><xs:complexType><xs:sequence><xs:element name="child" type="xs:string"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
            with zipfile.ZipFile(out,'w') as z:z.writestr('main.xsd',schema)
            content=out.getvalue();digest=sha(content);objects[digest]=content
            archives.append(dict(environment='prod',kind='zip',url='https://example/'+form+'.zip',sha256=digest,**inspect_file(content,'zip')))
        doc=dict(environment='prod',url='https://example/doc.docx',module_bindings=[dict(number='88',title='notice',root='item',namespace='urn:test')])
        matches,issues=match_document(doc,archives,lambda d:objects[d])
        self.assertFalse(matches);self.assertTrue(any('Неоднозначные' in i['reason'] for i in issues))

    def test_direct_xsd_limit_and_embedding_limit_before_read(self):
        from unittest.mock import patch
        with patch('module_xsd.MAX_FILE',4):
            with self.assertRaises(ValueError):unpack_schemas('large.xsd',b'12345')
            packages,issues=extract_archives(document())
            self.assertFalse(packages);self.assertTrue(issues)

    def test_fixed_qname_namespace_context_is_distinct(self):
        objects={};archives=[]
        for namespace in ['urn:A','urn:B']:
            out=io.BytesIO()
            schema=f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:p="{namespace}" targetNamespace="urn:test"><xs:element name="item" type="xs:QName" fixed="p:value"/></xs:schema>'
            with zipfile.ZipFile(out,'w') as z:z.writestr('main.xsd',schema)
            content=out.getvalue();digest=sha(content);objects[digest]=content
            archives.append(dict(environment='prod',kind='zip',url='https://example/archive.zip',sha256=digest,**inspect_file(content,'zip')))
        doc=dict(environment='prod',url='https://example/doc.docx',module_bindings=[dict(number='88',title='notice',root='item',namespace='urn:test')])
        matches,issues=match_document(doc,archives,lambda d:objects[d])
        self.assertFalse(matches);self.assertTrue(any('Неоднозначные' in i['reason'] for i in issues))

    def test_uncompressed_budget_is_shared_by_embedded_packages(self):
        from unittest.mock import patch
        archives=[]
        for marker in ['a','b']:
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('a.xsd',marker.encode()*150)
            archives.append(out.getvalue())
        self.assertLess(sum(map(len,archives)),250)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            for i,content in enumerate(archives):z.writestr('word/embeddings/'+str(i)+'.zip',content)
        with patch('module_xsd.MAX_TOTAL',250):packages,issues=extract_archives(out.getvalue())
        self.assertTrue(issues)
        self.assertLessEqual(len(packages),1)

    def test_unsafe_archive_path_is_rejected(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('../escape.xsd',b'<schema/>')
        with self.assertRaises(ValueError):unpack_schemas('bad.zip',out.getvalue())

    def test_native_package_rejects_truncated_payload(self):
        with self.assertRaises(ValueError):native_package(b'bad')

    def test_external_archive_respects_contour_and_namespace(self):
        raw=b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:test"><xs:element name="item" type="xs:string"/></xs:schema>'
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('main.xsd',raw)
        content=out.getvalue();digest=sha(content)
        archive=dict(environment='test',kind='zip',url='https://test.example/schemas.zip',sha256=digest,**inspect_file(content,'zip'))
        doc=dict(environment='prod',url='https://example/spec.docx',module_bindings=[dict(number='88',title='notice',root='item',namespace='urn:test')])
        matches,issues=match_document(doc,[archive],lambda _:content)
        self.assertFalse(matches);self.assertTrue(issues)
        doc['environment']='test';matches,issues=match_document(doc,[archive],lambda _:content)
        self.assertEqual(len(matches),1);self.assertFalse(issues)

if __name__=='__main__':unittest.main()
