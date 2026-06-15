"""Shared test helpers."""
import json
import os
import shutil
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REAL_DB = "/app/data/queue.db"
THRESHOLDS = "/app/policy/thresholds.yaml"
WORKER = "/app/bin/worker.sh"


def thresholds():
    with open(THRESHOLDS) as fh:
        return yaml.safe_load(fh)


def make_hidden_db():
    """Return a writable copy of the shipped hidden anti-cheat database.

    The hidden DB (a different seed + frame mix than the image's queue.db,
    verifier-only and never mounted into the agent's container) is pre-built and
    committed as tests/hidden.db. We copy it to /tmp so the worker's write-back
    does not mutate the committed fixture.
    """
    src = os.path.join(HERE, "hidden.db")
    dst = "/tmp/hidden.db"
    shutil.copy(src, dst)
    return dst


def run_worker(db, outdir):
    os.makedirs(outdir, exist_ok=True)
    proc = subprocess.run(
        ["bash", WORKER, db, outdir], text=True, capture_output=True
    )
    assert proc.returncode == 0, f"worker failed: {proc.stdout}\n{proc.stderr}"
    return proc


def load_json(path):
    with open(path) as fh:
        return json.load(fh)
