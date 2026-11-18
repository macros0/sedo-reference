const {execFileSync}=require('node:child_process');
const path=require('node:path');

module.exports=JSON.parse(execFileSync(process.env.PYTHON || 'python',
 ['-m','tests.comparison_xsd_fixture'],{cwd:path.resolve(__dirname,'../..'),encoding:'utf8'}));
