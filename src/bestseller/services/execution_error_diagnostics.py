"""Bounded failure locations without exception messages, SQL, params or locals."""
from pathlib import Path
from collections import deque
import re


def safe_error_chain(error: BaseException) -> list[dict]:
    pending, seen, records = [error], set(), []
    while pending and len(records) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        record = {"type": type(current).__name__, "frames": []}
        code = getattr(current, "code", None)
        if isinstance(code, str) and re.fullmatch(r"execution_[a-z_]{1,80}", code):
            record["execution_code"] = code
        reason = getattr(current, "guard_reason", None)
        if isinstance(reason, str) and reason in {"missing_execution_call", "missing_send_counter", "physical_guard_missing_counter", "sdk_missing_execution_call"}:
            record["guard_reason"] = reason
        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if isinstance(sqlstate, str) and re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
            record["sqlstate"] = sqlstate
        frame = current.__traceback__
        tail = deque(maxlen=8)
        while frame is not None:
            location = {"filename": Path(frame.tb_frame.f_code.co_filename).name,
                "lineno": frame.tb_lineno, "function": frame.tb_frame.f_code.co_name}
            if len(record["frames"]) < 4:
                record["frames"].append(location)
            else:
                tail.append(location)
            frame = frame.tb_next
        record["frames"].extend(tail)
        records.append(record)
        pending.extend(value for value in (current.__cause__, current.__context__, getattr(current, "orig", None))
            if isinstance(value, BaseException))
    return records
