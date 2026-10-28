"""Local policy-source custody and exact AST equivalence; never paid authority.

The native pipeline's bounded static inventory is the only source selector.
Archived Python is parsed as data and is never imported or executed.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tomllib
from uuid import uuid4

import yaml

_PIPELINE = "src/bestseller/services/pipelines.py"
_SELF = "src/bestseller/services/review_issuer_archive.py"
_PREFIX = "review-issuer-archives/sha256"
_CAPSULE_SCHEMA = "review-issuer-source-capsule.v1"
_ARCHIVE_SCHEMA = "review-issuer-source-archive.v1"
_SUMMARIES = ("runtime_policy_sha256", "python_ast_sha256", "non_python_sha256")
_CAPSULE_KEYS = {"schema", "archive_ref", "archive_sha256", *_SUMMARIES}
_MANIFEST_KEYS = {"schema", "files", *_SUMMARIES}
_ENTRY_KEYS = {"path", "kind", "sha256", "ast_sha256"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DENIALS = {
    "grade_granted": False,
    "new_credit_allowed": False,
    "dispatch_authorized": False,
    "adoption_authorized": False,
    "unknown_reconciled": False,
    "paid_binding_verified": False,
    "issued_report_verified": False,
    "current_applicability_checked": False,
}


class IssuerArchiveError(ValueError):
    """Stable, value-free diagnostics: never echo source or credential values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__("review_issuer_archive:" + code)


