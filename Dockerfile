# CodeRAG API + CLI image.
# Base install only (no torch); enable local embeddings with the `embeddings` extra
# by adjusting the pip line below if you want on-box SentenceTransformers.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# git is needed for repository ingestion (enumeration + diff).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE alembic.ini ./
COPY src ./src

RUN pip install --upgrade pip && pip install -e .

EXPOSE 8000
CMD ["uvicorn", "coderag.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
