"""the_vault class 常數池修補。

反編譯確認 Config.SUPPORTED_LOCALES 硬編碼、無 zh_tw → config/the_vault/
lang/zh_tw/ 覆蓋檔永不載入；選單樹（Vault Hunters Options）UI 文字為
class 字面值。兩者皆以常數池 CONSTANT_Utf8 替換修補：僅動「只被
CONSTANT_String 引用」的 entry，識別字（Class/NameAndType 引用）不碰。
"""
import zipfile

from modpack_translator.pipeline import vh
from modpack_translator.pipeline.patcher import (
    _parse_constant_pool,
    _replace_class_string_literals,
    apply_vault_class_patch,
    extract_vault_ui_literals,
    patch_vault_literal_map,
    plan_vault_class_patch,
)
from modpack_translator.pipeline.runner import (
    _protect_concat_slots,
    _restore_concat_slots,
    process_target,
)
from modpack_translator.pipeline.scanner import ModpackScanner


# ── 合成最小 class 檔 ────────────────────────────────────────────────────

def _utf8(text: str) -> bytes:
    raw = text.encode("utf-8")
    return b"\x01" + len(raw).to_bytes(2, "big") + raw


def _string(idx: int) -> bytes:
    return b"\x08" + idx.to_bytes(2, "big")


def _name_and_type(name_idx: int, desc_idx: int) -> bytes:
    return b"\x0c" + name_idx.to_bytes(2, "big") + desc_idx.to_bytes(2, "big")


def _class_file(*entries: bytes, tail: bytes = b"\x00\x21\x00\x00") -> bytes:
    count = len(entries) + 1  # constant_pool_count = 實體數 + 1
    return (
        b"\xca\xfe\xba\xbe"          # magic
        + b"\x00\x00\x00\x34"        # minor/major (Java 8)
        + count.to_bytes(2, "big")
        + b"".join(entries)
        + tail
    )


def _config_class() -> bytes:
    # SUPPORTED_LOCALES 常數：en_us 與 es_mx，皆被 CONSTANT_String 引用
    return _class_file(_utf8("en_us"), _string(1), _utf8("es_mx"), _string(3))


def _make_jar(tmp_path, members: dict[str, bytes]):
    mods = tmp_path / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    jar = mods / "the_vault-1.18.2-test.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return jar


# ── 常數池替換單元 ───────────────────────────────────────────────────────

def test_replace_string_only_literal():
    data = _class_file(_utf8("Back"), _string(1))
    patched, n = _replace_class_string_literals(data, {"Back": "返回"})
    assert n == 1
    utf8, string_refs, _, _ = _parse_constant_pool(patched)
    assert utf8[1][1].decode("utf-8") == "返回"
    assert string_refs == {1}
    # 尾段（class 本體）原樣保留
    assert patched.endswith(b"\x00\x21\x00\x00")


def test_identifier_referenced_utf8_untouched():
    # "OFF" 同時被 String 與 NameAndType 引用 → 是識別字，不可替換
    data = _class_file(
        _utf8("OFF"), _string(1), _utf8("()V"), _name_and_type(1, 3),
        _utf8("Colorblind Mode"), _string(5),
    )
    patched, n = _replace_class_string_literals(
        data, {"OFF": "關閉", "Colorblind Mode": "色盲模式"}
    )
    assert n == 1
    utf8, _, _, _ = _parse_constant_pool(patched)
    assert utf8[1][1] == b"OFF"
    assert utf8[5][1].decode("utf-8") == "色盲模式"


def test_concat_placeholder_slots_survive():
    data = _class_file(_utf8("Page \x01 of \x01"), _string(1))
    patched, n = _replace_class_string_literals(data, {"Page \x01 of \x01": "第 \x01 / \x01 頁"})
    assert n == 1
    utf8, _, _, _ = _parse_constant_pool(patched)
    assert utf8[1][1].decode("utf-8").count("\x01") == 2


def test_non_class_file_rejected():
    try:
        _replace_class_string_literals(b"PK\x03\x04junk", {"a": "b"})
    except ValueError:
        pass
    else:
        raise AssertionError("non-class data must raise")


# ── jar 層計畫/套用 ──────────────────────────────────────────────────────

def test_plan_patches_supported_locales(tmp_path):
    _make_jar(tmp_path, {vh.CONFIG_CLASS_PATH: _config_class()})
    plan = plan_vault_class_patch(tmp_path, "zh_tw")
    assert plan is not None and plan.locale_patched
    apply_vault_class_patch(plan)
    with zipfile.ZipFile(plan.jar_path) as zf:
        utf8, _, _, _ = _parse_constant_pool(zf.read(vh.CONFIG_CLASS_PATH))
    values = {payload for _, payload in utf8.values()}
    assert b"zh_tw" in values and b"es_mx" not in values
    # 冪等：已修補 → 無計畫
    assert plan_vault_class_patch(tmp_path, "zh_tw") is None


def test_plan_skips_officially_supported_locale(tmp_path):
    _make_jar(tmp_path, {
        vh.CONFIG_CLASS_PATH: _class_file(
            _utf8("zh_cn"), _string(1), _utf8("es_mx"), _string(3)
        ),
    })
    assert plan_vault_class_patch(tmp_path, "zh_cn") is None


