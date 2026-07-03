from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from modpack_translator.config import AppConfig
from modpack_translator.pipeline.batch_prefill import prefill_translation_cache
from modpack_translator.pipeline.glossary import load_glossary
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
from modpack_translator.pipeline._chat import TranslatorFatalError
from modpack_translator.pipeline.translator import build_translator

# src/modpack_translator/gui/ → 上 4 層到專案根目錄
_PROJECT_ROOT = Path(__file__).parents[3]


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


class ScanWorker(QThread):
    log      = Signal(str)
    finished = Signal(list, dict, int, dict)  # targets, format_counts, total_pairs, samples
    error    = Signal(str)

    def __init__(self, modpack_path: Path, skip_mods: bool, skip_quests: bool, lang_code: str = "zh_tw"):
        super().__init__()
        self._modpack_path = modpack_path
        self._skip_mods    = skip_mods
        self._skip_quests  = skip_quests
        self._lang_code    = lang_code

    def run(self):
        try:
            scanner = ModpackScanner()

            root = scanner._resolve_game_root(self._modpack_path)
            self.log.emit(f"偵測到遊戲根目錄：{root}")

            targets = _filter_pending_targets(scanner.scan(self._modpack_path, self._lang_code), self._lang_code)

            if self._skip_mods:
                targets = [t for t in targets if t.output_mode != "jar_inject"]
            if self._skip_quests:
                targets = [t for t in targets if t.output_mode != "in_place"]

            fmt_counts: dict[str, int] = {}
            for t in targets:
                fmt_counts[t.format] = fmt_counts.get(t.format, 0) + 1

            SAMPLES_PER_FMT = 3
            pair_counts: dict[str, int] = {}
            samples: dict[str, list[tuple[str, str, str]]] = {}
            total_pairs = 0

            for i, target in enumerate(targets):
                try:
                    strings = read_target_strings(target)
                    existing = read_existing_target(target, self._lang_code)
                except Exception:
                    continue
                pending_keys = diff_keys(strings, existing)
                pending = {k: strings[k] for k in pending_keys}

                fmt = target.format
                pair_counts[fmt] = pair_counts.get(fmt, 0) + len(pending)
                total_pairs += len(pending)

                if fmt not in samples:
                    samples[fmt] = []
                if len(samples[fmt]) < SAMPLES_PER_FMT and pending:
                    key, val = random.choice(list(pending.items()))
                    if val.strip():
                        samples[fmt].append((target.mod_id, key, val))

            self.finished.emit(targets, fmt_counts, total_pairs, samples)

        except Exception as exc:
            self.error.emit(str(exc))


