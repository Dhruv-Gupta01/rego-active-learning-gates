"""Milestone 1 - Join Queue Evidence."""
import os

import helpers
import reference


def _evidence(outdir="/app/out"):
    return helpers.load_json(os.path.join(outdir, "evidence.json"))


def test_evidence_exists():
    assert os.path.exists("/app/out/evidence.json")
    assert os.path.exists("/app/bin/worker.sh")


def test_evidence_sorted():
    ev = _evidence()
    ids = [r["frame_id"] for r in ev]
    assert ids == sorted(ids)


def test_evidence_matches_reference():
    th = helpers.thresholds()
    assert _evidence() == reference.expected_evidence(helpers.REAL_DB, th)


def test_calibrated_not_raw():
    # T_RVC has high raw confidence (0.95) but calibration bin 0 -> 0.10.
    ev = {r["frame_id"]: r for r in _evidence()}
    assert ev["T_RVC"]["raw_confidence"] == 0.95
    assert ev["T_RVC"]["calibrated_score"] == 0.10


def test_patient_holdout_flag():
    ev = {r["frame_id"]: r for r in _evidence()}
    # P_HS group has a holdout frame; the clean sibling sees patient_holdout.
    assert ev["T_HSS"]["patient_holdout"] is True
    assert ev["T_HSH"]["patient_holdout"] is True
    assert ev["T_OK"]["patient_holdout"] is False


def test_hidden_db_evidence():
    th = helpers.thresholds()
    db = helpers.make_hidden_db()
    out = "/tmp/m1_hidden"
    helpers.run_worker(db, out)
    got = helpers.load_json(os.path.join(out, "evidence.json"))
    assert got == reference.expected_evidence(db, th)
