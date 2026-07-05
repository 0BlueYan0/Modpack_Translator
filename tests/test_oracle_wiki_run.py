import zipfile

from modpack_translator.pipeline.scanner import ModpackScanner
from modpack_translator.pipeline.runner import process_target

_BASE = "assets/oracle_index/books/oritech"


class _Fixed:
    glossary = None

    def __init__(self, reply):
        self.reply = reply

    def translate(self, text, cancel_check=None):
        return self.reply


class _Dict:
    """依英文輸入回傳不同中文的假譯者：可辨別「哪一段」被送翻。
    未命中者原樣返回（交由既有驗證判定，通常會 fallback）。"""

    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping

    def translate(self, text, cancel_check=None):
        return self.mapping.get(text.strip(), text)


class _Boom:
    """一旦被呼叫就爆炸：用來證明冪等重跑不會送翻。"""

    glossary = None

    def translate(self, text, cancel_check=None):
        raise AssertionError("translator.translate must NOT be called on idempotent re-run")


def _oracle_targets(jar, include_translated=False):
    scanner = ModpackScanner()
    if include_translated:
        scanner._include_translated = True
    return [t for t in scanner._scan_jar(jar, "zh_tw", None) if t.format == "oracle_mdx"]


def test_process_oracle_mdx_writes_translated_tree(tmp_path):
    jar = tmp_path / "oritech.jar"
    base = _BASE
    mdx = "---\r\ntitle: Chainsaw\r\ntype: item\r\nid: oritech:chainsaw\r\n---\r\n\r\nThe chainsaw cuts wood fast.\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(f"{base}/content/equipment/chainsaw.mdx", mdx)
    [t] = _oracle_targets(jar)
    process_target(t, _Fixed("鏈鋸快速砍樹。"), {}, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        tgt = "assets/oracle_index/books/oritech/translated/zh_tw/content/equipment/chainsaw.mdx"
        assert tgt in zf.namelist()
        out = zf.read(tgt).decode("utf-8-sig")
    assert "鏈鋸快速砍樹。" in out         # 散文已翻
    assert "id: oritech:chainsaw" in out    # frontmatter 結構保留
    assert "type: item" in out


def test_oracle_mdx_translates_body_and_preserves_frontmatter(tmp_path):
    """散文段翻成中文;id/type 等結構欄位逐字保留;原英文句子不再出現。"""
    jar = tmp_path / "oritech.jar"
    src = "assets/oracle_index/books/oritech/content/equipment/chainsaw.mdx"
    tgt = "assets/oracle_index/books/oritech/translated/zh_tw/content/equipment/chainsaw.mdx"
    para = "The chainsaw cuts wood fast."
    mdx = f"---\r\ntitle: Chainsaw\r\ntype: item\r\nid: oritech:chainsaw\r\n---\r\n\r\n{para}\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(src, mdx)
    [t] = _oracle_targets(jar)
    process_target(t, _Dict({para: "鏈鋸能快速砍伐樹木。"}), {}, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        assert tgt in zf.namelist()
        out = zf.read(tgt).decode("utf-8-sig")
    assert "鏈鋸能快速砍伐樹木。" in out        # 散文已翻
    assert para not in out                       # 原英文句子已被取代
    assert "id: oritech:chainsaw" in out         # 結構欄位逐字保留
    assert "type: item" in out


def test_oracle_mdx_idempotent_no_rewrite(tmp_path):
    """翻一次後:重掃跳過(0 目標);以同 cache 重跑不送翻且不重寫 jar entry。"""
    jar = tmp_path / "oritech.jar"
    src = "assets/oracle_index/books/oritech/content/equipment/chainsaw.mdx"
    tgt = "assets/oracle_index/books/oritech/translated/zh_tw/content/equipment/chainsaw.mdx"
    para = "The chainsaw cuts wood fast."
    mdx = f"---\r\ntitle: Chainsaw\r\ntype: item\r\nid: oritech:chainsaw\r\n---\r\n\r\n{para}\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(src, mdx)

    cache: dict[str, str] = {}
    [t] = _oracle_targets(jar)
    # 標題與散文都翻,重掃 diff 才會清空
    process_target(t, _Dict({"Chainsaw": "鏈鋸", para: "鏈鋸能快速砍伐樹木。"}), cache, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        before = zf.read(tgt)

    # 已全數翻譯 → 重掃應無待翻 oracle_mdx 目標
    assert _oracle_targets(jar) == []

    # 以同 cache 對同一 target 重跑:_Boom 一旦被呼叫即失敗;內容相同不重寫
    process_target(t, _Boom(), cache, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        after = zf.read(tgt)
    assert after == before


def test_oracle_mdx_partial_existing_merge(tmp_path):
    """既有譯檔標題已中文、散文仍英文:只補譯散文,已中文的標題原封不動。"""
    jar = tmp_path / "oritech.jar"
    src = "assets/oracle_index/books/oritech/content/equipment/chainsaw.mdx"
    tgt = "assets/oracle_index/books/oritech/translated/zh_tw/content/equipment/chainsaw.mdx"
    para = "The chainsaw cuts wood fast."
    source_mdx = f"---\r\ntitle: Chainsaw\r\ntype: item\r\nid: oritech:chainsaw\r\n---\r\n\r\n{para}\r\n"
    # 部分譯檔:標題已中文,散文仍為英文(待補)
    partial_mdx = f"---\r\ntitle: 鏈鋸\r\ntype: item\r\nid: oritech:chainsaw\r\n---\r\n\r\n{para}\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(src, source_mdx)
        zf.writestr(tgt, partial_mdx)

    [t] = _oracle_targets(jar)
    # mapping 只含散文;若工具誤送標題重翻,"Chainsaw" 不在表中會退回英文
    process_target(t, _Dict({para: "鏈鋸能快速砍伐樹木。"}), {}, "zh_tw")
    with zipfile.ZipFile(jar) as zf:
        out = zf.read(tgt).decode("utf-8-sig")
    assert "鏈鋸能快速砍伐樹木。" in out    # 散文補譯完成
    assert para not in out                   # 英文散文已被取代
    assert "title: 鏈鋸" in out              # 既有中文標題保留(未被重翻成英文)


def test_oracle_mdx_zero_translatable_strings(tmp_path):
    """來源無任何可譯字串(僅 id/type + 自閉合 JSX):早退,完全不寫出目標檔。"""
    jar = tmp_path / "oritech.jar"
    src = "assets/oracle_index/books/oritech/content/misc/widget.mdx"
    tgt = "assets/oracle_index/books/oritech/translated/zh_tw/content/misc/widget.mdx"
    mdx = "---\r\ntype: item\r\nid: oritech:widget\r\n---\r\n\r\n<ModAsset id=\"oritech:widget\" />\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(src, mdx)
    # 掃描器會過濾掉零可譯來源,故以 include_translated 強制取得該 target 以驗早退
    [t] = _oracle_targets(jar, include_translated=True)
    process_target(t, _Boom(), {}, "zh_tw")   # 早退前不得送翻
    with zipfile.ZipFile(jar) as zf:
        assert tgt not in zf.namelist()        # 無可譯內容 → 不寫出目標
