import json
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from audit_module_tables import audit
from module_xsd import W


ROOT = Path(__file__).resolve().parents[1]
HEADER = ['Элемент/Атрибут', 'Тип', 'Ограничения', 'Количество вхождений', 'Описание']


def make_body(*blocks):
    body = ET.Element(W + 'body')
    for block in blocks:
        if isinstance(block, str):
            ET.SubElement(ET.SubElement(body, W + 'p'), W + 't').text = block
        else:
            table = ET.SubElement(body, W + 'tbl')
            for values in block:
                row = ET.SubElement(table, W + 'tr')
                for value in values:
                    cell = ET.SubElement(row, W + 'tc')
                    ET.SubElement(ET.SubElement(cell, W + 'p'), W + 't').text = value
    return body


def field(name, path, parent, typ, children=()):
    return dict(name=name, path=path, parent=parent, children=children, type=typ, base=typ,
                kind='element', min='1', max='1', group='', ns='urn:test', file='Example.xsd',
                facets=[], flags={}, values=[])


class ModuleAuditTests(unittest.TestCase):
    def setUp(self):
        self.revision = dict(number='55', root='notice', namespace='urn:test', schema_path='Example.xsd')
        self.fields = [field('notice', 'notice', -1, 'NoticeType', [1]),
                       field('code', 'notice/code', 0, 'xs:string')]

    def test_current_constraints_recomputed_after_either_source_changes(self):
        def document(name, typ, limit):
            return make_body('Тип сообщения 56: другое', 'Таблица 1', [HEADER, ['other', '', '', '1', '']],
                             'Тип сообщения 55: извещение', 'Таблица 2', [HEADER, [name, typ, limit, '1', '']])
        body = document('Code', 'integer', 'Макс. длина: 2')
        self.fields[1].update(min='0', facets=[['maxLength', '3']])
        checked, findings = audit(self.revision, body, self.fields)
        self.assertGreater(checked, 0)
        self.assertEqual({f['kind'] for f in findings}, {'Имя элемента', 'Кратность', 'Тип данных', 'Ограничение длины'})
        self.assertTrue(all('Таблица 2' in f['location'] and 'notice/code' in f['xlocation'] for f in findings))
        self.fields[1].update(min='1', facets=[['maxLength', '2']])
        self.assertEqual(audit(self.revision, document('code', 'string', 'Макс. длина: 2'), self.fields)[1], [])

    def test_root_card_checks_child_list_and_skips_another_section(self):
        body = make_body('Тип сообщения 55: извещение', 'Таблица 1',
                         [['Тип', 'NoticeType'], ['Дочерние элементы', 'code extra'],
                          ['Схема', '<xs:element name="notice" type="NoticeType"/>']],
                         'Тип сообщения 56: другое', 'Таблица 2', [HEADER, ['nonsense', 'string', '', '1', '']])
        findings = audit(self.revision, body, self.fields)[1]
        self.assertEqual(len(findings), 1)
        self.assertIn('extra', findings[0]['spec'])
        self.fields[0]['children'].append(2)
        self.fields.append(field('extra', 'notice/extra', 0, 'xs:string'))
        self.assertEqual(audit(self.revision, body, self.fields)[1], [])

    def test_current_service_message_six_scanned(self):
        editions = json.loads((ROOT/'data/catalog.json').read_bytes())['revisions']
        r = next(r for r in editions if r['number'] == '6' and r.get('source_type') == 'module'
                 and r['environment'] == 'test')
        with zipfile.ZipFile(ROOT/'state/objects'/r['document_sha256']) as z:
            body = ET.fromstring(z.read('word/document.xml')).find(W + 'body')
        fields = json.loads((ROOT/'data/fields'/(r['revision_id']+'.json')).read_bytes())['fields']
        checked, findings = audit(r, body, fields)
        self.assertGreater(checked, 0)
        self.assertFalse(any('Таблица 1 ' in f['location'] for f in findings))

    def test_nested_schema_fragment_checks_missing_child_and_attributes(self):
        self.fields[0]['children'].append(2)
        self.fields.append(field('@id', 'notice/@id', 0, 'xs:string'))
        self.fields[2].update(kind='attribute', min='0')
        def fragment(content):
            return make_body('Тип сообщения 55: извещение', 'Таблица 1', [
                ['Схема', '<xs:element name="notice"><xs:complexType>'+content+'</xs:complexType></xs:element>']])
        _, issues = audit(self.revision, fragment('<xs:sequence/>'), self.fields)
        self.assertEqual({i['xlocation'].split(' · ')[-1] for i in issues}, {'notice/code', 'notice/@id'})
        documented = '<xs:sequence><xs:element name="code" type="xs:string"/></xs:sequence><xs:attribute name="id" type="xs:string"/>'
        self.assertEqual(audit(self.revision, fragment(documented), self.fields)[1], [])

    def test_different_deadline_events_and_corrected_annotation(self):
        body = make_body('Тип сообщения 55: извещение', 'Таблица 4', [HEADER, [
            'code', 'date', '', '1', 'Крайняя дата получения подтверждения прочтения сообщения']])
        self.fields[1].update(type='xs:date', base='xs:date', doc='Крайняя дата выгрузки документа из СЭДО')
        self.assertEqual([f['kind'] for f in audit(self.revision, body, self.fields)[1]], ['Описание поля'])
        self.fields[1]['doc'] = 'Крайняя дата получения подтверждения прочтения'
        self.assertEqual(audit(self.revision, body, self.fields)[1], [])
