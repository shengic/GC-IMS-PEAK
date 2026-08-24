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


class TestSTDFileListing:
    """The MEA file box marks the calibration STD instead of hiding it.

    Decision (2026-08-24): the STD stays listed — it goes through the same
    detection path as a sample and inspecting its peaks is the only way to check
    the RI anchor assignment — but it is marked, sorted last, and refused by
    Generate Report. See `GCIMSApp.populate_file_list`.

    These drive the real `populate_file_list` / `is_std_file` against a fake
    Treeview, so they need no Tk display. What they actually lock down is that
    the marking follows the **header** (`Sample=STD`), not the filename.
    """

    class _FakeTree:
        """Records insert() calls the way ttk.Treeview would accept them."""

        def __init__(self):
            self.rows = []

        def delete(self, *items):
            self.rows = []

        def get_children(self):
            return []

        def insert(self, parent, index, text="", values=(), tags=()):
            self.rows.append({"text": text, "values": values, "tags": tuple(tags)})

    @staticmethod
    def _app_shim(tree):
        """Bind the real methods to a shim carrying just what they touch."""
        from main import AppState, GCIMSApp

        class _Shim:
            pass

        shim = _Shim()
        shim.state = AppState()
        shim.file_tree = tree
        shim.populate_file_list = GCIMSApp.populate_file_list.__get__(shim)
        shim.is_std_file = GCIMSApp.is_std_file.__get__(shim)
        return shim

    @staticmethod
    def _write_mea(folder, name, sample):
        """A stub .mea: `_read_header_lite` only ever reads the ASCII header."""
        p = Path(folder) / name
        p.write_bytes(
            f'Machine type = FlavourSpec\r\nSample = "{sample}"\r\n'
            f"Chunks count = 10\r\n".encode("latin-1")
        )
        return p

    def test_std_is_marked_last_and_samples_keep_order(self, tmp_path):
        self._write_mea(tmp_path, "260625_141215_STD.mea", "STD")
        self._write_mea(tmp_path, "260625_143022_A_1_2.mea", "A_1_2")
        self._write_mea(tmp_path, "260625_141900_A_1_1.mea", "A_1_1")

        tree = self._FakeTree()
        shim = self._app_shim(tree)
        shim.populate_file_list(str(tmp_path))

        assert [r["text"] for r in tree.rows] == [
            "260625_141900_A_1_1.mea",
            "260625_143022_A_1_2.mea",
            "260625_141215_STD.mea   · STD",
        ]
        assert tree.rows[-1]["tags"] == ("std_file",)
        assert all(r["tags"] == () for r in tree.rows[:-1])
        # The row still carries its path, so the STD remains openable.
        assert tree.rows[-1]["values"][0].endswith("260625_141215_STD.mea")

    def test_marking_follows_the_header_not_the_filename(self, tmp_path):
        """The two ways a filename convention would get it wrong, both covered.

        `scan_folder_for_std` judges by `Sample=STD` precisely because operators
        mistype names; if this list re-decided by filename it could disagree with
        the file the calibration actually used, in either direction.
        """
        # Named STD, but the header says it is a sample.
        self._write_mea(tmp_path, "260625_150000_STD_rerun.mea", "A_2_1")
        # A real STD whose name says nothing.
        self._write_mea(tmp_path, "260625_151000_kalibrierung.mea", "STD")

        tree = self._FakeTree()
        shim = self._app_shim(tree)
        shim.populate_file_list(str(tmp_path))

        marked = [r for r in tree.rows if r["tags"] == ("std_file",)]
        assert len(marked) == 1
        assert marked[0]["text"].startswith("260625_151000_kalibrierung.mea")
        assert shim.is_std_file(str(tmp_path / "260625_151000_kalibrierung.mea"))
        assert not shim.is_std_file(str(tmp_path / "260625_150000_STD_rerun.mea"))

    def test_folder_without_std_lists_everything_unmarked(self, tmp_path):
        self._write_mea(tmp_path, "260625_141900_A_1_1.mea", "A_1_1")
        tree = self._FakeTree()
        shim = self._app_shim(tree)
        shim.populate_file_list(str(tmp_path))

        assert len(tree.rows) == 1
        assert tree.rows[0]["tags"] == ()
        assert shim.state.std_files == set()

    def test_is_std_file_handles_no_selection(self, tmp_path):
        """Generate Report calls this with whatever is selected — possibly nothing."""
        shim = self._app_shim(self._FakeTree())
        assert not shim.is_std_file(None)
        assert not shim.is_std_file("")
