# Digest-pinned canonical base (python:3.13-slim-bookworm via public ECR mirror)
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

# Runtime-only OS deps. Test deps are NOT importable in this image — pytest is
# shipped as an inert tarball (built below) and extracted at test time, so the
# agent runtime stays free of test dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        unzip \
        tmux \
        asciinema \
    && rm -rf /var/lib/apt/lists/*

# Runtime Python dep the worker needs to read the YAML policy file.
RUN python -m pip install --no-cache-dir pyyaml==6.0.2

# --- Verifier-only pytest bundle ---------------------------------------------
# Install the test dependencies (offline, from pinned vendored wheels) into a
# throwaway target dir, pack them into an inert tarball, and delete the dir. The
# result: pytest is NOT importable in the image (so the runtime stays test-dep
# free), yet the verifier can restore it at test time with a plain `tar -xzf` +
# PYTHONPATH — no test-time pip install, no network.
COPY verifier-pytest-wheels /tmp/verifier-pytest-wheels
RUN python -m pip install --no-cache-dir --no-index \
        --find-links=/tmp/verifier-pytest-wheels \
        --target /opt/verifier-pytest \
        pytest==8.4.1 pytest-json-ctrf==0.3.5 \
    && tar -czf /opt/verifier-pytest.tgz -C /opt/verifier-pytest . \
    && rm -rf /opt/verifier-pytest /tmp/verifier-pytest-wheels \
    && ! python -c "import pytest" 2>/dev/null \
    && echo "confirmed: pytest not importable in image"

# --- OPA (Open Policy Agent) CLI, pinned by version + sha256 -------------------
ARG OPA_VERSION=1.17.1
ARG OPA_SHA256=3d4bb88482958d990351ec5d2f7558509992776bc473bc1b78d86d76cb993ca3
RUN curl -fsSL -o /usr/local/bin/opa \
        "https://github.com/open-policy-agent/opa/releases/download/v${OPA_VERSION}/opa_linux_amd64_static" \
    && echo "${OPA_SHA256}  /usr/local/bin/opa" | sha256sum -c - \
    && chmod +x /usr/local/bin/opa \
    && opa version

# --- DuckDB CLI, pinned by version + sha256 -----------------------------------
ARG DUCKDB_VERSION=1.5.3
ARG DUCKDB_SHA256=35caef1fecbc8d7e2c07de4fd2cdefc5189ec9ba9e1cca228fb1a1c48cc52a8a
RUN curl -fsSL -o /tmp/duckdb.zip \
        "https://github.com/duckdb/duckdb/releases/download/v${DUCKDB_VERSION}/duckdb_cli-linux-amd64.zip" \
    && echo "${DUCKDB_SHA256}  /tmp/duckdb.zip" | sha256sum -c - \
    && unzip -o /tmp/duckdb.zip -d /usr/local/bin \
    && rm /tmp/duckdb.zip \
    && chmod +x /usr/local/bin/duckdb \
    && duckdb --version

WORKDIR /app

# Build the DuckDB review-queue fixture at image build time, then remove the
# generator so it cannot leak into the agent's environment.
COPY data/thresholds.yaml /app/policy/thresholds.yaml
COPY data/build_queue.py /app/data/build_queue.py
RUN python /app/data/build_queue.py --seed 1729 --out /app/data/queue.db \
    && rm /app/data/build_queue.py
