/* Build journal counts using the same engine as the comparison page. No network. */
const {compare,hasUnresolvedUnionValues,hasUnresolvedNotationValues,hasUnresolvedListValues}=require('./web/compare-core.js');

function documentDate(value){
 const match=/^(\d{2})\.(\d{2})\.(\d{2}|\d{4})$/.exec(value||'');
 if(!match)return 0;
 return Number((match[3].length===2?'20':'')+match[3]+match[2]+match[1]);
}
function observationTime(value){
 const parts=/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:[.,](\d+))?(Z|[+-]\d{2}:?\d{2})$/.exec(value||'');
 if(!parts)return null;
 const [,day,clock,fraction='',offset]=parts;
 const milliseconds=Date.parse(day+'T'+clock+offset);
 if(!Number.isFinite(milliseconds))return null;
 return BigInt(milliseconds)*1000n+BigInt(fraction.slice(0,6).padEnd(6,'0'));
}
function compareTime(a,b){
 if(a===b)return 0;
 if(a===null)return 1;
 if(b===null)return -1;
 return a<b?-1:1;
}
function summarizeUpdates(catalog,loadFields){
 const revisions=new Map(catalog.revisions.map(r=>[r.revision_id,r])),cache=new Map();
 const displayId=revision=>revision.display_revision_id||revision.revision_id;
 const discoveries=new Map(),snapshots=new Map();
 const updates=catalog.updates||[];
 const observations=updates.map(event=>{
  const earlier=catalog.earlier_revision_observations?.[event.revision_id];
  return earlier?{...event,snapshot:earlier.snapshot,observed_at:earlier.observed_at,initial_import:earlier.initial_import}:event;
 });
 const exactTimes=catalog._observation_times;
 const times=new Map(observations.map(event=>{
  const value=event.observed_at;
  const instant=exactTimes&&Object.hasOwn(exactTimes,value)?exactTimes[value]:null;
  return [event,exactTimes?(instant===null?null:BigInt(instant)):observationTime(value)];
 }));
 const time=event=>times.get(event)??null;
 for(const [index,event] of observations.entries()){
  if(event.snapshot&&!snapshots.has(event.snapshot))snapshots.set(event.snapshot,index);
  if(!discoveries.has(event.revision_id))discoveries.set(event.revision_id,{event,index});
 }
 const snapshotRank=({event,index})=>snapshots.get(event.snapshot)??index;
 const initial=new Map();
 for(const [id,discovery] of discoveries){
  const revision=revisions.get(id);
  if(!revision)throw Error('Unknown journal edition '+id);
  const group=displayId(revision),prior=initial.get(group);
  const observed=compareTime(time(discovery.event),time(prior?.event));
  if(!prior||observed<0||(observed===0&&snapshotRank(discovery)<snapshotRank(prior)))initial.set(group,discovery);
 }
 const schemaRevisions=catalog.revisions.filter(r=>{
  const anchor=initial.get(displayId(r));
  return !anchor||anchor.event.revision_id===r.revision_id;
 });
 function known(candidate,event){
  const discovery=discoveries.get(candidate.revision_id);
  // Older consumers can supply catalogs without observation metadata. There
  // is no historical boundary to apply in that format.
  if(!event.snapshot&&time(event)===null)return true;
  if(!discovery)return false;
  if(event.snapshot&&discovery.event.snapshot===event.snapshot)return true;
  const observed=time(event),found=time(discovery.event);
  if(observed!==null&&found!==null)return compareTime(found,observed)<=0;
  return !!event.snapshot&&!!discovery.event.snapshot&&snapshots.get(discovery.event.snapshot)<=snapshots.get(event.snapshot);
 }
 function earlier(candidate,event,index){
  const discovery=discoveries.get(candidate.revision_id);
  if(!discovery)return false;
  if(event.snapshot&&discovery.event.snapshot===event.snapshot)return false;
  const observed=time(event),found=time(discovery.event);
  // Append order cannot place an undated observation before a dated replay.
  if(observed!==null&&found===null)return false;
  if(observed!==null&&found!==null&&observed!==found)return compareTime(found,observed)<0;
  const currentOrder=snapshots.get(event.snapshot)??index;
  const candidateOrder=snapshots.get(discovery.event.snapshot)??discovery.index;
  return candidateOrder<currentOrder;
 }
 function baselineOrder(a,b){
  const left=discoveries.get(a.revision_id),right=discoveries.get(b.revision_id);
  const observed=compareTime(time(right?.event)??0n,time(left?.event)??0n);
  const snapshotOrder=(snapshots.get(right?.event.snapshot)??right?.index??0)-(snapshots.get(left?.event.snapshot)??left?.index??0);
  const source=r=>catalog.update_sources?.[r.revision_id]||r;
  // listed_now and source labels can change after the observation. Prefer the
  // latest known discovery and use immutable discovery provenance for ties.
  return observed||snapshotOrder||documentDate(source(b).label_date)-documentDate(source(a).label_date)||String(source(b).document_version||'').localeCompare(String(source(a).document_version||''),undefined,{numeric:true})||a.revision_id.localeCompare(b.revision_id);
 }
 const fields=id=>{if(!cache.has(id))cache.set(id,loadFields(id));return cache.get(id)};
 return observations.map((event,index)=>{
  const current=revisions.get(event.revision_id);
  if(!current)throw Error('Unknown journal edition '+event.revision_id);
  const group=displayId(current),firstEvent=initial.get(group).event===event;
  let previous=null;
  if(event.previous_revision_id){
   previous=revisions.get(event.previous_revision_id);
   if(!previous||previous.number!==current.number||previous.environment!==current.environment)throw Error('Invalid previous message or contour');
  }
  const alternatives=(firstEvent?schemaRevisions:catalog.revisions).filter(r=>r.number===current.number&&displayId(r)!==group);
  const sameContour=alternatives.filter(r=>r.environment===current.environment&&earlier(r,event,index)).sort(baselineOrder);
  // Older events can name a predecessor without including its discovery event.
  const unknownPrevious=previous&&!discoveries.has(previous.revision_id)&&(!firstEvent||!initial.has(displayId(previous)));
  if(unknownPrevious&&displayId(previous)!==group)sameContour.push(previous);
  // Import reports also consume later events hidden by display_updates.
  const originalSourceComparison=!firstEvent&&previous?.ready&&previous!==current&&displayId(previous)===group
   &&(unknownPrevious||earlier(previous,event,index));
  const previousReady=sameContour.find(r=>r.ready);
  const chronologicalPrevious=previousReady||sameContour[0];
  let before=originalSourceComparison?previous:previousReady,basis='previous';
  if(!before){
   before=alternatives.filter(r=>r.environment!==current.environment&&r.ready&&known(r,event)).sort(baselineOrder)[0];
   if(before||!sameContour.length)basis='other_contour';
   else before=chronologicalPrevious;
  }
  const comparison={status:!current.ready?'unavailable':!before?'no_baseline':!before.ready?'unavailable':'ready',basis,left:before?.revision_id||null,right:current.revision_id,counts:null};
  if(comparison.status==='ready'){
   comparison.counts={added:0,removed:0,changed:0};
   const before=fields(comparison.left),after=fields(comparison.right);
   for(const difference of compare(before,after))comparison.counts[difference.status]++;
   if(before.some(hasUnresolvedUnionValues)||after.some(hasUnresolvedUnionValues))comparison.limitedUnion=true;
   if(before.some(hasUnresolvedNotationValues)||after.some(hasUnresolvedNotationValues))comparison.limitedNotation=true;
   if(before.some(hasUnresolvedListValues)||after.some(hasUnresolvedListValues))comparison.limitedList=true;
  }
  const raw=updates[index];
  const summary={...raw,chronological_first:firstEvent,
   chronological_previous_revision_id:chronologicalPrevious?.revision_id||null,comparison};
  if(raw!==event){
   summary.chronological_observation={snapshot:event.snapshot,observed_at:event.observed_at,initial_import:event.initial_import};
  }
  return summary;
 });
}
module.exports={summarizeUpdates};
if(require.main===module){
 const fs=require('node:fs'),path=require('node:path');
 const catalog=JSON.parse(fs.readFileSync(0,'utf8'));
 const updates=summarizeUpdates(catalog,id=>JSON.parse(fs.readFileSync(path.join(process.argv[2],id+'.json'),'utf8')).fields);
 process.stdout.write(JSON.stringify(updates));
}
