import base64
import io
import json
import sys
import tempfile
import subprocess
import unittest
import zipfile
from pathlib import Path
from lxml import etree

from schema import Schema
from xsd_bundle import _prepare, _zip
from xsd_validate import compile_schema

XS = "http://www.w3.org/2001/XMLSchema"

def fixture():
    main = f'''<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" xmlns:a="urn:attrs" targetNamespace="urn:main">
<xs:import namespace="urn:attrs" schemaLocation="attrs.xsd"/>
<xs:element name="request"><xs:complexType><xs:sequence><xs:element name="item" minOccurs="0" maxOccurs="unbounded"><xs:complexType>
<xs:attribute ref="a:editable" fixed="false"/><xs:attribute ref="a:label" default="local"/><xs:attribute xmlns:k="urn:value" ref="a:kind" fixed="k:known"/><xs:attribute ref="a:token" fixed="a b"/>
</xs:complexType></xs:element></xs:sequence></xs:complexType></xs:element></xs:schema>'''.encode()
    attrs = f'''<xs:schema xmlns:xs="{XS}" targetNamespace="urn:attrs">
<xs:attribute name="editable" type="xs:boolean"/><xs:attribute name="label" type="xs:string" default="global"/><xs:attribute name="kind" type="xs:QName"/><xs:attribute name="token"><xs:simpleType><xs:restriction base="xs:token"/></xs:simpleType></xs:attribute>
</xs:schema>'''.encode()
    row = dict(number="42", environment="prod", revision_id="a"*64, schema_path="main.xsd", root="request", namespace="urn:main")
    return row, {"main.xsd": main, "attrs.xsd": attrs}

