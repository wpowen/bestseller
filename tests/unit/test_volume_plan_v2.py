from __future__ import annotations

import pytest

from bestseller.domain.volume_plan import VolumeMilestone, VolumePlanV2, load_volume_plans_v2


def _milestone(start: int, end: int, label: str = "林渊核验井口账印并付代价") -> VolumeMilestone:
    return VolumeMilestone(
        chapter_range=(start, end),
        milestone_label=label,
        required_evidence=("井口账印",),
        reveals_unlocked=("kou_zhang_ren",),
        character_state_promises=("林渊手腕债印加深",),
    )


def test_volume_plan_v2_requires_five_milestones() -> None:
    with pytest.raises(ValueError, match="at least five"):
        VolumePlanV2(volume_no=1, chapter_range=(1, 50), milestones=(_milestone(1, 10),))


def test_volume_milestone_requires_evidence() -> None:
    with pytest.raises(ValueError, match="required_evidence"):
        VolumeMilestone(
            chapter_range=(1, 6),
            milestone_label="林渊核验井口账印并付代价",
            required_evidence=(),
        )


def test_load_volume_plans_v2_from_mapping() -> None:
    payload = {
        "volumes": [
            {
                "volume_no": 1,
                "chapter_range": [1, 30],
                "milestones": [
                    {
                        "chapter_range": [i, i + 5],
                        "milestone_label": f"林渊核验第{i}段井口账印并付代价",
                        "required_evidence": ["井口账印"],
                    }
                    for i in (1, 7, 13, 19, 25)
                ],
            }
        ]
    }

    plans = load_volume_plans_v2(payload)

    assert plans[0].volume_no == 1
    assert len(plans[0].milestones) == 5
