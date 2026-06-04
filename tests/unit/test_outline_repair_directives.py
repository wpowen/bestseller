from __future__ import annotations

from bestseller.domain.llm_quality_judge import quality_judge_result_from_mapping
from bestseller.services.outline_llm_judge import build_outline_repair_directives


def _result(payload):
    return quality_judge_result_from_mapping(payload, scope="commercial_planning", min_overall=0.82)


def test_directives_built_from_blocking_issues():
    result = _result(
        {
            "overall_score": 0.58,
            "blocking_issues": [
                {
                    "code": "B-SCENE-CARD-INCOMPLETE",
                    "severity": "high",
                    "evidence": "ch1-scene1 entry_state 空泛",
                    "required_fix": "补具体人物动作与代价",
                },
                {
                    "code": "B-KNOWLEDGE-BOUNDARY",
                    "severity": "high",
                    "evidence": "ch3 HR 用内部术语",
                    "required_fix": "非专业角色只描述症状",
                },
            ],
            "rewrite_plan": {"instructions": "只重写被点名场景，保留其余。"},
        }
    )
    directives = build_outline_repair_directives(result)
    assert len(directives) == 3  # 2 issues + rewrite_plan
    joined = "\n".join(directives)
    assert "B-SCENE-CARD-INCOMPLETE" in joined
    assert "ch1-scene1" in joined
    assert "补具体人物动作与代价" in joined
    assert "只重写被点名场景" in joined


def test_no_directives_when_clean():
    result = _result({"overall_score": 0.9, "blocking_issues": []})
    assert build_outline_repair_directives(result) == []
