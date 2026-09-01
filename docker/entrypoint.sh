#!/usr/bin/env sh
# One-shot startup: apply migrations, optionally index the bundled demo repo so
# the dashboard has data on first boot, then serve the API.
set -e

echo "[coderag] applying database migrations…"
alembic upgrade head

if [ "${CODERAG_INDEX_DEMO:-0}" = "1" ]; then
  echo "[coderag] indexing bundled demo repository (set CODERAG_INDEX_DEMO=0 to skip)…"
  coderag index ./examples/demo-repository --name payments || \
    echo "[coderag] demo index skipped"
fi

echo "[coderag] starting API on http://0.0.0.0:8000  (dashboard at /dashboard)"
exec uvicorn coderag.api.app:app --host 0.0.0.0 --port 8000
