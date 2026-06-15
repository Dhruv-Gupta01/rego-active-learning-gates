# Active-Learning Gates for the Retinal Triage Queue

The retinal triage queue is promoting blurred and duplicate camera frames because
raw model confidence is being trusted directly — without calibration bins, camera
quality flags, or patient-holdout state. You will build a background worker that
reads a DuckDB review queue, combines the SQL evidence with a YAML policy, and
classifies every frame as **promote**, **review**, or **quarantine**.

The work is split into three milestones that share the same container (files you
create persist between milestones):

1. **Join Queue Evidence** (this milestone) — build a normalized evidence snapshot.
2. **Author Rego Decisions** — a Rego policy that classifies each frame.
3. **Run Snapshot Worker** — run the policy over the queue and emit decisions.

Read this entire file: it is the full specification for all three milestones.

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

---

## Milestone 1 — Join Queue Evidence

Produce `/app/out/evidence.json`: a JSON array with **one object per frame**,
**sorted ascending by `frame_id`**, each object containing exactly:

| field | value |
|---|---|
| `frame_id` | from the row |
| `patient_id` | from the row |
| `raw_confidence` | from the row |
| `calibration_bin` | from the row |
| `calibrated_score` | the calibrated score for this row's `calibration_bin`, looked up in `calibration_bins` |
| `quality_flag` | from the row |
| `holdout` | boolean |
| `dup_hash` | the `dup_hash` value as a **decimal string** |
| `patient_holdout` | `true` iff **any** frame sharing this row's `patient_id` has `holdout = true` (this includes the holdout frame itself) |

`calibrated_score` must come from the calibration bin, **not** from
`raw_confidence`. `patient_holdout` is a cross-row property of the whole patient
group, not of the single row.

### Worker entrypoint (required from this milestone on)

Provide an executable **`/app/bin/worker.sh`** taking two arguments:

```
/app/bin/worker.sh <db_path> <out_dir>
```

It must read the queue from `<db_path>` and write this milestone's output
file(s) into `<out_dir>`. Running it with no arguments must default to
`/app/data/queue.db` and `/app/out`. The verifier invokes `worker.sh` against a
**different, freshly generated** database to confirm the logic is general
(in addition to checking the files your solution wrote to `/app/out`). Each later
milestone extends the same `worker.sh` to also emit its new outputs.

---

## The decision policy (specified now; implemented in milestones 2 and 3)

Milestones 2 and 3 classify each frame. The full required behavior is below so you
can design the evidence with it in mind. **Decisions never use `raw_confidence`.**

Each frame ultimately receives one `decision` (`promote` / `review` / `quarantine`)
and one `reason_code`. Conceptually, several rules each cast a *vote* for a decision
level; the final decision is the **most restrictive** vote
(`quarantine` > `review` > `promote`), and the `reason_code` is the highest-priority
reason among the votes at that final level (priority list given below).

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
- Otherwise the cluster is *clean*: exactly one member — the **representative**,
  defined as the frame with the highest `calibrated_score` (ties broken by the
  smallest `frame_id`) — keeps its base decision and casts **no** duplicate vote;
  every **other** member casts a **review** vote with reason `DUP_REVIEW`.

A frame not in any cluster of size ≥ 2 casts no duplicate vote.

> The quarantine taint must reach the entire connected component, not just directly
> linked neighbours. Pairwise-only logic is incorrect. In a clean cluster only the
> single highest-calibrated representative may keep a `promote`; its near-duplicates
> cannot.

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

### Rule E — holdout taint through duplicates (cross-frame)

Independently of Rule C's quarantine taint, a duplicate cluster (size ≥ 2) is
**holdout-exposed** if **any** of its members has `holdout = true`. Every member of
a holdout-exposed cluster casts a vote:

- if its own `quality_flag` is not `ok` → a **quarantine** vote with reason
  `HOLDOUT_DUP_FLAGGED`;
- otherwise → a **review** vote with reason `HOLDOUT_DUP`.

> This couples duplicate clustering with holdout state: a frame can be downgraded
> because a *duplicate of it* (possibly from a different patient) is a holdout frame
> — even when the frame's own patient group contains no holdout. Holdout logic that
> only looks within a patient group is incorrect.

### Precedence and reason selection

`decision` = the most restrictive level among all votes a frame cast
(`quarantine` > `review` > `promote`). The base vote from Rule A is always present,
so every frame has at least one vote.

`reason_code` = among the votes whose level equals the final decision level, the
reason that comes **first** in this priority list:

```
QUALITY_BLUR, QUALITY_GLARE, CALIBRATED_FAIL, HOLDOUT_FLAGGED,
HOLDOUT_DUP_FLAGGED, DUP_TAINT, HOLDOUT, HOLDOUT_SIBLING, HOLDOUT_DUP,
DUP_REVIEW, LOW_CALIBRATED, CALIBRATED_OK
```

> ⚠️ **Use these exact string literals — verbatim, do not rename, paraphrase, or
> re-case them.** The verifier compares strings exactly.
>
> - `decision` is exactly one of: `promote`, `review`, `quarantine` (lowercase).
> - `reason_code` is exactly one of the twelve codes above (UPPER_SNAKE_CASE).
>   `QUALITY_<FLAG>` is the literal flag upper-cased — `blur` → `QUALITY_BLUR`,
>   `glare` → `QUALITY_GLARE` (no other quality flags produce a quality vote).
>
> The complete, closed set of valid `reason_code` values is:
> `CALIBRATED_OK`, `LOW_CALIBRATED`, `CALIBRATED_FAIL`, `QUALITY_BLUR`,
> `QUALITY_GLARE`, `DUP_TAINT`, `DUP_REVIEW`, `HOLDOUT`, `HOLDOUT_SIBLING`,
> `HOLDOUT_FLAGGED`, `HOLDOUT_DUP`, `HOLDOUT_DUP_FLAGGED`. No other strings are accepted.

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

## Output / verification notes

- All JSON output is sorted by `frame_id`. The verifier recomputes the expected
  results independently and compares exactly, and also re-runs your worker against a
  freshly generated database, so hardcoding to the shipped queue will fail.
- For milestone 1, only `/app/out/evidence.json` is checked, but later milestones
  re-check that earlier artifacts still exist.
