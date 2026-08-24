"""丧葬子族不许靠「丧失」和比喻性的「棺材」撑起来。

2026-08-24 真机（末日验证书 custom-apocalypse-1787538561，零创意种子）：
丧葬子族命中 4 次，**4 次全是误报**——

    方向感丧失 / 丧失运输能力 ×2   ← 「丧失」是 lose，不是丧事
    否则驾驶室就是棺材              ← 末日文里最普通的比喻

`丧(?!尸)` 的负向断言只排除了「丧尸」，而中文里 丧失/沮丧/丧气/丧胆/懊丧
全是高频常用词。裸「棺」同理：棺材当比喻是通用修辞。

本仓库定过案两次：
- 「子串词表每项必须是**事件**不能是名词」（2026-07-26 裸字「门」使 91% 钩子
   为幻影）
- 「单字信号是重灾区」

这条子族参与 distinct>=2 的支配判定，一个假子族就能把一本干净的书推过线。
（本案里剔掉它仍是 账+债 两族，判定不变——但那是运气，不是设计。）
"""

from bestseller.services.anti_default_motif import (
    default_debt_family_hits,
    is_debt_dominated,
)

_FUNERAL_RE = "灵堂|棺|出殡|殡[仪葬]|丧(?!尸)"


def _funeral_hit(text: str) -> bool:
    return any("灵堂" in p or "殡" in p for p in default_debt_family_hits(text))


# 真机原句
def test_losing_something_is_not_a_funeral() -> None:
    for text in (
        "被感染者四十八小时内会出现方向感丧失、攻击性倍增",
        "电力中断或设备故障即丧失运输能力",
        "他有些沮丧，却没有丧气",
    ):
        assert not _funeral_hit(text), text


def test_a_coffin_metaphor_is_not_a_funeral_motif() -> None:
    assert not _funeral_hit("撑过三周底座螺栓倒计时，否则驾驶室就是棺材")


def test_a_real_funeral_still_registers() -> None:
    """降误报不许把这一族整个关掉——真丧事必须还抓得到。"""

    for text in ("灵堂上棺盖被人掀开", "第二天出殡，殡仪馆的人来抬棺", "家里正在治丧"):
        assert _funeral_hit(text), text


def test_the_apocalypse_book_verdict_does_not_rest_on_the_false_subfamily() -> None:
    """真机复核：末日书仍判支配，靠的是 账+债 两个真子族。"""

    premise = (
        "他困在二十七米高的塔吊驾驶室里，每一次放钩都是一次新的算账；"
        "一本越翻越厚的账，每一次放钩都是一次新的信任债或仇恨债；"
        "否则驾驶室就是棺材。被感染者出现方向感丧失。"
    )
    hits = default_debt_family_hits(premise)
    assert not any("殡" in p for p in hits), hits
    assert is_debt_dominated(premise)
