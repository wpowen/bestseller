"""World-rule path identity on real isolated SQLite; no PG or model calls.

Reuse the current-consumer schema and its SQLite-only advisory-lock functions.
NULL codes are exercised on transient rows because the SQL column is NOT NULL.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_inspection_retired_world_rules import _row_state
from test_world_current_tree_consumers import (
    _node,
)
from test_world_current_tree_consumers import (
    db as db,
)
from test_world_current_tree_consumers import (
    knowledge_db as knowledge_db,
)
from test_world_current_tree_consumers import (
    offline as offline,
)
from test_world_current_tree_consumers import (
    tree_tables as tree_tables,
)

from bestseller.infra.db.models import NarrativeTreeNodeModel, ProjectModel, WorldRuleModel
from bestseller.services import narrative_tree as tree
from bestseller.services import world_rule_current

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _rules(project_id, *, include_null=False):
    codes = [
        "_", "__", "_____", "", "---",  # Different codes, empty normalized slug.
        "A.B", "A_B", "a-b", "A B",  # Punctuation and case collapse together.
        "Case", "CASE", "rule", "R001", "独立规则",
        "rule-00000001",  # Looks like a short UUID fallback suffix.
        UUID(int=1).hex,  # Even a full fallback UUID remains a normal code.
        "@" + UUID(int=2).hex,
    ]
    if include_null:
        codes.append(None)
    return [
        WorldRuleModel(
            id=UUID(int=index),
            project_id=project_id,
            rule_code=code,
            name=f"Identitybeacon rule {index}",
            description=f"Identitybeacon consequence {index}.",
            metadata_json={"untouched": index},
        )
        for index, code in enumerate(codes, 1)
    ]


def _expected_paths(rules):
    # These are the unambiguous normal codes in _rules, not a reimplementation
    # of the allocator. The literal 'rule' shares the old fallback path.
    ordinary = {
        "R001": "r001",
        "独立规则": "独立规则",
        "rule-00000001": "rule-00000001",
        UUID(int=1).hex: UUID(int=1).hex,
        "@" + UUID(int=2).hex: UUID(int=2).hex,
    }
    return {
        rule.id: f"/world/rules/{ordinary.get(rule.rule_code, '@' + rule.id.hex)}"
        for rule in rules
    }


async def test_complete_group_allocation_is_reserved_and_order_independent():
    rules = _rules(uuid4(), include_null=True)
    before = [(row.id, row.rule_code, row.name, dict(row.metadata_json)) for row in rules]
    paths = tree._world_rule_paths(rules)
    assert paths == _expected_paths(rules)
    assert tree._world_rule_paths(list(reversed(rules))) == paths
    assert len(set(paths.values())) == len(rules)
    assert [(row.id, row.rule_code, row.name, row.metadata_json) for row in rules] == before
    assert tree._world_rule_paths([]) == {}
    # A solitary ordinary fallback word remains backward compatible.
    literal = next(row for row in rules if row.rule_code == "rule")
    assert tree._world_rule_paths([literal]) == {literal.id: "/world/rules/rule"}
    assert tree.tree_segment("___", "rule") == "rule"
    assert tree.character_path("___") == "/characters/character"


@pytest.mark.parametrize("reverse_insertion", [False, True])
async def test_real_rebuild_retains_every_identity_and_never_reuses_ambiguous_path(
    db, tree_tables, monkeypatch, reverse_insertion,
):
    session, project, *_ = db
    assert session.bind.dialect.name == "sqlite"
    other = ProjectModel(
        slug=f"other-path-test-{uuid4().hex}",
        title="Unrelated ordinary book",
        genre="test",
        target_word_count=10000,
        target_chapters=4,
    )
    session.add(other)
    await session.flush()
    ordinary = WorldRuleModel(
        project_id=other.id,
        rule_code="R001",
        name="Other ordinary rule",
        description="Other book stays untouched.",
        metadata_json={},
    )
    rules = _rules(project.id)
    session.add_all([ordinary, *(reversed(rules) if reverse_insertion else rules)])
    await session.commit()
    await tree.rebuild_narrative_tree(session, project=other)
    await session.commit()
    other_nodes = list(await session.scalars(
        select(NarrativeTreeNodeModel).where(NarrativeTreeNodeModel.project_id == other.id)
    ))
    other_before = [_row_state(node) for node in other_nodes]
    source_before = [_row_state(row) for row in [*rules, ordinary]]

    # Materialized legacy aliases must disappear instead of selecting a member.
    session.add_all([
        _node(project.id, "/world/rules/rule", source_ref_id=rules[0].id),
        _node(project.id, "/world/rules/a-b", source_ref_id=rules[5].id),
        _node(project.id, "/world/rules/case", source_ref_id=rules[9].id),
    ])
    await session.commit()
    expected = _expected_paths(rules)

    # Real selection/validation still runs; only its iteration order changes.
    current = world_rule_current.current_world_rules

    async def reverse_current(*args, **kwargs):
        return list(reversed(await current(*args, **kwargs)))

    for reverse_current_group in (False, True, False):
        with monkeypatch.context() as order_patch:
            if reverse_current_group:
                order_patch.setattr(world_rule_current, "current_world_rules", reverse_current)
            counts = await tree.rebuild_narrative_tree(session, project=project)
            await session.commit()
        nodes = list(await session.scalars(
            select(NarrativeTreeNodeModel).where(
                NarrativeTreeNodeModel.project_id == project.id,
                NarrativeTreeNodeModel.source_type == "world_rule",
            )
        ))
        assert counts["node_type_counts"]["world_rule"] == len(rules)
        assert {node.source_ref_id: node.node_path for node in nodes} == expected
        for old_path in ("/world/rules/rule", "/world/rules/a-b", "/world/rules/case"):
            assert (
                await tree.get_narrative_tree_node_by_path(session, project.slug, old_path)
            ) is None
        assert await tree.resolve_narrative_tree_paths_for_project(
            session, project, ["/world/rules/rule", "/world/rules/a-b", "/world/rules/case"],
        ) == []
        for source_id, path in expected.items():
            node = await tree.get_narrative_tree_node_by_path(session, project.slug, path)
            assert node is not None and node.source_ref_id == source_id
        requested = list(reversed(list(expected.values())))
        resolved = await tree.resolve_narrative_tree_paths_for_project(session, project, requested)
        assert [node.node_path for node in resolved] == requested
        assert {node.source_ref_id for node in resolved} == set(expected)
        overview = await tree.build_narrative_tree_overview(session, project.slug)
        assert {
            node.source_ref_id for node in overview.nodes if node.source_type == "world_rule"
        } == set(expected)
        hits = await tree.search_narrative_tree_for_project(
            session, project, "identitybeacon", top_k=100,
        )
        assert {hit.node_path for hit in hits.hits if hit.source_type == "world_rule"} == set(
            expected.values()
        )
        assert [_row_state(row) for row in [*rules, ordinary]] == source_before
        assert [_row_state(node) for node in other_nodes] == other_before
        assert list(await session.scalars(
            select(NarrativeTreeNodeModel.id).where(NarrativeTreeNodeModel.project_id == other.id)
        )) == [node.id for node in other_nodes]
        other_node = await tree.get_narrative_tree_node_by_path(
            session, other.slug, "/world/rules/r001",
        )
        assert other_node is not None and other_node.source_ref_id == ordinary.id
