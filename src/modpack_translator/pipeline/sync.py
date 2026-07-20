"""伺服器同步：把「伺服器端才需要」的已翻檔從客戶端複製到伺服器實例。

背景：翻譯器對整個客戶端實例翻譯。物品名/GUI 等走 translate key 由客戶端
語言檔解析（伺服器不需要）；但任務資料、資料包字面文字是伺服器載入後同步
給客戶端顯示——連專用伺服器時必須讓伺服器那份也翻好才生效。本模組依輸出
格式挑出伺服器端檔，單向複製到伺服器實例（只增不減、覆蓋前備份）。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

# 伺服器端格式（唯一真相）。vh_config_json 經反編譯確認為客戶端載入，不列入。
SERVER_SIDE_FORMATS: frozenset[str] = frozenset({
    "ftbq_snbt",
    "ftbq_inline_snbt",
    "heracles_snbt",
    "heracles_inline_snbt",
    "bq_lang",
    "datapack_json",
})


def is_server_side(fmt: str) -> bool:
    return fmt in SERVER_SIDE_FORMATS


@dataclass(frozen=True)
class ManifestEntry:
    rel_path: str          # 相對客戶端遊戲根，一律用正斜線
    format: str


def manifest_path(game_root: Path) -> Path:
    return game_root / ".modpack_translator" / "sync_manifest.json"


def load_manifest(game_root: Path) -> list[ManifestEntry]:
    path = manifest_path(game_root)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    entries: list[ManifestEntry] = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("rel_path"), str) \
                and isinstance(item.get("format"), str):
            entries.append(ManifestEntry(item["rel_path"], item["format"]))
    return entries


def merge_manifest(game_root: Path, entries: list[ManifestEntry]) -> None:
    """以 rel_path 為鍵聯集合併後寫回（跨多次翻譯保持完整）。"""
    merged: dict[str, ManifestEntry] = {e.rel_path: e for e in load_manifest(game_root)}
    for e in entries:
        merged[e.rel_path] = e
    path = manifest_path(game_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(e) for e in merged.values()]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_manifest_from_targets(targets, game_root: Path) -> list[ManifestEntry]:
    """從掃描目標挑出伺服器端格式，產生 manifest 條目（供既有已翻實例
    首次同步時即時重建；輸出檔須落在 game_root 底下才收）。"""
    entries: list[ManifestEntry] = []
    for t in targets:
        if not is_server_side(t.format):
            continue
        out = getattr(t, "target_file", None)
        if out is None:
            continue
        try:
            rel = Path(out).resolve().relative_to(Path(game_root).resolve())
        except ValueError:
            continue
        entries.append(ManifestEntry(rel.as_posix(), t.format))
    return entries
