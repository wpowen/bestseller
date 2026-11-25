import pytest

from bookengine.q1_chapter import amend
from bookengine.repair_placement import place_guarded_patch


INSERTION = "守门人将铜签放在桌沿。"


@pytest.mark.parametrize("after", [2, 3])
def test_finish_patch_preserves_two_paragraph_reveal_at_its_actual_insertion_point(after):
    original = "林青回到渡口。\n\n真正值钱的，不是丢在船上的绸缎，也不是匣子里的金簪。\n\n是那封退婚书。"
    patch = {"after": after, "text": INSERTION}
    body, note = amend(original, {"patch": patch}, {"characters": []}, {}, {})
    assert body == original and not note["patch"]
    assert note["patch_guard"] == {
        "status": "rejected",
        "reason": "splits_closing_reveal",
        "requested_after": after,
        "effective_after": 2,
    }


@pytest.mark.parametrize(
    ("lead", "ending"),
    (
        ("渡船已经靠岸。", "是那封退婚书。"),
        ("小满找的不是金子。她合起匣子。", "是那封退婚书。"),
        ("她要的不是金子，而是银子。", "是那封退婚书。"),
        ("她要的不是金子，而是银子。", "是夜，摆渡的人下工了。"),
        ("原来不是他。", "是否有人从渡口来过？"),
    ),
)
def test_independent_ending_keeps_supported_insertion(lead, ending):
    body, note = place_guarded_patch(f"{lead}\n\n{ending}", {"after": 2, "text": INSERTION})
    assert body == f"{lead}\n\n{INSERTION}\n\n{ending}" and note["status"] == "placed"
