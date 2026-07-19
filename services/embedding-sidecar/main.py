"""AgentShield embedding sidecar.

A tiny FastAPI service that turns text into 384-dimensional vectors using the
``bge-small-en-v1.5`` model via fastembed (ONNX). It exists so that registry-api's
ingest pipeline AND the internal ``knowledge/search`` endpoint share exactly ONE
embedding model — query vectors and document vectors always come from the same
weights, so cosine similarity is meaningful.

The model is a module-level singleton, loaded exactly once at startup (never per
request). The ONNX weights are baked into the container image at build time (see
Dockerfile's dummy-embed step), so there is no runtime model download or network
egress. The model loads in a background thread during startup; ``GET /ready``
returns 503 until it finishes and 200 afterwards, so the Kubernetes readiness
probe holds traffic until the service can actually embed.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("embedding_sidecar")

# The three EMBEDDING_DIM=384 sites (this sidecar, migration 0067 vector(384),
# PgVectorStore) must never drift. bge-small-en-v1.5 emits 384-dim vectors.
MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_LABEL = "bge-small-en-v1.5"
EMBEDDING_DIM = 384

# Module-level singleton. Assigned exactly once by _load_model() at startup.
_model = None
_ready = False


def _load_model():
    """Load the fastembed model once into the module-level singleton.

    Idempotent: a second call returns the already-loaded model. Blocking (ONNX
    session init), so callers run it in a worker thread.
    """
    global _model, _ready
    if _model is not None:
        return _model
    from fastembed import TextEmbedding

    logger.info("Loading embedding model %s ...", MODEL_NAME)
    model = TextEmbedding(MODEL_NAME)
    _model = model
    _ready = True
    logger.info("Embedding model loaded (dim=%d)", EMBEDDING_DIM)
    return _model


async def _load_model_bg() -> None:
    """Startup background loader — never crashes the process on failure.

    A load failure leaves ``_ready`` False, so ``/ready`` stays 503 and the pod
    never joins the Service (fail-closed rather than serving zero vectors).
    """
    try:
        await asyncio.to_thread(_load_model)
    except Exception:  # noqa: BLE001 — log and stay not-ready
        logger.exception("Failed to load embedding model %s", MODEL_NAME)


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dim: int
    model: str


app = FastAPI(title="AgentShield Embedding Sidecar")


@app.on_event("startup")
async def _startup() -> None:
    # Load in the background so the ASGI server starts accepting connections
    # immediately; /ready reports 503 until the weights finish loading.
    app.state.load_task = asyncio.create_task(_load_model_bg())


@app.get("/ready")
async def ready():
    """Readiness probe: 200 once the model is loaded, 503 before."""
    if not _ready or _model is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    return {"status": "ready", "model": MODEL_LABEL, "dim": EMBEDDING_DIM}


@app.get("/health")
async def health():
    """Liveness: the process is up (independent of model-load state)."""
    return {"status": "ok"}


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest) -> EmbedResponse:
    """Embed a batch of texts → one 384-float vector each, order-preserving."""
    if _model is None or not _ready:
        raise HTTPException(status_code=503, detail="model not loaded")

    texts = req.texts
    if not isinstance(texts, list):
        raise HTTPException(status_code=422, detail="texts must be a list of strings")
    if not texts:
        return EmbedResponse(embeddings=[], dim=EMBEDDING_DIM, model=MODEL_LABEL)

    def _run() -> list[list[float]]:
        # fastembed.embed yields one numpy vector per input, in order.
        return [vector.tolist() for vector in _model.embed(texts)]

    embeddings = await asyncio.to_thread(_run)

    for vec in embeddings:
        if len(vec) != EMBEDDING_DIM:
            # Fail loud — a wrong-dim vector would poison the vector index.
            raise HTTPException(
                status_code=500,
                detail=f"embedding dim {len(vec)} != expected {EMBEDDING_DIM}",
            )

    return EmbedResponse(embeddings=embeddings, dim=EMBEDDING_DIM, model=MODEL_LABEL)
