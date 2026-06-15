#!/usr/bin/env python3
"""Deterministic generator for the DuckDB retinal-triage review queue.

Builds a `frames` table in a DuckDB database. The data is seeded so a given
--seed always yields the same database (the image build uses one seed; the
verifier generates a second, hidden database with a different seed for
anti-cheat). Regardless of seed, a fixed block of trap frames (prefix "T_") is
always injected so the cross-frame difficulty scenarios are guaranteed present.

The generator shells out to the `duckdb` CLI; it does not require the duckdb
Python package.

Schema (table `frames`):
    frame_id        TEXT     unique id
    patient_id      TEXT     patient grouping key
    raw_confidence  DOUBLE   uncalibrated model confidence (0..1) -- a decoy
    calibration_bin INTEGER  0..4, indexes calibration_bins in thresholds.yaml
    quality_flag    TEXT     'ok' | 'blur' | 'glare' | 'dark'
    holdout         BOOLEAN  frame is reserved in the patient holdout set
    dup_hash        BIGINT   64-bit perceptual hash (Hamming-comparable)
"""
import argparse
import random
import subprocess
import sys

def _block(lo):
    """8 set bits starting at bit `lo` (popcount 8). Disjoint blocks are
    pairwise Hamming distance 16 — well isolated for the dup closure."""
    v = 0
    for b in range(lo, lo + 8):
        v |= 1 << b
    return v


# Fixed trap frames — always present, values hand-chosen to exercise the §3
# cross-frame traps. Columns: frame_id, patient, raw, bin, quality, holdout, hash
# Isolated frames use disjoint high-bit blocks (>= bit 8); the dup chain uses
# small low-bit values, so no isolated frame links to another or to the chain.
TRAP_FRAMES = [
    # raw-vs-calibrated: high raw confidence, but calibration bin 0 -> fails.
    ("T_RVC", "P_RVC", 0.95, 0, "ok", False, _block(8)),
    # transitive dup chain A~B~C: ham(A,B)=3, ham(B,C)=4, ham(A,C)=7 (> max 5).
    # A is intrinsically quarantined (blur); taint must reach C transitively.
    ("T_DUPA", "P_DUPA", 0.90, 4, "blur", False, 0),
    ("T_DUPB", "P_DUPB", 0.90, 4, "ok", False, 7),
    ("T_DUPC", "P_DUPC", 0.90, 4, "ok", False, 247),
    # holdout sibling: P_HS group has a holdout frame; clean sibling downgraded.
    ("T_HSH", "P_HS", 0.90, 4, "ok", True, _block(16)),
    ("T_HSS", "P_HS", 0.90, 4, "ok", False, _block(24)),
    # precedence: holdout group + soft-flagged ('dark') sibling -> quarantine.
    ("T_PRH", "P_PR", 0.90, 4, "ok", True, _block(32)),
    ("T_PRS", "P_PR", 0.90, 4, "dark", False, _block(40)),
    # a clean promote so the happy path is exercised.
    ("T_OK", "P_OK", 0.90, 4, "ok", False, _block(48)),
]

QUALITY_CHOICES = ["ok", "ok", "ok", "blur", "glare", "dark"]


def popcount(n: int) -> int:
    return bin(n).count("1")


def random_hash(rng: random.Random) -> int:
    """A 60-bit hash with 24..40 bits set, so its Hamming distance to any
    low-popcount trap hash is well above dup_hamming_max."""
    while True:
        h = rng.getrandbits(60)
        if 24 <= popcount(h) <= 40:
            return h


def gen_random_frames(rng: random.Random, n: int):
    rows = []
    for i in range(n):
        fid = f"f{i:03d}"
        pid = f"PT{i % 12:02d}"
        raw = round(rng.uniform(0.05, 0.99), 3)
        cbin = rng.randint(0, 4)
        quality = rng.choice(QUALITY_CHOICES)
        holdout = rng.random() < 0.15
        rows.append((fid, pid, raw, cbin, quality, holdout, random_hash(rng)))
    return rows


def sql_literal(v):
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return repr(v)


def build(seed: int, out: str, n_random: int):
    rng = random.Random(seed)
    rows = list(TRAP_FRAMES) + gen_random_frames(rng, n_random)

    stmts = [
        "DROP TABLE IF EXISTS frames;",
        (
            "CREATE TABLE frames ("
            "frame_id TEXT PRIMARY KEY, patient_id TEXT, raw_confidence DOUBLE, "
            "calibration_bin INTEGER, quality_flag TEXT, holdout BOOLEAN, "
            "dup_hash BIGINT);"
        ),
    ]
    for r in rows:
        vals = ", ".join(sql_literal(v) for v in r)
        stmts.append(f"INSERT INTO frames VALUES ({vals});")

    script = "\n".join(stmts) + "\n"
    proc = subprocess.run(
        ["duckdb", out],
        input=script,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"duckdb failed: {proc.returncode}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1729)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-random", type=int, default=36)
    args = ap.parse_args()
    build(args.seed, args.out, args.n_random)


if __name__ == "__main__":
    main()
