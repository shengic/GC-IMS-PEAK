# GC-IMS-PEAK — Status & Session Handoff

**Last updated**: 2026-07-27 (session with Claude) — code tagged **v3**
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
- STD confirmed **C4–C9 methyl ketones**; the 6 RI values are **borrowed**
  (`assumed_unverified` / `borrowed_cross_referenced`, see
  `methyl_ketone_RI_provenance.md`) — upgrade path documented there.
- UI (`main.py`) rollout: **Batches 1, 2, 3, 4, 5, 6 done** + Stage-4 wiring.
  Batches 7, 8 pending. (Batch 5 = the ▶ compound-match panel: clicking a peak's
  ▶ loads the `.ril`/`.iml` libraries and lists candidate compounds via
  `match.match_all`.)
- **125** pytest checks passing across `test/` (added `test_calibration.py`).
- Project uses `.venv` at project root; install with
  `"F:/GC-IMS-PEAK/.venv/Scripts/python.exe" -m pip install -r requirements.txt`.
  VS Code auto-detects this venv.

### What changed on 2026-07-27 (workflow draft.16–21, tagged v3) — Stage 4 RT→RI

**Stage 4 is implemented.** `calibration.py` + `reference_series.py` convert a
peak's retention time to a Retention Index, and the whole thing runs
automatically from a folder's STD.

- **Compound identity (draft.19):** the STD is **C4–C9 methyl ketones**
  (2-butanone → 2-nonanone), *not* n-alkanes. Their RI is not `100·n`.
- **RI values (methyl_ketone_RI_provenance.md):** the 6 values
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
  `methyl_ketone_RI_provenance.md`, `test/test_calibration.py`.

**Still open:** RI is borrowed/assumed — upgrade to verified per
`methyl_ketone_RI_provenance.md`. A fresh folder whose STD isn't detected yet
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
`pytest` (91 passing) because the lost code was UI wiring the suite does not
exercise — it was caught only by the manual smoke script. If work seems to
vanish, check `git diff` before re-deriving anything, and keep `main.py` closed
in the editor while another process is editing it.

---

## Pipeline status (workflow stages)

| # | Stage | Module | Status | Notes |
|---|---|---|---|---|
| 1 | RIP normalization | `rip.py` | Done | `find_rip()` + `attach_drift_relative()`. Integrated into `peaks.py` ver.03. RIP found at dt_index=680 (4.53 ms) on the test .mea. |
| 2 | K0 conversion | `dt_convert.py` | Done — architecture; two decisions still open | Three modes: `standard_based` / `raw_parameters` / `unavailable`. `raw_parameters` refuses to run unless caller passes `raw_TP={T_C, P_mbar}` (workflow explicitly wants no silent assumption). `L`/`U`/`sample_rate_khz` confirmed extractable from header; `T`/`P` field mapping still ambiguous. |
| 3 | Library readers | `library.py` | Done | `.ril` 21 cols / `.iml` 16 cols. `source_file` provenance auto-propagates via dict copy. `resolve_data_dir()` priority chain: explicit arg → `GCIMS_LIBRARY_DIR` env → `<PROJECT>/library_data/` → legacy VOCal folder → None. |
| 4 | RT→RI conversion | `calibration.py` + `reference_series.py` | **Done — values borrowed** | Auto-selects 6 STD anchors (DT_rel homolog ladder), `log10(RT)` piecewise-linear interp (extrapolate+flag), 3-tier folder resolution + cache. STD = C4–C9 methyl ketones; 6 RI values **borrowed** (`assumed_unverified`, see `methyl_ketone_RI_provenance.md`). Wired into `identify.py` + UI; heatmaps show a linear RI y-axis. Upgrade to verified still pending. |
| 5 | Tolerance-window match | `match.py` | Done — **now 2-D** | `gc_matches` / `ims_matches` / `combined_matches` (intersect by CAS). GC = RI (or Rt fallback). **IMS now works without K0**: `match_drift_rel()` compares `peak.drift_relative` vs library `Dt[a.u.]` where `DtMode=="RIPrel"` (drift relative to RIP — same quantity peaks carry). `match_all` prefers K0 if `k0_value` present, else RIPrel; reports `ims_dimension`. Combined = agree on both axes → collapses hundreds of RI-only hits to a few. Tolerances placeholder (RI ±10, Rt ±5s, drift ±0.05, K0 ±0.05). |
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
| 5 | Compound-match panel (reads `_peaks_identified.json`; three-strip stacked layout with confidence dots + source_file trace); wire `identify.py` into main.py flow so peaks table shows real match counts | Not started |
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

Verified by 91 passing tests plus a scratch smoke script that drives the real Tk
app (circle click, drag-vs-click, table sync, state round-trip). Full visual QA
by the user still pending.

---

## Key files (this session's additions in **bold**)

```
K:/GC-IMS-PEAK/
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

Total test count: **77 pass in ~4 s** (all under `pytest test/`).

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

1. **K0 raw_parameters mode: which `Start temp` and pressure field?**
   Header has `Start temp 1..6` (45/60/80/80/45/off) — which one is the drift
   tube? `Start pressure EPC IMS` (1.45 kPa) vs `Start ambient pressure`
   (100.4 kPa) — which is the drift-tube gas pressure? Until decided,
   `dt_convert.compute_k0()` returns `(None, "raw_parameters_missing_TP", ...)`
   for that mode.

2. **K0 standard_based mode: is a calibration standard available?**
   If yes, need one run of a known-K0 compound on this instrument to derive
   `instrument_constant`. If no, we stay stuck on `unavailable` for anything
   requiring reliable K0, which means R004 works (uses `drift_relative`, not
   K0) but K0-based matching (.iml Dt column) has no trustworthy input.

3. **n-alkane RI calibration data (stage 4 blocker)**
   Without this, gc-branch match uses RT-seconds fallback (works but less
   precise). With this, we can compute proper RI values via Van den Dool–Kratz.

4. **Tolerance windows (workflow §第五階段)**
   `match.py` defaults (RI ±10, Rt ±5s, K0 ±0.05) are placeholders. Need
   real known-compound data to calibrate. UI Batch 4 (Rules panel) may
   eventually expose these as user-editable.

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

**Immediate expected next action**: user runs `python main.py` to visually
accept Batch 6 (native circles + numbers, zoom/pan tracking) and Batch 4 (Rules
panel: toggling R001/R003/R005 or editing params must re-circle instantly;
R004/R006 shown locked as "always on").

**Next batch**: Batch 3 — `toggle_peak(peak_id)` shared by two surfaces:
- click a circle on the canvas → that peak's circle turns semi-transparent red
  and its table row greys out
- tick/untick the On checkbox in the table → the circle follows
- clicking again toggles back; the two directions stay in sync

Design constraints already settled that Batch 3 must respect:
- Tk Canvas has no true alpha. "Semi-transparent red" has to be simulated —
  either a lighter red or `-outlinestipple`. Same limitation the workflow noted
  for `create_text` (draft.12/13, which chose colour simulation).
- Left-drag already pans the canvas, so a click must be distinguished from a
  drag by press/release distance.
- `active` state should key off `(rt_index, dt_index)`, **not** `peak_id`, so a
  saved selection survives a baseline renumber (changing `R004.half_width` or
  `R006.boundary` reassigns every `peak_id`).
- Persist state to `_peaks.json` or a sidecar (workflow §第八階段 point 5).

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
- `test/test_*.py`: 70 passing tests including smoke tests for every new
  module; state machine + peak table tests updated for new columns/toolbar.
