from modpack_translator.pipeline.mdx import extract_mdx, rebuild_mdx

FM = (
    "---\r\n"
    "id: oritech:chainsaw\r\n"
    "title: Chainsaw\r\n"
    "type: item\r\n"
    "custom:\r\n"
    "    RF Capacity: \"10,000\"\r\n"
    "    Charge speed: \"512 RF/t\"\r\n"
    "related_items: [\"oritech:charger_block\"]\r\n"
    "---\r\n"
    "\r\n"
    "Body stays literal for now.\r\n"
)

def test_frontmatter_extracts_title_and_custom_labels():
    got = extract_mdx(FM)
    assert set(got.values()) >= {"Chainsaw", "RF Capacity", "Charge speed"}
    # 保留項不可被抽出
    assert "oritech:chainsaw" not in got.values()
    assert "item" not in got.values()

def test_frontmatter_rebuild_is_exact_when_no_translation():
    assert rebuild_mdx(FM, {}) == FM

def test_frontmatter_rebuild_applies_translation():
    got = extract_mdx(FM)
    title_key = next(k for k, v in got.items() if v == "Chainsaw")
    cap_key = next(k for k, v in got.items() if v == "RF Capacity")
    out = rebuild_mdx(FM, {title_key: "鏈鋸", cap_key: "RF 容量"})
    assert "title: 鏈鋸\r\n" in out
    assert "    RF 容量: \"10,000\"\r\n" in out
    assert "id: oritech:chainsaw\r\n" in out          # 保留
    assert "type: item\r\n" in out                     # 保留
    assert "    Charge speed: \"512 RF/t\"\r\n" in out  # 未譯者原樣

BODY = (
    "---\r\ntitle: X\r\ntype: item\r\nid: m:x\r\n---\r\n"
    "\r\n"
    "The chainsaw is a fast tool. It also\r\n"
    "works as a sword.\r\n"
    "\r\n"
    "### How to use\r\n"
    "\r\n"
    "- Charge it in a [charger](@oritech:charger_block).\r\n"
    "- Hold **Shift** to fell trees.\r\n"
)

def test_body_prose_blocks_extracted():
    vals = list(extract_mdx(BODY).values())
    assert "The chainsaw is a fast tool. It also\r\nworks as a sword." in vals  # 段落含軟換行
    assert "How to use" in vals                                                # 標題文字
    assert "Charge it in a [charger](@oritech:charger_block)." in vals          # 清單項含連結原文
    assert "Hold **Shift** to fell trees." in vals

def test_body_markers_preserved_on_rebuild():
    got = extract_mdx(BODY)
    hk = next(k for k, v in got.items() if v == "How to use")
    out = rebuild_mdx(BODY, {hk: "如何使用"})
    assert "### 如何使用\r\n" in out       # 保留 "### " 與換行
    assert "- Charge it in a [charger]" in out  # 清單標記保留

def test_body_rebuild_exact_when_no_translation():
    assert rebuild_mdx(BODY, {}) == BODY

JSX_BODY = (
    "Some paragraph text.\r\n"
    "<Callout>\r\n"
    "Nested text.\r\n"
    "</Callout>\r\n"
    "More text.\r\n"
)

def test_body_stops_at_any_angle_bracket_line():
    vals = list(extract_mdx(JSX_BODY).values())
    # 閉標籤不得被吸入任何可翻段落
    assert all("</Callout>" not in v for v in vals)
    # < 開頭行(開/閉標籤)本身不可成為可翻段落
    assert "<Callout>" not in vals
    # Callout 前後兩段是各自獨立的可翻片段
    assert "Some paragraph text." in vals
    assert "Nested text." in vals
    assert "More text." in vals
    # 兩段散文不得被黏成一段
    assert not any(("Nested text." in v and "More text." in v) for v in vals)

def test_body_angle_bracket_rebuild_exact_when_no_translation():
    assert rebuild_mdx(JSX_BODY, {}) == JSX_BODY

JSX = (
    "---\r\ntitle: X\r\ntype: item\r\nid: m:x\r\n---\r\n"
    "\r\n"
    "<Callout variant=\"info\">\r\n"
    "    The forests will fall.\r\n"
    "</Callout>\r\n"
    "\r\n"
    "<center>\r\n"
    "<ModAsset location=\"oritech:area/x\" width={512} />\r\n"
    "</center>\r\n"
    "\r\n"
    "<CraftingRecipe\r\n"
    "    slots={[\r\n"
    "        '', 'oritech:steel_ingot', '',\r\n"
    "    ]}\r\n"
    "/>\r\n"
)

