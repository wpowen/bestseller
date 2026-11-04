'use strict';
(() => {
const $ = id => document.getElementById(id);
const state = { config: null, topic: null, candidate: null, topicInput: null, topicTimer: null, topicBusy: false, creating: false, booksBusy: false, book: null, readerId: null, readerBusy: false, topicReady: false, topicSubmitting: false, topicEpoch: 0, topicRenderKey: null, bookCards: new Map(), pendingActions: new Set() };
const text = (id, value) => { const next = value == null ? '' : String(value); if ($(id).textContent !== next) $(id).textContent = next; };
const printable = value => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2);
async function api(path, body) {
 const response = await fetch(path, body === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
 const raw = await response.text(); let data;
 try { data = raw ? JSON.parse(raw) : {}; } catch { throw new Error('服务返回了非 JSON 内容，请检查后端。'); }
 if (!response.ok) throw new Error(printable(data.error || data.detail || data.message) || `请求失败 (${response.status})`);
 return data;
}
const el = (tag, content, cls) => { const node = document.createElement(tag); if (content != null) node.textContent = String(content); if (cls) node.className = cls; return node; };
function updateCreate() {
 const enabled = state.config?.models?.some(m => String(m.id) === $('model').value && m.enabled === true);
 $('create-book').disabled = state.creating || state.candidate === null || !state.topic || !state.topicReady || !enabled;
 text('create-book', state.creating ? '正在创建，请稍候…' : state.candidate === null ? '选定故事后，开始写作' : '以这个故事，开始写作');
 const model = state.config?.models?.find(m => String(m.id) === $('model').value);
 text('model-info', model ? `${model.name}${model.reason ? ' · ' + model.reason : ''}` : '没有可用写手时，无法开书。');
}
async function loadConfig() {
 try {
  const config = await api('/api/config'); state.config = config;
  $('genre').replaceChildren();
  for (const genre of config.genres || []) { const option = el('option', genre.name); option.value = genre.id; $('genre').append(option); }
  $('model').replaceChildren();
  for (const model of config.models || []) { const option = el('option', `${model.name}${model.enabled === true ? '' : ' · 不可用'}${model.reason ? ' — ' + model.reason : ''}`); option.value = model.id; option.disabled = model.enabled !== true; $('model').append(option); }
  const preferred = config.models?.find(m => m.id === 'flash' && m.enabled === true) || config.models?.find(m => m.enabled === true);
  $('model').value = preferred?.id || '';
  $('generate-topics').disabled = !(config.genres || []).length;
  text('topic-status', '已连接。已有选题可从上方恢复，不必再次生成。');
  text('budget-info', config.budgets ? `总预算上限 $${config.budgets.total ?? '—'} · 预留 $${config.budgets.reserve ?? '—'}；写作与去 AI 分别限额，开启润色时同时预留两份预算。` : '预算以服务端配置为准。'); updateCreate();
 } catch (error) { text('topic-status', error.message + ' 刷新页面可重试。'); }
}
const topicStorageKey = 'bestseller-b-topics-v1';
function savedTopics() { try { const saved = JSON.parse(localStorage.getItem(topicStorageKey) || '{}'); return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {}; } catch { return {}; } }
function refreshRecent() {
 const select = $('recent-topics'); if (!select) return; const selected = select.value; select.replaceChildren(el('option', '选择已保存的选题')); select.firstChild.value = '';
 for (const [key, entry] of Object.entries(savedTopics()).sort((a, b) => (b[1].saved || 0) - (a[1].saved || 0))) {
  if (!entry?.id) continue;
  const option = el('option', `${entry.genreName || entry.input?.genre || '已有选题'} · ${entry.input?.chapters || '—'}章 · ${entry.id}`); option.value = key; select.append(option);
 }
 if ([...select.options].some(o => o.value === selected)) select.value = selected;
}
function saveTopic(data) {
 if (!state.topic || !state.topicInput) return;
 const saved = savedTopics(); const input = state.topicInput;
 saved[input.genre + ':' + input.chapters] = { id: state.topic, input, data, genreName: state.config?.genres?.find(g => g.id === input.genre)?.name, saved: Date.now() };
 try { localStorage.setItem(topicStorageKey, JSON.stringify(saved)); refreshRecent(); } catch { text('restore-status', '浏览器未允许本地保存，请记下选题 ID：' + state.topic); }
 if ($('topic-id')) $('topic-id').value = state.topic;
}
async function restoreTopic(id, entry) {
 if (!id || state.creating) { text('restore-status', '请先输入已有的选题 UUID。'); return; }
 clearTimeout(state.topicTimer); state.topicEpoch++; const epoch = state.topicEpoch; state.candidate = null; state.topic = id; state.topicReady = false; state.topicRenderKey = null; $('candidates').replaceChildren(); updateCreate();
 entry = entry || Object.values(savedTopics()).find(item => item?.id === id);
 const input = entry?.input || { genre: $('genre').value, chapters: Number($('chapters').value), idea: $('idea').value.trim() };
 state.topicInput = input; $('restore-topic').disabled = true; text('restore-status', '正在读取已有选题，不会重新生成…');
 try {
  const data = await api('/api/topics?id=' + encodeURIComponent(id));
  if (state.topic !== id || state.topicEpoch !== epoch) return;
  if (data.genre || data.chapters) { input.genre = data.genre || input.genre; input.chapters = Number(data.chapters) || input.chapters; }
  $('genre').value = input.genre; $('chapters').value = input.chapters; $('idea').value = input.idea || '';
  $('length').value = [...$('length').options].some(o => o.value === String(input.chapters)) ? String(input.chapters) : 'custom';
  if (renderTopics(data)) state.topicTimer = setTimeout(() => pollTopic(id), 2000);
  text('restore-status', '已恢复。请重新点击一个候选，再点“开始写作”。'); $('candidate-section').scrollIntoView({behavior:'smooth', block:'start'});
 } catch (error) { text('restore-status', error.message); updateCreate(); }
 finally { $('restore-topic').disabled = false; }
}
$('restore-topic').addEventListener('click', () => restoreTopic($('topic-id').value.trim()));
$('restore-recent').addEventListener('click', () => { const entry = savedTopics()[$('recent-topics').value]; if (entry) restoreTopic(entry.id, entry); else text('restore-status', '请先从本地最近选题中选择一项。'); });
refreshRecent();
function invalidateTopic() {
 if (state.creating) return;
 clearTimeout(state.topicTimer); state.topicEpoch++; state.topic = null; state.candidate = null; state.topicInput = null; state.topicReady = false; state.topicRenderKey = null;
 $('candidates').replaceChildren(); $('candidate-section').hidden = true; updateCreate();
}
function renderTopics(data) {
 if (data.id) state.topic = data.id;
 saveTopic(data);
 const candidates = Array.isArray(data.candidates) ? data.candidates : [];
 $('candidate-section').hidden = !candidates.length;
 const renderKey = JSON.stringify(candidates);
 if (state.topicRenderKey !== renderKey) {
 state.topicRenderKey = renderKey; $('candidates').replaceChildren();
 candidates.forEach((candidate, index) => {
  const label = el('label', null, 'candidate');
  const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'candidate'; radio.id = `candidate-${index}`; radio.value = String(index); radio.checked = state.candidate === index;
  radio.addEventListener('change', () => { state.candidate = index; $('candidates').querySelectorAll('.candidate').forEach(n => n.classList.toggle('selected', n.contains(radio))); updateCreate(); });
  label.classList.toggle('selected', radio.checked); label.append(radio, el('span', String(index + 1).padStart(2, '0'), 'candidate-number'), el('h3', candidate.title), el('p', candidate.premise)); $('candidates').append(label);
 });
 }
 const status = String(data.status || '').toLowerCase();
 const failed = /failed|error|cancelled/.test(status);
 const done = /^(ready|complete|completed|done|success|succeeded)$/.test(status);
 state.topicReady = done && !failed && candidates.length > 0;
 text('topic-status', failed ? printable(data.error || data.message || '选题生成失败，请重新生成。') : candidates.length ? `已收到 ${candidates.length} 个候选，请点击选择。${done ? '' : '其余候选生成中…'}` : '选题正在构思中，每 2 秒更新…');
 if (state.topic) text('restore-status', '选题 ID：' + state.topic + ' · 已保存时可从最近选题恢复。');
 text('candidate-help', `共 ${candidates.length} 个候选 · 请亲自点选一个，不会默认选择。`);
 $('generate-topics').disabled = !done && !failed; updateCreate();
 return !done && !failed;
}
async function pollTopic(id) {
 if (state.topic !== id) return;
 if (state.topicBusy) { state.topicTimer = setTimeout(() => pollTopic(id), 2000); return; }
 const epoch = state.topicEpoch; state.topicBusy = true; let again = true;
 try { const data = await api('/api/topics?id=' + encodeURIComponent(id)); if (state.topic === id && state.topicEpoch === epoch) again = renderTopics(data); }
 catch (error) { if (state.topic === id) text('topic-status', error.message + ' · 2 秒后重试查询，不会重新提交。'); }
 finally { state.topicBusy = false; if (again && state.topic === id && state.topicEpoch === epoch) state.topicTimer = setTimeout(() => pollTopic(id), 2000); }
}
$('topic-form').addEventListener('submit', async event => {
 event.preventDefault(); if (state.topicSubmitting || state.creating || !$('topic-form').reportValidity()) return;
 const input = { genre: $('genre').value, idea: $('idea').value.trim(), chapters: Number($('chapters').value) };
 const cached = savedTopics()[input.genre + ':' + input.chapters];
 if (cached?.id && (cached.input?.idea || '') === input.idea) { await restoreTopic(cached.id, cached); return; }
 invalidateTopic(); const epoch = state.topicEpoch; state.topicSubmitting = true; $('generate-topics').disabled = true; state.topicInput = input;
 text('topic-status', '正在提交选题请求…');
 try {
  const data = await api('/api/topics', input); if (!data.id) throw new Error('服务没有返回选题 ID。');
  if (state.topicEpoch !== epoch) {
   const saved = savedTopics(); saved[input.genre + ':' + input.chapters] = { id: data.id, input, data, saved: Date.now() };
   try { localStorage.setItem(topicStorageKey, JSON.stringify(saved)); refreshRecent(); } catch { text('restore-status', '请记下已有选题 ID：' + data.id); }
   return;
  }
  state.topic = data.id; if (renderTopics(data)) state.topicTimer = setTimeout(() => pollTopic(data.id), 2000);
 } catch (error) { if (state.topicEpoch === epoch) { text('topic-status', error.message + ' · 不会自动重复提交。'); $('generate-topics').disabled = false; } }
 finally { state.topicSubmitting = false; }
});
for (const id of ['genre', 'chapters', 'idea']) $(id).addEventListener('input', () => { invalidateTopic(); $('generate-topics').disabled = !state.config; });
$('length').addEventListener('change', () => { if ($('length').value !== 'custom') $('chapters').value = $('length').value; invalidateTopic(); $('generate-topics').disabled = !state.config; });
$('chapters').addEventListener('input', () => { $('length').value = [...$('length').options].some(o => o.value === $('chapters').value) ? $('chapters').value : 'custom'; });
function quickGenre(pattern, chapters) {
 if (state.creating) { text('topic-status', '书籍正在提交，请稍候再切换题材。'); return; }
 const genre = state.config?.genres?.find(g => pattern.test(g.name) || pattern.test(g.id));
 if (!genre) { text('topic-status', '配置中暂未找到该题材，请从题材列表选择。'); return; }
 $('genre').value = genre.id; $('chapters').value = String(chapters); $('length').value = String(chapters); invalidateTopic(); $('generate-topics').disabled = false; text('topic-status', `已选择${genre.name} · ${chapters}章。点击生成选题开始。`); const cached = savedTopics()[genre.id + ':' + chapters];
 if (cached?.id) { restoreTopic(cached.id, cached); return; }
 $('generate-topics').focus();
}
$('quick-suspense').addEventListener('click', () => quickGenre(/悬疑|suspense|mystery/i, 5));
$('quick-fantasy').addEventListener('click', () => quickGenre(/玄幻|xuanhuan|fantasy/i, 10));
$('model').addEventListener('change', updateCreate);
$('book-form').addEventListener('submit', async event => {
 event.preventDefault(); if (!$('book-form').reportValidity() || $('create-book').disabled) return;
 state.creating = true; updateCreate(); text('book-status', '正在创建书籍，请勿重复提交…');
 try {
  const result = await api('/api/books', { topic_id: state.topic, candidate: state.candidate, genre: state.topicInput.genre, chapters: state.topicInput.chapters, model: $('model').value, polish: $('polish').checked, budget: Number($('budget').value), deai_budget: Number($('deai-budget').value), jobs: Number($('jobs').value) });
  if (!result.id) throw new Error('服务没有返回书籍 ID，请先查看书库再决定是否重试。');
  text('book-status', '已开书，写作任务已提交。可继续开下一本，书库每 3 秒更新。');
  state.candidate = null; $('candidates').querySelectorAll('input').forEach(r => { r.checked = false; r.closest('label').classList.remove('selected'); });
  await refreshBooks();
 } catch (error) { text('book-status', error.message + ' 如提交结果不明，请先检查书库，避免重复开书。'); }
 finally { state.creating = false; updateCreate(); }
});
const statusNames = {
 queued: '等待写作', pending: '等待写作', running: '写作中', writing: '写作中', planning: '规划中',
 queued_polish: '等待去 AI', polishing: '润色中', postprocessing: '后处理中', postprocess_running: '后处理中', paused: '已暂停',
 pausing: '暂停中', done: '已完成', completed: '已完成', complete: '已完成', ready: '就绪',
 failed: '生成失败', error: '运行异常', blocked: '已阻塞', budget_exceeded: '预算已用尽',
 budget_paused: '预算暂停', interrupted: '运行中断', postprocess_failed: '后处理失败',
 orphaned: '运行已失联', stopped: '已停止', unknown: '结果待确认'
};
const number = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
const money = value => number(value) === null ? '—' : '$' + Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 });
const count = value => number(value) === null ? '—' : Number(value).toLocaleString('zh-CN');
const statusLabel = book => statusNames[String(book.status || '').toLowerCase()] || book.status || '状态待更新';
const isComplete = book => /^(complete|completed|done|success|succeeded)$/.test(String(book.status || '').toLowerCase());
function creationDate(value) { if (!value) return '创建时间未知'; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false }); }
function etaLabel(value) { return number(value) === null ? '预计剩余时间待估算' : '预计剩余 ' + Math.max(0, Math.ceil(Number(value))) + ' 秒'; }
function scoreLabel(value) { return number(value) === null ? '检测评分：暂无' : '检测评分：' + value; }
function bookStats(book) {
 return `${count(book.chapters ?? 0)} / ${count(book.total)} 章 · ${count(book.words ?? 0)} 字 · 已知 ${money(book.known)} + Unknown 上界 ${money(book.unknown_bound)} · 预算占用 ${money(book.cost)}`;
}
function bookDetails(book) {
 return `${book.genre || '题材待更新'} · ${creationDate(book.created)} · ${scoreLabel(book.score)} · ${etaLabel(book.eta)}`;
}
function bookUsage(book) {
 return `写作 ${money(book.writer_cost)} / 预算 ${money(book.budget)}（余额 ${money(book.writer_remaining)}） · 去 AI ${money(book.deai_cost)} / 预算 ${money(book.deai_budget)}（余额 ${money(book.deai_remaining)}） · 后处理累计 ${money(book.postprocess_cost)} · 调用 ${count(book.calls)} 次 · Token ${count(book.tokens)} · Unknown ${count(book.unknown)} 次 · 去 AI：${book.polish === true ? '开启' : '关闭'}`;
}
async function action(id, name) {
 if (state.pendingActions.has(id)) return;
 state.pendingActions.add(id); updateActionControls();
 text('action-status', name === 'postprocess' ? '正在提交后处理，请勿重复点击…' : '正在提交操作…');
 try {
  const result = await api('/api/action', { id, action: name });
  const detail = printable(result.message || result.status);
  text('action-status', (name === 'postprocess' ? '后处理请求已提交，最终稿与检测回执将随阅读区同步。' : '操作已提交。') + (detail ? ' ' + detail : ''));
  await refreshBooks(); if (state.readerId === id) await refreshReader();
 } catch (error) { text('action-status', error.message + ' · 未自动重试，请先核对任务状态。'); }
 finally { state.pendingActions.delete(id); updateActionControls(); }
}
function actionButtons(book) {
 const wrapper = el('div', null, 'book-actions');
 if (book.owned !== true) { wrapper.append(el('span', '历史书籍 · 只读', 'muted')); return wrapper; }
 const status = String(book.status || '').toLowerCase(); let choices = [];
 if (isComplete(book)) choices = [['postprocess', '运行去AI后处理']];
 else if (status === 'paused' || status === 'budget_paused') choices = [['resume', '继续当前阶段']];
 else if (/^(failed|error|blocked|interrupted|postprocess_failed|orphaned|stopped|budget_exceeded)$/.test(status)) choices = [['retry', '从检查点重试']];
 else if (/^(running|writing|planning|queued|queued_polish|pending|polishing|postprocessing|postprocess_running)$/.test(status)) choices = [['pause', '暂停']];
 else wrapper.append(el('span', status === 'unknown' ? '结果待确认，请先核对回执' : '暂无可用操作', 'muted'));
 for (const [name, label] of choices) {
  const button = el('button', label); button.type = 'button'; button.dataset.action = name; button.dataset.bookId = String(book.id);
  button.setAttribute('aria-label', label + ' · ' + (book.title || book.id)); button.disabled = state.pendingActions.has(String(book.id));
  if (name === 'postprocess') button.title = '手动提交后处理；复用缓存还是产生新调用，由后端返回的验证结果决定。';
  button.addEventListener('click', () => action(String(book.id), name)); wrapper.append(button);
 }
 return wrapper;
}
function updateActionControls() {
 document.querySelectorAll('button[data-action]').forEach(button => { button.disabled = state.pendingActions.has(button.dataset.bookId); });
}
function syncActions(container, book) {
 const signature = JSON.stringify([book.id, book.title, book.status, book.owned]);
 if (container.dataset.signature !== signature) { container.dataset.signature = signature; container.replaceChildren(actionButtons(book)); }
 updateActionControls();
}
function renderBookCard(book) {
 const id = String(book.id); let entry = state.bookCards.get(id);
 if (!entry) {
  const card = el('article', null, 'book-card'); card.dataset.bookId = id;
  const badge = el('span', null, 'badge'); const open = el('button', null, 'book-title'); open.type = 'button'; open.dataset.bookId = id;
  open.addEventListener('click', () => openBook(id));
  const details = el('p', null, 'book-details'); const stats = el('p', null, 'book-stats'); const usage = el('p', null, 'muted');
  const step = el('p', null, 'book-step'); const progress = document.createElement('progress'); progress.setAttribute('aria-label', '章节进度');
  const controls = el('div'); card.append(badge, open, details, stats, usage, step, progress, controls);
  entry = { card, badge, open, details, stats, usage, step, progress, controls }; state.bookCards.set(id, entry);
 }
 const setText = (node, value) => { const next = String(value); if (node.textContent !== next) node.textContent = next; };
 setText(entry.badge, statusLabel(book)); entry.badge.dataset.status = String(book.status || '');
 setText(entry.open, book.title || '未命名作品'); setText(entry.details, bookDetails(book)); setText(entry.stats, bookStats(book));
 setText(entry.usage, bookUsage(book)); setText(entry.step, book.step || '等待阶段更新');
 entry.progress.max = Math.max(1, Number(book.total) || 1); entry.progress.value = Math.max(0, Number(book.chapters) || 0);
 entry.progress.setAttribute('aria-valuetext', `${book.chapters ?? 0} / ${book.total ?? '—'} 章`);
 syncActions(entry.controls, { ...book, id }); return entry.card;
}
async function refreshBooks() {
 if (state.booksBusy) return; state.booksBusy = true;
 try {
  const result = await api('/api/books');
  const books = (Array.isArray(result.books) ? result.books : []).slice().sort((a, b) => Number(b.owned === true) - Number(a.owned === true) || (new Date(b.created).getTime() || 0) - (new Date(a.created).getTime() || 0));
  const ids = new Set(books.map(book => String(book.id)));
  for (const [id, entry] of state.bookCards) { if (!ids.has(id)) { entry.card.remove(); state.bookCards.delete(id); } }
  books.forEach((book, index) => { const card = renderBookCard(book); const current = $('books').children[index]; if (current !== card) $('books').insertBefore(card, current || null); });
  text('spending-value', `${money(result.spending)} 已占用 / ${money(result.limit)} 上限`);
  text('spending-note', number(result.limit) !== null && number(result.spending) !== null ? `剩余 ${money(Math.max(0, result.limit - result.spending))} · 以服务端回读为准` : '等待预算回读');
  $('spending-summary').classList.toggle('budget-tight', number(result.limit) !== null && number(result.spending) !== null && result.spending >= result.limit);
  text('library-status', books.length ? `${books.length} 本作品 · 当前任务优先 · 每 3 秒更新 · 最近同步 ${new Date().toLocaleTimeString('zh-CN')}` : '书架还空着。选定一个故事，第一本书就从这里开始。');
 } catch (error) { text('library-status', error.message + ' · 3 秒后重试，保留上次书库内容。'); }
 finally { state.booksBusy = false; }
}
function clearReader() {
 state.book = null; $('chapter-select').replaceChildren(); delete $('chapter-select').dataset.signature; text('reader-title', '正在打开…');
 for (const id of ['reader-meta', 'draft', 'final', 'bible', 'outline', 'report', 'chapter-title']) text(id, '');
 $('reader-actions').replaceChildren(); delete $('reader-actions').dataset.signature; updateDownloads();
}
async function openBook(id) {
 state.readerId = String(id); clearReader(); $('reader').hidden = false; text('reader-status', '正在读取书籍…');
 await refreshReader(); $('reader').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
async function refreshReader() {
 if (!state.readerId || state.readerBusy) return; state.readerBusy = true; const id = state.readerId;
 try {
  const book = await api('/api/book?id=' + encodeURIComponent(id)); if (state.readerId !== id) return;
  const selected = $('chapter-select').value;
  const chapters = (Array.isArray(book.chapters) ? book.chapters : []).slice().sort((a, b) => Number(a.number) - Number(b.number));
  book.chapters = chapters; state.book = book;
  text('reader-title', book.meta?.title || '未命名作品'); text('reader-meta', [statusLabel(book.meta || {}), bookDetails(book.meta || {}), bookStats(book.meta || {}), bookUsage(book.meta || {})].join(' · '));
  syncActions($('reader-actions'), { ...book.meta, id });
  const chapterSignature = JSON.stringify(chapters.map(chapter => [chapter.number, chapter.title]));
  if ($('chapter-select').dataset.signature !== chapterSignature) {
   $('chapter-select').dataset.signature = chapterSignature; $('chapter-select').replaceChildren();
   for (const chapter of chapters) { const option = el('option', `第${chapter.number}章 ${chapter.title || ''}`); option.value = String(chapter.number); $('chapter-select').append(option); }
   if ([...$('chapter-select').options].some(option => option.value === selected)) $('chapter-select').value = selected;
  }
  text('bible', printable(book.bible) || '人物圣经尚未生成。'); text('outline', printable(book.outline) || '章纲尚未生成。');
  renderChapter(); updateDownloads();
  if (!$('reader-status').dataset.transient) text('reader-status', '阅读内容每 3 秒同步；缺失的最终稿会明确保留空缺。');
 } catch (error) { readerMessage(error.message); }
 finally { state.readerBusy = false; }
}
function selectedChapter() { return state.book?.chapters?.find(chapter => String(chapter.number) === $('chapter-select').value); }
function renderChapter() {
 const chapter = selectedChapter();
 text('chapter-title', chapter ? `第${chapter.number}章 ${chapter.title || ''}` : '章节正在写作中');
 text('draft', chapter?.draft || '草稿尚未生成。'); text('final', chapter?.final || '最终稿尚未生成。去 AI 关闭时，请先阅读草稿；完成后可另行后处理。');
 text('report', printable(chapter?.report) || '暂无检测回执。');
 const chapters = state.book?.chapters || []; const index = chapters.indexOf(chapter);
 $('prev-chapter').disabled = index <= 0; $('next-chapter').disabled = index < 0 || index >= chapters.length - 1;
 $('copy-chapter').disabled = !chapter?.[$('version').value]?.trim();
 $('copy-book').disabled = !chapters.length;
}
function updateDownloads() {
 const version = $('version').value;
 const hasContent = (state.book?.chapters || []).some(chapter => typeof chapter[version] === 'string' && chapter[version].trim());
 for (const format of ['md', 'docx', 'txt', 'epub', 'fanqie']) {
  const link = $('download-' + format); link.setAttribute('download', ''); link.setAttribute('aria-disabled', String(!hasContent));
  if (hasContent && state.readerId) link.href = '/download?id=' + encodeURIComponent(state.readerId) + '&format=' + format + '&version=' + version;
  else link.removeAttribute('href');
 }
}
function readerMessage(message) { text('reader-status', message); $('reader-status').dataset.transient = 'true'; }
async function copyText(value, message = '已复制。') {
 if (!value) { readerMessage('所选版本还没有正文。'); return; }
 try { await navigator.clipboard.writeText(value); readerMessage(message); }
 catch { readerMessage('浏览器未允许剪贴板访问，请直接选中正文复制。'); }
}
$('copy-chapter').addEventListener('click', () => {
 const chapter = selectedChapter(); const body = chapter?.[$('version').value];
 copyText(typeof body === 'string' && body.trim() ? `第${chapter.number}章 ${chapter.title || ''}\n\n${body}` : '');
});
$('copy-book').addEventListener('click', () => {
 const chapters = state.book?.chapters || []; const version = $('version').value; const total = number(state.book?.meta?.total);
 if (!total || !Number.isInteger(total) || total < 1) { readerMessage('全书目标章数尚未确认，无法复制为全书。'); return; }
 const byNumber = new Map(chapters.map(chapter => [Number(chapter.number), chapter])); let missing = 0;
 for (let index = 1; index <= total; index++) { const body = byNumber.get(index)?.[version]; if (typeof body !== 'string' || !body.trim()) missing++; }
 if (missing) { readerMessage(`${version === 'final' ? '最终稿' : '草稿'}缺少 ${missing} / ${total} 章，已拒绝复制全书。请等待生成完成；当前可复制已有单章。`); return; }
 const ordered = Array.from({ length: total }, (_, index) => byNumber.get(index + 1));
 copyText(`${state.book.meta.title || '未命名作品'}\n\n` + ordered.map(chapter => `第${chapter.number}章 ${chapter.title || ''}\n\n${chapter[version]}`).join('\n\n'), `已复制全书${version === 'final' ? '最终稿' : '草稿'}，共 ${total} 章。`);
});
function changeChapter(delta) {
 const chapters = state.book?.chapters || []; const index = chapters.indexOf(selectedChapter()); const next = chapters[index + delta];
 if (!next) return; $('chapter-select').value = String(next.number); renderChapter();
}
$('prev-chapter').addEventListener('click', () => changeChapter(-1)); $('next-chapter').addEventListener('click', () => changeChapter(1));
$('chapter-select').addEventListener('change', renderChapter);
$('version').addEventListener('change', () => { renderChapter(); updateDownloads(); });
$('read-mode').addEventListener('change', () => { $('comparison').dataset.view = $('read-mode').value; });
$('close-reader').addEventListener('click', () => { state.readerId = null; $('reader').hidden = true; });
$('refresh-books').addEventListener('click', refreshBooks);
loadConfig(); refreshBooks(); setInterval(() => { refreshBooks(); refreshReader(); }, 3000);
})();
