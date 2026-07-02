"""FTB Quests lang 資料夾相容性：既有 zh_tw 譯文不得被誤判為未翻譯。"""
from modpack_translator.pipeline.preprocessor import diff_keys, is_usable_translation
from modpack_translator.pipeline.scanner import ModpackScanner


# ── 字面 \n 與真換行等價 ─────────────────────────────────────────────

def test_literal_newline_token_accepts_real_newline():
    source = "First paragraph. \\n\\nSecond paragraph."
    target = "第一段。\n\n第二段。"
    assert is_usable_translation(source, target)


def test_literal_newline_token_accepts_literal_newline():
    source = "First paragraph. \\n\\nSecond paragraph."
    target = "第一段。\\n\\n第二段。"
    assert is_usable_translation(source, target)


def test_dropping_line_breaks_entirely_still_rejected():
    source = "First paragraph. \\n\\nSecond paragraph."
    target = "第一段。第二段。"
    assert not is_usable_translation(source, target)


# ── \& 為軟性 token ──────────────────────────────────────────────────

def test_escaped_ampersand_may_be_dropped():
    assert is_usable_translation("Forbidden \\& Arcanus", "禁忌與秘術")


# ── 任務標題保留英文專有名詞 ─────────────────────────────────────────

def test_identical_proper_noun_quest_title_is_usable():
    key = "chapter.4EEDECACDE8F5A67.title"
    assert is_usable_translation("Oritech", "Oritech", key=key)
    assert is_usable_translation("&b&lThe Other", "&b&lThe Other", key="quest.68518CCD2EF8B22F.title")


def test_identical_sentence_quest_title_still_needs_translation():
    key = "quest.0000A88BB40B2149.title"
    source = "It's tough you'll need to prepare"
    assert not is_usable_translation(source, source, key=key)


def test_identical_english_description_still_needs_translation():
    key = "quest.034AB493E1EC2E63.quest_desc[0]"
    source = "Pattern Cores add 36 pattern slots to the multiblock."
    assert not is_usable_translation(source, source, key=key)


def test_identical_proper_noun_without_quest_key_still_needs_translation():
    # 模組 jar 語言檔沒有任務鍵格式，維持原本嚴格行為
    assert not is_usable_translation("Chaotic Chestplate", "Chaotic Chestplate", key="item.de.chestplate")


# ── 譯文括號標註不算未翻譯殘留 ────────────────────────────────────────

def test_parenthesized_english_annotation_is_usable():
    source = "The &7Paradox Machine&r needs &aTime Fluid&r to copy blocks."
    target = "&7悖論機器 (Paradox Machine)&r 需要&a時間流體 (Time Fluid)&r 才能複製方塊。"
    assert is_usable_translation(source, target)


def test_capitalized_player_name_is_usable():
    source = "3rd person to beat is &a&lMitinho Player&r."
    target = "第 3 個要打敗的人是 &a&lMitinho Player&r。"
    assert is_usable_translation(source, target)


def test_lowercase_generic_leak_still_rejected():
    source = "Click the claim button to get your reward."
    target = "點擊 claim 按鈕領取 reward。"
    assert not is_usable_translation(source, target)


# ── diff_keys 整合 ───────────────────────────────────────────────────

def test_diff_keys_skips_translated_entries_with_newline_style_mismatch():
    en = {
        "quest.0000000000000001.title": "Getting Started",
        "quest.0000000000000001.quest_desc[0]": "Welcome! \\n\\nHave fun.",
        "quest.0000000000000002.title": "Oritech",
    }
    zh = {
        "quest.0000000000000001.title": "開始遊戲",
        "quest.0000000000000001.quest_desc[0]": "歡迎！\n\n玩得開心。",
        "quest.0000000000000002.title": "Oritech",
    }
    assert diff_keys(en, zh) == set()


def test_diff_keys_flags_missing_and_untranslated():
    en = {
        "quest.0000000000000001.title": "Getting Started",
        "quest.0000000000000002.quest_desc[0]": "A brand new quest line awaits you.",
    }
    zh = {
        "quest.0000000000000002.quest_desc[0]": "A brand new quest line awaits you.",
    }
    assert diff_keys(en, zh) == {
        "quest.0000000000000001.title",
        "quest.0000000000000002.quest_desc[0]",
    }


# ── 誤放在 chapters/ 的 lang 檔不做 inline 掃描 ──────────────────────

_MISPLACED_LANG_SNBT = """{
\tquest.0000A88BB40B2149.title: "It's tough you'll need to prepare"
\tquest.01612963DBBAC9A1.title: "Chaotic Chestplate"
\tquest.01612963DBBAC9A1.quest_desc: ["Hopefully this should protect you."]
}
"""

_REAL_CHAPTER_SNBT = """{
\tfilename: "intro"
\tid: "24151251C8730F2E"
\tquests: [
\t\t{
\t\t\ttitle: "Welcome to the pack"
\t\t\tdescription: ["This text lives inline because there is no lang folder."]
\t\t\tid: "0000000000000001"
\t\t}
\t]
}
"""


def test_inline_scan_skips_misplaced_lang_file(tmp_path):
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "draconic_evolution.snbt").write_text(_MISPLACED_LANG_SNBT, encoding="utf-8")
    (chapters / "intro.snbt").write_text(_REAL_CHAPTER_SNBT, encoding="utf-8")

    targets = ModpackScanner()._scan_inline_snbt_files(tmp_path, "ftbquests", "ftbq_inline_snbt")
    names = {t.source_file.name for t in targets}
    assert names == {"intro.snbt"}
