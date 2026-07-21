"""真換行（0x0A）保留：JSON/SNBT lang 值經 json.loads 解碼後 \\n 變成真換行，
是 GUI 的硬折行（固定寬度框逐行排版）。encode() 不 token 化真換行（_PLACEHOLDERS
只收字面 \\n），故模型整串翻譯時可自由重排、把多行併成長行 → 超出依英文行寬
設計的框（overflow）。is_usable_translation 須拒絕「內部換行變少」的整串譯文，
迫使呼叫端回退逐行分段翻譯（保留每個 \\n 分隔）。

字面 \\n（反斜線+n，來自 legacy/bq lang）另由 _preserves_required_tokens 保護，
與此處的真換行檢查互不干涉——見 test_preprocessor_lang_compat.py。
"""
from modpack_translator.pipeline.preprocessor import diff_keys, is_usable_translation
from modpack_translator.pipeline.runner import _translate_segmented_text


# ── 單元：內部真換行不得變少 ─────────────────────────────────────────

def test_merged_internal_newline_rejected():
    source = "Open the menu\nSelect an item"
    assert not is_usable_translation(source, "開啟選單並選取一個項目")  # 併行
    assert is_usable_translation(source, "開啟選單\n選取一個項目")       # 逐行保留


def test_paragraph_break_collapse_rejected():
    source = "First paragraph.\n\nSecond paragraph."
    assert not is_usable_translation(source, "第一段。\n第二段。")   # \n\n→\n
    assert is_usable_translation(source, "第一段。\n\n第二段。")


def test_trailing_newline_drop_allowed():
    # 尾端換行 GUI 不顯示，增減無害——不得churn 這類（佔實測 drop 的絕大多數）
    assert is_usable_translation("Resizes the hitbox.\n", "調整碰撞箱大小。")
    assert is_usable_translation("A\n\n", "甲")


def test_added_newline_allowed():
    # 譯文比原文多換行（把長句折成兩行以塞進框）無害
    assert is_usable_translation("A single long line here", "一行\n很長的內容")


def test_single_line_unaffected():
    # 無真換行的來源不受此規則影響（Tank 這類 bare word 走既有邏輯）
    assert is_usable_translation("Tank", "坦克")


def test_fancymenu_multiline_tooltip():
    source = (
        "This source type lets you load local resources\n"
        "saved in Minecraft's instance directory.\n"
        "You need to save all of FancyMenu's resources\n"
        "to '/config/fancymenu/assets/'."
    )
    merged = (
        "此來源類型可讓你載入儲存在 Minecraft 實例目錄中的本機資源。\n"
        "你需要將所有 FancyMenu 的資源\n儲存到「/config/fancymenu/assets/」。"
    )  # 4 行併成 3 行 → 首行過長
    faithful = (
        "此來源類型可讓你載入本機資源，\n"
        "儲存在 Minecraft 的實例目錄中。\n"
        "你需要將所有 FancyMenu 的資源\n"
        "儲存到「/config/fancymenu/assets/」。"
    )
    assert not is_usable_translation(source, merged)
    assert is_usable_translation(source, faithful)


# ── diff：既有併行譯文須被重新標記待翻，忠實者不動 ───────────────────

def test_diff_flags_merged_but_keeps_faithful():
    en = {
        "a": "Line one\nLine two\nLine three",
        "b": "Line one\nLine two\nLine three",
    }
    zh = {
        "a": "一\n二三",     # 3 行併成 2 行 → 重翻
        "b": "一\n二\n三",   # 忠實 → 不動
    }
    assert diff_keys(en, zh) == {"a"}


# ── 整合：整串併行被拒 → 逐行分段翻譯恢復換行結構 ────────────────────

class _MergeThenSegmentTranslator:
    """整串呼叫（含真換行）回傳併行 CJK（觸發拒絕）；逐行呼叫回傳合法 CJK。
    證明拒絕整串後，_translate_segmented_text 的分段路徑會保留每個 \\n。"""

    glossary = None

    def translate(self, text, cancel_check=None):
        if "\n" in text:
            return "中文" * (text.count("\n") + 1)   # 併成一行，無換行
        return "中文"                                  # 逐行合法譯文


def test_segmentation_recovers_dropped_newlines():
    source = "Open the menu\nSelect an item\nConfirm the choice"
    final, ok = _translate_segmented_text(_MergeThenSegmentTranslator(), source, retry_count=0)
    assert ok
    assert final.count("\n") == source.count("\n") == 2   # 換行結構保住
    assert final != source                                 # 確實翻了
