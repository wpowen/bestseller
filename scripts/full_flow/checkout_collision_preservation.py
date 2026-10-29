"""Explicit preservation stages for the user's 2026-10-29 17:10 approval.

No work is performed on import. This script never runs checkout, reset, clean,
Docker, application code or a database command. The approved proposal remains
immutable (including authorized_to_move=False); it is not an execution grant.

verify/regenerate are read-only and reconstruct the approved manifest from live
bytes, Git objects and protection baselines. copy creates a NEW private directory
and requires 100% readback. move additionally requires main's explicit assertion
that the quiescent deployment window is open. It re-generates the exact manifest
immediately before moving; changed scope/bytes/HEAD means STOP. Atomic no-replace
renames retain originals separately from the verified copy. No unlink fallback.

TARGET_HEAD and receipt target_head mean the APPROVED PROPOSAL target only.
They do not select or validate the eventual deployment commit. Before asserting
the window is open, main separately verifies the actual candidate's descendant
relationship, src/config equality to 43ccfcad, and that its current collision
paths/main identities are exactly the approved set. No deployment is performed
or authorized by a successful result from this helper alone.

Rollback: main must FIRST perform its normal checkout of SOURCE_HEAD, then invoke
restore with the explicit normal-checkout confirmation. Existing differing files
are never overwritten. An interrupted move may be restored, but never silently
resumed. A partial copy is retained and requires review, not automatic deletion.

Only the named snapshot destination is writable by the CLI. Low-level SafeRoot
and rename_no_replace primitives accept isolated temporary roots for unit tests;
the CLI has no alternate-root, alternate-approval or force option.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


MAIN = Path('/Volumes/MACSSD/owen-home/Documents/workspace/bestseller')
INT = MAIN / 'scratch/int-worktree'
MANIFEST = INT / 'reports/full-flow-2026-10-28/deployment-preflight-review/CHECKOUT-COLLISIONS.json'
SNAPSHOT = MAIN.parent / 'bestseller-par-private/MAIN-SNAPSHOT-20261029'
DESTINATION = MAIN.parent / 'bestseller-par-private/MAIN-CHECKOUT-COLLISIONS-20261029'
APPROVED_SHA = '71a96778b9dc74f9171506c9dd10c5635c4a1844c569998355884378da4d8080'
SNAPSHOT_SHA = '47d8aebb421f5c67d410b8e89ab47d9ca7ed60c1189533b82bac068bdce0c21e'
SOURCE_HEAD = '57db9498c70015b334a79759d52ea950cce1afe6'
TARGET_HEAD = 'cdbb109a020e65b25b1c932b198ee5f13ad0b05d'
BASELINES = {
    'wip': ('refs/heads/wip/g0-unreviewed-retained', 'ea60c97fa34fe4ec29573a6a196edae9f6edc7b9'),
    'e2e17': ('refs/tags/e2e17', '419181a41f29363263cee8a12d1694ace0bcef24'),
}
WINDOW_CONFIRMATION = 'main:deployment-window-open:' + APPROVED_SHA
ROLLBACK_CONFIRMATION = 'main:normal-checkout-complete:' + SOURCE_HEAD
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Stop(RuntimeError):
    """A bounded, content-free failure code; never continue on an unknown state."""


def require(condition, code):
    if not condition:
        raise Stop(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def parts(relative):
    require(type(relative) is str and bool(relative), 'RELATIVE_PATH_REQUIRED')
    path = PurePosixPath(relative)
    require(not path.is_absolute() and path.as_posix() == relative, 'NONCANONICAL_PATH')
    require(all(p not in {'', '.', '..', '.git'} for p in path.parts), 'UNSAFE_PATH')
    require('\x00' not in relative and '\\' not in relative, 'UNSAFE_PATH')
    return path.parts


class SafeRoot:
    """Directory-FD traversal: no symlink is followed, including root ancestors."""

    def __init__(self, absolute, *, private=False):
        absolute = Path(absolute)
        require(absolute.is_absolute() and '..' not in absolute.parts, 'ABSOLUTE_ROOT_REQUIRED')
        fd = os.open('/', DIR_FLAGS)
        try:
            for component in absolute.parts[1:]:
                child = os.open(component, DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
            if private:
                require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, 'PRIVATE_ROOT_MODE')
        except BaseException:
            os.close(fd)
            raise
        self.fd, self.private = fd, private

    def __enter__(self):
        return self

    def __exit__(self, *_):
        os.close(self.fd)

    @contextmanager
    def parent(self, relative, *, create_modes=None):
        components = parts(relative)
        fd = os.dup(self.fd)
        prefix = []
        try:
            for component in components[:-1]:
                prefix.append(component)
                key = '/'.join(prefix)
                try:
                    child = os.open(component, DIR_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    require(create_modes is not None, 'PARENT_MISSING')
                    mode = create_modes.get(key, 0o700) if self.private else create_modes[key]
                    os.mkdir(component, mode=mode, dir_fd=fd)
                    child = os.open(component, DIR_FLAGS, dir_fd=fd)
                    os.fchmod(child, mode)
                    os.fsync(fd)
                if self.private:
                    require(stat.S_IMODE(os.fstat(child).st_mode) == 0o700, 'PRIVATE_PARENT_MODE')
                os.close(fd)
                fd = child
            yield fd, components[-1]
        finally:
            os.close(fd)

    def exists(self, relative):
        try:
            with self.parent(relative) as (fd, name):
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
        except Stop as error:
            if str(error) == 'PARENT_MISSING':
                return False
            raise

    def read(self, relative):
        with self.parent(relative) as (fd, name):
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            # This exact approved manifest contains regular files only. New
            # symlinks are drift, not permission to widen the approved scope.
            require(stat.S_ISREG(before.st_mode), 'EXPECTED_REGULAR_FILE_NO_SYMLINK')
            require(before.st_nlink == 1, 'HARDLINK_REQUIRES_SEPARATE_REVIEW')
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(handle, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino), 'FILE_REPLACED')
                raw = stream.read()
                after = os.fstat(stream.fileno())
            final = os.stat(name, dir_fd=fd, follow_symlinks=False)
            signature = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(signature(before) == signature(after) == signature(final), 'FILE_CHANGED_DURING_READ')
            if self.private:
                require(stat.S_IMODE(after.st_mode) == 0o600, 'PRIVATE_FILE_MODE')
            return raw, stat.S_IMODE(after.st_mode)

    def write_new(self, relative, raw, *, mode=0o600, create_modes=None):
        with self.parent(relative, create_modes=create_modes) as (fd, name):
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
            with os.fdopen(handle, 'wb') as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(fd)

    def directory_modes(self, relative):
        result = {}
        components = parts(relative)
        for index in range(1, len(components)):
            prefix = '/'.join(components[:index])
            with self.parent(prefix + '/unused') as (fd, _):
                result[prefix] = stat.S_IMODE(os.fstat(fd).st_mode)
        return result


def rename_no_replace(source, source_path, destination, destination_path, *, create_modes, after_mode=None):
    """Atomic same-filesystem move; fail closed if the OS lacks no-replace."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        function, flag = getattr(libc, 'renameatx_np', None), 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith('linux'):
        function, flag = getattr(libc, 'renameat2', None), 1  # RENAME_NOREPLACE
    else:
        function, flag = None, 0
    require(function is not None, 'ATOMIC_NOREPLACE_UNAVAILABLE')
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    with source.parent(source_path) as (sfd, sname), destination.parent(destination_path, create_modes=create_modes) as (dfd, dname):
        require(os.fstat(sfd).st_dev == os.fstat(dfd).st_dev, 'CROSS_DEVICE_MOVE_DENIED')
        handle = os.open(sname, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=sfd)
        try:
            before = os.fstat(handle)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'MOVE_SOURCE_TYPE_OR_LINKS')
            if function(sfd, os.fsencode(sname), dfd, os.fsencode(dname), flag) != 0:
                number = ctypes.get_errno()
                raise Stop('ATOMIC_MOVE_' + errno.errorcode.get(number, 'UNKNOWN'))
            after = os.stat(dname, dir_fd=dfd, follow_symlinks=False)
            require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino),
                    'MOVED_SOURCE_REPLACED_RETAIN_QUARANTINE')
            # Change only the opened, moved inode, never a subsequently replaced
            # path. Existing hardlinks are refused before any permission change.
            if after_mode is not None:
                os.fchmod(handle, after_mode)
                os.fsync(handle)
            os.fsync(sfd)
            os.fsync(dfd)
        finally:
            os.close(handle)


