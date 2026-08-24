"""待你批准的书，自愈不许替你开写。

2026-08-24 真机，当场抓到：末日验证书 custom-apocalypse-1787538561 用
``stop_after_conception=true`` 建的——写下 planning_status=awaiting_concept_approval
和 conception_only=True，emit 的事件是 conception_only_complete + planning_started:False。
50 分钟后 worker 日志：

    self-heal: re-queued slug=custom-apocalypse-1787538561 kind=autowrite
               reason=under_target_chapters stuck_at=1

自愈看见「0 章 < 目标 12 章」就把整本书开写了。更糟的是它走的是正常
autowrite 入口，于是 pipelines._mark_project_autowrite_started 清掉
conception_only 并写下 **conception_approved=True** —— 框架替用户按下了「同意」，
而且抹掉了自己曾在等批准的证据（真机复核：conception_only 已空，
planning_status 已变成 writing）。

判据本身早就存在，只是**以字面形式住在 pipelines 里**，自愈那边根本不知道
它存在——同一事实住两地的又一例。这里做成一份，两边引同一个。
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.services.pipelines import project_awaits_concept_approval
from bestseller.worker.self_heal import _project_is_finished


def _p(status="planning", **meta):
    return SimpleNamespace(status=status, metadata_json=meta)


def test_conception_only_book_awaits_approval() -> None:
    assert project_awaits_concept_approval(_p(conception_only=True))


def test_awaiting_planning_status_counts_too() -> None:
    assert project_awaits_concept_approval(
        _p(planning_status="awaiting_concept_approval")
    )


def test_an_approved_book_does_not_await() -> None:
    assert not project_awaits_concept_approval(_p(planning_status="writing"))
    assert not project_awaits_concept_approval(_p())


def test_self_heal_treats_awaiting_approval_as_untouchable() -> None:
    """自愈入口的守卫必须挡住它——和完本/归档同一个出口。"""

    assert _project_is_finished(_p(conception_only=True))
    assert _project_is_finished(_p(planning_status="awaiting_concept_approval"))


def test_a_normal_planning_book_is_still_healable() -> None:
    """别把守卫开得太宽：普通规划中的书照旧要能被自愈捞起来。"""

    assert not _project_is_finished(_p(status="planning"))
    assert not _project_is_finished(_p(status="writing"))


def test_the_pipeline_and_self_heal_read_the_same_predicate() -> None:
    """两边必须引同一个判据，不许各写一份字面量。"""

    from pathlib import Path

    import bestseller.worker.self_heal as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "project_awaits_concept_approval" in src
