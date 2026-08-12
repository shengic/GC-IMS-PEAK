# GC-IMS-PEAK — Status & Session Handoff

**Last updated**: 2026-08-12 (session with Claude) — code tagged **v3.1**
**Project root**: `F:/GC-IMS-PEAK` (was `K:/` in earlier sessions — paths below updated)
**Purpose**: catch a new session up on where the Identify Workflow implementation
stands, what has been decided, what is testable now, and what to do next. Pair
with `GC-IMS_Identify_Workflow.md` (the authoritative spec) — this file is the
progress tracker, not the design.

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
- **165** pytest checks passing across `test/` (~9 s; added `test_calibration.py`,
  `test_rt_axis.py`).
- Project uses `.venv` at project root; install with
  `"F:/GC-IMS-PEAK/.venv/Scripts/python.exe" -m pip install -r requirements.txt`.
  VS Code auto-detects this venv.

### What changed on 2026-08-12 (later, tagged v3.1) — RT axis was wrong; K0 solved by decompiling VOCal

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
  across files.
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
| 1 | RIP normalization | `rip.py` | Done | `find_rip()` + `attach_drift_relative()`. Integrated into `peaks.py` ver.03. RIP found at dt_index=680 (4.53 ms) on the test .mea. |
| 2 | K0 conversion | `dt_convert.py` | **Done — constant derived; not yet wired into the folder cache** | Three modes: `standard_based` / `raw_parameters` / `unavailable`. T/P field mapping **resolved** via VOCal decompilation (`extract_raw_tp()`: `Start temp 1`; pressure = 10×(ambient + EPC)), so `raw_parameters` now runs unaided — but lands +3.5% off, unusable for matching. `standard_based` is the usable path: `calibration.derive_k0_instrument_constant()` gives **IC = 25.0808, CV 0.133%** from the STD against `GAS BASE 3H_IMS K0.iml`. Remaining gap: the profile is not produced by `resolve_ri_calibration()`'s folder/cache path, so the UI still reports `k0_mode=unavailable`. |
| 3 | Library readers | `library.py` | Done | `.ril` 21 cols / `.iml` 16 cols. `source_file` provenance auto-propagates via dict copy. `resolve_data_dir()` priority chain: explicit arg → `GCIMS_LIBRARY_DIR` env → `<PROJECT>/library_data/` → legacy VOCal folder → None. **Selection is deliberately asymmetric**: `.ril` is column-specific (RI depends on the stationary phase), `.iml` is not (RIPrel drift is instrument-level) → all `.iml` are loaded. Drift-gas cross-check applied row-level, conservatively (only rows tagged as another gas are dropped; untagged kept — 39 of 201 RIPrel rows carry no tag). Both CLI and UI go through `identify.load_libraries()`. |
| 4 | RT→RI conversion | `calibration.py` + `reference_series.py` | **Done — values from the supplier table; column polarity unverified** | Anchors selected by **`match_anchors_by_dt()`** against the table's `Dt` (6/6, mean \|Δ\| 0.00273, RT-vs-carbon monotonicity checked), replacing the DT_rel spacing heuristic which picked the wrong six. `log10(RT)` piecewise-linear interp (extrapolate+flag), 3-tier folder resolution + cache. STD = C4–C9 2-alkanones (CAS-confirmed). RI values are the manager's table; `assumed_unverified` now flags **column polarity**, not identity (see `ketone_RI_provenance.md`). Wired into `identify.py` + UI; heatmaps show a linear RI y-axis; `axis_explanation()` backs the UI's ⓘ dialog. |
| 5 | Tolerance-window match | `match.py` | Done — **now 2-D** | `gc_matches` / `ims_matches` / `combined_matches` (intersect by CAS). GC = RI (or Rt fallback). **IMS now works without K0**: `match_drift_rel()` compares `peak.drift_relative` vs library `Dt[a.u.]` where `DtMode=="RIPrel"` (drift relative to RIP — same quantity peaks carry). `match_all` prefers K0 if `k0_value` present, else RIPrel; reports `ims_dimension`. Combined = agree on both axes → collapses hundreds of RI-only hits to a few. Tolerances placeholder (RI ±5, Rt ±5s, drift ±0.05, K0 ±0.05); `identify.py --drift-tol` overrides the drift one. |
| 6 | Integration | `identify.py` | Done | CLI-runnable. Full pipeline: peaks.json → header → K0 → rules → library → match → `_peaks_identified.json`. Provenance carried through (`k0_mode`, `source_file`, `match_dimensions`, `gc_dimension`). |
| 7 | Rule engine | `rules.py` | Done | R001–**R006** registered. Three rule types (per_peak / per_peak_with_context / batch) + a `mandatory` flag. `mark_rules()` marks `rule_active` without removing; `apply_rules()` = mark + filter (kept for `identify.py`). R004/R006 are mandatory and applied **before** the prominence gate inside `peaks.py`. |
| 8 | Interactive UI (main peak view) | `main.py` | Batches 1, 2, 3, 4, 5, 6 done; 7, 8 pending | See "UI batching" below. |
| 9 | Batch conversion | `batch_convert.py` (not yet) | Not started | Optional. |
| 10 | Compound-match panel | part of `main.py` | **Done (Batch 5), 2-D** | Auto-fills the table's **GC (RI)** (matched RI value), **IMS** (matched RIPrel drift value), and **GC×IMS** (compound agreeing on both axes) columns; ▶ opens the full candidate list. Loads all `.iml` for the drift dimension. `test/test_match_panel.py`. |
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

