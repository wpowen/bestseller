'use strict';
(() => {
const $ = id => document.getElementById(id);
const state = { config: null, topic: null, candidate: null, topicInput: null, topicTimer: null, topicBusy: false, creating: false, booksBusy: false, book: null, readerId: null, readerBusy: false, topicReady: false, topicSubmitting: false, topicEpoch: 0, topicRenderKey: null, candidates: [], edits: {}, bookCards: new Map(), pendingActions: new Set() };
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
 const busy = state.topicSubmitting || state.creating || (!!state.topic && !state.topicReady && $('generate-topics').disabled);
 $('refresh-topics').disabled = busy || !state.topic;
 document.querySelectorAll('[data-direction]').forEach(button => { button.disabled = busy || !state.topicReady; });
 document.querySelectorAll('.candidate-editor input,.candidate-editor textarea').forEach(input => { input.disabled = state.creating; });
 const enabled = state.config?.models?.some(m => String(m.id) === $('model').value && m.enabled === true);
 $('create-book').disabled = state.creating || state.topicSubmitting || state.candidate === null || !state.topic || !state.topicReady || !enabled;
 text('create-book', state.creating ? '正在创建，请稍候…' : state.candidate === null ? '选定故事后，开始写作' : '以这个故事，开始写作');
 const model = state.config?.models?.find(m => String(m.id) === $('model').value);
 text('model-info', model ? `${model.name}${model.reason ? ' · ' + model.reason : ''}` : '没有可用写手时，无法开书。');
}
async function loadConfig() {
 try {
  const config = await api('/api/config'); state.config = config;
  $('genre').replaceChildren();
  for (const genre of config.genres || []) { const option = el('option', genre.name); option.value = genre.id; $('genre').append(option); }
  genreTags();
  $('channel').replaceChildren(el('option', '不限')); $('channel').firstChild.value = '';
  for (const name of config.channels || ['男频','女频']) { const option = el('option', name); option.value = name; $('channel').append(option); }
  $('model').replaceChildren();
  for (const model of config.models || []) { const option = el('option', `${model.name}${model.enabled === true ? '' : ' · 不可用'}${model.reason ? ' — ' + model.reason : ''}`); option.value = model.id; option.disabled = model.enabled !== true; $('model').append(option); }
  const preferred = config.models?.find(m => m.id === 'flash' && m.enabled === true) || config.models?.find(m => m.enabled === true);
  $('model').value = preferred?.id || '';
  $('generate-topics').disabled = !(config.genres || []).length;
  text('topic-status', '已连接。每次生成都会抽取新方向；已有选题可从上方恢复。');
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
 saved[state.topic] = { id: state.topic, input, genreName: state.config?.genres?.find(g => g.id === input.genre)?.name, saved: Date.now() };
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
  input.idea = data.idea ?? input.idea ?? ''; input.channel = data.channel ?? input.channel ?? ''; input.direction = data.direction;
  $('genre').value = input.genre; $('chapters').value = input.chapters; $('idea').value = input.idea || ''; $('channel').value = input.channel || '';
  $('length').value = [...$('length').options].some(o => o.value === String(input.chapters)) ? String(input.chapters) : 'custom';
  if (renderTopics(data)) state.topicTimer = setTimeout(() => pollTopic(id), 2000);
  step(2);
  text('restore-status', '已恢复。请重新点击一个候选，再点“开始写作”。'); $('candidate-section').scrollIntoView({behavior:'smooth', block:'start'});
 } catch (error) { text('restore-status', error.message); updateCreate(); }
 finally { $('restore-topic').disabled = false; }
}
$('restore-topic').addEventListener('click', () => restoreTopic($('topic-id').value.trim()));
$('restore-recent').addEventListener('click', () => { const entry = savedTopics()[$('recent-topics').value]; if (entry) restoreTopic(entry.id, entry); else text('restore-status', '请先从本地最近选题中选择一项。'); });
refreshRecent();
function invalidateTopic() {
 if (state.creating) return;
 clearTimeout(state.topicTimer); state.topicEpoch++; state.topic = null; state.candidate = null; state.topicInput = null; state.topicReady = false; state.topicRenderKey = null; state.candidates = []; state.edits = {}; $('to-settings').disabled = true;
 $('candidates').replaceChildren(); $('candidate-section').hidden = true; updateCreate();
}
const cardFields = {title:'书名', hook:'一句话钩子', tags:'卖点标签（用顿号分隔）', premise:'人物与冲突', golden_finger_rule:'金手指规则', opening:'开场画面', engine:'长篇引擎'};
function currentCard(index) { return state.edits[index] || state.candidates[index]; }
function editorFor(candidate, index) {
 const details = el('details', null, 'candidate-editor'); details.append(el('summary', '改一改 · 修改后可直接开书'));
 const rich = Object.keys(cardFields).some(key => !['title','premise'].includes(key) && key in candidate);
 for (const [key, name] of Object.entries(cardFields)) {
  if (!rich && !['title','premise'].includes(key)) continue;
  const label = el('label', name); const input = el(key === 'title' || key === 'tags' ? 'input' : 'textarea');
  input.id = `edit-${index}-${key}`; label.htmlFor = input.id; input.value = key === 'tags' ? (candidate.tags || []).join('、') : candidate[key] || '';
  input.required = key !== 'engine' || state.topicInput.chapters >= 30;
  if (key !== 'tags') input.maxLength = {title:rich ? 40 : 80,hook:30,premise:120,golden_finger_rule:120,opening:100,engine:240}[key];
  input.addEventListener('input', () => {
   state.edits[index] = {...currentCard(index), [key]: key === 'tags' ? input.value.split(/[、，,\n]+/).map(s => s.trim()).filter(Boolean) : input.value};
   selectCandidate(index); text('book-status', '卡片修改将在开书时由服务端复检；原批次保留。');
  });
  label.append(input); details.append(label);
 }
 return details;
}
function selectCandidate(index) {
 state.candidate = index;
 $('candidates').querySelectorAll('.candidate').forEach((node, i) => { node.classList.toggle('selected', i === index); node.querySelector('input[type=radio]').checked = i === index; });
 updateCreate(); $('to-settings').disabled = false;
}
function renderTopics(data) {
 if (data.id) state.topic = data.id;
 saveTopic(data);
 const candidates = Array.isArray(data.candidates) ? data.candidates : [];
 $('candidate-section').hidden = !candidates.length;
 const renderKey = JSON.stringify([candidates, data.collisions || []]);
 if (state.topicRenderKey !== renderKey) {
  state.topicRenderKey = renderKey; state.candidates = candidates; state.edits = {}; state.candidate = null; $('candidates').replaceChildren();
  candidates.forEach((candidate, index) => {
   const card = el('article', null, 'candidate'); const label = el('label', null, 'candidate-choice');
   const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'candidate'; radio.id = `candidate-${index}`; radio.value = String(index);
   radio.addEventListener('change', () => selectCandidate(index));
   label.append(radio, el('span', String(index + 1).padStart(2, '0'), 'candidate-number'), el('h3', candidate.title)); card.append(label);
   if (candidate.hook) card.append(el('p', candidate.hook, 'candidate-hook'));
   if (Array.isArray(candidate.tags)) card.append(el('p', candidate.tags.map(t => `【${t}】`).join(''), 'candidate-tags'));
   card.append(el('p', candidate.premise));
   const fields = el('dl', null, 'candidate-fields');
   for (const key of ['golden_finger_rule', 'opening', 'engine']) { if (key in candidate) fields.append(el('dt', cardFields[key]), el('dd', candidate[key] || '暂无')); }
   card.append(fields);
   const scores = candidate.scores || {};
   card.append(el('p', '判官四轴（参考）：' + ['不可补全','点击欲','长篇引擎','好懂'].map(k => `${k} ${scores[k] ?? '—'}`).join(' · '), 'candidate-scores'));
   card.append(el('p', candidate.market_note || `熟壳：${candidate.axes?.shell || '未知'}；今日同壳新书：未知`, 'candidate-market'));
   const actions = el('div', null, 'candidate-actions');
   if (candidate.axes?.shell && candidate.axes?.variable) for (const [mode, name] of [['both','沿这个方向再来 · 两组各6个'],['shell','保留熟壳，换新变量'],['variable','保留新变量，换熟壳']]) {
    const button = el('button', name); button.type = 'button'; button.dataset.direction = mode;
    button.addEventListener('click', () => {
     if ([...card.querySelectorAll('.candidate-editor input,.candidate-editor textarea')].some(input => !input.reportValidity())) return;
     submitTopics({mode, candidate:currentCard(index)});
    }); actions.append(button);
   }
   card.append(actions, editorFor(candidate, index)); $('candidates').append(card);
  });
 }
 if (Array.isArray(data.collisions) && data.collisions.length && !$('candidates').querySelector('.market-collision')) {
  const notes = el('aside', null, 'market-collision'); notes.append(el('h3', '已避开的榜单撞车'));
  for (const row of data.collisions) notes.append(el('p', `${row.title} · ${(row.reasons || []).join('；')}（已剔除）`));
  $('candidates').append(notes);
 }
 const status = String(data.status || '').toLowerCase();
 const failed = /failed|error|cancelled/.test(status);
 const done = /^(ready|complete|completed|done|success|succeeded)$/.test(status);
 state.topicReady = done && !failed && candidates.length > 0;
 text('topic-status', failed ? printable(data.error || data.message || '选题生成失败，原始记录已保留。') : done ? `实际展示 ${candidates.length} 个候选。${data.notice || ''}` : '选题正在构思中，每 2 秒更新…');
 const usage = data.usage || {};
 text('topic-cost', `本批 ${money(data.cost_usd ?? usage.cost ?? data.cost)} · 上限 $0.05` + (usage.unknown ? ` · Unknown ${usage.unknown} 次，上界 ${money(usage.unknown_bound)}` : ''));
 text('topic-notes', [data.judge_status, data.market?.status].filter(Boolean).join(' · '));
 if (state.topic) text('restore-status', '选题 ID：' + state.topic + ' · 可从最近选题恢复。');
 text('candidate-help', `共 ${candidates.length} 个候选 · 请亲自点选一个；参考分不能代替读者判断。`);
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
async function submitTopics(direction) {
 if (state.topicSubmitting || state.creating || !$('topic-form').reportValidity()) return;
 const input = { genre: $('genre').value, idea: $('idea').value.trim(), chapters: Number($('chapters').value), channel:$('channel').value };
 if (direction) input.direction = direction;
 clearTimeout(state.topicTimer); state.topicEpoch++; const epoch = state.topicEpoch; state.topicSubmitting = true; $('generate-topics').disabled = true; updateCreate();
 text('topic-status', direction ? '正在沿选定方向抽取新组合…' : '正在提交新批次，不读取旧批次缓存…');
 try {
  const data = await api('/api/topics', input); if (!data.id) throw new Error('服务没有返回选题 ID。');
  if (state.topicEpoch !== epoch) {
   const saved = savedTopics(); saved[data.id] = { id: data.id, input, saved: Date.now() };
   try { localStorage.setItem(topicStorageKey, JSON.stringify(saved)); refreshRecent(); } catch { text('restore-status', '请记下已有选题 ID：' + data.id); }
   return;
  }
  state.topic = data.id; state.topicInput = input; state.candidate = null; state.topicReady = false; state.topicRenderKey = null;
  step(2);
  if (renderTopics(data)) state.topicTimer = setTimeout(() => pollTopic(data.id), 2000);
 } catch (error) { if (state.topicEpoch === epoch) { text('topic-status', error.message + ' · 不会自动重复提交。'); $('generate-topics').disabled = false; } }
 finally { state.topicSubmitting = false; updateCreate(); }
}
$('topic-form').addEventListener('submit', event => { event.preventDefault(); submitTopics(); });
$('refresh-topics').addEventListener('click', () => submitTopics(state.topicInput?.direction));
for (const id of ['genre', 'chapters', 'idea', 'channel']) $(id).addEventListener('input', () => { invalidateTopic(); $('generate-topics').disabled = !state.config; });
$('length').addEventListener('change', () => { if ($('length').value !== 'custom') $('chapters').value = $('length').value; invalidateTopic(); $('generate-topics').disabled = !state.config; });
$('chapters').addEventListener('input', () => { $('length').value = [...$('length').options].some(o => o.value === $('chapters').value) ? $('chapters').value : 'custom'; });
function quickGenre(pattern, chapters) {
 if (state.creating) { text('topic-status', '书籍正在提交，请稍候再切换题材。'); return; }
 const genre = state.config?.genres?.find(g => pattern.test(g.name) || pattern.test(g.id));
 if (!genre) { text('topic-status', '配置中暂未找到该题材，请从题材列表选择。'); return; }
 $('genre').value = genre.id; $('chapters').value = String(chapters); $('length').value = String(chapters); invalidateTopic(); $('generate-topics').disabled = false; text('topic-status', `已选择${genre.name} · ${chapters}章。点击生成选题开始。`);
 $('generate-topics').focus();
}
$('quick-suspense').addEventListener('click', () => quickGenre(/悬疑|suspense|mystery/i, 5));
$('quick-fantasy').addEventListener('click', () => quickGenre(/玄幻|xuanhuan|fantasy/i, 10));
$('model').addEventListener('change', updateCreate);
$('book-form').addEventListener('submit', async event => {
 event.preventDefault(); if (!$('book-form').reportValidity() || $('create-book').disabled) return;
 const selected = $('candidates').children[state.candidate];
 if ([...selected.querySelectorAll('.candidate-editor input,.candidate-editor textarea')].some(input => !input.reportValidity())) return;
 state.creating = true; updateCreate(); text('book-status', '正在创建书籍，请勿重复提交…');
 try {
  const result = await api('/api/books', { ...(state.edits[state.candidate] ? {candidate_edit:state.edits[state.candidate]} : {}), topic_id: state.topic, candidate: state.candidate, genre: state.topicInput.genre, chapters: state.topicInput.chapters, model: $('model').value, polish: $('polish').checked, budget: Number($('budget').value), deai_budget: Number($('deai-budget').value), jobs: Number($('jobs').value) });
  if (!result.id) throw new Error('服务没有返回书籍 ID，请先查看书库再决定是否重试。');
  text('book-status', '已开书，写作任务已提交。可继续开下一本，书库每 3 秒更新。');
  state.candidate = null; $('candidates').querySelectorAll('input[type=radio]').forEach(r => { r.checked = false; r.closest('.candidate').classList.remove('selected'); });
  await refreshBooks(); location.hash = '#/book/' + encodeURIComponent(result.id);
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
  await refreshBooks(); if (state.readerId === id) await route();
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
const link = (label, hash, cls) => { const a = el('a', label, cls); a.href = hash; return a; };
const button = (label, run, cls) => { const b = el('button', label, cls); b.type = 'button'; b.onclick = run; return b; };
const bookURL = id => '#/book/' + encodeURIComponent(id);
const readURL = (id, n = 1) => '#/read/' + encodeURIComponent(id) + '/' + n;
const stored = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
const persist = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} };
let books = [], archived = [], filter = 'all', tab = '目录', routeEpoch = 0, undoId, managed;
let prefs = {...{width:'medium',size:19,line:2,bg:'paper',mode:'final'}, ...stored('bookweb-reader', {})};
function notify(message) { text('action-status', message); $('toast').hidden = false; }
function step(n) {
 if (n === 3 && state.candidate === null) return;
 document.querySelectorAll('[data-pane]').forEach(p => { p.hidden = Number(p.dataset.pane) !== n; });
 document.querySelectorAll('#steps button').forEach(b => b.classList.toggle('on', Number(b.dataset.step) === n));
 text('picked', currentCard(state.candidate)?.title || '请先选定故事');
 text('estimate', `费用预估：本批选题最多 $0.05；写作独立上限 ${money($('budget').value)}${$('polish').checked ? ' + 去 AI ' + money($('deai-budget').value) : ''}。实际费用按回执累计。`);
}
function genreTags() {
 for (const group of ['男频','女频','其他']) {
  const row = el('div', null, 'genre-group'); row.append(el('span', group));
  for (const g of state.config.genres.filter(g => (/romance|lovers|harem|romantasy|villainess/.test(g.id) ? '女频' : /xuanhuan|xianxia|urban|history|suspense/.test(g.id) ? '男频' : '其他') === group)) {
   row.append(button(g.name, () => { $('genre').value = g.id; $('channel').value = group === '其他' ? '' : group; $('genre').dispatchEvent(new Event('input')); }));
  }
  $('genre-tags').append(row);
 }
}
function renderLibrary() {
 const visible = books.filter(b => (filter === 'all' || (filter === 'complete' ? isComplete(b) : filter === 'paused' ? /paused/.test(b.status) : /running|polishing|queued/.test(b.status))) && (b.title+b.genre).includes($('search').value));
 visible.sort((a,b) => $('sort').value === 'title' ? a.title.localeCompare(b.title, 'zh') : $('sort').value === 'chapters' ? b.chapters-b.chapters : (b.created||'').localeCompare(a.created||''));
 $('books').replaceChildren(...visible.map(b => {
  const card = el('article', null, 'book-card'); const cover = link('', bookURL(b.id), 'cover');
  cover.dataset.genre = b.genre; cover.append(el('small', b.genre), el('h2', b.title));
  const body = el('div', null, 'book-body'); const badge = el('span', statusLabel(b), 'badge'); badge.dataset.status = b.status;
  const progress = el('progress'); progress.max = Math.max(1, b.total); progress.value = b.chapters; progress.setAttribute('aria-label', '章节进度');
  const actions = actionButtons(b); actions.prepend(link('阅读', readURL(b.id, stored('chapter:'+b.id, 1)), 'primary button'));
  const menu = el('details', null, 'more'); menu.append(el('summary', '⋯'));
  menu.append(link('书籍详情', bookURL(b.id)), link('导出', bookURL(b.id)+'?tab=导出'));
  if (b.owned) for (const [name,label] of [['rename','重命名'],['archive','移到归档（可恢复）'],['delete','永久删除…']]) menu.append(button(label, () => manage(b, name), name === 'delete' ? 'danger' : 'ghost'));
  actions.append(menu); body.append(badge, progress, el('p', `${b.chapters}/${b.total} 章 · ${count(b.words)} 字 · ${money(b.cost)}`, 'muted'), actions);
  card.append(cover, body); return card;
 }), link('＋\n开一本新书', '#/new', 'new-tile'));
 $('archives').replaceChildren(...archived.map(b => { const row = el('article', null, 'panel'); row.append(el('h3', b.title), button('恢复到书库', () => manage(b, 'restore')), button('永久删除…', () => manage(b, 'delete'), 'danger')); return row; }));
 text('archive-toggle', `已归档 ${archived.length} 本`);
}
async function refreshBooks() {
 if (state.booksBusy) return; state.booksBusy = true;
 try {
  const [result, archive] = await Promise.all([api('/api/books'), api('/api/archive')]);
  books = result.books; archived = archive.books;
  text('spending-value', `${money(result.spending)} / ${money(result.limit)}`);
  $('spending-meter').max = Math.max(.01, result.limit); $('spending-meter').value = result.spending;
  text('library-status', `${books.length} 本故事 · ${books.filter(isComplete).length} 本已完成`); renderLibrary();
 } catch (error) { text('library-status', error.message); }
 finally { state.booksBusy = false; }
}
async function manage(book, name, title) {
 if (name === 'delete' || name === 'rename') {
  managed = {book, name}; text('dialog-title', name === 'delete' ? '永久删除这本书？' : '重命名');
  text('dialog-hint', name === 'delete' ? `此操作不可撤销。请输入完整书名：${book.title}。运行中或仍有进程的书会被拒绝。` : '输入新的书名。');
  $('confirm-title').value = name === 'rename' ? book.title : ''; text('dialog-status', ''); $('manage-dialog').showModal(); return;
 }
 try {
  await api('/api/action', {id:book.id, action:name, ...(title === undefined ? {} : {title})});
  undoId = name === 'archive' ? book.id : null; $('undo').hidden = !undoId;
  notify(name === 'archive' ? '已移到归档，可撤销。' : '操作已完成。'); await refreshBooks();
 } catch (error) { notify(error.message); }
}
function chapters() {
 const map = new Map((state.book?.chapters || []).map(c => [c.number, c]));
 return Array.from({length:state.book?.meta.total || 0}, (_,i) => map.get(i+1) || {number:i+1,title:'尚未写作',draft:'',final:''});
}
function renderDetail() {
 const b = state.book.meta; const last = Math.min(b.chapters || 1, stored('chapter:'+b.id, 1));
 const cover = el('div', null, 'cover'); cover.dataset.genre = b.genre; cover.append(el('small', b.genre), el('h2', b.title));
 const info = el('div'); info.append(el('span', statusLabel(b), 'badge'), el('h1', b.title), el('p', b.synopsis || '故事简介尚未生成。', 'muted'), el('p', `${b.chapters}/${b.total} 章　${count(b.words)} 字　${money(b.cost)}　${creationDate(b.created)}`, 'stats'), link(`继续阅读 · 第 ${last} 章`, readURL(b.id, last), 'primary button'), actionButtons(b), el('p', bookUsage(b), 'muted'));
 $('book-hero').replaceChildren(cover, info);
 $('book-tabs').replaceChildren(...['目录','故事圣经','章纲','检测报告（参考）','导出'].map(t => button(t, () => { tab=t; renderDetail(); }, t===tab?'on':'')));
 const content = $('book-content'); content.replaceChildren();
 if (tab === '目录') {
  const grid = el('div', null, 'chapter-grid');
  for (const c of chapters()) { const a = c.draft ? link(`第 ${c.number} 章　${c.title}`, readURL(b.id,c.number)) : el('span', `第 ${c.number} 章　${c.title}`, 'muted'); a.append(el('small', `${c.final?'终稿':c.draft?'草稿':'未写'} · ${(c.final||c.draft||'').replace(/[^\u3400-\u9fff]/g,'').length} 字`)); grid.append(a); }
  content.append(grid);
 } else if (tab === '导出') {
  const select = el('select'); select.id = 'export-version'; select.setAttribute('aria-label','导出版本');
  for (const [v,t] of [['draft','草稿'],['final','终稿']]) { const o = el('option',t); o.value=v; select.append(o); }
  const links = el('div',null,'toolbar'); const update = () => links.replaceChildren(...['md','docx','txt','epub','fanqie'].map((f,i) => link(['Markdown','Word','TXT','EPUB','番茄分章 ZIP'][i], '/download?id='+encodeURIComponent(b.id)+'&format='+f+'&version='+select.value,'button')));
  select.onchange=update; update(); content.append(select, links, button('复制全书', () => copyBook(select.value)));
 } else if (tab === '故事圣经') {
  const grid = el('div',null,'chapter-grid');
  for (const c of state.book.characters || []) { const card = el('article',null,'panel'); card.append(el('h3',c.name || '人物'), el('pre',printable(c))); grid.append(card); }
  content.append(grid, el('pre',state.book.bible || '故事圣经尚未生成。'));
 } else content.append(el('pre', tab === '章纲' ? state.book.outline || '章纲尚未生成。' : printable(state.book.chapters.map(c => ({number:c.number,report:c.report})))));
}
async function route() {
 const epoch = ++routeEpoch; const match = location.hash.match(/^#\/(book|read)\/(.+?)(?:\?(.*))?$/);
 const reading = match?.[1] === 'read'; const view = match ? reading?'reader':'detail' : location.hash.startsWith('#/new')?'create':'library';
 for (const id of ['library','create','detail','reader']) $(id).hidden = id !== view;
 $('top').hidden = reading; document.body.classList.toggle('reading', reading);
 if (!match) { state.readerId = null; return; }
 try {
  const tail = match[2]; const cut = reading ? tail.lastIndexOf('/') : -1;
  const id = decodeURIComponent(reading ? tail.slice(0,cut) : tail); state.readerId = id;
  const book = await api('/api/book?id='+encodeURIComponent(id)); if (epoch !== routeEpoch) return; state.book=book;
  if (reading) { state.chapter=Number(tail.slice(cut+1)); renderChapter(); $('read-scroll').scrollTop=0; }
  else { tab = new URLSearchParams(match[3]).get('tab') || '目录'; renderDetail(); }
 } catch (error) { notify(error.message); }
}
function renderTOC() {
 const nav = $('toc-links'); nav.replaceChildren(); let volume;
 for (const c of chapters().filter(c => `${c.number} ${c.title}`.includes($('toc-search').value))) {
  const v = c.volume || Math.ceil(c.number/25); if (v !== volume) { nav.append(el('h4',`第 ${v} 卷`)); volume=v; }
  const a = c.draft ? link(`第 ${c.number} 章　${c.title}`,readURL(state.readerId,c.number),c.number===state.chapter?'on':'') : el('span',`第 ${c.number} 章　未写`,'muted');
  a.setAttribute('aria-current',c.number===state.chapter?'page':'false'); nav.append(a);
 }
}
function prose(node, value, original) {
 node.replaceChildren(); const previous = new Set((original || '').split(/\n+/));
 for (const line of (value || '这个版本尚未生成。').split(/\n+/).filter(Boolean)) {
  if (/^#/.test(line)) continue;
  const p = el('p'); p.append(el(original && !previous.has(line) ? 'mark' : 'span',line)); node.append(p);
 }
}
function renderChapter() {
 const c = chapters().find(c => c.number===state.chapter); const b = state.book.meta;
 text('reader-title',`${b.title} · ${c?.title || '章节未生成'}`); $('close-reader').href=bookURL(b.id);
 text('chapter-title',`第 ${state.chapter} 章　${c?.title || ''}`); text('chapter-meta',b.title+' · '+(prefs.mode==='compare'?'草稿 / 终稿对照':'静下来，读一个故事'));
 prose($('draft'), c?.draft); prose($('final'), c?.final, prefs.mode==='compare'?c?.draft:null);
 $('comparison').dataset.mode=prefs.mode; $('reading-progress').max=Math.max(1,b.total); $('reading-progress').value=state.chapter;
 $('prev-chapter').disabled=state.chapter<=1; $('next-chapter').disabled=!chapters().find(n => n.number===state.chapter+1)?.draft;
 $('copy-chapter').disabled=!(prefs.mode==='draft'?c?.draft:c?.final);
 if (c?.draft) persist('chapter:'+b.id,state.chapter); renderTOC(); applyPrefs();
}
function applyPrefs() {
 const r=$('reader'); r.style.setProperty('--width',({narrow:'640px',medium:'820px',wide:'1080px',full:'100%'})[prefs.width]);
 r.style.setProperty('--font-size',prefs.size+'px'); r.style.setProperty('--leading',prefs.line); r.dataset.background=prefs.bg;
 document.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('on',b.dataset.mode===prefs.mode));
 document.querySelectorAll('[data-pref]').forEach(b => b.classList.toggle('on',String(prefs[b.dataset.pref])===b.dataset.value)); persist('bookweb-reader',prefs);
}
function toggleSide() { $('reader').classList.toggle('noside'); $('side-toggle').setAttribute('aria-expanded',String(!$('reader').classList.contains('noside'))); }
function flip(delta) { const n=state.chapter+delta; if (chapters().find(c => c.number===n)?.draft) location.hash=readURL(state.readerId,n); }
async function copyText(value) {
 try { if (!value) throw Error('所选版本尚未生成。'); await navigator.clipboard.writeText(value); text('reader-status','已复制。'); }
 catch (e) { notify(e.message || '剪贴板访问失败，请选中正文复制。'); }
}
function copyBook(version) {
 const rows=chapters(); if (!rows.length || rows.some(c => !c[version])) return notify('所选版本缺章，不能复制为全书。');
 copyText(state.book.meta.title+'\n\n'+rows.map(c => `第 ${c.number} 章 ${c.title}\n\n${c[version]}`).join('\n\n'));
}
for (const [key,label,options] of [['size','字号',[[17,'小'],[19,'中'],[22,'大'],[25,'特大']]],['width','版心宽度',[['narrow','窄'],['medium','适中'],['wide','宽'],['full','铺满']]],['line','行距',[[1.6,'紧凑'],[2,'舒适'],[2.4,'宽松']]],['bg','背景',[['paper','纸'],['eye','护眼'],['night','夜间']]]]) {
 const group=el('div',null,'setting-group'); group.append(el('p',label));
 for (const [value,name] of options) { const b=button(name, () => { prefs[key]=value; applyPrefs(); }); b.dataset.pref=key; b.dataset.value=value; group.append(b); }
 $('setting-groups').append(group);
}
document.querySelectorAll('[data-step]').forEach(b => b.onclick=() => step(Number(b.dataset.step)));
for (const id of ['budget','deai-budget','polish']) $(id).addEventListener('input',() => step(3));
$('filters').onclick=e => { if (e.target.dataset.filter) { filter=e.target.dataset.filter; [...$('filters').children].forEach(b => b.classList.toggle('on',b===e.target)); renderLibrary(); } };
$('search').oninput=renderLibrary; $('sort').onchange=renderLibrary; $('refresh-books').onclick=refreshBooks;
$('archive-toggle').onclick=() => { $('archives').hidden=!$('archives').hidden; };
$('undo').onclick=() => { if (undoId) manage({id:undoId},'restore'); };
$('dialog-cancel').onclick=() => $('manage-dialog').close();
$('manage-form').onsubmit=async e => {
 e.preventDefault(); const {book,name}=managed; const title=$('confirm-title').value;
 if (name==='delete' && title!==book.title) return text('dialog-status','书名不匹配，请输入完整书名。');
 $('dialog-confirm').disabled=true;
 try { await api('/api/action',{id:book.id,action:name,title}); $('manage-dialog').close(); notify('操作已完成。'); await refreshBooks(); }
 catch (error) { text('dialog-status',error.message); }
 finally { $('dialog-confirm').disabled=false; }
};
$('read-modes').onclick=e => { if (e.target.dataset.mode) { prefs.mode=e.target.dataset.mode; renderChapter(); } };
$('aa').onclick=() => { $('read-settings').hidden=!$('read-settings').hidden; $('aa').setAttribute('aria-expanded',String(!$('read-settings').hidden)); };
$('settings-close').onclick=() => { $('read-settings').hidden=true; $('aa').setAttribute('aria-expanded','false'); };
$('side-toggle').onclick=toggleSide; $('drawer-mask').onclick=toggleSide; $('chapter-directory').onclick=toggleSide; $('toc-search').oninput=renderTOC;
$('prev-chapter').onclick=() => flip(-1); $('next-chapter').onclick=() => flip(1);
$('copy-chapter').onclick=() => copyText(chapters().find(c => c.number===state.chapter)?.[prefs.mode==='draft'?'draft':'final']);
document.addEventListener('keydown',e => {
 if ($('reader').hidden || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
 if (e.key==='ArrowLeft' || e.key==='ArrowRight') { e.preventDefault(); flip(e.key==='ArrowLeft'?-1:1); }
 if (e.key.toLowerCase()==='t') toggleSide();
 if (e.key==='Escape') { if (!$('read-settings').hidden) $('settings-close').click(); else location.hash=bookURL(state.readerId); }
});
$('theme').onclick=() => { document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'':'dark'; persist('bookweb-theme',document.documentElement.dataset.theme); };
document.documentElement.dataset.theme=stored('bookweb-theme','');
$('reader').classList.toggle('noside',matchMedia('(max-width:899px)').matches);
window.addEventListener('hashchange',route);
if (!location.hash) history.replaceState(null,'','#/library');
loadConfig(); refreshBooks(); route();
setInterval(async () => { await refreshBooks(); if (state.readerId && !$('detail').hidden) await route(); },3000);
})();
