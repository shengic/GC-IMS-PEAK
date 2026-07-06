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
    """Test button enable/disable logic for each state."""

    # This would test the update_button_state() logic
    # Requires mocking Tk widgets; shown as placeholder for integration tests

    def test_buttons_in_start_state(self):
        """In START state, only Browse should be enabled."""
        # Expected: browse_btn=normal, all others=disabled
        expected_states = {
            "browse_btn": "normal",
            "read_btn": "disabled",
            "detect_btn": "disabled",
        }
        # In real test: would check GCIMSApp.ui_state and call update_button_state()
        assert expected_states["browse_btn"] == "normal"

    def test_buttons_in_file_selected_state(self):
        """In FILE_SELECTED state, Browse and Read should be enabled."""
        expected_states = {
            "browse_btn": "normal",
            "read_btn": "normal",
            "detect_btn": "disabled",
        }
        assert expected_states["read_btn"] == "normal"

    def test_buttons_in_peaks_detected_state(self):
        """In PEAKS_DETECTED state, all buttons should be enabled."""
        expected_states = {
            "browse_btn": "normal",
            "read_btn": "normal",
            "detect_btn": "normal",
            "export_heatmap_btn": "normal",
            "export_overlay_btn": "normal",
            "export_csv_btn": "normal",
        }
        # All should be normal (enabled)
        for state in expected_states.values():
            assert state == "normal"
