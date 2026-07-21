# Test Suite for GC-IMS Peak Detection UI

## Overview

This directory contains **Test-Driven Development (TDD)** style tests for `main.py` (the Tk desktop UI).

**Philosophy**: Write tests first to clarify requirements, then implement features to make tests pass.

## Test Files

| File | Purpose |
|---|---|
| `conftest.py` | pytest fixtures (sample data, temp directories) |
| `test_state_machine.py` | UI state transitions, button enable/disable logic |
| `test_file_operations.py` | JSON/CSV loading, fallback strategies, error handling |
| `test_peak_table.py` | Peak data structure, sorting, coordinate labels |
| `test_subprocess.py` | Subprocess execution, output streaming, threading |
| `test_ui_validators.py` | Input validation, file path checks, error messages |
| `test_rip.py` | Identify §1 — `rip.find_rip()` / `attach_drift_relative()` on real `.mea` |
| `test_dt_convert.py` | Identify §2 — K0 dual-mode dispatch, header extraction, profile I/O |
| `test_library.py` | Identify §3 — `.ril`/`.iml` readers, GC Column parser, selection strategy |
| `test_rules.py` | Identify §7 — rule engine + five built-in rules (R001-R005) |

**Note on `test_rip.py` / `test_library.py` / `test_dt_convert.py`**: these depend
on real files under `GAS/` and `VOCal Release 0.4.31.412/_portable/data/` and will
skip/fail if those folders aren't present. They double as standalone debug
scripts—running `python test/test_rip.py` prints diagnostic info that pytest
suppresses by default.

## Installation

```bash
pip install pytest pillow
```

## Running Tests

### Run all tests:
```bash
pytest test/
```

### Run a specific test file:
```bash
pytest test/test_state_machine.py
```

### Run a specific test class:
```bash
pytest test/test_file_operations.py::TestLoadPeaksFromJSON
```

### Run a specific test:
```bash
pytest test/test_state_machine.py::TestAppState::test_initial_state
```

### Run with verbose output:
```bash
pytest test/ -v
```

### Run with coverage report:
```bash
pytest test/ --cov=main --cov-report=html
# Open htmlcov/index.html in browser
```

## Test Coverage

**Current coverage targets:**
- ✅ State machine (state enum, transitions)
- ✅ File operations (JSON/CSV load, validation, fallback)
- ✅ Peak table (data structure, sorting, labels)
- ✅ Subprocess (execution, output streaming, errors)
- ✅ Input validation (paths, file existence, error messages)

**Not yet covered (integration tests):**
- UI widgets (Tk Canvas, Treeview rendering) — requires Tk display
- Button click handlers — requires mocking Tk widgets
- Threading integration — requires timed async testing
- Peak highlighting on overlay — requires pixel-level testing

## TDD Workflow

1. **Write a test** that fails (RED)
   ```bash
   pytest test/test_state_machine.py::TestAppState::test_reset_after_file_selection -v
   # Expected: FAILED (test not implemented yet)
   ```

2. **Implement minimal code** to make test pass (GREEN)
   ```python
   # In main.py: add AppState.reset_after_file_selection() method
   def reset_after_file_selection(self):
       self.heatmap_path = None
       self.overlay_path = None
       self.peaks = []
       self.selected_peak_row = None
   ```

3. **Run test again** to verify it passes
   ```bash
   pytest test/test_state_machine.py::TestAppState::test_reset_after_file_selection -v
   # Expected: PASSED
   ```

4. **Refactor** if needed, ensuring tests still pass

5. **Repeat** for next feature

## Example: Adding New Feature via TDD

**Goal**: Add support for filtering peaks by prominence threshold.

1. Write test in `test_peak_table.py`:
   ```python
   def test_filter_peaks_by_prominence(self):
       peaks = [
           {"peak_id": 1, "prominence": 500},
           {"peak_id": 2, "prominence": 100},
           {"peak_id": 3, "prominence": 50},
       ]
       filtered = filter_peaks_by_prominence(peaks, min_prominence=100)
       assert len(filtered) == 2
       assert filtered[0]["peak_id"] == 1
   ```

2. Run test (fails):
   ```bash
   pytest test/test_peak_table.py::test_filter_peaks_by_prominence
   # FAILED: NameError: name 'filter_peaks_by_prominence' is not defined
   ```

3. Implement function in `main.py`:
   ```python
   def filter_peaks_by_prominence(peaks, min_prominence):
       return [p for p in peaks if p.get("prominence", 0) >= min_prominence]
   ```

4. Run test again (passes):
   ```bash
   pytest test/test_peak_table.py::test_filter_peaks_by_prominence
   # PASSED
   ```

## Debugging Tests

### Print debug output:
```bash
pytest test/test_state_machine.py -v -s  # -s captures stdout
```

### Drop into debugger on failure:
```bash
pytest test/test_state_machine.py --pdb
```

### Show slowest tests:
```bash
pytest test/ --durations=5
```

## Fixtures

Common test fixtures in `conftest.py`:

| Fixture | Description |
|---|---|
| `temp_results_dir` | Temporary directory for test files |
| `sample_peaks_json` | Sample `peaks.json` file (3 peaks) |
| `sample_peaks_csv` | Sample `peaks.csv` file (3 peaks) |
| `malformed_json` | Intentionally broken JSON file |
| `sample_mea_files` | Temporary folder with dummy `.mea` files |

**Usage in test:**
```python
def test_load_peaks(sample_peaks_json):
    peaks, error = load_peaks_from_json(str(sample_peaks_json))
    assert error is None
    assert len(peaks) == 3
```

## Common Issues

### `ModuleNotFoundError: No module named 'main'`
- Make sure you're running pytest from the project root (`F:\GC-IMS-PEAK\`)
- Or add `F:\GC-IMS-PEAK` to `PYTHONPATH`

### `ModuleNotFoundError: No module named 'PIL'`
- Install Pillow: `pip install Pillow`

### Test hangs (subprocess not finishing)
- Check that `run_subprocess()` uses `universal_newlines=True` and proper buffering
- Use `thread.join(timeout=5)` to prevent infinite hangs

## Next Steps

1. Run tests to establish baseline:
   ```bash
   pytest test/ -v
   ```

2. Implement features in `main.py` driven by test requirements

3. Add more tests for UI widget behaviors (requires Tk integration testing)

4. Set up CI/CD to run tests on every commit (GitHub Actions, etc.)
