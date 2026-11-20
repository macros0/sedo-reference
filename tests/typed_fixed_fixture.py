"""Shared typed fixed regression corpus for native and actual WASM validators."""
import base64
import json
import sys

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
    ('<spacedliteral xsi:type="xs:token">foo bar</spacedliteral>', True),
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


def compound_attribute_fixture(kind, subject='attribute'):
    fixed = '2026-11-17T00:00:00.1234567890123456781Z'
    alias = '2026-11-16T23:00:00.1234567890123456781000-01:00'
    other = fixed.replace('6781Z', '6782Z')
    calendar_union = '<xs:union memberTypes="xs:dateTime xs:integer"/>'
    pattern = fixed.replace('.', '[.]') + ' [78]|' + alias.replace('.', '[.]') + ' 007'
    declarations = {
        'union': calendar_union,
        'string-first': '<xs:union memberTypes="xs:string xs:dateTime"/>',
        'restricted-member': '<xs:union memberTypes="OffsetText xs:dateTime"/>',
        'list': '<xs:list itemType="xs:dateTime"/>',
        'mixed-list': '<xs:list itemType="Mixed"/>',
        'mixed-qname-list': '<xs:list itemType="MixedQName"/>',
        'patterned-list': f'<xs:restriction base="MixedList"><xs:pattern value="{pattern}"/></xs:restriction>',
        'patterned-union-list': f'<xs:restriction base="ListOrInt"><xs:pattern value="{pattern}"/></xs:restriction>',
        'calendar-facet': '<xs:union memberTypes="Narrow xs:string"/>',
        'limited-literal': '<xs:union memberTypes="Narrow xs:string"/>',
        'nested-namespaces': calendar_union,
    }
    options = {
        'union': [(fixed, True), (alias, True), (other, False), ('42', False)],
        'string-first': [(fixed, True), (alias, False), (other, False)],
        'restricted-member': [(fixed, True), (alias, False),
                              ('2026-11-17T01:00:00.1234567890123456781+01:00', True)],
        'list': [(fixed+' '+fixed, True), (alias+'   '+alias, True), (alias+' '+other, False), (fixed, False)],
        'mixed-list': [(fixed+' 7', True), (alias+' 007', True), (alias+' 8', False), (other+' 7', False)],
        'mixed-qname-list': [(fixed+' k:name', True), (alias+' p:name', True),
                             (alias+' q:name', False), (alias+' p:other', False)],
        'patterned-list': [(fixed+' 7', True), (alias+' 007', True), (fixed+' 007', False), (fixed+' 8', False)],
        'patterned-union-list': [(fixed+' 7', True), (alias+' 007', True), (fixed+' 007', False), (fixed+' 8', False)],
        'calendar-facet': [(other, True), (other.replace('6782Z', '6782000Z'), False)],
        'limited-literal': [('constructor:x', True), ('constructor:y', False)],
        'nested-namespaces': [(fixed, True), (alias, True), (other, False)],
    }
    constraint = {'list': fixed+' '+fixed, 'mixed-list': fixed+' 7', 'patterned-list': fixed+' 7',
                  'mixed-qname-list': fixed+' k:name',
                  'patterned-union-list': fixed+' 7', 'calendar-facet': other,
                  'limited-literal': 'constructor:x'}.get(kind, fixed)
    definition = ('<xs:simpleType name="OffsetText"><xs:restriction base="xs:string">'
                  '<xs:pattern value=".*-01:00"/></xs:restriction></xs:simpleType>'
                  '<xs:simpleType name="Mixed">'+calendar_union+'</xs:simpleType>'
                  '<xs:simpleType name="MixedQName"><xs:union memberTypes="xs:dateTime xs:QName"/></xs:simpleType>'
                  '<xs:simpleType name="MixedList"><xs:list itemType="Mixed"/></xs:simpleType>'
                  '<xs:simpleType name="ListOrInt"><xs:union memberTypes="MixedList xs:int"/></xs:simpleType>')
    if kind in ('calendar-facet', 'limited-literal'):
        definition += ('<xs:simpleType name="Narrow"><xs:restriction base="xs:dateTime">'
                       f'<xs:enumeration value="{fixed}"/></xs:restriction></xs:simpleType>')
    attributes = (f'<xs:attribute name="instant" type="Stamp" fixed="{constraint}"/>'
                  f'<xs:attribute name="literal" type="xs:string" fixed="{fixed}"/>')
    if subject == 'simple-content':
        definition += ('<xs:complexType name="StampContent"><xs:simpleContent><xs:extension base="Stamp">'
                       '<xs:attribute name="note" type="xs:int"/></xs:extension></xs:simpleContent></xs:complexType>')
    if subject != 'attribute':
        element_type = 'StampContent' if subject == 'simple-content' else 'Stamp'
        request = f'<xs:element name="request" type="{element_type}" fixed="{constraint}"/>'
    elif kind == 'nested-namespaces':
        content = ('<xs:sequence><xs:element name="child"><xs:complexType>'+attributes
                   +'</xs:complexType></xs:element></xs:sequence>')
        request = f'<xs:element name="request"><xs:complexType>{content}</xs:complexType></xs:element>'
    else:
        request = f'<xs:element name="request"><xs:complexType>{attributes}</xs:complexType></xs:element>'
    source = f'''<xs:schema xmlns:xs="{XS}" xmlns:〇="urn:valid-prefix" xmlns:k="urn:key">
      {definition}<xs:simpleType name="Stamp">{declarations[kind]}</xs:simpleType>
      {request}
    </xs:schema>'''
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    if subject != 'attribute':
        note = ' note="7"' if subject == 'simple-content' else ''
        cases = [(f'<request{note}>{value}</request>', valid) for value, valid in options[kind]]
        cases.append((f'<request{note}/>', True))
        if subject == 'simple-content':
            cases.append((f'<request note="bad">{constraint}</request>', False))
    elif kind == 'nested-namespaces':
        cases = [(f'<request xmlns:sedoprobe="urn:parent" xmlns:sedoprobex="urn:parent-2">'
                  f'<child instant="{value}"/></request>', valid) for value, valid in options[kind]]
    elif kind == 'mixed-qname-list':
        cases = [(f'<request xmlns:k="urn:key" xmlns:p="urn:key" xmlns:q="urn:other" instant="{value}"/>', valid)
                 for value, valid in options[kind]]
    else:
        cases = [(f'<request instant="{value}"/>', valid) for value, valid in options[kind]]
    if kind == 'union' and subject == 'attribute':
        cases.append((f'<request instant="{alias}" literal="{alias}"/>', False))
    return row, {'main.xsd': source.encode()}, cases


COMPOUND_CASES = [(kind, 'attribute')
                  for kind in ('union', 'string-first', 'restricted-member', 'list', 'mixed-list', 'mixed-qname-list', 'patterned-list',
                               'patterned-union-list', 'calendar-facet', 'limited-literal', 'nested-namespaces')]
COMPOUND_CASES += [(kind, subject) for subject in ('element', 'simple-content')
                   for kind in ('union', 'list', 'patterned-union-list')]


