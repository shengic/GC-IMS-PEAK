"""
test_ui_ri.py — Headless tests for the Stage-4 RT→RI UI wiring in main.py.

These exercise the *logic* of two methods without a live Tk window, using the
same unbound-method-on-a-shim trick as test_peak_table.py:

  - GCIMSApp._peak_to_image_xy: places a peak's circle by peak['ri'] when the
    backdrop geometry is in RI space (geom['y_axis']=='ri'), else by retention_s.
  - GCIMSApp._on_calibration_ready / _folder_cal_status: the background
    calibration-ready state transition (store when absolute, clear otherwise,
    ignore a result for a folder the user already navigated away from).

The live Tk canvas rendering itself is verified by running the app, not here.
"""
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from main import GCIMSApp, AppState


def _bind(method_name):
    """Return a bare shim object with `method_name` bound as an instance method."""
    class _Shim:
        pass
    shim = _Shim()
    setattr(shim, method_name,
            getattr(GCIMSApp, method_name).__get__(shim, _Shim))
    return shim


class _FakeLabel:
    """Records .config(text=...) calls so we can assert on status updates."""
    def __init__(self):
        self.texts = []

    def config(self, **kw):
        if "text" in kw:
            self.texts.append(kw["text"])


# --------------------------------------------------------------------------- #
# _peak_to_image_xy — RI vs retention_s coordinate placement
# --------------------------------------------------------------------------- #
# A geometry chosen so the arithmetic is easy to check by hand:
#   x0,y0,bw,bh = 0.1, 0.1, 0.8, 0.8 (figure fractions), png 1000x1200.
#   A data point at the 50% mark of both axes lands at fraction (0.5, 0.5),
#   i.e. pixel (500, 600) after the y-flip (canvas y runs downward).
_GEOM_RI = {
    "png_size": [1000, 1200],
    "axes_bbox": [0.1, 0.1, 0.8, 0.8],
    "xlim": [0.0, 4.0],
    "ylim": [500.0, 1100.0],      # RI range
    "y_axis": "ri",
}
_GEOM_RT = {
    "png_size": [1000, 1200],
    "axes_bbox": [0.1, 0.1, 0.8, 0.8],
    "xlim": [0.0, 4.0],
    "ylim": [0.0, 2000.0],        # retention_s range
    "y_axis": "retention_s",
}


def _xy_shim(geom):
    shim = _bind("_peak_to_image_xy")
    shim.state = AppState()
    shim.state.canvas_geometry = geom
    return shim


def test_peak_xy_uses_ri_when_geometry_is_ri():
    shim = _xy_shim(_GEOM_RI)
    # ri=800 is the midpoint of [500,1100]; retention_s is deliberately absurd to
    # prove it is NOT used in RI mode.
    xy = shim._peak_to_image_xy({"drift_relative": 2.0, "ri": 800.0,
                                 "retention_s": 999999})
    assert xy is not None
    x, y = xy
    assert abs(x - 500.0) < 1e-6
    assert abs(y - 600.0) < 1e-6


def test_peak_xy_uses_retention_when_geometry_is_rt():
    shim = _xy_shim(_GEOM_RT)
    # retention_s=1000 is the midpoint of [0,2000]; ri is absurd to prove it is
    # NOT used in retention mode.
    xy = shim._peak_to_image_xy({"drift_relative": 2.0, "retention_s": 1000.0,
                                 "ri": 999999})
    assert xy is not None
    x, y = xy
    assert abs(x - 500.0) < 1e-6
    assert abs(y - 600.0) < 1e-6


def test_peak_xy_defaults_to_retention_when_y_axis_absent():
    geom = dict(_GEOM_RT)
    geom.pop("y_axis")                       # legacy geometry without the flag
    shim = _xy_shim(geom)
    xy = shim._peak_to_image_xy({"drift_relative": 0.0, "retention_s": 0.0})
    assert xy is not None                    # falls back to retention_s, no crash


