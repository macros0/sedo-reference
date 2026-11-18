'use strict';
// Both child elements appear on the same line. Only the one under the declared
// parent is validated, but libxml reports just its QName and line number.
function namespaceCandidates(revision){
 const root=revision.root,ns=revision.namespace,parent='benefit1',child='receivePayment';
 const schema='<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="'+ns+'" elementFormDefault="qualified">'+
  '<xs:element name="'+root+'"><xs:complexType><xs:choice maxOccurs="unbounded">'+
  '<xs:element name="'+parent+'"><xs:complexType><xs:sequence><xs:element name="'+child+'" type="xs:int"/></xs:sequence></xs:complexType></xs:element>'+
  '<xs:any namespace="urn:other" processContents="skip"/></xs:choice></xs:complexType></xs:element></xs:schema>';
 const fields=[root,root+'/'+parent,root+'/'+parent+'/'+child];
 const pack={format:1,number:revision.number,environment:revision.environment,revision_id:revision.revision_id,root,namespace:ns,
  fields,field_namespaces:fields.map(()=>[ns]),entrypoint:'message.xsd',files:{'message.xsd':Buffer.from(schema).toString('base64')}};
 const checked='<'+parent+'><'+child+'>wrong</'+child+'></'+parent+'>',foreign='<w:'+parent+'><'+child+'>7</'+child+'></w:'+parent+'>';
 return {pack,examples:[foreign+checked,checked+foreign].map(inner=>'<'+root+' xmlns="'+ns+'" xmlns:w="urn:other">'+inner+'</'+root+'>')};
}
module.exports={namespaceCandidates};
