"""GuideME 行內結構的 token 保護:連結目標與帶屬性標籤必須凍結,壞輸出必須被攔。"""
from modpack_translator.pipeline.preprocessor import (
    encode,
    decode,
    is_usable_translation,
    _preserves_required_tokens,
)

PARA = (
    'Applied Energistics 2\'s [ME Networks](me-network-connections.md) require '
    'Channels to support [devices](../ae2-mechanics/devices.md#anchor). '
    'Only <ItemLink id="me_p2p_tunnel" /> can transmit 32.'
)


def test_encode_freezes_link_targets_and_inline_tags():
    encoded, tokens = encode(PARA)
    assert "(me-network-connections.md)" not in encoded
    assert "(../ae2-mechanics/devices.md#anchor)" not in encoded
    assert '<ItemLink id="me_p2p_tunnel" />' not in encoded
    # 連結「文字」仍要可見可翻
    assert "[ME Networks]" in encoded
    assert "[devices]" in encoded
    assert decode(encoded, tokens) == PARA


def test_encode_freezes_paired_inline_tags_with_attrs():
    src = '<Color color="#0094FF">south</Color>, and more.'
    encoded, tokens = encode(src)
    assert '<Color color="#0094FF">' not in encoded
    assert "</Color>" not in encoded
    assert "south" in encoded                    # 標籤內文可翻
    assert decode(encoded, tokens) == src


def test_plain_parenthetical_still_translatable():
    encoded, _ = encode("This step is (optional) for now.")
    assert "(optional)" in encoded               # 一般括號詞不得被凍結


def test_mangled_link_target_rejected():
    zh = 'AE2 的[ME 網路](中-中-中.中)需要頻道支援[裝置](../中/中.md#中)。只有 <中 id="中" /> 能傳輸 32。'
    assert not _preserves_required_tokens(PARA, zh)
    assert not is_usable_translation(PARA, zh)


def test_kept_link_target_accepted():
    zh = ('AE2 的 [ME 網路](me-network-connections.md) 需要頻道支援'
          '[裝置](../ae2-mechanics/devices.md#anchor)。'
          '只有 <ItemLink id="me_p2p_tunnel" /> 能傳輸 32。')
    assert _preserves_required_tokens(PARA, zh)
    assert is_usable_translation(PARA, zh)
