from __future__ import annotations

import pytest

from bestseller.services.compliance_boundary_kernel import (
    build_compliance_boundary_kernel_seed,
    evaluate_compliance_boundary_kernel,
    scan_compliance_texts,
)
from bestseller.services.public_emotion_kernel import (
    build_public_emotion_kernel_seed,
    evaluate_public_emotion_kernel,
    render_public_emotion_prompt_block,
)

pytestmark = pytest.mark.unit


def _valid_public_emotion_kernel() -> dict[str, object]:
    return {
        "target_segments": [
            {
                "id": "segment-underestimated",
                "group_label": "被低估但仍想争一口气的类型读者",
                "life_context": "主角处境与作品独有世界规则绑定。",
                "public_emotion": "不甘、憋屈、想证明自己不是被标签定义的人。",
                "unsaid_sentence": "凭什么你一句话就决定我的位置？",
                "desired_compensation": "主角用本书独有规则拿回解释权。",
            }
        ],
        "emotion_bridges": [
            {
                "bridge_id": "bridge-main",
                "source_segment_id": "segment-underestimated",
                "bridge_type": "value_bridge",
                "public_anchor": "被标签低估",
                "genre_translation": "把标签压力转译成本书独有的虚构资格规则。",
                "story_hook": "主角发现资格规则背后存在可证明的漏洞。",
                "reader_payoff": "当众证明旧判断失效。",
                "title_hook": "旧榜错判我，我用新规则翻案",
            }
        ],
        "reader_comment_triggers": ["这就是被低估后的翻身感"],
        "forbidden_misreads": ["不能变成现实群体仇恨"],
        "project_specificity_notes": "只绑定当前测试项目。",
    }


def test_public_emotion_kernel_passes_with_segment_bridge_and_payoff() -> None:
    report = evaluate_public_emotion_kernel(_valid_public_emotion_kernel())

    assert report.passed is True
    assert report.target_segment_count == 1
    assert report.emotion_bridge_count == 1


def test_public_emotion_kernel_warns_when_only_generic_topic_exists() -> None:
    report = evaluate_public_emotion_kernel(
        {
            "target_segments": [],
            "emotion_bridges": [],
        }
    )
    codes = {issue.code for issue in report.issues}

    assert report.passed is False
    assert "PUBLIC_EMOTION_TARGET_MISSING" in codes
    assert "PUBLIC_EMOTION_BRIDGE_MISSING" in codes


def test_public_emotion_prompt_block_renders_project_local_bridge() -> None:
    block = render_public_emotion_prompt_block(_valid_public_emotion_kernel())

    assert "本书专属公共情绪桥" in block
    assert "旧榜错判我" in block
    assert "不能变成现实群体仇恨" in block


def test_compliance_boundary_detects_high_risk_real_world_revenge() -> None:
    kernel = build_compliance_boundary_kernel_seed(platform="番茄小说")
    report = evaluate_compliance_boundary_kernel(
        kernel,
        candidate_texts=["被真实学校欺负后，我要报复现实所有人"],
    )
    codes = {issue.code for issue in report.issues}

    assert report.passed is False
    assert "COMPLIANCE_REAL_WORLD_REVENGE" in codes
    assert "COMPLIANCE_IDENTIFIABLE_REAL_TARGET" in codes


def test_compliance_scan_allows_fictionalized_emotion_copy() -> None:
    risks = scan_compliance_texts(
        ["开局旧榜错判，我用新规则翻案"],
        build_compliance_boundary_kernel_seed(),
    )

    assert risks == []


def test_compliance_boundary_uses_default_pack_when_kernel_missing() -> None:
    report = evaluate_compliance_boundary_kernel(
        None,
        candidate_texts=["被真实学校欺负后，我要报复现实所有人"],
    )

    assert "COMPLIANCE_BOUNDARY_KERNEL_MISSING" in report.issue_codes
    assert "COMPLIANCE_REAL_WORLD_REVENGE" in report.issue_codes
    assert any(issue.severity == "high" for issue in report.issues)


def test_public_emotion_seed_is_project_scoped() -> None:
    seed = build_public_emotion_kernel_seed(
        book_spec={"title": "镜城夜行", "genre": "悬疑", "premise": "主角调查虚构城中旧案。"},
        commercial_brief={"target_audiences": ["悬疑读者"], "reader_promise": "规则破局。"},
    )

    assert seed["target_segments"][0]["group_label"] == "悬疑读者"
    assert "镜城夜行" in seed["project_specificity_notes"]
