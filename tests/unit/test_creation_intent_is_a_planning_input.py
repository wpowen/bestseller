"""建书页的入参集是规划层的必填输入，不是下游 agent 的附注。

正确的装配顺序（用户 2026-07-30 的原话）：前端选的题材／方向／类型／情绪构成一个
**入参集**，这个集合进入规划提示词，模型据此产出那句一句话设定，再由它衍生大纲
和全书。

现状不是这样。``_creation_intent_prompt_block`` 把全部勾选 JSON-dump 成一个块，
但它只被 ``_commercial_brief_prompt_block`` 使用——那是市场／角色／世界观那批
agent 的输入。而**一句话规划（概念淘汰赛）跑在它们之前**，收到的只有题材、章数、
频道、代价档和种子。

审计结果（2026-07-30）：叙事规模、反常识方向（用户界面自己写着「决定全书冲突
轴」）、脑洞引擎全都只落在那个 dict 里，一个都到不了一句话规划。调性和故事技能
在此前一小时刚被逐个补上——那是治症状：表单每加一个选项，就会再断一次。

判据：入参集**整体**作为一个输入交给规划层。这样新增选项自动在场，不需要记得
再补一次接线。

（JSON 块负责完整性，翻译成人话的行为指令负责可执行——块里那句注释说得很清楚：
模型无法只凭 ``"cost_style": "minimal"`` 行动。两层都要，不是二选一。）
"""

from __future__ import annotations

import inspect
import re

import pytest

from bestseller.services import concept_tournament as ct
from bestseller.services import conception

pytestmark = pytest.mark.unit


class TestTheIntentSetReachesPlanning:
    def test_the_tournament_accepts_the_intent_block(self) -> None:
        sig = inspect.signature(ct.run_concept_tournament)
        assert "creation_intent_block" in sig.parameters, (
            "入参集必须整体进入一句话规划，而不是逐字段补接线"
        )

    def test_the_premise_prompt_renders_it(self) -> None:
        source = inspect.getsource(ct._build_engine_kernel_messages)
        assert "creation_intent_block" in source

    def test_conception_passes_the_block_it_already_builds(self) -> None:
        source = inspect.getsource(conception.run_conception_pipeline)
        call = source[source.index("run_concept_tournament(") :]
        head = call[: call.index("retry_feedback=")]
        assert "creation_intent_block=" in head


class TestEveryStoryShapingChoiceIsInTheSet:
    """表单里任何影响故事内容的选项都必须出现在入参集里。

    这条是护栏而非描述：新增一个选项却忘了接线，它会立刻变红。
    """

    STORY_SHAPING_KEYS = (
        "tags",
        "audience",
        "scale",
        "tone",
        "brainhole",
        "wild_concept",
        "creativity_direction",
        "effect_skills",
        "cost_style",
    )

    def test_the_block_carries_them_all(self) -> None:
        source = inspect.getsource(conception._creation_intent_prompt_block)
        missing = [k for k in self.STORY_SHAPING_KEYS if f'"{k}"' not in source]
        assert not missing, f"入参集漏了影响故事的选项: {missing}"

    def test_the_form_sends_nothing_the_set_ignores(self) -> None:
        """反向检查：表单送出的故事类字段都要在入参集里有落点。"""

        import pathlib

        html = (
            pathlib.Path(inspect.getfile(conception)).parents[2]
            / "bestseller"
            / "web"
            / "novel_quickstart.html"
        ).read_text(encoding="utf-8")
        i = html.index("function collectStoryEnhancers")
        sent = set(re.findall(r"^\s{6}(\w+):", html[i : i + 900], re.M))
        source = inspect.getsource(conception._creation_intent_prompt_block)
        # concept_lab 是开关而非故事内容，其产物走 seed_concept。
        for key in sent - {"concept_lab"}:
            assert key in source, f"表单送出 {key}，入参集却没有它"


class TestGenreNeverDictatesTone:
    """玄幻 ≠ 苦难。题材不得替用户决定调性。"""

    def test_the_block_forbids_treating_itself_as_a_genre_override(self) -> None:
        source = inspect.getsource(conception._creation_intent_prompt_block)
        assert "不得改写题材" in source or "not a genre override" in source

    def test_the_light_tone_directive_rejects_suffering_as_depth(self) -> None:
        assert "禁止把苦难当深度" in ct._TONE_DIRECTIVES["light"]
