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
