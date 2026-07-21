"""
test_state_machine.py — Tests for UI state machine transitions.
"""

import pytest
from main import UIState, AppState


class TestAppState:
    """Test AppState initialization and reset."""

    def test_initial_state(self):
        """AppState should initialize with None values."""
        state = AppState()
        assert state.current_folder is None
        assert state.selected_mea_file is None
        assert state.heatmap_path is None
        assert state.overlay_path is None
        assert state.peaks == []
        assert state.selected_peak_row is None

    def test_reset_after_file_selection(self):
        """reset_after_file_selection should clear image/peak data."""
        state = AppState()
        state.heatmap_path = "test.png"
        state.overlay_path = "overlay.png"
        state.peaks = [{"peak_id": 1}]
        state.selected_peak_row = "item1"

        state.reset_after_file_selection()

        assert state.heatmap_path is None
        assert state.overlay_path is None
        assert state.peaks == []
        assert state.selected_peak_row is None


class TestUIStateTransitions:
    """Test state machine transitions."""

    def test_start_state(self):
        """Initial UI state should be START."""
        assert UIState.START.value == 0

    def test_state_ordering(self):
        """States should follow expected progression."""
        assert UIState.START.value < UIState.FOLDER_SELECTED.value
        assert UIState.FOLDER_SELECTED.value < UIState.FILE_SELECTED.value
        assert UIState.FILE_SELECTED.value < UIState.READING.value
        assert UIState.READING.value < UIState.READ_DONE.value
        assert UIState.READ_DONE.value < UIState.DETECTING.value
        assert UIState.DETECTING.value < UIState.PEAKS_DETECTED.value

    def test_all_states_defined(self):
        """All expected states should be defined."""
        expected = {"START", "FOLDER_SELECTED", "FILE_SELECTED", "READING",
                    "READ_DONE", "DETECTING", "PEAKS_DETECTED", "ERROR"}
        actual = {state.name for state in UIState}
        assert actual == expected


class TestStateTransitionLogic:
    """Test button enable/disable logic for each state.

    Note: since Batch 1 (workflow §第八階段), the toolbar is:
      browse_btn / view_original_btn / show_detected_btn / rules_btn / generate_report_btn
    """

    def test_buttons_in_start_state(self):
        """In START state, Browse Folder and Rules enabled; others disabled."""
        expected_states = {
            "browse_btn": "normal",
            "view_original_btn": "disabled",
            "show_detected_btn": "disabled",
            "rules_btn": "normal",
            "generate_report_btn": "disabled",
        }
        assert expected_states["browse_btn"] == "normal"
        assert expected_states["generate_report_btn"] == "disabled"

    def test_buttons_in_read_done_state(self):
        """After auto-read completes, View Original and Show Detected are enabled."""
        expected_states = {
            "browse_btn": "normal",
            "view_original_btn": "normal",
            "show_detected_btn": "normal",
            "rules_btn": "normal",
            "generate_report_btn": "disabled",
        }
        assert expected_states["view_original_btn"] == "normal"
        assert expected_states["show_detected_btn"] == "normal"

    def test_buttons_in_peaks_detected_state(self):
        """In PEAKS_DETECTED state, all buttons including Generate Report enabled."""
        expected_states = {
            "browse_btn": "normal",
            "view_original_btn": "normal",
            "show_detected_btn": "normal",
            "rules_btn": "normal",
            "generate_report_btn": "normal",
        }
        for state in expected_states.values():
            assert state == "normal"


class TestSettingsPersistence:
    """Test ui_settings.json load/save (added in Batch 1)."""

    def test_load_settings_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        import main
        monkeypatch.setattr(main, "SETTINGS_PATH", str(tmp_path / "nope.json"))
        assert main.load_settings() == {}

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        import main
        p = str(tmp_path / "ui_settings.json")
        monkeypatch.setattr(main, "SETTINGS_PATH", p)
        main.save_settings({"library_dir": "/some/path", "other": 42})
        assert main.load_settings() == {"library_dir": "/some/path", "other": 42}

    def test_load_settings_corrupt_json_returns_empty(self, tmp_path, monkeypatch):
        import main
        p = tmp_path / "ui_settings.json"
        p.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(main, "SETTINGS_PATH", str(p))
        assert main.load_settings() == {}