class TranslateWorker(QThread):
    log          = Signal(str)
    progress     = Signal(int, int, str, str, int) # current_idx, total, mod_id, format, pairs_done_so_far
    pair_progress = Signal(int)               # 每條字串完成後：累計已處理對數
    prefill_progress = Signal(int, int)       # 批次預翻譯：已完成/總數（去重後字串）
    finished     = Signal(int, int, int, int, int) # translated, cached, fallback, failed_files, prefill_translated
    error    = Signal(str)

    def __init__(
        self,
        targets: list[TranslationTarget],
        cfg: AppConfig,
        modpack_path: Path,
        retry_count: int = 0,
    ):
        super().__init__()
        self._targets      = targets
        self._cfg          = cfg
        self._modpack_path = modpack_path
        self._retry_count  = retry_count
        self._cancel       = False
        self._translator = None

    def cancel(self):
        self._cancel = True
        if self._translator is not None:
            self._translator.close()

    def run(self):
        self._thread_id = threading.current_thread().ident
        try:
            cache_path = self._cfg.paths.translation_cache
            cache = _load_cache(cache_path)
            total_translated = total_cached = total_fallback = 0
            total = len(self._targets)
            cache_dirty = 0
            failed_by_target: dict[str, dict[str, str]] = {}
            total_pairs_done = 0

            game_root = resolve_game_root(self._modpack_path)
            if any(t.output_mode == "jar_inject" for t in self._targets):
                backed_up = backup_mods(game_root)
                self.log.emit(f"已備份 {backed_up} 個原始模組 jar 至 mods_bak/")
                patched_fonts = patch_modonomicon_unicode_fonts(game_root)
                if patched_fonts:
                    self.log.emit(f"已修補 {patched_fonts} 個 Modonomicon Unicode 字型 fallback")
            if any(t.output_mode == "in_place" for t in self._targets):
                backed_up = backup_quest_configs(game_root)
                self.log.emit(f"已備份 {backed_up} 個任務/設定資料夾至 quests_bak/")

            # 官方用語庫：載入與 regex 編譯皆在 worker 執行緒內，建構後不可變、跨執行緒安全
            glossary = None
            if self._cfg.language.glossary_path:
                glossary = load_glossary(self._cfg.language.glossary_path)
                if glossary is not None:
                    self.log.emit(f"已載入官方用語庫：{len(glossary.terms):,} 條")
                else:
                    self.log.emit("[警告] 無法載入官方用語庫，本次翻譯不使用用語庫。")

            if self._cfg.model.backend_mode == "remote":
                self.log.emit("正在連線遠端 API，請稍候…")
            else:
                self.log.emit("正在連線或啟動本機模型服務，請稍候…")
            translator = None
            try:
                translator = build_translator(
                    self._cfg.model, self._cfg.language.system_prompt, glossary
                )
                self._translator = translator
            except Exception as exc:
                self.error.emit(f"模型服務啟動失敗：{exc}")
                return
            try:
                self.log.emit("模型服務已就緒，開始翻譯…")

                # 遠端模式：先把所有待翻字串批次併發翻進快取（三輪收斂），
                # 之後逐檔階段幾乎全是快取命中；極少數殘餘走既有逐條分段重試。
                # 預翻譯成功數是本輪真實 API 消耗的主體，須併入結尾統計。
                prefill_translated = 0
                if self._cfg.model.backend_mode == "remote" and self._cfg.model.remote_prefill:
                    prefill_stats = prefill_translation_cache(
                        self._targets,
                        self._cfg.model,
                        self._cfg.language.system_prompt,
                        self._cfg.language.code,
                        cache,
                        retry_count=self._retry_count,
                        cancel_check=lambda: self._cancel,
                        on_progress=lambda done, tot: self.prefill_progress.emit(done, tot),
                        on_log=self.log.emit,
                        flush_cache=lambda: _flush_cache(cache_path, cache),
                        glossary=glossary,
                    )
                    prefill_translated = prefill_stats.translated
                    _flush_cache(cache_path, cache)

                # 每條字串完成後觸發：更新累計數並節流發送信號（每 0.5 秒最多 1 次）
                _last_emit_t = [0.0]

                def _on_pair_done(n: int = 1) -> None:
                    nonlocal total_pairs_done
                    total_pairs_done += n
                    now = time.monotonic()
                    if now - _last_emit_t[0] >= 0.5:
                        self.pair_progress.emit(total_pairs_done)
                        _last_emit_t[0] = now

                for i, target in enumerate(self._targets):
                    if self._cancel:
                        self.log.emit("已由使用者取消翻譯。")
                        break

                    self.progress.emit(i, total, target.mod_id, target.format, total_pairs_done)

                    try:
                        n_t, n_c, n_f, failed = process_target(
                            target, translator, cache,
                            self._cfg.language.code,
                            self._retry_count,
                            cancel_check=lambda: self._cancel,
                            on_pair_done=_on_pair_done,
                        )
                        total_translated += n_t
                        total_cached     += n_c
                        total_fallback   += n_f
                        # total_pairs_done 已由 _on_pair_done 累加，不再重複計算
                        if failed:
                            failed_by_target[failed_target_name(target)] = failed
                    except TranslatorFatalError:
                        raise
                    except Exception as exc:
                        self.log.emit(f"[警告] 略過 {target.mod_id}/{target.format}：{exc}")
                        continue

                    cache_dirty += 1
                    if cache_dirty >= 100:
                        _flush_cache(cache_path, cache)
                        cache_dirty = 0
                        self.log.emit(f"進度已儲存（{i + 1}/{total} 個檔案）…")

                _flush_cache(cache_path, cache)

                # 寫出失敗項目
                failed_dir = _PROJECT_ROOT / "Failed Items"
                failed_files_written = _write_failed_items(failed_by_target, failed_dir)
                if failed_files_written > 0:
                    self.log.emit(
                        f"⚠ {failed_files_written} 個模組/任務書含翻譯失敗項目，"
                        f"詳見 Failed Items/ 資料夾。"
                    )

                self.finished.emit(
                    total_translated, total_cached, total_fallback,
                    failed_files_written, prefill_translated,
                )
            finally:
                translator.close()
                self._translator = None

        except Exception as exc:
            self.error.emit(str(exc))
