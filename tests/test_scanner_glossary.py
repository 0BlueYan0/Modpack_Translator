from __future__ import annotations

import json

from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.preprocessor import parse_json_lang
from modpack_translator.pipeline.scanner import ModpackScanner


def test_scan_file_pending_respects_glossary(tmp_path):
    """檔案唯一的「未翻」候選是與原文相同、命中用語庫的模組名任務標題時：
    無 glossary → 標題走任務標題豁免、視為已翻 → 檔案不列入掃描；
    有 glossary → 守門讓標題變待翻 → 檔案列入。這是掃描階段就必須帶
    glossary 的原因（否則整檔在產生 target 前就被丟棄，事後過濾救不回）。"""
    src = tmp_path / "en_us.json"
    tgt = tmp_path / "zh_tw.json"
    src.write_text(
        json.dumps({"quest.000000000000000A.title": "Twilight Forest", "b": "Hello"}),
        encoding="utf-8",
    )
    tgt.write_text(
        json.dumps({"quest.000000000000000A.title": "Twilight Forest", "b": "你好"}),
        encoding="utf-8",
    )
    s = ModpackScanner()
    assert s._scan_file_has_pending_text(src, tgt, parse_json_lang) is False
    g = Glossary({"Twilight Forest": "暮光森林"})
    assert s._scan_file_has_pending_text(src, tgt, parse_json_lang, g) is True
