import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
import unittest
from review_field_links import review_sources


class FieldReviewSourcesTests(unittest.TestCase):
    def test_new_document_only_edition_does_not_change_historical_composition_evidence(self):
        from unittest.mock import patch
        root = Path(__file__).resolve().parents[1]
        path = root / 'data/catalog.json'
        catalog = json.loads(path.read_bytes())
        catalog['revisions'].append(dict(number='313', environment='prod',
            revision_id='new-document-without-xsd', status='document_only', source_type='manual'))
        read = Path.read_bytes
        def candidate(source):
            return json.dumps(catalog).encode() if source == path else read(source)
        with patch.object(Path, 'read_bytes', candidate):
            self.test_ten_historical_composition_findings_link_to_declared_children()

    def test_occurrence_evidence_links_to_the_local_reference(self):
        import io
        from review_field_links import attach_schema_sources
        from tests.test_schema_recovery import archive
        from schema import XS
        main = (f'<xs:schema xmlns:xs="{XS}" xmlns:b="urn:b"><xs:import namespace="urn:b" schemaLocation="b.xsd"/>'
                '<xs:element name="root"><xs:complexType><xs:sequence><xs:element ref="b:child" minOccurs="0"/>'
                '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        child = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:b"><xs:element name="child" type="xs:string"/></xs:schema>'
        revision = dict(schema_path='a.xsd', root='root')
        finding = dict(kind='Кратность', xlocation='b.xsd · root/child', xsd='Кратность 0..1.', field_paths=['root/child'])
        linked = attach_schema_sources(revision, [finding], io.BytesIO(archive({'a.xsd':main,'b.xsd':child})))
        self.assertEqual(linked[0]['xsd_source']['path'], 'a.xsd')

    def test_xml_example_location_uses_known_field_or_named_parent(self):
        from review_field_links import attach_targets, attach_schema_sources
        from schema import Schema
        sources = {
            'main.xsd':'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:include schemaLocation="types.xsd"/><xs:element name="r" type="Record"/></xs:schema>',
            'types.xsd':'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:complexType name="Record"><xs:sequence><xs:element name="a" type="xs:int"/></xs:sequence></xs:complexType></xs:schema>'}
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder)/'sources.zip'
            with zipfile.ZipFile(archive,'w') as z:
                for name, content in sources.items(): z.writestr(name,content)
            with zipfile.ZipFile(archive) as z:
                tree = Schema(z,'main.xsd','r');tree.field(tree.root)
            row = dict(schema_path='main.xsd',root='r')
            for path, scope in [('/r/a','field'),('/r/a/unknown','parent')]:
                issue = dict(kind='XML-пример', location='Пример 1 — '+path, xlocation='main.xsd → r', xsd='Правило')
                finding = attach_schema_sources(row,[attach_targets(issue,tree.fields)],archive)[0]
                self.assertEqual(finding['field_link_scope'],scope)
                self.assertIn('r/a', finding['xlocation'])
                self.assertEqual('родитель' in finding['xlocation'].lower(),scope=='parent')
                self.assertEqual(finding['xsd_source']['sha256'],hashlib.sha256(sources['types.xsd'].encode()).hexdigest())

    def test_imported_group_owns_child_composition_evidence(self):
        from audit_module_tables import audit
        from review_field_links import attach_targets, attach_schema_sources
        from schema import Schema
        sources = {
            'main.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:include schemaLocation="group.xsd"/><xs:element name="root"><xs:complexType><xs:group ref="Children"/></xs:complexType></xs:element></xs:schema>',
            'group.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:group name="Children"><xs:sequence><xs:element name="letter"/></xs:sequence></xs:group></xs:schema>'}
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder)/'schemas.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for name, text in sources.items(): z.writestr(name, text)
            with zipfile.ZipFile(archive) as z:
                tree = Schema(z, 'main.xsd', 'root'); tree.field(tree.root)
            revision = dict(number='1', schema_path='main.xsd', root='root')
            tables = [('1', 'Таблица 1', '', None, {'Схема': '<xs:element name="root"/>', 'Дочерние элементы': 'other'})]
            findings = [attach_targets(f, tree.fields) for f in audit(revision, tables, tree.fields)[1]]
            resolved = attach_schema_sources(revision, findings, archive)
            self.assertEqual(resolved[0]['xsd_source']['path'], 'group.xsd')
            self.assertEqual(resolved[0]['xsd_source']['sha256'], hashlib.sha256(sources['group.xsd'].encode()).hexdigest())
            self.assertEqual(resolved[0]['field_paths'], ['root'])

    def test_group_ref_use_owns_composition_instead_of_global_element(self):
        from audit_module_tables import audit
        from review_field_links import attach_targets, attach_schema_sources
        from schema import Schema
        sources = {
            'main.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:include schemaLocation="group.xsd"/><xs:element name="root"><xs:complexType><xs:group ref="Children"/></xs:complexType></xs:element></xs:schema>',
            'group.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:include schemaLocation="defs.xsd"/><xs:group name="Children"><xs:sequence><xs:element ref="letter"/></xs:sequence></xs:group></xs:schema>',
            'defs.xsd': '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="letter" type="xs:string"/></xs:schema>'}
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder)/'schemas.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for name, text in sources.items(): z.writestr(name, text)
            with zipfile.ZipFile(archive) as z:
                tree = Schema(z, 'main.xsd', 'root'); tree.field(tree.root)
            revision = dict(number='1', schema_path='main.xsd', root='root')
            tables = [('1', 'Таблица 1', '', None, {'Схема': '<xs:element name="root"/>', 'Дочерние элементы': 'other'})]
            findings = [attach_targets(f, tree.fields) for f in audit(revision, tables, tree.fields)[1]]
            finding = attach_schema_sources(revision, findings, archive)[0]
            self.assertEqual(finding['xsd_source']['path'], 'group.xsd')
            self.assertEqual(finding['xsd_source']['sha256'], hashlib.sha256(sources['group.xsd'].encode()).hexdigest())
            self.assertEqual(finding['field_paths'], ['root'])

    def test_ten_historical_composition_findings_link_to_declared_children(self):
        from schema_review import compare_revision
        root = Path(__file__).resolve().parents[1]
        rows = json.loads((root/'data/catalog.json').read_bytes())['revisions']
        historical = {
            '5c84dc74a62652dd26176fe57be06656b43a2076d356ecfad591354865067043',
            '3a439568c2611ad1e4a5ea2743ea6def0c7a206115be01c85edfbd7e486ad1b1',
            '48e8c9aaa40a0f7fb9bb55c43ecfd02b5eabce84b85e15eb8e1250e762569f0c',
            'c1074170c72ba52e081bedd3f6a14451887f713bac8fb85f34b6659876cbece1',
            'bb8d09abfec9e0b648e8ca1031537f152203c60466743d7918a711052893c5e4',
            '3de9048efafa64190deb2e59908880cb4519d083cf0d3fee319a620338188ca7',
            'c649590d7dbf25b15ec6df7d99068100c1df97dbbd680aafc310134c12aa02c9',
            'd265fecf23e855212f268fe6bcbdc9dbc356d7039bd1c836befd47bb8510bb8b',
            '238ca3ef37659185f7831cc840aac86601b20a1fa6608566f0f9543a3bf7f003',
            '90431b6ba5de748e57a5d0b55e3241efa65bd6f3d4c5a78d64b2fd3802ed8c77',
        }
        self.assertTrue(historical <= {row['revision_id'] for row in rows})
        checked = set()
        for revision in rows:
            number = str(revision['number'])
            if revision['revision_id'] not in historical: continue
            fields = json.loads((root/'data/fields'/(revision['revision_id']+'.json')).read_bytes())['fields']
            findings = compare_revision(revision, fields, root/'state/objects')['findings']
            for finding in findings:
                if finding['kind'] != 'Состав сообщения' or not finding['xsd'].startswith('Дочерние элементы:'): continue
                expected = 'letter' if number in ('313', '314') else 'department'
                source = finding['xsd_source']
                with zipfile.ZipFile(root/'state/objects'/revision['archive_sha256']) as archive:
                    raw = archive.read(source['path'])
                self.assertIn(('name="'+expected+'"').encode(), raw)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), source['sha256'])
                self.assertEqual(source['download'], 'sources/'+source['sha256']+'.xsd')
                checked.add((number, revision['environment']))
        self.assertEqual(len(checked), 10)

    def test_only_same_schema_number_and_contour_sources_are_combined(self):
        finding = dict(kind="Тип", field_paths=["root/value"], spec="В документе", xsd="В XSD")
        first = dict(number="6", environment="prod", ready=True, revision_id="one", display_revision_id="group",
                     document_sha256="doc1", document_url="https://lk.sfr.gov.ru/first.docx",
                     schema_review=dict(findings=[finding]), document_version="1")
        second = dict(first, revision_id="two", document_sha256="doc2",
                      document_url="https://lk.sfr.gov.ru/second.docx", document_version="2")
        other_env = dict(first, environment="test", document_url="https://lk-test.fss.ru/test.docx")
        other_schema = dict(first, display_revision_id="different", document_url="https://lk.sfr.gov.ru/other.docx")
        other_number = dict(first, number="7", document_url="https://lk.sfr.gov.ru/seven.docx")
        sources = review_sources(dict(revisions=[first, second, first, other_env, other_schema, other_number]), first)
        self.assertEqual([s["name"] for s in sources], ["first.docx", "second.docx"])
        self.assertEqual([len(s["findings"]) for s in sources], [1, 1])

    def test_rebuilding_with_resolved_or_unlinked_findings_removes_field_notice(self):
        row = dict(number="6", environment="prod", ready=True, revision_id="one",
                   schema_review=dict(findings=[dict(kind="Тип", field_paths=["root/value"])]))
        self.assertEqual(len(review_sources(dict(revisions=[row]), row)), 1)
        row["schema_review"]["findings"] = [dict(kind="Отсутствующее объявление", field_paths=[])]
        self.assertEqual(review_sources(dict(revisions=[row]), row), [])
        row["schema_review"]["findings"] = []
        self.assertEqual(review_sources(dict(revisions=[row]), row), [])
