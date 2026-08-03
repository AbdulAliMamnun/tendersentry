"""Local sentence embeddings for tender titles.

**Model: ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2``** — 384
dimensions, ~470 MB, multilingual. The choice is driven by the corpus rather than by
benchmarks: SEAO titles are French ("Réfection de la rue Principale", "Travaux de
remplacement d'égouts pluviaux"), while CanadaBuys is English, and the same vector
space has to serve both. An English-only model would score the French half of the
corpus as noise.

Everything runs locally. The model is downloaded once to the sentence-transformers
cache and re-used; training never contacts a hosted API, so a run costs nothing and
works offline.

Embedding the same corpus repeatedly is the slow part of a training loop, so vectors
are cached to disk keyed by model name and text hash.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

import config


LOGGER = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384
CACHE_DIR = Path(config.PROJECT_ROOT) / "data" / "model_cache"

_MODEL: Any = None


def load_model(name: str = MODEL_NAME) -> Any:
    """Load the sentence-transformer once per process."""
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer

        LOGGER.info("Loading local embedding model %s", name)
        _MODEL = SentenceTransformer(name)
    return _MODEL


def _cache_path(texts: Sequence[str], name: str) -> Path:
    # Joined on a unit separator so that ["ab", "c"] and ["a", "bc"] cannot collide
    # onto the same cache file.
    joined = "\x1f".join(texts)
    digest = hashlib.sha256(f"{joined}|{name}".encode("utf-8")).hexdigest()[:32]
    return CACHE_DIR / f"emb-{digest}.npy"


def embed(
    texts: Sequence[str], name: str = MODEL_NAME, use_cache: bool = True
) -> np.ndarray:
    """Embed a corpus, caching the result on disk.

    Returns a ``(len(texts), EMBEDDING_DIM)`` array. Empty strings embed to zeros
    rather than to the model's opinion of emptiness, so a missing title contributes
    nothing to similarity instead of contributing a constant.
    """
    cleaned = [str(text or "").strip() for text in texts]
    if not cleaned:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cleaned, name)
    if use_cache and path.is_file():
        LOGGER.info("Reusing cached embeddings %s", path.name)
        return np.load(path)

    model = load_model(name)
    populated = [index for index, text in enumerate(cleaned) if text]
    vectors = np.zeros((len(cleaned), EMBEDDING_DIM), dtype=np.float32)
    if populated:
        LOGGER.info("Embedding %d text(s) with %s", len(populated), name)
        encoded = model.encode(
            [cleaned[index] for index in populated],
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        vectors[populated] = np.asarray(encoded, dtype=np.float32)

    if use_cache:
        np.save(path, vectors)
    return vectors


def centroid(vectors: Iterable[np.ndarray]) -> np.ndarray:
    """Mean direction of a firm's historical tenders.

    Used as the firm's semantic fingerprint: the centroid of what it has bid on
    before, compared against a new tender's embedding.
    """
    stacked = [vector for vector in vectors if vector is not None]
    if not stacked:
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
    mean = np.mean(np.vstack(stacked), axis=0)
    norm = float(np.linalg.norm(mean))
    return (mean / norm).astype(np.float32) if norm else mean.astype(np.float32)
