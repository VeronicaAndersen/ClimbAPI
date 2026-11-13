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

# Runs a single uvicorn worker on purpose: the rate limiter and the login
# throttle keep their counters in process memory, so several workers would
# each count separately. Move them to a shared store (Redis) before scaling.
ENV APP_MODULE="main:app" \
    HOST="0.0.0.0" \
    PORT="8080" \
    LOG_LEVEL="info"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Addresses of the reverse proxy whose X-Forwarded-For / X-Forwarded-Proto
# headers uvicorn trusts (comma separated IPs or CIDRs). The rate limits key
# on the client IP, so set this to your proxy's address. Never use "*" unless
# the container is only reachable through the proxy.
ENV FORWARDED_ALLOW_IPS="127.0.0.1,::1"

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY . .

RUN useradd -m appuser
USER appuser

CMD exec python -m uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT} \
    --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS}"
