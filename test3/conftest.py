"""test3 的共用設定。

**每一項測試都在自己的暫存 `results/` 裡跑。**

為什麼需要這條：第三支應用會寫 `results/` 底下的產物（`_peaks_state3.json`、
`_rules3.json`、`_rules3_default.json`、指紋 sidecar）。沒有隔離的話：

1. 測試會污染**專案真正的** `results/`，把使用者自己的設定改掉；
2. 更難查的是**測試之間互相污染**——實際發生過：某一項測試把
   `_rules3_default.json` 的 R001 門檻寫成 40，之後
   `test_..._mandatory_rule_param_warns_...` 讀到它當起點，於是「改了 R004 參數」
   前後的 `pre_gate_params()` 相同，警告沒出現，測試失敗。症狀出現在一項完全
   無關的測試上，而且**只在整批跑的時候才會發生**。

自己另外 monkeypatch `RESULTS_DIR` 的測試不受影響（後設定的贏）。
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import areas2  # noqa: E402
from compound_consensus import rules_store as _rules_store  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_rules_session():
    """規則覆寫是**行程層級**的字典，測試之間一定要清掉。

    不清的話前一項測試設的 `top_n` 會漏到下一項，症狀是「單獨跑會過、整批跑會炸」
    ——最難查的那一種。應用本身在 `ConsensusApp.__init__` 也做同一件事。
    """
    _rules_store.reset_session()
    yield
    _rules_store.reset_session()


@pytest.fixture(autouse=True)
def _isolated_results(tmp_path_factory, monkeypatch):
    """把 `areas2.RESULTS_DIR` 指到這一項測試專屬的暫存資料夾。

    **刻意不放在 `tmp_path` 裡面。** 很多測試自己就在 `tmp_path` 下建 `results/`，
    或斷言 `os.listdir(tmp_path) == []`；在那裡插一個資料夾會撞名，也會讓那些
    斷言看到一個它們沒建的東西。用獨立的暫存目錄，對測試完全隱形。
    """
    d = tmp_path_factory.mktemp("results3")
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(d))
    return d
