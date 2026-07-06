import json
import zipfile

from modpack_translator.pipeline import mdx
from modpack_translator.pipeline.scanner import ModpackScanner
from modpack_translator.pipeline.runner import process_target


_META_RAW = json.dumps({
    "getting_started": {"name": "Getting Started", "icon": None},
    "multiblocks.mdx": "Multiblocks",
    "reactor": {"name": "Nuclear Reactors", "icon": None},
    "no_name": {"icon": "foo"},
}, ensure_ascii=False)


def test_extract_meta_gets_string_and_dict_name_values():
    out = mdx.extract_meta(_META_RAW)
    assert out["getting_started"] == "Getting Started"
    assert out["multiblocks.mdx"] == "Multiblocks"
    assert out["reactor"] == "Nuclear Reactors"
    # dict without a string "name" is ignored entirely
    assert "no_name" not in out
    # icon/null must never leak into the translatable dict
    assert "icon" not in out
    assert None not in out.values()


def test_extract_meta_invalid_json_returns_empty():
    assert mdx.extract_meta("not json") == {}


def test_extract_meta_non_dict_json_returns_empty():
    assert mdx.extract_meta("[1, 2, 3]") == {}


def test_rebuild_meta_no_translations_is_semantically_identical():
    out = mdx.rebuild_meta(_META_RAW, {})
    assert json.loads(out) == json.loads(_META_RAW)


def test_rebuild_meta_applies_translations_and_preserves_structure():
    translations = {
        "getting_started": "入門指南",
        "multiblocks.mdx": "多方塊結構",
        "reactor": "核反應爐",
    }
    out = mdx.rebuild_meta(_META_RAW, translations)
    data = json.loads(out)

    assert data["multiblocks.mdx"] == "多方塊結構"
    assert data["getting_started"]["name"] == "入門指南"
    assert data["getting_started"]["icon"] is None
    assert data["reactor"]["name"] == "核反應爐"
    assert data["reactor"]["icon"] is None
    # untouched entry preserved verbatim
    assert data["no_name"] == {"icon": "foo"}

    # all original keys present, in original order
    assert list(data) == list(json.loads(_META_RAW))


class _Dict:
    """依英文輸入回傳不同中文的假譯者。"""

    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping

    def translate(self, text, cancel_check=None):
        return self.mapping.get(text.strip(), text)


def test_end_to_end_meta_dict_values_survive_translation(tmp_path):
    jar = tmp_path / "oritech.jar"
    base = "assets/oracle_index/books/oritech"
    src = f"{base}/docs/_meta.json"
    tgt = f"{base}/translated/zh_tw/docs/_meta.json"
    meta = json.dumps({
        "getting_started": {"name": "Getting Started", "icon": None},
        "multiblocks.mdx": "Multiblocks",
    }, ensure_ascii=False)
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(src, meta)

    targets = [
        t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None)
        if t.format == "oracle_meta"
    ]
    assert len(targets) == 1
    [t] = targets

    translator = _Dict({
        "Getting Started": "入門指南",
        "Multiblocks": "多方塊結構",
    })
    process_target(t, translator, {}, "zh_tw")

    with zipfile.ZipFile(jar) as zf:
        assert tgt in zf.namelist()
        out = json.loads(zf.read(tgt).decode("utf-8-sig"))

    assert out["multiblocks.mdx"] == "多方塊結構"
    assert out["getting_started"]["name"] == "入門指南"
    assert out["getting_started"]["icon"] is None
