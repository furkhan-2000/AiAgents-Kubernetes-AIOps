# syntax=docker/dockerfile:1.7
# Multi-stage, BuildKit-cached, checksum-pinned. Build via: docker compose build
#
# Rollout speed, by design:
#   - kubectl is fetched with `ADD --checksum` (no apt, no extra base image).
#   - python deps are an isolated venv layer, cached unless requirements.txt moves.
#   - only ./app changes on a code-only edit, so a routine rebuild re-runs just
#     the final COPY layer and the image export. Cold-build cost is paid once.

ARG PY_IMAGE=python:3.12-slim
# For production pin by digest, e.g. python:3.12-slim@sha256:<digest>, so the base
# can never drift. Resolve the current digest with `docker buildx imagetools`.

# kubectl: keep VERSION and SHA256 in sync (both come from dl.k8s.io). Match the
# cluster minor (Kind k8s 1.34) and stay within +/-1.
ARG KUBECTL_VERSION=v1.34.12
ARG KUBECTL_SHA256=90b7b9058ffeb5c10710bb1c73f541eaf426fb1b4e87df435db669de9a2564a2

############################
# Stage 1: python deps     #
# (isolated venv; cached unless requirements.txt changes)
############################
FROM ${PY_IMAGE} AS deps
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
# Cache pip downloads across builds; no cache dir ends up in the image.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-input -r requirements.txt

############################
# Stage 2: runtime (slim)  #
############################
FROM ${PY_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# kubectl, verified by sha256 at build time. No curl, no apt, no debian stage.
# The checksum is a literal (some BuildKit versions do not expand ARG inside
# --checksum); keep it equal to ARG KUBECTL_SHA256 above when bumping the version.
ARG KUBECTL_VERSION
ADD --chmod=0755 \
    --checksum=sha256:90b7b9058ffeb5c10710bb1c73f541eaf426fb1b4e87df435db669de9a2564a2 \
    https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl \
    /usr/local/bin/kubectl

# Bring in the finished venv only (no build tools in the final image).
COPY --from=deps /opt/venv /opt/venv

WORKDIR /app
# Order matters for layer caching: rarely-changed files first, code last.
COPY config.yaml .
COPY app/OLLAMA_BRAIN.md ./OLLAMA_BRAIN.md
COPY app ./app

# Non-root user.
RUN useradd -u 1000 -m agent
USER 1000:1000

EXPOSE 8080

# Lightweight healthcheck using stdlib (no curl needed in the image).
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

# Provenance labels last (VERSION/VCS_REF change every build; keeping them here
# means they never invalidate the code/deps layers above).
ARG VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="k8s-aiops-agent" \
      org.opencontainers.image.description="Read-only Kubernetes AIOps agent" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
