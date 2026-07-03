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


def test_cached_identical_modname_replaced_without_llm():
    g = Glossary({"Twilight Forest": "暮光森林"})
    tr = RecordingTranslator(g)
    ck = cache_key("Twilight Forest")
    cache = {ck: "Twilight Forest"}  # 舊「英文→英文」快取
    result, n_t, n_c, n_f, failed = translate_dict({"k": "Twilight Forest"}, {}, tr, cache)
    assert result == {"k": "暮光森林"}
    assert tr.calls == []           # 守門後由 exact_match 短路，不呼叫模型
    assert cache[ck] == "暮光森林"


def test_cached_translation_with_leftover_name_enforced():
    g = Glossary({"Twilight Forest": "暮光森林"})
    tr = RecordingTranslator(g)
    src = "Welcome to the Twilight Forest!"
    ck = cache_key(src)
    cache = {ck: "歡迎來到 Twilight Forest！"}
    result, *_ = translate_dict({"k": src}, {}, tr, cache)
    assert result == {"k": "歡迎來到暮光森林！"}
    assert cache[ck] == "歡迎來到暮光森林！"
    assert tr.calls == []


def test_model_identical_return_enforced():
    # 原文帶驚嘆號 → exact_match 不短路 → 送模型 → 模型原樣返回
    # （帶標點的原樣返回不觸發守門，但走專有名詞豁免後由 enforce 換上譯名）
    g = Glossary({"Twilight Forest": "暮光森林"})

    class EchoTranslator:
        glossary = g

        def translate(self, text, cancel_check=None):
            return text

    result, n_t, *_ = translate_dict({"k": "Twilight Forest!"}, {}, EchoTranslator(), {})
    assert result == {"k": "暮光森林!"}


def test_enforce_glossary_applies_and_preserves_tokens():
    from modpack_translator.pipeline.runner import _enforce_glossary
    g = Glossary({"Twilight Forest": "暮光森林"})
    # CJK 後緊接的半形空白一併吃掉（enforce 的 CJK-空白處理）
    assert _enforce_glossary(g, "Twilight Forest ahead", "前方 Twilight Forest") == "前方暮光森林"
    # 含 %s 硬性 token 的譯文：替換模組名但保留 token（_preserves_required_tokens 通過）
    assert _enforce_glossary(
        g, "Twilight Forest %s", "前方 Twilight Forest %s"
    ) == "前方暮光森林 %s"
    # glossary=None 直接原樣返回
    assert _enforce_glossary(None, "x", "y") == "y"
