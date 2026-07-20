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


# ── 5. 散文誤判為程式碼/署名的回歸（v1.7.x：pokenav 等整段被跳過） ────
# 根因：_looks_like_code_or_table_line 只要文中含 _CODE_WORDS 任一字
# （long/void/return/class/string/int…）且出現任一 = ( ) ; { } 就判為程式
# 碼；_looks_like_credit 只要有 " - " 且左半是普通英文字就判為署名。任務
# 描述括號與破折號極常見，導致整段英文被分類 skip、原樣寫回從未送翻。

def test_prose_with_code_word_and_parens_still_translates():
    # catch_em_all pokenav：「a long list」+「(or can)」→ 舊版誤判程式碼
    assert classify_translation_entry(
        "quest.410492282A446A06.quest_desc[0]",
        "The &aPokeNav&r is a tool to help understand what &cPokemon&r will (or can) "
        "Spawn near you. \\n\\nOpening up the &aPokeNav&r and you'll get a long list of "
        "&cPokemon&r!",
    ) == "translate"


def test_prose_with_void_keyword_still_translates():
    # allthemodium 系列：ATM 內容滿是 "Void"
    assert classify_translation_entry(
        "quest.3621155A4138EBCA.quest_desc[0]",
        "The Void Cell needs to be partitioned (filtered) in a Cell Workbench before use.",
    ) == "translate"


def test_prose_with_return_keyword_still_translates():
    assert classify_translation_entry(
        "quest.4CE571F942461909.quest_desc[2]",
        "The Artisanal Ritual Satchel functions nearly identically to the regular one, "
        "and you get it back in return (mostly).",
    ) == "translate"


def test_narrative_with_dash_not_treated_as_credit():
    # legendaries：破折號敘事句左半是整句子句，非「作者 - 標題」署名
    assert classify_translation_entry(
        "quest.1E6F000000001004.quest_desc[0]",
        "Team Rocket's masterwork sits in the heart of the volcano - a 300 million year "
        "old fossil Pokemon rebuilt as a living weapon.",
    ) == "translate"


def test_tier_dash_prose_still_translates():
    # mysticalagriculture：「This is the Tier 5 - Awakened Essence...」
    assert classify_translation_entry(
        "quest.7A103577EAE7B3F1.quest_desc[0]",
        "This is the Tier 5 - Awakened Essence, made by combining 4 Cognizant Dust, "
        "10 of each Elemental Essence and 1 Supremium Block together.",
    ) == "translate"


# 修正後：真正的程式碼與署名仍必須被跳過（不得因放寬而誤放行）
def test_genuine_java_code_line_still_skipped():
    assert classify_translation_entry(
        "some.mod.snippet", "public void doThing() { return; }"
    ) != "translate"
    assert classify_translation_entry("some.mod.snippet", "int count = 0;") != "translate"
    assert classify_translation_entry("some.mod.snippet", "boolean flag = true;") != "translate"


def test_genuine_author_credit_still_skipped():
    # 「作者 - 標題」短署名（左半 ≤4 字）仍視為不可譯
    assert classify_translation_entry(
        "quest.0000000000000009.quest_desc[0]", "Direwolf20 - Modpack Author"
    ) != "translate"


# ── 6. Failed Items（Downloads 執行版）實際樣本回歸 ──────────────────────
# 使用者回報的失敗清單分兩類：
#   short_fragments — 本就不該送翻的值（日期格式、單位、署名、社群連結、
#                     無母音縮寫關鍵字）被分類為 translate。
#   natural_text    — 可譯散文，但輸出關卡誤殺（CLI <..> 佔位符、句中保留的
#                     英文指令詞被改用中文引號包住後被當漏翻殘留）。

def test_date_time_format_pattern_is_skipped():
    # corpse: gui.corpse.death_history.date_format = "yyyy/MM/dd HH:mm:ss"
    assert classify_translation_entry(
        "gui.corpse.death_history.date_format", "yyyy/MM/dd HH:mm:ss"
    ) != "translate"
    # 既有純時間樣板仍要跳過
    assert classify_translation_entry("gui.ae2.ETAFormat", "HH:mm:ss") != "translate"
    assert classify_translation_entry("some.mod.date", "dd/MM/yyyy") != "translate"
    # 但一般散文（湊巧只用到格式字母的兩個單字）仍要翻譯
    assert classify_translation_entry("quest.x.desc", "same day") == "translate"
    assert classify_translation_entry("quest.x.desc", "Yes Sir") == "translate"


