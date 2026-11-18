"""Build comparison input through the XSD field exporter."""
import io
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from schema import Schema, XS


def tree(source, dependencies=()):
    with zipfile.ZipFile(io.BytesIO(), "w") as archive:
        archive.writestr("main.xsd", source)
        for name, content in dependencies:
            archive.writestr(name, content)
        schema = Schema(archive, "main.xsd", "r")
        schema.field(schema.root)
    return schema.fields


def facet_trees():
    def build(base, inherited, local):
        return tree(f'''<xs:schema xmlns:xs="{XS}">
          <xs:simpleType name="Base"><xs:restriction base="xs:{base}">{inherited}</xs:restriction></xs:simpleType>
          <xs:simpleType name="Code"><xs:restriction base="Base">{local}</xs:restriction></xs:simpleType>
          <xs:element name="r" type="Code"/>
        </xs:schema>''')

    collapse = '<xs:whiteSpace value="collapse"/>'
    max20 = '<xs:maxLength value="20"/>'
    max30 = '<xs:maxLength value="30"/>'
    max10 = '<xs:maxLength value="10"/>'
    max11 = '<xs:maxLength value="11"/>'
    length8 = '<xs:length value="8"/>'
    def digits(total, fraction):
        return f'<xs:totalDigits value="{total}"/><xs:fractionDigits value="{fraction}"/>'
    return {
        "token": [build("token", "", ""), build("token", "", collapse)],
        "string": [build("string", "", ""), build("string", "", collapse)],
        "dominatedLength": [build("string", max20, max10), build("string", max30, max10)],
        "effectiveLength": [build("string", max20, max10), build("string", max30, max11)],
        "exactLength": [build("string", '<xs:minLength value="5"/>'+max20, length8),
                        build("string", '<xs:minLength value="8"/>', '<xs:maxLength value="8"/>')],
        "dominatedDigits": [build("decimal", digits(9, 4), digits(6, 2)),
                            build("decimal", digits(10, 5), digits(6, 2))],
        "effectiveDigits": [build("decimal", digits(9, 4), digits(6, 2)),
                           build("decimal", digits(10, 5), digits(6, 3))],
        "integerDigits": [build("integer", "", ""), build("integer", "", '<xs:fractionDigits value="0"/>')],
    }


def bound_trees():
    from lxml import etree

    def build(base, inherited, local, values, expected):
        facets = lambda rows: ''.join(f'<xs:{key} value="{value}"/>' for key, value in rows)
        source = f'''<xs:schema xmlns:xs="{XS}">
          <xs:simpleType name="Base"><xs:restriction base="xs:{base}">{facets(inherited)}</xs:restriction></xs:simpleType>
          <xs:simpleType name="Code"><xs:restriction base="Base">{facets(local)}</xs:restriction></xs:simpleType>
          <xs:element name="r" type="Code"/>
        </xs:schema>'''
        native = etree.XMLSchema(etree.fromstring(source.encode()))
        assert [value for value in values if native.validate(etree.XML(f'<r>{value}</r>'))] == expected
        return tree(source)

    values = ('-1', '0', '1', '9', '10', '11', '19', '20', '21')
    left = build('decimal', [('minInclusive', '0'), ('maxInclusive', '30')],
                 [('minInclusive', '10'), ('maxExclusive', '20')], values, ['10', '11', '19'])
    right = build('decimal', [('minExclusive', '1'), ('maxInclusive', '25')],
                  [('minInclusive', '+10.000'), ('maxExclusive', '20.0')], values, ['10', '11', '19'])
    changed = build('decimal', [('minInclusive', '1'), ('maxInclusive', '25')],
                    [('minInclusive', '11'), ('maxExclusive', '20')], values, ['11', '19'])
    exclusive = build('decimal', [], [('minExclusive', '10'), ('maxExclusive', '20')], values, ['11', '19'])
    inclusive = build('decimal', [], [('minInclusive', '10'), ('maxInclusive', '20')], values, ['10', '11', '19', '20'])
    discrete = [build('integer', [], limits, values, ['10', '11', '19']) for limits in (
        [('minExclusive', '9'), ('maxExclusive', '20')], [('minInclusive', '10'), ('maxInclusive', '19')])]
    exact = ('9007199254740993.0123', '9007199254740993.0124')
    precise = [build('decimal', [], [('minInclusive', low)], exact, list(exact[i:])) for i, low in enumerate(exact)]
    return {'dominated': [left, right], 'changed': [left, changed], 'exclusive': [left, exclusive],
            'inclusive': [left, inclusive], 'integer': discrete, 'precise': precise}