def cross_branch_lists_fixture():
    stamp = '2026-11-17T00:00:00.1234567890123456781Z'
    alias = '2026-11-16T23:00:00.1234567890123456781000-01:00'
    other = stamp.replace('6781Z', '6782Z')
    source = f'''<xs:schema xmlns:xs="{XS}" xmlns:k="urn:key">
      <xs:simpleType name="FirstItem"><xs:union memberTypes="xs:dateTime xs:int xs:QName"/></xs:simpleType>
      <xs:simpleType name="Digits"><xs:restriction base="xs:int"><xs:pattern value="[0-9]+"/></xs:restriction></xs:simpleType>
      <xs:simpleType name="OtherItem"><xs:union memberTypes="xs:dateTime xs:QName Digits"/></xs:simpleType>
      <xs:simpleType name="TextItem"><xs:union memberTypes="xs:dateTime xs:QName xs:string"/></xs:simpleType>
      <xs:simpleType name="FirstList"><xs:list itemType="FirstItem"/></xs:simpleType>
      <xs:simpleType name="First"><xs:restriction base="FirstList"><xs:pattern value=".*Z 7 k:known"/></xs:restriction></xs:simpleType>
      <xs:simpleType name="OtherList"><xs:list itemType="OtherItem"/></xs:simpleType>
      <xs:simpleType name="TextList"><xs:list itemType="TextItem"/></xs:simpleType>
      <xs:simpleType name="Compatible"><xs:union memberTypes="First OtherList"/></xs:simpleType>
      <xs:simpleType name="Incompatible"><xs:union memberTypes="First TextList"/></xs:simpleType>
      <xs:element name="request"><xs:complexType>
        <xs:attribute name="same" type="Compatible" fixed="{stamp} 7 k:known"/>
        <xs:attribute name="distinct" type="Incompatible" fixed="{stamp} 7 k:known"/>
      </xs:complexType></xs:element>
    </xs:schema>'''
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    # Expected validator and original libxml2 verdicts. Native comparison
    # rounds fractional seconds and rejects the offset alias on this list.
    cases = [
        ('same', stamp+' 7 k:known', 'urn:key', True, True),
        ('same', stamp+' 007 k:known', 'urn:key', True, True),
        ('same', stamp+' 007 p:known', 'urn:key', True, True),
        ('same', alias+' 007 p:known', 'urn:key', True, False),
        ('same', stamp+' 008 p:known', 'urn:key', False, False),
        ('same', other+' 007 p:known', 'urn:key', False, True),
        ('same', stamp+' 007 p:other', 'urn:key', False, False),
        ('same', stamp+' 007 k:known', 'urn:other', False, False),
        ('distinct', stamp+' 007 p:known', 'urn:key', False, False),
        ('distinct', stamp+' 7 p:known', 'urn:key', False, False),
    ]
    return row, {'main.xsd': source.encode()}, [
        (f'<request xmlns:k="{namespace}" xmlns:p="urn:key" {attr}="{value}"/>', valid, native)
        for attr, value, namespace, valid, native in cases
    ]


def duration_fixture():
    stamp = '2026-11-17T00:00:00.1234567890123456781Z'
    precise = 'PT0.1234567890123456781S'
    source = f'''<xs:schema xmlns:xs="{XS}">
      <xs:simpleType name="Item"><xs:union memberTypes="xs:dateTime xs:duration"/></xs:simpleType>
      <xs:simpleType name="Items"><xs:list itemType="Item"/></xs:simpleType>
      <xs:simpleType name="Patterned"><xs:restriction base="Items">
        <xs:pattern value="{stamp.replace('.', '[.]')} {precise.replace('.', '[.]')}"/>
      </xs:restriction></xs:simpleType>
      <xs:simpleType name="Values"><xs:union memberTypes="Patterned Items"/></xs:simpleType>
      <xs:simpleType name="OneDay"><xs:restriction base="xs:duration">
        <xs:enumeration value="P1D"/>
      </xs:restriction></xs:simpleType>
      <xs:simpleType name="DayOrText"><xs:union memberTypes="OneDay xs:string"/></xs:simpleType>
      <xs:simpleType name="BoundedStamp"><xs:restriction base="xs:dateTime">
        <xs:minInclusive value="{stamp}"/>
      </xs:restriction></xs:simpleType>
      <xs:simpleType name="DateDayText"><xs:union memberTypes="BoundedStamp OneDay xs:string"/></xs:simpleType>
      <xs:simpleType name="DayList"><xs:list itemType="DayOrText"/></xs:simpleType>
      <xs:element name="request"><xs:complexType>
        <xs:attribute name="precise" type="Values" fixed="{stamp} {precise}"/>
        <xs:attribute name="day" type="xs:duration" fixed="P1D"/>
        <xs:attribute name="month" type="xs:duration" fixed="P1M"/>
        <xs:attribute name="negative" type="xs:duration" fixed="-P1DT0.75S"/>
        <xs:attribute name="zero" type="xs:duration" fixed="-PT0.00S"/>
        <xs:attribute name="selectedDay" type="DayOrText" fixed="P1D"/>
        <xs:attribute name="dateDayText" type="DateDayText" fixed="P1D"/>
        <xs:attribute name="dayList" type="DayList" fixed="P1D note"/>
      </xs:complexType></xs:element>
    </xs:schema>'''
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    cases = [('precise', stamp+' '+precise, True),
             ('precise', stamp+' PT0.1234567890123456781000S', True),
             ('precise', stamp+' PT0.1234567890123456782S', False),
             ('day', 'PT24H', True), ('month', 'P30D', False),
             ('negative', '-PT86400.75000S', True), ('negative', 'PT86400.75S', False),
             ('zero', 'P0Y', True), ('day', 'P1DT', False),
             ('day', 'P' + '0' * 4299 + '1D', True),
             ('day', 'P' + '0' * 4300 + '1D', True),
             ('day', 'PT' + '0' * 4300 + '24H', True),
             ('day', 'P' + '0' * 4300 + '2D', False),
             ('selectedDay', 'PT24H', True), ('selectedDay', 'P1DT0.0S', True),
             ('selectedDay', 'P1DT1S', False),
             ('selectedDay', 'P1DT0.0000000000000000001S', False),
             ('dateDayText', 'PT24H', True), ('dateDayText', stamp, False),
             ('dayList', 'PT24H note', True), ('dayList', 'PT25H note', False)]
    return row, {'main.xsd': source.encode()}, [(f'<request {attr}="{value}"/>', valid) for attr, value, valid in cases]


def compound_depth_fixture(depth):
    types = '<xs:simpleType name="t0"><xs:union memberTypes="xs:dateTime xs:int"/></xs:simpleType>'
    types += ''.join(f'<xs:simpleType name="t{i}"><xs:union memberTypes="t{i-1}"/></xs:simpleType>'
                     for i in range(1, depth))
    stamp = '2026-11-17T00:00:00.1234567890123456781Z'
    source = f'''<xs:schema xmlns:xs="{XS}">{types}
        <xs:element name="request"><xs:complexType>
          <xs:attribute name="instant" type="t{depth-1}" fixed="{stamp}"/>
        </xs:complexType></xs:element>
    </xs:schema>'''
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    return row, {'main.xsd': source.encode()}, f'<request instant="{stamp}"/>'


