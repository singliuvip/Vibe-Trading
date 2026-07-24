# ============================================================================
# Stage 1: Build frontend
# ============================================================================
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS frontend-build
# node:20-slim digest resolved 2026-07-13

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
# China mirror for npm to avoid slow/timeout from registry.npmjs.org
RUN npm config set registry https://registry.npmmirror.com
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

# ============================================================================
# Stage 2: Python builder — compiles wheels + builds a self-contained venv.
# build-essential lives ONLY here; it never reaches the runtime image.
# ============================================================================
FROM python:3.11-slim@sha256:e031123e3d85762b141ad1cbc56452ba69c6e722ebf2f042cc0dc86c47c0d8b3 AS builder
# python:3.11-slim digest resolved 2026-07-13

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv we can copy wholesale into the runtime stage.
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Chinese mirror for pip to avoid timeout when accessing files.pythonhosted.org
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

# Python deps first for layer caching. Uses agent/requirements.txt (loose
# constraints) to avoid lock-file conflicts with ccxt pinned sub-dependencies.
# The lock file is regenerated with pip freeze after successful builds.
COPY agent/requirements.txt agent/requirements.txt
RUN pip install --no-cache-dir -r agent/requirements.txt

# Copy project + install the CLI entrypoint (editable — the runtime stage
# re-creates the same /app/agent source tree the .pth file points at).
COPY pyproject.toml LICENSE README.md ./
COPY agent/ agent/
RUN pip install --no-cache-dir -e ".[feishu]"

# ============================================================================
# Stage 3: Runtime — carries the prebuilt venv only, no compilers/dev headers.
# ============================================================================
FROM python:3.11-slim@sha256:e031123e3d85762b141ad1cbc56452ba69c6e722ebf2f042cc0dc86c47c0d8b3 AS runtime
# python:3.11-slim digest resolved 2026-07-13

LABEL org.opencontainers.image.title="Vibe-Trading" \
    org.opencontainers.image.description="Natural-language finance research AI agent with backtesting" \
    org.opencontainers.image.version="0.1.11" \
    org.opencontainers.image.source="https://github.com/HKUDS/Vibe-Trading" \
    org.opencontainers.image.licenses="MIT"

# Mirror overrides for regions with slow access to defaults.
#   DEBIAN_MIRROR: e.g. mirrors.tuna.tsinghua.edu.cn (omit protocol/slash)
#   PIP_INDEX_URL: e.g. https://pypi.tuna.tsinghua.edu.cn/simple
ARG DEBIAN_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime-only native libs. NO build-essential here — these are weasyprint's
# shared libraries (Pango/HarfBuzz/Fontconfig/Cairo/gdk-pixbuf) per its official
# Debian install list; without them the lazy `from weasyprint import HTML` in
# reporter.py fails and PDF rendering silently downgrades to HTML-only.
# fonts-dejavu-core gives non-blank PDFs.
RUN sed -i "s|deb.debian.org|$DEBIAN_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i "s|deb.debian.org|$DEBIAN_MIRROR|g" /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libfontconfig1 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Bring in the prebuilt venv from the builder stage.
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
COPY --from=builder /opt/venv /opt/venv

# Re-materialize the source tree the editable install references, plus the
# built frontend static assets.
COPY pyproject.toml LICENSE README.md ./
COPY agent/ agent/
COPY --from=frontend-build /app/frontend/dist frontend/dist

# Runtime should not run as root. `vibe` owns the writable app-data dirs so
# named volumes inherit usable permissions. `vibe-sandbox` is an unprivileged
# system account (no home, no shell) that runner.py drops into via
# subprocess.run(user="vibe-sandbox") to execute LLM-generated code with the
# least privilege — created here by fixed contract, not otherwise used.
RUN useradd --create-home --shell /usr/sbin/nologin vibe \
    && useradd --system --no-create-home --shell /usr/sbin/nologin --uid 10001 vibe-sandbox \
    && mkdir -p agent/runs agent/sessions agent/uploads agent/.swarm/runs /home/vibe/.vibe-trading \
    && chown -R vibe:vibe /app /home/vibe/.vibe-trading
USER vibe

# Default port
EXPOSE 8899

# Health check — hits /live (liveness probe; /health remains a legacy alias).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8899/live')" || exit 1

# Run API server (serves frontend/dist as static files)
CMD ["vibe-trading", "serve", "--host", "0.0.0.0", "--port", "8899"]
