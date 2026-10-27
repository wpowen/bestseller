from pathlib import Path
import subprocess


QUICKSTART = Path("src/bestseller/web/novel_quickstart.html").read_text(encoding="utf-8")
DESIGN = Path("src/bestseller/web/novel_design_dossier.html").read_text(encoding="utf-8")


def test_navigation_contract_uses_real_task_id_and_prewrite_schema() -> None:
    assert "const bootOpenTaskId = bootParams.get('open_task');" in QUICKSTART
    assert "allTasks.find((item) => item.task_id === bootOpenTaskId)" in QUICKSTART
    assert "未在当前任务列表中找到，请刷新后重试" in QUICKSTART
    assert "enterBtn.disabled = !prewrite.is_approved" in DESIGN
    assert "approveBtn.disabled = !!prewrite.is_approved" in DESIGN
    assert "prewrite.is_approved" in DESIGN
    assert "window.location.href = `/quickstart?open_task=${encodeURIComponent(taskId)}`" in DESIGN
    assert "/read/${encodeURIComponent(slug)}" not in DESIGN[DESIGN.index("async function enterWriting") : DESIGN.index("async function enterWriting") + 1200]


def test_enter_writing_actual_function_guards_task_id_and_deep_links() -> None:
    script = r'''
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
function extract(name) {
  const start = html.indexOf(`async function ${name}(`);
  const open = html.indexOf('{', start); let depth = 0; let quote = '';
  for (let i = open; i < html.length; i++) {
    const c = html[i], prev = html[i - 1];
    if (quote) { if (c === quote && prev !== '\\') quote = ''; continue; }
    if (c === '"' || c === "'" || c === '`') { quote = c; continue; }
    if (c === '{') depth++;
    if (c === '}' && --depth === 0) return html.slice(start, i + 1);
  }
  throw new Error('function not found');
}
const button = { disabled: false };
const status = { textContent: '' };
const $ = (selector) => selector === '#enterWritingBtn' ? button : status;
const window = { location: { set href(v) { window._href = v; } }, _href: '' };
const slug = 'demo-book';
let response = {};
async function postJson() { return response; }
async function load() {}
eval(extract('enterWriting'));
(async () => {
  await enterWriting();
  if (!status.textContent.includes('未返回正文任务 ID')) throw new Error('missing task guard failed');
  if (window._href) throw new Error('missing task navigated');
  response = { task_id: 'task-123' };
  await enterWriting();
  if (window._href !== '/quickstart?open_task=task-123') throw new Error('wrong task deep link');
  console.log('navigation behavior ok');
})();
'''
    result = subprocess.run(
        ["node", "-e", script, "src/bestseller/web/novel_design_dossier.html"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "navigation behavior ok" in result.stdout
