import { $, state, text, printable, api, el, money, button, statusLabel, runtimeInfo } from './shared.js';
function updateCreate() {
 const busy = state.topicSubmitting || state.creating || (!!state.topic && !state.topicReady && $('generate-topics').disabled);
 $('refresh-topics').disabled = busy || !state.topic;
 document.querySelectorAll('[data-direction]').forEach((button) => {
  button.disabled = busy || !state.topicReady;
 });
 document.querySelectorAll('.candidate-editor input,.candidate-editor textarea').forEach((input) => {
  input.disabled = state.creating;
 });
 const enabled = state.config?.models?.some((m) => String(m.id) === $('model').value && m.enabled === true);
 $('create-book').disabled = state.creating || state.topicSubmitting || state.candidate === null || !state.topic || !state.topicReady || !enabled;
 text('create-book', state.creating ? '正在创建，请稍候…' : state.candidate === null ? '选定故事后，开始写作' : '以这个故事，开始写作');
 const model = state.config?.models?.find((m) => String(m.id) === $('model').value);
 text('model-info', model ? `${model.name}${model.reason ? ' · ' + model.reason : ''}` : '没有可用写手时，无法开书。');
}
async function loadConfig() {
 try {
  const config = await api('/api/config');
  state.config = config;
  $('genre').replaceChildren();
  for (const genre of config.genres || []) {
   const option = el('option', genre.name);
   option.value = genre.id;
   $('genre').append(option);
  }
  genreTags();
  $('channel').replaceChildren(el('option', '不限'));
  $('channel').firstChild.value = '';
  for (const name of config.channels || ['男频', '女频']) {
   const option = el('option', name);
   option.value = name;
   $('channel').append(option);
  }
  $('model').replaceChildren();
  for (const model of config.models || []) {
   const option = el('option', `${model.name}${model.enabled === true ? '' : ' · 不可用'}${model.reason ? ' — ' + model.reason : ''}`);
   option.value = model.id;
   option.disabled = model.enabled !== true;
   $('model').append(option);
  }
  const preferred = config.models?.find((m) => m.id === 'flash' && m.enabled === true) || config.models?.find((m) => m.enabled === true);
  $('model').value = preferred?.id || '';
  $('generate-topics').disabled = !(config.genres || []).length;
  text('topic-status', '已连接。每次生成都会抽取新方向；已有选题可从上方恢复。');
  updateCreate();
 } catch (error) {
  text('topic-status', error.message + ' 刷新页面可重试。');
 }
}
const topicStorageKey = 'bestseller-b-topics-v1';
function savedTopics() {
 try {
  const saved = JSON.parse(localStorage.getItem(topicStorageKey) || '{}');
  return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
 } catch {
  return {};
 }
}
function refreshRecent() {
 const select = $('recent-topics');
 if (!select) return;
 const selected = select.value;
 select.replaceChildren(el('option', '选择已保存的选题'));
 select.firstChild.value = '';
 for (const [key, entry] of Object.entries(savedTopics()).sort((a, b) => (b[1].saved || 0) - (a[1].saved || 0))) {
  if (!entry?.id) continue;
  const option = el('option', `${entry.genreName || entry.input?.genre || '已有选题'} · ${entry.input?.chapters || '—'}章 · ${entry.id}`);
  option.value = key;
  select.append(option);
 }
 if ([...select.options].some((o) => o.value === selected)) select.value = selected;
}
function saveTopic(data) {
 if (!state.topic || !state.topicInput) return;
 const saved = savedTopics();
 const input = state.topicInput;
 saved[state.topic] = { id: state.topic, input, genreName: state.config?.genres?.find((g) => g.id === input.genre)?.name, saved: Date.now() };
 try {
  localStorage.setItem(topicStorageKey, JSON.stringify(saved));
  refreshRecent();
 } catch {
  text('restore-status', '浏览器未允许本地保存，请记下选题 ID：' + state.topic);
 }
 if ($('topic-id')) $('topic-id').value = state.topic;
}
async function restoreTopic(id, entry) {
 if (!id || state.creating) {
  text('restore-status', '请先输入已有的选题 UUID。');
  return;
 }
 clearTimeout(state.topicTimer);
 state.topicEpoch++;
 const epoch = state.topicEpoch;
 state.candidate = null;
 state.topic = id;
 state.topicReady = false;
 state.topicRenderKey = null;
 $('candidates').replaceChildren();
 updateCreate();
 entry = entry || Object.values(savedTopics()).find((item) => item?.id === id);
 const input = entry?.input || { genre: $('genre').value, chapters: Number($('chapters').value), idea: $('idea').value.trim() };
 state.topicInput = input;
 $('restore-topic').disabled = true;
 text('restore-status', '正在读取已有选题，不会重新生成…');
 try {
  const data = await api('/api/topics?id=' + encodeURIComponent(id));
  if (state.topic !== id || state.topicEpoch !== epoch) return;
  if (data.genre || data.chapters) {
   input.genre = data.genre || input.genre;
   input.chapters = Number(data.chapters) || input.chapters;
  }
  input.idea = data.idea ?? input.idea ?? '';
  input.channel = data.channel ?? input.channel ?? '';
  input.direction = data.direction;
  $('genre').value = input.genre;
  $('chapters').value = input.chapters;
  $('idea').value = input.idea || '';
  $('channel').value = input.channel || '';
  $('length').value = [...$('length').options].some((o) => o.value === String(input.chapters)) ? String(input.chapters) : 'custom';
  if (renderTopics(data)) state.topicTimer = setTimeout(() => pollTopic(id), 2000);
  step(2);
  text('restore-status', '已恢复。请重新点击一个候选，再点“开始写作”。');
  $('candidate-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
 } catch (error) {
  text('restore-status', error.message);
  updateCreate();
 } finally {
  $('restore-topic').disabled = false;
 }
}
$('restore-topic').addEventListener('click', () => restoreTopic($('topic-id').value.trim()));
$('restore-recent').addEventListener('click', () => {
 const entry = savedTopics()[$('recent-topics').value];
 if (entry) restoreTopic(entry.id, entry);
 else text('restore-status', '请先从本地最近选题中选择一项。');
});
refreshRecent();
function invalidateTopic() {
 if (state.creating) return;
 clearTimeout(state.topicTimer);
 state.topicEpoch++;
 state.topic = null;
 state.candidate = null;
 state.topicInput = null;
 state.topicReady = false;
 state.topicRenderKey = null;
 state.candidates = [];
 state.edits = {};
 $('to-settings').disabled = true;
 $('candidates').replaceChildren();
 $('candidate-section').hidden = true;
 updateCreate();
}
const cardFields = { title: '书名', hook: '一句话钩子', tags: '卖点标签（用顿号分隔）', premise: '人物与冲突', golden_finger_rule: '金手指规则', opening: '开场画面', engine: '长篇引擎', };
function currentCard(index) {
 return state.edits[index] || state.candidates[index];
}
function editorFor(candidate, index) {
 const details = el('details', null, 'candidate-editor');
 details.append(el('summary', '改一改 · 修改后可直接开书'));
 const rich = Object.keys(cardFields).some((key) => !['title', 'premise'].includes(key) && key in candidate);
 for (const [key, name] of Object.entries(cardFields)) {
  if (!rich && !['title', 'premise'].includes(key)) continue;
  const label = el('label', name);
  const input = el(key === 'title' || key === 'tags' ? 'input' : 'textarea');
  input.id = `edit-${index}-${key}`;
  label.htmlFor = input.id;
  input.value = key === 'tags' ? (candidate.tags || []).join('、') : candidate[key] || '';
  input.required = key !== 'engine' || state.topicInput.chapters >= 30;
  if (key !== 'tags') input.maxLength = { title: rich ? 40 : 80, hook: 30, premise: 120, golden_finger_rule: 120, opening: 100, engine: 240 }[key];
  input.addEventListener('input', () => {
   state.edits[index] = {
    ...currentCard(index),
    [key]:
     key === 'tags'
      ? input.value
         .split(/[、，,\n]+/)
         .map((s) => s.trim())
         .filter(Boolean)
      : input.value,
   };
   selectCandidate(index);
   text('book-status', '卡片修改将在开书时由服务端复检；原批次保留。');
  });
  label.append(input);
  details.append(label);
 }
 return details;
}
function selectCandidate(index) {
 state.candidate = index;
 $('candidates')
  .querySelectorAll('.candidate')
  .forEach((node, i) => {
   node.classList.toggle('selected', i === index);
   node.querySelector('input[type=radio]').checked = i === index;
  });
 updateCreate();
 $('to-settings').disabled = false;
}
function renderTopics(data) {
 if (data.id) state.topic = data.id;
 saveTopic(data);
 const candidates = Array.isArray(data.candidates) ? data.candidates : [];
 $('candidate-section').hidden = !candidates.length;
 const renderKey = JSON.stringify([candidates, data.collisions || []]);
 if (state.topicRenderKey !== renderKey) {
  state.topicRenderKey = renderKey;
  state.candidates = candidates;
  state.edits = {};
  state.candidate = null;
  $('candidates').replaceChildren();
  candidates.forEach((candidate, index) => {
   const card = el('article', null, 'candidate');
   const label = el('label', null, 'candidate-choice');
   const radio = document.createElement('input');
   radio.type = 'radio';
   radio.name = 'candidate';
   radio.id = `candidate-${index}`;
   radio.value = String(index);
   radio.addEventListener('change', () => selectCandidate(index));
   label.append(radio, el('span', String(index + 1).padStart(2, '0'), 'candidate-number'), el('h3', candidate.title));
   card.append(label);
   if (candidate.hook) card.append(el('p', candidate.hook, 'candidate-hook'));
   if (Array.isArray(candidate.tags)) card.append(el('p', candidate.tags.map((t) => `【${t}】`).join(''), 'candidate-tags'));
   card.append(el('p', candidate.premise));
   const fields = el('dl', null, 'candidate-fields');
   for (const key of ['golden_finger_rule', 'opening', 'engine']) {
    if (key in candidate) fields.append(el('dt', cardFields[key]), el('dd', candidate[key] || '暂无'));
   }
   card.append(fields);
   const scores = candidate.scores || {};
   card.append(
    el('p', '判官四轴（参考）：' + ['不可补全', '点击欲', '长篇引擎', '好懂'].map((k) => `${k} ${scores[k] ?? '—'}`).join(' · '), 'candidate-scores'),
   );
   card.append(el('p', candidate.market_note || `熟壳：${candidate.axes?.shell || '未知'}；今日同壳新书：未知`, 'candidate-market'));
   const actions = el('div', null, 'candidate-actions');
   if (candidate.axes?.shell && candidate.axes?.variable)
    for (const [mode, name] of [ ['both', '沿这个方向再来 · 两组各6个'], ['shell', '保留熟壳，换新变量'], ['variable', '保留新变量，换熟壳'], ]) {
     const button = el('button', name);
     button.type = 'button';
     button.dataset.direction = mode;
     button.addEventListener('click', () => {
      if ([...card.querySelectorAll('.candidate-editor input,.candidate-editor textarea')].some((input) => !input.reportValidity())) return;
      submitTopics({ mode, candidate: currentCard(index) });
     });
     actions.append(button);
    }
   card.append(actions, editorFor(candidate, index));
   $('candidates').append(card);
  });
 }
 if (Array.isArray(data.collisions) && data.collisions.length && !$('candidates').querySelector('.market-collision')) {
  const notes = el('aside', null, 'market-collision');
  notes.append(el('h3', '已避开的榜单撞车'));
  for (const row of data.collisions) notes.append(el('p', `${row.title} · ${(row.reasons || []).join('；')}（已剔除）`));
  $('candidates').append(notes);
 }
 const status = String(data.status || '').toLowerCase();
 const failed = /failed|error|cancelled|quota_exhausted/.test(status);
 const done = /^(ready|complete|completed|done|success|succeeded)$/.test(status);
 state.topicReady = done && !failed && candidates.length > 0;
 text(
  'topic-status',
  failed
   ? [statusLabel(data), runtimeInfo(data) || printable(data.message || '选题生成失败，原始记录已保留。')].join(' · ')
   : done
     ? `实际展示 ${candidates.length} 个候选。${data.notice || ''}`
     : status === 'waiting' ? `等待接口 · ${runtimeInfo(data)}` : '选题正在构思中，每 2 秒更新…',
 );
 const usage = data.usage || {};
 text(
  'topic-cost',
  `本批已花费 ${money(data.cost_usd ?? usage.cost ?? data.cost)}` +
   (usage.unknown ? ` · ${usage.unknown} 次调用未返回用量，费用暂按最高可能值估算` : ''),
 );
 text('topic-notes', data.market?.status === 'stale' ? '市场参考来自缓存，可能不是最新数据。' : '');
 if (state.topic) text('restore-status', '选题 ID：' + state.topic + ' · 可从最近选题恢复。');
 text('candidate-help', `共 ${candidates.length} 个候选 · 请亲自点选一个；参考分不能代替读者判断。`);
 $('generate-topics').disabled = !done && !failed;
 updateCreate();
 return !done && !failed;
}
async function pollTopic(id) {
 if (state.topic !== id) return;
 if (state.topicBusy) {
  state.topicTimer = setTimeout(() => pollTopic(id), 2000);
  return;
 }
 const epoch = state.topicEpoch;
 state.topicBusy = true;
 let again = true;
 try {
  const data = await api('/api/topics?id=' + encodeURIComponent(id));
  if (state.topic === id && state.topicEpoch === epoch) again = renderTopics(data);
 } catch (error) {
  if (state.topic === id) text('topic-status', error.message + ' · 2 秒后重试查询，不会重新提交。');
 } finally {
  state.topicBusy = false;
  if (again && state.topic === id && state.topicEpoch === epoch) state.topicTimer = setTimeout(() => pollTopic(id), 2000);
 }
}
async function submitTopics(direction) {
 if (state.topicSubmitting || state.creating || !$('topic-form').reportValidity()) return;
 const input = { genre: $('genre').value, idea: $('idea').value.trim(), chapters: Number($('chapters').value), channel: $('channel').value };
 if (direction) input.direction = direction;
 clearTimeout(state.topicTimer);
 state.topicEpoch++;
 const epoch = state.topicEpoch;
 state.topicSubmitting = true;
 $('generate-topics').disabled = true;
 updateCreate();
 text('topic-status', direction ? '正在沿选定方向抽取新组合…' : '正在提交新批次，不读取旧批次缓存…');
 try {
  const data = await api('/api/topics', input);
  if (!data.id) throw new Error('服务没有返回选题 ID。');
  if (state.topicEpoch !== epoch) {
   const saved = savedTopics();
   saved[data.id] = { id: data.id, input, saved: Date.now() };
   try {
    localStorage.setItem(topicStorageKey, JSON.stringify(saved));
    refreshRecent();
   } catch {
    text('restore-status', '请记下已有选题 ID：' + data.id);
   }
   return;
  }
  state.topic = data.id;
  state.topicInput = input;
  state.candidate = null;
  state.topicReady = false;
  state.topicRenderKey = null;
  step(2);
  if (renderTopics(data)) state.topicTimer = setTimeout(() => pollTopic(data.id), 2000);
 } catch (error) {
  if (state.topicEpoch === epoch) {
   text('topic-status', error.message + ' · 不会自动重复提交。');
   $('generate-topics').disabled = false;
  }
 } finally {
  state.topicSubmitting = false;
  updateCreate();
 }
}
$('topic-form').addEventListener('submit', (event) => {
 event.preventDefault();
 submitTopics();
});
$('refresh-topics').addEventListener('click', () => submitTopics(state.topicInput?.direction));
for (const id of ['genre', 'chapters', 'idea', 'channel'])
 $(id).addEventListener('input', () => {
  invalidateTopic();
  $('generate-topics').disabled = !state.config;
 });
$('length').addEventListener('change', () => {
 if ($('length').value !== 'custom') $('chapters').value = $('length').value;
 invalidateTopic();
 $('generate-topics').disabled = !state.config;
});
$('chapters').addEventListener('input', () => {
 $('length').value = [...$('length').options].some((o) => o.value === $('chapters').value) ? $('chapters').value : 'custom';
});
function quickGenre(pattern, chapters) {
 if (state.creating) {
  text('topic-status', '书籍正在提交，请稍候再切换题材。');
  return;
 }
 const genre = state.config?.genres?.find((g) => pattern.test(g.name) || pattern.test(g.id));
 if (!genre) {
  text('topic-status', '配置中暂未找到该题材，请从题材列表选择。');
  return;
 }
 $('genre').value = genre.id;
 $('chapters').value = String(chapters);
 $('length').value = String(chapters);
 invalidateTopic();
 $('generate-topics').disabled = false;
 text('topic-status', `已选择${genre.name} · ${chapters}章。点击生成选题开始。`);
 $('generate-topics').focus();
}
$('quick-suspense').addEventListener('click', () => quickGenre(/悬疑|suspense|mystery/i, 5));
$('quick-fantasy').addEventListener('click', () => quickGenre(/玄幻|xuanhuan|fantasy/i, 10));
$('model').addEventListener('change', updateCreate);
$('book-form').addEventListener('submit', async (event) => {
 event.preventDefault();
 if (!$('book-form').reportValidity() || $('create-book').disabled) return;
 const selected = $('candidates').children[state.candidate];
 if ([...selected.querySelectorAll('.candidate-editor input,.candidate-editor textarea')].some((input) => !input.reportValidity())) return;
 state.creating = true;
 updateCreate();
 text('book-status', '正在创建书籍，请勿重复提交…');
 try {
  const result = await api('/api/books', {
   ...(state.edits[state.candidate] ? { candidate_edit: state.edits[state.candidate] } : {}),
   topic_id: state.topic,
   candidate: state.candidate,
   genre: state.topicInput.genre,
   chapters: state.topicInput.chapters,
   model: $('model').value,
   polish: $('polish').checked,
   jobs: Number($('jobs').value),
  });
  if (!result.id) throw new Error('服务没有返回书籍 ID，请先查看书库再决定是否重试。');
  text('book-status', '已开书，写作任务已提交。可继续开下一本，书库每 3 秒更新。');
  state.candidate = null;
  $('candidates')
   .querySelectorAll('input[type=radio]')
   .forEach((r) => {
    r.checked = false;
    r.closest('.candidate').classList.remove('selected');
   });
  window.dispatchEvent(new Event('books-changed'));
  location.hash = '#/book/' + encodeURIComponent(result.id);
 } catch (error) {
  text('book-status', error.message + ' 如提交结果不明，请先检查书库，避免重复开书。');
 } finally {
  state.creating = false;
  updateCreate();
 }
});
function step(n) {
 if (n === 3 && state.candidate === null) return;
 document.querySelectorAll('[data-pane]').forEach((p) => {
  p.hidden = Number(p.dataset.pane) !== n;
 });
 document.querySelectorAll('#steps button').forEach((b) => b.classList.toggle('on', Number(b.dataset.step) === n));
 text('picked', currentCard(state.candidate)?.title || '请先选定故事');
 text(
  'estimate',
  `费用按调用记录累计显示。正文完成后${$('polish').checked ? '自动继续去 AI 润色' : '保留草稿，可随时手动去 AI'}。`,
 );
}
function genreTags() {
 for (const group of ['男频', '女频', '其他']) {
  const row = el('div', null, 'genre-group');
  row.append(el('span', group));
  for (const g of state.config.genres.filter(
   (g) =>
    (/romance|lovers|harem|romantasy|villainess/.test(g.id) ? '女频' : /xuanhuan|xianxia|urban|history|suspense/.test(g.id) ? '男频' : '其他') ===
    group,
  )) {
   row.append(
    button(g.name, () => {
     $('genre').value = g.id;
     $('channel').value = group === '其他' ? '' : group;
     $('genre').dispatchEvent(new Event('input'));
    }),
   );
  }
  $('genre-tags').append(row);
 }
}
document.querySelectorAll('[data-step]').forEach((b) => (b.onclick = () => step(Number(b.dataset.step))));
$('polish').addEventListener('input', () => step(3));
export { loadConfig };
