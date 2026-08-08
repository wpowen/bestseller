"""空 CastSpec 必须当场失败，而不是被默认值填成合法对象。

真机 2026-08-08（deai-verify-20260808）：`planner_cast_spec` 调用成功
（finish_reason=stop、5073 output tokens、无失败落盘），但存进
planning_artifact_versions 的是 `{protagonist: null, antagonist: null,
supporting_cast: [], ...}` —— 每个字段都是 optional-with-default，
模型响应没带上契约字段时，「彻底解析失败」被静默降级成「合法空对象」。
空壳一路通过 foundation_identity_contract（它只校验存在的条目，空集合空真放行），
直到 ensure_project_identity_manifest 才抛 "CastSpec produced an empty
identity manifest" —— 报错位置与真因差好几步，整本书失败。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bestseller.domain.story_bible import CastSpecInput
from bestseller.services.story_bible import parse_cast_spec_input


def test_empty_payload_is_rejected() -> None:
    with pytest.raises(ValidationError, match="CastSpec is empty"):
        parse_cast_spec_input({})


def test_all_null_payload_is_rejected() -> None:
    """真机落库的那个空壳原样。"""
    with pytest.raises(ValidationError, match="CastSpec is empty"):
        parse_cast_spec_input(
            {
                "antagonist": None,
                "protagonist": None,
                "conflict_map": [],
                "supporting_cast": [],
                "antagonist_forces": [],
            }
        )


def test_protagonist_only_is_accepted() -> None:
    """修复不许收紧到误伤：只有主角的 cast 仍然合法。"""
    spec = CastSpecInput.model_validate({"protagonist": {"name": "林晚秋"}})
    assert spec.protagonist is not None
    assert spec.protagonist.role == "protagonist"


def test_supporting_cast_only_is_accepted() -> None:
    spec = CastSpecInput.model_validate({"supporting_cast": [{"name": "赵峰"}]})
    assert len(spec.supporting_cast) == 1
