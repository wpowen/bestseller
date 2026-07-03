from __future__ import annotations

import pytest

from bestseller.services.retrieval import build_hashed_embedding, cosine_similarity
from bestseller.services.semantic_embedding import build_semantic_embedding


pytestmark = pytest.mark.unit

ESTABLISHED_FACT = "他左眼失明，是十年前那场大火留下的伤"
PARAPHRASED_RESTATEMENT = "他的左眼看不见东西，因为很多年前被烧伤过"
UNRELATED_SENTENCE = "她今天穿了一件红色的连衣裙去赴宴"


def _require_semantic_backend() -> None:
    if build_semantic_embedding(ESTABLISHED_FACT) is None:
        pytest.skip("sentence-transformers model unavailable (offline/uninstalled)")


def test_hashed_embedding_fails_to_rank_paraphrase_above_unrelated_text() -> None:
    """Documents the gap this module fixes: n-gram overlap is not meaning."""
    established = build_hashed_embedding(ESTABLISHED_FACT, 256)
    paraphrase = build_hashed_embedding(PARAPHRASED_RESTATEMENT, 256)
    unrelated = build_hashed_embedding(UNRELATED_SENTENCE, 256)

    paraphrase_score = cosine_similarity(established, paraphrase)
    unrelated_score = cosine_similarity(established, unrelated)

    assert paraphrase_score <= unrelated_score


def test_semantic_embedding_ranks_paraphrase_above_unrelated_text() -> None:
    _require_semantic_backend()

    established = build_semantic_embedding(ESTABLISHED_FACT)
    paraphrase = build_semantic_embedding(PARAPHRASED_RESTATEMENT)
    unrelated = build_semantic_embedding(UNRELATED_SENTENCE)
    assert established is not None
    assert paraphrase is not None
    assert unrelated is not None

    paraphrase_score = cosine_similarity(established, paraphrase)
    unrelated_score = cosine_similarity(established, unrelated)

    assert paraphrase_score > 0.6
    assert paraphrase_score > unrelated_score


def test_semantic_embedding_returns_none_without_text() -> None:
    assert build_semantic_embedding("") is None
    assert build_semantic_embedding("   ") is None
