import io
import unittest
import zipfile
from wsdl_sources import discover,collect,bridge
from sync import inspect_file,sha
from schema import Schema

SEED="https://ecp-test.sfr.gov.ru/sedo/fss/api/soap/SedoGatewaySimple?wsdl"
BASE=SEED.split("?")[0]
XS="http://www.w3.org/2001/XMLSchema"
# These immutable editions introduced RegNumSfr; later contracts may change it.
ARCHIVED_91_IDS={"7e6f8189b4b21434371ed568a364cdc59848bcbef0fb0cdf1dd68619a5bf5331",
                 "12e1b7c0fb79734d27c96ac03a875bf71ec04dbd376fdfdad0fb2a61e96ab765"}

class WsdlSourcesTests(unittest.TestCase):
 def test_intermediate_manifest_ignores_annotation_imports_but_keeps_direct_roots(self):
  from collector_snapshots import make_manifest,validate_manifest
  base=BASE.rsplit('/',1)[0]+'/'
  resources={SEED:f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}"><w:types><xs:schema><xs:import namespace="urn:A" schemaLocation="main.xsd"/></xs:schema></w:types></w:definitions>'.encode(),
             base+'main.xsd':f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:A"><xs:annotation><xs:appinfo><xs:import namespace="urn:example" schemaLocation="example-only.xsd"/></xs:appinfo></xs:annotation><xs:include schemaLocation="common.xsd"/></xs:schema>'.encode(),
             base+'common.xsd':f'<xs:schema xmlns:xs="{XS}"><xs:element name="r" type="xs:string"/></xs:schema>'.encode()}
  row,objects=self.collect(resources)
  row.update(inspect_file(objects[row['sha256']],'zip',legacy_schema_roots=True))
  self.assertEqual(next(item for item in row['schemas'] if item['namespace']=='urn:A')['roots'],[])
  self.assertEqual(validate_manifest(make_manifest(row,'2026-11-17T00:00:00+00:00'),objects.__getitem__),row)

 def test_old_bridge_root_indexes_and_fresh_snapshot_hash_replay(self):
  import json,copy
  from xml.sax.saxutils import escape
  from sync import encode,write
  from wsdl_sources import _archive
  from tests.test_source_collection_regressions import SourceCollectionTests,archive
  library=_archive({'library.xsd':f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:library"><xs:include schemaLocation="common.xsd"/></xs:schema>'.encode(),
                    'common.xsd':f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:library"><xs:simpleType name="Code"><xs:restriction base="xs:string"/></xs:simpleType><xs:element name="r" type="xs:string"/></xs:schema>'.encode()})
  own=f'<xs:schema xmlns:xs="{XS}" xmlns:v="urn:library" targetNamespace="urn:arm"><xs:import namespace="urn:library" schemaLocation="library.xsd"/><xs:element name="arm" type="v:Code"/></xs:schema>'
  paragraphs=['Тип сообщения 999: Example','<a:arm xmlns:a="urn:arm"/>',own]
  doc=archive('word/document.xml',('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join('<w:p><w:r><w:t>'+escape(value)+'</w:t></w:r></w:p>' for value in paragraphs)+'</w:body></w:document>').encode())
  support=dict(environment='test',kind='zip',url=SEED+'#xsd-bundle',source_type='wsdl',source_url=SEED,source_files=[],sha256=sha(library),**inspect_file(library,'zip',legacy_schema_roots=True))
  for version in (4,5,6):
   with self.subTest(version=version):
    f=SourceCollectionTests();f.setUp();self.addCleanup(f.doCleanups)
    for url in f.resources:
     if url.endswith('.docx'):f.resources[url]=doc
    write(f.state/'objects'/sha(library),library)
    f.collect(1,snapshot_format=version,wsdl_entries=[support])
    snapshot=f.current();document=next(row for row in snapshot['entries'] if row['kind']=='docx' and row['environment']=='test')
    bridge_row=document['wsdl_archives'][0]
    self.assertEqual(next(item for item in bridge_row['schemas'] if item['path'].endswith('/library.xsd'))['roots'],[])
    old=(f.state/'current.json').read_bytes()
    f.collect(2,snapshot_format=version,wsdl_entries=[support])
    self.assertEqual((f.state/'current.json').read_bytes(),old)
    f.collect(3,snapshot_format=7,wsdl_entries=[support])
    fresh=(f.state/'current.json').read_bytes()
    self.assertNotEqual(fresh,old)
    current_document=next(row for row in f.current()['entries'] if row['kind']=='docx' and row['environment']=='test')
    self.assertEqual(next(item for item in current_document['wsdl_archives'][0]['schemas'] if item['path'].endswith('/library.xsd'))['roots'],[{'name':'r','description':''}])
    f.collect(4,snapshot_format=7,wsdl_entries=[support])
    self.assertEqual((f.state/'current.json').read_bytes(),fresh)

 def test_documentation_examples_are_neither_schemas_nor_downloads(self):
  base=BASE.rsplit('/',1)[0]+'/'
  example='<xs:schema><xs:element name="demo" type="xs:string"/></xs:schema>'
  definitions=(f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}">'
      f'<w:documentation>{example}</w:documentation><w:types><xs:schema>'
      '<xs:import namespace="urn:real" schemaLocation="real.xsd"/></xs:schema></w:types></w:definitions>').encode()
  schema=(f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:real"><xs:annotation><xs:appinfo xml:base="examples/">'
      '<xs:import namespace="urn:example" schemaLocation="example.xsd"/></xs:appinfo></xs:annotation>'
      '<xs:element name="working" type="xs:string"/></xs:schema>').encode()
  requested=[]
  def fetch(url):
   requested.append(url)
   return {SEED:definitions,base+'real.xsd':schema,base+'example.xsd':
           f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:example"/>'.encode()}[url]
  objects={}
  def blob(raw):
   objects[sha(raw)]=raw
   return sha(raw)
  row=collect(SEED,'test',fetch,blob)
  self.assertEqual(requested,[SEED,base+'real.xsd'])
  with zipfile.ZipFile(io.BytesIO(objects[row['sha256']])) as archive:
   self.assertEqual([archive.read(n) for n in archive.namelist()],[schema])
   tree=Schema(archive,archive.namelist()[0],'working')
   tree.field(tree.root)
   self.assertEqual(tree.fields[0]['name'],'working')

 def test_archived_manifest_with_documentation_example_replays_original_bytes(self):
  from collector_snapshots import make_manifest,validate_manifest
  from wsdl_sources import _archive
  attrs=f'xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}"'
  element=lambda name:f'<xs:element name="{name}" type="xs:string"/>'
  original=(f'<w:definitions {attrs}><w:documentation><xs:schema>{element("demo")}</xs:schema></w:documentation>'
            f'<w:types><xs:schema>{element("real")}</xs:schema></w:types></w:definitions>').encode()
  legacy=_archive({f'schemas/001/inline-{index}.xsd':f'<xs:schema {attrs}>{element(name)}</xs:schema>'.encode()
                   for index,name in enumerate(('demo','real'))})
  row=dict(environment='test', url=SEED+'#xsd-bundle', kind='zip',label='XSD по WSDL шлюза СФР',
           archive_name='wsdl-schemas.zip',source_type='wsdl',source_url=SEED,
           source_files=[dict(url=SEED,sha256=sha(original),bytes=len(original),kind='wsdl',namespace=None)],
           sha256=sha(legacy),bytes=len(legacy),**inspect_file(legacy,'zip'))
  objects={sha(original):original,sha(legacy):legacy}
  self.assertEqual(validate_manifest(make_manifest(row,'2026-11-16T00:00:00+00:00'),objects.__getitem__),row)
  fresh,_=self.collect({SEED:original})
  self.assertEqual([root['name'] for s in fresh['schemas'] for root in s['roots']],['real'])

 def test_redirected_schema_resolves_children_from_final_url_and_replays_offline(self):
  from unittest.mock import patch
  from types import SimpleNamespace
  from collector import capture
  from collector_snapshots import make_manifest, validate_manifest
  from collector_transport import PoliteFetch
  from sync import SITES, _get
  base = SEED.rsplit('/', 1)[0] + '/'
  moved = base + 'v2/main.xsd'
  schema = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:x">'
  page = SITES['test']
  initial = base + 'v1/main.xsd'
  resources = {page: (page, f'<a href="{SEED}">WSDL</a>'),
               SEED: (SEED, f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}"><w:types><xs:schema><xs:import namespace="urn:x" schemaLocation="v1/main.xsd"/></xs:schema></w:types></w:definitions>'),
               initial: (moved, schema+'<xs:include schemaLocation="Common.xsd"/></xs:schema>')}
  for folder, name in (('v1/', 'previous'), ('v2/', 'current')):
   url = base + folder + 'Common.xsd'
   resources[url] = (url, schema+f'<xs:element name="{name}"/></xs:schema>')
  expected = None
  for polite in ('0', '1'):
   with self.subTest(polite=polite):
    requested = []
    def opener(redirect, transport):
     def open_response(request, timeout):
      requested.append(request.full_url)
      final, xml = resources[request.full_url]
      if final != request.full_url:
       redirect.redirect_request(request, None, 302, 'Found', {}, final)
       requested.append(final)
      response = io.BytesIO(xml.encode())
      response.geturl = lambda: final
      return response
     return SimpleNamespace(open=open_response)
    with patch('sync.build_opener', side_effect=opener), patch.dict('os.environ', SEDO_SFR_POLITE=polite), \
         patch('sync._polite_fetch', PoliteFetch(_get)), patch('collector_transport.time.sleep'):
     manifest, objects = capture('test')
    self.assertEqual(requested, [page, SEED, initial, moved, base+'v2/Common.xsd'])
    row = validate_manifest(manifest, objects.__getitem__)
    self.assertEqual([r['name'] for s in row['schemas'] for r in s['roots']], ['current','current'])
    self.assertEqual(next(x for x in row['source_files'] if x['url']==initial)['final_url'], moved)
    if expected is None:
     expected = (row, objects)
    self.assertEqual((row, objects), expected)
    row['source_files'][1]['final_url'] = 'https://example.org/v2/main.xsd'
    with self.assertRaisesRegex(ValueError, 'outside'):
     validate_manifest(make_manifest(row, manifest['observed_at']), objects.__getitem__)
    row['source_files'][1]['final_url'] = moved

 def test_xml_base_cannot_confirm_an_unverified_offline_dependency(self):
  base, resources = self.branch_resources()
  root = base + 'v1/root.xsd'
  resources[base+'v1/v2/Common.xsd'] = resources[base+'v2/Common.xsd']
  for tag in (b'xs:schema', b'xs:include'):
   changed = dict(resources)
   changed[root] = changed[root].replace(b'<'+tag+b' ', b'<'+tag+b' xml:base="v2/" ', 1)
   with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, 'xml:base'):
    self.collect(changed)
  # An unrelated annotation has no influence on dependency resolution.
  resources[root] = resources[root].replace(b'<xs:include', b'<xs:annotation xml:base="v2/"/><xs:include')
  self.collect(resources)

 def test_archive_and_legacy_offline_consumers_reject_xml_base_on_dependency(self):
  from audit_xml_examples import _schema
  from wsdl_sources import _archive
  from xsd_bundle import _prepare
  main = f'<xs:schema xmlns:xs="{XS}"><xs:include xml:base="v2/" schemaLocation="types.xsd"/><xs:element name="root" type="Code"/></xs:schema>'
  def types(base):
   return f'<xs:schema xmlns:xs="{XS}"><xs:simpleType name="Code"><xs:restriction base="xs:{base}"/></xs:simpleType></xs:schema>'.encode()
  files = {'main.xsd':main.encode(),'types.xsd':types('string'),'v2/types.xsd':types('int')}
  archive = _archive(files)
  revision = dict(schema_path='main.xsd',root='root',namespace=None)
  for action in (lambda:inspect_file(archive,'zip'), lambda:_schema(io.BytesIO(archive),revision), lambda:_prepare(revision,files)):
   with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'xml:base'):
    action()

 def test_inline_schema_without_namespace_preserves_declarations_and_both_roots(self):
  resources={SEED:(f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}" targetNamespace="urn:service"><w:types>'
                   '<xs:schema><xs:element name="plain" type="xs:string"/></xs:schema>'
                   '<xs:schema targetNamespace="urn:qualified"><xs:element name="qualified" type="xs:string"/></xs:schema>'
                   '</w:types></w:definitions>').encode()}
  row,objects=self.collect(resources)
  self.assertEqual({root['name'] for schema in row['schemas'] for root in schema['roots']},{'plain','qualified'})
  with zipfile.ZipFile(io.BytesIO(objects[row['sha256']])) as archive:
   for schema in row['schemas']:
    root=schema['roots'][0]['name']
    tree=Schema(archive,schema['path'],root);tree.field(tree.root)
    self.assertEqual(tree.fields[0]['name'],root)

 def test_inline_schema_combining_two_includes_binds_message_and_validates_zip(self):
  import subprocess
  import sys
  import tempfile
  from pathlib import Path
  from lxml import etree
  from module_xsd import match_document
  from xsd_bundle import _prepare, _selected, _zip

  base = BASE.rsplit('/', 1)[0] + '/'
  composition = ('<xs:include schemaLocation="root.xsd"/>'
                 '<xs:include schemaLocation="types.xsd"/>')
  root = f'<xs:schema xmlns:xs="{XS}"><xs:element name="request" type="Code"/></xs:schema>'.encode()
  types = f'<xs:schema xmlns:xs="{XS}"><xs:simpleType name="Code"><xs:restriction base="xs:int"/></xs:simpleType></xs:schema>'.encode()
  inline = f'<xs:schema xmlns:xs="{XS}">{composition}</xs:schema>'.encode()
  wsdl = (f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}"><w:types>'
          f'<xs:schema>{composition}</xs:schema></w:types></w:definitions>').encode()
  valid = b'<request>42</request>'
  invalid = b'<request>text</request>'
  with tempfile.TemporaryDirectory() as tmp:
   folder = Path(tmp)
   for name, raw in [('inline.xsd', inline), ('root.xsd', root), ('types.xsd', types)]:
    (folder/name).write_bytes(raw)
   native = etree.XMLSchema(etree.parse(str(folder/'inline.xsd')))
   self.assertTrue(native.validate(etree.fromstring(valid)))
   self.assertFalse(native.validate(etree.fromstring(invalid)))

   support, objects = self.collect({SEED: wsdl, base+'root.xsd': root, base+'types.xsd': types})
   document = dict(environment='test', module_bindings=[dict(number='999', root='request', namespace='')])
   matches, diagnostics = match_document(document, [support], objects.__getitem__)
   self.assertEqual(len(matches), 1, diagnostics)
   revision = dict(matches[0], environment='test', revision_id='fixture')
   self.assertIn('inline-', revision['schema_path'])
   with zipfile.ZipFile(io.BytesIO(objects[support['sha256']])) as archive:
    output, records, constraints = _prepare(revision, _selected(revision, archive))
   with zipfile.ZipFile(io.BytesIO(_zip(revision, output, records, constraints))) as package:
    package.extractall(folder)
   for expected, xml in [(0, valid), (1, invalid)]:
    (folder/'input.xml').write_bytes(xml)
    result = subprocess.run([sys.executable, 'validate.py', 'input.xml'], cwd=folder,
                            capture_output=True, text=True, encoding='utf8')
    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

 def test_legacy_manifest_with_include_only_inline_schema_keeps_original_zip(self):
  from collector_snapshots import make_manifest, validate_manifest
  from wsdl_sources import _archive

  base = BASE.rsplit('/', 1)[0] + '/'
  wsdl = (f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}"><w:types>'
          '<xs:schema><xs:include schemaLocation="part.xsd"/></xs:schema></w:types></w:definitions>').encode()
  part = f'<xs:schema xmlns:xs="{XS}"><xs:element name="part" type="xs:string"/></xs:schema>'.encode()
  legacy = _archive({'schemas/002/part.xsd': part})
  row = dict(environment='test', url=SEED+'#xsd-bundle', kind='zip', label='XSD по WSDL шлюза СФР',
             archive_name='wsdl-schemas.zip', source_type='wsdl', source_url=SEED, source_files=[
                 dict(url=SEED, sha256=sha(wsdl), bytes=len(wsdl), kind='wsdl', namespace=None),
                 dict(url=base+'part.xsd', sha256=sha(part), bytes=len(part), kind='xsd', namespace=None)],
             sha256=sha(legacy), bytes=len(legacy), **inspect_file(legacy, 'zip'))
  objects = {sha(wsdl): wsdl, sha(part): part, sha(legacy): legacy}
  self.assertEqual(validate_manifest(make_manifest(row, '2026-11-16T00:00:00+00:00'),
                                     objects.__getitem__), row)
  fresh, _ = self.collect({SEED: wsdl, base+'part.xsd': part})
  self.assertIn('schemas/001/inline-0.xsd', {item['path'] for item in fresh['schemas']})

 def test_qualified_inline_entry_binds_module_and_individual_documents(self):
  import base64
  import json
  import subprocess
  import sys
  import tempfile
  from pathlib import Path
  from catalog import derive
  from collector_snapshots import make_manifest, validate_manifest
  from module_xsd import match_document
  from wsdl_sources import _archive
  from xsd_bundle import _prepare, _selected, _zip

  base = BASE.rsplit('/', 1)[0] + '/'
  schema = f'<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" targetNamespace="urn:main">'
  wrapper = ('<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/"><w:types>' + schema
             + '<xs:include schemaLocation="root.xsd"/><xs:include schemaLocation="types.xsd"/>'
             + '</xs:schema></w:types></w:definitions>').encode()
  root = (schema + '<xs:element name="r" type="m:Code"/></xs:schema>').encode()
  types = (schema + '<xs:simpleType name="Code"><xs:restriction base="xs:int"/></xs:simpleType></xs:schema>').encode()
  support, objects = self.collect({SEED: wrapper, base+'root.xsd': root, base+'types.xsd': types})
  document = dict(kind='docx', environment='test', url='https://example.test/Тип_сообщения_999.docx',
                  sha256='a'*64, label='Example',
                  structure=[['Корневой элемент','r'],['Пространство имен файла XML-схемы','urn:main']])
  module = dict(document, module_bindings=[dict(number='999', root='r', namespace='urn:main')])
  match, = derive(dict(format=7, entries=[document, support]))['revisions']
  self.assertEqual(match['status'], 'matched')
  self.assertIn('inline-', match['schema_path'])
  matches, issues = match_document(module, [support], objects.__getitem__)
  self.assertEqual(len(matches), 1, issues)
  self.assertEqual(matches[0]['schema_path'], match['schema_path'])
  named = dict(document, structure=document['structure'] + [['Расположение файла XML-схемы','root.xsd']])
  explicit, = derive(dict(format=7, entries=[named, support]))['revisions']
  self.assertTrue(explicit['schema_path'].endswith('/root.xsd'))

  other = _archive({'root.xsd': root.replace(b'm:Code', b'xs:string')})
  objects[sha(other)] = other
  alternative = dict(kind='zip', environment='test', url='https://example.test/other.zip',
                     sha256=sha(other), **inspect_file(other, 'zip'))
  unmatched, = derive(dict(format=7, entries=[document, support, alternative]))['revisions']
  self.assertEqual(unmatched['status'], 'review')
  matches, issues = match_document(module, [support, alternative], objects.__getitem__)
  self.assertEqual(matches, [])
  self.assertIn('Неоднозначные схемы', [issue['reason'] for issue in issues])

  original_archive = objects[support['sha256']]
  old = dict(support, **inspect_file(original_archive, 'zip'))
  self.assertNotEqual(support['schemas'], old['schemas'])
  old_manifest = make_manifest(old, '2026-11-16T00:00:00+00:00')
  self.assertEqual(validate_manifest(old_manifest, objects.__getitem__), old)
  with zipfile.ZipFile(io.BytesIO(original_archive)) as archive:
   output, records, constraints = _prepare(match, _selected(match, archive))
  with tempfile.TemporaryDirectory() as tmp:
   folder = Path(tmp)
   with zipfile.ZipFile(io.BytesIO(_zip(match, output, records, constraints))) as package:
    package.extractall(folder)
   for value, expected in [('1',0), ('bad',1)]:
    (folder/'input.xml').write_text(f'<r xmlns="urn:main">{value}</r>', encoding='utf8')
    result = subprocess.run([sys.executable, 'validate.py', 'input.xml'], cwd=folder,
                            capture_output=True, text=True, encoding='utf8')
    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
  pack = dict(format=1, number='999', environment='test', revision_id=match['revision_id'], root='r',
              namespace='urn:main', entrypoint='message.xsd', fields=['r'], field_namespaces=[['urn:main']],
              files={name:base64.b64encode(raw).decode('ascii') for name, raw in output.items()
                     if not name.startswith('originals/')})
  script = Path(__file__).with_name('included_root_wasm.cjs')
  result = subprocess.run(['node', str(script)], input=json.dumps([pack]), text=True, capture_output=True, timeout=40)
  self.assertEqual(result.returncode, 0, result.stderr)

 def test_exact_wsdl_import_uses_fetched_path_for_identical_bytes(self):
  import posixpath
  import tempfile
  from pathlib import Path
  from collector_snapshots import make_manifest, validate_manifest
  from review_field_links import attach_schema_sources
  from schema import dependency_set
  from source_provenance import publish_sources
  from sync import write

  base = BASE.rsplit('/', 1)[0] + '/'
  base_schema = f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:common">'
  for nested in (False, True):
   with self.subTest(nested=nested):
    imports = ''.join(f'<xs:import namespace="urn:{v}" schemaLocation="{v}/root.xsd"/>' for v in ('v1','v2'))
    resources = {SEED: (f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}">'
                        f'<w:types><xs:schema>{imports}</xs:schema></w:types></w:definitions>').encode()}
    restriction = '<xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:length value="1"/></xs:restriction></xs:simpleType>'
    for version, length in [('v1','1'), ('v2','2')]:
     directory = base + version + '/'
     resources[directory+'root.xsd'] = (f'<xs:schema xmlns:xs="{XS}" xmlns:c="urn:common" targetNamespace="urn:{version}">'
        f'<xs:import namespace="urn:common" schemaLocation="{directory}Common.xsd"/>'
        '<xs:element name="response" type="c:Code"/></xs:schema>').encode()
     common_content = '<xs:include schemaLocation="types.xsd"/>' if nested else restriction
     resources[directory+'Common.xsd'] = (base_schema + common_content + '</xs:schema>').encode()
     if nested:
      resources[directory+'types.xsd'] = (base_schema + restriction.replace('value="1"', f'value="{length}"') + '</xs:schema>').encode()
    support, objects = self.collect(resources)
    raw = objects[support['sha256']]
    revisions = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
     common_paths = [name for name in archive.namelist() if name.endswith('/Common.xsd')]
     self.assertEqual(len(common_paths), 2)
     for version, path in zip(('v1','v2'), common_paths):
      entry = next(s['path'] for s in support['schemas'] if s['namespace'] == 'urn:' + version)
      dependencies = dependency_set(support['schemas'], entry)
      closure = {posixpath.normpath(posixpath.join(posixpath.dirname(entry), name)) for name, _ in dependencies}
      self.assertIn(path, closure)
      self.assertEqual(len(set(common_paths) & closure), 1)
      tree = Schema(archive, entry, 'response'); tree.field(tree.root)
      self.assertIn(['length', version[-1] if nested else '1'], tree.fields[0]['facets'])
      expected = next(s for s in support['source_files'] if s['url'] == base+version+'/Common.xsd')
      self.assertEqual(expected['archive_path'], path)
      if not nested:
       revision = dict(schema_path=entry, root='response', schema_sources=support['source_files'],
                       archive_sha256=support['sha256'], schema_source_type='wsdl',
                       archive_url=support['url'], status='matched', revision_id=version,
                       dependencies=dependencies, environment='test')
       finding = dict(kind='Ограничение длины', xlocation='Common.xsd · response', xsd='length = 1', field_paths=['response'])
       linked, = attach_schema_sources(revision, [finding], io.BytesIO(raw))
       self.assertEqual(linked['xsd_source'], dict(path=path, sha256=expected['sha256'],
                                   url=expected['url'], download='sources/'+expected['sha256']+'.xsd'))
       revisions.append((revision, linked['xsd_source']))
    if not nested:
     with tempfile.TemporaryDirectory() as tmp:
      folder = Path(tmp)
      state, site = folder/'state', folder/'site'
      write(state/'objects'/support['sha256'], raw)
      publish_sources({'revisions': [revision for revision, _ in revisions]}, state, site)
      for revision, reference in revisions:
       published, = [source for source in revision['schema_files'] if source['path'] == reference['path']]
       self.assertEqual(published['download'], reference['download'])
       self.assertEqual((site/reference['download']).read_bytes(), resources[reference['url']])
     def blob(data):
      objects[sha(data)] = data
      return sha(data)
     legacy = collect(SEED, 'test', resources.__getitem__, blob, legacy_wsdl_layout=True)
     self.assertNotEqual(legacy['sha256'], support['sha256'])
     manifest = make_manifest(legacy, '2026-11-16T00:00:00+00:00')
     self.assertEqual(validate_manifest(manifest, objects.__getitem__), legacy)

 def test_inline_include_entry_keeps_a_single_root_candidate_but_not_unrelated_contracts(self):
  from module_xsd import match_document
  from wsdl_sources import _archive
  base = BASE.rsplit('/', 1)[0] + '/'
  wsdl = (f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}"><w:types>'
          '<xs:schema><xs:include schemaLocation="part.xsd"/></xs:schema></w:types></w:definitions>').encode()
  part = f'<xs:schema xmlns:xs="{XS}"><xs:element name="request" type="xs:int"/></xs:schema>'.encode()
  support, objects = self.collect({SEED: wsdl, base+'part.xsd': part})
  document = dict(environment='test', module_bindings=[dict(number='999', root='request', namespace='')])
  matches, issues = match_document(document, [support], objects.__getitem__)
  self.assertEqual(len(matches), 1, issues)
  self.assertIn('inline-', matches[0]['schema_path'])

  other = _archive({'part.xsd': part.replace(b'xs:int', b'xs:string')})
  objects[sha(other)] = other
  alternative = dict(kind='zip', environment='test', url='https://example.test/other.zip',
                     sha256=sha(other), **inspect_file(other, 'zip'))
  matches, issues = match_document(document, [support, alternative], objects.__getitem__)
  self.assertEqual(matches, [])
  self.assertIn('Неоднозначные схемы', [issue['reason'] for issue in issues])

 def test_individual_docx_uses_complete_wsdl_entry_and_preserves_explicit_or_legacy_path(self):
  import copy
  from catalog import derive
  from collector_snapshots import make_manifest, validate_manifest
  from wsdl_sources import _archive
  base = BASE.rsplit('/', 1)[0] + '/'
  wsdl = (f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}"><w:types>'
          '<xs:schema><xs:include schemaLocation="part.xsd"/></xs:schema></w:types></w:definitions>').encode()
  part = f'<xs:schema xmlns:xs="{XS}"><xs:element name="request" type="xs:int"/></xs:schema>'.encode()
  resources = {SEED: wsdl, base+'part.xsd': part}
  support, objects = self.collect(resources)
  document = dict(kind='docx', environment='test', url='https://example.test/Тип_сообщения_999.docx',
                  sha256='a'*64, label='Example',
                  structure=[['Корневой элемент', 'request'], ['Пространство имен файла xml-схемы', '']])
  result = derive(dict(format=7, entries=[document, support]))
  row, = result['revisions']
  self.assertEqual(row['status'], 'matched')
  self.assertEqual(row['schema_path'], 'schemas/001/inline-0.xsd')
  self.assertEqual(result['review'], [])

  named = copy.deepcopy(document)
  named['structure'].append(['Расположение файла xml-схемы', 'part.xsd'])
  row, = derive(dict(format=7, entries=[named, support]))['revisions']
  self.assertEqual(row['schema_path'], 'schemas/002/part.xsd')

  independent = _archive({'another.xsd': part.replace(b'xs:int', b'xs:string')})
  alternative = dict(kind='zip', environment='test', url='https://example.test/other.zip',
                     sha256=sha(independent), **inspect_file(independent, 'zip'))
  row, = derive(dict(format=7, entries=[document, support, alternative]))['revisions']
  self.assertEqual(row['status'], 'review')

  def blob(raw):
   objects[sha(raw)] = raw
   return sha(raw)
  old = collect(SEED, 'test', resources.__getitem__, blob, legacy_inline_includes=True)
  manifest = make_manifest(old, '2026-11-16T00:00:00+00:00')
  self.assertEqual(validate_manifest(manifest, objects.__getitem__), old)
  for version in (6, 7):
   row, = derive(dict(format=version, entries=[document, old]))['revisions']
   self.assertEqual(row['status'], 'matched')
   self.assertEqual(row['schema_path'], 'schemas/002/part.xsd')

 def test_import_without_namespace_accepts_only_unnamespaced_schema(self):
  resources=self.bundle()
  main,common=BASE+"?xsd=main.xsd",BASE+"?xsd=Common.xsd"
  resources[main]=resources[main].replace(b'namespace="urn:common" ',b'')
  with self.assertRaisesRegex(ValueError,"namespace"):self.collect(resources)
  resources[common]=resources[common].replace(b' targetNamespace="urn:common"',b'')
  self.collect(resources)

 def test_override_is_rejected_for_wsdl_and_zip(self):
  from wsdl_sources import _archive
  for operator in ('override',):
   resources=self.bundle()
   root=('<xs:schema xmlns:xs="'+XS+'" targetNamespace="urn:main"><xs:'+operator+' schemaLocation="Common.xsd"/><xs:element name="response" type="xs:string"/></xs:schema>').encode()
   resources[BASE+'?xsd=main.xsd']=root
   with self.subTest('wsdl'), self.assertRaisesRegex(ValueError,operator):self.collect(resources)
   with self.subTest('zip'), self.assertRaisesRegex(ValueError,operator):inspect_file(_archive({'root.xsd':root}), 'zip')

 def test_redefine_is_rejected_before_confirmation_for_wsdl_and_zip(self):
  from wsdl_sources import _archive
  resources=self.bundle()
  main=BASE+"?xsd=main.xsd"
  root=('<xs:schema xmlns:xs="'+XS+'" targetNamespace="urn:main"><xs:redefine schemaLocation="Common.xsd"/></xs:schema>').encode()
  resources[main]=root
  with self.subTest("wsdl"), self.assertRaisesRegex(ValueError,"redefine"):self.collect(resources)
  with self.subTest("zip"), self.assertRaisesRegex(ValueError,"redefine"):inspect_file(_archive({"root.xsd":root,"Common.xsd":resources[BASE+"?xsd=Common.xsd"]}),"zip")

 def branch_resources(self):
  base=BASE.rsplit("/",1)[0]+"/"
  resources={SEED:(f'<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="{XS}"><w:types><xs:schema>'+
                   ''.join(f'<xs:import namespace="urn:{v}" schemaLocation="{v}/root.xsd"/>' for v in ("v1","v2"))+
                   '</xs:schema></w:types></w:definitions>').encode()}
  for v in ("v1","v2"):
   resources[base+v+"/root.xsd"]=f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:{v}" xmlns:t="urn:{v}"><xs:include schemaLocation="Common.xsd"/><xs:element name="response" type="t:Code"/></xs:schema>'.encode()
   resources[base+v+"/Common.xsd"]=f'<xs:schema xmlns:xs="{XS}"><xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:enumeration value="{v}"/></xs:restriction></xs:simpleType></xs:schema>'.encode()
  return base,resources

 def test_exact_absolute_and_root_relative_includes_preserve_fetched_edges(self):
  from urllib.parse import urlsplit
  for mode in ("absolute","root-relative","mixed"):
   with self.subTest(mode=mode):
    base,resources=self.branch_resources()
    for v in ("v1","v2"):
     url=base+v+"/root.xsd"
     location=base+v+"/Common.xsd"
     if mode=="root-relative":location=urlsplit(location).path
     if mode=="mixed" and v=="v1":location="Common.xsd"
     resources[url]=resources[url].replace(b'"Common.xsd"',('"'+location+'"').encode())
    from collector_snapshots import make_manifest, validate_manifest
    from schema import dependency_set
    from xsd_bundle import _selected, _prepare
    row,objects=self.collect(resources)
    self.assertEqual(validate_manifest(make_manifest(row,'2026-11-17T00:00:00+00:00'),objects.__getitem__),row)
    with zipfile.ZipFile(io.BytesIO(objects[row['sha256']])) as archive:
     for v in ('v1','v2'):
      entry=next(s['path'] for s in row['schemas'] if s['namespace']=='urn:'+v)
      tree=Schema(archive,entry,'response');tree.field(tree.root)
      self.assertEqual(tree.fields[0]['values'],[[v,'']])
      selected=_selected(dict(schema_path=entry,dependencies=dependency_set(row['schemas'],entry)),archive)
      self.assertEqual(set(selected.values()),{resources[base+v+'/root.xsd'],resources[base+v+'/Common.xsd']})
      _prepare(dict(schema_path=entry,root='response',namespace='urn:'+v),selected)

 def test_exact_import_targets_survive_document_bridge(self):
  from schema import dependency_set
  from wsdl_sources import bridge, _archive
  from xsd_bundle import _selected, _prepare
  base,resources=self.branch_resources()
  for v in ('v1','v2'):
   resources[base+v+'/root.xsd']=resources[base+v+'/root.xsd'].replace(b'"Common.xsd"',('"'+base+v+'/Common.xsd"').encode())
  support,objects=self.collect(resources)
  original=_archive({'main.xsd':f'<xs:schema xmlns:xs="{XS}" xmlns:v="urn:v1" targetNamespace="urn:doc"><xs:import namespace="urn:v1" schemaLocation="{base}v1/root.xsd"/><xs:element name="request"><xs:complexType><xs:sequence><xs:element ref="v:response"/></xs:sequence></xs:complexType></xs:element></xs:schema>'.encode()})
  objects[sha(original)]=original
  document=dict(environment='test',url='https://example.test/doc.docx',embedded_archives=[dict(sha256=sha(original))])
  result=bridge(document,support,objects.__getitem__)[0]
  row=dict(schema_path='document/main.xsd',root='request',namespace='urn:doc',dependencies=dependency_set(result['schemas'],'document/main.xsd'))
  with zipfile.ZipFile(io.BytesIO(result['content'])) as archive:
   tree=Schema(archive,row['schema_path'],row['root']);tree.field(tree.root)
   self.assertEqual(tree.fields[1]['values'],[['v1','']])
   output,_,_=_prepare(row,_selected(row,archive))
  self.assertTrue(output['message.xsd'])

 def test_missing_exact_target_cannot_fall_back_to_another_common_schema(self):
  from schema import dependency_set
  base,resources=self.branch_resources()
  for v in ('v1','v2'):
   resources[base+v+'/root.xsd']=resources[base+v+'/root.xsd'].replace(b'"Common.xsd"',('"'+base+v+'/Common.xsd"').encode())
  row,_=self.collect(resources)
  entry=next(item for item in row['schemas'] if item['namespace']=='urn:v1')
  target=next(iter(entry['import_targets'].values()))
  with self.assertRaisesRegex(ValueError,'точный импорт'):
   dependency_set([item for item in row['schemas'] if item['path']!=target],entry['path'])

 def test_legacy_manifest_keeps_direct_roots_and_archive_bytes(self):
  from collector_snapshots import make_manifest, validate_manifest
  base,resources=self.branch_resources()
  for v in ('v1','v2'):
   resources[base+v+'/root.xsd']=resources[base+v+'/root.xsd'].replace(b'<xs:element name="response" type="t:Code"/>',b'')
   resources[base+v+'/Common.xsd']=resources[base+v+'/Common.xsd'].replace(b'</xs:schema>',b'<xs:element name="response" type="Code"/></xs:schema>')
  objects={}
  def blob(raw):objects[sha(raw)]=raw;return sha(raw)
  old=collect(SEED,'test',resources.__getitem__,blob,legacy_annotations=True,legacy_schema_roots=True)
  original=objects[old['sha256']]
  self.assertEqual([s['roots'] for s in old['schemas'] if s['namespace']], [[],[]])
  self.assertEqual(validate_manifest(make_manifest(old,'2026-11-17T00:00:00+00:00'),objects.__getitem__),old)
  self.assertEqual(objects[old['sha256']],original)

 def test_relative_includes_preserve_original_context_in_independent_subdirectories(self):
  from schema import dependency_set
  from xsd_bundle import _selected
  base,resources=self.branch_resources()
  row,objects=self.collect(resources)
  with zipfile.ZipFile(io.BytesIO(objects[row["sha256"]])) as archive:
   for v in ("v1","v2"):
    entry=next(s["path"] for s in row["schemas"] if s["namespace"]=="urn:"+v)
    tree=Schema(archive,entry,"response");tree.field(tree.root)
    self.assertEqual(tree.fields[0]["values"][0][0],v)
    files=_selected({"schema_path":entry,"dependencies":dependency_set(row["schemas"],entry)},archive)
    self.assertEqual(set(files.values()),{resources[base+v+"/root.xsd"],resources[base+v+"/Common.xsd"]})
  # Different explicit namespaces let the legacy bundles resolve these same
  # basenames unambiguously, so retaining their archive identity is possible.
  for v in ("v1","v2"):
   url=base+v+"/Common.xsd"
   resources[url]=resources[url].replace(b"<xs:schema ",f'<xs:schema targetNamespace="urn:{v}" '.encode())
  row,_=self.collect(resources)
  self.assertEqual([s["path"] for s in row["schemas"]],
                   ["schemas/002/root.xsd","schemas/003/root.xsd","schemas/004/Common.xsd","schemas/005/Common.xsd"])

 def test_bridge_restores_same_namespace_include_and_prefers_document_copy(self):
  from schema import dependency_set
  from wsdl_sources import _archive
  main=f'<xs:schema xmlns:xs="{XS}" xmlns:m="urn:main" targetNamespace="urn:main"><xs:include schemaLocation="types.xsd"/><xs:element name="response" type="m:Code"/></xs:schema>'.encode()
  types=f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:simpleType name="Code"><xs:restriction base="xs:string"/></xs:simpleType></xs:schema>'.encode()
  for local in (False,True):
   own={"root.xsd":main}
   if local:own["types.xsd"]=types
   support_raw=_archive({"root.xsd":main,"types.xsd":types.replace(b"xs:string",b"xs:int")})
   own_raw=_archive(own)
   objects={sha(own_raw):own_raw,sha(support_raw):support_raw}
   doc=dict(environment="test",url="https://example.test/doc.docx",embedded_archives=[dict(sha256=sha(own_raw))])
   support=dict(environment="test",sha256=sha(support_raw),source_url=SEED,source_files=[])
   result=bridge(doc,support,objects.__getitem__)[0]
   entry=next(s for s in result["schemas"] if s["path"]=="document/root.xsd")
   self.assertEqual(len(dependency_set(result["schemas"],entry["path"])),2)
   with zipfile.ZipFile(io.BytesIO(result["content"])) as archive:
    tree=Schema(archive,entry["path"],"response");tree.field(tree.root)
    self.assertEqual(tree.fields[0]["base"],"xs:string" if local else "xs:int")

 def test_bridge_does_not_choose_between_conflicting_include_candidates(self):
  from schema import dependency_set
  from wsdl_sources import _archive
  main=f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"><xs:include schemaLocation="types.xsd"/><xs:element name="response" type="xs:string"/></xs:schema>'.encode()
  a=f'<xs:schema xmlns:xs="{XS}" targetNamespace="urn:main"/>'.encode()
  own_raw=_archive({"root.xsd":main})
  support_raw=_archive({"a/types.xsd":a,"b/types.xsd":a.replace(b"/>",b"> </xs:schema>")})
  objects={sha(own_raw):own_raw,sha(support_raw):support_raw}
  doc=dict(environment="test",url="https://example.test/doc.docx",embedded_archives=[dict(sha256=sha(own_raw))])
  support=dict(environment="test",sha256=sha(support_raw),source_url=SEED,source_files=[])
  result=bridge(doc,support,objects.__getitem__)[0]
  with self.assertRaisesRegex(ValueError,"Неоднозначный импорт"):dependency_set(result["schemas"],"document/root.xsd")

 def bundle(self,body=None):
  return {SEED:('<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="'+XS+'"><w:types><xs:schema><xs:import namespace="urn:main" schemaLocation="'+BASE+'?xsd=main.xsd"/></xs:schema></w:types></w:definitions>').encode(),BASE+"?xsd=main.xsd":body or ('<xs:schema xmlns:xs="'+XS+'" xmlns:c="urn:common" targetNamespace="urn:main"><xs:import namespace="urn:common" schemaLocation="'+BASE+'?xsd=Common.xsd"/><xs:element name="response" type="c:Code"/></xs:schema>').encode(),BASE+"?xsd=Common.xsd":('<xs:schema xmlns:xs="'+XS+'" targetNamespace="urn:common"><xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:length value="10"/></xs:restriction></xs:simpleType></xs:schema>').encode()}
 def collect(self,resources):
  objects={}
  def blob(data):objects[sha(data)]=data;return sha(data)
  row=collect(SEED,"test",resources.__getitem__,blob)
  return row,objects
 def test_discovery_keeps_contours_and_prefers_ecp_simple(self):
  entries=[dict(environment="prod",url=SEED),dict(environment="test",url=SEED),dict(environment="test",url="https://docs-test.fss.ru/sedo-gateway/api/soap/SedoGatewaySimple?wsdl"),dict(environment="test",url="https://evil.test/gateway?wsdl")]
  self.assertEqual(discover(entries,"prod"),[])
  self.assertEqual(discover(entries,"test")[0],SEED)
  self.assertEqual(len(discover(entries,"test")),2)
 def test_recursive_bundle_preserves_bytes_and_is_deterministic(self):
  resources=self.bundle();row,objects=self.collect(resources);again,_=self.collect(resources)
  self.assertEqual(row,again);self.assertEqual(row["environment"],"test")
  self.assertEqual(len(row["source_files"]),3)
  with zipfile.ZipFile(io.BytesIO(objects[row["sha256"]])) as z:
   tree=Schema(z,next(s["path"] for s in row["schemas"] if s["namespace"]=="urn:main"),"response");tree.field(tree.root)
   self.assertIn(["length","10"],tree.fields[0]["facets"])
   self.assertIn(resources[BASE+"?xsd=Common.xsd"],[z.read(n) for n in z.namelist()])
  for source in row["source_files"]:self.assertEqual(objects[source["sha256"]],resources[source["url"]])
 def test_cross_host_and_cross_endpoint_dependencies_rejected(self):
  for target in ["https://evil.test/Common.xsd","file:///Common.xsd","http://ecp-test.sfr.gov.ru/a.xsd",BASE+"/../../admin/Common.xsd"]:
   with self.subTest(target=target):
    resources=self.bundle();resources[BASE+"?xsd=main.xsd"]=resources[BASE+"?xsd=main.xsd"].replace((BASE+"?xsd=Common.xsd").encode(),target.encode())
    with self.assertRaises(ValueError):self.collect(resources)
 def test_namespace_mismatch_and_oversized_source_rejected(self):
  resources=self.bundle();resources[BASE+"?xsd=Common.xsd"]=resources[BASE+"?xsd=Common.xsd"].replace(b'urn:common',b'urn:wrong')
  with self.assertRaisesRegex(ValueError,"namespace"):self.collect(resources)
  resources=self.bundle();resources[SEED]=b" "*(2*1024*1024+1)
  with self.assertRaises(ValueError):self.collect(resources)
 def test_namespaced_include_through_query_url(self):
  resources=self.bundle()
  resources[BASE+"?xsd=main.xsd"]=('<xs:schema xmlns:xs="'+XS+'" xmlns:m="urn:main" targetNamespace="urn:main"><xs:include schemaLocation="'+BASE+'?xsd=Common.xsd"/><xs:element name="response" type="m:Code"/></xs:schema>').encode()
  resources[BASE+"?xsd=Common.xsd"]=resources[BASE+"?xsd=Common.xsd"].replace(b'urn:common',b'urn:main')
  row,objects=self.collect(resources)
  from schema import dependency_set
  entry=next(s["path"] for s in row["schemas"] if s["path"].endswith("main.xsd"))
  self.assertEqual(len(dependency_set(row["schemas"],entry)),2)
  with zipfile.ZipFile(io.BytesIO(objects[row["sha256"]])) as z:
   tree=Schema(z,entry,"response");tree.field(tree.root);self.assertIn(["length","10"],tree.fields[0]["facets"])
 def test_inline_schema_retains_inherited_qname_prefixes(self):
  resources=self.bundle()
  resources[SEED]=('<w:definitions xmlns:w="http://schemas.xmlsoap.org/wsdl/" xmlns:xs="'+XS+'" xmlns:m="urn:main"><w:types><xs:schema targetNamespace="urn:main"><xs:simpleType name="Code"><xs:restriction base="xs:string"><xs:length value="3"/></xs:restriction></xs:simpleType><xs:element name="root" type="m:Code"/></xs:schema></w:types></w:definitions>').encode()
  row,objects=self.collect(resources)
  with zipfile.ZipFile(io.BytesIO(objects[row["sha256"]])) as z:
   tree=Schema(z,z.namelist()[0],"root");tree.field(tree.root);self.assertIn(["length","3"],tree.fields[0]["facets"])
 def test_identical_choice_leaves_merge_only_alternative_contexts(self):
  xml=('<xs:schema xmlns:xs="'+XS+'"><xs:element name="root"><xs:complexType><xs:choice><xs:sequence><xs:element name="inn" type="xs:string"/><xs:element name="kpp" type="xs:string"/></xs:sequence><xs:sequence><xs:element name="inn" type="xs:string"/><xs:element name="snils" type="xs:string"/></xs:sequence></xs:choice></xs:complexType></xs:element></xs:schema>').encode()
  out=io.BytesIO()
  with zipfile.ZipFile(out,"w") as z:z.writestr("main.xsd",xml)
  with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z:
   tree=Schema(z,"main.xsd","root");tree.field(tree.root)
  self.assertEqual(len(tree.fields),4)
  inn=next(f for f in tree.fields if f["name"]=="inn");self.assertIn("ИЛИ",inn["group"])
  self.assertEqual(tree.fields[0]["children"],[1,2,3])
  self.assertTrue(all(f["parent"]==0 for f in tree.fields[1:]))
 def test_different_choice_constraints_are_not_silently_merged(self):
  xml=('<xs:schema xmlns:xs="'+XS+'"><xs:element name="root"><xs:complexType><xs:choice><xs:element name="code" type="xs:string"/><xs:element name="code" type="xs:int"/></xs:choice></xs:complexType></xs:element></xs:schema>').encode()
  out=io.BytesIO()
  with zipfile.ZipFile(out,"w") as z:z.writestr("main.xsd",xml)
  with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z:
   tree=Schema(z,"main.xsd","root");tree.field(tree.root)
  self.assertEqual(len(tree.fields),3)
 def test_sync_reuses_same_contour_library_on_outage(self):
  import json,tempfile
  from pathlib import Path
  from unittest.mock import patch
  from sync import sync
  resources=self.bundle()
  zipped=io.BytesIO()
  with zipfile.ZipFile(zipped,"w") as z:z.writestr("main.xsd",resources[BASE+"?xsd=main.xsd"])
  for host in ["https://lk.sfr.gov.ru","https://lk-test.fss.ru"]:
   resources[host+"/sedo.html"]=(f'<a href="{SEED}">WSDL</a><a href="/specs_sedo/schemas.zip">XSD</a>').encode()
   resources[host+"/specs_sedo/schemas.zip"]=zipped.getvalue()
  document=io.BytesIO()
  with zipfile.ZipFile(document,"w") as z:
   z.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Тип сообщения 999: Fixture</w:t></w:r></w:p><w:p><w:r><w:t>&lt;m:response xmlns:m="urn:main"&gt;&lt;/m:response&gt;</w:t></w:r></w:p></w:body></w:document>')
  resources["https://lk-test.fss.ru/specs_sedo/fixture.docx"]=document.getvalue()
  resources["https://lk-test.fss.ru/sedo.html"]+=b'<a href="/specs_sedo/fixture.docx">Fixture</a>' 
  unavailable=False
  def fetch(url,**kwargs):
   if unavailable and url==SEED:raise OSError("temporary service outage")
   return resources[url]
  with tempfile.TemporaryDirectory() as tmp,patch("sync.get",side_effect=fetch):
   state=Path(tmp);sync(state)
   def snapshot():
    key=json.loads((state/"current.json").read_bytes())["snapshot"]
    return json.loads((state/"snapshots"/(key+".json")).read_bytes())
   first=next(r for r in snapshot()["entries"] if r.get("source_type")=="wsdl")
   self.assertTrue(any(r.get("module_matches") for r in snapshot()["entries"]))
   sync(state)
   self.assertFalse(any(json.loads((state/"last-run.json").read_bytes())["diff"].values()))
   unavailable=True;sync(state)
   cached=next(r for r in snapshot()["entries"] if r.get("source_type")=="wsdl")
   self.assertEqual(cached["environment"],"test");self.assertEqual(cached["sha256"],first["sha256"])
   self.assertEqual(cached["retrieval_status"],"cached")
   self.assertTrue(any(r["kind"]=="wsdl_status" for r in snapshot()["entries"]))
   self.assertFalse(json.loads((state/"last-run.json").read_bytes())["errors"])
 def test_archived_service_trees_retain_sources_and_correct_new_type(self):
  import json
  from pathlib import Path
  root=Path(__file__).resolve().parents[1]
  catalog=json.loads((root/"data/catalog.json").read_bytes())
  numbers={"1","2","6","9","11","14","27","28","90","91","330","331","332","1000","1001","1002"}
  selected=[r for r in catalog["revisions"] if r["number"] in numbers and r.get("schema_source_type")]
  self.assertEqual({r["number"] for r in selected},numbers)
  self.assertEqual({(r["environment"],r["number"]) for r in selected},{(env,n) for env in ("prod","test") for n in numbers})
  self.assertTrue(ARCHIVED_91_IDS<={r["revision_id"] for r in selected})
  for revision in selected:
   self.assert_service_tree(revision,root)
 def assert_service_tree(self,revision,root):
  import json
  fields=json.loads((root/"data/fields"/(revision["revision_id"]+".json")).read_bytes())["fields"]
  self.assertEqual(len(fields),len({f["path"] for f in fields}))
  self.assertTrue(all(fields[child]["parent"]==i for i,f in enumerate(fields) for child in f["children"]))
  for source in revision["schema_sources"]:self.assertEqual(sha((root/"state/objects"/source["sha256"]).read_bytes()),source["sha256"])
  if revision["revision_id"] in ARCHIVED_91_IDS:
   codes=[f for f in fields if f["type"]=="org:RegNumSfr"]
   self.assertEqual(len(codes),2)
   self.assertTrue(all(f["facets"]==[["length","10"]] for f in codes))
 def test_service_tree_accepts_new_91_contract_but_protects_archive_and_integrity(self):
  import json,tempfile
  from pathlib import Path
  fields=[dict(path="root",type="",parent=None,children=[1,2]),
          dict(path="root/a",type="org:RegNumSfr",facets=[["length","11"]],parent=0,children=[]),
          dict(path="root/b",type="org:RegNumSfr",facets=[["length","11"]],parent=0,children=[])]
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/"data/fields").mkdir(parents=True);(root/"state/objects").mkdir(parents=True)
   content=b"fixture xsd";digest=sha(content);(root/"state/objects"/digest).write_bytes(content)
   revision=dict(number="91",revision_id="future-edition",schema_sources=[dict(sha256=digest)])
   def write(): (root/"data/fields"/(revision["revision_id"]+".json")).write_text(json.dumps(dict(fields=fields)))
   write();self.assert_service_tree(revision,root)
   revision["revision_id"]=next(iter(ARCHIVED_91_IDS));write()
   with self.assertRaises(AssertionError):self.assert_service_tree(revision,root)
   revision["revision_id"]="future-edition";fields[1]["parent"]=2;write()
   with self.assertRaises(AssertionError):self.assert_service_tree(revision,root)
   fields[1]["parent"]=0;fields[2]["path"]=fields[1]["path"];write()
   with self.assertRaises(AssertionError):self.assert_service_tree(revision,root)
   fields[2]["path"]="root/b";write();(root/"state/objects"/digest).write_bytes(b"damaged source")
   with self.assertRaises(AssertionError):self.assert_service_tree(revision,root)
 def test_bridge_preserves_module_and_never_mixes_contours(self):
  support,objects=self.collect(self.bundle());out=io.BytesIO()
  arm=('<xs:schema xmlns:xs="'+XS+'" xmlns:c="urn:common" targetNamespace="urn:arm"><xs:import namespace="urn:common" schemaLocation="../type/Common.xsd"/><xs:element name="arm" type="c:Code"/></xs:schema>').encode()
  with zipfile.ZipFile(out,"w") as z:z.writestr("SedoArm.xsd",arm)
  raw=out.getvalue();objects[sha(raw)]=raw
  doc=dict(environment="test",url="https://lk-test.fss.ru/spec.docx",embedded_archives=[dict(sha256=sha(raw),**inspect_file(raw,"zip"))])
  derived=bridge(doc,support,objects.__getitem__)
  self.assertEqual(len(derived),1)
  content=derived[0].pop("content")
  with zipfile.ZipFile(io.BytesIO(content)) as z:
   entry=next(n for n in z.namelist() if n.endswith("SedoArm.xsd"));self.assertEqual(z.read(entry),arm)
   tree=Schema(z,entry,"arm");tree.field(tree.root);self.assertIn(["length","10"],tree.fields[0]["facets"])
  self.assertEqual(bridge(dict(doc,environment="prod"),support,objects.__getitem__),[])

if __name__=="__main__":unittest.main()
