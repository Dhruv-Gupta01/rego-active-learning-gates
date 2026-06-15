# Digest-pinned base (python:3.13-slim-bookworm, linux/amd64)
FROM python@sha256:05b95397cac02b060ff1251afaa78087d92d7034369afbc8eb765631cada8257

# Runtime-only OS deps. NO test dependencies (pytest is installed from vendored
# wheels at test time — see steps/milestone_N/tests/test.sh).
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
