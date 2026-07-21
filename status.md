# GC-IMS-PEAK — Status & Session Handoff

**Last updated**: 2026-07-17 (session with Claude, ongoing)
**Purpose**: catch a new session up on where the Identify Workflow implementation
stands, what has been decided, what is testable now, and what to do next. Pair
with `GC-IMS_Identify_Workflow.md` (the authoritative spec) — this file is the
progress tracker, not the design.

---

## TL;DR

- CLI-side identify pipeline (stages 1, 2, 3, 5, 6, 7 of the workflow) is
  **complete and end-to-end runnable**. Real `.mea` → `_peaks_identified.json`
  works. Verified on `260625_141215_STD.mea`, coffee-fermentation sample.
- UI (`main.py`) rollout: **Batches 1 & 2 done + layout refactor + heatmap
  normalization**. Batches 3–8 pending.
- Stage 4 (`calibration.py`, RT→RI) not written — blocked on user obtaining
  n-alkane calibration data (external decision, not code).
- 70 pytest checks passing across `test/`.
- Project uses `.venv` at project root; install with
  `"K:/GC-IMS-PEAK/.venv/Scripts/python.exe" -m pip install -r requirements.txt`.
  VS Code auto-detects this venv.

---

## Pipeline status (workflow stages)

| # | Stage | Module | Status | Notes |
|---|---|---|---|---|
| 1 | RIP normalization | `rip.py` | Done | `find_rip()` + `attach_drift_relative()`. Integrated into `peaks.py` ver.03. RIP found at dt_index=680 (4.53 ms) on the test .mea. |
| 2 | K0 conversion | `dt_convert.py` | Done — architecture; two decisions still open | Three modes: `standard_based` / `raw_parameters` / `unavailable`. `raw_parameters` refuses to run unless caller passes `raw_TP={T_C, P_mbar}` (workflow explicitly wants no silent assumption). `L`/`U`/`sample_rate_khz` confirmed extractable from header; `T`/`P` field mapping still ambiguous. |
| 3 | Library readers | `library.py` | Done | `.ril` 21 cols / `.iml` 16 cols. `source_file` provenance auto-propagates via dict copy. `resolve_data_dir()` priority chain: explicit arg → `GCIMS_LIBRARY_DIR` env → `<PROJECT>/library_data/` → legacy VOCal folder → None. |
| 4 | RT→RI conversion | `calibration.py` | **Not written** | Blocked on user obtaining n-alkane calibration run. Formula is Van den Dool–Kratz (workflow §第四階段). Without this, gc-branch match falls back to RT seconds via `.iml Rt[sec]`. |
| 5 | Tolerance-window match | `match.py` | Done | Three lists: `gc_matches` / `ims_matches` / `combined_matches` (intersect by CAS). `match_all()` picks RI vs RT fallback per peak. Default tolerances are placeholders (RI ±10, Rt ±5s, K0 ±0.05). |
| 6 | Integration | `identify.py` | Done | CLI-runnable. Full pipeline: peaks.json → header → K0 → rules → library → match → `_peaks_identified.json`. Provenance carried through (`k0_mode`, `source_file`, `match_dimensions`, `gc_dimension`). |
| 7 | Rule engine | `rules.py` | Done | R001–R005 registered. Three rule types (per_peak / per_peak_with_context / batch). `apply_rules()` returns filtered peaks + report (including `rip_missing_warning`). |
| 8 | Interactive UI (main peak view) | `main.py` | Batch 1 done, 2-8 pending | See "UI batching" below. |
| 9 | Batch conversion | `batch_convert.py` (not yet) | Not started | Optional. |
| 10 | Compound-match panel | part of `main.py` | Batch 5 | UI face of `identify.py` output. |
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
| 3 | Three-way sync: circle ↔ table row ↔ On checkbox via `toggle_peak()`; overlay circles gray out when inactive; save state to `_peaks.json` or sidecar | Not started |
| 4 | Rules management panel (independent Toplevel; based on `rules_config.json`) | Not started |
| 5 | Compound-match panel (reads `_peaks_identified.json`; three-strip stacked layout with confidence dots + source_file trace); wire `identify.py` into main.py flow so peaks table shows real match counts | Not started |
| 6 | Main canvas rewrite: matplotlib PNG → native `Canvas.create_oval / create_text` on PIL background | Not started (largest single change) |
| 7 | Translucent number labels (colour-simulated per draft.13) | Not started |
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

