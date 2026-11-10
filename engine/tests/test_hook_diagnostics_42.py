from bookengine.hook_momentum import book_momentum, diagnose_ending, payoff_evidence


def test_diagnose_flat_closures_and_concrete_unresolved_hooks():
    flat = [
        "顾冬岚把火拨旺了一点。\n\n“好。”",
        "众人上了车。\n\n车轮终于转动起来。",
        "他背起包袱。\n\n“走吧。”",
    ]
    assert all(diagnose_ending(text)["classification"] == "flat_candidate" for text in flat)

    cases = {
        "迫近危险": "巷口忽然响起密集脚步。\n\n追兵的刀光已经逼到门外。",
        "刚揭事实": "顾冬岚翻开账页。\n\n他这才发现，落款竟是妹妹的名字。",
        "悬停动作": "石门只开了一线。\n\n他刚要伸手，门后的影子先动了。",
        "两难选择": "火已经烧到梁上。\n\n救妹妹就会丢掉雪耳，他只能当场选一个。",
        "反常物件": "旧铜铃一直没有风。\n\n它却在顾冬岚掌心自己响了。",
        "关系裂痕": "顾安澜追问他是否同行。\n\n顾冬岚没有回答。",
    }
    for expected, text in cases.items():
        result = diagnose_ending(text)
        assert result["classification"] == "hook_candidate"
        assert expected in result["types"]
        assert result["status"] == "Inference"


def test_diagnose_specific_natural_tail_candidates_without_quality_claims():
    cases = [
        ("刚揭事实", "布角上绣着一个日期。\n\n正是三天之后。"),
        ("迫近危险", "前方最后一层冰封正向中间合拢。\n\n冰层又合拢一尺。"),
        ("两难选择", "证据和人，今晚只能先拿一样。"),
        ("关系裂痕", "她把最后一枚避寒药按回掌心，笑意也彻底藏了下去。"),
        ("关系裂痕", "他将炭笔搁下，没有看顾安澜。"),
        ("悬停动作", "他没有站起来，仍靠着山壁。\n\n他还在往前爬。"),
        ("反常物件", "顾冬岚掌中的绳尾忽然自行绷直，黑石泥逆坡聚拢。"),
    ]
    for expected, text in cases:
        result = diagnose_ending(text)
        assert result["classification"] == "hook_candidate"
        assert expected in result["types"]


def test_diagnose_laid_trap_reveal_without_treating_a_true_flat_close_as_hook():
    reveal = "他们选出的路，从一开始就是韩阙留下的错口。"
    flat = "韩阙走后，众人收好绳索，各自回房歇息。"

    result = diagnose_ending(reveal)
    assert result["classification"] == "hook_candidate"
    assert "刚揭事实" in result["types"]
    assert diagnose_ending(flat)["classification"] == "flat_candidate"


def test_payoff_evidence_requires_due_quote_grounded_in_body():
    text = "顾冬岚借来雪耳的蛮力，一拳震开石门，抢回三枚灵石。"
    fulfilled = payoff_evidence(
        text,
        {"payoff_due": True, "payoff_fulfilled": True, "payoff": {"quote": text, "fulfilled": True}},
    )
    assert fulfilled["classification"] == "fulfilled"
    assert fulfilled["status"] == "Evidence"

    missing = payoff_evidence(
        text,
        {"payoff_due": True, "payoff_fulfilled": False, "payoff": {"quote": "不存在的引文"}},
    )
    assert missing["classification"] == "unfulfilled"
    assert missing["status"] == "Unknown"


def test_book_momentum_lists_flat_and_due_unfulfilled_chapters():
    chapters = [
        {"number": 1, "text": "他点点头。\n\n“好。”", "meta": {"payoff_due": True}},
        {
            "number": 2,
            "text": "门外的追兵已经举刀。",
            "meta": {"payoff_due": True, "payoff_fulfilled": True, "payoff": {"fulfilled": True, "quote": "举刀"}},
        },
    ]
    report = book_momentum(chapters)
    assert report["ending"]["flat_count"] == 1
    assert report["ending"]["flat_chapters"] == [1]
    assert report["payoff"]["due"] == 2
    assert report["payoff"]["fulfilled"] == 1
    assert report["payoff"]["unfulfilled_chapters"] == [1]