def wildcard_attribute_fixture(explicit, *, implicit_only=False):
    fixed = '2026-11-17T00:00:00.1234567890123456781Z'
    alias = '2026-11-16T23:00:00.1234567890123456781000-01:00'
    other = fixed.replace('6781Z', '6782Z')
    direct = ('<xs:element name="direct"><xs:complexType><xs:attribute ref="m:instant"/>'
              '</xs:complexType></xs:element>') if explicit else ''
    source = f'''<xs:schema xmlns:xs="{XS}" xmlns:m="urn:wildcard" targetNamespace="urn:wildcard"
                          elementFormDefault="qualified">
      <xs:simpleType name="Stamp"><xs:union memberTypes="xs:dateTime xs:integer"/></xs:simpleType>
      <xs:attribute name="instant" type="m:Stamp" fixed="{fixed}"/>
      <xs:element name="bare"/>
      <xs:attributeGroup name="Strict"><xs:anyAttribute namespace="##targetNamespace"/></xs:attributeGroup>
      <xs:complexType name="StrictType"><xs:attributeGroup ref="m:Strict"/></xs:complexType>
      <xs:complexType name="SkipType"><xs:anyAttribute namespace="##targetNamespace" processContents="skip"/></xs:complexType>
      <xs:complexType name="OverrideStrict"><xs:complexContent><xs:extension base="m:SkipType">
        <xs:attributeGroup ref="m:Strict"/>
      </xs:extension></xs:complexContent></xs:complexType>
      <xs:complexType name="OverrideSkip"><xs:complexContent><xs:extension base="m:StrictType">
        <xs:anyAttribute namespace="##targetNamespace" processContents="skip"/>
      </xs:extension></xs:complexContent></xs:complexType>
      <xs:element name="request"><xs:complexType><xs:choice>{direct}
        <xs:element name="strict"><xs:complexType>
          <xs:anyAttribute namespace="##targetNamespace" processContents="strict"/>
        </xs:complexType></xs:element>
        <xs:element name="lax"><xs:complexType>
          <xs:anyAttribute namespace="##targetNamespace" processContents="lax"/>
        </xs:complexType></xs:element>
        <xs:element name="skip"><xs:complexType>
          <xs:anyAttribute namespace="##targetNamespace" processContents="skip"/>
        </xs:complexType></xs:element>
        <xs:element name="implicit"/>
        <xs:element name="overrideStrict" type="m:SkipType"/>
        <xs:element name="overrideSkip" type="m:StrictType"/>
        <xs:element name="open"><xs:complexType><xs:sequence>
          <xs:any namespace="##other" processContents="lax"/>
        </xs:sequence></xs:complexType></xs:element>
        <xs:element name="local"><xs:complexType>
          <xs:attribute name="instant" type="xs:string" form="qualified" fixed="literal"/>
          <xs:anyAttribute namespace="##targetNamespace" processContents="strict"/>
        </xs:complexType></xs:element>
      </xs:choice></xs:complexType></xs:element>
    </xs:schema>'''
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace='urn:wildcard', schema_path='main.xsd')
    if implicit_only:
        row['root']='bare'
        cases = [(f'<m:bare xmlns:m="urn:wildcard" m:instant="{value}"/>', valid)
                 for value, valid in ((fixed, True), (alias, True), (other, False), ('42', False))]
        return row, {'main.xsd': source.encode()}, cases
    cases = []
    for element in ('strict', 'lax', 'skip', 'implicit', *(['direct'] if explicit else [])):
        for value, valid in ((fixed, True), (alias, True), (other, False), ('42', False)):
            cases.append((f'<m:request xmlns:m="urn:wildcard"><m:{element} m:instant="{value}"/></m:request>',
                          valid or element == 'skip'))
    for element, valid in (('strict', False), ('lax', True), ('skip', True)):
        cases.append((f'<m:request xmlns:m="urn:wildcard"><m:{element} m:unknown="42"/></m:request>', valid))
    cases.append(('<m:request xmlns:m="urn:wildcard"><m:local m:instant="literal"/></m:request>', True))
    for value, valid in ((alias, True), ('42', False)):
        for element, declaration in (('overrideStrict', 'OverrideStrict'), ('overrideSkip', 'OverrideSkip')):
            cases.append((f'<m:request xmlns:m="urn:wildcard" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                          f'<m:{element} xsi:type="m:{declaration}" m:instant="{value}"/></m:request>',
                          valid or element == 'overrideSkip'))
        cases.append((f'<m:request xmlns:m="urn:wildcard" xmlns:u="urn:unknown">'
                      f'<m:open><u:unknown m:instant="{value}"/></m:open></m:request>', valid))
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


def calendar_facets_fixture():
    time = '2026-11-17T00:00:00.125Z'
    offset = '2026-11-16T23:00:00.12500-01:00'
    near = '2026-11-17T00:00:00.1250000000000000002Z'
    near_offset = '2026-11-16T23:00:00.125000000000000000200-01:00'
    duration = 'PT0.1250000000000000002S'
    prefix = f'<xs:schema xmlns:xs="{XS}" xmlns:k="urn:item">'
    types = {
        'Bound': '<xs:restriction base="xs:duration"><xs:minInclusive value="P1D"/></xs:restriction>',
        'EnumDate': f'<xs:restriction base="xs:dateTime"><xs:enumeration value="{time}"/></xs:restriction>',
        'BoundDate': f'<xs:restriction base="xs:dateTime"><xs:minInclusive value="{time}"/></xs:restriction>',
        'A': '<xs:union memberTypes="Bound xs:string"/>',
        'B': '<xs:union memberTypes="EnumDate xs:string"/>',
        'C': '<xs:union memberTypes="BoundDate xs:string"/>',
        'Exclusive': '<xs:restriction base="xs:dateTime"><xs:minExclusive value="2026-11-17T00:00:00.1250000000000000001Z"/></xs:restriction>',
        'Above': '<xs:union memberTypes="Exclusive xs:string"/>',
        'DurationExclusive': '<xs:restriction base="xs:duration"><xs:minExclusive value="PT0.1250000000000000001S"/></xs:restriction>',
        'DurationAbove': '<xs:union memberTypes="DurationExclusive xs:string"/>',
        'TextFirst': '<xs:union memberTypes="xs:string Bound"/>',
        'Item': '<xs:union memberTypes="xs:duration xs:QName"/>',
        'Items': '<xs:list itemType="Item"/>',
        'List': '<xs:restriction base="Items"><xs:enumeration value="P1D P2D k:a"/>'
                '<xs:enumeration value="P3D P4D k:b"/></xs:restriction>',
    }
    fields = [('day', 'A', 'P1D'), ('date', 'B', time), ('boundary', 'C', time),
              ('list', 'List', 'P1D P2D k:a'), ('string', 'TextFirst', 'P1D'),
              ('above', 'Above', near), ('durationAbove', 'DurationAbove', duration)]
    declarations = ''.join(f'<xs:simpleType name="{name}">{body}</xs:simpleType>'
                           for name, body in types.items())
    attributes = ''.join(f'<xs:attribute name="{name}" type="{typ}" fixed="{fixed}"/>'
                         for name, typ, fixed in fields)
    source = f'{prefix}{declarations}<xs:element name="request"><xs:complexType>{attributes}</xs:complexType></xs:element></xs:schema>'
    cases = [('day', 'PT24H', True), ('day', 'P1DT0.000000000000000001S', False),
             ('day', 'PT23H', False), ('date', offset, True), ('date', time, True),
             ('date', time.replace('125Z', '126Z'), False), ('boundary', offset, True),
             ('list', 'PT24H PT48H q:a', True), ('list', 'PT48H PT24H q:a', False),
             ('list', 'PT24H q:a', False), ('list', 'PT24H PT48H q:b', False),
             ('list', 'PT72H PT96H q:b', False), ('string', 'PT24H', False),
             ('above', near_offset, True), ('above', near.replace('2Z', '1Z'), False),
             ('durationAbove', duration.replace('2S', '200S'), True),
             ('durationAbove', duration.replace('2S', '1S'), False)]
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    return row, {'main.xsd': source.encode()}, [
        (f'<request xmlns:q="urn:item" {field}="{value}"/>', valid) for field, value, valid in cases
    ]


def local_simple_content_fixture():
    source = f'''<xs:schema xmlns:xs="{XS}" xmlns:k="urn:item" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <xs:complexType name="Base"><xs:simpleContent><xs:extension base="xs:string"/>
      </xs:simpleContent></xs:complexType>
      <xs:complexType name="Typed"><xs:simpleContent><xs:restriction base="Base">
        <xs:simpleType><xs:restriction base="xs:string"><xs:whiteSpace value="collapse"/>
        </xs:restriction></xs:simpleType>
        <xs:minLength value="2"/>
      </xs:restriction></xs:simpleContent></xs:complexType>
      <xs:complexType name="Outer"><xs:simpleContent><xs:restriction base="Base">
        <xs:simpleType><xs:restriction base="xs:string"><xs:whiteSpace value="replace"/>
        </xs:restriction></xs:simpleType>
        <xs:whiteSpace value="collapse"/>
      </xs:restriction></xs:simpleContent></xs:complexType>
      <xs:complexType name="Replace"><xs:simpleContent><xs:restriction base="Base">
        <xs:whiteSpace value="replace"/>
      </xs:restriction></xs:simpleContent></xs:complexType>
      <xs:complexType name="QNameBase"><xs:simpleContent><xs:extension base="xs:QName"/>
      </xs:simpleContent></xs:complexType>
      <xs:complexType name="Name"><xs:simpleContent><xs:restriction base="QNameBase">
        <xs:simpleType><xs:restriction base="xs:QName"/></xs:simpleType>
      </xs:restriction></xs:simpleContent></xs:complexType>
      <xs:element name="request"><xs:complexType><xs:sequence>
        <xs:element name="text" type="Typed" fixed="go" minOccurs="0"/>
        <xs:element name="name" type="Name" fixed="k:one" minOccurs="0"/>
        <xs:element name="plain" type="Base" fixed="go" minOccurs="0"/>
        <xs:element name="spaced" type="Base" fixed=" go " minOccurs="0"/>
        <xs:element name="replaced" type="Base" fixed="a   b" minOccurs="0"/>
      </xs:sequence></xs:complexType></xs:element>
    </xs:schema>'''
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    cases = [('<text> go </text>', True), ('<text>g</text>', False), ('<text>no</text>', False),
             ('<name>p:one</name>', True), ('<name>p:two</name>', False),
             ('<plain>go</plain>', True), ('<plain> go </plain>', False),
             ('<plain xsi:type="Typed"> go </plain>', True),
             ('<plain xsi:type="Typed"> no </plain>', False),
             ('<spaced> go </spaced>', True),
             ('<spaced xsi:type="Typed"> go </spaced>', True),
             ('<plain xsi:type="Outer">&#x9; go </plain>', True),
             ('<spaced xsi:type="Outer">&#x9; go </spaced>', True),
             ('<replaced>a&#x9;  b</replaced>', False),
             ('<replaced xsi:type="Replace">a&#x9;  b</replaced>', True),
             ('<replaced xsi:type="Replace">a&#x9; b</replaced>', False)]
    return row, {'main.xsd': source.encode()}, [
        (f'<request xmlns:p="urn:item" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{body}</request>', valid)
        for body, valid in cases
    ]


