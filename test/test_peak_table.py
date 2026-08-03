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
        # Drift labels shortened to make room for drift_relative column
        assert "ms" in COORD_LABELS["drift_ms"]
        assert "RIP" in COORD_LABELS["drift_relative"]
        assert COORD_LABELS["retention_s"] == "Retention Time [s]"
        assert COORD_LABELS["intensity"] == "Intensity"

    def test_ui_uses_correct_labels(self):
        """UI should display descriptive coordinate labels, not raw names."""
        assert COORD_LABELS["drift_ms"] != "drift_ms"       # not raw key
        assert COORD_LABELS["retention_s"] == "Retention Time [s]"
        assert COORD_LABELS["drift_relative"] != "drift_relative"


class TestPeakTableColumns:
    """Test peak table column definitions."""

    def test_peak_table_columns_defined(self):
        """Required columns should be defined."""
        for col in ("peak_id", "drift_ms", "drift_relative", "retention_s",
                    "intensity", "on", "gc_ims", "gc", "ims", "trigger"):
            assert col in PEAK_TABLE_COLUMNS

    def test_peak_table_column_order(self):
        """Column order (第四階段：ri column inserted after retention_s):
             # | drift_ms | drift_relative | retention_s | RI | intensity | On | GC×IMS | GC | IMS | ▶
        """
        expected = ("peak_id", "drift_ms", "drift_relative", "retention_s", "ri",
                    "intensity", "on", "gc_ims", "gc", "ims", "trigger")
        assert PEAK_TABLE_COLUMNS == expected

    def test_peak_table_columns_length(self):
        """Peak table has 11 columns (10 + ri for RT→RI 第四階段)."""
        assert len(PEAK_TABLE_COLUMNS) == 11


class TestCellValueLogic:
    """Test _cell_value display logic (Batch 2)."""

    @staticmethod
    def _mock_app():
        """Minimal object with _cell_value method for testing."""
        from main import GCIMSApp
        # We only need the _cell_value method, not full Tk setup.
        # Create a minimal shim: bind the unbound method to a bare object.
        class _Shim:
            pass
        shim = _Shim()
        shim._cell_value = GCIMSApp._cell_value.__get__(shim, _Shim)
        return shim

    def test_on_column_shows_checkbox_glyph(self):
        from main import CHECK_ON, CHECK_OFF
        app = self._mock_app()
        assert app._cell_value("on", {"rule_active": True}) == CHECK_ON
        assert app._cell_value("on", {"rule_active": False}) == CHECK_OFF
        # default is active
        assert app._cell_value("on", {}) == CHECK_ON

    def test_on_column_reflects_user_override_not_rule(self):
        """The checkbox shows the *effective* state, so a manual pick wins.

        Design decision (a): rules are heuristics, the user is the arbiter.
        A peak the rules rejected but the user kept must read as selected,
        otherwise the table would contradict the circle on the heatmap.
        """
        from main import CHECK_ON, CHECK_OFF
        app = self._mock_app()
        # rules said no, user rescued it
        assert app._cell_value(
            "on", {"rule_active": False, "user_active": True}) == CHECK_ON
        # rules said yes, user dropped it
        assert app._cell_value(
            "on", {"rule_active": True, "user_active": False}) == CHECK_OFF

    def test_ri_column_rendering(self):
        """RI cell: dash when uncalibrated, value when present, '*' when extrapolated."""
        app = self._mock_app()
        assert app._cell_value("ri", {}) == "—"                     # no calibration
        assert app._cell_value("ri", {"ri": None}) == "—"
        assert app._cell_value("ri", {"ri": 726.34}) == "726.3"     # interpolated
        assert app._cell_value(
            "ri", {"ri": 1097.1, "ri_extrapolated": True}) == "1097.1*"  # extrapolated

    def test_gc_ims_empty_shows_dash(self):
        app = self._mock_app()
        assert app._cell_value("gc_ims", {}) == "—"
        assert app._cell_value("gc_ims", {"matches": {"combined_matches": []}}) == "—"

    def test_gc_ims_shows_top_compound_name(self):
        app = self._mock_app()
        peak = {"matches": {"combined_matches": [{"Name": "Diacetyl", "CAS": "C1"}]}}
        assert app._cell_value("gc_ims", peak) == "Diacetyl"

    def test_gc_column_shows_matched_ri_value(self):
        app = self._mock_app()
        # No matches → "—" (not run)
        assert app._cell_value("gc", {}) == "—"
        # Shows the closest (delta-sorted [0]) hit's *RI value* + its Δ, so the
        # user can compare it against the peak's own RI. No compound name.
        peak = {"matches": {"combined_matches": [], "gc_matches": [
            {"Name": "Ethanol", "CAS": "A", "RI": 720.3, "delta_ri": 0.12,
             "match_dimensions": ["ri"]},
            {"NAME": "Acetone", "CAS": "B", "RI": 721.0, "delta_ri": 1.1,
             "match_dimensions": ["ri"]},
        ]}}
        v = app._cell_value("gc", peak)
        assert v == "720.3 (Δ0.12)"        # matched RI value + delta, closest first
        assert "Ethanol" not in v          # deliberately no compound name

    def test_ims_dash_when_no_drift_hit(self):
        app = self._mock_app()
        assert app._cell_value("ims", {}) == "—"                       # no matches
        assert app._cell_value("ims", {"matches": {"ims_matches": []}}) == "—"

    def test_ims_shows_matched_drift_value(self):
        """IMS column shows the closest RIP-relative drift library value + Δ, to
        read against the peak's own Drift rel. RIP column."""
        app = self._mock_app()
        peak = {"matches": {"combined_matches": [], "ims_matches": [
            {"Name": "Guaiacol", "CAS": "A", "Dt[a.u.]": 1.139,
             "delta_drift_rel": 0.026, "match_dimensions": ["drift_rel"]},
        ]}}
        assert app._cell_value("ims", peak) == "1.139 (Δ0.026)"

    def test_trigger_dims_when_inactive(self):
        from main import TRIGGER_ACTIVE, TRIGGER_DIM
        app = self._mock_app()
        assert app._cell_value("trigger", {"rule_active": True}) == TRIGGER_ACTIVE
        assert app._cell_value("trigger", {"rule_active": False}) == TRIGGER_DIM
        # A rescued peak is usable again — ▶ follows the effective state
        assert app._cell_value(
            "trigger", {"rule_active": False, "user_active": True}) == TRIGGER_ACTIVE


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
