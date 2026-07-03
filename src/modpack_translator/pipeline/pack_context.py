"""每包翻譯語境：<模組包遊戲根目錄>/.modpack_translator/context.json。

存兩樣東西：
- extra_prompt：使用者描述此包題材/語氣的額外提示詞（GUI 編輯，併入
  system prompt 靜態段）。
- learned_terms：翻譯過程自動累積的 en→zh 譯法（動態用語庫）。權限受限：
  只參與 prompt 注入，不參與 enforce/守門/exact_match——機器自學的譯法
  若一開始就錯，給強制力會把錯誤確定性地擴散到整包。

存放在包資料夾內（與 mods_bak/、quests_bak/ 同慣例）：一包一份、
跟著包走、缺檔壞檔視為空記憶。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.preprocessor import (
    _has_cjk_text,
    _looks_like_proper_noun_phrase,
)

_CONTEXT_RELPATH = Path(".modpack_translator") / "context.json"


class PackContext:
    def __init__(
        self,
        root: str | Path,
        extra_prompt: str = "",
        learned_terms: dict[str, str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.extra_prompt = extra_prompt
        self._terms: dict[str, str] = dict(learned_terms or {})
        self._lock = threading.Lock()
        self._snapshot: Glossary | None = None
        self._snapshot_stale = True

    @property
    def path(self) -> Path:
        return self.root / _CONTEXT_RELPATH

    def maybe_record(
        self, source: str, translation: str, main_glossary: Glossary | None
    ) -> bool:
        """整串成功翻譯時嘗試記錄 en→zh。只記「整串對整串」配對，
        不做句中對齊（會猜錯）。條件全過才記錄，回傳是否有新增/變更。"""
        en = source.strip()
        zh = translation.strip()
        if not en or not zh or en == zh:
            return False
        if not _looks_like_proper_noun_phrase(en):
            return False
        if not _has_cjk_text(zh):
            return False
        if main_glossary is not None and main_glossary.exact_match(en) is not None:
            return False
        with self._lock:
            if self._terms.get(en) == zh:
                return False
            self._terms[en] = zh
            self._snapshot_stale = True
        return True

    def learned_glossary(self) -> Glossary | None:
        """injection-only 的動態 Glossary 快照；無條目時 None。
        只在有新記錄時重建（Glossary 的 regex 編譯不便宜，不能每請求重來）。"""
        with self._lock:
            if self._snapshot_stale:
                self._snapshot = Glossary(self._terms) if self._terms else None
                self._snapshot_stale = False
            return self._snapshot

    def learned_count(self) -> int:
        with self._lock:
            return len(self._terms)

    def save(self) -> None:
        with self._lock:
            payload = {"extra_prompt": self.extra_prompt, "learned_terms": dict(self._terms)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def load_pack_context(game_root: str | Path) -> PackContext:
    """讀取包記憶。缺檔、壞檔、欄位型別不符都視為空記憶，不報錯。"""
    root = Path(game_root)
    try:
        data = json.loads((root / _CONTEXT_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PackContext(root)
    if not isinstance(data, dict):
        return PackContext(root)
    extra = data.get("extra_prompt")
    terms_raw = data.get("learned_terms")
    terms = {
        en: zh
        for en, zh in (terms_raw.items() if isinstance(terms_raw, dict) else ())
        if isinstance(en, str) and isinstance(zh, str) and en.strip() and zh.strip()
    }
    return PackContext(
        root,
        extra_prompt=extra if isinstance(extra, str) else "",
        learned_terms=terms,
    )
