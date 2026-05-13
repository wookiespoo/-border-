# ── Border Node — multi-stage build ────────────────────────────────────────
FROM python:3.12-slim AS base

LABEL maintainer="Border Protocol"
LABEL description="Censorship-resistant mesh network node"

# System deps (needed for cryptography wheel)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt pyproject.toml setup.py ./
COPY border/__init__.py border/__init__.py
RUN pip install --no-cache-dir -e .

# Copy full source
COPY . .

# Data directory for wallet + chain DB
RUN mkdir -p /data
VOLUME ["/data"]

# Default ports:
#   5000  — REST API (node_runner Flask)
#   6000  — P2P gossip
EXPOSE 5000 6000

ENV BORDER_DATA_DIR=/data
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "border.node_runner"]
CMD ["--host", "0.0.0.0", "--port", "5000", "--p2p-port", "6000", \
     "--persist", "/data/chain.db"]
