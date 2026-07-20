"""Vault Hunters 實包誤殺回歸（v1.10.0）。

兩類玩家看得到卻從未送翻的文字：
1. 「Name - Variant」方塊名與「LABEL - explanation」GUI tooltip 被值形
   署名啟發式誤殺（the_vault 礦石 ~50 鍵、neoncraft2 全 mod 320 鍵、
   靈魂萃取機 tooltip）。修正：署名判定改為鍵語境閘門
   （_is_credit_context_key），block.*/screen.* 等顯示名稱鍵不受影響。
2. 全大寫樣式化單字（DOWNED 倒地標題、BROKEN 裝備損壞、KEEP ROLLIN'
   成就標題）被 isupper 縮寫規則誤殺。修正：長度 ≥5 且含母音的全大寫
   token 視為真單字仍要翻；CPU/NBT/HTTPS/OFF 等縮寫照舊跳過。
"""
from modpack_translator.pipeline.preprocessor import classify_translation_entry


# ── 1. 「Name - Variant」顯示名稱不再被當署名 ──────────────────────────

def test_ore_variant_block_names_translate():
    # the_vault：寶庫挖礦天天看到的礦石名
    assert classify_translation_entry(
        "block.the_vault.ore_alexandrite_stone", "Alexandrite Ore - Stone"
    ) == "translate"
    assert classify_translation_entry(
        "block.the_vault.ore_black_opal_vault_stone", "Black Opal Ore - Vault Stone"
    ) == "translate"


def test_neon_letter_block_names_translate():
    # neoncraft2：整個 mod 的方塊名都是「Letter X Neon - Color」樣式
    assert classify_translation_entry(
        "block.neoncraft2.wallneon_letter_a_white", "Letter A Neon - White"
    ) == "translate"


def test_label_dash_explanation_gui_tooltips_translate():
    # the_vault 靈魂萃取機 GUI tooltip：「LABEL - 解說句」不是署名
    assert classify_translation_entry(
        "screen.the_vault.spirit_extractor.tooltip.recycle_locked",
        "LOCKED - Click on toggle button to the right to unlock",
    ) == "translate"
    assert classify_translation_entry(
        "screen.the_vault.spirit_extractor.tooltip.multiplier_explained",
        "Cost multiplier - goes up with every revive, is reduced by completing vaults",
    ) == "translate"


def test_dungeon_discoverable_key_not_credit_context():
    # 鍵含 "discoverable"（disc 子字串）不得誤入署名語境
    assert classify_translation_entry(
        "block.the_vault.placeholder_dungeon_discoverable",
        "Placeholder - Dungeon Discoverable",
    ) == "translate"


# 署名語境的鍵（quest_desc、*.desc、music/disc/…）仍原樣保留
def test_genuine_credits_under_credit_context_keys_still_copied():
    assert classify_translation_entry(
        "quest.0000000000000009.quest_desc[0]", "Direwolf20 - Modpack Author"
    ) != "translate"
    assert classify_translation_entry(
        "item.alexsmobs.music_disc_thime.desc", "LudoCrypt - Thime"
    ) != "translate"


# ── 2. 全大寫樣式化文字 ────────────────────────────────────────────────

def test_stylized_all_caps_words_translate():
    assert classify_translation_entry("the_vault.downed.title", "DOWNED") == "translate"
    assert classify_translation_entry("tooltip.the_vault.broken", "BROKEN") == "translate"
    assert classify_translation_entry(
        "advancements.alexsmobs.rocky_roller.title", "KEEP ROLLIN' ROLLIN' ROLLIN'"
    ) == "translate"


def test_short_or_vowelless_acronyms_still_skipped():
    assert classify_translation_entry("gui.ae2.units.rf", "RF") != "translate"
    # v1.12.0 政策修訂:ON/OFF 是切換鈕狀態詞(xaero_pac_ui_on/off 等),
    # 改走靜態譯表(開啟/關閉)確定性翻譯,不再視為縮寫跳過。
    assert classify_translation_entry("message.the_vault.off", "OFF") == "translate"
    assert classify_translation_entry("message.the_vault.on", "ON") == "translate"
    assert classify_translation_entry("item.the_vault.gem_pog", "POG") != "translate"
    assert classify_translation_entry("entity.the_vault.plastic_villager_tank", "NPV") != "translate"
    assert classify_translation_entry("some.mod.https_label", "HTTPS") != "translate"
    assert classify_translation_entry("objective.the_vault.pvp", "PvP") != "translate"


# ── 3. Soulrend 實包誤殺（v1.13.0）───────────────────────────────────
# 「The X」內容名（boss/物品/次元/畫作，值 slug 出現在鍵中）被
# value-slug-in-key 規則誤殺——實測全實例 290 筆命中幾乎全是該翻的
# 顯示名（The Nightwarden、The Nether、畫作標題），真中繼鍵
# （itemgroup./署名）另有專屬規則涵蓋，整條移除。

def test_the_prefixed_content_names_translate():
    assert classify_translation_entry(
        "entity.traveloptics.the_nightwarden", "The Nightwarden"
    ) == "translate"
    assert classify_translation_entry(
        "item.traveloptics.the_obliterator", "The Obliterator"
    ) == "translate"
    assert classify_translation_entry(
        "dimension.minecraft.the_nether", "The Nether"
    ) == "translate"
    assert classify_translation_entry(
        "painting.medieval_paintings.the_two_riders_of_the_south.title",
        "The Two Riders of the South",
    ) == "translate"


def test_itemgroup_mod_names_still_copied():
    assert classify_translation_entry("itemGroup.outer_end", "The Outer End") == "copy"
    assert classify_translation_entry(
        "itemGroup.graveyard.group", "The Graveyard"
    ) == "copy"


# ── 4. 角括號裝飾 boss 名（v1.13.0）──────────────────────────────────
# nightfall_invade 困難模式 boss 名 "<Flame Lord>" 整值被行內 JSX 標籤
# 規則凍結成無可譯字。真標籤必有 = 屬性、自閉合 /> 或閉合 </X> 形態；
# 含空白的多詞散文不再整段凍結。

def test_angle_bracketed_display_names_translate():
    assert classify_translation_entry(
        "boss_bar.nightfall_invade.arterius_hard", "<Flame Lord>"
    ) == "translate"
    assert classify_translation_entry(
        "entity.nightfall_invade.arterius_hard", "<Lord Of Flame - Arterius>"
    ) == "translate"


def test_single_token_placeholders_still_skipped():
    assert classify_translation_entry("commands.foo.usage", "<amount>") == "skip"
    assert classify_translation_entry("commands.foo.usage2", "<player|playerUuid>") == "skip"
