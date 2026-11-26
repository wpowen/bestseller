"""A dead character named only in a past event (“当年”) is history, not a new action."""

from bookengine.life_planning_facts import validate_life_constraints

DEATHS = {"陆青槐": {"chapter": 11, "quote": "他伸手去抓供桌，身体却先一步倒下。", "status": "Evidence"}}


def _hits(text):
    return validate_life_constraints({"action": text}, DEATHS)


def test_past_event_with_dead_as_object_is_history():
    text = (
        "陆厌依据陆青槐生前留下的旧证，承认陆沉锋当年逼陆青槐背下偷谱旧罪，刮名和逐母也由陆重山授意；"
        "他将旧证与残谱的禁纹逐处比对，敲开侧殿空墙。"
    )
    assert _hits(text) == []


def test_dead_acting_now_is_still_flagged():
    assert _hits("陆青槐敲开侧殿空墙，露出另一套祖谱。")
    assert _hits("曾祖遗言响起，陆青槐随即现身指认。")


def test_great_grandfather_is_not_a_past_marker():
    assert _hits("曾祖让陆青槐出手夺谱。")
