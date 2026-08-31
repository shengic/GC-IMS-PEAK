# GC-IMS-PEAK — Status & Session Handoff

**Last updated**: 2026-08-24 (session with Claude) — code tagged **v3.3**
*(Note on earlier entries: they say "tagged v3.1"/"v3.2", but no such git tags were
ever created — the repo went straight from `v3` to `v3.3`. Those names existed only
in file headers and in this file's prose. Treat pre-v3.3 version names as labels,
not as tags you can check out.)*
**Project root**: `C:\GC-IMS-PEAK`. The root has moved four times (`J:` → `K:` →
`F:` → `C:`), so **commands here use paths relative to the project root** rather
than a drive letter that goes stale silently. Older entries below still quote the
absolute path that was current when they were written; treat those as history.
**Purpose**: catch a new session up on where the Identify Workflow implementation
stands, what has been decided, what is testable now, and what to do next. Pair
with `GC-IMS_Identify_Workflow.md` (the authoritative spec) — this file is the
progress tracker, not the design.

> **⚠ This file covers the FIRST app only (`main.py`, 3.x).** There are now two
> other independent apps in this repo, both of which call this app's modules as a
> library and never modify them:
>
> - **Second app** — `main2.py` / `areas2.py`, version 1.x. Analyses a whole batch
>   at once and produces an area × file intensity matrix. Everything of its own
>   carries a `2` suffix. Tracker **`status2.md`**, usage `README2.md`, design
>   `Area_Matrix2.md`, tests `test2/`.
> - **Third app** — `compound_consensus/`, version 1.0 (2026-08-31). Takes the
>   repeat measurements of *one specimen* and consolidates their compound
>   candidates, ranked by how many replicates support each. Launch with
>   `python -m compound_consensus.app`. Tracker
>   **`compound_consensus/status.md`**, usage + measured numbers
>   `compound_consensus/README.md`, tests `test3/` (101).

### 2026-08-31 — three measurements from the third app that bear on decisions here

**None of these changed any code in this app.** They are recorded because they
answer, or re-frame, questions this file has been carrying.

**1. The RI scale is *not* offset on this batch — decision 3a is narrower than it
looked.** Taking the 14 compounds the operator annotated in Coffee-bean's
`.gasprj` and comparing *their* recorded RI against what this project's 6-point
ketone calibration computes at the same `Rt`: **median difference 0.0, every one
within ±5**. Two independent calibration paths agree. So whatever is unresolved
about the supplier table's column polarity, it does **not** show up as a shifted
RI axis here. Identification failures on this batch have a different cause (2).

**2. The real identification bottleneck is drift-library coverage, not RI.**

| library | rows | distinct compounds |
|---|---|---|
| `.ril` (RI) | 154,774 | 9,958 |
| `.iml` (drift) | 1,000 | 298 |
| **usable for RIPrel matching** | **201** | **84** |

A 2-D match needs both axes, so **97% of the RI library's compounds can never get
one**. Measured example: ethyl acetate — which the operator identified by hand —
has 469 `.ril` entries, 115 of them within ±5 of our measurement, and still cannot
be matched because it has no usable drift entry.

A further **213 compounds have drift data that is present but carries no
`DtMode`**, so `match_drift_rel()` skips them. Cross-checking the 55 compounds
that appear in both dialects: **51/55 (93%) agree within ±0.05, median difference
0.0028** — the undeclared rows are demonstrably on the same RIP-relative scale.
Accepting them would take coverage from 84 to 297 compounds (×3.5).

> **The user decided on 2026-08-31 that `library.py` and `library_data/` must
> never be modified.** That route is therefore closed. Recorded here so it is
> known to be *considered and declined*, not undiscovered — reopening it should
> start from these numbers. (An earlier claim in that investigation, that the
> `.iml` columns were misaligned, was **wrong**: both dialects share an identical
> 16-column layout and `Dt[a.u.]` parses correctly in all of them. The only
> difference is whether column 15 says `RIPrel`.)

**3. K0 is deliberately unused by the third app, and the measurement says that is
correct.** `resolve_calibrations_cached()` yields `k0_mode=standard_based`, but
attaching it makes matching worse: candidates **401 → 1507**, regions achieving a
2-D match **46 → 36**. `match_all()` prefers K0 whenever `k0_value` is present, so
it stops using RIPrel and more regions fall back to the RI-only path. This is a
second independent piece of evidence for **open decision 4** — the first being the
measured replicate RI spread (**median 0.29, 95th pct 1.10**) against a `±5`
window that has never been calibrated.

---

## TL;DR

- CLI-side identify pipeline (stages 1, 2, 3, **4**, 5, 6, 7 of the workflow) is
  **complete and end-to-end runnable**. Real `.mea` → `_peaks_identified.json`
  works. Verified on `260625_141215_STD.mea`, coffee-fermentation sample.
- **Stage 4 (RT→RI) is now built** (`calibration.py` + `reference_series.py`) and
  wired into the UI: selecting a folder silently resolves the STD calibration
  (auto-detecting the STD if needed), the peak table gains an **RI** column, and
  every heatmap's y-axis becomes a **linear Retention Index** axis (x-axis
  drift/RIP normalization untouched).
- STD identity **confirmed (draft.24)**: 2-alkanone (methyl ketone) series C4–C9,
  from the manager's `kintonemixed-C4-C9.xlsx` with matching CAS numbers. RI
  values now come from that table, and its `Dt` column also **fixed the anchor
  selection** (`match_anchors_by_dt()` replaces the spacing heuristic, which had
  been picking the wrong six peaks). One caveat remains: the table's column
  polarity is unconfirmed (~+302 vs non-polar literature), which would shift every
  RI by a constant. **Existing `_peaks.json` files need re-running** to pick up
  correct `ri`. See `ketone_RI_provenance.md`.
- UI (`main.py`) rollout: **Batches 1, 2, 3, 4, 5, 6 done** + Stage-4 wiring.
  Batches 7, 8 pending. (Batch 5 = the ▶ compound-match panel: clicking a peak's
  ▶ loads the `.ril`/`.iml` libraries and lists candidate compounds via
  `match.match_all`.)
- **339** pytest checks passing across `test/` + `test2/` + `test3/` (~9 s warm, ~20 s cold; added `test_calibration.py`,
  `test_rt_axis.py`, `test_baseline.py`).
- Project uses `.venv` at project root; install with
  `.venv/Scripts/python.exe -m pip install -r requirements.txt` from the project
  root. VS Code auto-detects this venv.
- **Retention-time baseline correction exists but is off by default** —
  `peaks.py --baseline` (AsLS). See the entry immediately below.

### 2026-08-24 (latest) — library selection now follows the RI scale in use

**Fixes open decision 9 without settling decision 3a.** Matching compares a
peak's RI against a library's RI; those must share a scale or a ±5 hit lands on
whatever compound's RI in *the other* scale happens to coincide — wrong reliably,
not noisily. Peak RI was on the supplier (polar-looking) scale while `.ril`
selection loaded non-polar libraries from the header's `POLARITY: np`.

Rather than hardcode a side, `library.detect_ri_scale_polarity()` votes the
calibration series' known compounds (CAS + RI) against `library_data` on both
phase families, and `identify.select_library_files()` selects `.ril` accordingly.
The supplier table votes **6/6 polar**; the non-polar 鱸魚 values vote **6/6
non-polar** — it follows the values in use.

Measured on the STD's own 2-butanone (identity certain): previously **407 hits
across 151 compounds with 2-butanone absent**; now **415 hits across 56
compounds, 2-butanone present**.

Also: `areas2.check_class_labels()` reports `.gasprj` `Class` values that
disagree with the filename — **3 of 15** in Coffee-bean (`E_1_2` tagged `E 1-1`,
`C_1_2` tagged `C 1-1`, `C_1_3` tagged `C 1-2`). Reported, never auto-corrected:
which side is right is a question about the raw data, and silently changing it
would be worse than leaving it visible. Group statistics rest on those labels.

RI values, anchor selection and the STD path are all unchanged.

### 2026-08-24 — evidence that the supplier RI table is a polar scale

**Documentation-only entry.** Reading `.gasprj` for the v3.3 RI tier surfaced a
`Compounds` block — operator-annotated peaks with CAS, RI, Rt, drift, and the
instrument serial. It bears directly on open decision 3a, which had been waiting
on the manager since 2026-08-12.

The short version: **two instruments running the same SE-54 phase carry RI scales
differing by +283 to +654 on seven shared compounds, and the size of the gap
tracks each compound's polarity** — ester +283 through to furan aldehyde +654.
A constant offset would mean a mis-set calibration; a polarity-ordered one is the
signature of a different stationary phase. The supplier table sits on the higher
(polar-looking) scale; the other instrument ran the same ketone standard ~300
lower, at exactly the `[589.4 … 1095.6]` values this project borrowed and then
discarded in draft.24.

**The user was shown this and chose to keep `library_data/` and the supplier
table unchanged, explicitly declining code changes off the back of the
`.gasprj`/`.mea` findings.** Nothing was modified — not `ri_values`, not anchor
selection, not library selection. Full analysis in `ketone_RI_provenance.md` §2
(four numbered pieces of evidence); consequences in open decisions 3a and **9**
(the RI scale and the `.ril` library polarity currently disagree).

Two things a future session should not have to rediscover: the discrepancy is
**not** a constant, so no single correction fixes it; and the evidence being
strong is *not* the same as the question being closed — the user decided to hold,
which is a different state from "unknown".

### What changed on 2026-08-24 — RI without an STD, from the folder's own `.gasprj`

**Follow-on from the entry below.** Once it was clear `藝妓咖啡` could only produce
RT matches, the question became whether RI is obtainable there at all. **From the
measurement alone: no** — RI is defined by interpolating against compounds of
known RI, and GC-IMS has no MS, so peak identity cannot come from the data. That
is the same gap the workflow calls the one thing genuinely blocking the chain.

**But VOCal had already stored an RI scale for that batch.** Both `.gasprj` files
in the folder carry a top-level `RI_Normalization` block —
`{ColNormY: RI, ColNormX: log10(Rt), ColNormisLog: true}` — which is *exactly* the
form this module's §第四階段 already uses (Kovats log, piecewise linear). So the
points drop straight in as anchors with no conversion.

**New tier `vocal_project_table`, placed between STD and registry:**

```
(a)  batch_own_std          folder has a usable STD          ← unchanged, still wins
(a2) vocal_project_table    the folder's own .gasprj         ← NEW
(b)  borrowed_from_registry same instrument|column|method
(c)  unavailable            → RT fallback
```

Above the registry deliberately: `.gasprj` is **this batch's own** scale (same
instrument, column, method, same run), the registry holds **another batch's**.
Both rank below the STD, because only that path has anchors this project can name
and check.

Measured across all four `GAS/` folders — STD still wins wherever one exists, and
the two folders that had no RI now have one:

