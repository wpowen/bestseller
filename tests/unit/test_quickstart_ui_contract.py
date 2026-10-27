from pathlib import Path
import json
import re
import subprocess


HTML = Path("src/bestseller/web/novel_quickstart.html").read_text(encoding="utf-8")


def test_budget_estimate_ignores_stale_response_after_length_change() -> None:
    """A slow estimate for the default 500 chapters must not overwrite 20."""
    assert "estimateRequestId" in HTML
    assert "if (requestId !== estimateRequestId) return;" in HTML


def test_failed_or_empty_tasks_cannot_advertise_reader_or_listing() -> None:
    """A conception failure with no project/chapters has no readable work."""
    assert "canShowReader" in HTML
    assert "canShowListing" in HTML
    assert "project_created !== false" in HTML
    assert "canShowReader ?" in HTML
    assert "if (slug && canShowListing)" in HTML


def test_failed_and_paused_states_have_truthful_diagnostic_signals() -> None:
    assert "effectiveErrorCount" in HTML
    assert "projectStatus === 'paused'" in HTML
    assert "缺少运行健康证据" in HTML
    assert "所有信号正常" not in HTML


def test_paused_projects_do_not_render_active_repair_language() -> None:
    assert "isPausedProject" in HTML
    assert "项目已暂停，等待恢复" in HTML
    assert "projectStatus === 'paused' && !isActiveTask(task)" in HTML
    assert "projectStatus === 'paused' && !isActive" in HTML


def test_taxonomy_contract_separates_defaults_from_user_tags() -> None:
    assert "txDefaultTags" in HTML
    assert "txUserTags" in HTML
    assert "default_tags:" in HTML
    assert "user_tags:" in HTML
    assert "tags: txEffectiveTags" in HTML


def test_conception_only_completion_is_not_rendered_as_finished_book() -> None:
    """A stop-after-conception task has no prose and must stay review-pending."""
    assert "const conceptionOnly = status === 'completed' && isConceptionOnlyTask(task);" in HTML
    assert "label: '概念已就绪'" in HTML
    assert "mode: '等待审批后进入正文'" in HTML
    assert "pvs.textContent = '概念待审核'" in HTML
    assert "🧠 审阅构思" in HTML
    assert HTML.count('title="审阅概念快照并批准后进入正文"') == 1
    assert "/design/${esc(slug)}" in HTML
    assert "designLink.textContent = conceptionOnly ? '🧠 审阅构思' : '书籍文档'" in HTML
    assert "const canShowListing = !conceptionOnly &&" in HTML
    assert "if (isConceptionOnlyTask(task)) return false;" in HTML
    assert "isCompletedBookTask(task) && !isAttentionTask(task)" in HTML
    assert "if (task?.status !== 'completed' || isConceptionOnlyTask(task) || isExecutionWindowTask(task)) return false;" in HTML
    # Execute the real page predicates: local window success and conception-only
    # success must never be presented as whole-book completion.
    names = ("getTaskChapterWordStats", "isConceptionOnlyTask", "isExecutionWindowTask", "isCompletedBookTask")
    functions = []
    for name in names:
        found = re.search(r"^  function " + name + r"\([^\n]*\) \{[\s\S]*?^  \}", HTML, re.M)
        assert found, name
        functions.append(found.group())
    stats = {"adoption_evidence": "verified", "adopted_chapters": 4,
             "adopted_chapter_numbers": [1, 2, 3, 4], "target_chapters": 4, "chapters": []}
    tasks = [
        {"status": "completed", "chapter_word_stats": stats,
         "delivery_closure": {"status": "verified", "is_complete": True}},
        {"status": "completed", "chapter_word_stats": stats, "result": {"mode": "conception_only"}},
        {"status": "completed", "chapter_word_stats": stats, "result": {"execution_window": {"start": 1, "end": 4}}},
        {"status": "completed", "chapter_word_stats": {**stats, "adopted_chapters": 2}},
        {"status": "completed", "chapter_word_stats": {**stats, "adoption_evidence": "unverified"}},
        {"status": "failed", "chapter_word_stats": stats},
        {"status": "completed", "chapter_word_stats": stats},
    ]
    script = "\n".join(functions) + "\nconsole.log(JSON.stringify(JSON.parse(process.argv[1]).map(isCompletedBookTask)));"
    result = subprocess.run(["/opt/homebrew/bin/node", "-e", script, json.dumps(tasks)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [True, False, False, False, False, False, False]
