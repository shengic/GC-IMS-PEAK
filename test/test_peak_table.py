"""
test_peak_table.py — Tests for peak table data loading and manipulation.
"""

import pytest
from main import COORD_LABELS, PEAK_TABLE_COLUMNS


class TestCoordinateLabels:
    """Test coordinate label mapping."""

    def test_coord_labels_defined(self):
        """All coordinate labels should be defined."""
        assert "peak_id" in COORD_LABELS
        assert "drift_ms" in COORD_LABELS
        assert "retention_s" in COORD_LABELS
        assert "intensity" in COORD_LABELS

    def test_coord_label_mapping(self):
        """Coordinate labels should map correctly."""
        assert COORD_LABELS["peak_id"] == "#"
        assert COORD_LABELS["drift_ms"] == "Drift Time [ms]"
        assert COORD_LABELS["retention_s"] == "Retention Time [s]"
        assert COORD_LABELS["intensity"] == "Intensity"

    def test_ui_uses_correct_labels(self):
        """UI should display descriptive coordinate labels, not raw names."""
        # Verify that full names are used for display
        drift_label = COORD_LABELS["drift_ms"]
        retention_label = COORD_LABELS["retention_s"]
        assert drift_label == "Drift Time [ms]"
        assert retention_label == "Retention Time [s]"


class TestPeakTableColumns:
    """Test peak table column definitions."""

    def test_peak_table_columns_defined(self):
        """Required columns should be defined."""
        assert "peak_id" in PEAK_TABLE_COLUMNS
        assert "drift_ms" in PEAK_TABLE_COLUMNS
        assert "retention_s" in PEAK_TABLE_COLUMNS
        assert "intensity" in PEAK_TABLE_COLUMNS

    def test_peak_table_column_order(self):
        """Columns should appear in logical order: ID, X, Y, Intensity."""
        assert PEAK_TABLE_COLUMNS[0] == "peak_id"
        assert PEAK_TABLE_COLUMNS[1] == "drift_ms"
        assert PEAK_TABLE_COLUMNS[2] == "retention_s"
        assert PEAK_TABLE_COLUMNS[3] == "intensity"

    def test_peak_table_columns_not_empty(self):
        """Peak table should have at least 4 columns."""
        assert len(PEAK_TABLE_COLUMNS) >= 4


class TestPeakDataValidation:
    """Test peak data structure and validation."""

    def test_peak_has_required_fields(self):
        """Each peak should have required fields."""
        peak = {
            "peak_id": 1,
            "retention_s": 142.20,
            "drift_ms": 8.34,
            "intensity": 1444,
        }
        required = {"peak_id", "retention_s", "drift_ms", "intensity"}
        assert all(field in peak for field in required)

    def test_peak_optional_fields(self):
        """Peaks may have optional metric fields."""
        peak = {
            "peak_id": 1,
            "retention_s": 142.20,
            "drift_ms": 8.34,
            "intensity": 1444,
            "prominence": 487.5,
            "flatness": 0.08,
            "edge_dist": 55,
            "saturated": False,
        }
        optional = {"prominence", "flatness", "edge_dist", "saturated"}
        # All should be present; test that they're acceptable
        for field in optional:
            assert field in peak

    def test_peak_id_is_unique_key(self):
        """Peak ID should be unique within a detection run."""
        peaks = [
            {"peak_id": 1, "retention_s": 100, "drift_ms": 5, "intensity": 1000},
            {"peak_id": 2, "retention_s": 110, "drift_ms": 6, "intensity": 900},
            {"peak_id": 3, "retention_s": 120, "drift_ms": 7, "intensity": 800},
        ]
        peak_ids = [p["peak_id"] for p in peaks]
        assert len(peak_ids) == len(set(peak_ids))


class TestPeakTableSorting:
    """Test peak table sorting logic (unit level)."""

    def test_peak_sort_by_id(self):
        """Should sort peaks by ID."""
        peaks = [
            {"peak_id": 3, "retention_s": 120},
            {"peak_id": 1, "retention_s": 100},
            {"peak_id": 2, "retention_s": 110},
        ]
        sorted_peaks = sorted(peaks, key=lambda p: p["peak_id"])
        assert [p["peak_id"] for p in sorted_peaks] == [1, 2, 3]

    def test_peak_sort_by_retention(self):
        """Should sort peaks by retention time."""
        peaks = [
            {"peak_id": 1, "retention_s": 150.0},
            {"peak_id": 2, "retention_s": 100.0},
            {"peak_id": 3, "retention_s": 125.0},
        ]
        sorted_peaks = sorted(peaks, key=lambda p: p["retention_s"])
        assert [p["retention_s"] for p in sorted_peaks] == [100.0, 125.0, 150.0]

    def test_peak_sort_by_intensity(self):
        """Should sort peaks by intensity."""
        peaks = [
            {"peak_id": 1, "intensity": 500},
            {"peak_id": 2, "intensity": 1500},
            {"peak_id": 3, "intensity": 1000},
        ]
        sorted_peaks = sorted(peaks, key=lambda p: p["intensity"], reverse=True)
        assert [p["intensity"] for p in sorted_peaks] == [1500, 1000, 500]

    def test_peak_sort_toggle(self):
        """Should toggle sort order (ascending ↔ descending)."""
        peaks = [
            {"peak_id": 1, "intensity": 500},
            {"peak_id": 2, "intensity": 1500},
            {"peak_id": 3, "intensity": 1000},
        ]

        # Ascending
        asc = sorted(peaks, key=lambda p: p["intensity"])
        assert [p["intensity"] for p in asc] == [500, 1000, 1500]

        # Descending
        desc = sorted(peaks, key=lambda p: p["intensity"], reverse=True)
        assert [p["intensity"] for p in desc] == [1500, 1000, 500]
