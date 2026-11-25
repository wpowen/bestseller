"""The final chapter of the 84 verification book needed 8664 characters of required writer material."""

from types import SimpleNamespace

from bookengine.longutil import ask


def _client(limit):
    seen = {}

    def stage(name, prompt, schema, **kwargs):
        seen.update(kwargs)
        return "正文"

    return SimpleNamespace(prefix="", context_limit=limit, stage=stage), seen


def _final_chapter_sections():
    # Section sizes from the chapter 25 stop: contract 1398, outline 3092, cast 1514, facts 913, ...
    sizes = {
        "本章主场景硬合同": 1398,
        "本章章纲": 3092,
        "出场人物档案、口吻卡与当前状态": 1514,
        "人物当前事实，不得违背": 913,
    }
    sizes.update({"本章开口人物": 598, "本章故事义务": 314, "用户选定事实": 306, "上章结尾": 249, "本章推进线": 236})
    return [(name, "字" * (size - len(name) - 4)) for name, size in sizes.items()]


def test_final_chapter_material_fits_the_writer_limit():
    from bookengine.q1 import WRITER_CONTEXT_LIMIT

    client, seen = _client(WRITER_CONTEXT_LIMIT)
    ask(client, "r2-ch25-draft", "指令" * 700, _final_chapter_sections(), input_limit=30000)
    assert "input_error" not in seen


def test_old_limit_reproduces_the_stop():
    client, seen = _client(10000)
    ask(client, "r2-ch25-draft", "指令" * 700, _final_chapter_sections(), input_limit=30000)
    assert "写手必要材料需要" in seen.get("input_error", "")