def test_callout_inner_text_is_translatable():
    assert "The forests will fall." in extract_mdx(JSX).values()

def test_jsx_structure_preserved_and_only_callout_translated():
    got = extract_mdx(JSX)
    # ModAsset/CraftingRecipe/center/slots 內容不可被抽成可翻
    assert not any("ModAsset" in v or "slots" in v or "steel_ingot" in v for v in got.values())
    ck = next(k for k, v in got.items() if v == "The forests will fall.")
    out = rebuild_mdx(JSX, {ck: "森林將傾倒。"})
    assert "<Callout variant=\"info\">\r\n" in out          # 開標籤保留
    assert "</Callout>\r\n" in out                          # 閉標籤保留
    assert "    森林將傾倒。\r\n" in out                     # 內文翻譯、縮排保留
    assert "<ModAsset location=\"oritech:area/x\" width={512} />\r\n" in out
    assert "        '', 'oritech:steel_ingot', '',\r\n" in out  # CraftingRecipe 多行整段保留

def test_jsx_rebuild_exact_when_no_translation():
    assert rebuild_mdx(JSX, {}) == JSX

REAL = (  # 濃縮自 oritech content/equipment/chainsaw.mdx 的代表結構
    "---\r\n"
    "id: oritech:chainsaw\r\n"
    "title: Chainsaw\r\n"
    "type: item\r\n"
    "custom:\r\n"
    "    RF Capacity: \"10,000\"\r\n"
    "---\r\n"
    "\r\n"
    "The chainsaw is a fast tool for harvesting wood. It functions as an axe\r\n"
    "that never breaks.\r\n"
    "\r\n"
    "<Callout variant=\"info\">\r\n"
    "    The forests will fall.\r\n"
    "</Callout>\r\n"
    "\r\n"
    "### How to use\r\n"
    "\r\n"
    "Charge the chainsaw in a [charger](@oritech:charger_block).\r\n"
)

def test_identity_rebuild_reproduces_source_byte_for_byte():
    assert rebuild_mdx(REAL, {}) == REAL

def test_link_target_preserved_when_link_text_translated():
    got = extract_mdx(REAL)
    k = next(key for key, v in got.items() if v.startswith("Charge the chainsaw"))
    out = rebuild_mdx(REAL, {k: "在[充電器](@oritech:charger_block)中充電。"})
    assert "(@oritech:charger_block)" in out          # link target preserved
    assert "在[充電器]" in out                          # translation actually applied
    assert "Charge the chainsaw" not in out            # source span was replaced (a no-op rebuild would leave this → fails)

def test_all_extracted_values_are_nonempty_prose():
    for v in extract_mdx(REAL).values():
        assert v.strip()                     # 無空白段
        assert not v.lstrip().startswith("<")  # 未把 JSX 標籤當可翻


# ── 圍欄程式碼區塊（```）原樣保留,不得抽出送翻（oracle_index gradle_task.mdx 失敗案例）──
CODE = (
    "---\r\ntitle: Gradle Sync\r\n---\r\n"
    "\r\n"
    "A gradle task may look as follows:\r\n"
    "\r\n"
    "```\r\n"
    "tasks.named('processResources') {\r\n"
    "    from(\"$rootDir/wiki\") {\r\n"
    "        into \"assets/oracle_index/books/oritech\"\r\n"
    "        exclude 'assets/item/**'   // items are rendered ingame\r\n"
    "    }\r\n"
    "}\r\n"
    "```\r\n"
    "\r\n"
    "The files are added to the resources folder.\r\n"
)

def test_fenced_code_block_not_extracted():
    vals = list(extract_mdx(CODE).values())
    # 圍欄內的程式碼不得被當成可翻譯段落
    assert not any("processResources" in v for v in vals)
    assert not any("tasks.named" in v for v in vals)
    assert not any("```" in v for v in vals)
    # 前後散文仍要抽出
    assert "A gradle task may look as follows:" in vals
    assert "The files are added to the resources folder." in vals

def test_fenced_code_block_rebuild_exact_when_untranslated():
    assert rebuild_mdx(CODE, {}) == CODE

def test_fenced_code_block_preserved_through_roundtrip():
    got = extract_mdx(CODE)
    k = next(key for key, v in got.items() if v.startswith("A gradle task"))
    out = rebuild_mdx(CODE, {k: "gradle 任務範例如下："})
    assert "gradle 任務範例如下：" in out
    assert "tasks.named('processResources') {" in out   # 程式碼原封不動
    assert out.count("```") == 2                          # 圍欄完整保留