def test_fluid_kilobucket_unit_fragment_is_skipped():
    # mantle: gui.mantle.fluid.kilobucket = "%s kb"
    assert classify_translation_entry("gui.mantle.fluid.kilobucket", "%s kb") != "translate"


def test_signature_key_is_copied():
    # mekanism: holiday.mekanism.signature = "-aidancbrady"
    assert classify_translation_entry(
        "holiday.mekanism.signature", "-aidancbrady"
    ) != "translate"


def test_social_link_keys_are_copied():
    # quark: quark.gui.config.social.reddit = "/r/QuarkMod Reddit"
    assert classify_translation_entry(
        "quark.gui.config.social.reddit", "/r/QuarkMod Reddit"
    ) != "translate"
    assert classify_translation_entry(
        "mod.social.twitter", "@SomeMod on Twitter"
    ) != "translate"
    assert classify_translation_entry(
        "mod.social.youtube", "SomeMod YouTube Channel"
    ) != "translate"


def test_consonant_acronym_keyword_is_skipped():
    # thermal: *.keyword = "tnt"（全無母音的縮寫，模型原樣返回被輸出關卡誤殺）
    assert classify_translation_entry("block.thermal.ender_tnt.keyword", "tnt") != "translate"
    # 但多字關鍵字（含可譯英文詞）仍要翻譯
    assert classify_translation_entry(
        "block.thermal.machine.keyword", "energy rf storage"
    ) == "translate"
    # 一般含母音的單字仍要翻譯
    assert classify_translation_entry("item.create.wrench", "wrench") == "translate"


def test_repeated_chant_easter_egg_is_skipped():
    # botania: advancement.botania:desuGun.desc = "ASADA-SAN" ×12 彩蛋咒語。
    # 官方 zh_cn/zh_tw 皆原樣保留;v1.10.0 放寬全大寫後曾把既有 zh==en 誤判
    # 未翻譯而送翻,模型原樣返回被輸出關卡(>5 詞非專有名詞片語)誤殺。
    chant = " ".join(["ASADA-SAN"] * 12)
    assert classify_translation_entry("advancement.botania:desuGun.desc", chant) != "translate"
    # 既有 zh==en(官方譯者的選擇)必須被視為可用,不再送翻
    assert is_usable_translation(chant, chant) is True
    # 用詞多樣的散文(含重複詞)仍要翻譯
    assert classify_translation_entry(
        "quest.0000000000000001.subtitle", "Dig dig dig your way down"
    ) == "translate"
    # 樣式化全大寫單字(v1.10.0 修正)不受影響,仍要翻譯
    assert classify_translation_entry("the_vault.downed.title", "DOWNED") == "translate"


def test_vocalization_subtitle_is_skipped():
    # botania: botania.subtitle.way = 純母音擬聲吟唱（Ievan Polkka），無可譯內容
    way = (
        "O-oooooooooo AAAAE-A-A-I-A-U-JO-oooooooooooo AAE-O-A-A-U-U-A-E-eee-ee-eee "
        "AAAAE-A-E-I-E-A-JO-ooo-oo-oo-oo EEEEO-A-AAA-AAAA"
    )
    assert classify_translation_entry("botania.subtitle.way", way) != "translate"
    # 一般字幕仍要翻譯
    assert classify_translation_entry(
        "botania.subtitle.some", "A mysterious sound echoes"
    ) == "translate"


def test_cli_angle_bracket_placeholder_command_is_usable():
    # botaniamisc.command.skyblock.help.4：<player|playerUuid> 是 CLI 佔位符，
    # 保留原樣不算「player」漏翻。
    source = "/gardenofglass regen-island <player|playerUuid> - rebuilds the specified player's island"
    target = "/gardenofglass regen-island <player|playerUuid> - 重建指定玩家的島嶼"
    assert is_usable_translation(source, target)


