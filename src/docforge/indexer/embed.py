"""Wrapper around fastembed so the rest of docforge doesn't have to know the model name."""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from fastembed import TextEmbedding


DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


@lru_cache(maxsize=1)
def _model(name: str = DEFAULT_MODEL) -> TextEmbedding:
    # First call downloads weights (~30 MB) to the OS cache dir.
    return TextEmbedding(model_name=name)


def embed_texts(texts: Iterable[str], model_name: str = DEFAULT_MODEL) -> list[list[float]]:
    model = _model(model_name)
    return [[float(x) for x in v] for v in model.embed(list(texts))]


def embed_query(text: str, model_name: str = DEFAULT_MODEL) -> list[float]:
    model = _model(model_name)
    return [float(x) for x in next(iter(model.embed([text])))]