| folder | STD | .gasprj | ri_mode | anchors |
|---|---|---|---|---|
| Coffee-bean | 1 | 1 | `batch_own_std` | 6 |
| 嘉義大學＿咖啡發酵 | 2 | 0 | `batch_own_std` | 6 |
| 海洋大學…鱸魚 | 0 | 3 | `vocal_project_table` | 6 |
| 藝妓咖啡 | 0 | 2 | `vocal_project_table` | 182 |

(The 鱸魚 `.gasprj` yielding exactly 6 anchors is a nice confirmation — those are
the original points whose RI values `ketone_RI_provenance.md` records as having
been *borrowed* from that project.)

**What the table is, and is not.** It is VOCal's **resampled** curve — 203 points
on a uniform 0.01 grid in `log10(Rt)`, not the original anchors. Which standard
produced it, how many points it really had, and its column polarity are **not
recoverable from the file**. So `assumed_unverified=True` with its own label,
`vocal_project_table_anchors_not_recoverable` — a different reason from ketone's
(that one is "polarity unconfirmed", this one is "provenance unknown entirely").

**The negative-RI tail is trimmed at the Kovats floor, not at a tuned threshold.**
The short-RT end is VOCal extrapolating below its first real anchor and goes
badly wrong — 13 points below zero, minimum **−631**. Methane is RI 100 *by
definition* on the Kovats scale, so anything under it is not a small RI but a
meaningless one; `KOVATS_RI_FLOOR = 100` cuts the leading run and
`n_points_dropped_below_kovats_floor` records how many went (203 → 182 on this
batch, leaving Rt 14.4–931 s). A slope-based rule was considered and lands in
almost the same place (16.2 s vs 14.4 s) but would have been a tuned number;
the definitional one is defensible without measurement.

**Trimmed out of the anchor range, not deleted** — peaks below it still get an RI
by boundary extrapolation and are automatically flagged `ri_extrapolated`, reusing
the existing extrapolate-and-flag machinery rather than inventing a second one.
Same for the other end: the table stops at Rt 931 s while this batch's RT axis
runs to ~1800 s, so late peaks extrapolate and are flagged (verified: 1200 s and
1800 s both flag, 500 s does not).

**`ColNormisLog: false` is refused rather than guessed.** That would mean X holds
`Rt` rather than `log10(Rt)`; guessing wrong yields a wholly displaced but
normal-looking RI axis, and there is no such sample on hand to verify against.

Implemented as `read_gasprj_ri_table()` / `scan_folder_for_gasprj()` /
`build_from_gasprj()` in `calibration.py`, plus a `vocal_project_table` entry in
`reference_series.py` — the series mechanism took it without any change to the
interpolation code, which is what that indirection was for. The UI status line
now names the source (`182 points from …gasprj`) instead of the hardcoded
"ketone anchors", which would have been wrong for this path. 6 tests in
`test_calibration.py`, including STD-outranks-`.gasprj` and the Kovats trim.

**Still open**: this makes an RI axis available, it does not make it *verified*.
Running an STD on that instrument and method remains the only route to
`batch_own_std` there. Also note `borrowed_from_registry` is still effectively
dead code — no registry file exists, nothing in production calls `save_registry()`,
and `main.py` never passes `dims`, so tier (b) cannot fire from the UI at all.

### What changed on 2026-08-24 (later) — ⚠ the GC column was showing RT under an "RI" heading

**Found by the user asking a good question**: `GAS/藝妓咖啡` has no STD, so how was
the GC column producing values at all? It shouldn't have been — not RI ones.

**1. The GC column silently degrades to retention-time matching.**
`match.match_all()` uses RI only when `peak["ri"]` is not None; otherwise it falls
through to `match_rt()`, comparing the peak's `retention_s` against the library's
`Rt[sec]` at ±5 s. Both land in the same `gc_matches` list and rendered
identically, while the column heading was the hardcoded string `"GC (RI)"`. So the
table displayed **seconds under an RI label**, with the RI column itself showing
`—` right next to it. Same class of silent fallback as the `.npz`/`mea_source` bug
and the RT-axis version marker.

**Why it matters here specifically**: retention time is not transferable — that is
the entire reason RI exists — and this batch differs from the library's conditions
on all three axes that move RT.

| | 藝妓咖啡 | 嘉義大學＿咖啡發酵 |
|---|---|---|
| instrument serial | 1H1-00088 | 5H4-00123 |
| column | FS-SE54-CB-0.5, 15 m × 0.32 | FS-SE-54-CB-1, 30 m × 0.53 |
| method | COFFEE_30 | COFFEE-40RAW |
| RT span | 1800 s | 3000 s |

Measured on that folder's real peaks: every peak drew **9–24 library hits inside
±5 s**, with implausible nearest matches (2,6-dichlorophenol at Δ0.01 s,
hexamethyldisiloxane — which is column bleed — methanol at RT 72 s). With 1000
`.iml` rows spread across the RT range, ±5 s almost always finds *something*; a
small Δ means numeric proximity, not identification.

**Fixed by labelling, not by removing the fallback** (user decision): the heading
becomes **`GC (RT s)`** when the RT path is in use, cells carry the ` s` unit, the
status bar states that RI is uncalibrated and RT is not transferable, and the ▶
panel — where compound *names* appear — gets an explicit warning. The fallback
itself is legitimate when library and sample really do share a method, and only
the user can know that. `_gc_column_dimension()` reads the actual
`gc_dimension`, preferring `ri` if any peak has it.

**2. That folder was also loading 0 `.ril` rows — invisibly.** Its `GC Column`
header uses an older layout with **no `POLARITY:` field**
(`FS-SE54-CB-0.5, 15m x 0,32ID`), so `parse_gc_column_header()` returned
`polarity=None`, the name matched no `.ril` filename, and the polarity fallback
never fired → `strategy='none'`, zero rows. The RI dimension had no library at
all, and nothing said so.

`infer_polarity_from_column_name()` now maps the stationary phase to a polarity
when the header omits it (SE-54/DB-5/HP-5/… → `np`; WAX/Carbowax/PEG/FFAP → `p`),
comparing on the separator-stripped name so SE-54 / SE54 / SE 54 all hit.
**The mapping is not this project's chemistry judgement** — the same instrument
writes `POLARITY: np` for SE-54 in its own newer-format header, so this follows
the instrument's own labelling. Explicit header values always win and are never
overwritten; `polarity_source` (`header` / `inferred_from_column_name` / `None`)
travels into `identify.py`'s `library_summary.selection`, per the project's rule
that a derived value must stay distinguishable from a measured one. An
unrecognised phase infers nothing — loading the *wrong* polarity's `.ril` is worse
than loading none, because it produces plausible-looking numbers off the wrong
scale. **Measured: 0 → 13 files / 117,329 `.ril` rows on that folder.**

**3. Fixed an intermittent Tk test skip while in there.** `tk.Tk()` occasionally
raised during the suite, and the skip reason said "no Tk display available" —
indistinguishable from a genuinely headless box. Root creation in isolation is
fine (300 cycles, no failure), so it is transient; `_tk_root_or_skip()` now
retries once and reports the real `TclError` if it still fails. 10 consecutive
suite runs clean afterwards. This was likely also the cause of the intermittent
skips recorded on 2026-08-12 further down this file.

### What changed on 2026-08-24 — STD marked in the file list; stale paths swept

**The MEA file box now marks the calibration STD instead of listing it as an
ordinary sample**: sorted last, grey italic, `· STD` appended, a status-bar note
on selection, and `Generate Report` declines it (reporting the STD would be
circular — it *is* the scale the samples are reported against).

**It is marked, not hidden**, which was the actual decision to make here. The STD
is a real measurement that goes through the same detection path as a sample —
`_start_folder_calibration()` runs `peaks.py` on it and the anchors come from its
own `_peaks.json` — and opening it is currently the only way to see which six
peaks became the anchors. That assignment is the least-verified step in the whole
RI chain (open decision 3 below) and is exactly what the anchor off-by-one turned
on. Hiding it would also turn a failed STD detection into "no RI axis" with
nothing to inspect.

**The marking follows the header, never the filename.**
`calibration.scan_folder_for_std()` decides by `Sample == "STD"` because
operators mistype names; re-deciding it in the UI by filename would let the list
disagree with the file the calibration actually used, in either direction. Both
directions are locked by `test/test_file_operations.py::TestSTDFileListing`
(4 tests, no Tk display needed — they drive the real `populate_file_list()`
against a fake tree).

**Also swept**: the project root moved `F:` → `C:`, so every doc command is now
relative to the project root instead of naming a drive letter that goes stale
silently (this is the fourth move: `J:` → `K:` → `F:` → `C:`), and `baseline.py`
— which had landed in the code with no mention in any document — is now recorded
here, in `README.md` and in `GC-IMS_Pipeline_Implementation.md`.

### What changed after v3.2 — retention-time baseline correction (AsLS), opt-in

**New module `baseline.py`** (commit `1f4ca59`): asymmetric least squares
(Eilers & Boelens 2005) along the retention-time axis, one drift channel at a
time. **Nothing existing changes behaviour** — `peaks.py` needs an explicit
`--baseline` for any of it to run, and the parameters used are recorded in
`_peaks.json` under `params["baseline"]` (`None` = not corrected), so corrected
and uncorrected results cannot be confused after the fact.

**Why it was needed.** The only background handling the project had was "floor =
85th percentile", which is a *horizontal* line and cannot subtract a *sloped*
one. GC-IMS drifts in the RT direction by construction: the temperature ramp
increases column bleed over time and lifts the baseline with it. `prominence =
peak height − saddle height` is a relative quantity and survives a uniform
offset, but **a baseline that slopes within one peak's width lifts the saddle**,
so prominence is systematically underestimated — and the later a peak elutes,
the more it is suppressed.

**λ is measured, not borrowed.** gc-ims-tools uses `1e7`; on this project's data
that eats wide peaks, because AsLS treats anything smoother than the baseline it
permits as baseline. The widest real peak in `260625_141215_STD` has an RT-direction
σ of **222 rows** (median 63) — at `1e7` it loses 37% of its height. Scanning λ at
the real row count gives **1e11** (widest peak keeps 99.9%, baseline residual
median 0.44 against a baseline of order 100). **Two premises to re-measure if they
change**: peak width (new column or new temperature program) and the RT row count
(λ's effect depends on sampling density — verified it behaves differently at
n=6000 vs n=20413).

**Measured effect on the final peak set: small.** 47 → 46 peaks, all six RI
anchors retained, prominence threshold 98.5 → 98.1 — as expected, since
prominence already cancels a slowly varying background. What actually changes is
the **candidate pool**: floor 218.6 → 64.2, raw local maxima 277k → 192k. **For
peak-volume integration (quantitation) it is a prerequisite**, not a nicety —
integrating without it counts the pedestal as signal.

