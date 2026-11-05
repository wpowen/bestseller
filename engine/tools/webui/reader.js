import { $, state, text, el, link, button, bookURL, readURL, stored, persist, notify } from './shared.js';
let prefs = { ...{ width: 'medium', size: 19, line: 2, bg: 'paper', mode: 'final' }, ...stored('bookweb-reader', {}) };
function chapters() {
 const map = new Map((state.book?.chapters || []).map((c) => [c.number, c]));
 return Array.from({ length: state.book?.meta.total || 0 }, (_, i) => map.get(i + 1) || { number: i + 1, title: '尚未写作', draft: '', final: '' });
}
function renderTOC() {
 const nav = $('toc-links');
 nav.replaceChildren();
 let volume;
 for (const c of chapters().filter((c) => `${c.number} ${c.title}`.includes($('toc-search').value))) {
  const v = c.volume || Math.ceil(c.number / 25);
  if (v !== volume) {
   nav.append(el('h4', `第 ${v} 卷`));
   volume = v;
  }
  const a = c.draft
   ? link(`第 ${c.number} 章　${c.title}`, readURL(state.readerId, c.number), c.number === state.chapter ? 'on' : '')
   : el('span', `第 ${c.number} 章　未写`, 'muted');
  a.setAttribute('aria-current', c.number === state.chapter ? 'page' : 'false');
  nav.append(a);
 }
}
const sentences = (line) => line.match(/[^。！？.!?]+[。！？.!?]*[”’」』"']*|[。！？.!?]+/g) || [];
function changedSentences(original, revised) {
 const before = original.split(/\n+/).filter(s => !/^#/.test(s)).flatMap(sentences);
 const after = revised.split(/\n+/).filter(s => !/^#/.test(s)).flatMap(sentences);
 const rows = Array.from({length: before.length + 1}, () => new Uint16Array(after.length + 1));
 for (let i = before.length - 1; i >= 0; i--) {
  for (let j = after.length - 1; j >= 0; j--) {
   rows[i][j] = before[i] === after[j] ? rows[i + 1][j + 1] + 1 : Math.max(rows[i + 1][j], rows[i][j + 1]);
  }
 }
 const unchanged = new Set();
 let i = 0, j = 0;
 while (i < before.length && j < after.length) {
  if (before[i] === after[j]) {
   unchanged.add(j++);
   i++;
  } else if (rows[i + 1][j] >= rows[i][j + 1]) i++;
  else j++;
 }
 return unchanged;
}
function prose(node, value, original) {
 node.replaceChildren();
 const unchanged = original == null ? null : changedSentences(original, value || '');
 let index = 0;
 for (const line of (value || '这个版本尚未生成。').split(/\n+/).filter(Boolean)) {
  if (/^#/.test(line)) continue;
  const p = el('p');
  for (const sentence of sentences(line)) {
   p.append(el(unchanged && !unchanged.has(index++) ? 'mark' : 'span', sentence));
  }
  node.append(p);
 }
}
function renderChapter() {
 const c = chapters().find((c) => c.number === state.chapter);
 const b = state.book.meta;
 text('reader-title', `${b.title} · ${c?.title || '章节未生成'}`);
 $('close-reader').href = bookURL(b.id);
 text('chapter-title', `第 ${state.chapter} 章　${c?.title || ''}`);
 text('chapter-meta', b.title + ' · ' + (prefs.mode === 'compare' ? '草稿 / 终稿对照' : '静下来，读一个故事'));
 prose($('draft'), c?.draft, prefs.mode === 'compare' ? c?.final : null);
 prose($('final'), c?.final, prefs.mode === 'compare' ? c?.draft : null);
 $('comparison').dataset.mode = prefs.mode;
 $('reading-progress').max = Math.max(1, b.total);
 $('reading-progress').value = state.chapter;
 $('prev-chapter').disabled = state.chapter <= 1;
 $('next-chapter').disabled = !chapters().find((n) => n.number === state.chapter + 1)?.draft;
 $('copy-chapter').disabled = !(prefs.mode === 'draft' ? c?.draft : c?.final);
 if (c?.draft) persist('chapter:' + b.id, state.chapter);
 renderTOC();
 applyPrefs();
}
function applyPrefs() {
 const r = $('reader');
 r.style.setProperty('--width', { narrow: '640px', medium: '820px', wide: '1080px', full: '100%' }[prefs.width]);
 r.style.setProperty('--font-size', prefs.size + 'px');
 r.style.setProperty('--leading', prefs.line);
 r.dataset.background = prefs.bg;
 document.querySelectorAll('#read-modes [data-mode]').forEach((b) => b.classList.toggle('on', b.dataset.mode === prefs.mode));
 document.querySelectorAll('[data-pref]').forEach((b) => b.classList.toggle('on', String(prefs[b.dataset.pref]) === b.dataset.value));
 persist('bookweb-reader', prefs);
}
function toggleSide() {
 $('reader').classList.toggle('noside');
 $('side-toggle').setAttribute('aria-expanded', String(!$('reader').classList.contains('noside')));
}
function flip(delta) {
 const n = state.chapter + delta;
 if (chapters().find((c) => c.number === n)?.draft) location.hash = readURL(state.readerId, n);
}
async function copyText(value) {
 try {
  if (!value) throw Error('所选版本尚未生成。');
  await navigator.clipboard.writeText(value);
  if ($('reader').hidden) notify('已复制。');
  text('reader-status', '已复制。');
 } catch (e) {
  notify(e.message || '剪贴板访问失败，请选中正文复制。');
 }
}
for (const [key, label, options] of [
 [ 'size', '字号', [ [17, '小'], [19, '中'], [22, '大'], [25, '特大'], ], ],
 [ 'width', '版心宽度', [ ['narrow', '窄'], ['medium', '适中'], ['wide', '宽'], ['full', '铺满'], ], ],
 [ 'line', '行距', [ [1.6, '紧凑'], [2, '舒适'], [2.4, '宽松'], ], ],
 [ 'bg', '背景', [ ['paper', '纸'], ['eye', '护眼'], ['night', '夜间'], ], ],
]) {
 const group = el('div', null, 'setting-group');
 group.append(el('p', label));
 for (const [value, name] of options) {
  const b = button(name, () => {
   prefs[key] = value;
   if (key === 'width' && value === 'full') collapseForWidth();
   applyPrefs();
  });
  b.dataset.pref = key;
  b.dataset.value = value;
  group.append(b);
 }
 $('setting-groups').append(group);
}
$('read-modes').onclick = (e) => {
 if (e.target.dataset.mode) {
  prefs.mode = e.target.dataset.mode;
  if (prefs.mode === 'compare') collapseForWidth();
  renderChapter();
 }
};
function collapseForWidth() {
 if (!matchMedia('(max-width:899px)').matches) {
  $('reader').classList.add('noside');
  $('side-toggle').setAttribute('aria-expanded', 'false');
 }
}
$('aa').onclick = () => {
 $('read-settings').hidden = !$('read-settings').hidden;
 $('aa').setAttribute('aria-expanded', String(!$('read-settings').hidden));
};
$('settings-close').onclick = () => {
 $('read-settings').hidden = true;
 $('aa').setAttribute('aria-expanded', 'false');
};
$('side-toggle').onclick = toggleSide;
$('drawer-mask').onclick = toggleSide;
$('chapter-directory').onclick = toggleSide;
$('toc-search').oninput = renderTOC;
$('prev-chapter').onclick = () => flip(-1);
$('next-chapter').onclick = () => flip(1);
$('copy-chapter').onclick = () => copyText(chapters().find((c) => c.number === state.chapter)?.[prefs.mode === 'draft' ? 'draft' : 'final']);
document.addEventListener('keydown', (e) => {
 if ($('reader').hidden || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
 if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
  e.preventDefault();
  flip(e.key === 'ArrowLeft' ? -1 : 1);
 }
 if (e.key.toLowerCase() === 't') toggleSide();
 if (e.key === 'Escape') {
  if (!$('read-settings').hidden) $('settings-close').click();
  else location.hash = bookURL(state.readerId);
 }
});
$('reader').classList.toggle('noside', matchMedia('(max-width:899px)').matches);
if (prefs.width === 'full' || prefs.mode === 'compare') collapseForWidth();
$('side-toggle').setAttribute('aria-expanded', String(!$('reader').classList.contains('noside')));
export { chapters, copyText, renderChapter };
