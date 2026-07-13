"""GuideME 風味 Markdown 切段:巢狀 navigation.title、容器透明、表格、內聯標籤散文。
fixtures 濃縮自 All the Mons 實際語料(AE2/AdvancedAE/LBR/Powah)。"""
from modpack_translator.pipeline.mdx import extract_mdx, rebuild_mdx

# ── frontmatter:巢狀 navigation.title ──────────────────────────────────────

NAV_FM = (
    "---\n"
    "navigation:\n"
    "  parent: ae2-mechanics/ae2-mechanics-index.md\n"
    "  title: Channels\n"
    "  icon: controller\n"
    "  position: 10\n"
    "categories:\n"
    "- ae2\n"
    "item_ids:\n"
    "- ae2:controller\n"
    "---\n"
    "\n"
    "Body prose.\n"
)

def test_nav_title_extracted_and_structure_preserved():
    got = extract_mdx(NAV_FM)
    assert "Channels" in got.values()
    # 結構欄位不得被抽出
    for banned in ("ae2-mechanics/ae2-mechanics-index.md", "controller", "ae2", "ae2:controller"):
        assert banned not in got.values()

def test_nav_title_rebuild_applies_translation():
    got = extract_mdx(NAV_FM)
    tk = next(k for k, v in got.items() if v == "Channels")
    out = rebuild_mdx(NAV_FM, {tk: "頻道"})
    assert "  title: 頻道\n" in out
    assert "  parent: ae2-mechanics/ae2-mechanics-index.md\n" in out
    assert "  icon: controller\n" in out
    assert "- ae2:controller\n" in out

def test_nav_fm_rebuild_exact_when_no_translation():
    assert rebuild_mdx(NAV_FM, {}) == NAV_FM

QUOTED_FM = (
    "---\n"
    "navigation:\n"
    "  title: \"Logic Arrays\"\n"
    "  icon: \"red_logic_array\"\n"
    "  position: 3\n"
    "---\n"
    "\n"
    "Body.\n"
)

def test_nav_title_quoted_inner_extracted():
    got = extract_mdx(QUOTED_FM)
    assert "Logic Arrays" in got.values()           # 引號內文
    assert '"Logic Arrays"' not in got.values()      # 引號不進可翻段
    assert "red_logic_array" not in got.values()

def test_nav_title_quoted_rebuild_keeps_quotes():
    got = extract_mdx(QUOTED_FM)
    tk = next(k for k, v in got.items() if v == "Logic Arrays")
    out = rebuild_mdx(QUOTED_FM, {tk: "邏輯陣列"})
    assert '  title: "邏輯陣列"\n' in out
    assert rebuild_mdx(QUOTED_FM, {}) == QUOTED_FM

COLON_FM = (
    "---\n"
    "navigation:\n"
    "  title: \"AE2: Getting Started\"\n"
    "---\n"
)

def test_nav_title_with_colon_in_quotes():
    got = extract_mdx(COLON_FM)
    assert "AE2: Getting Started" in got.values()
    assert rebuild_mdx(COLON_FM, {}) == COLON_FM

DEEP_FM = (
    "---\n"
    "navigation:\n"
    "  parent: x.md\n"
    "  icon_components:\n"
    "    title: not-a-real-title\n"
    "  title: Real Title\n"
    "---\n"
)

def test_only_nav_level_title_extracted():
    got = extract_mdx(DEEP_FM)
    assert "Real Title" in got.values()
    assert "not-a-real-title" not in got.values()   # 更深層的 title 不可誤抽
    assert rebuild_mdx(DEEP_FM, {}) == DEEP_FM

# ── 容器透明:配對標籤內散文要翻、標籤保留 ─────────────────────────────────

DIV_BODY = (
    "# Getting Started\n"
    "\n"
    "<div class=\"notification is-info\">\n"
    "This guide covers the basics.\n"
    "</div>\n"
    "\n"
    "Tail prose.\n"
)

def test_container_inner_prose_translatable():
    vals = list(extract_mdx(DIV_BODY).values())
    assert "This guide covers the basics." in vals
    assert "Getting Started" in vals
    assert "Tail prose." in vals
    assert not any("<div" in v or "</div>" in v for v in vals)

