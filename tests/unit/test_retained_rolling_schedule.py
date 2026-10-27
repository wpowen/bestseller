from copy import deepcopy
import pytest
from bestseller.services.rolling_outline import (
    bind_retained_rolling_schedule, rolling_window_schedule_hash,
    build_macro_plan, build_rolling_outline_plan, load_rolling_outline_plan,
)

pytestmark=pytest.mark.unit


def schedule():
    return [dict(window_start=a,window_end=b,volume_number=1,window_index=i)
        for i,(a,b) in enumerate([(1,6),(7,12),(13,20)],1)]


def test_valid_old_partition_is_bound_without_regenerating_or_promoting():
    rows=schedule();before=deepcopy(rows)
    entries=bind_retained_rolling_schedule([{'volume_number':1,'chapter_count_target':20,
        'chapter_range':[1,20],'unknown_author_clause':'Do not alter the clock.'}],rows,rolling_window_schedule_hash(rows))
    assert [(x['start_chapter_number'],x['end_chapter_number']) for x in entries]==[(1,6),(7,12),(13,20)]
    assert all(x['unknown_author_clause']=='Do not alter the clock.' for x in entries)
    assert rows==before
    macro=build_macro_plan([{'chapter_number':n,'anchor':f'anchor {n}'} for n in range(1,21)])
    plan=build_rolling_outline_plan(macro,current_state_snapshot={'current_chapter':0},
        next_macro_anchor=macro.slots[6].to_dict(),source_snapshot_hash='source',window_size=6)
    with pytest.raises(ValueError,match='not approved'):
        load_rolling_outline_plan(macro.to_dict(),plan.to_dict(),source_snapshot_hash='source')
    loaded_macro,loaded_plan=load_rolling_outline_plan(macro.to_dict(),plan.to_dict(),
        source_snapshot_hash='source',require_approved=False)
    assert loaded_plan.window_start==1 and loaded_plan.window_end==6
    assert loaded_macro.total_chapters==20 and loaded_plan.status=='planned'


@pytest.mark.parametrize('fault',['hash','gap','overlap','index','unknown','missing_end','bool'])
def test_bad_retained_schedule_is_not_a_compatibility_bypass(fault):
    rows=schedule()
    if fault=='gap':rows[1]['window_start']=8
    elif fault=='overlap':rows[1]['window_start']=6
    elif fault=='index':rows[1]['window_index']=8
    elif fault=='unknown':rows[0]['new_authority']='approved'
    elif fault=='missing_end':rows[-1]['window_end']=19
    elif fault=='bool':rows[0]['window_index']=True
    digest='0'*64 if fault=='hash' else rolling_window_schedule_hash(rows)
    with pytest.raises(ValueError):
        bind_retained_rolling_schedule([{'volume_number':1,'chapter_count_target':20}],rows,digest)


def test_nonfinal_five_chapter_volume_is_rejected_even_with_self_consistent_hash():
    rows=[dict(window_start=1,window_end=5,volume_number=1,window_index=1),
          dict(window_start=6,window_end=13,volume_number=2,window_index=1)]
    with pytest.raises(ValueError,match='narrative volume invalid'):
        bind_retained_rolling_schedule([{'volume_number':1,'chapter_count_target':5},
            {'volume_number':2,'chapter_count_target':8}],rows,rolling_window_schedule_hash(rows))