def test_plan_patches_menu_literals_for_zh_tw_only(tmp_path):
    tabbed = "iskallia/vault/client/gui/screen/custom/TabbedScreen.class"
    _make_jar(tmp_path, {tabbed: _class_file(_utf8("Back"), _string(1))})
    assert plan_vault_class_patch(tmp_path, "ja_jp") is None  # 白名單僅 zh_tw
    plan = plan_vault_class_patch(tmp_path, "zh_tw")
    assert plan is not None and plan.literal_count == 1 and not plan.locale_patched
    apply_vault_class_patch(plan)
    with zipfile.ZipFile(plan.jar_path) as zf:
        utf8, _, _, _ = _parse_constant_pool(zf.read(tabbed))
    assert utf8[1][1].decode("utf-8") == "返回"
    assert plan_vault_class_patch(tmp_path, "zh_tw") is None


def test_no_vault_jar_no_plan(tmp_path):
    (tmp_path / "mods").mkdir()
    assert plan_vault_class_patch(tmp_path, "zh_tw") is None


def test_real_literals_are_bmp_safe():
    # 白名單值必須可用 modified UTF-8 表示（BMP、非 NUL）
    for mapping in vh.HARDCODED_UI_LITERALS.values():
        for value in mapping.values():
            for ch in value:
                assert ch != "\x00" and ord(ch) <= 0xFFFF


# ── 自動字面值翻譯（vh_class_literals） ──────────────────────────────────

_SCREEN_CLS = "iskallia/vault/client/gui/screen/summary/VaultEndScreen.class"


def _screen_class() -> bytes:
    # 顯示文句（多詞）/ 單詞 / 識別字引用 / 串接槽位 各一
    return _class_file(
        _utf8("Claim Rewards"), _string(1),
        _utf8("Done"), _string(3),                       # 單詞：不自動翻
        _utf8("SMALL"), _string(5), _utf8("()V"), _name_and_type(5, 7),
        _utf8("Total: \x01"), _string(9),
    )


class _Dict:
    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping

    def translate(self, text, cancel_check=None):
        return self.mapping.get(text.strip(), text)


def test_slot_protect_restore_roundtrip():
    assert _protect_concat_slots("Total: \x01") == "Total: {0}"
    assert _protect_concat_slots("Page \x01 of \x01") == "Page {0} of {1}"
    assert _restore_concat_slots("第 {0} / {1} 頁", 2) == "第 \x01 / \x01 頁"
    assert _restore_concat_slots("共 {0} 頁", 2) is None      # 佔位符遺失
    assert _restore_concat_slots("{0}{0}", 1) is None          # 佔位符重複
    assert _restore_concat_slots("無槽位", 0) == "無槽位"


def test_extract_only_multiword_display_literals(tmp_path):
    _make_jar(tmp_path, {_SCREEN_CLS: _screen_class()})
    from modpack_translator.pipeline.patcher import find_vault_jar

    jar = find_vault_jar(tmp_path)
    literals = extract_vault_ui_literals(jar)
    assert literals == {_SCREEN_CLS: ["Claim Rewards", "Total: \x01"]}


def test_extract_skips_curated_whitelist(tmp_path):
    tabbed = "iskallia/vault/client/gui/screen/custom/TabbedScreen.class"
    # "Vault Hunters Options" 在白名單 → 不入自動清單
    cls = "iskallia/vault/client/gui/screen/VaultOptionsScreen.class"
    _make_jar(tmp_path, {cls: _class_file(_utf8("Vault Hunters Options"), _string(1))})
    from modpack_translator.pipeline.patcher import find_vault_jar

    assert extract_vault_ui_literals(find_vault_jar(tmp_path)) == {}


def test_scan_and_process_vh_class_literals(tmp_path):
    jar = _make_jar(tmp_path, {_SCREEN_CLS: _screen_class()})
    targets = [
        t for t in ModpackScanner().scan(tmp_path, "zh_tw")
        if t.format == "vh_class_literals"
    ]
    assert len(targets) == 1 and targets[0].source_file == jar

    translated, cached, fallback, failed = process_target(
        targets[0],
        _Dict({"Claim Rewards": "領取獎勵", "Total: {0}": "總計：{0}"}),
        {}, "zh_tw",
    )
    assert (translated, fallback) == (2, 0) and not failed
    with zipfile.ZipFile(jar) as zf:
        utf8, srefs, _, _ = _parse_constant_pool(zf.read(_SCREEN_CLS))
    texts = {utf8[i][1].decode("utf-8") for i in srefs}
    assert "領取獎勵" in texts and "總計：\x01" in texts
    assert "Done" in texts and "SMALL" in texts  # 單詞與識別字原樣
    # 已翻 → 不再入列，冪等
    assert extract_vault_ui_literals(jar) == {}
    assert [
        t for t in ModpackScanner().scan(tmp_path, "zh_tw")
        if t.format == "vh_class_literals"
    ] == []


def test_patch_vault_literal_map_partial(tmp_path):
    jar = _make_jar(tmp_path, {_SCREEN_CLS: _screen_class()})
    n = patch_vault_literal_map(jar, {
        _SCREEN_CLS: {"Claim Rewards": "領取獎勵"},
        "iskallia/vault/missing.class": {"X Y": "不存在"},
    })
    assert n == 1
