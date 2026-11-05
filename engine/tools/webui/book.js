import {
 $, state, el, money, count, statusLabel, creationDate, bookUsage, runtimeInfo, link, button, bookURL, readURL, stored, printable, notify
} from './shared.js';
import { actionButtons, manage } from './library.js';
import { chapters, copyText } from './reader.js';
let tab = '目录';
function renderDetail(selected = tab) {
 tab = selected;
 const b = state.book.meta;
 const last = Math.min(b.chapters || 1, stored('chapter:' + b.id, 1));
 const cover = el('div', null, 'cover');
 cover.dataset.genre = b.genre;
 cover.append(el('small', b.genre), el('h2', b.title));
 const info = el('div');
 info.append(
  el('span', statusLabel(b), 'badge'),
  el('h1', b.title),
  el('p', b.synopsis || '故事简介尚未生成。', 'muted'),
  el('p', `终稿 ${b.chapters} / 计划 ${b.total} 章　${count(b.words)} 字　${money(b.cost)}　${creationDate(b.created)}`, 'stats'),
  link(`继续阅读 · 第 ${last} 章`, readURL(b.id, last), 'primary button'),
  actionButtons(b),
  el('p', bookUsage(b), 'muted'),
  el('p', runtimeInfo(b), 'muted'),
 );
 if (b.owned) info.append(button('删除', () => manage(b, 'delete'), 'danger'));
 $('book-hero').replaceChildren(cover, info);
 $('book-tabs').replaceChildren(
  ...['目录', '故事圣经', '章纲', '检测报告（参考）', '导出'].map((t) =>
   button(
    t,
    () => {
     location.hash = bookURL(b.id) + '?tab=' + encodeURIComponent(t);
    },
    t === tab ? 'on' : '',
   ),
  ),
 );
 const content = $('book-content');
 content.replaceChildren();
 if (tab === '目录') {
  const grid = el('div', null, 'chapter-grid');
  for (const c of chapters()) {
   const a = c.final ? link(`第 ${c.number} 章　${c.title}`, readURL(b.id, c.number)) : el('span', `第 ${c.number} 章　${c.title}`, 'muted');
   a.append(
    el('small', `${c.deai_notice || (c.final ? '终稿' : c.draft ? '正在去 AI' : '未写')} · ${count(c.words)} 字`),
   );
   grid.append(a);
  }
  content.append(grid);
 } else if (tab === '导出') {
  const missing = chapters().filter((c) => !c.final).map((c) => c.number);
  content.append(el('p', missing.length ? `仅导出已有终稿，缺少第 ${missing.join('、')} 章。` : '全部终稿已就绪。', 'muted'));
  const links = el('div', null, 'toolbar');
  if (b.chapters) {
   for (const [format, label] of [['md', 'Markdown'], ['docx', 'Word'], ['txt', 'TXT'], ['epub', 'EPUB'], ['fanqie', '番茄分章 ZIP']]) {
    links.append(link(label, '/download?id=' + encodeURIComponent(b.id) + '&format=' + format + '&version=final', 'button'));
   }
  }
  content.append(links, button('复制全书', () => copyBook('final')));
 } else if (tab === '故事圣经') {
  const grid = el('div', null, 'chapter-grid');
  for (const c of state.book.characters || []) {
   const card = el('article', null, 'panel');
   card.append(el('h3', c.name || '人物'), el('p', [c.role, c.desire || c.wants, c.voice || c.speech].filter(Boolean).join(' · ')));
   grid.append(card);
  }
  content.append(grid, el('pre', state.book.bible || '故事圣经尚未生成。'));
 } else
  content.append(
   el(
    'pre',
    tab === '章纲' ? state.book.outline || '章纲尚未生成。'
     : printable(state.book.chapters.map((c) => ({ number: c.number, checks: c.checks, report: c.report }))),
   ),
  );
}
function copyBook(version) {
 const rows = chapters();
 if (!rows.length || rows.some((c) => !c[version])) return notify('所选版本缺章，不能复制为全书。');
 copyText(state.book.meta.title + '\n\n' + rows.map((c) => `第 ${c.number} 章 ${c.title}\n\n${c[version]}`).join('\n\n'));
}
export { renderDetail };
