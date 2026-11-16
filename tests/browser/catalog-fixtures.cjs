const fs=require('node:fs'),path=require('node:path');
const journal=require('../../web/review-journal.js');
const root=path.resolve(__dirname,'../..');
const catalog=JSON.parse(fs.readFileSync(path.join(root,'site/catalog-data.js'),'utf8').replace(/^window.SEDO_CATALOG=/,'').replace(/;\s*$/,''));
const journalRows=(number,env)=>journal.filter(journal.collect(catalog),number,env);
const rowFieldLinks=(number,env,field)=>journalRows(number,env).flatMap(({revision,finding})=>journal.fieldLinks(revision,finding).filter(link=>link.path===field).map(link=>({...link,revision})));
const fields=revision=>JSON.parse(fs.readFileSync(path.join(root,'data/fields',revision.revision_id+'.json'),'utf8')).fields;
module.exports={catalog,journal,journalRows,rowFieldLinks,fields};
