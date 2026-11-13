(function(scope){
"use strict";
const entity=text=>text.replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos);/gi,(_,key)=>key[0]==='#'?String.fromCodePoint(key[1].toLowerCase()==='x'?parseInt(key.slice(2),16):Number(key.slice(1))):({amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"})[key]);
// This index is auxiliary. libxml2 has already accepted the XML syntax. It never
// expands entities, loads resources, or copies text/attribute values into paths.
function index(source){
 try{
  const text=source.replace(/\r\n?/g,'\n'),nodes=[],stack=[],base=Object.assign(Object.create(null),{xml:'http://www.w3.org/XML/1998/namespace'});let pos=0,line=1;
  const advance=end=>{for(;pos<end;pos++)if(text.charCodeAt(pos)===10)line++};
  while(pos<text.length){
   const at=text.indexOf('<',pos);if(at<0)break;advance(at);const startLine=line;
   let delimiter=null,skip=0;
   if(text.startsWith('<!--',at)){delimiter='-->';skip=4}
   else if(text.startsWith('<![CDATA[',at)){delimiter=']]>';skip=9}
   else if(text.startsWith('<?',at)){delimiter='?>';skip=2}
   if(delimiter){const end=text.indexOf(delimiter,at+skip);if(end<0)return null;advance(end+delimiter.length);continue}
   if(text.startsWith('<!',at))return null;
   let end=at+1,quote='';for(;end<text.length;end++){const c=text[end];if(quote){if(c===quote)quote=''}else if(c==='"'||c==="'")quote=c;else if(c==='>')break}
   if(end===text.length)return null;
   const raw=text.slice(at+1,end);
   if(raw[0]==='/'){
    const node=stack.pop();if(!node||raw.slice(1).trim()!==node.qname)return null;advance(end+1);node.end=line;node.closeStart=at+2;continue;
   }
   const name=raw.match(/^([^\s/>]+)/)?.[1];if(!name)return null;
   const parent=stack.at(-1)||null,ns=Object.create(parent?.namespaces||base);
   for(const a of raw.slice(name.length).matchAll(/([^\s=]+)\s*=\s*(['"])([\s\S]*?)\2/g)){
    if(a[1]==='xmlns')ns['']=entity(a[3]);else if(a[1].startsWith('xmlns:'))ns[a[1].slice(6)]=entity(a[3]);
   }
   const colon=name.indexOf(':'),prefix=colon<0?'':name.slice(0,colon),local=colon<0?name:name.slice(colon+1);
   const node={qname:name,name:local,ns:ns[prefix]||'',namespaces:ns,parent,line:startLine,end:startLine,nameStart:at+1,nameEnd:at+1+name.length,openEnd:end+1,closeStart:null};nodes.push(node);
   if(nodes.length>100000||stack.length>=256)return null;
   advance(end+1);node.end=line;if(!/\/\s*$/.test(raw))stack.push(node);
  }
  nodes.source=text;return stack.length?null:nodes;
 }catch{return null}
}
function expanded(qname){if(qname.startsWith('{')){const at=qname.lastIndexOf('}');return {name:qname.slice(at+1),ns:qname.slice(1,at)}}return {name:qname,ns:''}}
function route(node){const parts=[];let length=0;for(let n=node;n;n=n.parent){length+=n.name.length+1;if(length>8192)return null;parts.push(n.name)}return parts.reverse().join('/')}
function sourceRanges(node,attribute,source){
 const result=[{start:node.nameStart,end:node.nameEnd,kind:'tag'}];
 if(node.closeStart!==null)result.push({start:node.closeStart,end:node.closeStart+node.qname.length,kind:'tag'});
 if(attribute&&node.openEnd-node.nameEnd<=65536){
  const wanted=expanded(attribute),prefix=attribute.includes(':')&&!attribute.startsWith('{')?attribute.split(':')[0]:null;
  if(prefix){wanted.name=attribute.split(':').at(-1);wanted.ns=node.namespaces[prefix]||'\u0000'}
  let count=0;for(const a of source.slice(node.nameEnd,node.openEnd-1).matchAll(/([^\s=]+)\s*=\s*(['"])([\s\S]*?)\2/g)){
   if(++count>512)break;
   const parts=a[1].split(':'),name=parts.at(-1),ns=parts.length>1?node.namespaces[parts[0]]||'\u0000':'';
   if(name===wanted.name&&ns===wanted.ns){const start=node.nameEnd+a.index;result.unshift({start,end:start+a[1].length,kind:'attribute'});break}
  }
 }
 return result;
}
function locate(errors,nodes,fields=[],namespaces=[]){
 const known=new Map(fields.map((path,i)=>[path,i]));
 return errors.map(error=>{
  const result={...error,locations:[],ambiguous:false,truncated:false,sourceAmbiguous:false};if(!nodes?.length)return result;
  const subject=error.message.match(/^(?:(?:[^\n]*?:\s*)?Schemas validity error\s*:\s*)?Element '([^']+)'(?:, attribute '([^']+)')?:\s*([\s\S]*)$/);
  const element=subject?.[1],body=subject?.[3]||'',attribute=subject?.[2]||body.match(/^The attribute '([^']+)' is required but missing\.$/)?.[1];
  let candidates=[];
  if(error.root)candidates=[nodes[0]];
  else if(element&&error.line){
   const wanted=expanded(element),same=n=>element.includes(':')&&!element.startsWith('{')?n.qname===element:n.name===wanted.name&&n.ns===wanted.ns;
   candidates=nodes.filter(n=>n.line<=error.line&&n.end>=error.line&&same(n));
  }
  const seen=new Set(),byPath=new Map();
  for(const node of candidates){
   const nodePath=route(node);if(nodePath===null)continue;
   const attr=attribute?expanded(attribute):null;
   if(attr&&attribute.includes(':')&&!attribute.startsWith('{')){const parts=attribute.split(':');attr.name=parts.at(-1);attr.ns=node.namespaces[parts[0]]||'\u0000'}
   const path=nodePath+(attr?'/@'+attr.name.split(':').at(-1):'');if(path.length>8192)continue;
   if(byPath.has(path)){const old=byPath.get(path);old.occurrences++;if(old.ranges.length<24){const extra=sourceRanges(node,attribute,nodes.source);if(extra.length>24-old.ranges.length)old.rangesTruncated=true;old.ranges.push(...extra.slice(0,24-old.ranges.length))}else old.rangesTruncated=true;continue}
   seen.add(path);
   if(result.locations.length>=8){result.truncated=true;break}
   const matches=candidate=>{
    for(let n=candidate;n;n=n.parent){const key=route(n),i=known.get(key);if(i===undefined||!namespaces[i]?.includes(n.ns))return false}return true;
   };
   let target='';
   if(!error.root){
    if(attr&&known.has(path)&&matches(node)&&namespaces[known.get(path)]?.includes(attr.ns))target=path;
    for(let n=node;!target&&n;n=n.parent){const key=route(n);if(matches(n))target=key}
   }

   const location={path:'/'+path,field:target?known.get(target):null,parent:!!target&&target!==path,ranges:sourceRanges(node,attribute,nodes.source),occurrences:1,rangesTruncated:false};
   result.locations.push(location);byPath.set(path,location);
  }
  result.ambiguous=seen.size>1;result.sourceAmbiguous=result.ambiguous||result.locations.some(l=>l.occurrences>1);return result;
 });
}
const api={index,locate};scope.SedoValidationPaths=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof self==='object'?self:globalThis);