def enum_whitespace_trees():
    from lxml import etree

    def build(value, base, site, expected):
        collapse = '<xs:whiteSpace value="collapse"/>'
        enum = f'<xs:enumeration value="{value}"/>'
        inherited, local = {
            'base': (enum, collapse),
            'local': ('', collapse + enum),
            'derived': ('', enum),
        }[site]
        source = f'''<xs:schema xmlns:xs="{XS}">
          <xs:simpleType name="Base"><xs:restriction base="xs:{base}">{inherited}</xs:restriction></xs:simpleType>
          <xs:simpleType name="Code"><xs:restriction base="Base">{local}</xs:restriction></xs:simpleType>
          <xs:element name="r" type="Code"/>
        </xs:schema>'''
        native = etree.XMLSchema(etree.fromstring(source.encode()))
        assert [native.validate(etree.fromstring(xml)) for xml in (b'<r>A</r>', b'<r> A </r>')] == [expected] * 2
        return tree(source)

    return {
        'inherited': [build(value, 'string', 'base', expected) for value, expected in [(' A ', False), ('A', True)]],
        'sameRestriction': [build(value, 'string', 'local', expected) for value, expected in [(' A ', False), ('A', True)]],
        'baseAlreadyCollapse': [build(value, 'token', 'derived', True) for value in (' A ', 'A')],
    }


def union_source(prefix="p", uri="urn:A", *, value=None, members="xs:QName xs:integer",
                 definitions="", inherited="", extra_enum=""):
    return f'''<xs:schema xmlns:xs="{XS}" xmlns:{prefix}="urn:root">
      {definitions}
      <xs:simpleType name="Base"><xs:union memberTypes="{members}"/></xs:simpleType>
      {inherited}
      <xs:simpleType name="Code"><xs:restriction base="{'Parent' if inherited else 'Base'}">
        <xs:enumeration xmlns:{prefix}="{uri}" value="{value if value is not None else prefix+':read'}"/>
        {extra_enum}
      </xs:restriction></xs:simpleType>
      <xs:element name="r" type="Code"/>
    </xs:schema>'''


def union_trees():
    notation = '''<xs:notation name="note" public="sample"/>
          <xs:simpleType name="Head"><xs:restriction base="xs:NOTATION">
            <xs:enumeration value="note"/>
          </xs:restriction></xs:simpleType>'''
    qname_choice = '''<xs:simpleType name="QNameChoice"><xs:restriction base="xs:QName">
      <xs:enumeration xmlns:p="urn:A" value="p:read"/><xs:enumeration xmlns:p="urn:B" value="p:read"/>
    </xs:restriction></xs:simpleType>'''
    string_head = '''<xs:simpleType name="Head"><xs:restriction base="xs:string">
      <xs:pattern value="a"/>
    </xs:restriction></xs:simpleType>'''

    result = {
        "qname": [tree(union_source()), tree(union_source(uri="urn:B")), tree(union_source("q"))],
        "stringFirst": [tree(union_source(uri=uri, members="xs:string xs:QName")) for uri in ("urn:A", "urn:B")],
        "integer": [tree(union_source(value=value)) for value in ("01", "1", "2")],
        "mixedNumeric": [tree(union_source(value=value, members="xs:integer xs:decimal"))
                         for value in ("1", "1.0", "1.1")],
        "mixedString": [tree(union_source(value=value, members="Head xs:token", definitions=string_head))
                        for value in ("a", " a ", " b ")],
        "mixed": [tree(union_source(uri=uri, members="xs:QName Head xs:string", definitions=notation,
                                    extra_enum='<xs:enumeration value="01"/>')) for uri in ("urn:A", "urn:B")],
        "mixedSameLexeme": [tree(union_source(uri=uri, members="QNameChoice Head xs:string",
            definitions=notation+qname_choice, extra_enum='<xs:enumeration xmlns:p="urn:other" value="p:read"/>'))
            for uri in ("urn:A", "urn:B")],
    }
    # Old snapshots could not select a member after a detached NOTATION probe.
    for case in ('mixed', 'mixedSameLexeme'):
        for fields in result[case]:
            fields[0]['unionValueTypes'][-1] = None
    return result


