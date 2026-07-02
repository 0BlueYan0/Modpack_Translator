from __future__ import annotations

from modpack_translator.pipeline.glossary import Glossary
from modpack_translator.pipeline.runner import cache_key, translate_dict


class RecordingTranslator:
    """記錄 translate 呼叫的假 translator；glossary 屬性由測試設定。"""

    def __init__(self, glossary: Glossary | None = None):
        self.glossary = glossary
        self.calls: list[str] = []

    def translate(self, text: str, cancel_check=None) -> str:
        self.calls.append(text)
        return ""


def test_exact_glossary_match_short_circuits_without_llm():
    tr = RecordingTranslator(Glossary({"Nether": "地獄"}))
    cache: dict[str, str] = {}
    result, n_t, n_c, n_f, failed = translate_dict({"k": "Nether"}, {}, tr, cache)
    assert result == {"k": "地獄"}
    assert tr.calls == []          # 不呼叫模型
    assert cache[cache_key("Nether")] == "地獄"  # 照 static 慣例寫入快取
    assert (n_t, n_f) == (1, 0)
    assert failed == {}


def test_exact_match_preserves_surrounding_whitespace():
    tr = RecordingTranslator(Glossary({"Overworld": "主世界"}))
    cache: dict[str, str] = {}
    result, *_ = translate_dict({"k": "  Overworld\n"}, {}, tr, cache)
    assert result == {"k": "  主世界\n"}
    assert tr.calls == []


def test_no_glossary_attribute_translator_still_works():
    # 沒有 glossary 屬性的既有 fake：getattr 預設 None，不得爆炸
    class Bare:
        def translate(self, text, cancel_check=None):
            return "地獄堡壘"

    result, *_ = translate_dict({"k": "Nether Fortress"}, {}, Bare(), {})
    assert result == {"k": "地獄堡壘"}


def test_non_exact_text_goes_to_llm_with_glossary_set():
    tr = RecordingTranslator(Glossary({"Nether": "地獄"}))
    tr.translate_response = ""
    result, n_t, n_c, n_f, failed = translate_dict({"k": "Go to the Nether"}, {}, tr, {})
    assert tr.calls  # 有送模型（假 translator 回空字串 → 驗證失敗回退原文）
    assert result == {"k": "Go to the Nether"}
    assert n_f == 1