def direct_calendar_bounds_fixture():
    forms = {
        'duration': ('PT0.{}S', 'PT00.{}0S', 'PT000.{}S', 'PT0[0]?[.].*S'),
        'dateTime': ('2026-11-17T00:00:00.{}Z', '2026-11-16T23:00:00.{}0-01:00',
                     '2026-11-17T01:00:00.{}+01:00', '2026-11-(16T23|17T00):.*'),
    }
    declarations, attributes, cases = [], [], []
    for kind, (lexical, alias, wrong_pattern, pattern) in forms.items():
        for prefix in ('875000000000000', '8750000000000000', '12500000000000000'):
            bound, fixed, next_value = [lexical.format(prefix+digit) for digit in '123']
            name = kind+str(len(prefix)+1)
            declarations.append(f'<xs:simpleType name="{name}"><xs:restriction base="xs:{kind}">'
                                f'<xs:minExclusive value="{bound}"/><xs:pattern value="{pattern}"/>'
                                '</xs:restriction></xs:simpleType>')
            attributes.extend((f'<xs:attribute name="{name}" type="{name}" fixed="{fixed}"/>',
                               f'<xs:attribute name="{name}Other" type="{name}"/>'))
            cases.extend((f'<request {attribute}="{value}"/>', valid) for attribute, value, valid in (
                (name, fixed, True), (name, alias.format(prefix+'2'), True),
                (name, bound, False), (name, lexical.format(prefix+'0'), False), (name, next_value, False),
                (name, wrong_pattern.format(prefix+'2'), False),
                *((name+'Other', lexical.format(prefix+digit), int(prefix+digit) > int(prefix+'1'))
                  for digit in '0123'),
                (name+'Other', alias.format(prefix+'2'), True),
                (name+'Other', wrong_pattern.format(prefix+'2'), False),
            ))
    source = (f'<xs:schema xmlns:xs="{XS}">{"".join(declarations)}'
              f'<xs:element name="request"><xs:complexType>{"".join(attributes)}</xs:complexType></xs:element>'
              '</xs:schema>')
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    return row, {'main.xsd': source.encode()}, [('<request/>', True), *cases]


def union_instance_type_fixture():
    source = f'''<xs:schema xmlns:xs="{XS}" xmlns:k="urn:item">
      <xs:simpleType name="WordUnion"><xs:union memberTypes="xs:string xs:boolean"/></xs:simpleType>
      <xs:simpleType name="NameUnion"><xs:union memberTypes="xs:QName xs:string"/></xs:simpleType>
      <xs:simpleType name="FlagUnion"><xs:union memberTypes="xs:boolean xs:string"/></xs:simpleType>
      <xs:simpleType name="Normalized"><xs:union memberTypes="xs:normalizedString xs:boolean"/></xs:simpleType>
      <xs:simpleType name="Token"><xs:union memberTypes="xs:token xs:boolean"/></xs:simpleType>
      <xs:simpleType name="Code"><xs:restriction base="xs:token"><xs:pattern value="go"/></xs:restriction></xs:simpleType>
      <xs:simpleType name="CodeUnion"><xs:union memberTypes="Code xs:string"/></xs:simpleType>
      <xs:complexType name="Base"><xs:simpleContent><xs:extension base="xs:anySimpleType"/></xs:simpleContent></xs:complexType>
      <xs:complexType name="WordContent"><xs:simpleContent><xs:restriction base="Base"><xs:simpleType>
        <xs:restriction base="WordUnion"/></xs:simpleType></xs:restriction></xs:simpleContent></xs:complexType>
      <xs:element name="request"><xs:complexType><xs:sequence>
        <xs:element name="word" type="Base" fixed=" go " minOccurs="0"/>
        <xs:element name="wrong" type="Base" fixed="go" minOccurs="0"/>
        <xs:element name="plain" type="xs:anySimpleType" fixed=" go " minOccurs="0"/>
        <xs:element name="tight" type="xs:anySimpleType" fixed="go" minOccurs="0"/>
        <xs:element name="spaced" type="xs:anySimpleType" fixed=" a   b " minOccurs="0"/>
        <xs:element name="flag" type="xs:anySimpleType" fixed="true" minOccurs="0"/>
        <xs:element name="name" type="xs:anySimpleType" fixed="k:one" minOccurs="0"/>
      </xs:sequence></xs:complexType></xs:element>
    </xs:schema>'''
    cases = [('word', 'WordContent', ' go ', True), ('wrong', 'WordContent', ' go ', False),
             ('plain', 'WordUnion', ' go ', True), ('tight', 'WordUnion', ' go ', False),
             ('spaced', 'Normalized', ' a&#x9;  b ', True), ('spaced', 'Normalized', 'a b', False),
             ('spaced', 'Token', 'a b', True), ('spaced', 'Token', 'a c', False),
             ('flag', 'FlagUnion', ' 1 ', True), ('flag', 'FlagUnion', ' false ', False),
             ('name', 'NameUnion', 'p:one', True), ('name', 'NameUnion', 'k:one', False),
             ('tight', 'CodeUnion', ' go ', True), ('tight', 'CodeUnion', ' no ', False)]
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    return row, {'main.xsd': source.encode()}, [
        (f'<request xmlns:p="urn:item" xmlns:k="urn:other" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
         f'<{element} xsi:type="{kind}">{value}</{element}></request>', valid)
        for element, kind, value, valid in cases
    ]


