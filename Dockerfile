# Multi-stage image: C sniper agent + Python app/bot.
#
# Two build modes (controlled by --build-arg USE_IMPERSONATE):
#
#   LOCAL (Mac arm64, for testing without Cloudflare):
#     docker build --build-arg USE_IMPERSONATE=0 -t p2c_app:local .
#
#   PROD (VPS x86_64, real Cloudflare on send.tg):
#     docker build --platform linux/amd64 --build-arg USE_IMPERSONATE=1 -t p2c_app:prod .

ARG USE_IMPERSONATE=0

# ── stage 1: build the C agent ─────────────────────────────────────────────
FROM debian:bookworm-slim AS agentbuild
ARG USE_IMPERSONATE
ARG TARGETARCH=amd64
ARG CURL_IMPERSONATE_VERSION=1.5.6

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates pkg-config curl \
        libwebsockets-dev libcurl4-openssl-dev libssl-dev libhiredis-dev \
    && rm -rf /var/lib/apt/lists/*

# Collect agent runtime libs into /agent-libs/ so stage 2 can COPY them
# with a single glob regardless of whether impersonate is used.
RUN mkdir -p /agent-libs && touch /agent-libs/.keep

# Download curl-impersonate only when USE_IMPERSONATE=1.
RUN set -eux; \
    if [ "$USE_IMPERSONATE" = "1" ]; then \
        case "$TARGETARCH" in \
          amd64) CI_ARCH=x86_64 ;; \
          arm64) CI_ARCH=aarch64 ;; \
          *) echo "unsupported TARGETARCH=$TARGETARCH"; exit 1 ;; \
        esac; \
        url="https://github.com/lexiforest/curl-impersonate/releases/download/v${CURL_IMPERSONATE_VERSION}/libcurl-impersonate-v${CURL_IMPERSONATE_VERSION}.${CI_ARCH}-linux-gnu.tar.gz"; \
        curl -fsSL "$url" -o /tmp/ci.tgz; \
        mkdir -p /usr/local/lib; \
        tar -xzf /tmp/ci.tgz -C /usr/local/lib; \
        rm /tmp/ci.tgz; \
        ldconfig; \
        cp /usr/local/lib/libcurl-impersonate.so* /agent-libs/; \
    else \
        echo "Skipping curl-impersonate (USE_IMPERSONATE=0, using stock libcurl)"; \
    fi

WORKDIR /build
COPY c_agent/CMakeLists.txt ./
COPY c_agent/include ./include
COPY c_agent/src ./src
RUN IMPER_FLAG=""; \
    [ "$USE_IMPERSONATE" = "1" ] && IMPER_FLAG="-DP2C_USE_IMPERSONATE=ON"; \
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release $IMPER_FLAG \
    && cmake --build build -j"$(nproc)"

# ── stage 2: python app/bot + agent binary ─────────────────────────────────
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libx11-6 \
    libxcb1 \
    libwebsockets17 \
    libhiredis0.14 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY docs ./docs
COPY app ./app
COPY tests ./tests

RUN python -m pip install --upgrade pip && pip install -e .

# Agent binary.
COPY --from=agentbuild /build/build/p2c_agent /usr/local/bin/p2c_agent

# curl-impersonate runtime libs (directory always exists; may contain only .keep).
COPY --from=agentbuild /agent-libs/ /usr/local/lib/
RUN ldconfig

RUN useradd -ms /bin/bash appuser
USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
