#!/bin/bash
set -uo pipefail

if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set." >&2
    exit 1
fi

mkdir -p /logs/verifier

# Restore the verifier-only pytest bundle (built into the image at /opt/...tgz)
# without any test-time package installation or network access.
mkdir -p /opt/verifier-pytest
tar -xzf /opt/verifier-pytest.tgz -C /opt/verifier-pytest
export PYTHONPATH="/opt/verifier-pytest:${PYTHONPATH:-}"

python -m pytest \
    -o cache_dir=/tmp/pytest_cache \
    --ctrf /logs/verifier/ctrf.json \
    /tests/test_m2.py -rA
RC=$?
if [ "$RC" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