def dynamic_fixture(kind='dynamic'):
    row, files=fixture()
    if kind.startswith('fixed-no-namespace-'):
        scalar = 'QName' if kind.endswith('qname') else 'decimal'
        fixed = 'known' if scalar == 'QName' else '1'
        element_default = kind == 'fixed-no-namespace-local-default-restricted'
        extension = '<xs:extension base="Code"/>' if element_default else '<xs:extension xmlns="" base="Code"/>'
        schema_scope = 'xmlns:m="urn:main"' if element_default else 'xmlns="urn:main"'
        element_scope = ' xmlns="urn:main"' if element_default else ''
        if kind.endswith('-restricted'):
            content = ('<xs:complexType name="Base"><xs:simpleContent>'+extension+'</xs:simpleContent></xs:complexType>')
            base = 'm:Base' if element_default else 'Base'
            derivation = f'<xs:restriction base="{base}"><xs:minInclusive value="1"/></xs:restriction>'
        else:
            content, derivation = '', extension
        return row, {
            'main.xsd': (f'<xs:schema xmlns:xs="{XS}" {schema_scope} targetNamespace="urn:main">'
                         '<xs:import schemaLocation="code.xsd"/>'+content+
                         f'<xs:element{element_scope} name="request" fixed="{fixed}"><xs:complexType><xs:simpleContent>{derivation}'
                         '</xs:simpleContent></xs:complexType></xs:element></xs:schema>').encode(),
            'code.xsd': (f'<xs:schema xmlns:xs="{XS}"><xs:simpleType name="Code">'
                         f'<xs:restriction base="xs:{scalar}"/></xs:simpleType></xs:schema>').encode(),
        }
    if kind == 'implicit-xml':
        namespace = 'http://www.w3.org/XML/1998/namespace'
        return row, {'main.xsd': (f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main">'
                                 f'<xs:import namespace="{namespace}" schemaLocation="xml.xsd"/>'
                                 '<xs:element name="request"><xs:complexType><xs:attribute ref="xml:lang" use="required"/>'
                                 '</xs:complexType></xs:element></xs:schema>').encode(),
                     'xml.xsd': f'<xs:schema xmlns:xs="{XS}" targetNamespace="{namespace}"><xs:attribute name="lang" type="xs:language"/></xs:schema>'.encode()}
    if kind == 'annotated':
        return row, {'main.xsd': (f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main">'
                                 '<xs:complexType name="Annotated"><xs:annotation><xs:appinfo>'
                                 '<price type="currency"/><xs:element type="UnknownQuotedType"/>'
                                 '</xs:appinfo></xs:annotation><xs:sequence><xs:element name="amount" type="xs:decimal" fixed="1.00"/>'
                                 '</xs:sequence></xs:complexType><xs:element name="request" type="m:Annotated" xmlns:m="urn:main"/></xs:schema>').encode()}
    if kind == 'chameleon':
        files = {
            'main.xsd': (f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main" xmlns:m="urn:main">'
                         '<xs:include schemaLocation="attributes.xsd"/><xs:simpleType name="Code">'
                         '<xs:restriction base="xs:string"><xs:pattern value="[A-Z]+"/></xs:restriction></xs:simpleType>'
                         '<xs:element name="request"><xs:complexType><xs:attribute ref="m:code" fixed="OK"/>'
                         '</xs:complexType></xs:element></xs:schema>').encode(),
            'attributes.xsd': (f'<xs:schema xmlns:xs="{XS}"><xs:attribute name="code" type="Code"/></xs:schema>').encode()}
        return row, files
    if kind=='dynamic':
        declarations='''<xs:complexType name="Leaf"><xs:attribute ref="a:editable"/></xs:complexType>
<xs:complexType name="FixedLeaf"><xs:complexContent><xs:restriction base="m:Leaf"><xs:attribute ref="a:editable" fixed="false"/></xs:restriction></xs:complexContent></xs:complexType>
<xs:complexType name="Base"><xs:sequence><xs:element name="child" type="m:Leaf" minOccurs="0"/></xs:sequence><xs:attribute ref="a:editable"/></xs:complexType>
<xs:complexType name="Derived"><xs:complexContent><xs:restriction base="m:Base"><xs:sequence><xs:element name="child" type="m:FixedLeaf" minOccurs="0"/></xs:sequence><xs:attribute ref="a:editable" fixed="false"/></xs:restriction></xs:complexContent></xs:complexType>
<xs:element name="request" type="m:Base"/>'''
    else:
        declarations='''<xs:complexType name="BooleanWithFixed"><xs:simpleContent><xs:extension base="xs:boolean"><xs:attribute ref="a:editable" fixed="false"/></xs:extension></xs:simpleContent></xs:complexType><xs:element name="request" type="xs:boolean"/>'''
    if kind == 'unused-recursive':
        declarations = '''<xs:complexType name="Unused"><xs:sequence>
<xs:element name="child" type="m:Unused" minOccurs="0"/>
</xs:sequence><xs:attribute ref="a:editable" fixed="false"/></xs:complexType>
<xs:element name="request" type="xs:string"/>'''
    if kind in ('inactive-element', 'inactive-group'):
        particle = '<xs:element name="child" type="m:Dynamic"'
        if kind == 'inactive-element':
            group, model = '', particle+' minOccurs="0" maxOccurs="0"/>'
        else:
            group = '<xs:group name="Children"><xs:sequence>'+particle+'/></xs:sequence></xs:group>'
            model = '<xs:group ref="m:Children" minOccurs="0" maxOccurs="0"/>'
        declarations = (group+'<xs:complexType name="Dynamic"><xs:sequence>'+model+
                        '</xs:sequence><xs:attribute ref="a:editable" fixed="false"/></xs:complexType>'
                        '<xs:element name="request" type="xs:anyType"/>')
    if kind == 'extended-any-type':
        declarations = '''<xs:complexType name="Leaf"><xs:attribute ref="a:editable" fixed="false"/></xs:complexType>
<xs:element name="known" type="m:Leaf"/>
<xs:complexType name="Extended"><xs:complexContent mixed="true"><xs:extension base="xs:anyType">
<xs:attribute ref="a:editable" fixed="false"/></xs:extension></xs:complexContent></xs:complexType>
<xs:element name="request" type="m:Extended"/>'''
    if kind == 'derived-scalar':
        declarations = '''<xs:simpleType name="Text"><xs:restriction base="xs:token"/></xs:simpleType>
<xs:simpleType name="Either"><xs:union memberTypes="xs:int xs:string"/></xs:simpleType>
<xs:complexType name="TextWithFixed"><xs:simpleContent><xs:extension base="m:Text">
<xs:attribute ref="a:editable" fixed="false"/></xs:extension></xs:simpleContent></xs:complexType>
<xs:element name="request" type="m:Either"/>'''
    if kind in ('named-base', 'anonymous-base'):
        restriction = ('<xs:restriction base="xs:string"/>' if kind == 'named-base' else
                       '<xs:restriction><xs:simpleType><xs:restriction base="xs:string"/>'
                       '</xs:simpleType></xs:restriction>')
        declarations = f'''<xs:simpleType name="Middle">{restriction}</xs:simpleType>
<xs:complexType name="Checked"><xs:simpleContent><xs:extension base="m:Middle">
<xs:attribute ref="a:editable" fixed="false"/></xs:extension></xs:simpleContent></xs:complexType>
<xs:element name="request" type="xs:string"/>'''
    if kind.startswith('choice-'):
        second = {'choice-unfixed': '', 'choice-same': ' fixed="1"',
                  'choice-different': ' fixed="2"', 'choice-repeated': '',
                  'choice-qualified': ''}[kind]
        repeat = ' maxOccurs="unbounded"' if kind == 'choice-repeated' else ''
        first_marker = 'name="marker" form="qualified"' if kind == 'choice-qualified' else 'name="a"'
        second_marker = 'name="marker"' if kind == 'choice-qualified' else 'name="b"'
        disabled = ('<xs:sequence minOccurs="0" maxOccurs="0"><xs:element name="disabled" type="m:ChoiceType"/>'
                    '</xs:sequence>') if kind == 'choice-qualified' else ''
        declarations = f'''<xs:element name="request" type="xs:anyType"/>
<xs:complexType name="ChoiceType"><xs:choice{repeat}>
<xs:sequence><xs:element {first_marker}/><xs:element name="value" type="xs:decimal" fixed="1"/></xs:sequence>
{disabled}
<xs:sequence><xs:element {second_marker}/><xs:element name="value" type="xs:decimal"{second}/></xs:sequence>
</xs:choice></xs:complexType>'''
    if kind in ('strict-derived', 'skip-recursive', 'lax-recursive'):
        mode = kind.split('-')[0]
        declarations = f'''<xs:element name="request"><xs:complexType><xs:sequence>
<xs:any namespace="##targetNamespace" processContents="{mode}"/>
</xs:sequence></xs:complexType></xs:element>
<xs:element name="wrapper"><xs:complexType><xs:sequence>
<xs:element name="child" type="m:Leaf"/>
</xs:sequence></xs:complexType></xs:element>
<xs:complexType name="Leaf"><xs:attribute ref="a:editable"/></xs:complexType>
<xs:complexType name="FixedLeaf"><xs:complexContent><xs:restriction base="m:Leaf">
<xs:attribute ref="a:editable" fixed="false"/></xs:restriction></xs:complexContent></xs:complexType>
<xs:complexType name="Unused"><xs:sequence><xs:element name="child" type="m:Unused" minOccurs="0"/>
</xs:sequence><xs:attribute ref="a:editable" fixed="false"/></xs:complexType>'''
    if kind=='equivalent':
        declarations='''<xs:complexType name="Base"><xs:attribute ref="a:editable" fixed="false"/></xs:complexType><xs:complexType name="Derived"><xs:complexContent><xs:restriction base="m:Base"><xs:attribute ref="a:editable" fixed="0"/></xs:restriction></xs:complexContent></xs:complexType><xs:element name="request" type="m:Base"/>'''
    if kind=='narrowed':
        declarations='''<xs:element name="item"><xs:complexType><xs:attribute ref="a:editable" fixed="false"/></xs:complexType></xs:element>
<xs:complexType name="Base"><xs:sequence><xs:element name="wrapper" type="xs:anyType"/></xs:sequence></xs:complexType>
<xs:complexType name="Known"><xs:sequence><xs:element name="item" form="qualified"><xs:complexType><xs:attribute ref="a:editable"/></xs:complexType></xs:element></xs:sequence></xs:complexType>
<xs:complexType name="Derived"><xs:complexContent><xs:restriction base="m:Base"><xs:sequence><xs:element name="wrapper" type="m:Known"/></xs:sequence></xs:restriction></xs:complexContent></xs:complexType>
<xs:element name="request" type="m:Base"/>'''
    if kind=='boolean':
        declarations='''<xs:simpleType name="Words"><xs:restriction base="xs:boolean"><xs:pattern value="true|false"/></xs:restriction></xs:simpleType>
<xs:element name="request"><xs:complexType><xs:sequence>
<xs:element name="flag" type="xs:boolean" fixed="0" maxOccurs="unbounded" nillable="true"/>
<xs:element name="word" type="m:Words" fixed="true" minOccurs="0"/>
<xs:element name="literal" type="xs:string" fixed="true" minOccurs="0"/>
</xs:sequence></xs:complexType></xs:element>'''
    if kind=='wildcard':
        wrappers = ''.join('<xs:element name="'+mode+'"><xs:complexType><xs:sequence>'
                           '<xs:any namespace="##targetNamespace" processContents="'+mode+'"/>'
                           '</xs:sequence></xs:complexType></xs:element>' for mode in ('strict','lax','skip'))
        declarations = ('<xs:element name="request"><xs:complexType><xs:choice>'+wrappers+
                        '<xs:element name="localAny"/></xs:choice></xs:complexType></xs:element>'
                        '<xs:element name="implicit"/><xs:element name="explicit" type="xs:anyType"/>'
                        '<xs:complexType name="LocalContent"><xs:sequence><xs:element name="item" form="qualified">'
                        '<xs:complexType><xs:attribute ref="a:editable"/></xs:complexType></xs:element></xs:sequence></xs:complexType>'
                        '<xs:complexType name="Fixed"><xs:attribute ref="a:editable" fixed="false"/></xs:complexType>'
                        '<xs:element name="item"><xs:complexType><xs:attribute ref="a:editable" fixed="false"/></xs:complexType></xs:element>'
                        '<xs:element name="group"><xs:complexType><xs:sequence><xs:any namespace="##targetNamespace" processContents="lax"/>'
                        '</xs:sequence></xs:complexType></xs:element>')
    files['main.xsd']=(f'<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" xmlns:a="urn:attrs" targetNamespace="urn:main"><xs:import namespace="urn:attrs" schemaLocation="attrs.xsd"/>'+declarations+'</xs:schema>').encode()
    return row,files

