#!/bin/bash
set -uo pipefail

if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set." >&2
    exit 1
fi

mkdir -p /logs/verifier

pip install --no-index --find-links=/tests/wheels \
    pytest==8.4.1 pytest-json-ctrf==0.3.5

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
