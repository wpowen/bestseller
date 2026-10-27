'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const html = fs.readFileSync(path.resolve(__dirname, '../src/bestseller/web/novel_design_dossier.html'), 'utf8');
const script = html.match(/<script>\n([\s\S]*?)\n<\/script>/)[1];
const start = script.indexOf('  async function postJson');
const end = script.indexOf('  async function editArtifact', start);
assert(start >= 0 && end > start, 'conception flow was not found');

const elements = new Map();
for (const id of [
  'conceptionApprovalPanel', 'conceptionApprovalMeta', 'conceptionApprovalStatus',
  'conceptionHook', 'conceptionContract', 'conceptionSource',
  'approveConceptionBtn', 'conceptionApprovalTaskStatus',
]) elements.set(id, { hidden: true, textContent: '', innerHTML: '', disabled: false });
const $ = selector => elements.get(selector.replace(/^#/, '')) || {
  hidden: false, textContent: '', innerHTML: '', disabled: false,
};
const obj = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const norm = value => String(value ?? '').trim().toLowerCase();
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[ch]));

let snapshotStatus = 'pending_user_approval';
let canonicalHash = 'hash-before';
let enterAttempts = 0;
const calls = [];
const makeOptions = () => ({
  snapshot: {
    id: 'snap-1', status: snapshotStatus, version_no: 1,
    content_hash: canonicalHash, lock_version: 3,
  },
  canonical: {
    option_id: 'canonical:snap-1:hash-before',
    hook_card: { one_liner: 'A concrete hook.' },
    concept_contract: {
      schema_version: 'concept-contract.v2',
      story_spine: { who: 'Mira', wants: 'escape', why_now: 'the gate closes', against: 'the warden', question: 'what will freedom cost?' },
      seriality_proof: { ok: true },
    },
    source: { snapshot_id: 'snap-1', snapshot_hash: canonicalHash },
  },
});
const fetch = async (url, init = {}) => {
  calls.push({ url, init });
  if (url.endsWith('/conception/options')) return { ok: true, status: 200, json: async () => makeOptions() };
  if (url.endsWith('/conception/approve')) {
    snapshotStatus = 'canonical';
    canonicalHash = 'hash-after-audit';
    return { ok: true, status: 200, json: async () => ({
      ok: true, conception: { status: 'approved', snapshot_id: 'snap-1', content_hash: canonicalHash },
    }) };
  }
  if (url.endsWith('/enter-writing')) {
    enterAttempts += 1;
    if (enterAttempts === 1) return { ok: false, status: 503, json: async () => ({ error: 'queue unavailable' }) };
    return { ok: true, status: 202, json: async () => ({ task_id: 'task-1' }) };
  }
  throw new Error(`unexpected ${url}`);
};

const context = {
  console, fetch, $, obj, norm, esc, slug: 'demo',
  conceptionState: { options: null, approved: false, approvalInFlight: false },
  window: { location: { href: '' } },
};
vm.runInNewContext(`(function () {\n${script.slice(start, end)}\n globalThis.api = { loadConceptionOptions, approveConceptionAndContinue };\n})()`, context);

(async () => {
  await context.api.loadConceptionOptions();
  assert.strictEqual(elements.get('approveConceptionBtn').textContent, '批准构思并继续规划');
  await context.api.approveConceptionAndContinue();
  assert.strictEqual(calls.filter(call => call.url.endsWith('/conception/approve')).length, 1);
  assert.strictEqual(enterAttempts, 1);
  assert.strictEqual(context.conceptionState.approved, true);
  await context.api.approveConceptionAndContinue();
  assert.strictEqual(calls.filter(call => call.url.endsWith('/conception/approve')).length, 1);
  assert.strictEqual(enterAttempts, 2);
  assert.strictEqual(context.window.location.href, '/quickstart?open_task=task-1');

  snapshotStatus = 'validated';
  await context.api.loadConceptionOptions();
  assert.strictEqual(elements.get('conceptionApprovalPanel').hidden, true);

  context.conceptionState = {
    options: { ...makeOptions(), snapshot: { ...makeOptions().snapshot, status: 'pending_user_approval', lock_version: null } },
    approved: false, approvalInFlight: false,
  };
  const before = calls.length;
  await context.api.approveConceptionAndContinue();
  assert.strictEqual(calls.length, before);
  console.log('novel design dossier UI behavior: PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