**Implementation notes.** Two things differ from the reference implementation: a
banded pentadiagonal solver (`solveh_banded`, O(n), verified against a sparse
solve in `test_baseline.py`) and `row_stride` downsampling — the baseline is a
low-frequency quantity by construction, so solving it every 8th row and
interpolating back takes the real file from ~107 s to ~16 s. `correct_rt_baseline()`
does not mutate its input, and `asls()` returns the **baseline itself** rather
than the corrected signal, so callers can inspect or overlay it.

6 tests in `test/test_baseline.py` cover the banded-vs-sparse equivalence, peak
height preservation above a local baseline, flat input left alone, the λ floor
set by the widest real peak, stride-invariance, and non-mutation of the input.

### What changed on 2026-08-12 (later, tagged v3.1) — RT axis was wrong; K0 solved by decompiling VOCal

*Workflow spec is now **draft.26**. Dated entries below quote the draft number
current at the time of writing and are left as-is.*

**1. ⚠ The retention-time axis was short by 16.7% — fixed, and versioned.**
`rt_step_ms` was `averages × trigger_repetition`; it is `(averages + 1) ×
trigger_repetition`. Four independent confirmations, recorded in full at the top
of `readGAS.py`:

| evidence | result |
|---|---|
| method total length | STD 42.87 → **50.01 min**; reference FM_1 25.70 → **29.9985 min** — both land on whole minutes only with the +1 |
| manager's table `Rt[sec]` | systematic −16.7% → residuals **−0.48%…+0.97%** |
| `gc-ims-tools` 0.1.10 `Spectrum.read_mea()` | uses `chunk_averages + 1`; same `.mea` gives a bit-identical 20413×3150 intensity matrix and identical drift axis — **only RT differed, by exactly 7/6** |
| VOCal `BufferedMEA.java:316` (decompiled) | same `+1` |

Because this changes what `retention_s` *means*, `readGAS.RT_AXIS_VERSION = 2`
now travels into `.npz` and `_peaks.json`, and `peaks.warn_if_stale_rt_axis()`
warns when an old artefact is loaded. Silent old/new mixing was the real hazard:
RT off by 16.7% with nothing anywhere raising. **RI is unaffected** — anchors and
query shift together by `log10(7/6)` and piecewise-linear interpolation is
invariant under an x-shift (measured difference ~1e-13). All anchor RTs quoted in
the 2026-08-12 entry below are already on the new axis.

**2. K0 — both open decisions closed by decompiling VOCal.** The `.jar`s that
ship with the instrument software were decompiled (interoperability RE of
software the user owns) and answered decision 1 outright:

- Drift-tube temperature is **`Start temp 1`** (45 °C).
- Pressure is the **sum**: `P_mbar = 10 × (Start ambient pressure + Start
  pressure EPC IMS)` = 10 × (100.393 + 1.45) = **1018.43 mbar**. Neither field
  alone — which is why picking one had looked arbitrary.
  Source recorded in the returned provenance dict: `BufferedMEA.java:313`.

Decision 2 is closed too, from data already on disk. `GAS BASE 3H_IMS K0.iml`
(G.A.S. official base library, `DtMode=1/K0`) supplies known K0 for the six
ketones, so **the instrument constant can be solved from our own STD**:
`calibration.derive_k0_instrument_constant()` solves `IC = K0_ref · t · U` per
anchor and rejects the result if the spread is too wide.

| | value |
|---|---|
| instrument constant | **25.0808** |
| CV across 6 anchors | **0.133%** |
| residual vs `raw_parameters` | that path is **+3.5%** off — 43% of one homolog's spacing, i.e. unusable for matching |

That last row is the point: `raw_parameters` is arithmetically correct now but
still not accurate enough to identify a compound, whereas the standard-based
constant is. `build_k0_profile_from_std()` returns a `dt_convert`-compatible
profile.

**3. Anchor matching now uses the table's `Dt`.** `match_anchors_by_dt()` gives
6/6 with mean |Δ| 0.00273 and verifies RT stays monotonic with carbon number,
falling back rather than silently accepting a table that doesn't fit. See the
entry below for why the spacing heuristic it replaced was untrustworthy.

**4. Bug: RI calibration was silently lost whenever a `.npz` was the input.**
`peaks.load_surface()` set `meta["source"]` to whatever path it was handed, so
starting from a `.npz` resolved the batch folder to `results/` — which contains
no STD — and RI came back `unavailable`. The y axis fell back to retention time
**with no error**, producing images indistinguishable from RI-calibrated ones but
in a different coordinate system. `read_mea()` now records the original `.mea` in
`axes["source"]`, `export_npz()` stores it as `mea_source`, `load_surface()`
prefers it, and a missing calibration under an explicit `--ri-series` warns on
stderr. Older `.npz` lack the field and keep the old behaviour.

**5. UI.** Heatmap header gains an **ⓘ 軸說明** button opening a Chinese
explanation of both axes; its text comes solely from
`calibration.axis_explanation()`, so it cannot drift from the actual calibration.
An earlier attempt in this session baked the same summary into the PNG — removed,
because it covered data and matplotlib's default font restricts it to ASCII.
`calibration.ri_slope_summary()` backs the RI half: it reports the global slope
(**804 RI per 10× RT**), the per-segment range (**716–894**, the five segments
differ by 22%), *and* the error a single straight line would cause (**14.5 RI**,
~3× the ±5 match tolerance) — so the summary figure cannot be mistaken for a
usable conversion factor. Also fixed: the interactive peak view was titled `bg`
(the caption table had no key for that `kind`, so the fallback printed the
internal code name); circles now clear when the file changes; the peak view is
the default on selecting a `.mea`; the peak popup shows the real
`peak["ri_caveat"]` instead of a hardcoded string; sub-windows re-raise on
re-click.

**6. Verified circle placement against the real renderer.** Circle positions had
only ever been unit-tested. Binding the real `_peak_to_image_xy()` to a shim and
measuring against `_bg.png` gives a **median offset of 1.2 px**; the apparent
outliers were the ±14 px search window catching neighbouring monomer/dimer peaks,
not misplacement.

**7. Housekeeping.** `results/` cleaned (62 files / 374 MB; the seven
`_peaks_state.json` kept), then the whole batch re-detected on the new RT axis.
`kintonemixed-C4-C9.xlsx` and `Gemini.pdf` are now tracked — the xlsx is the
authoritative source for RI values and anchors and had been sitting untracked.

### What changed on 2026-08-12 — supplier table lands; identity solved, calibration found to be wrong

**A compound table arrived from the manager (`kintonemixed-C4-C9.xlsx`) and it
changed three things at once.** Full analysis in `ketone_RI_provenance.md`;
workflow is now **draft.24**.

**1. Compound identity — SOLVED.** Six rows, each with a CAS that checks out
(78-93-3 / 107-87-9 / 591-78-6 / 110-43-0 / 111-13-7 / 821-55-6). The standard is
the **2-alkanone (methyl ketone) series, C4–C9**. This closes what workflow §4
point 7 called "the one gap genuinely blocking the whole chain" — and it closed
exactly as predicted: not from our own data (GC-IMS has no MS) but from an
external document. Identity went through three revisions today — draft.19
inferred 2-alkanones from the DT_rel ladder, draft.23 withdrew that as
unsupported, draft.24 confirmed it from the table. Series key stays `ketone`
(the mix's product name); member labels are the confirmed compound names plus
CAS/formula/MW.

**2. RI values replaced — with an unresolved caveat.** `ri_values` are now the
table's (916.8372 … 1392.9), replacing the values borrowed from the 鱸魚 project
(589.4 … 1095.6). The two differ by **+289…+327 (mean +302, strikingly uniform
across all six)** — the signature of a different column polarity, not noise. The
table's 2-butanone (916.84) lands almost exactly on the NIST **DB-Wax (polar)**
value 917–950 that workflow draft.19 had already recorded, while this batch's
column is `FS-SE-54-CB-1 / POLARITY: np` (**non-polar**, where 2-butanone belongs
near 589). If the table is polar-column data, every RI here is ~300 too high.
The user was told and chose to adopt the table's values, so `assumed=True`
remains and `confidence` is now
`supplier_table_column_polarity_unverified` — the flag no longer points at
compound identity, it points at column polarity.

**3. ⚠ The table's `Dt` column exposed an off-by-one in the anchor→carbon
assignment.** Matching the table's `Dt [a.u.]` (RIP-relative, same unit as our
`drift_relative`) against this batch's six anchors:

| assignment | mean \|Δ(Dt)\| |
|---|---|
| current: 6 anchors = C4…C9 | 0.1264 |
| shifted by one: anchors 2–6 = C4…C8 | **0.0028** |

45× better, five points agreeing to 0.0008–0.0054. So **RT 389.7 s is C4**, not
329.6 s, and **RT 329.6 s (DT_rel 1.104, the strongest peak in the image) is not
in the C4–C9 series at all**.

**Then a second pass — searching all 37 detected peaks by `Dt` instead of assuming
the anchors were among the six already chosen — found all six compounds,
including C9:**

| compound | table Dt | measured RT (s) | measured DT_rel | Δ |
|---|---|---|---|---|
| 2-butanone | 1.23938 | 389.7 | 1.234 | 0.0056 |
| 2-pentanone | 1.35521 | 467.0 | 1.356 | 0.0007 |
| 2-hexanone | 1.49035 | 609.5 | 1.487 | 0.0036 |
| 2-heptanone | 1.61390 | 813.4 | 1.613 | 0.0007 |
| 2-octanone | 1.73359 | 1107.2 | 1.737 | 0.0032 |
| **2-nonanone** | 1.85714 | **1523.4** | 1.854 | 0.0027 |

Both axes strictly increasing, mean |Δ| 0.0027. **The correct anchor set is
`[389.7, 467.0, 609.5, 813.4, 1107.2, 1523.4]` — still six points.** An earlier
version of this entry said C9 was never detected and the batch had only five
anchors; that was wrong and is retracted. It also retracts workflow draft.16
point 5's measured claim that "signal only spans RT 258–949 s, everything beyond
is flat background": there are three peaks above 1000 s (1523.4 / 1526.0 /
2853.4) and C9 is one of them.

**Why the ladder heuristic picked the wrong six** — worth recording, because both
of its scoring criteria pointed at the wrong answer: the wrong set has DT_rel
spacing std **0.0034** vs the correct set's 0.0046, *and* it contains the
prominence-4508 peak so it wins the prominence sum too. draft.18 took the 0.0034
as strong evidence; it turns out spacing uniformity cannot tell a genuine homolog
ladder from a coincidentally even mixture. Carbon assignment needs external
evidence — which is exactly what the table provides. (draft.18's other call,
keeping 334 over 347.9, was right; the reasoning was just incomplete.)

