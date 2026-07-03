from __future__ import annotations

from typing import Sequence

SPEED_WINDOW = 30.0  # 秒,滑動視窗寬度


def _format_hms(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _window_speed(now: float, samples: Sequence[tuple[float, int]]) -> float | None:
    """最近 SPEED_WINDOW 秒內有 ≥2 筆樣本且有進度時回傳視窗速度,否則 None。"""
    cutoff = now - SPEED_WINDOW
    window = [(t, p) for t, p in samples if t >= cutoff]
    if len(window) < 2:
        return None
    dt = window[-1][0] - window[0][0]
    dp = window[-1][1] - window[0][1]
    if dt <= 0 or dp <= 0:
        return None
    return dp / dt


def build_stats_text(
    now: float,
    start_time: float,
    samples: Sequence[tuple[float, int]],
    pairs_done: int,
    total_pairs: int,
) -> str:
    """組出統計標籤文字。samples 為 (monotonic 時間戳, 累計完成對數)。

    速度優先用滑動視窗計算(本地模型的即時速度);視窗樣本不足或無進度、
    但已有完成對數時,退回「開始至今的累計平均」並標示(平均),
    確保遠端慢速 API(單條 >8 秒)也永遠有數字與 ETA 可顯示。
    """
    elapsed = max(0.0, now - start_time)
    elapsed_str = _format_hms(int(elapsed))

    speed = _window_speed(now, samples)
    is_average = False
    if speed is None and pairs_done >= 1 and elapsed > 0:
        speed = pairs_done / elapsed
        is_average = True

    if speed is None or speed <= 0:
        # 尚未有任何字串完成:連線中或第一條仍在推理
        speed_part = "翻譯中…"
        eta_str = "計算中…"
    else:
        speed_str = f"{speed:.2f}" if speed < 1 else f"{speed:.1f}"
        suffix = "（平均）" if is_average else ""
        speed_part = f"{speed_str} 句/秒{suffix}"
        total = max(total_pairs, pairs_done + 1)
        remaining = max(0, total - pairs_done)
        eta_str = _format_hms(int(remaining / speed))

    return f"速度：{speed_part}  |  已用時間：{elapsed_str}  |  預計剩餘：{eta_str}"


def build_summary_lines(
    cancelled: bool,
    prefill_translated: int,
    translated: int,
    cached: int,
    fallback: int,
) -> list[str]:
    """組出翻譯結尾摘要（GUI 與 CLI 共用）。

    批次預翻譯的成功字串在逐檔階段以快取命中計數，若不併入顯示，
    「已翻譯」會嚴重低估真實 API 消耗（取消於預翻譯階段時甚至全為 0）。
    故「本輪 API 翻譯」= 批次預翻譯成功數 + 逐檔階段實翻數。
    """
    lines = ["翻譯已中止" if cancelled else "翻譯完成"]
    if prefill_translated > 0:
        api_total = prefill_translated + translated
        lines.append(
            f"  本輪 API 翻譯：{api_total:,} 條"
            f"（批次預翻譯 {prefill_translated:,} + 逐檔 {translated:,}）"
        )
        cache_note = "（含本輪批次預翻譯寫入的字串）" if cached > 0 else ""
        lines.append(f"  快取命中：{cached:,} 組{cache_note}")
    else:
        lines.append(f"  已翻譯：{translated:,} 組")
        lines.append(f"  快取命中：{cached:,} 組")
    lines.append(f"  回退（使用原文）：{fallback:,} 組")
    if cancelled and prefill_translated + translated > 0:
        lines.append("  已完成的字串皆已寫入快取，繼續翻譯不會重翻。")
    return lines
