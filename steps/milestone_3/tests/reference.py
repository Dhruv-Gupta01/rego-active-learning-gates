#!/usr/bin/env python3
"""Verifier-only reference implementation of the active-learning gate policy.

Independent of the agent's Rego/SQL/Bash: reads the DuckDB queue via the CLI and
recomputes the expected evidence snapshot and per-frame decisions in pure Python
(including the transitive duplicate closure and holdout taint). Tests compare the
worker's output to this.
"""
import json
import subprocess
from collections import defaultdict

QUARANTINE, REVIEW, PROMOTE = 2, 1, 0
DEC_NAME = {PROMOTE: "promote", REVIEW: "review", QUARANTINE: "quarantine"}

PRIORITY = [
    "QUALITY_BLUR", "QUALITY_GLARE", "CALIBRATED_FAIL", "HOLDOUT_FLAGGED",
    "HOLDOUT_DUP_FLAGGED", "DUP_TAINT", "HOLDOUT", "HOLDOUT_SIBLING",
    "HOLDOUT_DUP", "DUP_REVIEW", "LOW_CALIBRATED", "CALIBRATED_OK",
]
PRIO = {r: i for i, r in enumerate(PRIORITY)}


def duckdb_json(db, sql):
    proc = subprocess.run(
        ["duckdb", "-json", db, "-c", sql], text=True, capture_output=True
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    out = proc.stdout.strip()
    return json.loads(out) if out else []


def load_frames(db):
    rows = duckdb_json(
        db,
        "SELECT frame_id, patient_id, raw_confidence, calibration_bin, "
        "quality_flag, holdout, CAST(dup_hash AS VARCHAR) AS dup_hash "
        "FROM frames ORDER BY frame_id;",
    )
    for r in rows:
        r["holdout"] = bool(r["holdout"])
        r["dup_hash_int"] = int(r["dup_hash"])
    return rows


def expected_evidence(db, th):
    frames = load_frames(db)
    bins = th["calibration_bins"]
    holdout_patients = {f["patient_id"] for f in frames if f["holdout"]}
    out = [
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
        for f in frames
    ]
    out.sort(key=lambda r: r["frame_id"])
    return out


def _components(frames, hamming_max):
    ids = [f["frame_id"] for f in frames]
    hashes = {f["frame_id"]: f["dup_hash_int"] for f in frames}
    adj = defaultdict(set)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if bin(hashes[a] ^ hashes[b]).count("1") <= hamming_max:
                adj[a].add(b)
                adj[b].add(a)
    label = {}
    for fid in ids:
        if fid in label:
            continue
        stack, comp = [fid], []
        seen = {fid}
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        root = min(comp)
        for x in comp:
            label[x] = root
    members = defaultdict(list)
    for fid, root in label.items():
        members[root].append(fid)
    return label, members


def expected_decisions(db, th):
    frames = load_frames(db)
    bins = th["calibration_bins"]
    qflags = set(th["quality_quarantine_flags"])
    pmin = th["promote_min_calibrated"]
    rmin = th["review_min_calibrated"]

    def base(f):
        cs = bins[str(f["calibration_bin"])]
        if cs < rmin:
            return (QUARANTINE, "CALIBRATED_FAIL")
        if cs >= pmin:
            return (PROMOTE, "CALIBRATED_OK")
        return (REVIEW, "LOW_CALIBRATED")

    intrinsic_q = set()
    for f in frames:
        if base(f)[0] == QUARANTINE or f["quality_flag"] in qflags:
            intrinsic_q.add(f["frame_id"])

    label, members = _components(frames, th["dup_hamming_max"])
    holdout_patients = {f["patient_id"] for f in frames if f["holdout"]}
    holdout_frames = {f["frame_id"] for f in frames if f["holdout"]}
    cal = {f["frame_id"]: bins[str(f["calibration_bin"])] for f in frames}

    # Per-component aggregates (computed after the transitive closure).
    comp_tainted = {}
    comp_holdout_exposed = {}
    comp_rep = {}
    for root, mem in members.items():
        comp_tainted[root] = any(m in intrinsic_q for m in mem)
        comp_holdout_exposed[root] = any(m in holdout_frames for m in mem)
        best = max(cal[m] for m in mem)
        comp_rep[root] = min(m for m in mem if cal[m] == best)

    results = []
    for f in frames:
        fid = f["frame_id"]
        root = label[fid]
        comp = members[root]
        votes = [base(f)]
        if f["quality_flag"] in qflags:
            votes.append((QUARANTINE, "QUALITY_" + f["quality_flag"].upper()))
        if len(comp) >= 2:
            # Rule C / E: tainted cluster -> all quarantine; otherwise every
            # non-representative member is downgraded to review.
            if comp_tainted[root]:
                votes.append((QUARANTINE, "DUP_TAINT"))
            elif fid != comp_rep[root]:
                votes.append((REVIEW, "DUP_REVIEW"))
            # Rule F: holdout taint propagates across the duplicate cluster.
            if comp_holdout_exposed[root]:
                if f["quality_flag"] != "ok":
                    votes.append((QUARANTINE, "HOLDOUT_DUP_FLAGGED"))
                else:
                    votes.append((REVIEW, "HOLDOUT_DUP"))
        if f["holdout"]:
            votes.append((REVIEW, "HOLDOUT"))
        elif f["patient_id"] in holdout_patients:
            if f["quality_flag"] != "ok":
                votes.append((QUARANTINE, "HOLDOUT_FLAGGED"))
            else:
                votes.append((REVIEW, "HOLDOUT_SIBLING"))
        max_sev = max(v[0] for v in votes)
        reason = min(
            (v[1] for v in votes if v[0] == max_sev), key=lambda r: PRIO[r]
        )
        results.append(
            {
                "frame_id": fid,
                "decision": DEC_NAME[max_sev],
                "reason_code": reason,
            }
        )
    results.sort(key=lambda r: r["frame_id"])
    return results