**Fixed in code (same day).** New `calibration.match_anchors_by_dt()` matches the
table's `Dt` against **all** detected peaks (strongest wins when several share a
Dt) and checks that RT stays monotonic with carbon number — non-monotonic means
"this table doesn't fit this STD" and falls back rather than silently accepting.
`build_from_std_peaks()` now prefers Dt matching whenever the series carries
`dt_values`, falling back to `select_homolog_ladder()` for series that don't (e.g.
`n_alkane`). `anchor_selection.mode` gains `dt_matched`; the match detail is kept
under `anchor_selection.dt_match` for provenance. Verified on the real STD:
`dt_matched`, 6/6, mean |Δ| 0.00273, monotonic, anchors
`[389.7, 467.0, 609.5, 813.4, 1107.2, 1523.4]`.

Partial matches stay aligned — matching only 3 compounds takes *those* 3 RI values
by `_dt_index`, not the first 3. **RT coverage now extends to 1523.4 s instead of
1107.2 s**, so fewer peaks are flagged `ri_extrapolated`. **Every existing
`_peaks.json` `ri` needs re-running.**
`test_ketone_dt_values_expose_the_anchor_off_by_one()` is kept as a regression
guard: it records the arithmetic showing *why* the spacing heuristic was dropped,
so a future attempt to reinstate it has the counter-evidence ready.

**4. Evaluated `gc-ims-tools` (prompted by a Gemini write-up the manager shared).**
Not adopted — but three findings are worth keeping.

- **The Gemini write-up's code examples are not real.** Import name is **`ims`**,
  not `gc_ims_tools`; `Dataset.from_folder()`, `align_rip()`,
  `cut_retention_time()`, `get_roi_volumes()` do not exist anywhere in the
  package; `gcit.PCA` / `gcit.PLSDA` are really `PCA_Model` / `PLS_DA`. Real API:
  `Dataset.read_mea/read_zip/read_csv/read_hdf5`, `interp_riprel`, `rip_scaling`,
  `align_ret_time`, `cut_rt`, `cut_dt`; `Spectrum.find_peaks`, `detect_peaks`,
  `plot_persistence`, `watershed_segmentation`, `riprel`, `calc_reduced_mobility`.
  Verified by downloading the 0.1.10 wheel (no install — it pulls 24 packages
  including scikit-learn, pandas, xarray, numba and opencv, which this project
  deliberately does not carry).
- **The *capabilities* it described are real, and two corroborate our own code**:
  `calc_reduced_mobility()` (see open decision 5 below) and `riprel()`, which
  takes `argmax` of the first retention row exactly as our `rip.find_rip()` does
  (we additionally skip the first 200 samples, following VOCal).
- **`Spectrum.watershed_segmentation()` and `plot_persistence()` confirm the
  approach we already took.** Our `compute_prominence()` union-find flood *is*
  watershed, and it already computes the merge saddles — it just discards them
  (noted in workflow §第七階段 R005). ROI segmentation + volume integration, i.e.
  quantitation, would be an extension of existing code rather than a new
  algorithm. Much cheaper than the MCR-ALS/PARAFAC2 route the blueprint shelved.

Not adopted because: our `.mea` parsing is already reverse-engineered and working,
and the package's other half (PCA/PLS-DA/RF/SVM chemometrics) is explicitly ruled
out by `GC-IMS_Peak_Finding_Workflow.md` §9 "不建議的方向" and §1.3.

The rest of the pass found places where the docs asserted something the code no
longer did (or never did). Corrections:

- **CLI and UI no longer disagree about which `.iml` to match against.** The UI
  matched every `.iml` (correct: `DtMode=="RIPrel"` drift is an instrument-level
  dimensionless ratio, not GC-column-specific), while `identify.py` filtered them
  by GC column/polarity — same data, two different IMS candidate sets. Both now
  call one shared `identify.load_libraries()`; `select_library_files()` returns
  all `.iml` and keeps the column-specific selection for `.ril` only (RI *is*
  column-dependent). **This changes CLI output**: more IMS candidates than before.
- **Drift-gas cross-check is actually wired in.** `filter_iml_rows_by_drift_gas()`
  existed but nothing called it, so workflow §第三階段 point 3 was never enforced.
  Now applied inside `load_libraries()` — with **conservative** semantics: only
  rows explicitly tagged as a *different* gas are dropped, untagged rows are kept.
  Measured reason: of the 201 `RIPrel` rows in `library_data/`, only 162 carry a
  gas tag; the other 39 come from the older `.iml` layout whose columns shift.
  Strict filtering would silently discard those 39 real candidates.
- **`identify.py` gained `--drift-tol`** and now reports `ims_dimensions_used` +
  the drift tolerance in `match_tolerances`. Before, the IMS dimension it actually
  used (RIPrel, since K0 is dormant) had no CLI knob and was absent from the
  output's own tolerance record.
- **`main.py` paths are absolute.** Every `results/...` was relative, so the app
  only worked when launched with cwd == project root. Now built from `RESULTS_DIR`.
- **`ImageViewerDialog` drag now repaints** (it updated the pan offsets but never
  redrew); the double `display_image()` in its wheel handler is gone. Dead-ish
  code — the popup is not currently wired into the main flow.
- **`dt_convert.py` documents the K0 / 1/K0 reciprocal asymmetry** between its two
  modes (open decision 5 below). Math deliberately unchanged.
- Docs corrected: `peak_with_number.py`'s OOM bug is marked fixed (it was fixed in
  ver.02 but the 待實作清單 still said ❌); test count 125/91/77/70 → **143**;
  RI tolerance ±10 → ±5; the "n-alkane / Van den Dool–Kratz" open decision replaced
  with the actual one (borrowed ketone RI → verified); workflow §第七階段 now
  states that the `--prom-frac` migration was never done and why it cannot be done
  as written.

### What changed on 2026-07-27 (workflow draft.16–21, tagged v3) — Stage 4 RT→RI

**Stage 4 is implemented.** `calibration.py` + `reference_series.py` convert a
peak's retention time to a Retention Index, and the whole thing runs
automatically from a folder's STD.

