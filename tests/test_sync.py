from pathlib import Path

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


def test_manifest_path(tmp_path):
    assert sync.manifest_path(tmp_path) == tmp_path / ".modpack_translator" / "sync_manifest.json"


def test_load_manifest_missing_returns_empty(tmp_path):
    assert sync.load_manifest(tmp_path) == []


def test_merge_and_load_roundtrip(tmp_path):
    sync.merge_manifest(tmp_path, [
        sync.ManifestEntry("config/ftbquests/quests/a.snbt", "ftbq_inline_snbt"),
    ])
    got = sync.load_manifest(tmp_path)
    assert got == [sync.ManifestEntry("config/ftbquests/quests/a.snbt", "ftbq_inline_snbt")]


def test_merge_is_union_dedup_by_rel_path(tmp_path):
    sync.merge_manifest(tmp_path, [sync.ManifestEntry("a.snbt", "ftbq_snbt")])
    sync.merge_manifest(tmp_path, [
        sync.ManifestEntry("a.snbt", "ftbq_snbt"),          # 重複 → 去重
        sync.ManifestEntry("b.json", "datapack_json"),      # 新增
    ])
    got = {e.rel_path for e in sync.load_manifest(tmp_path)}
    assert got == {"a.snbt", "b.json"}


def test_load_manifest_corrupt_returns_empty(tmp_path):
    p = sync.manifest_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json", encoding="utf-8")
    assert sync.load_manifest(tmp_path) == []
