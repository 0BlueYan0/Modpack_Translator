"""Failed Items 回歸測試：v1.6.1 實際失敗樣本不得再被誤殺。

樣本取自使用者回報的 Failed Items 清單（All the Mons 模組包），
分四組根因：
1. process() 越界檢查在 decode 後執行，數字字面 token（{35}、{4}）必然被拒
2. 專有名詞模型原樣返回被輸出關卡拒絕（dst == src）
3. 程式識別字（q.player、@player、$$button）的小寫單字被當成漏翻殘留
4. 不可譯值（拉丁學名、唱片曲目、指令字面值、時間格式、色碼域名、色碼藝術字）
   被分類為需要翻譯
"""
from modpack_translator.pipeline.postprocessor import process
from modpack_translator.pipeline.preprocessor import (
    classify_translation_entry,
    encode,
    is_usable_translation,
)


# ── 1. process()：數字字面 token 不得觸發越界誤殺 ────────────────────

def test_numeric_literal_brace_token_survives_roundtrip():
    # industrialforegoing: "There can only be {35} animals at the same time"
    source = "There can only be {35} animals at the same time."
    encoded, tokens = encode(source)
    assert tokens == ["{35}"]
    final, ok = process("同一時間最多只能有 {0} 隻動物。", encoded, tokens)
    assert ok
    assert final == "同一時間最多只能有 {35} 隻動物。"


def test_theurgy_small_numeric_token_survives_roundtrip():
    # theurgy: "the refined item, {4} in our case."
    source = "The refined item, {4} in our case."
    encoded, tokens = encode(source)
    final, ok = process("精煉後的物品，在此例中為 {0}。", encoded, tokens)
    assert ok
    assert final == "精煉後的物品，在此例中為 {4}。"


def test_hallucinated_out_of_range_placeholder_still_rejected():
    source = "There can only be {35} animals."
    encoded, tokens = encode(source)
    _, ok = process("最多 {7} 隻動物。", encoded, tokens)
    assert not ok


def test_dropped_hard_token_still_rejected():
    source = "Produces {640,000}RF in total."
    encoded, tokens = encode(source)
    _, ok = process("總共產出能量。", encoded, tokens)
    assert not ok


# ── 2. 輸出關卡：專有名詞原樣輸出可接受（僅限旗標開啟） ──────────────

def test_identical_mod_name_accepted_at_output_gate():
    assert is_usable_translation("Curios", "Curios", accept_identical_proper_noun=True)
    assert is_usable_translation("Balm", "Balm", accept_identical_proper_noun=True)
    assert is_usable_translation(
        "SpongePowered Mixin", "SpongePowered Mixin", accept_identical_proper_noun=True
    )


def test_identical_mod_name_still_rejected_without_flag():
    # diff_keys 路徑維持嚴格：既有 zh 檔中未翻譯的一般名稱仍要送翻
    assert not is_usable_translation("Curios", "Curios")


def test_identical_alnum_code_name_accepted():
    assert is_usable_translation(
        "Mekanism CC2C", "Mekanism CC2C", accept_identical_proper_noun=True
    )
    assert is_usable_translation(
        "AES/CFB8+Base64R", "AES/CFB8+Base64R", accept_identical_proper_noun=True
    )


def test_identical_name_with_digit_leading_word_accepted():
    assert is_usable_translation("Kivi 1x", "Kivi 1x", accept_identical_proper_noun=True)


def test_identical_name_with_placeholder_accepted():
    assert is_usable_translation("Vulkan %s", "Vulkan %s", accept_identical_proper_noun=True)


def test_identical_channel_name_accepted():
    assert is_usable_translation(
        "Discord #allthemons-techsupport",
        "Discord #allthemons-techsupport",
        accept_identical_proper_noun=True,
    )


def test_identical_name_list_accepted():
    assert is_usable_translation(
        "Bob, Alice, Charlie", "Bob, Alice, Charlie", accept_identical_proper_noun=True
    )


