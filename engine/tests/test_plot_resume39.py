from bookengine import q1_plan
from bookengine.outline_diversity import STRUCTURE_CHECKS_VERSION
from bookengine.solution_substance import solution_issues
from bookengine.storage import save
from bookengine.q1_schema import DATA
from test_chapter_rhythm39 import OfflineClient, chapter


def test_same_version_cache_rechecks_changed_actual_methods_and_reuses_exhaustion(tmp_path):
    bible = dict(volumes=[dict(number=1, start=1, end=10)])
    original = dict(chapter(5), solution_method="阵钉", solution_target="门锁")
    save(
        tmp_path / "outline-diversity/005-005.json",
        dict(
            status="passed",
            structure_checks_version=STRUCTURE_CHECKS_VERSION,
            chapters=[original],
            planned_chapters=[original],
        ),
    )
    recent = [
        dict(
            chapter(n, "情感"),
            fact_source="final_body",
            scenes=1,
            solution_method="账本",
            solutions=[dict(method_noun="阵钉", content="拆阵")],
        )
        for n in range(1, 4)
    ]
    client = OfflineClient(tmp_path, [dict(chapters=[dict(original, solution_method="绳索")])] * 3)
    value = q1_plan.outlines(
        client, bible, {}, DATA["profile"], dict(number=1, start=1, end=10), 5, 5, {}, recent, [], []
    )
    assert client.calls
    assert not solution_issues(bible, recent, value)
    exhausted = dict(value[0], solution_method="阵钉")
    latest = max((tmp_path / "outline-diversity").glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    import json

    report = json.loads(latest.read_text())
    report.update(status="failed_continued", chapters=[exhausted], planned_chapters=[exhausted])
    save(latest, report)
    resumed = OfflineClient(tmp_path)
    value = q1_plan.outlines(
        resumed, bible, {}, DATA["profile"], dict(number=1, start=1, end=10), 5, 5, {}, recent, [], []
    )
    assert not resumed.calls  # same historical evidence cannot cause unbounded paid retries
    assert solution_issues(bible, recent, value)
