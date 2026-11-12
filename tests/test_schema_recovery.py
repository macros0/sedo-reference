import io
import unittest
import zipfile
from xml.etree import ElementTree as ET
from module_xsd import document_bindings, extract_archives
from schema import Schema, dependency_set
from sync import inspect_file

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
def docx(paragraphs):
    doc=ET.Element('{'+W+'}document');body=ET.SubElement(doc,'{'+W+'}body')
    for text in paragraphs:
        p=ET.SubElement(body,'{'+W+'}p');r=ET.SubElement(p,'{'+W+'}r');ET.SubElement(r,'{'+W+'}t').text=text
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr('word/document.xml',ET.tostring(doc))
    return out.getvalue(),doc

def archive(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for path,content in files.items():z.writestr(path,content)
    return out.getvalue()

class SchemaRecoveryTests(unittest.TestCase):
    def test_binding_from_complete_xml_example(self):
        _,doc=docx(['Тип сообщения 111: Уведомление ЭЛН','Пример','<?xml version="1.0"?>','<e:changeElnNotice xmlns:e="urn:eln">','<e:code>123</e:code>','</e:changeElnNotice>'])
        binding=document_bindings(doc)[0]
        self.assertEqual((binding['number'],binding['root'],binding['namespace']),('111','changeElnNotice','urn:eln'))

    def test_document_root_table_wins_over_copied_wrong_xml_example(self):
        _,doc=docx(['Тип сообщения 602: Надбавка'])
        body=doc.find('{'+W+'}body');table=ET.SubElement(body,'{'+W+'}tbl')
        for cells in [['Элемент/Атрибут','Тип','Ограничения','Количество вхождений','Описание'],['DecisionSurchargeRust','DocumentType','-','1','Решение']]:
            row=ET.SubElement(table,'{'+W+'}tr')
            for value in cells:
                cell=ET.SubElement(row,'{'+W+'}tc');p=ET.SubElement(cell,'{'+W+'}p');r=ET.SubElement(p,'{'+W+'}r');ET.SubElement(r,'{'+W+'}t').text=value
        p=ET.SubElement(body,'{'+W+'}p');r=ET.SubElement(p,'{'+W+'}r');ET.SubElement(r,'{'+W+'}t').text='<DecisionDiscountRust></DecisionDiscountRust>'
        self.assertEqual(document_bindings(doc)[0]['root'],'DecisionSurchargeRust')

    def test_published_module_roots_are_not_reinterpreted(self):
        import json
        from pathlib import Path
        root=Path(__file__).resolve().parents[1];state=root/'state'
        snapshot=json.loads((state/'snapshots'/(json.loads((state/'current.json').read_bytes())['snapshot']+'.json')).read_bytes())
        for row in snapshot['entries']:
            if not row.get('module_matches'):continue
            raw=(state/'objects'/row['sha256']).read_bytes()
            bindings={b['number']:b for b in inspect_file(raw,'docx').get('module_bindings',[])}
            for match in row['module_matches']:
                self.assertIn(match['number'],bindings)
                self.assertEqual(bindings[match['number']]['root'],match['root'],row['label']+' / '+match['number'])

    def test_root_from_complete_inline_schema_in_message_section(self):
        _,doc=docx(['Тип сообщения 115: Извещение','<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="notice" type="xs:string"/></xs:schema>'])
        self.assertEqual(document_bindings(doc)[0]['root'],'notice')

    def test_attribute_group_retains_required_attribute_constraints(self):
        xsd='<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:t="urn:test" targetNamespace="urn:test"><xs:attributeGroup name="Version"><xs:attribute name="version" use="required"><xs:simpleType><xs:restriction base="xs:string"><xs:length value="2"/></xs:restriction></xs:simpleType></xs:attribute></xs:attributeGroup><xs:element name="root"><xs:complexType><xs:attributeGroup ref="t:Version"/></xs:complexType></xs:element></xs:schema>'
        with zipfile.ZipFile(io.BytesIO(archive({'main.xsd':xsd}))) as z:
            tree=Schema(z,'main.xsd','root');tree.field(tree.root)
        attribute=tree.fields[1];self.assertEqual(attribute['name'],'@version');self.assertEqual(attribute['min'],'1');self.assertIn(['length','2'],attribute['facets'])

    def test_complete_inline_xsd_preserves_regex_whitespace(self):
        xsd='<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:vacation"><xs:element name="request"><xs:simpleType><xs:restriction base="xs:string"><xs:pattern value="a  b"/></xs:restriction></xs:simpleType></xs:element></xs:schema>'
        raw,_=docx(['Приложение А. XSD-схема',xsd]);packages,issues=extract_archives(raw)
        self.assertFalse(issues);self.assertEqual(len(packages),1)
        with zipfile.ZipFile(io.BytesIO(packages[0]['content'])) as z:
            self.assertIn(b'value="a  b"',z.read(z.namelist()[0]))

    def test_broken_relative_import_uses_unique_same_archive_namespace(self):
        main='<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:c="urn:common" targetNamespace="urn:root"><xs:import namespace="urn:common" schemaLocation="../wrong/type/Common.xsd"/><xs:element name="root" type="c:Code"/></xs:schema>'
        common='<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:common"><xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:length value="4"/></xs:restriction></xs:simpleType></xs:schema>'
        content=archive({'root/main.xsd':main,'type/Common.xsd':common})
        deps=dependency_set(inspect_file(content,'zip')['schemas'],'root/main.xsd')
        self.assertEqual(len(deps),2)
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            tree=Schema(z,'root/main.xsd','root');tree.field(tree.root)
        self.assertIn(['length','4'],tree.fields[0]['facets'])

    def test_ambiguous_import_versions_are_rejected(self):
        main='<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:import namespace="urn:common" schemaLocation="missing/Common.xsd"/><xs:element name="root" type="xs:string"/></xs:schema>'
        def common(length):return f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:common"><xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:length value="{length}"/></xs:restriction></xs:simpleType></xs:schema>'
        content=archive({'main.xsd':main,'a/Common.xsd':common(4),'b/Common.xsd':common(5)})
        with self.assertRaisesRegex(ValueError,'Неоднозначный импорт'):
            dependency_set(inspect_file(content,'zip')['schemas'],'main.xsd')

    def test_union_keeps_alternative_constraints_separate(self):
        xsd='<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="root"><xs:simpleType><xs:union><xs:simpleType><xs:restriction base="xs:string"><xs:maxLength value="3"/></xs:restriction></xs:simpleType><xs:simpleType><xs:restriction base="xs:int"><xs:maxInclusive value="10"/></xs:restriction></xs:simpleType></xs:union></xs:simpleType></xs:element></xs:schema>'
        with zipfile.ZipFile(io.BytesIO(archive({'main.xsd':xsd}))) as z:
            tree=Schema(z,'main.xsd','root');tree.field(tree.root)
        field=tree.fields[0]
        self.assertFalse(field['facets'])
        self.assertEqual(len(field['alternatives']),2)
        self.assertIn(['maxLength','3'],field['alternatives'][0]['facets'])
        self.assertIn(['maxInclusive','10'],field['alternatives'][1]['facets'])

if __name__=='__main__':unittest.main()
