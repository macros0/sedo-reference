/* Confirmed message-type migrations. New XML types are never treated as aliases. */
(function(root){
"use strict";
function replacement(number,catalog,environment){
 const config=catalog.message_transitions;
 if(!config||!Array.isArray(config.pairs))return null;
 const pair=config.pairs.find(p=>p.previous===String(number));
 if(!pair)return null;
 const exists=[...(catalog.revisions||[]),...(catalog.registry||[])].some(r=>r.number===pair.current&&(!environment||r.environment===environment));
 return exists?{...pair,platform:config.platform,production_retired_at:config.production_retired_at,source_url:config.source_url}:null;
}
const api={replacement};root.SedoTransitions=api;
if(typeof module!=="undefined"&&module.exports)module.exports=api;
})(typeof window!=="undefined"?window:globalThis);
