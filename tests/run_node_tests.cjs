'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

function runTests(directory = __dirname) {
  const suites = fs.readdirSync(directory).filter(name => /^test_.*\.cjs$/.test(name)).sort();
  for (const suite of suites) {
    const result = spawnSync(process.execPath, [path.join(directory, suite)], {stdio: 'inherit'});
    if (result.error) throw result.error;
    if (result.status !== 0) return result.status || 1;
  }
  console.log(`Passed Node suites: ${suites.length}`);
  return 0;
}

module.exports = {runTests};
if (require.main === module) process.exitCode = runTests();
