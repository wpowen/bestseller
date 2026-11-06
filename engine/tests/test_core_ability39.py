from bookengine.core_ability import core_source, ability_slots, ability_issues, verify_core_use, development_issues


def bible():
    return {
        "title": "借影换命",
        "power": {"name": "借影", "rule": "借他人影子复制动作"},
        "volumes": [
            {
                "number": 1,
                "start": 1,
                "end": 5,
                "core_development": {
                    "new_use": "复制影主开锁动作",
                    "new_cost": "忘记同伴的笑容",
                    "new_limit": "只能在灯下使用",
                },
            },
            {
                "number": 2,
                "start": 6,
                "end": 10,
                "core_development": {
                    "new_use": "同时借两人的影子",
                    "new_cost": "失去对亲人的亲近",
                    "new_limit": "须有两盏火光",
                },
            },
        ],
    }


def test_phase_half_including_odd_length_and_first_chapter():
    slots = ability_slots(bible(), 1, 10)
    assert sum(s["core_required"] for s in slots[:5]) >= 3
    assert sum(s["core_required"] for s in slots[5:]) >= 3
    assert slots[0]["core_required"]
    assert core_source(bible())["name"] == "借影"


def test_mention_or_fabricated_quote_does_not_pass():
    text = "他借影复制守卫的开锁动作，使铁门终于弹开。"
    good = {"decisive": True, "mechanism": "借影", "action_quote": "他借影复制守卫的开锁动作", "result_quote": text}
    assert verify_core_use(good, text, bible())["status"] == "verified"
    assert verify_core_use(dict(good, result_quote="铁门打开了。"), text, bible())["status"] == "unknown"
    assert verify_core_use(dict(good, decisive=False), text, bible())["status"] == "not_decisive"
    mention = "他想起借影的规矩，接着用普通钥匙开了门。"
    assert (
        verify_core_use(dict(good, action_quote=mention, result_quote=mention), mention, bible())["status"] == "unknown"
    )


def test_plan_requires_actual_application_and_stage_development():
    rows = [dict(number=i, core_required=True, core_application="") for i in range(1, 6)]
    assert any(i["kind"] == "core_application" for i in ability_issues(bible(), [], rows))
    bad = bible()
    bad["volumes"][1]["core_development"] = dict(bad["volumes"][0]["core_development"])
    assert development_issues(bad)[0]["stage"] == 2
    assert not development_issues(bible())


def test_exhausted_application_repair_still_reserves_concrete_scene_plan():
    from bookengine.core_ability import enforce_ability_plan

    source = bible()
    bad = [dict(number=n, type="对抗", scenes=[dict(goal="打开封门", conflict="守卫拦路")]) for n in range(1, 6)]
    planned = enforce_ability_plan(source, bad)
    assert len([r for r in planned if r.get("core_required")]) >= 3
    assert not ability_issues(source, [], planned)
    assert any("复制影主开锁动作" in str(r["scenes"]) for r in planned)
    assert all("core_required" not in r for r in bad)
