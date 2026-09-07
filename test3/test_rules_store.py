"""test_rules_store.py — 逐檔規則覆寫（`compound_consensus/rules_store.py`）。

兩層設計，**兩層的壽命刻意不同**：

- **預設**：記憶體，關掉就回到 `rules_config.json`（「這一輪想怎麼看」）
- **逐檔覆寫**：`results/<base>_rules3.json`，留到下一次（「對這個檔的判斷」）

守的症狀（都不會報錯）：

- 共用同一個 dict → 改 A 檔的參數把 B 檔一起改掉
- 預設殘留到下一次執行 → 使用者納悶峰數為什麼跟 `rules_config.json` 不一樣
- 反過來，連覆寫一起清掉 → 對個別檔案的判斷每次開都要重做
- 覆寫檔壞掉 → 整支應用打不開，或半套地套用一份看不懂的設定
- 傳錯 config 給 `params_fingerprint()` → 快取無聲地對應到別人的參數
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import areas2  # noqa: E402
from compound_consensus import logic as L  # noqa: E402
from compound_consensus import rules_store as RS  # noqa: E402


@pytest.fixture()
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(areas2, "RESULTS_DIR", str(tmp_path))
    return tmp_path


def _cfg(threshold=40, top_n=0):
    return [{"rule_number": "R001", "enabled": True,
             "params": {"threshold": threshold}},
            {"rule_number": "R004", "enabled": True,
             "params": {"top_n": top_n}}]


# --------------------------------------------------------------------------- #
# 檔名與隔離
# --------------------------------------------------------------------------- #
def test_the_two_layers_have_different_lifetimes(results):
    """**預設不寫檔、逐檔覆寫要寫檔。** 這是本模組的核心設計。

    改「全部檔案」是這一輪想怎麼看（調 `top_n` 看看少幾顆峰），下次打開不該還黏著；
    把某個檔**特別**調成不一樣是對那個檔的判斷，重開一次不該要人重做一遍。
    """
    RS.save_default(_cfg(threshold=7))
    assert os.listdir(results) == [], "預設不可以寫出任何檔"

    RS.save_override("/x/a.mea", _cfg(threshold=99))
    assert os.listdir(results) == ["a_rules3.json"], "逐檔覆寫要落地"


def test_override_file_carries_the_third_apps_suffix(results):
    """檔名帶 `3`，不可以撞到前兩支應用的產物（隔離規則 2）。"""
    p = RS.rules_path("/x/SAMPLE.mea")
    assert os.path.basename(p) == "SAMPLE_rules3.json"
    assert "_rules.json" not in os.path.basename(p)


def test_nothing_is_set_until_you_save(results):
    assert RS.load_override("/x/a.mea") is None
    assert RS.has_override("/x/a.mea") is False
    assert RS.customised_files() == []


def test_reset_session_clears_the_default_but_keeps_overrides(results):
    """`reset_session()` 是「重開一次」：預設歸零，**自訂的檔留著**。

    回歸測試：一度做成連覆寫一起清，於是使用者對個別檔案的判斷每次開都要重做。
    """
    RS.save_override("/x/a.mea", _cfg(threshold=99))
    RS.save_default(_cfg(threshold=7))
    RS.reset_session()
    assert RS.load_default(_cfg(threshold=40))[1] == "shared", "預設要歸零"
    assert RS.has_override("/x/a.mea") is True, "自訂過的檔要留著"
    assert RS.load_override("/x/a.mea")[0]["params"]["threshold"] == 99


def test_a_broken_override_is_ignored_not_fatal(results):
    """壞掉的覆寫不該讓應用打不開，也不該被半套地套用。"""
    with open(RS.rules_path("/x/a.mea"), "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    assert RS.load_override("/x/a.mea") is None
    assert RS.effective("/x/a.mea", _cfg())[1] == "default", \
        "讀不動就退回預設，不是丟例外"


def test_an_override_from_a_future_schema_is_ignored(results):
    """版本對不上就當成沒有。悄悄套用一份看不懂的設定比忽略它更糟。"""
    import json as _json
    with open(RS.rules_path("/x/a.mea"), "w", encoding="utf-8") as f:
        _json.dump({"schema": RS.SCHEMA_VERSION + 1, "rules": _cfg(1)}, f)
    assert RS.load_override("/x/a.mea") is None


# --------------------------------------------------------------------------- #
# 存 / 讀
# --------------------------------------------------------------------------- #
def test_save_then_load_round_trips(results):
    RS.save_override("/x/a.mea", _cfg(threshold=99, top_n=7))
    got = {e["rule_number"]: e for e in RS.load_override("/x/a.mea")}
    assert got["R001"]["params"]["threshold"] == 99
    assert got["R004"]["params"]["top_n"] == 7


def test_saved_config_is_a_snapshot_not_a_live_reference(results):
    """存的是深拷貝——呼叫端之後再改不可以回頭污染磁碟上那一份。"""
    cfg = _cfg(threshold=40)
    RS.save_override("/x/a.mea", cfg)
    cfg[0]["params"]["threshold"] = 999
    got = {e["rule_number"]: e for e in RS.load_override("/x/a.mea")}
    assert got["R001"]["params"]["threshold"] == 40


def test_the_default_does_not_survive_a_reset(results):
    """改過的預設不跨執行保留——這正是「每次重開都乾淨」的意思。"""
    RS.save_default(_cfg(threshold=99))
    assert RS.load_default(_cfg(threshold=40))[1] == "session"
    RS.reset_session()
    cfg, src = RS.load_default(_cfg(threshold=40))
    assert src == "shared"
    assert cfg[0]["params"]["threshold"] == 40


def test_clear_override_puts_the_file_back_on_the_default(results):
    RS.save_override("/x/a.mea", _cfg(threshold=99))
    assert RS.clear_override("/x/a.mea") is True
    assert RS.effective("/x/a.mea", _cfg(threshold=40))[1] == "default"
    assert RS.clear_override("/x/a.mea") is False, "沒有東西可清就回 False"


# --------------------------------------------------------------------------- #
# effective() —— 來源要跟著回傳
# --------------------------------------------------------------------------- #
def test_effective_reports_where_the_rules_came_from(results):
    """**來源要跟著規則走**。

    逐檔覆寫之後，兩個檔同一組看起來一樣的參數卻給出不同的峰數是完全可能的。
    不說是哪一份，使用者只會覺得程式壞了。同 `ri_mode` / `k0_mode` 的原則。
    """
    assert RS.effective("/x/a.mea", _cfg())[1] == "default"
    RS.save_override("/x/a.mea", _cfg(threshold=99))
    cfg, src = RS.effective("/x/a.mea", _cfg())
    assert src == "custom"
    assert {e["rule_number"]: e for e in cfg}["R001"]["params"]["threshold"] == 99


def test_effective_hands_back_a_copy_so_editing_one_file_does_not_move_others(
        results):
    """兩個沒有覆寫的檔不可以共用同一個 dict——改 A 會把 B 一起改掉。"""
    default = _cfg(threshold=40)
    a, _ = RS.effective("/x/a.mea", default)
    b, _ = RS.effective("/x/b.mea", default)
    a[0]["params"]["threshold"] = 999
    assert b[0]["params"]["threshold"] == 40
    assert default[0]["params"]["threshold"] == 40, "連預設本身都不可以被改到"


def test_effective_without_a_path_is_the_default(results):
    """還沒載入任何檔時就是預設。"""
    assert RS.effective(None, _cfg())[1] == "default"


# --------------------------------------------------------------------------- #
# 套用到一整組
# --------------------------------------------------------------------------- #
def test_apply_to_all_sets_every_member_and_skips_the_source(results):
    grp = ["/x/a.mea", "/x/b.mea", "/x/c.mea"]
    n = RS.apply_to_all(grp, _cfg(threshold=77), skip={"/x/a.mea"})
    assert n == 2, "來源檔不重複寫"
    for m in ("/x/b.mea", "/x/c.mea"):
        got = {e["rule_number"]: e for e in RS.load_override(m)}
        assert got["R001"]["params"]["threshold"] == 77
    assert RS.has_override("/x/a.mea") is False


def test_applied_members_do_not_share_one_dict(results):
    """每個檔各存一份。共用的話，之後單獨改一個檔會動到全部。"""
    grp = ["/x/a.mea", "/x/b.mea"]
    RS.apply_to_all(grp, _cfg(threshold=77))
    a = RS.load_override("/x/a.mea")
    a[0]["params"]["threshold"] = 1
    RS.save_override("/x/a.mea", a)
    b = {e["rule_number"]: e for e in RS.load_override("/x/b.mea")}
    assert b["R001"]["params"]["threshold"] == 77


def test_customised_files_lists_what_is_pinned(results):
    """面板要數「有幾個檔已自訂」，靠這個。回的是**檔名主幹**。

    覆寫是照 `.mea` 的檔名存的，從 `results/` 拿不回原本的資料夾——所以這裡不能
    假裝回得出完整路徑。
    """
    assert RS.customised_files() == []
    RS.save_override("/x/b.mea", _cfg())
    RS.save_override("/y/a.mea", _cfg())
    assert RS.customised_files() == ["a", "b"]
    RS.clear_override("/y/a.mea")
    assert RS.customised_files() == ["b"]


# --------------------------------------------------------------------------- #
# 與參數指紋的關係 —— 這是逐檔規則唯一的風險點
# --------------------------------------------------------------------------- #
def _pre_gate_cfg(half_width=0.02, boundary=1.0):
    """只含**會改變偵測**的兩條強制規則（R004 / R006）。"""
    return [{"rule_number": "R004", "enabled": True,
             "params": {"half_width": half_width}},
            {"rule_number": "R006", "enabled": True,
             "params": {"boundary": boundary}}]


def test_detection_changing_rules_give_different_fingerprints(results):
    """強制規則的參數不同，指紋就要不同——否則快取會把 A 檔的峰當成 B 檔的。

    R004/R006 在突出度門檻**之前**生效（`peaks.pre_gate_params()`），改它們會
    改變哪些峰活得下來，不是只改標記。逐檔規則之後這條特別要緊：同一個資料夾裡
    兩個檔可以有不同的 `half_width`，指紋不分開就會互相污染。
    """
    assert (L.params_fingerprint(_pre_gate_cfg(half_width=0.02))
            != L.params_fingerprint(_pre_gate_cfg(half_width=0.05)))
    assert (L.params_fingerprint(_pre_gate_cfg(boundary=1.0))
            != L.params_fingerprint(_pre_gate_cfg(boundary=1.2)))


def test_marking_only_rules_do_not_invalidate_the_cache(results):
    """**選配規則改了不該讓整批重跑。**

    R001/R002/R003/R005 只呼叫 `mark_rules()` 標記 `rule_active`，不移除任何峰。
    把整份 config 丟進指紋的話，「調一下 R002 的 top_n」會害 18 個檔各重跑 55 秒
    （約 16 分鐘）換來一模一樣的峰。這是刻意的設計，不是漏掉。
    """
    a = _pre_gate_cfg() + [{"rule_number": "R002", "enabled": True,
                            "params": {"top_n": 0}}]
    b = _pre_gate_cfg() + [{"rule_number": "R002", "enabled": True,
                            "params": {"top_n": 10}}]
    assert L.params_fingerprint(a) == L.params_fingerprint(b)


def test_same_rules_give_the_same_fingerprint_regardless_of_order(results):
    """只是順序不同不該讓整批重跑（18 檔 × 55 秒 ≈ 16 分鐘）。"""
    cfg = _cfg()
    assert L.params_fingerprint(cfg) == L.params_fingerprint(list(reversed(cfg)))
