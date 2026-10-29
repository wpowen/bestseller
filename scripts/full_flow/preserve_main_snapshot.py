"""User-authorized byte-exact supplemental backup; no source writes or model I/O."""
import datetime
import hashlib
import json
import os
import shutil
import stat
import subprocess
import traceback
from pathlib import Path, PurePosixPath

MAIN = Path('/Volumes/MACSSD/owen-home/Documents/workspace/bestseller')
REPORT = MAIN / 'scratch/int-worktree/reports/full-flow-2026-10-28'
DEST = MAIN.parent / 'bestseller-par-private/MAIN-SNAPSHOT-20261029'
os.umask(0o077)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def space():
    return {'internal_free_bytes': shutil.disk_usage('/System/Volumes/Data').free,
            'target_free_bytes': shutil.disk_usage(DEST.parent).free}


def require(ok, code, field, expected, actual):
    if not ok:
        raise ValueError(json.dumps({'code': code, 'field': field,
                                    'expected': expected, 'actual': actual}))


def read_identity(path):
    s = path.lstat()
    if stat.S_ISLNK(s.st_mode):
        raw = os.fsencode(os.readlink(path))
        return {'type': 'symlink', 'length': len(raw), 'sha256': digest(raw)}, raw, s
    require(stat.S_ISREG(s.st_mode), 'UNSUPPORTED_SOURCE_TYPE', str(path), 'regular_or_symlink', stat.S_IFMT(s.st_mode))
    h = hashlib.sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as f:
        while b := f.read(1024 * 1024):
            h.update(b)
        a = os.fstat(f.fileno())
    require((s.st_ino, s.st_size, s.st_mtime_ns) == (a.st_ino, a.st_size, a.st_mtime_ns),
            'SOURCE_CHANGED_DURING_READ', str(path), 'stable_stat', 'changed')
    return {'type': 'bytes', 'length': s.st_size, 'sha256': h.hexdigest()}, None, s


