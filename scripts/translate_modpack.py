#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from modpack_translator.config import load_config
from modpack_translator.gui.stats import build_summary_lines
from modpack_translator.pipeline.batch_prefill import prefill_translation_cache
from modpack_translator.pipeline.glossary import (
    default_custom_glossary_path,
    load_merged_glossary,
    modnames_glossary_path,
)
from modpack_translator.pipeline.patcher import (
    backup_mods,
    backup_quest_configs,
    patch_modonomicon_unicode_fonts,
)
from modpack_translator.pipeline.pack_context import load_pack_context
from modpack_translator.pipeline.preprocessor import diff_keys
from modpack_translator.pipeline.runner import (
    _write_failed_items,
    failed_target_name,
    iter_all_source_strings,
    normalize_cache_with_glossary,
    process_target,
    read_existing_target,
    read_target_strings,
    source_sidecar_path,
    sync_source_sidecar,
)
from modpack_translator.pipeline.scanner import ModpackScanner, TranslationTarget, resolve_game_root
from modpack_translator.pipeline._chat import TranslatorFatalError
from modpack_translator.pipeline.translator import build_translator
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
    p.add_argument(
        "--batch-size",
        type=int,
        default=None,
        metavar="N",
        help="遠端批次預翻譯：每個請求的字串數（預設取 model.yaml 的 remote_batch_size）",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=None,
        metavar="M",
        help="遠端批次預翻譯：併發請求數（預設取 model.yaml 的 remote_concurrency）",
    )
    p.add_argument(
        "--request-timeout",
        type=float,
        default=None,
        metavar="SEC",
        help="遠端每請求逾時秒數（預設取 model.yaml 的 remote_timeout_s）",
    )
    p.add_argument(
        "--no-prefill",
        action="store_true",
        help="停用遠端批次預翻譯（回到逐條序列翻譯）",
    )
    p.add_argument(
        "--glossary",
        default=None,
        metavar="PATH",
        help="官方用語庫對照表路徑（預設取 language yaml 的 glossary_path；可用來切換 MC 版本）",
    )
    p.add_argument(
        "--no-glossary",
        action="store_true",
        help="停用 Minecraft 官方用語庫提示（預設啟用）",
    )
    return p.parse_args()


def _load_cache(cache_path: Path) -> dict[str, str]:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return {}


def _flush_cache(cache_path: Path, cache: dict[str, str]) -> None:
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _filter_pending_targets(all_targets: list[TranslationTarget], lang_code: str,
                            glossary=None) -> list[TranslationTarget]:
    pending: list[TranslationTarget] = []
    for target in all_targets:
        try:
            strings = read_target_strings(target)
            existing = read_existing_target(target, lang_code)
        except Exception:
            pending.append(target)
            continue
        if diff_keys(strings, existing, glossary=glossary):
            pending.append(target)
    return pending


