"""Prepare a real browser, mutate only the fixture's temporary manuscripts, verify polling."""
import json
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CLI = Path.home() / '.codex/skills/playwright/scripts/playwright_cli.sh'
info = json.loads(Path('/tmp/bookweb-ui-fixture.json').read_text())
root = Path(info['root'])
assert root.name.startswith('bookweb-ui-round2-') and root.is_dir()
assert info['origin'] == 'http://127.0.0.1:18789'


def browser(filename):
    output = subprocess.check_output([str(CLI), '-s=bookweb-ui', 'run-code', '--filename=' + str(filename)], cwd=REPO, text=True)
    if '### Error' in output:
        raise RuntimeError(output)
    return json.loads(output.split('### Result\n', 1)[1].split('\n', 1)[0])


with tempfile.TemporaryDirectory(prefix='bookweb-refresh-check-') as tmp:
    prep = Path(tmp) / 'prepare.js'
    prep.write_text('''async page => {
 await page.setViewportSize({width:1440,height:1000});
 await page.goto('http://127.0.0.1:18789/#/read/webui%2Fsample/3');
 await page.locator('#chapter-title').filter({hasText:'第 3 章'}).waitFor();
 await page.locator('#read-modes [data-mode=final]').click();
 await page.locator('#read-scroll').evaluate(el => el.scrollTop = 500);
 return {chapter:3,scroll:await page.locator('#read-scroll').evaluate(el=>el.scrollTop)};
}''')
    before = browser(prep)
    assert before['scroll'] == 500
    folder = root / 'webui/sample'
    source = folder / 'final/chapter-003.md'
    source.write_text(source.read_text() + '\n\n实时刷新验收：新增的一句终稿。\n')
    for name in ('run.json', 'web.json'):
        path = folder / name
        data = json.loads(path.read_text())
        if name == 'run.json':
            data['book']['chapters'] = 6
        else:
            data['chapters'] = 6
        path.write_text(json.dumps(data))
    (folder / 'chapter-006.body.md').write_text('第六章的实时草稿。')
    (folder / 'chapters/006.json').write_text(json.dumps({'title': '实时出现的第六盏灯'}))
    result = dict(before=before, after=browser(HERE / 'verify_refresh.js'))
    (HERE / 'refresh-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
