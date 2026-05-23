from __future__ import annotations

from uuid import uuid4

from pydantic import ValidationError
import pytest

from bestseller.domain.crowd_dynamics import CrowdScene
from bestseller.domain.honorific_system import HonorificSystem
from bestseller.domain.lineage_system import LineageKernel, LineageNode
from bestseller.domain.meta_layer import MetaLayerContract
from bestseller.domain.zeitgeist import ZeitgeistContract
from bestseller.services.crowd_scene_planner import render_crowd_scene_prompt_block
from bestseller.services.kernel_composer import (
    NarrativeRichnessKernels,
    render_narrative_richness_prompt_block,
)
from bestseller.services.lineage_address_gate import expected_lineage_address
from bestseller.services.meta_layer_composer import render_meta_layer_prompt_block

pytestmark = pytest.mark.unit


def test_lineage_kernel_requires_valid_parent_generation() -> None:
    master_id = uuid4()
    disciple_id = uuid4()
    kernel = LineageKernel(
        schools={
            "剑池": [
                LineageNode(
                    person_id=master_id,
                    school="剑池",
                    generation=2,
                    role="master",
                ),
                LineageNode(
                    person_id=disciple_id,
                    school="剑池",
                    generation=3,
                    role="disciple",
                    parent_master=master_id,
                ),
            ]
        },
        school_rules={"剑池": ["不得越辈称名"]},
    )
    honorifics = HonorificSystem(
        superior_to_inferior={"elder->disciple": "小辈"},
        inferior_to_superior={"disciple->elder": "师叔"},
        peer_address={"disciple->disciple": "师兄"},
        kinship_terms={},
        civil_to_military={},
        monastic_or_religious={},
        forbidden_addresses=[],
    )
    assert (
        expected_lineage_address(
            kernel,
            honorifics,
            speaker_id=disciple_id,
            listener_id=master_id,
        )
        == "师叔"
    )


def test_lineage_kernel_rejects_unknown_parent() -> None:
    with pytest.raises(ValidationError):
        LineageKernel(
            schools={
                "剑池": [
                    LineageNode(
                        person_id=uuid4(),
                        school="剑池",
                        generation=3,
                        role="disciple",
                        parent_master=uuid4(),
                    )
                ]
            }
        )


def test_crowd_scene_requires_mood_movement_and_renders() -> None:
    scene = CrowdScene(
        crowd_size_class="large",
        initial_mood="狐疑",
        triggering_event="粮仓起火",
        mood_arc=["狐疑", "哗然", "恐慌"],
        rumor_seed="有人说官仓早被调空",
        factional_split=["灾民", "守军"],
        resolution="leader_emerges",
    )
    block = render_crowd_scene_prompt_block(scene)
    assert "Mood arc" in block
    assert "粮仓起火" in block


def test_zeitgeist_and_meta_layer_join_composer() -> None:
    block = render_narrative_richness_prompt_block(
        NarrativeRichnessKernels(
            zeitgeist_contract=ZeitgeistContract(
                label="末世焦虑",
                core_anxiety="秩序随时断裂",
                dominant_aspiration="建立可信的小共同体",
                aesthetic_pressure="物资旧痕必须压过宏大口号",
                social_mobility_rule="可靠交付比身份更重要",
                volume_injections={1: "第一卷强调物资旧痕和互不信任"},
            ),
            meta_layer_contract=MetaLayerContract(
                layer_type="volume_epigraph",
                placement="卷首",
                narrative_function="提前压出时代精神",
                voice_rule="像旧档案摘录",
                spoiler_boundary="不得泄露幕后主谋",
                payoff_targets=["旧城钟声"],
            ),
        ),
        chapter_no=1,
    )
    assert "Zeitgeist Contract" in block
    assert "Meta Layer Contract" in block
    assert "旧城钟声" in block


def test_meta_layer_renderer() -> None:
    block = render_meta_layer_prompt_block(
        MetaLayerContract(
            layer_type="afterword",
            placement="终章后",
            narrative_function="收束主题余波",
            voice_rule="克制,不解释",
            spoiler_boundary="只回看已揭示事件",
            payoff_targets=["师门旧债"],
        )
    )
    assert "afterword" in block
    assert "师门旧债" in block

