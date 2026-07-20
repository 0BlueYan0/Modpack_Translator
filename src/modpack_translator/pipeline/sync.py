"""伺服器同步：把「伺服器端才需要」的已翻檔從客戶端複製到伺服器實例。

背景：翻譯器對整個客戶端實例翻譯。物品名/GUI 等走 translate key 由客戶端
語言檔解析（伺服器不需要）；但任務資料、資料包字面文字是伺服器載入後同步
給客戶端顯示——連專用伺服器時必須讓伺服器那份也翻好才生效。本模組依輸出
格式挑出伺服器端檔，單向複製到伺服器實例（只增不減、覆蓋前備份）。
"""
from __future__ import annotations

import filecmp
import json
import shutil
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
    首次同步時即時重建；輸出檔須落在 game_root 底下才收）。

    伺服器端格式一律 output_mode="in_place"，輸出檔為 target_file；但就地
    改寫來源的 inline 格式（ftbq_inline_snbt / heracles_inline_snbt）
    target_file 為 None、輸出即 source_file——這類要用 source_file，否則
    FTB Quests 章節等就地翻譯的內容會被漏掉不同步。"""
    entries: list[ManifestEntry] = []
    for t in targets:
        if not is_server_side(t.format):
            continue
        out = t.target_file or t.source_file
        if out is None:
            continue
        try:
            rel = Path(out).resolve().relative_to(Path(game_root).resolve())
        except ValueError:
            continue
        entries.append(ManifestEntry(rel.as_posix(), t.format))
    return entries


@dataclass(frozen=True)
class SyncItem:
    rel_path: str
    action: str        # "copy" | "overwrite" | "skip"


@dataclass(frozen=True)
class SyncPlan:
    items: list[SyncItem]

    @property
    def copies(self) -> list[SyncItem]:
        return [i for i in self.items if i.action == "copy"]

    @property
    def overwrites(self) -> list[SyncItem]:
        return [i for i in self.items if i.action == "overwrite"]

    @property
    def skips(self) -> list[SyncItem]:
        return [i for i in self.items if i.action == "skip"]


def plan_sync(client_root: Path, server_root: Path, manifest: list[ManifestEntry]) -> SyncPlan:
    """逐 manifest 條目規劃複製動作：客戶端來源已刪 → 跳過(自我修復)；
    伺服器端無檔 → copy；有但位元組不同 → overwrite；相同 → skip。
    伺服器端 manifest 未涵蓋的檔一律不出現在 plan（絕不刪）。"""
    items: list[SyncItem] = []
    seen: set[str] = set()
    for entry in manifest:
        rel = entry.rel_path
        if rel in seen:
            continue
        seen.add(rel)
        src = client_root / rel
        if not src.is_file():
            continue  # 來源已不存在：略過（自我修復舊條目）
        dst = server_root / rel
        if not dst.exists():
            items.append(SyncItem(rel, "copy"))
        elif filecmp.cmp(str(src), str(dst), shallow=False):
            items.append(SyncItem(rel, "skip"))
        else:
            items.append(SyncItem(rel, "overwrite"))
    return SyncPlan(items)


@dataclass
class SyncResult:
    copied: list[str]
    overwritten: list[str]
    skipped: list[str]
    failed: list[tuple[str, str]]      # (rel_path, 錯誤訊息)
    backup_dir: Path | None


def apply_sync(plan: SyncPlan, client_root: Path, server_root: Path, backup_dir: Path,
               on_progress=None) -> SyncResult:
    """依 plan 執行實際複製：copy 補檔、overwrite 先備份原檔再覆蓋、skip 略過。
    backup_dir 由呼叫端傳入(含時間戳),只有真的發生 overwrite 才建立並使用；
    絕不刪除伺服器端既有檔（manifest 未涵蓋或 skip 的一律原樣保留）。"""
    copied: list[str] = []
    overwritten: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []
    backup_used = False

    actionable = [i for i in plan.items if i.action in ("copy", "overwrite")]
    total = len(actionable)
    done = 0
    for item in plan.items:
        if item.action == "skip":
            skipped.append(item.rel_path)
            continue
        src = client_root / item.rel_path
        dst = server_root / item.rel_path
        try:
            if item.action == "overwrite":
                bak = backup_dir / item.rel_path
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(dst), str(bak))
                backup_used = True
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            (overwritten if item.action == "overwrite" else copied).append(item.rel_path)
        except OSError as exc:
            failed.append((item.rel_path, str(exc)))
        done += 1
        if on_progress is not None:
            on_progress(done, total)
    return SyncResult(copied, overwritten, skipped, failed,
                      backup_dir if backup_used else None)
