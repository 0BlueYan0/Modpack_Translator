from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from modpack_translator.pipeline.glossary import Glossary


# Single-pass regex: matches structural tokens that must be preserved via {N} encoding.
# Minecraft color/format codes are markup, not words. Encoding them prevents cases like
# "&ricon" being treated as one token and leaving "icon" untranslated.
# $$var / $var / namespace:path / 巢狀 NBT 大括號也必須 token 化：這些結構常含
# display、player 等一般單字，不編碼的話正確譯文會被漏翻檢查誤殺，而把結構
# 翻譯掉的壞輸出反而通過（反向篩選）。
_PLACEHOLDERS = re.compile(
    r'\$\([^)]*\)'                          # Patchouli: $(thing), $()
    r'|/\$'                                  # Patchouli shorthand close marker
    r'|\[#\]\([0-9A-Fa-f]*\)'                # Modonomicon markdown color markers
    r'|\((?:item|entry|category|book|command|http|https)://[^)]*\)'  # Modonomicon markdown link targets
    # markdown 連結/圖片目標(GuideME/oracle 指南頁):](path.md#anchor)。目標無空白;
    # 需前綴 ](lookbehind,保留 ] 讓模型看見完整 [文字] 括號對)才凍結,
    # 一般括號詞 (optional) 仍可翻。
    r'|(?<=\])\([^()\s]+\)'
    # 行內 JSX/HTML 標籤(含屬性,GuideME <ItemLink id=… />、<Color color=…>、</Color>):
    # 標籤名+屬性整段凍結,標籤「之間」的內文仍可翻。無空白的 <token> 由下方既有模式涵蓋。
    r'|</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>'
    r'|\\?@[A-Z][A-Z0-9_]*@'                # legacy guide markers: @L@, \@L@, @PAGE@
    r'|\\n'                                 # escaped newline literal
    r'|\\&'                                 # escaped ampersand
    r'|§[0-9A-Za-z]'                        # section codes incl. FancyMenu custom §x §z
    r'|&[0-9A-FK-ORa-fk-or]'                # legacy ampersand color codes
    r'|%\d+\$[sdifcbxo%]'                  # positional: %1$s %2$d
    r'|%[sdifcbxo%]'                        # simple: %s %d %f
    r'|\$\$?[A-Za-z_][A-Za-z0-9_]*(?:=<[^>]*>)?'  # FancyMenu $$var / Patchouli $var、$player=<name>
    r'|<[^<>\s]+>'                          # CLI 用法佔位符：<player|playerUuid>、<amount>
    # 資源位置 namespace:path（小寫、無空白、path 不以 . 結尾）：minecraft:player、c:ores
    r'|(?<![A-Za-z0-9_])[a-z_][a-z0-9_.-]*:[a-z_](?:[a-z0-9_./-]*[a-z0-9_/-])?'
    # 巢狀 NBT 大括號的整段無空白字串塊：{ 與 { 之間不得有 }，確保只吃真巢狀
    r'|\S*\{[^\s}]*\{\S*'
    r'|\{[^{}]+\}'                          # existing curly-brace placeholders
)
_STRUCTURAL_TEXT_RE = re.compile(
    r"^[a-z0-9_.-]+(?::|/)[a-z0-9_./-]+(?:#[a-z0-9_./-]+)?$",
    re.IGNORECASE,
)
# Bare RGB/ARGB hex color, optional leading '#': 3, 4, 6 or 8 hex digits.
_HEX_COLOR_RE = re.compile(r"#?(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{4}|[0-9A-Fa-f]{3})")

_PREAMBLE = re.compile(
    r'^(以下是|翻譯如下|譯文：|Translation:|Here is|Here\'s)\s*'
)


