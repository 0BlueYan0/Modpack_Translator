import zipfile
from pathlib import Path
from modpack_translator.pipeline.patcher import write_jar_text

def test_write_jar_text_adds_utf8_entry(tmp_path):
    jar = tmp_path / "m.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/x/keep.txt", "keep")
    write_jar_text(jar, "assets/x/books/b/translated/zh_tw/content/a.mdx", "標題\r\n內文")
    with zipfile.ZipFile(jar) as zf:
        names = set(zf.namelist())
        assert "assets/x/books/b/translated/zh_tw/content/a.mdx" in names
        assert "assets/x/keep.txt" in names  # 原內容保留
        assert zf.read("assets/x/books/b/translated/zh_tw/content/a.mdx").decode("utf-8") == "標題\r\n內文"
