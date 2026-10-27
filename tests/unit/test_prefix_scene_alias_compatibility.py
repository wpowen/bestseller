"""Equivalent writer aliases are representation changes, not new story facts."""
import copy
import pytest
from bestseller.services.chapter_outline_readiness import _canonical_scene_plan_contract

@pytest.mark.parametrize('canonical,alias,value',[
    ('conflict_stakes','stakes','Lose the existing ticket'),
    ('conflict_buffs','pressure_stack',['Nightfall','Rising water']),
    ('spotlight_character','focus_character','Edda'),
    ('cut_point','breakpoint','The ferry departs'),
])
def test_exact_redundant_aliases_are_canonical_but_conflicts_still_change(canonical,alias,value):
    original={canonical:value,'unregistered_explicit_contract':{'keep':'all bytes'}}
    aliased={**copy.deepcopy(original),alias:copy.deepcopy(value)}
    assert _canonical_scene_plan_contract(aliased)==original
    assert aliased[alias]==value
    aliased[alias]='new contradictory requirement'
    assert _canonical_scene_plan_contract(aliased)!=original
    assert _canonical_scene_plan_contract(aliased)[alias]=='new contradictory requirement'
