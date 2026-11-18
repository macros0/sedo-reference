/* Build journal counts using the same engine as the comparison page. No network. */
const {compare,hasUnresolvedUnionValues}=require('./web/compare-core.js');

function documentDate(value){
 const match=/^(\d{2})\.(\d{2})\.(\d{2}|\d{4})$/.exec(value||'');
 if(!match)return 0;
 return Number((match[3].length===2?'20':'')+match[3]+match[2]+match[1]);
}
function summarizeUpdates(catalog,loadFields){
 const revisions=new Map(catalog.revisions.map(r=>[r.revision_id,r])),cache=new Map();
 const discoveries=new Map(),snapshots=new Map();
 const time=event=>{const value=Date.parse(event?.observed_at);return Number.isFinite(value)?value:null};
 for(const [index,event] of (catalog.updates||[]).entries()){
  if(event.snapshot&&!snapshots.has(event.snapshot))snapshots.set(event.snapshot,index);
  if(!discoveries.has(event.revision_id))discoveries.set(event.revision_id,{event,index});
 }
 function known(candidate,event){
  const discovery=discoveries.get(candidate.revision_id);
  // Older consumers can supply catalogs without observation metadata. There
  // is no historical boundary to apply in that format.
  if(!event.snapshot&&time(event)===null)return true;
  if(!discovery)return false;
  if(event.snapshot&&discovery.event.snapshot===event.snapshot)return true;
  const observed=time(event),found=time(discovery.event);
  if(observed!==null&&found!==null)return found<=observed;
  return !!event.snapshot&&!!discovery.event.snapshot&&snapshots.get(discovery.event.snapshot)<=snapshots.get(event.snapshot);
 }
 function baselineOrder(a,b){
  const left=discoveries.get(a.revision_id),right=discoveries.get(b.revision_id);
  const observed=(time(right?.event)??0)-(time(left?.event)??0);
  const snapshotOrder=(snapshots.get(right?.event.snapshot)??right?.index??0)-(snapshots.get(left?.event.snapshot)??left?.index??0);
  const source=r=>catalog.update_sources?.[r.revision_id]||r;
  // listed_now and source labels can change after the observation. Prefer the
  // latest known discovery and use immutable discovery provenance for ties.
  return observed||snapshotOrder||documentDate(source(b).label_date)-documentDate(source(a).label_date)||String(source(b).document_version||'').localeCompare(String(source(a).document_version||''),undefined,{numeric:true})||a.revision_id.localeCompare(b.revision_id);
 }
 const fields=id=>{if(!cache.has(id))cache.set(id,loadFields(id));return cache.get(id)};
 return (catalog.updates||[]).map(event=>{
  const current=revisions.get(event.revision_id);
  if(!current)throw Error('Unknown journal edition '+event.revision_id);
  let before=null,basis='previous';
  if(event.previous_revision_id){
   before=revisions.get(event.previous_revision_id);
   if(!before||before.number!==current.number||before.environment!==current.environment)throw Error('Invalid previous message or contour');
  }else{
   basis='other_contour';
   before=catalog.revisions.filter(r=>r.number===current.number&&r.environment!==current.environment&&r.ready&&known(r,event)).sort(baselineOrder)[0];
  }
  const comparison={status:!current.ready?'unavailable':!before?'no_baseline':!before.ready?'unavailable':'ready',basis,left:before?.revision_id||null,right:current.revision_id,counts:null};
  if(comparison.status==='ready'){
   comparison.counts={added:0,removed:0,changed:0};
   const before=fields(comparison.left),after=fields(comparison.right);
   for(const difference of compare(before,after))comparison.counts[difference.status]++;
   if(before.some(hasUnresolvedUnionValues)||after.some(hasUnresolvedUnionValues))comparison.limitedUnion=true;
  }
  return {...event,comparison};
 });
}
module.exports={summarizeUpdates};
if(require.main===module){
 const fs=require('node:fs'),path=require('node:path');
 const catalog=JSON.parse(fs.readFileSync(0,'utf8'));
 const updates=summarizeUpdates(catalog,id=>JSON.parse(fs.readFileSync(path.join(process.argv[2],id+'.json'),'utf8')).fields);
 process.stdout.write(JSON.stringify(updates));
}
