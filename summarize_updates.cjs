/* Build journal counts using the same engine as the comparison page. No network. */
const {compare}=require('./web/compare-core.js');

function documentDate(value){
 const match=/^(\d{2})\.(\d{2})\.(\d{2}|\d{4})$/.exec(value||'');
 if(!match)return 0;
 return Number((match[3].length===2?'20':'')+match[3]+match[2]+match[1]);
}
function summarizeUpdates(catalog,loadFields){
 const revisions=new Map(catalog.revisions.map(r=>[r.revision_id,r])),cache=new Map();
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
   before=[...catalog.revisions].reverse().filter(r=>r.number===current.number&&r.environment!==current.environment&&r.ready).sort((a,b)=>Number(b.listed_now!==false)-Number(a.listed_now!==false)||documentDate(b.label_date)-documentDate(a.label_date)||String(b.document_version||'').localeCompare(String(a.document_version||''),undefined,{numeric:true}))[0];
  }
  const comparison={status:!current.ready?'unavailable':!before?'no_baseline':!before.ready?'unavailable':'ready',basis,left:before?.revision_id||null,right:current.revision_id,counts:null};
  if(comparison.status==='ready'){
   comparison.counts={added:0,removed:0,changed:0};
   for(const difference of compare(fields(comparison.left),fields(comparison.right)))comparison.counts[difference.status]++;
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
