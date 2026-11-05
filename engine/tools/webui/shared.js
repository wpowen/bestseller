const $ = (id) => document.getElementById(id);
// Shared session data; each view owns its rendering and interaction state.
const state = {
 config: null, book: null, readerId: null, booksBusy: false,
 topic: null, candidate: null, topicInput: null, topicTimer: null,
 topicBusy: false, topicReady: false, topicSubmitting: false, creating: false,
 topicEpoch: 0, topicRenderKey: null, candidates: [], edits: {},
 pendingActions: new Set(),
};
const text = (id, value) => {
 const next = value == null ? '' : String(value);
 if ($(id).textContent !== next) $(id).textContent = next;
};
const printable = (value) => (typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2));
async function api(path, body) {
 const response = await fetch(
  path,
  body === undefined ? { cache: 'no-store' } : {
   method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Bookweb-Source': 'web' }, body: JSON.stringify(body) },
 );
 const raw = await response.text();
 let data;
 try {
  data = raw ? JSON.parse(raw) : {};
 } catch {
  throw new Error('服务返回了非 JSON 内容，请检查后端。');
 }
 if (!response.ok) throw new Error(printable(data.error || data.detail || data.message) || `请求失败 (${response.status})`);
 return data;
}
const el = (tag, content, cls) => {
 const node = document.createElement(tag);
 if (content != null) node.textContent = String(content);
 if (cls) node.className = cls;
 return node;
};
const statusNames = {
 queued: '等待写作',
 pending: '等待写作',
 running: '写作中',
 writing: '写作中',
 planning: '规划中',
 queued_polish: '等待去 AI',
 polishing: '润色中',
 postprocessing: '润色中',
 postprocess_running: '润色中',
 paused: '已暂停',
 concurrency_paused: '等待写作名额',
 success: '已完成',
 succeeded: '已完成',
 waiting: '等待接口',
 quota_exhausted: '额度用尽',
 pausing: '暂停中',
 done: '已完成',
 completed: '已完成',
 complete: '已完成',
 ready: '就绪',
 failed: '生成失败',
 error: '出错',
 blocked: '已阻塞',
 budget_paused: '已暂停',
 interrupted: '运行中断',
 postprocess_failed: '润色出错',
 orphaned: '运行已失联',
 stopped: '已停止',
 unknown: '结果待确认',
};
const number = (value) => (value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null);
const money = (value) =>
 number(value) === null ? '—' : '$' + Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 });
const count = (value) => (number(value) === null ? '—' : Number(value).toLocaleString('zh-CN'));
const statusLabel = (book) => statusNames[String(book.status || '').toLowerCase()] || '状态待更新';
const isComplete = (book) => /^(complete|completed|done|success|succeeded)$/.test(String(book.status || '').toLowerCase());
function creationDate(value) {
 if (!value) return '未记录';
 const date = new Date(value);
 return Number.isNaN(date.getTime()) ? '未记录' : date.toLocaleString('zh-CN', { hour12: false });
}
function bookUsage(book) {
 return [
  `写作费用 ${money(book.writer_cost)}`,
  `去 AI 费用 ${money(book.deai_cost)}`,
  `模型请求 ${count(book.calls)} 次 · 计费用量 ${count(book.tokens)} 词元`,
  `用量未返回 ${count(book.unknown)} 次（费用可能仍在估算）`,
  `自动去 AI：${book.polish === true ? '开启' : '关闭'}`,
 ].join(' · ');
}
function stageName(value) {
 const names = { draft: '正文写作', write: '正文写作', audit: '章节检查', extract: '整理人物与进度',
  polish: '去 AI 润色', book: '正文写作', bible: '故事圣经', outline: '章节规划', judge: '选题排序' };
 const stage = String(value || '');
 if (names[stage]) return names[stage];
 const chapter = stage.match(/^(?:ch|chapter-?)(\d+)[-_](draft|write|audit|extract|polish)$/);
 if (chapter) return `第 ${Number(chapter[1])} 章 · ${names[chapter[2]]}`;
 const group = stage.match(/^generate-(\d+)$/);
 if (group) return `构思第 ${Number(group[1]) + 1} 组`;
 return '';
}
function runtimeInfo(book) {
 if (book.status === 'waiting') {
  const details = [stageName(book.stage), `接口繁忙，正在等待 · 已重试 ${count(book.retry || 0)} 次`].filter(Boolean);
  const value = book.next_retry_at;
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  if (value != null && !Number.isNaN(date.getTime())) {
   details.push('下次尝试 ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }));
  }
  return details.join(' · ');
 }
 if (book.status === 'quota_exhausted') return '模型账户额度用尽，充值后可继续。';
 if (/^(failed|error|blocked|interrupted|postprocess_failed|orphaned)$/.test(book.status))
  return '任务遇到问题，修复后可从已保存的进度继续。';
 return /^(running|writing|planning|polishing)$/.test(book.status) ? stageName(book.stage) : '';
}
const link = (label, hash, cls) => {
 const a = el('a', label, cls);
 a.href = hash;
 return a;
};
const button = (label, run, cls) => {
 const b = el('button', label, cls);
 b.type = 'button';
 b.onclick = run;
 return b;
};
const bookURL = (id) => '#/book/' + encodeURIComponent(id);
const readURL = (id, n = 1) => '#/read/' + encodeURIComponent(id) + '/' + n;
const stored = (key, fallback) => {
 try {
  return JSON.parse(localStorage.getItem(key)) ?? fallback;
 } catch {
  return fallback;
 }
};
const persist = (key, value) => {
 try {
  localStorage.setItem(key, JSON.stringify(value));
 } catch {}
};
function notify(message) {
 text('action-status', message);
 $('toast').hidden = false;
}
export { $, state, text, printable, api, el, money, count, statusLabel, isComplete, creationDate, bookUsage, runtimeInfo,
 link, button, bookURL, readURL, stored, persist, notify };