def _dry_run_report(all_targets: list[TranslationTarget], lang_code: str, glossary=None) -> None:
    SAMPLES_PER_FMT = 3
    counts: dict[str, int] = {}
    samples: dict[str, list[tuple[str, str, str]]] = {}

    for target in tqdm(all_targets, desc="計算字串數", unit="file"):
        try:
            strings = read_target_strings(target)
            existing = read_existing_target(target, lang_code)
        except Exception:
            continue
        pending_keys = diff_keys(strings, existing, glossary=glossary)
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
    if args.batch_size is not None:
        cfg.model.remote_batch_size = args.batch_size
    if args.concurrency is not None:
        cfg.model.remote_concurrency = args.concurrency
    if args.request_timeout is not None:
        cfg.model.remote_timeout_s = args.request_timeout
    if args.no_prefill:
        cfg.model.remote_prefill = False
    cfg.paths.create_output_dirs()

    cache_path = cfg.paths.translation_cache

    # 用語庫須在掃描/過濾之前載入：過濾用 diff_keys 判定，缺 glossary 時
    # 只含「命中詞英文標題」的檔案會被判為已翻而過濾掉、永不修復。
    glossary = None
    if not args.no_glossary:
        official_path = args.glossary or cfg.language.glossary_path
        modnames_path = cfg.language.modnames_glossary_path
        if modnames_path is None:
            mp = modnames_glossary_path(cfg.language.code)
            modnames_path = str(mp) if mp.exists() else None
        custom_path = cfg.language.custom_glossary_path or str(default_custom_glossary_path())
        glossary = load_merged_glossary(official_path, modnames_path, custom_path)
        if glossary is not None:
            print(f"已載入用語庫：{len(glossary.terms):,} 條（官方＋模組名＋自訂）")
        else:
            print("[警告] 無法載入任何用語庫，本次不使用用語庫。")

    print(f"掃描模組包：{modpack_path}")
    scanner = ModpackScanner()
    all_targets = scanner.scan(modpack_path, cfg.language.code, glossary)
    all_targets = _filter_pending_targets(all_targets, cfg.language.code, glossary)

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
        _dry_run_report(all_targets, cfg.language.code, glossary)
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

    # 每包語境：extra_prompt 併入 system prompt 靜態段（[Glossary] 動態區塊恆在其後）
    pack_context = load_pack_context(game_root)
    system_prompt = cfg.language.system_prompt
    if pack_context.extra_prompt.strip():
        system_prompt = system_prompt + "\n\n[Pack context]\n" + pack_context.extra_prompt.strip()
        print("已載入此包的翻譯語境提示詞。")
    if pack_context.learned_count():
        print(f"已載入此包 {pack_context.learned_count()} 條學習譯法。")

    if cfg.model.backend_mode == "remote":
        print("\n正在連線遠端 API…")
    else:
        print("\n正在連線或啟動本機模型服務…")
    try:
        translator = build_translator(cfg.model, system_prompt, glossary, pack_context)
    except TranslatorFatalError as exc:
        print(f"模型服務啟動失敗：{exc}")
        raise SystemExit(1)

    try:
        cache = _load_cache(cache_path)
        fixed = normalize_cache_with_glossary(cache, glossary)
        if fixed:
            print(f"已依用語庫正規化 {fixed:,} 條既有快取（零 API 成本）。")
        total_translated = total_cached = total_fallback = 0
        cache_dirty = 0
        failed_by_target: dict[str, dict[str, str]] = {}

        # 遠端模式：先把所有待翻字串批次併發翻進快取，逐檔階段幾乎全快取命中；
        # 預翻譯成功數是本輪真實 API 消耗的主體，須併入結尾統計
        prefill_translated = 0
        if cfg.model.backend_mode == "remote" and cfg.model.remote_prefill:
            bar = tqdm(total=0, desc="批次預翻譯", unit="str")

            def _on_prefill_progress(done: int, total: int) -> None:
                if bar.total != total:
                    bar.total = total
                bar.n = done
                bar.refresh()

            prefill_stats = prefill_translation_cache(
                all_targets,
                cfg.model,
                system_prompt,
                cfg.language.code,
                cache,
                retry_count=args.retry,
                on_progress=_on_prefill_progress,
                on_log=tqdm.write,
                flush_cache=lambda: _flush_cache(cache_path, cache),
                glossary=glossary,
                pack_context=pack_context,
            )
            bar.close()
            prefill_translated = prefill_stats.translated
            _flush_cache(cache_path, cache)

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
            except TranslatorFatalError:
                raise
            except Exception as exc:
                tqdm.write(f"[警告] 略過 {target.mod_id}/{target.format}：{exc}")
                continue

            cache_dirty += 1
            if cache_dirty >= 100:
                _flush_cache(cache_path, cache)
                cache_dirty = 0

        _flush_cache(cache_path, cache)

        # 建立/更新 hash→英文 對照表（translation_sources.json，與快取同 key）
        try:
            resolved = sync_source_sidecar(
                cache,
                iter_all_source_strings(modpack_path, cfg.language.code),
                source_sidecar_path(cache_path),
            )
            print(f"已更新來源對照表 translation_sources.json（可反查 {resolved:,}/{len(cache):,}）。")
        except Exception as exc:  # noqa: BLE001 — 對照表失敗不可影響翻譯結果
            print(f"[警告] 來源對照表更新失敗（不影響翻譯）：{exc}")

        # 寫出失敗項目
        failed_dir = _PROJECT_ROOT / "Failed Items"
        failed_files = _write_failed_items(failed_by_target, failed_dir)
        if failed_files > 0:
            print(f"⚠ {failed_files} 個模組/任務書含翻譯失敗項目，詳見 Failed Items/ 資料夾。")

        print("\n" + "\n".join(build_summary_lines(
            False, prefill_translated, total_translated, total_cached, total_fallback,
        )))
    finally:
        try:
            pack_context.save()
        except OSError as exc:
            print(f"[警告] 包語境存檔失敗：{exc}")
        translator.close()


if __name__ == "__main__":
    main()