# 2. run identify pipeline (needs peaks.json from step 1)
python identify.py results/260625_141215_STD_peaks.json

# Optional flags:
#   --profile <cal.json>     use K0 calibration profile
#   --raw-tp 45,1013         raw_parameters mode: pass T (°C) and P (mbar)
#   --library-dir <path>     override library folder
#   --rules-config <path>    override rules_config.json
#   --ri-tol / --rt-tol / --k0-tol   tolerance overrides

# Expected output on the STD sample:
#   peaks: 47 detected, ~17 in RIP band (drift_relative ≈ 1.0, will be excluded by R004)
#   coffee marker Diacetyl (C431038) matched via RT retreat when profile absent
```

### UI (Batches 1, 2, 3, 4, 6)

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
1. **Browse mea folder** → defaults to `<project>/GAS/`.
2. Select a `.mea`. If an `.npz` already exists you are asked whether to reuse
   it (instant) or re-read the `.mea` (~13 s, overwrites the `.npz`). Missing
   display images are rebuilt from the `.npz` alone (`peaks.py --bg-only`).
   The `.mea` is never modified.
3. **Show Detected Peak Heatmap** → first run ~83 s; afterwards results load
   from disk (~35 ms). Circles and numbers appear as Canvas objects over the
   circle-free backdrop; the peak table populates.
4. **Show Original Heatmap** → swaps the same canvas back to the plain heatmap.
5. Wheel zooms toward the cursor; left-drag pans. Circles track both.
6. Peak table has 10 columns. Header shows
   `(n_rt × n_dt = N points)  Detected Peaks: 31    Current selected peaks: 31`.
   `GC×IMS/GC/IMS` show `—` until Batch 5.
7. **Click a circle** (or the `On` checkbox) → it greys out and the matching row
   greys with it; click again to restore. A peak the rules rejected looks the
   same as one you dropped by hand; clicking it keeps it anyway and adds a
   dashed ring. Choices survive a restart.
8. **Rules** → panel opens top-right (480×600). Toggling R001/R003/R005 or
   editing a parameter repaints the circles in ~4 ms; rejected peaks stay on
   screen greyed. R004/R006 are shown locked (`always on`).
9. **Generate Report** still a placeholder (Batch 8).

Verified by 165 passing tests plus a scratch smoke script that drives the real Tk
app (circle click, drag-vs-click, table sync, state round-trip). Full visual QA
by the user still pending.

---

## Key files (this session's additions in **bold**)

```
F:/GC-IMS-PEAK/
├── main.py                   # Tk UI (Batch 1 applied)
├── peaks.py                  # ver.03 (integrates rip.py)
├── readGAS.py                # unchanged this session
├── peak_with_number.py       # ver.02 (OOM fix applied)
├── gas_utils.py              # unchanged
├── rules_config.json         # NEW (draft.14) — R001-R006 enabled/params
├── rip.py                    # NEW — stage 1 (RIP normalization)
├── dt_convert.py             # NEW — stage 2 (K0 conversion)
├── library.py                # NEW — stage 3 (.ril/.iml readers, resolve_data_dir)
├── rules.py                  # NEW — stage 7 (rule engine + R001-R005)
├── match.py                  # NEW — stage 5 (tolerance-window match)
├── identify.py               # NEW — stage 6 (integration CLI)
├── library_data/             # NEW — 646 .ril + 7 .iml copied from VOCal (gitignored)
├── ui_settings.json          # NEW — user library_dir persisted (gitignored)
├── GC-IMS_Identify_Workflow.md   # authoritative spec (draft.13)
├── Report_Content_Example.md     # stage 11 content spec
├── status.md                     # THIS FILE
└── test/
    ├── test_rip.py           # NEW
    ├── test_dt_convert.py    # NEW
    ├── test_library.py       # NEW
    ├── test_rules.py         # NEW
    ├── test_match.py         # NEW
    ├── test_identify.py      # NEW
    ├── test_select_from_maxima.py  # NEW (draft.14) — locks "R004/R006 must
    │                               #   run before the prominence gate"
    ├── test_state_machine.py # updated for new toolbar
    └── (existing tests)      # test_file_operations / test_peak_table / test_subprocess / test_ui_validators
