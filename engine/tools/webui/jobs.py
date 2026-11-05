"""Web-owned queue and public CLI subprocesses. No engine imports/state writes."""
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from library import GENRES, costs, now, read, save, stage_costs


def finite(value, low, high):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high: raise ValueError('数值超出允许范围')
    return value


def check_card(card, chapters, legacy=False):
    if not isinstance(card, dict) or len(json.dumps(card, ensure_ascii=False)) > 10000:
        raise ValueError('候选字段无效或过长')
    if legacy:
        if set(card) != {'title', 'premise'} or any(not isinstance(card.get(k), str) or not card[k].strip() or len(card[k]) > cap for k, cap in (('title', 80), ('premise', 120))):
            raise ValueError('请填写有效的书名和简介')
    else:
        from ideation.filters import validate
        errors = validate(card, chapters)
        if errors: raise ValueError('候选无效：' + '；'.join(errors))
    return card


class Queue:
    def __init__(self, library, env, engine, limit=.5):
        if library.read_only:
            raise ValueError('只读模式禁止启动队列')
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
        return sum(stage_costs(folder, read(folder / 'web.json', {})))

    def reserve(self, folder, data):
        if not data or data.get('status') == 'complete': return 0
        writer, deai = stage_costs(folder, data)
        writing = read(folder / 'run.json', {}).get('status') != 'complete'
        return (max(0, data['budget']-writer) if writing else 0) + (max(0, data.get('deai_budget', .5)-deai) if data.get('polish') else 0)

    def remaining(self, folder, data, kind):
        writer, deai = stage_costs(folder, data)
        return data.get('deai_budget', .5)-deai if kind == 'polish' else data['budget']-writer

    def available(self, folder, data, kind):
        proposed = dict(data, status='queued_polish' if kind == 'polish' else 'queued')
        if kind == 'polish': proposed['polish'] = True
        old = read(folder / 'web.json', {})
        if self.committed()-self.reserve(folder, old)+self.reserve(folder, proposed) > self.limit + 1e-9: return 0
        return min(self.remaining(folder, data, kind), self.limit-self.spending())

    def spending(self):
        return self.library.spending()

    def committed(self):
        total = self.spending()
        for folder in self.library.own.iterdir():
            if not folder.is_dir() or folder.is_symlink(): continue
            data = read(folder / 'web.json', {})
            total += self.reserve(folder, data)
        return total

    def child_env(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(self.engine)
        env['PYTHONUNBUFFERED'] = '1'
        for key in list(env):
            if key in ('BOOKENGINE_PHASE', 'BOOKENGINE_RATE_FILE') or key.startswith(('BOOKENGINE_BUDGET_','DEAI_BUDGET_')): env.pop(key, None)
        return env

    def topic(self, data):
        genre = data.get('genre'); chapters = int(finite(data.get('chapters', 5), 1, 2000))
        idea = data.get('idea', ''); channel = data.get('channel', '')
        if genre not in GENRES or not isinstance(idea, str) or len(idea) > 3000: raise ValueError('题材或想法无效')
        if not isinstance(channel, str) or channel not in ('', '男频', '女频'): raise ValueError('频道无效')
        direction = data.get('direction')
        if 'direction' in data:
            if not isinstance(direction, dict) or direction.get('mode') not in ('both', 'shell', 'variable'): raise ValueError('方向模式无效')
            direction = dict(mode=direction['mode'], candidate=check_card(direction.get('candidate'), chapters))
            axes = direction['candidate'].get('axes')
            if not isinstance(axes, dict) or any(not isinstance(axes.get(k), str) or not axes[k].strip() for k in ('shell', 'variable')): raise ValueError('此候选缺少方向轴')
        with self.lock:
            if any(p['kind'] == 'topic' and p['p'].poll() is None for p in self.processes.values()): raise ValueError('选题正在生成，请稍候')
            if self.committed() + .05 > self.limit: raise ValueError('总预算不足以生成选题')
            key = str(uuid.uuid4()); folder = self.library.own / '.topics' / key
            topic = dict(id=key, status='running', genre=genre, chapters=chapters, idea=idea, channel=channel, candidates=[])
            if direction is not None: topic['direction'] = direction
            save(folder / 'topic.json', topic)
            command = [sys.executable, '-m', 'tools.webui', '--topic-worker', str(folder), '--env', str(self.env)]
            with (folder / 'worker.log').open('a') as log:
                p = subprocess.Popen(command, cwd=self.engine.parent, env=self.child_env(), stdout=log, stderr=log, start_new_session=True)
            self.processes['topic/'+key] = dict(p=p, kind='topic', folder=folder, paused=False)
            return self.get_topic(key)

    def get_topic(self, key, internal=False):
        uuid.UUID(key)
        folder = self.library.own / '.topics' / key
        data = read(folder / 'topic.json', {})
        if not data or internal: return data
        public = {k: data[k] for k in ('id', 'status', 'genre', 'chapters', 'idea', 'channel', 'direction', 'candidates', 'notice', 'judge_status', 'cost_usd', 'calls', 'seed', 'error', 'book_id') if k in data}
        if isinstance(data.get('market'), dict): public['market'] = {k: data['market'][k] for k in ('date', 'status') if k in data['market']}
        public['collisions'] = [dict(title=row['title'], reasons=[r for r in row['reasons'] if r.startswith('撞车:')]) for row in data.get('rejected', []) if any(r.startswith('撞车:') for r in row['reasons'])]
        public['usage'] = costs(folder)
        return public

    def create(self, data):
        with self.lock:
            topic = self.get_topic(data['topic_id'], internal=True)
            index = data.get('candidate')
            if type(index) is not int or not 0 <= index < len(topic.get('candidates', [])): raise ValueError('请先点击选择一个候选题')
            if topic.get('book_id'): raise ValueError('此选题已开书，请到书库继续')
            if topic.get('status') != 'complete': raise ValueError('请等待选题完成')
            if data.get('model') != 'flash': raise ValueError('公开CLI仅支持 MiniMax Flash')
            chapters = int(finite(data.get('chapters', topic['chapters']), 1, 2000))
            if chapters != topic['chapters'] or data.get('genre') != topic['genre']: raise ValueError('请按新题材/章数重新选题')
            budget = finite(data.get('budget', .23), .04, self.limit)
            deai_budget = finite(data.get('deai_budget', .5), .01, max(.5, self.limit))
            jobs = int(finite(data.get('jobs', 2), 1, 4))
            if self.committed() + budget + (deai_budget if data.get('polish') is True else 0) > self.limit: raise ValueError('已有书籍占用预算，请降低本书上限')
            candidate = topic['candidates'][index]
            if 'candidate_edit' in data:
                edited = data['candidate_edit']; legacy = set(candidate) == {'title', 'premise'}
                check_card(edited, chapters, legacy)
                editable = {'title', 'premise'} if legacy else {'title', 'hook', 'tags', 'premise', 'golden_finger_rule', 'opening', 'engine'}
                if set(edited) != set(candidate) or any(edited[k] != candidate[k] for k in candidate if k not in editable): raise ValueError('仅可编辑卡片内容，不能修改参考分和方向轴')
                candidate = edited
            self.jobs = jobs; save(self.library.own / '.queue.json', dict(jobs=jobs))
            key = str(uuid.uuid4()); folder = self.library.own / key
            settings = dict(prompt_pack=topic['genre'], chapters=chapters, words_per_chapter=2200,
                            user_input=json.dumps(candidate, ensure_ascii=False))
            save(folder / 'book.yaml', settings)  # JSON is a YAML subset, no YAML dependency.
            save(folder / 'web.json', dict(title=candidate['title'], genre=topic['genre'], chapters=chapters,
                budget=budget, deai_budget=deai_budget, polish=data.get('polish') is True, status='queued', created=now(), jobs=self.jobs))
            topic['book_id'] = 'webui/'+key
            save(self.library.own / '.topics' / topic['id'] / 'topic.json', topic)
            return dict(id='webui/'+key)

    def launch(self, folder, data, kind='book'):
        if kind == 'polish': data = dict(data, polish=True, deai_budget=data.get('deai_budget', .5))
        available = self.available(folder, data, kind)
        if available < .03:
            data.update(status='budget_paused', reason='预算余量不足 $0.03，保留检查点与 unknown')
            save(folder / 'web.json', data); return
        command = [sys.executable, '-m', 'bookengine', str(folder / 'book.yaml'), '--q1', '--style', 'b', '--env', str(self.env), '--out', str(folder)]
        env = self.child_env()
        if kind == 'polish':
            # CLI caps the final/ ledger lifetime; preserve externally paid history.
            gross = costs(folder)['cost'] - costs(folder, include_final=False)['cost']
            command = [sys.executable, '-m', 'deai', 'run', str(folder), '--env', str(self.env), '--budget', str(round(gross+available, 9)), '--budget-root', str(folder / 'final')]
        else: env.update(BOOKENGINE_BUDGET_ROOT=str(folder), BOOKENGINE_BUDGET_USD=str(costs(folder, include_final=False)['cost'] + available))
        with (folder / 'web-process.log').open('a') as log:
            p = subprocess.Popen(command, cwd=self.engine.parent, env=env, stdout=log, stderr=log, start_new_session=True)
        key = str(folder.relative_to(self.library.root))
        self.processes[key] = dict(p=p, kind=kind, folder=folder, paused=False)
        data.update(status='polishing' if kind == 'polish' else 'running', pid=p.pid, reason='')
        save(folder / 'web.json', data)

    def action(self, key, action, title=None):
        with self.lock:
            folder = self.library.folder(key); data = read(folder / 'web.json', {})
            if action in ('archive', 'restore', 'delete', 'rename'):
                return self.manage(folder, data, action, title)
            if folder.parent != self.library.own or not data: raise ValueError('共享书籍只读')
            item = self.processes.get(key)
            if action == 'postprocess':
                if item and item['p'].poll() is None: raise ValueError('本书已有运行进程，请勿重复启动')
                if read(folder / 'run.json', {}).get('status') != 'complete': raise ValueError('请先完成正文导出')
                base = costs(folder, include_final=False)['cost']
                if not data.get('polish'):
                    data['external_polish_cost'] = max(0, costs(folder)['cost']-base)
                data.update(polish=True, deai_budget=data.get('deai_budget', .5), status='queued_polish', reason='独立deai CLI后处理；原始草稿不变')
                if self.available(folder, data, 'polish') < .03: raise ValueError('去AI或总预算余量不足，含其他书籍预留')
            elif action == 'pause':
                if item and item['p'].poll() is None:
                    os.killpg(item['p'].pid, signal.SIGSTOP); item['paused'] = True
                data.update(status='paused', reason='已暂停；在途请求按未知上界计入')
            elif action in ('resume', 'retry'):
                if data.get('status') == 'orphaned': raise ValueError(data['reason'])
                run = read(folder / 'run.json', {})
                kind = item['kind'] if item and item['p'].poll() is None else ('polish' if run.get('status') == 'complete' and data.get('polish') else 'book')
                if self.available(folder, data, kind) < .03: raise ValueError('当前阶段或总预算余量不足，未启动调用')
                if run.get('status') == 'stopped': raise ValueError('公开CLI拒绝恢复 stopped 检查点；需A轨提供恢复能力，网页不会修改状态')
                if item and item['p'].poll() is None:
                    if sum(not x['paused'] for x in self.processes.values() if x['p'].poll() is None) >= self.jobs: raise ValueError('并发已满，请稍后继续')
                    os.killpg(item['p'].pid, signal.SIGCONT); item['paused'] = False
                    data.update(status='polishing' if item['kind']=='polish' else 'running', reason='')
                elif run.get('status') == 'complete' and not data.get('polish'): raise ValueError('本书已完成，无需重跑')
                else: data.update(status='queued_polish' if kind == 'polish' else 'queued', reason='从原检查点恢复，不重写已完成章节')
            else: raise ValueError('未知操作')
            save(folder / 'web.json', data)
            return {'ok': True}

    def manage(self, folder, data, action, title):
        lib = self.library
        archive = lib.safe(lib.own / '.archive')
        log = lib.safe(lib.own / '.ops.log')
        ledger = lib.safe(lib.own / '.spent.json')
        if lib.read_only or folder.parent not in (lib.own, archive) or folder.name.startswith('.') or not data:
            raise ValueError('仅可管理直属网页书籍')
        for path in folder.rglob('*'):
            if path.is_symlink():
                raise ValueError('书目录含符号链接')
        item = self.processes.get(str(folder.relative_to(lib.root)))
        if data.get('status') in ('running', 'polishing', 'orphaned') or item and item['p'].poll() is None:
            raise ValueError('运行中或孤立进程书籍不可操作')
        if data.get('pid') is not None:
            try:
                os.kill(data['pid'], 0)
            except ProcessLookupError:
                pass
            else:
                raise ValueError('子进程仍存在，请先结束进程')
        current = lib.meta(folder)['title']
        if action == 'delete' and title != current:
            raise ValueError('请输入完整书名确认永久删除')
        if action == 'rename' and (not isinstance(title, str) or not 1 <= len(title.strip()) <= 80 or any(ord(c) < 32 for c in title)):
            raise ValueError('书名须为1–80字的单行文字')
        target = folder
        if action in ('archive', 'restore'):
            if (action == 'archive') != (folder.parent == lib.own):
                raise ValueError('书籍已归档或未归档')
            target = lib.safe((archive if action == 'archive' else lib.own) / folder.name)
            if target.exists():
                raise ValueError('目标已存在，禁止覆盖')
        with log.open('a', encoding='utf-8') as output:
            output.write(json.dumps(dict(at=now(), action=action, id=str(folder.relative_to(lib.root)), title=current, new_title=title if action == 'rename' else None), ensure_ascii=False) + '\n')
            output.flush()
            os.fsync(output.fileno())
        if action == 'delete':
            paid = read(ledger, {})
            paid['webui/' + folder.name] = dict(costs(folder), charged=self.charge(folder))
            save(ledger, paid)
            shutil.rmtree(folder)
        elif action == 'rename':
            save(folder / 'web.json', dict(data, title=title.strip()))
        else:
            target.parent.mkdir(exist_ok=True)
            folder.rename(target)
        return dict(ok=True, id=str(target.relative_to(lib.root)))

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
            elif not item['paused'] and (total+.025 > self.limit or self.remaining(folder, data, item['kind']) < .025):
                os.killpg(p.pid, signal.SIGSTOP); item['paused'] = True
                data.update(status='budget_paused', reason='已冻结原子进程；含在途/unknown保守预算已接近上限')
                save(folder / 'web.json', data)
        running = sum(x['kind'] != 'topic' and not x['paused'] and x['p'].poll() is None for x in self.processes.values())
        for item in self.processes.values():
            if running >= self.jobs: break
            data = read(item['folder'] / 'web.json', {})
            if item['paused'] and data.get('status') == 'concurrency_paused' and self.available(item['folder'], data, item['kind']) >= .03:
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
    folder = Path(folder); data = read(folder / 'topic.json')
    try:
        from ideation import run
        client = MiniMax(folder, env, budget=.05)
        data = run(folder, client, folder.parents[2])
    except Exception as exc:
        data = read(folder / 'topic.json', data)
        data.update(status='failed', error=type(exc).__name__+'：选题未完成，保留原始调用与费用')
    save(folder / 'topic.json', data)
