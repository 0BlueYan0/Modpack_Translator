from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any


# Single-pass regex: matches structural tokens that must be preserved via {N} encoding.
# Minecraft color/format codes are markup, not words. Encoding them prevents cases like
# "&ricon" being treated as one token and leaving "icon" untranslated.
_PLACEHOLDERS = re.compile(
    r'\$\([^)]*\)'                          # Patchouli: $(thing), $()
    r'|/\$'                                  # Patchouli shorthand close marker
    r'|\[#\]\([0-9A-Fa-f]*\)'                # Modonomicon markdown color markers
    r'|\((?:item|entry|category|book|command|http|https)://[^)]*\)'  # Modonomicon markdown link targets
    r'|\\?@[A-Z][A-Z0-9_]*@'                # legacy guide markers: @L@, \@L@, @PAGE@
    r'|\\n'                                 # escaped newline literal
    r'|\\&'                                 # escaped ampersand
    r'|[&§][0-9A-FK-ORa-fk-or]'             # Minecraft color/format codes
    r'|%\d+\$[sdifcbxo%]'                  # positional: %1$s %2$d
    r'|%[sdifcbxo%]'                        # simple: %s %d %f
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


def _has_translatable_text(value: str) -> bool:
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


def is_usable_translation(source: str, target: str, key: str | None = None) -> bool:
    if not _has_translatable_text(source):
        return True

    src = _normalized_translation_value(source)
    dst = _normalized_translation_value(target)
    if not dst:
        return False
    needs_visible_translation = _requires_visible_translation(source)
    if dst == src:
        if not needs_visible_translation:
            return True
        # 任務標題常刻意保留英文專有名詞（模組名、玩家 ID）。既有翻譯檔中
        # 與原文完全相同的標題視為譯者的選擇，不再重複送翻。
        return _is_quest_title_key(key) and _looks_like_proper_noun_phrase(src)
    if not _preserves_required_tokens(source, target):
        return False
    if needs_visible_translation and not _has_cjk_text(target):
        return False
    return not _looks_undertranslated(source, target)


def _looks_undertranslated(source: str, target: str) -> bool:
    if not _has_cjk_text(target):
        return False

    src_words = _english_words(_PLACEHOLDERS.sub(" ", source))
    # 只算譯文中全小寫的英文單字：zh_tw 慣例以「譯名 (English Term)」保留
    # 原文標註，人名也常保留英文，這些都是首字大寫，不視為未翻譯殘留。
    target_words = {
        m.group(0) for m in re.finditer(r"\b[a-z]{2,}\b", _PLACEHOLDERS.sub(" ", target))
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
    return bool(re.fullmatch(r"[&§][0-9A-FK-ORa-fk-or]|\\&", token))


_QUEST_TITLE_KEY_RE = re.compile(
    r"^(?:chapter|chapter_group|quest|task|reward|reward_table|loot_crate|file)\."
    r"[0-9A-Fa-f]+\.(?:title|subtitle|quest_subtitle)(?:\[\d+\])?$"
)
_PROPER_NOUN_CONNECTOR_WORDS = {"a", "an", "and", "de", "of", "the"}


def _is_quest_title_key(key: str | None) -> bool:
    return bool(key and _QUEST_TITLE_KEY_RE.fullmatch(key))


def _looks_like_proper_noun_phrase(text: str) -> bool:
    plain = _PLACEHOLDERS.sub(" ", text)
    words = re.findall(r"[A-Za-z][\w'.-]*", plain)
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
    if "painting." in key and key.endswith(".author"):
        return True
    if "music_disc" in key and key.endswith((".desc", ".description")):
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
    if key.endswith((".docs", ".discord", ".github", ".modrinth", ".wiki")):
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
    return False


def _is_url_or_domain(text: str) -> bool:
    if re.fullmatch(r"[a-z][a-z0-9+.-]*://\S+", text, re.IGNORECASE):
        return True
    return bool(re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?", text, re.IGNORECASE))


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
    if not left.strip() or not right.strip():
        return False
    return bool(re.fullmatch(r"[A-Z][A-Za-z0-9' ._-]+", left.strip()))


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
    if re.search(r"\b(?:%s)\b" % "|".join(_CODE_WORDS), plain, re.IGNORECASE) and re.search(r"[=();{}]", plain):
        return True
    return False


def _requires_visible_translation(source: str) -> bool:
    text = _PLACEHOLDERS.sub(" ", source)
    text = re.sub(r"[a-z][a-z0-9+.-]*://\S+", " ", text, flags=re.IGNORECASE)
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


def diff_keys(en_dict: dict[str, str], zh_dict: dict[str, str]) -> set[str]:
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
        and not is_usable_translation(en_dict[k], zh_dict[k], key=k)
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