- **Compound identity (draft.19, corrected by draft.23):** the STD is **C4–C9
  ketones**, *not* n-alkanes. Their RI is not `100·n`. *(draft.19 recorded this as
  "methyl ketones, 2-butanone → 2-nonanone"; that structural assignment was this
  project's inference, not user-supplied, and draft.23 withdrew it.)*
- **RI values (ketone_RI_provenance.md):** the 6 values
  `[589.4, 688.6, 784.2, 892.2, 996.5, 1095.6]` are **borrowed** from another
  project's `.gasprj`, cross-referenced with literature (<11 RI). Marked
  `assumed_unverified` / `confidence="borrowed_cross_referenced"` and carried on
  every peak — **not** verified, not self-calibrated. Only the RI (Y) is
  borrowed; the RTs (X) are this batch's own STD peaks.
- **Auto 6-anchor selection:** `select_homolog_ladder()` picks the 6 ketones out
  of the STD's ~9–14 detected peaks via the monotonic DT_rel ladder — no template
  RTs needed. Recovers the documented 6 (282/334/400/522/697/949 s), spacing std
  0.0034.
- **Math (RT_to_RI_normalization_math.md, draft.20–21):** piecewise-linear in
  `log10(RT)`; out-of-range = **extrapolate + flag** (`ri_extrapolated`), never
  clamp. One shared `make_rt_to_ri()` used by both peak values and heatmap axis.
- **Folder resolution + cache (draft.17):** `resolve_ri_calibration()` is 3-tier
  (`batch_own_std` / `borrowed_from_registry` / `unavailable`), symmetric with
  Stage 2 `k0_mode`; session + `_folder_calibration.json` sidecar cache reused
  across files. *(A fourth tier, `vocal_project_table`, was added 2026-08-24 —
  see the entry at the top of this file. This 2026-07-27 entry is left as it was
  written.)*
- **Heatmaps show linear RI:** rows are resampled uniform-in-RI so the RI axis is
  linear (not log). Applies to the main canvas backdrop (`_bg.png`), the numbered
  overlay, and the original heatmap. Circles are placed in RI (`_bg.json` records
  `y_axis`). **x-axis drift/RIP normalization is untouched.**
- **UI:** folder-select spawns a background thread that resolves (and, if the STD
  isn't detected yet, detects it) the calibration once and caches it; the peak
  table gains an **RI** column (`*` = extrapolated); `identify.py` carries
  `ri_mode` / provenance into `_peaks_identified.json`.
- New modules/docs: `calibration.py`, `reference_series.py`,
  `RT_to_RI_normalization_math.md`, `Stage4_code_reference_for_CLI.md`,
  `ketone_RI_provenance.md`, `test/test_calibration.py`.

**Still open:** RI is borrowed/assumed — upgrade to verified per
`ketone_RI_provenance.md`. A fresh folder whose STD isn't detected yet
shows retention time until the background STD detection finishes.

### What changed on 2026-07-22 (workflow draft.14–15, tagged v2.1)

**Selection order — the load-bearing change.** `R004` (RIP band) and the new
`R006` (`drift_relative ≤ 1.0`, "faster than the reactant ion → not an analyte")
are **mandatory and applied before the prominence gate**. That gate is relative
(`prom_frac × max_prominence`) and the RIP is normally the strongest feature in
the image, so leaving it among the candidates inflated the threshold ~3.3× and
silently discarded real peaks. Measured on `A_1_3`: threshold 101.0 → 30.9;
peaks 37 (14 of them RIP artefacts) → 31, all with `drift_relative` > 1.0.
Applying these rules *after* detection makes the circles disappear but does not
recover the suppressed peaks — `test/test_select_from_maxima.py` fails if anyone
moves them back.

- "Mandatory" is enforced in `rules.load_config()`/`save_config()`, not in the
  UI, so hand-editing `rules_config.json` cannot bypass it. Their *params* stay
  editable — and changing those does move the baseline, which renumbers.

**Rules mark, they no longer remove.** `rules.mark_rules()` sets `rule_active`
and keeps every peak. The UI shows a rejected peak greyed instead of deleting
it, so the user can see what a rule did and override it; a manual pick beats the
rule and is drawn with a dashed ring. `apply_rules()` keeps its old filtering
contract for `identify.py`, where spending match compute on unwanted peaks would
be pointless.

**Numbering.** `peak_id` is assigned once, immediately after the mandatory rules
— that set *is* the baseline. Optional rules only mark, so numbering never
reshuffles and a gap is informative ("R001 dropped #19"). Verified: 0 renumbered
peaks across three optional-rule combinations.

**Per-peak selection (Batch 3).** `toggle_peak()` backs both the circle click
and the table's On checkbox. State persists to `<name>_peaks_state.json` keyed
by `(rt_index, dt_index)` — **not** `peak_id`, which is a prominence rank within
the baseline and is reassigned whenever `R004.half_width` / `R006.boundary`
changes; a selection saved against it would silently reattach to another peak.

**Canvas (Batch 6).** Circles and numbers are native Tk items over the
circle-free `_bg.png`, positioned from `_bg.json` (written by whoever renders
the PNG, so geometry always matches the image). This fixed a long-standing
offset: the old `highlight_peak_on_overlay()` assumed the plot area filled the
whole PNG, ignoring matplotlib's margins (8.5 % on the left alone), so markers
never sat on their peaks. Platform note: `-outlinestipple` is silently ignored
on Windows, so the circle changes colour while the number uses `-stipple`.

**New artefacts per `.mea`.** `_maxima.npz` (all 252k raw maxima, 2.4 MB) lets
the Rules panel recompute the whole funnel in ~4 ms instead of re-running the
83 s detection. `_bg.png` + `_bg.json` back the interactive canvas.

**Reuse and disk.** Selecting a `.mea` that already has an `.npz` now *asks*
whether to reuse it rather than deciding silently; `peaks.py --bg-only` rebuilds
the display background from the `.npz` alone. The full-res CSV became opt-in
(`readGAS.py --write-csv`): the seven existing ones (8.39 GB, read by nothing)
were deleted with the user's explicit approval, taking `results/` from 8.5 GB to
~245 MB. `.mea` files are never modified or deleted by any part of this project.

**Existing results are stale**: any `.mea` detected before this change has no
`_maxima.npz` / `_bg.png`, and its `_peaks.json` uses the old numbering. Only
`260623_161351_A_1_3` has been regenerated — re-run detection per file.

**Housekeeping decision**: no automatic cleanup of `results/`. Files stay until
the user says otherwise.

### ⚠ Editor hazard seen repeatedly this session

`main.py` was silently reverted **four times** by a stale IDE buffer being saved
over it, each time losing only the most recent edit. Twice it went unnoticed by
`pytest` (green at the time) because the lost code was UI wiring the suite does not
exercise — it was caught only by the manual smoke script. If work seems to
vanish, check `git diff` before re-deriving anything, and keep `main.py` closed
in the editor while another process is editing it.

---

## Pipeline status (workflow stages)

| # | Stage | Module | Status | Notes |
|---|---|---|---|---|
| 0 | RT baseline correction | `baseline.py` | **Done — opt-in, off by default** | Not a workflow stage; it runs before stage 1 when `peaks.py --baseline` is given. AsLS per drift channel, λ=1e11 measured on this batch's widest peak (**not** gc-ims-tools' 1e7, which eats σ≈222-row peaks). Effect on the final peak set is small (47→46) because prominence is already relative; it changes the candidate pool (floor 218.6→64.2) and is a prerequisite for peak-volume integration. Parameters recorded in `_peaks.json` `params["baseline"]`. |
| 1 | RIP normalization | `rip.py` | Done | `find_rip()` + `attach_drift_relative()`. Integrated into `peaks.py` ver.03. RIP found at dt_index=680 (4.53 ms) on the test .mea. |
| 2 | K0 conversion | `dt_convert.py` | **Done — wired end-to-end (v3.2)** | Three modes: `standard_based` / `raw_parameters` / `unavailable`. T/P field mapping **resolved** via VOCal decompilation (`extract_raw_tp()`: `Start temp 1`; pressure = 10×(ambient + EPC)), so `raw_parameters` runs unaided — but lands +3.5% off, unusable for matching. `standard_based` is the usable path: `calibration.derive_k0_instrument_constant()` gives **IC = 25.0808, CV 0.133%** from the STD against `GAS BASE 3H_IMS K0.iml`. `resolve_calibrations_cached()` produces the profile alongside RI from the same STD, and both `identify.py` and the UI use it — measured `k0_mode=standard_based` on all 37 STD peaks, IMS match dimension flips RIPrel → `{'k0': 37}`. **No registry-borrowing tier by design**: `instrument_constant` is tied to this machine's drift-tube geometry and voltage. *(This row previously said "not yet wired / UI reports unavailable" — that was already false when v3.2 landed and contradicted open decision 2 below.)* |
| 3 | Library readers | `library.py` | Done | `.ril` 21 cols / `.iml` 16 cols. `source_file` provenance auto-propagates via dict copy. `resolve_data_dir()` priority chain: explicit arg → `GCIMS_LIBRARY_DIR` env → `<PROJECT>/library_data/` → legacy VOCal folder → None. **Selection is deliberately asymmetric**: `.ril` is column-specific (RI depends on the stationary phase), `.iml` is not (RIPrel drift is instrument-level) → all `.iml` are loaded. Drift-gas cross-check applied row-level, conservatively (only rows tagged as another gas are dropped; untagged kept — 39 of 201 RIPrel rows carry no tag). Both CLI and UI go through `identify.load_libraries()`. **v3.3**: `infer_polarity_from_column_name()` supplies the polarity when the `GC Column` header has no `POLARITY:` field (older layout, e.g. `FS-SE54-CB-0.5, 15m x 0,32ID`) — without it the polarity fallback never fired and the folder silently loaded **0 `.ril` rows** (measured 0 → 13 files / 117,329 rows). Explicit header values always win; `polarity_source` records which it was. |
| 4 | RT→RI conversion | `calibration.py` + `reference_series.py` | **Done — 4 tiers; STD values from the supplier table, column polarity unverified** | Anchors selected by **`match_anchors_by_dt()`** against the table's `Dt` (6/6, mean \|Δ\| 0.00273, RT-vs-carbon monotonicity checked), replacing the DT_rel spacing heuristic which picked the wrong six. `log10(RT)` piecewise-linear interp (extrapolate+flag), **4-tier** folder resolution + cache (STD → the folder’s own `.gasprj` `RI_Normalization` table → registry → unavailable). STD = C4–C9 2-alkanones (CAS-confirmed). RI values are the manager's table; `assumed_unverified` now flags **column polarity**, not identity (see `ketone_RI_provenance.md`). Wired into `identify.py` + UI; heatmaps show a linear RI y-axis; `axis_explanation()` backs the UI's ⓘ dialog. **v3.3**: folders with no STD now get RI from their own `.gasprj` `RI_Normalization` table (`vocal_project_table`, ranked above registry borrowing because it is *this* batch's scale) — measured `藝妓咖啡` 182 anchors, `鱸魚` 6. Negative-RI extrapolation at the short-RT end trimmed at the Kovats floor (methane = 100, a definitional line, not a tuned threshold). |
| 5 | Tolerance-window match | `match.py` | Done — **now 2-D** | `gc_matches` / `ims_matches` / `combined_matches` (intersect by CAS). GC = RI, **or the Rt fallback when no RI calibration exists** — that fallback is *not* transferable across instrument/column/method and the UI now says so (see the v3.3 entry above); it used to render under a "GC (RI)" heading. **IMS now works without K0**: `match_drift_rel()` compares `peak.drift_relative` vs library `Dt[a.u.]` where `DtMode=="RIPrel"` (drift relative to RIP — same quantity peaks carry). `match_all` prefers K0 if `k0_value` present, else RIPrel; reports `ims_dimension`. Combined = agree on both axes → collapses hundreds of RI-only hits to a few. Tolerances placeholder (RI ±5, Rt ±5s, drift ±0.05, K0 ±0.05); `identify.py --drift-tol` overrides the drift one. |
| 6 | Integration | `identify.py` | Done | CLI-runnable. Full pipeline: peaks.json → header → K0 → rules → library → match → `_peaks_identified.json`. Provenance carried through (`k0_mode`, `source_file`, `match_dimensions`, `gc_dimension`). |
| 7 | Rule engine | `rules.py` | Done | R001–**R006** registered. Three rule types (per_peak / per_peak_with_context / batch) + a `mandatory` flag. `mark_rules()` marks `rule_active` without removing; `apply_rules()` = mark + filter (kept for `identify.py`). R004/R006 are mandatory and applied **before** the prominence gate inside `peaks.py`. |
| 8 | Interactive UI (main peak view) | `main.py` | Batches 1–6 done, 7 partly (stipple); **8 is the only unimplemented feature** | See "UI batching" below. v3.3 also: the calibration STD is marked in the file list and refused by Generate Report; the GC column heading names the dimension actually in use. |
| 9 | Batch conversion | `batch_convert.py` (not yet) | Not started | Optional. |
| 10 | Compound-match panel | part of `main.py` | **Done (Batch 5), 2-D** | Auto-fills the table's **GC** (matched RI value — heading reads `GC (RT s)` and cells carry the unit when the RT fallback is in use), **IMS** (matched RIPrel or K0 value), and **GC×IMS** (compound agreeing on both axes) columns; ▶ opens the full candidate list. Loads all `.iml` for the drift dimension. v3.3 layout fixes: one field per line in the header, footer count + Close packed before the tree so they stay on screen at the default size. `test/test_match_panel.py`. |
| 11 | Generate Report | part of `main.py` | Batch 8 | Content spec in `Report_Content_Example.md`; export format still TBD. |

---

## UI batching plan (`main.py`)

`main.py` is a 1000+ line Tk app. Rewrite is split into small user-testable
batches. Each batch: I code, user runs `python main.py` and reports visually.

| Batch | Content | Status |
|---|---|---|
| 1 | Toolbar redesign per §第八階段 (5 buttons); auto-read on file select; Settings menu; `ui_settings.json` persistence; Browse Library Data | **Done** |
| — | Bonus: heatmap x-axis normalized to `drift_relative` (readGAS/peaks/peak_with_number); major tick 0.5 / minor 0.1 / format `%.1f` | Done |
| — | Bonus: 3-pane horizontal layout per user's sketch (error.jpg): `files 1 : main image 3 : peak table 2`; window default 1700×950; merged dual-canvas into single `main_canvas` | Done |
| — | Bonus: Browse Folder defaults to `<project>/GAS/`; ImageViewerDialog mouse-wheel zoom toward cursor | Done |
| 2 | Peak table extended: 5 → 10 columns (`# / drift_ms / drift_relative / retention_s / intensity / On / GC×IMS / GC / IMS / ▶`); On column toggles `peak["active"]` (visual only, no sync yet); ▶ opens placeholder dialog | **Done** |
| — | Bonus: toolbar reordered / renamed to `Browse mea folder / Show Detected Peak Heatmap / Show Original Heatmap / Rules / Generate Report`; indeterminate progress bar during read (~13 s) and detect (~83 s); main_canvas now handles in-place zoom (wheel toward cursor) + pan (left-drag), no popup on click; "Show Original Heatmap" swaps main_canvas back to raw heatmap; "Show Detected Peak Heatmap" swaps to overlay if already computed, else triggers detection | Done |
| 3 | Sync: circle ↔ On checkbox via `toggle_peak()`; rule-rejected and hand-deselected peaks look identical (grey); manual pick overrides the rule (dashed ring); state saved to `_peaks_state.json` keyed by coordinates | **Done** |
| 4 | Rules management panel (independent Toplevel; based on `rules_config.json`) | **Done** — live toggle + params + funnel (~4 ms via `_maxima.npz`); mandatory rules locked; invalid params refused on save |
| 5 | Compound-match panel (reads `_peaks_identified.json`; three-strip stacked layout with confidence dots + source_file trace); wire `identify.py` into main.py flow so peaks table shows real match counts | **Done** — ▶ opens the candidate list; table's GC×IMS / GC / IMS columns auto-fill; both CLI and UI load libraries through `identify.load_libraries()` |
| 6 | Main canvas rewrite: matplotlib PNG → native `Canvas.create_oval / create_text` on PIL background | **Done** — `_bg.png` backdrop + `_bg.json` geometry; per-peak Canvas items keyed by `peak_id`; fixed the old margin-ignoring offset |
| 7 | Translucent number labels (colour-simulated per draft.13) | **Partly done** — numbers use Tk `-stipple`; circles use colour because `-outlinestipple` is ignored on Windows |
| 8 | Generate Report button — content assembly + export format | Not started |

