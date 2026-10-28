# syntax=docker/dockerfile:1.7
# Multi-stage, BuildKit-cached. Build with: DOCKER_BUILDKIT=1 docker build ...
# For production, pin bases by digest (python:3.12-slim@sha256:...).

############################
# Stage 1: fetch kubectl   #
# (runs in parallel with the deps stage under BuildKit)
############################
FROM debian:12-slim AS kubectl
ARG KUBECTL_VERSION=v1.34.12   # match the cluster minor (Kind k8s 1.34); keep within +/-1
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl; \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" -o /tmp/kubectl.sha256; \
    echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/kubectl

############################
# Stage 2: python deps     #
# (builds into an isolated venv; runs in parallel with stage 1)
############################
FROM python:3.12-slim AS deps
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
# Cache pip downloads between builds for speed.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

############################
# Stage 3: runtime (slim)  #
############################
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Copy only the finished artifacts (no build tools, no curl in the final image).
COPY --from=kubectl /usr/local/bin/kubectl /usr/local/bin/kubectl
COPY --from=deps    /opt/venv /opt/venv

WORKDIR /app
COPY config.yaml .
COPY app ./app
# Keep the strict brain system prompt next to config.yaml at /app/OLLAMA_BRAIN.md
# (the path config.yaml points to). It is also bundled inside ./app for reference.
COPY app/OLLAMA_BRAIN.md ./OLLAMA_BRAIN.md

# Non-root user.
RUN useradd -u 1000 -m agent
USER 1000:1000

EXPOSE 8080

# Lightweight healthcheck using stdlib (no curl needed in the image).
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
