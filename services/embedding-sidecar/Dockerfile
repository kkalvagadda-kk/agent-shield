# AgentShield embedding sidecar — bge-small-en-v1.5 (384-dim) via fastembed/ONNX.
#
# The ONNX model weights are baked into the image at build time (the dummy-embed
# RUN below) so the container needs NO network egress at runtime — it never
# downloads a model. Weights land in FASTEMBED_CACHE_PATH and are chowned to the
# non-root runtime user.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed_cache \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./

# Bake the model weights into the image with ONE dummy embed. This downloads the
# ONNX weights at BUILD time into FASTEMBED_CACHE_PATH so there is zero runtime
# model download / egress. If the model dim ever drifts from 384 the build fails.
RUN mkdir -p "$FASTEMBED_CACHE_PATH" && \
    python -c "from fastembed import TextEmbedding; m = TextEmbedding('BAAI/bge-small-en-v1.5'); v = list(m.embed(['warmup']))[0].tolist(); assert len(v) == 384, len(v); print('baked model, dim', len(v))"

# Non-root runtime user; owns the app dir and the baked model cache so it can
# read the weights (no write / re-download needed at runtime).
RUN useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app "$FASTEMBED_CACHE_PATH"
USER appuser

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
