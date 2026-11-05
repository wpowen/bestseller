"""Web views and restart recovery for the shared ephemeral runtime protocol."""
import fcntl
import os

from library import read, save

ACTIVE = {'running', 'queued', 'polishing', 'queued_polish', 'waiting', 'concurrency_paused'}
STOPPED = {'paused', 'budget_paused', 'quota_exhausted', 'error', 'failed', 'complete', 'orphaned'}


def auto_deai(data):
    return data.get('auto_deai', data.get('polish', False)) is True


def runtime_view(folder, state):
    runtime = read(folder / 'runtime.json', {})
    if not isinstance(runtime, dict) or state in {'complete', 'paused', 'queued', 'queued_polish', 'orphaned'}:
        return {}
    if runtime.get('kind') not in {'waiting', 'quota_exhausted', 'error', 'running'}:
        return {}
    return runtime


def alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def clear_runner_lock(library, folder):
    """An absent PID alone is insufficient if another runner still holds its lock."""
    path = library.safe(folder / 'runner.lock')
    if not path.exists():
        return True
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        path.unlink()
    return True


def recover(library):
    for folder in library.own.iterdir():
        if not folder.is_dir() or folder.is_symlink() or folder.name.startswith('.'):
            continue
        data = read(library.safe(folder / 'web.json'), {})
        if not data:
            continue
        state = data.get('status')
        run = read(library.safe(folder / 'run.json'), {})
        if state in STOPPED or (state not in ACTIVE and run.get('status') not in ACTIVE):
            continue
        if alive(data.get('pid', run.get('pid'))) or not clear_runner_lock(library, folder):
            data.update(status='orphaned', reason='旧子进程或写作锁仍在使用，未重复启动')
        else:
            kind = 'queued_polish' if state in {'polishing', 'queued_polish'} else 'queued'
            if run.get('status') == 'complete':
                kind = 'queued_polish' if auto_deai(data) or data.get('polish') or kind == 'queued_polish' else 'complete'
            data.pop('pid', None)
            runtime = runtime_view(folder, state)
            if runtime.get('kind') in ('quota_exhausted', 'error'):
                data.update(status=runtime['kind'], reason=runtime.get('error', ''))
            else:
                data.update(status=kind, reason='服务重启，从原检查点自动继续')
        save(folder / 'web.json', data)
