# Milestone 2 — Author Rego Decisions

This is milestone 2 of a 3-milestone task (the same container is shared, so files
created in milestone 1 — `/app/bin/worker.sh`, `/app/out/evidence.json` — persist).
This file is self-contained: the full specification needed for this milestone is
below.

You are building a worker for a retinal triage queue that classifies each camera
frame as **promote**, **review**, or **quarantine**, using calibrated scores,
camera-quality flags, perceptual-duplicate clustering, and patient-holdout state
(never the raw model confidence). Milestone 1 produced a normalized evidence
snapshot; **this milestone authors the Rego policy that makes the decisions.**

---

## Environment

- `/app/data/queue.db` — DuckDB database holding the review queue (table `frames`).
- `/app/policy/thresholds.yaml` — the policy thresholds (read these; do not hardcode).
- `opa` (Open Policy Agent) and the `duckdb` **CLI** are installed and on `PATH`.
  `python` is available with `pyyaml`. Note: the DuckDB **Python module is not
  installed** — query the database through the `duckdb` CLI, not `import duckdb`.
- No network access.

### Input table `frames`

| column | type | meaning |
|---|---|---|
| `frame_id` | TEXT | unique frame id |
| `patient_id` | TEXT | patient grouping key |
| `raw_confidence` | DOUBLE | uncalibrated model confidence (0..1) — **a decoy; not used for decisions** |
| `calibration_bin` | INTEGER | calibration bin index (0..4) |
| `quality_flag` | TEXT | one of `ok`, `blur`, `glare`, `dark` |
| `holdout` | BOOLEAN | frame is reserved in the patient holdout set |
| `dup_hash` | BIGINT | 64-bit perceptual hash of the frame |

### Policy file `thresholds.yaml`

- `calibration_bins` — map from bin index (as a string key `"0"`..`"4"`) to the
  **calibrated score** for that bin.
- `promote_min_calibrated`, `review_min_calibrated` — calibrated-score gates.
- `quality_quarantine_flags` — quality flags that force a quarantine on their own.
- `dup_hamming_max` — two frames are duplicate-linked when the Hamming distance
  between their `dup_hash` values is **≤** this number.
- `fixpoint_max_steps` — an upper bound for the duplicate-cluster computation.

### Milestone-1 evidence (already on disk)

