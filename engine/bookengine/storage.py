"""Small atomic on-disk checkpoints."""

import datetime as dt
import json
import os
from pathlib import Path


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        os.chmod(temp, 0o600)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
