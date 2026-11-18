'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {runTests} = require('./run_node_tests.cjs');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'sedo-runner-'));
try {
  fs.writeFileSync(path.join(directory, 'test_first.cjs'), 'process.exit(7)');
  const marker = path.join(directory, 'ran');
  fs.writeFileSync(path.join(directory, 'test_second.cjs'), `require('node:fs').writeFileSync(${JSON.stringify(marker)}, 'yes')`);
  assert.equal(runTests(directory), 7);
  assert.equal(fs.existsSync(marker), false);
  fs.writeFileSync(path.join(directory, 'test_first.cjs'), '');
  assert.equal(runTests(directory), 0);
  assert.equal(fs.readFileSync(marker, 'utf8'), 'yes');
} finally {
  fs.rmSync(directory, {recursive: true});
}
