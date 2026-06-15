#!/usr/bin/env python3
"""Verifier-only generator for a HIDDEN DuckDB retinal-triage review queue.

This module lives only under tests/ (never mounted into the agent's image). It is
used by the verifier to build a fresh database with a different seed for the
anti-cheat re-run. It exposes the same build() contract as the environment's
build_queue.py but is a separate verifier artifact; it reveals only how input
rows are generated, not how decisions are computed (that lives in reference.py).

Schema (table `frames`): frame_id TEXT, patient_id TEXT, raw_confidence DOUBLE,
calibration_bin INTEGER, quality_flag TEXT, holdout BOOLEAN, dup_hash BIGINT.
"""
import argparse
import random
import subprocess
import sys

def bits(*positions):
    """A hash with exactly the given bit positions set (low popcount)."""
    v = 0
    for b in positions:
        v |= 1 << b
    return v


# Fixed trap frames — always present, values hand-chosen to exercise the
# cross-frame traps. Columns: frame_id, patient, raw, bin, quality, holdout, hash
#
# Hash layout: each independent group occupies its own 5-bit "lane" (lanes start
# at bits 9, 16, 23, ... and are >= 7 apart). Frames within a duplicate cluster
# share a lane and differ by <= dup_hamming_max(=5); frames in different lanes
# differ by >= 6, so no cross-lane links form. The transitive dup chain lives in
# the low byte (bits 0-7) where A and C are 7 apart (only linked via B). Every
# trap hash has popcount <= 4, so random frames (popcount >= 24) never link to a
# trap. gen-time assertion below enforces the intended cluster structure.
TRAP_FRAMES = [
    # raw-vs-calibrated: high raw confidence, but calibration bin 0 -> fails.
    ("T_RVC", "P_RVC", 0.95, 0, "ok", False, bits(9, 10, 11)),
    # transitive dup chain A~B~C: ham(A,B)=3, ham(B,C)=4, ham(A,C)=7 (> max 5).
    # A is intrinsically quarantined (blur); taint must reach C transitively.
    # All popcounts >= 3 so the chain stays isolated from the other lanes.
    ("T_DUPA", "P_DUPA", 0.90, 4, "blur", False, bits(1, 2, 3, 5, 6, 7)),
    ("T_DUPB", "P_DUPB", 0.90, 4, "ok", False, bits(1, 2, 3)),
    ("T_DUPC", "P_DUPC", 0.90, 4, "ok", False, bits(0, 3, 4)),
    # holdout sibling: P_HS group has a holdout frame; clean sibling downgraded.
    ("T_HSH", "P_HS", 0.90, 4, "ok", True, bits(16, 17, 18)),
    ("T_HSS", "P_HS", 0.90, 4, "ok", False, bits(23, 24, 25)),
    # precedence: holdout group + soft-flagged ('dark') sibling -> quarantine.
    ("T_PRH", "P_PR", 0.90, 4, "ok", True, bits(30, 31, 32)),
    ("T_PRS", "P_PR", 0.90, 4, "dark", False, bits(37, 38, 39)),
    # a clean promote so the happy path is exercised.
    ("T_OK", "P_OK", 0.90, 4, "ok", False, bits(44, 45, 46)),
    # coupled holdout-through-duplicate (Rule E): T_HDB is a clean frame in a
    # holdout-free patient, but its duplicate T_HDA is holdout -> T_HDB downgraded.
    ("T_HDA", "P_HDA", 0.90, 4, "ok", True, bits(51, 52, 53)),
    ("T_HDB", "P_HDB", 0.90, 4, "ok", False, bits(51, 52, 53, 54)),
    # cluster representative (Rule E): untainted clique; only the highest-
    # calibrated member (T_REPA, bin 4) keeps promote, the rest -> review.
    ("T_REPA", "P_REPA", 0.90, 4, "ok", False, bits(58, 59, 60)),
    ("T_REPB", "P_REPB", 0.90, 3, "ok", False, bits(58, 59, 60, 61)),
    ("T_REPC", "P_REPC", 0.90, 2, "ok", False, bits(58, 59, 61)),
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
