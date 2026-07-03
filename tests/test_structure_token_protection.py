"""結構 token 保護回歸測試：v1.6.2 實際案例。

三類「譯文必須原樣保留」的結構先前未進 _PLACEHOLDERS，造成雙重問題：
1. 誤殺：結構內含 display/player 等漏翻偵測字，完美譯文也被拒（Failed Items）
2. 反向篩選：把結構翻譯掉的壞輸出反而通過驗證（fancymenu $$按鈕、occultism c:礦石）

樣本取自 All the Mons 模組包的實際失敗與翻壞條目。
另含 _rewrite_jar 對重複 central directory 記錄（ars_nouveau jar）的容錯測試。
"""
import io
import struct
import zipfile

from modpack_translator.pipeline.patcher import _rewrite_jar
from modpack_translator.pipeline.postprocessor import process
from modpack_translator.pipeline.preprocessor import (
    decode,
    encode,
    is_usable_translation,
)


# ── 1. encode：新增結構 token 類 ─────────────────────────────────────

def test_double_dollar_variable_is_tokenized():
    encoded, tokens = encode("- §z$$button §r= Which button was released")
    assert "$$button" in tokens
    assert "$$button" not in encoded


def test_single_dollar_variable_is_tokenized():
    encoded, tokens = encode("$(thing)$owner/$: an alias for $(thing)$owner_pos/$.")
    assert "$owner" in tokens
    assert "$owner_pos" in tokens


def test_single_dollar_variable_with_argument_is_tokenized():
    _encoded, tokens = encode("$(thing)$player=<name>/$: an alias")
    assert "$player=<name>" in tokens


def test_resource_location_is_tokenized():
    _encoded, tokens = encode("uses minecraft:player for players")
    assert "minecraft:player" in tokens


def test_short_namespace_resource_location_is_tokenized():
    _encoded, tokens = encode("Divination c:ores")
    assert "c:ores" in tokens


def test_syntax_hint_resource_location_is_tokenized():
    _encoded, tokens = encode("#tag or item:id")
    assert "item:id" in tokens


def test_nested_nbt_brace_run_is_tokenized_whole():
    blob = "netherite_sword{display:{Name:'[\"\",{\"text\":\"Kinabra\",\"italic\":false}]'}}"
    encoded, tokens = encode(f"/give @a {blob}")
    assert blob in tokens
    assert "display" not in encoded


def test_positional_format_followed_by_word_keeps_word_translatable():
    # quarryplus: "%1$sPlace this above fluid block."（%1$s 之後是正常單字）
    encoded, tokens = encode("%1$sPlace this above fluid block.")
    assert tokens == ["%1$s"]
    assert "Place this above" in encoded


def test_prose_colon_without_adjacent_word_not_tokenized():
    # 一般句子的冒號後有空白，不得誤判為資源位置
    _encoded, tokens = encode("Warning: do not translate this label")
    assert tokens == []


def test_time_string_not_tokenized_as_resource_location():
    _encoded, tokens = encode("Arrives at 12:30 sharp")
    assert tokens == []


def test_new_tokens_roundtrip_through_decode():
    source = "uses minecraft:player and $$entity_key with $owner_pos"
    encoded, tokens = encode(source)
    assert decode(encoded, tokens) == source


def test_fancymenu_custom_section_codes_are_soft_tokens():
    # §x / §z 是 FancyMenu 自訂格式碼，非 vanilla 集合
    source = "§xRequires FancyMenu on the server.§r"
    encoded, tokens = encode(source)
    assert "§x" in tokens
    # 軟性 token：模型輸出遺失 §x 仍可接受
    final, ok = process("需要伺服器端安裝 FancyMenu。{1}", encoded, tokens)
    assert ok
    assert final == "需要伺服器端安裝 FancyMenu。§r"


# ── 2. Failed Items 誤殺：完美譯文必須通過 ───────────────────────────

FANCYMENU_NBT_SRC = (
    "This allows you to set custom NBT data to the item.\n"
    "It is basically the part the /give command that follows\n"
    "after the item name in curly brackets {}.\n\n"
    "§lFull example command:\n"
    "/give @a netherite_sword{display:{Name:'[\"\",{\"text\":\"Kinabra\",\"italic\":false}]'}}\n\n"
    "§lSet this as value for NBT:\n"
    "{display:{Name:'[\"\",{\"text\":\"Kinabra\",\"italic\":false}]'}}\n\n"
    "There are lots of Give Command Generators available online,\n"
    "so just ask Google about it and you will find something."
)
FANCYMENU_NBT_DST = (
    "這允許你為物品設定自訂 NBT 資料。\n"
    "基本上就是 /give 指令中物品名稱後面\n"
    "大括號 {} 內的部分。\n\n"
    "§l完整範例指令：\n"
    "/give @a netherite_sword{display:{Name:'[\"\",{\"text\":\"Kinabra\",\"italic\":false}]'}}\n\n"
    "§l將此設為 NBT 的值：\n"
    "{display:{Name:'[\"\",{\"text\":\"Kinabra\",\"italic\":false}]'}}\n\n"
    "網路上有許多 Give 指令產生器，\n"
    "只要問問 Google 就能找到。"
)


