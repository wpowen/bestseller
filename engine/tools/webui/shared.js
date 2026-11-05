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
  body === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
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
 postprocessing: '后处理中',
 postprocess_running: '后处理中',
 paused: '已暂停',
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
 postprocess_failed: '后处理失败',
 orphaned: '运行已失联',
 stopped: '已停止',
 unknown: '结果待确认',
};
const number = (value) => (value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null);
const money = (value) =>
 number(value) === null ? '—' : '$' + Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 });
const count = (value) => (number(value) === null ? '—' : Number(value).toLocaleString('zh-CN'));
const statusLabel = (book) => statusNames[String(book.status || '').toLowerCase()] || book.status || '状态待更新';
const isComplete = (book) => /^(complete|completed|done|success|succeeded)$/.test(String(book.status || '').toLowerCase());
function creationDate(value) {
 if (!value) return '创建时间未知';
 const date = new Date(value);
 return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
}
function bookUsage(book) {
 return [
  `写作已花费 ${money(book.writer_cost)}`,
  `去 AI 已花费 ${money(book.deai_cost)}`,
  `后处理累计 ${money(book.postprocess_cost)} · 调用 ${count(book.calls)} 次 · Token ${count(book.tokens)}`,
  `Unknown ${count(book.unknown)} 次 · 去 AI：${book.polish === true ? '开启' : '关闭'}`,
 ].join(' · ');
}
function runtimeInfo(book) {
 const details = [book.stage];
 if (book.status === 'waiting') {
  details.push(`第 ${count(book.retry || 0)} 次重试`);
  const value = book.next_retry_at;
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  if (value != null && !Number.isNaN(date.getTime())) {
   details.push('下次 ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }));
  }
 }
 details.push(book.error || book.step);
 return details.filter(Boolean).join(' · ');
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