def test_cjk_quoted_command_words_not_counted_as_leak():
    # botania.page.corporeaIndex6：句中保留的英文指令詞（"display"、"any"…）
    # 被模型改用中文引號「」包住，不得被當成 display/any 漏翻殘留。
    source = (
        'The words "all" or "every" request every single item in the network matching the '
        'given criteria; for example, "all apples" will retrieve every single apple '
        '(as well as every item renamed to "apple") in the network.$(p)The words "count", '
        '"show", "display" and "tell" won\'t retrieve any items, but will count them for the '
        "requester's convenience."
    )
    target = (
        "「all」或「every」這兩個詞會請求網路中所有符合條件的單一物品；"
        "例如，「all apples」會取回網路中每一顆蘋果（以及所有被重新命名為「apple」的物品）。"
        "$(p)「count」、「show」、「display」和「tell」這些詞不會取回任何物品，"
        "但會為請求者統計數量以方便查看。"
    )
    assert is_usable_translation(source, target)


def test_bare_cjk_quoted_generic_leak_still_rejected():
    # 防過度放寬：真正漏翻（未保留在引號內的裸露 generic 詞）仍要擋下
    source = "Click the claim button to get your reward."
    target = "點擊 claim 按鈕領取 reward。"
    assert not is_usable_translation(source, target)


# ── 7. Failed Items 第二輪（Beyond Depth 模組包）實際樣本回歸 ────────────
# 根因分四類：
#   a. 程式碼樣貌的值（函式簽名、斜線指令用法、設定語法行）被分類 translate，
#      模型只能原樣返回而被輸出關卡誤殺（craftpresence ×38、ftbquests usage、
#      ETF 說明末行）。
#   b. 單位/快捷鍵縮寫不在既有詞表（fps 不在 _UNIT_WORDS、Alt+F3 和弦的 Alt
#      被當一般英文詞），原樣返回被誤殺（betterf3 ×2）。
#   c. 值=鍵尾片段的開發用識別字（taskdesc3、prototype_701）被送翻。
#   d. 輸出關卡：正確譯文保留 items.1=any 賦值字面後 "any" 被當漏翻殘留
#      （ETF）；專有名詞標題的全形標點變體（Chunky？）被「無 CJK」規則誤殺。

def test_camelcase_function_signature_is_skipped():
    # craftpresence.placeholders.*.usage 共 38 條函式簽名
    assert classify_translation_entry(
        "craftpresence.placeholders.asIcon.usage", "asIcon(input, whitespaceIndex ?: '')"
    ) != "translate"
    assert classify_translation_entry(
        "craftpresence.placeholders.getFields.usage", "getFields(classObj=Object|String|Class)"
    ) != "translate"
    assert classify_translation_entry(
        "craftpresence.placeholders.isWithinValue.usage",
        "isWithinValue(value, min, max, contains_min ?: false, contains_max ?: false, "
        "check_sanity ?: true)",
    ) != "translate"
    assert classify_translation_entry(
        "craftpresence.placeholders.timeToEpochMilli.usage", "timeToEpochMilli(data)"
    ) != "translate"


def test_lowercase_function_signature_is_skipped():
    # 全小寫名稱的簽名（length(input)）也要跳過
    assert classify_translation_entry(
        "craftpresence.placeholders.length.usage", "length(input)"
    ) != "translate"


def test_plural_parenthetical_still_translates():
    # 防過度放寬：「Item(s)」「second(s)」是散文複數標記，不是函式簽名
    assert classify_translation_entry("gui.mod.items", "Item(s)") == "translate"
    assert classify_translation_entry("gui.mod.seconds", "second(s)") == "translate"


def test_slash_command_usage_is_skipped():
    # ftbquests: commands.ftbquests.export_rewards_to_chest.usage
    assert classify_translation_entry(
        "commands.ftbquests.export_rewards_to_chest.usage",
        "/ftbquests export_rewards_to_chest <reward_table>",
    ) != "translate"