def _need(condition: object, code: str) -> None:
    if not condition:
        raise IssuerArchiveError(code)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _aggregate(values: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, value in sorted(values.items()):
        digest.update(relative.encode() + b"\0")
        digest.update(bytes.fromhex(value))
    return digest.hexdigest()


def _hex(value: object) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _relative(value: object, code: str) -> str:
    _need(type(value) is str and bool(value), code)
    path = PurePosixPath(value)
    _need(
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
        and "\\" not in value
        and not any(ord(char) < 32 for char in value),
        code,
    )
    return value


def _absolute(path: Path, code: str) -> Path:
    _need(".." not in path.parts, code)
    return path.absolute()


def _probe_store(path: Path) -> None:
    """Check existing ancestors without creating anything or resolving links."""
    path = _absolute(path, "capsule_invalid")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            try:
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except FileNotFoundError as exc:
                try:
                    os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    return
                _io_error(exc, descriptor, name, "archive_integrity")
            except OSError as exc:
                _io_error(exc, descriptor, name, "archive_integrity")
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


def _io_error(exc: OSError, parent: int, name: str, code: str) -> None:
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except OSError:
        info = None
    if exc.errno == errno.ELOOP or (info is not None and stat.S_ISLNK(info.st_mode)):
        raise IssuerArchiveError("symlink") from None
    raise IssuerArchiveError(code) from None


@contextmanager
def _directory(path: Path, code: str, *, create: bool = False):
    """Open every component with O_NOFOLLOW, including absolute ancestors."""
    path = _absolute(path, code)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    _io_error(exc, descriptor, name, code)
            try:
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except OSError as exc:
                _io_error(exc, descriptor, name, code)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _stamp(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read(path: Path, code: str, *, limit: int | None = None) -> bytes:
    with _directory(path.parent, code) as parent:
        try:
            descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        except OSError as exc:
            _io_error(exc, parent, path.name, code)
        try:
            before = os.fstat(descriptor)
            _need(stat.S_ISREG(before.st_mode), code)
            _need(limit is None or before.st_size <= limit, code)
            chunks = []
            while chunk := os.read(descriptor, 65536):
                chunks.append(chunk)
                _need(limit is None or sum(map(len, chunks)) <= limit, code)
            after = os.fstat(descriptor)
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            _need(not stat.S_ISLNK(current.st_mode), "symlink")
            _need(_stamp(before) == _stamp(after) == _stamp(current), code)
            return b"".join(chunks)
        except OSError as exc:
            _io_error(exc, parent, path.name, code)
        finally:
            os.close(descriptor)


def _children(path: Path, code: str) -> list[tuple[str, bool]]:
    with _directory(path, code) as descriptor:
        result = []
        with os.scandir(descriptor) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                _need(not stat.S_ISLNK(info.st_mode), "symlink")
                _need(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), code)
                result.append((entry.name, stat.S_ISDIR(info.st_mode)))
        return sorted(result)


def _quality_files(root: Path) -> list[Path]:
    result = []

    def visit(directory: Path) -> None:
        for name, is_directory in _children(directory, "source_missing"):
            path = directory / name
            if name.endswith(".py"):
                result.append(path)
            if is_directory:
                visit(path)

    visit(root / "src/bestseller/services/quality_levers")
    return result


def _ast(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _statement(source: str) -> ast.stmt:
    return ast.parse(source).body[0]


_ROOT_ASSIGN = _statement("root = Path(__file__).resolve().parents[3]")
_SERVICES_ASSIGN = _statement('services = root / "src/bestseller/services"')
_HASH_TAIL = ast.parse(
    "digest = hashlib.sha256()\n"
    "for path in sorted(policy_files):\n"
    '    digest.update(str(path.relative_to(root)).encode("utf-8") + b"\\0")\n'
    "    digest.update(hashlib.sha256(path.read_bytes()).digest())\n"
    "return digest.hexdigest()\n"
).body
_GENRE_GUARD = _statement(
    "if any(path not in policy_files for path in\n"
    '       (root / "config/genre_profile_thresholds").glob("*.yaml")):\n'
    '    raise ValueError("chapter_review_genre_policy_inventory_incomplete")\n'
)


def _inventory(root: Path, pipeline: bytes) -> list[str]:
    """Interpret bounded static Path expressions, not Python bytecode/source."""
    code = "inventory_unsupported"
    try:
        definitions = [
            node
            for node in ast.parse(pipeline).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "chapter_review_recovery_source_hash"
        ]
        _need(len(definitions) == 1, code)
        function = definitions[0]
        _need(
            not function.decorator_list
            and _ast(function.args)
            == _ast(
                ast.arguments(
                    posonlyargs=[],
                    args=[],
                    vararg=None,
                    kwonlyargs=[],
                    kw_defaults=[],
                    kwarg=None,
                    defaults=[],
                )
            ),
            code,
        )
        body = list(function.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            _need(type(body[0].value.value) is str, code)
            body.pop(0)
        _need(len(body) >= 6, code)
        _need(_ast(body[0]) == _ast(_ROOT_ASSIGN), code)
        _need(_ast(body[1]) == _ast(_SERVICES_ASSIGN), code)
        _need([_ast(n) for n in body[-3:]] == [_ast(n) for n in _HASH_TAIL], code)
        binding = body[2]
        _need(
            isinstance(binding, ast.Assign)
            and len(binding.targets) == 1
            and isinstance(binding.targets[0], ast.Name)
            and binding.targets[0].id == "policy_files",
            code,
        )

        def resolve(node: ast.AST, variables: dict | None = None):
            variables = {
                "root": root,
                "services": root / "src/bestseller/services",
                **(variables or {}),
            }
            if isinstance(node, ast.Name) and node.id in variables:
                return variables[node.id]
            if isinstance(node, ast.Constant) and type(node.value) is str:
                return node.value
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                left, right = resolve(node.left, variables), resolve(node.right, variables)
                _need(isinstance(left, Path) and type(right) is str, code)
                result = left / right
                _need(result.is_relative_to(root) and ".." not in result.parts, code)
                return result
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                return [resolve(item, variables) for item in node.elts]
            if isinstance(node, (ast.SetComp, ast.GeneratorExp)):
                _need(len(node.generators) == 1, code)
                generator = node.generators[0]
                _need(
                    isinstance(generator.target, ast.Name)
                    and not generator.ifs
                    and not generator.is_async,
                    code,
                )
                return [
                    resolve(node.elt, {**variables, generator.target.id: item})
                    for item in resolve(generator.iter, variables)
                ]
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "rglob"
                and len(node.args) == 1
                and not node.keywords
            ):
                _need(
                    resolve(node.func.value, variables)
                    == root / "src/bestseller/services/quality_levers"
                    and resolve(node.args[0], variables) == "*.py",
                    code,
                )
                return _quality_files(root)
            raise IssuerArchiveError(code)

        paths = set(resolve(binding.value))
        genre_guard = False
        for node in body[3:-3]:
            if _ast(node) == _ast(_GENRE_GUARD):
                _need(not genre_guard, code)
                genre_guard = True
                # Match native statement order: a later add cannot repair a
                # completeness guard that would already have rejected here.
                for name, _ in _children(
                    root / "config/genre_profile_thresholds", "source_missing"
                ):
                    if name.endswith(".yaml"):
                        _need(root / "config/genre_profile_thresholds" / name in paths, code)
                continue
            _need(isinstance(node, ast.Expr) and isinstance(node.value, ast.Call), code)
            call = node.value
            _need(
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "policy_files"
                and call.func.attr in {"add", "update"}
                and len(call.args) == 1
                and not call.keywords,
                code,
            )
            value = resolve(call.args[0])
            paths.update([value] if call.func.attr == "add" else value)
        _need(genre_guard and all(isinstance(path, Path) for path in paths), code)
        relative = sorted(_relative(path.relative_to(root).as_posix(), code) for path in paths)
        _need(_PIPELINE in relative, code)
        _need(_SELF in relative, "inventory_self_uncovered")
        return relative
    except (SyntaxError, UnicodeError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, IssuerArchiveError):
            raise
        raise IssuerArchiveError(code) from None


_SECRET_KEYS = {
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "private_key",
    "access_token",
    "refresh_token",
    "auth_token",
    "token",
    "authorization",
    "proxy_password",
}
_SECRET_LINE = re.compile(
    r"(?im)^\s*(?:#\s*)?[\"']?(api_key|apikey|password|passwd|secret|client_secret|private_key|"
    r"access_token|refresh_token|auth_token|token|authorization|proxy_password)[\"']?\s*[:=]\s*([^\r\n]+)"
)
_AUTH_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@]+@")


def _source_kind(relative: str) -> str:
    path = PurePosixPath(relative)
    lowered = [part.lower() for part in path.parts]
    _need(
        not any(
            part == ".env" or part.endswith(".env") or part in {"secrets", "credentials"}
            for part in lowered
        )
        and path.suffix.lower() not in {".pem", ".key", ".p12", ".pfx"},
        "secret_config",
    )
    if relative.endswith(".py"):
        _need(path.parts[:2] == ("src", "bestseller"), "source_invalid")
        return "python"
    _need(
        path.parts[0] in {"config", "data"}
        and path.suffix in {".yaml", ".yml", ".json", ".toml", ".md", ".txt"},
        "source_invalid",
    )
    return "non_python"


def _unique(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        _need(key not in result, "archive_integrity")
        result[key] = value
    return result


def _constant(_value: str):
    raise IssuerArchiveError("archive_integrity")


def _env_key(key: str) -> bool:
    return key.endswith("_env") and key.removesuffix("_env") in _SECRET_KEYS


def _yaml_nodes(text: str) -> None:
    """Inspect all original pairs before SafeLoader can overwrite/merge keys."""
    document = yaml.compose(text, Loader=yaml.SafeLoader)
    active = set()

    def visit(node: yaml.Node) -> None:
        _need(id(node) not in active, "source_invalid")
        active.add(id(node))
        if isinstance(node, yaml.MappingNode):
            keys = set()
            for key, item in node.value:
                _need(isinstance(key, yaml.ScalarNode), "source_invalid")
                identity = (key.tag, key.value)
                _need(identity not in keys, "source_invalid")
                keys.add(identity)
                normalized = key.value.lower().replace("-", "_")
                if normalized in _SECRET_KEYS:
                    _need(
                        isinstance(item, yaml.ScalarNode)
                        and (
                            (
                                item.tag == "tag:yaml.org,2002:null"
                                and item.value in {"", "~", "null", "Null", "NULL"}
                            )
                            or (item.tag == "tag:yaml.org,2002:str" and item.value == "")
                        ),
                        "secret_config",
                    )
                if _env_key(normalized):
                    _need(
                        isinstance(item, yaml.ScalarNode)
                        and item.tag == "tag:yaml.org,2002:str"
                        and re.fullmatch(r"[A-Z_][A-Z0-9_]*", item.value),
                        "secret_config",
                    )
                visit(item)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                visit(item)
        else:
            _need(isinstance(node, yaml.ScalarNode), "source_invalid")
        active.remove(id(node))

    if document is not None:
        visit(document)


def _config(relative: str, raw: bytes) -> None:
    try:
        text = raw.decode("utf-8")
        _need(_AUTH_URL.search(text) is None, "secret_config")
        for match in _SECRET_LINE.finditer(text):
            _need(match.group(2).strip() in {"", "null", "~", '""', "''"}, "secret_config")
        suffix = PurePosixPath(relative).suffix
        if suffix in {".yaml", ".yml"}:
            _yaml_nodes(text)
            decoded = yaml.safe_load(text)
        elif suffix == ".json":
            decoded = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
        elif suffix == ".toml":
            decoded = tomllib.loads(text)
        else:
            decoded = None
        seen = set()

        def visit(value: object) -> None:
            if isinstance(value, (dict, list)):
                _need(id(value) not in seen, "source_invalid")
                seen.add(id(value))
                if isinstance(value, dict):
                    for key, item in value.items():
                        normalized = str(key).lower().replace("-", "_")
                        if normalized in _SECRET_KEYS:
                            _need(item is None or item == "", "secret_config")
                        if _env_key(normalized):
                            _need(
                                type(item) is str and re.fullmatch(r"[A-Z_][A-Z0-9_]*", item),
                                "secret_config",
                            )
                        visit(item)
                else:
                    for item in value:
                        visit(item)
                seen.remove(id(value))

        visit(decoded)
    except (UnicodeError, ValueError, TypeError, yaml.YAMLError, RecursionError) as exc:
        if isinstance(exc, IssuerArchiveError):
            raise
        raise IssuerArchiveError("source_invalid") from None


def _manifest(raws: Mapping[str, bytes]) -> dict:
    raw_digests, python_digests, other_digests, entries = {}, {}, {}, []
    for relative, raw in sorted(raws.items()):
        _relative(relative, "archive_integrity")
        kind = _source_kind(relative)
        digest = _sha(raw)
        ast_digest = None
        if kind == "python":
            try:
                ast_digest = _sha(_ast(ast.parse(raw, type_comments=True)).encode())
            except (SyntaxError, UnicodeError, ValueError, RecursionError):
                raise IssuerArchiveError("source_invalid") from None
            python_digests[relative] = ast_digest
        else:
            _config(relative, raw)
            other_digests[relative] = digest
        raw_digests[relative] = digest
        entries.append({"path": relative, "kind": kind, "sha256": digest, "ast_sha256": ast_digest})
    return {
        "schema": _ARCHIVE_SCHEMA,
        "files": entries,
        "runtime_policy_sha256": _aggregate(raw_digests),
        "python_ast_sha256": _aggregate(python_digests),
        "non_python_sha256": _aggregate(other_digests),
    }


def _native_snapshot() -> tuple[Path, dict[str, bytes], dict]:
    root = _absolute(Path(__file__).parent.parent.parent.parent, "inventory_unsupported")
    pipeline = _read(root / _PIPELINE, "source_missing")
    paths = _inventory(root, pipeline)
    for relative in paths:
        _source_kind(relative)
    raws = {relative: _read(root / relative, "source_missing") for relative in paths}
    _need(raws[_PIPELINE] == pipeline, "source_changed")
    return root, raws, _manifest(raws)


def _still_current(root: Path, raws: Mapping[str, bytes]) -> None:
    _need(
        _inventory(root, _read(root / _PIPELINE, "source_missing")) == sorted(raws),
        "source_changed",
    )
    for relative, raw in raws.items():
        _need(_read(root / relative, "source_missing") == raw, "source_changed")


def _store(settings) -> Path:
    store = getattr(settings, "artifact_store", None)
    _need(store is not None and store.mode == "local", "non_local_store")
    _need(type(store.local_dir) is str and bool(store.local_dir), "non_local_store")
    return _absolute(Path(store.local_dir).expanduser(), "capsule_invalid")


def _capsule(value: Mapping[str, object]) -> dict:
    _need(isinstance(value, Mapping) and set(value) == _CAPSULE_KEYS, "capsule_invalid")
    result = dict(value)
    _need(
        result["schema"] == _CAPSULE_SCHEMA
        and all(_hex(result[key]) for key in (*_SUMMARIES, "archive_sha256")),
        "capsule_invalid",
    )
    _need(
        type(result["archive_ref"]) is str
        and result["archive_ref"] == _PREFIX + "/" + result["archive_sha256"],
        "capsule_invalid",
    )
    return result


def _archive(store: Path, capsule: dict) -> dict:
    code = "archive_integrity"
    directory = store / capsule["archive_ref"]
    raw = _read(directory / "manifest.json", code, limit=1024 * 1024)
    _need(_sha(raw) == capsule["archive_sha256"], "capsule_invalid")
    try:
        manifest = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
        _need(type(manifest) is dict and set(manifest) == _MANIFEST_KEYS, code)
        _need(raw == _canonical(manifest) and manifest["schema"] == _ARCHIVE_SCHEMA, code)
        _need(all(_hex(manifest[key]) for key in _SUMMARIES), code)
        _need(all(manifest[key] == capsule[key] for key in _SUMMARIES), "capsule_invalid")
        entries = manifest["files"]
        _need(type(entries) is list and 0 < len(entries) <= 4096, code)
        paths, expected_files, expected_dirs = [], {"manifest.json"}, {"sources"}
        for entry in entries:
            _need(type(entry) is dict and set(entry) == _ENTRY_KEYS, code)
            relative = _relative(entry["path"], code)
            _need(_hex(entry["sha256"]) and entry["kind"] in {"python", "non_python"}, code)
            _need(
                (
                    _hex(entry["ast_sha256"])
                    if entry["kind"] == "python"
                    else entry["ast_sha256"] is None
                ),
                code,
            )
            paths.append(relative)
            name = PurePosixPath("sources") / relative
            expected_files.add(name.as_posix())
            expected_dirs.update(
                parent.as_posix() for parent in name.parents if parent.as_posix() != "."
            )
        _need(paths == sorted(set(paths)), code)
        actual_files, actual_dirs = set(), set()

        def walk(folder: Path) -> None:
            for name, is_directory in _children(folder, code):
                path = folder / name
                relative = path.relative_to(directory).as_posix()
                if is_directory:
                    _need(relative in expected_dirs, code)
                    actual_dirs.add(relative)
                    walk(path)
                else:
                    _need(relative in expected_files, code)
                    actual_files.add(relative)

        walk(directory)
        _need(actual_files == expected_files and actual_dirs == expected_dirs, code)
        raws = {}
        for entry in entries:
            data = _read(directory / "sources" / entry["path"], code)
            _need(_sha(data) == entry["sha256"], code)
            raws[entry["path"]] = data
        _need(_canonical(_manifest(raws)) == raw, code)
        _need(_read(directory / "manifest.json", code) == raw, code)
        return manifest
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, IssuerArchiveError):
            raise
        raise IssuerArchiveError(code) from None


def _write_new(path: Path, data: bytes) -> None:
    with _directory(path.parent, "archive_integrity", create=True) as parent:
        try:
            descriptor = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
        except OSError as exc:
            _io_error(exc, parent, path.name, "archive_integrity")
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as output:
                output.write(data)
                output.flush()
                os.fsync(descriptor)
            os.fchmod(descriptor, 0o444)
        finally:
            os.close(descriptor)


def _link_new(source: Path, target: Path) -> None:
    """Publish an already-fsynced immutable file with atomic no-replace semantics."""
    with _directory(source.parent, "archive_integrity") as before:
        with _directory(target.parent, "archive_integrity", create=True) as after:
            try:
                info = os.stat(source.name, dir_fd=before, follow_symlinks=False)
                _need(stat.S_ISREG(info.st_mode), "archive_integrity")
                os.link(
                    source.name,
                    target.name,
                    src_dir_fd=before,
                    dst_dir_fd=after,
                    follow_symlinks=False,
                )
                os.fsync(after)
            except OSError as exc:
                _io_error(exc, after, target.name, "archive_integrity")


def capture_current_issuer_archive(settings) -> dict:
    """Capture the complete native inventory into immutable local source bytes."""
    store = _store(settings)
    # Check existing store ancestors before parsing sources or publishing files.
    _probe_store(store)
    root, raws, manifest = _native_snapshot()
    encoded = _canonical(manifest)
    digest = _sha(encoded)
    capsule = {
        "schema": _CAPSULE_SCHEMA,
        "archive_ref": _PREFIX + "/" + digest,
        "archive_sha256": digest,
        **{key: manifest[key] for key in _SUMMARIES},
    }
    namespace = store / _PREFIX
    with _directory(namespace, "archive_integrity", create=True) as parent:
        try:
            lock = os.open(
                ".capture.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent
            )
        except OSError as exc:
            _io_error(exc, parent, ".capture.lock", "archive_integrity")
        try:
            _need(stat.S_ISREG(os.fstat(lock).st_mode), "archive_integrity")
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                existing = os.stat(digest, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                _need(not stat.S_ISLNK(existing.st_mode), "symlink")
                _archive(store, capsule)
            else:
                stage_name = ".capture-" + uuid4().hex
                os.mkdir(stage_name, mode=0o700, dir_fd=parent)
                stage = namespace / stage_name
                try:
                    for relative, data in raws.items():
                        _write_new(stage / "sources" / relative, data)
                    _write_new(stage / "manifest.json", encoded)
                    _still_current(root, raws)
                    # mkdir is an atomic no-replace claim even against writers
                    # that do not participate in our local publication lock.
                    try:
                        os.mkdir(digest, mode=0o700, dir_fd=parent)
                    except OSError as exc:
                        _io_error(exc, parent, digest, "archive_integrity")
                    destination = namespace / digest
                    for relative in raws:
                        _link_new(stage / "sources" / relative, destination / "sources" / relative)
                    # The manifest is published last; interrupted/foreign
                    # partial objects fail closed and are never auto-repaired.
                    _link_new(stage / "manifest.json", destination / "manifest.json")
                    os.fsync(parent)
                finally:
                    if stage.exists() and not stage.is_symlink():
                        shutil.rmtree(stage)
                _archive(store, capsule)
            _still_current(root, raws)
        finally:
            os.close(lock)
    return {"status": "captured", "capsule": capsule, **_DENIALS}


def verify_current_issuer_archive(settings, *, capsule: Mapping[str, object]) -> dict:
    """Read-only integrity/equivalence; caller still needs native paid binding."""
    store, capsule = _store(settings), _capsule(capsule)
    original = _archive(store, capsule)
    root, raws, current = _native_snapshot()
    old_entries = {entry["path"]: entry for entry in original["files"]}
    new_entries = {entry["path"]: entry for entry in current["files"]}
    _need(set(old_entries) == set(new_entries), "inventory_changed")
    for path, old in old_entries.items():
        new = new_entries[path]
        if old["kind"] == "python":
            _need(old["ast_sha256"] == new["ast_sha256"], "python_ast_changed")
        else:
            _need(old["sha256"] == new["sha256"], "non_python_changed")
    _still_current(root, raws)
    _archive(store, capsule)
    return {
        "status": "equivalent",
        "byte_identical": original["runtime_policy_sha256"] == current["runtime_policy_sha256"],
        "issued_runtime_policy_sha256": original["runtime_policy_sha256"],
        "current_runtime_policy_sha256": current["runtime_policy_sha256"],
        "python_ast_sha256": current["python_ast_sha256"],
        "non_python_sha256": current["non_python_sha256"],
        **_DENIALS,
    }
