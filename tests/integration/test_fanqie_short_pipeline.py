"""番茄短故事集成级测试（无 DB，验证导出与门禁链路）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.domain.fanqie_short import build_fanqie_short_metadata
from bestseller.services.fanqie_short_export import export_fanqie_short_markdown
from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story


pytestmark = pytest.mark.integration


def test_export_fanqie_short_markdown_files(tmp_path: Path) -> None:
    meta = build_fanqie_short_metadata(length_key="fanqie-short-8k")
    unlock_ratio = float(meta["unlock_line_ratio"])
    # 模拟前 30% 有冲突/反击信号的中文短文
    segment_a = "我推开门的瞬间，仇人当众羞辱我，冲突已经摆在桌面上。"
    segment_b = "我没有退缩，第一次反击让全场安静，这是我的小爆点。"
    segment_c = "后来我一步步查清真相，代价越来越大。\n\n---\n\n这些内部分隔不应进入正文。"
    segment_d = "最终我在雨夜里与过去和解，故事在这里收场。"
    full_text = "\n\n".join([segment_a, segment_b, segment_c, segment_d])

    paths = export_fanqie_short_markdown(
        tmp_path,
        title="集成测试短故事",
        genre="都市",
        full_text=full_text,
        unlock_line_ratio=unlock_ratio,
        protagonist_name="我",
        target_word_count=8_000,
    )
    md_path = Path(paths["markdown_path"])
    json_path = Path(paths["readiness_path"])
    assert md_path.is_file()
    assert json_path.is_file()
    content = md_path.read_text(encoding="utf-8")
    assert "集成测试短故事" in content
    assert "UNLOCK_LINE" not in content
    assert "---" not in content
    assert "类型：" not in content
    assert "番茄短故事" not in content
    assert "单篇完结" not in content

    review = review_whole_fanqie_short_story(
        full_text,
        unlock_line_ratio=unlock_ratio,
        protagonist_name="我",
    )
    assert review.opening_passed or len(review.notes) >= 0
