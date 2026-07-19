"""Citadel 書本 txt 端到端:假譯者 → jar 注入 zh_tw/ 譯樹、CJK 折行、冪等。"""
import zipfile

from modpack_translator.pipeline.scanner import ModpackScanner
from modpack_translator.pipeline.runner import process_target

SRC = "assets/alexsmobs/book/animal_dictionary/en_us/anteater.txt"
TGT = "assets/alexsmobs/book/animal_dictionary/zh_tw/anteater.txt"
P0 = "The Anteater is a passive animal found in jungles. It eats leafcutter ants and can be tamed."
EN_TXT = (
    "<NEWLINE>\n"
    "<NEWLINE>\n"
    "The Anteater is a passive animal found in jungles.\n"
    "It eats leafcutter ants and can be tamed.\n"
)
ZH_P0 = "食蟻獸是一種出沒於叢林的被動生物,以切葉蟻為食,並且可以被馴服。"


class _Dict:
    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping

    def translate(self, text, cancel_check=None):
        return self.mapping.get(text.strip(), text)


class _Boom:
    glossary = None

    def translate(self, text, cancel_check=None):
        raise AssertionError("translator.translate must NOT be called on idempotent re-run")


def _make(tmp_path):
    jar = tmp_path / "alexsmobs.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/alexsmobs/book/animal_dictionary/root.json", "{}")
        zf.writestr(SRC, EN_TXT)
    return jar


def _citadel_targets(jar):
    return [t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None) if t.format == "citadel_book_txt"]


def test_process_writes_wrapped_zh_tw_txt(tmp_path):
    jar = _make(tmp_path)
    [t] = _citadel_targets(jar)
    n_translated, _c, n_fallback, failed = process_target(t, _Dict({P0: ZH_P0}), {}, "zh_tw")
    assert n_translated == 1 and n_fallback == 0 and not failed
    with zipfile.ZipFile(jar) as zf:
        assert TGT in zf.namelist()
        out = zf.read(TGT).decode("utf-8-sig")
        assert zf.read(SRC).decode("utf-8-sig") == EN_TXT  # 原文不動
    body = out.split("\n")
    assert body[0] == "<NEWLINE>" and body[1] == "<NEWLINE>"  # 前導佔位保留
    assert "The Anteater" not in out
    assert "食蟻獸" in out
    assert "\n<NEWLINE>\n" in out                            # 折行以 <NEWLINE> 分隔
    # 每行寬度受控(≤ ~16 全形)
    from modpack_translator.pipeline import citadel
    for line in out.splitlines():
        if line.strip() and line.strip() != "<NEWLINE>":
            assert citadel.display_width(line) <= citadel.WRAP_BUDGET + 3.0


def test_idempotent_rescan_and_no_rewrite(tmp_path):
    jar = _make(tmp_path)
    cache: dict[str, str] = {}
    [t] = _citadel_targets(jar)
    process_target(t, _Dict({P0: ZH_P0}), cache, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        before = zf.read(TGT)

    assert _citadel_targets(jar) == []                 # 已翻 → 重掃 0 目標

    process_target(t, _Boom(), cache, "zh_tw")         # 同 cache 重跑不得送翻
    with zipfile.ZipFile(jar) as zf:
        assert zf.read(TGT) == before                  # 不重寫


def test_all_failed_writes_nothing_and_stays_pending(tmp_path):
    jar = _make(tmp_path)
    [t] = _citadel_targets(jar)
    # 假譯者原樣返回 → 驗證不過 → 失敗;不得寫出英文複本佔據 zh_tw 路徑
    _t, _c, n_fallback, failed = process_target(t, _Dict({}), {}, "zh_tw")
    assert n_fallback == 1 and failed
    with zipfile.ZipFile(jar) as zf:
        assert TGT not in zf.namelist()
    assert len(_citadel_targets(jar)) == 1             # 下輪重跑可重試
