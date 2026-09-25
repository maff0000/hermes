# Incident: HMT-2 canonical-store data loss during test cleanup — 2026-09-25

## Classification

**This incident is classified as loss of REPRODUCIBLE DERIVED CANONICAL ARTEFACTS, not
source-data loss.** All native retained bytes in `research-source/` are intact and no
reacquisition of vendor market data is required. What was lost is derived, deterministically
re-computable canonical output (canonical-v1 partitions, evidence/lineage/quality records) for
sessions that had previously been canonicalised, plus a forensic analysis tree from an earlier,
unrelated defect investigation.

The deleted `canonical-v1-superseded-by-namespace-defect` forensic tree (raw parquet form, 1009
physical paths per the forensic report's own inventory) is **LOST DURING THE 2026-09-25
TEST-CLEANUP INCIDENT DESCRIBED BELOW**. Its raw physical form will not be recreated. If anyone
later regenerates canonical output that happens to look similar, it MUST NOT be represented as
the original defect-era artefact set — only the two surviving forensic analysis JSON reports
(copied into this incident's evidence directory, hashed below) remain as evidence of that
historical defect.

## Summary

On 2026-09-25, a merged test — `test_default_root_cli_invocation_still_writes_the_tracked_snapshot_as_before`
in `tests/hmt2/test_hmt2i_snapshot_output_path.py` (merged via PR #173) — was run with
`pytest tests/hmt1 tests/hmt2 -q` from within the persistent authoritative worktree
`/srv/rogue-hermes/worktrees/hmt2-governed-gc-mbp1-corpus`. The test's own `finally:` block
computes the **default** (non-overridden) canonical-store root via
`canonical_worker.corpus_canonical_store_root(None)` and unconditionally `shutil.rmtree()`s it.
Because the test was run from inside the authoritative worktree rather than from an isolated
checkout, "the default root" resolved to the real, live canonical-store directory, and the
`rmtree` call deleted it for real.

This deleted:
- Canonical/evidence/quality/lineage data for the ~150 previously-canonicalised sessions that
  existed before this incident (all now show `CANONICAL_PENDING` in the canonical ledger — see
  below).
- The `canonical-v1-superseded-by-namespace-defect` forensic tree (1009 parquet files) preserved
  from an earlier, separate defect investigation.

Native source data in `research-source/` was **not** touched by this test (it operates on the
canonical-store side only) and is confirmed intact — see the native-artefact integrity check
below.

A full 150/150-session, zero-drift canonical-store regression had been run and had completed
**before** this incident. Its output survives intact on disk and is treated as the critical
recovery oracle for any future rebuild-and-verify effort; it has been copied into this incident's
evidence directory (see below) and independently re-hashed.

The two fixes already merged same-day:
- **PR #173** — "[HMT-2] Fix: scratch canonicalise runs must never mutate authoritative
  snapshot" — merged `2026-09-25T07:25:55Z`, merge commit `36cab9ed37269775195c0f6d427c93d874537fb7`.
- **PR #175** — "[Ops hardening] Fail-closed hmt2.slice containment guard for real-data HMT-2
  entry points" — merged `2026-09-25T07:29:04Z`, merge commit `256dd3db6de014fb3c842c6184085875d5d7758d`.

Both merges predate the destructive run (directory birth-time evidence below, `08:31:25 BST`),
i.e. the destructive test run happened using code from a worktree state that had *just* received
these two hardening fixes but was executed in a way (or from a stale/pre-fix checkout position
relative to the authoritative worktree, or before the guard was actually exercised on this path)
that still allowed the real default root to be resolved and deleted. This document does not
attempt to adjudicate exactly why the new guard/fix did not prevent this specific invocation —
that root-cause analysis is out of scope for this evidence record and is left to the engineering
follow-up.

## This document's scope and method

This record was assembled by a read-only evidence-collection pass against:
- The real authoritative worktree, `/srv/rogue-hermes/worktrees/hmt2-governed-gc-mbp1-corpus`,
  on `dell-debian` (192.168.11.10) — **read only, never written to or executed against**.
- The dell-debian filesystem generally (`/tmp/hmt2_forensic_*.json`,
  `/tmp/full_regression_driver.log`, `/tmp/hmt2-bounded-proof-store/full-regression-progress.json`,
  `/tmp/hmt2-drain-*.log`).
- `gh pr view` against `github.com/maff0000/hermes`.

All figures below were independently re-derived from the raw files at the time of writing
(2026-09-25, ~10:20 BST), not merely copied from a prior narrative. Where a claim rests on a
prior account rather than something this pass could independently re-derive, that is stated
explicitly (see "Causal sequence" below).

## 1. Current authoritative branch SHA

```
$ git log -1   (in /srv/rogue-hermes/worktrees/hmt2-governed-gc-mbp1-corpus)
commit 8a0fb2e11ae582c2baed6b84c887efd0fd84df78
Author: Matt Scott <matt@infosecurs.com>
Date:   Fri Sep 25 09:07:56 2026 +0100

    [HMT-2 backlog drain] GC-2020-02-19 CANONICAL_COMPLETE (3/11)
```

Branch: `hmt-2/governed-gc-mbp1-historical-corpus`. Confirmed identical to `origin`'s tip via
`git fetch` immediately before creating this incident's own worktree — no emergency fix had
landed on top of it by the time of this evidence pass.

## 2. PR #173 / PR #175 merge SHAs and timestamps

| PR | Title | Merged at (UTC) | Merge commit |
|----|-------|------------------|--------------|
| #173 | [HMT-2] Fix: scratch canonicalise runs must never mutate authoritative snapshot | 2026-09-25T07:25:55Z | `36cab9ed37269775195c0f6d427c93d874537fb7` |
| #175 | [Ops hardening] Fail-closed hmt2.slice containment guard for real-data HMT-2 entry points | 2026-09-25T07:29:04Z | `256dd3db6de014fb3c842c6184085875d5d7758d` |

(07:25/07:29 UTC = 08:25/08:29 BST — both merges land ~2–6 minutes before the destructive
directory recreation at 08:31:25 BST, item 7 below.)

## 3. Acquisition ledger — current state

Source: `research-source/hmt2-gc-mbp1-v1/manifest/gc_corpus_acquisition_ledger.json` (448
entries, one per GC daily session considered for real acquisition).

| State | Count |
|-------|-------|
| COMPLETE | 162 |
| PLANNED | 286 |
| FAILED_AMBIGUOUS | 0 |
| **Total** | **448** |

**Ledger acquisition spend** (sum of `actual_cost_usd` across all 448 acquisition-ledger
entries): **$10.031334578989998** (≈ $10.03).

**Total HMT-2 programme spend** (per the established running-total formula in
`docs/research/hmt2-corpus-selection-methodology-v2.md` §8.4 — reference-series actual +
GC.FUT definitions actual + the per-session mbp-1 acquisition-ledger actual spend):

```
reference-series actual   $0.546575635672   (HMT2-REAL-ACQ-REFERENCE-SERIES-GC-V0-OHLCV1H)
GC.FUT definitions actual $1.692707203329   (HMT2-REAL-ACQ-GC-FUT-DEFINITIONS)
ledger acquisition spend  $10.031334578989998
---------------------------------------------
total HMT-2 programme spend = $12.270617417990998   (≈ $12.27)
```

No FAILED_AMBIGUOUS entries currently exist in the acquisition ledger.

## 4. Canonical ledger — current state

Source: `research-canonical-store/hmt2-gc-mbp1-v1/manifest/gc_corpus_canonical_ledger.json`
(162 entries, one per COMPLETE acquisition-ledger session).

| State | Count |
|-------|-------|
| CANONICAL_COMPLETE | 3 |
| CANONICAL_PENDING | 159 |
| FAILED_AMBIGUOUS | 0 |
| **Total** | **162** |

The 3 sessions currently `CANONICAL_COMPLETE` (all canonicalised via the post-incident,
post-PR#172/#174 bounded-memory fix, as the HMT-2 backlog drain resumes):

- `GC-2019-12-05`
- `GC-2020-02-19`
- `GC-2020-03-04`

**All other 159 sessions in the canonical ledger are `CANONICAL_PENDING`.** Before the incident,
per the account given for this task, ~150 of these had previously been `CANONICAL_COMPLETE`
(consistent with the 150/150 full-corpus regression evidence in item 10) and are now pending
again because their canonical-v1 output was deleted. This document does not have a
pre-incident snapshot of the canonical ledger to diff against directly (the ledger file itself
was inside the deleted tree and was recreated fresh); the 150/150 regression log/progress JSON
(item 10) is the strongest surviving direct evidence of which specific sessions were previously
complete, since it enumerates all 150 by session ID with per-session compare results.

## 5. Native artefact integrity check (research-source/)

164 `.dbn.zst` files exist under `research-source/hmt2-gc-mbp1-v1/`:

- **162** are per-session daily GC artefacts under `sessions/<SESSION_ID>/source/*.dbn.zst`,
  one per COMPLETE acquisition-ledger entry.
- **2** are auxiliary reference artefacts outside the per-session ledger schema:
  `HMT2-REAL-ACQ-GC-FUT-DEFINITIONS/definitions/gc_fut_definitions_2017-05-21_2026-09-19.dbn.zst`
  and `HMT2-REAL-ACQ-REFERENCE-SERIES-GC-V0-OHLCV1H/source/gc_v0_ohlcv1h_2017-05-21_2026-09-19.dbn.zst`.
  Each has its own evidence JSON recording `actual_cost_usd` (used in item 3 above) but is not
  keyed into the 448-entry per-session acquisition ledger and has no `native_artefact_sha256`
  field there to check against.

**SHA-256 verification result (full population, not a sample — 1.6 GiB total, ~6.3s to hash):**
all 162 per-session artefacts were re-hashed and compared against the acquisition ledger's
`artefact.sha256` field for that session.

```
match:            162
mismatch:           0
no_ledger_entry:    0   (once the 2 auxiliary reference/definitions artefacts are excluded
                          from the per-session comparison, as above)
```

**Result: 162/162 exact match, 0 mismatches.** Native source-data integrity is fully confirmed —
this incident did not touch `research-source/`.

## 6. Destructive test — exact location

File: `tests/hmt2/test_hmt2i_snapshot_output_path.py`
Function: `test_default_root_cli_invocation_still_writes_the_tracked_snapshot_as_before` (defined
at line 191)

The destructive statement, verbatim, at **line 225**, inside a `finally:` block starting at
line 219:

```python
219:    finally:
220:        # Restore the tracked file to its pre-test content (this test's whole point is that the
221:        # DEFAULT invocation legitimately writes here, so we must clean up after ourselves) and
222:        # remove the real, gitignored default canonical-store artefacts this run created.
223:        tracked_path.write_bytes(before_bytes)
224:        if real_default_store_root.exists():
225:            shutil.rmtree(real_default_store_root)
```

where (line 210, inside the `try:` above) `real_default_store_root` is computed as:

```python
210:    real_default_store_root = canonical_worker.corpus_canonical_store_root(None)
```

i.e. the test computes the same "default root" the real canonicalisation CLI would use with no
override, and — believing itself to be operating on a throwaway result it just created — deletes
that entire directory tree. When run from inside the persistent authoritative worktree, that
"default root" is the real, live canonical-store root, not a scratch location.

## 7. Incident timestamps

| Event | Time |
|-------|------|
| PR #173 merged | 2026-09-25T07:25:55Z (08:25:55 BST) |
| PR #175 merged | 2026-09-25T07:29:04Z (08:29:04 BST) |
| **Canonical-store directory recreated (post-delete) — Birth time, independently re-verified** | **2026-09-25 08:31:25.479851628 +0100 (BST)** |
| First post-incident drain-agent log (`hmt2-drain-GC-2019-12-05.log`, committed as `hmt2-drain-GC-2019-12-05.log.txt`) | 2026-09-25 08:43 BST |
| `hmt2-drain-GC-2020-03-04.log` (committed as `hmt2-drain-GC-2020-03-04.log.txt`) | 2026-09-25 08:55 BST |
| `hmt2-drain-GC-2020-02-19.log` (committed as `hmt2-drain-GC-2020-02-19.log.txt`) | 2026-09-25 09:07 BST |
| `hmt2-drain-GC-2020-01-10.log` (0 bytes — interrupted/in-flight attempt; committed as `hmt2-drain-GC-2020-01-10.log.txt`) | 2026-09-25 09:08 BST |
| Latest authoritative-worktree commit (this evidence pass) | 2026-09-25 09:07:56 BST (`8a0fb2e1`) |

Birth-time re-verification, run independently for this document (`stat` on the real authoritative
worktree, read-only):

```
$ stat research-canonical-store/hmt2-gc-mbp1-v1
  File: research-canonical-store/hmt2-gc-mbp1-v1
  Size: 4096       Blocks: 8          IO Block: 4096   directory
Access: 2026-09-25 09:15:42.333795602 +0100
Modify: 2026-09-25 08:43:27.222512799 +0100
Change: 2026-09-25 08:43:27.222512799 +0100
 Birth: 2026-09-25 08:31:25.479851628 +0100
```

This confirms, from raw filesystem metadata rather than narrative, that the directory now present
did not exist before **08:31:25 BST** on 2026-09-25 — consistent with the previously-established
incident time, and independently reproduced here rather than taken on faith.

## 8. Current canonical-store directory inventory

`research-canonical-store/hmt2-gc-mbp1-v1/` currently contains:

```
canonical-v2/                     -- 3 sessions' partitioned parquet output (81 parquet files
                                      total across GC-2019-12-05, GC-2020-02-19, GC-2020-03-04)
evidence/                         -- GC-2019-12-05.json, GC-2020-02-19.json, GC-2020-03-04.json
lineage/                          -- GC-2019-12-05.json, GC-2020-02-19.json, GC-2020-03-04.json
quality/                          -- GC-2019-12-05.json, GC-2020-02-19.json, GC-2020-03-04.json
manifest/gc_corpus_canonical_ledger.json
.staging/GC-2020-01-10-174018-e7d08f10/   -- in-flight/interrupted staging area (partition_staging
                                              + event_spool) for a drain attempt that had not
                                              completed at the time of this evidence pass
```

**What is known to be missing** (i.e. what existed before 08:31:25 BST on 2026-09-25 and does
not exist now):

- Any `canonical-v1` tree at all — none exists in the current inventory. Canonical output is now
  exclusively under `canonical-v2/`, and only for the 3 sessions drained since the incident.
- Canonical/evidence/lineage/quality records for the ~150 sessions that the pre-incident 150/150
  regression (item 10) shows as previously complete and matching — all now show
  `CANONICAL_PENDING` in the canonical ledger (item 4).
- The `canonical-v1-superseded-by-namespace-defect` forensic tree (1009 physical parquet paths
  per the forensic report's own aggregate count) — not present anywhere in the current
  authoritative worktree; no trace of that path name was found in the current tree or in
  `git log --all` for this repository, which is consistent with it having been an
  out-of-band/gitignored forensic artefact rather than a tracked one, now gone.

## 9. Forensic report evidence (copied, not moved)

Copied read-only from `dell-debian:/tmp/` into this incident's evidence directory at
`docs/incidents/2026-09-25-hmt2-canonical-store-data-loss-evidence/`. Originals on dell-debian
were left in place and untouched (verified: original `/tmp/hmt2_forensic_report.json` mtime
unchanged at `2026-09-22 22:29`, after the copy).

`hmt2_forensic_report.json` aggregate summary (from the file itself):

```json
{
  "collision_path_count": 57,
  "distinct_victim_session_count": 19,
  "sessions_fully_valid": 103,
  "sessions_with_at_least_one_stale_reference": 19,
  "total_artifact_hash_mismatches": 58,
  "total_completed_sessions_inspected": 122,
  "total_lineage_partition_references": 1067,
  "total_missing_files": 0,
  "total_semantic_hash_mismatches": 58,
  "unique_physical_paths_referenced": 1009
}
```

This report documents an **earlier, separate** namespace-collision defect (physical-path
collisions between distinct sessions' partition files under the old canonical-v1 layout),
investigated and forensically analysed before the 2026-09-25 test-cleanup incident. The
1009-physical-path forensic tree it analysed is the tree now lost per item 8 above. Only this
JSON analysis and its per-session companion (`hmt2_forensic_per_session.json`) survive as
evidence of that historical defect.

## 10. 150/150 full-corpus regression — the recovery oracle

**This is the single most important piece of surviving evidence for any future rebuild.** It was
generated by a completed pre-incident regression run and copied, not moved, from dell-debian.

- `full_regression_driver.log` (5,901 bytes; committed in this repo as
  `full_regression_driver.log.txt` — the repository's `.gitignore` blanket-excludes `*.log`, so
  evidence log files are committed with a `.txt` suffix; content and hash are unchanged by the
  rename) — human-readable run log. Tail confirms:
  ```
  [149/150] GC-2020-03-13: MATCH (622.0s)
  [150/150] GC-2020-03-16: MATCH (662.4s)
  ```
  `started_utc` (from the progress JSON): `2026-09-24T17:09:15Z`.

- `full-regression-progress.json` (164,544 bytes) — **not truncated**. Contains:
  - `total_governed: 150, completed_so_far: 150, match_count: 150, drift_count: 0, run_failed_count: 0, remaining: 0`
  - A `completed` object keyed by all 150 session IDs, each with a full `compare_report`
    (per-session hash-level detail: `authoritative_canonical_event_set_hash`,
    `new_canonical_event_set_hash`, per-check booleans for native artefact hash, partition
    artefact/semantic hashes, partition path set, source record count, etc.) and `status: "MATCH"`
    for every one of the 150 sessions.

This confirms, independently of the narrative given for this task, that a full 150-session,
zero-drift regression genuinely exists on disk with complete per-session hash detail — this is
not a summary-only or rotated/truncated log, and is sufficient on its own to re-verify any future
rebuild of the lost canonical-v1 output against a known-good hash baseline.

## 11. Causal sequence — narrative vs. independently re-verified

The account of the causal sequence (PR #173's test, run via `pytest tests/hmt1 tests/hmt2 -q`
from inside the authoritative worktree, deleting the real default canonical-store root via its
`finally:` block) was given as background for this task. This document does **not** treat that
narrative as proven fact on its own; the following is what this evidence pass could and could
not independently re-derive from raw artefacts:

**Independently re-verified from raw evidence:**
- The exact test file, function, and `shutil.rmtree()` statement exist exactly as described
  (item 6) — confirmed by reading the file directly, not by trusting the narrative.
- PR #173's title ("scratch canonicalise runs must never mutate authoritative snapshot") and
  PR #175's title ("containment guard for real-data HMT-2 entry points") are consistent with a
  team response to exactly this class of defect, and both merged within ~6 minutes of each other,
  shortly before the directory-recreation timestamp (item 2, item 7).
- The canonical-store directory's Birth time (08:31:25.479851628 BST) was independently
  re-`stat`'d for this document, not copied from a prior report, and matches the
  previously-established time.
- The directory currently contains no canonical-v1 tree and no
  `canonical-v1-superseded-by-namespace-defect` tree — consistent with both having been deleted
  and only canonical-v2 (post-fix) output existing now.
- The canonical ledger currently shows only 3 CANONICAL_COMPLETE sessions against 159 PENDING —
  consistent with ~150 previously-complete sessions having reverted to pending, matching the
  150/150 regression's session count.

**Not independently re-derivable from raw evidence available to this read-only pass:**
- No surviving log on dell-debian records the literal `pytest tests/hmt1 tests/hmt2 -q` command
  invocation, its working directory, or its PID at the time it ran. This evidence pass could not
  find a shell-history or process-accounting artefact proving the exact command line or that it
  was run *from* the authoritative worktree specifically (as opposed to, e.g., a symlinked or
  environment-variable-pointed path that resolved to the same real directory). The `finally:`
  block's logic and the directory's birth-time match are strong, consistent circumstantial
  evidence, but the exact operator command line itself rests on the narrative given for this
  task, not on a file this pass could read.
- This pass has no pre-incident copy of the canonical ledger to diff byte-for-byte against the
  current one; the "~150 previously CANONICAL_COMPLETE sessions" figure is corroborated by the
  150/150 regression's session list (item 10), not by a direct ledger-state diff.

Readers relying on this document for any future action should treat the reproducible,
independently-verified facts above (test code, directory birth-time, current ledger states,
native-artefact hash integrity, regression oracle) as the load-bearing evidence, and the specific
minute-by-minute operator narrative as background context, not as independently proven fact.

## 12. Host/service health at time of writing (2026-09-25 ~10:20 BST)

- `hmt2.slice` — **active**, `Active: active since Wed 2026-09-23 09:14:35 BST` (2 days), Tasks: 0
  (idle), Memory: 351.6M (high: 8.0G, max: 10.0G, available: 7.6G). No sign of throttling or
  ongoing runaway memory use at time of writing.
- `docker ps` — all HMT-2/HERMES-adjacent containers (`hermes-signal`, `darwin-darwin_core-1`,
  `darwin-darwin_sql-1`, etc.) report `Up`/`healthy`. Two containers unrelated to HMT-2 show
  non-nominal status (`tar-risk-engine` restarting, `tar-web-console` unhealthy) — these belong
  to a different, unrelated stack and were not investigated as part of this HMT-2 incident record;
  flagged here only for completeness, not as part of this incident.
- `free -h` — 62Gi total memory, 30Gi used, 17Gi free, 32Gi available; **swap fully used
  (975Mi / 975Mi, 0B free)** — noted as a minor host-level observation unrelated to this incident's
  scope; not investigated further here.
- `df -h /srv` — 1.8T filesystem, 746G used, 994G available (43% used) — ample headroom, no disk
  pressure.

**No ongoing host issue was found that is causally connected to this incident.** The destructive
event was a one-time test-cleanup mistake against a live path, not a symptom of resource
exhaustion, and the host is currently stable.

## Evidence file inventory and SHA-256 hashes

All files below are copied (not moved) from dell-debian `/tmp/` into
`docs/incidents/2026-09-25-hmt2-canonical-store-data-loss-evidence/` in this repository, and
re-hashed in place after copying to confirm the copy is byte-identical to the source:

Files ending `.log` on dell-debian are committed here with a `.txt` suffix appended (repo
`.gitignore` line 23 blanket-excludes `*.log`); this is a filename-only change, content and
hashes are unchanged.

| File (as committed) | Size (bytes) | SHA-256 |
|------|-------------:|---------|
| `hmt2_forensic_report.json` | 52,848 | `7407e7f759094af56991f4733326d25a447a928bd96545edbfc539283c406bea` |
| `hmt2_forensic_per_session.json` | 730,664 | `10ab90be0bc034fbc00915a68bf4c425a0a9d6537c53403886d1a93962ce8563` |
| `full_regression_driver.log.txt` | 5,901 | `a6bf1194c52fae5d13103b42bf6835bfad14fd860a154059e4cf98e8efe15886` |
| `full-regression-progress.json` | 164,544 | `a5808a1246ab3cf0e9555de86ae20f0c2500eb61ba5f21d3f677c9ba639ba768` |
| `hmt2-drain-GC-2019-12-05.log.txt` | 2,875 | `087cc83b41f56a5813848136e497b6d1fcbdf9ff23ce2791a58759f89c474577` |
| `hmt2-drain-GC-2020-02-19.log.txt` | 2,877 | `1261baf3172307cb4b05a7c9589712766b58d3c07d815e58992a03378170b91b` |
| `hmt2-drain-GC-2020-03-04.log.txt` | 2,818 | `6e44e6a9d3a29d60387cc0a15da1fa4aeb155ac2d7d2cc2a40164976a93535a0` |
| `hmt2-drain-GC-2020-01-10.log.txt` | 0 (empty — interrupted/in-flight attempt) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

`sha256sum` was run twice — once against the dell-debian `/tmp/` originals, once against the
copies committed here — and the digests are identical in every case.

## Note on scope

No raw native market-data bytes, credentials, API keys, or account details are included in this
document or in the files committed alongside it — only counts, hashes, paths, and the small JSON
evidence/log files listed above (largest: 730,664 bytes).
