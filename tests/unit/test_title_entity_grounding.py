"""书名的槽位词必须是故事实体，不是分类标签（2026-08-21 真机定罪）。

真机 custom-xuanhuan-1787320762 的书名 `怂货开锅全早市喊我外号` 读起来像
字符串拼接——因为它本来就是。离线复现整条链：

1. `build_platform_title_workflow` 产出 65 个候选，**全部**是把标签塞进模板：

       开局市井日常，我用市井日常证道      ← 同一个标签塞进两个槽
       市井日常巅峰：从市井日常开始
       重生后，市井日常成了市井日常心尖宠
       月照市井日常 / 市井日常藏娇 / 夫人今日也在证道

   槽位词来自 `tags`（玄幻/市井日常/奇物养成/轻松解压/单元剧/强反主人设/
   锅系金手指/男频/番茄爽文/长线伏笔）——**全是分类与营销标签**。
   而故事里真正的实体（温迟/蒸灵锅/通灵百家巷/老灶君）一个都没进去，
   因为 conception 传给书名生成器的 `main_characters` 是写死的
   `[{"name": "主角"}]`。

2. 质检 `_evaluate_title_quality` 给这些标题打 100 分，判词是
   「标题不是内部标签、半句碎片或明显关键词拼接」——**对着纯粹由内部标签
   拼成的标题自证不是标签拼接**。49/65 判 pass。

3. 选出的 primary 是 `{"title": "市井日常", "angle": "故事DNA兜底"}`——
   裸标签当书名，副标题还从「十九岁」中间截断成「最怂的十。」。
   单次 `title_platform_revision` 调用从这坨东西里抢救出了最终书名。

判据用与身份修复同一条原则、零词表、可确定性核对：
**槽位词必须在故事正文（logline+premise）里出现过。**
真机分离度：10 个分类标签命中 0/10，6 个故事实体命中 6/6。
"""

from __future__ import annotations

import pytest

from bestseller.services.platform_title_workflow import (
    is_story_grounded_token,
    ungrounded_title_tokens,
)

pytestmark = pytest.mark.unit

_PROSE = (
    "通灵百家巷里最怂的十九岁温符徒温迟，靠一口百年蒸灵锅守着父亲留下的早市摊，"
    "每天辰时开锅替坊民温符换零钱。锅底压着十二坊市三十年前的旧欠条，"
    "每张对应一座坊市欠老灶君的人情。"
)
_TAGS = [
    "玄幻", "市井日常", "奇物养成", "轻松解压", "单元剧",
    "强反主人设", "锅系金手指", "男频", "番茄爽文", "长线伏笔",
]


@pytest.mark.parametrize("tag", _TAGS)
def test_classification_tags_are_not_story_grounded(tag: str):
    assert is_story_grounded_token(tag, _PROSE) is False


@pytest.mark.parametrize("entity", ["蒸灵锅", "通灵百家巷", "温迟", "老灶君", "早市", "欠条"])
def test_real_story_entities_are_grounded(entity: str):
    assert is_story_grounded_token(entity, _PROSE) is True


def test_empty_prose_never_rejects():
    """构思正文缺失时无法判断，一律放行——不制造新的停产。"""
    assert is_story_grounded_token("市井日常", "") is True


def test_pile_up_title_reports_its_ungrounded_tokens():
    bad = "开局市井日常，我用市井日常证道"
    hits = ungrounded_title_tokens(bad, _TAGS, _PROSE)
    assert "市井日常" in hits


def test_grounded_title_reports_nothing():
    good = "我的蒸灵锅嘴比我还毒"
    assert ungrounded_title_tokens(good, _TAGS, _PROSE) == ()


def test_title_quality_gate_rejects_ungrounded_titles():
    """质检必须真的拒绝，而不是自证「不是标签拼接」。"""
    import inspect

    from bestseller.services import platform_title_workflow as m

    assert "ungrounded_title_tokens" in inspect.getsource(m._evaluate_title_quality)
