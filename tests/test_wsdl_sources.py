import io
import unittest
import zipfile
from wsdl_sources import discover,collect,bridge
from sync import inspect_file,sha
from schema import Schema

SEED="https://ecp-test.sfr.gov.ru/sedo/fss/api/soap/SedoGatewaySimple?wsdl"
BASE=SEED.split("?")[0]
XS="http://www.w3.org/2001/XMLSchema"

class WsdlSourcesTests(unittest.TestCase):
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
  for revision in selected:
   fields=json.loads((root/"data/fields"/(revision["revision_id"]+".json")).read_bytes())["fields"]
   self.assertEqual(len(fields),len({f["path"] for f in fields}))
   self.assertTrue(all(fields[child]["parent"]==i for i,f in enumerate(fields) for child in f["children"]))
   for source in revision["schema_sources"]:self.assertEqual(sha((root/"state/objects"/source["sha256"]).read_bytes()),source["sha256"])
   if revision["number"]=="91":
    codes=[f for f in fields if f["type"]=="org:RegNumSfr"]
    self.assertEqual(len(codes),2)
    self.assertTrue(all(f["facets"]==[["length","10"]] for f in codes))
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
