"""Public recovery details for rejected immutable listing caches."""

from __future__ import annotations


def listing_conflict_detail(error: ValueError) -> dict[str, object] | None:
    code = str(error)
    if code not in {"STALE_PACKAGING_CONSUMER", "ACTIVE_INTRO_VARIANT_MISSING"}:
        return None
    return {
        "code": code,
        "message": (
            "上架资料与当前已批准版本不一致。暂不可复制或发布。"
            "请重新生成并审核上架资料后再加载。"
        ),
        "recoverable": True,
        "recovery_action": "regenerate_and_approve_packaging",
    }
