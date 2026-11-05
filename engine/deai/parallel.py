"""Drain every worker so a malformed reply never hides another worker's bug."""
from .client import ModelResponseError


def collect(futures):
    values, errors = [], []
    for future in futures:
        try:
            values.append(future.result())
        except Exception as exc:
            errors.append(exc)
    if errors:
        terminal = [e for e in errors if not isinstance(e, (ModelResponseError, InterruptedError))]
        interrupted = [e for e in errors if isinstance(e, InterruptedError)]
        raise (terminal or interrupted or errors)[0]
    return values
