import pytest

import modpack_translator.pipeline.runner as runner
from modpack_translator.pipeline._chat import TranslatorFatalError


class BoomTranslator:
    def translate(self, text, cancel_check=None):
        raise TranslatorFatalError("金鑰錯誤")


def test_translate_dict_propagates_fatal(monkeypatch):
    # 強制該字串走「翻譯」路徑並讓驗證通過，確保會呼叫 translator.translate
    monkeypatch.setattr(runner, "classify_translation_entry", lambda k, s: "translate")
    monkeypatch.setattr(runner, "is_usable_translation", lambda s, t: True)
    with pytest.raises(TranslatorFatalError):
        runner.translate_dict({"k": "Hello world"}, {}, BoomTranslator(), {}, retry_count=0)
