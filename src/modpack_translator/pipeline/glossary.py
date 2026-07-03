"""Minecraft 官方用語庫：官方 en→zh_tw 詞彙對照的載入、比對與 prompt 注入。

對照表由 scripts/build_glossary.py 離線產生並 commit 至 assets/glossary/，
執行期不連網。兩種用途：
1. 翻譯請求前掃描來源文字，把命中的官方詞彙以 [Glossary] 區塊附加在
   system prompt 尾端（靜態前綴不變，可吃 provider 端 prompt caching）。
2. 整串（trim 後）正好等於官方詞彙時直接回官方譯名，不呼叫模型。

Glossary 建構後不可變（compiled regex + dict），可安全跨執行緒共用。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

# src/modpack_translator/pipeline/ → 上 4 層到專案根目錄
_PROJECT_ROOT = Path(__file__).parents[3]
_GLOSSARY_DIR = _PROJECT_ROOT / "assets" / "glossary"

# 單條/逐條救援請求的詞數上限（~100-130 tokens）
_SINGLE_TERM_CAP = 8
# 批次預翻譯請求的詞數上限（~350-500 tokens）
_BATCH_TERM_CAP = 40
# 區塊字元硬上限：詞數上限之外的第二道保險
_BLOCK_CHAR_BUDGET = 2000
_BLOCK_HEADER = (
    "\n\n[Glossary] Official Minecraft zh_tw translations. "
    "When these terms appear, you MUST use these translations:\n"
)
_EXACT_WS_RE = re.compile(r"^(\s*)(.*?)(\s*)$", re.DOTALL)


class Glossary:
    """英→繁對照表與比對器。terms 的 key 為官方英文詞、value 為官方繁中譯名。"""

    def __init__(self, terms: dict[str, str]) -> None:
        self.terms = dict(terms)
        self._zh_by_lower = {en.lower(): zh for en, zh in self.terms.items()}
        self._canon_by_lower = {en.lower(): en for en in self.terms}
        self._pattern: re.Pattern[str] | None = None
        self._enforce_pattern: re.Pattern[str] | None = None
        self._enforce_ready = False

    def _compiled(self) -> re.Pattern[str]:
        if self._pattern is None:
            # 長詞優先：regex alternation 取第一個命中的分支，
            # 降冪排序讓「Nether Star」先於「Nether」。
            # lookaround 而非 \b：避免「Networking」誤中「Nether」，
            # (?:e?s)? 容忍任務書散文中的複數形。
            ordered = sorted(self.terms, key=len, reverse=True)
            alternation = "|".join(re.escape(term) for term in ordered)
            self._pattern = re.compile(
                r"(?<![A-Za-z0-9])(?:" + alternation + r")(?:e?s)?(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
        return self._pattern

    def _lookup(self, surface: str) -> tuple[str, str] | None:
        """把命中的表面形（可能為複數/異大小寫）正規化回 (官方英文, 官方譯名)。"""
        lower = surface.lower()
        for candidate in (lower, lower[:-1], lower[:-2]):
            zh = self._zh_by_lower.get(candidate)
            if zh is not None:
                return self._canon_by_lower[candidate], zh
        return None

    def match_terms(self, texts: Iterable[str]) -> list[tuple[str, str]]:
        """掃描來源文字（可為 {N} 編碼後），回傳去重後的 (英文, 譯名) 列表。

        依詞長降冪排序，讓 format_block 截斷時保留最 specific 的詞。
        """
        if not self.terms:
            return []
        pattern = self._compiled()
        found: dict[str, str] = {}
        for text in texts:
            for m in pattern.finditer(text):
                pair = self._lookup(m.group(0))
                if pair is not None:
                    found.setdefault(pair[0], pair[1])
        return sorted(found.items(), key=lambda kv: (-len(kv[0]), kv[0]))

    def exact_match(self, text: str) -> str | None:
        """整串（trim 後）正好是官方詞彙時回官方譯名（保留前後空白），否則 None。"""
        m = _EXACT_WS_RE.match(text)
        lead, core, trail = m.group(1), m.group(2), m.group(3)
        if not core:
            return None
        zh = self._zh_by_lower.get(core.lower())
        if zh is None:
            return None
        return f"{lead}{zh}{trail}"

    def _enforce_compiled(self) -> re.Pattern[str] | None:
        """句中替換用的 pattern：只含多字詞條目、區分大小寫。無多字詞時 None。

        詞本體置於 group(1)、複數 (?:e?s)? 在群組外。詞前若為「CJK 字元＋
        單一半形空格」，把該空格一併納入 match：中文與譯名間不留突兀空白
        （英文語境如 "of Twilight Forest" 的空格因前字非 CJK 而保留）。
        """
        if not self._enforce_ready:
            multi = sorted(
                (t for t in self.terms if " " in t.strip()), key=len, reverse=True
            )
            if multi:
                alternation = "|".join(re.escape(term) for term in multi)
                self._enforce_pattern = re.compile(
                    r"(?:(?<=[㐀-鿿]) )?"
                    r"(?<![A-Za-z0-9])(" + alternation + r")(?:e?s)?(?![A-Za-z0-9])"
                )
            self._enforce_ready = True
        return self._enforce_pattern

    def enforce(self, text: str) -> str:
        """把譯文中殘留的英文詞彙替換為譯名（事後保證）。

        區分大小寫（避免動詞 create 誤傷）、整詞邊界、長詞優先。多字詞條目
        句中即替換（並吃掉中文與詞之間的單一空白）；單字詞條目只在整串
        （trim 後）完全等於該詞時替換，句中交給 prompt 注入讓模型判斷語境。
        該詞譯名已出現在譯文中則跳過（保護「中文名(English)」夾註）。純文字
        轉換，token 保全由呼叫端（runner._enforce_glossary）負責。

        group(1) 恰為某個 alternation 詞（大小寫敏感、複數在群組外），故可用
        self.terms.get(group(1)) 直接取譯名，不需再正規化。
        """
        if not self.terms:
            return text
        m = _EXACT_WS_RE.match(text)
        zh_exact = self.terms.get(m.group(2))
        if zh_exact is not None:
            return f"{m.group(1)}{zh_exact}{m.group(3)}"
        pattern = self._enforce_compiled()
        if pattern is None:
            return text

        def _sub(match: re.Match[str]) -> str:
            zh = self.terms.get(match.group(1))
            if zh is None or zh in text:
                return match.group(0)
            return zh

        return pattern.sub(_sub, text)

    def format_block(self, pairs: list[tuple[str, str]], cap: int = _SINGLE_TERM_CAP) -> str:
        """把命中的詞彙渲染成附加在 system prompt 尾端的 [Glossary] 區塊。"""
        if not pairs:
            return ""
        lines: list[str] = []
        size = len(_BLOCK_HEADER)
        for en, zh in pairs[:cap]:
            line = f"{en} = {zh}"
            if size + len(line) + 1 > _BLOCK_CHAR_BUDGET:
                break
            lines.append(line)
            size += len(line) + 1
        if not lines:
            return ""
        return _BLOCK_HEADER + "\n".join(lines)


def augment_prompt(
    system_prompt: str,
    glossary: Glossary | None,
    texts: Iterable[str],
    cap: int = _SINGLE_TERM_CAP,
) -> str:
    """glossary 為 None 或無命中時原樣回傳 system_prompt，否則附加 [Glossary] 區塊。"""
    if glossary is None:
        return system_prompt
    block = glossary.format_block(glossary.match_terms(texts), cap=cap)
    return system_prompt + block


def load_glossary(path: str | Path | None) -> Glossary | None:
    """載入對照表 JSON。路徑為空、檔案缺失或內容無效時回 None（呼叫端記警告）。"""
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    terms = {
        en: zh
        for en, zh in data.items()
        if isinstance(en, str) and isinstance(zh, str) and en.strip() and zh.strip()
    }
    if not terms:
        return None
    return Glossary(terms)


def _version_sort_key(version: str) -> tuple[int, ...]:
    return tuple(int(piece) if piece.isdigit() else -1 for piece in version.split("."))


def available_glossaries(
    lang_code: str,
    glossary_dir: str | Path | None = None,
) -> list[tuple[str, Path]]:
    """掃描 assets/glossary/ 下的 {lang_code}_{version}.json，回傳 (版本, 路徑) 版本降冪。"""
    d = Path(glossary_dir) if glossary_dir is not None else _GLOSSARY_DIR
    if not d.is_dir():
        return []
    prefix = f"{lang_code}_"
    out: list[tuple[str, Path]] = []
    for p in d.glob(f"{lang_code}_*.json"):
        version = p.stem[len(prefix):]
        if version:
            out.append((version, p))
    out.sort(key=lambda entry: _version_sort_key(entry[0]), reverse=True)
    return out
