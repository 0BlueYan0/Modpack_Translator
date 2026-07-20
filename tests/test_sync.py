from modpack_translator.pipeline import sync


def test_server_side_formats_membership():
    assert sync.is_server_side("ftbq_snbt")
    assert sync.is_server_side("ftbq_inline_snbt")
    assert sync.is_server_side("heracles_snbt")
    assert sync.is_server_side("heracles_inline_snbt")
    assert sync.is_server_side("bq_lang")
    assert sync.is_server_side("datapack_json")


def test_client_side_formats_excluded():
    for fmt in (
        "json_lang", "legacy_lang", "pack_json_lang", "pack_legacy_lang",
        "patchouli_json", "oracle_mdx", "oracle_meta", "guideme_md",
        "citadel_book_txt", "rct_names", "kubejs_json", "vh_config_json",
    ):
        assert not sync.is_server_side(fmt), fmt
