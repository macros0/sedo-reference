/* Explicit message-type correspondences. New XML types remain separate. */
(function(root){
"use strict";
function replacements(number,catalog,environment){
 const config=catalog.message_transitions;
 if(!config||!Array.isArray(config.pairs))return [];
 const entries=[...(catalog.revisions||[]),...(catalog.registry||[])];
 return config.pairs.filter(p=>p.previous===String(number)&&entries.some(r=>r.number===p.current&&(!environment||r.environment===environment))).map(pair=>{
  const source=config.sources?.[pair.source]||{};
  return {...pair,platform:config.platform,production_retired_at:source.production_retired_at,source_url:source.url,source_title:source.title};
 });
}
function isSuperseded(number,catalog,environment){
 const entries=[...(catalog.revisions||[]),...(catalog.registry||[])];
 const contours=[...new Set(entries.filter(r=>r.number===String(number)&&(!environment||r.environment===environment)).map(r=>r.environment))];
 return contours.length>0&&contours.every(env=>replacements(number,catalog,env).length>0);
}
const api={replacements,isSuperseded};root.SedoTransitions=api;
if(typeof module!=="undefined"&&module.exports)module.exports=api;
})(typeof window!=="undefined"?window:globalThis);
