"""GuideME 指南偵測:三種 root 形態、_<lang> 來源排除、navigation 資格審查、冪等。"""
import zipfile

from modpack_translator.pipeline.scanner import ModpackScanner

NAV_PAGE = (
    "---\n"
    "navigation:\n"
    "  title: Channels\n"
    "  icon: controller\n"
    "---\n"
    "\n"
    "Prose about channels.\n"
)
NAV_PAGE_ZH = (
    "---\n"
    "navigation:\n"
    "  title: 頻道\n"
    "  icon: controller\n"
    "---\n"
    "\n"
    "頻道的說明文。\n"
)
PLAIN_MD = "# Just a heading\n\nSome text without frontmatter.\n"


def _targets(jar, fmt="guideme_md"):
    return [t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None) if t.format == fmt]


def test_ae2guide_shape_detected(tmp_path):
    jar = tmp_path / "ae2.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/ae2/ae2guide/index.md", NAV_PAGE)
        zf.writestr("assets/ae2/ae2guide/ae2-mechanics/channels.md", NAV_PAGE)
    ts = _targets(jar)
    assert len(ts) == 2
    by_src = {t.path_in_jar: t for t in ts}
    t = by_src["assets/ae2/ae2guide/ae2-mechanics/channels.md"]
    assert t.target_path_in_jar == "assets/ae2/ae2guide/_zh_tw/ae2-mechanics/channels.md"
    assert t.existing_path_in_jar is None
    assert t.mod_id == "ae2"
    assert t.output_mode == "jar_inject"
    assert by_src["assets/ae2/ae2guide/index.md"].target_path_in_jar == "assets/ae2/ae2guide/_zh_tw/index.md"


def test_default_guides_layout_detected(tmp_path):
    jar = tmp_path / "powah.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/powah/guides/powah/book/generators/thermo_generator.md", NAV_PAGE)
        # 既有法文翻譯樹:不是來源
        zf.writestr("assets/powah/guides/powah/book/_fr_fr/generators/thermo_generator.md", NAV_PAGE)
    ts = _targets(jar)
    assert len(ts) == 1
    assert ts[0].path_in_jar == "assets/powah/guides/powah/book/generators/thermo_generator.md"
    assert ts[0].target_path_in_jar == "assets/powah/guides/powah/book/_zh_tw/generators/thermo_generator.md"


def test_custom_guide_folder_detected_and_zh_cn_not_source(tmp_path):
    jar = tmp_path / "lbr.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/little_big_redstone/guide/logic_arrays.md", NAV_PAGE)
        zf.writestr("assets/little_big_redstone/guide/_zh_cn/logic_arrays.md", NAV_PAGE_ZH)
    ts = _targets(jar)
    assert len(ts) == 1
    assert ts[0].path_in_jar == "assets/little_big_redstone/guide/logic_arrays.md"
    assert ts[0].target_path_in_jar == "assets/little_big_redstone/guide/_zh_tw/logic_arrays.md"


def test_noise_md_rejected(tmp_path):
    jar = tmp_path / "noise.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/fancymenu/credits_and_copyright.md", PLAIN_MD)   # 無資料夾層
        zf.writestr("assets/create/lang/notes.md", PLAIN_MD)                 # 子樹無 navigation
        zf.writestr("assets/zerocore/GUI Theme file.md", PLAIN_MD)
    assert _targets(jar) == []


def test_root_qualified_by_sibling_navigation_page(tmp_path):
    """root 內任一頁有 navigation frontmatter → 整個 root 合格,無 frontmatter 的頁也要翻。"""
    jar = tmp_path / "mixed.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/m/guide/index.md", NAV_PAGE)
        zf.writestr("assets/m/guide/raw_page.md", PLAIN_MD)
    srcs = {t.path_in_jar for t in _targets(jar)}
    assert srcs == {"assets/m/guide/index.md", "assets/m/guide/raw_page.md"}


def test_fully_translated_page_skipped(tmp_path):
    """既有 _zh_tw 已全譯 → diff 空 → 不產生目標(冪等)。"""
    jar = tmp_path / "done.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/ae2/ae2guide/index.md", NAV_PAGE)
        zf.writestr("assets/ae2/ae2guide/_zh_tw/index.md", NAV_PAGE_ZH)
    assert _targets(jar) == []


def test_partially_translated_page_targets_with_existing(tmp_path):
    """既有 _zh_tw 只翻了標題、散文仍英文 → 需翻,existing 指向既有譯頁。"""
    partial = NAV_PAGE.replace("title: Channels", "title: 頻道")
    jar = tmp_path / "partial.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/ae2/ae2guide/index.md", NAV_PAGE)
        zf.writestr("assets/ae2/ae2guide/_zh_tw/index.md", partial)
    ts = _targets(jar)
    assert len(ts) == 1
    assert ts[0].existing_path_in_jar == "assets/ae2/ae2guide/_zh_tw/index.md"


def test_shallow_guides_layout_skipped(tmp_path):
    """assets/<ns>/guides/ 層數不足(無法安全定位 root)→ 跳過。"""
    jar = tmp_path / "shallow.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/m/guides/readme.md", NAV_PAGE)
    assert _targets(jar) == []


def test_oracle_book_not_double_scanned(tmp_path):
    """oracle 書仍走 oracle_mdx;其樹內不產生 guideme_md 目標。"""
    jar = tmp_path / "oritech.jar"
    mdx = "---\r\ntitle: Chainsaw\r\ntype: item\r\nid: oritech:chainsaw\r\n---\r\n\r\nFast tool.\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/oracle_index/books/oritech/content/equipment/chainsaw.mdx", mdx)
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    assert {t.format for t in targets} == {"oracle_mdx"}


def test_include_translated_returns_all_pages(tmp_path):
    jar = tmp_path / "sidecar.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/ae2/ae2guide/index.md", NAV_PAGE)
        zf.writestr("assets/ae2/ae2guide/_zh_tw/index.md", NAV_PAGE_ZH)
    scanner = ModpackScanner()
    scanner._include_translated = True
    ts = [t for t in scanner._scan_jar(jar, "zh_tw", None) if t.format == "guideme_md"]
    assert len(ts) == 1                                  # 已全譯仍列出(sidecar 用),但 _zh_tw 樹不當來源
    assert ts[0].path_in_jar == "assets/ae2/ae2guide/index.md"