def union_flag_trees():
    from lxml import etree

    def build(uri, prefix='p', *, flag='fixed', declaration='direct', uncertain=False):
        members = ('Head ' if uncertain else '') + 'xs:QName xs:boolean'
        notation = '''<xs:notation name="note" public="sample"/>
        <xs:simpleType name="Head"><xs:restriction base="xs:NOTATION">
          <xs:enumeration value="note"/>
        </xs:restriction></xs:simpleType>''' if uncertain else ''
        value = f'xmlns:{prefix}="{uri}" {flag}="{prefix}:X"'
        attribute = '<xs:attribute name="value" type="Code"'
        if declaration == 'direct':
            global_attribute, use = '', f'{attribute} {value}/>'
        elif declaration == 'global':
            global_attribute, use = f'{attribute} {value}/>', '<xs:attribute ref="value"/>'
        else:
            global_attribute, use = f'{attribute}/>', f'<xs:attribute ref="value" {value}/>'
        source = f'''<xs:schema xmlns:xs="{XS}" xmlns:{prefix}="urn:outer">
          {notation}
          <xs:simpleType name="Code"><xs:union memberTypes="{members}"/></xs:simpleType>
          {global_attribute}
          <xs:element name="r"><xs:complexType>{use}</xs:complexType></xs:element>
        </xs:schema>'''
        native = etree.XMLSchema(etree.fromstring(source.encode()))
        if flag == 'fixed' and declaration != 'local':
            values = ('urn:A', 'urn:B')
            accepted = [u for u in values if native.validate(etree.XML(f'<r xmlns:i="{u}" value="i:X"/>'))]
            assert accepted == [uri], (declaration, uri, prefix, accepted, str(native.error_log))
        return tree(source)

    result = {declaration + flag.capitalize(): [build('urn:A', flag=flag, declaration=declaration),
                                               build('urn:B', flag=flag, declaration=declaration),
                                               build('urn:A', 'q', flag=flag, declaration=declaration)]
              for declaration in ('direct', 'global', 'local') for flag in ('fixed', 'default')}
    result['unresolved'] = [build(uri, uncertain=True) for uri in ('urn:A', 'urn:B')]
    for fields in result['unresolved']:
        fields[1]['unionFlagTypes']['fixed'] = None
        fields[1].pop('flagQNames', None)
    return result


