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
const api={replacements};root.SedoTransitions=api;
if(typeof module!=="undefined"&&module.exports)module.exports=api;
})(typeof window!=="undefined"?window:globalThis);
