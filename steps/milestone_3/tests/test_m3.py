"""Milestone 3 - Run Snapshot Worker."""
import os

import helpers
import reference


def _snapshot(outdir="/app/out"):
    with open(os.path.join(outdir, "snapshot.txt")) as fh:
        return fh.read()


def test_artifacts_persist():
    for p in (
        "/app/out/snapshot.txt",
        "/app/out/decisions.json",
        "/app/out/evidence.json",
        "/app/bin/worker.sh",
        "/app/policy/decision.rego",
    ):
        assert os.path.exists(p), p


def test_snapshot_matches_decisions():
    th = helpers.thresholds()
    expected = reference.expected_decisions(helpers.REAL_DB, th)
    lines = _snapshot().strip().splitlines()
    body = lines[:-1]
    parsed = [tuple(ln.split(" ")) for ln in body]
    want = [(d["frame_id"], d["decision"], d["reason_code"]) for d in expected]
    assert parsed == want


def test_snapshot_summary():
    th = helpers.thresholds()
    expected = reference.expected_decisions(helpers.REAL_DB, th)
    counts = {"promote": 0, "review": 0, "quarantine": 0}
    for d in expected:
        counts[d["decision"]] += 1
    summary = _snapshot().strip().splitlines()[-1]
    assert summary == (
        f"TOTAL {len(expected)} PROMOTE {counts['promote']} "
        f"REVIEW {counts['review']} QUARANTINE {counts['quarantine']}"
    )


def test_decisions_table_written():
    th = helpers.thresholds()
    rows = reference.duckdb_json(
        helpers.REAL_DB,
        "SELECT frame_id, decision, reason_code FROM decisions ORDER BY frame_id;",
    )
    expected = reference.expected_decisions(helpers.REAL_DB, th)
    assert rows == [
        {"frame_id": d["frame_id"], "decision": d["decision"], "reason_code": d["reason_code"]}
        for d in expected
    ]


def test_stdout_echoes_snapshot():
    """worker.sh must echo the snapshot to stdout. Benign progress logging is
    tolerated as long as the full snapshot text appears as a contiguous block."""
    db = helpers.make_hidden_db()
    out = "/tmp/m3_stdout"
    proc = helpers.run_worker(db, out)
    snap = open(os.path.join(out, "snapshot.txt")).read().strip()
    assert snap in proc.stdout


def test_hidden_db_snapshot_and_table():
    th = helpers.thresholds()
    db = helpers.make_hidden_db()
    out = "/tmp/m3_hidden"
    helpers.run_worker(db, out)
    expected = reference.expected_decisions(db, th)
    rows = reference.duckdb_json(
        db, "SELECT frame_id, decision, reason_code FROM decisions ORDER BY frame_id;"
    )
    assert rows == [
        {"frame_id": d["frame_id"], "decision": d["decision"], "reason_code": d["reason_code"]}
        for d in expected
    ]
    body = open(os.path.join(out, "snapshot.txt")).read().strip().splitlines()[:-1]
    parsed = [tuple(ln.split(" ")) for ln in body]
    want = [(d["frame_id"], d["decision"], d["reason_code"]) for d in expected]
    assert parsed == want
