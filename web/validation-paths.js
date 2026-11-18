(function(scope){
"use strict";
const entity=text=>text.replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos);/gi,(_,key)=>key[0]==='#'?String.fromCodePoint(key[1].toLowerCase()==='x'?parseInt(key.slice(2),16):Number(key.slice(1))):({amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"})[key]);
// This index is auxiliary. libxml2 has already accepted the XML syntax. It never
// expands entities, loads resources, or copies text/attribute values into paths.
function* scan(source){
  const text=source.replace(/\r\n?/g,'\n'),stack=[],base=Object.assign(Object.create(null),{xml:'http://www.w3.org/XML/1998/namespace'});let pos=0,line=1;
  const advance=end=>{for(;pos<end;pos++)if(text.charCodeAt(pos)===10)line++};
  while(pos<text.length){
   const at=text.indexOf('<',pos);if(at<0)break;advance(at);const startLine=line;
   let delimiter=null,skip=0;
   if(text.startsWith('<!--',at)){delimiter='-->';skip=4}
   else if(text.startsWith('<![CDATA[',at)){delimiter=']]>';skip=9}
   else if(text.startsWith('<?',at)){delimiter='?>';skip=2}
   if(delimiter){const end=text.indexOf(delimiter,at+skip);if(end<0)throw Error('Незаконченная XML-конструкция.');advance(end+delimiter.length);continue}
   if(text.startsWith('<!',at))throw Error('Неподдерживаемая XML-декларация.');
   let end=at+1,quote='';for(;end<text.length;end++){const c=text[end];if(quote){if(c===quote)quote=''}else if(c==='"'||c==="'")quote=c;else if(c==='>')break}
   if(end===text.length)throw Error('Незаконченный XML-тег.');
   const raw=text.slice(at+1,end);
   if(raw[0]==='/'){
    const node=stack.pop();if(!node||raw.slice(1).trim()!==node.qname)throw Error('Несогласованные XML-теги.');advance(end+1);node.end=line;node.closeStart=at+2;continue;
   }
   const name=raw.match(/^([^\s/>]+)/)?.[1];if(!name)throw Error('Отсутствует имя XML-тега.');
   const parent=stack.at(-1)||null,ns=Object.create(parent?.namespaces||base);
   for(const a of raw.slice(name.length).matchAll(/([^\s=]+)\s*=\s*(['"])([\s\S]*?)\2/g)){
    if(a[1]==='xmlns')ns['']=entity(a[3]);else if(a[1].startsWith('xmlns:'))ns[a[1].slice(6)]=entity(a[3]);
   }
   const colon=name.indexOf(':'),prefix=colon<0?'':name.slice(0,colon),local=colon<0?name:name.slice(colon+1);
   const node={qname:name,name:local,ns:ns[prefix]||'',namespaces:ns,parent,line:startLine,end:startLine,nameStart:at+1,nameEnd:at+1+name.length,openEnd:end+1,closeStart:null};
   advance(end+1);node.end=line;if(!/\/\s*$/.test(raw))stack.push(node);
   yield node;
  }
  if(stack.length)throw Error('Незакрытый XML-тег.');
}
// Navigation may omit highlights once its budget is exhausted. Attribute
// validation uses scan directly and retains only ancestors and actual probes.
function index(source){
 try{
  const text=source.replace(/\r\n?/g,'\n'),nodes=[];
  for(const node of scan(text)){
   nodes.push(node);if(nodes.length>100000)return null;
   let depth=0;for(let parent=node.parent;parent;parent=parent.parent)if(++depth>=256)return null;
  }
  nodes.source=text;return nodes;
 }catch{return null}
}
function expanded(qname){if(qname.startsWith('{')){const at=qname.lastIndexOf('}');return {name:qname.slice(at+1),ns:qname.slice(1,at)}}return {name:qname,ns:''}}
function route(node){const parts=[];let length=0;for(let n=node;n;n=n.parent){length+=n.name.length+1;if(length>8192)return null;parts.push(n.name)}return parts.reverse().join('/')}
function* attributeNames(node,source,limit=Infinity){
 let count=0;
 for(const a of source.slice(node.nameEnd,node.openEnd-1).matchAll(/([^\s=]+)\s*=\s*(['"])([\s\S]*?)\2/g)){
  if(++count>limit)break;
  const parts=a[1].split(':'),name=parts.at(-1),ns=parts.length>1?node.namespaces[parts[0]]||'\u0000':'';
  const start=node.nameEnd+a.index;
  yield {name,ns,declaration:a[1]==='xmlns'||parts[0]==='xmlns',range:{start,end:start+a[1].length,kind:'attribute'}};
 }
}
function sourceRanges(node,attribute,source){
 const result=[{start:node.nameStart,end:node.nameEnd,kind:'tag'}];
 if(node.closeStart!==null)result.push({start:node.closeStart,end:node.closeStart+node.qname.length,kind:'tag'});
 if(attribute&&node.openEnd-node.nameEnd<=65536){
  const wanted=expanded(attribute),prefix=attribute.includes(':')&&!attribute.startsWith('{')?attribute.split(':')[0]:null;
  if(prefix){wanted.name=attribute.split(':').at(-1);wanted.ns=node.namespaces[prefix]||'\u0000'}
  for(const attr of attributeNames(node,source,512)){
   if(attr.name===wanted.name&&attr.ns===wanted.ns){result.unshift(attr.range);break}
  }
 }
 return result;
}
// The XML index is in document order. Cache the matched public path of each
// parent so that a wrong ancestor namespace excludes its complete subtree.
function pathMatcher(known,namespaces){
 const cache=new WeakMap();
 function matches(node){
  if(cache.has(node))return cache.get(node);
  let path=null;
  if(!node.parent||matches(node.parent)!==null){
   const candidate=node.parent?cache.get(node.parent)+'/'+node.name:route(node);
   if(candidate!==null&&candidate.length<=8192&&namespaces[known.get(candidate)]?.includes(node.ns))path=candidate;
  }
  cache.set(node,path);
  return path;
 }
 return matches;
}
function sameElementPath(a,b){
 while(a!==b){
  if(!a||!b||a.name!==b.name||a.ns!==b.ns)return false;
  a=a.parent;b=b.parent;
 }
 return true;
}
function locate(errors,nodes,fields=[],namespaces=[]){
 const known=new Map(fields.map((path,i)=>[path,i]));
 const matches=pathMatcher(known,namespaces);
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
  const groups=[];
  for(const node of candidates){
   const nodePath=route(node);if(nodePath===null)continue;
   const attr=attribute?expanded(attribute):null;
   if(attr&&attribute.includes(':')&&!attribute.startsWith('{')){const parts=attribute.split(':');attr.name=parts.at(-1);attr.ns=node.namespaces[parts[0]]||'\u0000'}
   const path=nodePath+(attr?'/@'+attr.name.split(':').at(-1):'');if(path.length>8192)continue;
   let target='';
   if(!error.root){
    if(attr&&known.has(path)&&matches(node)!==null&&namespaces[known.get(path)]?.includes(attr.ns))target=path;
    for(let n=node;!target&&n;n=n.parent){target=matches(n)||''}
   }

   const field=target?known.get(target):null;
   const old=groups.find(g=>g.path===path&&g.attributeNs===attr?.ns&&g.location.field===field&&sameElementPath(g.node,node));
   const ranges=sourceRanges(node,attribute,nodes.source);
   if(old){
    const location=old.location,available=24-location.ranges.length;
    location.occurrences++;
    if(ranges.length>available)location.rangesTruncated=true;
    location.ranges.push(...ranges.slice(0,available));continue;
   }
   if(groups.length===8){result.truncated=true;break}
   const location={path:'/'+path,field,parent:!!target&&target!==path,ranges,occurrences:1,rangesTruncated:false};
   groups.push({node,path,attributeNs:attr?.ns,location});result.locations.push(location);
  }
  // Identical local paths can belong to different namespaces. Show their
  // expanded names in the private frame so the user can tell the options apart.
  for(const group of groups){
   if(!groups.some(other=>other!==group&&other.path===group.path))continue;
   const names=[];let length=attribute?attribute.length+(group.attributeNs?.length||0)+4:0;
   for(let n=group.node;n;n=n.parent){
    length+=n.name.length+n.ns.length+3;
    if(length>8192)break;
    names.push((n.ns?'{'+n.ns+'}':'')+n.name);
   }
   if(length>8192)continue;
   names.reverse();
   if(attribute)names.push('@'+(group.attributeNs?'{'+group.attributeNs+'}':'')+group.path.split('/@').at(-1));
   group.location.expandedPath='/'+names.join('/');
  }
  result.ambiguous=groups.length>1;result.sourceAmbiguous=result.ambiguous||result.locations.some(l=>l.occurrences>1);return result;
 });
}
function reviewLocations(nodes,fields=[],namespaces=[],reviewFields=[]){
 if(!nodes?.length||!reviewFields.length)return [];
 const selected=new Set(reviewFields.filter(i=>Number.isInteger(i)&&i>=0&&i<fields.length));
 if(!selected.size)return [];
 const known=new Map(fields.map((path,i)=>[path,i]));
 const matches=pathMatcher(known,namespaces);
 const attributeParents=new Set();
 for(const i of selected){
  const at=fields[i].lastIndexOf('/@');
  if(at>=0)attributeParents.add(fields[i].slice(0,at));
 }
 const groups=new Map();
 function record(field,line,ranges){
  if(!groups.has(field))groups.set(field,{path:'/'+fields[field],field,line,ranges:[],occurrences:0,rangesTruncated:false});
  const group=groups.get(field),available=24-group.ranges.length;
  group.occurrences++;
  if(ranges.length>available)group.rangesTruncated=true;
  group.ranges.push(...ranges.slice(0,available));
 }
 for(const node of nodes){
  const path=matches(node);
  if(path===null)continue;
  const field=known.get(path);
  if(selected.has(field))record(field,node.line,sourceRanges(node,null,nodes.source));
  if(!attributeParents.has(path))continue;
  let line=node.line,offset=node.nameEnd;
  for(const attr of attributeNames(node,nodes.source)){
   if(attr.declaration)continue;
   const i=known.get(path+'/@'+attr.name);
   if(!selected.has(i)||!namespaces[i]?.includes(attr.ns))continue;
   for(;offset<attr.range.start;offset++)if(nodes.source.charCodeAt(offset)===10)line++;
   record(i,line,[attr.range]);
  }
 }
 return [...groups.values()];
}
const api={index,scan,locate,reviewLocations};scope.SedoValidationPaths=api;if(typeof module==='object'&&module.exports)module.exports=api;
})(typeof self==='object'?self:globalThis);
