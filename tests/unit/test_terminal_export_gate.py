"""The last check before bytes leave the building.

``_run_terminal_export_gate`` re-runs the final quality gates on the exact text
a direct (non-pipeline) export is about to write. It had no test of its own —
its only coverage was incidental, via four export-plumbing tests whose
placeholder chapter happened to pass. When the ai_flavor detector learned to
grade chapter-level repetition by magnitude (2026-08-06), that placeholder
started failing on its own merits and took the plumbing tests down with it,
which is how the absence showed up.

So the two concerns are now separate: the plumbing tests stub this gate out,
and the gate is tested here for the property that actually matters — a chapter
the quality gates reject must not reach a file.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel
from bestseller.services import exports as export_services
from test_pipeline_services import build_project

pytestmark = pytest.mark.unit


# One sentence, 200 times. This was the export fixture until 2026-08-06; it is
# the most extreme form of the chapter-level repetition the detector grades, so
# it makes an honest negative sample.
_UNREADABLE = "# 第1章 失准星图\n\n" + ("沈砚按住星图，港口的雾又往前压了一尺。" * 200)


def _chapter_payload(content: str):
    project_id = uuid4()
    chapter = ChapterModel(
        project_id=project_id,
        chapter_number=1,
        title="失准星图",
        status="complete",
        production_state="ok",
    )
    chapter.id = uuid4()
    chapter.metadata_json = {}
    draft = ChapterDraftVersionModel(
        project_id=project_id,
        chapter_id=chapter.id,
        version_no=1,
        content_md=content,
        word_count=len(content),
        is_current=True,
    )
    draft.id = uuid4()
    return chapter, draft


def test_a_chapter_the_gates_reject_never_reaches_a_file() -> None:
    project = build_project()
    payload = _chapter_payload(_UNREADABLE)

    with pytest.raises(ValueError) as excinfo:
        export_services._run_terminal_export_gate(project, [payload])

    message = str(excinfo.value)
    assert "final_quality_gate_blocked" in message
    assert "chapter 1" in message, "拦下来却不说是哪一章，等于没法处理"


def test_the_block_reason_is_named() -> None:
    """A refusal with no reason cannot be acted on — that is how a book 'vanishes'."""

    project = build_project()
    payload = _chapter_payload(_UNREADABLE)

    with pytest.raises(ValueError) as excinfo:
        export_services._run_terminal_export_gate(project, [payload])

    message = str(excinfo.value)
    assert message.split("chapter 1:", 1)[1].strip(), "阻断原因为空"
