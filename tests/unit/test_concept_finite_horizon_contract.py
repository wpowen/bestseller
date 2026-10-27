import pytest

from bestseller.services import concept_tournament as ct


KERNEL = {"protagonist_identity": "修复师", "opening_crisis": "档案即将封锁"}


@pytest.mark.parametrize("chapter_count", [20, 49])
def test_single_engine_judge_allows_finite_reveal_and_requires_causal_resolution(chapter_count):
    _system, prompt = ct._build_engine_judge_messages(
        kernel=KERNEL,
        genre="悬疑推理",
        sub_genre="悬疑推理",
        chapter_count=chapter_count,
        seed_concept="修复师听见失踪者录音；封锁档案前必须交出证据。",
        authoritative_seed_concept="修复师听见失踪者录音；封锁档案前必须交出证据。",
    )
    assert "有限篇幅承载合同" in prompt
    assert "有限谜题在目标内揭晓并收束是有效完成" in prompt
    assert "不得因没有新主线扣分" in prompt
    assert "反复拖延同一谜底" in prompt
    assert "post_reveal_engine" in prompt


@pytest.mark.parametrize("chapter_count", [50, 200])
def test_single_engine_judge_keeps_long_seriality_requirement(chapter_count):
    _system, prompt = ct._build_engine_judge_messages(
        kernel=KERNEL,
        genre="悬疑推理",
        sub_genre="悬疑推理",
        chapter_count=chapter_count,
        seed_concept="修复师听见失踪者录音。",
    )
    assert "长篇承载合同" in prompt
    assert "揭晓后只能换主线不得超过4分" in prompt
    assert "临时换新主线填充篇幅" in prompt


@pytest.mark.parametrize("chapter_count", [20, 49, 50, 200])
def test_batch_and_single_share_the_same_horizon_contract(chapter_count):
    _single_system, single = ct._build_engine_judge_messages(
        kernel=KERNEL,
        genre="悬疑推理",
        sub_genre="悬疑推理",
        chapter_count=chapter_count,
        seed_concept="修复师听见失踪者录音。",
    )
    _batch_system, batch = ct._build_engine_batch_judge_messages(
        cards=[("用户原始#0", "修复师听见失踪者录音。", KERNEL)],
        genre="悬疑推理",
        sub_genre="悬疑推理",
        chapter_count=chapter_count,
    )
    marker = "有限篇幅承载合同" if chapter_count < 50 else "长篇承载合同"
    assert marker in single and marker in batch
    assert "不得因没有新主线扣分" in single if chapter_count < 50 else "不得因没有新主线扣分" not in single
    assert "不得因没有新主线扣分" in batch if chapter_count < 50 else "不得因没有新主线扣分" not in batch


def test_short_kernel_generation_keeps_three_post_reveal_scenes_without_demanding_new_mainline():
    _system, prompt = ct._build_engine_kernel_messages(
        genre="悬疑推理",
        sub_genre="悬疑推理",
        lane="用户原始#0",
        chapter_count=20,
        seed_concept="修复师听见失踪者录音；封锁档案前必须交出证据。",
        authoritative_seed_concept="修复师听见失踪者录音；封锁档案前必须交出证据。",
    )
    assert '"post_reveal_scene_seeds"' in prompt
    assert "另写3个" in prompt
    assert "不得因没有新主线扣分" in prompt
    assert "如果只能继续查同一个真相" not in prompt
