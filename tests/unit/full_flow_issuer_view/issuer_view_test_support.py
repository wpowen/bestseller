"""Technical custody fixtures only: no SDK issuance, grade, adoption or E05b."""

import hashlib
import json
from pathlib import Path

from sqlalchemy import inspect, select


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def filesystem(root: Path):
    return {
        path.relative_to(root).as_posix(): (
            path.lstat().st_mode,
            path.lstat().st_ino,
            path.lstat().st_size,
            path.lstat().st_mtime_ns,
            path.lstat().st_ctime_ns,
            sha(path.read_bytes()) if path.is_file() else None,
        )
        for path in [root, *sorted(root.rglob("*"))]
    }


async def rows(session, models):
    with session.no_autoflush:
        result = {}
        for model in models:
            columns = [getattr(model, attr.key) for attr in inspect(model).column_attrs]
            actual = await session.execute(select(*columns))
            result[model.__tablename__] = sorted(
                canonical(json.loads(json.dumps(dict(row), default=str)))
                for row in actual.mappings()
            )
        return result