def test_peak_xy_none_when_ri_missing_in_ri_mode():
    shim = _xy_shim(_GEOM_RI)
    # RI backdrop but this peak has no ri -> can't place it -> None (skip drawing)
    assert shim._peak_to_image_xy({"drift_relative": 1.0, "ri": None}) is None
    assert shim._peak_to_image_xy({"drift_relative": 1.0}) is None


def test_peak_xy_none_without_geometry():
    shim = _bind("_peak_to_image_xy")
    shim.state = AppState()                  # canvas_geometry stays None
    assert shim._peak_to_image_xy({"drift_relative": 1.0, "ri": 800.0}) is None


# --------------------------------------------------------------------------- #
# _on_calibration_ready — background calibration state transition
# --------------------------------------------------------------------------- #
def _ready_shim(current_folder="/batch"):
    shim = _bind("_on_calibration_ready")
    shim.state = AppState()
    shim.state.current_folder = current_folder
    shim.status_label = _FakeLabel()
    shim._populate_calls = []
    shim.populate_peak_table = lambda peaks: shim._populate_calls.append(peaks)
    return shim


_ABS_CAL = {"mode": "multi_point_loglinear", "assumed_unverified": True,
            "anchor_selection": {"n_clean_anchors": 6}}


def test_calibration_ready_stores_absolute():
    shim = _ready_shim("/batch")
    shim._on_calibration_ready("/batch", _ABS_CAL, "batch_own_std")
    assert shim.state.ri_calibration is _ABS_CAL
    assert shim.status_label.texts and "Retention Index" in shim.status_label.texts[-1]


def test_calibration_ready_repopulates_when_peaks_loaded():
    shim = _ready_shim("/batch")
    shim.state.peaks = [{"peak_id": 1}]
    shim._on_calibration_ready("/batch", _ABS_CAL, "batch_own_std")
    assert shim._populate_calls == [shim.state.peaks]   # table refreshed with RI


def test_calibration_ready_clears_when_not_absolute():
    shim = _ready_shim("/batch")
    shim.state.ri_calibration = "stale"
    # relative-mode result -> no absolute RI -> cleared, axis stays retention time
    shim._on_calibration_ready("/batch",
                               {"mode": "single_point_relative"}, "batch_own_std")
    assert shim.state.ri_calibration is None
    assert "unavailable" in shim.status_label.texts[-1].lower()


def test_calibration_ready_clears_when_none():
    shim = _ready_shim("/batch")
    shim._on_calibration_ready("/batch", None, "unavailable")
    assert shim.state.ri_calibration is None


def test_calibration_ready_ignored_after_folder_change():
    shim = _ready_shim("/new_folder")          # user already moved on
    shim.state.ri_calibration = "sentinel"
    shim._on_calibration_ready("/old_folder", _ABS_CAL, "batch_own_std")
    assert shim.state.ri_calibration == "sentinel"   # untouched
    assert shim.status_label.texts == []             # no status update either


def test_apply_cached_matches_by_coordinate():
    shim = _bind("_apply_cached_matches")
    shim.state = AppState()
    shim.state.match_cache = {
        (100, 50): {"gc_matches": [{"RI": 726.0, "delta_ri": 0.3,
                                    "match_dimensions": ["ri"]}]},
    }
    peaks = [{"rt_index": 100, "dt_index": 50},   # cached
             {"rt_index": 999, "dt_index": 9}]    # not cached
    shim._apply_cached_matches(peaks)
    assert peaks[0]["matches"]["gc_matches"][0]["RI"] == 726.0
    assert "matches" not in peaks[1]               # uncached row untouched


def test_folder_cal_status_only_when_still_on_folder():
    shim = _bind("_folder_cal_status")
    shim.state = AppState()
    shim.state.current_folder = "/batch"
    shim.status_label = _FakeLabel()
    shim._folder_cal_status("/batch", "detecting…")
    shim._folder_cal_status("/other", "should be dropped")
    assert shim.status_label.texts == ["detecting…"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("✓ all Stage-4 UI-wiring checks passed")
