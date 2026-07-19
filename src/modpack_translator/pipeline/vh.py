"""Vault Hunters（the_vault）config 在地化。

VH 的自訂 GUI 文字（技能樹/能力/任務/物品 tooltip/貪婪試煉/玩家統計面板）
不走 lang 檔，而是存於 config/the_vault/*.json；模組依遊戲語言載入
config/the_vault/lang/<locale>/<同相對路徑> 的覆蓋檔（VH 3.x 官方出貨
zh_cn/de_de/es_es/fr_fr/pt_br/ru_ru/sv_se 即此機制，唯獨沒有 zh_tw——
遊戲語言設 zh_tw 時整批 GUI 文字 fallback 英文）。

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
    "tooltip.json": FileSpec(text_fields=frozenset({"value"})),
    "gear/modifier_tooltips.json": FileSpec(all_values=True),
    "greed/trials_screen.json": FileSpec(
        text_fields=frozenset({"text"}),
        list_fields=frozenset({"trialWarningText"}),
    ),
    "menu_player_stat_description.json": FileSpec(all_values=True),
}


def spec_for_source(path: Path) -> tuple[str, FileSpec] | None:
    """從來源/既有檔路徑反查它是哪個可在地化檔（比對路徑尾段，不分大小寫）。"""
    parts = [p.lower() for p in path.parts]
    for rel, spec in LOCALIZABLE_FILES.items():
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
    return extract_text(read_config_json(path), LOCALIZABLE_FILES[rel])


def read_source_text(path: Path) -> dict[str, str]:
    """runner.read_target_strings 用：由來源路徑自動配對 spec。"""
    found = spec_for_source(path)
    if found is None:
        return {}
    return extract_text(read_config_json(path), found[1])


# JSON path 寫回與 Patchouli 共用同一工具
apply_text = write_patchouli_text


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
