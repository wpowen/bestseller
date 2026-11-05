"""Offline browser acceptance server; temporary manuscripts, no scheduler or models.
Run from any directory: python browser_fixture.py [--port 18789].
Only topic generation is synthetic; HTTP views, actions, creation and exports use
production webui code. No credentials are read. Stop with Ctrl-C.
"""
import argparse
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
import uuid
from unittest.mock import patch

ENGINE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ENGINE), str(ENGINE / 'tools/webui')]
from library import Library, read, save
from jobs import Queue
from server import Handler, ThreadingHTTPServer, config, docx


class OfflineQueue(Queue):
    def topic(self, data):
        # Deliberately synthetic model output, explicitly identified in screenshots.
        key = str(uuid.uuid4())
        card = dict(title='渡口借命，归来的人少了一岁', hook='摆渡人用余生换回失踪乘客',
                    tags=['民俗', '解谜', '逆袭'], premise='摆渡人要在涨潮前找回失踪乘客。',
                    golden_finger_rule='每渡一人可以借回一天寿命', opening='退潮后船底传来敲门声。',
                    engine='找回乘客、寻找失船、揭开渡口身世',
                    axes=dict(shell='民俗悬疑', variable='渡船', desire='守护', emotion='解谜'),
                    scores={'点击欲': 8}, market_note='离线验收样例 · 未调用模型')
        cards = [dict(card, title=f'{card["title"]}·{n}') for n in range(1, 13)]
        result = dict(data, id=key, status='complete', candidates=cards, cost_usd=0,
                      notice='离线合成选题，仅验证界面连接', judge_status='离线样例')
        save(self.library.own / '.topics' / key / 'topic.json', result)
        requests = read(self.library.root / 'topic-requests.json', [])
        save(self.library.root / 'topic-requests.json', requests + [data])
        return result

    def launch(self, *args, **kwargs):
        raise AssertionError('Offline acceptance must never launch a worker')


def seed(lib, key, title, status='complete', complete=True):
    folder = lib.own / key
    save(folder / 'web.json', dict(title=title, status=status, budget=.23, deai_budget=.5,
                                  polish=True, chapters=5, created='2026-11-05T00:00:00Z'))
    save(folder / 'run.json', dict(status='complete' if complete else 'running',
                                  book=dict(chapters=5, prompt_pack='cozy-mystery')))
    save(folder / 'book.yaml', dict(chapters=5, words_per_chapter=2200, prompt_pack='cozy-mystery'))
    save(folder / 'state.json', dict(checkpoint=3, note='must survive pause and resume'))
    save(folder / 'story_bible.yaml', 'title: 渡口归途\npremise: 摆渡人要在涨潮前找回失踪乘客。\n'
         'characters:\n  - name: 林舟\n    role: 主角\n    desire: 找回失踪乘客\n    voice: 说话短促，遇险先问人\n'
         '  - name: 江晚\n    role: 配角\n    desire: 保住渡口\n')
    for n in range(1, 6):
        draft = ('暮色压过渡口。林舟推开木门。江晚没有回头。\n\n'
                 '“船底又响了。”她说。林舟停在门边，把灯举高。\n\n') * 12
        final = draft.replace('林舟推开木门。', '林舟推开吱呀作响的木门。', 1)
        save(folder / f'chapter-{n:03}.body.md', draft)
        save(folder / f'chapters/{n:03}.json', dict(title=f'渡口的第{n}盏灯'))
        save(folder / f'final/chapter-{n:03}.md', final)
        save(folder / f'final/chapter-{n:03}.deai.json', dict(status='polished', complete=True, after_score=7))
        save(folder / f'writing_outlines/{n:03}.json', dict(chapter=n, goal='寻找乘客', conflict='江晚拒绝开船'))
    save(folder / 'calls.json', [dict(usage=dict(prompt_tokens=1000, completion_tokens=1000))])
    save(folder / 'final/.deai/001/http/ledger.json', [dict(usage=dict(prompt_tokens=2000, completion_tokens=1000))])
    save(folder / 'book.md', '# ' + title + '\n\n' + draft)
    (folder / 'book.docx').write_bytes(docx(draft))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18789)
    args = parser.parse_args()
    if args.port == 8787:
        parser.error('production port forbidden')
    with tempfile.TemporaryDirectory(prefix='bookweb-ui-round2-') as tmp:
        root = Path(tmp)
        lib = Library(root)
        with patch('jobs.threading.Thread.start'):
            queue = OfflineQueue(lib, root / 'absent.env', ENGINE, 20)
        seed(lib, 'sample', '渡口归途 · 离线验收')
        seed(lib, 'paused', '待续的灯 · 离线验收', 'paused', False)
        seed(lib, 'running', '正在写作 · 离线验收', 'running', False)
        seed(lib, 'retry', '中断的航程 · 离线验收', 'failed', False)
        http = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        http.library, http.queue = lib, queue
        http.origin = f'http://127.0.0.1:{args.port}'
        http.configuration = config(root / 'absent.env')
        http.configuration['models'][0].update(enabled=True, configured=False, reason='离线验收，禁止真实调用')
        http.configuration['budgets']['total'] = 20
        http.daemon_threads = True
        def stop(*_):
            threading.Thread(target=http.shutdown, daemon=True).start()
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        info = dict(root=str(root), origin=http.origin, scheduler=False, credentials=False, model_calls=0)
        Path('/tmp/bookweb-ui-fixture.json').write_text(json.dumps(info))
        print(json.dumps(info), flush=True)
        try:
            with patch('jobs.subprocess.Popen', side_effect=AssertionError('no paid subprocess')):
                http.serve_forever()
        finally:
            http.server_close()
            queue.exclusive.close()


if __name__ == '__main__':
    main()