def git(*arguments, input_bytes=None, allow_one=False):
    result = subprocess.run(['git', '-C', str(MAIN), *arguments], input=input_bytes,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}, timeout=60)
    require(result.returncode == 0 or (allow_one and result.returncode == 1), 'GIT_READ_FAILED')
    return result.stdout


def head():
    return git('rev-parse', 'HEAD^{commit}').decode().strip()


def tree(commit):
    result = {}
    for entry in git('ls-tree', '-r', '-z', '--full-tree', commit).split(b'\0'):
        if entry:
            metadata, path = entry.split(b'\t', 1)
            mode, kind, oid = metadata.decode().split()
            result[os.fsdecode(path)] = (mode, kind, oid)
    return result


class BlobDigests:
    """One read-only Git process, without printing or persisting blob contents."""

    def __enter__(self):
        self.cache = {}
        self.process = subprocess.Popen(
            ['git', '-C', str(MAIN), 'cat-file', '--batch'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        return self

    def digest(self, oid):
        if oid not in self.cache:
            process = self.process
            process.stdin.write((oid + '\n').encode())
            process.stdin.flush()
            header = process.stdout.readline().decode().split()
            require(len(header) == 3 and header[:2] == [oid, 'blob'], 'GIT_BLOB_HEADER')
            remaining = int(header[2])
            require(remaining >= 0, 'GIT_BLOB_SIZE')
            digest = hashlib.sha256()
            identity = hashlib.sha1(b'blob ' + str(remaining).encode() + b'\0')
            while remaining:
                block = process.stdout.read(min(remaining, 1024 * 1024))
                require(bool(block), 'GIT_BLOB_SHORT_READ')
                digest.update(block)
                identity.update(block)
                remaining -= len(block)
            require(process.stdout.read(1) == b'\n' and identity.hexdigest() == oid, 'GIT_BLOB_CHANGED')
            self.cache[oid] = digest.hexdigest()
        return self.cache[oid]

    def __exit__(self, kind, *_):
        self.process.stdin.close()
        if kind is not None:
            self.process.terminate()
        self.process.stdout.close()
        code = self.process.wait(timeout=10)
        if kind is None:
            require(code == 0, 'GIT_BLOB_PROCESS_FAILED')


def blob_sha(entry, cache):
    require(entry[1] == 'blob' and entry[0] in {'100644', '100755'}, 'UNSUPPORTED_GIT_OBJECT')
    return cache.digest(entry[2])


def approved():
    with SafeRoot(MANIFEST.parent) as directory:
        raw, _ = directory.read(MANIFEST.name)
    require(sha(raw) == APPROVED_SHA, 'APPROVED_MANIFEST_SHA_CHANGED')
    value = json.loads(raw)
    require(value['source_head'] == SOURCE_HEAD and value['target_head'] == TARGET_HEAD,
            'APPROVED_HEAD_CHANGED')
    require(value['count'] == len(value['files']) == 2742 and value['bytes'] == 64512264,
            'APPROVED_SCOPE_CHANGED')
    paths = [row['path'] for row in value['files']]
    require(paths == sorted(set(paths)), 'APPROVED_PATHS_INVALID')
    for path in paths:
        parts(path)
    return value, raw


def regenerate():
    """Rebuild every field; matching old JSON alone is not live verification."""
    value, original = approved()
    require(head() == SOURCE_HEAD, 'SOURCE_HEAD_CHANGED')
    tracked = {os.fsdecode(p) for p in git('ls-files', '--cached', '-z').split(b'\0') if p}
    targets = tree(TARGET_HEAD)
    baselines = {}
    for name, (ref, commit) in BASELINES.items():
        require(git('rev-parse', ref + '^{commit}').decode().strip() == commit, 'PROTECTION_REF_CHANGED')
        baselines[name] = tree(commit)
    with BlobDigests() as cache, SafeRoot(SNAPSHOT) as snapshot:
        snapshot_raw, _ = snapshot.read('MANIFEST.json')
        require(sha(snapshot_raw) == SNAPSHOT_SHA, 'SNAPSHOT_MANIFEST_CHANGED')
        snapshot_rows = {r['path']: r for r in json.loads(snapshot_raw)['files']}
        with SafeRoot(MAIN) as source:
            candidates = [path for path in sorted(targets) if path not in tracked and source.exists(path)]
            ignored = {os.fsdecode(p) for p in git('check-ignore', '-z', '--stdin',
                       input_bytes=b'\0'.join(os.fsencode(p) for p in candidates) + b'\0',
                       allow_one=True).split(b'\0') if p}
            rows = []
            for path in candidates:
                raw, _ = source.read(path)
                digest = sha(raw)
                target_digest = blob_sha(targets[path], cache)
                protection = []
                recorded = snapshot_rows.get(path)
                if recorded and recorded['type'] == 'bytes' and recorded['length'] == len(raw) and recorded['sha256'] == digest:
                    backup, _ = snapshot.read('files/' + path)
                    require(len(backup) == len(raw) and sha(backup) == digest, 'SNAPSHOT_OBJECT_CHANGED')
                    protection.append('main_snapshot')
                for name, baseline in baselines.items():
                    if path in baseline and blob_sha(baseline[path], cache) == digest:
                        protection.append(name)
                rows.append({'path': path, 'type': 'file' if path in ignored else 'bytes',
                             'bytes': len(raw), 'main_sha256': digest, 'target_sha256': target_digest,
                             'same_bytes': digest == target_digest, 'protected_matches': protection,
                             'ignored': path in ignored})
    rebuilt = {'schema': value['schema'], 'target_head': TARGET_HEAD, 'source_head': SOURCE_HEAD,
               'authorized_to_move': False, 'count': len(rows), 'bytes': sum(r['bytes'] for r in rows),
               'all_have_existing_protection': all(r['protected_matches'] for r in rows), 'files': rows}
    raw = encoded(rebuilt)
    require(raw == original and sha(raw) == APPROVED_SHA, 'REGENERATED_MANIFEST_CHANGED')
    require(head() == SOURCE_HEAD, 'SOURCE_HEAD_CHANGED_DURING_VERIFY')
    return rebuilt, raw


def check_file(root, relative, row, *, expected_mode=None):
    raw, mode = root.read(relative)
    require(len(raw) == row['bytes'] and sha(raw) == row['main_sha256'], 'FILE_IDENTITY_CHANGED')
    if expected_mode is not None:
        require(mode == expected_mode, 'FILE_MODE_CHANGED')
    return raw, mode


def receipt_binding(receipt):
    require(receipt['manifest_sha256'] == APPROVED_SHA and receipt['source_head'] == SOURCE_HEAD
            and receipt['target_head'] == TARGET_HEAD, 'RECEIPT_BINDING_CHANGED')


def verified_copy(backup, value):
    receipt_raw = backup.read('COPY-VERIFIED.json')[0]
    receipt = json.loads(receipt_raw)
    receipt_binding(receipt)
    require(receipt['schema'] == 'checkout-collision-copy.v1' and receipt['verified'] == 2742,
            'COPY_NOT_COMPLETE')
    require(backup.read('APPROVED-MANIFEST.json')[0] == approved()[1], 'COPIED_MANIFEST_CHANGED')
    require(set(receipt['original_modes']) == {r['path'] for r in value['files']}, 'COPY_PATHS_CHANGED')
    parents = {'/'.join(parts(r['path'])[:i]) for r in value['files']
               for i in range(1, len(parts(r['path'])))}
    require(set(receipt['original_directory_modes']) == parents, 'COPY_PARENT_PATHS_CHANGED')
    require(all(type(mode) is int and 0 <= mode <= 0o7777
                for mapping in (receipt['original_modes'], receipt['original_directory_modes'])
                for mode in mapping.values()), 'COPY_MODE_INVALID')
    for row in value['files']:
        check_file(backup, 'files/' + row['path'], row, expected_mode=0o600)
    receipt['_receipt_sha256'] = sha(receipt_raw)
    return receipt


@contextmanager
def exclusive(backup):
    with backup.parent('OPERATION.lock') as (fd, name):
        handle = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            require(stat.S_ISREG(os.fstat(handle).st_mode) and stat.S_IMODE(os.fstat(handle).st_mode) == 0o600,
                    'LOCK_FILE_INVALID')
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(handle)


def copy():
    value, raw = regenerate()
    # The fixed destination is outside MAIN and must be new; never merge copies.
    with SafeRoot(DESTINATION.parent) as parent:
        os.mkdir(DESTINATION.name, 0o700, dir_fd=parent.fd)
        os.fsync(parent.fd)
    with SafeRoot(DESTINATION, private=True) as backup, exclusive(backup), SafeRoot(MAIN) as source:
        backup.write_new('APPROVED-MANIFEST.json', raw)
        modes, directory_modes = {}, {}
        for row in value['files']:
            content, mode = check_file(source, row['path'], row)
            modes[row['path']] = mode
            directory_modes.update(source.directory_modes(row['path']))
            backup.write_new('files/' + row['path'], content, create_modes={})
            check_file(backup, 'files/' + row['path'], row, expected_mode=0o600)
        # Full second readback before a copy receipt can authorize a later stage.
        for row in value['files']:
            check_file(source, row['path'], row, expected_mode=modes[row['path']])
            check_file(backup, 'files/' + row['path'], row, expected_mode=0o600)
        require(regenerate()[1] == raw, 'SOURCE_CHANGED_DURING_COPY')
        receipt = {'schema': 'checkout-collision-copy.v1', 'manifest_sha256': APPROVED_SHA,
                   'source_head': SOURCE_HEAD, 'target_head': TARGET_HEAD, 'verified': len(modes),
                   'original_modes': modes, 'original_directory_modes': directory_modes}
        backup.write_new('COPY-VERIFIED.json', encoded(receipt))
        verified_copy(backup, value)
    return {'stage': 'copy', 'verified': len(modes), 'source_files_moved': 0}


def move(confirmation):
    require(confirmation == WINDOW_CONFIRMATION, 'MAIN_DEPLOYMENT_WINDOW_CONFIRMATION_REQUIRED')
    value, _ = approved()
    with SafeRoot(DESTINATION, private=True) as backup, exclusive(backup), SafeRoot(MAIN) as source:
        receipt = verified_copy(backup, value)
        require(not backup.exists('MOVE-STARTED.json'), 'MOVE_ALREADY_STARTED_REVIEW_OR_RESTORE')
        for row in value['files']:
            check_file(source, row['path'], row, expected_mode=receipt['original_modes'][row['path']])
        # Last all-files verification immediately before the first source move.
        require(regenerate()[1] == approved()[1], 'PREMOVE_REGENERATION_CHANGED')
        backup.write_new('MOVE-STARTED.json', encoded({'manifest_sha256': APPROVED_SHA,
                         'source_head': SOURCE_HEAD, 'target_head': TARGET_HEAD,
                         'copy_receipt_sha256': receipt['_receipt_sha256'],
                         'main_confirmation': confirmation}))
        for row in value['files']:
            require(head() == SOURCE_HEAD, 'SOURCE_HEAD_CHANGED_DURING_MOVE')
            check_file(source, row['path'], row, expected_mode=receipt['original_modes'][row['path']])
            check_file(backup, 'files/' + row['path'], row, expected_mode=0o600)
            rename_no_replace(source, row['path'], backup, 'moved/' + row['path'],
                              create_modes={}, after_mode=0o600)
            check_file(backup, 'moved/' + row['path'], row, expected_mode=0o600)
        require(all(not source.exists(row['path']) for row in value['files']), 'SOURCE_REAPPEARED_AFTER_MOVE')
        require(head() == SOURCE_HEAD, 'HEAD_CHANGED_BEFORE_MOVE_COMPLETE')
        backup.write_new('MOVE-COMPLETE.json', encoded({'manifest_sha256': APPROVED_SHA,
                         'source_head': SOURCE_HEAD, 'target_head': TARGET_HEAD, 'moved': len(value['files'])}))
    return {'stage': 'move', 'moved': len(value['files']), 'checkout_performed': False}


def restore(confirmation):
    require(confirmation == ROLLBACK_CONFIRMATION, 'MAIN_NORMAL_CHECKOUT_CONFIRMATION_REQUIRED')
    require(head() == SOURCE_HEAD, 'RESTORE_REQUIRES_ORIGINAL_HEAD_FIRST')
    value, _ = approved()
    tracked = {os.fsdecode(p) for p in git('ls-files', '--cached', '-z').split(b'\0') if p}
    require(not tracked.intersection(r['path'] for r in value['files']), 'RESTORE_PATH_NOW_TRACKED')
    with SafeRoot(DESTINATION, private=True) as backup, exclusive(backup), SafeRoot(MAIN) as source:
        receipt = verified_copy(backup, value)
        started = json.loads(backup.read('MOVE-STARTED.json')[0])
        receipt_binding(started)
        require(started['main_confirmation'] == WINDOW_CONFIRMATION, 'MOVE_RECEIPT_INVALID')
        require(started['copy_receipt_sha256'] == receipt['_receipt_sha256'], 'COPY_RECEIPT_CHANGED_AFTER_MOVE')
        # Reject all pre-existing differing destinations before restoring any.
        for row in value['files']:
            if source.exists(row['path']):
                check_file(source, row['path'], row, expected_mode=receipt['original_modes'][row['path']])
        restored = 0
        for row in value['files']:
            require(head() == SOURCE_HEAD, 'HEAD_CHANGED_DURING_RESTORE')
            path, mode = row['path'], receipt['original_modes'][row['path']]
            if not source.exists(path):
                content, _ = check_file(backup, 'files/' + path, row, expected_mode=0o600)
                staging = 'restore/' + path
                if not backup.exists(staging):
                    backup.write_new(staging, content, create_modes={})
                check_file(backup, staging, row, expected_mode=0o600)
                # Original mode is restored on the held, moved inode. No
                # existing destination is overwritten or chmod'ed by path.
                rename_no_replace(backup, staging, source, path,
                                  create_modes=receipt['original_directory_modes'], after_mode=mode)
                restored += 1
            check_file(source, path, row, expected_mode=mode)
        for row in value['files']:
            check_file(source, row['path'], row, expected_mode=receipt['original_modes'][row['path']])
        require(head() == SOURCE_HEAD, 'HEAD_CHANGED_BEFORE_RESTORE_COMPLETE')
        if not backup.exists('RESTORE-COMPLETE.json'):
            backup.write_new('RESTORE-COMPLETE.json', encoded({'manifest_sha256': APPROVED_SHA,
                             'source_head': SOURCE_HEAD, 'target_head': TARGET_HEAD,
                             'verified': len(value['files'])}))
    return {'stage': 'restore', 'restored_now': restored, 'verified': len(value['files']), 'checkout_performed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('verify', 'regenerate', 'copy', 'move', 'restore'))
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--approved-proposal-target', required=True)
    parser.add_argument('--deployment-window-confirmation')
    parser.add_argument('--normal-checkout-confirmation')
    args = parser.parse_args(argv)
    require(args.manifest_sha256 == APPROVED_SHA, 'EXACT_APPROVED_SHA_REQUIRED')
    require(args.approved_proposal_target == TARGET_HEAD, 'APPROVED_PROPOSAL_TARGET_CHANGED')
    if args.stage in {'verify', 'regenerate'}:
        value, raw = regenerate()
        result = {'stage': args.stage, 'count': value['count'], 'regenerated_sha256': sha(raw), 'writes': 0}
    elif args.stage == 'copy':
        result = copy()
    elif args.stage == 'move':
        result = move(args.deployment_window_confirmation)
    else:
        result = restore(args.normal_checkout_confirmation)
    print(json.dumps({'status': 'verified', 'manifest_sha256': APPROVED_SHA,
                      'approved_proposal_target': TARGET_HEAD, **result}, ensure_ascii=False))


class PreservationPrimitiveTests(unittest.TestCase):
    """Explicit-selector tests: temporary files only, no production-stage run."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='collision-preservation-test-')
        self.addCleanup(temporary.cleanup)
        # macOS temp paths may use /var -> /private/var. Resolve only this newly
        # allocated test root; production paths are never resolved past symlinks.
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'source'
        self.backup = self.root / 'backup'
        self.source.mkdir(mode=0o700)
        self.backup.mkdir(mode=0o700)
        self.body = b'isolated preservation fixture\n'
        self.row = {'bytes': len(self.body), 'main_sha256': sha(self.body)}

    def test_changed_bytes_symlinks_escape_and_existing_destination_are_rejected(self):
        with SafeRoot(self.source) as source, SafeRoot(self.backup, private=True) as backup:
            source.write_new('source.txt', self.body)
            with self.assertRaisesRegex(Stop, '^FILE_IDENTITY_CHANGED$'):
                check_file(source, 'source.txt', {**self.row, 'main_sha256': '0' * 64})
            os.symlink('source.txt', self.source / 'link.txt')
            os.symlink(self.backup, self.source / 'linked-parent')
            with self.assertRaisesRegex(Stop, '^EXPECTED_REGULAR_FILE_NO_SYMLINK$'):
                source.read('link.txt')
            with self.assertRaises(OSError):
                source.read('linked-parent/no-file')
            with self.assertRaisesRegex(Stop, '^UNSAFE_PATH$'):
                source.read('../backup/no-file')
            backup.write_new('existing.txt', b'incumbent')
            with self.assertRaisesRegex(Stop, '^ATOMIC_MOVE_EEXIST$'):
                rename_no_replace(source, 'source.txt', backup, 'existing.txt', create_modes={})
            self.assertEqual(source.read('source.txt')[0], self.body)
            self.assertEqual(backup.read('existing.txt')[0], b'incumbent')

    def test_move_restore_roundtrip_keeps_verified_copy_and_original_mode(self):
        with SafeRoot(self.source) as source, SafeRoot(self.backup, private=True) as backup:
            source.write_new('source.txt', self.body, mode=0o640)
            backup.write_new('files/source.txt', self.body, create_modes={})
            self.assertEqual(backup.directory_modes('files/source.txt'), {'files': 0o700})
            rename_no_replace(source, 'source.txt', backup, 'moved/source.txt',
                              create_modes={}, after_mode=0o600)
            self.assertFalse(source.exists('source.txt'))
            check_file(backup, 'files/source.txt', self.row, expected_mode=0o600)
            check_file(backup, 'moved/source.txt', self.row, expected_mode=0o600)
            backup.write_new('restore/source.txt', self.body, create_modes={})
            rename_no_replace(backup, 'restore/source.txt', source, 'source.txt',
                              create_modes={}, after_mode=0o640)
            self.assertEqual(source.read('source.txt'), (self.body, 0o640))
            check_file(backup, 'files/source.txt', self.row, expected_mode=0o600)
            check_file(backup, 'moved/source.txt', self.row, expected_mode=0o600)

    def test_pins_and_main_confirmations_reject_before_any_production_read(self):
        with patch.object(sys.modules[__name__], 'regenerate') as blocked:
            with self.assertRaisesRegex(Stop, '^EXACT_APPROVED_SHA_REQUIRED$'):
                main(['verify', '--manifest-sha256', '0' * 64,
                      '--approved-proposal-target', TARGET_HEAD])
            with self.assertRaisesRegex(Stop, '^APPROVED_PROPOSAL_TARGET_CHANGED$'):
                main(['verify', '--manifest-sha256', APPROVED_SHA,
                      '--approved-proposal-target', '0' * 40])
            blocked.assert_not_called()
        with self.assertRaisesRegex(Stop, '^MAIN_DEPLOYMENT_WINDOW_CONFIRMATION_REQUIRED$'):
            move(None)
        with self.assertRaisesRegex(Stop, '^MAIN_NORMAL_CHECKOUT_CONFIRMATION_REQUIRED$'):
            restore(None)


if __name__ == '__main__':
    try:
        main()
    except (Stop, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # Do not print arbitrary OS messages, payloads, environment or file bytes.
        code = str(error) if isinstance(error, Stop) else type(error).__name__
        print(json.dumps({'status': 'STOP', 'code': code, 'automatic_rollback': False}))
        raise SystemExit(1) from None