def test_nbt_example_ideal_translation_accepted():
    assert is_usable_translation(FANCYMENU_NBT_SRC, FANCYMENU_NBT_DST)


def test_resource_location_ideal_translation_accepted():
    source = (
        "- §z$$entity_killed_by_key §r= The resource location of the killer's type "
        "(uses minecraft:player for players, NONE when unavailable)"
    )
    target = (
        "- §z$$entity_killed_by_key §r= 擊殺者類型的資源位置"
        "（玩家使用 minecraft:player，無法取得時為 NONE）"
    )
    assert is_usable_translation(source, target)


def test_patchouli_dollar_variable_ideal_translation_accepted():
    source = (
        "Several older variables also exist which remain usable for compatibility reasons "
        "(but it's recommended to use the variables on the previous page):"
        "$(li)$(thing)$owner/$: an alias for $(thing)$owner_pos/$."
        "$(li)$(thing)$drone/$: gets the blockpos $(italic)above/$ the drone, for historical reasons."
        "$(li)$(thing)$player=<name>/$: an alias for $(thing)$player_pos/$."
    )
    target = (
        "還有幾個較舊的變數因相容性原因仍可使用（但建議使用上一頁的變數）："
        "$(li)$(thing)$owner/$：$(thing)$owner_pos/$ 的別名。"
        "$(li)$(thing)$drone/$：基於歷史因素，取得無人機$(italic)上方/$的方塊座標。"
        "$(li)$(thing)$player=<name>/$：$(thing)$player_pos/$ 的別名。"
    )
    assert is_usable_translation(source, target)


# ── 3. 反向篩選：把結構翻譯掉的壞輸出必須被拒 ────────────────────────

def test_translated_double_dollar_variable_rejected():
    source = "- §z$$button §r= Which button was released (left, right, middle)"
    target = "- §z$$按鈕 §r= 放開的是哪個按鈕（左鍵、右鍵、中鍵）"
    assert not is_usable_translation(source, target)


def test_translated_resource_location_rejected():
    assert not is_usable_translation("Divination c:ores", "占卜 c:礦石")


def test_translated_syntax_hint_rejected():
    assert not is_usable_translation("#tag or item:id", "#標籤或物品:id")


def test_preserved_structures_still_accepted():
    assert is_usable_translation("Divination c:ores", "占卜 c:ores")
    assert is_usable_translation("#tag or item:id", "#tag 或 item:id")


# ── 4. _rewrite_jar：重複 central directory 記錄容錯 ─────────────────

def _zip_with_duplicate_central_entry() -> bytes:
    """模擬 ars_nouveau jar：同一 entry 在 central directory 出現兩次、
    指向同一個 local header offset，觸發 CPython 的 Overlapped entries 防護。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("META-INF/LICENSE.txt", "Apache License " * 100)
        zf.writestr("assets/mod/lang/en_us.json", '{"k": "Value"}')
        zf.writestr("assets/mod/lang/zh_tw.json", '{"k": "Value"}')
    data = bytearray(buf.getvalue())

    eocd_off = bytes(data).rfind(b"PK\x05\x06")
    entries_disk, entries_total, cd_size, cd_off = struct.unpack(
        "<HHII", data[eocd_off + 8:eocd_off + 20]
    )
    cd = bytes(data[cd_off:cd_off + cd_size])
    nlen, elen, clen = struct.unpack("<HHH", cd[28:34])
    first_record = cd[:46 + nlen + elen + clen]

    new_cd = first_record + cd
    eocd = bytearray(data[eocd_off:])
    struct.pack_into(
        "<HHII", eocd, 8,
        entries_disk + 1, entries_total + 1, cd_size + len(first_record), cd_off,
    )
    return bytes(data[:cd_off]) + new_cd + bytes(eocd)


def test_rewrite_jar_tolerates_duplicate_central_entries(tmp_path):
    jar = tmp_path / "dup.jar"
    jar.write_bytes(_zip_with_duplicate_central_entry())

    # 前置確認：這個 zip 的確觸發 CPython 防護（讀 LICENSE 拋 BadZipFile）
    with zipfile.ZipFile(jar) as zf:
        try:
            zf.read("META-INF/LICENSE.txt")
            triggered = False
        except zipfile.BadZipFile:
            triggered = True
    assert triggered, "測試夾具未能重現 Overlapped entries（CPython 行為改變？）"

    payload = '{"k": "值"}'.encode("utf-8")
    _rewrite_jar(jar, {"assets/mod/lang/zh_tw.json": payload})

    with zipfile.ZipFile(jar) as zf:
        names = zf.namelist()
        assert names.count("META-INF/LICENSE.txt") == 1
        assert zf.read("assets/mod/lang/zh_tw.json") == payload
        assert zf.read("META-INF/LICENSE.txt") == b"Apache License " * 100
        assert zf.testzip() is None
