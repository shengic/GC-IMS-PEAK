"""
conftest.py — pytest fixtures for GC-IMS tests.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_results_dir():
    """Create a temporary results directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_peaks_json(temp_results_dir):
    """Create a sample peaks.json file."""
    peaks_data = {
        "source": "test.mea",
        "machine": "FlavourSpec®",
        "sample": "TEST-1",
        "detected_at": "2026-07-06T12:00:00",
        "matrix_shape": [8571, 4500],
        "detection_params": {
            "sigma": 1.0,
            "floor_pct": 85.0,
            "prom_frac": 0.02,
        },
        "stats": {
            "floor": 10.5,
            "n_raw_maxima": 150,
            "n_after_prom": 50,
            "n_final": 42,
            "max_prominence": 500.0,
            "prom_threshold": 10.0,
        },
        "n_peaks": 3,
        "peaks": [
            {
                "peak_id": 1,
                "rt_index": 790,
                "dt_index": 1251,
                "retention_s": 142.20,
                "drift_ms": 8.34,
                "intensity": 1444,
                "prominence": 487.5,
                "flatness": 0.08,
                "edge_dist": 55,
                "saturated": False,
                "rank": 1,
            },
            {
                "peak_id": 2,
                "rt_index": 850,
                "dt_index": 2100,
                "retention_s": 153.00,
                "drift_ms": 14.00,
                "intensity": 892,
                "prominence": 350.0,
                "flatness": 0.12,
                "edge_dist": 40,
                "saturated": False,
                "rank": 2,
            },
            {
                "peak_id": 3,
                "rt_index": 920,
                "dt_index": 3200,
                "retention_s": 165.60,
                "drift_ms": 21.33,
                "intensity": 650,
                "prominence": 200.0,
                "flatness": 0.15,
                "edge_dist": 30,
                "saturated": False,
                "rank": 3,
            },
        ],
    }

    json_path = temp_results_dir / "test_peaks.json"
    with open(json_path, "w") as f:
        json.dump(peaks_data, f)
    return json_path


@pytest.fixture
def sample_peaks_csv(temp_results_dir):
    """Create a sample peaks.csv file."""
    csv_content = """peak_id,retention_s,drift_ms,intensity
1,142.20,8.34,1444
2,153.00,14.00,892
3,165.60,21.33,650
"""
    csv_path = temp_results_dir / "test_peaks.csv"
    with open(csv_path, "w") as f:
        f.write(csv_content)
    return csv_path


@pytest.fixture
def malformed_json(temp_results_dir):
    """Create a malformed JSON file."""
    json_path = temp_results_dir / "malformed.json"
    with open(json_path, "w") as f:
        f.write("{broken json content")
    return json_path


@pytest.fixture
def sample_mea_files(temp_results_dir):
    """Create dummy .mea files in a temporary folder."""
    mea_dir = temp_results_dir / "mea_samples"
    mea_dir.mkdir(exist_ok=True)

    files = ["FM_1.mea", "FM_2.mea", "FM_3.mea"]
    for fname in files:
        (mea_dir / fname).write_text("dummy mea content")

    return mea_dir
