#!/bin/sh
set -e

# Start MinIO server in the background then create default buckets,
# then bring MinIO to the foreground.

minio server /data --console-address :9001 &
MINIO_PID=$!

echo "==> MinIO server started (PID $MINIO_PID), waiting for it to become ready..."
READY=0
for i in $(seq 1 24); do
    sleep 5
    if mc alias set local "http://localhost:9000" \
        "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" --quiet 2>/dev/null; then
        READY=1
        break
    fi
    echo "    attempt $i/24 — not ready yet"
done

if [ "$READY" -eq 0 ]; then
    echo "ERROR: MinIO did not become ready in 120 s" >&2
    kill "$MINIO_PID" 2>/dev/null || true
    exit 1
fi

echo "==> Creating buckets..."
for bucket in agentshield postgres-backups langfuse-media eval-artifacts; do
    mc mb "local/${bucket}" --ignore-existing --quiet 2>/dev/null && \
        echo "    bucket '${bucket}' ready" || \
        echo "    bucket '${bucket}' already exists"
done

echo "==> MinIO fully initialised — staying in foreground."
wait "$MINIO_PID"
