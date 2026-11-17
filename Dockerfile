# Production image for API, chat UI, and evals UI (same code, different CMD).
FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-prod.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements-prod.txt

COPY access_policy.json ./
COPY app/ ./app/
COPY ui/ ./ui/
COPY evals/ ./evals/
COPY .streamlit/ ./.streamlit/

EXPOSE 8000 8501 8502

# Default process is the API. Compose overrides for Streamlit services.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
