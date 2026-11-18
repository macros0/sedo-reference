"""Shared typed fixed regression corpus for native and actual WASM validators."""
import base64
import json

XS = 'http://www.w3.org/2001/XMLSchema'


def fixture():
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    source = f'''<xs:schema xmlns:xs="{XS}" xmlns:k="urn:kind">
<xs:simpleType name="Whole"><xs:restriction base="xs:decimal"><xs:pattern value="[0-9]+"/></xs:restriction></xs:simpleType>
<xs:simpleType name="UtcTime"><xs:restriction base="xs:time"><xs:pattern value=".*Z"/></xs:restriction></xs:simpleType>
<xs:complexType name="DecimalContent"><xs:simpleContent><xs:extension base="xs:decimal"><xs:attribute name="note"/></xs:extension></xs:simpleContent></xs:complexType>
<xs:complexType name="NarrowContent"><xs:simpleContent><xs:restriction base="DecimalContent"><xs:pattern value="[0-9]+"/></xs:restriction></xs:simpleContent></xs:complexType>
<xs:complexType name="BooleanContent"><xs:simpleContent><xs:extension base="xs:boolean"/></xs:simpleContent></xs:complexType>
<xs:complexType name="StringContent"><xs:simpleContent><xs:extension base="xs:string"/></xs:simpleContent></xs:complexType>
<xs:complexType name="Collapsed"><xs:simpleContent><xs:restriction base="StringContent"><xs:whiteSpace value="collapse"/></xs:restriction></xs:simpleContent></xs:complexType>
<xs:complexType name="Replaced"><xs:simpleContent><xs:restriction base="StringContent"><xs:whiteSpace value="replace"/></xs:restriction></xs:simpleContent></xs:complexType>
<xs:element name="request"><xs:complexType><xs:choice minOccurs="0" maxOccurs="unbounded">
<xs:element name="decimal" type="xs:decimal" fixed="1"/>
<xs:element name="integer" type="xs:int" fixed="1"/>
<xs:element name="token" type="xs:token" fixed="foo bar"/>
<xs:element name="qname" type="xs:QName" fixed="k:known"/>
<xs:element name="literal" type="xs:string" fixed="true"/>
<xs:element name="pattern" type="Whole" fixed="1"/>
<xs:element name="content" type="DecimalContent" fixed="1"/>
<xs:element name="flag" type="BooleanContent" fixed="true"/>
<xs:element name="text" type="xs:string" fixed="a&lt;/x"/>
<xs:element name="collapsed" type="Collapsed" fixed="foo bar"/>
<xs:element name="spaced" type="Collapsed" fixed=" foo   bar "/>
<xs:element name="replaced" type="Replaced" fixed="foo bar"/>
<xs:element name="spacedliteral" type="xs:string" fixed=" foo bar "/>
<xs:element name="instant" type="xs:dateTime" fixed="2026-11-17T00:00:00.1234567890123456781Z"/>
<xs:element name="dayStart" type="xs:dateTime" fixed="2024-03-01T00:00:00Z"/>
<xs:element name="eraStart" type="xs:dateTime" fixed="0001-01-01T00:00:00Z"/>
<xs:element name="midnight" type="xs:time" fixed="00:00:00"/>
<xs:element name="clock" type="xs:time" fixed="00:00:00.1234567890123456781Z"/>
<xs:element name="utc" type="UtcTime" fixed="00:00:00Z"/>
</xs:choice></xs:complexType></xs:element></xs:schema>'''.encode()
    return row, {'main.xsd': source}


