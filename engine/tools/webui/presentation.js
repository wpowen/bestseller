import { el, count } from './shared.js';
// Planning artifacts stay on disk; readers see named story fields rather than serialized schemas.
const roles = { protagonist: '主角', support: '配角', romance: '感情角色', antagonist: '对手', action: '行动', daily: '日常' };
const labels = {
 reader_expectation: '读者期待', selling_points: '卖点', power: '能力规则', interest: '利益',
 stages: '舞台变化', stage: '舞台', layer: '层次', truth: '真相', reveal_not_before: '最早揭示卷',
 main_foreshadows: '全书伏笔', close_volume: '回收卷', initial_dashboard: '初始状态',
 strength: '实力', wealth: '财富', status: '地位', affiliation: '归属', start: '起始章', end: '结束章',
 change: '变化', gain: '收获', exit_volume: '退场卷', attraction: '吸引点', obstacle: '阻碍',
 relationship_stages: '关系发展', ladder_volume: '登场卷', gender: '性别', address: '称呼',
 active_desire: '当前愿望', personality_tags: '性格', catchphrase: '口头习惯', never_do: '说话禁区',
 structure: '场景结构', required_event: '关键事件', hook_type: '悬念类型', where: '地点',
 choice: '选择', emotion: '情绪', kind: '场景类型', opening_conflict: '开篇冲突', callback_events: '前情呼应',
 title: '书名', premise: '故事简介', selling_point: '卖点', reader_promise: '读者期待',
 world: '故事世界', world_rules: '世界规则', rules: '规则', levels: '成长等级', factions: '势力',
 golden_finger: '金手指', cost: '代价', limit: '限制', truth_layers: '三层真相',
 characters: '人物', protagonist: '主角', antagonists: '对手', romance: '感情线',
 name: '姓名', role: '身份', desire: '愿望', wants: '想要', fear: '恐惧', flaw: '缺点',
 bottom_line: '底线', voice: '说话方式', speech: '说话方式', growth_end: '成长终点',
 main_threads: '全书主线', foreshadowing: '伏笔', volumes: '分卷', volume: '卷号',
 chapters: '章节', chapter: '章号', number: '章号', summary: '梗概', conflict: '冲突',
 events: '事件', must_happen: '必须发生', hook: '章末悬念', ending_hook: '章末悬念',
 protagonist_desire: '人物愿望', character_desires: '人物愿望', core_conflict: '核心冲突',
 key_events: '关键事件', opening: '开场', scenes: '场景', goal: '目标', stakes: '代价',
 description: '内容', setting: '背景', outline: '章纲', type: '类型', payoff: '本章收获',
};
function planning(value) {
 const box = el('div', null, 'planning-view');
 function append(parent, data) {
  if (Array.isArray(data)) {
   for (const item of data) {
    const section = el('div', null, 'planning-item');
    append(section, item);
    if (section.childNodes.length) parent.append(section);
   }
  } else if (data && typeof data === 'object') {
   for (const [key, item] of Object.entries(data)) {
    const label = labels[key] || (/^[\u3400-\u9fff、（） ]+$/.test(key) ? key : null);
    if (!label || item == null || item === '') continue;
    const section = el('section');
    section.append(el('h3', label));
    append(section, item);
    if (section.childNodes.length > 1) parent.append(section);
   }
  } else if (data != null && (roles[String(data)] || !/^[a-z][a-z0-9_-]*$/i.test(String(data)))) {
   parent.append(el('p', roles[String(data)] || String(data)));
  }
 }
 append(box, value);
 if (!box.childNodes.length) box.append(el('p', '暂无可展示的规划内容。', 'muted'));
 return box;
}
function checks(chapters) {
 const box = el('div');
 for (const chapter of chapters || []) {
  const check = chapter.checks || {}, report = chapter.report || {};
  const section = el('section');
  section.append(el('h3', `第 ${chapter.number} 章`));
  if (typeof check.within_band === 'boolean') section.append(el('p', `字数范围：${check.within_band ? '符合' : '需留意'}`));
  if (Array.isArray(check.hard_conflicts)) section.append(el('p', `一致性问题：${check.hard_conflicts.length} 项`));
  for (const [key, label] of Object.entries({ warnings: '提示', state_issues: '人物与状态', quality_issues: '情节建议', remaining: '待留意' })) {
   if (Array.isArray(check[key]) && check[key].length) section.append(el('p', `${label}：${check[key].length} 项`));
  }
  if (report.after_score != null) section.append(el('p', `润色后参考分：${count(report.after_score)}`));
  if (chapter.deai_notice) section.append(el('p', chapter.deai_notice));
  if (section.childNodes.length === 1) section.append(el('p', '暂无检测结果。', 'muted'));
  box.append(section);
 }
 if (!box.childNodes.length) box.append(el('p', '章节完成后显示检测结果。', 'muted'));
 return box;
}
export { planning, checks };
