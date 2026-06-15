#!/usr/bin/env python3
"""Oracle worker engine for the Rego Active-Learning Gates task.

Subcommands:
    evidence  --db DB --outdir DIR      -> DIR/evidence.json   (milestone 1)
    decisions --db DB --outdir DIR       -> DIR/decisions.json  (milestone 2)
    snapshot  --db DB --outdir DIR        -> DIR/snapshot.txt + `decisions`
                                            table in DB           (milestone 3)

It uses the `duckdb` CLI for SQL evidence + the perceptual-hash edge join, and
the `opa` CLI to evaluate the Rego policy. The policy at /app/policy/decision.rego
computes the cross-frame logic (transitive dup closure, holdout taint,
calibrated-bin gating, precedence).
"""
import argparse
import json
import os
import subprocess
import sys

import yaml

POLICY = "/app/policy/decision.rego"
THRESHOLDS = "/app/policy/thresholds.yaml"


def load_thresholds():
    with open(THRESHOLDS) as fh:
        return yaml.safe_load(fh)


def duckdb_json(db, sql):
    proc = subprocess.run(
        ["duckdb", "-json", db, "-c", sql],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"duckdb query failed: {proc.returncode}")
    out = proc.stdout.strip()
    return json.loads(out) if out else []


def duckdb_exec(db, sql):
    proc = subprocess.run(
        ["duckdb", db, "-c", sql], text=True, capture_output=True
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"duckdb exec failed: {proc.returncode}")


def fetch_frames(db):
    rows = duckdb_json(
        db,
        "SELECT frame_id, patient_id, raw_confidence, calibration_bin, "
        "quality_flag, holdout, CAST(dup_hash AS VARCHAR) AS dup_hash "
        "FROM frames ORDER BY frame_id;",
    )
    for r in rows:
        r["holdout"] = bool(r["holdout"])
    return rows


def fetch_edges(db, hamming_max):
    rows = duckdb_json(
        db,
        "SELECT a.frame_id AS u, b.frame_id AS v FROM frames a JOIN frames b "
        "ON a.frame_id < b.frame_id "
        f"WHERE bit_count(xor(a.dup_hash, b.dup_hash)) <= {int(hamming_max)} "
        "ORDER BY u, v;",
    )
    return [[r["u"], r["v"]] for r in rows]


def build_evidence(db, th):
    frames = fetch_frames(db)
    bins = th["calibration_bins"]
    holdout_patients = {f["patient_id"] for f in frames if f["holdout"]}
    out = []
    for f in frames:
        out.append(
            {
                "frame_id": f["frame_id"],
                "patient_id": f["patient_id"],
                "raw_confidence": f["raw_confidence"],
                "calibration_bin": f["calibration_bin"],
                "calibrated_score": bins[str(f["calibration_bin"])],
                "quality_flag": f["quality_flag"],
                "holdout": f["holdout"],
                "dup_hash": f["dup_hash"],
                "patient_holdout": f["patient_id"] in holdout_patients,
            }
        )
    out.sort(key=lambda r: r["frame_id"])
    return out


def build_decisions(db, th):
    frames = fetch_frames(db)
    edges = fetch_edges(db, th["dup_hamming_max"])
    opa_input = {
        "thresholds": th,
        "frames": [
            {
                "frame_id": f["frame_id"],
                "patient_id": f["patient_id"],
                "calibration_bin": f["calibration_bin"],
                "quality_flag": f["quality_flag"],
                "holdout": f["holdout"],
            }
            for f in frames
        ],
        "edges": edges,
    }
    proc = subprocess.run(
        ["opa", "eval", "-I", "-f", "json", "-d", POLICY, "data.gates.decision"],
        input=json.dumps(opa_input),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"opa eval failed: {proc.returncode}")
    res = json.loads(proc.stdout)
    decisions = res["result"][0]["expressions"][0]["value"]
    decisions = [
        {
            "frame_id": d["frame_id"],
            "decision": d["decision"],
            "reason_code": d["reason_code"],
        }
        for d in decisions
    ]
    decisions.sort(key=lambda r: r["frame_id"])
    return decisions


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")


def cmd_evidence(args):
    th = load_thresholds()
    write_json(os.path.join(args.outdir, "evidence.json"), build_evidence(args.db, th))


def cmd_decisions(args):
    th = load_thresholds()
    write_json(os.path.join(args.outdir, "evidence.json"), build_evidence(args.db, th))
    write_json(
        os.path.join(args.outdir, "decisions.json"), build_decisions(args.db, th)
    )


def cmd_snapshot(args):
    th = load_thresholds()
    decisions = build_decisions(args.db, th)
    write_json(os.path.join(args.outdir, "decisions.json"), decisions)
    write_json(os.path.join(args.outdir, "evidence.json"), build_evidence(args.db, th))

    # Deterministic terminal snapshot: sorted "frame_id decision reason_code".
    lines = [
        f"{d['frame_id']} {d['decision']} {d['reason_code']}" for d in decisions
    ]
    counts = {"promote": 0, "review": 0, "quarantine": 0}
    for d in decisions:
        counts[d["decision"]] += 1
    body = "\n".join(lines)
    summary = (
        f"TOTAL {len(decisions)} "
        f"PROMOTE {counts['promote']} "
        f"REVIEW {counts['review']} "
        f"QUARANTINE {counts['quarantine']}"
    )
    snap = body + "\n" + summary + "\n"
    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "snapshot.txt"), "w") as fh:
        fh.write(snap)
    sys.stdout.write(snap)

    # Write decisions back into the database.
    duckdb_exec(args.db, "DROP TABLE IF EXISTS decisions;")
    duckdb_exec(
        args.db,
        "CREATE TABLE decisions (frame_id TEXT PRIMARY KEY, decision TEXT, "
        "reason_code TEXT);",
    )
    values = ", ".join(
        "('{}', '{}', '{}')".format(d["frame_id"], d["decision"], d["reason_code"])
        for d in decisions
    )
    duckdb_exec(args.db, f"INSERT INTO decisions VALUES {values};")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("evidence", "decisions", "snapshot"):
        p = sub.add_parser(name)
        p.add_argument("--db", default="/app/data/queue.db")
        p.add_argument("--outdir", default="/app/out")
    args = ap.parse_args()
    {"evidence": cmd_evidence, "decisions": cmd_decisions, "snapshot": cmd_snapshot}[
        args.cmd
    ](args)


if __name__ == "__main__":
    main()