def run():
    census_path = REPORT / 'PRODUCTION-MOUNT-UNTRACKED-20261029.json'
    stop_path = REPORT / 'PRODUCTION-MOUNT-STOP-20261029.json'
    census = json.loads(census_path.read_bytes())
    stop = json.loads(stop_path.read_bytes())
    require(len(census['files']) == 26708, 'CENSUS_COUNT_MISMATCH', 'files', 26708, len(census['files']))
    special = {r['field'] for r in stop['differences']}
    require(len(special) == 4, 'TRACKED_COUNT_MISMATCH', 'tracked', 4, len(special))
    rows = [dict(r, category='untracked') for r in census['files']]
    rows += [{'path': p, 'category': 'tracked'} for p in sorted(special)]
    before = space()
    projected = sum(r.get('length', 1024 * 1024) for r in rows)
    require(before['internal_free_bytes'] >= 5_000_000_000, 'INTERNAL_SPACE_LOW', 'bytes', 5_000_000_000, before['internal_free_bytes'])
    require(before['target_free_bytes'] - projected >= 20_000_000_000, 'TARGET_SPACE_LOW', 'projected_free', 20_000_000_000, before['target_free_bytes'] - projected)
    docker = subprocess.run(['docker', 'version', '--format', '{{.Server.Version}}'], capture_output=True, timeout=15)
    require(docker.returncode == 0, 'DOCKER_UNRESPONSIVE', 'returncode', 0, docker.returncode)
    require(not DEST.exists(), 'SNAPSHOT_ALREADY_EXISTS', 'destination', 'absent', 'present')
    DEST.mkdir(mode=0o700)
    (DEST / 'files').mkdir(mode=0o700)
    parent_modes = {}
    entries = []
    for ordinal, row in enumerate(rows, 1):
        rel = PurePosixPath(row['path'])
        require(not rel.is_absolute() and '..' not in rel.parts, 'INVALID_RELATIVE_PATH', 'path', 'safe_relative', digest(str(rel).encode()))
        src, dst = MAIN / str(rel), DEST / 'files' / str(rel)
        for parent in src.parents:
            if parent == MAIN:
                break
            relative = str(parent.relative_to(MAIN))
            if relative not in parent_modes:
                mode = parent.lstat().st_mode
                require(stat.S_ISDIR(mode), 'SYMLINK_PARENT_DENIED', relative, 'directory', stat.S_IFMT(mode))
                parent_modes[relative] = stat.S_IMODE(mode)
        identity, link, source_stat = read_identity(src)
        if row['category'] == 'untracked':
            expected = {k: row[k] for k in ('type', 'length', 'sha256')}
            require(identity == expected, 'CENSUS_SOURCE_DRIFT', row['path'], expected, identity)
        dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if link is not None:
            os.symlink(os.fsdecode(link), dst)
            if hasattr(os, 'lchmod'):
                os.lchmod(dst, 0o600)
        else:
            with os.fdopen(os.open(src, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as inf, dst.open('xb') as outf:
                shutil.copyfileobj(inf, outf, 1024 * 1024)
                outf.flush()
                os.fsync(outf.fileno())
            os.chmod(dst, 0o600)
        actual, _, _ = read_identity(dst)
        require(actual == identity, 'COPY_READBACK_MISMATCH', row['path'], identity, actual)
        entries.append({'path': row['path'], 'category': row['category'], **identity,
                        'source_mode': stat.S_IMODE(source_stat.st_mode), 'snapshot_mode': stat.S_IMODE(dst.lstat().st_mode),
                        'source_mtime_ns': source_stat.st_mtime_ns})
        if ordinal % 2000 == 0:
            print(json.dumps({'copied_and_readback': ordinal, 'total': len(rows)}), flush=True)
    # Full second pass catches source drift and verifies every stored object again.
    for row in entries:
        expected = {k: row[k] for k in ('type', 'length', 'sha256')}
        for root in (MAIN, DEST / 'files'):
            actual, _, _ = read_identity(root / row['path'])
            require(actual == expected, 'FINAL_IDENTITY_MISMATCH', row['path'], expected, actual)
    after = space()
    require(after['internal_free_bytes'] >= 5_000_000_000, 'INTERNAL_SPACE_LOW_AFTER', 'bytes', 5_000_000_000, after['internal_free_bytes'])
    require(after['target_free_bytes'] >= 20_000_000_000, 'TARGET_SPACE_LOW_AFTER', 'bytes', 20_000_000_000, after['target_free_bytes'])
    manifest = {'schema': 'main-supplemental-protection-snapshot.v1',
                'at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'authority': '2026-10-29 09:50 user decision', 'source_root': str(MAIN),
                'census_sha256': digest(census_path.read_bytes()), 'tracked_census_sha256': digest(stop_path.read_bytes()),
                'space_before': before, 'space_after': after, 'files': entries, 'source_directory_modes': parent_modes,
                'verification': {'copied': len(entries), 'readback_verified': len(entries), 'source_unchanged': len(entries), 'ratio': 1.0},
                'symlink_policy': 'link bytes preserved, never dereferenced',
                'permission_policy': 'original modes in manifest; backup directories 0700, objects 0600'}
    raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    with (DEST / 'MANIFEST.json').open('xb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    require((DEST / 'MANIFEST.json').read_bytes() == raw, 'MANIFEST_READBACK_MISMATCH', 'manifest', digest(raw), digest((DEST / 'MANIFEST.json').read_bytes()))
    summary = {'schema': manifest['schema'], 'status': '通过', 'at_utc': manifest['at_utc'],
               'snapshot_directory': str(DEST), 'manifest_sha256': digest(raw),
               'total_objects': len(entries), 'tracked_files': 4, 'untracked_objects': 26708,
               'regular_files': sum(e['type'] == 'bytes' for e in entries),
               'symlinks': sum(e['type'] == 'symlink' for e in entries),
               'total_bytes': sum(e['length'] for e in entries), 'verification_ratio': 1.0,
               'space_before': before, 'space_after': after, 'source_writes': 0, 'model_calls': 0}
    (REPORT / 'MAIN-SNAPSHOT-20261029-SUMMARY.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        run()
    except Exception as exc:
        private = DEST if DEST.exists() else DEST.parent
        with (private / 'MAIN-SNAPSHOT-FAILURE.json').open('x') as f:
            json.dump({'exception': type(exc).__name__, 'details': str(exc), 'traceback': traceback.format_exc()}, f, indent=2)
        print(json.dumps({'status': '失败', 'exception': type(exc).__name__, 'details_private': True}), flush=True)
        raise SystemExit(1)
