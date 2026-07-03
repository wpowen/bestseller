"""Real semantic embeddings as a drop-in alternative to the hashed n-gram
vectors in :mod:`bestseller.services.retrieval`.

``retrieval.build_hashed_embedding`` scores text similarity via hashed
character n-gram overlap. That is fast and dependency-free, but it is a
lexical-overlap proxy, not a meaning proxy: two sentences that restate the
same established fact in different words can score *lower* than two
unrelated sentences that happen to share n-grams. This module offers a
same-shaped ``list[float]`` embedding (safe to feed into the existing
``cosine_similarity``) backed by a real sentence-embedding model, for
call sites that need true paraphrase-level consistency matching (e.g.
"did we already establish this fact about the character, just worded
differently?").

The ``sentence-transformers`` extra is optional (see ``pyproject.toml``
``bestseller[embeddings]``); when it or the model weights are unavailable
(e.g. offline CI), :func:`build_semantic_embedding` returns ``None`` so
callers can fall back to the hashed embedding.
"""

from __future__ import annotations

from functools import lru_cache

DEFAULT_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache(maxsize=1)
def _load_model(model_name: str = DEFAULT_MODEL_NAME) -> object | None:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    try:
        return SentenceTransformer(model_name)
    except Exception:
        return None


def build_semantic_embedding(text: str, *, model_name: str = DEFAULT_MODEL_NAME) -> list[float] | None:
    """Return a normalized semantic embedding for ``text``, or ``None`` if
    the embedding backend is unavailable. The returned vector is unit-norm,
    so it is compatible with ``retrieval.cosine_similarity``.
    """
    if not text or not text.strip():
        return None
    model = _load_model(model_name)
    if model is None:
        return None
    vector = model.encode(text, normalize_embeddings=True)
    return [float(value) for value in vector]