def calendar_without_fixed_fixture(kind, context='direct', *, inclusive_max=False, default=False):
    value = 'PT0.{}S' if kind == 'duration' else '2026-11-20T00:00:00.{}Z'
    digits = '8750000000000000'
    bound = value.format(digits+'1')
    facet = 'maxInclusive' if inclusive_max else 'minExclusive'
    attribute = '<xs:attribute name="peer" type="Bound"/>'
    if default:
        attribute = attribute.replace('/>', f' default="{value.format(digits+"2")}"/>')
    owner = '<xs:element name="request"><xs:complexType>{}</xs:complexType></xs:element>'
    body = {
        'direct': owner.format(attribute),
        'inline': owner.format(attribute.replace(' type="Bound"', '').replace('/>', '><xs:simpleType>'
            f'<xs:restriction base="xs:{kind}"><xs:{facet} value="{bound}"/></xs:restriction>'
            '</xs:simpleType></xs:attribute>')),
        'ref': attribute + owner.format('<xs:attribute ref="peer"/>'),
        'group': f'<xs:attributeGroup name="Fields">{attribute}</xs:attributeGroup>' +
            owner.format('<xs:attributeGroup ref="Fields"/>'),
        'named-owner': f'<xs:complexType name="Owner">{attribute}</xs:complexType>'
            '<xs:element name="request" type="Owner"/>',
        'element': '<xs:element name="request" type="Bound"/>',
        'identity-key': ('<xs:element name="request"><xs:complexType><xs:attribute name="peer" type="Bound"/>'
                         '</xs:complexType><xs:key name="selected"><xs:selector xpath="."/>'
                         '<xs:field xpath="@peer"/></xs:key></xs:element>'),
        'derived-substitution': (
            '<xs:complexType name="Base"/>'
            '<xs:complexType name="Derived"><xs:complexContent><xs:extension base="Base">'
            '<xs:sequence><xs:element ref="head"/></xs:sequence>'
            '</xs:extension></xs:complexContent></xs:complexType>'
            '<xs:complexType name="ChildBase"/><xs:element name="head" type="ChildBase" abstract="true"/>'
            '<xs:element name="member" substitutionGroup="head"><xs:complexType><xs:complexContent>'
            f'<xs:extension base="ChildBase">{attribute}</xs:extension>'
            '</xs:complexContent></xs:complexType></xs:element>'
            '<xs:element name="request" type="Base"/>'),
        'identity-union': '<xs:simpleType name="IdentityChoice"><xs:union memberTypes="Bound xs:ID"/></xs:simpleType>'
            + owner.format('<xs:sequence><xs:element name="item"><xs:complexType>'
                           '<xs:attribute name="id" type="xs:ID"/></xs:complexType></xs:element></xs:sequence>'
                           + attribute.replace('"Bound"', '"IdentityChoice"')),
    }[context]
    raw = (f'<xs:schema xmlns:xs="{XS}"><xs:simpleType name="Bound"><xs:restriction base="xs:{kind}">'
           f'<xs:{facet} value="{bound}"/></xs:restriction></xs:simpleType>{body}</xs:schema>')
    cases = [('<request/>', True)] + [
        (f'<request peer="{value.format(digits+digit)}"/>',
         int(digits+digit) <= int(digits+'1') if inclusive_max else int(digits+digit) > int(digits+'1'))
        for digit in '0123'
    ]
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')
    return row, {'main.xsd': raw.encode()}, cases


