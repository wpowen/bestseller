import {
 $, state, text, printable, api, el, money, count, statusLabel, isComplete, runtimeInfo, link, button, bookURL, readURL, stored, notify
} from './shared.js';
async function action(id, name) {
 if (state.pendingActions.has(id)) return;
 state.pendingActions.add(id);
 updateActionControls();
 notify(name === 'postprocess' ? '正在提交后处理，请勿重复点击…' : '正在提交操作…');
 try {
  const result = await api('/api/action', { id, action: name });
  const detail = printable(result.message || result.status);
  notify(
   (name === 'postprocess' ? '后处理请求已提交，最终稿与检测回执将随阅读区同步。' : '操作已提交。') + (detail ? ' ' + detail : ''),
  );
  await refreshBooks();
  if (state.readerId === id) window.dispatchEvent(new Event('book-changed'));
 } catch (error) {
  notify(error.message + ' · 未自动重试，请先核对任务状态。');
 } finally {
  state.pendingActions.delete(id);
  updateActionControls();
 }
}
function actionButtons(book) {
 const wrapper = el('div', null, 'book-actions');
 if (book.owned !== true) {
  wrapper.append(el('span', '历史书籍 · 只读', 'muted'));
  return wrapper;
 }
 const status = String(book.status || '').toLowerCase();
 let choices = [];
 if (isComplete(book)) choices = [['postprocess', '运行去AI后处理']];
 else if (/^(paused|budget_paused|quota_exhausted)$/.test(status)) choices = [['resume', '继续当前阶段']];
 else if (/^(failed|error|blocked|interrupted|postprocess_failed|orphaned|stopped|budget_exceeded)$/.test(status))
  choices = [['retry', '从检查点重试']];
 else if (/^(running|waiting|writing|planning|queued|queued_polish|pending|polishing|postprocessing|postprocess_running)$/.test(status))
  choices = [['pause', '暂停']];
 else wrapper.append(el('span', status === 'unknown' ? '结果待确认，请先核对回执' : '暂无可用操作', 'muted'));
 for (const [name, label] of choices) {
  const button = el('button', label);
  button.type = 'button';
  button.dataset.action = name;
  button.dataset.bookId = String(book.id);
  button.setAttribute('aria-label', label + ' · ' + (book.title || book.id));
  button.disabled = state.pendingActions.has(String(book.id));
  if (name === 'postprocess') button.title = '手动提交后处理；复用缓存还是产生新调用，由后端返回的验证结果决定。';
  button.addEventListener('click', () => action(String(book.id), name));
  wrapper.append(button);
 }
 return wrapper;
}
function updateActionControls() {
 document.querySelectorAll('button[data-action]').forEach((button) => {
  button.disabled = state.pendingActions.has(button.dataset.bookId);
 });
}
let books = [],
 archived = [],
 filter = 'all',
 undoId,
 managed;