---

## What the user can test right now

### CLI (fully working)

```bash
# 1. detect peaks (~83 s on a full-sized .mea)
python peaks.py "GAS/嘉義大學＿咖啡發酵/260625_141215_STD.mea" --top-n 100

# optional: subtract the sloped RT baseline first (+~16 s; off by default)
python peaks.py "GAS/嘉義大學＿咖啡發酵/260625_141215_STD.mea" --baseline
#   --baseline-lam 1e11   smoothness; the default is measured, do not guess
#   --baseline-stride 8   solve every n-th row and interpolate back

# 2. run identify pipeline (needs peaks.json from step 1)
python identify.py results/260625_141215_STD_peaks.json

# Optional flags:
#   --profile <cal.json>     use K0 calibration profile
#   --raw-tp 45,1013         raw_parameters mode: pass T (°C) and P (mbar)
#   --library-dir <path>     override library folder
#   --rules-config <path>    override rules_config.json
#   --ri-tol / --rt-tol / --k0-tol / --drift-tol   tolerance overrides
#   --ri-series ketone       reference series for the STD anchors
#   --no-ri                  disable stage 4 (forces the RT fallback)

# Expected output on the STD sample:
#   peaks: 47 detected, ~17 in RIP band (drift_relative ≈ 1.0, will be excluded by R004)
#   k0_mode=standard_based on every peak; ims_dimensions_used={'k0': 37}
```

