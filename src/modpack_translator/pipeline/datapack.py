"""資料包字面 JSON 在地化（PassiveSkillTree 技能節點、Origins 起源）。

有一類玩家可見文字既不在 lang 檔、也沒有 locale 覆蓋機制,而是把顯示字
「字面寫死」在資料包 JSON 裡,由 mod 直接以 Component.literal 渲染:

- PassiveSkillTree 技能樹(config/paxi/datapacks/*/data/skilltree/skills/*.json
  或 kubejs/data/skilltree/skills/*.json):節點 "title"（Minor/Notable/
  Keystone…分級與職業名）、"description"（職業根節點的完整敘述句,富文本
  元件陣列,可譯目標是各段 "text"）。SkillsReloader 直接讀資料包、無 per-
  locale 目錄,只能就地改寫。
- Origins 起源(data/<ns>/origins/*.json、data/<ns>/origin_layers/*.json):
  起源 "name"/"description" 若在 JSON 內填字面字串,mod 以字面顯示、並
  「繞過」lang 檔的 origin.<ns>.<id>.name 鍵（該鍵僅在 JSON 未指定 name 時
  作為後備）——實例中這些鍵其實已有良好譯文卻成孤兒。就地把字面值翻成
  中文即生效。translate 元件（{"translate": "key"}）走 lang 機制,不動。

輸出一律就地改寫來源檔（無 locale 變體可寫）;富文本元件只換 "text",
id/color/style 等結構欄位原樣保留。再次掃描時值已含 CJK → 自動跳過,冪等。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatapackSpec:
    # 就地翻譯的頂層欄位；值可為 str、富文本元件 dict（取 "text"）、
    # 或元件/字串構成的 list。translate 元件與非字串一律略過。
    fields: frozenset[str] = field(default_factory=frozenset)


SKILLTREE_SKILL = DatapackSpec(fields=frozenset({"title", "description"}))
ORIGIN = DatapackSpec(fields=frozenset({"name", "description"}))


def spec_for_path(rel_parts: tuple[str, ...]) -> DatapackSpec | None:
    """依資料包內相對路徑（data/ 之後）判定 schema。
    rel_parts 形如 ("data", "skilltree", "skills", "mage.json")。"""
    lowered = [p.lower() for p in rel_parts]
    if len(lowered) < 3 or lowered[0] != "data":
        return None
    if not lowered[-1].endswith(".json"):
        return None
    # data/skilltree/skills/**.json
    if "skilltree" in lowered and "skills" in lowered:
        return SKILLTREE_SKILL
    # data/<ns>/origins/**.json、data/<ns>/origin_layers/**.json
    if "origins" in lowered[2:] or "origin_layers" in lowered[2:]:
        return ORIGIN
    return None


def _iter_component_text_paths(value: Any, prefix: str):
    """遞迴走富文本欄位值,yield (path_key, text)。只吃字面 "text"/純字串,
    {"translate": …} 與非字串一律不 yield（不譯）。"""
    if isinstance(value, str):
        yield prefix, value
    elif isinstance(value, dict):
        if "translate" in value:
            return  # 已走 lang 機制
        text = value.get("text")
        if isinstance(text, str):
            yield f"{prefix}.text", text
        # 巢狀 extra 元件陣列（原版文字元件慣例）
        extra = value.get("extra")
        if isinstance(extra, list):
            for i, item in enumerate(extra):
                yield from _iter_component_text_paths(item, f"{prefix}.extra.{i}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _iter_component_text_paths(item, f"{prefix}.{i}")


def extract_text(data: dict[str, Any], spec: DatapackSpec) -> dict[str, str]:
    """抽出可翻譯的頂層欄位文字,鍵為穩定 JSON path。"""
    result: dict[str, str] = {}
    for field_name in spec.fields:
        if field_name not in data:
            continue
        for path_key, text in _iter_component_text_paths(data[field_name], field_name):
            if _is_translatable(text):
                result[path_key] = text
    return result


def _is_translatable(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    return True


def pending_english_keys(source: dict[str, str], glossary=None) -> dict[str, str]:
    """就地覆蓋格式:來源即目標,無獨立英文基準。以「值仍是英文字面且
    依可譯性規則需翻」為待翻集——已含 CJK 的值視為完成,跳過。"""
    from modpack_translator.pipeline.preprocessor import _is_translatable_entry

    pending: dict[str, str] = {}
    for path_key, value in source.items():
        if has_cjk(value):
            continue
        field_name = path_key.split(".", 1)[0]
        if _is_translatable_entry(field_name, value):
            pending[path_key] = value
    return pending


def apply_text(data: dict[str, Any], translations: dict[str, str]) -> dict[str, Any]:
    """回填譯文,結構其餘部分原樣保留。就地修改並回傳同一 dict。"""
    for path_key, translated in translations.items():
        _set_by_path(data, path_key.split("."), translated)
    return data


def _set_by_path(node: Any, parts: list[str], translated: str) -> None:
    key = parts[0]
    rest = parts[1:]
    if isinstance(node, dict):
        if key not in node:
            return
        if not rest:
            # 目標本身若是字串就直接換；若是含 "text" 的元件在下一層處理
            if isinstance(node[key], str):
                node[key] = translated
            return
        _descend(node[key], rest, translated)
    # 其餘型別不會是頂層欄位起點


def _descend(node: Any, parts: list[str], translated: str) -> None:
    key = parts[0]
    rest = parts[1:]
    if key == "text" and not rest and isinstance(node, dict):
        node["text"] = translated
        return
    if key == "extra" and isinstance(node, dict):
        _descend(node.get("extra"), rest, translated)
        return
    if key.isdigit() and isinstance(node, list):
        idx = int(key)
        if 0 <= idx < len(node):
            if not rest:
                if isinstance(node[idx], str):
                    node[idx] = translated
            else:
                _descend(node[idx], rest, translated)
        return
    if isinstance(node, dict) and key in node:
        if not rest and isinstance(node[key], str):
            node[key] = translated
        elif rest:
            _descend(node[key], rest, translated)


def read_source_text(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        return {}
    spec = _spec_for_source(path)
    if spec is None:
        return {}
    return extract_text(data, spec)


def _spec_for_source(path: Path) -> DatapackSpec | None:
    parts = path.parts
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].lower() == "data":
            return spec_for_path(tuple(parts[i:]))
    return None


def has_cjk(text: str) -> bool:
    return any("㐀" <= ch <= "鿿" for ch in text)
