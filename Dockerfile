# syntax=docker/dockerfile:1
# HERMES signal service — two-stage, zero-leak, minimal Python runtime.
# WO-HELM-HERMES-CONTAINER-ISOLATION-0002.
# Stage 1 builds wheels with a toolchain; Stage 2 is a slim runtime with NO build tools, NO source
# secrets, running as a non-root user. Nothing from the build stage (gcc, headers, caches) leaks
# into the final image. .dockerignore keeps .env / .git / backups / tests out of the build context.

# ---------------------------------------------------------------- Stage 1: builder ----------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Build toolchain ONLY in this stage (hiredis / pymysql C extensions). Removed by being a separate stage.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Pre-build all dependency wheels so the runtime stage installs offline (no index, no toolchain).
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---------------------------------------------------------------- Stage 2: runtime ----------------
FROM python:3.12-slim AS runtime

# Hardened, reproducible, quiet runtime env. UTC throughout (HERMES doctrine).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC \
    APP_HOME=/app

# OCI provenance inputs (WO-HELM-HERMES-PRE-STAGE-B-BUILD-CONTEXT-OCI-PROVENANCE-...). MANDATORY for every
# FUTURE Stage-B candidate image: the build MUST pass --build-arg SOURCE_SHA=<full 40-hex commit> and
# --build-arg BUILD_UTC=<tz-aware UTC ISO-8601>. Verified by tools/hermes_image_label_verify_v1.py.
# This block ONLY establishes label inputs + emission; it changes no entrypoint/base/packages/user.
ARG SOURCE_SHA
ARG BUILD_UTC
LABEL org.opencontainers.image.revision="${SOURCE_SHA}" \
      org.opencontainers.image.created="${BUILD_UTC}"

# Non-root, no-login service account (zero-leak: app never runs as root).
RUN groupadd --system --gid 10001 hermes \
 && useradd --system --uid 10001 --gid hermes --home-dir ${APP_HOME} --shell /usr/sbin/nologin hermes \
 && mkdir -p ${APP_HOME} /data/signal_history \
 && chown -R hermes:hermes ${APP_HOME} /data

WORKDIR ${APP_HOME}

# Install dependencies from prebuilt wheels only — no network index, no compiler in the final image.
COPY requirements.txt .
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
 && rm -rf /wheels

# Application source (build context is filtered by .dockerignore; .env is NEVER copied in).
COPY --chown=hermes:hermes . ${APP_HOME}

USER hermes

# Signal service HTTP port (overridable via SIGNAL_PORT).
EXPOSE 8210

# Liveness: the FastAPI signal port must be accepting connections.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,socket,sys; s=socket.socket(); s.settimeout(3); \
sys.exit(0 if s.connect_ex(('127.0.0.1', int(os.getenv('SIGNAL_PORT','8210'))))==0 else 1)"

# HARD resource-cap boot gate runs BEFORE the app: the entrypoint asserts cgroup caps and aborts
# (RC=101) on any GOV-STAGE-CAP violation, otherwise exec's the CMD. See docker/entrypoint.sh.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "main.py"]
