from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any


# Single-pass regex: matches structural tokens that must be preserved via {N} encoding.
#
# Minecraft color/format codes (&b, &r, §c …) are intentionally NOT encoded here.
# They are kept inline so the model sees them directly and preserves them in position.
# Abstract {N} encoding for color codes causes two failure modes:
#   1. Identical codes (e.g. &r appearing twice) map to different indices ({1} and {3}),
#      which confuses the model into dropping or merging them.
#   2. Numbered placeholders lose the semantic context that makes preservation intuitive.
# Gemma-4-E4B reliably preserves short 2-char markup codes when instructed to do so
# via the system prompt. See configs/languages/zh_tw.yaml rule #3.
_PLACEHOLDERS = re.compile(
    r'\$\([^)]+\)'                          # Patchouli: $(thing)
    r'|\\n'                                 # escaped newline literal
    r'|\\&'                                 # escaped ampersand
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


def diff_keys(en_dict: dict[str, str], zh_dict: dict[str, str]) -> set[str]:
    """Return keys that are missing from zh or still identical to en."""
    missing = set(en_dict) - set(zh_dict)
    untranslated = {
        k
        for k in en_dict
        if k in zh_dict
        and (
            not _normalized_translation_value(zh_dict[k])
            or _normalized_translation_value(zh_dict[k]) == _normalized_translation_value(en_dict[k])
        )
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
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except json.JSONDecodeError:
        pass

    result: dict[str, str] = {}
    for m in re.finditer(
        r'^\s*(?:"((?:[^"\\]|\\.)*)"|([\w.\-/]+))\s*:\s*"((?:[^"\\]|\\.)*)"',
        raw,
        re.MULTILINE,
    ):
        key = _json_unescape(m.group(1) if m.group(1) is not None else m.group(2))
        value = _json_unescape(m.group(3))
        result[key] = value
    return result


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