```

Total test count: **165 pass in ~9 s** (all under `pytest test/`).

Recent tests added this session:
- `test/test_state_machine.py`: `TestSettingsPersistence` (Batch 1 settings I/O)
- `test/test_peak_table.py`: `TestCellValueLogic` (Batch 2 cell rendering)
- `test/test_select_from_maxima.py`: 5 checks (draft.14). The load-bearing one is
  `test_r004_before_gate_lowers_threshold` — it fails if anyone moves the
  mandatory rules back to post-detection filtering.
- `test/test_rules.py`: `test_mandatory_rules_cannot_be_disabled` (covers
  `enabled:false`, whole entry deleted, and params-not-forced) and
  `test_r006_excludes_faster_than_rip`.

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

   **Still to wire**: the profile is not yet produced by the folder-resolution /
   cache path, so selecting a folder in the UI yields an RI calibration but not a
   K0 one. Until that is done `k0_mode` stays `unavailable` in the UI and
   K0-based matching still has no input, even though the constant now exists.

3. **The two problems the manager's table brought with it** — both block trusting
   any RI number the app prints. Full analysis: `ketone_RI_provenance.md`.

   **3a. Which column were the table's RI measured on?** They sit ~+302 above the
   non-polar values previously borrowed, and 2-butanone's 916.84 matches the NIST
   **DB-Wax (polar)** figure almost exactly, while this batch runs a non-polar
   SE-54. If the table is polar-column data, every RI is ~300 too high. Answer
   needed from whoever produced the table. Until then `assumed=True` /
   `confidence="supplier_table_column_polarity_unverified"`.

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

4. **Tolerance windows (workflow §第五階段)**
   `match.py` defaults (RI ±5, Rt ±5s, drift ±0.05, K0 ±0.05) are placeholders. Need
   real known-compound data to calibrate. UI Batch 4 (Rules panel) may
   eventually expose these as user-editable.

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

   Still no output change: `mode` remains `unavailable`, so `k0_value` is `None`
   until a calibration standard exists.

6. **`R001` vs `prom_frac`: two prominence gates in series**
   `prom_frac` (relative, inside detection) runs before `R001` (absolute, after
   detection), so `R001` has no effect for any threshold below the current
   relative gate. Consequence of the draft.14 ordering decision, not a bug, but
   the migration described in workflow §第七階段 ("remove `--prom-frac`") was
   never carried out and cannot be as written, because `prom_frac` is now
   load-bearing inside `select_from_maxima()`. Needs a decision: make `R001`
   relative, or demote `prom_frac` to a pure detection-layer floor.

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

1. **This file** (`status.md`) for current position.
2. **`GC-IMS_Identify_Workflow.md`** for the authoritative spec — especially
   the "待實作清單" section at the bottom.
3. If UI: read the Batch table above; pick the next unstarted batch.
4. If CLI: check the three open decisions; if user has resolved any, wire
   that into the corresponding module.

*(Batches 3, 4 and 6 were listed here as "next" in earlier revisions; all three
are done — see the Batch table above.)*

### Immediate next actions (as of v3.1)

**Blocked on the manager — none of these can be answered from our own data:**

1. **Which column were the `kintonemixed-C4-C9.xlsx` RI values measured on**
   (polar vs non-polar)? This is the last factor that shifts RI *absolutely*, by
   ~300 units across the board. Everything downstream — every match, every
   report — carries that uncertainty until answered. See open decision 3a.
2. **Which solvent was used?** May explain the unidentified strongest peak in the
   STD (RT 329.6 s, DT_rel 1.104 — open decision 3c).
3. **Can an n-alkane mix be run on this instrument?** That is the only route to a
   genuinely `self_calibrated` RI rather than an adopted external scale.

**Code work that can start now:**

4. **Wire K0 into the folder-resolution / cache path** so selecting a folder
   produces both an RI *and* a K0 profile. The instrument constant exists and is
   solid (IC = 25.0808, CV 0.133%) but nothing calls
   `build_k0_profile_from_std()` from the UI flow, so `k0_mode` is still
   `unavailable` there and `.iml` K0 matching still has no input. This is the
   single highest-value remaining task — it turns on a whole matching dimension.
5. **UI Batches 7 and 8.** 7 is cosmetic (translucent labels, already partly
   done via `-stipple`). 8 (Generate Report) is the last unimplemented feature;
   content spec is in `Report_Content_Example.md`, export format still TBD.
6. **Open decision 6** — `R001` vs `prom_frac`, two prominence gates in series.
   Needs a decision before either can be documented as intended behaviour.

**Key files touched in current session (recent → earlier)**:
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