**Which RI source a folder resolves to** (`ri_mode` in `_peaks_identified.json`,
and in the UI's status line) — measured across all four `GAS/` folders:

| folder | STD | `.gasprj` | `ri_mode` | anchors |
|---|---|---|---|---|
| `Coffee-bean` | 1 | 1 | `batch_own_std` | 6 |
| `嘉義大學＿咖啡發酵` | 2 | 0 | `batch_own_std` | 6 |
| `海洋大學…鱸魚` | 0 | 3 | `vocal_project_table` | 6 |
| `藝妓咖啡` | 0 | 2 | `vocal_project_table` | 182 |

A folder with neither stays `unavailable`, and the GC column then falls back to
retention-time matching — labelled `GC (RT s)`, not `GC (RI)`.

### UI (Batches 1–6; 7 partly)

```bash
python main.py
```

Expected on launch:
- Window sized to the screen and maximised, **3-pane horizontal layout**:
  MEA files (left) | main image (center) | peak table (right). Sashes draggable.
- Toolbar: `Browse mea folder | Show Detected Peak Heatmap | Show Original
  Heatmap | Rules | Generate Report` (Report styled dark-green bold).
- Settings menu: Browse Library Data... / Reset / Show Current.
- Bottom-right: indeterminate progress bar (idle until a subprocess runs).

Flow:
1. **Browse mea folder** → defaults to `<project>/GAS/`. Samples list first;
   the **calibration STD is sorted last, grey italic, with `· STD` appended**
   (decided by header `Sample=="STD"`, never by filename). A background thread
   resolves RI + K0 for the folder once and caches it; the status line names the
   source — `batch_own_std, 6 ketone anchors` or
   `vocal_project_table, 182 points from <file>.gasprj`.
2. Select a `.mea`. If an `.npz` already exists you are asked whether to reuse
   it (instant) or re-read the `.mea` (~13 s, overwrites the `.npz`). Missing
   display images are rebuilt from the `.npz` alone (`peaks.py --bg-only`).
   The `.mea` is never modified.
3. **Show Detected Peak Heatmap** → first run ~83 s; afterwards results load
   from disk (~35 ms). Circles and numbers appear as Canvas objects over the
   circle-free backdrop; the peak table populates.
4. **Show Original Heatmap** → swaps the same canvas back to the plain heatmap.
5. Wheel zooms toward the cursor; left-drag pans. Circles track both.
6. Peak table has 9 columns (`# / Drift rel. RIP / RI / Intensity / On /
   GC×IMS / GC / IMS / ▶`). Header shows
   `(n_rt × n_dt = N points)  Detected Peaks: 31    Current selected peaks: 31`.
   The identification columns auto-fill once the libraries load — no ▶ click.
   **The GC heading names its own dimension**: `GC (RI)` when calibrated,
   `GC (RT s)` when it fell back to retention time.
7. **Click a circle** (or the `On` checkbox) → it greys out and the matching row
   greys with it; click again to restore. A peak the rules rejected looks the
   same as one you dropped by hand; clicking it keeps it anyway and adds a
   dashed ring. Choices survive a restart.
8. **Rules** → panel opens top-right (480×600). Toggling R001/R003/R005 or
   editing a parameter repaints the circles in ~4 ms; rejected peaks stay on
   screen greyed. R004/R006 are shown locked (`always on`).
9. **▶** on any row opens the candidate list. Header is one field per line; the
   count line and **Close** sit at the bottom and stay visible without resizing.
   If RI is uncalibrated, a red warning above the list says the candidates come
   from retention-time proximity and are unverified.
10. **Generate Report** still a placeholder (Batch 8). It declines the STD with
    an explanation — reporting the calibration source would be circular.

Verified by 238 passing tests plus a scratch smoke script that drives the real Tk
app (circle click, drag-vs-click, table sync, state round-trip). **Full visual QA
by the user is still pending, including everything v3.3 changed** — see
"Immediate next actions" item 0.

---

## Key files (refreshed at v3.3)

```
C:/GC-IMS-PEAK/
├── CLAUDE.md                 # auto-loaded every session; points here. Keep short
├── main.py                   # Tk UI — batches 1–7 done, 8 (Report) pending
├── readGAS.py                # .mea parser + RT_AXIS_VERSION (the (averages+1) fix)
├── peaks.py                  # detection, _bg.png/_bg.json, _maxima.npz, --bg-only
├── baseline.py               # opt-in AsLS baseline along RT (peaks.py --baseline)
├── peak_with_number.py       # static numbered overlay (report export, not the canvas)
├── gas_utils.py              # file-picker + path resolution
├── rip.py                    # stage 1 — RIP normalization
├── dt_convert.py             # stage 2 — K0 (extract_raw_tp from VOCal decompilation)
├── library.py                # stage 3 — .ril/.iml readers, resolve_data_dir, polarity inference
├── calibration.py            # stage 4 — RT→RI (4 tiers incl. .gasprj), K0 constant, axis_explanation
├── reference_series.py       # stage 4 — ketone table, 1/K0 reference, vocal_project_table
├── match.py                  # stage 5 — 2-D tolerance-window match
├── identify.py               # stage 6 — integration CLI, load_libraries (shared w/ UI)
├── rules.py                  # stage 7 — R001–R006, mandatory-rule enforcement
├── rules_config.json         # per-rule enabled/params
├── kintonemixed-C4-C9.xlsx   # authoritative source for RI values + anchor Dt
├── library_data/             # 646 .ril + 7 .iml from VOCal (gitignored)
├── ui_settings.json          # user library_dir (gitignored)
├── results/                  # all artefacts (gitignored)
├── GAS/                      # raw .mea — never modified or deleted by this project
│                             #   also .gasprj: VOCal projects. Read-only, but now
│                             #   load-bearing — RI_Normalization is the stage-4
│                             #   source for folders with no STD
└── test/                     # 238 tests
    ├── test_rt_axis.py            # locks the (averages+1) formula + version marker
    ├── test_select_from_maxima.py # locks "R004/R006 before the prominence gate"
    ├── test_calibration.py        # stage 4, incl. the anchor off-by-one evidence
    ├── test_baseline.py           # AsLS: banded=sparse, λ floor, stride invariance
    └── test_{rip,dt_convert,library,rules,match,identify,peak_table,
        peak_selection,match_panel,state_machine,subprocess,ui_ri,
        ui_validators,file_operations}.py
```

Docs: `CLAUDE.md` → `status.md` (this file) → `GC-IMS_Identify_Workflow.md`
(design authority, draft.27) → `GC-IMS_Pipeline_Implementation.md` (artefacts,
CLI) / `UI.md` (Tk spec) / `ketone_RI_provenance.md` (where the RI numbers came
from). `GC-IMS_Peak_Finding_Workflow.md` is the original image-mode blueprint;
its founding premise no longer holds and it is kept only as a methodology record.

Total test count: **339 pass in ~26 s** — `test/` 194 + `test2/` 44 + `test3/` 101. `pytest.ini` `testpaths` collects all three, so a bare `pytest` catches everything; running `pytest test/` (as older docs say) silently skips 145.

> **[v3.3] The Tk flakiness is fixed, the data-dependent skips are not.**
> `tk.Tk()` occasionally raised mid-suite and the reason read "no Tk display
> available", which is indistinguishable from a genuinely headless box. Root
> creation in isolation is fine (300 cycles, no failure), so it is transient;
> `_tk_root_or_skip()` now retries once and reports the real `TclError` if it
> still fails. The note below still applies to the file-dependent skips.

> **Intermittent skips are expected, not a regression.** Several tests call
> `pytest.skip` when their inputs are absent — `test_identify.py` and
> `test_library.py` need the real `.mea` / `library_data/`, `test_match_panel.py`
> needs a Tk display. Running the suite *while a detection batch is rewriting
> `results/`* will show 1–2 skips that vanish on re-run. Seen twice on
> 2026-08-12; both times a plain re-run gave a clean full count. If you see a skip,
> re-run before investigating.

The load-bearing tests — these encode decisions that were expensive to reach, so
a failure here means someone has undone a fix, not that the test is wrong:
- `test_rt_axis.py` — the `(averages+1)` retention step, the version marker, and
  that RI is invariant under the axis change.
- `test_select_from_maxima.py::test_r004_before_gate_lowers_threshold` — fails if
  the mandatory rules move back to post-detection filtering.
- `test_calibration.py::test_ketone_dt_values_expose_the_anchor_off_by_one` —
  keeps the arithmetic showing *why* the spacing heuristic was dropped, so any
  attempt to reinstate it meets the counter-evidence.
- `test_rules.py::test_mandatory_rules_cannot_be_disabled` — covers
  `enabled:false`, a deleted entry, and params-not-forced.
- `test_dt_convert.py::test_both_modes_return_the_same_quantity` — locks the K0 /
  1÷K0 fix; both modes must return K0.

---

## Testing convention (established this session)

- Every new module (`rip`, `library`, `rules`, `dt_convert`, `match`, `identify`)
  has a companion `test/test_<module>.py` acting as both:
  - pytest test (function name `test_<module>_smoke`)
  - runnable debug script (`python test/test_<module>.py`, prints diagnostics)
- Tests needing real files (`.mea`, `.ril`, `.iml`) call `pytest.skip` when
  the files are absent — safe on fresh clones without data.
- `library.resolve_data_dir()` used inside tests instead of hardcoding VOCal
  path, so tests keep working after user's library reorganization.

---

## Open decisions (workflow-level, not code)

These are things I cannot resolve — user needs to decide or provide data.

1. ~~**K0 raw_parameters mode: which `Start temp` and pressure field?**~~ —
   ✅ **RESOLVED 2026-08-12** by decompiling VOCal. Temperature is `Start temp 1`;
   pressure is the **sum** `10 × (Start ambient pressure + Start pressure EPC
   IMS)` = 1018.43 mbar, not either field alone — which is why every attempt to
   pick one had felt arbitrary. `dt_convert.extract_raw_tp(header)` derives both
   and returns provenance naming `BufferedMEA.java:313`; `compute_k0()` calls it
   automatically when T/P are not supplied.

   **But accuracy, not correctness, is now the limit**: this path lands **+3.5%**
   off the standard-based value — 43% of one homolog's spacing — so it stays
   unusable for identification. Use decision 2's route.

2. ~~**K0 standard_based mode: is a calibration standard available?**~~ —
   ✅ **RESOLVED 2026-08-12.** No new measurement was needed: `GAS BASE 3H_IMS
   K0.iml` (G.A.S. official base library, `DtMode=1/K0`) already carries known K0
   for the six ketones, and the STD is already run on this instrument.
   `calibration.derive_k0_instrument_constant()` solves `IC = K0_ref · t · U` per
   anchor: **IC = 25.0808, CV = 0.133% across 6 anchors**, residuals < 0.25%. It
   refuses to return a value when the spread exceeds `max_cv`, so a mismatched
   library cannot silently produce a plausible-looking constant.
   `build_k0_profile_from_std()` wraps it as a `dt_convert` profile.

   **✅ Wired 2026-08-12 (v3.2).** `resolve_calibrations_cached()` now resolves RI
   and K0 in one pass from the same STD, and both `identify.py` and the UI use it.
   Measured on the STD: `k0_mode=standard_based` on all 37 peaks, and the IMS
   match dimension flips from RIPrel to **k0** (`ims_dimensions_used={'k0': 37}`).
   K0 deliberately has **no registry-borrowing tier** — `instrument_constant` is
   tied to this machine's drift-tube geometry and voltage, so a borrowed value is
   meaningless, unlike an RI scale.

3. **The two problems the manager's table brought with it** — both block trusting
   any RI number the app prints. Full analysis: `ketone_RI_provenance.md`.

   **3a. Which column were the table's RI measured on?** They sit ~+302 above the
   non-polar values previously borrowed, and 2-butanone's 916.84 matches the NIST
   **DB-Wax (polar)** figure almost exactly, while this batch runs a non-polar
   SE-54. If the table is polar-column data, every RI is ~300 too high. Answer
   needed from whoever produced the table. Until then `assumed=True` /
   `confidence="supplier_table_column_polarity_unverified"`.

   > **📌 2026-08-24 — strong internal evidence now says "polar". Behaviour
   > deliberately unchanged.** Reading the `.gasprj` files for the v3.3 RI tier
   > turned up a `Compounds` block: operator-annotated peaks in `.iml` row format
   > with CAS, RI, Rt, drift, plus the instrument serial. Four findings:
   >
   > 1. **Two instruments, same stationary phase, scales differing by +283…+654.**
   >    `5H4-00123` (`FS-SE-54-CB-1`) and `1H1-00088` (`FS-SE54-CB-0.5`) are both
   >    SE-54 — length and bore do not move RI. Seven compounds matched by CAS
   >    disagree by hundreds of units.
   > 2. **The size of Δ tracks polarity**: ester +283 → ketone +315 → cyclic
   >    ketone +395 → sec-alcohol +404 → prim-alcohol +501 → aromatic aldehyde
   >    +567 → furan aldehyde +654. A constant offset would mean a mis-set
   >    calibration; a polarity-ordered one is the signature of a *different
   >    phase*. This argument needs no external reference.
   > 3. **The supplier table and the `5H4-00123` project are one scale, not two
   >    witnesses** — Coffee-bean's VOCal curve reproduces the six ketone values
   >    to within ±4.4 RI.
   > 4. **The other instrument ran the same ketone standard ~300 lower** — 鱸魚's
   >    six anchors are exactly the `[589.4 … 1095.6]` this project borrowed and
   >    then discarded in draft.24, and 藝妓咖啡's independent 2-butanone
   >    annotation (594.3) agrees with them.
   >
   > Literature (approximate, from general knowledge — worth spot-checking two)
   > puts all seven pairs on the same side: `1H1-00088` non-polar, `5H4-00123`
   > polar.
   >
   > **The user was shown this on 2026-08-24 and chose to keep `library_data/`
   > and the supplier table as-is, explicitly declining code changes off the back
   > of the `.gasprj`/`.mea` findings.** So `ri_values`, anchor selection and
   > library selection are all **untouched**. This entry records evidence, not a
   > change. Full analysis in `ketone_RI_provenance.md` §2.
   >
   > Two consequences worth knowing: **a constant correction cannot fix it**
   > (283→654, compound-dependent), and it leaves the RI scale and the library
   > polarity disagreeing — see open decision 9.

   **3b. ~~Anchor selection picks the wrong six~~ — ✅ FIXED.** Replaced the
   spacing heuristic with `match_anchors_by_dt()`; see the 2026-08-12 entry above.
   Anchors are now `[389.7, 467.0, 609.5, 813.4, 1107.2, 1523.4]`. Action still
   outstanding: **re-run detection so existing `_peaks.json` files pick up correct
   `ri` values** — nothing invalidates them automatically.

   **3c. Unexplained: the strongest peak in the STD.** RT 329.6 s / DT_rel 1.104,
   intensity 4936 — not one of the six ketones, yet the largest signal in the
   image, and it sits on a vertical DT_rel≈1.104 band shared with RT 400.3 /
   483.6 / 585.2 / 634.7. Reactant-ion cluster? Solvent? Contaminant? Worth
   knowing, but it no longer affects RI now that it is not selected as an anchor.

   *(This entry used to read "n-alkane RI calibration data … via Van den
   Dool–Kratz". Both halves were superseded: draft.19 established the STD is
   ketones, not n-alkanes, and draft.16 point 6 replaced Van den Dool–Kratz with
   the Kovats log form after confirming the calibration table stores
   `log10(Rt)`. The "borrowed values → verified" version of this entry was in turn
   superseded by draft.24, when the values stopped being borrowed.)*

4. **Tolerance windows (workflow §第五階段)** — *the most actionable open item;
   there is now real data to calibrate against, which there was not before.*
   `match.py` defaults (RI ±5, Rt ±5s, drift ±0.05, K0 ±0.05) are placeholders.
   Three measurements say they are too loose:

   - **K0 ±0.05** yields **673 IMS hits across 37 peaks** on the STD once the
     dimension flipped from RIPrel to k0 (v3.2).
   - **RI ±5** now runs against **117,329 `.ril` rows** for `藝妓咖啡` once the
     polarity fallback started working (v3.3) — it was matching against 0 before,
     so this window has never actually been exercised at scale.
   - **Rt ±5 s** returns **9–24 hits per peak** on a folder with no RI, and its
     nearest matches are chemically implausible (see the v3.3 entry above). That
     one is arguably not a tolerance problem at all — RT is not transferable — but
     it sets a floor on how much the window can ever help.

   UI Batch 4 (Rules panel) may eventually expose these as user-editable.

5. ~~**K0 vs 1/K0: the two modes return reciprocal quantities**~~ — ✅ **FIXED
   2026-08-12.** Kept here because it defines what `instrument_constant` will mean
   when a standard is finally measured (see decision 2).

   The defect: `k0_from_instrument_constant()` returned **K0** while
   `k0_from_raw_params()` returned **1/K0** (`return 1.0 / K0`), yet both were
   written to the same `peak["k0_value"]` — for one peak on one instrument the two
   modes disagreed by an inversion, not by rounding, and nothing anywhere raised.

   **What settled it.** `gc-ims-tools` 0.1.10 (Food Chemistry 2022),
   `Spectrum.calc_reduced_mobility()`:

   ```python
   T0, p0 = 273.15, 1013.15
   K0 = (L**2 * T0 * p) / (dt * 1e-3 * Ud * T * p0)      # defaults L = 5.3 cm
   ```

   citing Ahrens & Zimmermann, *Anal Bioanal Chem* 413, 1009–1016 (2021). Expanded
   that is `(T0/T)·(p/p0)·L²/(t·U)` — **identical in form to ours**, and it returns
   **K0**. (Their default `L = 5.3 cm` also matches what we read from the header,
   which independently validates our `nom Drift Tube Length` parsing.)

   **The fix.** Both branches now return K0. The inversion moved to the comparison
   step, **explicitly**: `match.match_k0()` reads `DtMode` to decide whether the
   library value is `K0` or `1/K0` (203 rows in `library_data/` are literally
   `"1/K0"`), converts into K0 space, and stores the converted value as
   `k0_library_value` while leaving raw `Dt[a.u.]` untouched. Putting the
   conversion there is the point: it is a property of *how this library stores
   things*, not of *how a peak's K0 is computed* — conflating the two is what
   produced the reciprocal in the first place.
   Locked by `test_both_modes_return_the_same_quantity()`, which feeds `ah×L²` as
   `instrument_constant` into the standard_based branch and requires both modes to
   agree. The `return 1.0 / K0` in workflow §第二階段's design snippet — the origin
   of the bug — now carries a correction note.

   *(This entry used to end "Still no output change: `mode` remains
   `unavailable`, so `k0_value` is `None` until a calibration standard exists."
   That was true when written and stopped being true the same day — decision 2
   found the standard already on disk, and v3.2 wired it through. `k0_mode` is
   now `standard_based` on every peak of a folder that has a usable STD.)*

6. **`R001` vs `prom_frac`: two prominence gates in series**
   `prom_frac` (relative, inside detection) runs before `R001` (absolute, after
   detection), so `R001` has no effect for any threshold below the current
   relative gate. Consequence of the draft.14 ordering decision, not a bug, but
   the migration described in workflow §第七階段 ("remove `--prom-frac`") was
   never carried out and cannot be as written, because `prom_frac` is now
   load-bearing inside `select_from_maxima()`. Needs a decision: make `R001`
   relative, or demote `prom_frac` to a pure detection-layer floor.

7. **What produced the `.gasprj` RI tables?** (new 2026-08-24, v3.3)
   Two folders now calibrate from `RI_Normalization` blocks inside their own
   `.gasprj`, which makes RI available where there is no STD — but the file stores
   only VOCal's **resampled** curve. Which standard was run, how many anchors it
   really had, and **which column polarity it was measured on** are not recoverable
   from the file. That last one is the same question as 3a, and it matters the same
   way: a polar-column scale applied to a non-polar run shifts every RI by ~300.
   Until answered, `assumed_unverified=True` /
   `confidence="vocal_project_table_anchors_not_recoverable"`.

   Worth asking whoever ran those batches. Note `藝妓咖啡` is a **different
   instrument** (1H1-00088) from the calibrated batch (5H4-00123), so its answer
   need not match the ketone table's.

8. **`borrowed_from_registry` is unreachable dead code.** (noted 2026-08-24)
   Tier (b) of `resolve_ri_calibration()` is implemented and tested, but nothing
   can ever trigger it: no `ri_calibration_registry.json` exists, **nothing in
   production calls `save_registry()`** (only tests do), and `main.py` never passes
   `dims`, so the UI skips the tier unconditionally. Either wire it up — writing a
   registry entry whenever `batch_own_std` succeeds would be the natural place — or
   delete it. Leaving a tested-but-unreachable tier in the resolution chain invites
   someone to assume it works.

9. **The RI scale and the `.ril` library polarity currently disagree.**
   (new 2026-08-24 — a decision for the user; **no code changed**)

   Matching compares a peak's RI against a library's RI, and both must be on the
   same scale for the comparison to mean anything. Right now they are not:

   - **peak RI** comes from the supplier ketone table, which the evidence in
     decision 3a points to being a **polar** scale;
   - **library RI** comes from `.ril` files chosen by the header's
     `POLARITY: np` — deliberately **non-polar** (`AVERAGE LOW POLAR`, DB-5,
     HP-5 …).

   A ±5 hit across two different scales lands on whatever compound's non-polar RI
   happens to equal this peak's polar RI — a *different* compound than the right
   answer, and reliably rather than noisily. This is not something v3.3
   introduced: it has been latent since draft.24 swapped the RI values, and only
   became visible once the `.gasprj` evidence identified the scale.

   Two self-consistent options. **Both are essentially one-line changes, and
   neither should be made without the user choosing:**

   | keep | would change | effect |
   |---|---|---|
   | supplier RI (current) | select **polar** `.ril` (`Full_Polar`, `NIST2020 RI DB-Wax`, `Standard polar`) | query and reference on one scale; contradicts the column header |
   | non-polar libraries (current) | the RI values | contradicts the user's 2026-08-24 decision |

   **✅ RESOLVED 2026-08-24 — without choosing a side.** The fix is not to pick
   polar or non-polar RI values (that is a chemistry question, not a programming
   one) but to make **library selection follow whichever RI scale is actually in
   use**. `library.detect_ri_scale_polarity()` takes the calibration series'
   known compounds (CAS + RI) and looks each one up in `library_data` on both
   phase families, then votes. `identify.select_library_files()` uses that
   polarity for `.ril` instead of the header's, and records header polarity,
   detected polarity, which was used, and a `polarity_conflict` flag.

   Measured on the supplier table: **6/6 votes polar**, and the values sit almost
   exactly on the library's polar entries (2-butanone 916.8 vs 908.0, 2-heptanone
   1181.4 vs 1182.0). Fed the non-polar 鱸魚 values instead, the same function
   returns **6/6 non-polar** — it follows the data, it does not take a side.

   Effect on the STD's own 2-butanone, whose identity is certain:

   | | `.ril` loaded | hits | compounds | 2-butanone recovered |
   |---|---|---|---|---|
   | before | 13 non-polar files | 407 | 151 | **no** |
   | after | 106 polar files | 415 | **56** | **yes** |

   Detection needs a series carrying compound identities, so it works for
   `ketone` and returns `None` for `.gasprj`-derived curves (no identities in the
   file) — in which case selection falls back to the header, as before. Fewer
   than 2 usable probes, or a tie, also returns `None`: guessing wrong would put
   the whole batch on the wrong phase, which is worse than not knowing.

   **The RI values themselves are untouched** — the user's decision to keep the
   supplier table stands, and decision 3a remains open.

