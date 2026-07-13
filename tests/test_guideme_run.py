"""GuideME 指南端到端:假譯者 → jar 注入 _zh_tw 譯頁、結構保留、冪等。"""
import zipfile

from modpack_translator.pipeline.scanner import ModpackScanner
from modpack_translator.pipeline.runner import process_target

SRC = "assets/ae2/ae2guide/ae2-mechanics/channels.md"
TGT = "assets/ae2/ae2guide/_zh_tw/ae2-mechanics/channels.md"
PARA = "Channels are like USB cables to all your devices."
PAGE = (
    "---\n"
    "navigation:\n"
    "  parent: ae2-mechanics/ae2-mechanics-index.md\n"
    "  title: Channels\n"
    "  icon: controller\n"
    "---\n"
    "\n"
    "# Channels\n"
    "\n"
    f"{PARA}\n"
    "\n"
    "<GameScene zoom=\"7\" interactive={true}>\n"
    "  <ImportStructure src=\"../assets/assemblies/channel_demonstration_1.snbt\" />\n"
    "</GameScene>\n"
)


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


def _guideme_targets(jar):
    return [t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None) if t.format == "guideme_md"]


def _make(tmp_path):
    jar = tmp_path / "ae2.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(SRC, PAGE)
    return jar


def test_process_guideme_md_writes_lang_tree(tmp_path):
    jar = _make(tmp_path)
    [t] = _guideme_targets(jar)
    process_target(t, _Dict({
        "Channels": "頻道",
        PARA: "頻道就像接到所有裝置的 USB 線。",
    }), {}, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        assert TGT in zf.namelist()
        out = zf.read(TGT).decode("utf-8-sig")
        original = zf.read(SRC).decode("utf-8-sig")
    assert original == PAGE                                   # 原英文頁原封不動
    assert "  title: 頻道\n" in out                            # 側欄標題已翻
    assert "# 頻道\n" in out                                   # 內文標題已翻
    assert "頻道就像接到所有裝置的 USB 線。" in out             # 散文已翻
    assert PARA not in out
    assert "  parent: ae2-mechanics/ae2-mechanics-index.md\n" in out   # 結構保留
    assert "  icon: controller\n" in out
    assert "<ImportStructure src=\"../assets/assemblies/channel_demonstration_1.snbt\" />" in out


def test_guideme_md_idempotent_no_rewrite(tmp_path):
    jar = _make(tmp_path)
    cache: dict[str, str] = {}
    [t] = _guideme_targets(jar)
    process_target(t, _Dict({
        "Channels": "頻道",
        PARA: "頻道就像接到所有裝置的 USB 線。",
    }), cache, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        before = zf.read(TGT)

    assert _guideme_targets(jar) == []          # 已全譯 → 重掃 0 目標

    process_target(t, _Boom(), cache, "zh_tw")  # 同 cache 重跑不得送翻
    with zipfile.ZipFile(jar) as zf:
        after = zf.read(TGT)
    assert after == before                       # 內容相同不重寫


def test_guideme_md_partial_existing_merge(tmp_path):
    """既有 _zh_tw 標題已中文、散文仍英文:只補譯散文,既有中文標題原封不動。"""
    jar = tmp_path / "ae2.jar"
    partial = PAGE.replace("  title: Channels", "  title: 頻道").replace("# Channels", "# 頻道")
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(SRC, PAGE)
        zf.writestr(TGT, partial)
    [t] = _guideme_targets(jar)
    assert t.existing_path_in_jar == TGT
    # mapping 不含 "Channels":若工具誤送標題重翻會拿回英文,斷言就會抓到
    process_target(t, _Dict({
        PARA: "頻道就像接到所有裝置的 USB 線。",
        "Channels": "不應該用到這條",
    }), {}, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        out = zf.read(TGT).decode("utf-8-sig")
    assert "頻道就像接到所有裝置的 USB 線。" in out
    assert PARA not in out
    assert "  title: 頻道\n" in out              # 既有譯文保留
    assert "不應該用到這條" not in out