def test_container_roundtrip_exact_and_translated():
    assert rebuild_mdx(DIV_BODY, {}) == DIV_BODY
    got = extract_mdx(DIV_BODY)
    k = next(key for key, v in got.items() if v == "This guide covers the basics.")
    out = rebuild_mdx(DIV_BODY, {k: "本指南涵蓋基礎。"})
    assert "<div class=\"notification is-info\">\n本指南涵蓋基礎。\n</div>\n" in out

NESTED_BODY = (
    "<Row>\n"
    "  <Column>\n"
    "    Prose inside nested columns.\n"
    "  </Column>\n"
    "  <BlockImage id=\"ae2:controller\" scale=\"4\" />\n"
    "</Row>\n"
)

def test_nested_container_prose_translatable_tags_preserved():
    got = extract_mdx(NESTED_BODY)
    assert "Prose inside nested columns." in got.values()
    assert not any("BlockImage" in v or "Column" in v for v in got.values())
    assert rebuild_mdx(NESTED_BODY, {}) == NESTED_BODY

UNCLOSED_BODY = (
    "<Row>\n"
    "  <ItemImage id=\"advanced_ae:quantum_helmet\" scale={4}>\n"
    "</Row>\n"
    "\n"
    "Have you ever wondered about armor?\n"
    "\n"
    "### Flight Card\n"
)

def test_unclosed_tag_does_not_swallow_following_prose():
    vals = list(extract_mdx(UNCLOSED_BODY).values())
    assert "Have you ever wondered about armor?" in vals   # 未閉合標籤後的散文仍要翻
    assert "Flight Card" in vals
    assert not any("ItemImage" in v for v in vals)
    assert rebuild_mdx(UNCLOSED_BODY, {}) == UNCLOSED_BODY

GAMESCENE_BODY = (
    "Intro prose.\n"
    "\n"
    "<GameScene zoom=\"7\" interactive={true}>\n"
    "  <ImportStructure src=\"../assets/assemblies/channel_demonstration_1.snbt\" />\n"
    "\n"
    "  <LineAnnotation color=\"#33ff33\" from=\"1 .4 .7\" to=\"2.4 .4 .7\" alwaysOnTop={true}/>\n"
    "  <IsometricCamera yaw=\"195\" pitch=\"30\" />\n"
    "</GameScene>\n"
    "\n"
    "Outro prose.\n"
)

def test_gamescene_block_fully_preserved():
    vals = list(extract_mdx(GAMESCENE_BODY).values())
    assert "Intro prose." in vals
    assert "Outro prose." in vals
    assert not any("GameScene" in v or "LineAnnotation" in v or "snbt" in v for v in vals)
    assert rebuild_mdx(GAMESCENE_BODY, {}) == GAMESCENE_BODY

CATEGORY_INDEX_BODY = (
    "<CategoryIndex>\n"
    "\n"
    "## Advanced Items\n"
    "\n"
    "Found an issue? Missing a feature?\n"
)

def test_unclosed_category_index_following_content_translatable():
    vals = list(extract_mdx(CATEGORY_INDEX_BODY).values())
    assert "Advanced Items" in vals
    assert "Found an issue? Missing a feature?" in vals
    assert rebuild_mdx(CATEGORY_INDEX_BODY, {}) == CATEGORY_INDEX_BODY

# ── 行首內聯標籤:同行閉合+殘餘文字=散文;無殘餘=字面 ──────────────────────

INLINE_COLOR_BODY = (
    "Each direction has a color: north,\n"
    "<Color color=\"#0094FF\">south</Color>, <Color color=\"#FF0000\">east</Color>,\n"
    "and more.\n"
)

def test_inline_tag_line_stays_in_paragraph():
    vals = list(extract_mdx(INLINE_COLOR_BODY).values())
    joined = "".join(vals)
    assert "south" in joined                       # 內聯標籤行的文字可翻
    assert "Each direction has a color" in joined
    # 三行是同一段(不得被標籤行切成三段)
    assert any("north" in v and "south" in v and "and more." in v for v in vals)
    assert rebuild_mdx(INLINE_COLOR_BODY, {}) == INLINE_COLOR_BODY

