from bookengine.ability_methods import ability_terms
from bookengine.core_ability import core_source, verify_core_use


def eval45_bible():
    return {
        "title": "我借你兽力，你借我归途",
        "power": {
            "cost": "每次借力永久失去一段温暖记忆。",
            "limit": "单次最多借用感知、身法、力量中的一项，不可叠加。",
        },
        "volumes": [
            {
                "number": 1,
                "core_development": {
                    "new_use": "顾川序借霜砚秋的钻雪之力破开冻硬矿壁。",
                    "new_cost": "失去雪夜记忆。",
                    "new_limit": "一次只能借用一项能力。",
                },
            }
        ],
    }


def test_declared_power_aliases_include_bible_subabilities_not_arbitrary_plan_words():
    bible = eval45_bible()
    terms = ability_terms(bible)
    assert {"借力", "感知", "身法", "力量", "钻雪之力"} <= terms
    assert "冻硬矿壁" not in terms
    assert set(core_source(bible)["aliases"]) == terms


def test_grounded_ordered_operation_and_concrete_result_do_not_require_a_connector_word():
    bible = {"power": {"name": "借影", "rule": "借影复制动作"}}
    action = "林川借影复制守卫的开锁动作。"
    outcome = "铁门轰然打开，他带着同伴冲出牢房。"
    raw = dict(decisive=True, mechanism="借影", action_quote=action, result_quote=outcome)
    result = verify_core_use(raw, action + outcome, bible)
    assert result["status"] == "verified"
    assert result["causal_basis"] == "ordered_grounded_operation_and_concrete_result"


def test_prior_delay_and_post_use_stop_do_not_negate_positive_use_between_them():
    bible = {"power": {"name": "借力", "abilities": ["身法"]}}
    action = "顾川序没有立刻借身法。霜砚秋点头后，契纹发亮，身法涌入双腿。他借完立刻停住，没有继续借力。"
    outcome = "两人穿过骨阵，活着抵达谷口。"
    raw = dict(decisive=True, mechanism="身法", action_quote=action, result_quote=outcome)
    assert verify_core_use(raw, action + outcome, bible)["status"] == "verified"


def test_generic_power_label_normalizes_to_declared_specific_alias_in_quote():
    bible = eval45_bible()
    action = "顾川序伸手借她的钻雪之力，冻壁随即裂开。"
    outcome = "最后一层矿壁被推开，妹妹从夹缝里被救出。"
    raw = dict(decisive=True, mechanism="力量", action_quote=action, result_quote=outcome)
    result = verify_core_use(raw, action + outcome, bible)
    assert result["status"] == "verified"
    assert result["mechanism"] == "钻雪之力"
    assert result["reported_mechanism"] == "力量"


def test_opponent_failure_word_does_not_turn_successful_ability_into_failure():
    bible = {"power": {"name": "借力", "abilities": ["力量"]}}
    action = "顾川序借用力量推落冻柱，碎岩随即塌进井口。"
    outcome = "霍岐没能逃开，被碎岩逼退并坠入雪坑。"
    raw = dict(decisive=True, mechanism="力量", action_quote=action, result_quote=outcome)
    assert verify_core_use(raw, action + outcome, bible)["status"] == "verified"


def test_grounded_control_mark_shattering_is_a_concrete_result():
    bible = {"power": {"name": "借力", "abilities": ["感知"]}}
    action = "霜砚秋把感知送入顾川序体内，兽群方向立刻变得清楚。"
    outcome = "黑脊兽撞入废仓，脊上的控制红纹当场炸裂。"
    raw = dict(decisive=True, mechanism="感知", action_quote=action, result_quote=outcome)
    assert verify_core_use(raw, action + outcome, bible)["status"] == "verified"


def test_item40_non_use_failure_and_unregistered_mechanism_stay_unknown():
    bible = {"power": {"name": "借力", "abilities": ["身法"]}}
    cases = [
        (
            "顾川序没有借身法，只举起普通钥匙。",
            "普通钥匙打开铁门，他逃出牢房。",
            "身法",
            True,
        ),
        (
            "顾川序尝试借身法，契纹随即失效。",
            "他改用普通钥匙打开铁门，逃出牢房。",
            "身法",
            True,
        ),
        ("顾川序借飞行越过高墙。", "高墙被越过，他逃出牢房。", "飞行", True),
        ("顾川序借身法越过高墙。", "他越过高墙，逃出牢房。", "身法", "确实决定结果"),
        ("顾川序借力越过高墙。", "他越过高墙，逃出牢房。", "身法", True),
    ]
    for action, outcome, mechanism, decisive in cases:
        raw = dict(decisive=decisive, mechanism=mechanism, action_quote=action, result_quote=outcome)
        assert verify_core_use(raw, action + outcome, bible)["status"] == "unknown"


def test_distant_unrelated_result_does_not_turn_plan_or_mention_into_evidence():
    bible = {"power": {"name": "借影", "rule": "借影复制动作"}}
    action = "林川借影看清墙上的旧划痕。"
    outcome = "守卫倒下，铁门打开，同伴逃出牢房。"
    text = action + "他在空屋里等了很久。" * 180 + outcome
    raw = dict(decisive=True, mechanism="借影", action_quote=action, result_quote=outcome)
    assert verify_core_use(raw, text, bible)["status"] == "unknown"