# Expected application verdict, followed by the original lxml schema verdict.
CALENDAR_CASES = [
    ('<instant>2026-11-17T00:00:00.1234567890123456781Z</instant>', True, True),
    ('<instant>2026-11-17T00:00:00.1234567890123456782Z</instant>', False, False),
    ('<instant>2026-11-16T23:00:00.1234567890123456781000-01:00</instant>', True, False),
    ('<instant>2026-11-18T00:00:00.1234567890123456781Z</instant>', False, False),
    ('<instant>2026-11-17T00:00:00.1234567890123456781</instant>', False, False),
    ('<dayStart>2024-02-29T24:00:00.000Z</dayStart>', True, False),
    ('<dayStart>2024-02-29T23:00:00-01:00</dayStart>', True, False),
    ('<dayStart>2024-02-28T23:00:00-01:00</dayStart>', False, False),
    ('<eraStart>-0001-12-31T24:00:00Z</eraStart>', True, False),
    ('<eraStart>0000-12-31T24:00:00Z</eraStart>', False, False),
    ('<midnight>24:00:00</midnight>', True, False),
    ('<midnight>24:00:00.000</midnight>', True, False),
    ('<midnight>24:00:00.0000000000000000001</midnight>', False, False),
    ('<midnight>00:00:00Z</midnight>', False, False),
    ('<clock>23:00:00.12345678901234567810-01:00</clock>', True, False),
    ('<clock>00:00:00.1234567890123456782Z</clock>', False, False),
    ('<utc>24:00:00Z</utc>', True, False),
    ('<clock>14:00:00.1234567890123456781+14:00</clock>', True, False),
    ('<clock>14:01:00.1234567890123456781+14:01</clock>', False, False),
    ('<utc>24:00:00+00:00</utc>', False, False),
    ('<midnight>24:00:00</midnight><pattern>1.0</pattern>', False, False),
    ('<instant>2026-11-17T00:00:00.1234567890123456782Z</instant><decimal>2</decimal>', False, False),
]


CASES = [
    ('<decimal>1</decimal>', True), ('<decimal>1.0</decimal>', True),
    ('<decimal>01.000</decimal>', True), ('<decimal>2</decimal>', False),
    ('<integer>+01</integer>', True), ('<integer>2</integer>', False),
    ('<token> foo   bar </token>', True), ('<token>foo baz</token>', False),
    ('<token>foo&#x9;bar</token>', True),
    ('<qname xmlns:v="urn:kind">v:known</qname>', True),
    ('<qname xmlns:v="urn:other">v:known</qname>', False),
    ('<qname xmlns:v="urn:kind">known</qname>', False),
    ('<pattern>1</pattern>', True), ('<pattern>1.0</pattern>', False),
    ('<decimal xsi:type="Whole">1</decimal>', True),
    ('<decimal xsi:type="Whole">1.0</decimal>', False),
    ('<decimal>1.0</decimal><literal>1</literal>', False),
    ('<decimal/>', True), ('<decimal><![CDATA[1.0]]></decimal>', True),
    ('<decimal>1<!-- comment -->.0</decimal>', True),
    ('<content note="example">1.0</content>', True), ('<content>2</content>', False),
    ('<content xsi:type="NarrowContent">1</content>', True),
    ('<content xsi:type="NarrowContent">1.0</content>', False),
    ('<flag>1</flag>', True), ('<flag>false</flag><decimal>1.0</decimal>', False),
    ('<text><![CDATA[a</x]]></text>', True),
    ('<collapsed> foo   bar </collapsed>', True), ('<spaced>foo bar</spaced>', True),
    ('<replaced>foo&#x9;bar</replaced>', True), ('<replaced>foo   bar</replaced>', False),
    ('<literal xsi:type="xs:token"> true </literal>', True),
    ('<literal xsi:type="xs:string"> true </literal>', False),
    ('<literal xsi:type="Collapsed"> true </literal>', True),
    ('<spacedliteral xsi:type="xs:token">foo bar</spacedliteral>', False),
    ('<literal xsi:type="xs:token">   </literal>', False),
    ('<literal xsi:type="Collapsed">   </literal>', False),
] + [(body, expected) for body, expected, _ in CALENDAR_CASES]


def xml(body):
    return '<request xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xs="'+XS+'">'+body+'</request>'