function renderLibrary() {
 const visible = books.filter(
  (b) =>
   (filter === 'all' ||
    (filter === 'complete' ? isComplete(b) : filter === 'paused'
     ? /paused|quota_exhausted|error/.test(b.status) : /running|waiting|polishing|queued/.test(b.status))) &&
   (b.title + b.genre).includes($('search').value),
 );
 visible.sort((a, b) =>
  $('sort').value === 'title'
   ? a.title.localeCompare(b.title, 'zh')
   : $('sort').value === 'chapters'
     ? b.chapters - b.chapters
     : (b.created || '').localeCompare(a.created || ''),
 );
 $('books').replaceChildren(
  ...visible.map((b) => {
   const card = el('article', null, 'book-card');
   const cover = link('', bookURL(b.id), 'cover');
   cover.dataset.genre = b.genre;
   cover.append(el('small', b.genre), el('h2', b.title));
   const body = el('div', null, 'book-body');
   const badge = el('span', statusLabel(b), 'badge');
   badge.dataset.status = b.status;
   const progress = el('progress');
   progress.max = Math.max(1, b.total);
   progress.value = b.chapters;
   progress.setAttribute('aria-label', '章节进度');
   const actions = actionButtons(b);
   actions.prepend(link('阅读', readURL(b.id, stored('chapter:' + b.id, 1)), 'primary button'));
   const menu = el('details', null, 'more');
   menu.append(el('summary', '⋯'));
   menu.append(link('书籍详情', bookURL(b.id)), link('导出', bookURL(b.id) + '?tab=导出'));
   if (b.owned)
    for (const [name, label] of [ ['rename', '重命名'], ['archive', '移到归档（可恢复）'], ['delete', '永久删除…'], ])
     menu.append(button(label, () => manage(b, name), name === 'delete' ? 'danger' : 'ghost'));
   actions.append(menu);
   body.append(badge, progress, el('p', `${b.chapters}/${b.total} 章 · ${count(b.words)} 字 · ${money(b.cost)}`, 'muted'),
    el('p', runtimeInfo(b), 'muted'), actions);
   card.append(cover, body);
   return card;
  }),
  link('＋\n开一本新书', '#/new', 'new-tile'),
 );
 $('archives').replaceChildren(
  ...archived.map((b) => {
   const row = el('article', null, 'panel');
   row.append(
    el('h3', b.title),
    button('恢复到书库', () => manage(b, 'restore')),
    button('永久删除…', () => manage(b, 'delete'), 'danger'),
   );
   return row;
  }),
 );
 text('archive-toggle', `已归档 ${archived.length} 本`);
}
async function refreshBooks() {
 if (state.booksBusy) return;
 state.booksBusy = true;
 try {
  const [result, archive] = await Promise.all([api('/api/books'), api('/api/archive')]);
  const changed = !$('books').children.length || JSON.stringify([books, archived]) !== JSON.stringify([result.books, archive.books]);
  books = result.books;
  archived = archive.books;
  text('spending-value', `已花费 ${money(result.spending)}`);
  text('library-status', `${books.length} 本故事 · ${books.filter(isComplete).length} 本已完成`);
  if (changed) renderLibrary();
 } catch (error) {
  text('library-status', error.message);
 } finally {
  state.booksBusy = false;
 }
}
async function manage(book, name, title) {
 if (name === 'delete' || name === 'rename') {
  managed = { book, name };
  text('dialog-title', name === 'delete' ? '永久删除这本书？' : '重命名');
  text('dialog-hint', name === 'delete' ? `此操作不可撤销。请输入完整书名：${book.title}。运行中或仍有进程的书会被拒绝。` : '输入新的书名。');
  $('confirm-title').value = name === 'rename' ? book.title : '';
  text('dialog-status', '');
  $('manage-dialog').showModal();
  return;
 }
 try {
  const result = await api('/api/action', { id: book.id, action: name, ...(title === undefined ? {} : { title }) });
  undoId = name === 'archive' ? result.id : null;
  $('undo').hidden = !undoId;
  notify(name === 'archive' ? '已移到归档，可撤销。' : '操作已完成。');
  await refreshBooks();
 } catch (error) {
  notify(error.message);
 }
}
$('filters').onclick = (e) => {
 if (e.target.dataset.filter) {
  filter = e.target.dataset.filter;
  [...$('filters').children].forEach((b) => b.classList.toggle('on', b === e.target));
  renderLibrary();
 }
};
$('search').oninput = renderLibrary;
$('sort').onchange = renderLibrary;
$('refresh-books').onclick = refreshBooks;
$('archive-toggle').onclick = () => {
 $('archives').hidden = !$('archives').hidden;
};
$('undo').onclick = () => {
 if (undoId) manage({ id: undoId }, 'restore');
};
$('dialog-cancel').onclick = () => $('manage-dialog').close();
$('manage-form').onsubmit = async (e) => {
 e.preventDefault();
 const { book, name } = managed;
 const title = $('confirm-title').value;
 if (name === 'delete' && title !== book.title) return text('dialog-status', '书名不匹配，请输入完整书名。');
 $('dialog-confirm').disabled = true;
 try {
  await api('/api/action', { id: book.id, action: name, title });
  $('manage-dialog').close();
  notify('操作已完成。');
  await refreshBooks();
 } catch (error) {
  text('dialog-status', error.message);
 } finally {
  $('dialog-confirm').disabled = false;
 }
};
export { refreshBooks, actionButtons };
