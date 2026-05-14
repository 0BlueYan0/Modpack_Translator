from __future__ import annotations

import re

from modpack_translator.pipeline.preprocessor import decode, strip_preamble

_PH_RE = re.compile(r"\{(\d+)\}")

# 軟性 token：Minecraft 色碼 / 格式碼（裝飾性，遺失不影響結構正確性）
# 例如：&b &r &6 §c §r &k &l &m &n &o
_SOFT_TOKEN_RE = re.compile(r"^[&§][0-9a-fklmnorA-FKLMNOR]$")


def process(raw_translation: str, source_text: str, tokens: list[str]) -> tuple[str, bool]:
    """
    清理並驗證模型輸出。

    採用分層驗證（tiered validation）：
    - 硬性 token（格式字串 %1$s、結構佔位符 {key}、\n 等）遺失 → 回傳 False
    - 軟性 token（色碼 &b、§c 等）遺失 → 仍接受翻譯（文字正確，只是少色彩標記）

    回傳 (final_text, ok)，ok=False 表示硬性 token 遺失，呼叫端應回退至原文。
    """
    text = strip_preamble(raw_translation)
    text = decode(text, tokens)

    # 檢查 decode 後是否仍有越界的 {N}（模型自行生成的錯誤索引）
    remaining = {int(m.group(1)) for m in _PH_RE.finditer(text)}
    if remaining - set(range(len(tokens))):
        return source_text, False

    # 分層驗證：只有硬性 token 遺失才拒絕翻譯
    for idx, token in enumerate(tokens):
        if token not in text:
            if not _SOFT_TOKEN_RE.match(token):
                return source_text, False

    return text, True
