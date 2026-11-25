"""The draft request must resolve the same open event before its planned first scene takes effect."""

import pytest

from bookengine.opening_choice import HEADING
from bookengine.storage import save
from support_minimal_b import CaptureClient, MinimalBTests as WriterFixture


CHOICE = "她若烧掉账册，陆七便会失去作证的机会；若救下陆七，追兵便能拿走账册。"
PENDING = "阿宁的手停在册子和陆七中间，迟迟没有再进一步。"
ARCHIVED = f"阿宁走到门边。{CHOICE}\n\n{PENDING}"


@pytest.mark.parametrize(
    ("variant", "trigger"),
    [
        ("continued", True),
        ("settled", False),
        ("different_decision_at_end", False),
        ("another_physical_pause", False),
        ("retold_dilemma", False),
        ("alternate_cast", False),
        ("explicit_pov_switch", False),
        ("delayed_reveal", False),
        ("plan_without_actual_options", False),
        ("missing_pov_quote", False),
        ("failed_state", False),
    ],
)
def test_first_scene_instruction_in_the_production_draft(variant, trigger):
    fixture = WriterFixture()
    fixture.setUp()
    try:
        fixture.bible["characters"].append(dict(name="陆七", role="support"))
        body = ARCHIVED
        quote = PENDING
        if variant == "settled":
            body += "\n\n阿宁抓住陆七的腰带，把人拖出了木门。"
        elif variant == "different_decision_at_end":
            body += "\n\n阿宁牵着陆七离开，仍未决定晚饭吃什么。"
        elif variant == "another_physical_pause":
            body += "\n\n阿宁牵着陆七到了渡口。阿宁站在船头，迟迟没有再进一步。"
        elif variant == "retold_dilemma":
            body = f"阿宁想起旧戏里的台词：“{CHOICE}”\n\n{PENDING}"
        elif variant == "plan_without_actual_options":
            body = "门外的人都走了。\n\n" + PENDING
        elif variant == "missing_pov_quote":
            quote = "阿宁没有用过这句。"
        save(fixture.out / "chapter-001.body.md", body)
        prior = dict(
            number=1,
            status="complete",
            fact_source="final_body",
            summary="阿宁面临取舍",
            state_failed=variant == "failed_state",
            present=["阿宁", "陆七"],
            end_pov="阿宁",
            end_pov_quote=quote,
        )
        save(fixture.out / "chapters/001.json", prior)
        save(
            fixture.out / "writing_outlines/001.json",
            dict(number=1, ending_type="两难选择", ending_content="阿宁要权衡救人和毁册"),
        )
        cast = ["陆七"] if variant == "alternate_cast" else ["阿宁", "陆七"]
        scene = dict(goal="赶到渡口", characters=cast, speaker_intents=[{"name": "阿宁", "goal": "先保住人"}])
        outline = dict(fixture.outline, scenes=[scene], type="对抗", opening_conflict="码头有人等着")
        if variant == "explicit_pov_switch":
            scene["pov"] = "陆七"
        elif variant == "delayed_reveal":
            outline["opening_conflict"] = "首场看码头外的人等船，第二场才揭晓阿宁是怎么出来的"

        class DraftReceived(Exception):
            pass

        client = CaptureClient(fixture.out, [], {"r2-ch2-draft": DraftReceived()})
        with pytest.raises(DraftReceived):
            fixture.run_chapter(client, {}, [prior], outline)
        request = client.calls[0]["prompt"]
        assert (HEADING in request) == trigger
        assert "开头两段写明主角当场选了哪一边" not in request
        if trigger:
            assert request.index(HEADING) < request.index("本章主场景硬合同")
            assert all(
                k in request.split("\n", 3)[2]
                for k in ("烧掉账册", "失去作证的机会", "追兵便能拿走账册", "先保住人", "赶到渡口")
            )
            assert "改变陆七眼下的处境" in request and CHOICE not in request and PENDING not in request
    finally:
        fixture.doCleanups()
