"""Only explicit danger predicates can become hard immediate-threat threads."""
# ruff: noqa: RUF001
import pytest

from bestseller.services.chapter_seam import ThreadKind, extract_open_threads, validate_chapter_seam

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('tail', [
    '学徒接过手机，盯着屏幕看了片刻，说评论又多了几条。',
    '她锁好店门，扣上背包，抓起雨伞回家。',
    '大家围着桌子吃饭，周围一片笑声。',
    '大家围在桌边吃饭，周围一片笑声。',
    '街坊围在门口等维修，孩子们围住桌子观看。',
    '修理工被街坊围住，大家询问维修价格。',
    '他把信装进信封，追问明天几点出发。',
    '来人没有拔刀，只是把礼物放在桌上。',
    '守卫不再追击，双方各自退去。',
    '这场追击已经结束，众人收拾行囊。',
])
def test_ordinary_or_explicitly_negated_actions_do_not_create_threats(tail):
    assert not [t for t in extract_open_threads(tail) if t.kind == ThreadKind.IMMEDIATE_THREAT]


@pytest.mark.parametrize('tail', [
    '蒙面人突然拔刀，刀锋直指他的咽喉。',
    '守卫仍在追击，脚步声已经逼近。',
    '沈宁被几名守卫围在屋内。',
    '歹徒挟持了人质，要求所有人后退。',
    '守卫持刀围住主角，堵住了退路。',
])
def test_real_unresolved_threats_remain_detectable(tail):
    report = validate_chapter_seam(tail, '林青低头翻书，阳光正好。')
    threats = [t for t in report.open_threads if t.kind == ThreadKind.IMMEDIATE_THREAT]
    assert threats
    assert all(len(t.marker) > 1 for t in threats)
    assert any(d.thread.kind == ThreadKind.IMMEDIATE_THREAT for d in report.silent_drops)


def test_real_threat_continuation_still_passes_threat_check():
    report = validate_chapter_seam('守卫仍在追击，脚步声已经逼近。', '追击没有停止，他继续逃跑。')
    assert not any(d.thread.kind == ThreadKind.IMMEDIATE_THREAT for d in report.silent_drops)