def prepared(kind='static'):
    row, files = fixture() if kind=='static' else dynamic_fixture(kind)
    result = _prepare(row, files)
    output, records = result[:2]
    # The downloadable and browser entrypoints must use the same schema probes.
    with zipfile.ZipFile(io.BytesIO(_zip(row, *result))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    pack = {k:row[k] for k in ("number", "environment", "revision_id", "root", "namespace")}
    pack.update(format=1, entrypoint="message.xsd", files={name:base64.b64encode(content).decode() for name,content in output.items() if not name.startswith("originals/")})
    for key in ("attribute_checks", "attribute_type_checks", "attribute_wildcard_paths", "attribute_wildcards",
                "attribute_known_elements", "attribute_element_checks", "attribute_any_type_paths", "attribute_type_profiles_complete",
                "boolean_fixed_replacements", "supplementary_schema", "supplementary_namespace"):
        if key in manifest: pack[key] = manifest[key]
    return pack, output, manifest


NO_NAMESPACE_FIXED_EXAMPLES = {
    'fixed-no-namespace-decimal': [('<request xmlns="urn:main">1.00</request>', True),
                                   ('<request xmlns="urn:main">2</request>', False)],
    'fixed-no-namespace-decimal-restricted': [('<request xmlns="urn:main">1.00</request>', True),
                                              ('<request xmlns="urn:main">2</request>', False),
                                              ('<request xmlns="urn:main">0.50</request>', False)],
    'fixed-no-namespace-local-default-restricted': [('<request xmlns="urn:main">1.00</request>', True),
                                                    ('<request xmlns="urn:main">2</request>', False)],
    'fixed-no-namespace-qname': [('<m:request xmlns:m="urn:main">m:known</m:request>', True),
                                 ('<m:request xmlns:m="urn:main" xmlns="urn:main">known</m:request>', True),
                                 ('<m:request xmlns:m="urn:main" xmlns="urn:other">known</m:request>', False)],
}


class AttributeRefConstraintTests(unittest.TestCase):
    def test_profile_ignores_inactive_recursive_particles(self):
        for kind in ('inactive-element', 'inactive-group'):
            with self.subTest(kind=kind):
                self.check_downloaded_profile(kind, 'Dynamic', [
                    (' a:editable="false">', True), (' a:editable="true">', False),
                ])

    def test_typed_fixed_preserves_value_namespace_for_imported_no_namespace_types(self):
        for kind, examples in NO_NAMESPACE_FIXED_EXAMPLES.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                row, files = dynamic_fixture(kind)
                for name, content in files.items():
                    (base/name).write_bytes(content)
                compile_schema(base/'main.xsd', base)
                with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
                    archive.extractall(base)
                validator = compile_schema(base/'message.xsd', base)
                for xml, expected in examples:
                    with self.subTest(xml=xml):
                        self.assertEqual(validator.validate(etree.fromstring(xml.encode())), expected,
                                         str(validator.error_log))

    def test_xsi_type_derives_through_named_and_anonymous_restriction_bases(self):
        for kind in ('named-base', 'anonymous-base'):
            with self.subTest(kind=kind):
                self.check_downloaded_profile(kind, 'Checked', [
                    (' a:editable="false">text', True), (' a:editable="true">text', False),
                ])

    def test_typed_fixed_applies_only_to_the_selected_choice_branch(self):
        for kind, second in [('choice-unfixed', '2'), ('choice-same', '1.00'),
                             ('choice-different', '2.00')]:
            with self.subTest(kind=kind):
                self.check_downloaded_profile(kind, 'ChoiceType', [
                    ('><a/><value>1.00</value>', True),
                    ('><a/><value>2</value>', False),
                    ('><b/><value>'+second+'</value>', True),
                    ('><b/><value>3</value>', kind == 'choice-unfixed'),
                ])

    def test_unproved_choice_scope_reports_unsupported_fixed_check(self):
        row, files = dynamic_fixture('choice-repeated')
        with self.assertRaisesRegex(ValueError, 'Неподдерживаемая дополнительная проверка fixed'):
            _prepare(row, files)

    def test_branch_markers_are_matched_by_expanded_qname(self):
        self.check_downloaded_profile('choice-qualified', 'ChoiceType', [
            ('><m:marker/><!-- ignored --><value>1.00</value>', True),
            ('><m:marker/><value>2</value>', False),
            ('><marker/><value>2</value>', True),
        ])

    def check_downloaded_profile(self, kind, profile, examples):
        row, files = dynamic_fixture(kind)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
                archive.extractall(base)
            manifest = json.loads((base/'manifest.json').read_text('utf8'))
            self.assertIn(['urn:main', profile], [item['type'] for item in manifest.get('attribute_type_checks', [])])
            for body, expected in examples:
                with self.subTest(body=body):
                    payload = base/'payload.xml'
                    payload.write_text('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" '
                                       'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                                       f'xsi:type="m:{profile}"'+body+'</m:request>', 'utf8')
                    result = subprocess.run([sys.executable, str(base/'validate.py'), str(payload)],
                                            capture_output=True, text=True, encoding='utf8', timeout=15)
                    self.assertEqual(result.returncode, 0 if expected else 1, result.stdout+result.stderr)
                    if not expected:
                        self.assertIn('fixed value constraint', result.stderr)

    def test_annotation_payload_is_not_resolved_as_a_schema_type(self):
        row, files = dynamic_fixture('annotated')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
                archive.extractall(base)
            validator = compile_schema(base/'message.xsd', base)
            for value, valid in [('1.0', True), ('2', False)]:
                self.assertEqual(validator.validate(etree.fromstring(f'<m:request xmlns:m="urn:main"><amount>{value}</amount></m:request>'.encode())), valid)

    def test_implicit_xml_attribute_is_enforced_in_the_download(self):
        row, files = dynamic_fixture('implicit-xml')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
                archive.extractall(base)
            validator = compile_schema(base/'message.xsd', base)
            for attribute, valid in [('xml:lang="ru"', True), ('xml:lang="123"', False), ('', False)]:
                self.assertEqual(validator.validate(etree.fromstring(f'<m:request xmlns:m="urn:main" {attribute}/>'.encode())), valid)

    def test_boolean_aliases_preserve_patterns_nil_and_string_fixed(self):
        row, files = dynamic_fixture('boolean')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
                archive.extractall(base)
            validator = compile_schema(base/'message.xsd', base)
            for body, valid in [('<flag> false </flag>',True), ('<flag>\nfalse\n</flag>',True),
                                ('<flag/>',True), ('<flag xsi:nil="true"/>',False),
                                ('<flag>false</flag><word>true</word>',True), ('<flag>false</flag><word>1</word>',False),
                                ('<flag>false</flag><literal>1</literal>',False), ('<flag>false</flag><flag>true</flag>',False)]:
                node = etree.fromstring(('<m:request xmlns:m="urn:main" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'+body+'</m:request>').encode())
                self.assertEqual(validator.validate(node), valid, body)

    def test_narrowed_instance_type_replaces_descendant_wildcards(self):
        row, files = dynamic_fixture('narrowed')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
                archive.extractall(base)
            validator = compile_schema(base/'message.xsd', base)
            for selection, valid in [('Derived', True), ('Base', False)]:
                node = etree.fromstring(f'<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:{selection}"><wrapper><m:item a:editable="true"/></wrapper></m:request>'.encode())
                self.assertEqual(validator.validate(node), valid, selection)

    def test_inline_probe_keeps_type_prefix_from_chameleon_schema(self):
        row, files = dynamic_fixture('chameleon')
        files['attributes.xsd'] = (f'<xs:schema xmlns:xs="{XS}" xmlns:o="urn:main"><xs:attribute name="code"><xs:simpleType>'
                                   '<xs:restriction base="o:Code"><xs:minLength value="2"/></xs:restriction>'
                                   '</xs:simpleType></xs:attribute></xs:schema>').encode()
        result = _prepare(row, files)
        self.assertTrue(result[2]['attribute_checks'])
    def test_compiled_bundle_preserves_chameleon_attribute_type(self):
        row, files = dynamic_fixture('chameleon')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name, raw in files.items():
                (base/name).write_bytes(raw)
            native = compile_schema(base/'main.xsd', base)
            with zipfile.ZipFile(io.BytesIO(_zip(row, *_prepare(row, files)))) as archive:
                archive.extractall(base)
            bundled = compile_schema(base/'message.xsd', base)
            for value, allowed in [('OK', True), ('NO', False), ('lowercase', False)]:
                node = etree.fromstring(f'<m:request xmlns:m="urn:main" m:code="{value}"/>'.encode())
                self.assertEqual(bundled.validate(node), allowed)
                if value != 'NO':  # libxml2 ignores a fixed value on an attribute ref.
                    self.assertEqual(native.validate(node), allowed)

    def test_wildcard_checks_only_assessed_global_elements(self):
        row,files=dynamic_fixture('wildcard')
        for root_name in ('request','item'):
            result=_prepare(dict(row,root=root_name),files)
            with tempfile.TemporaryDirectory() as folder:
                base=Path(folder)
                with zipfile.ZipFile(io.BytesIO(_zip(dict(row,root=root_name),*result))) as archive:
                    archive.extractall(base)
                validator=compile_schema(base/'message.xsd',base)
                for mode, subject, expected in (
                    ('strict','<m:item a:editable="true"/>',False),('lax','<m:item a:editable="1"/>',False),
                    ('strict','<m:item a:editable="0"/>',True),('skip','<m:item a:editable="true"/>',True),
                    ('lax','<m:unknown a:editable="true"/>',True),
                    ('lax','<m:unknown><m:item a:editable="true"/></m:unknown>',False),
                    ('strict','<m:implicit><m:item a:editable="true"/></m:implicit>',False),
                    ('lax','<m:explicit><m:item a:editable="true"/></m:explicit>',False),
                    ('lax','<m:unknown xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:s="http://www.w3.org/2001/XMLSchema" xsi:type="s:anyType"><m:item a:editable="true"/></m:unknown>',False),
                    ('lax','<m:implicit xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:LocalContent"><m:item a:editable="true"/></m:implicit>',True),
                    ('localAny','<m:item a:editable="true"/>',False),
                    ('localAny','<m:item a:editable="0"/>',True),
                    ('skip','<m:unknown xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:Fixed" a:editable="true"/>',True),
                    ('lax','<m:group><m:item a:editable="true"/></m:group>',False),
                ):
                    if root_name=='item' and not (mode=='strict' and subject.startswith('<m:item ')):continue
                    xml=(subject.replace('<m:item ','<m:item xmlns:m="urn:main" xmlns:a="urn:attrs" ')
                         if root_name=='item' else '<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><'+mode+'>'+subject+'</'+mode+'></m:request>')
                    self.assertEqual(validator.validate(etree.fromstring(xml.encode())),expected,xml)

    def test_field_preserves_value_constraint_from_specific_attribute_use(self):
        row, files = fixture(); raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            for name, content in files.items(): archive.writestr(name, content)
        with zipfile.ZipFile(raw) as archive:
            tree = Schema(archive, "main.xsd", "request"); tree.field(tree.root)
        fields = {f["path"]:f for f in tree.fields}
        self.assertEqual(fields["request/item/@editable"]["flags"], {"fixed":"false"})
        self.assertEqual(fields["request/item/@label"]["flags"], {"default":"local"})

    def test_downloaded_python_validator_enforces_fixed_boolean_value_space(self):
        pack, output, manifest = prepared()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name, content in output.items():
                if name.startswith("originals/"): continue
                path = base/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
            (base/"manifest.json").write_text(json.dumps(manifest), "utf8")
            validator = compile_schema(base/"message.xsd", base)
            for value, expected in [(None,True), ("false",True), ("0",True), ("true",False), ("1",False)]:
                attribute = '' if value is None else ' a:editable="'+value+'"'
                xml = '<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item'+attribute+'/></m:request>'
                with self.subTest(value=value):
                    self.assertEqual(validator.validate(etree.fromstring(xml.encode())), expected)
            repeated = etree.fromstring(b'<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item a:editable="false"/><item a:editable="true"/></m:request>')
            self.assertFalse(validator.validate(repeated))
            invalid_first=etree.fromstring(b'<m:request xmlns:m="urn:main" xmlns:a="urn:attrs">\n<item a:editable="true"/>\n<item a:editable="false"/>\n</m:request>')
            self.assertFalse(validator.validate(invalid_first))
            self.assertEqual(validator.error_log[0].line,2)
            self.assertIn("attribute '{urn:attrs}editable'",validator.error_log[0].message)
            for attributes,expected in [('xmlns:v="urn:value" a:kind="v:known"',True),('xmlns:v="urn:other" a:kind="v:known"',False),('a:token="a   b"',True),('a:token="wrong"',False)]:
                xml='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item '+attributes+'/></m:request>'
                with self.subTest(attributes=attributes):self.assertEqual(validator.validate(etree.fromstring(xml.encode())),expected)

    def test_xsi_type_applies_fixed_constraints_at_root_and_nested_paths(self):
        for kind in ('dynamic','scalar','equivalent'):
            pack,output,manifest=prepared(kind)
            with tempfile.TemporaryDirectory() as folder:
                base=Path(folder)
                for name,content in output.items():
                    if name.startswith('originals/'):continue
                    path=base/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
                (base/'manifest.json').write_text(json.dumps(manifest),'utf8')
                validator=compile_schema(base/'message.xsd',base)
                for value,expected in [('false',True),('0',True),('true',False),('1',False)]:
                    xtype='m:BooleanWithFixed' if kind=='scalar' else 'm:Derived'
                    xml='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="'+xtype+'" a:editable="'+value+'">'+('true' if kind=='scalar' else '')+'</m:request>'
                    with self.subTest(kind=kind,value=value):self.assertEqual(validator.validate(etree.fromstring(xml.encode())),expected)
                if kind=='dynamic':
                    for xml,expected in [
                        ('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" a:editable="true"/>',True),
                        ('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:Base" a:editable="true"/>',True),
                        ('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="m:Derived"><child a:editable="true"/></m:request>',False),
                        ('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><child xsi:type="m:FixedLeaf" a:editable="true"/></m:request>',False)]:
                        with self.subTest(xml=xml):self.assertEqual(validator.validate(etree.fromstring(xml.encode())),expected)

    def test_unrelated_types_do_not_generate_supplementary_checks(self):
        row,files=fixture()
        for unrelated_fixed_type in (False,True):
            extra='<xs:complexType name="Fixed"><xs:attribute ref="a:editable" fixed="false"/></xs:complexType>' if unrelated_fixed_type else ''
            source=(f'''<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" xmlns:a="urn:attrs" targetNamespace="urn:main"><xs:import namespace="urn:attrs" schemaLocation="attrs.xsd"/><xs:complexType name="Recursive"><xs:sequence><xs:element name="child" type="m:Recursive" minOccurs="0"/></xs:sequence></xs:complexType><xs:element name="request" type="xs:string"/>'''+extra+'</xs:schema>').encode()
            try:output,records,constraints=_prepare(row,{'main.xsd':source,'attrs.xsd':files['attrs.xsd']})
            except ValueError as error:self.fail('Unused recursive type disabled an unchanged schema: '+str(error))
            self.assertEqual(constraints,{})

    def test_reachable_recursive_type_still_reports_unsupported_expansion(self):
        row,files=dynamic_fixture('unused-recursive')
        for declared_type,path in ((b'm:Unused','request/child'),(b'xs:anyType','typeRoot/child')):
            with self.subTest(declared_type=declared_type):
                source=files['main.xsd'].replace(b'xs:string',declared_type)
                with self.assertRaisesRegex(ValueError,'Рекурсивный тип в '+path):
                    _prepare(row,dict(files,**{'main.xsd':source}))

        row,files=dynamic_fixture('lax-recursive')
        with self.assertRaisesRegex(ValueError,'Рекурсивный тип в typeRoot/child'):
            _prepare(row,files)

    def test_skipped_wildcard_does_not_make_recursive_type_reachable(self):
        row,files=dynamic_fixture('skip-recursive')
        _,_,constraints=_prepare(row,files)
        self.assertEqual(constraints,{})

    def test_strict_wildcard_discovers_derived_type_of_nested_declared_element(self):
        _,_,manifest=prepared('strict-derived')
        self.assertEqual([profile['type'] for profile in manifest['attribute_type_checks']],
                         [['urn:main','FixedLeaf']])

    def test_abstract_global_is_unreachable_through_strict_wildcard(self):
        row,files=dynamic_fixture('strict-derived')
        files['main.xsd']=files['main.xsd'].replace(
            b'</xs:schema>', b'<xs:element name="abstract" abstract="true" type="m:Unused"/></xs:schema>')
        _,_,constraints=_prepare(row,files)
        self.assertEqual([profile['type'] for profile in constraints['attribute_type_checks']],
                         [['urn:main','FixedLeaf']])

    def test_extension_of_any_type_retains_inherited_lax_wildcard(self):
        _,output,manifest=prepared('extended-any-type')
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            for name,content in output.items():
                path=base/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(content)
            (base/'manifest.json').write_text(json.dumps(manifest),'utf8')
            validator=compile_schema(base/'message.xsd',base)
            for request_type in ('', ' xsi:type="m:Extended"'):
                prefix='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'+request_type+'>'
                for child in ('unknown xsi:type="m:Leaf"', 'm:known'):
                    for fixed,expected in (('false',True),('true',False)):
                        xml=f'{prefix}<{child} a:editable="{fixed}"/></m:request>'
                        with self.subTest(xml=xml):
                            self.assertEqual(validator.validate(etree.fromstring(xml.encode())),expected)

    def test_reachable_block_rules_preserve_allowed_type_and_fixed_constraints(self):
        row, files = dynamic_fixture('dynamic')
        namespaces = ('xmlns:m="urn:main" xmlns:a="urn:attrs" '
                      'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"')
        for original, changed, permits_derived in (
            (b'name="request" type=', b'name="request" block="restriction" type=', False),
            (b'targetNamespace="urn:main"', b'targetNamespace="urn:main" blockDefault="restriction"', False),
            (b'name="Derived"', b'name="Derived" block="restriction"', True),
        ):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as folder:
                source = files['main.xsd'].replace(original, changed)
                result = _prepare(row, files | {'main.xsd': source})
                base = Path(folder)
                with zipfile.ZipFile(io.BytesIO(_zip(row, *result))) as archive:
                    archive.extractall(base)
                validator = compile_schema(base/'message.xsd', base)
                for xtype, editable, valid in [('Base', 'true', True), ('Derived', 'false', permits_derived),
                                               ('Derived', 'true', False)]:
                    xml = f'<m:request {namespaces} xsi:type="m:{xtype}" a:editable="{editable}"/>'
                    with self.subTest(xtype=xtype, editable=editable):
                        self.assertEqual(validator.validate(etree.fromstring(xml.encode())), valid, validator.error_log)
                        if not valid:
                            diagnostic = 'editable' if permits_derived else 'Derived'
                            self.assertTrue(any(diagnostic in issue.message for issue in validator.error_log),
                                            validator.error_log)

    def test_shared_attribute_use_diagnostics_identify_the_actual_element_occurrence(self):
        row,files=fixture()
        files['main.xsd']=f'''<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" xmlns:a="urn:attrs" targetNamespace="urn:main"><xs:import namespace="urn:attrs" schemaLocation="attrs.xsd"/><xs:complexType name="Block"><xs:attribute ref="a:editable" fixed="false"/></xs:complexType><xs:element name="request"><xs:complexType><xs:sequence><xs:element name="a" type="m:Block"/><xs:element name="b" type="m:Block"/></xs:sequence></xs:complexType></xs:element></xs:schema>'''.encode()
        output,records,constraints=_prepare(row,files)
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with zipfile.ZipFile(io.BytesIO(_zip(row,output,records,constraints))) as archive:archive.extractall(base)
            validator=compile_schema(base/'message.xsd',base)
            prefix='<m:request xmlns:m="urn:main" xmlns:a="urn:attrs">'
            for separator in ('','\n'):
                for invalid,expected in [('a',1 if not separator else 2),('b',1 if not separator else 3)]:
                    body=separator+'<a a:editable="'+('true' if invalid=='a' else 'false')+'"/>'+separator+'<b a:editable="'+('true' if invalid=='b' else 'false')+'"/>'
                    document=etree.fromstring((prefix+body+'</m:request>').encode())
                    with self.subTest(separator=separator,invalid=invalid):
                        self.assertFalse(validator.validate(document))
                        self.assertEqual(len(validator.error_log),1)
                        self.assertEqual(validator.error_log[0].line,expected)
                        self.assertTrue(validator.error_log[0].message.startswith("Element '"+invalid+"', attribute '{urn:attrs}editable':"),validator.error_log[0].message)

    def test_copied_download_script_applies_supplementary_manifest(self):
        row,files=fixture();result=_prepare(row,files)
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)
            with zipfile.ZipFile(io.BytesIO(_zip(row,*result))) as archive:archive.extractall(base)
            payload=base/'payload.xml';payload.write_text('<m:request xmlns:m="urn:main" xmlns:a="urn:attrs"><item a:editable="true"/></m:request>','utf8')
            result=subprocess.run([sys.executable,str(base/'validate.py'),str(payload)],capture_output=True,text=True,encoding='utf8',timeout=15)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            self.assertIn('editable',result.stderr)

if __name__ == "__main__":
    if "--pack" in sys.argv: print(json.dumps(prepared(sys.argv[2] if len(sys.argv)>2 else "static")[0]))
    else: unittest.main()