def test_command_help_prose_still_translates():
    # 防過度放寬：以斜線指令開頭但含散文說明的句子仍要翻譯
    assert classify_translation_entry(
        "commands.mod.home.help", "/home Teleports you to your Home"
    ) == "translate"
    assert classify_translation_entry(
        "commands.mod.spawn.help", "/spawn teleports you back to the world spawn point"
    ) == "translate"


def test_fps_unit_fragment_is_skipped():
    # betterf3: format.betterf3.fps = "%s fps / %s fps %s"（fps 與 tps 同為單位）
    assert classify_translation_entry(
        "format.betterf3.fps", "%s fps / %s fps %s"
    ) != "translate"


def test_keybind_chord_acronym_line_is_skipped():
    # betterf3: text.betterf3.line.fps_tps = "FPS / TPS (Alt+F3)"
    # 內容字全為縮寫/快捷鍵和弦，無可譯文字
    assert classify_translation_entry(
        "text.betterf3.line.fps_tps", "FPS / TPS (Alt+F3)"
    ) != "translate"


def test_chord_with_prose_still_translates():
    # 防過度放寬：快捷鍵和弦旁有散文仍要翻譯
    assert classify_translation_entry(
        "key.mod.fly.desc", "Press Ctrl+F to toggle flying"
    ) == "translate"


def test_key_echo_identifier_is_copied():
    # caverns_and_chasms 畫作標題、realmrpg 佔位殘字：值=鍵尾的識別字
    assert classify_translation_entry(
        "painting.caverns_and_chasms.prototype_701.title", "prototype_701"
    ) != "translate"
    assert classify_translation_entry(
        "gui.realmrpg_quests.quest_book_page_a.tooltip_taskdesc3", "taskdesc3"
    ) != "translate"


def test_key_echo_display_name_still_translates():
    # 防過度放寬：一般顯示名稱（值=鍵尾但為正常單字，無數字/底線）仍要翻譯
    assert classify_translation_entry("item.minecraft.diamond", "Diamond") == "translate"
    assert classify_translation_entry("block.minecraft.stone", "stone") == "translate"


def test_config_assignment_syntax_line_is_skipped():
    # ETF 說明末行：§aitems.<n>=<list|none|any|holding|wearing> 純設定語法
    assert classify_translation_entry(
        "config.entity_texture_features.property_explanation.items",
        "§aitems.<n>=<list|none|any|holding|wearing>",
    ) != "translate"


def test_config_assignment_literal_not_counted_as_leak():
    # ETF：正確譯文保留 items.1=any 範例語法，"any" 不得算漏翻殘留
    source = "Example: items.1=any    (matches a mob holding or wearing any item)"
    target = "範例：items.1=any    （符合手持或穿戴任意物品的生物）"
    assert is_usable_translation(source, target)


def test_etf_full_property_explanation_translation_usable():
    # ETF 整串多行說明：正確中譯（保留全部設定範例語法）必須可用
    source = (
        "Items \n"
        "Select whether a mob must have certain, or any, items equipped or held\n"
        "Example: items.1=minecraft:book cool_mod:sunglasses   "
        "(matches a mob holding or wearing one of these items)\n"
        "Example: items.1=any                                  "
        "(matches a mob holding or wearing any item)\n"
        "Example: items.1=wearing                              "
        "(matches a mob wearing any item)\n"
        "Example: items.1=none                                 "
        "(matches a mob holding or wearing no items)\n"
        "§aitems.<n>=<list|none|any|holding|wearing>"
    )
    target = (
        "物品 \n"
        "選擇生物是否必須裝備或手持特定（或任意）物品\n"
        "範例：items.1=minecraft:book cool_mod:sunglasses   "
        "（符合手持或穿戴其中一件物品的生物）\n"
        "範例：items.1=any                                  "
        "（符合手持或穿戴任意物品的生物）\n"
        "範例：items.1=wearing                              "
        "（符合穿戴任意物品的生物）\n"
        "範例：items.1=none                                 "
        "（符合未手持且未穿戴任何物品的生物）\n"
        "§aitems.<n>=<list|none|any|holding|wearing>"
    )
    assert is_usable_translation(source, target)