---

## Session workflow agreement (from this session)

User + Claude split UI work as follows:
- Claude codes the change.
- Claude tells user "please run `python main.py`" with an acceptance checklist.
- User runs locally, reports errors (text) / visual issues (screenshots) /
  interaction bugs ("clicked X, expected Y, got Z").
- Small batches so bug source is easy to isolate.

Working style preferences the user reinforced this session:
- Ask before doing risky file operations (e.g. offered three ways to copy the
  library files; user picked "I'll do it manually").
- Keep `_verify_*.py` verification scripts (renamed to `test_*.py` and moved
  to `test/`). User explicitly wanted them retained as part of test code.
- Don't hardcode data paths — use `resolve_data_dir()`.
- Prefer conservative defaults for rule params (0 / disabled) so first-run
  behaviour doesn't accidentally filter everything out.

---

## Where a next session should look first

0. **`CLAUDE.md`** — loaded automatically, so you have already read it. It exists
   only to point here and to carry the handful of invariants that are expensive
   to rediscover (axis convention, the `(averages+1)` retention step, mandatory
   rules before the prominence gate, peak state keyed by coordinates, `.npz`
   carrying `mea_source`). **Keep it short**: details belong in this file, and
   duplicating them means one copy goes stale.
1. **This file** (`status.md`) for current position.
2. **`GC-IMS_Identify_Workflow.md`** for the authoritative spec — especially
   the "待實作清單" section at the bottom.
3. If UI: read the Batch table above; pick the next unstarted batch.
4. If CLI: check the open decisions; if the user has resolved any, wire
   that into the corresponding module.

*(Batches 3, 4 and 6 were listed here as "next" in earlier revisions; all three
are done — see the Batch table above.)*

### Immediate next actions (as of v3.3)

**0. Pending user verification — do this first.** v3.3 shipped without the manual
UI pass the working agreement calls for. The CLI side is verified end-to-end
across all four `GAS/` folders and 238 tests pass, but nobody has yet *looked* at:
the `.gasprj` RI axis on `藝妓咖啡` (RI column populated, y-axis in Retention
Index, the new status line), the STD marked in the file list, the `GC (RT s)`
heading, or the compound panel's fixed layout. If something looks wrong, fix
forward from `v3.3`.

**Blocked on the manager — none of these can be answered from our own data:**

1. **Which column were the `kintonemixed-C4-C9.xlsx` RI values measured on**
   (polar vs non-polar)? This is the last factor that shifts RI *absolutely*, by
   ~300 units across the board. Everything downstream — every match, every
   report — carries that uncertainty until answered. See open decision 3a.
   **Now doubled**: open decision 7 asks the same question of the `.gasprj`
   tables, for a *different instrument*, so the two answers need not agree.
2. **Which solvent was used?** May explain the unidentified strongest peak in the
   STD (RT 329.6 s, DT_rel 1.104 — open decision 3c).
3. **Can an n-alkane mix be run on this instrument?** That is the only route to a
   genuinely `self_calibrated` RI rather than an adopted external scale. Now also
   the only route to `batch_own_std` for `藝妓咖啡` / `鱸魚`, which currently rely
   on their `.gasprj` tables.

**Code work that can start now:**

4. **Calibrate the tolerance windows** (open decision 4) — promoted to the top of
   this list because it is the one place where more data has actually arrived.
   ±0.05 K0 gives 673 hits across 37 peaks; ±5 RI has never been exercised against
   a real library until `藝妓咖啡` started loading 117k `.ril` rows in v3.3.
5. **UI Batch 8 (Generate Report)** — the last unimplemented feature. Content spec
   in `Report_Content_Example.md`, export format still TBD. Note the entry point
   already refuses the STD, so that rule does not need re-deciding. Batch 7 is
   cosmetic and effectively done via `-stipple`.
6. **Open decision 6** — `R001` vs `prom_frac`, two prominence gates in series.
   Needs a decision before either can be documented as intended behaviour.
7. **Open decision 8** — either wire up `borrowed_from_registry` (write an entry
   whenever `batch_own_std` succeeds) or delete the tier. It is currently tested
   but unreachable.
8. **Re-run detection on stale batches.** Anything detected before v3.3 has no
   `ri` for the `.gasprj` folders, and anything before the 2026-08-12 anchor fix
   has wrong `ri`. Nothing invalidates `_peaks.json` automatically.

**Key files touched in v3.3**:
- `calibration.py`: `read_gasprj_ri_table()` / `scan_folder_for_gasprj()` /
  `build_from_gasprj()`, the fourth resolution tier, `KOVATS_RI_FLOOR`.
- `library.py`: `infer_polarity_from_column_name()`, `polarity_source`.
- `main.py`: STD marking in the file list, `_gc_column_dimension()` /
  `_sync_gc_column_heading()`, compound-panel layout + Close.
- `reference_series.py`: the `vocal_project_table` series.
- `identify.py`: `parsed_polarity_source` in the library provenance.

**Key files touched in earlier sessions (recent → earlier)**:
- `peaks.py` / `rules.py` / `main.py`: draft.14 — mandatory rules R004/R006
  before the prominence gate, `select_from_maxima()`, `_maxima.npz` cache,
  `_bg.png` + `canvas_geometry`, native Canvas circles, live Rules panel.
- `main.py`: toolbar rename+reorder; progress bar wiring; in-place zoom/pan
  on main_canvas (`_render_main_canvas` + wheel/drag bindings); merged
  heatmap/overlay canvases; auto-read on file select; Settings menu.
- `readGAS.py` / `peaks.py` / `peak_with_number.py`: heatmap x-axis switched
  to `drift_relative` with 0.5-major/0.1-minor ticks; peaks.py integrates
  `rip.find_rip()`.
- `rip.py` / `dt_convert.py` / `library.py` / `rules.py` / `match.py` /
  `identify.py`: new modules for stages 1–7 of the workflow.
- `test/test_*.py`: smoke tests for every new
  module; state machine + peak table tests updated for new columns/toolbar.
