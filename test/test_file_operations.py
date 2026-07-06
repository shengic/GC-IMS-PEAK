"""
test_file_operations.py — Tests for file loading and saving operations.
"""

import json
from pathlib import Path

import pytest
from main import load_peaks_from_json, load_peaks_from_csv


class TestLoadPeaksFromJSON:
    """Test loading peaks from JSON file."""

    def test_load_valid_json(self, sample_peaks_json):
        """Should load valid JSON with required fields."""
        peaks, error = load_peaks_from_json(str(sample_peaks_json))
        assert error is None
        assert len(peaks) == 3
        assert peaks[0]["peak_id"] == 1
        assert peaks[0]["retention_s"] == 142.20
        assert peaks[0]["drift_ms"] == 8.34

    def test_load_json_validates_required_fields(self, temp_results_dir):
        """Should error if JSON is missing required fields."""
        json_path = temp_results_dir / "incomplete.json"
        bad_data = {
            "peaks": [
                {"peak_id": 1, "retention_s": 100.0},  # missing drift_ms, intensity
            ]
        }
        with open(json_path, "w") as f:
            json.dump(bad_data, f)

        peaks, error = load_peaks_from_json(str(json_path))
        assert peaks is None
        assert "Missing fields" in error

    def test_load_malformed_json(self, malformed_json):
        """Should error on malformed JSON."""
        peaks, error = load_peaks_from_json(str(malformed_json))
        assert peaks is None
        assert "JSON parse error" in error

    def test_load_nonexistent_json(self):
        """Should error if file does not exist."""
        peaks, error = load_peaks_from_json("/nonexistent/path.json")
        assert peaks is None
        assert error is not None

    def test_load_json_empty_peaks_list(self, temp_results_dir):
        """Should handle JSON with empty peaks list."""
        json_path = temp_results_dir / "empty_peaks.json"
        data = {"peaks": []}
        with open(json_path, "w") as f:
            json.dump(data, f)

        peaks, error = load_peaks_from_json(str(json_path))
        assert error is None
        assert peaks == []


class TestLoadPeaksFromCSV:
    """Test loading peaks from CSV file."""

    def test_load_valid_csv(self, sample_peaks_csv):
        """Should load valid CSV with required columns."""
        peaks, error = load_peaks_from_csv(str(sample_peaks_csv))
        assert error is None
        assert len(peaks) == 3
        assert peaks[0]["peak_id"] == 1
        assert peaks[0]["retention_s"] == 142.20
        assert peaks[0]["intensity"] == 1444

    def test_load_csv_type_conversion(self, sample_peaks_csv):
        """Should convert CSV strings to correct types."""
        peaks, error = load_peaks_from_csv(str(sample_peaks_csv))
        assert error is None
        assert isinstance(peaks[0]["peak_id"], int)
        assert isinstance(peaks[0]["retention_s"], float)
        assert isinstance(peaks[0]["drift_ms"], float)
        assert isinstance(peaks[0]["intensity"], int)

    def test_load_csv_missing_columns(self, temp_results_dir):
        """Should error if CSV is missing required columns."""
        csv_path = temp_results_dir / "incomplete.csv"
        with open(csv_path, "w") as f:
            f.write("peak_id,retention_s\n1,100.0\n")

        peaks, error = load_peaks_from_csv(str(csv_path))
        assert peaks is None
        assert "CSV parse error" in error

    def test_load_csv_nonexistent(self):
        """Should error if CSV file does not exist."""
        peaks, error = load_peaks_from_csv("/nonexistent/path.csv")
        assert peaks is None
        assert error is not None

    def test_load_csv_empty(self, temp_results_dir):
        """Should handle empty CSV (header only)."""
        csv_path = temp_results_dir / "empty.csv"
        with open(csv_path, "w") as f:
            f.write("peak_id,retention_s,drift_ms,intensity\n")

        peaks, error = load_peaks_from_csv(str(csv_path))
        assert error is None
        assert peaks == []


class TestFileOperationsFallback:
    """Test fallback from JSON to CSV."""

    def test_json_exists_csv_ignored(self, sample_peaks_json, sample_peaks_csv, temp_results_dir):
        """Should use JSON if both exist."""
        peaks, error = load_peaks_from_json(str(sample_peaks_json))
        assert error is None
        assert len(peaks) == 3

    def test_json_corrupted_csv_fallback(self, malformed_json, sample_peaks_csv):
        """Should fall back to CSV if JSON is corrupted."""
        peaks_json, error_json = load_peaks_from_json(str(malformed_json))
        assert peaks_json is None
        assert "JSON parse error" in error_json

        peaks_csv, error_csv = load_peaks_from_csv(str(sample_peaks_csv))
        assert error_csv is None
        assert len(peaks_csv) == 3