ANCHOR_BODY = (
    "<a name=\"terminal-ui\"></a>\n"
    "\n"
    "## The Terminal UI\n"
)

def test_bare_anchor_preserved_not_translatable():
    vals = list(extract_mdx(ANCHOR_BODY).values())
    assert "The Terminal UI" in vals
    assert not any("terminal-ui" in v or "<a" in v for v in vals)
    assert rebuild_mdx(ANCHOR_BODY, {}) == ANCHOR_BODY

INLINE_LINK_LINE_BODY = (
    "<a href=\"facades.md\">Facades</a> will be hidden while holding a tool.\n"
)

def test_inline_anchor_with_text_is_prose():
    vals = list(extract_mdx(INLINE_LINK_LINE_BODY).values())
    assert any("Facades" in v and "will be hidden" in v for v in vals)
    assert rebuild_mdx(INLINE_LINK_LINE_BODY, {}) == INLINE_LINK_LINE_BODY

# ── 表格:逐列切段、分隔列原樣 ─────────────────────────────────────────────

TABLE_BODY = (
    "|                | Capacity  | Generates |\n"
    "|----------------|-----------|-----------|\n"
    "| Starter        | 10,000 FE | 50 FE/t   |\n"
    "\n"
    "After the table.\n"
)

def test_table_rows_are_separate_segments():
    got = extract_mdx(TABLE_BODY)
    vals = list(got.values())
    assert any("Capacity" in v and "Generates" in v for v in vals)     # 表頭列可翻
    assert not any("----" in v for v in vals)                           # 分隔列不送翻
    assert "After the table." in vals
    header_key = next(k for k, v in got.items() if "Capacity" in v)
    out = rebuild_mdx(TABLE_BODY, {header_key: "|                | 容量  | 產出 |"})
    assert "|                | 容量  | 產出 |\n" in out
    assert "|----------------|-----------|-----------|\n" in out        # 分隔列原樣
    assert "| Starter        | 10,000 FE | 50 FE/t   |\n" in out        # 未譯列原樣

def test_table_rebuild_exact_when_no_translation():
    assert rebuild_mdx(TABLE_BODY, {}) == TABLE_BODY

# ── 純圖片段字面保留;純連結段仍可翻 ───────────────────────────────────────

IMAGE_BODY = (
    "![Logo](assets/logo.png)\n"
    "\n"
    "* [Getting Started](getting-started.md)\n"
    "* ![Plus](assets/diagrams/plus.png)\n"
)

def test_image_only_segments_are_literal_links_translatable():
    vals = list(extract_mdx(IMAGE_BODY).values())
    assert not any("logo.png" in v for v in vals)          # 純圖片段不抽
    assert not any("plus.png" in v for v in vals)
    assert "[Getting Started](getting-started.md)" in vals  # 連結文字是顯示文案,要翻
    assert rebuild_mdx(IMAGE_BODY, {}) == IMAGE_BODY

TAG_ONLY_LIST_BODY = (
    "Cards:\n"
    "\n"
    "* <ItemLink id=\"advanced_ae:walk_speed_card\" />\n"
    "* <ItemLink id=\"advanced_ae:jump_height_card\" /> and friends\n"
)

def test_tag_only_list_item_is_literal():
    vals = list(extract_mdx(TAG_ONLY_LIST_BODY).values())
    # 純標籤清單項:顯示名由 lang 檔渲染,不送翻(避免原樣返回殘留)
    assert not any(v.strip() == "<ItemLink id=\"advanced_ae:walk_speed_card\" />" for v in vals)
    # 帶散文的項仍要翻
    assert any("and friends" in v for v in vals)
    assert rebuild_mdx(TAG_ONLY_LIST_BODY, {}) == TAG_ONLY_LIST_BODY

# ── oracle Callout 行為不回歸(共用切段器)────────────────────────────────

CALLOUT_BODY = (
    "<Callout variant=\"info\">\n"
    "    The forests will fall.\n"
    "</Callout>\n"
)

def test_callout_behavior_unchanged():
    got = extract_mdx(CALLOUT_BODY)
    assert "The forests will fall." in got.values()
    assert rebuild_mdx(CALLOUT_BODY, {}) == CALLOUT_BODY
