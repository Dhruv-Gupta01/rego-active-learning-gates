"""Shared test helpers."""
import json
import os
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


def make_hidden_db(seed=90210, n_random=44):
    import hidden_gen

    path = f"/tmp/hidden_{seed}.db"
    if os.path.exists(path):
        os.remove(path)
    hidden_gen.build(seed, path, n_random)
    return path


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
