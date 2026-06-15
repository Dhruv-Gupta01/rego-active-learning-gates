"""Milestone 2 - Author Rego Decisions (the algorithmic core)."""
import os
import subprocess

import helpers
import reference


def _decisions(outdir="/app/out"):
    return {r["frame_id"]: r for r in helpers.load_json(os.path.join(outdir, "decisions.json"))}


def test_artifacts_exist():
    assert os.path.exists("/app/out/decisions.json")
    assert os.path.exists("/app/policy/decision.rego")
    # milestone-1 artifact persists in the shared container
    assert os.path.exists("/app/out/evidence.json")


def test_policy_is_valid_rego():
    proc = subprocess.run(
        ["opa", "check", "/app/policy/decision.rego"],
        text=True, capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_decisions_match_reference():
    th = helpers.thresholds()
    got = sorted(helpers.load_json("/app/out/decisions.json"), key=lambda r: r["frame_id"])
    assert got == reference.expected_decisions(helpers.REAL_DB, th)


def test_trap_raw_vs_calibrated():
    d = _decisions()["T_RVC"]
    assert (d["decision"], d["reason_code"]) == ("quarantine", "CALIBRATED_FAIL")


def test_trap_transitive_dup_chain():
    d = _decisions()
    # A is intrinsically blur; taint must reach B and the non-adjacent C.
    assert d["T_DUPA"]["decision"] == "quarantine"
    assert d["T_DUPA"]["reason_code"] == "QUALITY_BLUR"
    assert (d["T_DUPB"]["decision"], d["T_DUPB"]["reason_code"]) == ("quarantine", "DUP_TAINT")
    assert (d["T_DUPC"]["decision"], d["T_DUPC"]["reason_code"]) == ("quarantine", "DUP_TAINT")


def test_trap_holdout_sibling():
    d = _decisions()
    assert (d["T_HSH"]["decision"], d["T_HSH"]["reason_code"]) == ("review", "HOLDOUT")
    assert (d["T_HSS"]["decision"], d["T_HSS"]["reason_code"]) == ("review", "HOLDOUT_SIBLING")


def test_trap_precedence_holdout_flagged():
    d = _decisions()["T_PRS"]
    assert (d["decision"], d["reason_code"]) == ("quarantine", "HOLDOUT_FLAGGED")


def test_clean_promote():
    d = _decisions()["T_OK"]
    assert (d["decision"], d["reason_code"]) == ("promote", "CALIBRATED_OK")


def test_hidden_db_decisions():
    th = helpers.thresholds()
    db = helpers.make_hidden_db()
    out = "/tmp/m2_hidden"
    helpers.run_worker(db, out)
    got = sorted(helpers.load_json(os.path.join(out, "decisions.json")), key=lambda r: r["frame_id"])
    assert got == reference.expected_decisions(db, th)
