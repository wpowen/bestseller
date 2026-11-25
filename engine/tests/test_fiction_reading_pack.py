import hashlib
import json
import re

import pytest

from tools.fiction_reading_pack import COUNTS, MAPPING, PRIVATE, SHEET, create_pack


@pytest.fixture
def trial(tmp_path):
    inputs = []
    for arm, character in [("baseline-source", "张三"), ("candidate-source", "李四")]:
        root = tmp_path / arm
        (root / "steps").mkdir(parents=True)
        (root / "chapters").mkdir()
        (root / "story_bible.yaml").write_text("title: 不应显示的书名", encoding="utf-8")
        for n in range(1, 5):
            text = f"{character}听说了消息{n}。\n\n他走开了。"
            (root / f"steps/r2-ch{n}-draft.md").write_text("模型回复包装\n" + text, encoding="utf-8")
            (root / f"chapter-{n:03d}.r2.draft.md").write_text(text, encoding="utf-8")
            body = (text + "\n").encode()
            (root / f"chapter-{n:03d}.body.md").write_bytes(body)
            (root / f"chapters/{n:03d}.json").write_text(json.dumps({"body_sha256": hashlib.sha256(body).hexdigest()}))
        inputs.append(root)
    return *inputs, tmp_path / "pack"


def test_blind_files_match_history_and_fourth_is_separate(trial):
    baseline, candidate, output = trial
    create_pack(*trial)
    manifest = json.loads((output / PRIVATE / MAPPING).read_text("utf-8"))
    files = sorted((output / "试读").rglob("*.md"))
    assert len(files) == 4
    assert {x.parent.name for x in files} == {"01", "02"}
    assert {source["directory"] for source in manifest["sources"].values()} == {str(baseline), str(candidate)}
    for label, source in manifest["sources"].items():
        root = baseline if source["arm"] == "baseline" else candidate
        first = (output / "试读" / label / f"{label}-前三章.md").read_text("utf-8")
        link = re.search(r"\[阅读第四章\]\(([^)]+)\)", first)[1]
        fourth = (output / "试读" / label / link).read_text("utf-8")
        chapters = [(root / f"chapter-{n:03d}.r2.draft.md").read_text("utf-8") for n in range(1, 5)]
        assert first.index(chapters[0]) < first.index(chapters[1]) < first.index(chapters[2])
        assert chapters[3] not in first and chapters[3] in fourth and chapters[2] not in fourth
        for word in ("source", "baseline", "candidate", "不应显示的书名", "模型回复包装", "sha256", str(output)):
            assert word not in first + fourth
        for n, expected in enumerate(source["chapters"], 1):
            raw = (root / f"steps/r2-ch{n}-draft.md").read_bytes()
            assert expected["stage_sha256"] == hashlib.sha256(raw).hexdigest()
            assert expected["draft_sha256"] == hashlib.sha256(chapters[n - 1].encode()).hexdigest()
            history = (root / f"chapter-{n:03d}.body.md").read_bytes()
            assert expected["history_sha256"] == hashlib.sha256(history).hexdigest()
    assert len((output / PRIVATE / SHEET).read_text("utf-8-sig").splitlines()) == 1
    assert "尚未招募" in (output / PRIVATE / COUNTS).read_text("utf-8")
    assert (output / PRIVATE).stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "failure", ["缺模型回复", "缺草稿", "缺历史", "只有空白", "正文改字", "正文加空行", "账本错误"]
)
def test_missing_or_modified_chapter_never_falls_back(trial, failure):
    _, root, output = trial
    draft = root / "chapter-004.r2.draft.md"
    body = root / "chapter-004.body.md"
    (root / "final").mkdir()
    (root / "final/chapter-004.md").write_bytes(draft.read_bytes())
    if failure == "缺模型回复":
        (root / "steps/r2-ch4-draft.md").unlink()
    elif failure == "缺草稿":
        draft.unlink()
    elif failure == "缺历史":
        body.unlink()
    elif failure == "只有空白":
        draft.write_bytes(b" \n")
        body.write_bytes(b" \n")
    elif failure == "正文改字":
        body.write_text("不属于这一章的内容", encoding="utf-8")
    elif failure == "账本错误":
        (root / "chapters/004.json").write_text('{"body_sha256":"mistaken"}', encoding="utf-8")
    else:
        body.write_bytes(body.read_bytes().replace(b"\n\n", b"\n\n\n"))
    with pytest.raises(ValueError, match="第4章"):
        create_pack(*trial)
    assert not output.exists()


def test_rerun_keeps_reader_entries_and_frozen_labels_and_rejects_drift(trial):
    baseline, _, output = trial
    create_pack(*trial)
    mapping = (output / PRIVATE / MAPPING).read_bytes()
    sheet = output / PRIVATE / SHEET
    with sheet.open("a", encoding="utf-8") as stream:
        stream.write("参与者01,02\n")
    create_pack(*trial)
    assert "参与者01,02" in sheet.read_text("utf-8-sig")
    assert (output / PRIVATE / MAPPING).read_bytes() == mapping
    for name in ("chapter-004.body.md", "chapter-004.r2.draft.md"):
        (baseline / name).write_text("新改的一章。", encoding="utf-8")
    ledger = {"body_sha256": hashlib.sha256("新改的一章。".encode()).hexdigest()}
    (baseline / "chapters/004.json").write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="不一致"):
        create_pack(*trial)
    assert (output / PRIVATE / MAPPING).read_bytes() == mapping


def test_existing_reader_edit_is_preserved_and_reported(trial):
    create_pack(*trial)
    chapter = next((trial[2] / "试读").rglob("*第四章.md"))
    chapter.write_text("有人标过的一句。", encoding="utf-8")
    with pytest.raises(ValueError, match="阅读文件"):
        create_pack(*trial)
    assert chapter.read_text("utf-8") == "有人标过的一句。"
