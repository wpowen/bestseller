"""完本的书不进自愈候选池。

2026-08-24 真机（书9）：一本 50/50 章、50 章有在架稿、status=completed 的书，
被自愈每 5 分钟捞一次，指纹恒为 ``repair|pending_rewrite_tasks||50|50``，修了
20 次全无进展，最后被盖上 requires_human_review + production_pause_reason。

政策**早已写下**，只是落在了隔壁分支：under_target 那条的注释写着
「a project the user explicitly finished or abandoned (completed / archived)
should not be auto-resumed」，而入口的 ``_project_is_archived`` 只挡 archived。
同一条政策只落在一处 —— 本仓库的招牌病。补在扫描入口 = 所有分支的汇合点。

例外：completion_export_error 在场时仍要放行，收尾自己说过「下一次结算重试
导出」，堵死它等于让导出失败的书永远导不出来。
"""

from __future__ import annotations

from types import SimpleNamespace

from bestseller.worker.self_heal import _project_is_finished


def _p(status: str, metadata: dict | None = None):
    return SimpleNamespace(status=status, metadata_json=metadata or {})


def test_completed_book_is_finished() -> None:
    assert _project_is_finished(_p("completed"))


def test_archived_book_is_finished() -> None:
    assert _project_is_finished(_p("archived"))
    assert _project_is_finished(_p("writing", {"library_archived": True}))


def test_writing_book_is_not_finished() -> None:
    for status in ("writing", "planning", "revising", "drafting", "paused", ""):
        assert not _project_is_finished(_p(status)), status


def test_completed_but_export_failed_still_gets_a_turn() -> None:
    """收尾说过下一次结算会重试导出——堵死它等于永远导不出来。"""

    assert not _project_is_finished(
        _p("completed", {"completion_export_error": "gate refused"})
    )


def test_archived_stays_blocked_even_with_an_export_error() -> None:
    """归档是用户的意思，任何理由都不该把它捞回来。"""

    assert _project_is_finished(
        _p("archived", {"completion_export_error": "gate refused"})
    )


def test_scan_entry_uses_the_finished_predicate() -> None:
    """守卫必须挂在扫描入口，不是某一条分支上。"""

    from pathlib import Path

    import bestseller.worker.self_heal as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    loop = src.split("    for project in projects:", 1)[1][:600]
    assert "_project_is_finished(project)" in loop
