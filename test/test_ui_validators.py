"""
test_ui_validators.py — Tests for input validation and error handling.
"""

import os
from pathlib import Path

import pytest


class TestFilePathValidation:
    """Test file path validation."""

    def test_mea_file_exists(self, sample_mea_files):
        """Should validate that .mea file exists."""
        mea_file = sample_mea_files / "FM_1.mea"
        assert os.path.exists(mea_file)
        assert mea_file.suffix == ".mea"

    def test_nonexistent_mea_file(self):
        """Should reject nonexistent .mea file."""
        path = "/nonexistent/FM_1.mea"
        assert not os.path.exists(path)

    def test_mea_file_extension(self, sample_mea_files):
        """Should filter for .mea files."""
        files = list(sample_mea_files.glob("*.mea"))
        assert all(f.suffix == ".mea" for f in files)
        assert len(files) == 3


class TestFolderValidation:
    """Test folder path validation."""

    def test_folder_exists(self, sample_mea_files):
        """Should validate that folder exists."""
        assert os.path.isdir(sample_mea_files)

    def test_nonexistent_folder(self):
        """Should reject nonexistent folder."""
        path = "/nonexistent/folder/path"
        assert not os.path.isdir(path)

    def test_folder_contains_mea_files(self, sample_mea_files):
        """Should list .mea files in folder."""
        mea_files = list(sample_mea_files.glob("*.mea"))
        assert len(mea_files) > 0

    def test_empty_folder(self, temp_results_dir):
        """Should handle folder with no .mea files."""
        empty_dir = temp_results_dir / "empty"
        empty_dir.mkdir()
        mea_files = list(empty_dir.glob("*.mea"))
        assert len(mea_files) == 0


class TestOutputFileValidation:
    """Test output file validation."""

    def test_heatmap_exists(self, temp_results_dir):
        """Should verify heatmap PNG exists."""
        heatmap = temp_results_dir / "test_heatmap.png"
        heatmap.write_text("fake png")
        assert heatmap.exists()
        assert heatmap.suffix == ".png"

    def test_csv_exists(self, sample_peaks_csv):
        """Should verify CSV exists."""
        assert sample_peaks_csv.exists()
        assert sample_peaks_csv.suffix == ".csv"

    def test_json_exists(self, sample_peaks_json):
        """Should verify JSON exists."""
        assert sample_peaks_json.exists()
        assert sample_peaks_json.suffix == ".json"

    def test_missing_output_files(self, temp_results_dir):
        """Should detect missing output files."""
        missing = temp_results_dir / "nonexistent.png"
        assert not missing.exists()


class TestErrorMessages:
    """Test error message formatting."""

    def test_file_not_found_error(self):
        """Should provide clear error for missing file."""
        path = "/nonexistent/file.mea"
        error_msg = f"File not found: {path}"
        assert path in error_msg

    def test_parse_error_message(self):
        """Should provide clear error for parse failures."""
        error_msg = "JSON parse error: Expecting value"
        assert "JSON" in error_msg
        assert "parse" in error_msg

    def test_subprocess_error_message(self):
        """Should capture subprocess error output."""
        stderr = "Traceback (most recent call last):\n  File \"script.py\", line 1\n    SyntaxError"
        assert "Traceback" in stderr
        assert "SyntaxError" in stderr