def notation_schema(prefix, uri, union):
    source = f'''<xs:schema xmlns:xs="{XS}" xmlns:{prefix}="{uri}">
          <xs:import namespace="{uri}" schemaLocation="notation.xsd"/>
          <xs:simpleType name="N"><xs:restriction base="xs:NOTATION">
            <xs:enumeration value="{prefix}:code"/>
          </xs:restriction></xs:simpleType>
          <xs:simpleType name="U"><xs:union memberTypes="N xs:integer"/></xs:simpleType>
          <xs:simpleType name="Code"><xs:restriction base="{'U' if union else 'N'}">
            <xs:enumeration value="{prefix}:code"/>
          </xs:restriction></xs:simpleType>
          <xs:element name="r"><xs:complexType>
            <xs:attribute name="first" type="Code" fixed="{prefix}:code"/>
            <xs:attribute name="second" type="Code" default="{prefix}:code"/>
          </xs:complexType></xs:element>
        </xs:schema>'''
    notation = f'<xs:schema xmlns:xs="{XS}" targetNamespace="{uri}"><xs:notation name="code" public="test"/></xs:schema>'
    return {'main.xsd': source, 'notation.xsd': notation}


def notation_examples(uri):
    return [(f'<r xmlns:i="{candidate}" first="i:code" second="i:code"/>', candidate == uri)
            for candidate in ('urn:A', 'urn:B')]


def notation_trees():
    from lxml import etree

    def build(prefix, uri, union):
        sources = notation_schema(prefix, uri, union)
        with TemporaryDirectory() as directory:
            base = Path(directory)
            for name, source in sources.items():
                (base/name).write_text(source, encoding='utf-8')
            validator = etree.XMLSchema(etree.parse(str(base/'main.xsd')))
            for xml, expected in notation_examples(uri):
                assert validator.validate(etree.XML(xml)) == expected, str(validator.error_log)
        return tree(sources['main.xsd'], [('notation.xsd', sources['notation.xsd'])])

    return {kind: [build(prefix, uri, kind == 'union') for prefix, uri in
                   [('p', 'urn:A'), ('p', 'urn:B'), ('q', 'urn:A')]] for kind in ('direct', 'union')}


def list_value_trees():
    from lxml import etree

    def build(item, value, uri='', *, union=False, flag=None):
        value_type = 'Choice' if union else 'Items'
        choice = '<xs:simpleType name="Choice"><xs:union memberTypes="Items xs:boolean"/></xs:simpleType>' if union else ''
        binding = f'xmlns:{value.split(":")[0]}="{uri}"' if uri else ''
        declaration = (f'<xs:element name="r" {binding} type="{value_type}" {flag}="{value}"/>' if flag else
                       f'<xs:element name="r"><xs:simpleType><xs:restriction base="{value_type}">'
                       f'<xs:enumeration {binding} value="{value}"/></xs:restriction></xs:simpleType></xs:element>')
        source = (f'<xs:schema xmlns:xs="{XS}"><xs:simpleType name="Items"><xs:list itemType="xs:{item}"/>'
                  '</xs:simpleType>'+choice+declaration+'</xs:schema>')
        validator = etree.XMLSchema(etree.XML(source))
        if not flag:
            accepted = (f'<r xmlns:q="{uri}">{value.replace(value.split(":")[0]+":", "q:")}</r>' if uri else '<r>1 2</r>')
            assert validator.validate(etree.XML(accepted))
            if uri:
                assert not validator.validate(etree.XML(accepted.replace(uri, 'urn:unrelated')))
        return tree(source)

    return {
        'numeric': [build('int', value) for value in ('1 2', '+01 002')],
        'order': [build('int', value, flag='default') for value in ('1 2', '2 1')],
        'qname': [build('QName', value, uri) for value, uri in (
            ('p:one p:two', 'urn:A'), ('p:one p:two', 'urn:B'), ('q:one q:two', 'urn:A'))],
        'unionFixed': [build('QName', value, uri, union=True, flag='fixed') for value, uri in (
            ('p:one p:two', 'urn:A'), ('p:one p:two', 'urn:B'), ('q:one q:two', 'urn:A'))],
    }


if __name__ == "__main__":
    print(json.dumps({"facets": facet_trees(), "bounds": bound_trees(), "enumWhitespace": enum_whitespace_trees(),
                      "union": union_trees(), "unionFlags": union_flag_trees(), "notation": notation_trees(),
                      "listValues": list_value_trees()}))
