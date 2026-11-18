"use strict";
const assert = require('node:assert/strict');
const {index, locate, reviewLocations} = require('../web/validation-paths.js');

const fields = ['r', 'r/group', 'r/group/id', 'r/group/id/id', 'r/group/@id', 'r/group/@absent', 'r/right', 'r/right/id'];
const namespaces = [['urn:r'], ['urn:g'], ['urn:id'], ['urn:id'], ['', 'urn:id'], [''], ['urn:g'], ['urn:id']];
const xml = [
  '<r xmlns="urn:r" xmlns:g="urn:g" xmlns:i="urn:id" xmlns:w="urn:other">',
  '<g:group',
  ' id="private-attribute" i:id="private-qualified" w:id="private-wrong">',
  '<i:id>private-text<i:id/></i:id>',
  '</g:group>',
  '<w:group><i:id>wrong ancestor</i:id></w:group>',
  '<g:group><w:id><i:id/></w:id></g:group>',
  '<g:right><i:id/></g:right>',
  '</r>'
].join('\r\n');
const nodes = index(xml);
const results = reviewLocations(nodes, fields, namespaces, [2, 3, 4, 5, 4, -1, 99, '0']);
const normalized = xml.replaceAll('\r\n', '\n');
const ranges = result => result.ranges.map(range => [range.kind, normalized.slice(range.start, range.end)]);
const byField = field => results.find(result => result.field === field);
assert.deepEqual(results.map(r => r.field).sort((a,b) => a-b), [2,3,4]);
assert.deepEqual(results.map(r => [r.path, r.occurrences, r.line]).sort(), [
  ['/r/group/id', 1, 4], ['/r/group/id/id', 1, 4], ['/r/group/@id', 2, 3]
].sort());
assert.deepEqual(ranges(byField(2)), [['tag', 'i:id'], ['tag', 'i:id']]);
assert.deepEqual(ranges(byField(3)), [['tag', 'i:id']]);
assert.deepEqual(ranges(byField(4)), [['attribute', 'id'], ['attribute', 'i:id']]);
assert.ok(!JSON.stringify(results).includes('private'));
assert.deepEqual(locate([], nodes, fields, namespaces), [], 'valid XML can have field reviews without schema errors');
assert.deepEqual(reviewLocations(null, fields, namespaces, [0,1]), []);
assert.deepEqual(reviewLocations(nodes, fields, [], [0,2]), []);
assert.deepEqual(reviewLocations(nodes, fields, namespaces, []), []);
const wrongRoot = index('<r xmlns="wrong" xmlns:g="urn:g" xmlns:i="urn:id"><g:group id=""><i:id/></g:group></r>');
assert.deepEqual(reviewLocations(wrongRoot, fields, namespaces, [2,4]), []);

const many = count => index('<r xmlns:g="urn:id">' + '<x g:id="value"></x>'.repeat(count) + '</r>');
const repeat = count => reviewLocations(many(count), ['r','r/x','r/x/@id'], [[''],[''],['urn:id']], [1,2]);
const [tags, attributes] = repeat(25);
assert.equal(tags.occurrences, 25);
assert.equal(tags.ranges.length, 24);
assert.ok(tags.rangesTruncated);
assert.equal(attributes.occurrences, 25);
assert.equal(attributes.ranges.length, 24);
assert.ok(attributes.rangesTruncated);
assert.ok(attributes.ranges.every(range => range.kind === 'attribute'));
assert.equal(repeat(12)[0].rangesTruncated, false, 'all 24 tag ranges fit');
assert.equal(repeat(24)[1].rangesTruncated, false, 'all 24 attribute ranges fit');

const namespaced = index('<r xmlns="u" xmlns:n="v" id="unqualified" n:id="qualified" xmlns:id="u"/>');
const onlyQualified = reviewLocations(namespaced, ['r','r/@id'], [['u'],['v']], [1]);
assert.equal(onlyQualified[0].occurrences, 1);
assert.deepEqual(onlyQualified[0].ranges.map(r => namespaced.source.slice(r.start, r.end)), ['n:id']);
assert.deepEqual(reviewLocations(namespaced, ['r','r/@id'], [['u'],['u']], [1]), []);

const hugeTag = index('<r ' + Array.from({length:520}, (_, i) => 'a'+i+'="0"').join(' ') +
                      ' description="' + 'x'.repeat(66000) + '" id="ok"/>');
const lateAttribute = reviewLocations(hugeTag, ['r','r/@id'], [[''],['']], [1]);
assert.equal(lateAttribute[0].occurrences, 1, 'late attributes count in present-field reviews');
assert.equal(hugeTag.source.slice(lateAttribute[0].ranges[0].start, lateAttribute[0].ranges[0].end), 'id');
