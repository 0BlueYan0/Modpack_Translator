"""光影包 shader lang 掃描（Iris/Oculus，v1.13.0）。

Soulrend 實包 6 個光影包的 shaders/lang/ 設定 GUI 文字全無 zh_tw；工具
先前完全不掃 shaderpacks/。檔名大小寫混用（en_us.lang / en_US.lang，
既有譯檔 zh_cn.lang / zh_CN.lang / ru_RU.lang 並存），證實 Iris 以小寫化
檔名推導語言碼——譯檔一律寫小寫 zh_tw.lang。
"""
import zipfile

from modpack_translator.pipeline.scanner import ModpackScanner
from modpack_translator.pipeline.runner import process_target


class _Dict:
    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping

    def translate(self, text, cancel_check=None):
        return self.mapping.get(text.strip(), text)


EN = "option.SHADOWS=Shadows\noption.BLOOM=Bloom\n#comment\nscreen.LIGHTING=Lighting\n"


def _shader_targets(root):
    return [t for t in ModpackScanner()._scan_shaderpacks(root, "zh_tw", None)]


def test_zip_shaderpack_uppercase_en_detected(tmp_path):
    root = tmp_path / "minecraft"
    sp = root / "shaderpacks"
    sp.mkdir(parents=True)
    zpath = sp / "Complementary.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("shaders/lang/en_US.lang", EN)  # 大寫區碼
    [t] = _shader_targets(root)
    assert t.format == "legacy_lang"
    assert t.output_mode == "jar_inject"
    assert t.path_in_jar == "shaders/lang/en_US.lang"
    assert t.target_path_in_jar == "shaders/lang/zh_tw.lang"  # 一律小寫


def test_zip_shaderpack_translation_written_lowercase(tmp_path):
    root = tmp_path / "minecraft"
    sp = root / "shaderpacks"
    sp.mkdir(parents=True)
    zpath = sp / "Bliss.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("shaders/lang/en_us.lang", EN)
    [t] = _shader_targets(root)
    process_target(t, _Dict({"Shadows": "陰影", "Bloom": "泛光", "Lighting": "光照"}), {}, "zh_tw")
    with zipfile.ZipFile(zpath) as zf:
        assert "shaders/lang/zh_tw.lang" in zf.namelist()
        out = zf.read("shaders/lang/zh_tw.lang").decode("utf-8")
    assert "option.SHADOWS=陰影" in out
    assert "screen.LIGHTING=光照" in out


def test_folder_shaderpack_detected_and_written(tmp_path):
    root = tmp_path / "minecraft"
    lang = root / "shaderpacks" / "MyPack" / "shaders" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_US.lang").write_text(EN, encoding="utf-8")
    [t] = _shader_targets(root)
    assert t.format == "pack_legacy_lang"
    assert t.output_mode == "in_place"
    assert t.target_file.name == "zh_tw.lang"
    process_target(t, _Dict({"Shadows": "陰影", "Bloom": "泛光", "Lighting": "光照"}), {}, "zh_tw")
    out = (lang / "zh_tw.lang").read_text(encoding="utf-8")
    assert "option.BLOOM=泛光" in out


def test_macosx_and_translated_locales_ignored(tmp_path):
    root = tmp_path / "minecraft"
    sp = root / "shaderpacks"
    sp.mkdir(parents=True)
    zpath = sp / "Retro.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("shaders/lang/en_us.lang", EN)
        zf.writestr("__MACOSX/shaders/lang/._en_us.lang", "junk")
        zf.writestr("shaders/lang/zh_cn.lang", "option.SHADOWS=阴影\n")
    targets = _shader_targets(root)
    # 只抽英文來源一次，不抽 __MACOSX、不抽既有 zh_cn
    assert len(targets) == 1
    assert targets[0].path_in_jar == "shaders/lang/en_us.lang"


def test_idempotent_when_zh_tw_complete(tmp_path):
    root = tmp_path / "minecraft"
    sp = root / "shaderpacks"
    sp.mkdir(parents=True)
    zpath = sp / "Solas.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("shaders/lang/en_US.lang", "option.SHADOWS=Shadows\n")
        zf.writestr("shaders/lang/zh_tw.lang", "option.SHADOWS=陰影\n")
    assert _shader_targets(root) == []