`/app/out/evidence.json` is a JSON array (sorted by `frame_id`) where each object
has: `frame_id`, `patient_id`, `raw_confidence`, `calibration_bin`,
`calibrated_score` (the bin's calibrated score), `quality_flag`, `holdout`,
`dup_hash` (decimal string), and `patient_holdout` (`true` iff any frame in the
patient group is holdout). You may use it or re-query the database.

---

## The decision specification

Each frame receives one `decision` (`promote` / `review` / `quarantine`) and one
`reason_code`. Conceptually, several rules each cast a *vote* for a decision level;
the final decision is the **most restrictive** vote
(`quarantine` > `review` > `promote`), and the `reason_code` is the highest-priority
reason among the votes at that final level (priority list below).
**Decisions never use `raw_confidence`.**

### Rule A — calibrated-bin gating (per frame)

Using `calibrated_score` (the bin's score), produce the base vote:

- `calibrated_score >= promote_min_calibrated` → **promote**, reason `CALIBRATED_OK`
- `review_min_calibrated <= calibrated_score < promote_min_calibrated` → **review**, reason `LOW_CALIBRATED`
- `calibrated_score < review_min_calibrated` → **quarantine**, reason `CALIBRATED_FAIL`

### Rule B — camera quality (per frame)

If `quality_flag` is one of `quality_quarantine_flags`, cast a **quarantine** vote
with reason `QUALITY_<FLAG>` where `<FLAG>` is the flag upper-cased
(e.g. `blur` → `QUALITY_BLUR`, `glare` → `QUALITY_GLARE`). Other flags (`ok`,
`dark`) cast no quality vote.

A frame is **intrinsically quarantined** if Rule A says quarantine **or** Rule B
applies. (This is what propagates in Rule C.)

### Rule C — transitive duplicate clustering (cross-frame)

Build duplicate links: two frames are linked when the Hamming distance between
their `dup_hash` values is ≤ `dup_hamming_max`. A **duplicate cluster** is a set of
frames connected **transitively** through these links — if A links B and B links C,
then A, B and C are all in one cluster, even if A and C are not directly linked.

For every frame in a cluster of size ≥ 2:

- If **any** frame in the whole cluster is intrinsically quarantined, every frame
  in that cluster casts a **quarantine** vote with reason `DUP_TAINT`.
- Otherwise (cluster has no intrinsic quarantine), every frame in the cluster casts
  a **review** vote with reason `DUP_REVIEW`.

A frame not in any cluster of size ≥ 2 casts no duplicate vote.

> The quarantine taint must reach the entire connected component, not just directly
> linked neighbours. Pairwise-only logic is incorrect.

### Rule D — holdout taint (cross-frame, by patient group)

Within a patient group (frames sharing `patient_id`):

- A frame with `holdout = true` casts a **review** vote, reason `HOLDOUT`.
- A frame with `holdout = false` **whose patient group contains at least one
  holdout frame** is a *sibling*:
  - if its own `quality_flag` is not `ok`, it casts a **quarantine** vote, reason
    `HOLDOUT_FLAGGED`;
  - otherwise it casts a **review** vote, reason `HOLDOUT_SIBLING`.
- A frame whose patient group has no holdout frame casts no holdout vote.

> A holdout frame downgrades its *siblings*, not only itself. Per-frame logic that
> ignores siblings is incorrect.

### Precedence and reason selection

`decision` = the most restrictive level among all votes a frame cast
(`quarantine` > `review` > `promote`). The base vote from Rule A is always present,
so every frame has at least one vote.

`reason_code` = among the votes whose level equals the final decision level, the
reason that comes **first** in this priority list:

```
QUALITY_BLUR, QUALITY_GLARE, CALIBRATED_FAIL, HOLDOUT_FLAGGED, DUP_TAINT,
HOLDOUT, HOLDOUT_SIBLING, DUP_REVIEW, LOW_CALIBRATED, CALIBRATED_OK
```

> ⚠️ **Use these exact string literals — verbatim, do not rename, paraphrase, or
> re-case them.** The verifier compares strings exactly.
>
> - `decision` is exactly one of: `promote`, `review`, `quarantine` (lowercase).
> - `reason_code` is exactly one of the ten codes above (UPPER_SNAKE_CASE).
>   `QUALITY_<FLAG>` is the literal flag upper-cased — `blur` → `QUALITY_BLUR`,
>   `glare` → `QUALITY_GLARE` (no other quality flags produce a quality vote).
>
> The complete, closed set of valid `reason_code` values is:
> `CALIBRATED_OK`, `LOW_CALIBRATED`, `CALIBRATED_FAIL`, `QUALITY_BLUR`,
> `QUALITY_GLARE`, `DUP_TAINT`, `DUP_REVIEW`, `HOLDOUT`, `HOLDOUT_SIBLING`,
> `HOLDOUT_FLAGGED`. No other strings are accepted. Invented or re-cased names
> (e.g. `dup_cluster`, `quality_quarantine`, `calibrated_promote`, `PROMOTE`,
> `OK`) will fail.

---

## Worked example

Five frames; `calibration_bins = {"0":0.10, "4":0.92}`,
`promote_min_calibrated = 0.70`, `review_min_calibrated = 0.40`,
`quality_quarantine_flags = [blur, glare]`, `dup_hamming_max = 5`.

| frame | patient | bin | quality | holdout | dup_hash (binary) |
|---|---|---|---|---|---|
| X1 | PA | 0 | ok | false | `0000` |
| X2 | PB | 4 | blur | false | `0000` |
| X3 | PC | 4 | ok | false | `0111` |
| X4 | PD | 4 | ok | false | `1110111` |
| X5 | PE | 4 | ok | true | `(isolated)` |

- **X1**: calibrated 0.10 < 0.40 → quarantine `CALIBRATED_FAIL`. No other votes →
  **quarantine / CALIBRATED_FAIL**.
- Duplicate links: dist(X2,X3)=3 ≤ 5 and dist(X3,X4)=4 ≤ 5, but dist(X2,X4)=7 > 5.
  Transitively {X2, X3, X4} form one cluster. X2 is intrinsically quarantined
  (blur), so the whole cluster is tainted.
  - **X2**: votes promote (`CALIBRATED_OK`), quarantine (`QUALITY_BLUR`),
    quarantine (`DUP_TAINT`). Final quarantine; `QUALITY_BLUR` outranks `DUP_TAINT`
    → **quarantine / QUALITY_BLUR**.
  - **X3**: votes promote, quarantine (`DUP_TAINT`) → **quarantine / DUP_TAINT**.
  - **X4**: votes promote, quarantine (`DUP_TAINT`) → **quarantine / DUP_TAINT**
    (reached only transitively via X3).
- **X5**: calibrated 0.92 → promote (`CALIBRATED_OK`); holdout → review
  (`HOLDOUT`). Final **review / HOLDOUT**.

---

## Deliverables

1. **`/app/policy/decision.rego`** — a valid Rego policy (must pass `opa check`)
   that implements the decision specification above (calibrated-bin gating, the
   camera-quality quarantine, the **transitive** duplicate-cluster taint, the
   holdout sibling taint, and the precedence / reason-code-priority resolution).

2. **`/app/out/decisions.json`** — a JSON array, **sorted ascending by
   `frame_id`**, with one object per frame containing exactly:

   | field | value |
   |---|---|
   | `frame_id` | the frame id |
   | `decision` | `promote` \| `review` \| `quarantine` |
   | `reason_code` | the resolved reason code (from the closed set above) |

Extend `/app/bin/worker.sh` so that, given `<db_path> <out_dir>`, it now produces
both `evidence.json` (milestone 1) **and** `decisions.json` in `<out_dir>`, and
still defaults to `/app/data/queue.db` and `/app/out` with no arguments. The
verifier re-runs `worker.sh` against a freshly generated database (so the logic
must be general, not hardcoded) and re-checks that the milestone-1 evidence output
still exists.
