# tests/test_glossary_enforce.py
from __future__ import annotations

import json

from modpack_translator.pipeline.glossary import Glossary, load_merged_glossary


def _g() -> Glossary:
    return Glossary({
        "Twilight Forest": "暮光森林",
        "Applied Energistics 2": "應用能源2",
        "Create": "機械動力",
    })


def test_multiword_replaced_mid_sentence():
    assert _g().enforce("歡迎來到 Twilight Forest！") == "歡迎來到暮光森林！"


def test_multiword_plural_tolerated():
    assert _g().enforce("探索 Twilight Forests 區域") == "探索暮光森林 區域"


def test_singleword_only_replaced_when_whole_string():
    g = _g()
    assert g.enforce("Create") == "機械動力"
    assert g.enforce("  Create\n") == "  機械動力\n"
    # 句中單字詞不動（避免動詞 create/標題 Create New World 誤傷）
    assert g.enforce("Create New World") == "Create New World"


def test_case_sensitive():
    g = _g()
    assert g.enforce("please create a farm") == "please create a farm"
    assert g.enforce("歡迎來到 twilight forest") == "歡迎來到 twilight forest"


def test_annotation_style_skipped():
    # 譯名已出現在譯文中：視為刻意的中英夾註，不重複替換
    assert _g().enforce("暮光森林(Twilight Forest)入門") == "暮光森林(Twilight Forest)入門"


def test_word_boundary_not_partial():
    g = Glossary({"Aether": "天境"})
    assert g.enforce("Aethersteel 合金") == "Aethersteel 合金"


def test_empty_glossary_noop():
    assert Glossary({}).enforce("Twilight Forest") == "Twilight Forest"


# ── 自訂用語庫：單字專有名詞句中強制替換（素材名一致性） ──────────────

def _gc() -> Glossary:
    # 對齊 load_merged_glossary 輸出：大小寫去重後只保留 Allthemodium 一鍵
    return Glossary(
        {"Unobtainium": "難得素", "Vibranium": "汎合金", "Allthemodium": "阿摩金"},
        custom_keys={"Unobtainium", "Vibranium", "Allthemodium"},
    )


def test_custom_singleword_replaced_mid_sentence():
    assert _gc().enforce("結合振金與 Unobtainium 的方法") == "結合振金與難得素的方法"


def test_custom_singleword_after_color_code():
    # 任務標題常見 &5Unobtainium：色碼數字緊貼詞,仍要替換,並吃掉尾隨空格
    assert _gc().enforce("&5Unobtainium 工具") == "&5難得素工具"
    assert _gc().enforce("&6Allthemodium&r 之星") == "&6阿摩金&r 之星"


def test_custom_singleword_after_hex_color_code():
    # FTB &#RRGGBB 十六進位色碼緊貼詞:&#F2EB2EAllthemodium
    assert _gc().enforce("&#F2EB2EAllthemodium 蜜蜂") == "&#F2EB2E阿摩金蜜蜂"


def test_custom_singleword_eats_trailing_space_before_cjk():
    assert _gc().enforce("Unobtainium 工具") == "難得素工具"
    # 後面接英文則保留原樣的空白
    assert _gc().enforce("Unobtainium tools") == "難得素 tools"


def test_custom_singleword_matches_mixed_case_not_lowercase_paths():
    g = _gc()
    # 全小寫（圖片路徑/資源位置）不得替換,以免破壞 atm:textures/allthemodium/…
    assert g.enforce("{image:atm:textures/allthemodium/x.png}") == \
        "{image:atm:textures/allthemodium/x.png}"
    # 散文中的混寫大小寫都統一(即使用語庫只存 Allthemodium 一種寫法)
    assert g.enforce("AllTheModium 金屬") == "阿摩金金屬"
    assert g.enforce("&6AllTheModium&r 之星") == "&6阿摩金&r 之星"


def test_official_singleword_still_conservative():
    # 非自訂的單字詞（官方詞庫）維持原本保守行為：句中不替換
    g = Glossary({"Create": "機械動力"}, custom_keys=set())
    assert g.enforce("Create New World") == "Create New World"


def test_custom_word_boundary_not_partial():
    g = Glossary({"Vibranium": "汎合金"}, custom_keys={"Vibranium"})
    # 不得誤傷更長的詞
    assert g.enforce("Vibraniumium 測試") == "Vibraniumium 測試"


def test_load_merged_marks_custom_singleword_inline(tmp_path):
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps({"Unobtainium": "難得素"}), encoding="utf-8")
    g = load_merged_glossary(None, None, custom)
    assert g is not None
    # 自訂單字詞句中生效
    assert g.enforce("挖掘 Unobtainium 礦石") == "挖掘難得素礦石"
