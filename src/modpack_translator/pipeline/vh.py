"""Vault Hunters（the_vault）config 在地化。

VH 的自訂 GUI 文字（技能樹/能力/任務/物品 tooltip/貪婪試煉/玩家統計面板）
不走 lang 檔，而是存於 config/the_vault/*.json；模組依遊戲語言載入
config/the_vault/lang/<locale>/<同相對路徑> 的覆蓋檔（VH 3.x 官方出貨
zh_cn/de_de/es_es/fr_fr/pt_br/ru_ru/sv_se 即此機制）。

反編譯確認：覆蓋檔只在 locale 位於 Config.SUPPORTED_LOCALES 硬編碼清單
（en_us/es_es/es_mx/pt_br/zh_cn/fr_fr/de_de/ru_ru/sv_se）時由
loadLocaleVariants() 載入——zh_tw 不在清單內，lang/zh_tw/ 覆蓋檔永遠
不被讀取，顯示端 getLocalizedName/Description 查無 locale 變體即
fallback 基底 config 英文。故 lang/zh_tw/ 產出必須搭配
patcher.patch_vault_client_strings() 把清單中官方無任何資源的死 locale
es_mx 常數改寫為目標語言（LOCALE_PATCH_DONOR）才會生效。

另有兩類不走 lang/<locale>/ 覆蓋機制的文字：

- translations.json：MixinClientLanguage 在每次語言載入時把其鍵值整批
  putAll 進客戶端語言表（蓋過 jar lang 同鍵譯文），無 locale 變體
  → 只能就地翻譯值（INPLACE_FILES）。
- 選單硬編碼字串：暫停選單「Vault Hunters Options」按鈕（MixinOptionsScreen）
  與其選單樹（VaultOptionsScreen/VaultAccessibilityScreen/
  VaultSoundOptionsScreen/InventoryHudEditScreen/TabbedScreen）的 UI 文字
  是 class 常數池字面值，lang/config 皆翻不到 → 由 HARDCODED_UI_LITERALS
  白名單經 patcher 常數池改寫（僅替換 CONSTANT_String 引用的顯示字串；
  \\x01 是 invokedynamic 字串串接槽位，原樣保留）。

可翻欄位依各檔 schema 固定（與官方 locale 檔實際翻譯的欄位一致）：

- skill_descriptions / abilities_descriptions：description 富文本段的 "text"
  （"color" 是樣式變數、"current"/"next" 是統計欄位識別字，原樣保留）
- quest/quests.json：任務 "name" 與描述段 "text"（"id"/"targetId"/
  "unlockedBy" 是跨檔引用識別字，絕不可譯）
- tooltip.json：條目 "value"（"item" 是物品資源 ID）
- gear/modifier_tooltips.json、menu_player_stat_description.json：
  字典值全部是說明文（鍵是屬性/統計資源 ID）
- greed/trials_screen.json："text" 段與 "trialWarningText" 字串陣列

字串以 JSON path 為鍵抽取/寫回（沿用 preprocessor 的 Patchouli path
工具），輸出檔以來源完整結構打底、僅替換譯文欄位。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from modpack_translator.pipeline.preprocessor import (
    _patchouli_path_key,
    write_patchouli_text,
)


@dataclass(frozen=True)
class FileSpec:
    text_fields: frozenset[str] = frozenset()
    list_fields: frozenset[str] = frozenset()
    all_values: bool = False  # 檔內所有字典字串值都是說明文


LOCALIZABLE_FILES: dict[str, FileSpec] = {
    "skill_descriptions.json": FileSpec(text_fields=frozenset({"text"})),
    "abilities_descriptions.json": FileSpec(text_fields=frozenset({"text"})),
    "quest/quests.json": FileSpec(text_fields=frozenset({"text", "name"})),
    # SkyVaultQuestConfig 繼承 QuestConfig，同 loadLocaleVariants 機制
    "quest/sky_quests.json": FileSpec(text_fields=frozenset({"text", "name"})),
    "tooltip.json": FileSpec(text_fields=frozenset({"value"})),
    "gear/modifier_tooltips.json": FileSpec(all_values=True),
    "greed/trials_screen.json": FileSpec(
        text_fields=frozenset({"text"}),
        list_fields=frozenset({"trialWarningText"}),
    ),
    "menu_player_stat_description.json": FileSpec(all_values=True),
}

# 無 locale 變體、只能就地翻譯值的檔（來源即目標；已含 CJK 的值視為完成）
INPLACE_FILES: dict[str, FileSpec] = {
    "translations.json": FileSpec(all_values=True),
}

FILE_SPECS: dict[str, FileSpec] = {**LOCALIZABLE_FILES, **INPLACE_FILES}


def spec_for_source(path: Path) -> tuple[str, FileSpec] | None:
    """從來源/既有檔路徑反查它是哪個可在地化檔（比對路徑尾段，不分大小寫）。"""
    parts = [p.lower() for p in path.parts]
    for rel, spec in FILE_SPECS.items():
        rel_parts = [p.lower() for p in rel.split("/")]
        if len(parts) >= len(rel_parts) and parts[-len(rel_parts):] == rel_parts:
            return rel, spec
    return None


def read_config_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def extract_text(data: Any, spec: FileSpec) -> dict[str, str]:
    """抽取可翻字串，鍵為 JSON path（與 Patchouli path 鍵同格式）。"""
    result: dict[str, str] = {}
    for path, value in _iter_text(data, spec):
        result[_patchouli_path_key(path)] = value
    return result


def read_config_text(path: Path, rel: str) -> dict[str, str]:
    return extract_text(read_config_json(path), FILE_SPECS[rel])


def read_source_text(path: Path) -> dict[str, str]:
    """runner.read_target_strings 用：由來源路徑自動配對 spec。"""
    found = spec_for_source(path)
    if found is None:
        return {}
    return extract_text(read_config_json(path), found[1])


# JSON path 寫回與 Patchouli 共用同一工具
apply_text = write_patchouli_text


def preserve_edges(source: str, translated: str) -> str:
    """譯文首尾空白以原文為準（quest 描述段以 "\\n\\n" 前綴分段，翻譯管線
    會剝掉邊緣空白——遺失會讓任務書段落擠成一團）。譯文為空白時原樣返回。"""
    core = translated.strip()
    if not core:
        return translated
    lead = source[: len(source) - len(source.lstrip())]
    trail = source[len(source.rstrip()):]
    return lead + core + trail


# ------------------------------------------------------------ class 常數池修補

# the_vault jar 內含 SUPPORTED_LOCALES 清單的 class 與犧牲用死 locale
# （es_mx 官方未出貨任何 lang/config 資源，改寫為目標語言零損失）
CONFIG_CLASS_PATH = "iskallia/vault/config/Config.class"
LOCALE_PATCH_DONOR = "es_mx"

# 選單硬編碼 UI 字面值 → zh_tw 白名單。僅列確定為顯示用途的字串
# （多詞含空白者不可能是 Java 識別字；單詞者已逐一確認僅被
# CONSTANT_String 引用且非持久化鍵——vaultOptions.json 以資源 ID 為鍵）。
# "\x01" 是 invokedynamic makeConcatWithConstants 的參數槽位，必須保留。
# 刻意不譯：品牌名（Patreon）、URL、格式串（"%dm %ds"、"\x01%"）、
# 識別字（SMALL/GENERIC/WOODEN、"sfx"、"_"）、音效名前綴 "SFX "。
HARDCODED_UI_LITERALS: dict[str, dict[str, str]] = {
    "iskallia/vault/mixin/MixinOptionsScreen.class": {
        "Vault Hunters Options": "寶庫獵人選項",
    },
    "iskallia/vault/client/gui/screen/VaultOptionsScreen.class": {
        "Vault Hunters Options": "寶庫獵人選項",
        "Vault Game Rules": "寶庫遊戲規則",
        "Accessibility": "輔助功能",
        "Pickup Notifier": "拾取通知",
        "Vault HUD": "寶庫 HUD",
        "Support the development team": "支持開發團隊",
        "Done": "完成",
        "Vault Hunters": "寶庫獵人",
    },
    "iskallia/vault/client/gui/screen/accessibility/VaultAccessibilityScreen.class": {
        "Vault Hunters Accessibility": "寶庫獵人輔助功能",
        "Visual": "視覺",
        "Vault": "寶庫",
        "Damage": "傷害",
        "Healthbar": "血條",
        "Hunter": "獵人",
        "Colorblind Mode": "色盲模式",
        "Has 3 modes. Changes colors for gear depending on the mode. All modes add gear rarity types to scavenger items, gear and shop pedestals (i.e 'Common Vault Sword').":
            "共有 3 種模式。依所選模式改變裝備的顏色。所有模式都會為搜刮物品、裝備與商店底座加上裝備稀有度類型（例如「普通寶庫劍」）。",
        "Gear Modifier Colours": "裝備詞綴顏色",
        "When disabled, all modifiers on gear will be colored white rather than their respective color, such as red for Attack Damage.":
            "停用時，裝備上的所有詞綴都會顯示為白色，而非各自的顏色（例如攻擊傷害的紅色）。",
        "Rarity Highlighter": "稀有度標示",
        "Determines when to render the color background on gear items in your inventory. ":
            "決定何時在物品欄的裝備上渲染顏色背景。",
        "Rendering": "渲染",
        "Item Uses Overlay": "物品使用次數顯示",
        "All items with a set amount of uses will now render this as their stack size. Applies to trinkets, charms, necklaces, and key rings.":
            "所有具固定使用次數的物品會將剩餘次數顯示為堆疊數量。適用於飾品、護符、項鍊與鑰匙圈。",
        "Unboxing Details": "開箱詳情",
        "When enabled, displays modifier info in Card and Jewel unboxing screens":
            "啟用時，在卡牌與寶石開箱畫面顯示詞綴資訊",
        "Totem Particles": "圖騰粒子",
        "When enabled, particles coming out of Vault Totems will show.":
            "啟用時，寶庫圖騰散發的粒子將會顯示。",
        "Player Health Bar": "玩家血條",
        "When enabled, replaces heart rendering with a health bar and numeric HP display. Useful for high HP builds.":
            "啟用時，以血條與數字生命值取代愛心顯示。適合高生命值配置。",
        "Hide Off-hand": "隱藏副手",
        "When enabled, the item in your off hand will not render in-game.":
            "啟用時，副手的物品將不會在遊戲中渲染。",
        "Vault Potion Effects": "寶庫藥水效果",
        "When enabled, visual effects for vault potions will show, such as the poison overlay.":
            "啟用時，寶庫藥水的視覺效果將會顯示，例如中毒畫面效果。",
        "Treasure Door Names": "寶藏門名稱",
        "When enabled, treasure doors will render their name on the front.":
            "啟用時，寶藏門會在正面顯示名稱。",
        "Ability Scrolling": "技能滾輪切換",
        "When enabled, allows you to scroll through abilities whilst holding 'Ability Cast Key'.":
            "啟用時，按住「技能施放鍵」可用滾輪切換技能。",
        "Elixir Orb Numbers": "靈藥球數值",
        "When enabled, Elixir Orbs will show numeric values, which helps figuring out what progress the objective more.":
            "啟用時，靈藥球會顯示數值，更容易掌握目標進度。",
        "Early Timer Warning": "提前計時警告",
        "When the timer reaches the threshold, the panic sound (that usually plays at 20s left) will play for 3 seconds.":
            "當計時達到門檻時，將播放 3 秒的緊迫音效（通常在剩餘 20 秒時播放）。",
        "Damage Type": "傷害類型",
        "Click to switch the type of damage you want to change the color of.":
            "點擊以切換要更改顏色的傷害類型。",
        "Color": "顏色",
        "Reset to Default": "重設為預設值",
        "Floored Numbers": "數值取整",
        "Round damage numbers down.": "將傷害數字無條件捨去。",
        "Visibility": "顯示時機",
        "When to show damage particles.": "何時顯示傷害粒子。",
        "When to show mob healthbars.": "何時顯示生物血條。",
        "Healthbar Color": "血條顏色",
        "Healthbar Height": "血條高度",
        "Mob Group Icon": "生物群組圖示",
        "Target Type": "目標類型",
        "Select a chest type to customize color": "選擇要自訂顏色的寶箱類型",
        "Hunter Style": "獵人樣式",
        "Whether to create square outlines around the chests, or generate particles.":
            "在寶箱周圍顯示方形輪廓，或產生粒子。",
        "Particle Density": "粒子密度",
        "Outline Mode": "輪廓模式",
        "Outline Thickness": "輪廓粗細",
        "Particles": "粒子",
        "Outlines": "輪廓",
        "OFF": "關閉",
        "Shifting": "按 Shift 時",
        "Always on": "一律顯示",
    },
    "iskallia/vault/client/gui/screen/accessibility/VaultSoundOptionsScreen.class": {
        "Vault Sound Options": "寶庫音效選項",
        "Page \x01 of \x01": "第 \x01 / \x01 頁",
        "Next": "下一頁",
        "Previous": "上一頁",
        "Done": "完成",
    },
    "iskallia/vault/client/gui/screen/accessibility/InventoryHudEditScreen.class": {
        "Vault HUD Settings": "寶庫 HUD 設定",
        "Shift + Click to edit, Hold Alt to snap to grid.":
            "Shift + 點擊編輯，按住 Alt 對齊格線。",
        "\x01 Settings": "\x01 設定",
        "Settings": "設定",
        "Reset": "重設",
        "Done": "完成",
    },
    "iskallia/vault/client/gui/screen/custom/TabbedScreen.class": {
        "Back": "返回",
    },
}


def _iter_text(
    data: Any, spec: FileSpec, path: tuple[str | int, ...] = ()
) -> Iterator[tuple[tuple[str | int, ...], str]]:
    if isinstance(data, dict):
        for key, value in data.items():
            child = path + (key,)
            if isinstance(value, str):
                if spec.all_values or key in spec.text_fields:
                    yield child, value
            elif isinstance(value, list) and key in spec.list_fields:
                for idx, item in enumerate(value):
                    if isinstance(item, str):
                        yield child + (idx,), item
                    elif isinstance(item, (dict, list)):
                        yield from _iter_text(item, spec, child + (idx,))
            elif isinstance(value, (dict, list)):
                yield from _iter_text(value, spec, child)
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            if isinstance(value, (dict, list)):
                yield from _iter_text(value, spec, path + (idx,))
