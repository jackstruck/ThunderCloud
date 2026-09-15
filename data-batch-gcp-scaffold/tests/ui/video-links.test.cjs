const {test} = require('node:test');
const assert = require('node:assert/strict');
const {parseVideoLinks} = require('../../ui/video-links.js');

test('plain text exports support comments, CRLF, blanks and duplicates', () => {
  assert.deepEqual(parseVideoLinks('\uFEFF# links\r\nhttps://media.example/video/one\r\n\r\nhttps://media.example/video/one\nhttps://media.example/video/two'),
    ['https://media.example/video/one', 'https://media.example/video/two']);
});
test('reject invalid lines before returning a partial list', () => {
  for (const invalid of ['http://media.example/a', 'https://user:pass@media.example/a',
    'javascript:alert(1)', 'https://media.example/a#fragment', 'https://media.example:444/a', 'not a url']) {
    assert.throws(() => parseVideoLinks(`https://media.example/good\n${invalid}`), /Line 2/);
  }
  assert.throws(() => parseVideoLinks('# empty\n'), /no video links/);
});
test('limit oversized lists', () => {
  assert.throws(() => parseVideoLinks(Array.from({length:10001}, (_, n) => `https://media.example/${n}`).join('\n')), /10,000/);
});

test('import populates a selection and copies only the chosen URL into the form', async () => {
  const vm = require('node:vm');
  const fs = require('node:fs');
  const element = () => ({value:'', textContent:'', hidden:false, files:[], children:[], events:{},
    addEventListener(event, fn) { this.events[event] = fn; },
    replaceChildren() { this.children = []; },
    append(child) { this.children.push(child); }
  });
  const nodes = Object.fromEntries(['video-links-file', 'video-links-choice', 'video-links-select',
    'video-links-message', 'source-url'].map(id => [`#${id}`, element()]));
  const context = vm.createContext({URL, document: {
    querySelector: id => nodes[id], createElement: element
  }});
  vm.runInContext(fs.readFileSync(require.resolve('../../ui/video-links.js'), 'utf8'), context);
  const input = nodes['#video-links-file'];
  input.files = [{size:100, text:async () => 'https://media.example/one\nhttps://media.example/two'}];
  await input.events.change();
  assert.equal(nodes['#video-links-choice'].hidden, false);
  assert.equal(nodes['#video-links-select'].children.length, 2);
  assert.equal(nodes['#source-url'].value, 'https://media.example/one');
  nodes['#video-links-select'].value = 'https://media.example/two';
  nodes['#video-links-select'].events.change();
  assert.equal(nodes['#source-url'].value, 'https://media.example/two');
  input.files = [{size:10, text:async () => 'invalid'}];
  await input.events.change();
  assert.equal(nodes['#video-links-choice'].hidden, true);
  assert.match(nodes['#video-links-message'].textContent, /Line 1/);
});
