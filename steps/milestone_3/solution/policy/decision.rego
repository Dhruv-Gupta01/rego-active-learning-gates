package gates

import rego.v1

# ---------------------------------------------------------------------------
# Inputs:
#   input.frames     [{frame_id, patient_id, calibration_bin, quality_flag, holdout}]
#   input.edges      [[frame_id, frame_id], ...]  undirected duplicate links
#   input.thresholds {calibration_bins, promote_min_calibrated,
#                     review_min_calibrated, quality_quarantine_flags,
#                     dup_hamming_max, fixpoint_max_steps}
# Output: data.gates.decision = [{frame_id, decision, reason_code}, ...]
# ---------------------------------------------------------------------------

th := input.thresholds

frame_ids := {f.frame_id | some f in input.frames}

frame_by_id[f.frame_id] := f if some f in input.frames

qflags := {x | some x in th.quality_quarantine_flags}

# symmetric adjacency
edge_set contains [e[0], e[1]] if some e in input.edges
edge_set contains [e[1], e[0]] if some e in input.edges

neighbors(fid) := {b | some e in edge_set; e[0] == fid; b := e[1]}

# calibrated score from the calibration bin (NOT raw confidence)
calibrated(fid) := th.calibration_bins[sprintf("%d", [frame_by_id[fid].calibration_bin])]

# base decision from the calibrated score
base(fid) := ["quarantine", "CALIBRATED_FAIL"] if calibrated(fid) < th.review_min_calibrated
base(fid) := ["promote", "CALIBRATED_OK"] if calibrated(fid) >= th.promote_min_calibrated
base(fid) := ["review", "LOW_CALIBRATED"] if {
    calibrated(fid) >= th.review_min_calibrated
    calibrated(fid) < th.promote_min_calibrated
}

# intrinsic (per-frame) quarantine: failing calibration OR a hard quality flag
intrinsic_quarantine contains fid if {
    some fid in frame_ids
    base(fid)[0] == "quarantine"
}
intrinsic_quarantine contains fid if {
    some fid in frame_ids
    frame_by_id[fid].quality_flag in qflags
}

# ---------------------------------------------------------------------------
# Transitive duplicate-cluster closure via a bounded, hand-unrolled label
# propagation (Rego forbids recursion). Each frame's label converges to the
# lexicographically smallest frame_id in its connected component.
# ---------------------------------------------------------------------------
label_0[fid] := fid if some fid in frame_ids

