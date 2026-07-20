"""驗證 worker 蒐集成功的伺服器端目標並寫入 manifest 的邏輯（抽出成純函式測）。"""
from pathlib import Path

from modpack_translator.pipeline import sync
from modpack_translator.pipeline.scanner import TranslationTarget


def _t(fmt, target_file):
    return TranslationTarget(
        source_file=target_file, path_in_jar=None, mod_id="x",
        format=fmt, output_mode="in_place", target_file=target_file,
    )


def test_only_server_side_successful_targets_written(tmp_path):
    root = tmp_path
    a = root / "config" / "ftbquests" / "quests" / "a.snbt"
    b = root / "kubejs" / "assets" / "ns" / "lang" / "zh_tw.json"  # 客戶端
    a.parent.mkdir(parents=True, exist_ok=True); a.write_text("x", encoding="utf-8")
    b.parent.mkdir(parents=True, exist_ok=True); b.write_text("x", encoding="utf-8")

    # 模擬 worker：成功目標清單（a 伺服器端、b 客戶端）
    successful = [_t("ftbq_inline_snbt", a), _t("kubejs_json", b)]
    entries = sync.build_manifest_from_targets(successful, root)
    sync.merge_manifest(root, entries)

    got = {e.rel_path for e in sync.load_manifest(root)}
    assert got == {"config/ftbquests/quests/a.snbt"}
