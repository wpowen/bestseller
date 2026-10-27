import pytest

from bestseller.services.concept_tournament import _build_hook_from_engine_messages


@pytest.mark.parametrize(
    "seed,name",
    [
        ("少年陆衡，带工友凿穿矿坑。", "陆衡"),
        ("落魄画师欧阳追，替失踪者画最后一张脸。", "欧阳追"),
    ],
)
def test_each_hook_preserves_authoritative_name(seed, name):
    _, prompt = _build_hook_from_engine_messages(
        genre="测试题材", sub_genre="", kernel={}, seed_concept=seed,
    )
    assert f"原始种子已指定主角姓名：{name}。每条concept必须逐字保留该姓名" in prompt
    assert "不能仅用代词、职业或身份称呼代替" in prompt


def test_unnamed_seed_does_not_acquire_a_name_from_generated_card():
    _, prompt = _build_hook_from_engine_messages(
        genre="测试题材", sub_genre="", kernel={"protagonist_identity": "少年陆衡"},
        seed_concept="有人发现港口的影子开始倒着走。",
    )
    assert "原始种子已指定主角姓名" not in prompt