def test_fullwidth_punct_identical_proper_noun_accepted():
    # ftbq mowzie 任務標題 "Chunky?"：模型保留原文但改用全形問號（U+FF1F），
    # 摺疊後視為原樣返回，走專有名詞豁免
    assert is_usable_translation(
        "Chunky?", "Chunky？", accept_identical_proper_noun=True
    )


def test_fullwidth_punct_variant_of_sentence_still_rejected():
    # 防過度放寬：非專有名詞的句子換全形標點仍不可用
    source = "It's tough you'll need to prepare!"
    assert not is_usable_translation(
        source, "It's tough you'll need to prepare！", accept_identical_proper_noun=True
    )


def test_untranslatable_segment_short_circuits_without_model_call():
    # 多行值切段後的純語法行（ETF 末行）直接原樣通過，不呼叫模型
    from modpack_translator.pipeline.runner import _translate_validated

    class BoomTranslator:
        glossary = None

        def translate(self, text, cancel_check=None):
            raise AssertionError("純語法行不應呼叫模型")

    line = "§aitems.<n>=<list|none|any|holding|wearing>"
    final, ok = _translate_validated(BoomTranslator(), line, 0)
    assert ok
    assert final == line


def test_translate_dict_skips_function_signatures_without_model_call():
    # 函式簽名條目經 classify 過濾後不進 diff、不呼叫模型、不進 failed
    from modpack_translator.pipeline.runner import translate_dict

    class BoomTranslator:
        glossary = None
        pack_context = None

        def translate(self, text, cancel_check=None):
            raise AssertionError("函式簽名不應呼叫模型")

    en = {
        "craftpresence.placeholders.asIcon.usage": "asIcon(input, whitespaceIndex ?: '')",
        "craftpresence.placeholders.length.usage": "length(input)",
    }
    result, n_translated, n_cached, n_fallback, failed = translate_dict(
        en, {}, BoomTranslator(), {}
    )
    assert failed == {}
    assert n_fallback == 0


# ── 5. v1.8.0 GuideME 實跑樣本(2026-07-14):公式行與指令用法文法 ──────

def test_energy_ratio_line_classified_like_forge_sibling():
    """AE2 ae2guide energy.md 的換算式清單:相鄰 Forge 行因 forge/fe/ae 在豁免清單
    而 skip,Fabric 行必須一致(fabric 同為 mod 載入器名),否則永遠送翻永遠失敗。"""
    assert classify_translation_entry("s7", "2 FE = 1 AE (Forge)") != "translate"
    assert classify_translation_entry("s8", "1 E  = 2 AE (Fabric)") != "translate"


LOOTR_USAGE = (
    "/lootr cart | cart <loot-table> | %s | custom-chest | "
    "custom-area <x> <y> <z> <x> <y> <z> | refresh | decay | open_as <player> | "
    "open_as_uuid <uuid> | id | openers | clear <player> | cclear <entity matcher>"
)


def test_command_usage_grammar_is_copy():
    """lootr.commands.usage:值是指令文法一覽(/子指令 | 子指令 <參數> | …),
    mod 自帶 zh_tw 也原樣保留;模型只能原樣返回,必須分類為 copy。"""
    assert classify_translation_entry("lootr.commands.usage", LOOTR_USAGE) == "copy"


def test_prose_with_pipes_or_slash_still_translatable():
    # 含管線符的散文(不以 / 起頭)與單一指令的說明文(無多重管線)仍要翻
    assert classify_translation_entry(
        "x.desc", "Use the hopper | filter | sorter combo to sort items."
    ) == "translate"
    assert classify_translation_entry(
        "x.cmd_help", "/home teleports you to your home point."
    ) == "translate"


# ── 6. v1.9.0 DawnCraft 實跑樣本(2026-07-17):camelCase 指令、§k 亂碼、
#      /指令字面殘留誤殺、link 鍵 slug ──────────────────────────────────

