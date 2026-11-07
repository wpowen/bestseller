"""Deterministic blind packages for INBOX 43 reader-proxy evaluation."""

from deai.evaluation import build_pairs, summarize


def _book(root, final_text):
    (root / "final").mkdir(parents=True)
    for number in range(1, 4):
        source = "\n\n".join(f"第{number}章原稿第{i}段。" * 20 for i in range(1, 7))
        (root / f"chapter-{number:03d}.body.md").write_text(source)
        (root / f"final/chapter-{number:03d}.md").write_text(final_text(source, number))


def test_blind_pairs_are_repeatable_and_hide_candidate_side(tmp_path):
    _book(tmp_path, lambda text, number: text.replace("原稿", "终稿"))

    first = build_pairs(tmp_path, tmp_path, count=2, seed=43, target_chars=80)
    second = build_pairs(tmp_path, tmp_path, count=2, seed=43, target_chars=80)

    assert first == second
    assert len(first) == 2
    assert {pair["chapter"] for pair in first} <= {1, 2, 3}
    assert all(pair["candidate_side"] in ("left", "right") for pair in first)
    assert all("candidate" not in pair["left"].lower() and "candidate" not in pair["right"].lower() for pair in first)
    assert all(pair["left"] != pair["right"] for pair in first)


def test_blind_summary_counts_candidate_wins_ties_and_losses():
    rows = [
        dict(candidate_side="left", result=dict(more_human="left", better_read="left")),
        dict(candidate_side="right", result=dict(more_human="left", better_read="tie")),
        dict(candidate_side="right", result=dict(more_human="right", better_read="right")),
    ]

    result = summarize(rows)

    assert result["more_human"] == dict(candidate_wins=2, ties=0, candidate_losses=1, unknown=0)
    assert result["better_read"] == dict(candidate_wins=2, ties=1, candidate_losses=0, unknown=0)


def test_pair_template_reuses_chapters_ranges_and_sides(tmp_path):
    _book(tmp_path, lambda text, number: text.replace("原稿", "终稿"))
    original = build_pairs(tmp_path, tmp_path, count=2, seed=43, target_chars=80)
    template = [{key: pair[key] for key in ("chapter", "paragraphs", "candidate_side")} for pair in original]

    repeated = build_pairs(tmp_path, tmp_path, count=3, seed=999, target_chars=20, template=template)

    assert [{key: pair[key] for key in template[0]} for pair in repeated] == template
