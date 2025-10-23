# Build stage
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip wheel \
 && pip wheel --wheel-dir /wheels -r requirements.txt


# Final stage
FROM python:3.13-slim

ENV APP_MODULE="main:app" \
    HOST="0.0.0.0" \
    PORT="8080" \
    WORKERS="2" \
    LOG_LEVEL="info"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY . .

RUN useradd -m appuser
USER appuser

CMD exec python -m uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT}