def test_camelcase_gamerule_command_is_skipped():
    # ftbquests useful_commands_for_servers:純指令行,gamerule 名是 camelCase
    # 識別字(sendCommandFeedback),非散文;模型只能原樣返回,必須不可譯
    assert classify_translation_entry(
        "61:description", "/gamerule sendCommandFeedback true"
    ) != "translate"
    assert classify_translation_entry(
        "64:description", "  /gamerule doDCBossesRespawn true"
    ) != "translate"


def test_capitalized_command_prose_still_translates():
    # 防過度放寬:首字大寫的散文說明(非 camelCase 識別字)仍要翻譯
    assert classify_translation_entry(
        "commands.mod.home.help", "/home Teleports you to your Home"
    ) == "translate"


def test_fully_obfuscated_gibberish_is_skipped():
    # paraglider anti_vessel:全文都在 §k 亂碼特效下,遊戲中渲染為隨機跳動
    # 亂碼,字元內容永遠不可見,內容也是鍵盤亂打,無可譯內容
    assert classify_translation_entry(
        "tooltip.paraglider.anti_vessel.1",
        "§5§k§oasdfasdfasdfas dfasdfasdfasfd sfn §c§k§o가나다§5§k§ofdadff§r",
    ) != "translate"


def test_obfuscated_prefix_with_visible_prose_still_translates():
    # 防過度放寬:§k 段之外還有可見散文時仍要翻譯
    assert classify_translation_entry(
        "item.mod.secret.tooltip", "§kXXX§r A mysterious fragment"
    ) == "translate"


def test_slash_command_reference_not_counted_as_leak():
    # roughlyenoughitems time_command.desc:正確譯文保留 /time 指令字面,
    # 其中的 time 是指令名而非漏翻殘留,不得誤殺正確輸出
    src = (
        "The command invoked to change the time. This may be useful if the "
        "server replaced the default /time command. Available placeholders: {time}."
    )
    tgt = "用於更改時間的指令。若伺服器替換了預設的 /time 指令,這可能很有用。可用佔位符:{time}。"
    assert is_usable_translation(src, tgt, accept_identical_proper_noun=True)


def test_prose_time_leak_still_rejected():
    # 防過度放寬:散文中未翻譯的 time(非 /指令字面)仍算漏翻殘留
    assert not is_usable_translation(
        "Click to change the time.", "點擊以更改 time。"
    )


def test_mod_link_slug_is_copied():
    # aquamirae.obscure_book.mod_link = "ob-aquamirae":link 鍵下的
    # 專案 slug(純小寫、-_. 相連的單一 token)原樣保留
    assert classify_translation_entry(
        "aquamirae.obscure_book.mod_link", "ob-aquamirae"
    ) != "translate"


def test_link_key_with_prose_still_translates():
    # 防過度放寬:link 鍵下的散文值仍要翻譯
    assert classify_translation_entry(
        "gui.mod.open_link", "Click here to open the link"
    ) == "translate"


# ── 5. v1.15.0 Failed Items:光影選項值的 token 優先序記號 ─────────────

def test_shader_priority_chain_value_is_skipped():
    # ComplementaryUnbound value.IPBR_EMISSIVE_MODE.2 = "seuspbr > IPBR+":
    # 資源包格式優先序記號,兩側皆技術 token,模型只能原樣返回 → 不可譯
    assert classify_translation_entry(
        "value.IPBR_EMISSIVE_MODE.2", "seuspbr > IPBR+"
    ) == "skip"
    assert is_usable_translation("seuspbr > IPBR+", "seuspbr > IPBR+")
    assert classify_translation_entry("value.MODE.1", "A > B > C") == "skip"


def test_priority_chain_rule_does_not_overkill():
    # 防過度放寬:含多詞段或句子結構的 ">" 文字仍要翻譯
    assert classify_translation_entry(
        "gui.path", "Options > Video Settings > Quality"
    ) == "translate"
    assert classify_translation_entry(
        "tooltip.compare", "Deals more damage > 10 hearts total"
    ) == "translate"
    # 合成轉換箭頭("->")兩側是可譯物品名,刻意不納入此規則
    assert classify_translation_entry("tooltip.convert", "Iron -> Gold") == "translate"
