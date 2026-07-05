import zipfile

from modpack_translator.pipeline.scanner import ModpackScanner
from modpack_translator.pipeline.runner import process_target


class _Fixed:
    glossary = None

    def __init__(self, reply):
        self.reply = reply

    def translate(self, text, cancel_check=None):
        return self.reply


def test_process_oracle_mdx_writes_translated_tree(tmp_path):
    jar = tmp_path / "oritech.jar"
    base = "assets/oracle_index/books/oritech"
    mdx = "---\r\ntitle: Chainsaw\r\ntype: item\r\nid: oritech:chainsaw\r\n---\r\n\r\nThe chainsaw cuts wood fast.\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(f"{base}/content/equipment/chainsaw.mdx", mdx)
    [t] = [t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None) if t.format == "oracle_mdx"]
    process_target(t, _Fixed("鏈鋸快速砍樹。"), {}, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        tgt = "assets/oracle_index/books/oritech/translated/zh_tw/content/equipment/chainsaw.mdx"
        assert tgt in zf.namelist()
        out = zf.read(tgt).decode("utf-8-sig")
    assert "鏈鋸快速砍樹。" in out         # 散文已翻
    assert "id: oritech:chainsaw" in out    # frontmatter 結構保留
    assert "type: item" in out
