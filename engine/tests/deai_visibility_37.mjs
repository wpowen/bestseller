// Execute the production view modules against a small text-only DOM.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

class Element {
 constructor(tagName = 'div') {
  this.tagName = tagName.toUpperCase();
  this.childNodes = [];
  this.dataset = {};
  this.value = '';
  this.style = { setProperty() {} };
  this.classList = { toggle() {}, contains() { return false; } };
 }
 set textContent(value) { this.childNodes = [String(value)]; }
 get textContent() { return this.childNodes.map(n => typeof n === 'string' ? n : n.textContent).join('\n'); }
 append(...nodes) { this.childNodes.push(...nodes); }
 replaceChildren(...nodes) { this.childNodes = nodes; }
 setAttribute() {}
 addEventListener() {}
}
const nodes = new Map();
const document = {
 createElement: tag => new Element(tag),
 getElementById(id) {
  if (!nodes.has(id)) nodes.set(id, new Element());
  return nodes.get(id);
 },
 querySelectorAll: () => [],
 addEventListener() {},
};
const context = vm.createContext({
 document, location: {}, matchMedia: () => ({ matches: false }),
 localStorage: { getItem: () => null, setItem() {} },
});
const root = join(dirname(fileURLToPath(import.meta.url)), '../tools/webui');
const modules = new Map();
function load(name) {
 if (modules.has(name)) return modules.get(name);
 let module;
 const stubs = name === 'library.js' ? ['actionButtons', 'manage']
  : name === 'overlays.js' ? ['closeOverlays', 'showSettings'] : null;
 if (stubs) {
  module = new vm.SyntheticModule(stubs, function() {
   for (const key of stubs) this.setExport(key, () => new Element());
  }, { context });
 } else {
  module = new vm.SourceTextModule(readFileSync(join(root, name), 'utf8'), { context, identifier: name });
 }
 modules.set(name, module);
 return module;
}
const book = load('book.js');
await book.link(specifier => load(specifier.replace('./', '')));
await book.evaluate();
const shared = modules.get('shared.js').namespace;
const reader = modules.get('reader.js').namespace;
const input = JSON.parse(readFileSync(0, 'utf8'));
const original = JSON.stringify(input);
shared.state.book = input;
shared.state.readerId = input.meta.id;
shared.state.chapter = input.chapters[0]?.number || 1;
book.namespace.renderDetail('目录');
const directory = document.getElementById('book-content').textContent;
reader.renderChapter();
const toc = document.getElementById('toc-links').textContent;
const meta = document.getElementById('chapter-meta').textContent;
const prose = document.getElementById('final').textContent;
book.namespace.renderDetail('检测报告（参考）');
const report = document.getElementById('book-content').textContent;
console.log(JSON.stringify({ directory, toc, meta, prose, report, unchanged: original === JSON.stringify(input) }));
