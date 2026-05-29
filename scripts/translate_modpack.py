#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from modpack_translator.config import load_config
from modpack_translator.pipeline.patcher import (
    backup_mods,
    backup_quest_configs,
    patch_modonomicon_unicode_fonts,
)
from modpack_translator.pipeline.preprocessor import diff_keys
from modpack_translator.pipeline.runner import (
    _write_failed_items,
    failed_target_name,
    process_target,
    read_existing_target,
    read_target_strings,
)
from modpack_translator.pipeline.scanner import ModpackScanner, TranslationTarget, resolve_game_root
from modpack_translator.pipeline.translator import GGUFTranslator
from tqdm import tqdm

_PROJECT_ROOT = Path(__file__).parent.parent


def parse_args():
    p = argparse.ArgumentParser(description="翻譯 Minecraft 模組包 en_us → zh_tw")
    p.add_argument("--modpack",      required=True,  help="模組包資料夾路徑")
    p.add_argument("--mc-version",   default=None,   help="已棄用；jar 注入不需要 pack_format")
    p.add_argument("--language",     default="configs/languages/zh_tw.yaml")
    p.add_argument("--model-config", default="configs/model.yaml")
    p.add_argument("--paths-config", default="configs/paths.yaml")
    p.add_argument("--output",       default=None,   help="已棄用；翻譯結果直接寫回 jar")
    p.add_argument("--dry-run",      action="store_true", help="僅掃描，不執行翻譯")
    p.add_argument("--skip-mods",    action="store_true", help="略過 jar 掃描")
    p.add_argument("--skip-quests",  action="store_true", help="略過任務模組掃描")
    p.add_argument("--max-steps",    type=int, default=-1, help="限制翻譯前 N 個檔案（煙霧測試）")
    p.add_argument(
        "--retry",
        type=int,
        default=0,
        metavar="N",
        help="後處理驗證失敗時每個字串的重試次數（預設：0）",
    )
    return p.parse_args()


def _load_cache(cache_path: Path) -> dict[str, str]:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return {}


def _flush_cache(cache_path: Path, cache: dict[str, str]) -> None:
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _filter_pending_targets(all_targets: list[TranslationTarget], lang_code: str) -> list[TranslationTarget]:
    pending: list[TranslationTarget] = []
    for target in all_targets:
        try:
            strings = read_target_strings(target)
            existing = read_existing_target(target, lang_code)
        except Exception:
            pending.append(target)
            continue
        if diff_keys(strings, existing):
            pending.append(target)
    return pending


def _dry_run_report(all_targets: list[TranslationTarget], lang_code: str) -> None:
    SAMPLES_PER_FMT = 3
    counts: dict[str, int] = {}
    samples: dict[str, list[tuple[str, str, str]]] = {}

    for target in tqdm(all_targets, desc="計算字串數", unit="file"):
        try:
            strings = read_target_strings(target)
            existing = read_existing_target(target, lang_code)
        except Exception:
            continue
        pending_keys = diff_keys(strings, existing)
        pending = {k: strings[k] for k in pending_keys}

        fmt = target.format
        counts[fmt] = counts.get(fmt, 0) + len(pending)

        if fmt not in samples:
            samples[fmt] = []
        if len(samples[fmt]) < SAMPLES_PER_FMT and pending:
            key, val = random.choice(list(pending.items()))
            if val.strip():
                samples[fmt].append((target.mod_id, key, val))

    total = sum(counts.values())
    print(f"\n待翻譯鍵值對：{total:,} 組")
    for fmt, count in sorted(counts.items()):
        print(f"  {fmt:22s}: {count:,}")

    print(f"\n樣本字串（每種格式 {SAMPLES_PER_FMT} 條）：")
    for fmt, fmt_samples in samples.items():
        print(f"  [{fmt}]")
        for mod_id, key, val in fmt_samples:
            display_val = val[:90] + "..." if len(val) > 90 else val
            print(f"    ({mod_id}) {key}")
            print(f'      "{display_val}"')

    print("\n[dry-run] 未執行翻譯。")


def main():
    args = parse_args()

    modpack_path = Path(args.modpack).resolve()

    cfg = load_config(args.model_config, args.paths_config, args.language)
    cfg.paths.create_output_dirs()

    cache_path = cfg.paths.translation_cache

    print(f"掃描模組包：{modpack_path}")
    scanner = ModpackScanner()
    all_targets = scanner.scan(modpack_path, cfg.language.code)
    all_targets = _filter_pending_targets(all_targets, cfg.language.code)

    if args.skip_mods:
        all_targets = [t for t in all_targets if t.output_mode != "jar_inject"]
    if args.skip_quests:
        all_targets = [t for t in all_targets if t.output_mode != "in_place"]
    if args.max_steps > 0:
        all_targets = all_targets[:args.max_steps]

    print(f"找到 {len(all_targets)} 個翻譯目標")
    for fmt in (
        "json_lang", "legacy_lang", "patchouli_json",
        "ftbq_snbt", "ftbq_inline_snbt",
        "heracles_snbt", "heracles_inline_snbt",
        "bq_lang", "kubejs_json",
    ):
        count = sum(1 for t in all_targets if t.format == fmt)
        if count:
            print(f"  {fmt}: {count}")

    if args.dry_run:
        _dry_run_report(all_targets, cfg.language.code)
        return

    game_root = resolve_game_root(modpack_path)
    if any(t.output_mode == "jar_inject" for t in all_targets) or not args.skip_mods:
        backed_up = backup_mods(game_root)
        print(f"已備份 {backed_up} 個原始模組 jar 至 mods_bak/")
    if not args.skip_mods:
        patched_fonts = patch_modonomicon_unicode_fonts(game_root)
        if patched_fonts:
            print(f"已修補 {patched_fonts} 個 Modonomicon Unicode 字型 fallback")
    if any(t.output_mode == "in_place" for t in all_targets):
        backed_up = backup_quest_configs(game_root)
        print(f"已備份 {backed_up} 個任務/設定資料夾至 quests_bak/")

    print("\n正在連線或啟動本機模型服務…")
    translator = GGUFTranslator(cfg.model, cfg.language.system_prompt)

    try:
        cache = _load_cache(cache_path)
        total_translated = total_cached = total_fallback = 0
        cache_dirty = 0
        failed_by_target: dict[str, dict[str, str]] = {}

        for target in tqdm(all_targets, desc="翻譯中", unit="file"):
            try:
                n_t, n_c, n_f, failed = process_target(
                    target, translator, cache, cfg.language.code, args.retry,
                )
                total_translated += n_t
                total_cached += n_c
                total_fallback += n_f
                if failed:
                    failed_by_target[failed_target_name(target)] = failed
            except Exception as exc:
                tqdm.write(f"[警告] 略過 {target.mod_id}/{target.format}：{exc}")
                continue

            cache_dirty += 1
            if cache_dirty >= 100:
                _flush_cache(cache_path, cache)
                cache_dirty = 0

        _flush_cache(cache_path, cache)

        # 寫出失敗項目
        failed_dir = _PROJECT_ROOT / "Failed Items"
        failed_files = _write_failed_items(failed_by_target, failed_dir)
        if failed_files > 0:
            print(f"⚠ {failed_files} 個模組/任務書含翻譯失敗項目，詳見 Failed Items/ 資料夾。")

        print(f"\n完成 — 已翻譯={total_translated:,}  快取={total_cached:,}  回退={total_fallback:,}")
    finally:
        translator.close()


if __name__ == "__main__":
    main()