def encode(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def _replace(m: re.Match) -> str:
        idx = len(tokens)
        tokens.append(m.group(0))
        return f"{{{idx}}}"

    return _PLACEHOLDERS.sub(_replace, text), tokens


def decode(text: str, tokens: list[str]) -> str:
    def _restore(m: re.Match) -> str:
        idx = int(m.group(1))
        return tokens[idx] if idx < len(tokens) else m.group(0)

    return re.sub(r"\{(\d+)\}", _restore, text)


def strip_preamble(text: str) -> str:
    return _PREAMBLE.sub("", text).strip()


def _normalized_translation_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


# 全形↔半形標點摺疊：模型輸出 zh 時常把保留原文的標點換成全形
# （"Chunky?" → "Chunky？"）。摺疊後相同即視為「原樣返回」，交由專有
# 名詞豁免等既有規則判斷；非專有名詞來源摺疊後相同仍照樣拒絕，不放寬。
_PUNCT_WIDTH_FOLD = str.maketrans({
    "？": "?", "！": "!", "：": ":", "；": ";", "，": ",",
    "（": "(", "）": ")", "。": ".", "、": ",", "～": "~",
})


def _folded_punct(value: str) -> str:
    return value.translate(_PUNCT_WIDTH_FOLD)


# 靜態譯表：整串（strip 後）命中者以固定譯文直接取代、不呼叫模型
# （runner._static_translation）。同時作為可譯性豁免——像 "E4"（四天王）
# 這種值樣貌像代號、會被不可譯過濾器攔下的詞，因為有確定譯文而必可譯。
STATIC_TRANSLATIONS = {
    "Bosses": "首領",
    "Cat": "貓",
    "Chicken": "雞",
    "Cow": "牛",
    "E4": "四天王",
    "Pig": "豬",
    "Sheep": "綿羊",
    "Villager": "村民",
}


def _has_translatable_text(value: str) -> bool:
    if value.strip() in STATIC_TRANSLATIONS:
        return True
    if _is_structural_text(value):
        return False
    if _is_untranslatable_value(value):
        return False
    return _requires_visible_translation(value)


_GENERIC_UNTRANSLATED_WORDS = {
    "any",
    "bottom",
    "button",
    "claim",
    "click",
    "display",
    "icon",
    "inventory",
    "left",
    "menu",
    "page",
    "player",
    "quest",
    "reward",
    "right",
    "screen",
    "slot",
    "slots",
    "task",
    "tasks",
    "time",
    "top",
    "visible",
}


def is_usable_translation(
    source: str,
    target: str,
    key: str | None = None,
    *,
    accept_identical_proper_noun: bool = False,
    glossary: "Glossary | None" = None,
) -> bool:
    if not _has_translatable_text(source):
        return True

    src = _normalized_translation_value(source)
    dst = _normalized_translation_value(target)
    if not dst:
        return False
    needs_visible_translation = _requires_visible_translation(source)
    if dst == src or _folded_punct(dst) == _folded_punct(src):
        if not needs_visible_translation:
            return True
        # 用語庫守門：整串命中用語庫的原樣返回一律不放行——凌駕下方的
        # 專有名詞豁免與任務標題豁免。呼叫端以 exact_match 譯名取代
        # （runner._translate_validated），或讓該鍵進 diff 重翻（零 API 成本）。
        if glossary is not None and glossary.exact_match(source) is not None:
            return False
        # 任務標題常刻意保留英文專有名詞（模組名、玩家 ID）。既有翻譯檔中
        # 與原文完全相同的標題視為譯者的選擇，不再重複送翻。
        # accept_identical_proper_noun 供模型輸出關卡與快取讀取使用：模型對
        # 專有名詞（模組名、方塊名、人名）原樣返回是正確判斷，不算翻譯失敗；
        # diff_keys 的既有譯文檢查不開啟，避免既有 zh 檔中未翻譯的一般名稱被跳過。
        if not _looks_like_proper_noun_phrase(src):
            return False
        return accept_identical_proper_noun or _is_quest_title_key(key)
    if not _preserves_required_tokens(source, target):
        return False
    if needs_visible_translation and not _has_cjk_text(target):
        return False
    return not _looks_undertranslated(source, target)


# 程式識別字：正確譯文必須原樣保留這些內容，計算「未翻譯殘留」與專有名詞
# 判斷前先剝除，其中的小寫單字（player、button、menu…）才不會被當成漏翻。
_CODE_IDENTIFIER_RE = re.compile(
    r"\$\$\w+"                          # FancyMenu 變數：$$button
    r"|%\w+%"                           # 佔位符變數：%player%
    r"|@\w+(?:\([^()]*\))?"             # 實體過濾器：@player、@animal(age=adult)
    r"|#[\w-]{2,}"                      # 頻道 / 標籤：#allthemons-techsupport
    r"|(?<![\w/])/[a-z][a-z0-9_-]*"     # 斜線指令字面：/time、/gamerule
    r"|\b[\w.]+=\S*"                    # 設定賦值字面：items.1=any、key=value
    r"|\b\w+(?:\.\w+)+(?::\d+)?\b"      # 點分識別字：q.player、some.menu.identifier:505280
    r"|'[^'\s]+'"                       # 引號包住的無空白字面值
    r'|"[^"\s]+"'
    # 中文/全形引號包住的無空白字面值：譯文常把保留的英文指令詞（"display"、
    # "count"…）改用「」『』“”‘’《》〈〉 包住，這些仍是被保留的字面值，
    # 其中的小寫單字不算漏翻殘留。
    r'|[「『“‘《〈][^「」『』“”‘’《》〈〉\s]+[」』”’》〉]'
)
# 括號內逗號分隔的小寫字面值枚舉：(left, right, middle)。這是變數的可能
# 回傳值列表，譯文保留原文（含中文頓號分隔）不算漏翻。
_LITERAL_ENUM_RE = re.compile(
    r"[(（]\s*[a-z][\w-]*(?:\s*[,、，]\s*[a-z][\w-]*)+\s*[)）]"
)


def _strip_code_literals(text: str) -> str:
    text = _CODE_IDENTIFIER_RE.sub(" ", text)
    return _LITERAL_ENUM_RE.sub(" ", text)


def _looks_undertranslated(source: str, target: str) -> bool:
    if not _has_cjk_text(target):
        return False

    src_words = _english_words(_PLACEHOLDERS.sub(" ", source))
    # 只算譯文中全小寫的英文單字：zh_tw 慣例以「譯名 (English Term)」保留
    # 原文標註，人名也常保留英文，這些都是首字大寫，不視為未翻譯殘留。
    plain_target = _strip_code_literals(_PLACEHOLDERS.sub(" ", target))
    target_words = {
        m.group(0) for m in re.finditer(r"\b[a-z]{2,}\b", plain_target)
    }
    leaked = src_words & target_words & _GENERIC_UNTRANSLATED_WORDS
    return bool(leaked)


def _preserves_required_tokens(source: str, target: str) -> bool:
    _encoded, tokens = encode(source)
    for token in tokens:
        if _is_soft_token(token):
            continue
        # FTB Quests 換行有兩種等價寫法：字面 "\n"（反斜線+n）與真換行字元。
        # en_us 與既有翻譯可能各用一種，兩者互相認可，否則整包譯文會被誤判未翻譯。
        if token == "\\n":
            if "\\n" not in target and "\n" not in target:
                return False
            continue
        if token not in target:
            return False
    return True


def _is_soft_token(token: str) -> bool:
    # \& 只是跳脫的 & 符號；譯文改寫句子時捨棄它不影響可讀性
    # §x/§z 等 FancyMenu 自訂格式碼同屬裝飾性標記
    return bool(re.fullmatch(r"§[0-9A-Za-z]|&[0-9A-FK-ORa-fk-or]|\\&", token))


_QUEST_TITLE_KEY_RE = re.compile(
    r"^(?:chapter|chapter_group|quest|task|reward|reward_table|loot_crate|file)\."
    r"[0-9A-Fa-f]+\.(?:title|subtitle|quest_subtitle)(?:\[\d+\])?$"
)
_PROPER_NOUN_CONNECTOR_WORDS = {"a", "an", "and", "de", "of", "the"}


def _is_quest_title_key(key: str | None) -> bool:
    return bool(key and _QUEST_TITLE_KEY_RE.fullmatch(key))


def _looks_like_proper_noun_phrase(text: str) -> bool:
    plain = _PLACEHOLDERS.sub(" ", text)
    plain = _CODE_IDENTIFIER_RE.sub(" ", plain)
    words = re.findall(r"[A-Za-z0-9][\w'.-]*", plain)
    if not words or len(words) > 5:
        return False
    return all(
        word[0].isupper() or word[0].isdigit() or word.lower() in _PROPER_NOUN_CONNECTOR_WORDS
        for word in words
    )


_TRANSLATION_OPTIONAL_WORDS = {
    "ae",
    "api",
    "cf",
    "emi",
    "eu",
    "fabric",
    "fe",
    "forge",
    "ftb",
    "gui",
    "http",
    "https",
    "id",
    "jei",
    "json",
    "kubejs",
    "lvl",
    "minecraft",
    "millibuckets",
    "mo",
    "nbt",
    "neoforge",
    "patchouli",
    "pm",
    "p2p",
    "rei",
    "rf",
    "rpm",
    "snbt",
    "su",
    "url",
    "xp",
}
_KEYBIND_WORDS = {
    "alt",
    "cmd",
    "command",
    "control",
    "ctrl",
    "delete",
    "enter",
    "escape",
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
    "meta",
    "mouse",
    "option",
    "r-click",
    "shift",
    "tab",
}
_UNIT_WORDS = {
    "bar",
    "cf",
    "eu",
    "fe",
    "fps",
    "kb",
    "mm",
    "mb",
    "ms",
    "rf",
    "rpm",
    "tick",
    "ticks",
    "tps",
    "us",
    "xp",
    "μs",
}
# Connectors that glue unit fragments together ("%s mB out of %s mB", "FE per EU").
# Only treated as untranslatable noise when every other word is a unit.
_UNIT_CONNECTOR_WORDS = {"of", "out", "per"}
_GRAMMAR_FRAGMENT_WORDS = {
    "a",
    "an",
    "are",
    "for",
    "has",
    "in",
    "is",
    "of",
    "that",
    "the",
    "to",
    "which",
}
_COPY_ONLY_VALUES = {
    "curseforge",
    "discord",
    "fabric",
    "github",
    "modrinth",
    "neoforge",
    "wiki",
    # Platform / format brand names that stay in English under zh_tw conventions.
    "java",
    "ko-fi",
    "kofi",
    "markdown",
    "mastodon",
    "patreon",
    "reddit",
    "twitter",
    "youtube",
}
_BRAND_WORDS = {
    "ae",
    "advanced",
    "apotheosis",
    "applied",
    "ars",
    "craftoria",
    "create",
    "crowdin",
    "energistics",
    "emi",
    "fabric",
    "immersive",
    "industrial",
    "industrialization",
    "modonomicon",
    "mekanism",
    "modrinth",
    "neoforge",
    "nouveau",
    "occultism",
    "patchouli",
    "pneumaticcraft",
    "powah",
}
_CODE_WORDS = {
    "boolean",
    "class",
    "double",
    "float",
    "int",
    "long",
    "private",
    "protected",
    "public",
    "return",
    "static",
    "string",
    "void",
}


def _has_cjk_text(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def _is_structural_text(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.startswith(("{", "[")):
        try:
            return isinstance(json.loads(text), (dict, list))
        except json.JSONDecodeError:
            pass
    if _STRUCTURAL_TEXT_RE.fullmatch(text):
        return True
    if text.startswith(("{", "[", "#")) and not re.search(r"\s", text):
        return True
    return False


def _is_translatable_entry(key: str, value: str) -> bool:
    if classify_translation_entry(key, value) != "translate":
        return False
    return True


def classify_translation_entry(key: str, value: str) -> str:
    """Classify a lang value as translate/copy/skip without changing file formats."""
    if not _has_translatable_text(value):
        return "skip"

    lowered_key = key.lower()
    if _is_metadata_key(lowered_key):
        return "copy"
    if lowered_key.endswith(".advancement.title.root") and _value_slug_in_key(lowered_key, value):
        return "copy"
    if _is_keybind_key(lowered_key) and _is_keybind_or_shortcut(value):
        return "copy"
    if _is_copy_only_key_value(lowered_key, value):
        return "copy"
    return "translate"


def _is_metadata_key(key: str) -> bool:
    if key.endswith(".author") or ".author." in key:
        return True
    # 作者署名（holiday.mekanism.signature = "-aidancbrady"）原樣保留
    if key.endswith(".signature"):
        return True
    if "painting." in key and key.endswith(".author"):
        return True
    # 唱片曲目說明（artist - title）：music_disc_*.desc、disc_*.desc 都是
    if re.search(r"(?:^|[._-])disc[._-]", key) and key.endswith((".desc", ".description")):
        return True
    # 拉丁學名（productivetrees/productivefarming 的 *.latin）原樣保留
    if key.endswith(".latin"):
        return True
    if key.startswith(("itemgroup.", "key.category.")):
        return True
    if key.startswith("category.") and key.endswith(".keybinding"):
        return True
    if key.startswith("__comment"):
        return True
    return False


def _is_keybind_key(key: str) -> bool:
    return any(part in key for part in ("keybind", "keyboard", "shortcut", ".key_", "modifier."))


def _is_copy_only_key_value(key: str, value: str) -> bool:
    text = _normalized_translation_value(value)
    lowered = text.lower()
    if lowered in _COPY_ONLY_VALUES:
        return True
    if key.startswith("mod_menu.") and (
        ".badge." in key
        or key.endswith((
            ".crowdin", ".modrinth", ".discord", ".github", ".wiki",
            ".kofi", ".patreon", ".reddit", ".twitter", ".mastodon",
            ".youtube", ".curseforge",
        ))
    ):
        return True
    # 社群/平台連結鍵：值是帳號代稱或連結（quark.gui.config.social.reddit =
    # "/r/QuarkMod Reddit"），原樣保留。與上方 mod_menu 後綴清單一致。
    if key.endswith((
        ".docs", ".discord", ".github", ".modrinth", ".wiki",
        ".reddit", ".twitter", ".mastodon", ".youtube",
        ".patreon", ".kofi", ".curseforge", ".crowdin",
    )):
        return True
    # 指令鍵下的單一小寫單字是指令字面值（如 create.command.killTPSCommand = "killtps"）
    if ".command" in key and re.fullmatch(r"[a-z][a-z0-9_-]{2,}", text):
        return True
    # 連結鍵下的專案 slug（aquamirae.obscure_book.mod_link = "ob-aquamirae"）：
    # 純小寫、以 -_. 相連的單一 token 是平台代稱/網址片段，原樣保留。
    # 散文值（"Click here to open the link"）含空白不中，仍要翻譯。
    if key.endswith((".link", "_link")) and re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)+", text):
        return True
    # 指令用法文法一覽（lootr.commands.usage = "/lootr cart | cart <loot-table> | …"）：
    # 以 / 起頭且含多重 | 分隔的子指令與 <參數> 佔位，是語法非散文，模型只能原樣
    # 返回；含管線的散文不以 / 起頭、單一指令說明文無多重管線，皆不受影響。
    if text.startswith("/") and text.count("|") >= 2:
        return True
    # 值=鍵尾片段的開發用識別字（painting prototype_701、tooltip taskdesc3）：
    # 純小寫單一 token、含數字或底線、且整個值出現在鍵名中 → 佔位殘字/代號，
    # 模型只能原樣返回，原樣保留。一般顯示名稱（Diamond、stone）無數字底線不中。
    if (
        re.fullmatch(r"[a-z][a-z0-9_]*", text)
        and re.search(r"[0-9_]", text)
        and _value_slug_in_key(key, text)
    ):
        return True
    if key.endswith((".color", ".colour")) and _HEX_COLOR_RE.fullmatch(text):
        return True
    if ".configuration." in key and key.endswith((".title", ".toml.title")) and _looks_like_config_title(text):
        return True
    if key.startswith(("chapter.", "chapter_group.")) and key.endswith(".title") and _looks_like_brand_name(text):
        return True
    if text.lower().startswith("the ") and _value_slug_in_key(key, text[4:]):
        return True
    return False


def _value_slug_in_key(key: str, value: str) -> bool:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not slug or len(slug) < 4:
        return False
    return slug in re.sub(r"[^a-z0-9]+", "_", key)


def _is_untranslatable_value(value: str) -> bool:
    text = _normalized_translation_value(value)
    if not text:
        return True
    if _LOCALIZATION_KEY_RE.fullmatch(text):
        return True
    if _RESOURCE_LOCATION_RE.fullmatch(text):
        return True
    if _is_url_or_domain(text):
        return True
    if _is_hex_color(text):
        return True
    if _is_placeholder_or_unit_fragment(text):
        return True
    if _is_short_grammar_fragment(text):
        return True
    if _is_keybind_or_shortcut(text):
        return True
    if _looks_like_credit(text):
        return True
    if _looks_like_code_or_table_line(text):
        return True
    if _looks_like_function_signature(text):
        return True
    if _looks_like_command_usage(text):
        return True
    if _looks_like_config_assignment(text):
        return True
    if _is_time_format(text):
        return True
    if _is_consonant_acronym(text):
        return True
    if _looks_like_vocalization(text):
        return True
    if _looks_like_color_code_art(text):
        return True
    if _looks_like_obfuscated_text(text):
        return True
    return False


def _is_url_or_domain(text: str) -> bool:
    # 色碼包住的裸域名（"&o&bexample.github.io&f&r."）也是純連結：
    # 剝除色碼等標記與頭尾標點後再整串比對。
    plain = _PLACEHOLDERS.sub(" ", text).strip()
    plain = plain.rstrip(" .,;:!?…")
    if re.fullmatch(r"[a-z][a-z0-9+.-]*://\S+", plain, re.IGNORECASE):
        return True
    return bool(re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?", plain, re.IGNORECASE))


# 同字母重複 1-4 次的段（yyyy、MM、HH），段間以日期分隔符相連；兩段各自
# 用獨立群組與反向參照（\1 與 \2），重複段每輪各自比對自己的字母。
_DATE_FORMAT_RE = re.compile(
    r"([yYMdDHhmsSaGEwWkK])\1{0,3}"
    r"(?:[\s/:.,T-]+([yYMdDHhmsSaGEwWkK])\2{0,3})+"
)
_DATE_STRONG_LETTERS = set("yYMdDHhms")


def _is_time_format(text: str) -> bool:
    # 日期/時間格式樣板（AE2 ETAFormat = "HH:mm:ss"、corpse date_format =
    # "yyyy/MM/dd HH:mm:ss"）。以「同字母重複的段 + 分隔符」的樣式辨識，避免
    # 湊巧只用到格式字母的散文（"same day"、"Yes Sir"）被誤判。
    if not _DATE_FORMAT_RE.fullmatch(text):
        return False
    return any(ch in _DATE_STRONG_LETTERS for ch in text)


def _is_consonant_acronym(text: str) -> bool:
    """單一無母音的小寫縮寫（thermal *.keyword = "tnt"）視為不可譯。

    真正的英文單字必含母音（a/e/i/o/u/y）；全由子音組成的 2-6 字母小寫單字
    是縮寫/代號（tnt、rf、fe…），模型只能原樣返回而被輸出關卡誤殺。僅限
    「整個值就是單一 token」時觸發，避免誤傷多字關鍵字（"blaze fire tnt"）。
    """
    if not re.fullmatch(r"[a-z]{2,6}", text):
        return False
    return not any(ch in "aeiouy" for ch in text)


_VOCALIZATION_RUN_RE = re.compile(r"([A-Za-z])\1{3,}")


def _looks_like_vocalization(text: str) -> bool:
    """純母音擬聲吟唱（botania.subtitle.way 的 Ievan Polkka scat 唱段）。

    幾乎全是母音、且含 4 個以上相同字母的連寫（"oooooooooo"、"AAAA"），
    拼不出可翻譯的詞，任何模型輸出都無法通過驗證，直接視為不可譯。
    散文母音比例約 0.4，永遠不會誤觸此門檻。
    """
    plain = _PLACEHOLDERS.sub(" ", text)
    letters = re.findall(r"[A-Za-z]", plain)
    if len(letters) < 12:
        return False
    vowels = sum(1 for ch in letters if ch.lower() in "aeiou")
    if vowels / len(letters) < 0.8:
        return False
    return bool(_VOCALIZATION_RUN_RE.search(plain))


_FORMAT_CODE_RE = re.compile(r"[§&]([0-9A-FK-ORa-fk-or])")


def _looks_like_obfuscated_text(text: str) -> bool:
    """全文都在 §k 亂碼特效下的字串（paraglider anti_vessel 的彩蛋 tooltip）。

    §k 在遊戲中渲染成不斷隨機跳動的亂碼字元，實際字元內容永遠不可見，
    內容也多為鍵盤亂打（asdf…），模型輸出必然無法通過驗證。色碼與 §r
    會重置格式（含 §k），逐段追蹤亂碼狀態；只要有任何可見文字段含字母
    就不算，仍要翻譯。
    """
    if not re.search(r"[§&][Kk]", text):
        return False
    obfuscated = False
    pos = 0
    for m in _FORMAT_CODE_RE.finditer(text):
        segment = text[pos:m.start()]
        if not obfuscated and re.search(r"[^\W\d_]", segment):
            return False
        code = m.group(1).lower()
        if code == "k":
            obfuscated = True
        elif code == "r" or code in "0123456789abcdef":
            obfuscated = False
        pos = m.end()
    tail = text[pos:]
    return obfuscated or not re.search(r"[^\W\d_]", tail)


def _looks_like_color_code_art(text: str) -> bool:
    """色碼穿插在單字字母之間的藝術字標題（如 "&l&cDy&6en&ea&ami&bcs&r"）。

    每個色碼之間只剩 1-3 個字母的碎片，拼不出可翻譯的單字，任何模型
    輸出都無法通過驗證，直接視為不可譯。"""
    codes = re.findall(r"[&§][0-9A-FK-ORa-fk-or]", text)
    if len(codes) < 4:
        return False
    fragments = [
        part.strip()
        for part in re.split(r"[&§][0-9A-FK-ORa-fk-or]", text)
        if part.strip()
    ]
    return bool(fragments) and all(len(part) <= 3 for part in fragments)


def _is_hex_color(text: str) -> bool:
    """A bare hex color value (dde9f4, #1a2b3c) is markup, never prose.

    Used across every Minecraft version for text/title colors (e.g. Traveler's
    Titles `.color` keys). A digit is required so all-letter words that happen to
    be valid hex (facade, decade, beaded, cafe) stay translatable.
    """
    if not _HEX_COLOR_RE.fullmatch(text):
        return False
    return any(ch.isdigit() for ch in text)


def _is_placeholder_or_unit_fragment(text: str) -> bool:
    stripped = _PLACEHOLDERS.sub(" ", text)
    stripped = re.sub(r"[<>=~+\-–—/:|(),.%\s\d]+", " ", stripped)
    words = [word.lower() for word in re.findall(r"[A-Za-zμ]+", stripped)]
    if not words or not any(word in _UNIT_WORDS for word in words):
        return False
    return all(word in _UNIT_WORDS or word in _UNIT_CONNECTOR_WORDS for word in words)


def _is_short_grammar_fragment(text: str) -> bool:
    if not re.search(r"%\d*\$?[sdifcbxo]|%[sdifcbxo]", text):
        return False
    stripped = _PLACEHOLDERS.sub(" ", text)
    words = [word.lower() for word in re.findall(r"[A-Za-z]+", stripped)]
    return bool(words) and len(words) <= 4 and all(word in _GRAMMAR_FRAGMENT_WORDS for word in words)


def _is_keybind_or_shortcut(text: str) -> bool:
    simplified = re.sub(r"[_+/,|()-]+", " ", text.lower())
    words = re.findall(r"[a-z0-9-]+", simplified)
    if not words:
        return False
    if all(word in _KEYBIND_WORDS or re.fullmatch(r"[a-z0-9]", word) for word in words):
        return True
    return False


def _looks_like_credit(text: str) -> bool:
    if " - " not in text:
        return False
    left, _, right = text.partition(" - ")
    left, right = left.strip(), right.strip()
    if not left or not right:
        return False
    if not re.fullmatch(r"[A-Z][A-Za-z0-9' ._-]+", left):
        return False
    # 真正的「作者 - 標題」署名左半是短名稱（Direwolf20、AllTheMods）。破折號
    # 敘事句左半是整個子句（"Team Rocket's masterwork sits in the heart of the
    # volcano"），字數多，不算署名——否則整段描述會被誤判不可譯而跳過。
    return len(re.findall(r"\S+", left)) <= 4


_FUNC_SIGNATURE_ARG_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*"          # 參數名
    r"(?:\s*(?:\?:|=)\s*[^,]*)?"       # 選填預設值（?: ''）或型別枚舉（=Object|String）
)


def _looks_like_function_signature(text: str) -> bool:
    """整串是函式呼叫簽名（craftpresence *.usage："asIcon(input, …)"、"length(input)"）。

    程式碼用法示範的正確譯文必為原樣，模型原樣返回會被輸出關卡誤殺，
    直接視為不可譯。名稱限小寫開頭（散文複數標記 "Item(s)" 首字大寫不中）；
    lowerCamelCase 名稱必為程式識別字，全小寫名稱另要求每個參數都像識別字
    且參數名 ≥3 字元（"second(s)" 的複數標記不中）。
    """
    m = re.fullmatch(r"([a-z_][A-Za-z0-9_]*)\((.*)\)", text.strip())
    if not m:
        return False
    name, args = m.groups()
    if re.search(r"[a-z][A-Z]", name):
        return True
    parts = [part.strip() for part in args.split(",")]
    if not all(_FUNC_SIGNATURE_ARG_RE.fullmatch(part) for part in parts):
        return False
    return all(
        len(re.match(r"[A-Za-z_][A-Za-z0-9_]*", part).group(0)) >= 3 for part in parts
    )


def _looks_like_command_usage(text: str) -> bool:
    """整串是斜線指令用法（ftbquests："/ftbquests export_rewards_to_chest <reward_table>"）。

    佔位符（<arg>）以外全為指令 token 且總長 ≤4 個 token 才算；token 須以
    小寫開頭，camelCase 遊戲規則名（"/gamerule sendCommandFeedback true"）
    是程式識別字也算。首字大寫的散文說明（"/home Teleports you to your Home"）
    不受影響，仍要翻譯。
    """
    plain = _PLACEHOLDERS.sub(" ", text).strip()
    if not plain.startswith("/"):
        return False
    if not re.fullmatch(
        r"/[a-z][a-z0-9_:-]*(?:\s+(?:[a-z][A-Za-z0-9_@.\[\]|:<>-]*|[a-z0-9_@.\[\]|:<>-]+))*",
        plain,
    ):
        return False
    return len(plain.split()) <= 4


def _looks_like_config_assignment(text: str) -> bool:
    """整串是無空白的設定語法行（ETF："§aitems.<n>=<list|none|any|holding|wearing>"）。

    佔位符與色碼以外只剩鍵名、= 與枚舉分隔符，無散文可翻，模型只能
    原樣返回，直接視為不可譯。"""
    stripped = text.strip()
    if "=" not in stripped or re.search(r"\s", stripped):
        return False
    plain = _PLACEHOLDERS.sub("", stripped)
    return bool(re.fullmatch(r"[A-Za-z0-9_.,|:=<>/\[\]()-]*", plain))


def _looks_like_config_title(text: str) -> bool:
    return bool(re.search(r"\b(?:config|configuration|toml)\b", text, re.IGNORECASE))


def _looks_like_brand_name(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z0-9+-]*", text)
    if not words or len(words) > 4:
        return False
    lowered = {word.lower() for word in words}
    return bool(lowered & _BRAND_WORDS)


def _looks_like_code_or_table_line(text: str) -> bool:
    plain = _PLACEHOLDERS.sub(" ", text).strip()
    plain = re.sub(r"^[&§][0-9A-FK-ORa-fk-or]\s*", "", plain)
    if re.fullmatch(r"(?:[-+*]\s*)?Tier\s+\d+\s*(?:[-=]*>|,)\s*\d+:\d+", plain, re.IGNORECASE):
        return True
    if re.fullmatch(r"(?:[-+*]\s*)?(?:public|private|protected)\s+[\w<>\[\]]+\s+\w+\s*\(.*", plain):
        return True
    if re.search(r"[;{}]$", plain) and re.search(r"\b(?:%s)\b" % "|".join(_CODE_WORDS), plain, re.IGNORECASE):
        return True
    # 型別關鍵字後緊接識別字與程式標點才算宣告式程式碼（long x = …、void foo(…、
    # int n = 0）。一般散文只是句中含 long/void/return/class/string 等字，加上任意
    # 括號（"a long list (or can)"）不該被誤判為程式碼而整段跳過不翻。
    code_words = "|".join(_CODE_WORDS)
    if re.search(r"\b(?:%s)\b\s+[A-Za-z_]\w*\s*=\s*\S" % code_words, plain, re.IGNORECASE):
        return True
    if re.search(r"\b(?:%s)\b\s+[A-Za-z_]\w*\(" % code_words, plain, re.IGNORECASE):
        return True
    return False


# 鍵盤快捷鍵和弦（Alt+F3、Ctrl+Shift+S）：修飾鍵/功能鍵/單一字元以 + 相連。
# 整組視為快捷鍵標記剝除，否則 "FPS / TPS (Alt+F3)" 的 Alt 會被當一般英文詞
# 送翻，模型原樣返回再被輸出關卡誤殺。
_KEY_CHORD_PART = r"(?:ctrl|alt|shift|cmd|meta|option|control|tab|esc|del|ins|end|home|f\d{1,2}|[a-z0-9])"
_KEY_CHORD_RE = re.compile(
    rf"\b{_KEY_CHORD_PART}(?:\s*\+\s*{_KEY_CHORD_PART})+\b",
    re.IGNORECASE,
)


def _requires_visible_translation(source: str) -> bool:
    text = _PLACEHOLDERS.sub(" ", source)
    text = re.sub(r"[a-z][a-z0-9+.-]*://\S+", " ", text, flags=re.IGNORECASE)
    text = _KEY_CHORD_RE.sub(" ", text)
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    return any(_is_translation_required_word(word) for word in words)


def _is_translation_required_word(word: str) -> bool:
    normalized = word.strip("'-")
    if len(normalized) < 2:
        return False
    if normalized.lower() in _TRANSLATION_OPTIONAL_WORDS:
        return False
    if normalized.isupper():
        return False
    if re.fullmatch(r"[A-Z0-9]+s?", normalized):
        return False
    if re.search(r"[a-z][A-Z]", normalized):
        return False
    return bool(re.search(r"[a-z]", normalized))


def _english_words(value: str) -> set[str]:
    return {m.group(0).lower() for m in re.finditer(r"[A-Za-z]{2,}", value)}


def diff_keys(
    en_dict: dict[str, str],
    zh_dict: dict[str, str],
    glossary: "Glossary | None" = None,
) -> set[str]:
    """Return keys that are missing from zh or still identical to en."""
    translatable_keys = {
        k for k, value in en_dict.items()
        if _is_translatable_entry(k, value)
    }
    missing = translatable_keys - set(zh_dict)
    untranslated = {
        k
        for k in translatable_keys
        if k in zh_dict
        and not is_usable_translation(en_dict[k], zh_dict[k], key=k, glossary=glossary)
    }
    return missing | untranslated


# ------------------------------------------------------------------ readers

def parse_json_lang(raw: str) -> dict[str, str]:
    data = json.loads(raw)
    return {k: v for k, v in data.items() if isinstance(v, str)}


def parse_legacy_lang(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value
    return result


def parse_snbt_lang(raw: str) -> dict[str, str]:
    try:
        data = json.loads(raw)
        result: dict[str, str] = {}
        for key, value in data.items():
            _append_snbt_lang_value(result, key, value)
        return result
    except json.JSONDecodeError:
        pass

    result: dict[str, str] = {}
    key_re = re.compile(r'^\s*(?:"((?:[^"\\]|\\.)*)"|([\w.\-/]+))\s*:', re.MULTILINE)
    consumed_spans: list[tuple[int, int]] = []
    for m in key_re.finditer(raw):
        if any(start <= m.start() < end for start, end in consumed_spans):
            continue

        key = _json_unescape(m.group(1) if m.group(1) is not None else m.group(2))
        pos = m.end()
        while pos < len(raw) and raw[pos].isspace():
            pos += 1
        if pos >= len(raw):
            continue

        if raw[pos] == "[":
            array_raw, end = _read_balanced_snbt_value(raw, pos)
            consumed_spans.append((m.start(), end))
            body = array_raw[1:-1]
            for idx, item in enumerate(_parse_snbt_array_items(body)):
                result[f"{key}[{idx}]"] = item
            continue

        if raw[pos] == '"':
            value, end = _read_snbt_quoted_string(raw, pos)
            consumed_spans.append((m.start(), end))
            result[key] = value
    return result


def _append_snbt_lang_value(result: dict[str, str], key: str, value: Any) -> None:
    if isinstance(value, str):
        result[key] = value
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, str):
                result[f"{key}[{idx}]"] = item


def format_snbt_lang(values: dict[str, str]) -> str:
    lines = ["{"]
    emitted_arrays: set[str] = set()
    for key, value in values.items():
        array_key = _split_snbt_array_entry_key(key)
        if array_key is not None:
            base_key, _idx = array_key
            if base_key in emitted_arrays:
                continue
            emitted_arrays.add(base_key)
            lines.append(f"\t{_snbt_key(base_key)}: [")
            for item in _snbt_array_items(values, base_key):
                lines.append(f"\t\t{_snbt_string(item)}")
            lines.append("\t]")
            continue
        lines.append(f"\t{_snbt_key(key)}: {_snbt_string(value)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _split_snbt_array_entry_key(key: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"(.+)\[(\d+)\]", key)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def _snbt_array_items(values: dict[str, str], base_key: str) -> list[str]:
    items: list[tuple[int, str]] = []
    for key, value in values.items():
        array_key = _split_snbt_array_entry_key(key)
        if array_key is not None and array_key[0] == base_key:
            items.append((array_key[1], value))
    return [value for _idx, value in sorted(items)]


def _snbt_key(key: str) -> str:
    if re.fullmatch(r"[\w.\-/]+", key):
        return key
    return _snbt_string(key)


def _snbt_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_unescape(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value.replace('\\"', '"')


def read_jar_text(source_file: Path, path_in_jar: str) -> str:
    with zipfile.ZipFile(source_file) as zf:
        return zf.read(path_in_jar).decode("utf-8-sig")


def jar_member_exists(source_file: Path, path_in_jar: str) -> bool:
    with zipfile.ZipFile(source_file) as zf:
        return path_in_jar in zf.namelist()


def read_json_lang(source_file: Path, path_in_jar: str | None) -> dict[str, str]:
    if path_in_jar:
        raw = read_jar_text(source_file, path_in_jar)
    else:
        raw = source_file.read_text(encoding="utf-8")
    return parse_json_lang(raw)


def read_legacy_lang(source_file: Path, path_in_jar: str | None) -> dict[str, str]:
    if path_in_jar:
        raw = read_jar_text(source_file, path_in_jar)
    else:
        raw = source_file.read_text(encoding="utf-8")
    return parse_legacy_lang(raw)


def read_snbt_lang(source_file: Path) -> dict[str, str]:
    """Parse FTB Quests / Heracles SNBT lang file.

    Format uses unquoted keys and quoted values, one per line:
        chapter.016D52CB8F1295E5.title: " &eNew Age"
        quest.001201DAFCC3FAEC.title: "Drink Mayonnaise"
    """
    return parse_snbt_lang(source_file.read_text(encoding="utf-8"))

    raw = source_file.read_text(encoding="utf-8")

    # Try standard JSON first (Heracles or future formats may use it)
    try:
        data = json.loads(raw)
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except json.JSONDecodeError:
        pass

    # FTB Quests SNBT: unquoted key, colon, quoted value
    # key chars: word chars, dots, hyphens — no whitespace
    result: dict[str, str] = {}
    for m in re.finditer(
        r'^\s*([\w.\-]+)\s*:\s*"((?:[^"\\]|\\.)*)"',
        raw,
        re.MULTILINE,
    ):
        result[m.group(1)] = m.group(2)
    return result


def read_bq_lang(source_file: Path) -> dict[str, str]:
    """Parse legacy Better Questing .lang format (key=value per line)."""
    return parse_legacy_lang(source_file.read_text(encoding="utf-8"))


def read_patchouli_page(source_file: Path, path_in_jar: str) -> dict[str, Any]:
    with zipfile.ZipFile(source_file) as zf:
        return json.loads(zf.read(path_in_jar).decode("utf-8-sig"))


PATCHOULI_TEXT_FIELDS = ("text", "title", "header", "name")
PATCHOULI_VISIBLE_TEXT_FIELDS = (
    "text",
    "title",
    "header",
    "name",
    "description",
    "link_text",
)
_PATCHOULI_STRUCTURAL_FIELDS = {
    "advancement",
    "anchor",
    "category",
    "entity",
    "extra_recipe_mappings",
    "flag",
    "icon",
    "images",
    "ingredient",
    "ingredients",
    "item",
    "items",
    "multiblock",
    "multiblock_id",
    "parent",
    "recipe",
    "recipe2",
    "tag",
    "trigger",
    "turnin",
    "type",
    "url",
}
_PATCHOULI_TEXT_SUFFIXES = (
    "_text",
    "_title",
    "_header",
    "_description",
    "_label",
)
_RESOURCE_LOCATION_RE = re.compile(
    r"^[a-z0-9_.-]+(?::|/)[a-z0-9_./-]+(?:#[a-z0-9_./-]+)?$",
    re.IGNORECASE,
)
_LOCALIZATION_KEY_RE = re.compile(r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+)+$", re.IGNORECASE)
_JSON_PATH_PART_RE = re.compile(
    r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]|\[(\"(?:[^\"\\]|\\.)*\")\]"
)


def read_patchouli_text(data: Any) -> dict[str, str]:
    """Extract player-visible Patchouli strings as stable JSON-path keys."""
    result: dict[str, str] = {}
    for path, value in _iter_patchouli_text(data):
        result[_patchouli_path_key(path)] = value
    return result


def _parse_snbt_array_items(body: str) -> list[str]:
    items: list[str] = []
    pos = 0
    while pos < len(body):
        while pos < len(body) and (body[pos].isspace() or body[pos] == ","):
            pos += 1
        if pos >= len(body):
            break

        char = body[pos]
        if char == '"':
            value, pos = _read_snbt_quoted_string(body, pos)
            items.append(value)
            continue
        if char in "{[":
            value, pos = _read_balanced_snbt_value(body, pos)
            items.append(value.strip())
            continue

        start = pos
        while pos < len(body) and body[pos] not in ",\r\n":
            pos += 1
        value = body[start:pos].strip()
        if value:
            items.append(value)
    return items


def _read_snbt_quoted_string(value: str, start: int) -> tuple[str, int]:
    pos = start + 1
    escaped = False
    while pos < len(value):
        char = value[pos]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return _json_unescape(value[start + 1:pos]), pos + 1
        pos += 1
    return _json_unescape(value[start + 1:]), len(value)


def _read_balanced_snbt_value(value: str, start: int) -> tuple[str, int]:
    opening = value[start]
    closing = "}" if opening == "{" else "]"
    stack = [closing]
    pos = start + 1
    in_string = False
    escaped = False
    while pos < len(value):
        char = value[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return value[start:pos + 1], pos + 1
        pos += 1
    return value[start:], len(value)


def write_patchouli_text(data: Any, path_key: str, value: str) -> None:
    path = _parse_patchouli_path_key(path_key)
    cursor = data
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value


def _iter_patchouli_text(data: Any, path: tuple[str | int, ...] = ()):
    if isinstance(data, dict):
        for key, value in data.items():
            child_path = path + (key,)
            if isinstance(value, str):
                if _is_patchouli_text_field(key) and _is_patchouli_visible_text_value(value):
                    yield child_path, value
            elif isinstance(value, (dict, list)):
                yield from _iter_patchouli_text(value, child_path)
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            child_path = path + (idx,)
            if isinstance(value, str):
                if path and path[-1] == "pages" and _is_patchouli_visible_text_value(value):
                    yield child_path, value
            elif isinstance(value, (dict, list)):
                yield from _iter_patchouli_text(value, child_path)


def _is_patchouli_text_field(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    if lowered in _PATCHOULI_STRUCTURAL_FIELDS:
        return False
    return lowered in PATCHOULI_VISIBLE_TEXT_FIELDS or lowered.endswith(_PATCHOULI_TEXT_SUFFIXES)


def _is_patchouli_visible_text_value(value: str) -> bool:
    text = value.strip()
    if len(text) < 2:
        return False
    if text.startswith(("#", "{", "[")):
        return False
    if re.fullmatch(r"[a-z][a-z0-9+.-]*://\S+", text, re.IGNORECASE):
        return False
    if _RESOURCE_LOCATION_RE.fullmatch(text):
        return False
    if _LOCALIZATION_KEY_RE.fullmatch(text):
        return False
    return True


def _patchouli_path_key(path: tuple[str | int, ...]) -> str:
    if len(path) == 1 and isinstance(path[0], str):
        return path[0]

    result = "$"
    for part in path:
        if isinstance(part, int):
            result += f"[{part}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
            result += f".{part}"
        else:
            result += f"[{json.dumps(part)}]"
    return result


def _parse_patchouli_path_key(path_key: str) -> tuple[str | int, ...]:
    if not path_key.startswith("$"):
        return (path_key,)

    path: list[str | int] = []
    pos = 1
    while pos < len(path_key):
        match = _JSON_PATH_PART_RE.match(path_key, pos)
        if not match:
            raise ValueError(f"Invalid Patchouli path: {path_key}")
        if match.group(1) is not None:
            path.append(match.group(1))
        elif match.group(2) is not None:
            path.append(int(match.group(2)))
        else:
            path.append(json.loads(match.group(3)))
        pos = match.end()
    return tuple(path)


INLINE_SNBT_TEXT_FIELDS = ("title", "subtitle", "description", "text", "hover", "name")
_INLINE_FIELD_RE = re.compile(
    r'(?P<prefix>\b(?P<field>title|subtitle|description|text|hover|name)\s*:\s*)"(?P<value>(?:[^"\\]|\\.)*)"',
    re.IGNORECASE,
)
_INLINE_ARRAY_FIELD_RE = re.compile(
    r'\b(?P<field>title|subtitle|description|text|hover|name)\s*:\s*\[(?P<body>.*?)\]',
    re.IGNORECASE | re.DOTALL,
)
_STRING_LITERAL_RE = re.compile(r'"(?P<value>(?:[^"\\]|\\.)*)"')


def read_inline_snbt_text(source_file: Path) -> dict[str, str]:
    raw = source_file.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    for idx, (field, _start, _end, value) in enumerate(_iter_inline_snbt_text_matches(raw)):
        if _is_translatable_inline_text(value):
            result[f"{idx}:{field}"] = value
    return result


def replace_inline_snbt_text(raw: str, translations: dict[str, str]) -> str:
    pieces: list[str] = []
    last = 0

    for idx, (field, start, end, _value) in enumerate(_iter_inline_snbt_text_matches(raw)):
        key = f"{idx}:{field}"
        if key not in translations:
            continue

        pieces.append(raw[last:start])
        pieces.append(_json_escape(translations[key]))
        last = end

    if not pieces:
        return raw

    pieces.append(raw[last:])
    return "".join(pieces)


def _iter_inline_snbt_text_matches(raw: str) -> list[tuple[str, int, int, str]]:
    matches: list[tuple[str, int, int, str]] = []

    for m in _INLINE_FIELD_RE.finditer(raw):
        matches.append((
            m.group("field").lower(),
            m.start("value"),
            m.end("value"),
            _json_unescape(m.group("value")),
        ))

    for array_match in _INLINE_ARRAY_FIELD_RE.finditer(raw):
        body = array_match.group("body")
        offset = array_match.start("body")
        field = array_match.group("field").lower()
        for string_match in _STRING_LITERAL_RE.finditer(body):
            matches.append((
                field,
                offset + string_match.start("value"),
                offset + string_match.end("value"),
                _json_unescape(string_match.group("value")),
            ))

    matches.sort(key=lambda item: item[1])
    return matches


def _json_escape(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _is_translatable_inline_text(value: str) -> bool:
    text = value.strip()
    if len(text) < 2:
        return False
    if re.fullmatch(r"[a-z0-9_.:/#\-]+", text, re.IGNORECASE):
        return False
    if text.startswith(("{", "[", "$(", "#")):
        return False
    if "://" in text:
        return False
    if re.search(r"[\u3400-\u9fff]", text):
        return False
    return bool(re.search(r"[A-Za-z]", text))