label_1[fid] := m if {
    some fid in frame_ids
    cands := {label_0[fid]} | {label_0[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_2[fid] := m if {
    some fid in frame_ids
    cands := {label_1[fid]} | {label_1[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_3[fid] := m if {
    some fid in frame_ids
    cands := {label_2[fid]} | {label_2[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_4[fid] := m if {
    some fid in frame_ids
    cands := {label_3[fid]} | {label_3[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_5[fid] := m if {
    some fid in frame_ids
    cands := {label_4[fid]} | {label_4[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_6[fid] := m if {
    some fid in frame_ids
    cands := {label_5[fid]} | {label_5[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_7[fid] := m if {
    some fid in frame_ids
    cands := {label_6[fid]} | {label_6[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_8[fid] := m if {
    some fid in frame_ids
    cands := {label_7[fid]} | {label_7[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_9[fid] := m if {
    some fid in frame_ids
    cands := {label_8[fid]} | {label_8[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_10[fid] := m if {
    some fid in frame_ids
    cands := {label_9[fid]} | {label_9[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_11[fid] := m if {
    some fid in frame_ids
    cands := {label_10[fid]} | {label_10[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_12[fid] := m if {
    some fid in frame_ids
    cands := {label_11[fid]} | {label_11[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_13[fid] := m if {
    some fid in frame_ids
    cands := {label_12[fid]} | {label_12[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_14[fid] := m if {
    some fid in frame_ids
    cands := {label_13[fid]} | {label_13[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_15[fid] := m if {
    some fid in frame_ids
    cands := {label_14[fid]} | {label_14[n] | some n in neighbors(fid)}
    m := min(cands)
}
label_16[fid] := m if {
    some fid in frame_ids
    cands := {label_15[fid]} | {label_15[n] | some n in neighbors(fid)}
    m := min(cands)
}

component(fid) := label_16[fid]

members(c) := {fid | some fid in frame_ids; label_16[fid] == c}

comp_size(fid) := count(members(component(fid)))

comp_tainted(fid) if {
    some m in members(component(fid))
    m in intrinsic_quarantine
}

# a duplicate cluster is holdout-exposed if any member is a holdout frame
comp_holdout_exposed(fid) if {
    some m in members(component(fid))
    frame_by_id[m].holdout
}

# cluster representative: highest calibrated score, ties broken by smallest id
comp_max_score(fid) := max([calibrated(m) | some m in members(component(fid))])

comp_rep(fid) := min([m |
    some m in members(component(fid))
    calibrated(m) == comp_max_score(fid)
])

# patients that have at least one holdout frame
holdout_patients contains pid if {
    some f in input.frames
    f.holdout
    pid := f.patient_id
}

# ---------------------------------------------------------------------------
# Votes: each (severity, reason). Final severity is the max; the reason is the
# highest-priority reason among those at the max severity.
# severity: promote=0, review=1, quarantine=2
# ---------------------------------------------------------------------------
sev := {"promote": 0, "review": 1, "quarantine": 2}

votes contains [fid, sev[base(fid)[0]], base(fid)[1]] if some fid in frame_ids

votes contains [fid, 2, sprintf("QUALITY_%s", [upper(frame_by_id[fid].quality_flag)])] if {
    some fid in frame_ids
    frame_by_id[fid].quality_flag in qflags
}

votes contains [fid, 2, "DUP_TAINT"] if {
    some fid in frame_ids
    comp_size(fid) >= 2
    comp_tainted(fid)
}

votes contains [fid, 1, "DUP_REVIEW"] if {
    some fid in frame_ids
    comp_size(fid) >= 2
    not comp_tainted(fid)
    fid != comp_rep(fid)
}

votes contains [fid, 2, "HOLDOUT_DUP_FLAGGED"] if {
    some fid in frame_ids
    comp_size(fid) >= 2
    comp_holdout_exposed(fid)
    frame_by_id[fid].quality_flag != "ok"
}

votes contains [fid, 1, "HOLDOUT_DUP"] if {
    some fid in frame_ids
    comp_size(fid) >= 2
    comp_holdout_exposed(fid)
    frame_by_id[fid].quality_flag == "ok"
}

votes contains [fid, 1, "HOLDOUT"] if {
    some fid in frame_ids
    frame_by_id[fid].holdout
}

votes contains [fid, 2, "HOLDOUT_FLAGGED"] if {
    some fid in frame_ids
    not frame_by_id[fid].holdout
    frame_by_id[fid].patient_id in holdout_patients
    frame_by_id[fid].quality_flag != "ok"
}

votes contains [fid, 1, "HOLDOUT_SIBLING"] if {
    some fid in frame_ids
    not frame_by_id[fid].holdout
    frame_by_id[fid].patient_id in holdout_patients
    frame_by_id[fid].quality_flag == "ok"
}

priority := ["QUALITY_BLUR", "QUALITY_GLARE", "CALIBRATED_FAIL",
             "HOLDOUT_FLAGGED", "HOLDOUT_DUP_FLAGGED", "DUP_TAINT", "HOLDOUT",
             "HOLDOUT_SIBLING", "HOLDOUT_DUP", "DUP_REVIEW", "LOW_CALIBRATED",
             "CALIBRATED_OK"]

prio_index(r) := i if {
    some i
    priority[i] == r
}

max_sev(fid) := max([v[1] | some v in votes; v[0] == fid])

chosen_reason(fid) := priority[idx] if {
    ms := max_sev(fid)
    idx := min([prio_index(v[2]) | some v in votes; v[0] == fid; v[1] == ms])
}

decision_name := ["promote", "review", "quarantine"]

decision contains {
    "frame_id": fid,
    "decision": decision_name[max_sev(fid)],
    "reason_code": chosen_reason(fid),
} if some fid in frame_ids