def attribute_fixture(kind):
    clock = kind == 'required'
    base_type = 'time' if clock else 'dateTime'
    day = '' if clock else '2026-11-17T'
    previous_day = '' if clock else '2026-11-16T'
    fixed = day+'00:00:00.1234567890123456781Z'
    direct = f'<xs:attribute name="instant" type="Stamp" fixed="{fixed}"/>'
    if clock:
        direct = direct.replace(' type=', ' use="required" type=')
    reference = '<xs:attribute ref="instant"/>' if kind == 'reference' else direct
    definition = direct if kind == 'reference' else ''
    neighbor = (f'<xs:sequence><xs:element name="instant" type="Stamp" fixed="{fixed}"/></xs:sequence>'
                if kind == 'neighbor' else '')
    source = f'''<xs:schema xmlns:xs="{XS}">
    <xs:simpleType name="Stamp"><xs:restriction base="xs:{base_type}"/></xs:simpleType>
    {definition}
    <xs:element name="request"><xs:complexType>{neighbor}{reference}
      <xs:attribute name="literal" type="xs:string" fixed="{fixed}"/>
    </xs:complexType></xs:element>
    </xs:schema>'''
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    cases = []
    alias = previous_day+'23:00:00.1234567890123456781000-01:00'
    for value, valid in [(fixed, True), (alias, True), (day+'00:00:00.1234567890123456782Z', False)]:
        body = f'<instant>{fixed}</instant>' if neighbor else ''
        cases.append((f'<request instant="{value}">{body}</request>', valid))
        if neighbor:
            cases.append((f'<request instant="{fixed}"><instant>{value}</instant></request>', valid))
    if kind == 'direct':
        cases.append((f'<request instant="{alias}" literal="{alias}"/>', False))
    if clock:
        cases.append(('<request/>', False))
    return row, {'main.xsd': source.encode()}, cases


def pack(row, files):
    from xsd_bundle import _prepare
    output, _, constraints = _prepare(row, files)
    return dict(row, format=1, entrypoint='message.xsd', fields=[], field_namespaces=[],
                files={name:base64.b64encode(raw).decode() for name, raw in output.items()
                       if not name.startswith('originals/')}, **constraints)


def chameleon_attribute_fixture():
    fixed = '2026-11-17T00:00:00.1234567890123456781Z'
    alias = '2026-11-16T23:00:00.1234567890123456781000-01:00'
    source = {
        'main.xsd': f'''<xs:schema xmlns:xs="{XS}" xmlns:c="urn:calendar" xmlns:s="urn:literal">
          <xs:import namespace="urn:calendar" schemaLocation="calendar.xsd"/>
          <xs:import namespace="urn:literal" schemaLocation="literal.xsd"/>
          <xs:element name="request"><xs:complexType><xs:sequence>
            <xs:element ref="c:calendar"/><xs:element ref="s:literal"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>''',
        'shared.xsd': f'''<xs:schema xmlns:xs="{XS}">
          <xs:complexType name="Shared"><xs:attribute name="instant" type="Stamp" fixed="{fixed}"/></xs:complexType>
        </xs:schema>''',
    }
    for name, primitive in (('calendar', 'dateTime'), ('literal', 'string')):
        uri = 'urn:'+name
        source[name+'.xsd'] = f'''<xs:schema xmlns:xs="{XS}" xmlns="{uri}" targetNamespace="{uri}">
          <xs:include schemaLocation="shared.xsd"/>
          <xs:simpleType name="Stamp"><xs:restriction base="xs:{primitive}"/></xs:simpleType>
          <xs:element name="{name}" type="Shared"/>
        </xs:schema>'''
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    cases = [(f'<request xmlns:c="urn:calendar" xmlns:s="urn:literal">'
              f'<c:calendar instant="{calendar}"/><s:literal instant="{literal}"/></request>', valid)
             for calendar, literal, valid in ((fixed, fixed, True), (alias, fixed, True), (fixed, alias, False),
                                              (alias, alias, False), (fixed.replace('6781Z', '6782Z'), fixed, False))]
    return row, {name: content.encode() for name, content in source.items()}, cases


def notation_packs():
    from tests.comparison_xsd_fixture import notation_examples, notation_schema
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='r', namespace=None, schema_path='main.xsd')
    for union in (False, True):
        for prefix, uri in (('p', 'urn:A'), ('p', 'urn:B'), ('q', 'urn:A')):
            sources = notation_schema(prefix, uri, union)
            yield dict(pack=pack(row, {name: content.encode() for name, content in sources.items()}),
                       cases=notation_examples(uri))


if __name__ == '__main__':
    attributes = [(kind, attribute_fixture(kind)) for kind in ('direct', 'reference', 'neighbor', 'required')]
    attributes.append(('chameleon', chameleon_attribute_fixture()))
    payload = dict(pack=pack(*fixture()), cases=[(xml(body), expected) for body, expected in CASES],
                   attributes={kind: dict(pack=pack(row, files), cases=cases)
                               for kind, (row, files, cases) in attributes},
                   notation=list(notation_packs()))
    print(json.dumps(payload))
