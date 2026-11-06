import json
import yaml
from tools.book_checkup import check_book


def test_body_receipts_stage_caps_and_unknowns(tmp_path):
    bible = {
        "title": "借影换命",
        "power": {"name": "借影", "rule": "借影复制动作"},
        "volumes": [{"number": 1, "start": 1, "end": 5}],
    }
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(bible, allow_unicode=True))
    (tmp_path / "chapters").mkdir()
    text = "他借影复制守卫的开锁动作，使铁门终于弹开。"
    for n in range(1, 6):
        (tmp_path / f"chapter-{n:03d}.body.md").write_text(text)
        core = {"decisive": True, "mechanism": "借影", "action_quote": "他借影复制守卫的开锁动作", "result_quote": text}
        meta = dict(
            number=n,
            type="对抗" if n < 5 else "情感",
            solutions=[{"type": str(n), "content": "拆阵眼"}],
            core_use={"raw": core} if n != 3 else {"raw": dict(core, result_quote="伪造的结果。")},
        )
        (tmp_path / f"chapters/{n:03d}.json").write_text(json.dumps(meta, ensure_ascii=False))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = check_book(tmp_path)
    assert report["solution_substance"]["distribution"] == {"阵": 5}
    assert report["solution_substance"]["excess_count"] == 2
    assert report["core_ability"]["verified_chapters"] == [1, 2, 4, 5]
    assert report["core_ability"]["unknown_chapters"] == [3]
    assert report["core_ability"]["ratio"] == 0.8
    assert report["chapter_rhythm"]["longest_confrontation"] == 4
    assert report["chapter_rhythm"]["stages"][0]["relationship_chapters"] == [5]
    assert all(p.read_bytes() == value for p, value in before.items())


def test_invalid_explicit_noun_receipt_stays_unknown(tmp_path):
    from bookengine.plot_diagnostics import plot_findings

    bible = {"volumes": [{"number": 1, "start": 1, "end": 1}]}
    chapter = dict(
        number=1,
        text="他用铜秤开了门。",
        meta=dict(
            solutions=[
                dict(
                    content="完成开门",
                    target_noun="阵眼",
                    method_noun="阵钉",
                    quote="伪造的原文",
                    noun_status="Evidence",
                )
            ]
        ),
    )
    result = plot_findings(bible, [chapter])["solution_substance"]
    assert result["distribution"] == {}
    assert result["unknown_chapters"] == [1]


def test_checkup_reads_only_source_bound_supplement_without_rewriting_checkpoint(tmp_path):
    source = {
        "title": "借影换命",
        "power": {"name": "借影", "rule": "借影复制动作"},
        "canon": {"solution_menu": ["借力"]},
        "volumes": [{"number": 1, "start": 1, "end": 1}],
    }
    (tmp_path / "story_bible.yaml").write_text(yaml.safe_dump(source, allow_unicode=True))
    text = "他借影复制守卫的开锁动作，使铁门终于弹开。"
    (tmp_path / "chapter-001.body.md").write_text(text)
    (tmp_path / "chapters").mkdir()
    checkpoint = tmp_path / "chapters/001.json"
    checkpoint.write_text(json.dumps(dict(number=1, solutions=[])))
    before = checkpoint.read_bytes()
    folder = tmp_path / "plot-observations"
    folder.mkdir()
    raw = dict(
        solutions=[
            dict(
                type="借力",
                content="复制动作使门打开",
                primary=True,
                target_noun="铁门",
                method_noun="借影",
                quote=text,
            )
        ],
        core_use={},
    )
    sidecar = folder / "draft-001-v1.json"
    sidecar.write_text(
        json.dumps(dict(number=1, source="draft", source_path="chapter-001.body.md", source_text=text, raw=raw))
    )
    report = check_book(tmp_path, source="draft")
    assert report["solution_substance"]["distribution"] == {}
    assert report["core_ability"]["applications"]["distribution"] == {"借影": 1}
    assert report["plot_observations"]["applied_chapters"] == [1]
    assert checkpoint.read_bytes() == before
    (tmp_path / "chapter-001.body.md").write_text(text + "他离开了。")
    assert check_book(tmp_path, source="draft")["solution_substance"]["unknown_chapters"] == [1]
