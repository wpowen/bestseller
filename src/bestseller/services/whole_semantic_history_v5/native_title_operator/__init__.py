"""Package bootstrap for byte-preserved standalone native producer modules.

Their legacy absolute import is scoped to loading the adapter, then restored;
no global alias, database connection, dispatch or CLI execution is left behind.
The original CLI remains available by running title_operator.py directly.
"""
from importlib import import_module
import sys

from . import title_operator


def _load_adapter():
    missing = object()
    previous = sys.modules.get("title_operator", missing)
    sys.modules["title_operator"] = title_operator
    try:
        return import_module(".title_operator_native", __name__)
    finally:
        if previous is missing:
            sys.modules.pop("title_operator", None)
        else:
            sys.modules["title_operator"] = previous


title_operator_native = _load_adapter()

__all__ = ["title_operator", "title_operator_native"]