### UI (Batches 1 + 2 + refactor)

```bash
python main.py
```

Expected on launch:
- Window 1700×950 with **3-pane horizontal layout**: MEA files (left) | main
  image (center) | peak table (right). Sashes are draggable.
- Toolbar: `Browse mea folder | Show Detected Peak Heatmap | Show Original
  Heatmap | Rules | Generate Report` (Report styled dark-green bold).
- Settings menu (top of window): Browse Library Data... / Reset / Show Current.
- Bottom-right corner: indeterminate progress bar (idle until a subprocess runs).

Flow:
1. Click **Browse mea folder** → defaults to `<project>/GAS/`.
2. Select a .mea → auto-reads (~13s, progress bar animates) → heatmap fills
   center pane; x-axis reads 0.0-4.0 with 0.5 major ticks, labelled
   `Drift relative to RIP (RIP at 1.0)`; RIP band lands at x=1.0.
3. Click **Show Detected Peak Heatmap** → first time, ~83s (progress bar
   animates); afterwards, instant swap. Overlay (heatmap + red circles)
   replaces heatmap in the same center pane; peak table populates.
4. Click **Show Original Heatmap** → swaps main_canvas back to raw heatmap
   (no popup — same canvas).
5. **Interact with main_canvas**: mouse wheel zooms toward cursor; left-click
   drag pans. No click-to-popup (in-place interaction only).
6. Peak table has 10 columns; `GC×IMS/GC/IMS` show `—` (real values come in
   Batch 5); `On` column shows `☑`/`☐` toggle-able; `▶` opens placeholder.
7. **Rules** / **Generate Report** buttons open placeholder dialogs (Batches
   4 / 8 respectively).

Verified toolbar-through-flow works with 70+ passing tests. Full visual QA
by user still pending.

---

## Key files (this session's additions in **bold**)

```
K:/GC-IMS-PEAK/
├── main.py                   # Tk UI (Batch 1 applied)
├── peaks.py                  # ver.03 (integrates rip.py)
├── readGAS.py                # unchanged this session
├── peak_with_number.py       # ver.02 (OOM fix applied)
├── gas_utils.py              # unchanged
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
    ├── test_state_machine.py # updated for new toolbar
    └── (existing tests)      # test_file_operations / test_peak_table / test_subprocess / test_ui_validators
```

Total test count: **70 pass in ~4 s** (all under `pytest test/`).

Recent tests added this session:
- `test/test_state_machine.py`: `TestSettingsPersistence` (Batch 1 settings I/O)
- `test/test_peak_table.py`: `TestCellValueLogic` (Batch 2 cell rendering)

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
accept Batches 1 + 2 + layout refactor + heatmap normalization + toolbar
reorder + progress bar + in-place canvas zoom/pan. Result determines whether
Batch 3 (three-way sync circle ↔ table row ↔ On checkbox) starts or something
needs fixing.

**Next batch if accepted**: Batch 3 — implement `toggle_peak(peak_id)` used by
all three interaction surfaces:
- click a circle on the overlay canvas → toggle that peak's active state
- toggle checkbox in peak table's On column (already wired in Batch 2, but
  currently only updates the row — needs to sync to circle + tag styling)
- click a table row → highlight is already implemented; Batch 3 refines this
  so inactive peaks display with grayed row + grayed circle

Also in Batch 3: persist state to `_peaks.json` or sidecar so restart keeps
selection intact (workflow §第八階段 point 5).

**Key files touched in current session (recent → earlier)**:
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
