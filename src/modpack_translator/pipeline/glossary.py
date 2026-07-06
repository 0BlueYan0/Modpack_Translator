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
from typing import Iterable, Sequence

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

    def __init__(
        self, terms: dict[str, str], custom_keys: Iterable[str] | None = None
    ) -> None:
        self.terms = dict(terms)
        # 使用者自訂用語庫的詞條 key（大小寫敏感）。只有這些單字詞會啟用「句中
        # 替換」；官方詞庫的單字詞維持保守（僅整串相等時替換），避免 create 等
        # 英文常用字被誤傷。使用者自行加入的專有名詞（模組名、素材名）才強制。
        self._custom_keys = {k for k in (custom_keys or ()) if k in self.terms}
        self._zh_by_lower = {en.lower(): zh for en, zh in self.terms.items()}
        self._canon_by_lower = {en.lower(): en for en in self.terms}
        self._pattern: re.Pattern[str] | None = None
        self._enforce_pattern: re.Pattern[str] | None = None
        self._enforce_ready = False
        self._enforce_custom_pattern: re.Pattern[str] | None = None
        self._enforce_custom_ready = False

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

    def _enforce_custom_compiled(self) -> "re.Pattern[str] | None":
        """自訂用語庫「單字詞」的句中替換 pattern（不分大小寫）。無則 None。

        官方詞庫的單字詞不納入（維持保守），只有使用者自訂的專有名詞才句中
        替換。以不分大小寫比對，涵蓋散文中的 Allthemodium／AllTheModium 等
        混寫；全小寫的表面字（如圖片路徑 atm:textures/allthemodium/…）由
        enforce() 的替換函式跳過，避免破壞資源位置。詞前邊界除了「非英數」也
        接受色碼（&5、§a）——任務標題常見「&5Unobtainium」色碼緊貼詞。詞後
        若接中文，連同中間單一半形空白一併吃掉：「Unobtainium 工具」→「難得
        素工具」。
        """
        if not self._enforce_custom_ready:
            singles = sorted(
                (t for t in self._custom_keys if " " not in t.strip()),
                key=len,
                reverse=True,
            )
            if singles:
                alternation = "|".join(re.escape(term) for term in singles)
                self._enforce_custom_pattern = re.compile(
                    r"(?:(?<![A-Za-z0-9])|(?<=[&§][0-9A-Za-z])|(?<=&#[0-9A-Fa-f]{6}))"
                    r"(?:(?<=[㐀-鿿]) )?"
                    r"(" + alternation + r")(?:e?s)?"
                    r"(?![A-Za-z0-9])(?: (?=[㐀-鿿]))?",
                    re.IGNORECASE,
                )
            self._enforce_custom_ready = True
        return self._enforce_custom_pattern

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
        def _sub(match: re.Match[str]) -> str:
            zh = self.terms.get(match.group(1))
            if zh is None or zh in result:
                return match.group(0)
            return zh

        def _sub_custom(match: re.Match[str]) -> str:
            surface = match.group(1)
            # 全小寫表面字視為資源位置/識別字（atm:.../allthemodium/…），不動
            if surface.islower():
                return match.group(0)
            zh = self._zh_by_lower.get(surface.lower())
            if zh is None or zh in result:
                return match.group(0)
            return zh

        result = text
        pattern = self._enforce_compiled()
        if pattern is not None:
            result = pattern.sub(_sub, result)
        custom = self._enforce_custom_compiled()
        if custom is not None:
            result = custom.sub(_sub_custom, result)
        return result

    def format_block(self, pairs: list[tuple[str, str]], cap: int = _SINGLE_TERM_CAP) -> str:
        """把命中的詞彙渲染成附加在 system prompt 尾端的 [Glossary] 區塊。"""
        return format_block(pairs, cap=cap)


def format_block(pairs: list[tuple[str, str]], cap: int = _SINGLE_TERM_CAP) -> str:
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


def merged_match_pairs(
    glossaries: "Sequence[Glossary | None]",
    texts: Iterable[str],
) -> list[tuple[str, str]]:
    """依序合併多個用語庫的命中結果：先者優先（主用語庫先佔詞數上限、
    衝突時先者勝），大小寫不敏感去重。texts 會被重複掃描，先物化。"""
    materialized = list(texts)
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for g in glossaries:
        if g is None:
            continue
        for en, zh in g.match_terms(materialized):
            if en.lower() in seen:
                continue
            seen.add(en.lower())
            pairs.append((en, zh))
    return pairs


def augment_prompt(
    system_prompt: str,
    glossary: Glossary | None,
    texts: Iterable[str],
    cap: int = _SINGLE_TERM_CAP,
    context_glossary: Glossary | None = None,
) -> str:
    """無用語庫或無命中時原樣回傳 system_prompt，否則附加 [Glossary] 區塊。
    context_glossary（動態層）墊底：主用語庫命中先佔詞數上限。"""
    if glossary is None and context_glossary is None:
        return system_prompt
    pairs = merged_match_pairs((glossary, context_glossary), texts)
    block = format_block(pairs, cap=cap)
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


def load_custom_terms(path: str | Path | None) -> dict[str, str]:
    """讀取自訂用語 JSON（en→zh）。zh 空字串是合法值（＝刪除該詞條），
    必須保留給合併器判讀。缺檔、壞檔回空 dict。"""
    if not path:
        return {}
    p = Path(path).expanduser()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        en.strip(): zh.strip()
        for en, zh in data.items()
        if isinstance(en, str) and isinstance(zh, str) and en.strip()
    }


def save_custom_terms(path: str | Path, terms: dict[str, str]) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(terms, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_merged_glossary(
    official_path: str | Path | None,
    modnames_path: str | Path | None,
    custom_path: str | Path | None,
) -> Glossary | None:
    """三層合併：官方 → 模組名 → 自訂，後者覆蓋前者；自訂空譯名刪除詞條
    （大小寫不敏感比對既有鍵）。全空時回 None。

    自訂覆蓋既有詞時保留既有鍵的原始大小寫（enforce 為大小寫敏感），只更新
    譯名；既有無此詞才以自訂鍵新增。"""
    terms: dict[str, str] = {}
    for p in (official_path, modnames_path):
        layer = load_glossary(p)
        if layer is not None:
            terms.update(layer.terms)
    custom_keys: set[str] = set()
    for en, zh in load_custom_terms(custom_path).items():
        existing = [k for k in terms if k.lower() == en.lower()]
        if zh:
            if existing:
                for k in existing:
                    terms[k] = zh
                    custom_keys.add(k)
            else:
                terms[en] = zh
                custom_keys.add(en)
        else:
            for k in existing:
                del terms[k]
                custom_keys.discard(k)
    return Glossary(terms, custom_keys=custom_keys) if terms else None


def modnames_glossary_path(lang_code: str) -> Path:
    return _GLOSSARY_DIR / f"modnames_{lang_code}.json"


def default_custom_glossary_path() -> Path:
    """使用者級自訂用語檔：住家目錄下，更新/重灌程式都不會被清掉。"""
    return Path.home() / ".modpack_translator" / "custom_glossary.json"


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
