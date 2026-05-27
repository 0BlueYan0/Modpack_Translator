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
    r'\$\([^)]+\)'                          # Patchouli: $(thing)
    r'|\\n'                                 # escaped newline literal
    r'|\\&'                                 # escaped ampersand
    r'|[&§][0-9A-FK-ORa-fk-or]'             # Minecraft color/format codes
    r'|%\d+\$[sdifcbxo%]'                  # positional: %1$s %2$d
    r'|%[sdifcbxo%]'                        # simple: %s %d %f
    r'|\{[^{}]+\}'                          # existing curly-brace placeholders
)

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
    return bool(re.search(r"[A-Za-z]", _PLACEHOLDERS.sub("", value)))


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


def is_usable_translation(source: str, target: str) -> bool:
    if not _has_translatable_text(source):
        return True

    src = _normalized_translation_value(source)
    dst = _normalized_translation_value(target)
    if not dst or dst == src:
        return False
    return not _looks_undertranslated(source, target)


def _looks_undertranslated(source: str, target: str) -> bool:
    if not re.search(r"[\u3400-\u9fff]", target):
        return False

    src_words = _english_words(_PLACEHOLDERS.sub(" ", source))
    target_words = _english_words(_PLACEHOLDERS.sub(" ", target))
    leaked = src_words & target_words & _GENERIC_UNTRANSLATED_WORDS
    return bool(leaked)


def _english_words(value: str) -> set[str]:
    return {m.group(0).lower() for m in re.finditer(r"[A-Za-z]{2,}", value)}


def diff_keys(en_dict: dict[str, str], zh_dict: dict[str, str]) -> set[str]:
    """Return keys that are missing from zh or still identical to en."""
    translatable_keys = {
        k for k, value in en_dict.items()
        if _has_translatable_text(value)
    }
    missing = translatable_keys - set(zh_dict)
    untranslated = {
        k
        for k in translatable_keys
        if k in zh_dict
        and not is_usable_translation(en_dict[k], zh_dict[k])
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
    array_spans: list[tuple[int, int]] = []
    key_pattern = r'(?:\"((?:[^\"\\]|\\.)*)\"|([\w.\-/]+))'
    for m in re.finditer(
        rf'^\s*{key_pattern}\s*:\s*\[(.*?)^\s*\]',
        raw,
        re.MULTILINE | re.DOTALL,
    ):
        key = _json_unescape(m.group(1) if m.group(1) is not None else m.group(2))
        body = m.group(3)
        array_spans.append((m.start(), m.end()))
        for idx, item in enumerate(_STRING_LITERAL_RE.finditer(body)):
            result[f"{key}[{idx}]"] = _json_unescape(item.group("value"))
    for m in re.finditer(
        r'^\s*(?:"((?:[^"\\]|\\.)*)"|([\w.\-/]+))\s*:\s*"((?:[^"\\]|\\.)*)"',
        raw,
        re.MULTILINE,
    ):
        if any(start <= m.start() < end for start, end in array_spans):
            continue
        key = _json_unescape(m.group(1) if m.group(1) is not None else m.group(2))
        value = _json_unescape(m.group(3))
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