def effective_value_and_projection_fixtures():
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')

    def files(body, attributes=''):
        return {'main.xsd': f'<xs:schema xmlns:xs="{XS}" {attributes}>{body}</xs:schema>'.encode()}

    def instance(kind, value, bindings=''):
        return (f'<request xmlns:xs="{XS}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                f'{bindings} xsi:type="{kind}">{value}</request>')

    scalar = [
        ('string', ' a  b ', '', [('xs:token', 'a b', '', True), ('xs:token', ' a  b ', '', True),
                                 ('xs:token', 'a c', '', False), ('xs:string', 'a b', '', False)]),
        ('anySimpleType', 'k:one', 'xmlns:k="urn:expected"',
         [('xs:QName', 'p:one', 'xmlns:p="urn:expected"', True),
          ('xs:QName', 'k:one', 'xmlns:k="urn:other"', False)]),
        ('anySimpleType', '1', '', [('xs:int', '+01', '', True), ('xs:int', '2', '', False)]),
        ('anySimpleType', 'true', '', [('xs:boolean', '1', '', True), ('xs:boolean', '0', '', False)]),
        ('anySimpleType', 'PT60S', '', [('xs:duration', 'PT1M', '', True), ('xs:duration', 'PT61S', '', False)]),
    ]
    for base, fixed, namespaces, cases in scalar:
        source = files(f'<xs:element name="request" type="xs:{base}" fixed="{fixed}"/>', namespaces)
        yield 'effective-'+cases[0][0].split(':')[-1], (row.copy(), source, [
            (instance(kind, value, bindings), valid) for kind, value, bindings, valid in cases
        ])
    declarations = ('<xs:simpleType name="List"><xs:list itemType="xs:decimal"/></xs:simpleType>'
                    '<xs:simpleType name="Token"><xs:restriction base="xs:token"/></xs:simpleType>')
    yield 'effective-list', (row.copy(), files(declarations +
        '<xs:element name="request" type="xs:anySimpleType" fixed="1 2"/>'), [
        (instance('List', '1.0 2.00'), True), (instance('List', '1 3'), False)])
    yield 'effective-named-token', (row.copy(), files(declarations +
        '<xs:element name="request" type="xs:string" fixed=" a  b "/>'), [
        (instance('Token', ' a  b '), True), (instance('Token', 'a c'), False)])

    digits = '8750000000000000'
    bound = f'PT0.{digits}1S'
    exact = f'PT0.{digits}2S'
    duration = ('<xs:simpleType name="Bound"><xs:restriction base="xs:duration">'
                f'<xs:minExclusive value="{bound}"/></xs:restriction></xs:simpleType>')
    request = '<xs:element name="request"><xs:complexType>{}</xs:complexType></xs:element>'
    attribute = '<xs:attribute name="d" type="Bound"/>'
    other = ('<xs:element name="other"><xs:complexType><xs:sequence>'
             '<xs:element name="label" type="xs:int"/></xs:sequence></xs:complexType>'
             '<xs:key name="key"><xs:selector xpath="label"/><xs:field xpath="."/></xs:key></xs:element>')
    for key, fragment in [
        ('owner', request.replace('name="request"', 'name="other"').format(attribute)),
        ('group', f'<xs:attributeGroup name="Unused">{attribute}</xs:attributeGroup>'),
    ]:
        selected = '<xs:element name="request" type="xs:string"/>'
        yield 'unreachable-'+key, (row.copy(), files(duration+selected+fragment), [
            ('<request>value</request>', True)])
    yield 'unreachable-key', (row.copy(), files(duration+request.format(attribute)+other), [
        (f'<request d="PT0.{digits}{digit}S"/>', int(digits+digit)>int(digits+'1')) for digit in '0123'
    ])
    with_sibling_key = request.format('<xs:sequence><xs:element ref="other"/>'
                                      '</xs:sequence>'+attribute)
    # The sibling's key refers to an attribute with the same name, but cannot
    # select a value on its ancestor. Changing the selected root's attribute
    # can neither alter the key nor hide a violation of it.
    sibling = ('<xs:element name="other"><xs:complexType><xs:attribute name="d" type="xs:int"/>'
               '</xs:complexType><xs:key name="k"><xs:selector xpath="."/>'
               '<xs:field xpath="@d"/></xs:key></xs:element>')
    yield 'unrelated-sibling-key', (row.copy(), files(duration+with_sibling_key+sibling), [
        (f'<request d="{exact}"><other d="7"/></request>', True),
        (f'<request d="{bound}"><other d="7"/></request>', False),
        (f'<request d="{exact}"><other/></request>', False),
    ])
    own_key = ('<xs:key name="codeKey"><xs:selector xpath="."/>'
               '<xs:field xpath="@code"/></xs:key></xs:element>')
    source = request.format(attribute+'<xs:attribute name="code" type="xs:int"/>').removesuffix('</xs:element>')
    yield 'unrelated-own-key-field', (row.copy(), files(duration+source+own_key), [
        (f'<request d="{exact}" code="7"/>', True),
        (f'<request d="{bound}" code="7"/>', False),
    ])
    for key, attributes, element_block in [('block', '', ' block="#all"'),
                                            ('block-default', 'blockDefault="#all"', '')]:
        derived = ('<xs:complexType name="Base"/>'
                   '<xs:complexType name="Derived"><xs:complexContent><xs:extension base="Base">'
                   f'{attribute}</xs:extension></xs:complexContent></xs:complexType>'
                   f'<xs:element name="request" type="Base"{element_block}/>')
        yield 'unreachable-derived-'+key, (row.copy(), files(duration+derived, attributes), [
            ('<request/>', True), (instance('Derived', ''), False)
        ])
    types = ('<xs:complexType name="Base"/><xs:complexType name="Derived"><xs:complexContent>'
             '<xs:extension base="t:Base"><xs:attribute name="d" type="t:Bound"/>'
             '</xs:extension></xs:complexContent></xs:complexType>')
    for key, type_block, schema_block in [('type-block', ' block="#all"', ''),
                                           ('type-block-default', '', ' blockDefault="#all"')]:
        schemas = files('<xs:import namespace="urn:types" schemaLocation="types.xsd"/>'
                        '<xs:element name="request" type="t:Base"/>', 'xmlns:t="urn:types"')
        schemas['types.xsd'] = files(duration+types.replace('name="Base"', f'name="Base"{type_block}'),
                                    'xmlns:t="urn:types" targetNamespace="urn:types"'+schema_block)['main.xsd']
        yield 'unreachable-derived-'+key, (row.copy(), schemas, [
            ('<request/>', True), (instance('t:Derived', '', 'xmlns:t="urn:types"'), False)
        ])

    union = '<xs:simpleType name="Choice"><xs:union memberTypes="Bound xs:boolean"/></xs:simpleType>'
    for kind, definition, constraint, value in [
        ('union', union, 'fixed', exact),
        ('list', '<xs:simpleType name="Items"><xs:list itemType="Bound"/></xs:simpleType>'
                 '<xs:simpleType name="Choice"><xs:restriction base="Items">'
                 '<xs:length value="2"/></xs:restriction></xs:simpleType>', 'default', f'{exact} {exact}'),
    ]:
        selected = request.format(f'<xs:attribute name="d" type="Choice" {constraint}="{value}"/>')
        cases = [('<request/>', True), (f'<request d="{value}"/>', True),
                 (f'<request d="{value.replace(exact, bound, 1)}"/>', False)]
        if kind == 'list':
            cases.extend([(f'<request d="{exact}"/>', False), (f'<request d="{exact} nope"/>', False),
                          (f'<request d="{exact} PT0.{digits}3S"/>', True)])
        else:
            cases.append(('<request d="true"/>', False))
        yield 'projected-'+kind, (row.copy(), files(duration+definition+selected), cases)

    union = ('<xs:simpleType name="Choice">'
             '<xs:union xmlns="" memberTypes="m:Bound Text"/></xs:simpleType>')
    qualified = row | dict(namespace='urn:main')
    for variant in ('default', 'prefixed', 'included'):
        nested = variant == 'included'
        type_name = 'm:Choice' if variant == 'prefixed' else 'Choice'
        body = duration+union+request.format(f'<xs:attribute name="d" type="{type_name}"/>')
        attrs = 'xmlns:m="urn:main" targetNamespace="urn:main"'
        if variant != 'prefixed':
            attrs += ' xmlns="urn:main"'
        schemas = files('<xs:import schemaLocation="plain.xsd"/>' +
                       ('<xs:include schemaLocation="nested.xsd"/>' if nested else body), attrs)
        if nested:
            schemas['nested.xsd'] = files(body, attrs)['main.xsd']
        schemas['plain.xsd'] = files('<xs:simpleType name="Text"><xs:restriction base="xs:string">'
                                    '<xs:enumeration value="ok"/></xs:restriction></xs:simpleType>')['main.xsd']
        yield 'projected-QName-'+variant, (qualified.copy(), schemas, [
            ('<request xmlns="urn:main" d="ok"/>', True), ('<request xmlns="urn:main" d="bad"/>', False),
            (f'<request xmlns="urn:main" d="{exact}"/>', True), (f'<request xmlns="urn:main" d="{bound}"/>', False)])


def validation_scope_fixtures():
    """Shared XML expectations for the downloaded, audit and WASM validators."""
    row = dict(number='42', environment='prod', revision_id='a'*64,
               root='request', namespace=None, schema_path='main.xsd')

    def schema(body, bindings=''):
        return f'<xs:schema xmlns:xs="{XS}" {bindings}>{body}</xs:schema>'.encode()

    def fixture(body, cases, *, unavailable=None, bindings=''):
        return row.copy(), {'main.xsd': schema(body, bindings)}, cases, unavailable

    xsi = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    union = '<xs:simpleType name="Choice"><xs:union memberTypes="xs:ID xs:int"/></xs:simpleType>'
    owner = '<xs:element name="record" maxOccurs="unbounded"><xs:complexType><xs:sequence>'
    ending = ('</xs:sequence></xs:complexType></xs:element></xs:sequence>'
              '<xs:attribute name="id" type="xs:ID"/></xs:complexType></xs:element>')
    for name, declared, types, explicit, identifies in [
            ('direct','xs:ID','','',True),
            ('derived','Derived','<xs:simpleType name="Derived"><xs:restriction base="xs:ID"/></xs:simpleType>','',True),
            ('instance','xs:anySimpleType','',' xsi:type="xs:ID"',True),
            ('union','Choice',union.replace('xs:int','xs:string'),'',True),
            ('union-instance','xs:anySimpleType',union.replace('xs:int','xs:string'),' xsi:type="Choice"',True),
            ('string-first','Choice',union.replace('xs:ID xs:int','xs:string xs:ID'),'',False),
            ('string','xs:string','','',False)]:
        body = (types+'<xs:element name="request"><xs:complexType><xs:sequence>'+owner
                +f'<xs:element name="value" type="{declared}" maxOccurs="unbounded"/>'+ending)
        def document(groups, attribute=''):
            header = f'<request {xsi} xmlns:xs="{XS}"{attribute}>'
            return header+'\n'.join('<record>'+''.join(f'<value{explicit}>{v}</value>' for v in group)
                                     +'</record>' for group in groups)+'</request>'
        duplicate = ('/request/record/value', 'Значение ID') if identifies else (None, None)
        yield 'document-id-'+name, fixture(body, [
            (document([['hello']]),None,None), (document([['hello'],['world']]),None,None),
            (document([['hello','hello']]),None,None),
            (document([['hello'],['  hello  ']]),*duplicate),
            (document([['world'],['hello']], ' id="hello"'),*duplicate),
        ])

    no_profiles = ('<xs:element name="request"><xs:complexType><xs:sequence>'
                   '<xs:element name="record" maxOccurs="unbounded"><xs:complexType><xs:sequence>'
                   '<xs:element name="value" type="xs:anySimpleType" minOccurs="0"/>'
                   '<xs:any namespace="urn:skip" processContents="skip" minOccurs="0"/>'
                   '</xs:sequence></xs:complexType></xs:element></xs:sequence></xs:complexType></xs:element>')
    header = f'<request {xsi} xmlns:xs="{XS}" xmlns:s="urn:skip">'
    active = '<record><value xsi:type="xs:ID">hello</value></record>'
    skipped = '<record><s:extra xsi:type="xs:ID">hello</s:extra></record>'
    yield 'document-id-no-profiles', fixture(no_profiles, [
        (header+active+'\n'+active+'</request>','/request/record/value','Значение ID'),
        (header+active+'\n'+skipped+'</request>',None,None),
        (header+skipped+'\n'+active+'</request>',None,None),
        (header+skipped+'\n'+skipped+'</request>',None,None),
    ])
    yield 'contextual-union', fixture(union +
        '<xs:element name="request" type="xs:anySimpleType" fixed="hello"/>', [
            (f'<request {xsi} xsi:type="Choice">{value}</request>', path, reason)
            for value, path, reason in [
                ('hello', None, None), (' hello ', None, None), ('world', '/request', 'fixed value'),
            ]
        ])
    yield 'contextual-real-duplicate', fixture(union +
        '<xs:element name="request"><xs:complexType><xs:sequence>'
        '<xs:element name="value" type="xs:anySimpleType" fixed="hello"/>'
        '<xs:element name="repeat" minOccurs="0"><xs:complexType>'
        '<xs:attribute name="id" type="xs:ID"/></xs:complexType></xs:element>'
        '</xs:sequence><xs:attribute name="id" type="xs:ID"/></xs:complexType></xs:element>', [
            (f'<request {xsi}>\n<value xsi:type="Choice">hello</value>\n</request>', None, None),
            (f'<request {xsi} id="hello">\n<value xsi:type="Choice">hello</value>\n<repeat id="hello"/>\n</request>',
             '/request/repeat/@id', 'ID'),
        ])

    bound = 'PT0.87500000000000001S'
    exact = 'PT0.87500000000000002S'
    duration = ('<xs:simpleType name="Bound"><xs:restriction base="xs:duration">'
                f'<xs:minExclusive value="{bound}"/></xs:restriction></xs:simpleType>')
    child = ('<xs:element name="other"><xs:complexType>'
             '<xs:attribute name="d" type="xs:int"/></xs:complexType></xs:element>')
    for name, selector, field, constraint in (
        ('selector', 'other', '@d', 'key'),
        ('field', '.', 'other/@d', 'key'),
        ('descendant', './/other', '@d', 'key'),
        ('union', 'absent | child::other', 'attribute::d', 'unique'),
        ('self', '.', '@d', 'key'),
    ):
        checks = (f'<xs:{constraint} name="k"><xs:selector xpath="{selector}"/>'
                  f'<xs:field xpath="{field}"/></xs:{constraint}>')
        request = ('<xs:element name="request"><xs:complexType><xs:sequence>'+child+'</xs:sequence>'
                   '<xs:attribute name="d" type="Bound"/></xs:complexType>'+checks+'</xs:element>')
        cases = [(f'<request d="{exact}">\n<other d="7"/>\n</request>', None, None),
                 (f'<request d="{bound}">\n<other d="7"/>\n</request>', '/request/@d', 'календарное ограничение')]
        if constraint == 'key' and name != 'self':
            cases.append(('<request d="PT1S">\n<other/>\n</request>',
                          '/request' if name == 'field' else '/request/other', 'key'))
        yield 'identity-path-'+name, fixture(duration+request, cases, unavailable='minExclusive' if name == 'self' else None)

    inclusive = duration.replace('minExclusive', 'minInclusive').replace(bound, exact)
    for mode in ('none', 'lax', 'strict', 'skip'):
        wildcard = '' if mode == 'none' else f'<xs:any minOccurs="0" namespace="urn:extra" processContents="{mode}"/>'
        key = '<xs:key name="k"><xs:selector xpath="other"/><xs:field xpath="@d"/></xs:key>'
        request = ('<xs:element name="request"><xs:complexType><xs:sequence>'+child+wildcard+'</xs:sequence>'
                   '<xs:attribute name="d" type="Bound"/></xs:complexType>'+key+'</xs:element>')
        cases = [(f'<request d="{exact}">\n<other d="7"/>\n</request>', None, None),
                 (f'<request d="{bound}">\n<other d="7"/>\n</request>', '/request/@d', 'календарное ограничение'),
                 ('<request d="PT1S">\n<other/>\n</request>', '/request/other', 'key')]
        if mode == 'lax':
            for value, location in [(exact, None), (bound, '/request/extra')]:
                xml = (f'<request d="{exact}" {xsi} xmlns:e="urn:extra">\n'
                       f'<other d="7"/><e:extra xsi:type="Bound">{value}</e:extra>\n</request>')
                cases.append((xml, location, 'календарное ограничение' if location else None))
        yield 'identity-sibling-'+mode, fixture(inclusive+request, cases)
        if mode == 'lax':
            for label, selector in [('self', '.'), ('descendant', './/request')]:
                yield 'identity-lax-'+label, fixture(inclusive+request.replace('xpath="other"', f'xpath="{selector}"'),
                                                     cases[:1], unavailable='minInclusive')

    dynamic = ('<xs:complexType name="Dynamic"><xs:sequence><xs:element name="target"><xs:complexType>'
               '<xs:attribute name="d" type="Bound"/></xs:complexType></xs:element></xs:sequence></xs:complexType>'
               '<xs:element name="request"><xs:complexType><xs:sequence>'
               '<xs:any namespace="urn:extra" processContents="lax"/></xs:sequence></xs:complexType>'
               '<xs:key name="k"><xs:selector xpath=".//target"/><xs:field xpath="@d"/></xs:key></xs:element>')
    yield 'identity-lax-explicit-complex-type', fixture(inclusive+dynamic, [
        (f'<request {xsi} xmlns:e="urn:extra"><e:other xsi:type="Dynamic"><target d="PT1S"/></e:other></request>',
         None, None),
    ], unavailable='minInclusive')

    namespaced = ('<xs:element name="request"><xs:complexType><xs:sequence>'+child+'</xs:sequence>'
                  '<xs:attribute name="d" type="m:Bound"/><xs:attribute name="code" type="xs:int"/>'
                  '</xs:complexType><xs:key name="codes"><xs:selector xpath="."/>'
                  '<xs:field xpath="@m:code"/></xs:key><xs:keyref name="uses" refer="m:codes">'
                  '<xs:selector xmlns:s="urn:request" xpath=".//s:other"/>'
                  '<xs:field xmlns:f="urn:request" xpath="@f:d"/></xs:keyref></xs:element>')
    yield 'identity-path-prefixed-keyref', (
        row | {'namespace': 'urn:request'},
        {'main.xsd': schema(duration+namespaced, 'xmlns:m="urn:request" targetNamespace="urn:request" '
                           'elementFormDefault="qualified" attributeFormDefault="qualified"')},
        [(f'<m:request xmlns:m="urn:request" m:d="{exact}" m:code="7">\n'
          '<m:other m:d="7"/>\n</m:request>', None, None),
         (f'<m:request xmlns:m="urn:request" m:d="{bound}" m:code="7">\n'
          '<m:other m:d="7"/>\n</m:request>', '/request/@d', 'календарное ограничение')],
        None,
    )
    recursive = ('<xs:element name="request"><xs:complexType><xs:sequence>'
                 '<xs:element ref="request" minOccurs="0"/></xs:sequence>'
                 '<xs:attribute name="d" type="Bound"/></xs:complexType>'
                 '<xs:unique name="nested"><xs:selector xpath=".//request"/>'
                 '<xs:field xpath="@d"/></xs:unique></xs:element>')
    yield 'identity-path-recursive-intersection', fixture(duration+recursive, [
        ('<request><request d="PT1S"/></request>', None, None),
    ], unavailable='minExclusive')
    implicit_member = ('<xs:element name="head"><xs:complexType>'
                       '<xs:attribute name="d" type="Bound"/></xs:complexType></xs:element>'
                       '<xs:element name="member" substitutionGroup="head"/>'
                       '<xs:element name="request"><xs:complexType><xs:sequence><xs:element ref="head"/>'
                       '</xs:sequence></xs:complexType><xs:key name="memberKey"><xs:selector xpath="member"/>'
                       '<xs:field xpath="@d"/></xs:key></xs:element>')
    yield 'identity-path-substitute-intersection', fixture(duration+implicit_member, [
        ('<request><member d="PT1S"/></request>', None, None),
    ], unavailable='minExclusive')

    def substitution(head_block, mid_block='', refs=('head',), *, method='extension', tight=True, imported=False):
        ns = 'm:' if imported else ''
        base_attribute = '<xs:attribute name="d" type="xs:duration"/>' if method == 'restriction' else ''
        bound_type = ns+'Bound' if tight else 'xs:duration'
        base_block = ' block=""' if imported else ''
        def block(value):
            return f' block="{value}"' if value else ''
        derived = (f'<xs:complexContent><xs:{method} base="{ns}Base">'
                   f'<xs:attribute name="d" type="{bound_type}"/></xs:{method}></xs:complexContent>')
        definitions = (
            (duration if tight else '') + f'<xs:complexType name="Base"{base_block}>{base_attribute}</xs:complexType>'
            f'<xs:element name="head" type="{ns}Base"{block("" if imported else head_block)}/>'
            f'<xs:element name="mid" type="{ns}Base" substitutionGroup="{ns}head"{block(mid_block)}/>'
            f'<xs:element name="leaf" substitutionGroup="{ns}mid"><xs:complexType>'
            f'{derived}</xs:complexType></xs:element>'
        )
        request = '<xs:element name="request"><xs:complexType><xs:sequence>' + ''.join(
            f'<xs:element ref="{ns}{ref}"/>' for ref in refs) + '</xs:sequence></xs:complexType></xs:element>'
        if imported:
            return {'main.xsd': schema('<xs:import namespace="urn:group" schemaLocation="group.xsd"/>'+request,
                                       'xmlns:m="urn:group"'),
                    'group.xsd': schema(definitions, 'xmlns:m="urn:group" targetNamespace="urn:group"'
                                         f' blockDefault="{head_block}"')}
        return {'main.xsd': schema(definitions+request)}

    for name, head, mid, refs, accepted, rejected, method, imported in (
        ('head-extension', 'extension', '', ('head',), ('head', 'mid'), ('leaf',), 'extension', False),
        ('mid-extension', 'extension', '', ('mid',), ('mid', 'leaf'), ('head',), 'extension', False),
        ('both-extension', 'extension', '', ('head', 'mid'), ('mid leaf',), ('leaf mid',), 'extension', False),
        ('head-through-blocked-mid', '', 'substitution', ('head',), ('head', 'mid', 'leaf'), (), 'extension', False),
        ('mid-substitution', '', 'substitution', ('mid',), ('mid',), ('leaf',), 'extension', False),
        ('head-restriction', 'restriction', '', ('head',), ('head', 'mid'), ('leaf',), 'restriction', False),
        ('head-all', '#all', '', ('head',), ('head',), ('mid', 'leaf'), 'extension', False),
        ('imported-default', 'extension', '', ('head',), ('head', 'mid'), ('leaf',), 'extension', True),
    ):
        def xml(children):
            prefix = 'm:' if imported else ''
            attributes = ' xmlns:m="urn:group"' if imported else ''
            return f'<request{attributes}>\n' + '\n'.join(
                f'<{prefix}{name}'+(' d="PT1S"' if name == 'leaf' else '')+'/>' for name in children.split()
            ) + '\n</request>'
        cases = ([(xml(child), None, None) for child in accepted]
                 + [(xml(child), '/request/'+child.split()[0], 'not expected') for child in rejected])
        for tight in (False, True):
            files = substitution(head, mid, refs, method=method, tight=tight, imported=imported)
            unprojectable = tight and any('leaf' in children.split() for children in accepted)
            yield ('substitution-'+name+('-exact' if tight else ''),
                   (row.copy(), files, cases, 'minExclusive' if unprojectable else None))
    definitions = ('<xs:complexType name="Base"/>'
                   '<xs:complexType name="Derived"><xs:complexContent><xs:extension base="Base">'
                   '<xs:attribute name="d" type="Bound"/></xs:extension></xs:complexContent></xs:complexType>'
                   '<xs:element name="head" type="Base" block="extension"/>'
                   '<xs:element name="member" type="Derived" substitutionGroup="head"/>'
                   '<xs:element name="request"><xs:complexType><xs:sequence><xs:element ref="head"/>'
                   '</xs:sequence></xs:complexType></xs:element>')
    yield 'substitution-direct-extension', fixture(duration+definitions, [
        ('<request>\n<head/>\n</request>', None, None),
        ('<request>\n<member d="PT1S"/>\n</request>', '/request/member', 'not expected'),
    ])
    marker = '<xs:element name="marker" type="xs:decimal" fixed="1.0"/>'
    marked = definitions.replace('<xs:element ref="head"/>', '<xs:element ref="head"/>'+marker)
    yield 'substitution-fixed-neighbor', fixture(duration+marked, [
        ('<request>\n<head/><marker>1.00</marker>\n</request>', None, None),
        ('<request>\n<head/><marker>2</marker>\n</request>', '/request/marker', 'fixed'),
        ('<request>\n<member/><marker>1</marker>\n</request>', '/request/member', 'not expected'),
    ])
    allowed = marked.replace('block="extension"', '').replace('type="Bound"', 'type="xs:decimal" fixed="2.0"')
    yield 'substitution-fixed-member', fixture(allowed, [
        ('<request>\n<member d="2.00"/><marker>1</marker>\n</request>', None, None),
        ('<request>\n<member d="3"/><marker>1</marker>\n</request>', '/request/member/@d', 'fixed'),
    ])
    mixed = definitions.replace('<xs:complexType name="Derived">', '<xs:complexType name="Extended">').replace(
        'name="d" type="Bound"', 'name="d" type="xs:duration"').replace(
        '<xs:element name="head"',
        '<xs:complexType name="Derived"><xs:complexContent><xs:restriction base="Extended">'
        '<xs:attribute name="d" type="Bound"/></xs:restriction></xs:complexContent></xs:complexType>'
        '<xs:element name="head"')
    # libxml2 admits the member when walking a restriction above an extension.
    # Do not publish without an exact check for the admitted attribute.
    yield 'substitution-mixed-derivation', fixture(duration+mixed, [
        ('<request>\n<member d="PT1S"/>\n</request>', None, None),
    ], unavailable='minExclusive')
    xtype_chain = ('<xs:complexType name="B"/><xs:complexType name="M" block="extension">'
                   '<xs:complexContent><xs:extension base="B"/></xs:complexContent></xs:complexType>'
                   '<xs:complexType name="Leaf"><xs:complexContent><xs:extension base="M">'
                   '<xs:attribute name="d" type="Bound"/></xs:extension></xs:complexContent></xs:complexType>')
    xtype_bound = duration.replace('minExclusive', 'minInclusive').replace(bound, exact)
    for base, value, path, reason, unavailable in (
        ('B', f' d="{bound}"', None, None, 'minInclusive'),
        ('M', '', '/request/@type', 'validly derived', None),
    ):
        yield 'instance-type-block-from-'+base, fixture(
            xtype_bound+xtype_chain+f'<xs:element name="request" type="{base}"/>', [
                ('<request/>', None, None),
                (f'<request {xsi} xsi:type="Leaf"{value}/>', path, reason),
            ], unavailable=unavailable,
        )


if __name__ == '__main__':
    if '--validation-scopes' in sys.argv:
        print(json.dumps({name: dict(pack=pack(row, files), cases=cases)
                          for name, (row, files, cases, unavailable) in validation_scope_fixtures()
                          if unavailable is None and (len(sys.argv) < 3 or name.startswith(sys.argv[2]))}))
        sys.exit()
    attributes = [(kind, attribute_fixture(kind)) for kind in ('direct', 'reference', 'neighbor', 'required')]
    attributes.append(('chameleon', chameleon_attribute_fixture()))
    attributes.extend((kind if subject == 'attribute' else subject+'-'+kind, compound_attribute_fixture(kind, subject))
                      for kind, subject in COMPOUND_CASES)
    attributes.extend((name, wildcard_attribute_fixture(explicit))
                      for name, explicit in (('wildcard-only', False), ('wildcard-with-explicit', True)))
    attributes.append(('implicit-wildcard-only', wildcard_attribute_fixture(False, implicit_only=True)))
    row, files, cases = cross_branch_lists_fixture()
    attributes.append(('cross-branch-lists', (row, files, [(document, valid) for document, valid, _ in cases])))
    attributes.append(('duration', duration_fixture()))
    attributes.append(('calendar-facets', calendar_facets_fixture()))
    attributes.append(('local-simple-content', local_simple_content_fixture()))
    attributes.append(('direct-calendar-bounds', direct_calendar_bounds_fixture()))
    attributes.append(('union-instance-type', union_instance_type_fixture()))
    attributes.extend((kind+'-'+context+'-without-fixed', calendar_without_fixed_fixture(kind, context))
                      for kind in ('duration', 'dateTime') for context in ('direct', 'inline'))
    attributes.extend((kind+'-max-inclusive-without-fixed', calendar_without_fixed_fixture(kind, inclusive_max=True))
                      for kind in ('duration', 'dateTime'))
    attributes.append(('duration-default-without-fixed', calendar_without_fixed_fixture('duration', default=True)))
    attributes.extend(effective_value_and_projection_fixtures())
    payload = dict(pack=pack(*fixture()), cases=[(xml(body), expected) for body, expected in CASES],
                   attributes={kind: dict(pack=pack(row, files), cases=cases)
                               for kind, (row, files, cases) in attributes},
                   notation=list(notation_packs()))
    print(json.dumps(payload))
