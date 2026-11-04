"""Web-owned queue and public CLI subprocesses. No engine imports/state writes."""
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid
from library import GENRES, costs, now, read, save


def finite(value, low, high):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high: raise ValueError('数值超出允许范围')
    return value


class Queue:
    def __init__(self, library, env, engine, limit=.5):
        self.library, self.env, self.engine, self.limit = library, env, engine, limit
        self.lock = threading.RLock(); self.processes = {}; self.closed = False; self.jobs = int(finite(read(library.own / '.queue.json', {}).get('jobs', 2), 1, 4))
        self.exclusive = (library.own / '.service.lock').open('a')
        fcntl.flock(self.exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for folder in library.own.iterdir():
            data = read(folder / 'web.json', {}) if folder.is_dir() else {}
            if data.get('status') in ('running', 'polishing', 'paused'):
                pid = data.get('pid')
                try:
                    os.kill(pid, 0)
                    data.update(status='orphaned', reason='旧子进程仍存在；禁止重复启动，请回到原服务处理')
                except (OSError, TypeError): data.update(status='interrupted', reason='服务重启，可从检查点重试')
                save(folder / 'web.json', data)
        threading.Thread(target=self.loop, daemon=True).start()

    def charge(self, folder):
        data = read(folder / 'web.json', {})
        base = costs(folder, include_final=False)['cost']
        extra = costs(folder)['cost'] - base if data.get('polish') else 0
        return base + max(0, extra - data.get('external_polish_cost', 0))

    def spending(self):
        folders = [p for p in self.library.own.iterdir() if p.is_dir() and not p.is_symlink()]
        return sum(self.charge(p) for p in folders) + sum(costs(p)['cost'] for p in (self.library.own / '.topics').glob('*') if p.is_dir())

    def committed(self):
        total = self.spending()
        for folder in self.library.own.iterdir():
            if not folder.is_dir() or folder.is_symlink(): continue
            data = read(folder / 'web.json', {})
            if data and data.get('status') != 'complete':
                total += max(0, data['budget'] - self.charge(folder))
        return total

    def child_env(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(self.engine)
        env['PYTHONUNBUFFERED'] = '1'
        for key in ('BOOKENGINE_PHASE', 'BOOKENGINE_RATE_FILE'): env.pop(key, None)
        return env

    def topic(self, data):
        genre = data.get('genre'); chapters = int(finite(data.get('chapters', 5), 1, 2000))
        idea = data.get('idea', '')
        if genre not in GENRES or not isinstance(idea, str) or len(idea) > 3000: raise ValueError('题材或想法无效')
        with self.lock:
            if any(p['kind'] == 'topic' and p['p'].poll() is None for p in self.processes.values()): raise ValueError('选题正在生成，请稍候')
            if self.committed() + .015 > self.limit: raise ValueError('总预算不足以生成选题')
            key = str(uuid.uuid4()); folder = self.library.own / '.topics' / key
            save(folder / 'topic.json', dict(id=key, status='running', genre=genre, chapters=chapters, idea=idea, candidates=[]))
            command = [sys.executable, '-m', 'tools.webui', '--topic-worker', str(folder), '--env', str(self.env)]
            with (folder / 'worker.log').open('a') as log:
                p = subprocess.Popen(command, cwd=self.engine.parent, env=self.child_env(), stdout=log, stderr=log, start_new_session=True)
            self.processes['topic/'+key] = dict(p=p, kind='topic', folder=folder, paused=False)
            return read(folder / 'topic.json')

    def get_topic(self, key):
        uuid.UUID(key)
        return read(self.library.own / '.topics' / key / 'topic.json', {})

    def create(self, data):
        with self.lock:
            topic = self.get_topic(data['topic_id'])
            index = data.get('candidate')
            if type(index) is not int or not 0 <= index < len(topic.get('candidates', [])): raise ValueError('请先点击选择一个候选题')
            if topic.get('book_id'): raise ValueError('此选题已开书，请到书库继续')
            if data.get('model') != 'flash': raise ValueError('公开CLI仅支持 MiniMax Flash')
            chapters = int(finite(data.get('chapters', topic['chapters']), 1, 2000))
            if chapters != topic['chapters'] or data.get('genre') != topic['genre']: raise ValueError('请按新题材/章数重新选题')
            budget = finite(data.get('budget', .23), .04, self.limit)
            jobs = int(finite(data.get('jobs', 2), 1, 4))
            if self.committed() + budget > self.limit: raise ValueError('已有书籍占用预算，请降低本书上限')
            self.jobs = jobs; save(self.library.own / '.queue.json', dict(jobs=jobs))
            candidate = topic['candidates'][index]; key = str(uuid.uuid4()); folder = self.library.own / key
            settings = dict(prompt_pack=topic['genre'], chapters=chapters, words_per_chapter=2200,
                            user_input=candidate['title']+'。'+candidate['premise'])
            save(folder / 'book.yaml', settings)  # JSON is a YAML subset, no YAML dependency.
            save(folder / 'web.json', dict(title=candidate['title'], genre=topic['genre'], chapters=chapters,
                budget=budget, polish=data.get('polish') is True, status='queued', created=now(), jobs=self.jobs))
            topic['book_id'] = 'webui/'+key
            save(self.library.own / '.topics' / topic['id'] / 'topic.json', topic)
            return dict(id='webui/'+key)

    def launch(self, folder, data, kind='book'):
        available = min(data['budget']-self.charge(folder), self.limit-self.spending())
        if available < .03:
            data.update(status='budget_paused', reason='预算余量不足 $0.03，保留检查点与 unknown')
            save(folder / 'web.json', data); return
        command = [sys.executable, '-m', 'bookengine', str(folder / 'book.yaml'), '--q1', '--style', 'b', '--env', str(self.env), '--out', str(folder)]
        if kind == 'polish': command = [sys.executable, '-m', 'deai', 'run', str(folder), '--env', str(self.env)]
        env = self.child_env()
        env.update(BOOKENGINE_BUDGET_ROOT=str(folder), BOOKENGINE_BUDGET_USD=str(costs(folder, include_final=False)['cost'] + available))
        with (folder / 'web-process.log').open('a') as log:
            p = subprocess.Popen(command, cwd=self.engine.parent, env=env, stdout=log, stderr=log, start_new_session=True)
        key = str(folder.relative_to(self.library.root))
        self.processes[key] = dict(p=p, kind=kind, folder=folder, paused=False)
        data.update(status='polishing' if kind == 'polish' else 'running', pid=p.pid, reason='')
        save(folder / 'web.json', data)

    def action(self, key, action):
        with self.lock:
            folder = self.library.folder(key); data = read(folder / 'web.json', {})
            if folder.parent != self.library.own or not data: raise ValueError('共享书籍只读')
            item = self.processes.get(key)
            if action == 'postprocess':
                if item and item['p'].poll() is None: raise ValueError('本书已有运行进程，请勿重复启动')
                if read(folder / 'run.json', {}).get('status') != 'complete': raise ValueError('请先完成正文导出')
                base = costs(folder, include_final=False)['cost']
                if not data.get('polish'):
                    data['external_polish_cost'] = max(0, costs(folder)['cost']-base)
                if self.charge(folder)+.03 > data['budget'] or self.spending()+.03 > self.limit: raise ValueError('预算余量不足')
                extra_reserve = max(0, data['budget']-self.charge(folder)) if data.get('status') == 'complete' else 0
                if self.committed()+extra_reserve > self.limit: raise ValueError('其他书籍已预留预算，暂不能启动后处理')
                data.update(polish=True, status='queued_polish', reason='独立deai CLI后处理；原始草稿不变')
            elif action == 'pause':
                if item and item['p'].poll() is None:
                    os.killpg(item['p'].pid, signal.SIGSTOP); item['paused'] = True
                data.update(status='paused', reason='已暂停；在途请求按未知上界计入')
            elif action in ('resume', 'retry'):
                if data.get('status') == 'orphaned': raise ValueError(data['reason'])
                if self.charge(folder)+.03 > data['budget'] or self.spending()+.03 > self.limit: raise ValueError('预算余量不足，未启动调用')
                run = read(folder / 'run.json', {})
                if run.get('status') == 'stopped': raise ValueError('公开CLI拒绝恢复 stopped 检查点；需A轨提供恢复能力，网页不会修改状态')
                if item and item['p'].poll() is None:
                    if sum(not x['paused'] for x in self.processes.values() if x['p'].poll() is None) >= self.jobs: raise ValueError('并发已满，请稍后继续')
                    os.killpg(item['p'].pid, signal.SIGCONT); item['paused'] = False
                    data.update(status='polishing' if item['kind']=='polish' else 'running', reason='')
                elif run.get('status') == 'complete' and not data.get('polish'): raise ValueError('本书已完成，无需重跑')
                else: data.update(status='queued', reason='从原检查点恢复，不重写已完成章节')
            else: raise ValueError('未知操作')
            save(folder / 'web.json', data)
            return {'ok': True}

    def loop(self):
        while not self.closed:
            try:
                with self.lock: self.tick()
            except Exception:
                # A transient concurrent file replacement must not kill the scheduler.
                pass
            time.sleep(.5)

    def tick(self):
        active = [x for x in self.processes.values() if x['kind'] != 'topic' and not x['paused'] and x['p'].poll() is None]
        for item in active[self.jobs:]:
            os.killpg(item['p'].pid, signal.SIGSTOP); item['paused'] = True
            data = read(item['folder'] / 'web.json', {})
            data.update(status='concurrency_paused', reason='并发上限降低，已暂停原子进程，空出位置后自动继续')
            save(item['folder'] / 'web.json', data)
        total = self.spending()
        for key, item in list(self.processes.items()):
            p, folder = item['p'], item['folder']
            if item['kind'] == 'topic':
                if p.poll() is not None:
                    topic = read(folder / 'topic.json', {})
                    if topic.get('status') == 'running':
                        topic.update(status='failed', error='选题子进程退出，原始调用保留'); save(folder / 'topic.json', topic)
                    del self.processes[key]
                continue
            data = read(folder / 'web.json', {}); run = read(folder / 'run.json', {})
            if p.poll() is not None:
                del self.processes[key]
                if item['kind'] == 'book' and run.get('status') == 'complete':
                    if data.get('polish'): data.update(status='queued_polish')
                    else: data.update(status='complete')
                elif item['kind'] == 'polish': data.update(status='complete' if p.returncode == 0 else 'postprocess_failed', reason='' if p.returncode == 0 else '后处理未全部通过，草稿保留；可从检查点重试')
                else: data.update(status='budget_paused' if run.get('status') == 'stopped' else 'failed', reason=run.get('stop_reason') or 'CLI退出；可从检查点重试')
                save(folder / 'web.json', data); self.library.static()
            elif not item['paused'] and (total+.025 > self.limit or self.charge(folder)+.025 > data['budget']):
                os.killpg(p.pid, signal.SIGSTOP); item['paused'] = True
                data.update(status='budget_paused', reason='已冻结原子进程；含在途/unknown保守预算已接近上限')
                save(folder / 'web.json', data)
        running = sum(x['kind'] != 'topic' and not x['paused'] and x['p'].poll() is None for x in self.processes.values())
        for item in self.processes.values():
            if running >= self.jobs: break
            data = read(item['folder'] / 'web.json', {})
            if item['paused'] and data.get('status') == 'concurrency_paused' and self.charge(item['folder'])+.03 <= data['budget'] and total+.03 <= self.limit:
                os.killpg(item['p'].pid, signal.SIGCONT); item['paused'] = False; running += 1
                data.update(status='polishing' if item['kind']=='polish' else 'running', reason='')
                save(item['folder'] / 'web.json', data)
        for folder in sorted(self.library.own.iterdir()):
            if running >= self.jobs: break
            if not folder.is_dir() or folder.is_symlink(): continue
            data = read(folder / 'web.json', {})
            if data.get('status') in ('queued', 'queued_polish'):
                self.launch(folder, data, 'polish' if data['status']=='queued_polish' or read(folder / 'run.json', {}).get('status')=='complete' else 'book')
                running += 1

    def close(self):
        self.closed = True
        with self.lock:
            for item in self.processes.values():
                if item['p'].poll() is None:
                    os.killpg(item['p'].pid, signal.SIGINT)
                    os.killpg(item['p'].pid, signal.SIGCONT)


def topic_worker(folder, env):
    from deai.client import MiniMax
    from deai.io import parse_json
    folder = Path(folder); data = read(folder / 'topic.json')
    try:
        client = MiniMax(folder, env, budget=.015)
        prompt = f"为{GENRES[data['genre']]}、{data['chapters']}章短篇生成12个不同的中文小说选题。想法：{data['idea'] or '自由发挥'}。每个含title和premise（80字以内，具体人物欲望、处境和核心冲突）。只返回JSON数组，恰好12项。"
        raw = client([dict(role='user', content=prompt)], stage='topics-12', max_tokens=2600)
        candidates = parse_json(raw)
        if not isinstance(candidates, list) or len(candidates) != 12 or any(not isinstance(x, dict) or not all(isinstance(x.get(k), str) and x[k].strip() for k in ('title','premise')) for x in candidates): raise ValueError('未返回有效的12候选，未自动重试')
        data.update(status='complete', candidates=[{k:x[k] for k in ('title','premise')} for x in candidates])
    except Exception as exc:
        data.update(status='failed', error=type(exc).__name__+'：选题未完成，保留原始调用与费用')
    save(folder / 'topic.json', data)
