import io
import unittest
import zipfile
from schema import Schema


class SchemaContentTests(unittest.TestCase):
    def test_unknown_or_wrong_kind_builtin_components_are_rejected(self):
        for declaration in ('<xs:element name="r" type="xs:strng"/>',
                            '<xs:element name="r"><xs:complexType><xs:attribute name="bad" type="xs:anyType"/></xs:complexType></xs:element>',
                            '<xs:element name="r"><xs:simpleType><xs:restriction base="xs:anyType"/></xs:simpleType></xs:element>',
                            '<xs:element name="r"><xs:complexType><xs:sequence><xs:element ref="xs:string"/></xs:sequence></xs:complexType></xs:element>'):
            with self.subTest(declaration=declaration), self.assertRaises(ValueError):
                self.tree({'main.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'+declaration+'</xs:schema>'})
        for builtin in ('anyType','anySimpleType','string'):
            tree=self.tree({'main.xsd': f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="r" type="xs:{builtin}"/></xs:schema>'})
            self.assertEqual(tree.fields[0]['base'],'xs:'+builtin)

    def test_included_root_metadata_replays_each_snapshot_generation_exactly(self):
        from tests.test_source_collection_regressions import SourceCollectionTests
        from tests.test_schema_recovery import archive
        fixture=SourceCollectionTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        contents=archive({'main.xsd':b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:main"><xs:include schemaLocation="common.xsd"/></xs:schema>',
                          'common.xsd':b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="r" type="xs:string"/></xs:schema>'})
        for url in fixture.resources:
            if url.endswith('.zip'):fixture.resources[url]=contents
        fixture.collect(1,snapshot_format=6,wsdl_entries=[])
        old=(fixture.state/'current.json').read_bytes()
        fixture.collect(2,snapshot_format=6,wsdl_entries=[])
        self.assertEqual((fixture.state/'current.json').read_bytes(),old)
        fixture.collect(3,wsdl_entries=[])
        fresh=(fixture.state/'current.json').read_bytes()
        self.assertNotEqual(fresh,old)
        self.assertEqual(fixture.current()['format'],7)
        fixture.collect(4,snapshot_format=7,wsdl_entries=[])
        self.assertEqual((fixture.state/'current.json').read_bytes(),fresh)

    def test_occurrence_lexical_aliases_are_canonical_for_fields_and_groups(self):
        for low, high, expected in [('00', '01', ('0','1')), (' 0 ', 'unbounded', ('0','unbounded'))]:
            source = f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="r"><xs:complexType><xs:sequence minOccurs="{low}" maxOccurs="{high}"><xs:element name="a" type="xs:string" minOccurs="{low}" maxOccurs="{high}"/></xs:sequence></xs:complexType></xs:element></xs:schema>'
            tree = self.tree({'main.xsd': source})
            self.assertEqual((tree.fields[1]['min'],tree.fields[1]['max']), expected)
            self.assertEqual((tree.fields[0]['contentModelBounds']['min'],tree.fields[0]['contentModelBounds']['max']), expected)

    def test_reserved_xml_prefix_resolves_imported_language_attribute(self):
        xs = 'http://www.w3.org/2001/XMLSchema'
        xml = 'http://www.w3.org/XML/1998/namespace'
        tree = self.tree({'main.xsd': f'<xs:schema xmlns:xs="{xs}"><xs:import namespace="{xml}" schemaLocation="xml.xsd"/><xs:element name="r"><xs:complexType><xs:attribute ref="xml:lang"/></xs:complexType></xs:element></xs:schema>',
                          'xml.xsd': f'<xs:schema xmlns:xs="{xs}" targetNamespace="{xml}"><xs:attribute name="lang" type="xs:language"/></xs:schema>'})
        self.assertEqual((tree.fields[1]['name'], tree.fields[1]['ns']), ('@lang', xml))

    def test_wildcards_retain_the_effective_namespace_constraint(self):
        for attribute, expected in [
            ('', '##any'), ('namespace="##local"', '##local'),
            ('namespace="##other"', '##other (targetNamespace=urn:main)'),
            ('namespace="##targetNamespace ##local urn:z"', '##local urn:main urn:z'),
        ]:
            source = f'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
                targetNamespace="urn:main" elementFormDefault="qualified">
              <xs:element name="r"><xs:complexType><xs:sequence><xs:any {attribute}/></xs:sequence>
                <xs:anyAttribute {attribute}/></xs:complexType></xs:element></xs:schema>'''
            with self.subTest(attribute=attribute):
                root, element, attr = self.tree({'main.xsd': source}).fields
                self.assertEqual(root['ns'], 'urn:main')
                self.assertEqual([f.get('wildcardNamespace') for f in (element, attr)], [expected]*2)

    def test_qname_enumerations_and_constraints_retain_uri_and_spelling(self):
        for prefix, uri in [('p','urn:A'), ('q','urn:A'), ('p','urn:B')]:
            source = f'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:{prefix}="{uri}">
              <xs:simpleType name="Code"><xs:restriction base="xs:QName">
                <xs:enumeration value="{prefix}:read"/>
              </xs:restriction></xs:simpleType>
              <xs:simpleType name="Restricted"><xs:restriction base="Code"/></xs:simpleType>
              <xs:element name="r" type="Restricted" fixed="{prefix}:read"/>
            </xs:schema>'''
            field = self.tree({'main.xsd':source}).fields[0]
            self.assertEqual(field['values'], [[prefix+':read','']])
            self.assertEqual(field['valueQNames'], [[uri,'read']])
            self.assertEqual(field['flagQNames'], {'fixed':[uri,'read']})

    def test_union_enumerations_select_the_first_member_accepting_the_declaration(self):
        from lxml import etree
        from tests.comparison_xsd_fixture import tree, union_source

        setup = '<xs:simpleType name="Head"><xs:restriction base="xs:string"><xs:pattern value="plain"/></xs:restriction></xs:simpleType>'
        numeric_setup = '<xs:simpleType name="Head"><xs:restriction base="xs:integer"><xs:minInclusive value="2"/></xs:restriction></xs:simpleType>'
        nested_setup = '<xs:simpleType name="Head"><xs:union memberTypes="xs:QName xs:decimal"/></xs:simpleType>'
        rejecting_qname = '''<xs:simpleType name="Head"><xs:restriction base="xs:QName">
          <xs:enumeration xmlns:p="urn:other" value="p:read"/>
        </xs:restriction></xs:simpleType>'''
        qnames = ['<r xmlns:q="urn:A">q:read</r>', '<r xmlns:q="urn:B">q:read</r>']
        numeric = ['<r>01</r>', '<r>1</r>', '<r>2</r>']
        for source, selected, name, examples, accepted in (
            (union_source(), ['QName', 'collapse'], ['urn:A', 'read'], qnames, [True,False]),
            (union_source(uri='urn:B'), ['QName', 'collapse'], ['urn:B', 'read'], qnames, [False,True]),
            (union_source('q'), ['QName', 'collapse'], ['urn:A', 'read'], qnames, [True,False]),
            (union_source(members='xs:string xs:QName'), ['string', 'preserve'], None,
             ['<r xmlns:p="urn:A">p:read</r>', '<r xmlns:p="urn:B">p:read</r>']+qnames, [True,True,False,False]),
            (union_source(value='01'), ['integer', 'collapse'], None, numeric, [True,True,False]),
            (union_source(value='1', members='xs:integer xs:decimal'),
             ['integer', 'collapse'], None, numeric+['<r>1.0</r>'], [True,True,False,True]),
            (union_source(value='1.0', members='xs:integer xs:decimal'),
             ['decimal', 'collapse'], None, numeric+['<r>1.0</r>'], [True,True,False,True]),
            (union_source(members='Head xs:QName', definitions=setup), ['QName', 'collapse'],
             ['urn:A', 'read'], qnames, [True,False]),
            (union_source(members='Head xs:string', definitions=numeric_setup, value='01'),
             ['string', 'preserve'], None, numeric, [True,False,False]),
            (union_source(members='Head xs:string', definitions=nested_setup), ['QName', 'collapse'],
             ['urn:A', 'read'], qnames, [True,False]),
            (union_source(members='').replace('<xs:union memberTypes=""/>',
             '<xs:union><xs:simpleType><xs:restriction base="xs:QName"/></xs:simpleType></xs:union>'),
             ['QName', 'collapse'], ['urn:A', 'read'], qnames, [True,False]),
            (union_source(members='Head xs:string', definitions=rejecting_qname), ['string', 'preserve'], None,
             ['<r xmlns:p="urn:A">p:read</r>', '<r xmlns:p="urn:other">p:read</r>'], [True,False]),
        ):
            with self.subTest(members=source.split('name="Base"', 1)[1].split('/>', 1)[0], examples=examples):
                validator = etree.XMLSchema(etree.fromstring(source.encode()))
                self.assertEqual([validator.validate(etree.fromstring(xml.encode())) for xml in examples], accepted)
                field = tree(source)[0]
                self.assertEqual(field.get('unionValueTypes'), [selected])
                self.assertEqual(field.get('valueQNames', [None]), [name])

    def test_union_enum_selects_restricted_notation_member(self):
        from lxml import etree
        from tests.comparison_xsd_fixture import tree, union_source

        source = union_source(value='read', members='Head xs:string', definitions='''
          <xs:notation name="read" public="sample"/>
          <xs:simpleType name="Head"><xs:restriction base="xs:NOTATION">
            <xs:enumeration value="read"/>
          </xs:restriction></xs:simpleType>''')
        validator = etree.XMLSchema(etree.fromstring(source.encode()))
        self.assertTrue(validator.validate(etree.fromstring(b'<r>read</r>')))
        field = tree(source)[0]
        self.assertEqual(field.get('unionValueTypes'), [['NOTATION', 'collapse']])
        self.assertEqual(field.get('valueQNames'), [['', 'read']])
        self.assertEqual(field['values'], [['read', '']])

    def test_notation_values_retain_their_expanded_names(self):
        from tests.comparison_xsd_fixture import notation_trees
        for union, trees in notation_trees().items():
            for fields, uri in zip(trees, ('urn:A', 'urn:B', 'urn:A')):
                for field in fields[1:]:
                    self.assertEqual(field.get('valueQNames'), [[uri, 'code']])
                    self.assertEqual(next(iter(field.get('flagQNames', {}).values())), [uri, 'code'])
                    if union == 'union':
                        self.assertEqual(next(iter(field['unionFlagTypes'].values())), ['NOTATION', 'collapse'])

    def test_substitution_rules_are_explicitly_unsupported(self):
        for attribute in ('block="extension"', 'substitutionGroup="head"'):
            source = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="head"/><xs:element name="r" '+attribute+' type="xs:string"/></xs:schema>'
            with self.subTest(attribute=attribute), self.assertRaisesRegex(ValueError, 'block|substitutionGroup'):
                self.tree({'main.xsd': source})
        source = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" blockDefault="extension"><xs:element name="r" type="xs:string"/></xs:schema>'
        with self.assertRaisesRegex(ValueError, 'blockDefault'):
            self.tree({'main.xsd': source})

    def test_substitution_changes_validator_contract_and_export_requires_review(self):
        import json
        import tempfile
        from pathlib import Path
        from lxml import etree
        from fields import build
        from sync import sha
        block_schema = ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                        '<xs:complexType name="Base"/><xs:complexType name="Derived"><xs:complexContent>'
                        '<xs:extension base="Base"><xs:attribute name="extra"/></xs:extension></xs:complexContent></xs:complexType>'
                        '<xs:element name="r" type="Base" BLOCK/></xs:schema>')
        substitution_schema = ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                               '<xs:element name="head" type="xs:string"/><xs:element name="member" type="xs:string" GROUP/>'
                               '<xs:element name="r"><xs:complexType><xs:sequence><xs:element ref="head"/></xs:sequence></xs:complexType></xs:element></xs:schema>')
        for template, marker, attribute, xml, old_valid in (
            (block_schema, 'BLOCK', 'block="extension"', '<r xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="Derived" extra="1"/>', True),
            (substitution_schema, 'GROUP', 'substitutionGroup="head"', '<r><member>text</member></r>', False)):
            for changed in (False, True):
                source = template.replace(marker, attribute if changed else '')
                validator = etree.XMLSchema(etree.fromstring(source.encode()))
                self.assertEqual(validator.validate(etree.fromstring(xml.encode())), old_valid != changed)
                if not changed: continue
                with tempfile.TemporaryDirectory() as folder:
                    base = Path(folder); state = base/'state'; (state/'objects').mkdir(parents=True)
                    raw = io.BytesIO()
                    with zipfile.ZipFile(raw, 'w') as archive: archive.writestr('main.xsd', source)
                    digest = sha(raw.getvalue()); (state/'objects'/digest).write_bytes(raw.getvalue())
                    index = base/'catalog.json'
                    index.write_text(json.dumps({'revisions': [dict(revision_id='one', number='1', environment='prod', status='matched', archive_sha256=digest, schema_path='main.xsd', root='r')]}))
                    build(index, state, base/'fields')
                    status = json.loads((base/'fields/index.json').read_bytes())[0]
                    self.assertEqual(status['status'], 'review')
                    self.assertIn(attribute.split('=')[0], status['error'])
                    self.assertFalse((base/'fields/one.json').exists())

    def test_unused_substitution_rules_and_empty_blocks_do_not_hide_ready_root(self):
        for extra in ('<xs:element name="unused" block="extension"/>',
                      '<xs:element name="head"/><xs:element name="unused" substitutionGroup="head"/>',
                      '<xs:complexType name="Unused" block="extension"/>'):
            source = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'+extra+'<xs:element name="r" type="xs:string"/></xs:schema>'
            self.assertEqual(self.tree({'main.xsd': source}).fields[0]['name'], 'r')
        for attributes, element in (('blockDefault=""', ''), ('', 'block=""'),
                                     ('blockDefault="extension"', 'block=""')):
            source = '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '+attributes+'><xs:element name="r" '+element+' type="xs:string"/></xs:schema>'
            self.assertEqual(self.tree({'main.xsd': source}).fields[0]['name'], 'r')

    def test_group_bounds_preserve_joint_presence_and_group_refs(self):
        source = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
          <xs:group name="Pair"><xs:sequence><xs:element name="a"/><xs:element name="b"/></xs:sequence></xs:group>
          <xs:element name="r"><xs:complexType><xs:sequence><xs:group ref="Pair" minOccurs="0" maxOccurs="2"/></xs:sequence></xs:complexType></xs:element></xs:schema>'''
        tree = self.tree({'main.xsd': source})
        model = tree.fields[0].get('contentModelBounds')
        self.assertIsNotNone(model)
        group = model['children'][0]
        self.assertEqual((group['kind'], group['min'], group['max']), ('group', '0', '2'))
        self.assertEqual(group['children'][0]['children'], ['a', 'b'])
        self.assertEqual(tree.fields[0]['contentModel'], ['sequence', ['sequence', 'a', 'b']])

    def test_optional_sequence_contract_change_reaches_comparison(self):
        import json
        import subprocess
        from pathlib import Path
        from lxml import etree
        trees=[]
        for groups in [('ab','cd'),('a','bc','d')]:
            particles=''.join('<xs:sequence minOccurs="0">'+''.join('<xs:element name="'+name+'"/>' for name in group)+'</xs:sequence>' for group in groups)
            source='<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="r"><xs:complexType><xs:sequence>'+particles+'</xs:sequence></xs:complexType></xs:element></xs:schema>'
            validator=etree.XMLSchema(etree.fromstring(source.encode()))
            self.assertEqual(validator.validate(etree.fromstring(b'<r><a/><b/></r>')), groups==('ab','cd'))
            self.assertEqual(validator.validate(etree.fromstring(b'<r><a/></r>')), groups!=('ab','cd'))
            trees.append(self.tree({'main.xsd':source}).fields)
        result=subprocess.run(['node','-e',"const fs=require('fs'),{compare}=require('./web/compare-core.js');const [a,b]=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(compare(a,b)));"],input=json.dumps(trees),cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,check=True)
        differences=json.loads(result.stdout)
        self.assertTrue(any(row['path']=='r' and any(change['key']=='contentModel' for change in row['changes']) for row in differences))

    def test_individual_export_rejects_ambiguous_paths_after_choice_coalescing(self):
        import json
        import tempfile
        from pathlib import Path
        from fields import build
        from sync import sha
        source = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="r"><xs:complexType><xs:sequence><xs:element name="item"/><xs:element name="item"/></xs:sequence></xs:complexType></xs:element></xs:schema>'''
        from lxml import etree
        validator = etree.XMLSchema(etree.fromstring(source.encode()))
        self.assertTrue(validator.validate(etree.fromstring(b'<r><item/><item/></r>')))
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); state = base/'state'; (state/'objects').mkdir(parents=True)
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, 'w') as archive: archive.writestr('main.xsd', source)
            digest = sha(raw.getvalue()); (state/'objects'/digest).write_bytes(raw.getvalue())
            index = base/'catalog.json'; index.write_text(json.dumps({'revisions':[dict(revision_id='one', number='1', environment='prod', status='matched', archive_sha256=digest, schema_path='main.xsd', root='r')]}))
            build(index, state, base/'fields')
            status = json.loads((base/'fields/index.json').read_bytes())[0]
            self.assertEqual(status['status'], 'review')
            self.assertIn('r/item', status['error'])
            self.assertFalse((base/'fields/one.json').exists())

    def test_pattern_groups_preserve_distinct_restrictions_and_union_members(self):
        from lxml import etree
        a, b = '[0-9]{10}', '[0-9]{12}'
        first, second = (f'<xs:pattern value="{p}"/>' for p in (a, b))
        for inherited, expected in ((False, [[a, b]]), (True, [[a], [b]])):
            for union in (False, True):
                source = f'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
                  <xs:simpleType name="Base"><xs:restriction base="xs:string">{first if inherited else ''}</xs:restriction></xs:simpleType>
                  <xs:simpleType name="Code"><xs:restriction base="Base">{'' if inherited else first}{second}</xs:restriction></xs:simpleType>
                  <xs:simpleType name="Either"><xs:union memberTypes="Code xs:boolean"/></xs:simpleType>
                  <xs:element name="r" type="{'Either' if union else 'Code'}"/></xs:schema>'''
                validator = etree.XMLSchema(etree.fromstring(source.encode()))
                for length in (10, 12):
                    self.assertEqual(validator.validate(etree.fromstring(('<r>' + '2' * length + '</r>').encode())), not inherited)
                field = self.tree({'main.xsd': source}).fields[0]
                option = field['alternatives'][0] if union else field
                self.assertEqual(option.get('patternGroups'), expected)

    def test_dependency_metadata_distinguishes_legacy_unknown_from_no_namespace_import(self):
        from schema import dependency_set
        source={'path':'main.xsd','namespace':'urn:main','sha256':'main','dependencies':['types.xsd']}
        target={'path':'types.xsd','namespace':'urn:types','sha256':'types','dependencies':[]}
        self.assertEqual(dependency_set([source,target],'main.xsd'),[('main.xsd','main'),('types.xsd','types')])
        source['imports']=[{'location':'types.xsd','namespace':None,'kind':'import'}]
        with self.assertRaisesRegex(ValueError,'namespace'):
            dependency_set([source,target],'main.xsd')

    def tree(self, sources):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, content in sources.items():
                archive.writestr(name, content)
        with zipfile.ZipFile(output) as archive:
            tree = Schema(archive, "main.xsd", "r")
            tree.field(tree.root)
        return tree

    def test_complex_restriction_replaces_particles_and_inherits_attributes(self):
        from lxml import etree
        source = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
          <xs:complexType name="Base"><xs:sequence><xs:element name="optional" minOccurs="0"/></xs:sequence>
          <xs:attribute name="kept"/><xs:attribute name="removed"/><xs:attribute name="changed"/><xs:anyAttribute processContents="lax"/></xs:complexType>
          <xs:element name="r"><xs:complexType><xs:complexContent><xs:restriction base="Base">
          <xs:attribute name="removed" use="prohibited"/><xs:attribute name="changed" use="required"/>
          </xs:restriction></xs:complexContent></xs:complexType></xs:element></xs:schema>'''
        validator = etree.XMLSchema(etree.fromstring(source.encode()))
        self.assertTrue(validator.validate(etree.fromstring(b'<r changed="x" kept="y"/>')))
        self.assertFalse(validator.validate(etree.fromstring(b'<r changed="x"><optional/></r>')))
        self.assertFalse(validator.validate(etree.fromstring(b'<r changed="x" wildcard="removed"/>')))
        tree = self.tree({'main.xsd': source})
        self.assertEqual({f['path'] for f in tree.fields}, {'r', 'r/@kept', 'r/@changed'})
        self.assertEqual(tree.fields[0]['contentModel'], ['empty'])
        self.assertEqual(next(f for f in tree.fields if f['name'] == '@changed')['min'], '1')

    def test_simple_content_restriction_narrows_attributes_and_preserves_scalar_rules(self):
        from lxml import etree
        source = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
          <xs:simpleType name="Text"><xs:restriction base="xs:string"><xs:minLength value="1"/></xs:restriction></xs:simpleType>
          <xs:complexType name="Base"><xs:simpleContent><xs:extension base="Text">
            <xs:attribute name="kept"/><xs:attribute name="removed"/><xs:attribute name="changed"/>
            <xs:anyAttribute processContents="lax"/>
          </xs:extension></xs:simpleContent></xs:complexType>
          <xs:complexType name="Derived"><xs:simpleContent><xs:restriction base="Base">
            <xs:maxLength value="2"/><xs:attribute name="removed" use="prohibited"/><xs:attribute name="changed" use="required"/>
          </xs:restriction></xs:simpleContent></xs:complexType><xs:element name="r" type="Derived"/></xs:schema>'''
        validator = etree.XMLSchema(etree.fromstring(source.encode()))
        self.assertTrue(validator.validate(etree.fromstring(b'<r changed="yes" kept="ok">ab</r>')))
        for xml in ('<r>ab</r>', '<r changed="yes" removed="x">ab</r>', '<r changed="yes" extra="x">ab</r>',
                    '<r changed="yes">abc</r>', '<r changed="yes"/>'):
            self.assertFalse(validator.validate(etree.fromstring(xml)))
        fields = self.tree({'main.xsd': source}).fields
        self.assertCountEqual([(f['path'], f['min']) for f in fields],
                              [('r', '1'), ('r/@kept', '0'), ('r/@changed', '1')])
        self.assertEqual(fields[0]['base'], 'xs:string')
        self.assertCountEqual(fields[0]['facets'], [['minLength', '1'], ['maxLength', '2']])

    def test_abstract_element_and_direct_type_are_independent(self):
        from lxml import etree
        for element_abstract, type_abstract, assigned, flags in (
                ('0', '0', 'Base', {}), ('false', '1', 'Base', {'typeAbstract': 'true'}),
                ('true', '0', 'Base', {'abstract': 'true'}),
                ('1', 'true', 'Base', {'abstract': 'true', 'typeAbstract': 'true'}),
                ('false', 'true', 'Concrete', {})):
            with self.subTest(element=element_abstract, typ=type_abstract, assigned=assigned):
                source = f'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
                  <xs:complexType name="Base" abstract="{type_abstract}"/>
                  <xs:complexType name="Concrete"><xs:complexContent><xs:extension base="Base"/></xs:complexContent></xs:complexType>
                  <xs:element name="r" type="{assigned}" abstract="{element_abstract}"/></xs:schema>'''
                validator = etree.XMLSchema(etree.fromstring(source.encode()))
                self.assertEqual(validator.validate(etree.fromstring(b'<r/>')), not flags)
                specific = b'<r xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="Concrete"/>'
                self.assertEqual(validator.validate(etree.fromstring(specific)), 'abstract' not in flags)
                self.assertEqual(self.tree({'main.xsd': source}).fields[0]['flags'], flags)

    def test_identity_constraints_reported_for_named_and_inline_types(self):
        from lxml import etree
        record = ('<xs:sequence><xs:element name="id" type="xs:string"/>'
                  '<xs:element name="a" type="xs:string" maxOccurs="unbounded"/></xs:sequence>')
        for kind in ('key', 'unique', 'keyref'):
            refer = ' refer="ids"' if kind == 'keyref' else ''
            constraint = f'<xs:{kind} name="rule"{refer}><xs:selector xpath="a"/><xs:field xpath="."/></xs:{kind}>'
            if kind == 'keyref':
                constraint += '<xs:key name="ids"><xs:selector xpath="id"/><xs:field xpath="."/></xs:key>'
            for named in (False, True):
                with self.subTest(kind=kind, named=named):
                    definition = (f'<xs:complexType name="Record">{record}</xs:complexType><xs:element name="r" type="Record">'
                                  if named else f'<xs:element name="r"><xs:complexType>{record}</xs:complexType>')
                    source = f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">{definition}{constraint}</xs:element></xs:schema>'
                    validator = etree.XMLSchema(etree.fromstring(source.encode()))
                    self.assertTrue(validator.validate(etree.fromstring(b'<r><id>y</id><a>y</a></r>')))
                    self.assertFalse(validator.validate(etree.fromstring(b'<r><id>y</id><a>x</a><a>x</a></r>')))
                    with self.assertRaisesRegex(ValueError, 'Неподдерживаемая конструкция '+kind+' в r'):
                        self.tree({'main.xsd': source})

    def test_chameleon_include_resolves_unprefixed_components_in_each_namespace(self):
        xs = 'http://www.w3.org/2001/XMLSchema'
        common = f'''<xs:schema xmlns:xs="{xs}" elementFormDefault="qualified">
        <xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:length value="2"/></xs:restriction></xs:simpleType>
        <xs:complexType name="Box"><xs:sequence><xs:element name="code" type="Code"/></xs:sequence></xs:complexType></xs:schema>'''
        sources = {'main.xsd': f'''<xs:schema xmlns:xs="{xs}" xmlns:a="urn:a" xmlns:b="urn:b">
        <xs:import namespace="urn:a" schemaLocation="a.xsd"/><xs:import namespace="urn:b" schemaLocation="b.xsd"/>
        <xs:element name="r"><xs:complexType><xs:sequence><xs:element name="a" type="a:Box"/>
        <xs:element name="b" type="b:Box"/></xs:sequence></xs:complexType></xs:element></xs:schema>''',
        'common.xsd': common}
        for ns in ('a', 'b'):
            sources[ns+'.xsd'] = f'<xs:schema xmlns:xs="{xs}" targetNamespace="urn:{ns}"><xs:include schemaLocation="common.xsd"/></xs:schema>'
        tree = self.tree(sources)
        for ns in ('a', 'b'):
            field = next(f for f in tree.fields if f['path'] == 'r/'+ns+'/code')
            self.assertEqual((field['ns'], field['typeNs'], field['baseNs']), ('urn:'+ns, 'urn:'+ns, xs))
            self.assertEqual(field['facets'], [['length', '2']])

    def test_import_namespace_mismatch_is_rejected_even_at_exact_path(self):
        with self.assertRaisesRegex(ValueError, 'namespace'):
            self.tree({'main.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:import namespace="urn:a" schemaLocation="other.xsd"/><xs:element name="r"/></xs:schema>',
                       'other.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:b"/>'})

    def test_both_boolean_forms_of_mixed_content_are_preserved(self):
        for value in ("true", "1"):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("main.xsd", '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="r"><xs:complexType mixed="'+value+'"/></xs:element></xs:schema>')
            with zipfile.ZipFile(output) as archive:
                tree = Schema(archive, "main.xsd", "r"); tree.field(tree.root)
            self.assertEqual(tree.fields[0]["flags"].get("mixed"), "true")
        from lxml import etree
        for declaration in ('mixed="true"', 'mixed="1"'):
            schema = ('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:complexType name="Base" mixed="false"/>'
                      '<xs:element name="r"><xs:complexType '+declaration+'><xs:complexContent><xs:extension base="Base"/>'
                      '</xs:complexContent></xs:complexType></xs:element></xs:schema>')
            validator = etree.XMLSchema(etree.fromstring(schema.encode()))
            self.assertTrue(validator.validate(etree.fromstring(b"<r>text</r>")))
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("main.xsd", schema)
            with zipfile.ZipFile(output) as archive:
                tree = Schema(archive, "main.xsd", "r"); tree.field(tree.root)
            self.assertEqual(tree.fields[0]["flags"].get("mixed"), "true")

    def test_implicit_any_type_differs_from_empty_complex_type(self):
        fields = []
        for content in ("", "<xs:complexType/>"):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("main.xsd", '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="r">'+content+'</xs:element></xs:schema>')
            with zipfile.ZipFile(output) as archive:
                tree = Schema(archive, "main.xsd", "r")
                tree.field(tree.root)
                fields.append(tree.fields[0])
        self.assertEqual(fields[0]["base"], "xs:anyType")
        self.assertNotEqual(fields[0], fields[1])
        self.assertEqual(fields[1]["contentModel"], ["empty"])
