const fs=require('node:fs'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const journal=require('../../web/review-journal.js');
const root=path.resolve(__dirname,'../..');
const catalog=JSON.parse(fs.readFileSync(path.join(root,'site/catalog-data.js'),'utf8').replace(/^window.SEDO_CATALOG=/,'').replace(/;\s*$/,''));
const journalRows=(number,env)=>journal.filter(journal.collect(catalog),number,env);
const rowFieldLinks=(number,env,field)=>journalRows(number,env).flatMap(({revision,finding})=>journal.fieldLinks(revision,finding).filter(link=>link.path===field).map(link=>({...link,revision})));
const fields=revision=>JSON.parse(fs.readFileSync(path.join(root,'data/fields',revision.revision_id+'.json'),'utf8')).fields;
function pythonFixture(script){
 const venv=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const python=process.env.SEDO_PYTHON||(fs.existsSync(venv)?venv:'python');
 return JSON.parse(execFileSync(python,['-B','-c',script],{cwd:root,encoding:'utf8',timeout:20000}));
}
module.exports={catalog,journal,journalRows,rowFieldLinks,fields,pythonFixture};
