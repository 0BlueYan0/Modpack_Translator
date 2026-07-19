"""Beyond Depth 實包 GUI 標籤誤殺回歸(v1.12.0)。

三類玩家看得到卻永遠不送翻的文字(Xaero 地圖全家、FancyMenu、FTBQ、DH…):
1. 鍵盤詞表無鍵語境誤殺:值「Delete」「Command」是刪除按鈕/獎勵名,
   卻因 delete/command 在 _KEYBIND_WORDS 而被值層過濾器整殺。修正:
   值層只認「含明確按鍵 token(ctrl/alt/cmd/f1-12…)的和弦」或純單字符片段;
   鍵語境(keybind/shortcut/modifier 鍵)的舊行為不變。
2. 全大寫短詞誤殺:OPEN/BACK/[ACCEPT]/GO UP/API LOCK/'I SAID DART GUN!'
   因 <5 字母全大寫=縮寫規則被殺。修正:常見 GUI 大寫單字白名單 +
   「≥2 個含母音的全大寫詞」片語規則;ON/OFF/OK/YES/NO 走靜態譯表。
3. 大小寫混合斜線對誤殺:「Add/Edit」「World/Server」被資源路徑
   regex(IGNORECASE)當結構值。修正:資源路徑依 MC 規格僅小寫。
"""
from modpack_translator.pipeline.preprocessor import (
    STATIC_TRANSLATIONS,
    classify_translation_entry,
    is_usable_translation,
)
from modpack_translator.pipeline.runner import _static_translation


# ── 1. 鍵盤詞表誤殺 ────────────────────────────────────────────────────

def test_delete_button_labels_translate():
    assert classify_translation_entry("gui.xaero_delete", "Delete") == "translate"
    assert classify_translation_entry("fancymenu.elements.delete", "Delete") == "translate"
    assert classify_translation_entry(
        "gui.xaero_pac_ui_sub_config_delete_button", "Delete %1$s"
    ) == "translate"


def test_command_reward_name_translates():
    assert classify_translation_entry("ftbquests.reward.ftbquests.command", "Command") == "translate"


def test_real_key_chords_still_skipped():
    assert classify_translation_entry(
        "fancymenu.overlay.debug.toggle.shortcut", "CTRL + ALT + D"
    ) != "translate"
    # 鍵語境:fzzy_config 的 keybind 顯示模板照舊 copy
    assert classify_translation_entry("fc.keybind.ctrl.shift", "Ctrl Shift %s") != "translate"
    assert classify_translation_entry("fc.search.modifier.SHIFT", "Shift") != "translate"


def test_single_char_fragments_still_skipped():
    assert classify_translation_entry("gui.xaero_compass_north", "N") != "translate"
    assert classify_translation_entry("block.alexsmobs.terrapin_egg.desc", "%s x %s") != "translate"
    assert classify_translation_entry("gui.config.reset", "r") != "translate"


# ── 2. 全大寫 GUI 標籤 ─────────────────────────────────────────────────

def test_common_upper_gui_words_translate():
    assert classify_translation_entry(
        "config.do_a_barrel_roll.documentation.get_help.text", "OPEN"
    ) == "translate"
    assert classify_translation_entry("fancymenu.requirements.screens.lists.back", "BACK") == "translate"
    assert classify_translation_entry("chant.celestisynth.solaris3", "HEAT.") == "translate"


def test_all_caps_phrases_translate():
    assert classify_translation_entry(
        "advancements.alexsmobs.stink_ray.title", "I SAID DART GUN!"
    ) == "translate"
    assert classify_translation_entry("distanthorizons.general.apiOverride", "API LOCK") == "translate"
    assert classify_translation_entry("gui.xaero_dropdown_scroll_up", "[GO UP]") == "translate"


def test_bracketed_word_labels_translate():
    assert classify_translation_entry(
        "gui.xaero_parties_invite_target_message_accept", "[ACCEPT]"
    ) == "translate"
    assert classify_translation_entry("gui.xaero_waypoint_shared_add", " [Add]") == "translate"


def test_on_off_ok_static_translations():
    for src, zh in (("ON", "開啟"), ("OFF", "關閉"), ("OK", "確定"), ("YES", "是"), ("NO", "否")):
        assert STATIC_TRANSLATIONS[src] == zh
        assert classify_translation_entry(f"gui.some_{src.lower()}", src) == "translate"
        assert _static_translation(src) == zh
    # 既有譯檔把 "ON" 原樣留著 → 視為未翻,下輪以靜態譯名補上
    assert not is_usable_translation("ON", "ON")
    assert is_usable_translation("ON", "開啟")


def test_true_acronyms_still_skipped():
    assert classify_translation_entry("gui.ae2.units.rf", "RF") != "translate"
    assert classify_translation_entry("block.caverns_and_chasms.tmt", "TMT") != "translate"
    assert classify_translation_entry("tooltip.caverns_and_chasms.am", "AM") != "translate"
    assert classify_translation_entry("fancymenu.requirements.categories.gui", "GUI") != "translate"
    assert classify_translation_entry("item.the_vault.gem_pog", "POG") != "translate"
    # 無母音縮寫對不受片語規則影響
    assert classify_translation_entry("gui.xaero_both_light_value", "BL: %d SL: %d") != "translate"


# ── 3. 大小寫混合斜線對 ────────────────────────────────────────────────

def test_titlecase_slash_pairs_translate():
    assert classify_translation_entry("gui.xaero_add_edit", "Add/Edit") == "translate"
    assert classify_translation_entry("gui.xaero_world_server", "World/Server") == "translate"
    assert classify_translation_entry("gui.xaero_weather_raining", "Raining/Snowing") == "translate"
    assert classify_translation_entry("gui.xaero_instructions", "Instructions/Help") == "translate"


def test_lowercase_resource_paths_still_skipped():
    assert classify_translation_entry("some.key", "minecraft:stone") != "translate"
    assert classify_translation_entry("some.key", "path/to/thing") != "translate"
    assert classify_translation_entry("some.key", "textures/gui/book.png") != "translate"