def test_identical_sentence_still_rejected_even_with_flag():
    source = "It's tough you'll need to prepare"
    assert not is_usable_translation(source, source, accept_identical_proper_noun=True)
    long_source = "Pattern Cores add 36 pattern slots to the multiblock."
    assert not is_usable_translation(
        long_source, long_source, accept_identical_proper_noun=True
    )


# ── 3. 程式識別字不算漏翻殘留 ────────────────────────────────────────

def test_molang_identifier_not_counted_as_leak():
    source = (
        "MoLang expression to run when a player loses a battle against the entity. "
        "This has q.player as the player and q.entity as the entity."
    )
    target = (
        "當玩家輸掉與該實體的對戰時執行的 MoLang 運算式。"
        "其中 q.player 為玩家，q.entity 為該實體。"
    )
    assert is_usable_translation(source, target)


def test_entity_filter_at_identifier_not_counted_as_leak():
    source = "§e@player§f: match players"
    target = "§e@player§f：符合玩家"
    assert is_usable_translation(source, target)


def test_fancymenu_variable_and_enum_literals_not_counted_as_leak():
    source = "- §z$$button §r= Which button was pressed (left, right, middle)"
    target = "- §z$$button §r= 按下了哪個滑鼠按鍵（left、right、middle）"
    assert is_usable_translation(source, target)


def test_quoted_dotted_locator_not_counted_as_leak():
    source = "Replace 'some.menu.identifier:505280' with the correct widget locator."
    target = "請將 'some.menu.identifier:505280' 替換為正確的元件定位碼。"
    assert is_usable_translation(source, target)


def test_bare_lowercase_generic_leak_still_rejected():
    # 非識別字的裸露小寫單字仍視為漏翻
    source = "Click the claim button to get your reward."
    target = "點擊 claim 按鈕領取 reward。"
    assert not is_usable_translation(source, target)


# ── 4. 分類規則：不可譯值不再送翻 ────────────────────────────────────

def test_latin_species_name_keys_are_copied():
    assert classify_translation_entry(
        "block.productivetrees.purple_spiral.latin", "Ysabella purpurea"
    ) != "translate"
    assert classify_translation_entry(
        "tooltip.productivefarming.spoon_gourd.latin", "Cucurbita pepo var. ovifera"
    ) != "translate"


def test_music_disc_desc_is_copied():
    assert classify_translation_entry(
        "item.allthemons.disc_scarlet.desc", "Helynt, GameChops, mellow mode - Scarlet"
    ) != "translate"


def test_command_literal_is_copied():
    assert classify_translation_entry(
        "create.command.killTPSCommand", "killtps"
    ) != "translate"
    # 指令鍵下的一般句子仍要翻譯
    assert classify_translation_entry(
        "create.command.description", "Kills the TPS counter"
    ) == "translate"


def test_time_format_pattern_is_skipped():
    assert classify_translation_entry("gui.ae2.ETAFormat", "HH:mm:ss") != "translate"


def test_color_code_wrapped_domain_is_skipped():
    assert classify_translation_entry(
        "quest.2893F483C10293E6.quest_desc[1]", "&o&bappliedenergistics.github.io&f&r."
    ) != "translate"


def test_color_code_interleaved_word_art_is_skipped():
    # Dyenamics 任務標題：色碼穿插在單字字母之間，無法翻譯
    assert classify_translation_entry(
        "quest.6B0D84B730C6C76C.title", "&l&cDy&6en&ea&ami&bcs&r"
    ) != "translate"


def test_ordinary_colored_sentence_still_translates():
    assert classify_translation_entry(
        "quest.0000000000000001.subtitle", "&aVery &bimportant &cmessage here&r"
    ) == "translate"


def test_ordinary_item_names_still_translate():
    assert classify_translation_entry("block.minecraft.stone", "Stone") == "translate"
    assert classify_translation_entry("item.create.wrench", "Wrench") == "translate"
